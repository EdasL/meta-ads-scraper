import asyncio
from sqlalchemy import text
from agf.discovery.link_apps import link_app_to_advertiser
from agf.discovery.seeds import Candidate
from tests.discovery.conftest import FakeExtractor, make_ad


def _app_candidate(store_id="6470000001"):
    return Candidate(source="bridge", kind="app", store="ios", store_id=store_id,
                     app_meta={"store": "ios", "store_id": store_id, "name": "FitGem"})


def test_matching_ad_is_linked(pg_conn):
    ad = make_ad("a1", "P1", "GemCo",
                 "https://apps.apple.com/us/app/x/id6470000001", days_old=30)
    ex = FakeExtractor(keyword_ads={"FitGem": [ad]})
    page_id = asyncio.run(link_app_to_advertiser(pg_conn, ex, _app_candidate()))
    assert page_id == "P1"
    row = pg_conn.execute(text("SELECT advertiser_id, app_id FROM public.ads "
                               "WHERE external_ad_id='a1'")).first()
    assert row.advertiser_id is not None and row.app_id is not None


def test_non_matching_ad_is_skipped(pg_conn):
    ad = make_ad("a2", "P2", "Other",
                 "https://apps.apple.com/us/app/x/id999", days_old=30)
    ex = FakeExtractor(keyword_ads={"FitGem": [ad]})
    page_id = asyncio.run(link_app_to_advertiser(pg_conn, ex, _app_candidate()))
    assert page_id is None
    cnt = pg_conn.execute(text("SELECT count(*) FROM public.ads WHERE external_ad_id='a2'")).scalar()
    assert cnt == 0                                       # non-match never written
