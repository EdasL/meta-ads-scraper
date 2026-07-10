"""Concurrent K-worker sweep (spec 2026-07-10): _partition/_merge pure functions and
the run_parallel_sweep orchestrator. DB tests use SWEEPTEST* sentinels (shared session
DB) with delete-before/after, mirroring test_sweep.test_run_sweep_persists_and_counts."""
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
