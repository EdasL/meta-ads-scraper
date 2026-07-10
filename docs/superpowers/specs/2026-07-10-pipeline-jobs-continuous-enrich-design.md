# Continuous enrich via pipeline_jobs queue (worker refactor, step 3)

Date: 2026-07-10
Status: spec (founder chose the continuous-enrich scope over doc's foundation-only step 3)
Parent: `docs/research/2026-07-10-worker-architecture-refactor.md` §Migration path, steps 3-4 (merged for the enrich workload)

## Goal

Move the worker's BATCH enrich off the sequential ~40-min tick and onto a
`pipeline_jobs` Postgres queue drained by a continuous consumer, so a
discovered gem gets enriched (and thus scored + surfaced) within minutes of
being kept, not once per day at the end of the loop. The worker tick stops
blocking on enrich — it just enqueues. The live on-demand "Refresh data"
flow (`enrich_jobs` + `queue_daemon`) is UNTOUCHED.

This is the doc's step 3 (the queue) merged with the enrich slice of step 4
(chained/periodic enqueue), because a queue with no consumer is unexercised
scaffolding — the founder chose the value-delivering shape.

## Verified facts (from the enrich-path map, this session)

- `enrich_one` (`enrich_full/enrich_one.py`) enriches ONE advertiser fully:
  self-prefetches creative URLs, `enrich_gem` (ad history + funnel + creatives
  + media), then `_default_score` (per-advertiser score + matview). Writes
  terminal status to `public.enrich_jobs` by `--job-id`. Runs as a subprocess
  from `queue_daemon` (crash isolation — a wedged render can't fell the daemon).
- The in-loop batch (`main.py` stage 3) and `enrich_one` share the SAME core
  `enrich_gem` (`enrich_full/run_enrich.py:190`). Difference: the batch marks
  `logging.crawl_log` per gem (via `run_worker`→`make_enrich_handler`), which is
  the TTL/dedup basis; `enrich_one` writes `enrich_jobs`, NOT `crawl_log`.
- Batch selection (must preserve): `cand_ids =
  dict.fromkeys(taste_prioritized_kept(conn, user, explore_frac=0.25,
  penalty=0.5) + eligible_gem_ids(conn, min_score))` (kept in taste order, then
  gems by `gem_score DESC`), TTL-filtered + capped by `claim_due(conn,
  "advertiser", cand_ids, due_before=now-enrich_ttl_hours, limit=enrich_limit)`,
  `enrich_limit = (enrich_budget - enrich_budget//2)//14`.
- `mark_crawled(conn, "advertiser", id, result)` and `claim_due(conn,
  entity_type, candidate_ids, due_before, limit)` live in `workers/scheduler.py`.
- Prod VM `/opt/agf/docker-compose.yml` (drifted) HAS an `enrich` service
  (on-demand `queue_daemon`); the repo `ops/prod/docker-compose.yml` does not.
  Next free migration = 152.

## Design

### `public.pipeline_jobs` (migration 152)

```sql
CREATE TABLE IF NOT EXISTS public.pipeline_jobs (
    id            bigserial PRIMARY KEY,
    kind          text        NOT NULL,               -- 'enrich' (future: score, embed, …)
    advertiser_id bigint,                              -- the entity (null-ok for future payload kinds)
    payload       jsonb       NOT NULL DEFAULT '{}',
    status        text        NOT NULL DEFAULT 'queued', -- queued|running|done|error
    run_after     timestamptz NOT NULL DEFAULT now(),
    attempts      int         NOT NULL DEFAULT 0,
    max_attempts  int         NOT NULL DEFAULT 3,
    lease_expires_at timestamptz,
    error_text    text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    claimed_at    timestamptz,
    finished_at   timestamptz
);
CREATE INDEX IF NOT EXISTS pipeline_jobs_claim_idx
    ON public.pipeline_jobs (kind, run_after) WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS pipeline_jobs_lease_idx
    ON public.pipeline_jobs (lease_expires_at) WHERE status = 'running';
-- at most ONE active job per (kind, advertiser) — the enqueue-dedup arbiter
CREATE UNIQUE INDEX IF NOT EXISTS pipeline_jobs_active_uq
    ON public.pipeline_jobs (kind, advertiser_id) WHERE status IN ('queued','running');
-- churn table: keep autovacuum aggressive so the partial indexes don't bloat
ALTER TABLE public.pipeline_jobs SET (autovacuum_vacuum_scale_factor = 0.02,
                                      autovacuum_analyze_scale_factor = 0.02);
```
(Migration is exec_driver_sql'd with no params — NO bare `%`; use `make_interval`
not `%` anywhere. Lesson from migr 147.)

### DB helpers (`src/agf/db/pipeline_jobs.py`, new)

- `enqueue_enrich(conn, advertiser_ids) -> int`: bulk-insert `kind='enrich'`
  rows in the given order, `ON CONFLICT (kind, advertiser_id) WHERE status IN
  ('queued','running') DO NOTHING` (the active-uq dedup). Returns count newly
  enqueued. Order = insertion order = FIFO drain order = taste priority.
- `claim_next(conn, kind, lease_sec) -> (id, advertiser_id) | None`: SKIP LOCKED
  claim of the oldest due `queued` row, set `status='running',
  attempts=attempts+1, claimed_at=now(), lease_expires_at=now()+lease`.
- `reap_expired(conn, kind) -> int`: `running` rows past `lease_expires_at` →
  back to `queued` (or `error` if `attempts >= max_attempts`), clear the lease.
  This is the daemon-crash recovery the on-demand daemon lacks.
- `set_job_status(conn, id, status, error=None)`: terminal write.
- `purge_done(conn, older_than_days) -> int`: delete `done` rows past N days
  (bloat control; called opportunistically by the daemon).

### Enqueue step (replaces the batch in `run_once`)

Delete the in-loop batch (main.py stage 3: `cand_ids` build, `prefetch_creative_urls`,
`run_worker(...)`). Replace with a fast enqueue (no Meta traffic in the tick):

```python
# 3) enqueue enrich jobs — the continuous `pipeline` consumer drains them.
if not dry_run:
    with engine.connect() as conn:
        kept = _taste_or_fifo(conn, ...)            # taste_prioritized_kept w/ FIFO fallback
        cand_ids = list(dict.fromkeys(kept + eligible_gem_ids(conn, min_score)))
        due = claim_due(conn, "advertiser", cand_ids,
                        due_before=datetime.now(tz=utc) - timedelta(hours=enrich_ttl_hours),
                        limit=enrich_limit)          # SAME selection/budget/TTL as the batch
    with begin(engine) as conn:
        enqueued = enqueue_enrich(conn, due)         # deduped vs in-flight jobs
```
Selection/order/budget/TTL are computed EXACTLY as the batch did (reusing
`taste_prioritized_kept`/`eligible_gem_ids`/`claim_due`), so the queue enriches
the same set in the same order under the same budget — only the *execution* is
decoupled and continuous. `mark_crawled` still happens (in the consumer, below),
so an advertiser enriched this cycle is TTL-excluded from the next enqueue.
Dry-run path unchanged (still enriches inline so the offline e2e stays whole).

### Consumer (`src/agf/enrich_full/pipeline_daemon.py`, new)

Mirrors `queue_daemon` structurally, adds lease/reaper, drains `kind='enrich'`:
`process_once` = `reap_expired` → `claim_next` → run enrich as a SUBPROCESS
(crash isolation, same reason as on-demand) → safety-net status write. Poll
`AGF_PIPELINE_POLL_SEC` (default 5). Opportunistic `purge_done` daily.

Enrich subprocess reuses `enrich_one`, GENERALIZED minimally:
- add `--status-table {enrich_jobs|pipeline_jobs}` (default `enrich_jobs` →
  on-demand unchanged) so `set_status` writes the right table (both have
  `status/error_text/finished_at` columns);
- add `--mark-crawled` flag → after a successful enrich, call
  `mark_crawled(conn, "advertiser", advertiser_id)` so the TTL/dedup basis
  updates (the batch did this; on-demand does not and stays that way).
The pipeline consumer runs `enrich_one --status-table pipeline_jobs
--mark-crawled --job-id <pipeline id> --advertiser-id <aid>`.

Lease > the enrich_one wall-clock (`AGF_ENRICH_ONE_TIMEOUT_SEC` default 180 +
grace), so a still-running job is never reaped out from under itself.

### Compose (`ops/prod/docker-compose.yml` + the drifted VM copy)

Add a `pipeline` service (same image/env as `worker` — it needs the Meta
extractor + proxies + Gemini + S3 for enrich): `command: python -m
agf.enrich_full.pipeline_daemon`, `init: true`, `restart: unless-stopped`.
Mirror the on-demand `enrich` service block. Edit BOTH the repo compose and
`/opt/agf/docker-compose.yml` on the VM (drifted; backup first).

## Behaviors preserved (checklist from the map)

1. Candidate set = `dict.fromkeys(taste_prioritized_kept + eligible_gem_ids)`. ✓ (enqueue)
2. Ordering = taste keepers, then gems by score desc → FIFO drain. ✓
3. `enrich_limit` budget cap. ✓ (enqueue `claim_due` limit)
4. TTL `due_before` dedup of already-enriched. ✓ (enqueue `claim_due`)
5. `crawl_log` mark per enrich. ✓ (consumer `--mark-crawled`)
6. Single-claim. ✓ (`FOR UPDATE SKIP LOCKED` replaces the pid mutex)
7. Per-advertiser txn isolation + failure recorded, never aborts others. ✓ (per-job)
8. Crash isolation of a wedged render. ✓ (subprocess) + daemon-crash recovery. ✓ (lease/reaper — NEW, better than the batch)

## Test plan (RED→GREEN)

- `tests/db/test_pipeline_jobs.py`: enqueue dedups vs an in-flight job (active-uq);
  claim_next is SKIP-LOCKED single-claim + sets lease; reap_expired requeues a
  lease-expired running row and errors it at max_attempts; purge_done deletes only
  old done rows. Migration applies to a fresh DB (placeholder-safe lint already
  covers `%`).
- `tests/workers/test_main.py`: run_once now ENQUEUES (asserts pipeline_jobs rows
  for the due set in taste order, capped to enrich_limit, TTL-filtered) and no
  longer calls `run_worker`/`prefetch_creative_urls` in the loop; dry-run still
  enriches inline.
- `tests/enrich_full/test_pipeline_daemon.py`: process_once claims + runs the
  (faked) subprocess + writes terminal status; a runner failure → error, not stuck.
- `tests/enrich_full/test_enrich_one.py`: `--status-table pipeline_jobs` writes
  pipeline_jobs; `--mark-crawled` calls mark_crawled; defaults unchanged
  (enrich_jobs, no crawl_log) so on-demand is byte-identical.

## Live verification (controller, post-deploy)

Migration 152 applied to prod first. Rebuild image, roll `worker` + add
`pipeline` service. Then:
- Enqueue fires from a worker tick (or trigger once): `SELECT kind,status,count(*)
  FROM pipeline_jobs GROUP BY 1,2` shows `enrich/queued` rows.
- `pipeline` daemon drains them: watch rows go `queued→running→done`, and
  `logging.crawl_log` advance for those advertisers; the enriched advertisers
  get ads + a score.
- On-demand "Refresh data" still works (enrich_jobs untouched) — one live click.
- Kill the pipeline container mid-job → the reaper requeues the leased job (no
  stuck `running`). Worker + pipeline `Up (healthy)`.

## Out of scope (steps 4-5)

Chaining OTHER stages (triage/score/embed) onto pipeline_jobs; per-item
`next_sweep_at` scheduling; replacing the compute cron with queue consumers;
LISTEN/NOTIFY wakeups; adopting Procrastinate (revisit when multiple kinds +
periodic tasks make its features pay off — the hand-rolled table is
Procrastinate-shaped, so migration stays cheap).
