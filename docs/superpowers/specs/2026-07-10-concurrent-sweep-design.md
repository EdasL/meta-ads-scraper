# Concurrent sweep (worker refactor, step 1) — design

Date: 2026-07-10
Status: spec (approved direction — founder endorsed the parent design)
Parent: `docs/research/2026-07-10-worker-architecture-refactor.md` §Migration path, step 1

## Goal

Fan the keyword sweep out over K=3 concurrent workers — one warm session +
one proxy per worker — inside the existing worker loop. No schema change,
no new dependencies, no scheduling change. This is the biggest single
wall-time win in the refactor with the smallest diff: today Meta scraping
concurrency is 1 (one warm session, one `search_page` at a time), and a
full cycle is ~40 min mostly of serial network waits.

## Constraints carried from the parent design

- **Per-keyword daily cadence is untouched.** This step changes only
  intra-sweep concurrency; the `discovery_due`/continuous-discovery gate,
  patience/max-depth paging, and per-keyword pacing all stay as-is. We do
  not re-sweep keywords more often (yield-collapse, 07-02 research).
- **Threads, not curl_cffi AsyncSession.** Each worker uses an independent
  synchronous curl_cffi session (its own `Extractor`/`SessionPool`), so the
  AsyncSession proxy-isolation bug (lexiforest/curl_cffi #41) cannot apply.
- **Start at K=3** (DataImpulse proven at 3-4 concurrent, memory
  `overnight-burst-300`); env-tunable, do not raise without measuring
  challenge/error rates live.

## Verified facts (from code, this session)

- `run_sweep` (`discovery/sweep.py:131`) is async, iterates `entries` one at
  a time; each `extractor.search_page` wraps a blocking curl in
  `asyncio.to_thread` (`facade.py:105`). Meta concurrency = 1.
- `Extractor()` (`facade.py:26`) builds its own `SessionPool` (one warm
  session, `session_pool.py:21`) from `ProxyPool.from_env()`
  (`proxy.py:20`). `AdLibrarySession` binds one proxy at construction
  (`tls_client.py`). It already accepts an injected `proxies=` pool.
- Circuit breaker is a local `consecutive_errors` int in `run_sweep`; raises
  `ExtractError` to abort the cycle after N consecutive facade-exhausted
  errors; resets on any successful keyword (`sweep.py:187-247`).
- DB writes are short per-page txns via `begin(engine)` (`sweep.py:102-113`).
  Engine uses default SQLAlchemy QueuePool (size 5 + overflow) — comfortably
  covers K=3 concurrent connections (`db/engine.py:19`).
- `_touch_heartbeat` writes `/tmp/agf-worker.heartbeat` (mtime only) — safe
  under concurrent touches from multiple workers.
- No schema change: engine pool and heartbeat already tolerate concurrency;
  confirmed no migration needed.

## Design

### Execution model — `ThreadPoolExecutor(K)`, one `asyncio.run` per worker

A new orchestrator `run_parallel_sweep` partitions the entries across K
workers and runs each shard in its own thread, each thread executing
`asyncio.run(run_sweep(engine, extractor_k, entries=shard_k, ...))` against
its **own** `Extractor`. This gives true parallelism of the whole sweep
(HTTP + classify + DB writes), matches the parent doc's "ThreadPoolExecutor
over the existing sync sessions", and is forward-compatible with the
eventual multi-process split. `run_sweep` itself is unchanged in shape (it
already sweeps a list of entries correctly); we only make its circuit
breaker shareable.

Why not `asyncio.gather` on one loop: it would parallelize only the curl
calls (Python logic stays on the loop thread), the same throughput but a
misleading "thread-safe" story. ThreadPoolExecutor keeps the concurrency
model honest and closest to the target architecture.

### Worker → proxy → session mapping

`build_extractors(dry_run, now_epoch, k)` builds K independent extractors:
- Parse `AGF_PROXIES` once. If `len(proxies) >= k`, give each extractor a
  `ProxyPool` over a **disjoint slice** (distinct gateways per worker). If
  `len(proxies) < k` (e.g. one DataImpulse gateway), each extractor gets a
  `ProxyPool` over the full list — they share the gateway string but each
  opens its own connection, and DataImpulse rotates residential IPs per
  connection (3-4 concurrent proven). Either way each worker has an
  independent `SessionPool` + curl session → no shared session across
  threads, distinct egress IPs.
- Add `ProxyPool.list_from_env()` classmethod so the partition reads the
  parsed list without reaching into private state.
- Dry-run returns a single `FakeExtractor.demo` (K collapses to 1).

### Shared circuit breaker (`SweepBreaker`)

A small thread-safe breaker replaces the local int so K workers share one
"is the whole gateway down" signal:

```python
class SweepBreaker:
    def __init__(self, threshold: int):   # 0 disables
        self._threshold = threshold
        self._lock = threading.Lock()
        self._consecutive = 0
        self._tripped = False
    def record_error(self) -> bool:       # True once tripped
        with self._lock:
            self._consecutive += 1
            if self._threshold and self._consecutive >= self._threshold:
                self._tripped = True
            return self._tripped
    def record_ok(self) -> None:
        with self._lock:
            self._consecutive = 0
    def tripped(self) -> bool:
        with self._lock:
            return self._tripped
```

`run_sweep` grows one optional param `breaker=None`; when None it builds
`SweepBreaker(max_consecutive_errors)` internally, so **every existing
caller and test is behaviourally identical** (single-worker: increment on
error, reset on success, raise at threshold, 0 disables). Inside the entry
loop: check `breaker.tripped()` first and stop early if another worker
tripped; on error call `record_error()` and raise if it returns True; on a
fully-paged keyword call `record_ok()`.

Shared semantics: 5 consecutive errors *across all workers* trip the
cycle; if any worker is still succeeding, its `record_ok()` resets the
streak — i.e. we abort only when the whole gateway looks down, which is the
correct "dead-proxy death-march" guard for the concurrent case.

### Stats merge

Each worker's `run_sweep` returns its own stats dict.
`_merge_sweep_stats(list)` sums the int counters, sums the nested `drops`
sub-counters, and concatenates `page_log` (when present). The merged dict
is byte-compatible with what `run_once`/`close_sweep_run` and the log line
already consume — no downstream change.

### Wiring (`workers/main.py`)

- Production sweep path (`main.py:561`) calls `run_parallel_sweep(engine,
  extractors, workers=K, max_consecutive_errors=..., kw_delay=...,
  heartbeat=_touch_heartbeat)` where `extractors = build_extractors(...)`.
- `K = AGF_SWEEP_WORKERS` (default 3).
- The **dry-run path stays on `run_sweep`** with the single fake extractor
  (K=1) — no behavioural change to `--dry-run`/`--once`.
- `build_extractor` (singular, used elsewhere) stays; `build_extractors`
  (plural) is added for the sweep.

## What could go wrong (and the guard)

- **Pool exhaustion**: K=3 < pool size 5 → fine; if K is raised above pool
  capacity, connections queue (pre_ping) rather than error. Note in the plan
  to bump `AGF_SWEEP_WORKERS` and pool together if ever going wide.
- **One worker's dead proxy**: isolated to its shard; the shared breaker
  only trips if *all* workers stall (no `record_ok`), which is the intended
  gateway-down signal.
- **Uneven shards**: round-robin partition balances keyword counts; depth is
  adaptive per keyword so wall-time is roughly balanced.
- **Heartbeat**: concurrent touches update mtime; watchdog reads mtime only.

## Test plan (RED→GREEN)

`tests/discovery/test_sweep.py` (breaker refactor — existing 4 breaker tests
must stay green unchanged) + new:
- `SweepBreaker`: trips at threshold, resets on ok, disabled at 0, tripped()
  is sticky.
- `run_sweep` with an injected shared `breaker`: two interleaved callers
  share the trip.

`tests/discovery/test_parallel_sweep.py` (new):
- Partition: N entries over K extractors → each extractor sees a disjoint
  round-robin shard, union == all entries.
- Concurrent run over K `FakeExtractor`s → merged stats equal the sum of
  per-worker stats (candidates_new, errors, drops, page_log).
- Shared breaker trips across workers: K dead-proxy extractors abort the
  whole parallel sweep (raises), not just one shard.
- Per-worker isolation: each `FakeExtractor` only ever receives its own
  shard's keywords (no cross-thread session/entry bleed).
- `ProxyPool` partition: ≥K proxies → disjoint slices; <K → full list each.

## Live verification (controller-run, post-merge)

Deploy to the worker VM, then in the deployed container run one real sweep
at K=3 and read from `discovery.sweep_runs` + logs:
- **challenge/error rate at K=3**: `stats["errors"]` and rd_challenge
  incidence vs a K=1 baseline — must not spike (the go/no-go for K=3).
- wall-clock of the sweep stage vs the ~single-threaded baseline (expect
  ~2-3x faster at the same keyword budget).
- candidates_new non-zero and triage flows as before.
If challenge rates climb at K=3, fall back to K=2 via `AGF_SWEEP_WORKERS`
before considering the container split (step 2).

## Explicitly out of scope (later steps)

Container split (step 2), `pipeline_jobs`/Procrastinate queue (step 3),
chained enqueues (step 4), per-item `next_sweep_at` scheduling (step 5).
This step leaves the sequential loop intact and only parallelises the sweep
stage within it.
