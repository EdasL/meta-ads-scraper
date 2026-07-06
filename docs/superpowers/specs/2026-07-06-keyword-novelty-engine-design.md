# Keyword novelty engine — redundancy gate + external breadth — design

**Date:** 2026-07-06
**Research basis:** docs/research/2026-07-06-adlibrary-scraping-techniques.md (frequency-
based keyword selection IS the convergence bug; fix = harvest-rate/redundancy selection +
external-frame breadth) and docs/research/2026-07-02-novel-discovery-algorithm.md (flywheel
= depth, external frame = breadth, must be separate subsystems).

## Problem

Discovery has two independent failures, and one fixes the other's blind spot:

1. **Pipe 1 (flywheel) re-digs.** `mine_phrases` ranks new keyword candidates by
   distinct-advertiser count in the found corpus (`keyword_mining.py:142`) — literally
   selecting for *sameness*. It re-mines exhausted concept-space; ~3,588 of 4,012
   candidates are re-finds (`times_seen>1`). No redundancy pre-filter exists; the only
   redundancy signal is post-hoc (`was_new` after the sweep is already spent).
2. **No Pipe 2 breadth.** The flywheel reads our own corpus, so it is structurally
   incapable of surfacing a niche it has never touched — filtering its output cannot create
   novelty. Genuine breadth must come from sources *outside* our corpus. `frame_mint`
   (Apple new-releases + LLM) is a small external pipe; it needs company.

## Architecture — two pipes, one shared gate

```
  Pipe 1: flywheel        mine_phrases (niche-band pre-filter)  ─┐
  Pipe 2: store autocomplete  (Apple hints + GPlay suggest)     ─┤
  Pipe 2: LLM niche brainstorm (category + new-release seed)    ─┼─▶ REDUNDANCY GATE ─▶ yield-weighted
  Pipe 2: Apple new-releases  (existing frame_mint)             ─┘   (novelty admit)     category budget
                                                                                          ─▶ add_trial_keywords
                                                                                          ─▶ existing trial→active→retired
```

Every keyword source, old and new, passes through **one** redundancy gate before admission,
so all pipes are deduped against the corpus and against each other. Taste-blind throughout
(novelty = semantic distance to exhausted basins, never user ratings).

---

## Part A — the redundancy gate (shared core)

### A1. Migration 146 — `discovery.dynamic_keyword_embeddings`
Cache keyword-phrase embeddings so we never re-embed. Mirrors `candidate_embeddings`:
```
CREATE TABLE discovery.dynamic_keyword_embeddings (
    keyword_id   BIGINT PRIMARY KEY REFERENCES discovery.dynamic_keywords(id) ON DELETE CASCADE,
    embedding    REAL[] NOT NULL,          -- L2-normalized, 768d (cosine = dot product)
    content_hash TEXT   NOT NULL,
    dim          INT    NOT NULL,
    model        TEXT   NOT NULL DEFAULT '',
    embedded_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```
Candidate phrases not yet in `dynamic_keywords` are embedded transiently (not stored until
admitted); once admitted + swept they get cached lazily like candidates do.

### A2. `keyword_redundancy.py` (new module)
- `exhausted_basin_embeddings(conn, *, dry_floor)` → embeddings of keywords we've already
  worked: `status='retired'` OR (`status IN ('trial','active')` AND `dry_streak >= dry_floor`).
  Reads from the cache; lazily embeds+caches any live keyword missing an embedding. Returns
  a matrix (list of 768d vectors) + the keyword strings (for debug/logging).
- `redundancy_scores(phrases, basin_vectors, *, embed=embed_texts)` → embed each candidate
  phrase, return `{phrase: max_cosine_to_any_basin}`. Cosine = dot product (all L2-normed).
  Empty basin set → all scores 0.0 (nothing is redundant yet; cold-start admits freely).
- `admit_by_novelty(scored, *, threshold, limit)` → drop phrases with score `>= threshold`,
  return survivors sorted ascending by redundancy (novel first), capped at `limit`. Pure
  function — the unit-test seam.
- Thresholds via env: `AGF_KEYWORD_REDUNDANCY_THRESHOLD` (default 0.90 — tune on backtest),
  `AGF_KEYWORD_BASIN_DRY_FLOOR` (default = `retire_dry_streak()`, i.e. treat "about to
  retire" as exhausted).

### A3. Wire the gate into Pipe 1
In `run_flywheel_stage` (`main.py:390`), after `mine_phrases` produces candidates and before
`add_trial_keywords`: fetch basin vectors once, score the candidates, `admit_by_novelty`.
`mine_phrases` keeps its niche-band + rare-word pre-filter (still useful, cheap) but its
advertiser-frequency sort is no longer the admission order — novelty is. Minimal change; no
rewrite of `mine_phrases`.

---

## Part B — the breadth engine (Pipe 2)

All Part-B sources emit `(phrase, category)` candidates, funnel through the Part-A gate,
then through the existing yield-weighted `mint_budget` (2026-07-06) for per-category slot
allocation, then `add_trial_keywords(source=…, kind='app')` with a distinct `source` tag per
origin so we can measure each pipe's yield separately.

### B1. `store_autocomplete.py` (new) — external consumer vocabulary
- **Apple hints:** `GET https://search.itunes.apple.com/WebObjects/MZSearchHints.woa/wa/hints`
  `?clientApplication=Software&term=<seed>` with header `X-Apple-Store-Front: 143441-1,29`
  → plist; parse `hints[].term`. (Live-verified 2026-07-06: `sleep app` →
  `["sleep apple watch","sleep app","sleep apps for free",…]`.)
- **Google Play suggest:** via the existing node sidecar (`google-play-scraper` `suggest`,
  already vendored). (Live-verified: `sleep` → `["sleeper","sleep sounds","sleep number",…]`.)
- **Seeds:** each in-scope category name + its taxonomy `NICHE_TERMS_BY_CATEGORY` head terms.
  For each seed, take both stores' suggestions, keep phrases that look like app-search terms
  (`_normalize` from `release_keywords.py`: 2–5 words, lowercased). Category = the seed's
  category. Zero LLM, zero Meta traffic.
- **NOTE — Google Play new-releases dropped:** Google removed the NEW_* store collections
  (`google-play-scraper.collection` = only `TOP_FREE/TOP_PAID/GROSSING`; `NEW_FREE` is
  `undefined`, live-verified). Top-charts are incumbents we veto, so Google Play contributes
  via autocomplete only. Apple new-releases (existing `frame_mint`) remain the new-release feed.

### B2. LLM niche-brainstorm mode (extend `release_keywords.py`)
- New function `brainstorm_niche_keywords(category, seed_listings, n, *, _post=…)`: prompt a
  cheap Gemini model to list emerging *sub-niches* + their search phrases in `category`,
  seeded by 1–2 real new-release listings so it grounds on a live signal instead of pure
  priors (the domain research: zero-grounding LLM "risks hallucination or drift"; a light
  corpus seed mitigates). Reuses the `_SCHEMA`/`_normalize`/`ProposeError` machinery already
  in the file. Distinct from the existing `propose_release_keywords` (which paraphrases the
  listing in front of it) — this one reaches into adjacent niches the listing doesn't name.
- Wired as an additional candidate source in the mint stage, same gate + budget + source tag
  (`source='brainstorm:<category>'`).

### B3. Stage wiring
Extend the existing frame-mint stage (`main.py:593`, gated by `AGF_RELEASE_MINT`) into a
"mint" stage that runs all Pipe-2 sources, unions their candidates, dedups via the gate,
allocates via `mint_budget`, admits. Each source behind its own env flag
(`AGF_MINT_AUTOCOMPLETE`, `AGF_MINT_BRAINSTORM`, default on) so any one can be cut without a
deploy. Failure of one source is isolated (try/except → stats.errors, never aborts the tick),
exactly like the current frame-mint isolation.

---

## Data flow (one mint cycle)

1. Gather candidates: flywheel (Pipe 1, its own stage) and Pipe-2 sources each produce
   `(phrase, category)` lists.
2. `exhausted_basin_embeddings(conn)` once per cycle (cached; lazy-embeds new basin keywords).
3. `redundancy_scores` → `admit_by_novelty(threshold, limit)` per source.
4. Survivors → `mint_budget(coverage, eligible=categories-with-candidates)` for slot counts.
5. `add_trial_keywords` with per-source `source` tag.
6. Existing sweep/lifecycle takes over: trials get swept, `record_keyword_sweep` tracks
   new-unique yield, promote/retire unchanged.

## Error handling
- Each external fetch (Apple hints, GPlay suggest, Gemini brainstorm) wrapped; a failure
  increments `stats['errors']` and skips that source, never raises. Matches `frame_mint`'s
  existing contract.
- Embedding failure (`EmbedError`, no `GEMINI_API_KEY`) → gate degrades OPEN for that cycle
  (admit by the pre-filter order, log a warning) rather than blocking all minting. Cold-start
  (empty basin) also admits freely. The gate must never be able to starve discovery.
- Apple plist / GPlay JSON parse failures degrade to `[]` for that seed (per-seed isolation).

## Testing
- **Pure logic (no DB/network):** `admit_by_novelty` (threshold drop, novel-first order,
  limit, empty-basin cold-start); `redundancy_scores` with fake `embed` (cosine math,
  max-over-basins); Apple-hints plist parser + GPlay-suggest parser on captured fixtures.
- **DB-backed (rolled-back pg):** `exhausted_basin_embeddings` selects retired + high-dry
  keywords and lazily caches; migration 146 up/down.
- **Integration (fake fetch/embed, real engine):** flywheel stage admits novel + drops a
  near-duplicate of a retired keyword; mint stage unions sources, dedups across pipes,
  respects the budget; one source erroring doesn't abort the others.
- **Live e2e (controller-run, real deps):** one mint cycle in the deployed worker — Apple
  hints + GPlay suggest return real terms, gate admits, keywords land as trials with correct
  source tags, zero errors. (Same base64-piped `docker compose exec` trick as the 07-06 mint
  verify.)
- Full suite green: `AGF_TEST_DB_URL=…@localhost:55433/agf_it pytest -q`, 0 failed.

## Build order (phases — each independently shippable)
1. **Migration 146 + `keyword_redundancy.py` + wire into flywheel (Pipe 1).** The bouncer.
   Immediately cuts re-dig waste. Smallest, highest-confidence.
2. **`store_autocomplete.py` + stage wiring (Pipe 2 core).** The main breadth source; no LLM.
3. **LLM niche-brainstorm mode (Pipe 2 amplifier).** Additive; behind its own flag.

## Success criteria
- Gate: minted/mined keywords show a materially lower re-find rate (`times_seen>1` share of
  their candidates) than the pre-change baseline; near-duplicate keywords of retired basins
  stop being admitted.
- Breadth: a rising share of new kept apps arrive via Pipe-2 source tags
  (`autocomplete:*`, `brainstorm:*`) — i.e. keepers the flywheel could not have reached.
- No regression in total kept/day; 0 failing tests; live cycle admits real terms with 0 errors.

## Out of scope
- Live-pool sweep prioritization / cap by harvest rate (retirement already sheds dry arms).
- Re-scoring / pruning the static ~305-keyword taxonomy (deliberate permanent backbone).
- Google Play new-releases (feed removed by Google — see B1).
- General web/search autocomplete (noisier; deferred).
- RAW-UCB scheduler, Chao2/urn meters (separate instrumentation; prior docs).
