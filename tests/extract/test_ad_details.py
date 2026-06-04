import json
from pathlib import Path
from agf.extract.ad_details import AdDetails, parse_ad_details, fetch_ad_details
from agf.extract.token_heal import SessionTokens
from tests.extract.conftest import FakeResponse, FakeSession

DATA = json.loads((Path(__file__).parent / "fixtures" / "ad_details.json").read_text())
TOKENS = SessionTokens(lsd="AVlsd", fb_dtsg="dtsg", doc_id="DOCDETAILS")


def test_parse_ad_details_reads_eu_reach_and_active():
    d = parse_ad_details(DATA)
    assert d == AdDetails(eu_total_reach=12345, active=True)


def test_parse_ad_details_absent_reach_is_none():
    d = parse_ad_details({"data": {"ad_library_main": {"ad_details": {"aaa_info": {}}}}})
    assert d.eu_total_reach is None


def test_fetch_ad_details_posts_and_parses():
    session = FakeSession(responses={
        ("POST", "/api/graphql/"): [FakeResponse(200, "for (;;);" + json.dumps(DATA))],
    })
    d = fetch_ad_details(session, TOKENS, "999")
    assert d.eu_total_reach == 12345 and d.active is True
