# Keyword-minting external frame — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dead `release_mint` page-matching stage with a keyword-minting external frame that mints head-term search keywords from App Store new-release listings, keeps every in-scope category above a keyword floor, biases minting toward under-covered categories, and rides the existing flywheel sweep + lifecycle.

**Architecture:** One new pure+read module `discovery/coverage.py` (category normalization, coverage read, budget math) and one new stage module `discovery/frame_mint.py` (replaces `release_mint.py`). Minted keywords enter the existing `discovery.dynamic_keywords` table as `kind='app'` trials, so the existing flywheel sweep discovers/promotes/retires them with no new sweep path. The worker's daily-gated stage 1.93 swaps its call body; the `release_mint_runs` table and `AGF_RELEASE_MINT` env keep their names (no migration).

**Tech Stack:** Python 3.13, SQLAlchemy Core (`text()`), psycopg; pytest with a real Postgres test DB; existing `mine_phrases` (n-gram miner), `dynamic_keywords` DB helpers, iTunes RSS + Lookup fetchers.

## Global Constraints

- **Taste-blind discovery:** no reward/steering may read user ratings, likes, or the preference model. Minting is steered only by category coverage + new-unique yield. (Standing product rule.)
- **US-only, apps-only.** New-release fetch is `country="us"`.
- **Zero Meta traffic in minting:** term extraction uses only Apple RSS + Lookup (free). Meta spend happens later, in the existing flywheel sweep of the minted keywords.
- **Test DB:** `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it`. Full suite must end at **0 failed** (skips are not passes).
- **Dev subagents run on Opus.**
- **Commit to `main` directly** (solo repo, no feature branches). Stage explicit paths only — never `git add -A`.
- **Product copy rule** (if any stat string surfaces): plain, no marketing words.

---

## File Structure

- **Create** `src/agf/discovery/coverage.py` — `CATEGORY_ALIASES`, `canonical_category()`, `CategoryCoverage`, `category_coverage()`, `mint_budget()`, `under_floor_categories()`.
- **Create** `src/agf/discovery/frame_mint.py` — `ensure_category_frame()`, `mint_keywords_from_releases()`. (Supersedes `release_mint.py`.)
- **Delete** `src/agf/discovery/release_mint.py` (its `OUT_OF_SCOPE_GENRES` moves to `coverage.py`; `expand_releases_via_typeahead` and `app_graph_seed_apps` are dropped — the page-matching mechanism and the deferred app-graph lane).
- **Modify** `src/agf/workers/main.py` — import + stage 1.93 call body + new env knobs + stats print.
- **Create** `tests/discovery/test_coverage.py`.
- **Rename/replace** `tests/discovery/test_release_mint.py` → `tests/discovery/test_frame_mint.py`.
- **Modify** `ops/prod/docker-compose.yml` — add the new env knobs (documented; code has defaults).

Reused unchanged: `discovery/keyword_mining.py` (`mine_phrases`), `db/dynamic_keywords.py` (`add_trial_keywords`, `tried_keywords`, `open_release_mint_run`, `close_release_mint_run`, `release_mint_ran_today`), `discovery/taxonomy.py` (`IN_SCOPE_CATEGORIES`, `NICHE_TERMS_BY_CATEGORY`), `enrich_full/appstore.py` (`fetch_itunes_new_releases`, `lookup_itunes_batch`), `enrich.py` (`AppMeta`).

---

### Task 1: Category normalization (`canonical_category`)

**Files:**
- Create: `src/agf/discovery/coverage.py`
- Test: `tests/discovery/test_coverage.py`

**Interfaces:**
- Consumes: `agf.discovery.taxonomy.IN_SCOPE_CATEGORIES` (frozenset of canonical names).
- Produces:
  - `OUT_OF_SCOPE_GENRES: frozenset[str]` = `{"Games", "Stickers"}`
  - `CATEGORY_ALIASES: dict[str, str]` (raw store genre → canonical)
  - `canonical_category(genre: str | None) -> str | None` (returns a member of `IN_SCOPE_CATEGORIES`, or `None` if out-of-scope/unmapped)

- [ ] **Step 1: Write the failing test**

```python
# tests/discovery/test_coverage.py
from agf.discovery.coverage import canonical_category
from agf.discovery.taxonomy import IN_SCOPE_CATEGORIES


def test_canonical_category_passthrough_and_aliases():
    # already-canonical names pass through
    assert canonical_category("Health & Fitness") == "Health & Fitness"
    assert canonical_category("Education") == "Education"
    # iOS Apple names fold to the taxonomy vocabulary
    assert canonical_category("Social Networking") == "Social"
    assert canonical_category("Graphics & Design") == "Art & Design"
    assert canonical_category("Book") == "Books"
    # Android Play names fold too
    assert canonical_category("Tools") == "Utilities"
    assert canonical_category("Photography") == "Photo & Video"
    assert canonical_category("Music & Audio") == "Music"
    assert canonical_category("Maps & Navigation") == "Navigation"
    assert canonical_category("Video Players & Editors") == "Video Players"
    assert canonical_category("Books & Reference") == "Books"
    assert canonical_category("Travel & Local") == "Travel"
    assert canonical_category("News & Magazines") == "News"
    # out-of-scope / unknown / empty -> None
    assert canonical_category("Games") is None
    assert canonical_category("Stickers") is None
    assert canonical_category("Kazoo Polishing") is None
    assert canonical_category(None) is None
    assert canonical_category("") is None
    # every alias target and every passthrough must be in-scope
    for raw in ("Social Networking", "Tools", "Photography"):
        assert canonical_category(raw) in IN_SCOPE_CATEGORIES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/python -m pytest tests/discovery/test_coverage.py::test_canonical_category_passthrough_and_aliases -v`
Expected: FAIL (ModuleNotFoundError: agf.discovery.coverage)

- [ ] **Step 3: Write minimal implementation**

```python
# src/agf/discovery/coverage.py
"""Category coverage of the discovery frame: normalize store genres to the taxonomy's
canonical vocabulary, read per-category kept-app counts + live keyword counts, and
allocate mint budget toward under-covered categories. Taste-blind (no ratings read)."""
from dataclasses import dataclass

from sqlalchemy import text

from agf.discovery.taxonomy import IN_SCOPE_CATEGORIES

# Genres we never mint into (Games are vetoed product-wide; Stickers aren't standalone apps).
OUT_OF_SCOPE_GENRES = frozenset({"Games", "Stickers"})

# Raw store genre (iOS primaryGenreName / Android Play category) -> taxonomy canonical.
# Grounded in the live genre distribution (2026-07-02). Names already equal to a member of
# IN_SCOPE_CATEGORIES need no entry — canonical_category passes them through.
CATEGORY_ALIASES: dict[str, str] = {
    # iOS Apple names
    "Social Networking": "Social",
    "Graphics & Design": "Art & Design",
    "Book": "Books",
    # Android Play names
    "Tools": "Utilities",
    "Photography": "Photo & Video",
    "Music & Audio": "Music",
    "Maps & Navigation": "Navigation",
    "Video Players & Editors": "Video Players",
    "Books & Reference": "Books",
    "Travel & Local": "Travel",
    "News & Magazines": "News",
    "Communication": "Communication",  # in-scope already; explicit for clarity
}


def canonical_category(genre: str | None) -> str | None:
    """Fold a raw store genre into the taxonomy's IN_SCOPE_CATEGORIES vocabulary.
    Returns None for out-of-scope, unmapped, or empty input."""
    if not genre:
        return None
    if genre in OUT_OF_SCOPE_GENRES:
        return None
    mapped = CATEGORY_ALIASES.get(genre, genre)
    return mapped if mapped in IN_SCOPE_CATEGORIES else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/python -m pytest tests/discovery/test_coverage.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/coverage.py tests/discovery/test_coverage.py
git commit -m "feat(discovery): canonical_category — normalize store genres to taxonomy vocab"
```

---

### Task 2: Budget math (`CategoryCoverage`, `mint_budget`, `under_floor_categories`)

**Files:**
- Modify: `src/agf/discovery/coverage.py`
- Test: `tests/discovery/test_coverage.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `@dataclass CategoryCoverage: category: str; found: int; live_keywords: int`
  - `mint_budget(coverage: dict[str, CategoryCoverage], *, K: int, total_slots: int) -> dict[str, int]` — allocates `total_slots` across categories with `deficit = max(0, K - found)`, proportional to deficit, every category with deficit>0 getting ≥1 slot; returns only categories granted ≥1 slot; sum ≤ `total_slots`.
  - `under_floor_categories(coverage: dict[str, CategoryCoverage], *, floor: int) -> list[str]` — categories whose `live_keywords < floor`, sorted ascending by `live_keywords` then name.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/discovery/test_coverage.py
from agf.discovery.coverage import (
    CategoryCoverage, mint_budget, under_floor_categories)


def _cov(rows):  # rows: list[(category, found, live_keywords)]
    return {c: CategoryCoverage(c, f, lk) for c, f, lk in rows}


def test_mint_budget_favors_deficit_and_floors_at_one():
    cov = _cov([
        ("Health & Fitness", 40, 9),  # over K -> deficit 0 -> no slots
        ("Dating", 0, 0),             # deficit 15
        ("Beauty", 10, 2),            # deficit 5
    ])
    budget = mint_budget(cov, K=15, total_slots=20)
    assert "Health & Fitness" not in budget         # no deficit, no slots
    assert budget["Dating"] > budget["Beauty"]      # bigger deficit -> more slots
    assert budget["Beauty"] >= 1                    # any deficit gets >=1
    assert sum(budget.values()) <= 20


def test_mint_budget_all_covered_returns_empty():
    cov = _cov([("Finance", 20, 8), ("Sports", 16, 6)])
    assert mint_budget(cov, K=15, total_slots=40) == {}


def test_under_floor_categories_sorted_by_scarcity():
    cov = _cov([("Dating", 0, 0), ("Beauty", 10, 2), ("Finance", 20, 8)])
    assert under_floor_categories(cov, floor=5) == ["Dating", "Beauty"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/python -m pytest tests/discovery/test_coverage.py -v`
Expected: FAIL (ImportError: cannot import name 'CategoryCoverage')

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/agf/discovery/coverage.py (after canonical_category)


@dataclass
class CategoryCoverage:
    category: str
    found: int          # distinct kept apps in this canonical category
    live_keywords: int  # trial+active dynamic keywords sourced to this category


def mint_budget(coverage: dict[str, "CategoryCoverage"], *, K: int,
                total_slots: int) -> dict[str, int]:
    """Allocate total_slots across categories proportional to their coverage deficit
    (max(0, K - found)). Every category with a positive deficit gets at least one slot;
    fully-covered categories get none. Largest-remainder rounding keeps the sum <= total."""
    deficits = {c: max(0, K - cov.found) for c, cov in coverage.items()}
    deficits = {c: d for c, d in deficits.items() if d > 0}
    if not deficits or total_slots <= 0:
        return {}
    total_deficit = sum(deficits.values())
    # Proportional share, floored to >=1 for any deficit, then trimmed to total_slots by
    # taking the largest deficits first (deterministic: -deficit, then name).
    raw = {c: max(1, (d * total_slots) // total_deficit) for c, d in deficits.items()}
    if sum(raw.values()) <= total_slots:
        return raw
    order = sorted(deficits, key=lambda c: (-deficits[c], c))
    out, spent = {}, 0
    for c in order:
        if spent >= total_slots:
            break
        take = min(raw[c], total_slots - spent)
        if take >= 1:
            out[c] = take
            spent += take
    return out


def under_floor_categories(coverage: dict[str, "CategoryCoverage"], *,
                           floor: int) -> list[str]:
    """Categories whose live keyword count is below the floor, scarcest first."""
    below = [cov for cov in coverage.values() if cov.live_keywords < floor]
    below.sort(key=lambda cov: (cov.live_keywords, cov.category))
    return [cov.category for cov in below]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/python -m pytest tests/discovery/test_coverage.py -v`
Expected: PASS (all Task 1 + Task 2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/coverage.py tests/discovery/test_coverage.py
git commit -m "feat(discovery): mint_budget + under_floor_categories (deficit-weighted)"
```

---

### Task 3: Coverage read (`category_coverage`)

**Files:**
- Modify: `src/agf/discovery/coverage.py`
- Test: `tests/discovery/test_coverage.py`

**Interfaces:**
- Consumes: `canonical_category`, `CategoryCoverage`, `IN_SCOPE_CATEGORIES`.
- Produces: `category_coverage(conn) -> dict[str, CategoryCoverage]` — one entry per in-scope category (found=0/live=0 when absent). `found` = distinct kept apps (`discovery.candidates` status in `('kept','enriched')` joined `public.apps` on `(store, store_id)`, genre→canonical, distinct app id). `live_keywords` = count of `discovery.dynamic_keywords` with `status IN ('trial','active')` and `source` like `new_release:%` or `frame:%`, category parsed from the source suffix.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/discovery/test_coverage.py
from sqlalchemy import text
from agf.discovery.coverage import category_coverage


def test_category_coverage_counts_kept_apps_and_live_keywords(db_engine):
    # db_engine: the repo's transactional test-DB fixture (rolled back after test).
    with db_engine.begin() as conn:
        # two kept iOS apps in Health & Fitness (one via alias-free name), one Android
        # "Tools" app that must fold into Utilities; one DROPPED candidate must NOT count.
        conn.execute(text(
            "INSERT INTO public.apps (store, store_id, name, genre) VALUES "
            "('ios','111','A','Health & Fitness'),"
            "('ios','222','B','Health & Fitness'),"
            "('android','333','C','Tools'),"
            "('ios','444','D','Health & Fitness')"))
        conn.execute(text(
            "INSERT INTO discovery.candidates (page_id, store, store_id, source_keyword,"
            " technique, status) VALUES "
            "('p1','ios','111','k','niche_term','kept'),"
            "('p2','ios','222','k','niche_term','enriched'),"
            "('p3','android','333','k','niche_term','kept'),"
            "('p4','ios','444','k','niche_term','new')"))  # 'new' -> not counted
        conn.execute(text(
            "INSERT INTO discovery.dynamic_keywords (keyword, source, kind, status) VALUES "
            "('hf one','new_release:Health & Fitness','app','trial'),"
            "('hf two','frame:Health & Fitness','app','active'),"
            "('util one','new_release:Utilities','app','trial'),"
            "('retired kw','new_release:Utilities','app','retired')"))  # retired excluded
        cov = category_coverage(conn)
    assert cov["Health & Fitness"].found == 2
    assert cov["Health & Fitness"].live_keywords == 2
    assert cov["Utilities"].found == 1          # Android "Tools" folded in
    assert cov["Utilities"].live_keywords == 1  # retired excluded
    assert cov["Dating"].found == 0 and cov["Dating"].live_keywords == 0
    assert set(cov) == set(__import__("agf.discovery.taxonomy", fromlist=["x"]).IN_SCOPE_CATEGORIES)
```

Note: confirm the test-DB fixture name in `conftest.py` (repo uses a transactional engine fixture); if it differs from `db_engine`, use the repo's actual fixture name.

- [ ] **Step 2: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/python -m pytest tests/discovery/test_coverage.py::test_category_coverage_counts_kept_apps_and_live_keywords -v`
Expected: FAIL (ImportError: cannot import name 'category_coverage')

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/agf/discovery/coverage.py

def category_coverage(conn) -> dict[str, CategoryCoverage]:
    """Per in-scope category: distinct kept apps found + live (trial/active) minted/frame
    keywords. Read-only. Categories with no data still appear (found=0, live=0)."""
    found: dict[str, int] = {c: 0 for c in IN_SCOPE_CATEGORIES}
    live: dict[str, int] = {c: 0 for c in IN_SCOPE_CATEGORIES}

    app_rows = conn.execute(text(
        "SELECT a.genre AS genre, count(DISTINCT a.id) AS n "
        "FROM discovery.candidates c "
        "JOIN public.apps a ON a.store = c.store AND a.store_id = c.store_id "
        "WHERE c.status IN ('kept','enriched') AND a.genre IS NOT NULL "
        "GROUP BY a.genre")).all()
    for r in app_rows:
        cat = canonical_category(r.genre)
        if cat is not None:
            found[cat] += r.n

    kw_rows = conn.execute(text(
        "SELECT source, count(*) AS n FROM discovery.dynamic_keywords "
        "WHERE status IN ('trial','active') "
        "AND (source LIKE 'new_release:%' OR source LIKE 'frame:%') "
        "GROUP BY source")).all()
    for r in kw_rows:
        cat = r.source.split(":", 1)[1] if ":" in r.source else None
        if cat in live:
            live[cat] += r.n

    return {c: CategoryCoverage(c, found[c], live[c]) for c in IN_SCOPE_CATEGORIES}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/python -m pytest tests/discovery/test_coverage.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/coverage.py tests/discovery/test_coverage.py
git commit -m "feat(discovery): category_coverage read (kept apps + live keywords per category)"
```

---

### Task 4: Frame backbone (`ensure_category_frame`)

**Files:**
- Create: `src/agf/discovery/frame_mint.py`
- Test: `tests/discovery/test_frame_mint.py`

**Interfaces:**
- Consumes: `category_coverage`, `under_floor_categories`, `CategoryCoverage` (coverage.py); `agf.discovery.taxonomy.NICHE_TERMS_BY_CATEGORY`; `agf.db.dynamic_keywords.add_trial_keywords`.
- Produces: `ensure_category_frame(conn, coverage: dict[str, CategoryCoverage], *, floor: int) -> int` — for each under-floor category, add its `NICHE_TERMS_BY_CATEGORY` seeds as `source=f"frame:{category}"`, `kind="app"` trials; returns total keywords newly added (existing keywords are no-ops via the table's ON CONFLICT).

- [ ] **Step 1: Write the failing test**

```python
# tests/discovery/test_frame_mint.py
from sqlalchemy import text
from agf.discovery.frame_mint import ensure_category_frame
from agf.discovery.coverage import CategoryCoverage


def test_ensure_category_frame_seeds_under_floor_only(db_engine):
    with db_engine.begin() as conn:
        coverage = {
            "Dating": CategoryCoverage("Dating", 0, 0),          # under floor -> seed
            "Health & Fitness": CategoryCoverage("Health & Fitness", 40, 9),  # >= floor
        }
        added = ensure_category_frame(conn, coverage, floor=5)
        assert added > 0
        dating = conn.execute(text(
            "SELECT count(*) FROM discovery.dynamic_keywords "
            "WHERE source = 'frame:Dating' AND kind='app'")).scalar()
        hf = conn.execute(text(
            "SELECT count(*) FROM discovery.dynamic_keywords "
            "WHERE source = 'frame:Health & Fitness'")).scalar()
        assert dating > 0      # under-floor category seeded
        assert hf == 0         # at-floor category untouched
        # idempotent: a second call adds nothing new
        assert ensure_category_frame(conn, coverage, floor=5) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/python -m pytest tests/discovery/test_frame_mint.py::test_ensure_category_frame_seeds_under_floor_only -v`
Expected: FAIL (ModuleNotFoundError: agf.discovery.frame_mint)

- [ ] **Step 3: Write minimal implementation**

```python
# src/agf/discovery/frame_mint.py
"""Layer-0 external-frame keyword minting. Replaces the old release_mint page-matching
mechanism (dead — see docs/research/2026-07-02-novel-discovery-algorithm.md §5b).

Two jobs, both taste-blind:
  1. ensure_category_frame — every in-scope category always holds a floor of live
     keywords (taxonomy seeds), the fixed anti-collapse backbone.
  2. mint_keywords_from_releases — mint head-term SEARCH keywords from App Store
     new-release listings, biased toward under-covered categories.
Minted keywords are dynamic_keywords trials (kind='app'); the existing flywheel sweep
discovers/promotes/retires them. Zero Meta traffic here (Apple RSS + Lookup only)."""
from agf.db.dynamic_keywords import add_trial_keywords
from agf.discovery.coverage import under_floor_categories
from agf.discovery.taxonomy import NICHE_TERMS_BY_CATEGORY


def ensure_category_frame(conn, coverage, *, floor: int) -> int:
    """Seed taxonomy keywords for every under-floor in-scope category so no category can
    silently fall out of the sweep. Idempotent (existing keywords no-op). Returns added."""
    added = 0
    for cat in under_floor_categories(coverage, floor=floor):
        seeds = NICHE_TERMS_BY_CATEGORY.get(cat)
        if not seeds:
            continue
        added += add_trial_keywords(conn, seeds, source=f"frame:{cat}", kind="app")
    return added
```

- [ ] **Step 4: Run test to verify it passes**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/python -m pytest tests/discovery/test_frame_mint.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/frame_mint.py tests/discovery/test_frame_mint.py
git commit -m "feat(discovery): ensure_category_frame — taxonomy backbone for under-covered categories"
```

---

### Task 5: The mint stage (`mint_keywords_from_releases`)

**Files:**
- Modify: `src/agf/discovery/frame_mint.py`
- Delete: `src/agf/discovery/release_mint.py`
- Test: `tests/discovery/test_frame_mint.py`

**Interfaces:**
- Consumes: `fetch_itunes_new_releases`, `lookup_itunes_batch` (`enrich_full/appstore.py`); `AppMeta` (`enrich.py`); `mine_phrases` (`keyword_mining.py`); `tried_keywords`, `add_trial_keywords` (`db/dynamic_keywords.py`); `category_coverage`, `mint_budget`, `canonical_category` (coverage.py); `ensure_category_frame` (Task 4).
- Produces: `mint_keywords_from_releases(engine, client, *, country="us", limit=200, K=15, floor=5, total_slots=40, min_advertisers=2, fetch=fetch_itunes_new_releases, lookup=lookup_itunes_batch) -> dict` returning stats `{"seeds", "in_scope", "looked_up", "minted", "minted_by_category", "frame_seeded", "coverage", "errors"}`. `engine` is the SQLAlchemy engine; `client` is an httpx client for Apple endpoints. `fetch`/`lookup` are injectable for tests.

**Note on `mine_phrases`:** signature is `mine_phrases(corpus, *, exclude, min_advertisers=2, limit=10)` where `corpus` is an iterable of `(author_id, body_text)`. Pass `(store_id, name + " " + description)` tuples so a phrase must recur across ≥`min_advertisers` DISTINCT apps, and pass `limit=budget[category]`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/discovery/test_frame_mint.py
from sqlalchemy import text
from agf.discovery.frame_mint import mint_keywords_from_releases
from agf.enrich import AppMeta


def _meta(track_id, name, genre, desc):
    return AppMeta(track_id=track_id, name=name, seller="s", genre=genre,
                   release_date="2026-07-01", rating_count=0, avg_rating=0.0,
                   description=desc)


def test_mint_from_releases_adds_biased_trials(db_engine):
    # Fake new-releases: 3 Dating apps sharing the phrase "long distance couple",
    # 1 Games app (out of scope), 1 Health app. Dating has 0 coverage -> gets budget.
    seeds = [
        {"store_id": "10", "name": "LovePing", "genre": "Lifestyle"},
        {"store_id": "11", "name": "MissYou", "genre": "Lifestyle"},
        {"store_id": "12", "name": "FarApart", "genre": "Lifestyle"},
        {"store_id": "20", "name": "BlastZone", "genre": "Games"},
    ]
    metas = {
        "10": _meta("10", "LovePing", "Social Networking",
                    "long distance couple app for partners apart"),
        "11": _meta("11", "MissYou", "Social Networking",
                    "long distance couple reminders for partners"),
        "12": _meta("12", "FarApart", "Social Networking",
                    "long distance couple countdown for partners"),
        "20": _meta("20", "BlastZone", "Games", "shoot aliens"),
    }

    def fake_fetch(client, *, country, genre, limit):
        return list(seeds)

    def fake_lookup(client, ids, **kw):
        return {i: metas[i] for i in ids if i in metas}

    stats = mint_keywords_from_releases(
        db_engine, client=None, K=15, floor=0, total_slots=10, min_advertisers=2,
        fetch=fake_fetch, lookup=fake_lookup)

    assert stats["seeds"] == 4
    assert stats["in_scope"] == 3          # Games dropped
    assert stats["minted"] >= 1
    # the shared cross-app phrase was minted into the Social category
    with db_engine.begin() as conn:
        rows = conn.execute(text(
            "SELECT keyword FROM discovery.dynamic_keywords "
            "WHERE source = 'new_release:Social' AND kind='app'")).all()
    kws = {r.keyword for r in rows}
    assert any("long distance couple" in k for k in kws)


def test_mint_from_releases_lookup_error_is_isolated(db_engine):
    def fake_fetch(client, *, country, genre, limit):
        return [{"store_id": "1", "name": "X", "genre": "Lifestyle"}]

    def boom_lookup(client, ids, **kw):
        raise RuntimeError("apple down")

    stats = mint_keywords_from_releases(
        db_engine, client=None, floor=0, fetch=fake_fetch, lookup=boom_lookup)
    assert stats["errors"] >= 1
    assert stats["minted"] == 0   # no crash, stats returned
```

- [ ] **Step 2: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/python -m pytest tests/discovery/test_frame_mint.py -v`
Expected: FAIL (ImportError: cannot import name 'mint_keywords_from_releases')

- [ ] **Step 3: Write minimal implementation**

```python
# add imports at the top of src/agf/discovery/frame_mint.py
from collections import defaultdict

from sqlalchemy import text

from agf.db.dynamic_keywords import tried_keywords
from agf.discovery.coverage import canonical_category, category_coverage, mint_budget
from agf.discovery.keyword_mining import mine_phrases
from agf.enrich_full.appstore import fetch_itunes_new_releases, lookup_itunes_batch


def mint_keywords_from_releases(engine, client, *, country="us", limit=200, K=15,
                                floor=5, total_slots=40, min_advertisers=2,
                                fetch=fetch_itunes_new_releases,
                                lookup=lookup_itunes_batch) -> dict:
    """Mint head-term SEARCH keywords from App Store new-release listings, biased toward
    under-covered categories, plus keep the category frame above its floor. Taste-blind.
    Apple-only network (zero Meta). Returns stats; never raises on a fetch/lookup failure."""
    stats = {"seeds": 0, "in_scope": 0, "looked_up": 0, "minted": 0,
             "minted_by_category": {}, "frame_seeded": 0, "coverage": {}, "errors": 0}

    try:
        seeds = list(fetch(client, country=country, genre=None, limit=limit))
    except Exception:
        stats["errors"] += 1
        seeds = []
    stats["seeds"] = len(seeds)

    # In-scope only, dedup by store_id (iOS ids for the Apple Lookup).
    ids, seen = [], set()
    for a in seeds:
        sid = a.get("store_id")
        if not sid or sid in seen or canonical_category(a.get("genre")) is None:
            continue
        seen.add(sid)
        ids.append(sid)
    stats["in_scope"] = len(ids)

    metas = {}
    if ids:
        try:
            metas = lookup(client, ids, country=country)
        except Exception:
            stats["errors"] += 1
            metas = {}
    stats["looked_up"] = len(metas)

    # Group (store_id, listing_text) by canonical category from the Lookup genre.
    by_cat: dict[str, list] = defaultdict(list)
    for sid, m in metas.items():
        cat = canonical_category(getattr(m, "genre", None))
        if cat is None:
            continue
        text_blob = f"{m.name} {m.description}".strip()
        by_cat[cat].append((sid, text_blob))

    with engine.begin() as conn:
        coverage = category_coverage(conn)
        budget = mint_budget(coverage, K=K, total_slots=total_slots)
        exclude = tried_keywords(conn, kind="app")
        for cat, slots in budget.items():
            corpus = by_cat.get(cat)
            if not corpus:
                continue
            terms = mine_phrases(corpus, exclude=exclude,
                                 min_advertisers=min_advertisers, limit=slots)
            if not terms:
                continue
            added = add_trial_keywords(conn, terms, source=f"new_release:{cat}",
                                       kind="app")
            stats["minted"] += added
            stats["minted_by_category"][cat] = added
            exclude.update(t.lower() for t in terms)  # don't re-mint within this run
        stats["frame_seeded"] = ensure_category_frame(conn, coverage, floor=floor)
        stats["coverage"] = {c: {"found": cov.found, "live_keywords": cov.live_keywords}
                             for c, cov in coverage.items()}
    return stats
```

Then delete the old module:

```bash
git rm src/agf/discovery/release_mint.py
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/python -m pytest tests/discovery/test_frame_mint.py -v`
Expected: PASS (both new tests)

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/frame_mint.py tests/discovery/test_frame_mint.py
git rm src/agf/discovery/release_mint.py tests/discovery/test_release_mint.py
git commit -m "feat(discovery): mint_keywords_from_releases — coverage-biased keyword minting"
```

---

### Task 6: Wire the worker stage + env knobs

**Files:**
- Modify: `src/agf/workers/main.py` (import ~line 585; call ~586-587; config ~431 & ~924-925; stats print ~862-863)
- Modify: `ops/prod/docker-compose.yml` (worker env block)

**Interfaces:**
- Consumes: `mint_keywords_from_releases` (Task 5).
- Produces: worker stage 1.93 now mints keywords instead of page-matching; keeps `release_mint_due` gate, `open_release_mint_run`/`close_release_mint_run`, `AGF_RELEASE_MINT` on/off.

- [ ] **Step 1: Swap the import and call in stage 1.93**

Replace (main.py ~585-587):

```python
                from agf.discovery.release_mint import expand_releases_via_typeahead
                release_mint = expand_releases_via_typeahead(
                    engine, extractor, http_client, max_listings=release_mint_max_listings)
```

with:

```python
                from agf.discovery.frame_mint import mint_keywords_from_releases
                release_mint = mint_keywords_from_releases(
                    engine, http_client, K=frame_mint_k, floor=frame_mint_floor,
                    total_slots=frame_mint_slots,
                    min_advertisers=frame_mint_min_advertisers)
```

- [ ] **Step 2: Thread the new config**

In the `run_once(...)` signature (~423-434) replace `release_mint_max_listings=120,` with:

```python
             frame_mint_k=15, frame_mint_floor=5, frame_mint_slots=40,
             frame_mint_min_advertisers=2,
```

In `main()` cfg dict (~924-925) replace the `release_mint_max_listings=...` line with:

```python
        frame_mint_k=_env_int("AGF_FRAME_MINT_K", 15),
        frame_mint_floor=_env_int("AGF_FRAME_MINT_FLOOR", 5),
        frame_mint_slots=_env_int("AGF_FRAME_MINT_SLOTS", 40),
        frame_mint_min_advertisers=_env_int("AGF_FRAME_MINT_MIN_ADVERTISERS", 2),
```

Update the worker module docstring env table (~26-52): remove `AGF_RELEASE_MINT_MAX_LISTINGS`; add the four `AGF_FRAME_MINT_*` knobs with the defaults above; note `AGF_RELEASE_MINT` still gates the (now keyword-minting) stage on/off.

- [ ] **Step 3: Update the stats print**

Replace the release_mint stats print (~862-863) so it reads the new shape:

```python
    if s.get("release_mint") is not None:
        rm = s["release_mint"]
        print(f"[worker]   frame-mint: {rm.get('minted', 0)} keywords minted "
              f"across {len(rm.get('minted_by_category', {}))} categories, "
              f"{rm.get('frame_seeded', 0)} frame-seeded "
              f"({rm.get('in_scope', 0)} in-scope new-releases, "
              f"{rm.get('errors', 0)} errors)", flush=True)
```

- [ ] **Step 4: Add env knobs to prod compose**

In `ops/prod/docker-compose.yml` worker env block, replace the
`AGF_RELEASE_MINT_MAX_LISTINGS` line with:

```yaml
      AGF_FRAME_MINT_K: ${AGF_FRAME_MINT_K:-15}
      AGF_FRAME_MINT_FLOOR: ${AGF_FRAME_MINT_FLOOR:-5}
      AGF_FRAME_MINT_SLOTS: ${AGF_FRAME_MINT_SLOTS:-40}
      AGF_FRAME_MINT_MIN_ADVERTISERS: ${AGF_FRAME_MINT_MIN_ADVERTISERS:-2}
```

- [ ] **Step 5: Verify the worker imports and dry-runs**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/python -c "import agf.workers.main"`
Expected: no ImportError.
Run: `.venv/bin/python -m pyflakes src/agf/workers/main.py src/agf/discovery/frame_mint.py src/agf/discovery/coverage.py`
Expected: no unused-import / undefined-name errors (fix any: e.g. drop now-unused `expand_releases_via_typeahead` references, `release_mint_max_listings`).

- [ ] **Step 6: Full suite green**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/python -m pytest -q`
Expected: 0 failed (skips allowed only where already baseline). Grep the summary for "failed".

- [ ] **Step 7: Commit**

```bash
git add src/agf/workers/main.py ops/prod/docker-compose.yml
git commit -m "feat(worker): wire keyword-minting frame stage + AGF_FRAME_MINT_* knobs"
```

---

### Task 7: Live verification on prod

**Files:** none (ops).

- [ ] **Step 1: Build + push the worker image** per `DEPLOYMENT.md` / `ops/prod/cloudbuild.yaml` (worker image tag = short git sha).

- [ ] **Step 2: Update the running worker** on `agf-worker` (pull new image, `docker compose up -d worker`), confirm healthy.

- [ ] **Step 3: Trigger one frame-mint run** (it is daily-gated via `release_mint_ran_today`; either wait for the gate or run once out-of-band in the container with a one-off `python -c` calling `mint_keywords_from_releases`). Capture the returned stats.

- [ ] **Step 4: Verify in the DB** (via cloud-sql-proxy):

```sql
-- fresh minted + frame keywords appear
SELECT source, status, count(*) FROM discovery.dynamic_keywords
WHERE source LIKE 'new_release:%' OR source LIKE 'frame:%'
GROUP BY 1,2 ORDER BY 3 DESC LIMIT 30;
-- the run row carries the new stats shape
SELECT started_at, stats FROM discovery.release_mint_runs ORDER BY started_at DESC LIMIT 1;
```

Expected: non-zero `new_release:*` / `frame:*` trials; `stats.minted > 0`; coverage map present.

- [ ] **Step 5: Confirm the flywheel picks them up** (next flywheel run): the new trials appear in `live_keywords`, get swept, and some produce candidates (`discovery.candidates` with those keywords). Record the before/after against the old release_mint baseline (~0) in a short note / memory. Success = minted keywords produce candidates the page-matching mechanism never could.

---

## Self-Review

**Spec coverage:**
- §4a canonical_category → Task 1 ✓; CategoryCoverage/mint_budget/under_floor → Task 2 ✓; category_coverage → Task 3 ✓.
- §4b ensure_category_frame → Task 4 ✓; mint_keywords_from_releases → Task 5 ✓.
- §4c worker wiring + env knobs → Task 6 ✓.
- §6 taste-blindness → enforced (reward = coverage + new-unique; no ratings read) ✓.
- §7 testing table → Tasks 1-5 tests cover each row ✓.
- §8 rollout/verification → Task 7 ✓.
- §9 non-goals (app-graph, RAW-UCB, MMR-embeddings) → not implemented (release_mint.py's `app_graph_seed_apps` dropped) ✓.

**Placeholder scan:** no TBD/TODO; all steps carry real code/commands.

**Type consistency:** `canonical_category`, `CategoryCoverage(category, found, live_keywords)`, `mint_budget(coverage, *, K, total_slots)`, `under_floor_categories(coverage, *, floor)`, `category_coverage(conn)`, `ensure_category_frame(conn, coverage, *, floor)`, `mint_keywords_from_releases(engine, client, *, ...)` — names/signatures consistent across tasks and the worker call.

**Known verify-time checks for the implementer:**
- Confirm the test-DB fixture name (`db_engine` used here) matches `conftest.py`; adjust if the repo names it differently.
- Confirm `add_trial_keywords(conn, keywords, *, source, kind)` accepts the `conn` (it does per `db/dynamic_keywords.py`); the stage passes the open `conn` inside `engine.begin()`.
- After deleting `release_mint.py`, grep for any lingering import (`git grep expand_releases_via_typeahead`, `git grep app_graph_seed_apps`, `git grep release_mint_max_listings`) and remove.
