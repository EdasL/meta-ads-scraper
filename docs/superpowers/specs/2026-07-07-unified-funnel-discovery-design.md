# Unified funnel discovery — design

**Date:** 2026-07-07
**Status:** awaiting user review
**Driver:** the funnel keyword flywheel has fully self-terminated — all 305 keywords it
ever minted are retired, zero are live, and new-funnel inflow collapsed from ~1,360/week
to ~30/week. Its mining source (n-grams over confirmed funnels' ad copy) mints
motivational boilerplate ("take control", "tried everything") that the retirement
lifecycle correctly kills. Direction from the user: funnel discovery should not be a
separate keyword process — funnels should fall out of the SAME keyword sweeps as apps,
and the separate process should be the generic funnel-intent phrases swept to full
pagination depth.

## Decisions

| # | Decision | Chosen by |
|---|----------|-----------|
| 1 | Web ads found by app-keyword sweeps become funnel candidates only when they pass the existing `funnel_signal` gate (funnel host/path, quiz-words in copy, or leadgen CTA) | user |
| 2 | The 28 static `FUNNEL_PHRASES` keep the daily 8-page sweep; a new weekly pass runs each phrase to pagination exhaustion (100-page safety ceiling, ceiling hits logged) | agent (user AFK; alternatives: uncapped daily, rotating slice) |
| 3 | The funnel keyword flywheel (`run_funnel_flywheel_stage`, `kind='funnel'` minting) is removed entirely | follows from the user's "shouldn't be a separate process" |

## Measured basis (2026-07-07 prod probes)

- `dynamic_keywords kind='funnel'`: 305 rows, **all retired**, 0 trial/active. Newest
  mints are ad-copy filler ("think about", "over time", "nothing else").
- Funnel candidate inflow by week (`first_seen_at`): 360 → 709 → 1,360 → 1,021 → **30**.
  With zero live dynamic keywords, only the static 28 phrases sweep — mostly re-finding
  known candidates.
- App sweep discards **~3,050 web-destination ads/day** as `non_app` (raw, pre-dedup,
  301 keywords × 4 pages), plus an unmeasured number dropped earlier as
  `incumbent_likes` (>10k) that the funnel pipeline would tolerate (its ceiling is 5M).
- Confirm (Playwright) processes 50/cycle in the daily worker funnel stage; the queue
  currently holds ~2,082 rows from the broken-probe-era redrain (ETA ~07-09).

## Component 1 — web-funnel capture in the app sweep

`src/agf/discovery/sweep.py` today drops every non-store destination on the floor.
Change the sweep loop (not `classify_candidate`'s app contract) so ads that fail the
app path at `non_app` — or at `incumbent_likes` with `page_like_count` ≤
`FUNNEL_INCUMBENT_PAGE_LIKES_MAX` — are offered to a shared web-funnel classifier:

- **Shared unit:** extract the web-destination branch of
  `funnel_sweep.classify_funnel_candidate` (funnel_signal gate → landing_domain →
  funnel row build incl. `body_text` and UTMs) into one helper used by BOTH sweeps,
  e.g. `funnel_classify.fold_web_funnel(ad, *, keyword, technique) -> dict | None`.
  `classify_funnel_candidate` keeps its signature and delegates; behavior for the
  funnel sweep is unchanged (its `funnel_phrase` layer auto-passes the gate).
- **Gate semantics in the app sweep:** `technique` = the app keyword's taxonomy layer
  (`niche`, `store`, flywheel/minted lanes…), never `funnel_phrase` — so `funnel_signal`
  actually gates (host/path match, FUNNEL_WORDS in copy, or leadgen CTA).
- **Write path:** collected per keyword and upserted into `discovery.funnel_candidates`
  in its own short transaction right after the keyword's app-candidate txn (mirrors
  `run_funnel_sweep`'s two-txn shape). `source_keyword` = the app keyword — keeper yield stays analyzable by
  source, same as the minted-lane tags.
- **Stats:** `drops.non_app` splits into `funnel_captured` (new counter) and the
  remaining `non_app`. `candidates_new`/`seen` for funnels get their own
  `funnel_new`/`funnel_seen` counters on the sweep stats so `sweep_runs.drop_reasons`
  consumers keep working (additive keys only).
- **Social/store-bounce hosts:** already excluded before this branch (shared
  `SOCIAL_HOSTS`; `STORE_BOUNCE_HOSTS` handled inside the funnel row build as today).

Cost model: capture itself is free (fields already on the SERP result). The metered
cost is confirm probes for genuinely new (page_id, landing_domain) pairs, bounded by
the funnel_signal gate. Expected order: low tens/day of new pairs (most of the 3k raw
ads repeat daily and dedup away); measured after ship, see Verification.

## Component 2 — remove the funnel keyword flywheel

- Delete `run_funnel_flywheel_stage` (workers/main.py) and its wiring: the
  `flywheel*` params of `run_funnel_stage`, the `AGF_FUNNEL_FLYWHEEL_*` env knobs,
  the `fetch_funnel_copy` import (and `fetch_funnel_copy` itself if the flywheel was
  its only consumer).
- `run_funnel_stage` becomes sweep → confirm (+ the new weekly deep sweep, below).
- **No migration:** `kind='funnel'` rows stay as inert history; `live_keywords`
  already returns nothing for them (all retired) and nothing else reads the pool.
  The `kind` column and its uniqueness stay — the app pool uses the same table.
- `dynamic_keywords.py` lifecycle helpers keep their `kind` parameter (used by app
  pool); only the funnel *callers* go.

## Component 3 — weekly deep sweep of the static phrases

- Daily funnel sweep: unchanged (28 phrases × ≤8 pages).
- New weekly pass: the same `run_funnel_sweep` with
  `max_pages=AGF_FUNNEL_DEEP_PAGES` (default **100**, a circuit-breaker not a target —
  `extractor.search` already early-exits when Meta's pagination is exhausted).
- **Gating:** once per calendar week, inside the daily funnel stage, using the
  existing `funnel_sweep_runs` pattern — the deep run writes `{"deep": true}` into its
  stats and the due-check looks for a FINISHED run with that marker in the last 7 days.
  Same shape as `flywheel_due`/`release_mint_due`.
- **No silent caps:** the deep sweep logs per-phrase pages fetched and ad counts, and
  flags any phrase that hit the ceiling. This needs the search facade to report pages
  actually fetched (today `serp_requests` is an upper bound); add an optional per-call
  stats sink to `extractor.search` (e.g. `on_pages=callback`) so existing call sites
  are untouched and the deep sweep alone opts in.
- **Heartbeat:** the deep sweep beats per phrase (same `heartbeat` hook as the app
  sweep) — a to-exhaustion pass on a hot phrase can be long, and the watchdog is live
  again as of today's `init: true` fix.
- **Confirm backlog:** each weekly deep pass will spike `status='new'` funnel rows;
  confirm stays at 50/cycle and absorbs the spike over the following days. If the first
  deep pass (largest novelty) floods the queue, run a one-off drain in a separate
  container (the `agf-redrain` pattern, script already proven).

## Out of scope

- Raising the confirm limit / parallelizing Playwright probes.
- Any change to payoff classification, digital-only keeps, the app bridge, or web_sub
  surfacing.
- LLM-minted funnel-intent keywords (a possible future lane if post-unification yield
  disappoints; deliberately not built now).
- The in-flight broken-era redrain (finishes ~07-09 independently).

## Verification

- Unit: `fold_web_funnel` gate behavior (funnel-host pass, quiz-word pass, leadgen-CTA
  pass, plain brand-site reject); app-sweep loop captures at `non_app` AND at
  `incumbent_likes` ≤ 5M but not > 5M; funnel-sweep behavior unchanged (golden stats
  on a fake extractor); deep-sweep weekly gate (due/not-due); ceiling-hit logging.
- Regression: worker stage wiring after flywheel removal (stats shape, run-row close).
- Full suite green (`AGF_TEST_DB_URL=…@localhost:5433/agf_it`, zero failed, zero
  unexpected skips).
- Live e2e (the real gate): one worker cycle in the deployed container — app sweep
  writes ≥1 funnel candidate from an app keyword (or logs `funnel_captured` > 0),
  deep sweep completes with per-phrase page logs, confirm still verdicts. Then measure
  for ~a week: new funnel candidates/day by `source_keyword` technique (app-keyword vs
  funnel_phrase vs deep), and confirm-queue depth staying bounded.
