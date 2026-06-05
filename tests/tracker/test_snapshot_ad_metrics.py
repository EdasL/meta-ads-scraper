import asyncio
from sqlalchemy import text
from agf.parse import AdRecord
from agf.db import repo
from agf.extract.ad_details import AdDetails
from agf.tracker.snapshots import snapshot_ad_metrics
from tests.tracker.conftest import FakeDetailsExtractor


def _ad(ext, active=True):
    return AdRecord(ext, "P1", "GemCo", active, 1_714_500_000, None, 3,
                    "INSTALL_MOBILE_APP", "Install", "https://x.example", "body")


def test_snapshots_active_ads_only_and_is_idempotent(pg_conn):
    adv = repo.upsert_advertiser(pg_conn, _ad("A1"))
    active_id = repo.upsert_ad(pg_conn, _ad("A1", active=True), adv, None)
    inactive_id = repo.upsert_ad(pg_conn, _ad("A2", active=False), adv, None)  # inactive -> skipped
    ex = FakeDetailsExtractor({"A1": AdDetails(eu_total_reach=4321, active=True)})

    # scope to this test's two ads so committed rows elsewhere in the shared DB (e.g. a
    # demo seed) can't inflate the global active-ad count; A2 is still filtered by is_active
    scope = [active_id, inactive_id]
    n1 = asyncio.run(snapshot_ad_metrics(pg_conn, ex, "2026-06-04", only_ids=scope))
    n2 = asyncio.run(snapshot_ad_metrics(pg_conn, ex, "2026-06-04", only_ids=scope))  # re-run same day

    assert n1 == 1 and n2 == 1                       # one active ad, idempotent
    assert ex.calls == ["A1", "A1"]                  # only the active ad queried
    row = pg_conn.execute(text("SELECT eu_total_reach, active FROM adtech.ad_metrics "
                               "WHERE ad_id=:a AND snapshot_date='2026-06-04'"),
                          {"a": active_id}).first()
    assert row.eu_total_reach == 4321 and row.active is True
    cnt = pg_conn.execute(text("SELECT count(*) FROM adtech.ad_metrics WHERE ad_id=:a"),
                          {"a": active_id}).scalar()
    assert cnt == 1                                  # PK conflict updated, not duplicated
