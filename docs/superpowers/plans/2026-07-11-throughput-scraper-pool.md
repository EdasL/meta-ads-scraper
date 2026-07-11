# Throughput-first scraper pool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

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

- [ ] Write failing tests proving that a free lane claims a new job, continuation pages
  stay on their original lane, and a page is requeued only when its cursor remains live.
- [ ] Run `.venv/bin/pytest -q tests/discovery/test_page_pool.py`; expect import/API
  failures before implementation.
- [ ] Implement per-lane ready heaps guarded by a condition variable. A job contains
  `key`, `payload`, `cursor`, `depth`, `ready_at`, and `lane_id`. Process at most one
  job per lane; call the page handler before deciding whether to requeue; stop only
  after every job is complete or recorded as failed.
- [ ] Run the focused scheduler tests and confirm they pass.
- [ ] Commit with `git add src/agf/discovery/page_pool.py tests/discovery/test_page_pool.py`
  and `git commit -m "feat(scraper): add dynamic proxy lane page pool"`.

### Task 2: Integrate dynamic scheduling into keyword sweeps

**Files:** Modify `src/agf/discovery/sweep.py:333-370` and
`tests/discovery/test_parallel_sweep.py`.

**Produces:** Existing `run_parallel_sweep()` signature and stats shape; one extractor
keeps the serial path, while two or more use `run_page_pool()`.

- [ ] Write failing tests for dynamic reuse of a fast lane, cursor/depth preservation,
  and candidate/stat merging using existing `SWEEPTEST` fixtures.
- [ ] Run `.venv/bin/pytest -q tests/discovery/test_parallel_sweep.py -k "dynamically or preserves_depth"`;
  verify the load-balancing test fails against fixed shards.
- [ ] For each `TaxonomyEntry`, create an initial `PageJob` with `cursor=None` and
  `depth=0`. Fetch through `asyncio.run(extractor.search_page(query=..., countries=...,
  search_type=..., cursor=...))`. Reuse `_process_page()` for persistence and update
  `pages_fetched`, `kw_ads`, `consec_dry`, `page_log`, and aggregate counters.
- [ ] Requeue only when `has_next`, cursor, patience, and depth rules allow it. Keep
  continuation jobs lane-affine. Use lane id as the existing `SweepBreaker.worker_id`.
  Preserve error counting and keyword-delay behavior without marking failed keywords
  complete.
- [ ] Run `.venv/bin/pytest -q tests/discovery/test_parallel_sweep.py`; all existing
  breaker, persistence, and stats tests must pass.
- [ ] Commit with `git add src/agf/discovery/sweep.py tests/discovery/test_parallel_sweep.py`
  and `git commit -m "feat(scraper): schedule sweep pages dynamically"`.

### Task 3: Add throughput telemetry without a schema migration

**Files:** Modify `src/agf/discovery/page_pool.py`, `src/agf/discovery/sweep.py`, and
`src/agf/workers/main.py:1077-1098`; create `tests/discovery/test_sweep_telemetry.py`.

**Produces:** `stats["telemetry"]` with `pages_completed`, `request_seconds`,
`queue_wait_seconds`, `lane_pages`, `lane_errors`, and `peak_ready_jobs`.

- [ ] Write a failing test asserting telemetry exists for a two-lane fake run while
  `serp_requests` and existing counters remain unchanged.
- [ ] Run `.venv/bin/pytest -q tests/discovery/test_sweep_telemetry.py`; verify failure.
- [ ] Measure fetch, queue wait, and persistence with `time.perf_counter()`. Merge lane
  metrics with existing stats. Expose only lane indexes or one-way proxy hashes, never
  proxy credentials.
- [ ] Run `.venv/bin/pytest -q tests/discovery/test_sweep_telemetry.py tests/discovery/test_parallel_sweep.py tests/workers/test_main.py`.
- [ ] Commit with explicit paths and message `feat(scraper): expose sweep throughput telemetry`.

### Task 4: Bounded benchmark and live verification

**Files:** Create `scripts/benchmark_sweep_pool.py`; modify `docs/scraping-overview.md`.

**Produces:** A bounded command accepting `--keywords`, `--pages`, and `--workers`,
using the real `build_extractors()`/`run_parallel_sweep()` path and printing JSON with
elapsed time, pages, errors, telemetry, and candidate counts.

- [ ] Add a failing CLI test or parser test for required arguments and missing-credential
  refusal, then implement the bounded benchmark without invoking the full worker loop.
- [ ] Document comparison at identical keywords/depth/proxy set: pages/hour, latency,
  challenge/error rate, and candidates per 1,000 requests. Worker count must not exceed
  distinct proxy endpoints.
- [ ] Run `PYTHONPATH=src .venv/bin/python -m compileall -q src scripts/benchmark_sweep_pool.py`.
- [ ] Run the selected focused suite plus `git diff --check` as separate commands.
- [ ] When credentials are available, run a real proxy-backed sample and compare against
  the old fixed-shard baseline. Do not claim throughput improvement if safety metrics
  regress or cursors are lost.
- [ ] Commit the benchmark and documentation with explicit paths.

## Deferred follow-up

After Phase 1 demonstrates higher pages/hour at stable safety metrics, create a separate
plan for typed priority queues across flywheel, corpus, funnel, and generic deep work,
then a separate plan for durable Postgres queue consumers and compute-lane separation.
Those phases reuse the `PageJob` and fetcher contracts.
