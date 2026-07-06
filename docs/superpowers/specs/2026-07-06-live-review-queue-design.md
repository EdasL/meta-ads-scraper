# Live-adapting review queue — design

**Date:** 2026-07-06
**Status:** Approved (approach + design sections approved in-session)

## Problem

The /review queue is static within a session. `ReviewDeck` fetches up to
2,000 cards once on mount and advances locally; taste scores (`p_like`)
come from `frontend.preference_scores`, written only when
`scripts/score_content_preferences.py` is run by hand (it is not on the
cron). Ratings therefore never influence what you see next — neither in
the session nor, in practice, across sessions.

**Goal:** the ranking adapts in-session — after rating a few apps, the
next cards served reflect that taste immediately (TikTok-style steering).

## Approach (chosen: serve-time taste scoring)

Score candidates at request time in the Go API and serve the queue in
small refills instead of one 2,000-row dump. Every refill recomputes the
taste model from the latest ratings, so each rating steers the next
cards. This also removes the offline-job staleness entirely: the queue
no longer depends on `preference_scores` being fresh.

Rejected alternatives: (B) rating-triggered rerun of the Python scoring
job — adaptation lags by job runtime, fragile subprocess plumbing;
(C) client-side re-rank of the prefetched deck — duplicates ranking
logic in TypeScript on a weaker signal.

## 1. Backend — serve-time scoring

### Port `ContentPreferenceModel` to Go

Port `src/agf/scoring/content_preference.py` faithfully into
`backend/internal/reco` (pure math, no DB/gin deps, mirroring the
existing package doc contract):

- `direction = l2normalize(mean(likedVecs) − mean(dislikedVecs))`
  (disliked centroid = zero vector when there are no dislikes).
- `raw = dot(vec, direction)` (vectors are unit-norm).
- Platt calibration: 500-iteration gradient-descent logistic of
  `label ~ a·raw + b` (lr=0.5, init a=1 b=0, non-finite → (1,0)) over
  the labeled ratings (liked=1, disliked=0).
- `p_like(raw) = sigmoid(a·raw + b)`;
  `uncertainty = 1 − |2·p_like − 1|` (as in
  `scripts/score_content_preferences.py`).
- `why = "similar to <nearest liked app by dot>"`.
- Cold start: fewer than `MIN_LIKES = 8` likes → coldstart model. In
  the serving path the coldstart substitute is
  `p_like = gem_score / 100`, `why = ""` (mirroring
  `score_content_preferences.py`'s coldstart branch — NOT the model's
  raw 0.5).
- Rating→label mapping (verified): `rating = 'like'` → positive,
  `'dislike'` → negative, neutral → neither (neutral only excludes the
  app from the queue via the already-rated filter).

The Python model stays as-is (analysis / reference); parity between the
two is pinned by a golden-vector test (same small fixture through both
implementations → same p_like to ~1e-6).

### `PreferenceQueue` reads live scores

`backend/repositories/preference_repo.go` stops reading
`frontend.preference_scores`. Per request:

1. Load the user's ratings joined to `frontend.app_embeddings`
   (`embedding::text`, parsed via existing `reco.ParsePgFloatArray`).
2. Fit the model (in-request; a few hundred vectors — sub-millisecond).
3. Score all unrated candidates that have embeddings; candidates
   without embeddings keep flowing into the "fresh" lane exactly as
   today (ranked by `gem_score`).
4. Feed `(p_like, uncertainty)` into the existing `assembleQueue`
   unchanged: 60/25/15 exploit/uncertain/fresh blend, 3-per-niche cap,
   top-40 weighted taste sampling, per-request RNG.

Response shape is unchanged (`p_like`, `why` etc. now computed live).

### Embedding cache

Candidate embeddings are immutable per app. The Go API keeps an
in-process cache (`advertiser_id → []float32`) so refills only fetch
vectors not yet cached (plus a full reload, piggybacked on the next
request when the cache is older than 10 minutes, to pick up newly
embedded apps). Warm refills are pure
in-memory math over ~2k × 768-d vectors. First request after process
start pays the bulk load once.

### `exclude` parameter

`GET /preference-queue` gains `exclude=<comma-separated advertiser_ids>`:
ids the client already holds in its buffer or has just rated. Applied in
SQL alongside the existing `NOT EXISTS app_ratings` exclusion. This
protects against the rating-write race (refill arriving before the
rating row commits) and against refills duplicating buffered cards.
Cap the list length (it is bounded by the client buffer size anyway).

## 2. Frontend — buffered feed with refill

`web/src/components/review/ReviewDeck.tsx` changes from one-shot
fetch-all to a refillable buffer:

- Initial fetch: `limit ≈ 12` (not 2,000).
- On rating: advance instantly from the buffer (existing optimistic
  UX, keyboard shortcuts unchanged), `await` the rating POST, then run
  a debounced background refill.
- Refill: `GET /preference-queue?limit=12&exclude=<current card, next
  card, buffered ids, ids rated this session>`; the response **replaces
  the unseen tail** — current card and the immediately-next card are
  kept so nothing shifts under the user; everything after them is
  replaced by the fresh ranking.
- Concurrency: at most one refill in flight; rapid arrow-key rating
  coalesces (debounce ~300 ms after the last rating; if ratings landed
  during a refill, run one more after it settles).
- Failure: a failed refill is silent — the deck keeps advancing through
  its existing buffer (today's behavior as the degraded mode).
- Empty state: "All caught up" only when the buffer is drained AND the
  latest refill returned no rows.

The buffer/refill state machine lives in a plain TypeScript module
(e.g. `web/src/lib/reviewBuffer.ts`) so it is unit-testable with
`node:test`, with `ReviewDeck` as a thin consumer.

Dev-latency note: the Mac reads prod Cloud SQL over the proxy
(~0.6–5 s); refills are background prefetch, so the visible card
advance stays instant either way.

## 3. Scope boundaries

- No UI redesign: card component, dossier lazy-load, lightbox,
  shortcuts all unchanged.
- `scripts/score_content_preferences.py` and
  `frontend.preference_scores` remain for analysis; nothing in the
  serving path reads the table afterwards. No migration needed.
- No session/recency memory across visits (unrated apps may recur
  next session, as today).
- No change to the recommended-gems path that also uses `reco`.

## 4. Testing & verification

- **Go unit:** ported model (fit/score/p_like/why/coldstart) including
  the golden-vector parity fixture vs the Python model's output.
- **Go DB-backed:** serve-time queue — ratings present → exploit lane
  ordered by live p_like; `exclude` honored; cold start (<8 likes)
  serves `p_like = gem_score/100`; unembedded candidates land in
  the fresh lane. (Run with `AGF_TEST_DB_URL=…@localhost:55433/agf_test_pg`
  / the repo's Go integration DB convention.)
- **Web unit (`node:test`):** buffer/refill module — tail replacement
  keeps current+next, single-flight + debounce coalescing, failure
  degradation, empty-state condition.
- **Live e2e gate (required):** against the real DB — rate a distinctive
  app (e.g. like a language-learning app), observe the next refill's
  ordering shift toward that niche and the rated/excluded ids absent.
  Verify one full deck session end-to-end in the browser (Playwright).
- Full suites bare (Go + web + Python) before "done".
