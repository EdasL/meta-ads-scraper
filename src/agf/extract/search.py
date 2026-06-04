from agf.request import build_variables, build_form_body
from agf.parse import parse_search_response, AdRecord
from agf.fbjson import parse_fb_json
from agf.extract.token_heal import SessionTokens

_GRAPHQL = "https://www.facebook.com/api/graphql/"


def build_search_variables(*, query: str, countries: list[str],
                           active_status: str = "ACTIVE",
                           sort_by_impressions: bool = False,
                           search_type: str = "KEYWORD_UNORDERED",
                           view_all_page_id: str | None = None,
                           cursor: str | None = None) -> dict:
    """Build the AdLibrarySearchPaginationQuery `variables`, extending the Plan 1
    base with firehose-B (impressions sort) and EU->US bridge (page-scope) params."""
    v = build_variables(query=query, countries=countries, cursor=cursor)
    v["activeStatus"] = active_status
    v["searchType"] = search_type
    if view_all_page_id is not None:
        v["viewAllPageID"] = view_all_page_id
    if sort_by_impressions:
        v["sortData"] = {"mode": "SORT_BY_TOTAL_IMPRESSIONS", "direction": "DESCENDING"}
    return v


def search_ads(session, tokens: SessionTokens, *, query: str, countries: list[str],
               active_status: str = "ACTIVE", sort_by_impressions: bool = False,
               search_type: str = "KEYWORD_UNORDERED", view_all_page_id: str | None = None,
               max_pages: int = 10) -> tuple[list[AdRecord], str | None, bool]:
    """Page through an Ad Library search via `session` (curl_cffi-backed); collate ads.

    Returns (records, last_cursor, has_next). Reuses sessionID+collationToken across
    pages by building base variables once and mutating only the cursor."""
    out: list[AdRecord] = []
    base = build_search_variables(query=query, countries=countries,
                                  active_status=active_status,
                                  sort_by_impressions=sort_by_impressions,
                                  search_type=search_type, view_all_page_id=view_all_page_id)
    cursor: str | None = None
    has_next = False
    for _ in range(max_pages):
        variables = dict(base)
        if cursor is not None:
            variables["cursor"] = cursor
        body = build_form_body(lsd=tokens.lsd, variables=variables, doc_id=tokens.doc_id)
        resp = session.post(_GRAPHQL, data=body, headers={
            "x-fb-lsd": tokens.lsd,
            "x-fb-friendly-name": "AdLibrarySearchPaginationQuery",
            "content-type": "application/x-www-form-urlencoded",
        })
        records, cursor, has_next = parse_search_response(parse_fb_json(resp.text))
        out.extend(records)
        if not has_next or not cursor:
            break
    return out, cursor, has_next
