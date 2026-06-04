import httpx
from agf.fbjson import parse_fb_json
from agf.bootstrap import extract_lsd, extract_doc_id
from agf.request import build_form_body, build_variables
from agf.parse import parse_search_response, AdRecord

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
_FALLBACK_DOC_ID = "27201872659451053"  # live-verified 2026-06-04
_GRAPHQL = "https://www.facebook.com/api/graphql/"
_LIBRARY = "https://www.facebook.com/ads/library/"


def bootstrap(client: httpx.Client) -> tuple[str, str]:
    """Fetch the Ad Library page; return (lsd, doc_id)."""
    html = client.get(_LIBRARY, headers={"User-Agent": _UA}).text
    return extract_lsd(html), extract_doc_id(html, _FALLBACK_DOC_ID)


def search_us(client: httpx.Client, query: str, lsd: str, doc_id: str,
              max_pages: int = 3) -> list[AdRecord]:
    """Page through a US Ad Library keyword search; return all collated ads."""
    out: list[AdRecord] = []
    cursor = None
    for _ in range(max_pages):
        variables = build_variables(query=query, countries=["US"], cursor=cursor)
        body = build_form_body(lsd=lsd, variables=variables, doc_id=doc_id)
        resp = client.post(_GRAPHQL, data=body, headers={
            "User-Agent": _UA, "X-FB-LSD": lsd,
            "Content-Type": "application/x-www-form-urlencoded",
        })
        records, cursor, has_next = parse_search_response(parse_fb_json(resp.text))
        out.extend(records)
        if not has_next or not cursor:
            break
    return out


def itunes_lookup(client: httpx.Client, track_id: str) -> dict:
    """Call the free iTunes Lookup API for one app id."""
    return client.get("https://itunes.apple.com/lookup",
                      params={"id": track_id, "country": "us", "entity": "software"}).json()
