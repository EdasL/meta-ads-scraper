from agf.parse import AdRecord
from agf.tracker.history import (reconstruct_weekly_ad_counts, new_ads_per_week, wow_metrics)

NOW = 1_000_000_000.0
WEEK = 7 * 86400


def ad(start_weeks_ago, end_weeks_ago=None, active=True):
    """An AdRecord with start/end expressed in whole weeks before NOW."""
    return AdRecord(
        ad_archive_id="a", page_id="P", page_name="n", is_active=active,
        start_date=int(NOW - start_weeks_ago * WEEK),
        end_date=None if end_weeks_ago is None else int(NOW - end_weeks_ago * WEEK),
        collation_count=1, cta_type=None, cta_text=None, link_url=None, body_text=None)


def test_always_on_ad_counts_every_week():
    series = reconstruct_weekly_ad_counts([ad(40)], weeks=16, now=NOW)
    assert series == [1] * 16


def test_inactive_ad_only_counts_its_window():
    # ran from 10 weeks ago to 6 weeks ago, then stopped
    series = reconstruct_weekly_ad_counts([ad(10, end_weeks_ago=6, active=False)],
                                          weeks=16, now=NOW)
    assert series[0] == 0 and series[-1] == 0      # absent at the far-old and newest weeks
    assert max(series) == 1 and sum(series) >= 3   # present across its ~4-week run


def test_scaling_curve_increases():
    # a page that keeps launching new always-on ads over time -> rising weekly count
    ads = [ad(w) for w in (12, 9, 6, 3, 1)]
    series = reconstruct_weekly_ad_counts(ads, weeks=16, now=NOW)
    assert series[-1] > series[0]                  # more ads active now than 16 weeks ago
    assert series[-1] == 5                          # all five active in the newest week


def test_new_ads_per_week_counts_starts():
    series = new_ads_per_week([ad(0.5), ad(0.5), ad(5.5)], weeks=16, now=NOW)
    assert series[-1] == 2                          # two ads started in the most recent week
    assert series[-6] == 1                          # one started ~5 weeks ago


def test_wow_metrics_scaling():
    m = wow_metrics([30, 34, 41, 74, 85])
    assert m["current"] == 85 and m["prev"] == 74
    assert m["growth_4w"] == round(85 / 30, 2)
    assert m["trend"] == "scaling"


def test_wow_metrics_declining_and_flat():
    assert wow_metrics([20, 18, 12, 9, 8])["trend"] == "declining"
    assert wow_metrics([10, 10, 10, 10, 10])["trend"] == "steady"
    assert wow_metrics([])["trend"] == "flat"
