# Throughput-first scraper pool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Replace fixed keyword shards with a dynamic, proxy-isolated page scheduler that increases completed Meta pages per hour without reducing pagination depth or keyword coverage.

**Architecture:** Add a generic scheduler with one lane per extractor/proxy. Initial keyword jobs are assigned dynamically; continuation pages remain lane-affine so their cursor and warm session stay together. Each lane processes one page at a time, requeues continuations after pacing, and claims new work when a keyword finishes. Existing persistence, adaptive depth, patience, and breaker behavior remain in `sweep.py`.

**Tech Stack:** Python 3.13, `threading`, `queue`, `heapq`, curl_cffi-backed extractors, pytest, SQLAlchemy fixtures.

## Global Constraints

- Preserve keyword count, page ceilings, patience, lifecycle, and `first_seen_depth` semantics.
- One active Meta request per extractor/proxy lane; never multiply workers over one endpoint.
- Never hold a database transaction during network I/O.
- No new Redis/Kafka/Celery dependency in this phase.
- Use TDD: failing test, verify failure, minimal implementation, green verification.
- Commit each task separately with explicit paths.

---

### Task 1: Dynamic lane scheduler primitive

**Files:** Create `src/agf/discovery/page_pool.py` and `tests/discovery/test_page_pool.py`.

**Produces:** `PageJob`, `PageOutcome`, and `run_page_pool()`; the scheduler is generic
and does not import `sweep.py`.

- [x] Write failing tests proving that a free lane claims a new job, continuation pages
  stay on their original lane, and a page is requeued only when its cursor remains live.
- [x] Run `.venv/bin/pytest -q tests/discovery/test_page_pool.py`; expect import/API
  failures before implementation.
- [x] Implement per-lane ready heaps guarded by a condition variable. A job contains
  `key`, `payload`, `cursor`, `depth`, `ready_at`, and `lane_id`. Process at most one
  job per lane; call the page handler before deciding whether to requeue; stop only
  after every job is complete or recorded as failed.
- [x] Run the focused scheduler tests and confirm they pass.
- [x] Commit with `git add src/agf/discovery/page_pool.py tests/discovery/test_page_pool.py`
  and `git commit -m "feat(scraper): add dynamic proxy lane page pool"`.

### Task 2: Integrate dynamic scheduling into keyword sweeps

**Files:** Modify `src/agf/discovery/sweep.py:333-370` and
`tests/discovery/test_parallel_sweep.py`.

**Produces:** Existing `run_parallel_sweep()` signature and stats shape; one extractor
keeps the serial path, while two or more use `run_page_pool()`.

- [x] Write failing tests for dynamic reuse of a fast lane, cursor/depth preservation,
  and candidate/stat merging using existing `SWEEPTEST` fixtures.
- [x] Run `.venv/bin/pytest -q tests/discovery/test_parallel_sweep.py -k "dynamically or preserves_depth"`;
  verify the load-balancing test fails against fixed shards.
- [x] For each `TaxonomyEntry`, create an initial `PageJob` with `cursor=None` and
  `depth=0`. Fetch through `asyncio.run(extractor.search_page(query=..., countries=...,
  search_type=..., cursor=...))`. Reuse `_process_page()` for persistence and update
  `pages_fetched`, `kw_ads`, `consec_dry`, `page_log`, and aggregate counters.
- [x] Requeue only when `has_next`, cursor, patience, and depth rules allow it. Keep
  continuation jobs lane-affine. Use lane id as the existing `SweepBreaker.worker_id`.
  Preserve error counting and keyword-delay behavior without marking failed keywords
  complete.
- [x] Run `.venv/bin/pytest -q tests/discovery/test_parallel_sweep.py`; all existing
  breaker, persistence, and stats tests must pass.
- [x] Commit with `git add src/agf/discovery/sweep.py tests/discovery/test_parallel_sweep.py`
  and `git commit -m "feat(scraper): schedule sweep pages dynamically"`.

### Task 3: Add throughput telemetry without a schema migration

**Files:** Modify `src/agf/discovery/page_pool.py`, `src/agf/discovery/sweep.py`, and
`src/agf/workers/main.py:1077-1098`; create `tests/discovery/test_sweep_telemetry.py`.

**Produces:** `stats["telemetry"]` with `pages_completed`, `request_seconds`,
`queue_wait_seconds`, `lane_pages`, `lane_errors`, and `peak_ready_jobs`.

- [x] Write a failing test asserting telemetry exists for a two-lane fake run while
  `serp_requests` and existing counters remain unchanged.
- [x] Run `.venv/bin/pytest -q tests/discovery/test_sweep_telemetry.py`; verify failure.
- [x] Measure fetch, queue wait, and persistence with `time.perf_counter()`. Merge lane
  metrics with existing stats. Expose only lane indexes or one-way proxy hashes, never
  proxy credentials.
- [x] Run `.venv/bin/pytest -q tests/discovery/test_sweep_telemetry.py tests/discovery/test_parallel_sweep.py tests/workers/test_main.py`.
- [x] Commit with explicit paths and message `feat(scraper): expose sweep throughput telemetry`.

### Task 4: Bounded benchmark and live verification

**Files:** Create `scripts/benchmark_sweep_pool.py`; modify `docs/scraping-overview.md`.

**Produces:** A bounded command accepting `--keywords`, `--pages`, and `--workers`,
using the real `build_extractors()`/`run_parallel_sweep()` path and printing JSON with
elapsed time, pages, errors, telemetry, and candidate counts.

- [x] Add a failing CLI test or parser test for required arguments and missing-credential
  refusal, then implement the bounded benchmark without invoking the full worker loop.
- [x] Document comparison at identical keywords/depth/proxy set: pages/hour, latency,
  challenge/error rate, and candidates per 1,000 requests. Worker count must not exceed
  distinct proxy endpoints.
- [x] Run `PYTHONPATH=src .venv/bin/python -m compileall -q src scripts/benchmark_sweep_pool.py`.
- [x] Run the selected focused suite plus `git diff --check` as separate commands.
- [x] When credentials are available, run a real proxy-backed sample and compare against
  the old fixed-shard baseline. Do not claim throughput improvement if safety metrics
  regress or cursors are lost.
- [x] Commit the benchmark and documentation with explicit paths.

## Deferred follow-up

After Phase 1 demonstrates higher pages/hour at stable safety metrics, create a separate
plan for typed priority queues across flywheel, corpus, funnel, and generic deep work,
then a separate plan for durable Postgres queue consumers and compute-lane separation.
Those phases reuse the `PageJob` and fetcher contracts.

## Execution record (2026-07-11)

Tasks 1-2 implemented and committed by the Codex session (`6ec5fdb`..`f41c068`);
Task 3 finished by its agent but committed by Claude after the Codex usage limit
killed the session (`d3be760`); Task 4 by Claude (`c8e670c`). One committed test
bug fixed (`e3287ca`: dict(CursorResult) — the authoring env had no test DB, so
the DB test skipped silently). Full suite 852 passed / 0 failed.

Live verification (bounded, on agf-worker via a throwaway container from the
current image + bind-mounted new code; prod proxies + prod DB): 6 keywords x 3
pages, identical coverage both runs (18 pages, 177 raw ads, 48 candidates, 0
errors). Serial baseline 69.3s (936 pages/hour); dynamic 3-lane pool 42.5s
(1,527 pages/hour, lanes balanced 6/6/6) — 1.63x on a set this small; the tail
effect shrinks on real ~300-keyword passes. Watch: mean request latency rose
~1.5s -> ~5.2s under 3-way concurrency on the shared DataImpulse gateway;
throughput still net-positive, re-check on a full pass.

NOT deployed: the dynamic scheduler activates on the next worker image roll,
which must be a conscious decision (compare the first full cycle's summary-line
telemetry against the shard-era cycle times).
