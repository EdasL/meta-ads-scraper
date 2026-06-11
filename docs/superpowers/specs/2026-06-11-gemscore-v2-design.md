# GemScore v2 — "worth copying" score

**Date:** 2026-06-11
**Status:** Approved by user, pending implementation plan

## Goal

Reframe the gem decision around the venture-studio thesis: a hidden gem is an
app that is **provably making money** (someone keeps paying for ads, demand is
growing) and **not yet copied by everybody** (uncrowded niche). The current
score proves "someone pays" and "not yet famous" but has no demand
confirmation and no crowdedness measure.

Research basis (deep-research run, 2026-06-11, 23 sources, adversarial
verification): every *hard numeric threshold* in this space failed
fact-checking (rank-to-revenue formulas, "25+ days = winner", revenue-per-
download tables — all refuted). Surviving signals are directional only.
**Rule: all new signals are encoded as cohort/ordinal percentiles, never
fixed constants.** User explicitly rejected grossing-chart tracking and
run-length-distribution deepening; accepted demand velocity, creative
iteration rate, and niche saturation.

## Decisions

### 1. Veto changes (`src/agf/scoring/vetoes.py`)

| Change | Detail |
|---|---|
| Remove `seasonal_spike` | Delete the STL deseasonalization veto and its machinery (`_STL_PERIOD`, `_REAL_GROWTH_EPS`, statsmodels usage if now unused). Nearly inert today (needs ≥8 weeks of history) and user rejected it in principle. |
| Remove `disguised_incumbent` | Delete the `parent_company_id` veto. Ownership is irrelevant to copy-worthiness. Known trade-off (accepted): corporate-backed apps can sustain unprofitable ads longer, so spend-persistence is weaker proof for them; footprint ceilings still bound the risk. |
| Raise ad ceiling | `MAX_ACTIVE_ADS_FOR_GEM` 50 → 80. The separate pagination-cap condition (`meta_active_ads_capped`) stays — it fires at ~150–200 ads, still consistent. |

Veto chain goes 11 → 9. Discovery triage thresholds (10k page likes,
100k installs, 5k ratings) are **unchanged**.

### 2. Formula

```
v1:  gem_score = 100 · SV^0.50 · UR^0.35 · FI^0.15
v2:  gem_score = 100 · SV^0.40 · UR^0.25 · D^0.20 · W^0.10 · FI^0.05
```

Exponents sum to 1.0. Existing mechanics unchanged: each axis clamps to
[0,1]; a missing (None) axis imputes to the 0.5 floor; a present low axis
hard-gates. FI demoted .15 → .05 because every feed app already passed the
app-link/install-CTA gates, so FI barely discriminates (user accepted).

### 3. SV: new creative-iteration component

New ads launched per week — practitioners' "they're reinvesting because it
works" signal. Computed in Python at scoring time from `public.ads.start_date`
(already fetched per-ad in `features.py:125-128` for longevity): count
launches per ISO week over the observed window, smooth with an EWMA
(alpha=0.5, same constant as the existing velocity component), cohort-
percentile the result.

New SV weights (was wow .20, ramp .35, accel .15, ewma .15, longevity .15):

| component | weight |
|---|---|
| wow | .15 |
| ramp | .30 |
| accel | .10 |
| ewma | .10 |
| ad longevity share | .15 |
| **iteration rate (new)** | **.20** |

Weights renormalize over present components, as today.

### 4. D (Demand) — new axis

Store-traction velocity from the daily `adtech.app_metrics` snapshots
(`db/schema/108_app_metrics_reviews.sql`, written by
`src/agf/enrich_full/app_refresh.py`):

| component | weight | source |
|---|---|---|
| install velocity | .55 | Play `maxInstalls` delta over trailing 28 days (`installs` column; **NULL on iOS**) |
| review velocity | .45 | `rating_count` delta over trailing 28 days (both platforms; the iOS demand proxy) |

Both cohort-percentiled. iOS-only apps: installs component is None → weights
renormalize onto review velocity. Fewer than 2 snapshots ≥7 days apart in the
window → component is None; both None → D axis is None → 0.5 floor.
Deltas use the earliest and latest snapshot inside the window (no
interpolation). Negative deltas are kept (demand shrinking should score low).
Velocity values are normalized per day elapsed between the two snapshots, so
sparse snapshot histories don't deflate the rate.

The traction *levels* keep their existing roles (Under-Radar inputs and the
100k/5k veto ceilings). Only the *growth* is new.

### 5. W (Whitespace) — new axis

The "not yet copied" dial, adapted from the Superscale competitor-proof
rubric (the one verified scoring framework; low confidence — single vendor —
hence the small .10 exponent).

- Niche key = `discovery.candidates.source_keyword`
  (`db/schema/112_discovery_candidates.sql:16`), joined via
  `kept_advertiser_id` → `public.advertisers.id`.
- Crowding(niche) = count of **distinct kept advertisers** under the same
  `source_keyword` with sustained activity (`weeks_observed ≥ 2` in
  `adtech.app_advertiser_metrics`).
- W = 1 − percentile(crowding of own niche, across all niches with ≥1
  sustained advertiser). Sole advertiser in a niche scores high; ten
  lookalikes score low.
- Advertisers with no candidates row (seeded before keyword discovery) or a
  NULL `source_keyword`: W = None → 0.5 floor. Never punished for missing
  lineage.
- An advertiser appearing under multiple keywords (multiple candidate rows):
  use the **most crowded** of its niches (conservative).

### 6. Plumbing

- **Migration 116**: add `demand REAL`, `whitespace REAL`, `niche_keyword
  TEXT`, `niche_advertiser_count INT` to `adtech.advertiser_scores`.
- **Matview migration (117)**: rebuild `frontend.emerging_scaling_apps` to
  expose the four new columns (keep `vetoed = FALSE` + DEMO exclusion +
  unique index on `advertiser_id` for CONCURRENTLY refresh).
- **Go API** (`backend/`): add the new fields to the shortlist + gem-detail
  payloads.
- **UI**: gem detail signals section shows the new axes grounded in real
  numbers only — e.g. "reviews +340 in 4 weeks", "installs +5,200 in 4
  weeks", "1 of 2 advertisers in this niche" — no derived adjectives, per
  the product-voice rules in CLAUDE.md. Discover cards unchanged in v2 of
  this work.
- Deploy order (per ops runbook): apply migrations before the new API binary;
  rebuild Go API → `mv ~/bin/agf-api` → `launchctl kickstart
  com.fielddesk.api`; scores recompute on the next worker scoring pass.

### 7. Out of scope (explicitly rejected or deferred)

- Top Grossing chart-rank tracking (user: rejected).
- Per-ad run-length distribution beyond the existing 14-day longevity share
  (user: rejected).
- Concept-level clustering for saturation (keyword join is the v2 proxy).
- Review-content/complaint mining, paywall inspection (no verified evidence;
  not requested).

### 8. Testing

- Unit tests: launch-week counting from start_dates; demand deltas from
  app_metrics fixtures (incl. iOS-only renormalization, <2-snapshot None,
  negative delta); niche-crowding SQL (multi-keyword advertiser, missing
  lineage); new formula exponents; veto changes (seasonal tests deleted,
  parent-company tests deleted, ceiling test updated to 80).
- Full Python suite, Go tests, and web tests must pass before "done".
- Sanity check on live DB: re-score, then eyeball the top-20 feed for rank
  sanity (no demo rows, no obvious incumbents) before deploy.
