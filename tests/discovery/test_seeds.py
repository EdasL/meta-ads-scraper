from sqlalchemy import text
from agf.discovery.seeds import Candidate, upsert_candidate
from tests.discovery.conftest import make_ad


def test_app_candidate_routes_to_upsert_app(pg_conn):
    c = Candidate(source="bridge", kind="app", store="ios", store_id="111",
                  app_meta={"store": "ios", "store_id": "111", "name": "FitGem"})
    out = upsert_candidate(pg_conn, c)
    out2 = upsert_candidate(pg_conn, c)
    assert out2["app_id"] == out["app_id"]            # dedup-safe
    cnt = pg_conn.execute(text("SELECT count(*) FROM public.apps WHERE store='ios' "
                               "AND store_id='111'")).scalar()
    assert cnt == 1


def test_page_candidate_routes_to_upsert_advertiser(pg_conn):
    c = Candidate(source="firehose_eu", kind="page", page_id="PG1", page_name="GemCo")
    out = upsert_candidate(pg_conn, c)
    out2 = upsert_candidate(pg_conn, c)
    assert out2["advertiser_id"] == out["advertiser_id"]
    cnt = pg_conn.execute(text("SELECT count(*) FROM public.advertisers WHERE page_id='PG1'")).scalar()
    assert cnt == 1


def test_candidate_with_ad_record_persists_the_ad(pg_conn):
    # bridge-style candidate carries its originating AdRecord -> the ad must land in `ads`
    ad = make_ad("AD9", "PG2", "GemCo", "https://getgem.com/quiz", days_old=20)
    c = Candidate(source="bridge", kind="page", page_id="PG2", page_name="GemCo", ad_record=ad)
    out = upsert_candidate(pg_conn, c)
    assert "ad_id" in out
    row = pg_conn.execute(text("SELECT advertiser_id FROM public.ads WHERE external_ad_id='AD9'")).first()
    assert row is not None and row.advertiser_id == out["advertiser_id"]
