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
