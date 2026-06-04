# Emerging App Finder — PoC Implementation Plan (Plan 1 of 7)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the full discovery→classify→emerging→scaling chain end-to-end in a single self-contained Python package that, given a US Ad Library search, outputs a ranked CSV of recently-released apps running multiple active, longevity-cleared creatives.

**Architecture:** Pure functions for every transform (parse, classify, score) unit-tested against saved fixtures; thin live-I/O wrappers (`httpx`) for the Ad Library GraphQL + iTunes Lookup, exercised by opt-in integration smoke tests. No DB, no proxies, no browser — the PoC uses direct `httpx` form-POST with a self-healed/fallback `doc_id`. This validates the load-bearing assumptions (live `doc_id`, response field paths, app-intent fields, iTunes enrich) before we invest in the durable pipeline (Plans 2–7).

**Tech Stack:** Python 3.13, `httpx`, `pytest`. Standard library `re`, `json`, `urllib.parse`, `dataclasses`, `csv`, `datetime`.

**Companion design:** `docs/superpowers/specs/2026-06-04-emerging-meta-ads-app-finder-design.md`

---

## Project decomposition (the 7 plans)

| Plan | Subsystem | Produces |
|---|---|---|
| **1 (this)** | **PoC** | one script: US search → ranked CSV; proves the thesis |
| 2 | Extraction core | durable scraper: Playwright `doc_id`/token self-heal, pagination, proxy + backoff |
| 3 | Data spine | Postgres-as-queue schema (forked from `adscrawler`), dedup, staleness queue, workers |
| 4 | Discovery firehoses | A (RSS + MWM) · B (EU firehose + US bridge) · C (funnel fingerprinting) |
| 5 | Velocity tracker + GemScore | daily snapshots, full scoring + vetoes + persistence gate, matview output |
| 6 | Enrichment | funnel capture, creative byte download, company info |
| 7 | Output + ops | FastAPI read model / shortlist, monitoring, proxy ops |

Each later plan is written only after the prior one runs green.

---

## File structure (this plan)

```
emerging-app-finder/
  requirements.txt                 # httpx, pytest
  README.md                        # run instructions
  src/agf/
    __init__.py
    fbjson.py        # strip "for (;;);" guard + json.loads
    bootstrap.py     # extract LSD token + doc_id from Ad Library HTML
    request.py       # build variables JSON + form body + jazoest
    parse.py         # search response JSON -> list[AdRecord]
    classify.py      # AdRecord -> AppIntent (ios/android/web/none + store_id)
    enrich.py        # iTunes Lookup parse + emerging flag
    scale.py         # group ads by advertiser -> scaling proxy
    score.py         # PoC GemScore (simplified AND-gate)
    livesearch.py    # live httpx wrappers (Ad Library + iTunes)
    run_poc.py       # orchestrate live run -> ranked CSV
  tests/
    fixtures/
      ads_library_page.html        # saved /ads/library/ HTML (LSD + doc_id)
      search_response.json         # saved AdLibrarySearchPaginationQuery JSON
      itunes_lookup.json           # saved iTunes Lookup JSON
    test_fbjson.py  test_bootstrap.py  test_request.py
    test_parse.py   test_classify.py   test_enrich.py
    test_scale.py   test_score.py
```

**Module responsibilities are one-each and pure** except `livesearch.py` (the only network module). Tests import the pure modules and never hit the network; network is covered by opt-in smoke tests in Task 12.

---

### Task 1: Project scaffold

**Files:**
- Create: `emerging-app-finder/requirements.txt`
- Create: `emerging-app-finder/src/agf/__init__.py`
- Create: `emerging-app-finder/pytest.ini`

- [ ] **Step 1: Create the directory layout and dependency file**

`emerging-app-finder/requirements.txt`:
```
httpx==0.28.1
pytest==8.3.4
```

- [ ] **Step 2: Create the package marker**

`emerging-app-finder/src/agf/__init__.py`:
```python
"""Emerging App Finder — PoC package."""
```

- [ ] **Step 3: Configure pytest to find src/ and mark integration tests**

`emerging-app-finder/pytest.ini`:
```ini
[pytest]
pythonpath = src
markers =
    integration: hits the live network (opt-in: `pytest -m integration`)
testpaths = tests
addopts = -m "not integration"
```

- [ ] **Step 4: Create venv and install**

Run:
```bash
cd emerging-app-finder && python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt
```
Expected: installs httpx + pytest with no errors.

- [ ] **Step 5: Commit**

```bash
git add emerging-app-finder/requirements.txt emerging-app-finder/src/agf/__init__.py emerging-app-finder/pytest.ini
git commit -m "chore: scaffold emerging-app-finder PoC package"
```

---

### Task 2: Strip Facebook's JSON guard prefix

Facebook GraphQL responses are prefixed with `for (;;);` to prevent hijacking. We must strip it before `json.loads`.

**Files:**
- Create: `emerging-app-finder/src/agf/fbjson.py`
- Test: `emerging-app-finder/tests/test_fbjson.py`

- [ ] **Step 1: Write the failing test**

`tests/test_fbjson.py`:
```python
from agf.fbjson import parse_fb_json


def test_strips_guard_prefix():
    assert parse_fb_json('for (;;);{"a": 1}') == {"a": 1}


def test_parses_plain_json():
    assert parse_fb_json('{"a": 2}') == {"a": 2}


def test_strips_with_leading_whitespace():
    assert parse_fb_json('  for (;;);{"a": 3}') == {"a": 3}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd emerging-app-finder && .venv/bin/pytest tests/test_fbjson.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agf.fbjson'`

- [ ] **Step 3: Write minimal implementation**

`src/agf/fbjson.py`:
```python
import json

_GUARD = "for (;;);"


def parse_fb_json(text: str) -> dict:
    """Strip Facebook's anti-hijack guard prefix, then parse JSON."""
    stripped = text.lstrip()
    if stripped.startswith(_GUARD):
        stripped = stripped[len(_GUARD):]
    return json.loads(stripped)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_fbjson.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agf/fbjson.py tests/test_fbjson.py
git commit -m "feat: strip Facebook JSON guard prefix"
```

---

### Task 3: Bootstrap LSD token + doc_id from page HTML

**Files:**
- Create: `emerging-app-finder/src/agf/bootstrap.py`
- Create: `emerging-app-finder/tests/fixtures/ads_library_page.html`
- Test: `emerging-app-finder/tests/test_bootstrap.py`

- [ ] **Step 1: Create a minimal HTML fixture**

`tests/fixtures/ads_library_page.html`:
```html
<!doctype html><html><head><script>
{"name":"LSD","data":[],"props":{"token":"AVxyz123"}}
... "AdLibrarySearchPaginationQuery" ... "doc_id":"27201872659451053" ...
</script></head><body></body></html>
```

- [ ] **Step 2: Write the failing test**

`tests/test_bootstrap.py`:
```python
from pathlib import Path
from agf.bootstrap import extract_lsd, extract_doc_id

HTML = (Path(__file__).parent / "fixtures" / "ads_library_page.html").read_text()


def test_extract_lsd():
    assert extract_lsd(HTML) == "AVxyz123"


def test_extract_doc_id_finds_value():
    assert extract_doc_id(HTML, fallback="000") == "27201872659451053"


def test_extract_doc_id_uses_fallback_when_missing():
    assert extract_doc_id("<html></html>", fallback="999") == "999"


def test_extract_lsd_raises_when_missing():
    import pytest
    with pytest.raises(ValueError):
        extract_lsd("<html></html>")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_bootstrap.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agf.bootstrap'`

- [ ] **Step 4: Write minimal implementation**

`src/agf/bootstrap.py`:
```python
import re

_LSD_RE = re.compile(r'"LSD",\[\],\{"token":"([^"]+)"')
_DOCID_RE = re.compile(
    r'AdLibrarySearchPaginationQuery.{0,400}?"doc_id":"(\d+)"', re.DOTALL
)


def extract_lsd(html: str) -> str:
    """Extract the LSD CSRF token from Ad Library page HTML."""
    m = _LSD_RE.search(html)
    if not m:
        raise ValueError("LSD token not found in page HTML")
    return m.group(1)


def extract_doc_id(html: str, fallback: str) -> str:
    """Self-heal the AdLibrarySearchPaginationQuery doc_id; fall back if absent.

    NOTE: in production (Plan 2) the doc_id often lives in a separate JS bundle,
    so the durable scraper fetches bundles too. For the PoC, the inline match
    plus a known-good fallback (live-verified 27201872659451053 on 2026-06-04)
    is sufficient.
    """
    m = _DOCID_RE.search(html)
    return m.group(1) if m else fallback
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_bootstrap.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add src/agf/bootstrap.py tests/fixtures/ads_library_page.html tests/test_bootstrap.py
git commit -m "feat: bootstrap LSD token and doc_id from Ad Library HTML"
```

---

### Task 4: Build the GraphQL request (variables + form body + jazoest)

**Files:**
- Create: `emerging-app-finder/src/agf/request.py`
- Test: `emerging-app-finder/tests/test_request.py`

- [ ] **Step 1: Write the failing test**

`tests/test_request.py`:
```python
import json
from agf.request import build_variables, jazoest, build_form_body


def test_build_variables_defaults():
    v = build_variables(query="workout app", countries=["US"])
    assert v["queryString"] == "workout app"
    assert v["countries"] == ["US"]
    assert v["activeStatus"] == "ACTIVE"
    assert v["adType"] == "ALL"
    assert v["searchType"] == "KEYWORD_UNORDERED"
    assert v["first"] == 30
    assert "cursor" not in v  # absent on page 1


def test_build_variables_with_cursor():
    v = build_variables(query="x", countries=["US"], cursor="CUR123")
    assert v["cursor"] == "CUR123"


def test_jazoest_formula():
    # '2' + sum(ord) ; 'AB' -> 65+66 = 131 -> "2131"
    assert jazoest("AB") == "2131"


def test_build_form_body_contains_required_fields():
    body = build_form_body(lsd="AVxyz", variables={"a": 1}, doc_id="555")
    assert body["lsd"] == "AVxyz"
    assert body["doc_id"] == "555"
    assert body["fb_api_req_friendly_name"] == "AdLibrarySearchPaginationQuery"
    assert body["jazoest"] == jazoest("AVxyz")
    assert json.loads(body["variables"]) == {"a": 1}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_request.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agf.request'`

- [ ] **Step 3: Write minimal implementation**

`src/agf/request.py`:
```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_request.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agf/request.py tests/test_request.py
git commit -m "feat: build Ad Library GraphQL request body"
```

---

### Task 5: Parse the search response into AdRecord objects

**Files:**
- Create: `emerging-app-finder/src/agf/parse.py`
- Create: `emerging-app-finder/tests/fixtures/search_response.json`
- Test: `emerging-app-finder/tests/test_parse.py`

- [ ] **Step 1: Create a representative response fixture**

`tests/fixtures/search_response.json`:
```json
{"data":{"ad_library_main":{"search_results_connection":{
  "edges":[{"node":{"collated_results":[
    {"ad_archive_id":"111","page_id":"P1","page_name":"FitGem","is_active":true,
     "start_date":1714500000,"end_date":null,"collation_count":7,
     "publisher_platform":["FACEBOOK","INSTAGRAM"],
     "snapshot":{"cta_type":"INSTALL_MOBILE_APP","cta_text":"Install",
       "link_url":"https://apps.apple.com/us/app/fitgem/id6470000001",
       "body":{"text":"Get fit in 7"},"title":"FitGem",
       "images":[{"original_image_url":"https://scontent.fbcdn.net/x.jpg"}],
       "videos":[{"video_hd_url":"https://video.fbcdn.net/y.mp4"}],"cards":[]}}
  ]}}],
  "page_info":{"has_next_page":true,"end_cursor":"CUR_NEXT"}
}}}}
```

- [ ] **Step 2: Write the failing test**

`tests/test_parse.py`:
```python
import json
from pathlib import Path
from agf.parse import parse_search_response, AdRecord

DATA = json.loads(
    (Path(__file__).parent / "fixtures" / "search_response.json").read_text()
)


def test_parse_returns_records_and_cursor():
    records, cursor, has_next = parse_search_response(DATA)
    assert cursor == "CUR_NEXT"
    assert has_next is True
    assert len(records) == 1


def test_record_fields():
    rec = parse_search_response(DATA)[0][0]
    assert isinstance(rec, AdRecord)
    assert rec.ad_archive_id == "111"
    assert rec.page_id == "P1"
    assert rec.page_name == "FitGem"
    assert rec.is_active is True
    assert rec.cta_type == "INSTALL_MOBILE_APP"
    assert rec.link_url == "https://apps.apple.com/us/app/fitgem/id6470000001"
    assert rec.collation_count == 7
    assert rec.start_date == 1714500000
    assert rec.image_urls == ["https://scontent.fbcdn.net/x.jpg"]
    assert rec.video_urls == ["https://video.fbcdn.net/y.mp4"]


def test_parse_empty_response_is_safe():
    records, cursor, has_next = parse_search_response({"data": {}})
    assert records == [] and cursor is None and has_next is False
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_parse.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agf.parse'`

- [ ] **Step 4: Write minimal implementation**

`src/agf/parse.py`:
```python
from dataclasses import dataclass, field


@dataclass
class AdRecord:
    ad_archive_id: str
    page_id: str
    page_name: str
    is_active: bool
    start_date: int | None
    end_date: int | None
    collation_count: int
    cta_type: str | None
    cta_text: str | None
    link_url: str | None
    body_text: str | None
    image_urls: list[str] = field(default_factory=list)
    video_urls: list[str] = field(default_factory=list)


def _connection(data: dict) -> dict:
    main = data.get("data", {}).get("ad_library_main") or data.get("data", {}).get(
        "adLibraryMain", {}
    )
    return main.get("search_results_connection") or main.get(
        "searchResultsConnection", {}
    )


def _to_record(node: dict) -> AdRecord:
    snap = node.get("snapshot") or {}
    body = snap.get("body") or {}
    return AdRecord(
        ad_archive_id=str(node.get("ad_archive_id", "")),
        page_id=str(node.get("page_id", "")),
        page_name=node.get("page_name", ""),
        is_active=bool(node.get("is_active")),
        start_date=node.get("start_date"),
        end_date=node.get("end_date"),
        collation_count=int(node.get("collation_count") or 0),
        cta_type=snap.get("cta_type"),
        cta_text=snap.get("cta_text"),
        link_url=snap.get("link_url"),
        body_text=body.get("text"),
        image_urls=[i["original_image_url"] for i in (snap.get("images") or []) if i.get("original_image_url")],
        video_urls=[v["video_hd_url"] for v in (snap.get("videos") or []) if v.get("video_hd_url")],
    )


def parse_search_response(data: dict) -> tuple[list[AdRecord], str | None, bool]:
    """Return (records, end_cursor, has_next_page)."""
    conn = _connection(data)
    records: list[AdRecord] = []
    for edge in conn.get("edges", []):
        for node in (edge.get("node", {}) or {}).get("collated_results", []):
            records.append(_to_record(node))
    page_info = conn.get("page_info") or conn.get("pageInfo") or {}
    cursor = page_info.get("end_cursor") or page_info.get("endCursor")
    has_next = bool(page_info.get("has_next_page") or page_info.get("hasNextPage"))
    return records, cursor, has_next
```

> Note: `parse_search_response(DATA)[0][0]` in the test indexes the records list (`[0]`) then the first record (`[0]`).

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_parse.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add src/agf/parse.py tests/fixtures/search_response.json tests/test_parse.py
git commit -m "feat: parse Ad Library search response into AdRecord"
```

---

### Task 6: Classify app-intent and extract the store ID

**Files:**
- Create: `emerging-app-finder/src/agf/classify.py`
- Test: `emerging-app-finder/tests/test_classify.py`

- [ ] **Step 1: Write the failing test**

`tests/test_classify.py`:
```python
from agf.parse import AdRecord
from agf.classify import classify_url, AppIntent


def _rec(link_url, cta="DOWNLOAD"):
    return AdRecord("1", "P", "N", True, 1, None, 1, cta, "x", link_url, "b")


def test_ios_app_store():
    out = classify_url("https://apps.apple.com/us/app/fitgem/id6470000001")
    assert out == AppIntent(kind="ios", store_id="6470000001")


def test_android_play_store():
    out = classify_url("https://play.google.com/store/apps/details?id=com.fit.gem")
    assert out == AppIntent(kind="android", store_id="com.fit.gem")


def test_facebook_shim_is_unwrapped():
    wrapped = "https://l.facebook.com/l.php?u=https%3A%2F%2Fapps.apple.com%2Fus%2Fapp%2Fx%2Fid42&h=AT"
    assert classify_url(wrapped) == AppIntent(kind="ios", store_id="42")


def test_plain_brand_domain_is_web():
    assert classify_url("https://getfitgem.com/quiz") == AppIntent(kind="web", store_id=None)


def test_none_when_no_url():
    assert classify_url(None) == AppIntent(kind="none", store_id=None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_classify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agf.classify'`

- [ ] **Step 3: Write minimal implementation**

`src/agf/classify.py`:
```python
import re
from dataclasses import dataclass
from urllib.parse import urlparse, parse_qs, unquote

_IOS_ID = re.compile(r"/id(\d+)")
_FB_SHIMS = {"l.facebook.com", "lm.facebook.com"}


@dataclass(frozen=True)
class AppIntent:
    kind: str          # "ios" | "android" | "web" | "none"
    store_id: str | None


def _unwrap(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc in _FB_SHIMS:
        u = parse_qs(parsed.query).get("u", [None])[0]
        if u:
            return unquote(u)
    return url


def classify_url(url: str | None) -> AppIntent:
    """Classify a destination URL as ios/android/web/none and extract store id."""
    if not url:
        return AppIntent("none", None)
    url = _unwrap(url)
    host = urlparse(url).netloc.lower()
    if host in {"apps.apple.com", "itunes.apple.com"}:
        m = _IOS_ID.search(urlparse(url).path)
        return AppIntent("ios", m.group(1) if m else None)
    if host in {"play.google.com"}:
        pkg = parse_qs(urlparse(url).query).get("id", [None])[0]
        return AppIntent("android", pkg)
    return AppIntent("web", None)
```

> Wrapped MMP deeplinks (`app.link`, `onelink.to`, `apple.co`) need a live iOS-UA redirect to resolve — deferred to Plan 2's resolver. The PoC classifies those as `web` (acceptable: they still surface as candidates, just unenriched).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_classify.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agf/classify.py tests/test_classify.py
git commit -m "feat: classify app-intent and extract store id"
```

---

### Task 7: Emerging flag from iTunes Lookup data

**Files:**
- Create: `emerging-app-finder/src/agf/enrich.py`
- Create: `emerging-app-finder/tests/fixtures/itunes_lookup.json`
- Test: `emerging-app-finder/tests/test_enrich.py`

- [ ] **Step 1: Create the iTunes Lookup fixture**

`tests/fixtures/itunes_lookup.json`:
```json
{"resultCount":1,"results":[{"trackId":6470000001,"trackName":"FitGem",
  "sellerName":"Gem Labs Inc","primaryGenreName":"Health & Fitness",
  "releaseDate":"2026-02-10T08:00:00Z","userRatingCount":340,
  "averageUserRating":4.6,"formattedPrice":"Free"}]}
```

- [ ] **Step 2: Write the failing test**

`tests/test_enrich.py`:
```python
import json
from datetime import datetime, timezone
from pathlib import Path
from agf.enrich import parse_lookup, AppMeta, emerging_from_release

LOOKUP = json.loads(
    (Path(__file__).parent / "fixtures" / "itunes_lookup.json").read_text()
)
NOW = datetime(2026, 6, 4, tzinfo=timezone.utc)


def test_parse_lookup():
    meta = parse_lookup(LOOKUP)
    assert meta == AppMeta(
        track_id="6470000001", name="FitGem", seller="Gem Labs Inc",
        genre="Health & Fitness", release_date="2026-02-10T08:00:00Z",
        rating_count=340, avg_rating=4.6,
    )


def test_parse_lookup_empty_returns_none():
    assert parse_lookup({"resultCount": 0, "results": []}) is None


def test_emerging_true_when_recent():
    assert emerging_from_release("2026-02-10T08:00:00Z", NOW, months=18) is True


def test_emerging_false_when_old():
    assert emerging_from_release("2022-01-01T00:00:00Z", NOW, months=18) is False
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_enrich.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agf.enrich'`

- [ ] **Step 4: Write minimal implementation**

`src/agf/enrich.py`:
```python
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class AppMeta:
    track_id: str
    name: str
    seller: str
    genre: str
    release_date: str
    rating_count: int
    avg_rating: float


def parse_lookup(payload: dict) -> AppMeta | None:
    """Parse an iTunes Lookup response into AppMeta (None if no result)."""
    results = payload.get("results") or []
    if not results:
        return None
    r = results[0]
    return AppMeta(
        track_id=str(r.get("trackId", "")),
        name=r.get("trackName", ""),
        seller=r.get("sellerName", ""),
        genre=r.get("primaryGenreName", ""),
        release_date=r.get("releaseDate", ""),
        rating_count=int(r.get("userRatingCount") or 0),
        avg_rating=float(r.get("averageUserRating") or 0.0),
    )


def emerging_from_release(release_date: str, now: datetime, months: int = 18) -> bool:
    """True if the app was first released within the last `months`."""
    released = datetime.fromisoformat(release_date.replace("Z", "+00:00"))
    if released.tzinfo is None:
        released = released.replace(tzinfo=timezone.utc)
    age_days = (now - released).days
    return age_days <= months * 30
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_enrich.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add src/agf/enrich.py tests/fixtures/itunes_lookup.json tests/test_enrich.py
git commit -m "feat: parse iTunes Lookup and compute emerging flag"
```

---

### Task 8: Scaling proxy — aggregate ads per advertiser

**Files:**
- Create: `emerging-app-finder/src/agf/scale.py`
- Test: `emerging-app-finder/tests/test_scale.py`

- [ ] **Step 1: Write the failing test**

`tests/test_scale.py`:
```python
from agf.parse import AdRecord
from agf.scale import aggregate_advertisers, AdvertiserScale, longevity_days


def _ad(page_id, archive, start, active=True):
    return AdRecord(archive, page_id, "FitGem", active, start, None, 1, "X", "x",
                    "https://apps.apple.com/us/app/x/id1", "b")


def test_longevity_days():
    # start 2026-05-05 (1746403200), now 2026-06-04 (1749000000) -> ~30 days
    assert longevity_days(1746403200, now_epoch=1749000000) == 30


def test_aggregate_counts_active_distinct_creatives():
    ads = [_ad("P1", "a1", 1746403200), _ad("P1", "a2", 1746403200),
           _ad("P1", "a1", 1746403200),  # duplicate archive id -> not double counted
           _ad("P2", "b1", 1748000000)]
    out = aggregate_advertisers(ads, now_epoch=1749000000)
    by_id = {a.page_id: a for a in out}
    assert by_id["P1"].active_creative_count == 2
    assert by_id["P2"].active_creative_count == 1
    assert isinstance(by_id["P1"], AdvertiserScale)


def test_max_longevity_is_oldest_active_creative():
    ads = [_ad("P1", "a1", 1746403200), _ad("P1", "a2", 1748000000)]
    out = aggregate_advertisers(ads, now_epoch=1749000000)
    assert out[0].max_longevity_days == 30
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_scale.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agf.scale'`

- [ ] **Step 3: Write minimal implementation**

`src/agf/scale.py`:
```python
from dataclasses import dataclass
from agf.parse import AdRecord

_SECONDS_PER_DAY = 86400


@dataclass
class AdvertiserScale:
    page_id: str
    page_name: str
    active_creative_count: int
    max_longevity_days: int


def longevity_days(start_epoch: int, now_epoch: int) -> int:
    """Whole days an ad has been running."""
    return max(0, (now_epoch - start_epoch) // _SECONDS_PER_DAY)


def aggregate_advertisers(ads: list[AdRecord], now_epoch: int) -> list[AdvertiserScale]:
    """Collapse ads to advertisers; count distinct active creatives + max longevity."""
    by_page: dict[str, dict] = {}
    for ad in ads:
        if not ad.is_active:
            continue
        bucket = by_page.setdefault(
            ad.page_id, {"name": ad.page_name, "ids": set(), "longevity": 0}
        )
        bucket["ids"].add(ad.ad_archive_id)
        if ad.start_date:
            bucket["longevity"] = max(bucket["longevity"], longevity_days(ad.start_date, now_epoch))
    return [
        AdvertiserScale(page_id=pid, page_name=b["name"],
                        active_creative_count=len(b["ids"]),
                        max_longevity_days=b["longevity"])
        for pid, b in by_page.items()
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_scale.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agf/scale.py tests/test_scale.py
git commit -m "feat: aggregate ads into per-advertiser scaling proxy"
```

---

### Task 9: PoC GemScore (simplified AND-gate)

The PoC scorer uses the available PoC signals only: scaling (active count + longevity), under-radar (low rating count), emerging (recent release). Full cohort-normalized GemScore with vetoes is Plan 5.

**Files:**
- Create: `emerging-app-finder/src/agf/score.py`
- Test: `emerging-app-finder/tests/test_score.py`

- [ ] **Step 1: Write the failing test**

`tests/test_score.py`:
```python
from agf.score import gem_score_poc


def test_famous_app_scores_near_zero():
    # huge rating base -> under_radar ~0 -> AND-gate collapses score
    s = gem_score_poc(active_count=40, longevity_days=120,
                      rating_count=5_000_000, is_emerging=True)
    assert s < 5


def test_emerging_scaler_scores_high():
    s = gem_score_poc(active_count=20, longevity_days=45,
                      rating_count=300, is_emerging=True)
    assert s > 50


def test_not_emerging_zeroes_score():
    s = gem_score_poc(active_count=20, longevity_days=45,
                      rating_count=300, is_emerging=False)
    assert s == 0.0


def test_no_longevity_means_no_scaling():
    s = gem_score_poc(active_count=20, longevity_days=2,
                      rating_count=300, is_emerging=True)
    assert s < 20
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_score.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agf.score'`

- [ ] **Step 3: Write minimal implementation**

`src/agf/score.py`:
```python
import math


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def gem_score_poc(active_count: int, longevity_days: int, rating_count: int,
                  is_emerging: bool) -> float:
    """Simplified PoC GemScore: multiplicative AND-gate of scaling x under-radar x emerging.

    Mirrors the design's core property — a famous brand (huge rating base)
    drives under_radar -> 0, collapsing the score. Returns 0..100.
    Full cohort z-scores, acceleration, FunnelIntent and vetoes are Plan 5.
    """
    if not is_emerging:
        return 0.0
    # scaling: more active creatives + longer longevity = higher (saturating)
    scaling = _clamp01(math.log1p(active_count) / math.log1p(50)) * _clamp01(longevity_days / 60)
    # under-radar: small rating base = high; log-scaled so 300 ~ high, 5M ~ 0
    under_radar = _clamp01(1 - math.log1p(rating_count) / math.log1p(1_000_000))
    score = 100 * (scaling ** 0.6) * (under_radar ** 0.4)
    return round(score, 1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_score.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agf/score.py tests/test_score.py
git commit -m "feat: simplified PoC GemScore AND-gate"
```

---

### Task 10: Live I/O wrappers (Ad Library + iTunes)

**Files:**
- Create: `emerging-app-finder/src/agf/livesearch.py`

- [ ] **Step 1: Write the live wrappers (no unit test — covered by Task 12 smoke test)**

`src/agf/livesearch.py`:
```python
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
```

- [ ] **Step 2: Verify it imports cleanly**

Run: `.venv/bin/python -c "import agf.livesearch"`
Expected: no output, exit 0.

- [ ] **Step 3: Commit**

```bash
git add src/agf/livesearch.py
git commit -m "feat: live httpx wrappers for Ad Library and iTunes Lookup"
```

---

### Task 11: Orchestrate the PoC run → ranked CSV

**Files:**
- Create: `emerging-app-finder/src/agf/run_poc.py`

- [ ] **Step 1: Write the orchestrator**

`src/agf/run_poc.py`:
```python
import csv
import sys
import time
import httpx
from agf.livesearch import bootstrap, search_us, itunes_lookup
from agf.classify import classify_url
from agf.enrich import parse_lookup, emerging_from_release
from agf.scale import aggregate_advertisers
from agf.score import gem_score_poc
from datetime import datetime, timezone


def run(queries: list[str], out_path: str = "gems.csv") -> int:
    now = datetime.now(timezone.utc)
    now_epoch = int(time.time())
    rows = []
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        lsd, doc_id = bootstrap(client)
        print(f"bootstrapped lsd={lsd[:6]}… doc_id={doc_id}", file=sys.stderr)
        for q in queries:
            ads = search_us(client, q, lsd, doc_id)
            print(f"  '{q}': {len(ads)} ads", file=sys.stderr)
            app_ads = [a for a in ads if classify_url(a.link_url).kind in ("ios", "android")]
            advertisers = {a.page_id: a for a in aggregate_advertisers(app_ads, now_epoch)}
            for ad in app_ads:
                intent = classify_url(ad.link_url)
                if intent.kind != "ios" or not intent.store_id:
                    continue
                meta = parse_lookup(itunes_lookup(client, intent.store_id))
                time.sleep(3)  # iTunes ~20 req/min
                if not meta:
                    continue
                emerging = emerging_from_release(meta.release_date, now)
                scale = advertisers[ad.page_id]
                score = gem_score_poc(scale.active_creative_count,
                                      scale.max_longevity_days, meta.rating_count, emerging)
                rows.append({
                    "score": score, "app": meta.name, "advertiser": ad.page_name,
                    "store_id": meta.track_id, "release_date": meta.release_date,
                    "rating_count": meta.rating_count,
                    "active_creatives": scale.active_creative_count,
                    "max_longevity_days": scale.max_longevity_days,
                    "emerging": emerging, "query": q,
                })
    # dedupe by store_id keeping highest score
    best = {}
    for r in rows:
        if r["store_id"] not in best or r["score"] > best[r["store_id"]]["score"]:
            best[r["store_id"]] = r
    ranked = sorted(best.values(), key=lambda r: r["score"], reverse=True)
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(ranked[0].keys()) if ranked else ["score"])
        w.writeheader()
        w.writerows(ranked)
    print(f"wrote {len(ranked)} apps to {out_path}", file=sys.stderr)
    return len(ranked)


if __name__ == "__main__":
    seeds = sys.argv[1:] or ["workout app", "calorie tracker", "habit tracker"]
    run(seeds)
```

- [ ] **Step 2: Verify it imports cleanly**

Run: `.venv/bin/python -c "import agf.run_poc"`
Expected: no output, exit 0.

- [ ] **Step 3: Commit**

```bash
git add src/agf/run_poc.py
git commit -m "feat: PoC orchestrator producing ranked gem CSV"
```

---

### Task 12: Live smoke test + README

**Files:**
- Create: `emerging-app-finder/tests/test_live_smoke.py`
- Create: `emerging-app-finder/README.md`

- [ ] **Step 1: Write an opt-in integration smoke test**

`tests/test_live_smoke.py`:
```python
import pytest
import httpx
from agf.livesearch import bootstrap, search_us, itunes_lookup
from agf.enrich import parse_lookup


@pytest.mark.integration
def test_bootstrap_and_search_returns_ads():
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        lsd, doc_id = bootstrap(client)
        assert lsd and doc_id
        ads = search_us(client, "workout app", lsd, doc_id, max_pages=1)
        assert len(ads) > 0, "expected at least one ad from a US keyword search"


@pytest.mark.integration
def test_itunes_lookup_live():
    with httpx.Client(timeout=30) as client:
        meta = parse_lookup(itunes_lookup(client, "389801252"))  # Instagram
        assert meta and meta.name
```

- [ ] **Step 2: Run the smoke test (manual, network)**

Run: `.venv/bin/pytest -m integration tests/test_live_smoke.py -v`
Expected: PASS if the live `doc_id` is current. If `test_bootstrap_and_search` returns 0 ads, the `doc_id` rotated → update `_FALLBACK_DOC_ID` in `livesearch.py` (capture the current one from browser DevTools Network tab) and re-run. This is the expected maintenance reality (see design §9).

- [ ] **Step 3: Run the full unit suite green**

Run: `.venv/bin/pytest -v`
Expected: all unit tests PASS, integration deselected (28 passed, 2 deselected).

- [ ] **Step 4: Write the README**

`README.md`:
```markdown
# Emerging App Finder — PoC

Proves: US Ad Library search → app-classify → emerging enrich → scaling proxy → ranked CSV.

## Setup
    python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt

## Run the unit tests
    .venv/bin/pytest -v

## Run the live smoke test (hits Facebook + Apple)
    .venv/bin/pytest -m integration -v

## Run the PoC end-to-end
    .venv/bin/python -m agf.run_poc "workout app" "calorie tracker"
    # → writes gems.csv ranked by PoC GemScore

## Maintenance
The Ad Library `doc_id` rotates (~weekly). If a live search returns 0 ads,
capture the current `AdLibrarySearchPaginationQuery` doc_id from browser
DevTools (Network tab) and update `_FALLBACK_DOC_ID` in `src/agf/livesearch.py`.
This fragility is why Plan 2 replaces this with Playwright GraphQL interception.

## Scope
PoC only: no DB, no proxies, no browser, no funnel/creative download.
See `docs/superpowers/specs/2026-06-04-emerging-meta-ads-app-finder-design.md`.
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_live_smoke.py README.md
git commit -m "test: live smoke test + PoC README"
```

---

## Self-review (completed)

- **Spec coverage:** PoC covers design §4 extraction (direct form-POST variant), §5 discovery (single keyword lane — full firehoses are Plan 4), §6 scoring (simplified AND-gate — full GemScore is Plan 5), §7 enrichment (app-store only — funnel/ads/company are Plan 6). Gaps are intentional and assigned to later plans.
- **Type consistency:** `AdRecord` (Task 5) is consumed unchanged by `classify_url` (6), `aggregate_advertisers` (8), and `run_poc` (11). `AppIntent.kind`, `AppMeta.release_date`, `AdvertiserScale.active_creative_count`/`max_longevity_days` names match across tasks.
- **Placeholder scan:** no TBD/TODO; every code step shows complete code; the only "deferred" items explicitly name the future plan.
- **The one risk that can fail Task 12:** a rotated `doc_id` → documented remediation inline (capture from DevTools / Plan 2 self-heal).

## Execution handoff — see end of message.
