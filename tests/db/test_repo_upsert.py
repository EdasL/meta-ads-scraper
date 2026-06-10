from sqlalchemy import text
from agf.parse import AdRecord
from agf.db import repo


def _ad(page_id="P1", archive="A1"):
    return AdRecord(archive, page_id, "GemCo", True, 1714500000, None, 5,
                    "INSTALL_MOBILE_APP", "Install",
                    "https://apps.apple.com/us/app/x/id111", "body")


def test_upsert_advertiser_idempotent(pg_conn):
    a1 = repo.upsert_advertiser(pg_conn, _ad())
    a2 = repo.upsert_advertiser(pg_conn, _ad())
    assert a1 == a2
    cnt = pg_conn.execute(text("SELECT count(*) FROM public.advertisers WHERE page_id='P1'")).scalar()
    assert cnt == 1


def test_upsert_advertiser_persists_page_like_count_non_null_only(pg_conn):
    ad = _ad()
    ad.page_like_count = 842
    aid = repo.upsert_advertiser(pg_conn, ad)
    assert pg_conn.execute(text(
        "SELECT page_like_count FROM public.advertisers WHERE id=:i"),
        {"i": aid}).scalar() == 842
    # a later record with NULL likes must NOT wipe the stored value
    repo.upsert_advertiser(pg_conn, _ad())
    assert pg_conn.execute(text(
        "SELECT page_like_count FROM public.advertisers WHERE id=:i"),
        {"i": aid}).scalar() == 842
    # a fresh non-NULL value updates it
    ad.page_like_count = 900
    repo.upsert_advertiser(pg_conn, ad)
    assert pg_conn.execute(text(
        "SELECT page_like_count FROM public.advertisers WHERE id=:i"),
        {"i": aid}).scalar() == 900


def test_upsert_app_screenshots_and_price_label(pg_conn):
    app1 = repo.upsert_app(pg_conn, {"store": "ios", "store_id": "777111",
                                     "screenshots": ["https://s/1.png", "https://s/2.png"],
                                     "price_label": "Free", "icon_url": "https://i.png"})
    row = pg_conn.execute(text(
        "SELECT screenshots, price_label, icon_url FROM public.apps WHERE id=:i"),
        {"i": app1}).first()
    assert row.screenshots == ["https://s/1.png", "https://s/2.png"]   # JSONB array
    assert row.price_label == "Free" and row.icon_url == "https://i.png"
    # a refresh without screenshots keeps the stored set (COALESCE)
    repo.upsert_app(pg_conn, {"store": "ios", "store_id": "777111", "name": "Later"})
    row = pg_conn.execute(text(
        "SELECT name, screenshots FROM public.apps WHERE id=:i"), {"i": app1}).first()
    assert row.name == "Later" and row.screenshots == ["https://s/1.png", "https://s/2.png"]


def test_upsert_app_idempotent_on_store_store_id(pg_conn):
    app1 = repo.upsert_app(pg_conn, {"store": "ios", "store_id": "111", "name": "FitGem",
                                     "release_date": "2026-02-10T08:00:00Z",
                                     "rating_count": 340, "rating": 4.6, "genre": "Health & Fitness"})
    app2 = repo.upsert_app(pg_conn, {"store": "ios", "store_id": "111", "name": "FitGem 2.0"})
    assert app1 == app2
    row = pg_conn.execute(text("SELECT name, release_date FROM public.apps WHERE id=:i"),
                          {"i": app1}).first()
    assert row.name == "FitGem 2.0"            # updated
    assert str(row.release_date) == "2026-02-10"  # preserved (COALESCE)


def test_upsert_ad_links_advertiser_and_app(pg_conn):
    adv = repo.upsert_advertiser(pg_conn, _ad())
    app = repo.upsert_app(pg_conn, {"store": "ios", "store_id": "111", "name": "FitGem"})
    ad1 = repo.upsert_ad(pg_conn, _ad(), adv, app)
    ad2 = repo.upsert_ad(pg_conn, _ad(), adv, app)
    assert ad1 == ad2                          # idempotent on external_ad_id
    row = pg_conn.execute(text("SELECT advertiser_id, app_id, start_date FROM public.ads "
                               "WHERE external_ad_id='A1'")).first()
    assert row.advertiser_id == adv and row.app_id == app
    assert row.start_date is not None          # epoch -> timestamptz
