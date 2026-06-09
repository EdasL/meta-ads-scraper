from pathlib import Path
import pytest
from agf.extract.search import build_search_variables, search_ads
from agf.extract.token_heal import SessionTokens
from agf.extract.errors import ErrorKind, ExtractError
from tests.extract.conftest import FakeResponse, FakeSession

RATE_LIMIT_BODY = ('{"errors":[{"message":"Rate limit exceeded","severity":"CRITICAL",'
                   '"code":1675004}],"extensions":{"is_final":true}}')

FX = Path(__file__).parent / "fixtures"
PAGE1 = (FX / "search_page1.json").read_text()
PAGE2 = (FX / "search_page2.json").read_text()
TOKENS = SessionTokens(lsd="AVlsd", fb_dtsg="dtsg", doc_id="27201872659451053")


def test_build_search_variables_defaults():
    v = build_search_variables(query="workout", countries=["US"])
    assert v["queryString"] == "workout"
    assert v["countries"] == ["US"]
    assert v["searchType"] == "KEYWORD_UNORDERED"
    assert "sortData" not in v
    assert v["viewAllPageID"] == "0"


def test_build_search_variables_impressions_sort_and_page_scope():
    v = build_search_variables(query="", countries=["DE"], sort_by_impressions=True,
                               search_type="page", view_all_page_id="98765")
    assert v["sortData"] == {"mode": "SORT_BY_TOTAL_IMPRESSIONS", "direction": "DESCENDING"}
    assert v["searchType"] == "page"
    assert v["viewAllPageID"] == "98765"


def test_search_ads_paginates_and_collates():
    session = FakeSession(responses={
        ("POST", "/api/graphql/"): [
            FakeResponse(200, "for (;;);" + PAGE1),
            FakeResponse(200, PAGE2),
        ],
    })
    records, cursor, has_next = search_ads(session, TOKENS, query="x",
                                           countries=["US"], max_pages=5)
    assert [r.ad_archive_id for r in records] == ["1", "2"]
    assert has_next is False
    assert sum(1 for m, u in session.calls if m == "POST") == 2


def test_search_ads_respects_max_pages():
    session = FakeSession(responses={
        ("POST", "/api/graphql/"): [FakeResponse(200, PAGE1), FakeResponse(200, PAGE1)],
    })
    records, cursor, has_next = search_ads(session, TOKENS, query="x",
                                           countries=["US"], max_pages=1)
    assert len(records) == 1                  # only page 1 fetched
    assert sum(1 for m, u in session.calls if m == "POST") == 1


def test_search_ads_raises_on_rate_limit():
    # Meta returns HTTP 200 with an errors[] body when throttling; must surface as RATE_LIMIT
    session = FakeSession(responses={
        ("POST", "/api/graphql/"): [FakeResponse(200, RATE_LIMIT_BODY)],
    })
    with pytest.raises(ExtractError) as ei:
        search_ads(session, TOKENS, query="x", countries=["DE"], max_pages=1)
    assert ei.value.kind is ErrorKind.RATE_LIMIT


def test_search_ads_sleeps_between_pages():
    # polite pacing: a jittered delay between consecutive page requests (not before the first)
    slept = []
    session = FakeSession(responses={
        ("POST", "/api/graphql/"): [FakeResponse(200, PAGE1), FakeResponse(200, PAGE2)],
    })
    search_ads(session, TOKENS, query="x", countries=["US"], max_pages=5,
               delay=1.0, jitter=0.0, sleep=slept.append)
    assert slept == [1.0]            # exactly one inter-page sleep for two pages
