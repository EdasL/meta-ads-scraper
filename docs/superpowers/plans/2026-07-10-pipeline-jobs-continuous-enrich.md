# Continuous enrich via pipeline_jobs — implementation plan

Spec: `docs/superpowers/specs/2026-07-10-pipeline-jobs-continuous-enrich-design.md`
TDD. `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it`.
Migration 152 (verify still free at build time — parallel session may claim it).
Shared `main` with a live session: re-read before each edit, stage explicit paths.

Four sequential tasks. Each RED→GREEN, committed separately.

---

## Task 1 — migration 152 + `pipeline_jobs` DB helpers

**Files:** `db/schema/152_pipeline_jobs.sql` (new), `src/agf/db/pipeline_jobs.py`
(new), `tests/db/test_pipeline_jobs.py` (new).

- Migration: the exact DDL from the spec (table + 3 indexes + autovacuum ALTER).
  VERIFY 152 is free (`ls db/schema/`); if taken, use the next number and update
  the filename. NO bare `%` anywhere (exec_driver_sql runner). Confirm it applies
  to a fresh DB — the existing `tests/db/test_migrations_placeholder_safe.py`
  guards `%`.
- `src/agf/db/pipeline_jobs.py` — the helpers from the spec:
  `enqueue_enrich(conn, advertiser_ids) -> int` (bulk insert in order, `ON CONFLICT
  (kind, advertiser_id) WHERE status IN ('queued','running') DO NOTHING`),
  `claim_next(conn, kind, lease_sec)`, `reap_expired(conn, kind)`,
  `set_job_status(conn, job_id, status, error=None)`, `purge_done(conn,
  older_than_days)`. Use `make_interval(secs => :lease)` for the lease, `text()`
  bound params throughout.
- Tests (use `_migrated_engine`, sentinel advertiser_ids like 999000001+ and
  DELETE-before/after cleanup — mirror test_candidates sentinel convention):
  - enqueue inserts N rows; re-enqueuing a still-queued advertiser is a no-op
    (active-uq dedup); enqueuing after it goes `done` inserts again.
  - claim_next returns oldest, flips to running, sets lease + attempts=1; a second
    concurrent claim (separate conn/txn) SKIP-LOCKs past it.
  - reap_expired: a running row with `lease_expires_at < now()` → requeued
    (attempts preserved); at `attempts >= max_attempts` → error.
  - purge_done deletes done rows older than N days, keeps recent + non-done.

Commit: `feat(queue): pipeline_jobs table + DB helpers (T1)`

---

## Task 2 — generalize `enrich_one` (status-table + mark-crawled)

**Files:** `src/agf/enrich_full/enrich_one.py`, `tests/enrich_full/test_enrich_one.py`.

Read enrich_one.py first. Add two CLI args, DEFAULTS keep on-demand byte-identical:
- `--status-table` (default `enrich_jobs`): the table `set_status` writes. Validate
  it's one of `{enrich_jobs, pipeline_jobs}` (avoid SQL injection — map to a literal,
  don't interpolate raw). Thread it into `set_status`/the terminal writes.
- `--mark-crawled` (store_true, default False): after a SUCCESSFUL enrich, call
  `mark_crawled(conn, "advertiser", advertiser_id)` (from `agf.workers.scheduler`)
  in the same/adjacent txn.
Tests:
- default invocation still writes `enrich_jobs`, does NOT mark crawl_log (on-demand
  unchanged) — assert against a faked enrich core.
- `--status-table pipeline_jobs` writes pipeline_jobs status.
- `--mark-crawled` calls mark_crawled on success, NOT on error.
Keep the SIGALRM/timeout + subprocess contract intact.

Commit: `feat(enrich): enrich_one --status-table + --mark-crawled for the pipeline queue (T2)`

---

## Task 3 — consumer daemon + `run_once` enqueue (replace the batch)

**Files:** `src/agf/enrich_full/pipeline_daemon.py` (new),
`src/agf/workers/main.py`, `tests/enrich_full/test_pipeline_daemon.py` (new),
`tests/workers/test_main.py`.

1. `pipeline_daemon.py` — mirror `queue_daemon.py` structure:
   `process_once(engine, *, runner=run_pipeline_subprocess)`:
   `reap_expired(conn,'enrich')` → `claim_next(conn,'enrich',lease)` →
   run `runner(advertiser_id, job_id)` → safety-net `set_job_status` if the row
   is still 'running'. `run_pipeline_subprocess` runs
   `python -m agf.enrich_full.enrich_one --advertiser-id A --job-id J
   --status-table pipeline_jobs --mark-crawled --timeout T` (subprocess, timeout
   `ENRICH_ONE_TIMEOUT_SEC+60`). `main()` loop polls `AGF_PIPELINE_POLL_SEC`
   (default 5); opportunistic `purge_done` (guard so it runs ~daily, not every tick).
   Lease sec = `ENRICH_ONE_TIMEOUT_SEC + 120`.
   Tests: process_once with a fake runner claims+completes a queued row (→done);
   a runner that raises → error (not stuck running); reap path covered in T1 so
   here just assert the daemon calls it.
2. `main.py` run_once — RE-READ first (shared). Replace stage 3 (the `cand_ids`
   build at ~L806-838, `prefetch_creative_urls` call, and `run_worker(...)` +
   `_touch_heartbeat`) with the enqueue step from the spec:
   - compute `kept` (taste_prioritized_kept with the existing FIFO-fallback
     try/except) + `eligible_gem_ids`, dedup, `claim_due(..., due_before, limit=
     enrich_limit)`, then `enqueue_enrich(conn, due)` in a write txn. Log
     `enqueued=N`.
   - Dry-run path: KEEP the inline enrich (so the offline `--once` e2e still
     exercises the full path) — gate the enqueue-vs-inline on `dry_run`.
   - Remove now-unused imports if nothing else uses them (`run_worker`,
     `prefetch_creative_urls` may still be used by dry-run/other stages — CHECK
     before removing; media_repair/funnel_repair still use `prefetch`? grep).
   - Return dict: `enriched`/`enrich_candidates` keys → replace with
     `enqueued` (or keep the keys meaningful for dry-run). Update `_summary_line`
     (`enriched=…/…`) to log `enqueued=N` in production, inline count in dry-run.
   - Update `tests/workers/test_main.py`: production run_once enqueues (assert
     pipeline_jobs rows for the due set, order, cap, dedup, TTL) and does NOT call
     run_worker; dry-run still enriches inline.

Commit: `feat(worker): enqueue enrich jobs instead of in-loop batch; pipeline consumer daemon (T3)`

---

## Task 4 — compose `pipeline` service (repo)

**File:** `ops/prod/docker-compose.yml`.

Add a `pipeline` service mirroring the on-demand `enrich` service shape (same
image + worker-class env: AGF_DATABASE_URL, AGF_PROXIES, GEMINI_API_KEY, S3_*,
AGF_FUNNEL_PROXY if used), `command: python -m agf.enrich_full.pipeline_daemon`,
`init: true`, `restart: unless-stopped`, `depends_on: cloud-sql-proxy`. (The VM's
drifted compose gets the same block by hand at deploy time — controller does that,
not this task.)

Commit: `ops(pipeline): compose service for the continuous enrich consumer (T4)`

---

## Final gates + live verify (controller)
- Full suite bare, 0 failed.
- Opus whole-branch review of the queue + enqueue + daemon (focus: selection/
  budget/TTL parity with the old batch; dedup correctness; lease/reaper races;
  enrich_one on-demand byte-identical).
- Apply migration 152 to prod; rebuild image; roll `worker`; add `pipeline`
  service to the VM compose (backup) + `up -d worker pipeline`.
- Live: pipeline_jobs enrich rows enqueue from a tick, drain queued→running→done,
  crawl_log advances, an enriched advertiser gets ads+score; on-demand Refresh
  still works; kill pipeline mid-job → reaper requeues. All services healthy.
