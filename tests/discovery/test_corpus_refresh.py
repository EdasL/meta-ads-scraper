"""Corpus ad-refresh: search_listing truncation flag, whole-corpus selection,
vanished-ad flip, and the budgeted refresh loop. CORPUSREF* sentinels."""
import asyncio
from tests.discovery.conftest import FakeExtractor, make_ad


def test_search_listing_returns_ads_and_truncation_flag():
    ads = [make_ad("cr-a1", "CORPUSREF1", "Adv", "https://x/app")]
    ext = FakeExtractor(page_ads={"CORPUSREF1": ads},
                        page_truncated={"CORPUSREF1": True})
    got, truncated = asyncio.run(ext.search_listing(page_id="CORPUSREF1", max_pages=12))
    assert [a.ad_archive_id for a in got] == ["cr-a1"]
    assert truncated is True
    # unknown page → empty, not truncated
    empty, trunc2 = asyncio.run(ext.search_listing(page_id="NOPE", max_pages=12))
    assert empty == [] and trunc2 is False
