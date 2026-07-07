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


# --- web-funnel capture (app sweep folds its web rejects) --------------------
def _web_ad(link, likes=500, page_id="SWEEPFUN1", cta="LEARN_MORE", body="body"):
    return AdRecord("a1", page_id, "Sweep Funnel", True, NOW - 5 * 86_400, None, 1,
                    cta, "x", link, body, page_like_count=likes)


def _clear_funnel_sentinels(eng):
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.candidates WHERE page_id LIKE 'SWEEPFUN%'"))
        conn.execute(text(
            "DELETE FROM discovery.funnel_candidates WHERE page_id LIKE 'SWEEPFUN%'"))


def test_sweep_captures_web_funnel(_migrated_engine):
    _clear_funnel_sentinels(_migrated_engine)
    try:
        ext = FakeExtractor(keyword_ads={"hair loss app": [
            ad("https://apps.apple.com/us/app/x/id111", page_id="SWEEPFUN_APP"),
            _web_ad("https://quiz.example.com/start", page_id="SWEEPFUN1"),
            _web_ad("https://plainsite.com/x", cta="NO", body="nothing", page_id="SWEEPFUN2"),
        ]})
        entry = TaxonomyEntry("hair loss app", "niche_term", "KEYWORD_UNORDERED", 2)
        stats = asyncio.run(run_sweep(_migrated_engine, ext, entries=[entry], kw_delay=0))
        assert stats["candidates_new"] == 1                    # the store ad
        assert stats["funnel_new"] == 1 and stats["funnel_seen"] == 0
        assert stats["drops"]["funnel_captured"] == 1
        assert stats["drops"]["non_app"] == 1                  # plainsite: no signal
        with _migrated_engine.connect() as conn:
            row = conn.execute(text(
                "SELECT source_keyword, technique, landing_domain "
                "FROM discovery.funnel_candidates WHERE page_id = 'SWEEPFUN1'")).one()
        assert row.source_keyword == "hair loss app"
        assert row.technique == "niche_term"
        assert row.landing_domain == "quiz.example.com"
    finally:
        _clear_funnel_sentinels(_migrated_engine)


def test_sweep_funnel_capture_tolerates_big_pages(_migrated_engine):
    # 200k likes: app pipeline vetoes (>10k) but the funnel ceiling is 5M -> captured.
    # 6M likes: over the funnel ceiling too -> stays an incumbent_likes drop.
    _clear_funnel_sentinels(_migrated_engine)
    try:
        ext = FakeExtractor(keyword_ads={"meal plan app": [
            _web_ad("https://quiz.bigbrand.com/q", likes=200_000, page_id="SWEEPFUN3"),
            _web_ad("https://quiz.megabrand.com/q", likes=6_000_000, page_id="SWEEPFUN4"),
        ]})
        entry = TaxonomyEntry("meal plan app", "niche_term", "KEYWORD_UNORDERED", 2)
        stats = asyncio.run(run_sweep(_migrated_engine, ext, entries=[entry], kw_delay=0))
        assert stats["funnel_new"] == 1
        assert stats["drops"]["funnel_captured"] == 1
        assert stats["drops"]["incumbent_likes"] == 1
        with _migrated_engine.connect() as conn:
            got = conn.execute(text(
                "SELECT page_id FROM discovery.funnel_candidates "
                "WHERE page_id LIKE 'SWEEPFUN%' AND page_id IN ('SWEEPFUN3','SWEEPFUN4')")).all()
        assert [r.page_id for r in got] == ["SWEEPFUN3"]
    finally:
        _clear_funnel_sentinels(_migrated_engine)


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


def test_sweep_touches_heartbeat_each_keyword():
    # The sweep is the long stage; the worker watchdog kills on a stale heartbeat. A
    # progressing-but-slow sweep (hundreds of keywords) must touch the heartbeat as it
    # goes, or the watchdog kills it mid-sweep before triage ever runs.
    class _Empty:
        async def search(self, *, query="", **kw):
            return []

    beats = []
    asyncio.run(run_sweep(None, _Empty(), entries=_entries(5), sleep=lambda s: None,
                          heartbeat=lambda: beats.append(1)))
    assert len(beats) == 5                    # one per swept keyword


def test_sweep_heartbeat_optional():
    # No heartbeat callback (default) must not error — the experiment harness / tests
    # call run_sweep without one.
    class _Empty:
        async def search(self, *, query="", **kw):
            return []

    stats = asyncio.run(run_sweep(None, _Empty(), entries=_entries(3), sleep=lambda s: None))
    assert stats["keywords_swept"] == 3


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
