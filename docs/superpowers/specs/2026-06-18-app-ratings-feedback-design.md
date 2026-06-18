# App ratings — like/neutral/dislike feedback loop

**Date:** 2026-06-18
**Status:** approved (direction)
**Goal:** rate apps in the directory (like/neutral/dislike + comment) as a labeled
dataset that Claude analyzes to tune GemScore weights, vetoes, and scraping rules.

## Scope & decisions

- **Capture:** inline in the Discover `GemTable` — a 3-state control per row + a
  comment box. Rate anything in the directory without saving it first.
- **Loop:** Claude-in-the-loop. Ratings accumulate; on request Claude analyzes them
  and proposes concrete diffs (veto threshold, GemScore weight, keyword boost/kill).
  Nothing auto-mutates the algorithm. Sparse human labels can't refit the formula;
  they surface *systematic* signals (disliked categories, incumbents that slip the
  veto, liked traits).
- **Single-user** (your `user_email`, like `saved_gems`). **Comment optional.**
- Separate from `review_decisions` (accept/reject = feed gatekeeping) and `saved_gems`
  (watchlist) — a preference rating is its own axis.

## Components

### 1. DB — `frontend.app_ratings` (migration 132)
```
PK (user_email, advertiser_id)
rating     TEXT NOT NULL CHECK (rating IN ('like','neutral','dislike'))
comment    TEXT NOT NULL DEFAULT ''
features   JSONB NOT NULL DEFAULT '{}'   -- snapshot at rating time
rated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
```
`features` freezes what the app looked like when judged (scoring runs 24/7, so the
live row drifts): gem_score, scaling_velocity, under_radar, funnel_intent, demand,
whitespace, active_ad_count, cohort, installs, rating_count, page_like_count,
has_funnel. Snapshotted server-side from `frontend.emerging_scaling_apps` (+ join
`public.advertisers` for page_like_count) at write time. Upsert overwrites
rating/comment/features/updated_at; first write sets rated_at. A removed rating
(set to none) deletes the row.

### 2. API (Go, mirrors saved-gems: backend/controllers/ + repositories/ + models/)
- `POST /api/ratings/{advertiser_id}` — body `{user_email, rating, comment}`. Upsert;
  snapshot features from the matview. `rating:"none"` deletes the row (un-rate).
- `GET /api/ratings?user_email=...` — `{advertiser_id: {rating, comment}}` for the
  current user. Kept separate from the shortlist query so shortlist caching is
  untouched; merged into the table client-side.
- Models: `RatingIn {user_email, rating, comment}`. Repo: `UpsertRating`,
  `DeleteRating`, `ListRatings`. Follow the saved_gems_repo.go pattern exactly.

### 3. Web (web/src/components/discover/)
- `RatingControl` component: 👍 / ➖ / 👎 (active state highlighted) + a note icon
  toggling an inline comment textarea that saves on blur/enter. Optimistic update,
  POST to `/api/ratings/{id}`; revert + toast on failure.
- Mount it in a `GemTable` row (new column). Fetch the user's ratings once
  (`GET /api/ratings`) in `DiscoverFeed`, pass the per-row rating down.
- Plain, professional copy per CLAUDE.md (no novelty labels).

### 4. Analysis (scripts/analyze_ratings.py)
Read `frontend.app_ratings`, break likes vs dislikes down by trait from the `features`
snapshot: cohort, gem_score band, funnel-vs-not, incumbent signals (page_like_count,
active_ad_count, installs), each axis. Print counts + the sharpest contrasts (e.g.
"10/12 dislikes are games", "you dislike 70-80 scores with >50k likes"). Output is a
report for Claude to act on — it does NOT mutate scoring constants.

## Data flow
rate in table → POST /api/ratings/{id} (snapshot features) → app_ratings →
analyze_ratings.py report → Claude proposes veto/weight/keyword diffs → you approve →
edit vetoes.py / gemscore.py / taxonomy.py → rescore / next sweep.

## Testing
- Go: handler + repo tests for upsert / un-rate / list, snapshot populated (mirror
  saved_gems tests).
- DB: migration applies; CHECK rejects bad rating.
- Web: RatingControl renders state, optimistic update, comment save (component test if
  a harness exists; else manual).
- analyze_ratings.py: unit test the aggregation on seeded ratings.
- Full pytest green with AGF_TEST_DB_URL set.

## Out of scope
- Multi-user / multi-reviewer.
- Automated weight-fitting or auto-applied vetoes (Claude proposes; human approves).
- Rating from the detail page or a dedicated review screen (inline only, per decision).
- Touching the existing review_decisions / saved_gems flows.
