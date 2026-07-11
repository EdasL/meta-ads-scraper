"""Concurrent K-worker sweep (spec 2026-07-10): _partition/_merge pure functions and
the run_parallel_sweep orchestrator. DB tests use SWEEPTEST* sentinels (shared session
DB) with delete-before/after, mirroring test_sweep.test_run_sweep_persists_and_counts."""
from threading import Barrier, Event, Lock, Thread
import pytest
import agf.discovery.sweep as sweep_module
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


def test_parallel_sweep_dynamically_reuses_fast_lane():
    first_calls = Barrier(2)
    release_slow = Event()
    fast_calls = []
    fast_lock = Lock()

    class _Slow(FakeExtractor):
        async def search_page(self, *, query="", cursor=None, **kw):
            first_calls.wait(timeout=1)
            assert release_slow.wait(timeout=1)
            return [], None, False

    class _Fast(FakeExtractor):
        async def search_page(self, *, query="", cursor=None, **kw):
            with fast_lock:
                fast_calls.append(query)
                call_count = len(fast_calls)
            if call_count == 1:
                first_calls.wait(timeout=1)
            else:
                release_slow.set()
            return [], None, False

    stats = run_parallel_sweep(
        None,
        [_Slow(), _Fast()],
        entries=_entries(3),
        kw_delay=0,
        page_delay=0,
    )

    assert len(fast_calls) == 2
    assert release_slow.is_set()
    assert stats["keywords_swept"] == 3
    assert stats["serp_requests"] == 3


def test_parallel_sweep_cancels_pool_when_extractor_raises_unexpected_error():
    first_calls = Barrier(2)
    failure_raised = Event()
    claimed_after_failure = Event()
    release_healthy = Event()
    errors = []
    failing_calls = []

    class _Failing(FakeExtractor):
        async def search_page(self, *, query="", cursor=None, **kw):
            failing_calls.append(query)
            if len(failing_calls) == 1:
                first_calls.wait(timeout=1)
                failure_raised.set()
                raise RuntimeError("persistence failed")
            claimed_after_failure.set()
            return [], None, False

    class _Healthy(FakeExtractor):
        async def search_page(self, *, query="", cursor=None, **kw):
            first_calls.wait(timeout=1)
            assert release_healthy.wait(timeout=1)
            return [], None, False

    def _run_sweep():
        try:
            run_parallel_sweep(
                None,
                [_Failing(), _Healthy()],
                entries=_entries(3),
                kw_delay=0,
                page_delay=0,
            )
        except Exception as error:
            errors.append(error)

    sweep_thread = Thread(target=_run_sweep)
    sweep_thread.start()
    assert failure_raised.wait(timeout=1)
    failed_lane_claimed_again = claimed_after_failure.wait(timeout=0.2)
    release_healthy.set()
    sweep_thread.join(timeout=1)

    assert not sweep_thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)
    assert not failed_lane_claimed_again
    assert len(failing_calls) == 1


def test_parallel_sweep_preserves_depth_cursor_and_lane(monkeypatch):
    calls = []
    processed = []

    class _Paged(FakeExtractor):
        def __init__(self, lane_id):
            super().__init__()
            self.lane_id = lane_id

        async def search_page(self, *, query="", cursor=None, **kw):
            calls.append((self.lane_id, query, cursor))
            if query != "kw0":
                return [], None, False
            if cursor is None:
                return ["page-1"], "cursor-2", True
            if cursor == "cursor-2":
                return ["page-2"], "cursor-3", True
            return ["page-3"], None, False

    def _record_page(_engine, records, *, entry, depth, stats):
        processed.append((entry.keyword, depth, list(records)))
        stats["raw_ads"] += len(records)
        return len(records)

    monkeypatch.setattr(sweep_module, "_process_page", _record_page)
    stats = run_parallel_sweep(
        None,
        [_Paged(0), _Paged(1)],
        entries=_entries(2),
        kw_delay=0,
        page_delay=0,
        patience_pages=3,
        max_depth=3,
        track_pages=True,
    )

    kw0_calls = [call for call in calls if call[1] == "kw0"]
    assert [cursor for _lane, _query, cursor in kw0_calls] == [
        None,
        "cursor-2",
        "cursor-3",
    ]
    assert len({lane for lane, _query, _cursor in kw0_calls}) == 1
    assert [depth for keyword, depth, _records in processed if keyword == "kw0"] == [
        1,
        2,
        3,
    ]
    assert stats["keywords_swept"] == 2
    assert stats["serp_requests"] == 4
    assert stats["raw_ads"] == 3
    page_log = {row["keyword"]: row for row in stats["page_log"]}
    assert page_log == {
        "kw0": {"keyword": "kw0", "pages": 3, "ads": 3, "ceiling_hit": False},
        "kw1": {"keyword": "kw1", "pages": 1, "ads": 0, "ceiling_hit": False},
    }


def test_parallel_sweep_keeps_each_keyword_on_one_extractor():
    # Each lane's fake records which keywords it was asked to search; assert no
    # keyword continuation crosses extractors (no cross-thread bleed).
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
    run_parallel_sweep(None, exts, entries=_entries(9),
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
    # Two dynamically assigned keywords land distinct app candidates, including one
    # on page 2. Merged stats and persisted depth must survive the real _process_page
    # DB path, so this MUST use SWEEPTEST sentinels + delete-before/after.
    from sqlalchemy import text
    eng = _migrated_engine
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.candidates WHERE page_id LIKE 'SWEEPTEST%'"))
    try:
        a = _sweep_ad("https://apps.apple.com/us/app/x/id111", "SWEEPTESTP1")
        b = _sweep_ad("https://play.google.com/store/apps/details?id=com.b", "SWEEPTESTP2")
        exts = [
            FakeExtractor()
            .script_pages("kw0", [[], [a]])
            .script_pages("kw1", [[b]])
            for _ in range(2)
        ]
        stats = run_parallel_sweep(eng, exts, entries=_entries(2),
                                   kw_delay=0, page_delay=0, patience_pages=3,
                                   max_depth=3, track_pages=True)
        assert stats["candidates_new"] == 2
        assert stats["keywords_swept"] == 2
        assert stats["serp_requests"] == 3
        assert stats["raw_ads"] == 2
        page_log = {row["keyword"]: row for row in stats["page_log"]}
        assert page_log["kw0"]["pages"] == 2
        assert page_log["kw1"]["pages"] == 1
        with eng.connect() as conn:
            depths = dict(conn.execute(text(
                "SELECT page_id, first_seen_depth FROM discovery.candidates "
                "WHERE page_id LIKE 'SWEEPTEST%'"
            )).all())
        assert depths == {"SWEEPTESTP1": 2, "SWEEPTESTP2": 1}
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.candidates WHERE page_id LIKE 'SWEEPTEST%'"))


def test_parallel_sweep_shared_breaker_aborts_all():
    rounds = Barrier(3)

    class _Dead(FakeExtractor):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def search_page(self, *, query="", cursor=None, **kw):
            self.calls += 1
            if self.calls <= 3:
                rounds.wait(timeout=1)
            raise ExtractError("proxy down", ErrorKind.TRANSIENT)

    exts = [_Dead(), _Dead(), _Dead()]
    with pytest.raises(ExtractError):
        run_parallel_sweep(None, exts, entries=_entries(30),
                           kw_delay=0, page_delay=0, max_consecutive_errors=3)


def test_parallel_sweep_one_dead_worker_does_not_abort():
    # PER-LANE breaker: one dead proxy lane erroring past the threshold must NOT
    # false-abort the cycle while the other lane is healthy. The breaker trips only
    # when ALL active lanes stall.
    started = Barrier(2)

    class _Dead(FakeExtractor):
        def __init__(self):
            super().__init__()
            self.first = True

        async def search_page(self, *, query="", cursor=None, **kw):
            if self.first:
                self.first = False
                started.wait(timeout=1)
            raise ExtractError("proxy down", ErrorKind.TRANSIENT)

    class _Healthy(FakeExtractor):
        def __init__(self):
            super().__init__()
            self.first = True

        async def search_page(self, *, query="", cursor=None, **kw):
            if self.first:
                self.first = False
                started.wait(timeout=1)
            return [], None, False

    exts = [_Dead(), _Healthy()]
    stats = run_parallel_sweep(None, exts, entries=_entries(20),
                               kw_delay=0, page_delay=0, max_consecutive_errors=3)
    assert stats["keywords_swept"] > 0
    assert stats["errors"] > 0
    assert stats["keywords_swept"] + stats["errors"] == 20
