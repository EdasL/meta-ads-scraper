# Plan 6 — Enrichment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For flagged gems only (top-N, never all ads), gather the full dossier — app-store metadata, funnel screenshots + pricing, deduped creative bytes → S3, company/WHOIS/tech — reusing Plan 2's `Extractor.resolve_deeplink` + Playwright and Plan 3's dedup + S3, and backfill advertiser enrichment (which Plan 5's disguised-incumbent veto reads).

**Architecture:** Three layers. (1) **Pure parsers** (`appstore.lookup_itunes_batch`, `funnel_capture.extract_pricing`/`autoconsent_cmp`/`resolve_landing`, `company.builtwith_tech`/`domain_age`/`publisher_from_app`) — stdlib/regex/JSON, no network in tests. (2) **External-dep wrappers** (Node `gp_lookup` sidecar for Google Play, Playwright `capture_funnel`, httpx `download_creative`) — dependency-injected so units run offline. (3) **Orchestrator** (`run_enrich.enrich_gem` + `make_enrich_handler`, the §3.9 worker adapter) that walks a gem's top-N ads: resolve landing → app-store metadata OR funnel capture + pricing → download deduped creatives → company enrichment, all conn-first (§3.3, no commit). Adds **no schema** — it backfills existing tables.

**Tech Stack:** Python 3.13 · httpx (iTunes Lookup + creative bytes) · Node + `facundoolano/google-play-scraper` (sidecar) · Playwright (optional/standby, funnel screenshots) · python-whois · Pillow/imagehash (Plan 3 dedup) · boto3/MinIO (Plan 3 S3) · SQLAlchemy Core + psycopg · pytest (pure-unit + live-Postgres/MinIO/Playwright integration).

---

## Frozen inputs this plan builds against (do not change)

- **§3.4 `Extractor.resolve_deeplink(url, ua='ios') -> ResolvedLink(final_url, intent: AppIntent(kind, store_id))`** — async. `resolve_landing` delegates to it (NO second resolver). `Extractor.search(...)` is used by the worker adapter to re-fetch fresh creative URLs (fbcdn signed URLs expire).
- **§3.3 repo** (`src/agf/db/repo.py`, conn-first, **never commits**): `upsert_app(conn, meta)`, `upsert_ad(conn, ar, advertiser_id, app_id)`, `upsert_creative(conn, ad_id, type, body_text, title, snapshot_image_url, s3_key, sha256, phash)` (sha-dedup-aware), `upsert_funnel(conn, ad_id, landing_url, final_url_after_redirect, screenshot_s3_key)`, `upsert_advertiser_enrichment(conn, advertiser_id, domain_id, parent_company_id, page_category, ig_username, ig_followers)`.
- **§3.6 dedup** (`src/agf/db/dedup.py`): `sha256_bytes(data)`, `phash_image(data)->hex|None`, `is_duplicate_creative(conn, sha256)->id|None` (exact). Plan 6 adds perceptual near-dup on top.
- **§3.8 S3** (`src/agf/storage/s3.py`): `get_s3()`, `put_bytes(key, data, content_type, client=None)->key`.
- **§3.9 worker** (`src/agf/workers/runner.py`): `run_worker(name, entity_type, handler, *, source, candidate_ids, due_before, limit)`; `handler(conn, entity_id)->str`. Cross-cutting deps arrive via an adapter closure.
- **§3.5 tables** (all exist; Plan 6 writes, creates none): `public.domains(id, domain UNIQUE)`, `public.advertisers(id, page_id, domain_id FK, parent_company_id self-FK, page_category, ig_*)`, `public.apps(UNIQUE(store,store_id), …, is_emerging)`, `public.ads(id, external_ad_id, advertiser_id, app_id, link_url, last_seen)`, `public.creatives(ad_id, type, s3_key, content_type, file_size, sha256, phash, snapshot_image_url; UNIQUE(sha256))`, `public.funnels(ad_id UNIQUE, landing_url, final_url_after_redirect, screenshot_s3_key)`.
- **Reused Plan 1 helpers:** `agf.enrich.parse_lookup(payload)->AppMeta` (uses `releaseDate`), `AppMeta(track_id, name, seller, genre, release_date, rating_count, avg_rating)`, `emerging_from_release(release_date, now, months=18)->bool`. `agf.classify.classify_url`/`AppIntent`.

## Hard rules carried from prior plans (each cost us something)

- **conn-first, never commit** inside Plan-6 write functions — the caller (worker/txn) owns the transaction.
- **iTunes Lookup: use `releaseDate`, NOT `currentVersionReleaseDate`** (research §2 — the latter is the last *update* and mis-tags old apps as new). `parse_lookup` already does this; Google Play release date is normalized to an ISO `releaseDateISO` in the sidecar.
- **fbcdn signed URLs expire → download creative bytes at scrape time** (the worker adapter re-fetches fresh creative URLs via `extractor.search` right before downloading).
- **Playwright is the optional/standby dep** (not installed by default; `pip install playwright` + `playwright install chromium`). Funnel capture is brittle + memory-heavy → **cap to one context + top-N**, screenshot is the ground truth (pricing extraction is best-effort on top).
- **Do not touch other phases' owned files.** Plan 6 owns only `src/agf/enrich_full/` + `tests/enrich_full/` + the `# --- plan-6 ---` requirements section. It needs a `public.domains` row to set `advertisers.domain_id`; since Plan 3's `repo.py` exposes no domain upsert, Plan 6 writes its own conn-first `_upsert_domain` against the frozen §3.5 schema (idiomatic — Plan 5 also wrote its own SQL).
- **Adds NO schema** (`db/schema/` untouched; backfills existing tables).
- **RUN THE ASSEMBLED THING LIVE before claiming done** (fakes + contract-adherence hid real bugs in Plans 4 and 5).

## Scope decisions (documented, v1)

- **`parent_company_id` is left NULL.** Automated parent-company detection needs a company graph we don't have; the Plan 5 disguised-incumbent veto already tolerates NULL. Plan 6 reliably backfills `domain_id` (and `page_category` when the caller supplies it).
- **Creative download = images via httpx.** `aria2c`/`ffmpeg` (bulk/video) are documented system deps for Plan 7's compose; v1 downloads image creatives by URL and stores the video `snapshot_image_url` without fetching video bytes. Dedup (sha256 + perceptual phash) applies to what is downloaded.
- **Near-dup phash** scans existing `creatives.phash` (top-N volume → fine); documented as a full-scan to revisit if creative volume grows.

## Files created by this plan

| File | Responsibility |
|---|---|
| `src/agf/enrich_full/__init__.py` | package marker |
| `src/agf/enrich_full/schemas.py` | frozen dataclasses: `ResolvedTarget`, `FunnelStep`, `PricingExtract`, `CreativeAsset`, `CompanyInfo`, `EnrichResult` |
| `src/agf/enrich_full/appstore.py` | `lookup_itunes_batch`, `lookup_google_play` |
| `src/agf/enrich_full/sidecar/gp_lookup.js` | Node google-play-scraper sidecar |
| `src/agf/enrich_full/sidecar/package.json` | sidecar Node deps |
| `src/agf/enrich_full/funnel_capture.py` | `resolve_landing`, `extract_pricing`, `autoconsent_cmp`, `capture_funnel` |
| `src/agf/enrich_full/creatives.py` | `is_duplicate` (sha+phash), `download_creative` |
| `src/agf/enrich_full/company.py` | `domain_age`, `builtwith_tech`, `publisher_from_app`, `enrich_company` |
| `src/agf/enrich_full/run_enrich.py` | `EnrichDeps`, `enrich_gem`, `make_enrich_handler` |
| `tests/enrich_full/{__init__.py,conftest.py,fixtures/,test_*.py}` | tests |
| `requirements.txt` | append `# --- plan-6 ---` (python-whois) |

## Task ↔ master-plan mapping (built pure leaves → external wrappers → live e2e)

| Plan task | master-plan | what |
|---|---|---|
| Task 0 | — | scaffold package + pin python-whois |
| Task 1 | P6-T1 | `schemas.py` frozen dataclasses |
| Task 2 | P6-T2 | `lookup_itunes_batch` |
| Task 3 | P6-T4 | `resolve_landing` (delegates to `resolve_deeplink`) |
| Task 4 | P6-T7 | `extract_pricing` |
| Task 5 | P6-T5 | `autoconsent_cmp` |
| Task 6 | P6-T9 | `company.py` (`domain_age`/`builtwith_tech`/`publisher_from_app`/`enrich_company`) |
| Task 7 | P6-T3 | Node `gp_lookup.js` + `lookup_google_play` |
| Task 8 | P6-T8 | `creatives.py` (`is_duplicate` + `download_creative`) |
| Task 9 | P6-T6 | `capture_funnel` (Playwright) |
| Task 10 | P6-T10 | `enrich_gem` + `make_enrich_handler` + LIVE e2e |
| Task 11 | — | full-suite + live assembled smoke + docs + merge |

---

## Task 0: Scaffold package + pin python-whois

**Files:**
- Create: `src/agf/enrich_full/__init__.py`, `tests/enrich_full/__init__.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Create empty package markers** — both `__init__.py` files empty.

- [ ] **Step 2: Append the plan-6 deps to `requirements.txt`**

```
# --- plan-6 ---
python-whois==0.9.5    # domain_age (WHOIS creation_date) in company.py
# Node sidecar (Android enrichment) deps are NOT pip — see src/agf/enrich_full/sidecar/package.json
#   cd src/agf/enrich_full/sidecar && npm install   (needs Node)
# Playwright is the OPTIONAL standby (funnel capture); install only if used:
#   playwright==1.49.0   (+ `playwright install chromium`)
```

- [ ] **Step 3: Install + smoke-import**

Run: `.venv/bin/pip install -r requirements.txt && .venv/bin/python -c "import whois; print('whois ok')"`
Expected: prints `whois ok`

- [ ] **Step 4: Commit**

```bash
git add src/agf/enrich_full/__init__.py tests/enrich_full/__init__.py requirements.txt
git commit -m "chore(plan-6): scaffold enrich_full package, pin python-whois"
```

---

## Task 1 (P6-T1): `schemas.py` — frozen dataclasses

**Files:**
- Create: `src/agf/enrich_full/schemas.py`
- Test: `tests/enrich_full/test_schemas.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/enrich_full/test_schemas.py
import dataclasses
import pytest
from agf.enrich_full.schemas import (
    ResolvedTarget, FunnelStep, PricingExtract, CreativeAsset, CompanyInfo, EnrichResult,
)


def test_construct_all_schemas():
    rt = ResolvedTarget(kind="ios", store_id="42", landing_url="https://l", final_url="https://f")
    fs = FunnelStep(step=1, url="https://f", screenshot_s3_key="k", title="t")
    pe = PricingExtract(prices=("9.99",), currency="USD", trial="7-day free")
    ca = CreativeAsset(type="image", sha256="s", phash="p", s3_key="k",
                       content_type="image/png", file_size=12, creative_id=3, dedup_of=None)
    ci = CompanyInfo(domain="x.com", domain_id=1, created_date=None, domain_age_days=100,
                     registrar="R", tech=("Stripe",), publisher="Acme")
    er = EnrichResult(advertiser_id=5, n_ads_processed=2, funnels=1, creatives=3, company=ci)
    assert rt.store_id == "42" and fs.step == 1 and pe.currency == "USD"
    assert ca.dedup_of is None and ci.tech == ("Stripe",) and er.company is ci


def test_schemas_are_frozen():
    rt = ResolvedTarget(kind="web", store_id=None, landing_url="l", final_url="f")
    with pytest.raises(dataclasses.FrozenInstanceError):
        rt.kind = "ios"


def test_optional_defaults():
    assert PricingExtract().prices == () and PricingExtract().currency is None
    assert EnrichResult(advertiser_id=1, n_ads_processed=0).company is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/enrich_full/test_schemas.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'agf.enrich_full.schemas'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agf/enrich_full/schemas.py
"""Frozen result schemas for enrichment (master-plan §6 P6-T1)."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ResolvedTarget:
    kind: str                 # 'ios' | 'android' | 'web' | 'none'
    store_id: str | None
    landing_url: str          # the ad's original link_url
    final_url: str            # after redirect resolution


@dataclass(frozen=True)
class FunnelStep:
    step: int
    url: str
    screenshot_s3_key: str | None
    title: str | None


@dataclass(frozen=True)
class PricingExtract:
    prices: tuple = ()        # ('9.99', '59.99')
    currency: str | None = None
    trial: str | None = None


@dataclass(frozen=True)
class CreativeAsset:
    type: str                 # 'image' | 'video'
    sha256: str
    phash: str | None
    s3_key: str | None
    content_type: str | None
    file_size: int | None
    creative_id: int | None   # row id (existing on dedup, new otherwise)
    dedup_of: int | None      # existing creative id if this was a dup, else None


@dataclass(frozen=True)
class CompanyInfo:
    domain: str | None
    domain_id: int | None
    created_date: object | None    # date | None
    domain_age_days: int | None
    registrar: str | None
    tech: tuple = ()
    publisher: str | None = None


@dataclass(frozen=True)
class EnrichResult:
    advertiser_id: int
    n_ads_processed: int
    funnels: int = 0
    creatives: int = 0
    company: CompanyInfo | None = None
```

- [ ] **Step 4: Run test to verify it passes** — `.venv/bin/pytest tests/enrich_full/test_schemas.py -q` → PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agf/enrich_full/schemas.py tests/enrich_full/test_schemas.py
git commit -m "feat(plan-6): frozen enrichment schemas (ResolvedTarget/FunnelStep/PricingExtract/CreativeAsset/CompanyInfo/EnrichResult)"
```

---

## Task 2 (P6-T2): `appstore.lookup_itunes_batch`

Batch iOS lookups in ≤200 chunks, reusing Plan 1 `parse_lookup` per result so `releaseDate` handling stays in one place.

**Files:**
- Create: `src/agf/enrich_full/appstore.py` (`lookup_itunes_batch`; `lookup_google_play` added in Task 7)
- Test: `tests/enrich_full/test_appstore.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/enrich_full/test_appstore.py
from agf.enrich_full.appstore import lookup_itunes_batch


class FakeITunes:
    """Echoes back an iTunes result per requested id; records each GET's id-param."""
    def __init__(self):
        self.calls = []

    def get(self, url, params=None):
        ids = (params or {}).get("id", "").split(",")
        self.calls.append(ids)
        results = [{"trackId": int(i), "trackName": f"App {i}", "sellerName": "Dev",
                    "primaryGenreName": "Health & Fitness",
                    "releaseDate": "2026-05-20T07:00:00Z",
                    "userRatingCount": 123, "averageUserRating": 4.5} for i in ids if i]
        return _Resp({"resultCount": len(results), "results": results})


class _Resp:
    def __init__(self, payload): self._p = payload
    def json(self): return self._p


def test_batches_in_chunks_and_keys_by_track_id():
    cl = FakeITunes()
    out = lookup_itunes_batch(cl, ["111", "222", "333"], chunk=2)
    assert len(cl.calls) == 2                     # 3 ids, chunk 2 -> 2 GETs
    assert cl.calls[0] == ["111", "222"] and cl.calls[1] == ["333"]
    assert set(out) == {"111", "222", "333"}
    assert out["111"].name == "App 111"
    assert out["111"].release_date == "2026-05-20T07:00:00Z"   # releaseDate, not currentVersion
    assert out["222"].rating_count == 123


def test_empty_ids_no_calls():
    cl = FakeITunes()
    assert lookup_itunes_batch(cl, []) == {}
    assert cl.calls == []
```

- [ ] **Step 2: Run test to verify it fails** — Expected: `ModuleNotFoundError: No module named 'agf.enrich_full.appstore'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agf/enrich_full/appstore.py
"""App-store metadata enrichment (master-plan §6 P6-T2/T3).

iTunes Lookup (batch) reuses Plan 1 parse_lookup; Google Play goes through a Node
sidecar. RESEARCH RULE: use releaseDate (first release) for the emerging gate, NOT
currentVersionReleaseDate (last update)."""
import json
import subprocess
from pathlib import Path
from agf.enrich import parse_lookup, AppMeta

_ITUNES_LOOKUP = "https://itunes.apple.com/lookup"
_SIDECAR = Path(__file__).parent / "sidecar" / "gp_lookup.js"


def _chunks(seq, n):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def lookup_itunes_batch(client, ids, *, chunk=200, country="us") -> dict:
    """Look up iOS apps by trackId in ≤chunk batches -> {track_id: AppMeta}.
    Reuses Plan 1 parse_lookup per result (single source of releaseDate truth)."""
    out: dict = {}
    for batch in _chunks([str(i) for i in ids if i], chunk):
        resp = client.get(_ITUNES_LOOKUP, params={
            "id": ",".join(batch), "entity": "software", "country": country})
        payload = resp.json() or {}
        for r in payload.get("results", []):
            meta = parse_lookup({"results": [r]})
            if meta and meta.track_id:
                out[meta.track_id] = meta
    return out
```

- [ ] **Step 4: Run test to verify it passes** — `.venv/bin/pytest tests/enrich_full/test_appstore.py -q` → PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agf/enrich_full/appstore.py tests/enrich_full/test_appstore.py
git commit -m "feat(plan-6): lookup_itunes_batch (200-chunk, reuses Plan 1 parse_lookup, releaseDate)"
```

---

## Task 3 (P6-T4): `funnel_capture.resolve_landing`

Delegates to the Plan 2 `Extractor.resolve_deeplink` (NO second resolver) and wraps the `ResolvedLink` as a `ResolvedTarget` (adds the original landing URL).

**Files:**
- Create: `src/agf/enrich_full/funnel_capture.py` (`resolve_landing`; `extract_pricing`/`autoconsent_cmp`/`capture_funnel` appended later)
- Create: `tests/enrich_full/conftest.py`
- Test: `tests/enrich_full/test_resolve_landing.py`

- [ ] **Step 1: Write the shared fakes + the failing test**

```python
# tests/enrich_full/conftest.py
from agf.classify import classify_url
from agf.extract.deeplink import ResolvedLink


class FakeExtractor:
    """Async stand-in for the Plan 2 facade (resolve_deeplink + search)."""
    def __init__(self, links=None, search_results=None):
        self.links = links or {}                     # url -> ResolvedLink
        self.search_results = search_results or {}    # view_all_page_id -> [AdRecord]
        self.resolved = []

    async def resolve_deeplink(self, url, ua="ios"):
        self.resolved.append(url)
        return self.links.get(url, ResolvedLink(url, classify_url(url)))

    async def search(self, *, query="", countries=("US",), active_status="ACTIVE",
                     sort_by_impressions=False, search_type="KEYWORD_UNORDERED",
                     view_all_page_id=None, max_pages=10):
        return list(self.search_results.get(view_all_page_id, []))
```

```python
# tests/enrich_full/test_resolve_landing.py
import asyncio
from agf.classify import AppIntent
from agf.extract.deeplink import ResolvedLink
from agf.enrich_full.schemas import ResolvedTarget
from agf.enrich_full.funnel_capture import resolve_landing
from tests.enrich_full.conftest import FakeExtractor


def test_resolves_mmp_to_ios_target():
    ex = FakeExtractor(links={"https://x.app.link/a":
                              ResolvedLink("https://apps.apple.com/us/app/x/id77",
                                           AppIntent("ios", "77"))})
    out = asyncio.run(resolve_landing(ex, "https://x.app.link/a"))
    assert out == ResolvedTarget(kind="ios", store_id="77",
                                 landing_url="https://x.app.link/a",
                                 final_url="https://apps.apple.com/us/app/x/id77")


def test_plain_web_funnel_target():
    ex = FakeExtractor()
    out = asyncio.run(resolve_landing(ex, "https://getgem.com/quiz"))
    assert out.kind == "web" and out.store_id is None
    assert out.landing_url == "https://getgem.com/quiz"
    assert ex.resolved == ["https://getgem.com/quiz"]   # delegated to the extractor
```

- [ ] **Step 2: Run test to verify it fails** — Expected: `ImportError: cannot import name 'resolve_landing'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agf/enrich_full/funnel_capture.py
"""Funnel handling (master-plan §6 P6-T4/T5/T6/T7): resolve the landing URL (via the
Plan 2 resolver — no second resolver), dismiss CMP banners, screenshot the funnel
step-by-step → S3, and best-effort pricing extraction. Playwright is the optional
standby dep (lazy-imported)."""
from agf.enrich_full.schemas import ResolvedTarget


async def resolve_landing(extractor, url, ua="ios") -> ResolvedTarget:
    """Resolve an ad's destination via the Plan 2 Extractor.resolve_deeplink (§3.4).
    Returns a ResolvedTarget carrying both the original landing URL and the final URL."""
    resolved = await extractor.resolve_deeplink(url, ua=ua)
    return ResolvedTarget(kind=resolved.intent.kind, store_id=resolved.intent.store_id,
                          landing_url=url, final_url=resolved.final_url)
```

- [ ] **Step 4: Run test to verify it passes** — `.venv/bin/pytest tests/enrich_full/test_resolve_landing.py -q` → PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agf/enrich_full/funnel_capture.py tests/enrich_full/conftest.py tests/enrich_full/test_resolve_landing.py
git commit -m "feat(plan-6): resolve_landing delegating to Plan 2 Extractor.resolve_deeplink"
```

---

## Task 4 (P6-T7): `funnel_capture.extract_pricing`

Pure parser. JSON-LD `Offer` first, regex fallback over visible text. None when nothing pricing-like is found (the screenshot stays the ground truth).

**Files:**
- Modify: `src/agf/enrich_full/funnel_capture.py` (append)
- Create: `tests/enrich_full/fixtures/paywall_jsonld.html`, `tests/enrich_full/fixtures/paywall_text.html`, `tests/enrich_full/fixtures/no_pricing.html`
- Test: `tests/enrich_full/test_extract_pricing.py`

- [ ] **Step 1: Write the fixtures + failing test**

```html
<!-- tests/enrich_full/fixtures/paywall_jsonld.html -->
<html><head>
<script type="application/ld+json">
{"@type":"Product","name":"Pro","offers":[
  {"@type":"Offer","price":"9.99","priceCurrency":"USD"},
  {"@type":"Offer","price":"59.99","priceCurrency":"USD"}]}
</script></head><body>Start your 7-day free trial</body></html>
```

```html
<!-- tests/enrich_full/fixtures/paywall_text.html -->
<html><body>
<h1>Unlock Premium</h1>
<p>Only $12.99/month or $99.00/year</p>
<p>Includes a free trial</p>
</body></html>
```

```html
<!-- tests/enrich_full/fixtures/no_pricing.html -->
<html><body><h1>About us</h1><p>We make great software.</p></body></html>
```

```python
# tests/enrich_full/test_extract_pricing.py
from pathlib import Path
from agf.enrich_full.funnel_capture import extract_pricing

FIX = Path(__file__).parent / "fixtures"


def _read(name): return (FIX / name).read_text()


def test_jsonld_offers_and_trial():
    p = extract_pricing(_read("paywall_jsonld.html"))
    assert p is not None
    assert p.prices == ("9.99", "59.99")
    assert p.currency == "USD"
    assert "7-day free" in (p.trial or "")


def test_text_fallback_prices_and_trial():
    p = extract_pricing(_read("paywall_text.html"))
    assert p is not None
    assert "12.99" in p.prices and "99.00" in p.prices
    assert p.currency == "USD"            # from the $ symbol
    assert p.trial and "free trial" in p.trial.lower()


def test_no_pricing_returns_none():
    assert extract_pricing(_read("no_pricing.html")) is None
    assert extract_pricing("") is None
```

- [ ] **Step 2: Run test to verify it fails** — Expected: `ImportError: cannot import name 'extract_pricing'`

- [ ] **Step 3: Append the implementation**

```python
# --- append to src/agf/enrich_full/funnel_capture.py ---
import json
import re
from html import unescape
from agf.enrich_full.schemas import PricingExtract

_JSONLD = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL)
_PRICE = re.compile(r'[$€£]\s?(\d{1,4}(?:[.,]\d{2})?)')
_CCY_SYM = {"$": "USD", "€": "EUR", "£": "GBP"}
_CCY_WORD = re.compile(r'\b(USD|EUR|GBP)\b', re.IGNORECASE)
_TRIAL = re.compile(
    r'(\d+[\s-]?(?:day|week|month)s?\s+free(?:\s+trial)?'
    r'|free\s+(?:\d+[\s-]?(?:day|week|month)s?\s+)?trial)', re.IGNORECASE)


def _walk_jsonld(obj, prices, ccy):
    if isinstance(obj, dict):
        if "price" in obj and obj["price"] is not None:
            prices.append(str(obj["price"]))
        if obj.get("priceCurrency"):
            ccy.append(str(obj["priceCurrency"]))
        for v in obj.values():
            _walk_jsonld(v, prices, ccy)
    elif isinstance(obj, list):
        for v in obj:
            _walk_jsonld(v, prices, ccy)


def extract_pricing(html) -> PricingExtract | None:
    """Pull pricing/trial from a paywall page. JSON-LD Offer first, regex fallback.
    None when nothing pricing-like is found."""
    html = html or ""
    prices, ccy = [], []
    for block in _JSONLD.findall(html):
        try:
            _walk_jsonld(json.loads(block.strip()), prices, ccy)
        except (ValueError, TypeError):
            continue
    currency = ccy[0].upper() if ccy else None
    text = unescape(html)
    if not prices:                                    # fallback: visible-text prices
        prices = [m.group(1).replace(",", ".") for m in _PRICE.finditer(text)]
        if currency is None:
            sym = next((c for c in _CCY_SYM if c in text), None)
            mw = _CCY_WORD.search(text)
            currency = _CCY_SYM.get(sym) or (mw.group(1).upper() if mw else None)
    seen, uniq = set(), []
    for p in prices:
        if p not in seen:
            seen.add(p); uniq.append(p)
    mt = _TRIAL.search(text)
    trial = mt.group(0).strip() if mt else None
    if not uniq and not trial:
        return None
    return PricingExtract(prices=tuple(uniq), currency=currency, trial=trial)
```

- [ ] **Step 4: Run test to verify it passes** — `.venv/bin/pytest tests/enrich_full/test_extract_pricing.py -q` → PASS (3 passed).

> If the text fixture's currency comes back from a stray word, the `$`-symbol path takes precedence (it's checked first); keep the rule, nudge the fixture only if a price regex over-matches.

- [ ] **Step 5: Commit**

```bash
git add src/agf/enrich_full/funnel_capture.py tests/enrich_full/fixtures/paywall_jsonld.html tests/enrich_full/fixtures/paywall_text.html tests/enrich_full/fixtures/no_pricing.html tests/enrich_full/test_extract_pricing.py
git commit -m "feat(plan-6): extract_pricing (JSON-LD Offer + regex fallback, trial detection)"
```

---

## Task 5 (P6-T5): `funnel_capture.autoconsent_cmp`

Dismiss a CMP/cookie banner so the screenshot isn't obscured. Selector sweep (OneTrust/Cookiebot/Quantcast/generic); best-effort, errors swallowed.

**Files:**
- Modify: `src/agf/enrich_full/funnel_capture.py` (append)
- Test: `tests/enrich_full/test_autoconsent.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/enrich_full/test_autoconsent.py
from agf.enrich_full.funnel_capture import autoconsent_cmp, _CMP_ACCEPT_SELECTORS


class FakeEl:
    def __init__(self): self.clicked = False
    def click(self): self.clicked = True


class FakePage:
    def __init__(self, present):       # present: set of selectors that exist
        self.present = set(present)
        self.els = {}

    def query_selector(self, sel):
        if sel in self.present:
            self.els.setdefault(sel, FakeEl())
            return self.els[sel]
        return None


def test_dismisses_known_cmp():
    sel = _CMP_ACCEPT_SELECTORS[0]
    page = FakePage({sel})
    assert autoconsent_cmp(page) is True
    assert page.els[sel].clicked is True


def test_no_banner_returns_false():
    assert autoconsent_cmp(FakePage(set())) is False
```

- [ ] **Step 2: Run test to verify it fails** — Expected: `ImportError: cannot import name 'autoconsent_cmp'`

- [ ] **Step 3: Append the implementation**

```python
# --- append to src/agf/enrich_full/funnel_capture.py ---
_CMP_ACCEPT_SELECTORS = (
    "#onetrust-accept-btn-handler",
    "#CybotCookiebotDialogBodyButtonAccept",
    "#accept-recommended-btn-handler",
    ".fc-cta-consent",                       # Google Funding Choices
    "[aria-label='Accept all']",
    "[aria-label='Accept All']",
    "button[mode='primary']",                # Quantcast
)


def autoconsent_cmp(page) -> bool:
    """Dismiss a cookie/consent banner (selector sweep). True if one was dismissed,
    False if none present. Best-effort: per-selector errors are swallowed."""
    for sel in _CMP_ACCEPT_SELECTORS:
        try:
            el = page.query_selector(sel)
        except Exception:
            el = None
        if el:
            try:
                el.click()
                return True
            except Exception:
                continue
    return False
```

- [ ] **Step 4: Run test to verify it passes** — `.venv/bin/pytest tests/enrich_full/test_autoconsent.py -q` → PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agf/enrich_full/funnel_capture.py tests/enrich_full/test_autoconsent.py
git commit -m "feat(plan-6): autoconsent_cmp — selector-sweep CMP banner dismissal"
```

---

## Task 6 (P6-T9): `company.py` — domain age / tech / publisher + DB backfill

**Files:**
- Create: `src/agf/enrich_full/company.py`
- Test: `tests/enrich_full/test_company.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/enrich_full/test_company.py
from datetime import date, datetime
from sqlalchemy import text
from agf.parse import AdRecord
from agf.enrich import AppMeta
from agf.db import repo
from agf.enrich_full.company import (
    domain_age, builtwith_tech, publisher_from_app, enrich_company,
)


class FakeWhois:
    def __init__(self, rec): self.rec = rec
    def __call__(self, domain): return self.rec


def _rec(**kw):
    from types import SimpleNamespace
    base = dict(creation_date=datetime(2020, 1, 1), registrar="NameCheap")
    base.update(kw)
    return SimpleNamespace(**base)


def test_domain_age_computes_days_and_registrar():
    created, age, reg = domain_age("x.com", whois_fn=FakeWhois(_rec()), now=date(2026, 6, 4))
    assert created == date(2020, 1, 1) and reg == "NameCheap"
    assert age == (date(2026, 6, 4) - date(2020, 1, 1)).days


def test_domain_age_handles_list_dates_and_failures():
    rec = _rec(creation_date=[datetime(2021, 5, 1), datetime(2021, 6, 1)])
    created, age, _ = domain_age("x.com", whois_fn=FakeWhois(rec), now=date(2026, 6, 4))
    assert created == date(2021, 5, 1)

    def boom(_): raise RuntimeError("whois down")
    assert domain_age("x.com", whois_fn=boom) == (None, None, None)
    assert domain_age(None) == (None, None, None)


def test_builtwith_tech_detects_from_html_and_headers():
    html = '<script src="https://js.stripe.com/v3"></script><div data-reactroot>'
    tech = builtwith_tech(html, headers={"x-shopify-stage": "production"})
    assert "Stripe" in tech and "React" in tech and "Shopify" in tech


def test_publisher_from_app_accepts_meta_or_dict():
    m = AppMeta("1", "X", "Acme Inc", "Health & Fitness", "2026-01-01", 10, 4.0)
    assert publisher_from_app(m) == "Acme Inc"
    assert publisher_from_app({"developer": "Beta LLC"}) == "Beta LLC"
    assert publisher_from_app(None) is None


def _ad():
    return AdRecord("CADV", "PCO", "GemCo", True, 1_714_500_000, None, 1,
                    "INSTALL_MOBILE_APP", "Install", "https://getgem.com", "body")


def test_enrich_company_backfills_domain_id(pg_conn):
    adv = repo.upsert_advertiser(pg_conn, _ad())
    info = enrich_company(pg_conn, adv, domain="getgem.com",
                          html='<script src="https://js.stripe.com/v3"></script>',
                          app_meta=AppMeta("1", "X", "Acme", "G", "2026-01-01", 1, 4.0),
                          whois_fn=FakeWhois(_rec()), now=date(2026, 6, 4))
    assert info.domain == "getgem.com" and info.domain_id is not None
    assert "Stripe" in info.tech and info.publisher == "Acme"
    row = pg_conn.execute(text("SELECT domain_id FROM public.advertisers WHERE id=:i"),
                          {"i": adv}).first()
    assert row.domain_id == info.domain_id
    dom = pg_conn.execute(text("SELECT domain FROM public.domains WHERE id=:i"),
                          {"i": info.domain_id}).scalar()
    assert dom == "getgem.com"
```

- [ ] **Step 2: Run test to verify it fails** — Expected: `ModuleNotFoundError: No module named 'agf.enrich_full.company'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agf/enrich_full/company.py
"""Company/domain enrichment (master-plan §6 P6-T9): WHOIS domain age, a lightweight
BuiltWith-style tech fingerprint (page source + headers, no paid API), and the app
publisher. Backfills advertiser enrichment (§3.3 domain_id/page_category);
parent_company_id is left NULL (the Plan 5 veto tolerates it — see Scope decisions)."""
from datetime import date, datetime, timezone
from sqlalchemy import text
from agf.db import repo
from agf.enrich_full.schemas import CompanyInfo

# source-code signatures -> tech name (extends the Firehose-C spirit to general stack)
TECH_SIGNATURES = {
    "Shopify": ["cdn.shopify.com", "x-shopify", "Shopify.theme"],
    "Stripe": ["js.stripe.com", "stripe.com/v3"],
    "RevenueCat": ["api.revenuecat.com", "@revenuecat/purchases-js"],
    "Superwall": ["cdn.superwall.me", "superwall"],
    "Next.js": ["/_next/static", "__NEXT_DATA__"],
    "React": ["data-reactroot", "react.production.min.js", "__REACT_DEVTOOLS"],
    "Webflow": ["assets.website-files.com", "webflow.js"],
    "Meta Pixel": ["connect.facebook.net", "fbevents.js", "fbq("],
    "Google Analytics": ["googletagmanager.com/gtag", "google-analytics.com/analytics.js"],
    "Typeform": ["embed.typeform.com"],
    "Framer": ["framerusercontent.com"],
    "WordPress": ["wp-content/", "wp-includes/"],
}


def _coerce_date(v):
    if isinstance(v, list):
        v = next((x for x in v if x is not None), None)
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


def domain_age(domain, *, whois_fn=None, now=None):
    """(created_date, age_days, registrar) via WHOIS. whois_fn injectable for tests;
    any failure degrades to (None, None, None)."""
    if not domain:
        return (None, None, None)
    if whois_fn is None:
        import whois
        whois_fn = whois.whois
    try:
        rec = whois_fn(domain)
    except Exception:
        return (None, None, None)
    get = (rec.get if isinstance(rec, dict) else lambda k: getattr(rec, k, None))
    created = _coerce_date(get("creation_date"))
    registrar = get("registrar")
    age = None
    if created:
        ref = now or datetime.now(timezone.utc).date()
        age = (ref - created).days
    return (created, age, registrar)


def builtwith_tech(html, headers=None) -> tuple:
    """Lightweight tech fingerprint from page source + response headers (substring
    match, no paid API). Returns sorted tech names."""
    hay = html or ""
    if headers:
        hay += " " + " ".join(f"{k}:{v}" for k, v in dict(headers).items())
    found = {name for name, sigs in TECH_SIGNATURES.items() if any(s in hay for s in sigs)}
    return tuple(sorted(found))


def publisher_from_app(app_meta) -> str | None:
    """The app's developer/seller name (AppMeta or dict)."""
    if app_meta is None:
        return None
    if isinstance(app_meta, dict):
        return app_meta.get("seller") or app_meta.get("developer")
    return getattr(app_meta, "seller", None)


def _upsert_domain(conn, domain) -> int | None:
    if not domain:
        return None
    return conn.execute(text("""
        INSERT INTO public.domains (domain) VALUES (:d)
        ON CONFLICT (domain) DO UPDATE SET domain = EXCLUDED.domain
        RETURNING id"""), {"d": domain}).scalar_one()


def enrich_company(conn, advertiser_id, *, domain=None, html=None, headers=None,
                   app_meta=None, page_category=None, whois_fn=None, now=None) -> CompanyInfo:
    """Compute + persist company enrichment: upsert the domain, backfill the
    advertiser's domain_id/page_category (§3.3), return the gathered CompanyInfo."""
    domain_id = _upsert_domain(conn, domain)
    created, age, registrar = domain_age(domain, whois_fn=whois_fn, now=now)
    tech = builtwith_tech(html, headers)
    publisher = publisher_from_app(app_meta)
    repo.upsert_advertiser_enrichment(conn, advertiser_id, domain_id=domain_id,
                                      page_category=page_category)
    return CompanyInfo(domain=domain, domain_id=domain_id, created_date=created,
                       domain_age_days=age, registrar=registrar, tech=tech,
                       publisher=publisher)
```

- [ ] **Step 4: Run test to verify it passes** — `.venv/bin/pytest tests/enrich_full/test_company.py -q` (needs DB env for the last test) → PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agf/enrich_full/company.py tests/enrich_full/test_company.py
git commit -m "feat(plan-6): company.py — WHOIS domain_age, builtwith_tech, publisher, enrich_company (domain_id backfill)"
```

---

## Task 7 (P6-T3): Node `gp_lookup.js` sidecar + `lookup_google_play`

**Files:**
- Create: `src/agf/enrich_full/sidecar/gp_lookup.js`, `src/agf/enrich_full/sidecar/package.json`
- Modify: `src/agf/enrich_full/appstore.py` (append `lookup_google_play`)
- Test: `tests/enrich_full/test_google_play.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/enrich_full/test_google_play.py
import json
import shutil
import subprocess
from pathlib import Path
import pytest
from agf.enrich_full.appstore import lookup_google_play, _SIDECAR


def test_parses_sidecar_json_into_appmeta():
    def fake_runner(ids):
        return json.dumps([
            {"appId": "com.gem.app", "title": "Gem", "developer": "Acme",
             "genre": "Health & Fitness", "released": "Jun 3, 2026",
             "releaseDateISO": "2026-06-03", "score": 4.6, "ratings": 321},
        ])
    out = lookup_google_play(["com.gem.app"], runner=fake_runner)
    assert set(out) == {"com.gem.app"}
    m = out["com.gem.app"]
    assert m.name == "Gem" and m.seller == "Acme"
    assert m.release_date == "2026-06-03" and m.rating_count == 321 and m.avg_rating == 4.6


def test_empty_ids_no_runner_call():
    called = []
    assert lookup_google_play([], runner=lambda ids: called.append(ids) or "[]") == {}
    assert called == []


@pytest.mark.integration
def test_live_sidecar_runs_node():
    if not shutil.which("node") or not (Path(_SIDECAR).parent / "node_modules").exists():
        pytest.skip("node or sidecar node_modules not installed")
    out = lookup_google_play(["com.whatsapp"])
    assert "com.whatsapp" in out and out["com.whatsapp"].name
```

- [ ] **Step 2: Run test to verify it fails** — Expected: `ImportError: cannot import name 'lookup_google_play'`

- [ ] **Step 3: Write the sidecar + append `lookup_google_play`**

```javascript
// src/agf/enrich_full/sidecar/gp_lookup.js
// Android app-metadata sidecar (master-plan §6 P6-T3) via facundoolano/google-play-scraper.
// Usage: node gp_lookup.js <packageId> [<packageId> ...]
// Emits a JSON array to stdout. releaseDateISO normalizes `released` for the emerging gate.
async function main() {
  const mod = await import('google-play-scraper');
  const gplay = mod.default || mod;
  const ids = process.argv.slice(2);
  const out = [];
  for (const appId of ids) {
    try {
      const a = await gplay.app({ appId });
      let releaseDateISO = null;
      if (a.released) {
        const d = new Date(a.released);
        if (!isNaN(d.getTime())) releaseDateISO = d.toISOString().slice(0, 10);
      }
      out.push({ appId: a.appId, title: a.title, developer: a.developer,
                 genre: a.genre, released: a.released, releaseDateISO,
                 score: a.score, ratings: a.ratings });
    } catch (e) {
      out.push({ appId, error: String((e && e.message) || e) });
    }
  }
  process.stdout.write(JSON.stringify(out));
}
main().catch((e) => { process.stderr.write(String(e)); process.exit(1); });
```

```json
// src/agf/enrich_full/sidecar/package.json
{
  "name": "agf-gp-lookup",
  "version": "1.0.0",
  "private": true,
  "type": "module",
  "description": "google-play-scraper sidecar for Plan 6 Android enrichment",
  "dependencies": { "google-play-scraper": "^10.0.0" }
}
```

```python
# --- append to src/agf/enrich_full/appstore.py ---

def _default_node_runner(ids):
    return subprocess.run(["node", str(_SIDECAR), *ids],
                          capture_output=True, text=True, timeout=90).stdout


def lookup_google_play(package_ids, *, runner=None) -> dict:
    """Look up Android apps via the Node google-play-scraper sidecar -> {package_id:
    AppMeta}. `runner(ids)->stdout` is injectable for tests. release_date uses the
    sidecar's normalized ISO date (first release)."""
    ids = [str(p) for p in package_ids if p]
    if not ids:
        return {}
    runner = runner or _default_node_runner
    raw = runner(ids) or "[]"
    out: dict = {}
    for r in json.loads(raw):
        track_id = str(r.get("appId", ""))
        if not track_id or r.get("error"):
            continue
        out[track_id] = AppMeta(
            track_id=track_id, name=r.get("title", ""), seller=r.get("developer", ""),
            genre=r.get("genre", ""),
            release_date=r.get("releaseDateISO") or r.get("released") or "",
            rating_count=int(r.get("ratings") or 0),
            avg_rating=float(r.get("score") or 0.0))
    return out
```

- [ ] **Step 4: Run unit test + install sidecar deps + run live**

Run: `.venv/bin/pytest tests/enrich_full/test_google_play.py -q` → PASS (2 passed, 1 integration deselected)
Then: `cd src/agf/enrich_full/sidecar && npm install` (needs Node), and `cd <repo> && .venv/bin/pytest -m integration tests/enrich_full/test_google_play.py -q` → live sidecar returns WhatsApp metadata.

- [ ] **Step 5: Commit**

```bash
git add src/agf/enrich_full/sidecar/gp_lookup.js src/agf/enrich_full/sidecar/package.json src/agf/enrich_full/appstore.py tests/enrich_full/test_google_play.py
git commit -m "feat(plan-6): Node gp_lookup sidecar + lookup_google_play (ISO release date)"
```

*(`node_modules/` and `package-lock.json` under the sidecar are git-ignored; see Task 11.)*

---

## Task 8 (P6-T8): `creatives.py` — sha256 + phash dedup + download → S3

**Files:**
- Create: `src/agf/enrich_full/creatives.py`
- Test: `tests/enrich_full/test_creatives.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/enrich_full/test_creatives.py
import io
from sqlalchemy import text
from agf.parse import AdRecord
from agf.db import repo
from agf.enrich_full.creatives import is_duplicate, download_creative, _hamming_hex


def _png(color):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color).save(buf, format="PNG")
    return buf.getvalue()


def _ad(ext="C1"):
    return AdRecord(ext, "PC", "GemCo", True, 1_714_500_000, None, 1,
                    "INSTALL_MOBILE_APP", "Install", "https://x/y", "body")


def test_hamming_hex_counts_bit_differences():
    assert _hamming_hex("ffff", "ffff") == 0
    assert _hamming_hex("ffff", "fffe") == 1
    assert _hamming_hex("0000", "ffff") == 16


def test_is_duplicate_exact_then_near(pg_conn):
    adv = repo.upsert_advertiser(pg_conn, _ad())
    ad = repo.upsert_ad(pg_conn, _ad(), adv, None)
    repo.upsert_creative(pg_conn, ad, "image", "b", "t", None, "k", "sha-A", "ffffffffffffffff")
    assert is_duplicate(pg_conn, "sha-A", None) == \
        pg_conn.execute(text("SELECT id FROM public.creatives WHERE sha256='sha-A'")).scalar()
    # near phash (1 bit) -> dup; far phash -> None
    near = is_duplicate(pg_conn, "sha-NEW", "fffffffffffffffe", threshold=10)
    assert near is not None
    assert is_duplicate(pg_conn, "sha-NEW2", "0000000000000000", threshold=10) is None


def test_download_creative_uploads_then_dedups_identical(pg_conn):
    adv = repo.upsert_advertiser(pg_conn, _ad("C2"))
    ad = repo.upsert_ad(pg_conn, _ad("C2"), adv, None)
    data = _png((10, 120, 200))
    puts = []
    fake_put = lambda key, d, ct, client=None: (puts.append(key) or key)
    fetch = lambda url: (data, "image/png")

    a1 = download_creative(pg_conn, ad, "https://cdn/x.png", "image", fetch=fetch, put=fake_put)
    a2 = download_creative(pg_conn, ad, "https://cdn/x.png", "image", fetch=fetch, put=fake_put)

    assert a1.dedup_of is None and a1.s3_key and a1.creative_id
    assert a2.dedup_of == a1.creative_id          # identical bytes -> deduped
    assert len(puts) == 1                          # dup NOT re-uploaded
    cnt = pg_conn.execute(text("SELECT count(*) FROM public.creatives WHERE sha256=:s"),
                          {"s": a1.sha256}).scalar()
    assert cnt == 1
    row = pg_conn.execute(text("SELECT content_type, file_size FROM public.creatives WHERE id=:i"),
                          {"i": a1.creative_id}).first()
    assert row.content_type == "image/png" and row.file_size == len(data)
```

- [ ] **Step 2: Run test to verify it fails** — Expected: `ModuleNotFoundError: No module named 'agf.enrich_full.creatives'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agf/enrich_full/creatives.py
"""Creative byte download + dedup → S3 (master-plan §6 P6-T8). Reuses Plan 3 dedup
(sha256 exact + perceptual phash near-dup) and storage.s3. fbcdn signed URLs expire,
so creatives are downloaded at scrape time (the worker re-fetches fresh URLs)."""
from sqlalchemy import text
from agf.db import repo
from agf.db.dedup import sha256_bytes, phash_image, is_duplicate_creative
from agf.storage.s3 import put_bytes
from agf.enrich_full.schemas import CreativeAsset

PHASH_HAMMING_THRESHOLD = 10
_EXT = {"image": "jpg", "video": "mp4"}


def _hamming_hex(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def is_duplicate(conn, sha256, phash, threshold: int = PHASH_HAMMING_THRESHOLD):
    """Existing creative id if this asset duplicates one already stored: exact
    byte-identity (sha256) OR perceptual near-dup (phash hamming < threshold). Else None."""
    exact = is_duplicate_creative(conn, sha256)
    if exact is not None:
        return exact
    if not phash:
        return None
    rows = conn.execute(text(
        "SELECT id, phash FROM public.creatives WHERE phash IS NOT NULL")).all()
    for cid, existing in rows:
        try:
            if _hamming_hex(phash, existing) < threshold:
                return cid
        except (ValueError, TypeError):
            continue
    return None


def _default_fetch(url):
    import httpx
    r = httpx.get(url, follow_redirects=True, timeout=30)
    return r.content, r.headers.get("content-type")


def download_creative(conn, ad_id, url, type, *, body_text=None, title=None,
                      fetch=None, put=None, s3=None,
                      threshold: int = PHASH_HAMMING_THRESHOLD) -> CreativeAsset:
    """Download a creative URL, dedup (sha256 + phash), upload new ones to S3, and
    record the row (§3.3 upsert_creative). Duplicates are NOT re-uploaded; dedup_of
    carries the existing creative id."""
    fetch = fetch or _default_fetch
    put = put or put_bytes
    data, content_type = fetch(url)
    sha = sha256_bytes(data)
    ph = phash_image(data)
    dup = is_duplicate(conn, sha, ph, threshold)
    if dup is not None:
        return CreativeAsset(type=type, sha256=sha, phash=ph, s3_key=None,
                             content_type=content_type, file_size=len(data),
                             creative_id=dup, dedup_of=dup)
    key = f"creatives/{sha}.{_EXT.get(type, 'bin')}"
    put(key, data, content_type or "application/octet-stream", client=s3)
    cid = repo.upsert_creative(conn, ad_id, type, body_text, title, url, key, sha, ph)
    conn.execute(text("UPDATE public.creatives SET content_type=:ct, file_size=:fs WHERE id=:i"),
                 {"ct": content_type, "fs": len(data), "i": cid})
    return CreativeAsset(type=type, sha256=sha, phash=ph, s3_key=key,
                         content_type=content_type, file_size=len(data),
                         creative_id=cid, dedup_of=None)
```

- [ ] **Step 4: Run test to verify it passes** — `.venv/bin/pytest tests/enrich_full/test_creatives.py -q` (needs DB env) → PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agf/enrich_full/creatives.py tests/enrich_full/test_creatives.py
git commit -m "feat(plan-6): download_creative + is_duplicate (sha256 exact + phash near-dup) → S3"
```

---

## Task 9 (P6-T6): `funnel_capture.capture_funnel` (Playwright)

Screenshot a funnel step-by-step. One context (memory), capped at `max_steps`. `page` is injectable (fake in unit, real chromium otherwise). Reuses `autoconsent_cmp` + Plan 3 `put_bytes`.

**Files:**
- Modify: `src/agf/enrich_full/funnel_capture.py` (append)
- Test: `tests/enrich_full/test_capture_funnel.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/enrich_full/test_capture_funnel.py
from agf.enrich_full.funnel_capture import capture_funnel


class FakeEl:
    def __init__(self, page): self.page = page
    def click(self): self.page._advance()


class FakeFunnelPage:
    """Scripted multi-step page. goto sets step 0; the next-CTA exists until the last."""
    def __init__(self, urls, titles):
        self.urls, self.titles, self.i = urls, titles, 0
        self.shots = 0

    def goto(self, url):
        self.i = self.urls.index(url) if url in self.urls else 0

    @property
    def url(self): return self.urls[self.i]

    def title(self): return self.titles[self.i]

    def screenshot(self):
        self.shots += 1
        return f"png-{self.i}".encode()

    def query_selector(self, sel):
        if sel == "a.cta" and self.i < len(self.urls) - 1:
            return FakeEl(self)
        return None

    def _advance(self): self.i = min(self.i + 1, len(self.urls) - 1)


def test_captures_three_steps_to_s3():
    urls = ["https://f/1", "https://f/2", "https://f/3"]
    page = FakeFunnelPage(urls, ["One", "Two", "Three"])
    puts = []
    fake_put = lambda key, d, ct, client=None: (puts.append((key, d)) or key)

    steps = capture_funnel("https://f/1", max_steps=3, page=page, put=fake_put)

    assert [s.step for s in steps] == [1, 2, 3]
    assert [s.url for s in steps] == urls
    assert [s.title for s in steps] == ["One", "Two", "Three"]
    assert all(s.screenshot_s3_key for s in steps)
    assert len(puts) == 3 and page.shots == 3


def test_stops_when_no_next_cta():
    page = FakeFunnelPage(["https://f/1"], ["Only"])
    steps = capture_funnel("https://f/1", max_steps=3, page=page,
                           put=lambda *a, **k: "key")
    assert len(steps) == 1
```

- [ ] **Step 2: Run test to verify it fails** — Expected: `ImportError: cannot import name 'capture_funnel'`

- [ ] **Step 3: Append the implementation**

```python
# --- append to src/agf/enrich_full/funnel_capture.py ---
import hashlib
from contextlib import contextmanager
from agf.storage.s3 import put_bytes
from agf.enrich_full.schemas import FunnelStep

_NEXT_SELECTORS = ("a.cta", "button[type='submit']", ".btn-primary", "a.button",
                   "button.primary", "[data-testid='continue']")


def _url_hash(url: str) -> str:
    return hashlib.sha256((url or "").encode()).hexdigest()[:16]


def _next_element(page):
    for sel in _NEXT_SELECTORS:
        try:
            el = page.query_selector(sel)
        except Exception:
            el = None
        if el:
            return el
    return None


def _safe_title(page):
    try:
        return page.title()
    except Exception:
        return None


def _run_capture(page, start_url, max_steps, put, s3):
    steps = []
    page.goto(start_url)
    for i in range(max_steps):
        autoconsent_cmp(page)
        key = None
        try:
            shot = page.screenshot()
            key = f"funnels/{_url_hash(start_url)}/step{i + 1}.png"
            put(key, shot, "image/png", client=s3)
        except Exception:
            key = None
        steps.append(FunnelStep(step=i + 1, url=getattr(page, "url", start_url),
                                screenshot_s3_key=key, title=_safe_title(page)))
        if i == max_steps - 1:
            break
        nxt = _next_element(page)
        if not nxt:
            break
        try:
            nxt.click()
            if hasattr(page, "wait_for_load_state"):
                page.wait_for_load_state("domcontentloaded")
        except Exception:
            break
    return steps


@contextmanager
def _playwright_page(proxy=None, timeout_ms=30_000):
    from playwright.sync_api import sync_playwright   # lazy: optional dep
    with sync_playwright() as p:
        ctx_kw = {}
        if proxy:
            host = proxy.split("@")[-1]
            ctx_kw["proxy"] = {"server": f"http://{host}"}
            if "@" in proxy:
                user, _, pw = proxy.split("@")[0].partition(":")
                ctx_kw["proxy"].update(username=user, password=pw)
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(**ctx_kw)
        page = context.new_page()
        page.set_default_timeout(timeout_ms)
        try:
            yield page
        finally:
            browser.close()


def capture_funnel(start_url, *, max_steps=3, put=put_bytes, s3=None,
                   page=None, proxy=None) -> list:
    """Screenshot a funnel step-by-step (reuses the Plan 2 Playwright standby; one
    context, capped at max_steps). Each step's PNG → S3; returns FunnelSteps. Pass
    `page` to drive an existing page (tests); otherwise a headless chromium is opened."""
    if page is not None:
        return _run_capture(page, start_url, max_steps, put, s3)
    with _playwright_page(proxy) as p:
        return _run_capture(p, start_url, max_steps, put, s3)
```

- [ ] **Step 4: Run test to verify it passes** — `.venv/bin/pytest tests/enrich_full/test_capture_funnel.py -q` → PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agf/enrich_full/funnel_capture.py tests/enrich_full/test_capture_funnel.py
git commit -m "feat(plan-6): capture_funnel — step-by-step Playwright screenshots → S3 (CMP-dismissed, capped)"
```

---

## Task 10 (P6-T10): `run_enrich.enrich_gem` + worker adapter + LIVE e2e

`enrich_gem` walks a gem's top-N ads: resolve landing → app-store metadata (set `ads.app_id`) OR funnel capture + (best-effort pricing), download deduped creatives, then company enrichment. conn-first (no commit). `make_enrich_handler` is the §3.9 adapter. `EnrichDeps` carries cross-cutting deps (all injectable).

**Files:**
- Create: `src/agf/enrich_full/run_enrich.py`
- Test: `tests/enrich_full/test_enrich_gem.py`

- [ ] **Step 1: Write the failing test (assembled, real Postgres + fake externals)**

```python
# tests/enrich_full/test_enrich_gem.py
import asyncio
from datetime import date
from sqlalchemy import text
from agf.parse import AdRecord
from agf.classify import AppIntent
from agf.extract.deeplink import ResolvedLink
from agf.db import repo
from agf.enrich import AppMeta
from agf.enrich_full.run_enrich import enrich_gem, EnrichDeps
from tests.enrich_full.conftest import FakeExtractor
from tests.enrich_full.test_capture_funnel import FakeFunnelPage
from tests.enrich_full.test_company import FakeWhois, _rec


def _ad(ext, link):
    return AdRecord(ext, "PGEM", "GemCo", True, 1_714_500_000, None, 1,
                    "INSTALL_MOBILE_APP", "Install", link, "body")


class _FakeITunes:
    def get(self, url, params=None):
        return _J({"results": [{"trackId": 999, "trackName": "GemApp", "sellerName": "Acme",
                                "primaryGenreName": "Health & Fitness",
                                "releaseDate": "2026-05-20T07:00:00Z",
                                "userRatingCount": 80, "averageUserRating": 4.7}]})


class _J:
    def __init__(self, p): self._p = p
    def json(self): return self._p


def _png():
    import io
    from PIL import Image
    buf = io.BytesIO(); Image.new("RGB", (16, 16), (9, 9, 9)).save(buf, "PNG")
    return buf.getvalue()


def test_enrich_gem_assembled(pg_conn):
    # seed a gem with one iOS-app ad + one web-funnel ad
    app_ad = _ad("GEM-IOS", "https://apps.apple.com/us/app/x/id999")
    funnel_ad = _ad("GEM-WEB", "https://getgem.com/quiz")
    adv = repo.upsert_advertiser(pg_conn, app_ad)
    ad_ios = repo.upsert_ad(pg_conn, app_ad, adv, None)
    ad_web = repo.upsert_ad(pg_conn, funnel_ad, adv, None)

    ex = FakeExtractor(links={
        "https://apps.apple.com/us/app/x/id999":
            ResolvedLink("https://apps.apple.com/us/app/x/id999", AppIntent("ios", "999")),
        "https://getgem.com/quiz":
            ResolvedLink("https://getgem.com/quiz", AppIntent("web", None)),
    })
    page = FakeFunnelPage(["https://getgem.com/quiz", "https://getgem.com/checkout"],
                          ["Quiz", "Checkout"])
    puts = []
    deps = EnrichDeps(
        extractor=ex, itunes_client=_FakeITunes(),
        fetch_text=lambda u: '<script src="https://js.stripe.com/v3"></script>',
        fetch_bytes=lambda u: (_png(), "image/png"),
        put=lambda key, d, ct, client=None: (puts.append(key) or key),
        whois_fn=FakeWhois(_rec()), page=page, capture=True,
        creative_urls={"GEM-WEB": [("image", "https://cdn/c1.png")]})

    res = asyncio.run(enrich_gem(pg_conn, adv, top_n=5, deps=deps))

    assert res.n_ads_processed == 2
    # app metadata linked
    app_id = pg_conn.execute(text("SELECT app_id FROM public.ads WHERE id=:i"),
                             {"i": ad_ios}).scalar()
    assert app_id is not None
    app = pg_conn.execute(text("SELECT name, is_emerging, genre FROM public.apps WHERE id=:i"),
                          {"i": app_id}).first()
    assert app.name == "GemApp" and app.is_emerging is True
    # funnel captured
    assert res.funnels == 1
    fr = pg_conn.execute(text("SELECT screenshot_s3_key, final_url_after_redirect "
                              "FROM public.funnels WHERE ad_id=:i"), {"i": ad_web}).first()
    assert fr.screenshot_s3_key and fr.final_url_after_redirect == "https://getgem.com/checkout"
    # creative deduped + stored
    assert res.creatives == 1
    # company enrichment backfilled domain_id, tech captured
    dom = pg_conn.execute(text("SELECT domain_id FROM public.advertisers WHERE id=:i"),
                          {"i": adv}).scalar()
    assert dom is not None
    assert res.company and "Stripe" in res.company.tech
```

- [ ] **Step 2: Run test to verify it fails** — Expected: `ModuleNotFoundError: No module named 'agf.enrich_full.run_enrich'`

- [ ] **Step 3: Write the implementation**

```python
# src/agf/enrich_full/run_enrich.py
"""Top-N gem enrichment orchestrator (master-plan §6 P6-T10). For a flagged gem
(advertiser), enrich its top-N ads: resolve landing → app-store metadata OR funnel
capture + pricing, download deduped creatives, then company enrichment. conn-first
(no commit); cross-cutting deps arrive via EnrichDeps (worker adapter §3.9)."""
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse
from sqlalchemy import text
from agf.db import repo
from agf.enrich import emerging_from_release
from agf.enrich_full.schemas import EnrichResult
from agf.enrich_full.funnel_capture import resolve_landing, capture_funnel, extract_pricing
from agf.enrich_full.creatives import download_creative
from agf.enrich_full.appstore import lookup_itunes_batch, lookup_google_play
from agf.enrich_full.company import enrich_company


@dataclass
class EnrichDeps:
    """Cross-cutting deps for enrich_gem (all injectable; production wiring lives in
    make_enrich_handler)."""
    extractor: object                  # Plan 2 facade (resolve_deeplink, search)
    itunes_client: object = None       # httpx-like client for iTunes Lookup
    gp_runner: object = None           # node runner for the gplay sidecar
    fetch_text: object = None          # (url) -> html str  (pricing + tech)
    fetch_bytes: object = None         # (url) -> (bytes, content_type)  (creatives)
    put: object = None                 # put_bytes-compatible
    s3: object = None                  # boto3/MinIO client
    whois_fn: object = None            # whois.whois-compatible
    page: object = None                # Playwright page or None (open real chromium)
    proxy: str | None = None
    capture: bool = True               # run funnel screenshots
    creative_urls: dict = None         # {external_ad_id: [(type, url)]}


def _domain_of(url):
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else (host or None)
    except Exception:
        return None


def _is_emerging(release_date, now):
    try:
        return emerging_from_release(release_date, now) if release_date else None
    except (ValueError, TypeError):
        return None


async def _lookup_app(deps, kind, store_id):
    if kind == "ios" and deps.itunes_client is not None:
        return lookup_itunes_batch(deps.itunes_client, [store_id]).get(str(store_id))
    if kind == "android":
        return lookup_google_play([store_id], runner=deps.gp_runner).get(str(store_id))
    return None


async def enrich_gem(conn, advertiser_id, *, top_n=5, deps: EnrichDeps) -> EnrichResult:
    ads = conn.execute(text(
        "SELECT id, external_ad_id, link_url FROM public.ads WHERE advertiser_id=:a "
        "ORDER BY last_seen DESC NULLS LAST, id LIMIT :n"),
        {"a": advertiser_id, "n": top_n}).all()
    now = datetime.now(timezone.utc)
    n_funnels = n_creatives = 0
    last_app_meta = None
    company_domain = company_html = None

    for ad in ads:
        target = await resolve_landing(deps.extractor, ad.link_url)
        if target.kind in ("ios", "android") and target.store_id:
            meta = await _lookup_app(deps, target.kind, target.store_id)
            if meta:
                app_id = repo.upsert_app(conn, {
                    "store": target.kind, "store_id": meta.track_id, "name": meta.name,
                    "developer": meta.seller, "genre": meta.genre,
                    "release_date": meta.release_date, "rating": meta.avg_rating,
                    "rating_count": meta.rating_count,
                    "is_emerging": _is_emerging(meta.release_date, now)})
                conn.execute(text("UPDATE public.ads SET app_id=:p WHERE id=:i"),
                             {"p": app_id, "i": ad.id})
                last_app_meta = meta
        elif target.kind == "web":
            company_domain = company_domain or _domain_of(target.final_url)
            if deps.fetch_text is not None and company_html is None:
                try:
                    company_html = deps.fetch_text(target.final_url)
                except Exception:
                    company_html = None
            if deps.capture:
                steps = capture_funnel(target.final_url, page=deps.page, put=deps.put,
                                       s3=deps.s3, proxy=deps.proxy)
                if steps:
                    repo.upsert_funnel(conn, ad.id, ad.link_url, steps[-1].url,
                                       steps[0].screenshot_s3_key)
                    n_funnels += 1

        for ctype, url in (deps.creative_urls or {}).get(str(ad.external_ad_id), []):
            ca = download_creative(conn, ad.id, url, ctype, fetch=deps.fetch_bytes,
                                   put=deps.put, s3=deps.s3)
            if ca.dedup_of is None:
                n_creatives += 1

    company = enrich_company(conn, advertiser_id, domain=company_domain,
                             html=company_html, app_meta=last_app_meta,
                             whois_fn=deps.whois_fn)
    return EnrichResult(advertiser_id=advertiser_id, n_ads_processed=len(ads),
                        funnels=n_funnels, creatives=n_creatives, company=company)


def make_enrich_handler(deps_factory, *, top_n=5):
    """Adapter (§3.9): handler(conn, advertiser_id) for run_worker. deps_factory(conn,
    advertiser_id)->EnrichDeps builds per-gem deps (e.g. a fresh extractor.search for
    current creative URLs — fbcdn signed URLs expire)."""
    import asyncio

    def handler(conn, advertiser_id):
        deps = deps_factory(conn, advertiser_id)
        res = asyncio.run(enrich_gem(conn, advertiser_id, top_n=top_n, deps=deps))
        return f"ads={res.n_ads_processed} funnels={res.funnels} creatives={res.creatives}"
    return handler
```

- [ ] **Step 4: Run the assembled test to verify it passes** — `.venv/bin/pytest tests/enrich_full/test_enrich_gem.py -q` (needs DB env) → PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agf/enrich_full/run_enrich.py tests/enrich_full/test_enrich_gem.py
git commit -m "feat(plan-6): enrich_gem orchestrator (top-N: appstore/funnel/creatives/company) + make_enrich_handler adapter"
```

---

## Task 11: Full-suite + LIVE assembled smoke (real chromium + MinIO + iTunes) + docs + merge

- [ ] **Step 1: Git-ignore the sidecar node_modules**

Append to `.gitignore`:
```
src/agf/enrich_full/sidecar/node_modules/
src/agf/enrich_full/sidecar/package-lock.json
```

- [ ] **Step 2: Run the whole suite with infra up**

```bash
docker compose -f db/docker-compose.yml up -d
export AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf
export S3_ENDPOINT=http://localhost:9010 MINIO_ACCESS_KEY=minioadmin MINIO_SECRET_KEY=minioadmin S3_BUCKET=agf-test
.venv/bin/pytest -q
```
Expected: all prior tests still green + the new `tests/enrich_full/` tests; integration deselected. Record the pass count.

- [ ] **Step 3: LIVE assembled smoke (the RUN-IT-LIVE rule)** — a throwaway script that drives the genuinely-live parts against real infra:
  - real **iTunes Lookup** for a known app id → AppMeta with a real `releaseDate`;
  - real **Playwright chromium** `capture_funnel("https://example.com", max_steps=1)` → real PNG → **MinIO** `put_bytes` → assert the object exists (`head_object`);
  - real **WHOIS** `domain_age("example.com")` → a real creation date / age (network; tolerate failure with a logged skip);
  - real **Node sidecar** `lookup_google_play(["com.whatsapp"])` (if `node_modules` present) → AppMeta;
  - then `enrich_gem` on a seeded gem using a direct-link extractor stub (no FB session warming) + real MinIO `put_bytes` + real chromium `page=None` → assert funnel row + screenshot key + creative row + advertiser.domain_id. Clean up sentinels.

Expected: each live part returns real data; the MinIO object for the screenshot exists; enrich_gem writes funnel/creative/enrichment rows.

- [ ] **Step 4: `/self-eval` then update the dashboards**

Update `docs/plans/00-master-plan.md` §5: Plan 6 → `✅ done`, tick P6-T1…T10 in §6, set Plan 7 to `⬜ ready (deps met)`. Update `docs/HANDOFF.md` §1 (Plan 6 ✅, Plan 7 NEXT), §6, the test count, and the §8 continuation prompt (point at Plan 7).

```bash
git add docs/plans/00-master-plan.md docs/HANDOFF.md .gitignore
git commit -m "docs(plan-6): mark Plan 6 done, unblock Plan 7, refresh handoff + dashboard"
```

- [ ] **Step 5: Merge the branch**

```bash
git checkout main
git merge --no-ff feat/plan-6-enrichment -m "merge(plan-6): enrichment — appstore/funnel/creatives/company for top-N gems"
```

---

## Self-review (run against master-plan §6 Plan 6 + §3)

- **P6-T1 schemas** → Task 1 (6 frozen dataclasses; construct + frozen). ✓
- **P6-T2 lookup_itunes_batch** → Task 2 (200-chunk, reuses `parse_lookup`, `releaseDate`). ✓
- **P6-T3 gp sidecar + lookup_google_play** → Task 7 (Node sidecar, ISO release date, mock + live). ✓
- **P6-T4 resolve_landing** → Task 3 (delegates to Plan 2 `Extractor.resolve_deeplink`, no second resolver). ✓
- **P6-T5 autoconsent_cmp** → Task 5 (selector sweep; dismissed/None). ✓
- **P6-T6 capture_funnel** → Task 9 (Playwright reuse, per-step screenshots → S3, capped, CMP-dismissed). ✓
- **P6-T7 extract_pricing** → Task 4 (JSON-LD Offer + regex fallback; none→None). ✓
- **P6-T8 download_creative** → Task 8 (sha256 + phash near-dup via Plan 3 dedup → S3). ✓
- **P6-T9 company** → Task 6 (WHOIS domain_age / builtwith_tech / publisher; writes `upsert_advertiser_enrichment`). ✓
- **P6-T10 enrich_gem** → Task 10 (top-N gated, adapter §3.9; funnels/creatives/apps/enrichment rows). ✓
- **Reuse, don't reinvent** → `resolve_deeplink` (T3), Plan 3 `dedup`+`s3` (T8), Playwright warmer pattern (T9), `parse_lookup` (T2). ✓
- **iTunes `releaseDate` not `currentVersionReleaseDate`** → `parse_lookup` (T2); gplay ISO (T7). ✓
- **fbcdn URLs expire → download at scrape time** → `make_enrich_handler`'s `deps_factory` re-fetches fresh creative URLs (T10). ✓
- **Cap Playwright contexts + top-N** → one context in `_playwright_page`; `top_n` LIMIT (T9/T10). ✓
- **Adds NO schema; conn-first/no-commit; python-whois pinned** → Tasks 0/6/8/10. ✓
- **RUN IT LIVE** → Task 11 (full suite + live chromium/MinIO/iTunes/whois/sidecar smoke). ✓
- **Scope notes (documented):** `parent_company_id` left NULL (veto NULL-tolerant); images via httpx (aria2c/ffmpeg = Plan 7 system deps); phash full-scan at top-N volume. ✓
