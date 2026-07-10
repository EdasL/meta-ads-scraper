# Compute-cron decouple — implementation plan

Spec: `docs/superpowers/specs/2026-07-10-compute-cron-decouple-design.md`
TDD. `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it`. No migration.
Shared `main` with a live parallel session — re-read before each edit, stage
explicit paths only.

Two sequential tasks.

---

## Task 1 — new `compute_cycle` module + tests

**Files:** `src/agf/workers/compute_cycle.py` (new),
`tests/workers/test_compute_cycle.py` (new).

Extract the four compute stages VERBATIM from `run_once` (main.py lines noted
in the spec) into one best-effort function. Read the current code at
`src/agf/workers/main.py:615-617` (velocity), `:821-843` (embeddings+crowding),
`:845-853` (preference), `:855-872` (candidate-embed) and preserve each block's
exact behavior (try/except logging, `commit_every=25`, `engine.connect()` +
`conn.commit()` progress-persist, env-flag gates `AGF_EMBED_APPS`,
`AGF_SCORE_PREFERENCES`, `AGF_TRIAGE_PRIOR`, `AGF_TRIAGE_EMBED_PER_CYCLE`).

```python
# src/agf/workers/compute_cycle.py
"""Corpus-scoring compute stages, decoupled from the sweep and run on the */15
cron (spec 2026-07-10). Moved out of the worker loop so Discover-feed /
review-queue freshness no longer waits behind the ~40-min discovery+enrich
cycle. Scoring itself stays on its own */30 run_scoring cron."""
import os
import sys
from datetime import date, timedelta

from agf.db.engine import get_engine, begin
from agf.tracker.snapshots import snapshot_advertiser_week
from agf.discovery.crowding_refresh import refresh_crowding


def run_compute_cycle(engine, http_client, as_of) -> dict:
    out = {"snapshot_weeks": 0, "embed": None, "preference": None, "candidate_embed": None}

    # 1) velocity snapshot — this week's active-ad counts into the weekly series
    #    the */30 scoring cron reads. Pure DB.
    try:
        week_start = as_of - timedelta(days=as_of.weekday())
        with begin(engine) as conn:
            out["snapshot_weeks"] = snapshot_advertiser_week(conn, week_start, only_ids=None)
    except Exception as exc:
        print(f"[compute] velocity snapshot failed: {exc!r}", file=sys.stderr, flush=True)

    # 2) content embeddings + crowding (frontend.app_embeddings). Gemini.
    if os.environ.get("AGF_EMBED_APPS", "1") != "0":
        try:
            from scripts.embed_apps import embed_apps
            with engine.connect() as conn:
                try:
                    emb = embed_apps(conn, commit_every=25)
                    conn.commit()
                except Exception:
                    conn.commit()
                    raise
            out["embed"] = emb
            if _added(emb):                     # skip the full recompute on a no-op cycle
                out["embed_crowding"] = refresh_crowding(engine, table="frontend.app_embeddings")
            print(f"[compute] embeddings: {emb} crowding={out.get('embed_crowding')}", flush=True)
        except Exception as exc:
            print(f"[compute] embedding stage failed: {exc!r}", file=sys.stderr, flush=True)

    # 3) preference re-scoring. Pure DB.
    if os.environ.get("AGF_SCORE_PREFERENCES", "1") != "0":
        try:
            from scripts.score_content_preferences import score_all_users
            with begin(engine) as conn:
                out["preference"] = score_all_users(conn)
            print(f"[compute] preference scoring: {out['preference']}", flush=True)
        except Exception as exc:
            print(f"[compute] preference scoring failed: {exc!r}", file=sys.stderr, flush=True)

    # 4) candidate embeddings + crowding (discovery.candidate_embeddings). Gemini/iTunes.
    if os.environ.get("AGF_TRIAGE_PRIOR", "1") != "0":
        try:
            from agf.discovery.candidate_embed import embed_candidates
            with engine.connect() as conn:
                cst = embed_candidates(conn, itunes_client=http_client, commit_every=25,
                                       limit=int(os.environ.get("AGF_TRIAGE_EMBED_PER_CYCLE", "200")))
                conn.commit()
            out["candidate_embed"] = cst
            if _added(cst):
                out["cand_crowding"] = refresh_crowding(engine, table="discovery.candidate_embeddings")
            print(f"[compute] candidate embeddings: {cst} crowding={out.get('cand_crowding')}", flush=True)
        except Exception as exc:
            print(f"[compute] candidate embed stage failed: {exc!r}", file=sys.stderr, flush=True)

    return out


def _added(stats) -> bool:
    """True if an embed step reported new rows (so crowding needs recompute).
    embed_apps/embed_candidates return a dict; be liberal about the count key."""
    if not stats:
        return False
    if isinstance(stats, dict):
        for k in ("embedded", "new", "added", "count", "n"):
            if stats.get(k):
                return True
        return False
    return bool(stats)


if __name__ == "__main__":
    import httpx
    engine = get_engine()
    with httpx.Client(timeout=20) as http_client:
        summary = run_compute_cycle(engine, http_client, date.today())
    print(f"[compute] cycle done: {summary}", flush=True)
```

IMPORTANT — verify the real return shapes before trusting `_added`: read
`scripts/embed_apps.py` and `agf/discovery/candidate_embed.py` for what
`embed_apps`/`embed_candidates` actually return, and key `_added` on the real
count field(s). If they return an int, handle that. The plan code is
illustrative — make `_added` match reality (RED test first).

### Tests (`tests/workers/test_compute_cycle.py`)

Use monkeypatch to replace the four stage callables with spies; assert:
- `test_runs_all_four_stages_in_order`: all four called, in order (record a
  call-order list).
- `test_one_stage_failure_does_not_abort_others`: make stage 2 (embeddings)
  raise; assert stages 3 and 4 STILL ran and the function returned (no raise).
  This is the headline decoupling-safety behavior.
- `test_crowding_skipped_when_no_new_embeddings`: embed spy returns a
  zero-count → `refresh_crowding` spy NOT called; nonzero → called.
Patch targets: patch where used, e.g.
`monkeypatch.setattr("agf.workers.compute_cycle.refresh_crowding", spy)` and
`monkeypatch.setattr("scripts.embed_apps.embed_apps", spy)` (import path the
module uses). No DB needed if all four are spied — keep it a pure unit test;
skip `_migrated_engine` unless a stage's real signature forces it.

Run: `AGF_TEST_DB_URL=… python -m pytest tests/workers/test_compute_cycle.py -q`.

Commit: `feat(worker): compute_cycle module — corpus-scoring stages for the cron (T1)`

---

## Task 2 — strip compute from `run_once` + wire cron/compose + fix tests

**Files:** `src/agf/workers/main.py`, `ops/prod/crontab`,
`ops/prod/docker-compose.yml`, `tests/workers/test_main.py`.

1. **main.py** — re-read the file first (shared repo). Delete the five compute
   blocks (velocity 1.5, scoring 2, embeddings 2a, preference 2b,
   candidate-embed 2c) per the spec's line map. Then:
   - Imports: drop `snapshot_advertiser_week` (131), `embed_apps` (129),
     `score_all_users` (130); change line 128 to import ONLY `ALERT_THRESHOLD`
     from `agf.scoring.run_scoring` (keep it — `min_score` default), drop
     `run_scoring`.
   - Remove `score_after` from the `run_once` signature and every use; remove
     `score_after=_env_bool("AGF_SCORE_AFTER_SWEEP")` from `cfg`.
   - Return dict: drop `"scored"` and `"snapshot_weeks"`.
   - Summary-line builder (~1048 `weekly_snapshots=`, ~1060 `scored=`): remove
     those fragments.
   - Help block line 33 (`AGF_SCORE_AFTER_SWEEP=1 …`): delete the line.
   - `now_epoch`/`as_of` still used by remaining stages — keep.
2. **ops/prod/crontab** — add after the run_scoring line:
   `*/15 * * * *  cd /app && PYTHONPATH=/app/src /usr/local/bin/python3 -m agf.workers.compute_cycle >> /var/log/agf/compute.log 2>&1`
3. **ops/prod/docker-compose.yml** — `cron` service `environment:` add
   `GEMINI_API_KEY: ${GEMINI_API_KEY}` (and optionally
   `AGF_TRIAGE_EMBED_PER_CYCLE: ${AGF_TRIAGE_EMBED_PER_CYCLE:-200}`). Worker
   `environment:` `AGF_SCORE_AFTER_SWEEP` line: delete it (worker no longer
   reads it).
4. **tests/workers/test_main.py** — re-read first. Update any assertion on
   `run_once`'s return (`scored`, `snapshot_weeks`) and any test that passed
   `score_after=` or asserted `run_scoring`/`embed_apps`/`score_all_users`/
   `snapshot_advertiser_week` were called from `run_once`. The
   `test_production_sweep_calls_run_parallel_sweep` and `build_extractors`
   tests are unaffected. If a test asserted the full compute path, move/retire
   it (the coverage now lives in test_compute_cycle.py).

### Verify
- `python -c "import agf.workers.main; import agf.workers.compute_cycle"` clean.
- `AGF_TEST_DB_URL=… python -m pytest tests/workers tests/discovery -q` green.
- `AGF_DRY_RUN=1 python -m agf.workers.main --once` completes; its summary line
  no longer contains `scored=`/`weekly_snapshots=`.
- FULL suite bare: `AGF_TEST_DB_URL=… python -m pytest -q` → 0 failed
  (grep for "skipped"; the MinIO skip is pre-existing).

Commit: `feat(worker): move compute stages out of run_once onto the */15 cron (T2)`

---

## Final gates + live verify (controller)
- Full suite bare, 0 failed.
- Deploy: rebuild image (crontab + module baked in), roll `worker` + `cron`.
- `docker compose exec cron python -m agf.workers.compute_cycle` → 0 exit, no
  `scripts`/GEMINI import error, prints summary.
- `crontab -l` in cron container shows the `*/15` entry; `/var/log/agf/compute.log`
  accrues; `preference_scores`/`app_embeddings` advance off-sweep-cadence; worker
  log drops `scored=`/`weekly_snapshots=`. Worker + cron `Up (healthy)`.
