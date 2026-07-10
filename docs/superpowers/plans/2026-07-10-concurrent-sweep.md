# Concurrent sweep — implementation plan

Spec: `docs/superpowers/specs/2026-07-10-concurrent-sweep-design.md`
Repo: TDD, Python data pipeline. `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it`
No migration (no schema change). No new dependencies.

Three sequential tasks. Each is RED→GREEN, committed separately.

---

## Task 1 — `SweepBreaker` + make `run_sweep`'s breaker shareable

**Files:** `src/agf/discovery/sweep.py`, `tests/discovery/test_sweep.py`

Goal: extract the local `consecutive_errors` int into a thread-safe
`SweepBreaker`, and let `run_sweep` accept an optional shared `breaker`.
Single-worker behaviour must be identical (existing 4 breaker tests stay
green unchanged).

### 1a. Add the breaker class (top of sweep.py, after imports)

```python
import threading

class SweepBreaker:
    """Thread-safe dead-proxy circuit breaker shared across concurrent sweep
    workers. `threshold` consecutive facade-exhausted errors ACROSS workers trip
    it; any worker's success resets the streak (so we abort only when the whole
    gateway looks down). threshold=0 disables (the default single-caller case)."""

    def __init__(self, threshold: int):
        self._threshold = threshold
        self._lock = threading.Lock()
        self._consecutive = 0
        self._tripped = False

    def record_error(self) -> bool:
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

### 1b. Thread `breaker` through `run_sweep`

Add `breaker: "SweepBreaker | None" = None` to the signature (after
`max_consecutive_errors`). At the top of the body, after the env defaults:

```python
    if breaker is None:
        breaker = SweepBreaker(max_consecutive_errors)
```

Replace the local `consecutive_errors = 0` line and its uses:

- Delete `consecutive_errors = 0` (before the `for entry` loop).
- At the top of the `for entry in entries:` loop body, add an early bail so a
  trip in another worker stops this one promptly:
  ```python
      if breaker.tripped():
          break
  ```
- In the `except ExtractError` block, replace:
  ```python
                  stats["errors"] += 1
                  consecutive_errors += 1
                  if max_consecutive_errors and consecutive_errors >= max_consecutive_errors:
                      raise ExtractError(
                          f"sweep circuit-breaker: {consecutive_errors} consecutive search "
                          f"errors — proxy/gateway likely down, aborting cycle",
                          ErrorKind.TRANSIENT)
  ```
  with:
  ```python
                  stats["errors"] += 1
                  if breaker.record_error():
                      raise ExtractError(
                          "sweep circuit-breaker: consecutive search errors across "
                          "workers — proxy/gateway likely down, aborting cycle",
                          ErrorKind.TRANSIENT)
  ```
- Replace the post-keyword reset `consecutive_errors = 0` (after `if errored:
  continue`) with `breaker.record_ok()`.

Everything else in `run_sweep` unchanged.

### 1c. Tests (add to test_sweep.py; keep the existing 4 breaker tests as-is)

```python
from agf.discovery.sweep import SweepBreaker  # add to imports

def test_breaker_trips_at_threshold_and_is_sticky():
    b = SweepBreaker(3)
    assert b.record_error() is False
    assert b.record_error() is False
    assert b.record_error() is True          # 3rd consecutive trips
    assert b.tripped() is True
    b.record_ok()                             # ok after trip does not un-trip
    assert b.tripped() is True

def test_breaker_resets_on_ok():
    b = SweepBreaker(3)
    b.record_error(); b.record_error()
    b.record_ok()
    assert b.record_error() is False          # streak reset, only 1 now
    assert b.tripped() is False

def test_breaker_disabled_at_zero():
    b = SweepBreaker(0)
    for _ in range(10):
        assert b.record_error() is False
    assert b.tripped() is False

def test_run_sweep_uses_injected_shared_breaker():
    # A breaker already near its threshold from a sibling worker trips this
    # sweep on its first error even though max_consecutive_errors is not passed.
    shared = SweepBreaker(2)
    shared.record_error()                     # sibling worker already errored once
    ext = _DeadProxyExtractor()
    with pytest.raises(ExtractError):
        asyncio.run(run_sweep(None, ext, entries=_entries(20), sleep=lambda s: None,
                              breaker=shared))
    assert ext.calls == 1                     # one more error -> 2 total -> trip
```

Run: `AGF_TEST_DB_URL=… pytest tests/discovery/test_sweep.py -q` → all green
(the 4 pre-existing breaker tests + 4 new).

---

## Task 2 — `run_parallel_sweep` orchestrator + stats merge + proxy partition

**Files:** `src/agf/discovery/sweep.py`, `src/agf/extract/proxy.py`,
`tests/discovery/test_parallel_sweep.py` (new)

### 2a. `ProxyPool.list_from_env` (proxy.py)

Refactor `from_env` to reuse a parser so the partition reads the list:

```python
    @staticmethod
    def list_from_env(var: str = "AGF_PROXIES") -> list[str]:
        raw = os.environ.get(var, "") or ""
        return [p.strip() for p in re.split(r"[\n,]+", raw) if p.strip()]

    @classmethod
    def from_env(cls, var: str = "AGF_PROXIES", **kw) -> "ProxyPool":
        return cls(cls.list_from_env(var), **kw)
```

### 2b. Orchestrator + merge (sweep.py, after `run_sweep`)

```python
from concurrent.futures import ThreadPoolExecutor

def _partition(items: list, k: int) -> list[list]:
    """Round-robin `items` into k shards (balanced counts; empty shards dropped)."""
    shards = [[] for _ in range(max(1, k))]
    for i, it in enumerate(items):
        shards[i % len(shards)].append(it)
    return [s for s in shards if s]

def _merge_sweep_stats(parts: list[dict]) -> dict:
    """Sum the per-worker stats dicts into one run_once/close_sweep_run-shaped dict."""
    merged = {"keywords_swept": 0, "serp_requests": 0, "raw_ads": 0,
              "candidates_new": 0, "candidates_seen": 0, "errors": 0,
              "funnel_new": 0, "funnel_seen": 0,
              "drops": {"no_link": 0, "social": 0, "non_app": 0,
                        "no_store_id": 0, "funnel_captured": 0}}
    has_page_log = any("page_log" in p for p in parts)
    if has_page_log:
        merged["page_log"] = []
    for p in parts:
        for key, val in p.items():
            if key == "drops":
                for dk, dv in val.items():
                    merged["drops"][dk] = merged["drops"].get(dk, 0) + dv
            elif key == "page_log":
                merged["page_log"].extend(val)
            else:
                merged[key] = merged.get(key, 0) + val
    return merged

def run_parallel_sweep(engine, extractors, *, entries=None, keywords=None,
                       countries=("US",), max_pages: int = 2, kw_delay: float = 3.0,
                       max_consecutive_errors: int = 0, heartbeat=None,
                       patience_pages: int | None = None, max_depth: int | None = None,
                       page_delay: float | None = None, track_pages: bool = False) -> dict:
    """Run the sweep across len(extractors) concurrent workers, each with its OWN
    extractor (own warm session + proxy). Entries are round-robin partitioned into
    disjoint shards; a single SweepBreaker is shared so a gateway-wide outage aborts
    the whole cycle. Returns one merged stats dict identical in shape to run_sweep's.

    Falls back to a single in-loop run_sweep when there is one extractor (or one
    shard), so the dry-run / small-keyword paths keep their exact behaviour."""
    if entries is None:
        entries = sweep_entries()
    if keywords is not None:
        entries = [e for e in entries if e.keyword in set(keywords)]
    shards = _partition(entries, len(extractors))
    breaker = SweepBreaker(max_consecutive_errors)

    def _run(extractor, shard):
        return asyncio.run(run_sweep(
            engine, extractor, entries=shard, countries=countries,
            max_pages=max_pages, kw_delay=kw_delay,
            max_consecutive_errors=max_consecutive_errors, breaker=breaker,
            heartbeat=heartbeat, patience_pages=patience_pages,
            max_depth=max_depth, page_delay=page_delay, track_pages=track_pages))

    if len(shards) <= 1:
        parts = [_run(extractors[0], shards[0])] if shards else []
    else:
        pairs = list(zip(extractors, shards))       # extractor i owns shard i
        with ThreadPoolExecutor(max_workers=len(pairs)) as pool:
            parts = list(pool.map(lambda pr: _run(pr[0], pr[1]), pairs))
    return _merge_sweep_stats(parts) if parts else _merge_sweep_stats([{}])
```

Note: `breaker` is passed to every worker's `run_sweep`, so
`max_consecutive_errors` is honoured by the shared breaker (each worker's
own `SweepBreaker(max_consecutive_errors)` is overridden by the injected one).

### 2c. Tests (`tests/discovery/test_parallel_sweep.py`, new)

Use `FakeExtractor` from `tests/discovery/conftest.py` (has `search_page`,
`script_pages`). Build per-keyword fakes and assert isolation + merge.

```python
import asyncio
import pytest
from agf.discovery.sweep import (run_parallel_sweep, _partition, _merge_sweep_stats,
                                 SweepBreaker)
from agf.discovery.taxonomy import TaxonomyEntry
from agf.extract.errors import ErrorKind, ExtractError
from tests.discovery.conftest import FakeExtractor

def _entries(n):
    return [TaxonomyEntry(f"kw{i}", "niche_term", "KEYWORD_UNORDERED", 2) for i in range(n)]

def test_partition_round_robin_disjoint_and_complete():
    items = list(range(7))
    shards = _partition(items, 3)
    assert [len(s) for s in shards] == [3, 2, 2]
    flat = sorted(x for s in shards for x in s)
    assert flat == items                      # union == all, disjoint

def test_partition_drops_empty_shards():
    assert _partition([1, 2], 3) == [[1], [2]]   # only 2 non-empty

def test_merge_sums_counters_drops_and_page_log():
    a = {"keywords_swept": 2, "errors": 1, "candidates_new": 3,
         "drops": {"no_link": 1, "social": 0, "non_app": 2,
                   "no_store_id": 0, "funnel_captured": 0}, "page_log": [{"keyword": "a"}]}
    b = {"keywords_swept": 1, "errors": 0, "candidates_new": 5,
         "drops": {"no_link": 0, "social": 3, "non_app": 0,
                   "no_store_id": 0, "funnel_captured": 1}, "page_log": [{"keyword": "b"}]}
    m = _merge_sweep_stats([a, b])
    assert m["keywords_swept"] == 3 and m["candidates_new"] == 8 and m["errors"] == 1
    assert m["drops"]["non_app"] == 2 and m["drops"]["social"] == 3
    assert m["drops"]["funnel_captured"] == 1
    assert len(m["page_log"]) == 2

def _fake_with(keyword_to_ads: dict) -> FakeExtractor:
    fe = FakeExtractor()
    fe.keyword_ads = keyword_to_ads
    return fe

def test_parallel_sweep_isolates_shards_per_extractor(_migrated_engine):
    # Each worker's fake records which keywords it was asked to search; assert no
    # keyword is served by more than one extractor (no cross-thread bleed).
    # Empty-returning fakes => no DB writes, so no sentinel cleanup needed.
    seen = [set(), set(), set()]
    lock = __import__("threading").Lock()
    class _Recording(FakeExtractor):
        def __init__(self, idx):
            super().__init__(); self._idx = idx
        async def search_page(self, *, query="", cursor=None, **kw):
            with lock:
                seen[self._idx].add(query)
            return [], None, False
    exts = [_Recording(i) for i in range(3)]
    run_parallel_sweep(_migrated_engine, exts, entries=_entries(9),
                       kw_delay=0, page_delay=0)
    union = seen[0] | seen[1] | seen[2]
    assert union == {f"kw{i}" for i in range(9)}
    assert seen[0] & seen[1] == set() and seen[1] & seen[2] == set()

_NOW = 1_749_000_000
def _sweep_ad(link, page_id):
    # AdRecord shaped like test_sweep.ad(); SWEEPTEST-prefixed page_id so the
    # sentinel cleanup convention (DELETE ... LIKE 'SWEEPTEST%') applies.
    from agf.parse import AdRecord
    return AdRecord("a1", page_id, "PSweep", True, _NOW - 5 * 86_400, None, 1,
                    "DOWNLOAD", "Install", link, "body", page_like_count=500)

def test_parallel_sweep_merges_candidate_counts(_migrated_engine):
    # Two keywords land distinct app candidates through different workers; merged
    # stats must sum both. Uses the real _process_page -> DB path, so it MUST use
    # SWEEPTEST sentinels + delete-before/after (mirrors test_run_sweep_persists).
    from sqlalchemy import text
    eng = _migrated_engine
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.candidates WHERE page_id LIKE 'SWEEPTEST%'"))
    try:
        a = _sweep_ad("https://apps.apple.com/us/app/x/id111", "SWEEPTESTP1")
        b = _sweep_ad("https://play.google.com/store/apps/details?id=com.b", "SWEEPTESTP2")
        exts = [_fake_with({"kw0": [a]}), _fake_with({"kw1": [b]})]
        stats = run_parallel_sweep(eng, exts, entries=_entries(2),
                                   kw_delay=0, page_delay=0)
        assert stats["candidates_new"] == 2 and stats["keywords_swept"] == 2
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.candidates WHERE page_id LIKE 'SWEEPTEST%'"))

def test_parallel_sweep_shared_breaker_aborts_all(_migrated_engine):
    class _Dead(FakeExtractor):
        async def search_page(self, *, query="", cursor=None, **kw):
            raise ExtractError("proxy down", ErrorKind.TRANSIENT)
    exts = [_Dead(), _Dead(), _Dead()]
    with pytest.raises(ExtractError):
        run_parallel_sweep(_migrated_engine, exts, entries=_entries(30),
                           kw_delay=0, page_delay=0, max_consecutive_errors=3)
```

`_migrated_engine` is the shared DB fixture used across
`tests/discovery/test_sweep.py` — reuse the same fixture import pattern
(check its definition in `tests/discovery/test_sweep.py` / `conftest.py` and
mirror it; if it lives in test_sweep.py, move/import appropriately or add a
local fixture that migrates the same way). Scope candidate writes to sentinel
page_ids and clean up if the sweep tests do.

Run: `AGF_TEST_DB_URL=… pytest tests/discovery/test_parallel_sweep.py -q`.

---

## Task 3 — Wire the production sweep to `run_parallel_sweep`

**Files:** `src/agf/workers/main.py`

### 3a. `build_extractors` (near `build_extractor`, ~main.py:158)

```python
def build_extractors(dry_run, now_epoch, k):
    """K independent extractors (own SessionPool + proxy each) for the concurrent
    sweep. Distinct proxy slices when AGF_PROXIES has >=k entries; otherwise each
    shares the full pool (DataImpulse rotates residential IPs per connection).
    Dry-run collapses to a single zero-network fake."""
    if dry_run:
        from agf.extract.fake import FakeExtractor
        return [FakeExtractor.demo(now_epoch)]
    from agf.extract.facade import Extractor
    from agf.extract.proxy import ProxyPool
    proxies = ProxyPool.list_from_env()
    k = max(1, k)
    if len(proxies) >= k and proxies:
        slices = [proxies[i::k] for i in range(k)]          # disjoint round-robin slices
        return [Extractor(proxies=ProxyPool(sl)) for sl in slices]
    # fewer proxies than workers (or none): each worker its own pool over the full list
    return [Extractor(proxies=ProxyPool(proxies)) for _ in range(k)]
```

### 3b. Route the production sweep (main.py ~561, the `else` non-dry-run branch)

Replace:
```python
                sweep_stats = asyncio.run(run_sweep(
                    engine, extractor, max_pages=sweep_pages, kw_delay=kw_delay,
                    max_consecutive_errors=sweep_max_consecutive_errors,
                    heartbeat=_touch_heartbeat))
```
with:
```python
                extractors = build_extractors(dry_run, now_epoch,
                                              _env_int("AGF_SWEEP_WORKERS", 3))
                sweep_stats = run_parallel_sweep(
                    engine, extractors, max_pages=sweep_pages, kw_delay=kw_delay,
                    max_consecutive_errors=sweep_max_consecutive_errors,
                    heartbeat=_touch_heartbeat)
```
(The dry-run branch just above keeps using `run_sweep` with the single
`extractor` — leave it untouched.)

Add `run_parallel_sweep` to the existing `from agf.discovery.sweep import ...`
line and import `_env_int` is already present. `build_extractors` is defined
in this module.

### 3c. Verify

- `python -c "import agf.workers.main"` (import clean).
- Full discovery suite: `AGF_TEST_DB_URL=… pytest tests/discovery -q`.
- `AGF_DRY_RUN=1 python -m agf.workers.main --once` still runs the offline
  single-fake path (dry-run untouched).

---

## Final gates

- `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it pytest -q`
  (full suite, bare run, 0 failed — a skip ≠ pass).
- Import + dry-run smoke as above.
- Live verify (post-deploy, controller-run): one real K=3 sweep in the
  deployed container; read `discovery.sweep_runs` errors + rd_challenge
  incidence vs K=1 baseline; confirm no challenge-rate spike before leaving
  K=3 on. Fall back to `AGF_SWEEP_WORKERS=2` if it spikes.
