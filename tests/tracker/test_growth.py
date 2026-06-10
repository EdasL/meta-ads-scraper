"""compute_advertiser_growth — rolls adtech.app_advertiser_metrics into
adtech.advertiser_growth. Covers the pct_change floor: a non-zero base below
MIN_PCT_BASE yields NULL (tiny-base percents like 2→25 = +1150% read as fake);
first_count/latest_count/weeks_observed semantics are unchanged."""
from datetime import date, timedelta

from sqlalchemy import text
from agf.parse import AdRecord
from agf.db import repo
from agf.tracker.growth import compute_advertiser_growth, MIN_PCT_BASE


def _ad(ext, pg):
    return AdRecord(ext, pg, "GrowthCo", True, 1_714_500_000, None, 2,
                    "INSTALL_MOBILE_APP", "Install", "https://x.example", "body")


def _seed(pg_conn, ext, pg, weekly_counts):
    """An advertiser with one weekly metric row per count, consecutive weeks."""
    adv = repo.upsert_advertiser(pg_conn, _ad(ext, pg))
    for i, count in enumerate(weekly_counts):
        week = date(2026, 3, 2) + timedelta(weeks=i)
        repo.upsert_app_advertiser_metric(pg_conn, adv, week, count, 0, 0)
    return adv


def _growth_row(pg_conn, adv):
    return pg_conn.execute(text(
        "SELECT first_count, latest_count, weeks_observed, pct_change "
        "FROM adtech.advertiser_growth WHERE advertiser_id=:a"), {"a": adv}).first()


def test_pct_change_null_when_nonzero_base_below_floor(pg_conn):
    # 0 → 2 → 25: the first non-zero week (2) is under MIN_PCT_BASE, so no percent —
    # the counts still summarize the ramp.
    adv = _seed(pg_conn, "G1", "PG1", [0, 2, 25])

    n = compute_advertiser_growth(pg_conn, only_ids=[adv])

    assert n == 1
    row = _growth_row(pg_conn, adv)
    assert row.first_count == 0 and row.latest_count == 25 and row.weeks_observed == 3
    assert row.pct_change is None


def test_pct_change_computed_when_base_at_floor(pg_conn):
    # base == MIN_PCT_BASE (5) → percent is still computed: 5 → 20 = +300%
    adv = _seed(pg_conn, "G2", "PG2", [0, MIN_PCT_BASE, 20])

    compute_advertiser_growth(pg_conn, only_ids=[adv])

    row = _growth_row(pg_conn, adv)
    assert row.first_count == 0 and row.latest_count == 20 and row.weeks_observed == 3
    assert float(row.pct_change) == 300.0


def test_pct_change_null_when_never_advertised(pg_conn):
    # all-zero series: no non-zero base exists → NULL (the floor must not break this)
    adv = _seed(pg_conn, "G3", "PG3", [0, 0, 0])

    compute_advertiser_growth(pg_conn, only_ids=[adv])

    row = _growth_row(pg_conn, adv)
    assert row.first_count == 0 and row.latest_count == 0
    assert row.pct_change is None
