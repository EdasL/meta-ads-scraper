import asyncio
from sqlalchemy import text

from agf.discovery.funnel_sweep import classify_funnel_candidate, run_funnel_sweep
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
        (ad("https://apps.apple.com/us/app/x/id12345"), "app"),           # store link -> app pipeline
        (ad("https://www.instagram.com/x"), "social"),
        (ad("https://quiz.example.com/x", likes=5_000_001), "incumbent_likes"),  # only mega-brands
        (ad(None), "no_link"),
        (ad("https://plainsite.com/x", cta="NO", body="nothing"), "not_funnel"),
    ]
    for record, expected in cases:
        d, row = classify_funnel_candidate(record, keyword="k", technique="niche_term")
        assert (d, row) == (expected, None)


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
    try:
        ext = FakeExtractor(keyword_ads={
            "take the quiz": [
                ad("https://quiz.example.com/start"),
                ad("https://apps.apple.com/us/app/x/id1", page_id="FUNSWEEP2"),  # app -> drop
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
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT times_seen, status, kind FROM discovery.funnel_candidates "
                "WHERE page_id='FUNSWEEP1' AND landing_domain='quiz.example.com'")).one()
        assert row.times_seen == 2 and row.status == "new" and row.kind == "unknown"
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.funnel_candidates WHERE page_id LIKE 'FUNSWEEP%'"))
