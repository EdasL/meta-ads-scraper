# Topic diversity — embedding-cluster niches + keyword redundancy retirement

Date: 2026-08-03. Founder-approved scope (session: corpus diversity analysis).
Analysis record: memory `corpus-diversity-analysis-2026-08-03.md`.

## Problem (measured)

- 27.2% of the feed (1,048/3,847 products) is commodity utilities with ZERO founder
  likes/saves across 123 labeled — and higher avg gem (42.0) than the rest (39.5).
- The diversity key is broken: `niche_keyword` = `discovery.candidates.source_keyword`.
  Top "niche" is the phrase "on the app store" (322 products) — 322 unrelated apps
  count as ONE niche for the review-queue 3-per-niche cap and the whitespace axis,
  while 294 near-identical AI photo editors count as dozens of niches, uncapped.
- Live-probe correction to the analysis: the copy-phrase keywords are static
  taxonomy lanes (`GENERIC_DEEP_TERMS`, `STORE_FRAGMENTS`), not flywheel mints, and
  most are NOT redundant by exclusive yield ("on the app store": 340 advertisers no
  other keyword finds, crowded share 0.03). Genuinely redundant: bare "app" (0.57),
  "ai" (0.57), "try the app free" (0.73), "install the app" (0.80), ~10 dynamic
  stragglers. Standing decision 2026-06-19 holds: the sweep stays taste-blind — no
  topical bias on search terms.

## Workstream 1 — topic re-keying (the main fix)

New table `frontend.app_topics` (migration 161):

```sql
CREATE TABLE IF NOT EXISTS frontend.app_topics (
  advertiser_id BIGINT PRIMARY KEY REFERENCES public.advertisers(id) ON DELETE CASCADE,
  topic_id      INTEGER NOT NULL,
  topic_size    INTEGER NOT NULL,
  computed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Producer: `src/agf/scoring/topics.py` — `refresh_topics(engine, k=None)`.
- Loads all of `frontend.app_embeddings` (advertiser_id, embedding; ~7.7k × 768d,
  L2-normalized), clusters with pure-numpy k-means++ (cosine ≡ dot on unit vectors),
  fixed seed for run-to-run determinism of the algorithm (assignments may still
  shift as the corpus grows — acceptable: consumers are per-run percentiles and
  per-request cap keys, no persistence semantics).
- K = `AGF_TOPIC_K` (default 40; calibrated by the 2026-08-03 analysis — K=40 gave
  human-sensible groups). Guard: K capped at n_rows; no rows → no-op.
- Writes DELETE + bulk INSERT in one txn (whole-corpus swap, same pattern as
  crowding refresh). Returns {rows, k} for the cycle log.
- Wiring: `compute_cycle` step right after the app-embed/crowding step, gated on
  the same "embeds changed OR table empty" signal (`_embeds_changed` guard idiom),
  plus a `run_stage` entry for manual runs.

Consumer 1 — whitespace axis (`src/agf/scoring/whitespace.py`, `run_scoring.py`):
- New `load_topic_crowding(conn)` → `({advertiser_id: (topic_id, topic_size)},
  sorted topic sizes)`; the axis becomes `w = 1 - pctile(topic_size, sizes)`.
- Advertiser without a topic row (no embedding yet) → None → axis floors neutral,
  exactly like today's missing-lineage path.
- `load_niche_crowding` is KEPT solely to fill the display/provenance columns
  `niche_keyword`/`niche_advertiser_count` (UI "found via" context; matview
  unchanged). Docstrings updated to say display-only.
- NOTE: GemScore W exponent is 0.05 — the axis swap is a semantic fix, not the
  visible lever. Raising W is a separate founder decision (deferred).

Consumer 2 — review-queue cap (`backend/repositories/preference_queue.go` + repo):
- Queue query LEFT JOINs `frontend.app_topics` (matview definition untouched),
  selecting `topic_id`.
- `nicheKey`: `topic_id` present → `"t:<id>"`; else fall back to existing
  niche_keyword → cohort → exempt chain (coverage is 100% today; fallback protects
  cold-start/new advertisers between cycles).
- `defaultPerNicheCap` stays 3.

## Workstream 2 — keyword redundancy retirement (graded, not name-based)

Rule (both pools): a keyword with ≥ 20 embedded kept candidates whose crowded share
(`candidate_embeddings.crowding >= AGF_CROWDING_TAIL_CANDIDATE` = 0.90) is ≥ 0.6 has
a redundant catch → remove from rotation. Thresholds env-tunable
(`AGF_KW_REDUNDANT_MIN_KEPT=20`, `AGF_KW_REDUNDANT_SHARE=0.6`).

- Dynamic pool: `retire_redundant_keywords(conn)` in `agf/db/dynamic_keywords.py` —
  one UPDATE over status IN ('active','resting') matching the rule → 'retired'.
  Today's victims: ~10 of 1,037 graded (e.g. "ai trend free" 0.98, "programu ya"
  0.86, "blood sugar is high" 0.86). Trials untouched (existing uncrowded-promotion
  verdict already gates them).
- Static pool (taxonomy lists stay founder-approved code): new table
  `discovery.keyword_suppressions` (same migration 161):

```sql
CREATE TABLE IF NOT EXISTS discovery.keyword_suppressions (
  keyword       TEXT PRIMARY KEY,
  reason        TEXT NOT NULL,
  kept_n        INTEGER NOT NULL,
  crowded_share REAL NOT NULL,
  suppressed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

  A grading pass (`suppress_redundant_static_keywords(conn)`) applies the same rule
  to every distinct static-lane source_keyword and upserts rows; deleting a row
  un-suppresses (founder override). Today's victims: "app", "ai",
  "try the app free", "install the app".
- Enforcement: the sweep entry pipeline (daily lanes + generic_deep) filters out
  suppressed keywords at entry-build time (one `SELECT keyword FROM
  keyword_suppressions` per run; fail-open on read error — suppression is an
  optimization, never a sweep-blocker).
- Cadence: both passes run weekly alongside the generic_deep gate in the worker.
  Explicitly NOT retired: "on the app store", "get the app", "ai powered",
  "start your free trial" — high exclusive yield, low crowding (measured).

## Out of scope (flagged, founder decision needed)

- Discover-feed-level diversity (cap/interleave/penalty on the main feed ordering).
- Commodity-family triage veto (would cut 27% of feed at zero like-cost).
- Raising the whitespace weight in GemScore v3.
- Topic display names in the UI (topic_id is an opaque int for now).

## Verification gates

- Full suite to 0 failed (AGF_TEST_DB_URL @55433/agf_it, solo consumer, bare run).
- Live e2e on prod: migration 161 → `run_stage` topics refresh → row-count/coverage
  probe → run_scoring smoke (whitespace populated from topics) → Go queue API probe
  showing ≤3 per topic_id in a batch → grading passes dry-run report, then real run,
  with before/after keyword counts.
