from agf.parse import AdRecord
from agf.scale import aggregate_advertisers, AdvertiserScale, longevity_days


def _ad(page_id, archive, start, active=True):
    return AdRecord(archive, page_id, "FitGem", active, start, None, 1, "X", "x",
                    "https://apps.apple.com/us/app/x/id1", "b")


def test_longevity_days():
    # start 2026-05-05 (1746403200), now 2026-06-04 (1749000000) -> ~30 days
    assert longevity_days(1746403200, now_epoch=1749000000) == 30


def test_aggregate_counts_active_distinct_creatives():
    ads = [_ad("P1", "a1", 1746403200), _ad("P1", "a2", 1746403200),
           _ad("P1", "a1", 1746403200),  # duplicate archive id -> not double counted
           _ad("P2", "b1", 1748000000)]
    out = aggregate_advertisers(ads, now_epoch=1749000000)
    by_id = {a.page_id: a for a in out}
    assert by_id["P1"].active_creative_count == 2
    assert by_id["P2"].active_creative_count == 1
    assert isinstance(by_id["P1"], AdvertiserScale)


def test_max_longevity_is_oldest_active_creative():
    ads = [_ad("P1", "a1", 1746403200), _ad("P1", "a2", 1748000000)]
    out = aggregate_advertisers(ads, now_epoch=1749000000)
    assert out[0].max_longevity_days == 30
