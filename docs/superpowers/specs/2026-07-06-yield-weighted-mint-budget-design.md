# Yield-weighted mint budget — design

**Date:** 2026-07-06
**Problem:** the frame-mint stage (shipped 2026-07-02) mints high-quality keywords but at
negligible volume, pointed at barren categories. Four days of prod data:

- Deficit-only allocation (`max(0, K - found)`, K=15) sent **100% of mint slots to News,
  Weather, Shopping** — the only categories that ever got minted keywords (9/6/5). Across
  70 sweeps those 20 keywords produced **zero new candidates**; 15 are already retired.
- The categories that demonstrably produce keepers on Meta got **zero slots** because
  their all-time `found` ≥ K zeroes the deficit: Health & Fitness (159 kept/21d),
  Tools (110), Entertainment (86), Utilities (82), Lifestyle (65), Productivity (57).
  The bias is inverted: it steers minting *away* from wherever minting can work.
- Only ~5 keywords/day were minted against `total_slots=40`, because slots allocated to a
  category with no same-day new-release listings are silently dropped
  (`if not listings: continue`).

Context: the flywheel pool is ~92% Chao2-exhausted
(docs/research/2026-07-02-novel-discovery-algorithm.md §5) — new keepers come from new
keyword arms, and frame-mint is the arm factory. Fixing its aim and volume is the
highest-leverage change; the RAW-UCB scheduler is deferred (it would optimize a
shrinking basin).

## Goal

Mint budget flows mostly to categories that are **productive on Meta** (recent keeper
yield), with a smaller exploration lane for under-covered categories, and no slot is
wasted on a category the run has no listing material for. Volume per run approaches
`total_slots`. Everything stays taste-blind (yield = triage-kept counts, never user
ratings).

## Changes (2 files, no migration)

### 1. `coverage.py` — recent yield + two-lane allocator

- `CategoryCoverage` gains `kept_recent: int` — distinct kept apps in the category with
  `candidates.first_seen_at >= now() - interval '30 days'` (same join/fold as `found`,
  windowed). `category_coverage()` fills it with one extra windowed query.
- `mint_budget(coverage, *, K, total_slots, explore_share=0.25, eligible=None)` replaces
  the deficit-only rule with two lanes:
  - **Exploit lane** (`total_slots - explore_slots`): proportional to `kept_recent`
    across categories with `kept_recent > 0`.
  - **Explore lane** (`explore_slots = round(explore_share * total_slots)`): the old
    rule — proportional to `max(0, K - found)` across categories with a positive
    deficit. Keeps deserts probed at a bounded cost (minted keywords retire fast via
    `dry_streak`, so a losing probe costs ~5 sweeps).
  - `eligible` (a set of category names, or None = all): both lanes allocate only among
    eligible categories. The caller passes the categories that actually have listings
    this run, so no slot evaporates.
  - Lane totals merge (a category can win slots in both). Same deterministic
    floor-1 / overflow-trim behavior as today, factored into one proportional helper.

### 2. `frame_mint.py` — pass eligibility

- Build `by_cat` first (unchanged), then call
  `mint_budget(coverage, K=K, total_slots=total_slots, explore_share=explore_share,
  eligible=set(by_cat))`. The `if not listings: continue` guard stays as a belt-and-braces
  no-op.
- `mint_keywords_from_releases` gains `explore_share=0.25`, wired to a new
  `AGF_FRAME_MINT_EXPLORE_SHARE` env knob in `workers/main.py` alongside the existing
  frame-mint knobs.

## Why yield-weighting is not the taste bias the user declined

The 2026-06-19 decision bans steering the scraper by **user preference** (like-rate /
semantic taste). `kept_recent` is the triage quality gate (store-listing checks, rating
floors) — a measure of where emerging advertisers exist on Meta, not what the user
likes. The research spec itself uses new-unique yield as the reward everywhere.

Known limitation, accepted: category-level yield is measured through our own query pool,
so it inherits some vocabulary bias. The explore lane (and the daily static-taxonomy
sweep, which covers every category regardless) is the counterweight.

## Expected effect

With Health & Fitness / Tools / Entertainment / Utilities / Lifestyle dominating the
exploit lane and eligibility reallocation, a typical run should mint ~30–40 crisp
keywords/day into proven-fertile space instead of ~5/day into deserts. Success check
after a few days: `new_release:%` keywords show `new_candidates_total > 0` and minted
keepers appear outside News/Weather/Shopping.

## Testing

- Pure-logic: exploit lane favors high `kept_recent`; explore lane preserves the deficit
  rule; `eligible` filter reallocates fully; determinism; overflow trim; explore_share
  edge cases (0 and 1).
- DB-backed: `category_coverage` fills `kept_recent` with the 30-day window (old kept
  apps excluded).
- Integration (`frame_mint`): budget lands only on categories with listings; a fertile
  category with zero deficit now receives slots.
- Full suite green (`AGF_TEST_DB_URL` per repo standard).

## Out of scope

- RAW-UCB scheduler / recent-window flywheel reward (research Layer 1) — deferred until
  the arm factory produces.
- Rolling multi-day listing pool — not needed: the LLM proposer generalizes from
  `listings[:10]` regardless of count; revisit only if weighted categories persistently
  lack listings.
- Any change to `ensure_category_frame`, retirement (`dry_streak`), or the proposer.
