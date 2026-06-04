from sqlalchemy import text
from agf.parse import AdRecord
from agf.db import repo


def _ad():
    return AdRecord("A1", "P1", "GemCo", True, 1714500000, None, 5,
                    "INSTALL_MOBILE_APP", "Install",
                    "https://apps.apple.com/us/app/x/id111", "body")


def test_record_ad_metric_conflict_update(pg_conn):
    adv = repo.upsert_advertiser(pg_conn, _ad())
    ad = repo.upsert_ad(pg_conn, _ad(), adv, None)
    repo.record_ad_metric(pg_conn, ad, "2026-06-04", 100, True)
    repo.record_ad_metric(pg_conn, ad, "2026-06-04", 200, False)   # same PK -> update
    row = pg_conn.execute(text("SELECT eu_total_reach, active FROM adtech.ad_metrics "
                               "WHERE ad_id=:a"), {"a": ad}).first()
    assert row.eu_total_reach == 200 and row.active is False


def test_app_advertiser_metric_conflict_update(pg_conn):
    adv = repo.upsert_advertiser(pg_conn, _ad())
    repo.upsert_app_advertiser_metric(pg_conn, adv, "2026-06-01", 5, 3, 999)
    repo.upsert_app_advertiser_metric(pg_conn, adv, "2026-06-01", 7, 4, 1000)
    row = pg_conn.execute(text("SELECT active_ad_count, total_eu_reach FROM "
                               "adtech.app_advertiser_metrics WHERE advertiser_id=:a"),
                          {"a": adv}).first()
    assert row.active_ad_count == 7 and row.total_eu_reach == 1000


def test_upsert_funnel_idempotent_per_ad(pg_conn):
    adv = repo.upsert_advertiser(pg_conn, _ad())
    ad = repo.upsert_ad(pg_conn, _ad(), adv, None)
    repo.upsert_funnel(pg_conn, ad, "https://land", "https://final", "shot.png")
    repo.upsert_funnel(pg_conn, ad, "https://land2", "https://final2", "shot2.png")
    cnt = pg_conn.execute(text("SELECT count(*) FROM public.funnels WHERE ad_id=:a"),
                          {"a": ad}).scalar()
    assert cnt == 1
    final = pg_conn.execute(text("SELECT final_url_after_redirect FROM public.funnels "
                                 "WHERE ad_id=:a"), {"a": ad}).scalar()
    assert final == "https://final2"           # updated


def test_advertiser_enrichment_backfill(pg_conn):
    adv = repo.upsert_advertiser(pg_conn, _ad())
    repo.upsert_advertiser_enrichment(pg_conn, adv, page_category="Health", ig_username="gem")
    row = pg_conn.execute(text("SELECT page_category, ig_username FROM public.advertisers "
                               "WHERE id=:a"), {"a": adv}).first()
    assert row.page_category == "Health" and row.ig_username == "gem"
