"""Stage-1 sweep: SERP ad -> candidate fold rules, and persistence via FakeExtractor.
DB tests use SWEEPTEST* sentinels on the rolled-back pg_conn-style engine txn."""
import asyncio
from datetime import date

import pytest
from sqlalchemy import text

from agf.discovery.sweep import SweepBreaker, classify_candidate, run_sweep
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
        (ad("https://apps.apple.com/us/app/broken"), "no_store_id"),
        (ad(None), "no_link"),
    ]
    for record, expected in cases:
        disposition, row = classify_candidate(record, keyword="k", technique="t")
        assert (disposition, row) == (expected, None)


def test_page_likes_never_drop_an_app_candidate():
    # The 10k incumbent-likes ceiling was removed (user 2026-07-10): a big FB page
    # advertising a store app is a candidate; NULL/unknown likes also pass.
    for likes in (10_001, 5_000_000, None):
        d, _ = classify_candidate(ad("https://apps.apple.com/us/app/x/id1", likes=likes),
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
    # 200k likes on a web ad: under the 5M funnel ceiling -> captured.
    # 6M likes: over the funnel ceiling -> dropped as a plain non_app.
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
        assert stats["drops"]["non_app"] == 1
        with _migrated_engine.connect() as conn:
            got = conn.execute(text(
                "SELECT page_id FROM discovery.funnel_candidates "
                "WHERE page_id LIKE 'SWEEPFUN%' AND page_id IN ('SWEEPFUN3','SWEEPFUN4')")).all()
        assert [r.page_id for r in got] == ["SWEEPFUN3"]
    finally:
        _clear_funnel_sentinels(_migrated_engine)


# --- circuit-breaker (dead-proxy death-march guard) --------------------------
class _DeadProxyExtractor:
    """Every search_page raises — simulates a dead/throttling proxy where the facade has
    exhausted its retries+rotations for each keyword."""
    def __init__(self):
        self.calls = 0

    async def search_page(self, *, query="", cursor=None, **kw):
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
        async def search_page(self, *, query="", cursor=None, **kw):
            return [], None, False

    beats = []
    asyncio.run(run_sweep(None, _Empty(), entries=_entries(5), sleep=lambda s: None,
                          heartbeat=lambda: beats.append(1)))
    assert len(beats) == 5                    # one page per keyword -> one beat each


def test_sweep_heartbeat_optional():
    # No heartbeat callback (default) must not error — the experiment harness / tests
    # call run_sweep without one.
    class _Empty:
        async def search_page(self, *, query="", cursor=None, **kw):
            return [], None, False

    stats = asyncio.run(run_sweep(None, _Empty(), entries=_entries(3), sleep=lambda s: None))
    assert stats["keywords_swept"] == 3


def test_sweep_isolated_errors_do_not_trip_breaker():
    # Errors that never reach N-in-a-row (the consecutive counter resets on each success)
    # must not abort a healthy sweep that hits the odd transient throttle.
    class _Flaky:
        def __init__(self):
            self.n = 0

        async def search_page(self, *, query="", cursor=None, **kw):
            self.n += 1
            if self.n % 2 == 1:               # odd calls fail, even calls succeed
                raise ExtractError("blip", ErrorKind.TRANSIENT)
            return [], None, False

    stats = asyncio.run(run_sweep(None, _Flaky(), entries=_entries(8), sleep=lambda s: None,
                                  max_consecutive_errors=3))
    assert stats["errors"] == 4 and stats["keywords_swept"] == 4


# --- SweepBreaker (shareable circuit-breaker, spec 2026-07-10 concurrent) ----------
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


def test_breaker_per_worker_one_dead_slice_does_not_trip():
    # PER-WORKER (2026-07-10 review): with 2 workers, worker 0 erroring past the
    # threshold must NOT trip the cycle while worker 1 stays healthy — a partial
    # outage (one dead proxy slice) is isolated, not a whole-cycle abort.
    b = SweepBreaker(3, workers=2)
    assert b.record_error(0) is False
    assert b.record_error(0) is False
    assert b.record_error(0) is False         # worker 0 hit threshold, but worker 1 hasn't
    assert b.record_error(0) is False         # still only 1 of 2 workers down
    assert b.tripped() is False


def test_breaker_per_worker_trips_only_when_all_workers_fail():
    # Both workers reach the threshold -> the whole gateway looks down -> trip.
    b = SweepBreaker(3, workers=2)
    b.record_error(0); b.record_error(0); b.record_error(0)   # worker 0 at threshold
    assert b.tripped() is False
    assert b.record_error(1) is False
    assert b.record_error(1) is False
    assert b.record_error(1) is True          # worker 1 hits threshold too -> trip
    assert b.tripped() is True


def test_breaker_per_worker_success_resets_only_that_worker():
    # A worker's success clears only ITS streak, so a healthy worker keeps the
    # cycle alive even as a dead worker keeps erroring.
    b = SweepBreaker(3, workers=2)
    b.record_error(0); b.record_error(0); b.record_error(0)   # worker 0 at threshold
    b.record_error(1); b.record_error(1)
    b.record_ok(1)                            # worker 1 recovers -> its streak resets
    assert b.record_error(1) is False         # worker 1 back to 1, not at threshold
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


# --- adaptive-depth paging (patience window + safety cap + first_seen_depth) -------
from agf.db.candidates import upsert_sweep_candidate  # noqa: E402


def _cand(n, likes=500):
    """A distinct iOS candidate: page_id SWEEPAD<n>, store_id <n> (own row per n)."""
    return ad(f"https://apps.apple.com/us/app/x/id{n}", likes=likes, page_id=f"SWEEPAD{n}")


def _clear_adaptive(eng):
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.candidates WHERE page_id LIKE 'SWEEPAD%'"))


def _seed(eng, adrec, depth=1):
    """Pre-store a candidate at `depth` so a later sweep sees it as already-known."""
    _, row = classify_candidate(adrec, keyword="seed", technique="niche_term")
    row["first_seen_depth"] = depth
    with eng.begin() as conn:
        upsert_sweep_candidate(conn, row)


def _entry(kw):
    return TaxonomyEntry(kw, "niche_term", "KEYWORD_UNORDERED", 2)


def test_sweep_broad_keyword_pages_deep(_migrated_engine):
    # 6 pages, each a NEW candidate: the sweep fetches all 6 and tags first_seen_depth
    # with the 1-based page index it was found on.
    eng = _migrated_engine
    _clear_adaptive(eng)
    try:
        pages = [[_cand(100 + i)] for i in range(6)]
        ext = FakeExtractor().script_pages("broad kw", pages)
        stats = asyncio.run(run_sweep(eng, ext, entries=[_entry("broad kw")],
                                      sleep=lambda s: None, patience_pages=3, max_depth=10))
        assert stats["serp_requests"] == 6
        assert stats["candidates_new"] == 6
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT store_id, first_seen_depth FROM discovery.candidates "
                "WHERE page_id LIKE 'SWEEPAD%'")).all()
        depth_by = {r.store_id: r.first_seen_depth for r in rows}
        for i in range(6):
            assert depth_by[str(100 + i)] == i + 1
    finally:
        _clear_adaptive(eng)


def test_sweep_narrow_keyword_stops_at_page_one(_migrated_engine):
    # One page, has_next False: exactly one fetch, no runaway.
    eng = _migrated_engine
    _clear_adaptive(eng)
    try:
        ext = FakeExtractor().script_pages("narrow kw", [[_cand(200)]])
        stats = asyncio.run(run_sweep(eng, ext, entries=[_entry("narrow kw")],
                                      sleep=lambda s: None, patience_pages=3, max_depth=10))
        assert stats["serp_requests"] == 1
        assert stats["candidates_new"] == 1
    finally:
        _clear_adaptive(eng)


def test_sweep_patience_clears_a_known_prefix(_migrated_engine):
    # page1+page2 are already-stored candidates (0 net-new), page3 is NEW, pages4-6 known.
    # With patience_pages=3 the dry streak only reaches 2 across the prefix, so it MUST
    # push to page3 (store the new candidate) and then stop by page6.
    eng = _migrated_engine
    _clear_adaptive(eng)
    try:
        A, B, C = _cand(300), _cand(301), _cand(302)
        _seed(eng, A)
        _seed(eng, B)
        pages = [[A], [B], [C], [A], [B], [A]]
        ext = FakeExtractor().script_pages("prefix kw", pages)
        stats = asyncio.run(run_sweep(eng, ext, entries=[_entry("prefix kw")],
                                      sleep=lambda s: None, patience_pages=3, max_depth=10))
        assert stats["serp_requests"] == 6            # reached page 6, never bailed early
        assert stats["candidates_new"] == 1           # only C was net-new this sweep
        with eng.connect() as conn:
            got = conn.execute(text(
                "SELECT 1 FROM discovery.candidates WHERE page_id='SWEEPAD302'")).one_or_none()
        assert got is not None                        # the page-3 candidate got stored
    finally:
        _clear_adaptive(eng)


def test_sweep_all_dry_stops_at_patience(_migrated_engine):
    # Every page is already-known: stop after exactly patience_pages pages (not the full
    # scripted depth). has_next stays True through page 3, so the stop is patience, not
    # Meta exhaustion.
    eng = _migrated_engine
    _clear_adaptive(eng)
    try:
        A, B, D = _cand(310), _cand(311), _cand(312)
        _seed(eng, A)
        _seed(eng, B)
        _seed(eng, D)
        pages = [[A], [B], [D], [A], [B], [D]]
        ext = FakeExtractor().script_pages("dry kw", pages)
        stats = asyncio.run(run_sweep(eng, ext, entries=[_entry("dry kw")],
                                      sleep=lambda s: None, patience_pages=3, max_depth=10))
        assert stats["serp_requests"] == 3
        assert stats["candidates_new"] == 0
    finally:
        _clear_adaptive(eng)


def test_sweep_safety_cap_stops_at_max_depth(_migrated_engine):
    # 20 all-new pages but max_depth=5: stop at 5 regardless of how much is left.
    eng = _migrated_engine
    _clear_adaptive(eng)
    try:
        pages = [[_cand(400 + i)] for i in range(20)]
        ext = FakeExtractor().script_pages("cap kw", pages)
        stats = asyncio.run(run_sweep(eng, ext, entries=[_entry("cap kw")],
                                      sleep=lambda s: None, patience_pages=10, max_depth=5))
        assert stats["serp_requests"] == 5
        assert stats["candidates_new"] == 5
    finally:
        _clear_adaptive(eng)


def test_sweep_first_seen_depth_is_shallowest_across_resweeps(_migrated_engine):
    # A candidate first seen on page 4, then later on page 2, ends at depth 2 (LEAST).
    eng = _migrated_engine
    _clear_adaptive(eng)
    try:
        T = _cand(500)
        first = [[_cand(510)], [_cand(511)], [_cand(512)], [T]]      # T on page 4
        ext1 = FakeExtractor().script_pages("resweep a", first)
        asyncio.run(run_sweep(eng, ext1, entries=[_entry("resweep a")],
                              sleep=lambda s: None, patience_pages=10, max_depth=10))
        with eng.connect() as conn:
            assert conn.execute(text(
                "SELECT first_seen_depth FROM discovery.candidates "
                "WHERE page_id='SWEEPAD500'")).scalar_one() == 4
        second = [[_cand(520)], [T]]                                 # T on page 2
        ext2 = FakeExtractor().script_pages("resweep b", second)
        asyncio.run(run_sweep(eng, ext2, entries=[_entry("resweep b")],
                              sleep=lambda s: None, patience_pages=10, max_depth=10))
        with eng.connect() as conn:
            assert conn.execute(text(
                "SELECT first_seen_depth FROM discovery.candidates "
                "WHERE page_id='SWEEPAD500'")).scalar_one() == 2
    finally:
        _clear_adaptive(eng)


# --- collect callback + page tracking (generic-deep lane, spec 2026-07-10) ---------
def test_sweep_collect_receives_every_raw_ad():
    # Two pages, two ads each — collect sees all 4 AdRecords even though every ad
    # is a drop (social host), BEFORE any fold. No DB needed (engine=None is safe
    # because no candidate/funnel rows are produced).
    pages = [[ad("https://www.instagram.com/a", page_id="SWEEPCOL1"),
              ad("https://www.instagram.com/b", page_id="SWEEPCOL2")],
             [ad("https://www.instagram.com/c", page_id="SWEEPCOL3"),
              ad("https://www.instagram.com/d", page_id="SWEEPCOL4")]]
    ext = FakeExtractor().script_pages("collect kw", pages)
    got = []
    asyncio.run(run_sweep(None, ext, entries=[_entry("collect kw")],
                          sleep=lambda s: None, patience_pages=10, max_depth=10,
                          collect=got.append))
    assert [a.page_id for a in got] == ["SWEEPCOL1", "SWEEPCOL2",
                                        "SWEEPCOL3", "SWEEPCOL4"]


def test_sweep_track_pages_logs_depth_and_ceiling(_migrated_engine):
    # Keyword A: 6 all-new pages but max_depth=3 -> stopped AT the cap with more
    # pages left => ceiling_hit True, pages=3. Keyword B: 1 page, has_next False
    # => exhausted, ceiling_hit False.
    eng = _migrated_engine
    _clear_adaptive(eng)
    try:
        ext = (FakeExtractor()
               .script_pages("ceil kw", [[_cand(600 + i)] for i in range(6)])
               .script_pages("done kw", [[_cand(650)]]))
        stats = asyncio.run(run_sweep(
            eng, ext, entries=[_entry("ceil kw"), _entry("done kw")],
            sleep=lambda s: None, patience_pages=10, max_depth=3, track_pages=True))
        log = {r["keyword"]: r for r in stats["page_log"]}
        assert log["ceil kw"]["pages"] == 3 and log["ceil kw"]["ceiling_hit"] is True
        assert log["ceil kw"]["ads"] == 3
        assert log["done kw"]["pages"] == 1 and log["done kw"]["ceiling_hit"] is False
    finally:
        _clear_adaptive(eng)
