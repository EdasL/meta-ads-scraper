# Funnel Pipeline — Engine 1 (discovery + domain clustering) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a sibling "funnel" discovery pipeline that sweeps quiz/funnel-intent keywords, KEEPS web destinations (instead of dropping them like the apps-only sweep), parses each funnel's landing domain + UTMs, persists them to their own store, and clusters advertisers by shared landing domain.

**Architecture:** A new pipeline under `src/agf/discovery/` (`funnel_classify.py` pure helpers + `funnel_sweep.py`) writing to a new `discovery.funnel_candidates` table via `src/agf/db/funnel_candidates.py`. It shares only pure primitives with the app pipeline (`classify_url`, `Extractor.search`, `taxonomy`, the page-likes veto constant). The existing app pipeline (`discovery/sweep.py` → `triage.py`, `discovery.candidates`) is **not touched**. Confirm/render (Engine 2 — install-vs-subscribe classification) is a later plan; Engine 1 persists every funnel candidate with `kind='unknown'`.

**Tech Stack:** Python 3.13, SQLAlchemy Core (raw `text()`), PostgreSQL (`discovery` schema), pytest, asyncio. Background: `docs/research/2026-06-11-funnel-discovery-method.md` (Stage 1 "three engines").

**Conventions to mirror (read these first):**
- App sweep: `src/agf/discovery/sweep.py` (the structure we parallel — note `sweep.py:51` drops web as `non_app`; we do the inverse).
- App candidate repo: `src/agf/db/candidates.py` (upsert/`new_*` patterns).
- App candidate schema: `db/schema/112_discovery_candidates.sql`.
- Funnel heuristics live today inside `scripts/discover_experiment.py` (`funnel_signal`, `FUNNEL_HOST_HINTS`, `FUNNEL_WORDS`, `LEADGEN_CTAS`, `SOCIAL_HOSTS`, `host_of`) — Task 1 promotes them into a real module.
- Tests: `tests/discovery/test_sweep.py` (fold-rule + `FakeExtractor` persistence style), `tests/db/test_candidates.py` (`pg_conn` rolled-back style with `SENTINEL*` page_ids), `tests/discovery/conftest.py` (`FakeExtractor`, `make_ad`).
- Migrations auto-apply in lexical order via `agf.db.migrate.apply_migrations` (the test DB fixture runs this; for a local DB run the one-liner in Task 2).

**Migration number:** uses **120** (latest committed on `main` is 117). NOTE: the unmerged `feat/gemscore-traction-axis` branch reserves 118/119 — using 120 here avoids a collision if both branches land. If 118/119 are still free at merge time, renumbering down is optional, not required (apply order is lexical, 120 sorts after them fine).

**DB test prerequisite:** the `pg_conn` / `_migrated_engine` fixtures need the local test Postgres up (the `agf_it` test DB — see memory `local-stack-run-2026-06-09`). Pure-function tests (Task 1) need no DB.

**Run tests with:** `PYTHONPATH=src .venv/bin/pytest <path> -v`

---

### Task 1: `funnel_classify.py` — pure funnel-classification helpers

**Files:**
- Create: `src/agf/discovery/funnel_classify.py`
- Test: `tests/discovery/test_funnel_classify.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/discovery/test_funnel_classify.py
from agf.discovery import funnel_classify as fc
from agf.parse import AdRecord


def _ad(link, cta="LEARN_MORE", body="hello"):
    return AdRecord("a1", "P1", "Page", True, None, None, 1, cta, "x", link, body)


def test_landing_domain_unwraps_fb_shim_and_strips_www():
    shim = "https://l.facebook.com/l.php?u=https%3A%2F%2Fwww.quiz.betterme.world%2Fstart%3Fa%3D1"
    assert fc.landing_domain(shim) == "quiz.betterme.world"
    assert fc.landing_domain("https://www.simple.life/") == "simple.life"
    assert fc.landing_domain(None) is None


def test_parse_utms():
    got = fc.parse_utms("https://quiz.x.com/?utm_source=fb&utm_campaign=wallpilates_es")
    assert got["utm_source"] == "fb"
    assert got["utm_campaign"] == "wallpilates_es"
    assert got["utm_medium"] is None and got["utm_content"] is None


def test_matched_funnel_host():
    assert fc.matched_funnel_host("https://quiz.example.com/x") == "quiz."
    assert fc.matched_funnel_host("https://example.com/x") is None


def test_funnel_signal_true_paths():
    assert fc.funnel_signal(_ad("https://example.com/p", cta="NO"), "funnel_phrase") is True
    assert fc.funnel_signal(_ad("https://try.example.com/p", cta="NO", body=""), "niche_term") is True
    assert fc.funnel_signal(_ad("https://example.com/p", cta="NO", body="take the quiz"), "niche_term") is True
    assert fc.funnel_signal(_ad("https://example.com/p", cta="SUBSCRIBE", body=""), "niche_term") is True


def test_funnel_signal_false_for_plain_web():
    assert fc.funnel_signal(_ad("https://example.com/p", cta="NO", body="just words"), "niche_term") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/pytest tests/discovery/test_funnel_classify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agf.discovery.funnel_classify'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agf/discovery/funnel_classify.py
"""Pure funnel-classification helpers, promoted from scripts/discover_experiment.py so the
funnel pipeline can share them. No DB, no network. The app pipeline never imports this."""
from urllib.parse import urlparse, parse_qs, unquote

# Destinations that are neither an app nor a funnel — a social page or ad redirect.
SOCIAL_HOSTS = {"facebook.com", "fb.me", "m.facebook.com", "l.facebook.com", "lm.facebook.com",
                "instagram.com", "api.whatsapp.com", "wa.me", "t.co", "ad.doubleclick.net",
                "youtube.com", "youtu.be", "twitter.com", "x.com", "linktr.ee", "messenger.com"}
# Sub-domains / infra that signal a real web2app or quiz funnel even without funnel copy.
FUNNEL_HOST_HINTS = ("quiz.", "try.", "tour.", "get.", "go.", "start.", "join.", "app.",
                     "interview.", "info.", "onelink", "adjust", "adapty", "dynamicflow",
                     "lovable.app", "typeform", "involve.me", ".health", "reverse.health")
FUNNEL_WORDS = ("quiz", "personalized", "plan", "your results", "based on your",
                "tailored", "custom", "journey", "assessment", "find your")
LEADGEN_CTAS = {"SIGN_UP", "SUBSCRIBE", "GET_OFFER", "LEARN_MORE", "GET_QUOTE",
                "BOOK_TRAVEL", "APPLY_NOW", "DOWNLOAD"}

_FB_SHIMS = {"l.facebook.com", "lm.facebook.com"}


def _unwrap(url: str) -> str:
    """Unwrap an l.facebook.com/?u=<real> click-shim to the real destination."""
    p = urlparse(url)
    if p.netloc in _FB_SHIMS:
        u = parse_qs(p.query).get("u", [None])[0]
        if u:
            return unquote(u)
    return url


def host_of(url: str | None) -> str:
    if not url:
        return ""
    return urlparse(url).netloc.lower().replace("www.", "")


def landing_domain(url: str | None) -> str | None:
    """Host of the true destination (FB shim unwrapped, www stripped). None if empty."""
    if not url:
        return None
    host = urlparse(_unwrap(url)).netloc.lower().replace("www.", "")
    return host or None


def parse_utms(url: str | None) -> dict:
    """{'utm_source','utm_medium','utm_campaign','utm_content'} from the destination query
    (FB shim unwrapped); missing keys -> None."""
    q = parse_qs(urlparse(_unwrap(url or "")).query)
    return {k: q.get(k, [None])[0]
            for k in ("utm_source", "utm_medium", "utm_campaign", "utm_content")}


def matched_funnel_host(url: str | None) -> str | None:
    """The first FUNNEL_HOST_HINT present in the (unwrapped) host, or None."""
    host = host_of(_unwrap(url or ""))
    return next((h for h in FUNNEL_HOST_HINTS if h in host), None)


def funnel_signal(ad, keyword_layer: str) -> bool:
    """True when an ad's web destination looks like a quiz/web2app funnel."""
    if keyword_layer == "funnel_phrase":
        return True
    if matched_funnel_host(ad.link_url):
        return True
    blob = ((ad.body_text or "") + " " + (ad.link_url or "")).lower()
    if any(w in blob for w in FUNNEL_WORDS):
        return True
    return (ad.cta_type or "") in LEADGEN_CTAS
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/pytest tests/discovery/test_funnel_classify.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/funnel_classify.py tests/discovery/test_funnel_classify.py
git commit -m "feat(funnel): pure funnel-classification helpers (domain/UTM/signal)"
```

---

### Task 2: Migration — `discovery.funnel_candidates` table

**Files:**
- Create: `db/schema/120_funnel_candidates.sql`

- [ ] **Step 1: Write the migration**

```sql
-- db/schema/120_funnel_candidates.sql
-- 120: web-funnel discovery pool (docs/research/2026-06-11-funnel-discovery-method.md).
-- Sibling to discovery.candidates (apps). store/store_id are NULLABLE here — a
-- web2subscribe funnel may have no app. Keyed on (page_id, landing_domain): one
-- advertiser can run several funnels. status: new -> confirmed|dropped (the confirm
-- stage, a later milestone, sets kind=install|subscribe and the store columns).
CREATE TABLE IF NOT EXISTS discovery.funnel_candidates (
  id              BIGSERIAL PRIMARY KEY,
  page_id         TEXT NOT NULL,
  page_name       TEXT,
  page_like_count INTEGER,
  landing_url     TEXT,
  landing_domain  TEXT NOT NULL,
  funnel_host     TEXT,
  cta_type        TEXT,
  utm_source      TEXT,
  utm_medium      TEXT,
  utm_campaign    TEXT,
  utm_content     TEXT,
  ad_start_date   DATE,
  source_keyword  TEXT,
  technique       TEXT,
  kind            TEXT NOT NULL DEFAULT 'unknown' CHECK (kind IN ('unknown','install','subscribe')),
  store           TEXT CHECK (store IN ('ios','android')),
  store_id        TEXT,
  times_seen      INTEGER NOT NULL DEFAULT 1,
  first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  status          TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new','confirmed','dropped')),
  drop_reason     TEXT,
  confirmed_at    TIMESTAMPTZ,
  UNIQUE (page_id, landing_domain)
);
CREATE INDEX IF NOT EXISTS funnel_candidates_status_idx ON discovery.funnel_candidates (status);
CREATE INDEX IF NOT EXISTS funnel_candidates_domain_idx ON discovery.funnel_candidates (landing_domain);
```

- [ ] **Step 2: Apply to the local DB and verify**

Run:
```bash
PYTHONPATH=src AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf .venv/bin/python -c "from agf.db.engine import get_engine; from agf.db.migrate import apply_migrations; print(apply_migrations(get_engine()))"
```
Expected: output includes `120_funnel_candidates.sql`. (A second run prints `[]` — idempotent.)

Verify the table exists:
```bash
PYTHONPATH=src AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf .venv/bin/python -c "from sqlalchemy import text; from agf.db.engine import get_engine; print(get_engine().connect().execute(text(\"SELECT to_regclass('discovery.funnel_candidates')\")).scalar())"
```
Expected: `discovery.funnel_candidates`

- [ ] **Step 3: Commit**

```bash
git add db/schema/120_funnel_candidates.sql
git commit -m "feat(db): discovery.funnel_candidates table (migration 120)"
```

---

### Task 3: `db/funnel_candidates.py` — repo (upsert / new / cluster)

**Files:**
- Create: `src/agf/db/funnel_candidates.py`
- Test: `tests/db/test_funnel_candidates.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_funnel_candidates.py
"""discovery.funnel_candidates repo — dedup bumps on (page_id, landing_domain), distinct
domain = new row, shared-domain clustering. Rolled-back pg_conn; FUNTEST* sentinels only."""
from datetime import date

from agf.db import funnel_candidates as fcand

ROW = dict(page_id="FUNTEST1", page_name="Funnel Test", page_like_count=900,
           landing_url="https://quiz.funtest.world/start?utm_source=fb",
           landing_domain="quiz.funtest.world", funnel_host="quiz.",
           cta_type="LEARN_MORE", utm_source="fb", utm_medium=None,
           utm_campaign=None, utm_content=None, ad_start_date=date(2026, 6, 1),
           source_keyword="take the quiz", technique="funnel_phrase")


def _mine(rows):
    return [r for r in rows if r.page_id.startswith("FUNTEST")]


def test_upsert_inserts_then_bumps(pg_conn):
    cid, was_new = fcand.upsert_funnel_candidate(pg_conn, ROW)
    assert was_new is True
    cid2, was_new2 = fcand.upsert_funnel_candidate(pg_conn, {**ROW, "page_like_count": 1500})
    assert cid2 == cid and was_new2 is False
    row = _mine(fcand.new_funnel_candidates(pg_conn))[0]
    assert row.times_seen == 2
    assert row.page_like_count == 1500          # freshest non-null likes win


def test_distinct_domain_is_a_new_row(pg_conn):
    fcand.upsert_funnel_candidate(pg_conn, ROW)
    _, was_new = fcand.upsert_funnel_candidate(pg_conn, {**ROW, "landing_domain": "quiz2.funtest.world"})
    assert was_new is True


def test_cluster_by_domain(pg_conn):
    fcand.upsert_funnel_candidate(pg_conn, {**ROW, "page_id": "FUNTESTA"})
    fcand.upsert_funnel_candidate(pg_conn, {**ROW, "page_id": "FUNTESTB"})
    clusters = [c for c in fcand.cluster_by_domain(pg_conn, min_advertisers=2)
                if c.landing_domain == "quiz.funtest.world"]
    assert clusters and clusters[0].n_advertisers >= 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/pytest tests/db/test_funnel_candidates.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agf.db.funnel_candidates'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agf/db/funnel_candidates.py
"""Repo for the web-funnel discovery pool (docs/research/2026-06-11-funnel-discovery-method).
Sibling to db/candidates.py; append-mostly, conn-first (callers own the txn). A re-seen
(page_id, landing_domain) bumps times_seen/last_seen/likes and NEVER regresses status."""
from sqlalchemy import text

_COLS = ("page_id, page_name, page_like_count, landing_url, landing_domain, funnel_host, "
         "cta_type, utm_source, utm_medium, utm_campaign, utm_content, ad_start_date, "
         "source_keyword, technique")


def upsert_funnel_candidate(conn, row: dict) -> tuple[int, bool]:
    """Insert or bump one funnel candidate. Returns (id, was_new)."""
    got = conn.execute(text(f"""
        INSERT INTO discovery.funnel_candidates ({_COLS})
        VALUES (:page_id, :page_name, :page_like_count, :landing_url, :landing_domain,
                :funnel_host, :cta_type, :utm_source, :utm_medium, :utm_campaign,
                :utm_content, :ad_start_date, :source_keyword, :technique)
        ON CONFLICT (page_id, landing_domain) DO UPDATE SET
          times_seen = discovery.funnel_candidates.times_seen + 1,
          last_seen_at = now(),
          page_like_count = COALESCE(EXCLUDED.page_like_count,
                                     discovery.funnel_candidates.page_like_count)
        RETURNING id, (xmax = 0) AS was_new
    """), row).one()
    return got.id, bool(got.was_new)


def new_funnel_candidates(conn, limit: int | None = None):
    """Unconfirmed funnel candidates, oldest first (the confirm-stage feed)."""
    q = ("SELECT id, page_id, page_name, page_like_count, landing_url, landing_domain, "
         "funnel_host, cta_type, times_seen, source_keyword "
         "FROM discovery.funnel_candidates WHERE status = 'new' ORDER BY first_seen_at, id")
    if limit:
        q += f" LIMIT {int(limit)}"
    return conn.execute(text(q)).all()


def cluster_by_domain(conn, min_advertisers: int = 2):
    """Landing domains pushed by >= min_advertisers distinct pages — shared-funnel /
    affiliate-network clusters. Ordered by advertiser count desc."""
    return conn.execute(text("""
        SELECT landing_domain,
               count(DISTINCT page_id) AS n_advertisers,
               array_agg(DISTINCT page_id) AS page_ids
        FROM discovery.funnel_candidates
        GROUP BY landing_domain
        HAVING count(DISTINCT page_id) >= :m
        ORDER BY n_advertisers DESC, landing_domain
    """), {"m": min_advertisers}).all()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/pytest tests/db/test_funnel_candidates.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agf/db/funnel_candidates.py tests/db/test_funnel_candidates.py
git commit -m "feat(db): funnel_candidates repo (upsert/new/cluster-by-domain)"
```

---

### Task 4: `funnel_sweep.py` — the fold rule (`classify_funnel_candidate`)

**Files:**
- Create: `src/agf/discovery/funnel_sweep.py`
- Test: `tests/discovery/test_funnel_sweep.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/discovery/test_funnel_sweep.py
import asyncio
from sqlalchemy import text

from agf.discovery.funnel_sweep import classify_funnel_candidate, run_funnel_sweep
from agf.discovery.taxonomy import TaxonomyEntry
from agf.parse import AdRecord
from tests.discovery.conftest import FakeExtractor

NOW = 1_749_000_000


def ad(link, likes=500, page_id="FUNSWEEP1", cta="LEARN_MORE", body="body"):
    return AdRecord("a1", page_id, "Funnel Sweep", True, NOW - 5 * 86_400, None, 1,
                    cta, "x", link, body, page_like_count=likes)


def test_web_funnel_is_a_candidate():
    d, row = classify_funnel_candidate(ad("https://quiz.example.com/start?utm_source=fb"),
                                       keyword="take the quiz", technique="funnel_phrase")
    assert d == "candidate"
    assert row["landing_domain"] == "quiz.example.com"
    assert row["funnel_host"] == "quiz." and row["utm_source"] == "fb"
    assert row["source_keyword"] == "take the quiz"


def test_funnel_drops():
    cases = [
        (ad("https://apps.apple.com/us/app/x/id12345"), "app"),           # store link -> app pipeline
        (ad("https://www.instagram.com/x"), "social"),
        (ad("https://quiz.example.com/x", likes=10_001), "incumbent_likes"),
        (ad(None), "no_link"),
        (ad("https://plainsite.com/x", cta="NO", body="nothing"), "not_funnel"),
    ]
    for record, expected in cases:
        d, row = classify_funnel_candidate(record, keyword="k", technique="niche_term")
        assert (d, row) == (expected, None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/pytest tests/discovery/test_funnel_sweep.py::test_web_funnel_is_a_candidate tests/discovery/test_funnel_sweep.py::test_funnel_drops -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agf.discovery.funnel_sweep'`

- [ ] **Step 3: Write minimal implementation (fold rule + the run loop used by Task 5)**

```python
# src/agf/discovery/funnel_sweep.py
"""Stage 1 of the FUNNEL pipeline — sibling to discovery/sweep.py, but it KEEPS web
destinations (the inverse of sweep.py's non_app drop) and writes discovery.funnel_candidates.
Shares only pure helpers (funnel_classify, classify_url). The app sweep is untouched.

Inline drops (cheapest-first): no link -> social host -> incumbent page likes -> store link
(belongs to the APP pipeline) -> web destination without a funnel signal.
Commits per keyword (short txns) so a mid-run throttle-kill keeps progress."""
import time
from datetime import datetime, timezone

from agf.classify import classify_url
from agf.db.engine import begin
from agf.db.funnel_candidates import upsert_funnel_candidate
from agf.discovery import funnel_classify as fc
from agf.discovery.taxonomy import all_entries
from agf.extract.errors import ExtractError
from agf.scoring.vetoes import INCUMBENT_PAGE_LIKES_MAX


def _start_date(epoch):
    if not epoch:
        return None
    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).date()
    except (TypeError, ValueError, OSError):
        return None


def classify_funnel_candidate(ad, *, keyword: str, technique: str) -> tuple[str, dict | None]:
    """Fold one AdRecord into (disposition, funnel-row). Dispositions:
    'candidate' | 'no_link' | 'social' | 'incumbent_likes' | 'app' | 'not_funnel'."""
    if not ad.page_id or not ad.link_url:
        return "no_link", None
    if fc.host_of(ad.link_url) in fc.SOCIAL_HOSTS:
        return "social", None
    likes = ad.page_like_count
    if likes is not None and likes > INCUMBENT_PAGE_LIKES_MAX:
        return "incumbent_likes", None
    if classify_url(ad.link_url).kind in ("ios", "android"):
        return "app", None                       # store link -> the APP pipeline owns it
    if not fc.funnel_signal(ad, technique):
        return "not_funnel", None
    domain = fc.landing_domain(ad.link_url)
    if not domain:
        return "no_link", None
    return "candidate", {
        "page_id": ad.page_id, "page_name": ad.page_name, "page_like_count": likes,
        "landing_url": ad.link_url, "landing_domain": domain,
        "funnel_host": fc.matched_funnel_host(ad.link_url),
        "cta_type": ad.cta_type, "ad_start_date": _start_date(ad.start_date),
        "source_keyword": keyword, "technique": technique, **fc.parse_utms(ad.link_url),
    }


async def run_funnel_sweep(engine, extractor, *, entries=None, countries=("US",),
                           max_pages: int = 3, kw_delay: float = 3.0,
                           sleep=time.sleep) -> dict:
    """Sweep the funnel_phrase layer (default) and persist funnel candidates.
    Returns counters. Commits per keyword."""
    if entries is None:
        entries = all_entries(layer="funnel_phrase")
    stats = {"keywords_swept": 0, "serp_requests": 0, "raw_ads": 0,
             "candidates_new": 0, "candidates_seen": 0, "errors": 0,
             "drops": {"no_link": 0, "social": 0, "incumbent_likes": 0,
                       "app": 0, "not_funnel": 0}}
    for entry in entries:
        pages = min(entry.max_pages, max_pages)
        try:
            ads = await extractor.search(query=entry.keyword, countries=list(countries),
                                         search_type=entry.search_type, max_pages=pages)
        except ExtractError:
            stats["errors"] += 1
            sleep(kw_delay * 2)
            continue
        stats["keywords_swept"] += 1
        stats["serp_requests"] += pages
        stats["raw_ads"] += len(ads)
        rows, seen = [], set()
        for ad in ads:
            disp, row = classify_funnel_candidate(ad, keyword=entry.keyword, technique=entry.layer)
            if disp != "candidate":
                stats["drops"][disp] += 1
                continue
            key = (row["page_id"], row["landing_domain"])
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
        if rows:
            with begin(engine) as conn:
                for row in rows:
                    _, was_new = upsert_funnel_candidate(conn, row)
                    stats["candidates_new" if was_new else "candidates_seen"] += 1
        sleep(kw_delay)
    return stats
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/pytest tests/discovery/test_funnel_sweep.py::test_web_funnel_is_a_candidate tests/discovery/test_funnel_sweep.py::test_funnel_drops -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/funnel_sweep.py tests/discovery/test_funnel_sweep.py
git commit -m "feat(funnel): funnel_sweep fold rule (keep web, drop app/social/incumbent)"
```

---

### Task 5: `run_funnel_sweep` — persistence via FakeExtractor

**Files:**
- Modify: `tests/discovery/test_funnel_sweep.py` (add the persistence test; implementation already written in Task 4)

- [ ] **Step 1: Write the failing test (append to the file)**

```python
def test_run_funnel_sweep_persists(_migrated_engine):
    eng = _migrated_engine
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.funnel_candidates WHERE page_id LIKE 'FUNSWEEP%'"))
    try:
        ext = FakeExtractor(keyword_ads={
            "take the quiz": [
                ad("https://quiz.example.com/start"),
                ad("https://apps.apple.com/us/app/x/id1", page_id="FUNSWEEP2"),  # app -> drop
            ],
            "personalized plan": [
                ad("https://quiz.example.com/start"),    # same page+domain -> bump
            ],
        })
        entries = [TaxonomyEntry("take the quiz", "funnel_phrase", "KEYWORD_EXACT_PHRASE", 3),
                   TaxonomyEntry("personalized plan", "funnel_phrase", "KEYWORD_EXACT_PHRASE", 3)]
        stats = asyncio.run(run_funnel_sweep(eng, ext, entries=entries, sleep=lambda s: None))
        assert stats["keywords_swept"] == 2
        assert stats["candidates_new"] == 1
        assert stats["candidates_seen"] == 1          # the dup bumped
        assert stats["drops"]["app"] == 1
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT times_seen, status, kind FROM discovery.funnel_candidates "
                "WHERE page_id='FUNSWEEP1' AND landing_domain='quiz.example.com'")).one()
        assert row.times_seen == 2 and row.status == "new" and row.kind == "unknown"
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.funnel_candidates WHERE page_id LIKE 'FUNSWEEP%'"))
```

- [ ] **Step 2: Run test to verify it passes** (implementation exists from Task 4)

Run: `PYTHONPATH=src .venv/bin/pytest tests/discovery/test_funnel_sweep.py -v`
Expected: PASS (3 passed — the two from Task 4 plus this one)

- [ ] **Step 3: Commit**

```bash
git add tests/discovery/test_funnel_sweep.py
git commit -m "test(funnel): run_funnel_sweep persists + dedups via FakeExtractor"
```

---

### Task 6 (optional): runnable entrypoint — live sweep + cluster report

A thin glue script (no new logic; not unit-tested — it wires proven infra). It mirrors the Extractor + `AGF_PROXIES` setup at the top of `scripts/discover_experiment.py`.

**Files:**
- Create: `scripts/run_funnel_sweep.py`

- [ ] **Step 1: Write the script**

```python
# scripts/run_funnel_sweep.py
"""Live funnel-phrase sweep -> discovery.funnel_candidates, then print domain clusters.
Run: AGF_PROXIES="http://user:pass@gw.dataimpulse.com:823" \
     AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf PYTHONPATH=src \
     .venv/bin/python scripts/run_funnel_sweep.py
Mirrors the Extractor construction in scripts/discover_experiment.py."""
import asyncio

from agf.db.engine import get_engine
from agf.db.funnel_candidates import cluster_by_domain
from agf.discovery.funnel_sweep import run_funnel_sweep
from agf.extract.facade import Extractor


async def main() -> None:
    engine = get_engine()
    extractor = Extractor(search_delay=2.0, search_jitter=1.5)   # same as discover_experiment.py:352
    stats = await run_funnel_sweep(engine, extractor)
    print("sweep:", stats)
    with engine.connect() as conn:
        clusters = cluster_by_domain(conn, min_advertisers=2)
    print(f"\nshared-domain funnel clusters ({len(clusters)}):")
    for c in clusters[:40]:
        print(f"  {c.n_advertisers:3d}  {c.landing_domain}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Smoke-run (requires DataImpulse proxy + local DB)**

Run the command in the script docstring.
Expected: a `sweep: {...}` counter line with `candidates_new > 0`, then a list of shared-domain clusters.

- [ ] **Step 3: Commit**

```bash
git add scripts/run_funnel_sweep.py
git commit -m "feat(funnel): runnable live funnel sweep + domain-cluster report"
```

---

## Final verification

- [ ] Run the whole new suite: `PYTHONPATH=src .venv/bin/pytest tests/discovery/test_funnel_classify.py tests/discovery/test_funnel_sweep.py tests/db/test_funnel_candidates.py -v` — all green.
- [ ] Run the full suite to confirm the app pipeline is untouched: `PYTHONPATH=src .venv/bin/pytest -q` — no regressions.

## Out of scope (later plans)

- **Engine 2 — confirm/render:** step through the quiz with `funnel_capture` (Playwright), fingerprint the post-quiz paywall (Solidgate/`fnlfx`/Stripe + MMP + pixel), set `kind=install|subscribe`, resolve `store`/`store_id`, mark `status=confirmed|dropped`, add `funnels.app_id` linkage.
- **Engine 2 — PublicWWW code-fingerprint expansion** (find funnels not on Meta) and **Engine 3 — seed-list ingestion** (Apple top-grossing RSS, Screens Design, etc.).
- **Worker wiring:** call `run_funnel_sweep` from the production worker once confirm exists (we don't want unconfirmed funnels in the live pool yet).
- **Localization signal + UTM-cluster scoring** (per §7 of the research doc).
