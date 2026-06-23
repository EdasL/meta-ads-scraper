# Web-sub Funnel Feed Surfacing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface confirmed `web_sub` funnels (digital subscription products with no app store listing) in the main Discover feed, enriched with a web-sourced icon + name/description and a "Web only" badge, ranked by the same GemScore as apps.

**Architecture:** Bridge each confirmed `discovery.funnel_candidates` row with `payoff_kind='web_sub'` into the advertiser pipeline: create the advertiser, fetch its web identity (OG tags/favicon), scrape its Meta ad corpus by `page_id` via the existing `refresh_one_advertiser`, then let the existing enrich/score/matview machinery carry it to the feed. A `no_app` veto exception and a neutral-floor scoring path make app-less entries score fairly; the existing incumbent vetoes drop the non-emerging ones automatically.

**Tech Stack:** Python 3.13 (SQLAlchemy core, `text()` queries), Postgres (raw `db/schema/*.sql` migrations), Go API (Gin), Next.js/TypeScript web. DB tests run against a real Postgres.

## Global Constraints

- Commit directly to `main` (solo local repo, no feature branches).
- Run the FULL test suite with `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it python -m pytest -q` — **0 failed** before any task is "done"; a silently-skipped DB test is not a pass.
- All user-facing copy is plain, clear, professional (Field Desk voice) — no marketing fluff. The badge text is exactly **"Web only"**.
- Migrations are `db/schema/NNN_name.sql`, applied in lexical order by `src/agf/db/migrate.py::apply_migrations`. The latest existing file is `139_funnel_payoff_kind.sql`; new files are `140_*` and `141_*`.
- No new third-party Python dependencies — parse HTML with the standard library.
- Per-cycle work is bounded by an env cap, mirroring the other worker stages.

---

## File Structure

- `db/schema/140_advertiser_web_identity.sql` (new) — adds web identity columns to `public.advertisers`.
- `db/schema/141_emerging_matview_websub.sql` (new) — rebuilds `frontend.emerging_scaling_apps` with COALESCE'd web identity + `web_only`.
- `src/agf/db/repo.py` (modify) — `set_web_identity()` writer.
- `src/agf/scoring/features.py` (modify) — `FeatureVector.web_payoff_kind` + thread it through `build_features`.
- `src/agf/scoring/vetoes.py` (modify) — `no_app` exception for web-sub.
- `src/agf/scoring/run_scoring.py` (modify) — pass `ur=None, d=None` for web-sub.
- `src/agf/discovery/web_identity.py` (new) — OG/favicon HTML parser (pure).
- `src/agf/discovery/websub_bridge.py` (new) — the bridge stage.
- `src/agf/workers/main.py` (modify) — wire the stage + `AGF_WEBSUB_BRIDGE_PER_CYCLE`.
- `backend/` shortlist query + struct (modify, located in Task 9) — surface `web_only`.
- `web/src/types/contracts.ts` + `web/src/components/discover/GemTable.tsx` (modify) — `web_only` field + badge.
- Tests alongside each.

---

### Task 1: Migration — advertiser web identity columns

**Files:**
- Create: `db/schema/140_advertiser_web_identity.sql`
- Test: `tests/db/test_migration_140_web_identity.py`

**Interfaces:**
- Produces: columns `public.advertisers.web_payoff_kind` (text, nullable), `web_name`, `web_description`, `web_icon_url`, `web_landing_url` (all text, nullable).

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_migration_140_web_identity.py
import os
import pytest
from sqlalchemy import text
from agf.db.engine import get_engine
from agf.db.migrate import apply_migrations

DB = os.environ.get("AGF_TEST_DB_URL")
pytestmark = pytest.mark.skipif(not DB, reason="needs AGF_TEST_DB_URL")

def test_web_identity_columns_exist():
    eng = get_engine(DB)
    apply_migrations(eng)
    with eng.connect() as c:
        cols = {r[0] for r in c.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='advertisers'"))}
    assert {"web_payoff_kind", "web_name", "web_description",
            "web_icon_url", "web_landing_url"} <= cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it python -m pytest tests/db/test_migration_140_web_identity.py -v`
Expected: FAIL (columns missing).

- [ ] **Step 3: Write the migration**

```sql
-- db/schema/140_advertiser_web_identity.sql
-- Web-sub funnels have no app-store listing; carry a web-sourced identity
-- (OG title/description, favicon, landing URL) and a payoff marker on the
-- advertiser so the no_app veto and the feed matview can treat them correctly.
ALTER TABLE public.advertisers
  ADD COLUMN IF NOT EXISTS web_payoff_kind  text,
  ADD COLUMN IF NOT EXISTS web_name         text,
  ADD COLUMN IF NOT EXISTS web_description  text,
  ADD COLUMN IF NOT EXISTS web_icon_url     text,
  ADD COLUMN IF NOT EXISTS web_landing_url  text;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it python -m pytest tests/db/test_migration_140_web_identity.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add db/schema/140_advertiser_web_identity.sql tests/db/test_migration_140_web_identity.py
git commit -m "feat(db): advertiser web identity columns for web-sub funnels (migration 140)"
```

---

### Task 2: FeatureVector carries `web_payoff_kind`

**Files:**
- Modify: `src/agf/scoring/features.py` (FeatureVector dataclass; the `adv` SELECT; the constructor)
- Test: `tests/scoring/test_features_web_payoff.py`

**Interfaces:**
- Consumes: migration 140 columns (Task 1).
- Produces: `FeatureVector.web_payoff_kind: str | None` (default `None`), populated by `build_features` from `advertisers.web_payoff_kind`.

- [ ] **Step 1: Write the failing test** (DB-backed: insert an advertiser with `web_payoff_kind='web_sub'` and assert it surfaces)

```python
# tests/scoring/test_features_web_payoff.py
import os, datetime as dt
import pytest
from sqlalchemy import text
from agf.db.engine import get_engine
from agf.db.migrate import apply_migrations
from agf.scoring.features import build_features

DB = os.environ.get("AGF_TEST_DB_URL")
pytestmark = pytest.mark.skipif(not DB, reason="needs AGF_TEST_DB_URL")

def test_build_features_surfaces_web_payoff_kind():
    eng = get_engine(DB); apply_migrations(eng)
    with eng.begin() as c:
        aid = c.execute(text(
            "INSERT INTO public.advertisers (page_id, page_name, web_payoff_kind) "
            "VALUES ('WSTEST1', 'WS Test', 'web_sub') "
            "ON CONFLICT (page_id) DO UPDATE SET web_payoff_kind='web_sub' "
            "RETURNING id")).scalar_one()
    with eng.connect() as c:
        fv = build_features(c, aid, dt.date(2026, 6, 23))
    assert fv.web_payoff_kind == "web_sub"

def test_build_features_web_payoff_none_for_normal_advertiser():
    eng = get_engine(DB); apply_migrations(eng)
    with eng.begin() as c:
        aid = c.execute(text(
            "INSERT INTO public.advertisers (page_id, page_name) "
            "VALUES ('WSTEST2', 'Normal') ON CONFLICT (page_id) DO UPDATE "
            "SET page_name='Normal' RETURNING id")).scalar_one()
    with eng.connect() as c:
        fv = build_features(c, aid, dt.date(2026, 6, 23))
    assert fv.web_payoff_kind is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it python -m pytest tests/scoring/test_features_web_payoff.py -v`
Expected: FAIL (`AttributeError: web_payoff_kind` or TypeError on unknown column).

- [ ] **Step 3: Implement**

In `src/agf/scoring/features.py`, add to the end of the `FeatureVector` dataclass field list (alongside the other defaulted fields):

```python
    web_payoff_kind: str | None = None   # 'web_sub' for app-less digital subscription funnels
```

In `build_features`, extend the `adv` query to select the new column:

```python
    adv = conn.execute(text(
        "SELECT page_name, parent_company_id, ig_followers, "
        "       page_like_count, meta_active_ads, meta_active_ads_capped, "
        "       web_payoff_kind "
        "FROM public.advertisers WHERE id=:a"), {"a": advertiser_id}).first()
```

And add to the `FeatureVector(...)` constructor call:

```python
        web_payoff_kind=adv.web_payoff_kind if adv else None,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it python -m pytest tests/scoring/test_features_web_payoff.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agf/scoring/features.py tests/scoring/test_features_web_payoff.py
git commit -m "feat(scoring): FeatureVector.web_payoff_kind threaded from advertisers"
```

---

### Task 3: `no_app` veto exception for web-sub

**Files:**
- Modify: `src/agf/scoring/vetoes.py:39` (`no_app`)
- Test: `tests/scoring/test_vetoes_websub.py`

**Interfaces:**
- Consumes: `FeatureVector.web_payoff_kind` (Task 2).
- Produces: `no_app(fv)` returns `False` when `fv.web_payoff_kind` is set, regardless of `has_app`.

- [ ] **Step 1: Write the failing test**

```python
# tests/scoring/test_vetoes_websub.py
from types import SimpleNamespace
from agf.scoring.vetoes import no_app

def _fv(**kw):
    base = dict(has_app=False, web_payoff_kind=None)
    base.update(kw)
    return SimpleNamespace(**base)

def test_no_app_vetoes_appless_non_websub():
    assert no_app(_fv(has_app=False, web_payoff_kind=None)) is True

def test_no_app_skips_websub():
    assert no_app(_fv(has_app=False, web_payoff_kind="web_sub")) is False

def test_no_app_passes_real_app():
    assert no_app(_fv(has_app=True, web_payoff_kind=None)) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/scoring/test_vetoes_websub.py -v`
Expected: FAIL on `test_no_app_skips_websub` (currently returns True) — also AttributeError if `no_app` reads `web_payoff_kind` before the change; the SimpleNamespace provides it so it fails on the assertion.

- [ ] **Step 3: Implement** — replace the `no_app` body:

```python
def no_app(fv) -> bool:
    # v1 is apps-only: physical products and services are out. EXCEPTION: a
    # confirmed web-sub funnel is legitimately app-less (digital subscription
    # sold web-to-subscribe), so it is allowed through and ranked on its ad
    # corpus. getattr keeps older positional FeatureVectors working.
    if getattr(fv, "web_payoff_kind", None):
        return False
    return not fv.has_app
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/scoring/test_vetoes_websub.py -v`
Expected: PASS (all three).

- [ ] **Step 5: Commit**

```bash
git add src/agf/scoring/vetoes.py tests/scoring/test_vetoes_websub.py
git commit -m "feat(scoring): no_app veto exception for confirmed web-sub funnels"
```

---

### Task 4: Neutral-floor scoring for web-sub (no app-derived axes)

**Files:**
- Modify: `src/agf/scoring/run_scoring.py` (the per-`fv` scoring block where `gs = gem_score(sv, ur, fi, d, w)`)
- Test: `tests/scoring/test_run_scoring_websub_axes.py`

**Interfaces:**
- Consumes: `FeatureVector.web_payoff_kind` (Task 2), `gem_score` (existing, imputes `None` → 0.5 floor).
- Produces: for a web-sub advertiser, `under_radar` and `demand` are written as `NULL` and excluded from the GemScore product (replaced by the neutral floor), so the entry ranks on SV/FI/whitespace.

- [ ] **Step 1: Write the failing test**

This guards the exact transformation. Extract the decision into a tiny pure helper so it is unit-testable without a full DB score run.

```python
# tests/scoring/test_run_scoring_websub_axes.py
from agf.scoring.run_scoring import websub_axis_override

def test_override_nulls_app_axes_for_websub():
    ur, d = websub_axis_override("web_sub", ur=0.9, d=0.8)
    assert ur is None and d is None

def test_override_passthrough_for_normal():
    ur, d = websub_axis_override(None, ur=0.9, d=0.8)
    assert ur == 0.9 and d == 0.8
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/scoring/test_run_scoring_websub_axes.py -v`
Expected: FAIL (`ImportError: cannot import name 'websub_axis_override'`).

- [ ] **Step 3: Implement**

Add the helper near the top of `src/agf/scoring/run_scoring.py` (module level):

```python
def websub_axis_override(web_payoff_kind, ur, d):
    """Web-sub funnels have no app-store traction (no installs/reviews), so the
    User-Radar and Demand axes are not measurable. Return them as None so
    gem_score imputes the cohort-neutral 0.5 floor instead of a misleading 0,
    letting the entry rank on scaling-velocity / funnel-intent / whitespace."""
    if web_payoff_kind:
        return None, None
    return ur, d
```

Then in the scoring loop, immediately before `gs = gem_score(sv, ur, fi, d, w)`, insert:

```python
            ur, d = websub_axis_override(fv.web_payoff_kind, ur, d)
```

(The existing `INSERT ... VALUES (... :ur ... :d ...)` already binds `ur`/`d`; `None` writes SQL NULL — both columns are nullable.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/scoring/test_run_scoring_websub_axes.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agf/scoring/run_scoring.py tests/scoring/test_run_scoring_websub_axes.py
git commit -m "feat(scoring): null app-derived axes for web-sub so GemScore uses neutral floor"
```

---

### Task 5: Web identity parser (OG tags + favicon)

**Files:**
- Create: `src/agf/discovery/web_identity.py`
- Test: `tests/discovery/test_web_identity.py`

**Interfaces:**
- Produces: `parse_web_identity(html: str, base_url: str, *, fallback_name: str | None = None) -> WebIdentity` where `WebIdentity` is a frozen dataclass `(name: str | None, description: str | None, icon_url: str | None)`. Pure; no network.

- [ ] **Step 1: Write the failing test**

```python
# tests/discovery/test_web_identity.py
from agf.discovery.web_identity import parse_web_identity

HTML = '''<html><head>
<meta property="og:title" content="Sleep Tight" />
<meta property="og:description" content="Better sleep in 7 days." />
<link rel="icon" href="/favicon.ico" />
</head><body></body></html>'''

def test_extracts_og_and_favicon():
    wi = parse_web_identity(HTML, "https://trysleeptight.app/start")
    assert wi.name == "Sleep Tight"
    assert wi.description == "Better sleep in 7 days."
    assert wi.icon_url == "https://trysleeptight.app/favicon.ico"

def test_falls_back_to_meta_description_and_title_tag():
    html = ('<html><head><title>Bossed AI</title>'
            '<meta name="description" content="AI for founders."></head></html>')
    wi = parse_web_identity(html, "https://app.bossed.ai/")
    assert wi.name == "Bossed AI"
    assert wi.description == "AI for founders."
    # no explicit icon link -> default to /favicon.ico at the origin
    assert wi.icon_url == "https://app.bossed.ai/favicon.ico"

def test_uses_fallback_name_when_no_title():
    wi = parse_web_identity("<html><head></head></html>", "https://x.co/",
                            fallback_name="Page Name")
    assert wi.name == "Page Name"
    assert wi.description is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/discovery/test_web_identity.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement** (stdlib only — `html.parser` + `urllib.parse.urljoin`)

```python
# src/agf/discovery/web_identity.py
"""Pure parser for a web-sub funnel's display identity: OG title/description and
favicon, extracted from the landing page HTML. No network — the caller fetches."""
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit


@dataclass(frozen=True)
class WebIdentity:
    name: str | None
    description: str | None
    icon_url: str | None


class _Head(HTMLParser):
    def __init__(self):
        super().__init__()
        self.og_title = None
        self.og_desc = None
        self.meta_desc = None
        self.icon_href = None
        self.title = None
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            prop = a.get("property", "").lower()
            name = a.get("name", "").lower()
            content = a.get("content")
            if prop == "og:title" and content:
                self.og_title = content.strip()
            elif prop == "og:description" and content:
                self.og_desc = content.strip()
            elif name == "description" and content:
                self.meta_desc = content.strip()
        elif tag == "link":
            rels = a.get("rel", "").lower().split()
            if ("icon" in rels or "shortcut" in rels) and a.get("href") and not self.icon_href:
                self.icon_href = a["href"].strip()
        elif tag == "title":
            self._in_title = True

    def handle_data(self, data):
        if self._in_title and data.strip() and not self.title:
            self.title = data.strip()

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False


def parse_web_identity(html: str, base_url: str, *,
                       fallback_name: str | None = None) -> WebIdentity:
    p = _Head()
    try:
        p.feed(html or "")
    except Exception:
        pass
    name = p.og_title or p.title or fallback_name
    description = p.og_desc or p.meta_desc
    if p.icon_href:
        icon_url = urljoin(base_url, p.icon_href)
    else:
        parts = urlsplit(base_url)
        icon_url = f"{parts.scheme}://{parts.netloc}/favicon.ico" if parts.netloc else None
    return WebIdentity(name=name, description=description, icon_url=icon_url)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/discovery/test_web_identity.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/web_identity.py tests/discovery/test_web_identity.py
git commit -m "feat(discovery): pure OG/favicon web-identity parser"
```

---

### Task 6: `set_web_identity` repo writer

**Files:**
- Modify: `src/agf/db/repo.py` (add writer next to `upsert_advertiser`)
- Test: `tests/db/test_set_web_identity.py`

**Interfaces:**
- Consumes: migration 140 columns (Task 1), `upsert_advertiser` (existing, `repo.upsert_advertiser(conn, ar) -> int`).
- Produces: `repo.set_web_identity(conn, advertiser_id, *, payoff_kind, name, description, icon_url, landing_url) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_set_web_identity.py
import os, pytest
from types import SimpleNamespace
from sqlalchemy import text
from agf.db.engine import get_engine
from agf.db.migrate import apply_migrations
from agf.db import repo

DB = os.environ.get("AGF_TEST_DB_URL")
pytestmark = pytest.mark.skipif(not DB, reason="needs AGF_TEST_DB_URL")

def test_set_web_identity_writes_columns():
    eng = get_engine(DB); apply_migrations(eng)
    with eng.begin() as c:
        aid = repo.upsert_advertiser(c, SimpleNamespace(
            page_id="WSID6", page_name="WS6", page_like_count=10))
        repo.set_web_identity(c, aid, payoff_kind="web_sub", name="Sleep Tight",
                              description="Better sleep.", icon_url="https://x/favicon.ico",
                              landing_url="https://trysleeptight.app/")
        row = c.execute(text(
            "SELECT web_payoff_kind, web_name, web_description, web_icon_url, web_landing_url "
            "FROM public.advertisers WHERE id=:a"), {"a": aid}).one()
    assert row.web_payoff_kind == "web_sub"
    assert row.web_name == "Sleep Tight"
    assert row.web_landing_url == "https://trysleeptight.app/"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it python -m pytest tests/db/test_set_web_identity.py -v`
Expected: FAIL (`AttributeError: module 'agf.db.repo' has no attribute 'set_web_identity'`).

- [ ] **Step 3: Implement** — add to `src/agf/db/repo.py`:

```python
def set_web_identity(conn, advertiser_id, *, payoff_kind, name, description,
                     icon_url, landing_url) -> None:
    """Write a web-sub advertiser's display identity + payoff marker. COALESCE so a
    re-run never blanks a previously-resolved field with a later None."""
    conn.execute(text("""
        UPDATE public.advertisers SET
          web_payoff_kind = :pk,
          web_name        = COALESCE(:name, web_name),
          web_description = COALESCE(:desc, web_description),
          web_icon_url    = COALESCE(:icon, web_icon_url),
          web_landing_url = COALESCE(:land, web_landing_url)
        WHERE id = :a
    """), {"pk": payoff_kind, "name": name, "desc": description,
           "icon": icon_url, "land": landing_url, "a": advertiser_id})
```

(Ensure `from sqlalchemy import text` is already imported in `repo.py` — it is, used by `upsert_advertiser`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it python -m pytest tests/db/test_set_web_identity.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agf/db/repo.py tests/db/test_set_web_identity.py
git commit -m "feat(db): set_web_identity writer for web-sub advertisers"
```

---

### Task 7: Web-sub bridge stage

**Files:**
- Create: `src/agf/discovery/websub_bridge.py`
- Test: `tests/discovery/test_websub_bridge.py`

**Interfaces:**
- Consumes: `repo.upsert_advertiser`, `repo.set_web_identity` (Task 6), `parse_web_identity` (Task 5), `refresh_one_advertiser(engine, extractor, advertiser_id, page_id, *, hist_pages=12, weeks=16, flip_vanished=False) -> dict` (existing, `src/agf/discovery/keeper_history.py`).
- Produces:
  - `pending_websub_pages(conn, limit) -> list` — confirmed web-sub funnel pages not yet advertisers (distinct page_id, emerging-first by `page_like_count NULLS FIRST`).
  - `async def run_websub_bridge(engine, extractor, *, fetch_text, limit=10) -> dict` returning `{"examined": int, "bridged": int, "errors": int}`. `fetch_text(url) -> str` fetches landing HTML.

- [ ] **Step 1: Write the failing test** (fakes for extractor + fetch_text; real DB)

```python
# tests/discovery/test_websub_bridge.py
import os, asyncio, pytest
from sqlalchemy import text
from agf.db.engine import get_engine
from agf.db.migrate import apply_migrations
from agf.discovery import websub_bridge

DB = os.environ.get("AGF_TEST_DB_URL")
pytestmark = pytest.mark.skipif(not DB, reason="needs AGF_TEST_DB_URL")

LANDING_HTML = ('<html><head><meta property="og:title" content="Sleep Tight">'
                '<meta property="og:description" content="Sleep better."></head></html>')

async def _fake_refresh(engine, extractor, advertiser_id, page_id, **kw):
    # simulate a Meta scrape persisting one ad so the page becomes a real advertiser
    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO public.ads (ad_archive_id, advertiser_id, is_active, start_date) "
            "VALUES (:aid, :adv, true, now()) ON CONFLICT DO NOTHING"),
            {"aid": f"ad-{page_id}", "adv": advertiser_id})
    return {"ads_stored": 1, "truncated": False, "flipped": 0}

def _seed_confirmed_websub(eng, page_id, domain):
    with eng.begin() as c:
        c.execute(text("""
            INSERT INTO discovery.funnel_candidates
              (page_id, page_name, page_like_count, landing_url, landing_domain,
               funnel_host, source_keyword, technique, status, payoff_kind, confirmed_at)
            VALUES (:pid, 'Sleep Tight', 0, :url, :dom, :dom, 'kw', 'funnel',
                    'confirmed', 'web_sub', now())
            ON CONFLICT (page_id, landing_domain) DO UPDATE
              SET status='confirmed', payoff_kind='web_sub'
        """), {"pid": page_id, "url": f"https://{domain}/start", "dom": domain})

def test_bridge_creates_advertiser_with_web_identity(monkeypatch):
    eng = get_engine(DB); apply_migrations(eng)
    monkeypatch.setattr(websub_bridge, "refresh_one_advertiser", _fake_refresh)
    _seed_confirmed_websub(eng, "WSBR1", "trysleeptight.app")
    stats = asyncio.run(websub_bridge.run_websub_bridge(
        eng, extractor=object(), fetch_text=lambda u: LANDING_HTML, limit=10))
    assert stats["bridged"] >= 1
    with eng.connect() as c:
        row = c.execute(text(
            "SELECT web_payoff_kind, web_name, web_landing_url FROM public.advertisers "
            "WHERE page_id='WSBR1'")).one()
    assert row.web_payoff_kind == "web_sub"
    assert row.web_name == "Sleep Tight"

def test_bridge_skips_pages_already_advertisers(monkeypatch):
    eng = get_engine(DB); apply_migrations(eng)
    monkeypatch.setattr(websub_bridge, "refresh_one_advertiser", _fake_refresh)
    _seed_confirmed_websub(eng, "WSBR2", "app.bossed.ai")
    asyncio.run(websub_bridge.run_websub_bridge(
        eng, extractor=object(), fetch_text=lambda u: LANDING_HTML, limit=10))
    with eng.connect() as c:
        pend = websub_bridge.pending_websub_pages(c, 10)
    assert all(r.page_id != "WSBR2" for r in pend)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it python -m pytest tests/discovery/test_websub_bridge.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

```python
# src/agf/discovery/websub_bridge.py
"""Bridge confirmed web-sub funnels into the advertiser pipeline.

A discovery.funnel_candidates row with payoff_kind='web_sub', status='confirmed'
has a page_id but no advertiser/ads. For each such page not yet an advertiser:
create the advertiser, fetch + store its web identity (OG/favicon), then scrape
its Meta ad corpus by page_id (refresh_one_advertiser). Downstream enrich/score/
matview then carry it to the feed. Bounded per cycle; idempotent."""
from types import SimpleNamespace

from sqlalchemy import text

from agf.db import repo
from agf.db.engine import begin
from agf.discovery.keeper_history import refresh_one_advertiser
from agf.discovery.web_identity import parse_web_identity

_PENDING = text("""
    SELECT DISTINCT ON (fc.page_id)
           fc.page_id, fc.page_name, fc.page_like_count, fc.landing_url
    FROM discovery.funnel_candidates fc
    WHERE fc.payoff_kind = 'web_sub' AND fc.status = 'confirmed'
      AND NOT EXISTS (SELECT 1 FROM public.advertisers a WHERE a.page_id = fc.page_id)
    ORDER BY fc.page_id, fc.page_like_count ASC NULLS FIRST, fc.first_seen_at
""")


def pending_websub_pages(conn, limit):
    rows = conn.execute(_PENDING).all()
    # emerging-first overall ordering, then cap
    rows = sorted(rows, key=lambda r: (r.page_like_count is not None,
                                       r.page_like_count or 0))
    return rows[:limit]


async def run_websub_bridge(engine, extractor, *, fetch_text, limit=10) -> dict:
    with engine.connect() as conn:
        rows = pending_websub_pages(conn, limit)
    stats = {"examined": len(rows), "bridged": 0, "errors": 0}
    for r in rows:
        try:
            try:
                html = fetch_text(r.landing_url)
            except Exception:
                html = ""
            ident = parse_web_identity(html, r.landing_url, fallback_name=r.page_name)
            with begin(engine) as conn:
                advertiser_id = repo.upsert_advertiser(conn, SimpleNamespace(
                    page_id=r.page_id, page_name=r.page_name,
                    page_like_count=r.page_like_count))
                repo.set_web_identity(conn, advertiser_id, payoff_kind="web_sub",
                                      name=ident.name, description=ident.description,
                                      icon_url=ident.icon_url, landing_url=r.landing_url)
            await refresh_one_advertiser(engine, extractor, advertiser_id, r.page_id)
            stats["bridged"] += 1
        except Exception:
            stats["errors"] += 1
    return stats
```

(`begin` is the same transaction helper used by `funnel_confirm.py`; confirm the import path matches that file's `from agf.db.engine import begin`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it python -m pytest tests/discovery/test_websub_bridge.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/websub_bridge.py tests/discovery/test_websub_bridge.py
git commit -m "feat(discovery): web-sub bridge stage (advertiser + identity + ad-corpus scrape)"
```

---

### Task 8: Wire the bridge into the worker cycle

**Files:**
- Modify: `src/agf/workers/main.py` (near the funnel stages, ~line 321-328; and the caps dict / env reads)
- Test: `tests/workers/test_websub_stage_wiring.py`

**Interfaces:**
- Consumes: `run_websub_bridge` (Task 7), `build_extractor` + the sweep-scoped `http_client` (existing in `main`).
- Produces: a per-cycle invocation bounded by `AGF_WEBSUB_BRIDGE_PER_CYCLE` (default `10`); a `websub(...)` summary appended to the sweep log line; the cap surfaced in the `caps={...}` startup dict as `websub_bridge_per_cycle`.

- [ ] **Step 1: Write the failing test** (assert the env→cap plumbing exists, mirroring the other `_int_env` caps)

```python
# tests/workers/test_websub_stage_wiring.py
import agf.workers.main as m

def test_websub_cap_default(monkeypatch):
    monkeypatch.delenv("AGF_WEBSUB_BRIDGE_PER_CYCLE", raising=False)
    assert m.websub_bridge_per_cycle() == 10

def test_websub_cap_env(monkeypatch):
    monkeypatch.setenv("AGF_WEBSUB_BRIDGE_PER_CYCLE", "3")
    assert m.websub_bridge_per_cycle() == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/workers/test_websub_stage_wiring.py -v`
Expected: FAIL (`AttributeError: websub_bridge_per_cycle`).

- [ ] **Step 3: Implement**

Add the env reader near the other cap helpers in `src/agf/workers/main.py`:

```python
def websub_bridge_per_cycle() -> int:
    try:
        return int(os.environ.get("AGF_WEBSUB_BRIDGE_PER_CYCLE", "10"))
    except ValueError:
        return 10
```

Add the import at the top of the file:

```python
from agf.discovery.websub_bridge import run_websub_bridge
```

In the sweep cycle, right after the `run_funnel_flywheel_stage(...)` call (~line 328), add (the cycle already has `engine`, `extractor`, and the sweep-scoped `http_client` in scope; `run_websub_bridge` is async, so run it on the loop the cycle already uses for async stages — match how `corpus_refresh`/`refresh_one_advertiser` are driven nearby, e.g. `asyncio.run(...)` if this block is synchronous):

```python
        try:
            websub = asyncio.run(run_websub_bridge(
                engine, extractor, fetch_text=lambda u: http_client.get(u).text,
                limit=websub_bridge_per_cycle()))
        except Exception as exc:
            websub = {"examined": 0, "bridged": 0, "errors": 1}
            print(f"[worker] websub bridge failed: {exc!r}", file=sys.stderr, flush=True)
```

Append to the sweep summary print (the `[worker] sweep: ...` f-string):

```python
            f" websub(examined={websub['examined']} bridged={websub['bridged']})"
```

Add `'websub_bridge_per_cycle': websub_bridge_per_cycle()` to the `caps={...}` dict logged at startup.

> NOTE for the implementer: confirm whether the surrounding cycle is already inside an event loop. If `refresh_one_advertiser`/`corpus_refresh` nearby are invoked via `asyncio.run(...)`, mirror that exactly; if the cycle is itself an async function, `await run_websub_bridge(...)` instead. Use the existing pattern in this file — do not introduce a second event-loop style.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/workers/test_websub_stage_wiring.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agf/workers/main.py tests/workers/test_websub_stage_wiring.py
git commit -m "feat(worker): wire web-sub bridge stage + AGF_WEBSUB_BRIDGE_PER_CYCLE"
```

---

### Task 9: Matview rebuild — COALESCE web identity + `web_only`

**Files:**
- Create: `db/schema/141_emerging_matview_websub.sql`
- Test: `tests/db/test_matview_websub.py`

**Interfaces:**
- Consumes: migration 140 columns; web-sub advertisers passing the `no_app` veto (Tasks 3-4).
- Produces: `frontend.emerging_scaling_apps` with `app_name`, `icon_url`, `tagline`, `funnel_url` COALESCE'd against the `web_*` columns, plus a new boolean `web_only` (true when `adv.web_payoff_kind IS NOT NULL`).

**IMPLEMENTER NOTE:** `frontend.emerging_scaling_apps` is a materialized view with a unique index used for `REFRESH ... CONCURRENTLY`. Migration 141 must `DROP MATERIALIZED VIEW` then `CREATE MATERIALIZED VIEW` with the FULL current definition plus the diffs below, and **recreate every index** the current matview has. Read `db/schema/138_emerging_matview_funnel_mmp_gated.sql` (the current definition + its `CREATE UNIQUE INDEX`/other indexes) and reproduce it verbatim, applying only these changes:
- In the `app` LATERAL subquery output, the feed reads `app.app_name`, `app.icon_url`, `app.tagline`. Replace the top-level select items so they COALESCE with web identity:
  - `COALESCE(app.app_name, adv.web_name) AS app_name`
  - `COALESCE(app.icon_url, adv.web_icon_url) AS icon_url`
  - `COALESCE(app.tagline, NULLIF(left(regexp_replace(adv.web_description, '\s+', ' ', 'g'), 200), '')) AS tagline`
- Replace the funnel column with `COALESCE(fn.funnel_url, adv.web_landing_url) AS funnel_url`.
- Add `(adv.web_payoff_kind IS NOT NULL) AS web_only` to the select list.
- Keep the `WHERE (s.vetoed = false OR s.veto_reason = 'longevity_below_floor') AND starts_with(adv.page_id, 'DEMO') IS NOT TRUE` clause unchanged.

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_matview_websub.py
import os, pytest
from sqlalchemy import text
from agf.db.engine import get_engine
from agf.db.migrate import apply_migrations

DB = os.environ.get("AGF_TEST_DB_URL")
pytestmark = pytest.mark.skipif(not DB, reason="needs AGF_TEST_DB_URL")

def test_matview_has_web_only_and_coalesces_identity():
    eng = get_engine(DB); apply_migrations(eng)
    with eng.connect() as c:
        cols = {r[0] for r in c.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='frontend' AND table_name='emerging_scaling_apps'"))}
    assert "web_only" in cols
    # identity columns still present (COALESCE keeps the same names)
    assert {"app_name", "icon_url", "tagline", "funnel_url"} <= cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it python -m pytest tests/db/test_matview_websub.py -v`
Expected: FAIL (`web_only` missing).

- [ ] **Step 3: Write the migration** — `db/schema/141_emerging_matview_websub.sql`, per the IMPLEMENTER NOTE above (drop + recreate matview from 138's body with the four diffs + index recreation).

- [ ] **Step 4: Run test to verify it passes**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it python -m pytest tests/db/test_matview_websub.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add db/schema/141_emerging_matview_websub.sql tests/db/test_matview_websub.py
git commit -m "feat(db): matview surfaces web-sub identity + web_only flag (migration 141)"
```

---

### Task 10: Go API — surface `web_only`

**Files:**
- Modify: the Go shortlist handler/struct (locate in Step 1)
- Test: Go test alongside (or the existing API test file for the shortlist endpoint)

**Interfaces:**
- Consumes: matview `web_only` column (Task 9).
- Produces: the `/api/shortlist` JSON rows include `web_only` (bool) and the already-COALESCE'd `icon_url`/`funnel_url`/`app_name` (no Go change needed for those — they reuse existing columns).

- [ ] **Step 1: Locate the query + struct**

Run:
```bash
grep -rIn -i "emerging_scaling_apps\|app_name\|funnel_url" backend/ | grep -iE '\.go' | head
```
Identify the SQL `SELECT` that reads the matview and the Go struct it scans into (the `ShortlistRow` equivalent with JSON tags).

- [ ] **Step 2: Write the failing test**

Add a test asserting the shortlist row struct has a `WebOnly` field with JSON tag `web_only` (mirror the nearest existing field test, or assert the JSON of a fixture row contains `"web_only"`). Run it; expected FAIL.

- [ ] **Step 3: Implement** — add `web_only` to the matview `SELECT`, add the struct field:

```go
WebOnly bool `json:"web_only"`
```

and include it in the row scan in matview column order.

- [ ] **Step 4: Run the Go tests**

Run: `cd backend && go test ./...`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/
git commit -m "feat(api): surface web_only on shortlist rows"
```

---

### Task 11: Web — `web_only` field + "Web only" badge

**Files:**
- Modify: `web/src/types/contracts.ts` (`ShortlistRow`)
- Modify: `web/src/components/discover/GemTable.tsx`
- Test: `web/` component/unit test for the badge (mirror an existing GemTable test if present)

**Interfaces:**
- Consumes: API `web_only` (Task 10).
- Produces: web-sub rows render the favicon in the icon slot, a plain **"Web only"** badge beside the name, and "—" in app-only numeric columns (installs, reviews).

- [ ] **Step 1: Write the failing test**

Add a test rendering `GemTable` with a fixture row `{ web_only: true, app_name: "Sleep Tight", icon_url: "https://x/favicon.ico", funnel_url: "https://trysleeptight.app/" }` and assert the text "Web only" appears and installs/reviews cells show "—". Run it; expected FAIL.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npm test -- GemTable`
Expected: FAIL (badge not rendered).

- [ ] **Step 3: Implement**

In `web/src/types/contracts.ts`, add to `ShortlistRow`:

```typescript
  web_only?: boolean;
```

In `GemTable.tsx`, where the name cell renders: when `row.web_only`, render a small badge after the name with the exact text `Web only` (reuse the existing badge/chip styling used elsewhere in the table; plain styling, no color implying a metric). In the installs and reviews cells, render `—` when `row.web_only` (those fields are null for web-sub). The icon cell already uses `icon_url`, which the matview COALESCEs to the favicon — no change needed there.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && npm test -- GemTable`
Expected: PASS. Also run `cd web && npm run build` (or the project's typecheck/lint) — expected: clean.

- [ ] **Step 5: Commit**

```bash
git add web/src/types/contracts.ts web/src/components/discover/GemTable.tsx web/
git commit -m "feat(web): Web only badge + favicon for web-sub feed entries"
```

---

### Task 12: Live end-to-end verification

**Files:** none (verification only).

- [ ] **Step 1: Full suite green**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it python -m pytest -q`
Expected: 0 failed.

- [ ] **Step 2: Apply migrations to the live DB**

Run the project's migration entrypoint against the live DB (host `localhost:5433`). Confirm `140_*` and `141_*` are recorded in `public.schema_migrations`.

- [ ] **Step 3: Run the bridge for the real backlog**

Drive `run_websub_bridge` (via the worker after a kickstart, or a one-shot script) with the real `Extractor()` and an httpx `fetch_text`, `limit=22`. Expected: advertisers created for the confirmed web-sub pages, ads scraped (`ads_stored > 0` for most), `bridged` count > 0.

- [ ] **Step 4: Verify the feed**

Query `frontend.emerging_scaling_apps WHERE web_only` after a score + matview refresh. Expected: the emerging tier appears — Sleep Tight, Bossed AI, Ultiself, Emma Collins, Facet — each with `icon_url` (favicon), `app_name` (OG/page name), `funnel_url` (landing). Confirm Marisa Peer / Acquisition Ace / Prose are **absent** (dropped by `incumbent_audience` / `established_traction`).

- [ ] **Step 5: Verify in the browser**

Load Discover; confirm a web-sub entry shows its favicon, the **"Web only"** badge, "—" for installs/reviews, and a working funnel link.

- [ ] **Step 6: Commit any verification notes / restart the worker**

```bash
launchctl kickstart -k gui/$(id -u)/com.fielddesk.worker
```

---

## Self-Review

**Spec coverage:**
- Web-sub bridge stage → Tasks 6-8. ✓
- Web-sub marker column → Task 1 (`web_payoff_kind`). ✓
- `no_app` veto exception → Task 3. ✓
- Scoring axis handling (None not 0) → Task 4. ✓
- Web identity (icon + info) fetch + storage → Tasks 5, 6, 7. ✓
- Matview COALESCE + `web_only` → Task 9. ✓
- UI badge → Tasks 10 (API), 11 (web). ✓
- Emerging filter via existing incumbent vetoes (no new ceiling) → verified in Task 12 Step 4. ✓
- Testing (unit/DB/live) → each task + Task 12. ✓
- Non-goals respected: no separate feed, no like ceiling, only `web_sub`, no growth backfill. ✓

**Placeholder scan:** No TBD/TODO. The two "locate via grep" steps (Tasks 10-11) are for the Go/web layers whose exact paths weren't pinned; each gives the exact grep + the exact field/struct change. The matview migration (Task 9) intentionally references `138_*.sql` as the index/definition source of truth and lists the precise diffs — reproducing 200 lines of matview + index DDL verbatim here would be error-prone vs. the canonical file.

**Type consistency:** `web_payoff_kind` (DB + FeatureVector + repo), `web_only` (matview + Go struct `WebOnly`/`web_only` + TS `web_only`), `WebIdentity(name, description, icon_url)`, `run_websub_bridge(engine, extractor, *, fetch_text, limit)`, `set_web_identity(conn, advertiser_id, *, payoff_kind, name, description, icon_url, landing_url)`, `refresh_one_advertiser(engine, extractor, advertiser_id, page_id, ...)` — names consistent across tasks.
