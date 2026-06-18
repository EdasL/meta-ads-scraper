# Design — Review page + content-based preference ranking

Written 2026-06-18. Status: **approved, ready for implementation plan.** On `main`
(solo repo, commit straight to main). This is **Spec 1 of 2**; Spec 2 (wiring the
same preference signals into the discovery/scraping pipeline) is deferred — see §8.

## 1. Problem & goal

The Discover table (`GemTable`) is hard to review one app at a time. The user wants a
**TikTok-style, one-app-at-a-time review page** that surfaces apps they're most likely
to engage with, and gets smarter as they rate. The 134 ratings collected so far show the
user's taste is **not** well captured by GemScore (funnel presence and low saturation
dominate their likes, but GemScore weights both at ≈0).

Two jobs from one model:
1. **Rank** a review feed by predicted preference (`P(like)`).
2. **Tune GemScore** — the model's coefficients quantify how much each axis drives the
   user's preference, producing a reweight proposal (Claude-in-the-loop, not auto-applied).

## 2. Scope

**In scope:** a per-user preference model (logistic regression), a precomputed score
table, a Go read endpoint that serves a ranked review queue, and the `/review` web page.

**Out of scope (Spec 2):** feeding preference back into discovery/scraping (triage
prioritization, keyword-flywheel bias, candidate vetoes). **Out of scope (later):**
collaborative filtering, touch-swipe gestures, auto-applying the GemScore reweight.

## 3. Model

**Algorithm: logistic regression** via `statsmodels.Logit` — already a dependency
(numpy/scipy/statsmodels in `requirements.txt`); **no new package**, no sklearn.

Chosen because: with only ~134 labels it is the right complexity (richer models overfit);
its coefficients are interpretable and double as the GemScore-tuning proposal; it outputs
a calibrated-ish `P(like)` we can rank and interleave for explore/exploit.

### 3.1 Multi-user from day one
The system will have multiple users later. Content-based per-user models are **not**
throwaway — they are the cold-start half every hybrid recommender keeps. So:
- Fit **one model per user**, on that user's own labels (`app_ratings` is already keyed
  `(user_email, advertiser_id)`).
- Score table is keyed `(user_email, advertiser_id)`.
- Collaborative filtering is a **future project**, added on top when there is real user
  overlap — it does not replace this model.

### 3.2 Target (neutral handling)
**Like vs rest:** `like = 1`, `neutral + dislike = 0`. Uses all 134 labels and directly
optimizes "surface what I'll like." Imbalance (18/116) is acceptable because we use the
model only for *ranking* (relative `P(like)`), not classification at a fixed threshold.

### 3.3 Features
Start small (134 rows can't support many), read from the `app_ratings.features` JSONB
snapshot, standardized, **L1-regularized fit** (`fit_regularized`) so weak features go to
zero:

1. `has_funnel` (binary) — #1 like driver
2. `whitespace` — #1 dislike driver (saturation)
3. `funnel_intent`
4. `scaling_velocity`
5. `under_radar`
6. `demand`

**Excluded for now:** the 27 `cohort` categories as dummies (would overfit 134 rows);
`gem_score` itself (it is a function of the other axes — including it double-counts and
muddies the coefficients we want for tuning). Features can grow as labels accumulate.

### 3.4 Cold-start fallback
A user with too few labels (threshold ~30–50; the current user is past it at 134) falls
back to a **funnel + whitespace heuristic** ranking until they have rated enough. The
model refits as labels grow. This is also the path for brand-new future users.

### 3.5 Outputs
Per fit, for each scored app: `p_like` and `uncertainty` (= `1 − |2·p_like − 1|`, peaks
at `p_like = 0.5`). Plus the **coefficient table**, logged as the GemScore-reweight
proposal (e.g. "funnel + whitespace dominate preference but GemScore weights them ≈0").

## 4. Architecture & data flow

Mirrors the existing GemScore pattern: **Python writes scores → DB table → Go reads.**
The model stays in Python (statsmodels); Go is a thin reader. Rejected alternatives:
scoring at request time in Go (would reimplement LR or require a Python service);
client-side ranking in JS (can't run statsmodels; model is shared with GemScore tuning).

```
worker tick ──> scripts/score_preferences.py
                  ├─ for each user with ratings:
                  │    ├─ fit statsmodels Logit (L1) on that user's app_ratings labels+features
                  │    ├─ score every advertiser in frontend.emerging_scaling_apps → p_like, uncertainty
                  │    │    (or cold-start heuristic if < ~30–50 labels)
                  │    ├─ compute queue_rank: 75/25 exploit/explore interleave
                  │    │    (exploit list = p_like desc; explore list = uncertainty desc)
                  │    └─ upsert frontend.preference_scores rows for that user
                  └─ log coefficient table (GemScore-tuning proposal)

Go API: GET /api/review-queue?user_email=...
          ├─ frontend.emerging_scaling_apps ⋈ preference_scores (this user)
          ├─ LEFT JOIN app_ratings (this user) WHERE rating IS NULL   -- exclude already-rated
          ├─ ORDER BY queue_rank
          ├─ attach hero ad creative per app (active → meta_rank order; fallback icon_url)
          └─ return card payload (matview row + hero creative + p_like + why)

Web: /review page — full-screen card, L/N/D + comment, keyboard shortcuts.
       Rating reuses the existing setRating() POST. Advance to next card on rating.
```

- The **explore/exploit blend is precomputed in Python** (`queue_rank`), so Go stays a
  thin reader and the cold-start fallback lives next to the model.
- `queue_rank` is a stable global order per user; Go filters out already-rated apps at
  read time, so the queue shrinks as the user swipes and re-ranks on the next script run.
- **Start more exploratory, drift toward exploit** as the label count grows (the explore
  share can taper with label count; 75/25 is the steady-state target).

## 5. Data model

New table:

```sql
CREATE TABLE frontend.preference_scores (
    user_email    TEXT   NOT NULL,
    advertiser_id BIGINT NOT NULL,
    p_like        DOUBLE PRECISION NOT NULL,
    uncertainty   DOUBLE PRECISION NOT NULL,
    queue_rank    INTEGER NOT NULL,
    model_version TEXT   NOT NULL DEFAULT '',
    scored_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_email, advertiser_id)
);
CREATE INDEX ON frontend.preference_scores (user_email, queue_rank);
```

Migration: `db/schema/133_preference_scores.sql` (132 is the latest used).

## 6. API

`GET /api/review-queue?user_email=<email>&limit=<n>` (default limit ~50).

- Reader joins `emerging_scaling_apps` ⋈ `preference_scores` for the user, excludes apps
  the user already rated, orders by `queue_rank`, attaches the hero creative.
- Response: array of card rows. Card fields (from the matview + score + creative):
  `advertiser_id, app_name, cohort, tagline, icon_url, hero_creative_url, gem_score,
  scaling_velocity, active_ad_count, pct_change, new_ads_4w, installs, rating_count,
  funnel_url, store, store_id, p_like, why` (`why` = short string from top model features).
- Rating uses the **existing** `POST /api/ratings/{advertiser_id}` — no new write endpoint.

Files (mirror the ratings/shortlist pattern): `backend/controllers/review_queue_controller.go`,
`backend/repositories/review_queue_repo.go`, route in `backend/routers/routers.go`.

## 7. Web page (`/review`)

Full-screen, one app at a time. Layout:

```
[ ← Discover ]                          card 1 / 47
        ┌────────────────────────┐
        │   HERO AD CREATIVE     │   (best ad; fallback to icon)
        └────────────────────────┘
   Bloom        Health & Fitness
   "Daily habit tracker for ..."
   ● 12 active ads   ▲ +40% 4w
   GemScore 68    🔗 Quiz funnel
   Why shown: has funnel · low saturation
      👎 Dislike    ➖ Neutral    👍 Like
      [ + add a note ]
   ←  dislike     ↓ neutral     →  like
```

- **Hero creative**: top ad per app (active → meta_rank, same order the dossier reel
  uses), fallback `icon_url`. The TikTok-like visual hook.
- **Signals**: only grounded data — active ad count, 4-week growth, GemScore, funnel link.
  No re-derived trend words (per `CLAUDE.md` product-copy rules).
- **Why shown**: short, from the model's top contributing features. Honest, plain.
- **Rating**: reuses `setRating()` (like/neutral/dislike + optional comment). On rating,
  advance to the next card; queue re-ranks on the next script run.
- **Keyboard**: ← dislike, → like, ↓ / space neutral; a key to expand the note; a link to
  the existing **dossier** page for deep dives (no new deep-dive UI).
- **Queue position** indicator (`card N / total`).
- **v1 = buttons + keyboard only** (no swipe-drag); touch-swipe deferred.

Files (mirror Discover): a `/review` route + `ReviewDeck`/`ReviewCard` components under
`web/src/`, plus `getReviewQueue()` in `web/src/lib/api.ts`.

## 8. Spec 2 (deferred) — preference feedback into discovery

Not built here; depends entirely on the artifacts this spec produces (`p_like`,
coefficients). Planned mechanisms:
1. **Triage prioritization** — enrich predicted-likeable candidates first (enrichment is
   the metered-proxy bottleneck).
2. **Keyword-flywheel bias** — weight discovery toward niches of liked apps; downrank
   lineages producing disliked/saturated cohorts.
3. **Rating-surfaced vetoes** — physical-products, fake store-bounce funnels, duplicates
   become candidate filters (the "bugs" track).

**Caution carried forward:** a preference signal in *discovery* is a stronger filter
bubble than in the feed — Spec 2 must reserve its own discovery **explore budget** so we
keep finding new niches the model has never seen.

## 9. Testing

- **Python:** unit tests for `score_preferences.py` — target collapse (like vs rest),
  cold-start fallback path, `uncertainty` formula, queue interleave ratio, per-user
  isolation. DB-backed tests require `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it`
  (they **skip silently** without it — grep the pytest summary for "skipped"; a skip is
  not a pass).
- **Go:** `review_queue_repo` test — excludes already-rated, orders by `queue_rank`,
  attaches hero creative. Use `AGF_IT_DB_URL` (same DSN). Re-run with `-count=1` /
  `go clean -testcache` before trusting green (a cached/FK-fragile pass burned us before).
- **End-to-end:** live run against the real DB + API + browser — fit the model, serve the
  queue, rate a card, confirm it advances and persists and drops out of the queue.

## 10. Verification gate

Not done until a live end-to-end run passes: `score_preferences.py` writes
`preference_scores` for the user → `/api/review-queue` returns a ranked, already-rated-
excluded queue with hero creatives → the `/review` page renders a card, rating it
advances + persists. Python + Go test suites green (with the DB env vars set, summaries
checked for skips).
