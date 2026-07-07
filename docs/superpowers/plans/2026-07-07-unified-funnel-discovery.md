# Unified Funnel Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Funnels fall out of the same keyword sweeps as apps (gated by `funnel_signal`), the dead funnel keyword flywheel is removed, and the 28 static funnel phrases get a weekly to-exhaustion deep sweep with logged pagination.

**Architecture:** Extract the web-funnel fold out of `funnel_sweep.classify_funnel_candidate` into a shared `funnel_classify.fold_web_funnel`; the app sweep offers its `non_app` / big-page rejects to it and upserts survivors into `discovery.funnel_candidates`. `run_funnel_flywheel_stage` and its env knobs are deleted. A weekly-gated deep pass reuses `run_funnel_sweep` with 100-page entries and a new page-stats sink threaded through `extractor.search`.

**Tech Stack:** Python 3.13, SQLAlchemy + psycopg (raw SQL repos), pytest. No new dependencies, no DB migration.

**Spec:** `docs/superpowers/specs/2026-07-07-unified-funnel-discovery-design.md`

## Global Constraints

- Test DB required for DB tests: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it` — MUST be exported or DB tests silently skip (a skip is not a pass).
- Full-suite standard: `AGF_TEST_DB_URL=… pytest -q` with **zero failed** before any task is called done. Run the bare command; never read the verdict through a pipe.
- Commit to `main` directly (no feature branches); stage explicit paths only (never `git add -A`).
- Constants come from `agf.scoring.vetoes`: `INCUMBENT_PAGE_LIKES_MAX = 10_000` (app), `FUNNEL_INCUMBENT_PAGE_LIKES_MAX = 5_000_000` (funnel). Never inline the numbers.
- No new sweep semantics: `pages = min(entry.max_pages, max_pages)` stays; the deep pass raises the ENTRY ceiling instead.
- Product nouns/copy rules don't apply (no UI strings in this plan).

---

### Task 1: Shared web-funnel fold (`fold_web_funnel`)

**Files:**
- Modify: `src/agf/discovery/funnel_classify.py` (header comment, imports, add `start_date_of` + `fold_web_funnel`)
- Modify: `src/agf/discovery/funnel_sweep.py` (delegate its web branch; drop its `_start_date`)
- Test: `tests/discovery/test_funnel_classify.py` (new fold tests)
- Test (regression, no edits expected): `tests/discovery/test_funnel_sweep.py`

**Interfaces:**
- Consumes: existing pure helpers in `funnel_classify` (`funnel_signal`, `landing_domain`, `matched_funnel_host`, `parse_utms`) and `agf.classify.classify_url`.
- Produces: `fold_web_funnel(ad, *, keyword: str, technique: str) -> tuple[str, dict | None]` with dispositions `'candidate' | 'app_link' | 'not_funnel' | 'no_link'` (row only for `'candidate'`), and `start_date_of(epoch) -> date | None`. Task 2 imports both names from `agf.discovery.funnel_classify`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/discovery/test_funnel_classify.py` (reuse the file's existing imports where present; `AdRecord` positional signature copied from `tests/discovery/test_funnel_sweep.py`):

```python
from agf.discovery.funnel_classify import fold_web_funnel
from agf.parse import AdRecord

_NOW = 1_749_000_000


def _fad(link, cta="LEARN_MORE", body="body", likes=500):
    return AdRecord("a1", "FOLDTEST1", "Fold Test", True, _NOW - 5 * 86_400, None, 1,
                    cta, "x", link, body, page_like_count=likes)


def test_fold_web_funnel_candidate_row():
    d, row = fold_web_funnel(_fad("https://quiz.example.com/start?utm_source=fb"),
                             keyword="hair loss app", technique="niche_term")
    assert d == "candidate"
    assert row["landing_domain"] == "quiz.example.com"
    assert row["funnel_host"] == "quiz." and row["utm_source"] == "fb"
    assert row["source_keyword"] == "hair loss app" and row["technique"] == "niche_term"
    assert row["body_text"] == "body"


def test_fold_web_funnel_store_link_is_app_link():
    d, row = fold_web_funnel(_fad("https://apps.apple.com/us/app/x/id12345"),
                             keyword="k", technique="niche_term")
    assert (d, row) == ("app_link", None)


def test_fold_web_funnel_plain_site_is_not_funnel():
    d, row = fold_web_funnel(_fad("https://plainsite.com/x", cta="NO", body="nothing"),
                             keyword="k", technique="niche_term")
    assert (d, row) == ("not_funnel", None)


def test_fold_web_funnel_phrase_layer_auto_passes_gate():
    # funnel_phrase layer bypasses funnel_signal — a plain site still folds
    d, row = fold_web_funnel(_fad("https://plainsite.com/x", cta="NO", body="nothing"),
                             keyword="take the quiz", technique="funnel_phrase")
    assert d == "candidate" and row["landing_domain"] == "plainsite.com"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/discovery/test_funnel_classify.py -v -k fold`
Expected: FAIL with `ImportError: cannot import name 'fold_web_funnel'`

- [ ] **Step 3: Implement `fold_web_funnel`**

In `src/agf/discovery/funnel_classify.py`:

1. Header comment: replace `The app pipeline never imports this.` with `Shared by BOTH pipelines since the 2026-07-07 unification (the app sweep folds its web rejects through fold_web_funnel).`
2. Add `from datetime import datetime, timezone` to the imports and extend line 23 to `from agf.classify import classify_url, unwrap_fb_shim`.
3. Append at the end of the module:

```python
def start_date_of(epoch) -> object | None:
    """Ad start epoch -> UTC date; None when missing or garbage."""
    if not epoch:
        return None
    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).date()
    except (TypeError, ValueError, OSError):
        return None


def fold_web_funnel(ad, *, keyword: str, technique: str) -> tuple[str, dict | None]:
    """Fold one SERP ad's WEB destination into a discovery.funnel_candidates row.

    Shared by the funnel sweep (whose funnel_phrase layer auto-passes funnel_signal)
    and the app sweep (where the gate really bites). The CALLER owns the no-link /
    social-host / incumbent-likes drops; this owns store-link routing, the signal
    gate, and the landing-domain check.
    Dispositions: 'candidate' (with row) | 'app_link' | 'not_funnel' | 'no_link'."""
    intent = classify_url(ad.link_url)
    if intent.kind in ("ios", "android"):
        return "app_link", None
    if not funnel_signal(ad, technique):
        return "not_funnel", None
    domain = landing_domain(ad.link_url)
    if not domain:
        return "no_link", None
    return "candidate", {
        "page_id": ad.page_id, "page_name": ad.page_name,
        "page_like_count": ad.page_like_count,
        "landing_url": ad.link_url, "landing_domain": domain,
        "funnel_host": matched_funnel_host(ad.link_url),
        "cta_type": ad.cta_type, "ad_start_date": start_date_of(ad.start_date),
        "source_keyword": keyword, "technique": technique,
        "body_text": ad.body_text,
        **parse_utms(ad.link_url),
    }
```

- [ ] **Step 4: Delegate the funnel sweep's web branch**

In `src/agf/discovery/funnel_sweep.py`:

1. Delete the module-level `_start_date` function and the `from datetime import datetime, timezone` import.
2. In the app-bridge branch of `classify_funnel_candidate`, replace `"ad_start_date": _start_date(ad.start_date),` with `"ad_start_date": fc.start_date_of(ad.start_date),`.
3. Replace the web branch (everything from `if not fc.funnel_signal(ad, technique):` to the end of the function — the `return "candidate", {...}` block) with:

```python
    return fc.fold_web_funnel(ad, keyword=keyword, technique=technique)
```

(`fc.fold_web_funnel` never returns `'app_link'` from here — store links were routed to the `'app'` branch above — and it reproduces the previous `'not_funnel'` / `'no_link'` / `'candidate'` dispositions and the identical row dict.)

- [ ] **Step 5: Run tests to verify they pass (incl. regression)**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it pytest tests/discovery/test_funnel_classify.py tests/discovery/test_funnel_sweep.py -v`
Expected: PASS, zero failures, zero skips of previously-running tests. The untouched `test_funnel_sweep.py` passing proves the delegation is behavior-preserving.

- [ ] **Step 6: Commit**

```bash
git add src/agf/discovery/funnel_classify.py src/agf/discovery/funnel_sweep.py tests/discovery/test_funnel_classify.py
git commit -m "refactor(discovery): extract fold_web_funnel — shared web-funnel classifier for both sweeps"
```

---

### Task 2: App sweep captures web funnels

**Files:**
- Modify: `src/agf/discovery/sweep.py` (docstring, imports, stats init, loop, write txn)
- Test: `tests/discovery/test_sweep.py`

**Interfaces:**
- Consumes: `fold_web_funnel`, from Task 1 (`from agf.discovery.funnel_classify import fold_web_funnel`); `upsert_funnel_candidate(conn, row) -> (id, was_new)` from `agf.db.funnel_candidates`; `FUNNEL_INCUMBENT_PAGE_LIKES_MAX` from `agf.scoring.vetoes`.
- Produces: `run_sweep` stats gain top-level `"funnel_new"`/`"funnel_seen"` counters and a `drops["funnel_captured"]` counter (persisted automatically via `close_sweep_run`'s drop_reasons JSONB). `classify_candidate` is UNCHANGED — the capture lives in the loop.

- [ ] **Step 1: Write the failing tests**

Append to `tests/discovery/test_sweep.py` (mirror the fixture/cleanup style of the existing `test_run_sweep_persists_and_counts` in the same file — same `_migrated_engine` usage, sentinel page_ids, and any teardown it does; use sentinel prefix `SWEEPFUN*`):

```python
def _web_ad(link, likes=500, page_id="SWEEPFUN1", cta="LEARN_MORE", body="body"):
    return AdRecord("a1", page_id, "Sweep Funnel", True, NOW - 5 * 86_400, None, 1,
                    cta, "x", link, body, page_like_count=likes)


def test_sweep_captures_web_funnel(_migrated_engine):
    ext = FakeExtractor(keyword_ads={"hair loss app": [
        ad("https://apps.apple.com/us/app/x/id111", page_id="SWEEPFUN_APP"),
        _web_ad("https://quiz.example.com/start", page_id="SWEEPFUN1"),
        _web_ad("https://plainsite.com/x", cta="NO", body="nothing", page_id="SWEEPFUN2"),
    ]})
    entry = TaxonomyEntry("hair loss app", "niche_term", "KEYWORD_UNORDERED", 2)
    stats = asyncio.run(run_sweep(_migrated_engine, ext, entries=[entry], kw_delay=0))
    assert stats["candidates_new"] == 1                    # the store ad
    assert stats["funnel_new"] == 1 and stats["funnel_seen"] == 0
    assert stats["drops"]["funnel_captured"] == 1
    assert stats["drops"]["non_app"] == 1                  # plainsite: no signal
    with _migrated_engine.connect() as conn:
        row = conn.execute(text(
            "SELECT source_keyword, technique, landing_domain "
            "FROM discovery.funnel_candidates WHERE page_id = 'SWEEPFUN1'")).one()
    assert row.source_keyword == "hair loss app"
    assert row.technique == "niche_term"
    assert row.landing_domain == "quiz.example.com"


def test_sweep_funnel_capture_tolerates_big_pages(_migrated_engine):
    # 200k likes: app pipeline vetoes (>10k) but the funnel ceiling is 5M -> captured.
    # 6M likes: over the funnel ceiling too -> stays an incumbent_likes drop.
    ext = FakeExtractor(keyword_ads={"meal plan app": [
        _web_ad("https://quiz.bigbrand.com/q", likes=200_000, page_id="SWEEPFUN3"),
        _web_ad("https://quiz.megabrand.com/q", likes=6_000_000, page_id="SWEEPFUN4"),
    ]})
    entry = TaxonomyEntry("meal plan app", "niche_term", "KEYWORD_UNORDERED", 2)
    stats = asyncio.run(run_sweep(_migrated_engine, ext, entries=[entry], kw_delay=0))
    assert stats["funnel_new"] == 1
    assert stats["drops"]["funnel_captured"] == 1
    assert stats["drops"]["incumbent_likes"] == 1
    with _migrated_engine.connect() as conn:
        got = conn.execute(text(
            "SELECT page_id FROM discovery.funnel_candidates "
            "WHERE page_id LIKE 'SWEEPFUN%' AND page_id IN ('SWEEPFUN3','SWEEPFUN4')")).all()
    assert [r.page_id for r in got] == ["SWEEPFUN3"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it pytest tests/discovery/test_sweep.py -v -k funnel`
Expected: FAIL — `KeyError: 'funnel_new'` (or funnel_candidates row missing).

- [ ] **Step 3: Implement the capture in `run_sweep`**

In `src/agf/discovery/sweep.py`:

1. Docstring line `-> non-app destination (funnels are v2) ->` becomes `-> non-app destination (web funnels fold into discovery.funnel_candidates via fold_web_funnel since 2026-07-07) ->`.
2. Add imports (`INCUMBENT_PAGE_LIKES_MAX` is already imported — extend that line rather than duplicating it):

```python
from agf.db.funnel_candidates import upsert_funnel_candidate
from agf.discovery.funnel_classify import fold_web_funnel
from agf.scoring.vetoes import FUNNEL_INCUMBENT_PAGE_LIKES_MAX, INCUMBENT_PAGE_LIKES_MAX  # noqa: replaces the existing vetoes import line
```

3. Extend the stats init:

```python
    stats = {"keywords_swept": 0, "serp_requests": 0, "raw_ads": 0,
             "candidates_new": 0, "candidates_seen": 0, "errors": 0,
             "funnel_new": 0, "funnel_seen": 0,
             "drops": {"no_link": 0, "social": 0, "incumbent_likes": 0,
                       "non_app": 0, "no_store_id": 0, "funnel_captured": 0}}
```

4. Replace the per-ad fold loop (the `rows, seen_keys = [], set()` block through the end of the `for ad in ads:` loop) with:

```python
        rows, seen_keys = [], set()
        funnel_rows, funnel_keys = [], set()
        for ad in ads:
            disposition, row = classify_candidate(ad, keyword=entry.keyword,
                                                  technique=entry.layer)
            if disposition == "candidate":
                key = (row["page_id"], row["store"], row["store_id"])
                if key in seen_keys:                  # same candidate, several ads, one SERP
                    continue
                seen_keys.add(key)
                rows.append(row)
                continue
            if disposition in ("non_app", "incumbent_likes"):
                # The app pipeline rejected this ad, but its destination may be a web
                # funnel. Funnels deliberately tolerate much bigger pages than apps
                # (a huge page can run a small new funnel), so only the funnel
                # ceiling applies on this branch.
                likes = ad.page_like_count
                if likes is None or likes <= FUNNEL_INCUMBENT_PAGE_LIKES_MAX:
                    fdisp, frow = fold_web_funnel(ad, keyword=entry.keyword,
                                                  technique=entry.layer)
                    if fdisp == "candidate":
                        stats["drops"]["funnel_captured"] += 1
                        fkey = (frow["page_id"], frow["landing_domain"])
                        if fkey not in funnel_keys:
                            funnel_keys.add(fkey)
                            funnel_rows.append(frow)
                        continue
            stats["drops"][disposition] += 1
```

5. After the existing `if rows:` transaction block, add the funnel write (its own short txn, mirroring `run_funnel_sweep`'s two-txn shape):

```python
        if funnel_rows:                               # second short txn: the funnel pool
            with begin(engine) as conn:
                for frow in funnel_rows:
                    _, was_new = upsert_funnel_candidate(conn, frow)
                    stats["funnel_new" if was_new else "funnel_seen"] += 1
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it pytest tests/discovery/test_sweep.py -v`
Expected: PASS — all pre-existing sweep tests still green (a `non_app` web ad without funnel signal still lands in `drops["non_app"]`). If an existing test asserts the WHOLE stats/drops dict by equality, extend its expected dict with the new keys (`funnel_new`/`funnel_seen`/`funnel_captured` at 0) — do not weaken the assert.

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/sweep.py tests/discovery/test_sweep.py
git commit -m "feat(discovery): app sweep folds web rejects into the funnel pool (funnel_signal-gated)"
```

---

### Task 3: Remove the funnel keyword flywheel

**Files:**
- Modify: `src/agf/workers/main.py` (delete `run_funnel_flywheel_stage`, its params/env/docstring rows)
- Modify: `src/agf/discovery/keyword_mining.py` (delete `fetch_funnel_copy`)
- Delete: `tests/discovery/test_funnel_flywheel.py`
- Test (adjust if they reference flywheel kwargs/keys): any test importing `run_funnel_stage` — find with `grep -rn "run_funnel_stage\|funnel_flywheel\|fetch_funnel_copy" tests/`

**Interfaces:**
- Consumes: nothing new.
- Produces: `run_funnel_stage(engine, extractor, *, max_pages=8, kw_delay=3.0, confirm_limit=50, resolve=None, probe=None) -> dict` returning `{"sweep": …, "confirm": …}` (no `"flywheel"` key). Task 5 modifies this same signature further — Task 3 must land first.

- [ ] **Step 1: Delete the flywheel**

In `src/agf/workers/main.py`:
1. Delete the whole `run_funnel_flywheel_stage` function (currently lines ~272–314).
2. In `run_funnel_stage`: remove the `flywheel=True, flywheel_pages=4, flywheel_max_trials=30, flywheel_min_advertisers=2` params, the `fly = None / if flywheel: …` block, and the word `(+flywheel)` + the `Flywheel: …` sentence from its docstring; return `{"sweep": sweep, "confirm": confirm}`.
3. In `run_once`'s signature defaults (the line containing `funnel_daily=True, funnel_pages=8, funnel_confirm_limit=50,`): remove `funnel_flywheel`, `funnel_flywheel_pages`, `funnel_flywheel_max_trials`, `funnel_flywheel_min_advertisers` wherever they appear in the signature.
4. At the `run_funnel_stage(...)` call site (~line 593): remove the four `flywheel*` kwargs.
5. In the env-parsing block (~lines 964–967): delete the `funnel_flywheel*=_env_int("AGF_FUNNEL_FLYWHEEL_…")` lines (and any `AGF_FUNNEL_FLYWHEEL`/`funnel_flywheel` boolean flag line found by grep).
6. In the module docstring's env table: delete every `AGF_FUNNEL_FLYWHEEL_*` row.
7. Remove `fetch_funnel_copy` from the `agf.discovery.keyword_mining` import line; remove the `FUNNEL_PHRASES` import IF grep shows the flywheel was its only consumer in this module.
8. Verify: `grep -n "flywheel" src/agf/workers/main.py` shows ONLY app-flywheel (`run_flywheel_stage`, `flywheel_due`, `AGF_FLYWHEEL_*`) references.

In `src/agf/discovery/keyword_mining.py`: delete `fetch_funnel_copy` (grep first — if any non-test consumer besides `workers/main.py` exists, leave it and note in the commit message; expected: none).

- [ ] **Step 2: Delete the flywheel test file**

```bash
git rm tests/discovery/test_funnel_flywheel.py
```

Then `grep -rn "funnel_flywheel\|fetch_funnel_copy\|run_funnel_flywheel_stage" tests/ src/` — fix any remaining references (e.g. a worker test passing `flywheel=False` to `run_funnel_stage`: drop the kwarg).

- [ ] **Step 3: Run the affected suites**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it pytest tests/discovery tests/workers -q`
Expected: PASS, zero failed. (`kind='funnel'` rows in prod stay as inert history — `live_keywords` returns nothing for them; no migration.)

- [ ] **Step 4: Commit**

```bash
git add -u src/agf/workers/main.py src/agf/discovery/keyword_mining.py tests/
git commit -m "feat(discovery): remove the funnel keyword flywheel — 100% of its mints self-retired (ad-copy n-grams = boilerplate); unified capture replaces it"
```

---

### Task 4: Page-count stats from `extractor.search`

**Files:**
- Modify: `src/agf/extract/search.py` (`search_ads` gains `stats: dict | None = None`)
- Modify: `src/agf/extract/facade.py` (`search` gains and forwards `stats`)
- Modify: `src/agf/extract/fake.py` and `tests/discovery/conftest.py` (both `FakeExtractor.search` accept `stats=None`)
- Test: `tests/extract/test_search.py`

**Interfaces:**
- Consumes: existing `search_ads` loop (`for i in range(max_pages)` with `has_next` early-exit).
- Produces: after any `search_ads`/facade-`search` call made with `stats={}`, the dict holds `{"pages": <int pages actually fetched>, "has_next": <bool more pages existed>}`. `has_next=True` with `pages == max_pages` is the "ceiling hit" signal Task 5 consumes.

- [ ] **Step 1: Write the failing tests**

Append to `tests/extract/test_search.py`:

```python
def test_search_ads_reports_page_stats():
    session = FakeSession(responses={
        ("POST", "/api/graphql/"): [
            FakeResponse(200, "for (;;);" + PAGE1),
            FakeResponse(200, PAGE2),
        ],
    })
    st = {}
    search_ads(session, TOKENS, query="x", countries=["US"], max_pages=5, stats=st)
    assert st == {"pages": 2, "has_next": False}


def test_search_ads_stats_flags_ceiling_hit():
    # PAGE1 carries a cursor + has_next=true; max_pages=1 stops at the ceiling
    session = FakeSession(responses={
        ("POST", "/api/graphql/"): [FakeResponse(200, "for (;;);" + PAGE1)],
    })
    st = {}
    search_ads(session, TOKENS, query="x", countries=["US"], max_pages=1, stats=st)
    assert st["pages"] == 1 and st["has_next"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/extract/test_search.py -v -k stats`
Expected: FAIL with `TypeError: search_ads() got an unexpected keyword argument 'stats'`

- [ ] **Step 3: Implement the stats sink**

In `src/agf/extract/search.py`, `search_ads`:
1. Add `stats: dict | None = None` to the signature (after `sleep`).
2. Track pages: initialize `pages_fetched = 0` before the loop; set `pages_fetched = i + 1` at the top of each iteration's successful parse (right after `records, cursor, has_next = parse_search_response(parsed)`).
3. Just before `return out, cursor, has_next`, add:

```python
    if stats is not None:
        stats["pages"] = pages_fetched
        stats["has_next"] = bool(has_next)
```

In `src/agf/extract/facade.py`, `search`: add `stats: dict | None = None` to the signature and pass `stats=stats` in the `search_ads(...)` call inside `attempt()` (retried attempts overwrite the dict; the surviving attempt's numbers win).

In `src/agf/extract/fake.py` and `tests/discovery/conftest.py`, both `FakeExtractor.search`: add `stats=None` to the signature and, first thing in the body:

```python
        if stats is not None:
            stats.update(pages=1, has_next=False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it pytest tests/extract tests/discovery -q`
Expected: PASS, zero failed.

- [ ] **Step 5: Commit**

```bash
git add src/agf/extract/search.py src/agf/extract/facade.py src/agf/extract/fake.py tests/discovery/conftest.py tests/extract/test_search.py
git commit -m "feat(extract): optional stats sink on search — pages fetched + has_next (ceiling-hit signal for the deep funnel sweep)"
```

---

### Task 5: Weekly deep sweep of the static funnel phrases

**Files:**
- Modify: `src/agf/discovery/funnel_sweep.py` (`run_funnel_sweep` gains `heartbeat`/`track_pages`; add `deep_funnel_entries`)
- Modify: `src/agf/db/funnel_candidates.py` (add `funnel_deep_ran_within_days`)
- Modify: `src/agf/workers/main.py` (`run_funnel_stage` deep wiring, `run_once` gate + env, docstring env rows)
- Modify: `ops/prod/docker-compose.yml` (env passthrough, worker service)
- Test: `tests/discovery/test_funnel_sweep.py`, `tests/db/test_funnel_candidates.py` (or the file that already tests `funnel_sweep_ran_within` — find with `grep -rln funnel_sweep_ran_within tests/`)

**Interfaces:**
- Consumes: `stats` sink from Task 4; `run_funnel_stage` shape from Task 3; `TaxonomyEntry(keyword, layer, search_type, max_pages)` and `all_entries(layer="funnel_phrase")` from `agf.discovery.taxonomy`.
- Produces:
  - `run_funnel_sweep(…, heartbeat=None, track_pages=False)` — beats per keyword; with `track_pages` the returned stats carry `"page_log": [{"keyword", "pages", "ads", "ceiling_hit"}, …]`.
  - `deep_funnel_entries(deep_pages: int) -> list[TaxonomyEntry]` (funnel_sweep.py).
  - `funnel_deep_ran_within_days(conn, days: int) -> bool` (db/funnel_candidates.py).
  - `run_funnel_stage(…, deep=False, deep_pages=100, …)` returning `{"sweep", "deep", "confirm"}` (`"deep"` is `None` when not run — the gate query checks `jsonb_typeof(stats->'deep') = 'object'` precisely because a skipped run stores JSON null).
  - Env knobs: `AGF_FUNNEL_DEEP_PAGES` (default 100), `AGF_FUNNEL_DEEP_EVERY_DAYS` (default 7, `0` disables).

- [ ] **Step 1: Write the failing tests**

Append to `tests/discovery/test_funnel_sweep.py`:

```python
from agf.discovery.funnel_sweep import deep_funnel_entries


def test_run_funnel_sweep_heartbeat_and_page_log(_migrated_engine):
    beats = []
    ext = FakeExtractor(keyword_ads={"take the quiz": [
        ad("https://quiz.example.com/s", page_id="FUNDEEP1")]})
    entry = TaxonomyEntry("take the quiz", "funnel_phrase", "KEYWORD_EXACT_PHRASE", 100)
    stats = asyncio.run(run_funnel_sweep(
        _migrated_engine, ext, entries=[entry], max_pages=100, kw_delay=0,
        heartbeat=lambda: beats.append(1), track_pages=True))
    assert beats == [1]                                   # one beat per keyword
    assert stats["page_log"] == [
        {"keyword": "take the quiz", "pages": 1, "ads": 1, "ceiling_hit": False}]


def test_deep_funnel_entries_raise_the_entry_ceiling():
    entries = deep_funnel_entries(100)
    assert len(entries) == 28
    assert all(e.layer == "funnel_phrase" and e.max_pages == 100 for e in entries)
    assert all(e.search_type == "KEYWORD_EXACT_PHRASE" for e in entries)
```

Append to the file that tests `funnel_sweep_ran_within` (import `funnel_deep_ran_within_days`, `open_funnel_sweep_run`, `close_funnel_sweep_run` from `agf.db.funnel_candidates`):

```python
def test_funnel_deep_ran_within_days(pg_conn):
    assert funnel_deep_ran_within_days(pg_conn, 7) is False
    rid = open_funnel_sweep_run(pg_conn)
    close_funnel_sweep_run(pg_conn, rid, stats={"sweep": {}, "deep": None, "confirm": {}})
    assert funnel_deep_ran_within_days(pg_conn, 7) is False      # JSON null != deep ran
    rid2 = open_funnel_sweep_run(pg_conn)
    close_funnel_sweep_run(pg_conn, rid2,
                           stats={"sweep": {}, "deep": {"page_log": []}, "confirm": {}})
    assert funnel_deep_ran_within_days(pg_conn, 7) is True
```

(Use the same conn/rollback fixture name that file already uses for `funnel_sweep_ran_within` — `pg_conn` shown here; adapt to the file's actual fixture.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it pytest tests/discovery/test_funnel_sweep.py tests/db -q`
Expected: FAIL — `ImportError: cannot import name 'deep_funnel_entries'` / `funnel_deep_ran_within_days`.

- [ ] **Step 3: Implement sweep-side support**

In `src/agf/discovery/funnel_sweep.py`:

1. Import `TaxonomyEntry`: extend the taxonomy import to `from agf.discovery.taxonomy import TaxonomyEntry, all_entries`.
2. New signature + loop hooks:

```python
async def run_funnel_sweep(engine, extractor, *, entries=None, countries=("US",),
                           max_pages: int = 3, kw_delay: float = 3.0,
                           sleep=time.sleep, heartbeat=None,
                           track_pages: bool = False) -> dict:
```

   - After the stats dict init: `if track_pages: stats["page_log"] = []`.
   - The search call becomes:

```python
        page_stat = {} if track_pages else None
        kwargs = {"stats": page_stat} if track_pages else {}
        try:
            ads = await extractor.search(query=entry.keyword, countries=list(countries),
                                         search_type=entry.search_type, max_pages=pages,
                                         **kwargs)
```

   - After `stats["raw_ads"] += len(ads)`:

```python
        if track_pages:
            stats["page_log"].append({
                "keyword": entry.keyword, "pages": page_stat.get("pages"),
                "ads": len(ads), "ceiling_hit": bool(page_stat.get("has_next"))})
```

   - Before the trailing `sleep(kw_delay)` at the loop end:

```python
        if heartbeat is not None:
            heartbeat()        # deep passes are long; prove liveness per phrase
```

3. Add at module end:

```python
def deep_funnel_entries(deep_pages: int) -> list[TaxonomyEntry]:
    """The static funnel phrases with the per-entry page ceiling raised to deep_pages.
    The taxonomy bakes 8 pages into the funnel_phrase layer and the sweep takes
    min(entry.max_pages, max_pages) — so a to-exhaustion pass must raise the ENTRY
    ceiling, not just the call's max_pages."""
    return [TaxonomyEntry(e.keyword, e.layer, e.search_type, deep_pages)
            for e in all_entries(layer="funnel_phrase")]
```

In `src/agf/db/funnel_candidates.py`, after `funnel_sweep_ran_within`:

```python
def funnel_deep_ran_within_days(conn, days: int) -> bool:
    """True when a FINISHED funnel run in the last `days` days carried a deep-sweep
    result. A run that skipped deep stores JSON null at stats->'deep', so the check
    is jsonb_typeof = 'object', not IS NOT NULL."""
    return bool(conn.execute(text(
        "SELECT EXISTS(SELECT 1 FROM discovery.funnel_sweep_runs "
        "WHERE finished_at IS NOT NULL "
        "AND jsonb_typeof(stats->'deep') = 'object' "
        "AND finished_at > now() - make_interval(days => :d))"),
        {"d": days}).scalar())
```

- [ ] **Step 4: Wire the worker**

In `src/agf/workers/main.py`:

1. `run_funnel_stage` (post-Task-3 shape) becomes:

```python
def run_funnel_stage(engine, extractor, *, max_pages=8, kw_delay=3.0,
                     confirm_limit=50, deep=False, deep_pages=100,
                     resolve=None, probe=None) -> dict:
```

   Between the daily sweep and confirm:

```python
    deep_stats = None
    if deep:
        # Weekly to-exhaustion pass over the 28 static phrases. deep_pages is a
        # circuit-breaker, not a target — search early-exits when Meta's pagination
        # runs out. No silent caps: every ceiling hit is logged.
        deep_stats = asyncio.run(run_funnel_sweep(
            engine, extractor, entries=deep_funnel_entries(deep_pages),
            max_pages=deep_pages, kw_delay=kw_delay,
            heartbeat=_touch_heartbeat, track_pages=True))
        for row in deep_stats["page_log"]:
            if row["ceiling_hit"]:
                print(f"[worker] funnel deep sweep: {row['keyword']!r} hit the "
                      f"{deep_pages}-page ceiling ({row['ads']} ads) — more pages exist",
                      file=sys.stderr, flush=True)
        _touch_heartbeat()
```

   Return `{"sweep": sweep, "deep": deep_stats, "confirm": confirm}`. Import `deep_funnel_entries` alongside `run_funnel_sweep`. Update the docstring: add a `Deep:` sentence describing the weekly pass.
2. `run_once`: add `funnel_deep_pages=100, funnel_deep_every_days=7` to the signature (next to `funnel_pages=8`). At the funnel call site, compute the gate and pass it:

```python
        if f_due:
            with engine.connect() as conn:
                deep_due = (funnel_deep_every_days > 0 and not
                            funnel_deep_ran_within_days(conn, funnel_deep_every_days))
            ...
                funnel = run_funnel_stage(engine, extractor, max_pages=funnel_pages,
                                          kw_delay=kw_delay,
                                          confirm_limit=funnel_confirm_limit,
                                          deep=deep_due, deep_pages=funnel_deep_pages)
```

   Import `funnel_deep_ran_within_days` from `agf.db.funnel_candidates` (same import line as `open_funnel_sweep_run`).
3. Env parsing block: add

```python
        funnel_deep_pages=_env_int("AGF_FUNNEL_DEEP_PAGES", 100),
        funnel_deep_every_days=_env_int("AGF_FUNNEL_DEEP_EVERY_DAYS", 7),
```

4. Module docstring env table: add rows `AGF_FUNNEL_DEEP_PAGES   deep-sweep page ceiling per phrase (default 100)` and `AGF_FUNNEL_DEEP_EVERY_DAYS   deep sweep cadence, 0 disables (default 7)`.

In `ops/prod/docker-compose.yml` (worker `environment:`, next to the other AGF_ passthroughs):

```yaml
      AGF_FUNNEL_DEEP_PAGES: ${AGF_FUNNEL_DEEP_PAGES:-100}
      AGF_FUNNEL_DEEP_EVERY_DAYS: ${AGF_FUNNEL_DEEP_EVERY_DAYS:-7}
```

(NOTE for the deployer: the VM's live compose at `/opt/agf/docker-compose.yml` has drifted from the repo copy — apply the same two lines there by hand at deploy time; do NOT copy the repo file over it.)

- [ ] **Step 5: Add a stage-level test**

Append to `tests/discovery/test_funnel_sweep.py` (run_funnel_stage lives in the worker; a stage test proves the deep wiring + run-row stats shape end to end):

```python
def test_run_funnel_stage_deep_wiring(_migrated_engine, monkeypatch):
    import agf.workers.main as wm
    # stub confirm: the shared test DB may hold other tests' 'new' funnel rows, and this
    # test is about the DEEP wiring, not the (already-tested) confirm stage
    monkeypatch.setattr(wm, "run_funnel_confirm",
                        lambda engine, *, resolve, probe, limit: {"examined": 0})
    ext = FakeExtractor()                                  # no ads anywhere
    out = wm.run_funnel_stage(_migrated_engine, ext, kw_delay=0, deep=True, deep_pages=100,
                              resolve=lambda url: None, probe=lambda url: None)
    assert out["deep"] is not None
    assert len(out["deep"]["page_log"]) == 28              # every static phrase logged
    out2 = wm.run_funnel_stage(_migrated_engine, ext, kw_delay=0, deep=False,
                               resolve=lambda url: None, probe=lambda url: None)
    assert out2["deep"] is None
```

(Match the monkeypatched stub's signature to the real `run_funnel_confirm(engine, resolve=…, probe=…, limit=…)` call shape in `run_funnel_stage` — adjust keyword-vs-positional to the actual call.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it pytest tests/discovery tests/db tests/workers -q`
Expected: PASS, zero failed.

- [ ] **Step 7: Commit**

```bash
git add src/agf/discovery/funnel_sweep.py src/agf/db/funnel_candidates.py src/agf/workers/main.py ops/prod/docker-compose.yml tests/discovery/test_funnel_sweep.py tests/db
git commit -m "feat(discovery): weekly to-exhaustion deep sweep of the static funnel phrases (logged pagination, no silent caps)"
```

---

### Task 6: Full-suite gate, deploy, live e2e

**Files:**
- No source changes expected (fixes only if the gate finds breakage).

- [ ] **Step 1: Full suite, bare**

Run exactly: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it pytest -q`
Read the full output directly (no pipes). Expected: **0 failed**; skip count at the suite's known baseline. Fix anything red before proceeding.

- [ ] **Step 2: Build + roll the worker image**

Build from a clean worktree (never the possibly-mixed working tree), using the existing pipeline:

```bash
git worktree add /tmp/agf-build HEAD
(cd /tmp/agf-build && bash ops/prod/02_build_image.sh)
git worktree remove /tmp/agf-build
```

Then on the VM (`gcloud compute ssh agf-worker --zone europe-west1-b --project peon-489013`): add the two `AGF_FUNNEL_DEEP_*` passthrough lines to the worker service in `/opt/agf/docker-compose.yml` (hand-edit — the VM compose has drifted; do not overwrite), then `cd /opt/agf && sudo docker compose pull worker cron && sudo docker compose up -d worker cron`. The in-flight funnel redrain runs in its own `agf-redrain` container and survives the worker roll.

- [ ] **Step 3: Live e2e (the real done-gate)**

One-off inside the deployed worker container (base64-pipe a script via ssh + docker exec, the proven pattern):

1. **App-sweep capture live:** run `run_sweep` over ~10 real taxonomy niche keywords (e.g. the Health & Fitness entries) with `max_pages=2` against the live extractor. Assert cumulative `drops["funnel_captured"] > 0` and `funnel_new + funnel_seen > 0`, and print 5 newest `discovery.funnel_candidates` rows where `technique <> 'funnel_phrase'` — eyeball that the landing domains are plausibly funnels (quiz/plan pages, not brand homepages). Judge against the spec, don't just count.
2. **Deep sweep live (bounded):** run `run_funnel_sweep(entries=deep_funnel_entries(15), max_pages=15, track_pages=True)` for TWO phrases only (slice the list) and print the `page_log` — expect real page counts > 8 for at least one hot phrase, `ceiling_hit` flags correct.
3. Confirm the worker's next cycle is healthy: `docker logs` shows the funnel stage summary with the new stats shape and no stage failure.

Report honestly: which legs ran live, what the numbers were, and that the first FULL deep pass (100 pages, all 28 phrases) happens on the next daily funnel run — check its `page_log` and the confirm-queue depth the following day.

- [ ] **Step 4: Update memory + close out**

Update the project memory (funnel unification shipped: what changed, the new env knobs, the "watch confirm-queue depth after first deep pass" follow-up) and commit any doc/memory files touched.
