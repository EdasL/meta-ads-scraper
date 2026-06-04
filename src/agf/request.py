import json
import uuid

FRIENDLY_NAME = "AdLibrarySearchPaginationQuery"


def build_variables(query: str, countries: list[str], cursor: str | None = None) -> dict:
    """Build the AdLibrarySearchPaginationQuery `variables` object."""
    variables = {
        "activeStatus": "ACTIVE",
        "adType": "ALL",
        "bylines": [],
        "collationToken": str(uuid.uuid4()),
        "contentLanguages": [],
        "countries": countries,
        "excludedIDs": [],
        "first": 30,
        "isTargetedCountry": False,
        "location": None,
        "mediaType": "ALL",
        "multiCountryFilterMode": None,
        "pageIDs": [],
        "potentialReachInput": [],
        "publisherPlatforms": [],
        "queryString": query,
        "regions": [],
        "searchType": "KEYWORD_UNORDERED",
        "sessionID": str(uuid.uuid4()),
        "source": None,
        "startDate": None,
        "viewAllPageID": "0",
    }
    if cursor is not None:
        variables["cursor"] = cursor
    return variables


def jazoest(lsd: str) -> str:
    """Facebook's anti-CSRF checksum: '2' + sum of char codes."""
    return "2" + str(sum(ord(c) for c in lsd))


def build_form_body(lsd: str, variables: dict, doc_id: str) -> dict:
    """Build the x-www-form-urlencoded POST body."""
    return {
        "av": "0",
        "__user": "0",
        "__a": "1",
        "__comet_req": "1",
        "lsd": lsd,
        "jazoest": jazoest(lsd),
        "fb_api_caller_class": "RelayModern",
        "fb_api_req_friendly_name": FRIENDLY_NAME,
        "server_timestamps": "true",
        "variables": json.dumps(variables, separators=(",", ":")),
        "doc_id": doc_id,
    }
