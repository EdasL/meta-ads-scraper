from sqlalchemy import text
from agf.discovery.seeds import Candidate, upsert_candidate


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
