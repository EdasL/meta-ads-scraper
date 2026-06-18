# Spec 2 — Content-based preference model + keyword mining from likes

Written 2026-06-18. Successor to Spec 1 (review-page-preference-ranking). All work
lands on `main` (solo local repo). Worker + Go API run on this Mac.

## Problem

Spec 1 ranks apps for `/review` and Discover "For you" with a per-user
`statsmodels.Logit` over **6 hand-engineered axes** (`has_funnel, whitespace,
funnel_intent, scaling_velocity, under_radar, demand`). Those axes discard *what
the app actually is*: "AI Vocal Remover" and "calorie tracker" look near-identical
on the axes yet are semantically opposite, and the user's real taste runs on
content ("AI creator tools yes / faith + calorie trackers no"), not axis proxies.

We want the model to learn taste from **content** (what the app does, the problem
it solves, category, store copy, ad copy), and to use the same representation to
**steer scraping** toward taste-aligned apps.

## Decisions (locked in brainstorm)

| Decision | Choice |
|---|---|
| Scope | **Both together**: content-based ranking **and** keyword mining from likes in one spec. |
| Discovery lever | **Keyword mining from liked apps only.** (Triage-prior, corpus neighbor re-rank, funnel-sharpening deferred.) |
| Embedding source | **Gemini `gemini-embedding-001`**, `outputDimensionality=768`, L2-renormalized. Reuses `GEMINI_API_KEY`. **Verified live this session** (key works for embeddings; native dim 3072 → we request 768). |
| Ranking model | **Similarity-to-likes** (centroid of liked − disliked, cosine rank). Robust few-shot, zero training, no overfit. Only 36 likes / 455 labels. |
| 6-axis Logit | **Kept, but moved off the serving path** into an offline GemScore-tuning analysis. Two models, two jobs. |
| Storage | `float4[]` column, numpy at read time. **No pgvector** (~1k apps). |
| Keyword extraction | **Gemini, a CHEAP flash-lite-tier model** (easy extraction task) → candidate search phrases. Human-approval gate before phrases go live. |
| Filter-bubble guard | Mined phrases are **additive**; the existing breadth sweep (taxonomy + new-releases expansion) stays as the explore budget. |

## Grounded facts (checked live this session)

- Content available: 976/981 apps have store descriptions (`apps.description`,
  migration 103); 50,756/66,641 creatives have ad copy (`public.creatives.body_text`);
  `tagline`, category/genre present.
- Labels: **455 ratings — 36 like / 156 neutral / 263 dislike** (~8% like-rate).
- Migrations live in `db/schema/NNN_*.sql`; latest is **134** → new ones are **135, 136**.
- `frontend.preference_scores(user_email, advertiser_id, p_like, uncertainty,
  queue_rank, why, model_version, scored_at)` PK `(user_email, advertiser_id)` (migration 133).
- `frontend.app_ratings(user_email, advertiser_id, rating, comment, features, rated_at, updated_at)`;
  `rating ∈ {like, neutral, dislike}` (migration 132).
- `discovery.dynamic_keywords(id, keyword, source, status, sweeps_used,
  new_candidates_total, created_at, last_swept_at, kind)`. Observed `status ∈
  {active, trial, retired}`, `source ∈ {gem_copy, funnel_copy}`, `kind ∈ {app, funnel}`.
  The sweep selects `WHERE status IN ('trial','active')` — so a held status is a natural gate.
- **Existing** `src/agf/discovery/keyword_mining.py` is the *flywheel* miner (pure
  TF-IDF cross-advertiser recurrence over scored gems → trial keywords); promotion in
  `src/agf/db/dynamic_keywords.py`. The taste-driven miner here is **separate** and
  must not collide with it.
- statsmodels gotcha (carried from Spec 1): `import statsmodels.api` is broken on this
  env; import `Logit` directly. Relevant only to the retained axis analysis.

## Architecture

Five units, each independently testable.

### 1. Content embedding pipeline — `src/agf/scoring/embeddings.py` + `scripts/embed_apps.py`

- **`build_embed_text(app) -> str`** (pure): `name + tagline + category +
  truncated store description + top-K deduped ad copy` (K configurable, ad copy
  ordered by reach/recency, deduped). Deterministic, unit-testable.
- **`embed_texts(texts) -> list[list[float]]`**: batches to Gemini
  `gemini-embedding-001` with `outputDimensionality=768`; **L2-renormalizes** each
  vector (reduced-dim Gemini output is not unit-norm). Network call isolated here.
- **`content_hash(text)`**: stable hash → only re-embed new/changed apps.
- `scripts/embed_apps.py`: idempotent — reads apps needing embedding (new or
  `content_hash` changed), embeds, upserts `frontend.app_embeddings`.
- Storage (**migration 135**):
  `frontend.app_embeddings(advertiser_id bigint PK, embedding float4[],
  content_hash text, dim int, model text, embedded_at timestamptz)`.

### 2. Similarity-to-likes ranking — `src/agf/scoring/content_preference.py`

- Load `app_embeddings` for the user's rated apps. `liked = like`, `disliked =
  dislike` (neutral excluded; matches the "like vs rest" intent but uses dislikes
  as the contrast pole). Pure numpy.
- **Direction**: `dir = l2norm(mean(liked_vecs) - mean(disliked_vecs))`.
- **Score**: `raw = cosine(app_emb, dir)` for every app.
- **Calibrated `p_like`**: `sigmoid(a * raw + b)` with `a, b` fit by a 1-D logistic
  regression of label on `raw` over rated apps (cheap, keeps the column
  probability-shaped for the "For you" sort and the card). Falls back to a fixed
  scale if the 1-D fit fails to converge.
- **`queue_rank`**: dense rank of unrated apps by `raw` desc.
- **`why`**: nearest liked app by cosine — `"similar to <liked app name>"`.
- **Cold-start**: `< MIN_LIKES` (reuse Spec 1's threshold) → fall back to GemScore
  order, `why = "ranked by GemScore (rate more to personalize)"`.
- Writes `frontend.preference_scores` with `model_version = 'content-sim-v1'`.
  **This replaces the 6-axis Logit in the serving path** (same table, same Go
  endpoint — no API or web change required).

### 3. Retained 6-axis analysis — `scripts/analyze_axis_coefficients.py`

- The Spec 1 `statsmodels.Logit` fit, **moved off serving**. Prints/persists the
  6 coefficients as the GemScore-reweight proposal (interpretable knob). Does **not**
  write `preference_scores`. `src/agf/scoring/preference.py` stays as its library.

### 4. Taste-driven keyword mining — `src/agf/discovery/liked_keyword_mining.py` + `scripts/mine_liked_keywords.py`

- **`gather_liked_content(user_email) -> list[LikedApp]`** (DB read): name +
  description + top ad copy for each `like`-rated app.
- **`propose_phrases(liked_apps) -> list[Candidate]`**: one Gemini call to a
  **cheap flash-lite-tier model** with a `ResponseSchema`, asking for N
  Meta-Ad-Library-style search phrases that would find *similar* apps, each with a
  one-line `rationale` and the `inspired_by` advertiser id(s). Network call isolated;
  mocked in unit tests.
- **`dedup_against_existing(candidates, taxonomy, dynamic_keywords)`** (pure):
  drop phrases already in the static taxonomy or `dynamic_keywords` (any status).
- `scripts/mine_liked_keywords.py`: gather → propose → dedup → insert into
  `discovery.dynamic_keywords` as **`status='pending'`**, `source='liked-mining'`,
  `kind='app'`, with `rationale` + `inspired_by` (new columns, **migration 136**).
  The sweep's `status IN ('trial','active')` filter **ignores `pending`** → held.
  Migration 136 must also **replace `dynamic_keywords_status_check`** — it currently
  allows only `('trial','active','retired')` and would reject `pending`; drop and
  recreate it including `'pending'`.
- **Human-approval gate**: `scripts/review_mined_keywords.py` lists `pending`
  rows (phrase + rationale + inspired-by app names) and, on approval, flips
  `status='pending' → 'trial'` to admit them into the existing flywheel
  (trial → active/retired by yield, already built). Filter-bubble guard: existing
  breadth sweep untouched.

### 5. Worker wiring — `src/agf/worker` (existing preference stage)

- Each cycle, after `run_scoring`: **embed new/changed apps** (`AGF_EMBED_APPS`,
  default on) → **content-similarity score** (`AGF_SCORE_PREFERENCES`, stays on,
  now backed by `content_preference.py`).
- Keyword mining is **on-demand only** (manual `scripts/mine_liked_keywords.py` +
  review), not a per-cycle stage — it needs the human gate and changes slowly.

## Data flow

```
enrich app ─► embed_apps (changed only) ─► app_embeddings
                                              │
app_ratings ──────────────────────────────────┤
                                              ▼
                            content_preference: dir = like−dislike centroid
                                              │ cosine rank + calibrated p_like
                                              ▼
                              preference_scores (content-sim-v1)
                                              │
                                  /review deck + Discover "For you"

(periodic, manual)
liked apps' copy ─► Gemini cheap model ─► candidate phrases (+rationale)
   ─► dynamic_keywords status='pending' ─► [human approves] ─► status='trial'
   ─► existing sweep finds taste-aligned apps ─► user rates ─► loop
```

## Error handling

- **Gemini embed failure / partial batch**: embed is idempotent and incremental;
  failed apps keep their old (or no) embedding and are retried next cycle. A whole
  batch failing logs and aborts the embed stage without touching scoring (last
  good `preference_scores` remains served).
- **Gemini mining failure**: script exits non-zero, writes nothing (no partial
  `pending` rows); safe to re-run.
- **Too few likes**: cold-start fallback to GemScore (no crash, honest `why`).
- **Dimension mismatch** (e.g. model returns 3072): `embed_texts` asserts `len ==
  768` after the call and renormalizes; mismatch raises before any DB write.

## Testing

- **Pure, unit-tested with synthetic vectors / fixtures**: `build_embed_text`,
  `content_hash`, centroid/cosine/`dir` math, `p_like` calibration shape, nearest-liked
  `why`, cold-start fallback, `dedup_against_existing`, the mining prompt's
  schema-parsing.
- **Gemini calls mocked** in unit tests; **one live e2e probe each** for embeddings
  (done this session) and mining.
- **DB / IT** (`AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it`, a skip
  is NOT a pass): migrations 135/136 apply; `embed_apps` upsert + change detection;
  `content_preference` writes `preference_scores`; mining inserts `pending` and the
  sweep selector excludes it; review flips `pending → trial`.
- **Live e2e**: embed a real corpus subset, score, eyeball `/review` ordering moves
  toward AI-creator-type apps and away from faith/calorie; run mining, inspect the
  `pending` candidates by hand.
- **Full suite green** before "done" — `AGF_TEST_DB_URL=… python -m pytest -q`,
  drive to 0 failed (do not flag-and-skip pre-existing failures).

## Out of scope (deferred to a later spec)

- Triage prior at candidate time (embed ad `body_text` pre-enrich to steer the
  enrich budget).
- Semantic neighbor re-rank over the whole corpus beyond what the ranking model gives.
- Funnel-detection sharpening (real web2app vs store/MMP bounce).
- Closing the keyword loop by like-rate weighting / auto-retire (the existing
  flywheel already does yield-based retire; taste-weighting is a follow-up).
- pgvector (not needed at ~1k apps).

## Build sequence

1. Migration 135 (`app_embeddings`) + 136 (`dynamic_keywords` add `rationale`,
   `inspired_by` + extend `status` check to include `pending`); IT tests.
2. `embeddings.py` + `scripts/embed_apps.py` (pure builder TDD; live embed probe).
3. `content_preference.py` + swap the scorer the worker calls; IT write test.
4. `analyze_axis_coefficients.py` (carve the Logit off serving).
5. `liked_keyword_mining.py` + `scripts/mine_liked_keywords.py` +
   `scripts/review_mined_keywords.py`; mining live probe + review gate IT test.
6. Worker wiring (`AGF_EMBED_APPS`); live e2e; full suite; rescore live; commit per unit.
