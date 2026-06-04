import asyncio
from agf.discovery.firehose_eu import run_eu_impressions_firehose, collapse_to_advertisers
from tests.discovery.conftest import FakeExtractor, make_ad, NOW_EPOCH


def test_collapse_drops_short_running():
    ads = [make_ad("a1", "P1", "GemA", "https://apps.apple.com/us/app/x/id1", days_old=30),
           make_ad("a2", "P2", "GemB", "https://apps.apple.com/us/app/y/id2", days_old=5)]
    cands = collapse_to_advertisers(ads, NOW_EPOCH, min_running_days=25)
    assert {c.page_id for c in cands} == {"P1"}          # P2 (5d) dropped
    assert all(c.kind == "page" and c.source == "firehose_eu" for c in cands)


def test_run_eu_firehose_sweeps_each_country():
    ex = FakeExtractor(eu_ads=[make_ad("a1", "P1", "GemA", "https://x.com", days_old=30)])
    ads = asyncio.run(run_eu_impressions_firehose(ex, countries=("DE", "GB")))
    assert len(ads) == 2                                 # one sweep per country
