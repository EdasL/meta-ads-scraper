# Category-balanced keyword seed — design

**Date:** 2026-06-19
**Status:** approved + bake-off-validated; pending full-seed generation → implementation plan
**Supersedes (in part):** the hand-authored `NICHE_TERMS` vocabulary in
`src/agf/discovery/taxonomy.py`. Builds on the diagnosis in
`docs/research/2026-06-18-keyword-seed-design.md`. Empirical results in
`docs/research/keyword-experiments/RESULTS.md`.

---

## 0. Bake-off findings (live experiments, 2026-06-19)

Three keyword-generation techniques were swept against the live Meta Ad Library
(`scripts/keyword_seed_experiment.py`, no DB writes) and scored on category
diversity + yield. The winning recipe — **category-balanced + `app` suffix +
common ad wording** — beat the production baseline on every axis (top-genre 26%→
**14.6%**, evenness 76%→**87.5%**, yield 1.3→~2.0, dead-kw 32%→**20%**) and its
catch was verified diverse (130 apps / 34 genres; the 31 *new* apps span 19
genres with no clustering). Three findings shaped this spec:

1. **The `app` suffix is required, not optional.** It raised yield +28% AND
   improved diversity vs. bare phrases (it filters to app advertisers; bare
   phrases pull funnel/web ads concentrated in a few verticals). This reverses an
   earlier "suffix optional" relaxation. (§5, §6, §7)
2. **Prefer common ad wording over over-specific phrasing.** Over-specific terms
   get zero Meta inventory; rephrasing dead terms to common wording cut dead-rate
   47%→19% and doubled yield. (§7)
3. **The app seed and the funnel seed are complementary by advertising style.**
   ~half of "dead" app terms are real niches that advertise via *funnels* (no
   direct store link) — the category-agnostic funnel-phrase pipeline already
   catches those. Do not force the app keyword seed to cover them; its "dead
   rate" overstates true system waste. (§8)

## 1. Problem

The app-discovery sweep's keyword vocabulary (`NICHE_TERMS`, ~270 hand-authored
"X app" terms) is **biased by category**. It was written by one author's
instincts and piles ~70% of its terms into a handful of wellness-adjacent store
categories (Health & Fitness, Mental, Diet, Sleep, Productivity). Live data
confirms the skew:

- `niche_term` is the dominant discovery surface: 1576 candidates / **722 kept**
  (vs. flywheel 79, store_fragment 14, funnels ~15). So its bias dominates what
  the product surfaces.
- Health & Fitness alone is ~25% of kept apps; the deliberate niche vocabulary
  piles into ~6 of ~35 store categories (per the 2026-06-18 research doc).

We want a seed that finds niche emerging apps **without being biased to a
specific category or a specific app**.

### Avenues ruled out (do not re-litigate)

- **`release_mint` (App Store new-releases → Meta typeahead).** Built and enabled
  in prod (`AGF_RELEASE_MINT=1`) but produces **zero** candidates (run id 1:
  91 apps → 41 typeahead → 40 listings scraped → 0 candidates). The mechanism
  has been checked and does not work; we are not betting this redesign on it.
  (Root cause, for the record: `classify_candidate` demands a direct app-store
  link in the ad creative, which new-release apps' funnel ads don't carry — but
  fixing that is out of scope here per the product owner's call.)
- **Keyword-mining new-release names/descriptions.** Already live-tested as a
  dead end (names too sparse, descriptions boilerplate — see `release_mint.py`
  docstring).

## 2. Approach

Anchor the keyword vocabulary to a **fixed external frame — the store category
taxonomy — and give every in-scope category an equal quota of narrow keywords.**

- **Even coverage = not biased to a category.** Health & Fitness gets the same
  quota as Weather. The wellness pile-up is removed by construction.
- **Narrow niche terms = still emerging-biased.** The probe insight
  (`taxonomy.py:2`) holds: a narrow keyword filters to small emerging
  advertisers even when impression-sorted. We keep the narrowness; we only
  change *which* niches are chosen and *how evenly*.
- **Generated, never brand-named = not biased to a specific app.** Terms are
  generic niche/pain-point phrases ("sleep tracker app"), never app or brand
  names.

Taste is applied at **feed ranking** (the p_like model), never at the **sweep**.
This is consistent with the standing "no scraper-search bias" decision
(2026-06-19): keep the breadth sweep taste-blind to avoid a filter bubble.

## 3. Decisions (locked during brainstorm)

| Decision | Choice |
|---|---|
| Bias model | Category-agnostic via **even per-category coverage** (the seed's *selection* is unbiased; terms stay topical+narrow) |
| Generation | **LLM per-category quota** — an LLM proposes N narrow terms per category; reviewed/pruned once by the product owner |
| Coverage gap (apps that launched months ago, scaling now) | **Frame covers it** — the keyword sweep catches actively-advertising apps regardless of store-launch date; no extra mechanism, no yield-weighting (YAGNI) |
| Cross-cutting modifiers (AI, etc.) | **Instruct-to-include** — AI is a modifier, not a category; the generation prompt tells the LLM to weave emerging-modifier variants into each category naturally. No reserved slots, no separate theme axis |

## 4. The frame — in-scope categories

Every **consumer** store category is in scope. Only **structural** exclusions
(not preferential ones), matching the existing `release_mint`
`OUT_OF_SCOPE_GENRES` philosophy:

- **Games** — out of product scope (already vetoed via `is_game`; scraping just
  burns budget).
- **Stickers** — not standalone apps.

In-scope set (~30; Apple non-game primaries + Play-side consumer adds):

```
Art & Design, Auto & Vehicles, Beauty, Books, Business, Comics,
Communication, Dating, Education, Entertainment, Events, Finance,
Food & Drink, Health & Fitness, House & Home, Lifestyle, Medical, Music,
Navigation, News, Parenting, Personalization, Photo & Video, Productivity,
Reference, Shopping, Social, Sports, Travel, Utilities, Video Players, Weather
```

The product owner may veto specific categories at spec review (e.g. Weather /
Navigation if judged to have no emerging-app activity) — but the default is "all
consumer categories in," because excluding by taste is exactly the bias we're
removing. Emerging-only is still enforced downstream by the incumbent
(`page_like_count`) veto and triage.

## 5. Structure (in `src/agf/discovery/taxonomy.py`)

Replace the `_NICHE_CORE / _NICHE_FITNESS / _NICHE_HEALTH_WOMEN / …` vertical
blobs with a single category-keyed mapping so balance is **visible and
enforceable**:

```python
# Layer 2 — app-niche terms, balanced across the store category frame.
# Each in-scope category holds an equal quota (NICHE_PER_CATEGORY_MIN..MAX) of
# narrow, `app`-suffixed niche terms (suffix required — see §0 findings). Balance
# is asserted by test_taxonomy_balance so the frame cannot silently re-skew.
# Generated per CATEGORY_KEYWORD_GEN_PROMPT (§7); regenerate a category's list
# when it saturates — do not hand-pile.
NICHE_TERMS_BY_CATEGORY: dict[str, list[str]] = {
    "Health & Fitness": [...],   # same quota as every other category
    "Photo & Video":    [...],   # includes ai photo/headshot/avatar variants
    "Weather":          [...],
    # ... one key per in-scope category ...
}

NICHE_TERMS = [t for terms in NICHE_TERMS_BY_CATEGORY.values() for t in terms]
```

- `NICHE_TERMS` keeps its existing type (flat `list[str]`) so
  `all_entries()` / `sweep_entries()` and every downstream consumer are
  unchanged.
- Quota: **target ~8–10 terms per category, enforced band 8–12** → ~250–300
  total across ~30 categories, keeping sweep cost/cadence the same as today (no
  change to `AGF_SWEEP_PAGES`, cadence, or proxy budget).
- Define module constants `NICHE_PER_CATEGORY_MIN = 8`, `NICHE_PER_CATEGORY_MAX = 12`
  and `IN_SCOPE_CATEGORIES` (the frozenset above) for the balance test to read.

The `FUNNEL_PHRASES`, `STORE_FRAGMENTS`, `TaxonomyEntry`, `all_entries`, and
`sweep_entries` definitions are **unchanged**.

## 6. The balance test (the frame can't silently re-skew)

Add `tests/discovery/test_taxonomy_balance.py` (no DB) asserting:

1. `set(NICHE_TERMS_BY_CATEGORY) == IN_SCOPE_CATEGORIES` — every in-scope
   category present, no out-of-scope category (Games/Stickers) present.
2. For every category, `MIN <= len(terms) <= MAX` — no category over- or
   under-represented.
3. No duplicate terms across the whole frame.
4. Every term is lowercase, **ends in the word `app`** (the bake-off in §0 showed
   the suffix raises both yield and diversity — it filters to app advertisers),
   contains no obvious brand/app names (lightweight denylist check), and is not a
   generic ad-copy phrase (a small denylist, e.g. reject "what's your goal").

This test is the durable guard the research doc called for ("the flywheel may
drift, the frame cannot").

## 7. Generation rule (reproducible, taste-free)

The initial `NICHE_TERMS_BY_CATEGORY` is generated by an LLM during
implementation (the implementer, i.e. Claude — no runtime LLM dependency, no
regen script yet; YAGNI). The **exact prompt is recorded here** so any future
saturation refresh is reproducible:

> **`CATEGORY_KEYWORD_GEN_PROMPT`** (run once per in-scope category):
>
> "You are seeding a Meta Ad Library discovery sweep that hunts *emerging,
> niche* consumer apps (small advertisers, not category leaders) in the App
> Store / Google Play category **{CATEGORY}**.
>
> Produce **{N} short, narrow search keywords**, one per line, each a specific
> sub-niche or pain-point in this category, phrased as it would appear in app
> marketing and **ending in the word `app`** (e.g. `sleep tracker app`,
> `wall pilates app`, `macro tracker app`).
>
> Rules:
> - **Narrow, not broad.** `wall pilates app`, not `fitness app`. Narrow terms
>   filter to small emerging advertisers; broad terms return incumbents.
> - **End every term in `app`.** The bake-off showed the suffix acts as an
>   app-intent filter — it raises yield (+28%) AND diversity vs. bare phrases,
>   which pull funnel/web ads instead of app advertisers.
> - **Use common ad wording, not over-specific phrasing.** `pickleball app`,
>   not `pickleball score tracker app`; `math homework app`, not `ai math solver
>   app`. Over-specific phrasings get zero Meta inventory (no advertiser words it
>   that way) — the bake-off's dead-rate fell 47%→19% when dead terms were
>   rephrased to common wording.
> - **Weave in current emerging-modifier variants where they're real for this
>   category** — AI-powered first (`ai {thing} app`), plus where natural:
>   `… tracker app`, `… scanner app`, `… coach app`. Do not force a modifier
>   where no such app exists.
> - **No brand or app names.** Never reference a specific product
>   (no "Calm", "Duolingo", etc.).
> - **No generic ad-copy phrases** ("what's your goal", "try the app free").
> - **No games.**
> - Cover distinct sub-niches; avoid near-duplicates of each other."

The product owner reviews and prunes the generated list once before it ships.

## 8. What stays unchanged

- `FUNNEL_PHRASES` — already shape-based and category-agnostic; the funnel
  pipeline owns it. **Complementary to this app seed by advertising style**
  (finding 3): the app seed catches apps that link directly to the store; the
  funnel seed catches apps that advertise via funnels. Niches that came up
  "dead" for the app seed because they advertise via funnels (e.g. menopause,
  adhd, manifestation, price-tracker) are caught here — do not duplicate them
  into the app seed to chase its dead-rate.
- `STORE_FRAGMENTS` — broad app-install baseline catcher.
- The keyword flywheel, funnel sweep, app-graph seeding, and the feed p_like
  ranking.
- Sweep cost: term count stays ~250–300, so cadence/pages/proxy budget are
  untouched.

Note: the `_NICHE_SPIRITUAL` removal (taxonomy.py:141, dropped 2026-06-18
because its apps were disliked) **naturally reverses** — Lifestyle / Reference
get their fair quota back. This is intentional and correct: disliked niches are
filtered by the *feed model*, not by starving the *sweep*. The sweep stays
taste-blind by design.

## 9. Out of scope

- Fixing or reviving `release_mint` (ruled out, §1).
- Yield × like-rate keyword weighting (deferred; product owner chose "frame
  covers it").
- An automated per-category regeneration script (YAGNI — the prompt in §7 makes
  manual refresh reproducible).
- Any change to funnel discovery, scoring, or the feed.

## 10. Success criteria

- `NICHE_TERMS_BY_CATEGORY` covers all in-scope categories with an even quota
  (~1/N of the *keyword* vocabulary each); `test_taxonomy_balance` passes.
- The *found-app* distribution is far more even than the baseline — top genre
  ≤ ~15% and category evenness ≥ ~0.85 on a validation bake-off (vs baseline
  26% / 0.76). Note H&F still leads (~14.6% measured) because it genuinely has
  more emerging app supply; the goal is even *coverage*, not a flat histogram —
  forcing H&F lower would be bias in reverse.
- AI/emerging-modifier terms appear across the categories where such apps ship
  (Photo & Video, Productivity, Education, Entertainment, Lifestyle, …), not in
  a single bucket.
- Full test suite green
  (`AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it python -m pytest -q`,
  0 failed, and the new balance test is not skipped).
- No change to sweep cadence/cost or to any downstream consumer of
  `NICHE_TERMS`.
