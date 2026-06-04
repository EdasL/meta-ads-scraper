from sqlalchemy import text
from agf.parse import AdRecord
from agf.db import repo
from agf.tracker.snapshots import snapshot_advertiser_week


def _ad(ext, active=True):
    return AdRecord(ext, "P1", "GemCo", active, 1_714_500_000, None, 2,
                    "INSTALL_MOBILE_APP", "Install", "https://x.example", "body")


def test_weekly_rollup_counts_and_reach_idempotent(pg_conn):
    adv = repo.upsert_advertiser(pg_conn, _ad("A1"))
    a1 = repo.upsert_ad(pg_conn, _ad("A1", active=True), adv, None)
    a2 = repo.upsert_ad(pg_conn, _ad("A2", active=True), adv, None)
    repo.upsert_ad(pg_conn, _ad("A3", active=False), adv, None)        # inactive
    # two creatives on a1, one on a2 -> 3 distinct creatives
    for sha in ("s1", "s2"):
        repo.upsert_creative(pg_conn, a1, "image", "b", "t", None, None, sha, None)
    repo.upsert_creative(pg_conn, a2, "image", "b", "t", None, None, "s3", None)
    # in-week ad_metrics: a1 reach 100, a2 reach 50
    repo.record_ad_metric(pg_conn, a1, "2026-06-02", 100, True)
    repo.record_ad_metric(pg_conn, a2, "2026-06-03", 50, True)

    snapshot_advertiser_week(pg_conn, "2026-06-01")
    snapshot_advertiser_week(pg_conn, "2026-06-01")                    # idempotent

    row = pg_conn.execute(text(
        "SELECT active_ad_count, distinct_creative_count, total_eu_reach "
        "FROM adtech.app_advertiser_metrics WHERE advertiser_id=:a AND week_start='2026-06-01'"),
        {"a": adv}).first()
    assert row.active_ad_count == 2          # a1, a2 (a3 inactive)
    assert row.distinct_creative_count == 3  # s1, s2, s3
    assert row.total_eu_reach == 150         # 100 + 50
    cnt = pg_conn.execute(text("SELECT count(*) FROM adtech.app_advertiser_metrics "
                               "WHERE advertiser_id=:a"), {"a": adv}).scalar()
    assert cnt == 1
