# Funnel Pipeline — Engine 2 v1 (confirm + label) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For each `new` row in `discovery.funnel_candidates`, render its landing page, fingerprint the funnel tech, and label it `install | subscribe | unknown` (status `confirmed`) or drop it (status `dropped`, generic-web noise) — so the funnel pool becomes clean and labeled.

**Architecture:** A new `discovery/funnel_confirm.py` with a PURE classifier (`classify_funnel`) and a SYNC orchestrator (`run_funnel_confirm`) that takes **injectable `resolve` and `render` callables** (so the async `Extractor.resolve_deeplink` + sync Playwright render are wired in the runnable script, never inside the orchestrator — keeps it testable with fakes, no network). Reuses `resolve_deeplink` (MMP→store), a new landing-render helper in `funnel_capture.py`, and a reworked `firehose_fingerprint.SIGNATURES` (the corrected web-checkout set from the live test). Engine 1 and the app pipeline are untouched.

**v1 scope (decided):** render the **landing page** (consent-dismissed) + resolve the deeplink. Install detection is reliable (resolve→store, or store/MMP sig in HTML). Subscribe = a real web-checkout SDK present. Pages with a Meta pixel or a recognized funnel host but no clear payoff are kept as `unknown`/confirmed. No funnel tech + no store ⇒ dropped (the "Pulse Giftcards"/"Locate Isle of Man" noise). **Out of scope (v2):** answer-aware quiz-stepping to *guarantee* reaching every post-quiz paywall; screenshots; worker wiring (surface funnels first, then turn on continuous).

**Tech Stack:** Python 3.13, SQLAlchemy Core, Playwright (lazy, optional), pytest. Background: `docs/research/2026-06-11-funnel-discovery-method.md` §Stage 2 + §4 live validation.

**Conventions to mirror (read first):**
- `src/agf/discovery/firehose_fingerprint.py` (the SIGNATURES/detect_signatures we rework) + `tests/discovery/test_firehose_fingerprint.py` (test style).
- `src/agf/enrich_full/funnel_capture.py` (`autoconsent_cmp`, `_playwright_page`, `resolve_landing`; schemas `ResolvedTarget{kind,store_id,landing_url,final_url}`).
- `src/agf/extract/deeplink.py` (`ResolvedLink{final_url, intent}`, `intent` is `AppIntent{kind,store_id}`).
- `src/agf/db/funnel_candidates.py` (`new_funnel_candidates`, the upsert pattern) — Engine 1, migration 120.
- `src/agf/classify.py` (`classify_url`).

**Run tests:** `PYTHONPATH=src .venv/bin/pytest <path> -v`. DB tests need `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf` (else they SKIP). The funnel pool already has live rows from Engine 1.

---

### Task 1: Rework `firehose_fingerprint.SIGNATURES` (corrected web-checkout set)

**Files:**
- Modify: `src/agf/discovery/firehose_fingerprint.py`
- Modify: `tests/discovery/test_firehose_fingerprint.py`

- [ ] **Step 1: Check existing usages** (so renaming the `paywall` category doesn't break a prod caller)

Run: `grep -rn "classify_funnel_intent\|SIGNATURES\|detect_signatures" src/ tests/ | grep -v test_firehose_fingerprint`
Expected: only `firehose_fingerprint.py` itself (Firehose C is not wired into the production worker). If any OTHER `src/` caller appears, STOP and report — the rename needs that caller updated too.

- [ ] **Step 2: Write/extend the failing test** — replace the signature-detection test body in `tests/discovery/test_firehose_fingerprint.py` with these (keep any unrelated `parse_publicwww` tests in the file):

```python
from agf.discovery.firehose_fingerprint import detect_signatures, classify_funnel_intent


def test_detect_checkout_solidgate_and_stripe():
    assert "checkout" in detect_signatures('<script src="https://cdn.solidgate.com/js/solid-form.js">')
    assert "checkout" in detect_signatures('<script src="https://js.stripe.com/v3/"></script>')
    assert "checkout" in detect_signatures('<script src="https://primer.io/sdk"></script>')


def test_detect_mmp_and_store():
    assert "mmp" in detect_signatures('<a href="https://brand.onelink.me/abc">get the app</a>')
    assert "store" in detect_signatures('<a href="https://apps.apple.com/us/app/x/id123">iOS</a>')


def test_detect_pixel_via_gtm():
    assert "pixel" in detect_signatures('<script src="https://www.googletagmanager.com/gtm.js?id=GTM-X">')
    assert "pixel" in detect_signatures("fbq('init','123')")


def test_app_side_sdks_are_not_checkout():
    # RevenueCat/Superwall/Adapty are app-side — they matched 0/6 live, must NOT be 'checkout'
    assert "checkout" not in detect_signatures('<script src="https://api.revenuecat.com/v1"></script>')


def test_classify_funnel_intent_is_checkout_and_pixel():
    html = '<script src="https://cdn.solidgate.com/js/solid-form.js"></script>' + "fbq('init')"
    assert classify_funnel_intent(html) is True
    assert classify_funnel_intent('<script src="https://js.stripe.com/v3/"></script>') is False  # pixel missing
```

- [ ] **Step 3: Run it, confirm FAIL**

Run: `PYTHONPATH=src .venv/bin/pytest tests/discovery/test_firehose_fingerprint.py -v`
Expected: failures — `'checkout' not in ...` / KeyErrors (old SIGNATURES has `paywall`, not `checkout`/`store`).

- [ ] **Step 4: Rework the implementation** — replace the `SIGNATURES` dict and `classify_funnel_intent` in `src/agf/discovery/firehose_fingerprint.py` (leave `detect_signatures`, `parse_publicwww`, `fingerprint_search` as-is):

```python
# Corrected by the 2026-06-12 live test (docs/research/2026-06-11-funnel-discovery-method.md §4):
# web2app funnels use WEB-checkout SDKs (Solidgate dominant), not the app-side RevenueCat/
# Superwall/Adapty. The Meta pixel loads via GTM behind a CMP, so GTM counts as the pixel tell.
SIGNATURES = {
    "checkout": ["cdn.solidgate.com", "solid-form.js", "js.stripe.com", "stripe.com/v3",
                 "primer.io", "cdn.paddle.com", "braintreegateway", "recurly",
                 "paypalobjects", "fnlfx.com", "paywalls-"],
    "pixel":    ["connect.facebook.net", "fbevents.js", "fbq(",
                 "googletagmanager.com", "gtm.js"],
    "mmp":      ["cdn.branch.io", "branch-latest.min.js", ".app.link", "onelink.me",
                 "onelink.to", "appsflyer", "adjust.com", "app.adjust", "adj.st",
                 "go.link", "sng.link"],
    "store":    ["apps.apple.com", "itunes.apple.com", "play.google.com"],
}


def classify_funnel_intent(html: str) -> bool:
    """A page is a paid-acquisition subscription funnel when it pairs a WEB-checkout SDK
    with a Meta pixel (GTM counts). MMP/store presence is the install tell, handled separately."""
    found = detect_signatures(html)
    return "checkout" in found and "pixel" in found
```

- [ ] **Step 5: Run tests, confirm PASS**

Run: `PYTHONPATH=src .venv/bin/pytest tests/discovery/test_firehose_fingerprint.py -v`
Expected: PASS (all checkout/mmp/store/pixel tests green).

- [ ] **Step 6: Commit**

```bash
git add src/agf/discovery/firehose_fingerprint.py tests/discovery/test_firehose_fingerprint.py
git commit -m "fix(funnel): firehose signatures = web-checkout (Solidgate/Stripe/Primer) + mmp + store + GTM pixel"
```

---

### Task 2: Landing-page render helper

**Files:**
- Modify: `src/agf/enrich_full/funnel_capture.py` (add one function)
- Test: `tests/enrich_full/test_render_landing.py`

- [ ] **Step 1: Write the failing test** (a FakePage with no network)

```python
# tests/enrich_full/test_render_landing.py
from agf.enrich_full.funnel_capture import render_landing_html


class FakePage:
    def __init__(self, html):
        self._html = html
        self.url = "https://quiz.example.com/start"
        self.consented = False
        self.default_timeout = None

    def goto(self, url):
        self.url = url

    def set_default_timeout(self, ms):
        self.default_timeout = ms

    def query_selector(self, sel):
        # pretend a Cookiebot accept button exists; clicking it sets consented
        if sel == "#CybotCookiebotDialogBodyButtonAccept":
            page = self
            class _El:
                def click(self_inner):
                    page.consented = True
            return _El()
        return None

    def content(self):
        return self._html


def test_render_landing_dismisses_cmp_and_returns_html():
    page = FakePage("<html><body>paywall + fbq</body></html>")
    html = render_landing_html("https://quiz.example.com/start", page=page)
    assert html == "<html><body>paywall + fbq</body></html>"
    assert page.consented is True          # CMP was dismissed before reading content
    assert page.url == "https://quiz.example.com/start"
```

- [ ] **Step 2: Run it, confirm FAIL**

Run: `PYTHONPATH=src .venv/bin/pytest tests/enrich_full/test_render_landing.py -v`
Expected: FAIL — `ImportError: cannot import name 'render_landing_html'`.

- [ ] **Step 3: Add the function** to `src/agf/enrich_full/funnel_capture.py` (after `capture_funnel`; reuses `autoconsent_cmp` and `_playwright_page` already in the file):

```python
def render_landing_html(start_url, *, page=None, proxy=None, timeout_ms=30_000) -> str:
    """Render a funnel landing page (headless, CMP dismissed) and return its DOM HTML —
    the input to firehose_fingerprint.detect_signatures. v1 renders the landing page only
    (install/MMP/checkout tells live there or resolve from it); answer-aware quiz-stepping
    to the post-quiz paywall is v2. Pass `page` to drive an injected page (tests)."""
    def _go(p):
        p.goto(start_url)
        autoconsent_cmp(p)
        try:
            return p.content()
        except Exception:
            return ""
    if page is not None:
        return _go(page)
    with _playwright_page(proxy, timeout_ms) as p:
        return _go(p)
```

- [ ] **Step 4: Run test, confirm PASS**

Run: `PYTHONPATH=src .venv/bin/pytest tests/enrich_full/test_render_landing.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agf/enrich_full/funnel_capture.py tests/enrich_full/test_render_landing.py
git commit -m "feat(funnel): render_landing_html — headless DOM capture for fingerprinting"
```

---

### Task 3: DB — `mark_funnel_confirmed`

**Files:**
- Modify: `src/agf/db/funnel_candidates.py`
- Modify: `tests/db/test_funnel_candidates.py`

- [ ] **Step 1: Append the failing test** to `tests/db/test_funnel_candidates.py`:

```python
def test_mark_funnel_confirmed(pg_conn):
    cid, _ = fcand.upsert_funnel_candidate(pg_conn, {**ROW, "page_id": "FUNTESTC"})
    fcand.mark_funnel_confirmed(pg_conn, cid, kind="install", status="confirmed",
                                store="ios", store_id="555")
    row = pg_conn.execute(text(
        "SELECT kind, status, store, store_id, confirmed_at "
        "FROM discovery.funnel_candidates WHERE id=:i"), {"i": cid}).one()
    assert row.kind == "install" and row.status == "confirmed"
    assert row.store == "ios" and row.store_id == "555" and row.confirmed_at is not None
```

Also add `from sqlalchemy import text` to the test file's imports if not present.

- [ ] **Step 2: Run it, confirm FAIL**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf .venv/bin/pytest tests/db/test_funnel_candidates.py::test_mark_funnel_confirmed -v`
Expected: FAIL — `AttributeError: module 'agf.db.funnel_candidates' has no attribute 'mark_funnel_confirmed'`. (Must say FAIL, not SKIP — set AGF_TEST_DB_URL.)

- [ ] **Step 3: Add the function** to `src/agf/db/funnel_candidates.py`:

```python
def mark_funnel_confirmed(conn, candidate_id: int, *, kind: str, status: str,
                          store: str | None = None, store_id: str | None = None,
                          drop_reason: str | None = None) -> None:
    """Write the confirm-stage verdict onto a funnel candidate."""
    conn.execute(text("""
        UPDATE discovery.funnel_candidates
           SET kind=:k, status=:s, store=:st, store_id=:sid,
               drop_reason=:r, confirmed_at=now()
         WHERE id=:i
    """), {"k": kind, "s": status, "st": store, "sid": store_id,
           "r": drop_reason, "i": candidate_id})
```

- [ ] **Step 4: Run test, confirm PASS**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf .venv/bin/pytest tests/db/test_funnel_candidates.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/agf/db/funnel_candidates.py tests/db/test_funnel_candidates.py
git commit -m "feat(db): mark_funnel_confirmed — persist confirm-stage verdict"
```

---

### Task 4: `funnel_confirm.py` — classifier + orchestrator

**Files:**
- Create: `src/agf/discovery/funnel_confirm.py`
- Test: `tests/discovery/test_funnel_confirm.py`

- [ ] **Step 1: Write the failing test** (pure classifier + orchestrator with fakes — no network/DB beyond the migrated engine)

```python
# tests/discovery/test_funnel_confirm.py
from types import SimpleNamespace
from sqlalchemy import text

from agf.discovery.funnel_confirm import classify_funnel, run_funnel_confirm


def _resolved(kind, store_id=None):
    return SimpleNamespace(kind=kind, store_id=store_id)


# --- pure classifier ---------------------------------------------------------
def test_classify_install_from_resolved_store():
    k, s, store, sid, reason = classify_funnel("<html>x</html>", _resolved("ios", "999"))
    assert (k, s, store, sid) == ("install", "confirmed", "ios", "999")


def test_classify_install_from_mmp_in_html():
    k, s, *_ = classify_funnel('<a href="https://b.onelink.me/x">app</a>', _resolved("web"))
    assert (k, s) == ("install", "confirmed")


def test_classify_subscribe_from_checkout():
    k, s, *_ = classify_funnel('<script src="https://cdn.solidgate.com/js/solid-form.js">', _resolved("web"))
    assert (k, s) == ("subscribe", "confirmed")


def test_classify_unknown_kept_when_pixel_or_host():
    k, s, *_ = classify_funnel("fbq('init')", _resolved("web"))
    assert (k, s) == ("unknown", "confirmed")
    k2, s2, *_ = classify_funnel("<html>nothing</html>", _resolved("web"), funnel_host="quiz.")
    assert (k2, s2) == ("unknown", "confirmed")


def test_classify_drops_generic_web():
    k, s, store, sid, reason = classify_funnel("<html>just a giftcard shop</html>", _resolved("web"))
    assert (k, s, reason) == ("unknown", "dropped", "no_funnel_tech")


# --- orchestrator (fakes; uses the migrated DB only for the candidate rows) ---
def _ids(eng, prefix):
    with eng.connect() as c:
        return [r.id for r in c.execute(text(
            "SELECT id FROM discovery.funnel_candidates WHERE page_id LIKE :p"),
            {"p": prefix + "%"}).all()]


def test_run_funnel_confirm_labels_pool(_migrated_engine):
    eng = _migrated_engine
    from agf.db.funnel_candidates import upsert_funnel_candidate
    BASE = dict(page_name="C", page_like_count=1,
                cta_type="LEARN_MORE", utm_source=None, utm_medium=None,
                utm_campaign=None, utm_content=None, ad_start_date=None,
                source_keyword="take the quiz", technique="funnel_phrase")
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.funnel_candidates WHERE page_id LIKE 'FCONF%'"))
        upsert_funnel_candidate(conn, {**BASE, "page_id": "FCONF_SUB", "funnel_host": "quiz.",
                                       "landing_url": "https://fconf-sub.io/s",
                                       "landing_domain": "fconf-sub.io"})
        upsert_funnel_candidate(conn, {**BASE, "page_id": "FCONF_DROP", "funnel_host": None,
                                       "landing_url": "https://fconf-drop.io/s",
                                       "landing_domain": "fconf-drop.io"})
    RENDER = {"https://fconf-sub.io/s": '<script src="https://js.stripe.com/v3/"></script>fbq("init")',
              "https://fconf-drop.io/s": "<html>generic</html>"}
    def fake_resolve(url):
        return _resolved("web")
    def fake_render(url):
        return RENDER[url]
    try:
        stats = run_funnel_confirm(eng, resolve=fake_resolve, render=fake_render,
                                   ids=_ids(eng, "FCONF"))
        assert stats["confirmed"] >= 1 and stats["dropped"] >= 1
        rows = {r.page_id: (r.kind, r.status) for r in eng.connect().execute(text(
            "SELECT page_id, kind, status FROM discovery.funnel_candidates WHERE page_id LIKE 'FCONF%'")).all()}
        assert rows["FCONF_SUB"] == ("subscribe", "confirmed")
        assert rows["FCONF_DROP"][1] == "dropped"
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.funnel_candidates WHERE page_id LIKE 'FCONF%'"))
```

- [ ] **Step 2: Run it, confirm FAIL**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf .venv/bin/pytest tests/discovery/test_funnel_confirm.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agf.discovery.funnel_confirm'`.

- [ ] **Step 3: Write the implementation** `src/agf/discovery/funnel_confirm.py`:

```python
"""Stage 2 of the FUNNEL pipeline — confirm + label each new funnel candidate.
Renders the landing page, fingerprints the funnel tech (firehose_fingerprint), resolves the
deeplink (MMP->store), and writes a verdict: install | subscribe | unknown (confirmed) or
dropped (generic web, no funnel tech). v1 renders the landing page only.

The orchestrator is SYNC and takes injectable `resolve(url)->obj{kind,store_id}` and
`render(url)->html` callables — the runnable script wires the async Extractor.resolve_deeplink
(via asyncio.run) and the sync Playwright render. This keeps the orchestrator network-free and
testable with fakes."""
from sqlalchemy import text

from agf.db.engine import begin
from agf.db.funnel_candidates import mark_funnel_confirmed
from agf.discovery.firehose_fingerprint import detect_signatures


def classify_funnel(html, resolved, funnel_host=None):
    """(kind, status, store, store_id, drop_reason) for one funnel candidate.
    resolved: obj with .kind ('ios'|'android'|'web'|'none') and .store_id."""
    sigs = detect_signatures(html or "")
    if resolved is not None and resolved.kind in ("ios", "android") and resolved.store_id:
        return ("install", "confirmed", resolved.kind, resolved.store_id, None)
    if "store" in sigs or "mmp" in sigs:
        return ("install", "confirmed", None, None, None)   # store/app handoff present, unresolved
    if "checkout" in sigs:
        return ("subscribe", "confirmed", None, None, None)
    if "pixel" in sigs or funnel_host:
        return ("unknown", "confirmed", None, None, None)    # a paid funnel, payoff unclear
    return ("unknown", "dropped", None, None, "no_funnel_tech")


def _new_rows(conn, ids):
    rows = conn.execute(text(
        "SELECT id, landing_url, landing_domain, funnel_host FROM discovery.funnel_candidates "
        "WHERE status='new' ORDER BY first_seen_at, id")).all()
    if ids is not None:                      # filter in Python — driver-agnostic, avoids ANY/IN binding
        keep = set(ids)
        rows = [r for r in rows if r.id in keep]
    return rows


def run_funnel_confirm(engine, *, resolve, render, ids=None, limit=None) -> dict:
    """Confirm every 'new' funnel candidate (optionally restricted to `ids`). Commits per row.
    resolve(landing_url) -> obj{kind,store_id}; render(landing_url) -> html str."""
    with engine.connect() as conn:
        rows = _new_rows(conn, ids)
    if limit:
        rows = rows[:limit]
    stats = {"examined": len(rows), "confirmed": 0, "dropped": 0,
             "kinds": {"install": 0, "subscribe": 0, "unknown": 0}}
    for r in rows:
        try:
            resolved = resolve(r.landing_url)
        except Exception:
            resolved = None
        try:
            html = render(r.landing_url)
        except Exception:
            html = ""
        kind, status, store, store_id, reason = classify_funnel(html, resolved, r.funnel_host)
        with begin(engine) as conn:
            mark_funnel_confirmed(conn, r.id, kind=kind, status=status,
                                  store=store, store_id=store_id, drop_reason=reason)
        if status == "confirmed":
            stats["confirmed"] += 1
            stats["kinds"][kind] += 1
        else:
            stats["dropped"] += 1
    return stats
```

- [ ] **Step 4: Run tests, confirm PASS**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf .venv/bin/pytest tests/discovery/test_funnel_confirm.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/funnel_confirm.py tests/discovery/test_funnel_confirm.py
git commit -m "feat(funnel): funnel_confirm — classify install|subscribe|unknown|drop"
```

---

### Task 5: runnable entrypoint — live confirm over the pool

**Files:**
- Create: `scripts/run_funnel_confirm.py`

- [ ] **Step 1: Write the script**

```python
# scripts/run_funnel_confirm.py
"""Confirm + label the new funnel candidates (render landing -> fingerprint -> verdict).
Run (from repo root):
    set -a; . ops/.env; set +a   # AGF_PROXIES for render + resolve
    AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf AGF_CONFIRM_LIMIT=20 \
    PYTHONPATH=src .venv/bin/python scripts/run_funnel_confirm.py
Needs the sandbox OFF (real network). Wires the async Extractor.resolve_deeplink and the
sync Playwright render into the sync orchestrator."""
import asyncio
import os

from sqlalchemy import text

from agf.db.engine import get_engine
from agf.discovery.funnel_confirm import run_funnel_confirm
from agf.enrich_full.funnel_capture import render_landing_html
from agf.extract.facade import Extractor

PROXY = os.environ.get("AGF_PROXIES", "").split(",")[0] or None


def main() -> None:
    engine = get_engine()
    extractor = Extractor(search_delay=2.0, search_jitter=1.5)

    def resolve(url):
        return asyncio.run(extractor.resolve_deeplink(url)).intent  # -> AppIntent{kind,store_id}

    def render(url):
        return render_landing_html(url, proxy=PROXY)

    limit = int(os.environ.get("AGF_CONFIRM_LIMIT", "0")) or None
    stats = run_funnel_confirm(engine, resolve=resolve, render=render, limit=limit)
    print("confirm:", stats)
    with engine.connect() as conn:
        for r in conn.execute(text(
            "SELECT kind, status, landing_domain, store, store_id FROM discovery.funnel_candidates "
            "WHERE confirmed_at IS NOT NULL ORDER BY confirmed_at DESC LIMIT 30")).all():
            print(f"  {r.status:9s} {r.kind:9s} {r.landing_domain:34s} {r.store or ''} {r.store_id or ''}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-run (bounded; needs proxy + sandbox off)**

Run (sandbox disabled):
```bash
set -a; . ops/.env; set +a
AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf AGF_CONFIRM_LIMIT=15 PYTHONPATH=src .venv/bin/python scripts/run_funnel_confirm.py
```
Expected: a `confirm: {...}` line with non-zero `confirmed`, then labeled rows. Spot-check: a `mybody.health`/`quiz.orynapp.ai` row labels `subscribe` or `install`; a generic one drops.

- [ ] **Step 3: Commit**

```bash
git add scripts/run_funnel_confirm.py
git commit -m "feat(funnel): runnable confirm entrypoint over the candidate pool"
```

---

## Final verification

- [ ] `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf .venv/bin/pytest tests/discovery/test_funnel_confirm.py tests/discovery/test_firehose_fingerprint.py tests/enrich_full/test_render_landing.py tests/db/test_funnel_candidates.py -v` — all green.
- [ ] Full suite has no NEW failures vs. baseline (the 2 known pre-existing failures — `test_sweep_run_gate` shared-DB, `test_main` whois/network — are unrelated).

## Self-review notes (for the implementer)

- `classify_funnel` order matters: resolved-store → store/mmp-in-html → checkout → pixel/host → drop. An install that also has checkout should read as **install** (the store handoff wins) — the order encodes that.
- The orchestrator never imports Playwright or the Extractor — only the script does. Keep it that way (testability).
- `resolve()` in the script returns `.intent` (an `AppIntent{kind,store_id}`) so it matches what `classify_funnel` expects (`.kind`, `.store_id`).

## Out of scope (v2 / later)

- Answer-aware quiz-stepping to guarantee reaching the post-quiz paywall (v1 renders the landing page only).
- Screenshots to S3 for the funnel detail view.
- Funnel surfacing (scoring + a feed/API) — the next milestone after this.
- Wiring the funnel sweep+confirm into the continuous worker (only after surfacing exists).
