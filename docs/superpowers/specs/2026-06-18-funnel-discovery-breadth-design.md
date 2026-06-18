# Funnel discovery — find more funnels

**Date:** 2026-06-18
**Status:** approved (direction); apps-only scope
**Goal:** the funnel sweep re-scrapes the same tiny pool and leaks its best finds.
Grow what it discovers and route app-backed funnels into the feed.

## Diagnosis (live probe, 2026-06-18)

Probed 3 funnel phrases + 1 niche term, 6 pages each, against the live `agf` DB:

| keyword | distinct advertisers | already in DB | new | per-page new |
|---|---|---|---|---|
| take the quiz | 25 | 1 | 24 | [6,4,5, **3,3,4**] |
| personalized plan | 30 | 3 | 27 | [8,5,5, **2,6,4**] |
| based on your answers | 21 | 0 | 21 | [4,5,4, **1,3,4**] |
| calorie counter app (niche) | 4 | 1 | 3 | [3,1,**0**] |

Run-stats corroborate: each daily funnel sweep pulls ~700 raw ads, **0 errors, 0
throttles**, `not_funnel: 0`, and `candidates_seen ~315 / candidates_new ~15`.

**Three root causes (the scrape itself is healthy):**

1. **Depth cap too shallow.** Funnel phrases keep surfacing *new* advertisers on
   pages 4–6 and never plateau (bold tail above); the niche term plateaus at page 3.
   The sweep caps at `max_pages=3` (`taxonomy.py:181` + `AGF_FUNNEL_MAX_PAGES`), so
   we see ~60% of each phrase's advertisers and miss the deeper, lower-impression =
   smaller/emerging ones.
2. **Fixed vocabulary.** 28 funnel phrases, run unchanged every day; each caps at
   ~25 advertisers, so the net is structurally bounded at ~330. The app sweep has a
   keyword flywheel; the funnel sweep has none.
3. **Bridge gap.** Only 0–3 of ~25 advertisers per phrase exist in
   `public.advertisers`. Funnel finds live in `discovery.funnel_candidates` and are
   never created as advertisers, so they can't reach the feed. In the sweep,
   store-linked ads (`disposition="app"`, ~50/run) are dropped with the comment "the
   APP pipeline owns it" — but nothing routes them there.

## Fixes

### 1. Depth (immediate)
Raise the funnel-phrase entry `max_pages` (`taxonomy.py:181`) and the
`AGF_FUNNEL_MAX_PAGES` default (`workers/main.py`) from 3 → **8**. The sweep takes
`min(entry.max_pages, env)`, so both must rise. Cost: ~224 SERP requests/day for the
funnel stage (from 84), fine for a once-daily stage.

### 2. Bridge — route app-backed funnels into app discovery
In `run_funnel_sweep`, an ad classified `"app"` has a direct store `link_url`, so
`classify_url` yields `(store, store_id)`. Instead of dropping it, upsert it into
`discovery.candidates` via `candidates.upsert_sweep_candidate` (`source_keyword=` the
funnel phrase, `technique="funnel_sweep"`). The existing `run_triage` then resolves
the app, creates `public.advertisers` + `public.apps`, and it flows to scoring/feed
under the normal app vetoes — no duplication. `classify_funnel_candidate` returns the
app row alongside the disposition; the sweep loop persists app rows to
`discovery.candidates` and funnel rows to `discovery.funnel_candidates`. Stats gain
`bridged_app`.

### 3. Funnel keyword flywheel
Mirror `run_flywheel_stage`, kept separate so funnel trial keywords don't pollute the
app sweep.

- **Migration:** `discovery.dynamic_keywords` add `kind TEXT NOT NULL DEFAULT 'app'`
  (existing rows = app). `discovery.funnel_candidates` add `body_text TEXT` (the ad
  copy to mine — not currently stored).
- **Mining source:** the funnel sweep stores each candidate's ad `body_text`.
  `keyword_mining.fetch_funnel_copy(conn, *, min_times_seen=1)` returns
  `(id, body_text)` for **confirmed** funnel candidates; `mine_phrases` (reused
  as-is) extracts cross-advertiser 2–4-grams.
- **Lifecycle:** `dynamic_keywords` functions gain a `kind="app"` param
  (`add_trial_keywords`, `live_keywords`, `tried_keywords`, `trial_count`,
  `apply_verdicts`) — default preserves app behavior; the funnel flywheel passes
  `kind="funnel"`.
- **Stage:** `run_funnel_flywheel_stage` mines confirmed-funnel copy → adds
  `kind="funnel"` trials (excluding static FUNNEL_PHRASES ∪ tried) → sweeps each live
  funnel keyword via `run_funnel_sweep` (so it feeds funnel_candidates AND the bridge)
  → `record_keyword_sweep`/verdicts on funnel new-candidate yield. Wired into the
  worker behind the funnel daily gate; `AGF_FUNNEL_FLYWHEEL_*` knobs mirror the app
  flywheel; `AGF_FUNNEL_FLYWHEEL=0` disables.

## Build order (incremental, each committed + verified)

1. **Depth + bridge** — small, high-confidence, immediate feed growth. Live-verify:
   funnel sweep yields more candidates and `discovery.candidates` gains funnel-sourced
   app advertisers; feed funnel-app count rises after triage+score.
2. **Flywheel** — migration → store body_text → mining → kind-aware lifecycle →
   funnel flywheel stage. Verify: funnel trial keywords minted, swept, promoted/
   retired without touching the app keyword pool.

## Out of scope
- App-less web funnels (no_app veto stays — apps-only).
- Off-Meta discovery (PublicWWW/seed-lists/crt.sh) — separate larger initiative.
- Resolving `kind='install'` funnel_candidates (no direct store link) into apps —
  the bridge covers direct-store-link funnel ads; MMP-signature-only ones are a later
  follow-up.

## Testing
- Bridge: unit test that a funnel-sweep `"app"` ad lands in `discovery.candidates`
  with store/store_id and a funnel `"web"` ad does not.
- Depth: probe re-run shows pages 4–8 add advertisers (already demonstrated).
- Flywheel: unit tests for `kind` isolation (funnel trials don't appear in app
  `live_keywords`), mining from funnel copy, promote/retire on funnel yield.
- Full `pytest` green with `AGF_TEST_DB_URL` set (DB tests skip silently otherwise).
