import random
import time as _time
from agf.request import build_variables, build_form_body
from agf.parse import parse_search_response, AdRecord
from agf.fbjson import parse_fb_json
from agf.extract.token_heal import SessionTokens
from agf.extract.backoff import raise_for_fb_error

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
               max_pages: int = 10, delay: float = 0.0, jitter: float = 0.0,
               sleep=_time.sleep) -> tuple[list[AdRecord], str | None, bool]:
    """Page through an Ad Library search via `session` (curl_cffi-backed); collate ads.

    Returns (records, last_cursor, has_next). Reuses sessionID+collationToken across
    pages by building base variables once and mutating only the cursor.

    `delay`/`jitter` add a polite `delay + U(0, jitter)` second pause BETWEEN page requests
    (never before the first) to avoid tripping Meta's per-IP throttle. A 200 response that
    carries an `errors` block (e.g. rate-limit code 1675004) raises an ExtractError via
    `raise_for_fb_error` so the caller can back off / rotate instead of silently seeing 0 ads."""
    out: list[AdRecord] = []
    base = build_search_variables(query=query, countries=countries,
                                  active_status=active_status,
                                  sort_by_impressions=sort_by_impressions,
                                  search_type=search_type, view_all_page_id=view_all_page_id)
    cursor: str | None = None
    has_next = False
    for i in range(max_pages):
        if i > 0 and (delay or jitter):
            sleep(delay + random.uniform(0, jitter))
        variables = dict(base)
        if cursor is not None:
            variables["cursor"] = cursor
        body = build_form_body(lsd=tokens.lsd, variables=variables, doc_id=tokens.doc_id)
        resp = session.post(_GRAPHQL, data=body, headers={
            "x-fb-lsd": tokens.lsd,
            "x-fb-friendly-name": "AdLibrarySearchPaginationQuery",
            "content-type": "application/x-www-form-urlencoded",
        })
        parsed = parse_fb_json(resp.text)
        raise_for_fb_error(parsed)
        records, cursor, has_next = parse_search_response(parsed)
        out.extend(records)
        if not has_next or not cursor:
            break
    return out, cursor, has_next
