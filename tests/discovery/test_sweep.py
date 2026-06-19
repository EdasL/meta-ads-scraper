"""Stage-1 sweep: SERP ad -> candidate fold rules, and persistence via FakeExtractor.
DB tests use SWEEPTEST* sentinels on the rolled-back pg_conn-style engine txn."""
import asyncio
from datetime import date

import pytest
from sqlalchemy import text

from agf.discovery.sweep import classify_candidate, run_sweep
from agf.discovery.taxonomy import TaxonomyEntry
from agf.extract.errors import ErrorKind, ExtractError
from agf.parse import AdRecord
from tests.discovery.conftest import FakeExtractor

NOW = 1_749_000_000


def ad(link, likes=500, page_id="SWEEPTEST1", cta="DOWNLOAD"):
    return AdRecord("a1", page_id, "Sweep Test", True, NOW - 5 * 86_400, None, 1,
                    cta, "Install", link, "body text", page_like_count=likes)


# --- fold rules --------------------------------------------------------------
def test_ios_store_link_is_a_candidate():
    disposition, row = classify_candidate(ad("https://apps.apple.com/us/app/x/id12345"),
                                          keyword="pelvic floor app", technique="niche_term")
    assert disposition == "candidate"
    assert row["store"] == "ios" and row["store_id"] == "12345"
    assert row["page_id"] == "SWEEPTEST1" and row["page_like_count"] == 500
    assert row["source_keyword"] == "pelvic floor app"
    assert row["ad_start_date"] is not None


def test_android_store_link_is_a_candidate():
    d, row = classify_candidate(ad("https://play.google.com/store/apps/details?id=com.x.y"),
                                keyword="k", technique="t")
    assert d == "candidate" and row["store"] == "android" and row["store_id"] == "com.x.y"


def test_drops():
    cases = [
        (ad("https://example.com/landing"), "non_app"),          # web/funnel -> v2
        (ad("https://www.instagram.com/someone"), "social"),
        (ad("https://apps.apple.com/us/app/x/id12345", likes=10_001), "incumbent_likes"),
        (ad("https://apps.apple.com/us/app/broken"), "no_store_id"),
        (ad(None), "no_link"),
    ]
    for record, expected in cases:
        disposition, row = classify_candidate(record, keyword="k", technique="t")
        assert (disposition, row) == (expected, None)


def test_threshold_boundary_is_inclusive():
    d, _ = classify_candidate(ad("https://apps.apple.com/us/app/x/id1", likes=10_000),
                              keyword="k", technique="t")
    assert d == "candidate"                       # exactly 10k passes (veto is >, not >=)


def test_null_likes_pass():                       # NULL/unknown never vetoes (repo rule)
    d, _ = classify_candidate(ad("https://apps.apple.com/us/app/x/id1", likes=None),
                              keyword="k", technique="t")
    assert d == "candidate"


# --- persistence -------------------------------------------------------------
def test_run_sweep_persists_and_counts(_migrated_engine):
    eng = _migrated_engine
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.candidates WHERE page_id LIKE 'SWEEPTEST%'"))
    try:
        ext = FakeExtractor(keyword_ads={
            "pelvic floor app": [
                ad("https://apps.apple.com/us/app/x/id12345"),
                ad("https://www.instagram.com/x", page_id="SWEEPTEST2"),
            ],
            "menopause app": [
                ad("https://apps.apple.com/us/app/x/id12345"),        # dup -> bump
            ],
        })
        entries = [TaxonomyEntry("pelvic floor app", "niche_term", "KEYWORD_UNORDERED", 2),
                   TaxonomyEntry("menopause app", "niche_term", "KEYWORD_UNORDERED", 2)]
        stats = asyncio.run(run_sweep(eng, ext, entries=entries, sleep=lambda s: None))
        assert stats["keywords_swept"] == 2
        assert stats["candidates_new"] == 1
        assert stats["candidates_seen"] == 1            # the dup bumped
        assert stats["drops"]["social"] == 1
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT times_seen, status FROM discovery.candidates "
                "WHERE page_id='SWEEPTEST1' AND store_id='12345'")).one()
        assert row.times_seen == 2 and row.status == "new"
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.candidates WHERE page_id LIKE 'SWEEPTEST%'"))


# --- circuit-breaker (dead-proxy death-march guard) --------------------------
class _DeadProxyExtractor:
    """Every search raises — simulates a dead/throttling proxy where the facade has
    exhausted its retries+rotations for each keyword."""
    def __init__(self):
        self.calls = 0

    async def search(self, *, query="", **kw):
        self.calls += 1
        raise ExtractError("proxy down", ErrorKind.TRANSIENT)


def _entries(n):
    return [TaxonomyEntry(f"kw{i}", "niche_term", "KEYWORD_UNORDERED", 2) for i in range(n)]


def test_sweep_aborts_after_consecutive_errors():
    # A dead proxy must trip the breaker fast instead of grinding all 20 keywords; the
    # raise lets run_once fail the cycle and retry next tick (bounds the wasted Meta time).
    ext = _DeadProxyExtractor()
    with pytest.raises(ExtractError):
        asyncio.run(run_sweep(None, ext, entries=_entries(20), sleep=lambda s: None,
                              max_consecutive_errors=3))
    assert ext.calls == 3                     # stopped at the threshold, not all 20


def test_sweep_breaker_disabled_by_default():
    # Default (0) preserves the old behavior: every keyword error is swallowed, no raise.
    ext = _DeadProxyExtractor()
    stats = asyncio.run(run_sweep(None, ext, entries=_entries(4), sleep=lambda s: None))
    assert stats["errors"] == 4 and ext.calls == 4


def test_sweep_isolated_errors_do_not_trip_breaker():
    # Errors that never reach N-in-a-row (the consecutive counter resets on each success)
    # must not abort a healthy sweep that hits the odd transient throttle.
    class _Flaky:
        def __init__(self):
            self.n = 0

        async def search(self, *, query="", **kw):
            self.n += 1
            if self.n % 2 == 1:               # odd calls fail, even calls succeed
                raise ExtractError("blip", ErrorKind.TRANSIENT)
            return []

    stats = asyncio.run(run_sweep(None, _Flaky(), entries=_entries(8), sleep=lambda s: None,
                                  max_consecutive_errors=3))
    assert stats["errors"] == 4 and stats["keywords_swept"] == 4
