import json
from pathlib import Path
import pytest
from agf.extract.search import (build_search_variables, count_active_ads, search_ads,
                                search_pages_typeahead)
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


def test_sort_mode_relevancy_omits_sortdata():
    # sort_mode=None (the default) => relevancy ranking => sortData must be absent
    v = build_search_variables(query="calorie tracker", countries=["US"], sort_mode=None)
    assert "sortData" not in v


def test_sort_mode_ascending_sets_direction():
    v = build_search_variables(query="x", countries=["US"], sort_mode="ASCENDING")
    assert v["sortData"] == {"mode": "SORT_BY_TOTAL_IMPRESSIONS", "direction": "ASCENDING"}


def test_sort_mode_overrides_legacy_bool():
    # explicit sort_mode wins over the legacy sort_by_impressions alias
    v = build_search_variables(query="x", countries=["US"],
                               sort_by_impressions=True, sort_mode="ASCENDING")
    assert v["sortData"]["direction"] == "ASCENDING"


TYPEAHEAD_BODY = (
    '{"data":{"ad_library_main":{"typeahead_suggestions":{"page_results":['
    '{"page_id":"545533155319546","likes":678,"name":"QuitBuddy","ig_username":null,'
    '"page_is_deleted":false},'
    '{"page_id":"999","likes":12,"name":"Deleted Co","page_is_deleted":true}]}}}}'
)


def test_search_pages_typeahead_parses_and_drops_deleted():
    session = FakeSession(responses={
        ("POST", "/api/graphql/"): [FakeResponse(200, "for (;;);" + TYPEAHEAD_BODY)],
    })
    pages = search_pages_typeahead(session, TOKENS, query="personalized plan")
    assert [p["page_id"] for p in pages] == ["545533155319546"]   # deleted page filtered out
    assert pages[0]["page_like_count"] == 678
    assert pages[0]["page_name"] == "QuitBuddy"


def test_search_pages_typeahead_raises_on_rate_limit():
    session = FakeSession(responses={
        ("POST", "/api/graphql/"): [FakeResponse(200, RATE_LIMIT_BODY)],
    })
    with pytest.raises(ExtractError) as ei:
        search_pages_typeahead(session, TOKENS, query="x")
    assert ei.value.kind is ErrorKind.RATE_LIMIT


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


class _RecordingSession(FakeSession):
    """FakeSession that also records the POST `data` body so a test can assert the
    outgoing GraphQL variables (e.g. the seeded cursor)."""

    def post(self, url, data=None, headers=None, **kw):
        if not hasattr(self, "bodies"):
            self.bodies = []
        self.bodies.append(data)
        return self._pop("POST", url)


def test_search_ads_seeds_cursor_on_first_page():
    # a caller resuming from a prior page passes cursor= → the FIRST request carries it,
    # so single-page (max_pages=1) adaptive paging can walk from any point.
    session = _RecordingSession(responses={
        ("POST", "/api/graphql/"): [FakeResponse(200, "for (;;);" + PAGE1)],
    })
    search_ads(session, TOKENS, query="x", countries=["US"], max_pages=1, cursor="SEED42")
    assert session.bodies[0]["variables"] and '"cursor":"SEED42"' in session.bodies[0]["variables"]


def test_search_ads_no_cursor_omits_it_on_first_page():
    # default (cursor=None) is unchanged: the first request carries no cursor
    session = _RecordingSession(responses={
        ("POST", "/api/graphql/"): [FakeResponse(200, "for (;;);" + PAGE1)],
    })
    search_ads(session, TOKENS, query="x", countries=["US"], max_pages=1)
    assert '"cursor"' not in session.bodies[0]["variables"]


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


def test_search_ads_reports_page_stats():
    session = FakeSession(responses={
        ("POST", "/api/graphql/"): [
            FakeResponse(200, "for (;;);" + PAGE1),
            FakeResponse(200, PAGE2),
        ],
    })
    st = {}
    search_ads(session, TOKENS, query="x", countries=["US"], max_pages=5, stats=st)
    assert st == {"pages": 2, "has_next": False}


def test_search_ads_stats_flags_ceiling_hit():
    # PAGE1 carries a cursor + has_next=true; max_pages=1 stops at the ceiling
    session = FakeSession(responses={
        ("POST", "/api/graphql/"): [FakeResponse(200, "for (;;);" + PAGE1)],
    })
    st = {}
    search_ads(session, TOKENS, query="x", countries=["US"], max_pages=1, stats=st)
    assert st["pages"] == 1 and st["has_next"] is True


# --- count_active_ads (bounded view_all_page_id pagination) ----------------------
def _count_page(ids, *, cursor=None, has_next=False):
    """A Page-scoped search response body with one collated ad per archive id."""
    results = [{"ad_archive_id": i, "page_id": "P9", "page_name": "Gem",
                "is_active": True, "start_date": 1714500000, "end_date": None,
                "collation_count": 1, "snapshot": {}} for i in ids]
    return json.dumps({"data": {"ad_library_main": {"search_results_connection": {
        "edges": [{"node": {"collated_results": results}}],
        "page_info": {"has_next_page": has_next, "end_cursor": cursor}}}}})


def test_count_active_ads_exact_total_when_pagination_ends():
    session = FakeSession(responses={("POST", "/api/graphql/"): [
        FakeResponse(200, _count_page(["1", "2"], cursor="C1", has_next=True)),
        FakeResponse(200, _count_page(["3"], cursor=None, has_next=False)),
    ]})
    count, capped = count_active_ads(session, TOKENS, "P9", cap=250)
    assert (count, capped) == (3, False)
    assert sum(1 for m, _u in session.calls if m == "POST") == 2


def test_count_active_ads_early_exits_at_cap():
    session = FakeSession(responses={("POST", "/api/graphql/"): [
        FakeResponse(200, _count_page(["1", "2"], cursor="C1", has_next=True)),
        FakeResponse(200, _count_page(["3", "4"], cursor="C2", has_next=True)),
        FakeResponse(200, _count_page(["5", "6"], cursor="C3", has_next=True)),
    ]})
    count, capped = count_active_ads(session, TOKENS, "P9", cap=3)
    assert capped is True and count >= 3
    assert sum(1 for m, _u in session.calls if m == "POST") == 2   # stopped at the cap


def test_count_active_ads_counts_distinct_archive_ids():
    # the same ad can reappear across pages (collation churn) — count it once
    session = FakeSession(responses={("POST", "/api/graphql/"): [
        FakeResponse(200, _count_page(["1", "2"], cursor="C1", has_next=True)),
        FakeResponse(200, _count_page(["2", "3"], cursor=None, has_next=False)),
    ]})
    count, capped = count_active_ads(session, TOKENS, "P9", cap=250)
    assert (count, capped) == (3, False)


def test_count_active_ads_page_bound_reads_as_capped():
    # max_pages exhausted with more pages remaining -> the count is a lower bound
    session = FakeSession(responses={("POST", "/api/graphql/"): [
        FakeResponse(200, _count_page(["1"], cursor="C1", has_next=True)),
        FakeResponse(200, _count_page(["2"], cursor="C2", has_next=True)),
    ]})
    count, capped = count_active_ads(session, TOKENS, "P9", cap=250, max_pages=2)
    assert (count, capped) == (2, True)


def test_count_active_ads_raises_on_rate_limit():
    session = FakeSession(responses={
        ("POST", "/api/graphql/"): [FakeResponse(200, RATE_LIMIT_BODY)],
    })
    with pytest.raises(ExtractError) as ei:
        count_active_ads(session, TOKENS, "P9")
    assert ei.value.kind is ErrorKind.RATE_LIMIT


def test_count_active_ads_sleeps_between_pages():
    slept = []
    session = FakeSession(responses={("POST", "/api/graphql/"): [
        FakeResponse(200, _count_page(["1"], cursor="C1", has_next=True)),
        FakeResponse(200, _count_page(["2"], cursor=None, has_next=False)),
    ]})
    count_active_ads(session, TOKENS, "P9", delay=1.0, jitter=0.0, sleep=slept.append)
    assert slept == [1.0]            # pacing matches the search_ads contract
