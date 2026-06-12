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
        (ad("https://quiz.example.com/x", likes=10_001), "incumbent_likes"),
        (ad(None), "no_link"),
        (ad("https://plainsite.com/x", cta="NO", body="nothing"), "not_funnel"),
    ]
    for record, expected in cases:
        d, row = classify_funnel_candidate(record, keyword="k", technique="niche_term")
        assert (d, row) == (expected, None)
