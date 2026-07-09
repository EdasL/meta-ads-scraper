# Corpus-refresh as a recurring worker stage — design spec

**Date:** 2026-07-09
**Status:** approved (founder), grounded by prod measurement + code map.

## Problem

Gem (score≥60) conversion is declining per discovery cohort (19% → 4%) even though
candidate/advertiser intake is at record volume. Root cause (measured): the corpus
ad-refresh — `refresh_corpus` — is a **manual one-shot script, dead since ~2026-06-10**.
Consequences:
- Found advertisers' ad metrics are **frozen at discovery**. A newcomer found young (avg ad
  tenure now 62d, down from 142d) can never mature past ≥60 because its winner-signal
  (run-days, new ads, depth growth) never updates. We've harvested the already-proven
  winners; the young ones we now find are stuck sub-60 forever.
- `public.ads.is_active` is **frozen true** from discovery → GemScore keeps inflating
  `winner_age` (weight 0.40) and `winner_depth` (0.30) for ads that likely ended weeks ago
  → some current gems are **phantom** (dead-ad advertisers scored as running winners).
  `meta_active_ads_checked_at` newest = 2026-06-10; 90% of "active" ads not re-scraped in 7d+.

## Goal

Run `refresh_corpus` as a **recurring, budget-bounded worker stage** so found advertisers'
live Meta listings are continuously re-scraped → real `is_active`/run-days/new-ads → young
newcomers genuinely mature into gems and phantom winners get demoted. Uses the ~90% idle
proxy headroom (re-scraping knowns, not keyword search). No change to `refresh_corpus`
internals or scoring math — the existing 30-min scoring cron consumes the refreshed data.

## Design

Pure wiring — copy the funnel-stage pattern in `src/agf/workers/main.py`.

### 1. Cadence runs table + helpers
- New migration `db/schema/148_corpus_refresh_runs.sql` (verify 147 is highest first):
  `discovery.corpus_refresh_runs` (id BIGSERIAL, started_at, finished_at, stats jsonb, error)
  — copy `121_funnel_sweep_runs.sql`.
- New helpers `src/agf/db/corpus_refresh_runs.py` mirroring
  `db/funnel_candidates.py:59-102`: `open_corpus_refresh_run`, `close_corpus_refresh_run`,
  `corpus_refresh_ran_today(conn, as_of)`, `corpus_refresh_ran_within(conn, seconds)`.

### 2. The stage in `run_once`
Copy the funnel-stage template (`main.py:284-297` `funnel_due`, `:595-618` the slot):
- Env gates (add to `run_once` params + `main()` env wiring, mirroring `AGF_FUNNEL_*`):
  - `AGF_CORPUS_REFRESH_DAILY` (`_env_bool`, default **1** = on)
  - `AGF_CORPUS_REFRESH_EVERY_SEC` (`_env_int`, default **0** = once/day)
  - `AGF_CORPUS_REFRESH_BUDGET` (`_env_int`, default **3000**) — its OWN budget, NOT carved
    from `AGF_DISCOVERY_ENRICH_BUDGET` (that would starve enrich). ~3000 req/cycle at
    hist_pages=12 ≈ ~250 stalest advertisers/day → full ~4.3k corpus refreshed ~every 2-3
    weeks; tunable UP given the 90% egress headroom.
- `corpus_refresh_due(conn, as_of, *, dry_run, continuous)` mirroring `funnel_due`:
  `every>0 → not corpus_refresh_ran_within(every)` else `not corpus_refresh_ran_today(as_of)`.
- Slot in `run_once` alongside the other budget-bounded Meta stages (after keeper_history /
  funnel §1.8-1.9, **before** enrich §3): if `corpus_refresh_daily` and due → open run row →
  `asyncio.run(refresh_corpus(engine, extractor, budget_requests=budget, hist_pages=12))`
  in try/except → close run row with stats or error → `_touch_heartbeat()`. A failure lands
  on the run row and never aborts the tick (funnel pattern).
- `refresh_corpus` selects stalest-first (`MAX(ad.last_seen) ASC`) and rotates through the
  corpus across cycles automatically as it updates `last_seen`; no per-advertiser cursor
  needed. `flip_vanished` stays True (its default) so ended ads → `is_active=false`.

### 3. Scoring linkage (no code change)
The prod scoring cron (`*/30 * * * * python -m agf.scoring.run_scoring`) already rescores the
whole corpus. `features.py:177` filters `active = [r for r in ad_rows if r.is_active]`, feeding
`winner_age`/`winner_depth`/`count_growth`. So refreshing `is_active` + weekly metrics
automatically corrects phantom inflation and lets matured newcomers cross ≥60 on the next
scoring pass. Verify the linkage in review; do not change scoring math.

## Files
- `db/schema/148_corpus_refresh_runs.sql` (new)
- `src/agf/db/corpus_refresh_runs.py` (new helpers)
- `src/agf/workers/main.py` (stage wiring + env gates + `corpus_refresh_due`)
- `tests/workers/test_corpus_refresh_stage.py` (new; mirror `tests/workers/test_funnel_stage.py`)
- (existing `tests/discovery/test_corpus_refresh.py` already covers `refresh_corpus` internals)

## Tests
Worker-stage tests (mirror funnel stage): cadence gate runs when due / skips when already ran
(today + within-seconds), opens+closes a `corpus_refresh_runs` row with stats, touches
heartbeat, a stage exception lands on the run row and does NOT abort the tick, the stage's own
budget is passed through (not enrich's). Use the `pg_conn`/`_migrated_engine` fixtures and the
`tests/discovery/conftest.py` `FakeExtractor` (has `search_listing`). Full suite bare
(`AGF_TEST_DB_URL=…@localhost:55433/agf_it`), 0 failed, skip≠pass.

## Env knobs (defaults; tunable)
`AGF_CORPUS_REFRESH_DAILY=1`, `AGF_CORPUS_REFRESH_EVERY_SEC=0`, `AGF_CORPUS_REFRESH_BUDGET=3000`.

## Non-goals / decisions
- No score-priority selection in v1 — stalest-first (existing `last_seen` ordering) rotates the
  whole corpus; prioritizing near-gems (score 50-59) is a future enhancement.
- No change to `refresh_corpus`/`refresh_one_advertiser` internals or to GemScore math.
- Own budget (not shared with enrich). Start conservative (3000), raise given headroom.

## Verification bar
- Full suite green.
- Live (prod worker, clean-worktree build + deploy): enable the stage, run one cycle, confirm
  it (a) writes a `corpus_refresh_runs` FINISHED row with `advertisers_done`/`ads_stored`/
  `flipped`>0, (b) updates `ads.last_seen`/`is_active` on stale advertisers (spot-check a few
  whose `last_seen` was 20d+ old), (c) flips some vanished ads `is_active=false`, and (d) after
  the next scoring cron, phantom `winner_age` on demoted advertisers drops. 2+ real components
  (scraper + DB + scoring) → live run required, mock greens insufficient.
