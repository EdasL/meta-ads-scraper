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


def test_upsert_app_metric_conflict_update(pg_conn):
    app = repo.upsert_app(pg_conn, {"store": "ios", "store_id": "888222", "name": "M"})
    repo.upsert_app_metric(pg_conn, app, "2026-06-10", 4.5, 100, None)
    repo.upsert_app_metric(pg_conn, app, "2026-06-10", 4.6, 120, 5000)  # same day -> update
    rows = pg_conn.execute(text(
        "SELECT rating, rating_count, installs FROM adtech.app_metrics "
        "WHERE app_id=:a"), {"a": app}).all()
    assert len(rows) == 1
    assert (round(rows[0].rating, 1), rows[0].rating_count, rows[0].installs) == (4.6, 120, 5000)


def test_prune_app_history_window(pg_conn):
    app = repo.upsert_app(pg_conn, {"store": "ios", "store_id": "888333", "name": "P"})
    repo.upsert_app_metric(pg_conn, app, "2020-01-01", 4.0, 10, None)   # ancient
    repo.upsert_app_metric(pg_conn, app, "2026-06-10", 4.5, 50, None)   # recent
    pg_conn.execute(text(
        "INSERT INTO public.app_reviews (app_id, review_id, content, fetched_at) "
        "VALUES (:a, 'old', 'x', now() - interval '900 days'), (:a, 'new', 'y', now())"),
        {"a": app})
    deleted_metrics, deleted_reviews = repo.prune_app_history(pg_conn, keep_days=730)
    assert deleted_metrics >= 1 and deleted_reviews >= 1
    kept_m = pg_conn.execute(text(
        "SELECT snapshot_date FROM adtech.app_metrics WHERE app_id=:a"), {"a": app}).all()
    kept_r = pg_conn.execute(text(
        "SELECT review_id FROM public.app_reviews WHERE app_id=:a"), {"a": app}).all()
    assert [str(r.snapshot_date) for r in kept_m] == ["2026-06-10"]
    assert [r.review_id for r in kept_r] == ["new"]


def test_set_meta_active_ads(pg_conn):
    adv = repo.upsert_advertiser(pg_conn, _ad())
    repo.set_meta_active_ads(pg_conn, adv, 42, False)
    row = pg_conn.execute(text(
        "SELECT meta_active_ads, meta_active_ads_capped, meta_active_ads_checked_at "
        "FROM public.advertisers WHERE id=:a"), {"a": adv}).first()
    assert row.meta_active_ads == 42 and row.meta_active_ads_capped is False
    assert row.meta_active_ads_checked_at is not None
    repo.set_meta_active_ads(pg_conn, adv, 250, True)       # re-check overwrites
    row = pg_conn.execute(text(
        "SELECT meta_active_ads, meta_active_ads_capped FROM public.advertisers "
        "WHERE id=:a"), {"a": adv}).first()
    assert row.meta_active_ads == 250 and row.meta_active_ads_capped is True


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
