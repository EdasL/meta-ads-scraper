# Keyword-minting external frame — design spec

**Date:** 2026-07-02
**Status:** approved (brainstorm), pending implementation plan
**Replaces:** the broken `release_mint` page-matching stage (yields ~0 — see
`docs/research/2026-07-02-novel-discovery-algorithm.md` §5b)
**Research basis:** `docs/research/2026-07-02-novel-discovery-algorithm.md` (Layer 0 +
category-coverage alarm), refining `docs/research/2026-06-18-keyword-seed-design.md`.

---

## 1. Problem

Discovery is converging: the keyword flywheel only deepens topical basins it already
sits in (documented property of corpus-mined probing), and the intended external-frame
stage `release_mint` is structurally dead — it matches new-release apps to Meta pages by
NAME typeahead, which fails because new apps don't advertise yet and names collide with
unrelated brands (7 candidates all-time vs 2,317 from `niche_term`). The fix the research
prescribes is **keyword minting**, not page-matching: turn new-release listings and the
store-category taxonomy into SEARCH keywords that feed the normal sweep, so breadth comes
from an external frame the corpus can't self-generate.

## 2. Goal & non-goals

**Goal:** a daily, taste-blind stage that (a) mints head-term search keywords from App
Store new-release listings, (b) guarantees every in-scope store category always holds a
floor of live keywords (the fixed anti-collapse backbone), (c) biases minting toward
under-covered categories, and (d) surfaces a category-coverage metric. Minted keywords
ride the EXISTING flywheel sweep + trial→active→retired lifecycle.

**Non-goals (deferred to later increments):**
- App-graph traversal (similar / same-developer edges).
- The RAW-UCB / rotting-bandit scheduler (Layer 1) — keyword survival keeps using the
  existing promote/retire rule for now.
- MMR-over-embeddings dedup — v1 reuses the existing never-re-mint set + containment dedup.
- Any scraping-health MCP UI beyond writing coverage into the stage `stats` jsonb.

## 3. What we reuse (unchanged)

| Component | File | Role here |
|---|---|---|
| `phrases_of()`, `mine_phrases()` | `discovery/keyword_mining.py` | head-term n-gram extraction with function-word/generic/non-English filtering + containment dedup |
| `add_trial_keywords()`, `live_keywords()`, `record_keyword_sweep()`, `tried_keywords()` | `db/dynamic_keywords.py` | trial→active→retired lifecycle keyed on `(keyword, kind)`, with `source` label |
| Flywheel daily sweep of live dynamic keywords | worker stage 1.95 | sweeps `kind='app'` trials+actives, promotes earners, retires duds — minted keywords flow in automatically |
| `fetch_itunes_new_releases()`, `lookup_itunes_batch()` | `enrich_full/appstore.py`, `enrich.py` (`AppMeta`) | fetch new-release store_ids then batch-lookup name+genre+description (free Apple, ZERO Meta traffic) |
| `OUT_OF_SCOPE_GENRES` | `discovery/release_mint.py` (moves to frame_mint) | the in-scope category filter (no games/weather/nav) |
| taxonomy seeds | `discovery/taxonomy.py` / `sweep_entries()` | fixed per-category seed keywords for the frame backbone |
| `release_mint_runs` table + daily gate + isolation contract | `db/*`, worker stage 1.93 | run-tracking (stats jsonb) — REUSED as-is, no migration |
| `AGF_RELEASE_MINT` on/off env | worker | stays the on/off flag (no deploy-var rename) |

## 4. New code

Two small modules. `release_mint.py` is renamed to `frame_mint.py` (the mechanism is no
longer page-matching); the single worker import and any tests are repointed. The
`release_mint_runs` table and `AGF_RELEASE_MINT` env KEEP their names (no migration, no
deploy change) — a module docstring notes the mechanism changed on 2026-07-02.

### 4a. `discovery/coverage.py` (pure + one read)

- `category_coverage(conn) -> dict[str, CategoryCoverage]` — per in-scope category:
  `found` = distinct kept apps (candidates with `status IN ('kept','enriched')`, joined
  `discovery.candidates ⋈ public.apps ON (store, store_id)`, grouped by `apps.genre`),
  `live_keywords` = count of live dynamic keywords whose `source` is `new_release:<cat>`
  OR `frame:<cat>`, `deficit = max(0, K - found)`. Reads only. (`apps.genre` is populated
  for ~99.7% of apps; the `(store, store_id)` composite avoids cross-store id collisions.)
- `mint_budget(coverage, *, total_slots) -> dict[str, int]` — pure. Allocates
  `total_slots` across categories proportional to `deficit` (so empty categories get the
  most), with a small floor so every under-target category gets ≥1 slot. Returns
  per-category keyword-slot counts.
- `under_floor_categories(coverage, *, floor) -> list[str]` — pure. Categories whose
  `live_keywords < floor`.

### 4b. `discovery/frame_mint.py` (the stage body)

- `mint_keywords_from_releases(engine, client, *, country, limit, K, floor, total_slots,
  min_advertisers, fetch=fetch_itunes_new_releases, lookup=lookup_itunes_batch) -> dict`:
  1. `seeds = fetch(...)` → new-release `{store_id, name, genre}`; drop out-of-scope
     genres and store_ids already linked/known.
  2. `metas = lookup(client, [store_id...])` → `AppMeta` per app (name + description +
     primaryGenreName). Batches of ≤200, free.
  3. `coverage = category_coverage(conn)`; `budget = mint_budget(coverage, total_slots)`.
  4. Group listing texts (`name + " " + description`) by category. Per category, run
     `mine_phrases(texts, exclude=tried_keywords(conn), min_advertisers=min_advertisers,
     limit=budget[category])` — the corpus is `(store_id, listing_text)` tuples, so
     `mine_phrases`'s distinct-author key becomes the app; a phrase must recur across
     ≥`min_advertisers` DISTINCT apps to qualify (category vocabulary, never one app's
     brand), and `limit` caps it to the category's budget.
  5. `add_trial_keywords(terms, source=f"new_release:{category}", kind="app")`.
  6. `ensure_category_frame(conn, coverage, floor)`: for each under-floor category, add its
     `sweep_entries()` taxonomy seed keywords as `source=f"frame:{category}"` trials (the
     fixed backbone; `add_trial_keywords` no-ops on existing keywords).
  7. Return `stats = {seeds, in_scope, looked_up, minted_by_category, frame_seeded,
     coverage: {category: {found, live_keywords, deficit}}, errors}`.
- Isolation: any exception is caught by the worker stage wrapper, recorded on the
  `release_mint_runs` row, and never aborts the tick (existing contract). Apple lookups
  degrade to `[]` on a bad feed → stats show 0, no crash.

### 4c. Worker wiring

Stage 1.93 keeps its daily gate (`release_mint_due`) and run-row open/close; the call
body swaps `expand_releases_via_typeahead(...)` for `mint_keywords_from_releases(...)`.
New env knobs (all `_env_int`, documented in the worker header):
- `AGF_FRAME_MINT_K` (default 15) — target distinct kept apps per category.
- `AGF_FRAME_MINT_FLOOR` (default 5) — min live keywords per category (backbone).
- `AGF_FRAME_MINT_SLOTS` (default 40) — total trial keyword slots minted per run.
- `AGF_FRAME_MINT_MIN_ADVERTISERS` (default 2) — distinct apps a phrase must span.
`AGF_RELEASE_MINT` still gates the stage on/off.

## 5. Data flow

```
daily (release_mint_due gate):
  new-releases ──fetch+lookup(free Apple)──▶ [{store_id, genre, name, description}]
        │
        ├─ category_coverage(conn) ──▶ per-category {found, live_keywords, deficit}
        │        │
        │        └─ mint_budget ──▶ per-category slot counts (∝ deficit)
        │
  group by genre ─ per category: mine_phrases(texts, exclude=tried, min_advertisers)[:slots]
        │                              (author key = store_id)
        ▼
  add_trial_keywords(source="new_release:<cat>", kind="app")
  ensure_category_frame: under-floor cats ← taxonomy seeds (source="frame:<cat>")
        ▼
  (existing) flywheel sweep runs live dynamic keywords → new candidates → triage → kept
        ▼
  promote earners / retire dry (existing record_keyword_sweep) ; coverage rises next run
```

## 6. Taste-blindness (invariant)

The only steering signal is category coverage (fill under-covered categories) and
new-unique keyword yield (existing promote/retire). Neither reads user ratings, likes, or
the preference model. Keyword minting is human-configurable via the K/floor/slots knobs
but never taste-weighted. This preserves the standing product decision
(`no-scraper-search-bias-2026-06-19`).

## 7. Testing

| Unit | Type | Assertion |
|---|---|---|
| `mine_phrases` on listing corpus | pure unit | cross-app terms qualify; one app's repeated brand word does NOT; generic/non-English filtered |
| `mint_budget` | pure unit | slots allocated ∝ deficit; every under-target category ≥1; sums to total_slots |
| `under_floor_categories` | pure unit | returns exactly categories below floor |
| `category_coverage` | DB test | seed candidates across genres → correct found/live_keywords/deficit map |
| `ensure_category_frame` | DB test | under-floor category gains its taxonomy seed trials; at-floor category untouched |
| `mint_keywords_from_releases` | integration (fake fetch+lookup, real DB) | trials added with correct `source`, biased by coverage; out-of-scope genres dropped; error in lookup → recorded, no raise |

Run DB tests with `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it`. Full
suite must stay at 0 failed.

## 8. Rollout & verification

1. Ship behind `AGF_RELEASE_MINT=1` (already set in prod).
2. Deploy the worker image; the stage runs on its daily gate.
3. Live verify: after one run, `discovery.dynamic_keywords` shows fresh
   `source='new_release:*'` and `source='frame:*'` trials; over subsequent days, some
   promote to `active` and produce candidates (candidates with `technique='flywheel'`
   from those keywords). Coverage map in `release_mint_runs.stats` shows deficits shrinking.
4. Success signal: kept-candidates/week stops declining and category coverage entropy
   rises (the diversity meter from the research). Contrast against the old `release_mint`
   baseline of ~0.

## 9. Open follow-ups (explicitly out of scope here)

- Layer 1 RAW-UCB scheduler (recent-window new-unique reward) once this frame supplies
  wet arms.
- App-graph frontier lane.
- Chao2 saturation denominator + coverage surfacing in the scraping-health MCP.
