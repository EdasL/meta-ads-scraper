# Category-Balanced Keyword Seed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the wellness-skewed 270-term `NICHE_TERMS` vocabulary with a category-balanced frame (`NICHE_TERMS_BY_CATEGORY`, 32 store categories × 9 terms) guarded by a balance test, so the app-discovery sweep is unbiased by construction.

**Architecture:** `taxonomy.py` gains a category-keyed dict that flattens into the existing `NICHE_TERMS` list (so `all_entries()` / `sweep_entries()` and every downstream consumer are untouched). A new pure unit test asserts the frame stays balanced. The seed content is the bake-off-validated `docs/research/keyword-experiments/full-seed-v1.txt` (97.6% category evenness, 31% novelty, feeds the flywheel a 277-keyword pool — see `RESULTS.md`).

**Tech Stack:** Python 3.13, pytest. No new dependencies.

## Global Constraints

- The seed content is **`docs/research/keyword-experiments/full-seed-v1.txt`** verbatim (288 terms, 9 per category, all `app`-suffixed, validated). Do not hand-edit term content.
- In-scope categories = all consumer store categories **except Games and Stickers** (structural exclusions only).
- Per-category quota: enforced band **8–12** terms (`NICHE_PER_CATEGORY_MIN = 8`, `NICHE_PER_CATEGORY_MAX = 12`); the seed ships 9 each.
- `NICHE_TERMS` must remain a flat `list[str]` — `all_entries()` consumes it unchanged.
- **No pruning of "dead" terms** in this plan (deliberate — see spec §9; pruning by yield reintroduces bias and breaks the balance band).
- Run the full suite with `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it` (DB tests skip silently without it — a skip is not a pass).
- Product copy rule (CLAUDE.md): N/A here (no user-facing strings).

---

### Task 1: Balance test (the durable anti-skew guard)

**Files:**
- Create: `tests/discovery/test_taxonomy_balance.py`

**Interfaces:**
- Consumes (from Task 2): `agf.discovery.taxonomy.NICHE_TERMS_BY_CATEGORY: dict[str, list[str]]`, `IN_SCOPE_CATEGORIES: frozenset[str]`, `NICHE_PER_CATEGORY_MIN: int`, `NICHE_PER_CATEGORY_MAX: int`, `NICHE_TERMS: list[str]`.
- Produces: nothing (leaf test).

- [ ] **Step 1: Write the failing test**

```python
"""The frame can't silently re-skew: every in-scope category present, even quota,
no dupes, app-suffixed, no brand/ad-copy noise. Pure (no DB)."""
from agf.discovery import taxonomy

# A few obvious brand tokens a generic niche term should never contain.
_BRAND_TOKENS = {"calm", "duolingo", "noom", "spotify", "tinder", "headspace", "strava"}
# Generic ad-copy phrases that are not niches.
_GENERIC = {"what's your goal", "try the app free", "download the app", "best app"}


def test_every_in_scope_category_present_and_no_others():
    assert set(taxonomy.NICHE_TERMS_BY_CATEGORY) == set(taxonomy.IN_SCOPE_CATEGORIES)
    assert "Games" not in taxonomy.NICHE_TERMS_BY_CATEGORY
    assert "Stickers" not in taxonomy.NICHE_TERMS_BY_CATEGORY


def test_each_category_within_quota_band():
    lo, hi = taxonomy.NICHE_PER_CATEGORY_MIN, taxonomy.NICHE_PER_CATEGORY_MAX
    for cat, terms in taxonomy.NICHE_TERMS_BY_CATEGORY.items():
        assert lo <= len(terms) <= hi, f"{cat} has {len(terms)} terms (band {lo}-{hi})"


def test_no_duplicate_terms_across_frame():
    flat = taxonomy.NICHE_TERMS
    assert len(flat) == len(set(flat)), "duplicate niche terms"


def test_terms_are_clean_and_app_suffixed():
    for t in taxonomy.NICHE_TERMS:
        assert t == t.lower(), f"{t!r} not lowercase"
        assert t.endswith(" app"), f"{t!r} must end in 'app'"
        assert t not in _GENERIC, f"{t!r} is a generic ad-copy phrase"
        toks = set(t.split())
        assert not (toks & _BRAND_TOKENS), f"{t!r} contains a brand name"


def test_niche_terms_is_flatten_of_the_dict():
    expected = [t for terms in taxonomy.NICHE_TERMS_BY_CATEGORY.values() for t in terms]
    assert taxonomy.NICHE_TERMS == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/discovery/test_taxonomy_balance.py -v`
Expected: FAIL — `AttributeError: module 'agf.discovery.taxonomy' has no attribute 'NICHE_TERMS_BY_CATEGORY'` (and `IN_SCOPE_CATEGORIES`, constants).

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/discovery/test_taxonomy_balance.py
git commit -m "test(taxonomy): balance test for category-balanced niche seed"
```

---

### Task 2: Category-balanced taxonomy (`NICHE_TERMS_BY_CATEGORY`)

**Files:**
- Modify: `src/agf/discovery/taxonomy.py` — replace the `_NICHE_CORE`/`_NICHE_FITNESS`/`_NICHE_HEALTH_WOMEN`/`_NICHE_DIET`/`_NICHE_SLEEP`/`_NICHE_MENTAL`/`_NICHE_PRODUCTIVITY`/`_NICHE_LEARNING`/`_NICHE_AI_TOOLS`/`_NICHE_FINANCE`/`_NICHE_FAMILY_RELATION`/`_NICHE_LIFESTYLE` blocks and the `NICHE_TERMS = (...)` assignment (currently `taxonomy.py:44-160`) with the new structure. Leave `FUNNEL_PHRASES`, `STORE_FRAGMENTS`, `TaxonomyEntry`, `all_entries`, `sweep_entries` unchanged.
- Source data: `docs/research/keyword-experiments/full-seed-v1.txt` (do not retype — generate, Step 1).

**Interfaces:**
- Consumes: `docs/research/keyword-experiments/full-seed-v1.txt` (category headers `# <Category>` + one term per line).
- Produces (for Task 1 + all existing consumers): `NICHE_TERMS_BY_CATEGORY: dict[str, list[str]]`, `IN_SCOPE_CATEGORIES: frozenset[str]`, `NICHE_PER_CATEGORY_MIN = 8`, `NICHE_PER_CATEGORY_MAX = 12`, `NICHE_TERMS: list[str]` (flattened).

- [ ] **Step 1: Generate the dict literal from the validated seed file**

Run this one-off codegen (DRY — avoids transcription error) and capture its stdout:

```bash
cd /Users/edaslakavicius/Projects/emerging-app-finder
python3 - <<'PY'
cat=None; d={}
for ln in open('docs/research/keyword-experiments/full-seed-v1.txt'):
    ln=ln.rstrip('\n')
    if ln.startswith('# ') and 'recipe' not in ln and 'Source' not in ln:
        cat=ln[2:]; d[cat]=[]
    elif ln and not ln.startswith('#'):
        d[cat].append(ln.strip())
print("NICHE_TERMS_BY_CATEGORY: dict[str, list[str]] = {")
for c, terms in d.items():
    print(f"    {c!r}: [")
    line="        "
    for t in terms:
        chunk=f"{t!r}, "
        if len(line)+len(chunk) > 96:
            print(line.rstrip()); line="        "
        line+=chunk
    if line.strip(): print(line.rstrip())
    print("    ],")
print("}")
PY
```

Expected: a Python dict literal with 32 category keys, 9 terms each, printed to stdout. Copy it for Step 2.

- [ ] **Step 2: Replace the niche section in `taxonomy.py`**

Delete everything from the `# Layer 2 — app-niche / pain-point terms ...` comment block and the `_NICHE_CORE = [...]` list through the `NICHE_TERMS = (_NICHE_CORE + ... + _NICHE_LIFESTYLE)` assignment (`taxonomy.py:42-160`). Replace with:

```python
# Layer 2 — app-niche terms, balanced across the store category frame. Each
# in-scope category holds an equal quota (NICHE_PER_CATEGORY_MIN..MAX) of narrow,
# `app`-suffixed terms. Balance is asserted by tests/discovery/test_taxonomy_balance.py
# so the frame cannot silently re-skew. Source: bake-off-validated
# docs/research/keyword-experiments/full-seed-v1.txt (see that dir's RESULTS.md).
# Regenerate a category's list when it saturates — do not hand-pile one vertical.
NICHE_PER_CATEGORY_MIN = 8
NICHE_PER_CATEGORY_MAX = 12

# In-scope = every consumer store category; only Games (out of product scope,
# vetoed via is_game) and Stickers (not standalone apps) are excluded.
IN_SCOPE_CATEGORIES = frozenset({
    "Art & Design", "Auto & Vehicles", "Beauty", "Books", "Business", "Comics",
    "Communication", "Dating", "Education", "Entertainment", "Events", "Finance",
    "Food & Drink", "Health & Fitness", "House & Home", "Lifestyle", "Medical",
    "Music", "Navigation", "News", "Parenting", "Personalization", "Photo & Video",
    "Productivity", "Reference", "Shopping", "Social", "Sports", "Travel",
    "Utilities", "Video Players", "Weather",
})

# <-- paste the NICHE_TERMS_BY_CATEGORY = {...} literal from Step 1 here -->

NICHE_TERMS = [t for terms in NICHE_TERMS_BY_CATEGORY.values() for t in terms]
```

(Keep the module docstring's three-layer description; update the `NICHE_TERMS` line in it from "app-category / pain-point terms" to note the category-balanced frame if desired — optional, non-functional.)

- [ ] **Step 3: Run the balance test — verify it passes**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/discovery/test_taxonomy_balance.py -v`
Expected: PASS (5 tests). If `test_every_in_scope_category_present_and_no_others` fails, the dict keys don't match `IN_SCOPE_CATEGORIES` — reconcile the category header spelling in `full-seed-v1.txt` against the frozenset.

- [ ] **Step 4: Verify downstream consumers are intact**

Run:
```bash
PYTHONPATH=src .venv/bin/python -c "
from agf.discovery.taxonomy import NICHE_TERMS, all_entries, sweep_entries
print('niche terms:', len(NICHE_TERMS))
print('all_entries:', len(all_entries()))
print('sweep_entries:', len(sweep_entries()))
assert len(NICHE_TERMS) == 288
assert all(e.layer in ('funnel_phrase','niche_term','store_fragment') for e in all_entries())
print('OK')
"
```
Expected: `niche terms: 288`, `sweep_entries` ≈ 288 + len(STORE_FRAGMENTS), `OK`.

- [ ] **Step 5: Run the full suite (no regressions)**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest -q`
Expected: 0 failed. Grep the summary for `skipped` — DB tests must not be silently skipped (the env var above prevents that). The new balance test is among the passes.

- [ ] **Step 6: Commit**

```bash
git add src/agf/discovery/taxonomy.py
git commit -m "feat(taxonomy): category-balanced niche seed (NICHE_TERMS_BY_CATEGORY)

Replace the wellness-skewed 270-term niche vocabulary with a 32-category x 9
balanced frame (validated: 97.6% category evenness, 31% novelty). NICHE_TERMS
stays a flat list so all_entries/sweep_entries and downstream are unchanged.
Guarded by test_taxonomy_balance."
```

---

### Task 3: Restart the worker so the live sweep picks up the new seed

**Files:** none (operational).

**Interfaces:**
- Consumes: the committed `taxonomy.py`.
- Produces: a running worker sweeping the new vocabulary.

- [ ] **Step 1: Restart the worker (it holds taxonomy in-memory)**

Run: `launchctl kickstart -k gui/$(id -u)/com.fielddesk.worker`
Expected: no error. (Per memory: the live worker caches code in-memory; a kickstart reloads it.)

- [ ] **Step 2: Confirm it's sweeping the new terms**

Run: `tail -n 40 ~/Library/Logs/fielddesk-*.log` (or the worker stdout log) and look for sweep lines quoting new category terms (e.g. `'comic reader app'`, `'ai itinerary planner app'`) rather than only the old wellness terms.
Expected: log shows the new vocabulary in the sweep. If the worker errors on import, the balance/structure is wrong — revert the commit and re-check Task 2.

---

## Self-Review

**Spec coverage:**
- §4 in-scope categories → Task 2 `IN_SCOPE_CATEGORIES` (Games/Stickers excluded). ✓
- §5 structure (`NICHE_TERMS_BY_CATEGORY` + flat `NICHE_TERMS`, constants, 8–12 band) → Task 2. ✓
- §6 balance test (5 assertions: coverage, band, dups, lowercase+suffix+no-brand/generic, flatten) → Task 1. ✓
- §7 generation rule → not code; the seed file was generated per it. Recorded in spec. ✓
- §0/§8 findings (suffix required, common wording, app/funnel complementarity) → embedded in the validated seed; suffix asserted in Task 1. ✓
- §10 success criteria (balance test passes, suite green, no downstream change) → Tasks 2–3. ✓

**Placeholder scan:** Step 1 of Task 2 generates the dict (no 288-line paste, but complete + reproducible); the paste point is explicitly marked. No TBD/TODO. ✓

**Type consistency:** `NICHE_TERMS_BY_CATEGORY` (dict[str,list[str]]), `IN_SCOPE_CATEGORIES` (frozenset), `NICHE_PER_CATEGORY_MIN/MAX` (int), `NICHE_TERMS` (list[str]) — names identical across Task 1 (consumes) and Task 2 (produces). ✓

**Deliberate non-goal:** dead-term pruning (would break the 8–12 band and reintroduce yield bias; spec §9). Documented in Global Constraints.
