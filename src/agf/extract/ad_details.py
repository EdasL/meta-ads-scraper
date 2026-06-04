from dataclasses import dataclass
from agf.fbjson import parse_fb_json
from agf.request import build_form_body
from agf.extract.token_heal import SessionTokens

_GRAPHQL = "https://www.facebook.com/api/graphql/"
_FRIENDLY = "AdLibraryAdDetailsV2Query"


@dataclass
class AdDetails:
    eu_total_reach: int | None
    active: bool


def parse_ad_details(data: dict) -> AdDetails:
    """Pull aaa_info.eu_total_reach + is_active from an AdDetails response."""
    main = data.get("data", {}).get("ad_library_main", {}) or {}
    details = main.get("ad_details") or {}
    aaa = details.get("aaa_info") or {}
    reach = aaa.get("eu_total_reach")
    return AdDetails(
        eu_total_reach=int(reach) if reach is not None else None,
        active=bool(aaa.get("is_active")),
    )


def fetch_ad_details(session, tokens: SessionTokens, ad_archive_id) -> AdDetails:
    """POST AdLibraryAdDetailsV2Query for one ad and parse the EU reach + active."""
    variables = {"adArchiveID": str(ad_archive_id), "country": "ALL"}
    body = build_form_body(lsd=tokens.lsd, variables=variables, doc_id=tokens.doc_id)
    body["fb_api_req_friendly_name"] = _FRIENDLY
    resp = session.post(_GRAPHQL, data=body, headers={
        "x-fb-lsd": tokens.lsd,
        "x-fb-friendly-name": _FRIENDLY,
        "content-type": "application/x-www-form-urlencoded",
    })
    return parse_ad_details(parse_fb_json(resp.text))
