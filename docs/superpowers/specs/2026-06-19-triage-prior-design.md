# Spec 2b (increment 1) — Triage prior: taste-prioritized enrich budget

Written 2026-06-19. First increment of Spec 2b (semantic discovery). Successor to
Spec 2 (content-preference ranking). All work on `main`. Worker + Go API on this Mac.

## Problem

The daily **sweep** finds thousands of advertiser candidates; **triage** keeps the
plausible ones (~800 `status='kept'`); **enrich** then scrapes each kept
advertiser's full ad history + store listing — the **expensive step** (~14 Meta
requests/app through the paid DataImpulse proxy). The per-cycle enrich budget only
covers a slice, so most kept candidates wait. Today they're enriched **FIFO**
(`kept_advertiser_ids` orders by `MIN(triaged_at)`) — blind to whether the app
matches the user's taste, so the budget burns on category leaders and off-taste apps.

**Goal:** before spending the metered enrich budget, make a cheap *prior* guess of
how much the user will like each candidate (cosine of its content embedding to the
user's liked centroid) and enrich the most-likeable candidates first — with an
explore reserve so new niches still surface. Changes only the *order the budget is
spent*, not *what's searched* (low filter-bubble risk).

## Decisions (locked in brainstorm)

| Decision | Choice |
|---|---|
| First Spec 2b lever | **Triage prior** (steer the enrich budget). Other levers (keyword-loop weighting, funnel sharpening, semantic re-rank) are later increments. |
| Candidate content | **Capture ad copy** (option A): add `discovery.candidates.body_text` (mirrors `funnel_candidates.body_text`), captured at sweep; embed `page_name + source_keyword + ad copy`. |
| Prior signal | `priority = cosine(candidate_emb, liked_dir)` where `liked_dir` is the Spec 2 `ContentPreferenceModel` direction (`mean(liked) − mean(disliked)`) for the configured user. |
| Filter-bubble guard | Epsilon-greedy explore reserve `AGF_TRIAGE_EXPLORE_FRAC=0.25`: 25% of enrich slots go oldest-first (taste-blind); 75% highest-priority. |
| Cold-start | < `MIN_LIKES` likes (or no embeddings) → full FIFO (current behavior, zero regression). |
| Embedding reuse | Same Gemini `gemini-embedding-001` @768d + `embeddings.embed_texts` from Spec 2; cache in `discovery.candidate_embeddings`, incremental by `content_hash`. |
| Whose taste | The configured single user `AGF_TRIAGE_USER` (default `founder@fielddesk.local`) — solo product. |

## Grounded facts (checked live)

- `discovery.candidates(id, page_id, page_name, page_like_count, store, store_id,
  link_url, cta_type, ad_start_date, source_keyword, technique, times_seen,
  first_seen_at, last_seen_at, status, drop_reason, triaged_at, kept_advertiser_id)`
  — **no ad copy stored today**. 2020 rows, 800 kept.
- `discovery.funnel_candidates` **already has `body_text`** — precedent for capturing
  ad copy at sweep. `upsert_sweep_candidate` (`src/agf/db/candidates.py:10`) inserts
  the candidate row and does NOT capture body_text yet.
- `kept_advertiser_ids` (`candidates.py:47`) → `ORDER BY MIN(triaged_at)` (FIFO).
- Worker enrich selection (`src/agf/workers/main.py:642-650`): `enrich_limit =
  (enrich_budget - enrich_budget//2)//14`; `kept = kept_advertiser_ids(conn)`;
  `cand_ids = kept + eligible_gem_ids(conn, min_score)`; `cand_ids[:enrich_limit]`
  is what gets enriched. So the **order of `kept` decides who gets the budget**.
- Spec 2 shipped: `frontend.app_embeddings` (768d), `ContentPreferenceModel`
  (`src/agf/scoring/content_preference.py`, exposes the direction via `_dir`),
  `embeddings.embed_texts` / `build_embed_text`, shared `agf.net.http_backoff.post_json`.
- `frontend.app_ratings(user_email, advertiser_id, rating)` — 36 like / 156 neutral / 263 dislike.

## Architecture

Six units, each independently testable.

### 1. Migrations — `db/schema/137_candidate_body_text.sql`, `138_candidate_embeddings.sql`
- `ALTER TABLE discovery.candidates ADD COLUMN IF NOT EXISTS body_text TEXT;`
- `CREATE TABLE discovery.candidate_embeddings(candidate_id BIGINT PRIMARY KEY
  REFERENCES discovery.candidates(id) ON DELETE CASCADE, embedding REAL[] NOT NULL,
  content_hash TEXT NOT NULL, dim INT NOT NULL, model TEXT NOT NULL DEFAULT '',
  embedded_at TIMESTAMPTZ NOT NULL DEFAULT now());`

### 2. Sweep capture — `src/agf/db/candidates.py` + the sweep row builder
- Extend `upsert_sweep_candidate` to insert `body_text` (set on first insert; the
  ON CONFLICT bump leaves an existing non-null body_text intact, fills if was null).
- The sweep code that builds the candidate `row` dict (the app-candidate path in the
  discovery harness) passes the top ad's `body_text` (it already holds the AdRecord).
  Pure mapping change; covered by the existing sweep tests + a new assertion.

### 3. Candidate embedding — `src/agf/discovery/candidate_embed.py` + `scripts/embed_candidates.py`
- Pure `build_candidate_text(page_name, source_keyword, body_text) -> str`
  (name + keyword + ad copy; reuse the dedupe/truncation spirit of `build_embed_text`).
- `embed_candidates(conn, *, embed_fn=embed_texts, commit_every=25, limit=None)`:
  embed `status='kept'` candidates whose `content_hash` changed/absent → upsert
  `discovery.candidate_embeddings`. Incremental, idempotent, 429-backoff via the
  shared transport. Mirrors `scripts/embed_apps.py`.

### 4. Triage-prior ordering — `src/agf/discovery/triage_prior.py` (pure)
- `liked_direction(liked_vecs, disliked_vecs) -> np.ndarray | None` — thin wrapper
  over `ContentPreferenceModel.fit(...)._dir` (or a direct centroid-diff); returns
  `None` if cold-start (< `MIN_LIKES`).
- `prioritize(candidate_ids, emb_by_id, fifo_order, direction, explore_frac=0.25)
  -> list[int]`: pure. If `direction is None` → return `fifo_order` unchanged.
  Else: rank candidates with an embedding by `cosine(emb, direction)` desc; build the
  output by interleaving so that `floor(explore_frac * N)` slots are filled from
  `fifo_order` (oldest-first, taste-blind) and the rest from the priority order;
  candidates lacking an embedding fall to the FIFO tail. Deterministic.

### 5. Worker wiring — `src/agf/workers/main.py`
- New stage before enrich selection (gated `AGF_TRIAGE_PRIOR`, default on): embed kept
  candidates (`embed_candidates`, best-effort, isolated try/except).
- Replace `kept = kept_advertiser_ids(conn)` with
  `kept = taste_prioritized_kept(conn, user=AGF_TRIAGE_USER, explore_frac=AGF_TRIAGE_EXPLORE_FRAC)`
  — loads kept candidates' embeddings + their `kept_advertiser_id`, the FIFO order, and
  the user's liked direction; returns reordered advertiser ids. Falls back to
  `kept_advertiser_ids` on any failure or cold-start (best-effort; never stalls/regresses).
- `eligible_gem_ids` (TTL refresh of existing gems) is appended after, unchanged.

### 6. Direction accessor — `src/agf/scoring/content_preference.py`
- Add a public `direction` property returning `self._dir` (or `None` in cold-start) so
  triage_prior doesn't reach into a private attr.

## Data flow

```
sweep → candidates (+body_text)         app_ratings ─┐
   │                                                 ▼
   ▼                                   ContentPreferenceModel → liked_dir
embed_candidates → candidate_embeddings ─┐           │
                                         ▼           ▼
                         triage_prior.prioritize(kept, emb, fifo, dir, 0.25)
                                         │ 75% by cosine-to-likes + 25% FIFO explore
                                         ▼
                          enrich budget spends on taste-aligned candidates first
                                         ▼
                      more liked gems per enrich dollar → feed → ratings → loop
```

## Error handling
- Embed failure / 429: shared backoff; best-effort stage; incremental commit so a
  mid-batch failure keeps progress; enrich falls back to FIFO order.
- Cold-start / no direction / no candidate embeddings → FIFO (current behavior).
- A candidate with no embedding is never dropped — it sorts to the FIFO tail.
- The whole prior is advisory: any exception in `taste_prioritized_kept` logs and
  returns plain `kept_advertiser_ids` — enrich correctness never depends on it.

## Testing
- **Pure unit**: `build_candidate_text`; `prioritize` (cold-start passthrough;
  explore-fraction count exact; priority order correct; no-embedding tail; determinism);
  `liked_direction` cold-start `None`.
- **Gemini mocked** in unit; one live embed probe.
- **IT** (`AGF_TEST_DB_URL`, a skip is NOT a pass): migrations 137/138; `embed_candidates`
  incremental + cascade delete; `upsert_sweep_candidate` stores body_text;
  `taste_prioritized_kept` returns a reordering and falls back on cold-start.
- **Live e2e** (gated on the worker-sweep-hang fix, below): run a sweep → candidates get
  body_text → embed_candidates → confirm the enrich order leads with high-cosine
  candidates and 25% explore slots are FIFO. Until the hang is fixed, verify on a
  hand-seeded candidate set.
- Full suite green (`AGF_TEST_DB_URL=… pytest -q`), 0 failed.

## Dependencies / risks
- **BLOCKER (not ours): the 24/7 worker hangs in the app sweep** (un-timed-out
  curl_cffi Meta call; see memory `worker-hangs-in-sweep-2026-06-19`). Live
  verification of the sweep-capture + end-to-end ordering is gated on that fix
  (a parallel session owns it). The code + IT tests do not need the live worker.
- Single-user taste steers a shared scraper — acceptable (solo product); `AGF_TRIAGE_USER`
  makes it explicit.
- Filter bubble: mitigated by the 25% explore reserve + the unchanged breadth sweep.

## Out of scope (later Spec 2b increments)
- Keyword-loop like-rate weighting / auto-retire; funnel-detection sharpening;
  semantic neighbor re-rank; multi-user taste blending; pgvector.

## Build sequence
1. Migrations 137/138 + IT. 2. Sweep capture (body_text). 3. `candidate_embed` +
`embed_candidates` script. 4. `direction` accessor. 5. `triage_prior.prioritize` (pure
TDD). 6. `taste_prioritized_kept` + worker wiring + fallback. 7. Live e2e (after hang fix).
