import random
import time as _time
from agf.request import build_variables, build_form_body
from agf.parse import parse_search_response, AdRecord
from agf.fbjson import parse_fb_json
from agf.extract.token_heal import SessionTokens
from agf.extract.backoff import raise_for_fb_error

_GRAPHQL = "https://www.facebook.com/api/graphql/"

# Typeahead resolves a keyword -> candidate Pages (page_id + likes + name) in one cheap
# call. Separate friendly-name + doc_id from the search query (probed live 2026-06-10).
_TYPEAHEAD_FRIENDLY = "useAdLibraryTypeaheadSuggestionDataSourceQuery"
_TYPEAHEAD_DOC_ID = "9755915494515334"


def _resolve_sort(sort_mode: str | None, sort_by_impressions: bool) -> str | None:
    """Effective sort: explicit sort_mode wins; else the legacy bool maps to DESCENDING.
    Returns 'DESCENDING' | 'ASCENDING' | None (None => omit sortData => relevancy ranking)."""
    if sort_mode is not None:
        return sort_mode
    return "DESCENDING" if sort_by_impressions else None


def build_search_variables(*, query: str, countries: list[str],
                           active_status: str = "ACTIVE",
                           sort_by_impressions: bool = False,
                           sort_mode: str | None = None,
                           search_type: str = "KEYWORD_UNORDERED",
                           view_all_page_id: str | None = None,
                           cursor: str | None = None) -> dict:
    """Build the AdLibrarySearchPaginationQuery `variables`.

    `sort_mode` ('DESCENDING'|'ASCENDING'|None) is the current knob; `None` omits sortData
    entirely (relevancy ranking). The legacy `sort_by_impressions` bool is kept as an alias
    (True -> DESCENDING) so existing callers (firehose_eu) are unaffected."""
    v = build_variables(query=query, countries=countries, cursor=cursor)
    v["activeStatus"] = active_status
    v["searchType"] = search_type
    if view_all_page_id is not None:
        v["viewAllPageID"] = view_all_page_id
    effective = _resolve_sort(sort_mode, sort_by_impressions)
    if effective in ("DESCENDING", "ASCENDING"):
        v["sortData"] = {"mode": "SORT_BY_TOTAL_IMPRESSIONS", "direction": effective}
    return v


def search_ads(session, tokens: SessionTokens, *, query: str, countries: list[str],
               active_status: str = "ACTIVE", sort_by_impressions: bool = False,
               sort_mode: str | None = None,
               search_type: str = "KEYWORD_UNORDERED", view_all_page_id: str | None = None,
               max_pages: int = 10, delay: float = 0.0, jitter: float = 0.0,
               sleep=_time.sleep,
               stats: dict | None = None) -> tuple[list[AdRecord], str | None, bool]:
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
                                  sort_mode=sort_mode,
                                  search_type=search_type, view_all_page_id=view_all_page_id)
    cursor: str | None = None
    has_next = False
    pages_fetched = 0
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
        pages_fetched = i + 1
        out.extend(records)
        if not has_next or not cursor:
            break
    if stats is not None:
        stats["pages"] = pages_fetched
        stats["has_next"] = bool(has_next)
    return out, cursor, has_next


def count_active_ads(session, tokens: SessionTokens, page_id, *, countries=None,
                     cap=250, max_pages=40, delay: float = 0.0, jitter: float = 0.0,
                     sleep=_time.sleep) -> tuple[int, bool]:
    """True active-ad total for a Page via bounded `view_all_page_id` pagination.

    Meta exposes no search-level result count (live-probed 2026-06-10), so the only way
    to get a per-advertiser active-ad total is to page the Page-scoped search and count
    DISTINCT ad archive ids. Returns (count, capped):
      - early exit as soon as count >= `cap` -> (count, True) — the incumbent veto
        ("cap+ ads"), so a Nike never costs more than ~cap/30 page requests;
      - natural pagination end -> (count, False) — the exact total;
      - `max_pages` exhausted with pages remaining -> (count, True) — a lower bound.

    Same pacing contract as `search_ads` (`delay + U(0, jitter)` BETWEEN page requests)
    and the same throttle surfacing via `raise_for_fb_error` — callers (the facade)
    back off + rotate the proxy on a RATE_LIMIT."""
    base = build_search_variables(query="", countries=list(countries or ["US"]),
                                  active_status="ACTIVE", search_type="page",
                                  view_all_page_id=str(page_id))
    seen: set[str] = set()
    cursor: str | None = None
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
        seen.update(r.ad_archive_id for r in records if r.ad_archive_id)
        if len(seen) >= cap:
            return len(seen), True
        if not has_next or not cursor:
            return len(seen), False
    return len(seen), True


def search_pages_typeahead(session, tokens: SessionTokens, *, query: str,
                           country: str = "US") -> list[dict]:
    """Resolve a keyword to candidate advertiser Pages via the typeahead datasource.

    One cheap GraphQL call -> up to ~15 pages, each `{page_id, page_name, page_like_count,
    ig_username}`. The matched field is the page NAME (so results are noisier than an ad
    search), but each row carries `likes` for a discovery-time incumbent veto. Returns [] on
    a Facebook error block (caller decides whether to rotate/back off)."""
    variables = {"queryString": query, "isMobile": False, "country": country, "adType": "ALL"}
    body = build_form_body(lsd=tokens.lsd, variables=variables, doc_id=_TYPEAHEAD_DOC_ID)
    body["fb_api_req_friendly_name"] = _TYPEAHEAD_FRIENDLY
    resp = session.post(_GRAPHQL, data=body, headers={
        "x-fb-lsd": tokens.lsd,
        "x-fb-friendly-name": _TYPEAHEAD_FRIENDLY,
        "content-type": "application/x-www-form-urlencoded",
    })
    parsed = parse_fb_json(resp.text)
    raise_for_fb_error(parsed)
    main = (parsed.get("data", {}) or {}).get("ad_library_main") \
        or (parsed.get("data", {}) or {}).get("adLibraryMain") or {}
    pages = (main.get("typeahead_suggestions") or {}).get("page_results") or []
    out = []
    for p in pages:
        if p.get("page_is_deleted"):
            continue
        out.append({
            "page_id": str(p.get("page_id", "")),
            "page_name": p.get("name"),
            "page_like_count": p.get("likes"),
            "ig_username": p.get("ig_username"),
        })
    return out
