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
| Candidate content | **Fetch the store listing** (user decision): every app candidate has `store` + `store_id`, so look the app up by id — batch **iTunes Lookup** (`lookup_itunes_batch`, ≤200 ids/call) for iOS + the **Play sidecar** (`lookup_google_play`) for Android — and embed its real `name + genre + description` via the SAME `build_embed_text` Spec 2 uses for app embeddings. Cheap (~4 iOS calls / 800 candidates), no sweep change, and the candidate vector is directly comparable to the liked centroid. NOT ad-copy capture, NOT thin page_name. |
| Prior signal | `priority = cosine(candidate_emb, liked_dir)` where `liked_dir` is the Spec 2 `ContentPreferenceModel` direction (`mean(liked) − mean(disliked)`) for the configured user. |
| Filter-bubble guard | Epsilon-greedy explore reserve `AGF_TRIAGE_EXPLORE_FRAC=0.25`: 25% of enrich slots go oldest-first (taste-blind); 75% highest-priority. |
| Cold-start | < `MIN_LIKES` likes (or no embeddings) → full FIFO (current behavior, zero regression). |
| Embedding reuse | Same Gemini `gemini-embedding-001` @768d + `embeddings.embed_texts` + `build_embed_text` from Spec 2; cache in `discovery.candidate_embeddings`. Incremental = embed each kept candidate **once** (skip those already in `candidate_embeddings`) so we don't re-fetch listings every cycle. |
| Whose taste | The configured single user `AGF_TRIAGE_USER` (default `founder@fielddesk.local`) — solo product. |

## Grounded facts (checked live)

- `discovery.candidates(id, page_id, page_name, page_like_count, store, store_id,
  link_url, cta_type, ad_start_date, source_keyword, technique, times_seen,
  first_seen_at, last_seen_at, status, drop_reason, triaged_at, kept_advertiser_id)`
  — **every app candidate has `store` (`ios`/`android`) + `store_id`** (`classify_candidate`
  requires a store_id), so the listing is always fetchable. 2020 rows, 800 kept.
- **Store-fetch-by-id already exists** in `src/agf/enrich_full/appstore.py`:
  `lookup_itunes_batch(client, ids, *, chunk=200, country="us") -> {track_id: AppMeta}`
  (batch, free, no proxy) and `lookup_google_play(package_ids, *, runner=None) ->
  {package_id: AppMeta}` (Node sidecar). `AppMeta` (`agf.enrich`) carries `name, genre,
  description`. Spec 2's `build_embed_text(app_name, tagline, genre, description, ad_copy)`
  embeds exactly these fields — so a candidate vector built from the fetched listing is
  the same shape as an app vector.
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

Five units, each independently testable.

### 1. Migration — `db/schema/137_candidate_embeddings.sql`
- `CREATE TABLE discovery.candidate_embeddings(candidate_id BIGINT PRIMARY KEY
  REFERENCES discovery.candidates(id) ON DELETE CASCADE, embedding REAL[] NOT NULL,
  content_hash TEXT NOT NULL, dim INT NOT NULL, model TEXT NOT NULL DEFAULT '',
  embedded_at TIMESTAMPTZ NOT NULL DEFAULT now());`
- No `candidates` schema change — the listing is fetched, not stored on the candidate.

### 2. Candidate store-fetch + embed — `src/agf/discovery/candidate_embed.py` + `scripts/embed_candidates.py`
- `embed_candidates(conn, *, itunes_client, gplay_runner=None, embed_fn=embed_texts,
  commit_every=25, limit=None) -> dict`:
  1. Select kept candidates **not yet in `candidate_embeddings`** (embed each once — no
     re-fetch of unchanged listings).
  2. Group by `store`: iOS `store_id`s → `lookup_itunes_batch(itunes_client, ids)`;
     android → `lookup_google_play(ids, runner=gplay_runner)`. Both return `{id: AppMeta}`.
  3. For each candidate with a fetched `AppMeta`, build the text with the SAME Spec 2
     builder: `build_embed_text(app_name=meta.name, tagline=None, genre=meta.genre,
     description=meta.description, ad_copy=[])`; `content_hash` it; `embed_texts`; upsert.
  4. Candidates whose listing can't be fetched (delisted/404) are skipped this cycle
     (they fall to the FIFO tail in `prioritize`). Incremental commit + 429 backoff via
     the Spec 2 embedder. `itunes_client` is the worker's httpx client; `gplay_runner`
     defaults to the Node sidecar and is injectable for tests.
- The fetch is the CHEAP prior signal (≤200 ids/iTunes call); the full Meta enrich
  (~14 requests) is still reserved for the prioritized candidates.

### 3. Triage-prior ordering — `src/agf/discovery/triage_prior.py` (pure)
- `liked_direction(liked_vecs, disliked_vecs) -> np.ndarray | None` — thin wrapper
  over `ContentPreferenceModel.fit(...)._dir` (or a direct centroid-diff); returns
  `None` if cold-start (< `MIN_LIKES`).
- `prioritize(candidate_ids, emb_by_id, fifo_order, direction, explore_frac=0.25)
  -> list[int]`: pure. If `direction is None` → return `fifo_order` unchanged.
  Else: rank candidates with an embedding by `cosine(emb, direction)` desc; build the
  output by interleaving so that `floor(explore_frac * N)` slots are filled from
  `fifo_order` (oldest-first, taste-blind) and the rest from the priority order;
  candidates lacking an embedding fall to the FIFO tail. Deterministic.

### 4. Worker wiring — `src/agf/workers/main.py`
- New stage before enrich selection (gated `AGF_TRIAGE_PRIOR`, default on): fetch+embed
  kept candidates (`embed_candidates(conn, itunes_client=http_client)`, best-effort,
  isolated try/except).
- Replace `kept = kept_advertiser_ids(conn)` with
  `kept = taste_prioritized_kept(conn, user=AGF_TRIAGE_USER, explore_frac=AGF_TRIAGE_EXPLORE_FRAC)`
  — loads kept candidates' embeddings + their `kept_advertiser_id`, the FIFO order, and
  the user's liked direction; returns reordered advertiser ids. Falls back to
  `kept_advertiser_ids` on any failure or cold-start (best-effort; never stalls/regresses).
- `eligible_gem_ids` (TTL refresh of existing gems) is appended after, unchanged.

### 5. Direction accessor — `src/agf/scoring/content_preference.py`
- Add a public `direction` property returning `self._dir` (or `None` in cold-start) so
  triage_prior doesn't reach into a private attr.

## Data flow

```
candidates (store, store_id)            app_ratings ─┐
   │ fetch listing by id (iTunes batch / Play sidecar)▼
   ▼                                   ContentPreferenceModel → liked_dir
embed_candidates (name+genre+description) ─┐         │
   → candidate_embeddings                  ▼         ▼
                         triage_prior.prioritize(fifo, emb, dir, 0.25)
                                         │ 75% by cosine-to-likes + 25% FIFO explore
                                         ▼
                          enrich budget spends on taste-aligned candidates first
                                         ▼
                      more liked gems per enrich dollar → feed → ratings → loop
```

## Error handling
- Listing fetch failure (404/delisted/network) → that candidate is skipped this cycle
  (no embedding → FIFO tail in `prioritize`); never dropped from enrich.
- Embed failure / 429: shared backoff; best-effort stage; incremental commit so a
  mid-batch failure keeps progress; enrich falls back to FIFO order.
- Cold-start / no direction / no candidate embeddings → FIFO (current behavior).
- The whole prior is advisory: any exception in `taste_prioritized_kept` logs and
  returns plain `kept_advertiser_ids` — enrich correctness never depends on it.

## Testing
- **Pure unit**: `prioritize` (cold-start passthrough; explore-fraction injects FIFO;
  priority order correct; no-embedding tail; determinism); `liked_direction` cold-start `None`.
- **Gemini + store-fetch mocked** in unit (inject `embed_fn` + a fake `lookup` runner);
  one live iTunes-lookup + embed probe.
- **IT** (`AGF_TEST_DB_URL`, a skip is NOT a pass): migration 137; `embed_candidates`
  embeds-once + cascade delete (inject a fake iTunes client + gplay runner);
  `taste_prioritized_kept` falls back to FIFO on cold-start.
- **Live e2e**: apply migration → `scripts/embed_candidates.py` fetches+embeds the
  existing kept candidates (real iTunes Lookup) → confirm `taste_prioritized_kept`
  reorders the FIFO head and 25% explore slots remain oldest-first. (Does NOT need the
  worker sweep — kept candidates already exist; the only sweep dependency is for NEW
  candidates to flow, gated on the hang fix below.)
- Full suite green (`AGF_TEST_DB_URL=… pytest -q`), 0 failed.

## Dependencies / risks
- **BLOCKER (not ours): the 24/7 worker hangs in the app sweep** (un-timed-out
  curl_cffi Meta call; see memory `worker-hangs-in-sweep-2026-06-19`). This gates only
  the flow of NEW candidates; the triage-prior code, IT tests, AND the live e2e on the
  existing 800 kept candidates do NOT need the worker sweep. A parallel session owns the fix.
- Single-user taste steers a shared scraper — acceptable (solo product); `AGF_TRIAGE_USER`
  makes it explicit.
- Filter bubble: mitigated by the 25% explore reserve + the unchanged breadth sweep.
- Single-user taste steers a shared scraper — acceptable (solo product); `AGF_TRIAGE_USER`
  makes it explicit.
- Filter bubble: mitigated by the 25% explore reserve + the unchanged breadth sweep.

## Out of scope (later Spec 2b increments)
- Keyword-loop like-rate weighting / auto-retire; funnel-detection sharpening;
  semantic neighbor re-rank; multi-user taste blending; pgvector.

## Build sequence
1. Migration 137 (`candidate_embeddings`) + IT. 2. `direction` accessor. 3.
`candidate_embed` (store-fetch + embed) + `embed_candidates` script. 4.
`triage_prior.prioritize` (pure TDD). 5. `taste_prioritized_kept` + worker wiring +
fallback. 6. Live e2e on the existing kept candidates (real iTunes Lookup).
