import asyncio
from sqlalchemy import text

from agf.discovery.funnel_sweep import (classify_funnel_candidate, deep_funnel_entries,
                                        run_funnel_sweep)
from agf.discovery.taxonomy import TaxonomyEntry
from agf.parse import AdRecord
from tests.discovery.conftest import FakeExtractor

NOW = 1_749_000_000


def ad(link, likes=500, page_id="FUNSWEEP1", cta="LEARN_MORE", body="body"):
    return AdRecord("a1", page_id, "Funnel Sweep", True, NOW - 5 * 86_400, None, 1,
                    cta, "x", link, body, page_like_count=likes)


def test_web_funnel_is_a_candidate():
    d, row = classify_funnel_candidate(ad("https://quiz.example.com/start?utm_source=fb"),
                                       keyword="take the quiz", technique="funnel_phrase")
    assert d == "candidate"
    assert row["landing_domain"] == "quiz.example.com"
    assert row["funnel_host"] == "quiz." and row["utm_source"] == "fb"
    assert row["source_keyword"] == "take the quiz"


def test_funnel_drops():
    cases = [
        (ad("https://www.instagram.com/x"), "social"),
        (ad("https://quiz.example.com/x", likes=5_000_001), "incumbent_likes"),  # only mega-brands
        (ad(None), "no_link"),
        (ad("https://plainsite.com/x", cta="NO", body="nothing"), "not_funnel"),
    ]
    for record, expected in cases:
        d, row = classify_funnel_candidate(record, keyword="k", technique="niche_term")
        assert (d, row) == (expected, None)


def test_app_backed_funnel_is_bridged_to_app_pipeline():
    # A funnel-phrase ad whose link is a direct store URL is an app-backed funnel.
    # It's dropped from the funnel pipeline ('app') but carries an app-candidate row
    # so the sweep can route it into discovery.candidates for the normal app pipeline.
    d, row = classify_funnel_candidate(ad("https://apps.apple.com/us/app/x/id12345"),
                                       keyword="take the quiz", technique="funnel_phrase")
    assert d == "app"
    assert row is not None
    assert row["store"] == "ios" and row["store_id"] == "12345"
    assert row["technique"] == "funnel_sweep" and row["source_keyword"] == "take the quiz"


def test_store_link_without_parseable_id_is_not_bridged():
    # No store_id to resolve the app -> nothing to bridge.
    d, row = classify_funnel_candidate(ad("https://apps.apple.com/us/app/x"),
                                       keyword="take the quiz", technique="funnel_phrase")
    assert d == "app" and row is None


def test_large_page_funnel_is_kept():
    # funnels scale a big FB audience precisely because they WORK — a 50k-like funnel that
    # the emerging-app 10k veto would kill must still be a candidate here.
    d, row = classify_funnel_candidate(ad("https://quiz.example.com/x", likes=50_000),
                                       keyword="take the quiz", technique="funnel_phrase")
    assert d == "candidate" and row["landing_domain"] == "quiz.example.com"


def test_run_funnel_sweep_persists(_migrated_engine):
    eng = _migrated_engine
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.funnel_candidates WHERE page_id LIKE 'FUNSWEEP%'"))
        conn.execute(text("DELETE FROM discovery.candidates WHERE page_id LIKE 'FUNSWEEP%'"))
    try:
        ext = FakeExtractor(keyword_ads={
            "take the quiz": [
                ad("https://quiz.example.com/start"),
                ad("https://apps.apple.com/us/app/x/id1", page_id="FUNSWEEP2"),  # app -> bridged
            ],
            "personalized plan": [
                ad("https://quiz.example.com/start"),    # same page+domain -> bump
            ],
        })
        entries = [TaxonomyEntry("take the quiz", "funnel_phrase", "KEYWORD_EXACT_PHRASE", 3),
                   TaxonomyEntry("personalized plan", "funnel_phrase", "KEYWORD_EXACT_PHRASE", 3)]
        stats = asyncio.run(run_funnel_sweep(eng, ext, entries=entries, sleep=lambda s: None))
        assert stats["keywords_swept"] == 2
        assert stats["candidates_new"] == 1
        assert stats["candidates_seen"] == 1          # the dup bumped
        assert stats["drops"]["app"] == 1
        assert stats["bridged_app_new"] == 1          # app-backed funnel routed to app pool
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT times_seen, status, kind FROM discovery.funnel_candidates "
                "WHERE page_id='FUNSWEEP1' AND landing_domain='quiz.example.com'")).one()
            cand = conn.execute(text(
                "SELECT store, store_id, source_keyword, technique FROM discovery.candidates "
                "WHERE page_id='FUNSWEEP2'")).one()
        assert row.times_seen == 2 and row.status == "new" and row.kind == "unknown"
        assert cand.store == "ios" and cand.store_id == "1"
        assert cand.technique == "funnel_sweep" and cand.source_keyword == "take the quiz"
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.funnel_candidates WHERE page_id LIKE 'FUNSWEEP%'"))
            conn.execute(text("DELETE FROM discovery.candidates WHERE page_id LIKE 'FUNSWEEP%'"))


def test_run_funnel_sweep_heartbeat_and_page_log(_migrated_engine):
    with _migrated_engine.begin() as conn:
        conn.execute(text("DELETE FROM discovery.funnel_candidates WHERE page_id LIKE 'FUNDEEP%'"))
    try:
        beats = []
        ext = FakeExtractor(keyword_ads={"take the quiz": [
            ad("https://quiz.example.com/s", page_id="FUNDEEP1")]})
        entry = TaxonomyEntry("take the quiz", "funnel_phrase", "KEYWORD_EXACT_PHRASE", 100)
        stats = asyncio.run(run_funnel_sweep(
            _migrated_engine, ext, entries=[entry], max_pages=100, kw_delay=0,
            heartbeat=lambda: beats.append(1), track_pages=True))
        assert beats == [1]                                   # one beat per keyword
        assert stats["page_log"] == [
            {"keyword": "take the quiz", "pages": 1, "ads": 1, "ceiling_hit": False}]
    finally:
        with _migrated_engine.begin() as conn:
            conn.execute(text("DELETE FROM discovery.funnel_candidates WHERE page_id LIKE 'FUNDEEP%'"))


def test_deep_funnel_entries_raise_the_entry_ceiling():
    entries = deep_funnel_entries(100)
    assert len(entries) == 28
    assert all(e.layer == "funnel_phrase" and e.max_pages == 100 for e in entries)
    assert all(e.search_type == "KEYWORD_EXACT_PHRASE" for e in entries)
