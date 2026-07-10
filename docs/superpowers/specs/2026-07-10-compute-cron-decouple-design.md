# Decouple compute from the sweep via cron (worker refactor, step 2 — rescoped)

Date: 2026-07-10
Status: spec (founder chose "extend the cron" over a two-process split)
Parent: `docs/research/2026-07-10-worker-architecture-refactor.md` §Migration path, step 2

## Why this shape (finding that rescoped step 2)

The original step 2 was "split the worker into scrape-lane + compute-lane
processes." On inspection that's more machinery than the moment needs:
**scoring is already decoupled** — `ops/prod/crontab` runs
`python -m agf.scoring.run_scoring` every 30 min in the `cron` service,
independent of the worker loop. (Prod even double-scores today: the worker
also scores in-loop via `AGF_SCORE_AFTER_SWEEP=1`, redundant with the cron —
the RUNBOOK says score_after should be off, "cron owns it".)

So the only compute still trapped behind the ~40-min sequential sweep is:
velocity snapshot (1.5), content embeddings + crowding (2a), preference
scoring (2b), and candidate embeddings (2c) — and those feed Discover-feed /
review-queue freshness. The smallest change that decouples them is to move
them onto the existing cron, exactly as scoring already is — no second
long-lived process, no new watchdog/heartbeat. Founder chose this over the
two-process split and over leaping to the job queue. (The job queue, steps
3-5, will later replace these cron entries with queue consumers.)

## What moves

A new `*/15` cron entry runs the four compute stages; they are **removed
from the worker loop** (`run_once`). Scoring stays on its own `*/30` cron
(unchanged). Net effect: the worker loop becomes purely
discovery+enrich (Meta/producer); all corpus-scoring runs on cron cadence,
fresh regardless of how long the sweep takes.

Stage → new home:
- 1.5 velocity snapshot (`snapshot_advertiser_week`) — pure DB → compute cron
- 2a content embeddings + crowding (`embed_apps` + `refresh_crowding`,
  `frontend.app_embeddings`) — Gemini → compute cron
- 2b preference scoring (`score_all_users`) — pure DB → compute cron
- 2c candidate embeddings + crowding (`embed_candidates` +
  `refresh_crowding`, `discovery.candidate_embeddings`) — Gemini/iTunes →
  compute cron
- 2 scoring (`run_scoring`) — ALREADY on the `*/30` cron; **removed from the
  worker loop** (the redundant `score_after` path deleted).

## Design

### New module `src/agf/workers/compute_cycle.py`

```python
def run_compute_cycle(engine, http_client, as_of) -> dict:
    """The corpus-scoring stages, decoupled from the sweep and run on cron.
    Each stage is best-effort + isolated (a failure logs and never aborts the
    others) — identical contract to their old in-loop blocks. Order matters:
    velocity feeds the */30 scoring cron; app-embeddings feed preference."""
```

Runs, in order, each wrapped in try/except that logs and continues (mirroring
the current in-loop best-effort blocks verbatim):
1. `snapshot_advertiser_week(conn, week_start, only_ids=None)` (week_start =
   as_of - weekday()).
2. `embed_apps(conn, commit_every=25)` + `refresh_crowding(engine,
   table="frontend.app_embeddings")` (crowding only when embed added rows —
   see cost note).
3. `score_all_users(conn)`.
4. `embed_candidates(conn, itunes_client=http_client, commit_every=25,
   limit=AGF_TRIAGE_EMBED_PER_CYCLE)` + `refresh_crowding(engine,
   table="discovery.candidate_embeddings")`.

`__main__` block (so it runs as `python -m agf.workers.compute_cycle`):
build `engine = get_engine()`, an `httpx.Client(timeout=20)` for the iTunes
lookups in 2c, `as_of = date.today()`, call `run_compute_cycle`, print a
one-line summary. Mirror the `run_scoring` `__main__` pattern.

Cost note: `refresh_crowding` is a full leave-one-out recompute. At `*/15`
that's 4× its old hourly cadence. Guard each crowding call to run only when
its embed step reported new rows (`emb_stats`/`cstats` count > 0); an
all-cached cycle skips the recompute. Keeps the extraction faithful (same
call, just not on no-op cycles).

### `run_once` (`src/agf/workers/main.py`) — remove the compute stages

Delete the five blocks (1.5 at ~615-617, 2 at ~819, 2a at ~821-843, 2b at
~845-853, 2c at ~855-872) and:
- Drop the now-unused imports: `snapshot_advertiser_week` (131), `run_scoring`
  from line 128 **but keep `ALERT_THRESHOLD`** (still the `min_score`
  default), `embed_apps` (129), `score_all_users` (130).
- Remove the `score_after` param + `AGF_SCORE_AFTER_SWEEP` from `cfg`
  (`run_once` no longer scores). Update the help block (line 33).
- Return dict: drop `"scored"` and `"snapshot_weeks"`. Update the summary
  line builder (the `scored=`/`weekly_snapshots=` fragments, ~1048/1060).
- `refresh_crowding`/`embed_candidates`/`embed_apps`/`score_all_users` local
  imports inside the deleted blocks go with them.

The worker loop keeps: sweep+triage, metadata, app-refresh, keeper-history,
funnel, corpus-refresh, generic-deep, frame-mint, flywheel, enrich, repairs,
websub. Its heartbeat/watchdog/gates are unchanged.

### Cron + compose

- `ops/prod/crontab`: add
  `*/15 * * * *  cd /app && PYTHONPATH=/app/src /usr/local/bin/python3 -m agf.workers.compute_cycle >> /var/log/agf/compute.log 2>&1`
  The `cd /app` is load-bearing: `embed_apps`/`score_all_users` import from
  `scripts.*` (at `/app/scripts`), so `/app` must be on `sys.path` (the `-m`
  cwd entry) — same reason the run_scoring entry `cd /app` first. Absolute
  `/usr/local/bin/python3` (cron PATH omits it — the 2026-07-02 gotcha).
- `ops/prod/docker-compose.yml` `cron` service `environment:`: add
  `GEMINI_API_KEY: ${GEMINI_API_KEY}` (embeddings need it; the cron block
  currently has only `AGF_DATABASE_URL`). Also pass through the optional
  knobs with code defaults if convenient (`AGF_TRIAGE_EMBED_PER_CYCLE`,
  `AGF_EMBED_APPS`, `AGF_SCORE_PREFERENCES`, `AGF_TRIAGE_PRIOR`).
- Worker `environment:`: set `AGF_SCORE_AFTER_SWEEP` default to `0` (or drop
  the line — the worker no longer reads it). Keep for one deploy as `:-0` to
  be explicit, then it's dead.

## Concurrency / safety

The compute cron runs concurrently with the worker loop. All four stages
read what the worker produced and write disjoint tables the worker doesn't
touch (`frontend.app_embeddings`, `discovery.candidate_embeddings`,
`frontend.preference_scores`), except `snapshot_advertiser_week` →
`app_advertiser_metrics`, which keeper-history also upserts — row-level
ON CONFLICT upserts on mostly-different weeks, safe. Best-effort isolation
(try/except per stage) is preserved verbatim, and embeddings keep
`commit_every=25` + `engine.connect()` so a mid-run 429 persists progress.

## Test plan (RED→GREEN)

`tests/workers/test_compute_cycle.py` (new):
- `run_compute_cycle` invokes all four stages in order (spy/fake each).
- One stage raising does NOT abort the others (best-effort isolation) — the
  headline behavior; assert the later stages still ran and the failure was
  logged, not raised.
- crowding refresh is skipped when the embed step added zero rows, run when
  it added rows.

`tests/workers/test_main.py` (update): `run_once` no longer returns
`scored`/`snapshot_weeks`, no longer calls `run_scoring`/`embed_apps`/
`score_all_users`/`snapshot_advertiser_week`; the trimmed return dict + summary
line assertions updated. Dry-run/`--once` still completes.

## Live verification (controller-run, post-deploy)

Deploy (rebuild image — crontab + module are baked in; roll `worker` +
`cron`). Then:
- `docker compose exec cron python -m agf.workers.compute_cycle` once by hand
  → prints the summary, exits 0, no `scripts`/`GEMINI` import error.
- Confirm the `*/15` entry is installed (`crontab -l` in the cron container)
  and `/var/log/agf/compute.log` accrues runs.
- Confirm `frontend.preference_scores` / `app_embeddings` `updated_at` advance
  on the ~15-min cadence (independent of the sweep), and the worker loop no
  longer logs `scored=`/`weekly_snapshots=`.
- Worker + cron both `Up (healthy)`.

## Out of scope

The job queue (`pipeline_jobs` / Procrastinate, step 3) that will later
replace these cron entries with due-time consumers; chained enqueues (4);
per-item `next_sweep_at` (5). This step only relocates the compute stages to
cron; it adds no new scheduling primitive beyond a crontab line.
