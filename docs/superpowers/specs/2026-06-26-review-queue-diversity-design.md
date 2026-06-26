# Review queue diversity + freshness (Approach A)

**Date:** 2026-06-26
**Status:** Approved design, pre-implementation
**Area:** `/review` ("For You") queue — backend selection logic

## Problem

Opening `/review` keeps showing the same apps. The review's purpose is to
**train the preference algorithm** so discovery surfaces more liked gems — but
when the queue re-serves one tight cluster, judgments are spent re-confirming
near-duplicates instead of teaching new distinctions, and the variety of
training signal collapses.

### Root cause (from code, not data)

The queue *correctly* excludes already-rated apps
(`preference_repo.go:51-54` `NOT EXISTS`). Repetition comes from three missing
diversity mechanisms:

1. **Pure-exploit ranking, frozen at scoring time.**
   `scripts/score_content_preferences.py:52` sorts unrated apps purely by cosine
   similarity to the liked-centroid and writes that order as `queue_rank`
   (`:64`). Migration 133 documents a "75/25 exploit/explore" order — **it was
   never implemented.** Every visit serves the nearest neighbors of what was
   already liked → a filter bubble of the same *kind* of app.
2. **No diversity cap.** `emerging_scaling_apps` is one row per advertiser, so
   the clustering is by *kind* (`niche_keyword`), not duplicate companies. One
   niche can dominate the deck.
3. **Static rank + remount-from-0.** `ReviewDeck.tsx:22` refetches from index 0
   each visit, and `queue_rank` is identical every time, so sessions open on the
   same faces. Newly scraped apps only appear after a re-score *and* only if they
   rank high.

## Goal

Every fetch of `/review` returns a varied, non-repeating batch that blends the
user's taste with genuinely new and uncertain apps, so each judgment teaches a
new distinction. Discovery of diverse new gems is the priority; richer training
signal is the byproduct.

### Non-goals (YAGNI)

- No MMR / embedding-diversity re-ranking yet (the "B later" follow-up).
- No multi-armed bandit / Thompson sampling.
- No rating-scale change — input stays 👍 / ➖ / 👎 (`like`/`neutral`/`dislike`).
- No `preference_scores` / `app_ratings` schema change.
- No frontend change (queue is fetched as today; it just arrives diverse).

## Design

### Overview

Selection moves from **scoring time** (frozen `queue_rank`) to **serve time**
(per-request), implemented entirely in the Go API. The Python scorer
(`score_content_preferences.py`) is unchanged — it keeps producing `p_like` and
`uncertainty` per app; we simply stop trusting its `queue_rank` as the final
order.

### Component 1 — candidate query (`preference_repo.go`)

Rewrite the `PreferenceQueue` SQL to return a **tagged candidate set**, not a
final order. Two sources, unioned:

- **Scored candidates** — `frontend.preference_scores` for the user, carrying
  `p_like`, `uncertainty`, `why`, joined to the matview. Excludes rated apps
  (unchanged `NOT EXISTS`).
- **Fresh-unscored candidates** — matview apps with **no row** in
  `preference_scores` for this user (brand-new arrivals, e.g. not yet embedded).
  Surfaced via `LEFT JOIN ... WHERE ps.advertiser_id IS NULL`. These get
  `p_like = 0.5` (neutral), `uncertainty = 1.0`, `why = 'new'`. **This is the
  staleness fix** — new apps appear immediately, not after the next embed+score.

Each candidate row carries: all `mvCols`, `p_like`, `uncertainty`, `why`,
`niche_keyword`, and a `source` tag (`scored` | `fresh`).

The query returns a **generous pool** (≈ 3× the request limit, capped) ordered
so the strongest members of each bucket are present, then hands off to the
assembler. Rated apps are excluded in both branches.

### Component 2 — `assembleQueue` (new pure Go function)

A pure, DB-free function — the testable core:

```
assembleQueue(candidates, opts) -> []PreferenceCardRow
  opts: ratios{exploit, uncertain, fresh}, perNicheCap, limit, rng
```

Steps:

1. **Bucket** each candidate:
   - `exploit`  — scored, ranked by `p_like` desc.
   - `uncertain` — scored, ranked by `uncertainty` desc (p_like ≈ 0.5).
   - `fresh`    — `source = fresh` plus a random sample of remaining scored,
     shuffled.
   (An app belongs to exactly one bucket; exploit/uncertain are disjoint by
   taking the top slice of each ranking and removing claimed ids.)
2. **Blend** to `limit` at ratios **≈ 60 / 25 / 15** (exploit / uncertain /
   fresh), round-robin interleave so the deck alternates rather than front-loads
   one bucket. If a bucket underfills, redistribute its slots to the others.
3. **Diversity cap** — at most `perNicheCap` cards per `niche_keyword` in the
   final batch (default 3). Overflow cards are dropped in favor of the next
   eligible candidate.
4. **Per-request variety** — randomized tiebreak within buckets (seeded `rng`),
   so two consecutive fetches return different batches.

Ratios, `perNicheCap`, and pool multiplier are named constants (tunable).

### Component 3 — controller / route / frontend

Unchanged. `Queue` still handles `GET /api/preference-queue`. `ReviewDeck.tsx`
still fetches the batch and reviews continuously. Fresh-unscored cards render
with their matview creative/growth via the existing `attachEnrichment` pass;
their `why` shows `"new"`.

## Data flow

```
GET /api/preference-queue?user_email&limit
  -> PreferenceController.Queue
  -> repositories.PreferenceQueue(email, limit)
       SQL: scored ∪ fresh-unscored candidate pool (≈3×limit),
            rated excluded, tagged with bucket inputs + niche_keyword
  -> assembleQueue(candidates, ratios, perNicheCap, limit, rng)
       bucket -> blend 60/25/15 -> per-niche cap -> randomized order
  -> attachEnrichment (hero creative + growth, shared read tx)
  -> JSON []PreferenceCardRow
```

## Error handling

- Empty pool (no unrated apps) → empty array, frontend shows the existing
  "All caught up" state.
- Fewer candidates than `limit` → return what exists (no padding).
- A bucket empty (e.g. cold-start user, no dislikes) → its slots redistribute to
  the remaining buckets; cold-start naturally leans on `fresh` + gem_score-based
  `p_like` already produced by the scorer's coldstart path.
- DB error → unchanged 500 ("could not load the review queue.").

## Testing

**Unit (no DB) — `assembleQueue`:**
- Ratio mix: with a large balanced pool, output ≈ 60/25/15 ± rounding.
- Per-niche cap: no `niche_keyword` exceeds `perNicheCap` in output.
- No within-batch repeat: output advertiser_ids are unique.
- Fresh inclusion: fresh-unscored candidates can reach the output.
- Underfill redistribution: empty uncertain bucket → exploit/fresh absorb slots.
- Determinism under fixed `rng`; variety under rotated `rng`.

**Integration (live DB, `AGF_TEST_DB_URL=...agf_it`):**
- Two consecutive `PreferenceQueue` calls return different batches.
- No rated app appears in any batch.
- Fresh-unscored app present in pool is reachable in output.

**Live verification:** one real run against the working DB confirming the two
properties above (per global rule: a feature spanning DB + API is not done until
a live end-to-end run passes).

## Files

| Action | File | What |
|--------|------|------|
| Rewrite | `backend/repositories/preference_repo.go` | candidate-pool SQL (scored ∪ fresh), tag buckets + niche_keyword |
| Add | `backend/repositories/preference_queue.go` (or same file) | pure `assembleQueue` + constants |
| Add | `backend/repositories/preference_queue_test.go` | unit tests for `assembleQueue` |
| (maybe) | `backend/models/models.go` | add `Uncertainty`, `Source` to the pref scan row / card if surfaced |
| Unchanged | `preference_controller.go`, `routers.go`, `ReviewDeck.tsx`, `score_content_preferences.py`, migrations | — |

## Open implementation notes

- `niche_keyword` is the diversity axis (present in `mvCols`). If it is NULL for
  many rows, fall back to `cohort` for the cap. Confirm NULL rate during build.
- Confirm `uncertainty` is populated in `preference_scores` for current users
  (it is written at `score_content_preferences.py:63`).
- Pick the candidate-pool multiplier so the per-niche cap has enough overflow to
  draw from (start at 3× limit).
