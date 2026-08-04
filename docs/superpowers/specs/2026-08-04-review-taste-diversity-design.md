# Review queue: decayed topic cap + multi-centroid taste + neutrals as weak negatives

Date: 2026-08-04. Status: draft (measurement section pending prod probe).

## Problem

The founder reports the review deck "shows very similar apps and not the ones I
like". Verified mechanics behind it:

1. **Exploit = one cone.** `ContentPreferenceModel` (Python canon,
   `src/agf/scoring/content_preference.py`) compresses all likes into a single
   liked-minus-disliked direction; 60% of every 12-card deck is sampled from the
   top-40 apps by similarity to that one direction
   (`backend/repositories/preference_queue.go:16-26`).
2. **The diversity cap has no memory.** `nicheCount` lives inside one
   `assembleQueue` call; every refill resets it, so one topic can supply 3 of 12
   cards in every batch indefinitely (`preference_queue.go:159-176`).
3. **Skips teach nothing.** `neutral` ratings remove an app from candidates but
   never touch the model (`preference_repo.go:91-96` handles only like/dislike),
   so dismissing a cluster 20 times leaves the model still pointed at it.

## Fixes (three, shipped as one increment)

### 1. Decayed per-topic cap (server-side, zero new state)

The deck is rate-then-advance, so the user's recent ratings ARE the served-card
trail. Pre-seed the niche-cap counter with the user's ratings from the last
`AGF_REVIEW_CAP_HOURS` (default 24h), keyed by the same `nicheKey` chain
(topic → niche_keyword → cohort):

- `PreferenceQueue`'s rated-rows SELECT gains `rated_at` + the topic-key columns
  (LEFT JOIN `frontend.app_topics`; matview LEFT JOIN already present for
  names — extend it to pull `niche_keyword`/`cohort`).
- Go computes seed counts for ratings with `rated_at >= now()-window` and passes
  them via `queueOpts.SeedNicheCounts map[string]int`.
- `assembleQueue` initialises `nicheCount` from the seed map.
- **Starvation guard:** a final fill pass that ignores the niche cap (still
  deduped) so a heavily-seeded cap can never underfill the deck. Ordering:
  targets pass → underfill-ignoring-targets pass (existing) → cap-free pass (new).

Effect: a topic that supplied 3 cards in the last 24h stops appearing until the
window rolls off — the cap becomes session-persistent and survives reloads.

Knobs: `AGF_REVIEW_CAP_HOURS` (default 24; 0 disables seeding → old behavior).

### 2. Multi-centroid taste model (both languages, parity preserved)

Replace the single direction with K cluster directions; score = max over
clusters. Python stays canonical; Go port pinned by regenerated golden vectors.

- **Inputs get stable identity:** `LabeledVec` gains `ID int64`
  (advertiser_id); Python liked/disliked tuples already carry it. Sort by ID
  before clustering so both languages see the same order.
- **Deterministic clustering (no RNG, portable):** greedy farthest-point init
  (c1 = lowest-ID vector; each next center = the point with the smallest
  max-dot to chosen centers), then Lloyd's iterations (assign to
  highest-dot centroid, ties → lowest cluster index; recompute means; stop on
  stable assignment or 50 iterations).
- **K policy:** K = clamp(nLikes / AGF_TASTE_LIKES_PER_CENTROID, 1,
  AGF_TASTE_CENTROIDS_MAX). Defaults pending measurement (placeholder: 12 per
  centroid, max 4). Degenerate empty clusters are dropped.
- **Scoring:** `dir_k = l2norm(mean(cluster_k) − dis_c)` (shared disliked
  centroid); `raw(vec) = max_k dot(vec, dir_k)`; single Platt calibration fit
  on the labeled set's raw (max) scores. `why` unchanged (nearest liked app).
- Version string bumps to `content-sim-v2`.
- Consumers updated: Go serve path (`FitContentModel` signature),
  `scripts/score_content_preferences.py` (Discover for-you gets the fix free
  via the shared class).

### 3. Neutrals as weak negatives (both languages)

- Loaders pass neutral-rated vectors: Go `PreferenceQueue` rating switch gains
  a `neutral` case; Python `score_user` builds a `neutral` list.
- `fit(liked, disliked, neutral)`: disliked centroid becomes the weighted mean
  `(Σ disliked + w·Σ neutral) / (nDis + w·nNeut)` with
  `w = AGF_NEUTRAL_WEIGHT` (default 0.25).
- Neutrals are **excluded from Platt calibration** — they are not 0-labels,
  they only pull the negative centroid. Keeps calibration semantics honest.

## Non-goals

- No schema/migration changes (rated_at + app_topics already exist).
- No client changes (web keeps sending the same request; deck size unchanged).
- preference_scores/Discover job cadence unchanged (it inherits the model fix).
- Crowding demotion, 60/25/15 blend, buffer/refill logic all unchanged.

## Measurement (prod, 2026-08-04)

PENDING — probe running. Will pin: rating counts by value, liked-embedding
cluster structure (k-means inertia/silhouette → K policy defaults), candidate
topic concentration (validates cap effect), neutral topic concentration
(validates weak-negative effect).

## Test plan

- Golden parity: extend `goldenFixture` with enough likes to form ≥2 clusters +
  neutrals; regenerate golden values from Python; Go asserts equality.
- Python unit tests: clustering determinism (order-shuffled input → same result
  after ID sort), K policy bounds, neutral weighting math, coldstart unchanged.
- Go `assembleQueue` tests: seeded niche counts suppress a topic; cap-free
  final pass prevents underfill; window=0 reproduces old behavior.
- Go repo test: rated-rows query returns rated_at + topic keys.
- Live gate: prod-shaped local run — serve the queue for the founder's ratings
  snapshot, assert (a) no topic exceeds cap across two consecutive simulated
  refills with ratings in between, (b) deck fills to limit, (c) p_like spread
  across ≥2 like-clusters' nearest apps.
