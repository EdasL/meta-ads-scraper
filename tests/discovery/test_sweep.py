"""Stage-1 sweep: SERP ad -> candidate fold rules, and persistence via FakeExtractor.
DB tests use SWEEPTEST* sentinels on the rolled-back pg_conn-style engine txn."""
import asyncio
from datetime import date

from sqlalchemy import text

from agf.discovery.sweep import classify_candidate, run_sweep
from agf.discovery.taxonomy import TaxonomyEntry
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
