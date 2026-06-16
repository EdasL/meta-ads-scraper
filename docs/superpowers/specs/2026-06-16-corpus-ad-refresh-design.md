# Corpus ad refresh — re-scrape running-days for every advertiser

**Date:** 2026-06-16
**Status:** approved (design), pending implementation plan

## Problem

GemScore v3 leans on **running-days** (`winner_age` = oldest live ad,
`winner_depth` = live ads ≥21d), derived from `public.ads.is_active` +
`start_date`. That data only refreshes when an advertiser's ads are re-scraped
from Meta. Today the worker re-scrapes only *alerting* advertisers (~19), so the
bulk of the corpus (706 of 772 advertisers) carries ad data last seen days ago:
ads that have since stopped still read `is_active=true` (inflating winner_age /
winner_depth), and newly-launched ads are missing.

The enrich path (`enrich_gem`) does **not** help — it reads existing DB ads and
attaches media/funnels; it never re-fetches the live ad set. The fetch+upsert
logic we need already exists in `discovery/keeper_history.pull_keeper_history`,
but it is scoped to *unscored keepers* (kept candidates with zero metric rows).

## Goal

Refresh the live ad set for the **whole advertiser corpus** by re-scraping each
advertiser's Meta `ALL` listing and reconciling `public.ads`, so v3
running-days reflects current reality. One-shot operator run now; global rescore
after.

## What already exists (reuse, don't rebuild)

`pull_keeper_history` (keeper_history.py:61) already does, per advertiser:
`extractor.search(view_all_page_id=page_id, active_status="ALL",
max_pages=hist_pages)` → dedup by `ad_archive_id` → `repo.upsert_ad` for each
(which on conflict updates `is_active`, `end_date`, `last_seen` and inserts new
ads) → reconstructs the 16-week `app_advertiser_metrics` series. It is
budget-bounded (`budget_requests`), error-isolated per advertiser (`ExtractError`
→ skip), and paced (`kw_delay`). This is exactly the per-advertiser refresh we
need.

## Design

### 1. Refactor: extract `refresh_one_advertiser`

Pull the per-advertiser body of `pull_keeper_history` (the search → dedup →
per-ad `upsert_ad` + `ensure_text_creative` → weekly-series reconstruct, all in
one `begin(engine)` txn) into:

```
refresh_one_advertiser(engine, extractor, advertiser_id, page_id, app_id_lookup,
                       *, hist_pages, weeks) -> {ads_stored, truncated, flipped}
```

`pull_keeper_history` then calls `refresh_one_advertiser` inside its budget loop —
its behaviour is unchanged (same selection, same per-advertiser work). No logic
is duplicated.

### 2. New selection: the whole corpus

Add `advertisers_with_page(conn) -> list[(advertiser_id, page_id)]`:
every `public.advertisers` row with a non-null `page_id` **and at least one stored
ad** (`JOIN public.ads`), ordered **stalest first** (`MAX(ads.last_seen) ASC`) so a
budget-limited or interrupted run refreshes the most-stale advertisers first.
(Refined during implementation: advertisers with **zero** ads have no running-days
to refresh and are the keeper-backfill's job (`pull_keeper_history`), so the refresh
target is advertisers-with-ads — exactly the v3-relevant set, and it avoids
double-processing keepers.)

### 3. "Trust the listing" vanished-ad flip (user decision 2026-06-16)

After upserting an advertiser's listing in the same txn, flip ads that have
ended-and-delisted:

```sql
UPDATE public.ads
   SET is_active = false,
       end_date  = COALESCE(end_date, last_seen)   -- end_date = last-seen, per the
                                                    -- lifecycle convention (commit 682a70e)
 WHERE advertiser_id = :adv
   AND is_active
   AND external_ad_id <> ALL(:seen_archive_ids)
```

**Truncation guard:** only run the flip when the listing fetch was *complete* —
i.e. pagination terminated before the `hist_pages` cap. If the search returned a
full `hist_pages` worth of results (possibly truncated), we cannot distinguish
"ended" from "on an unfetched later page", so we **skip the flip** for that
advertiser (it self-heals on a later run with a higher cap). `refresh_one_advertiser`
returns `truncated: bool`; the flip is gated on `not truncated`.

**Truncation signal:** `truncated` is true iff the paginator stopped because it
hit `max_pages` (more pages were available) rather than because Meta returned no
next cursor. If `extractor.search` does not already surface page-exhaustion, the
plan adds a minimal `search_paginated(...) -> (ads, exhausted)` wrapper (or a
return flag) so `refresh_one_advertiser` can set `truncated` deterministically —
no count-based heuristic.

`hist_pages` defaults high enough to cover the incumbent ad cap
(`MAX_ACTIVE_ADS_FOR_GEM = 200`) — at ~30 ads/page that is ≤7 pages; use the same
default as keeper history (12) which comfortably covers it, so truncation is rare.

### 4. Runner: `scripts/refresh_corpus_ads.py`

One-shot operator script (mirrors the other operator scripts + the plist-env
pattern):
1. Load the worker's plist env (proxy + MinIO + localhost DB).
2. Build the real extractor (`build_extractor`).
3. `advertisers_with_page` → loop `refresh_one_advertiser` under a request budget
   + `kw_delay` pacing, accumulating stats (`advertisers_done`, `ads_stored`,
   `flipped`, `truncated`, `errors`). `--limit` / `--offset` for probe/resume.
4. Print a summary.

**Operational sequence (documented in the script header, run by the operator):**
pause worker (`launchctl bootout …worker`) → run → restart worker
(`bootstrap`) → global `run_scoring(today)`.

## Files in scope

- `src/agf/discovery/keeper_history.py` — extract `refresh_one_advertiser`;
  `pull_keeper_history` calls it (behaviour unchanged).
- `src/agf/discovery/keeper_history.py` (or a sibling) — `advertisers_with_page`
  + the vanished-ad flip helper.
- `scripts/refresh_corpus_ads.py` — new runner.
- Tests under `tests/discovery/`.

## Out of scope

- No DB migration (uses existing `public.ads` columns).
- No worker-loop integration (one-shot only — user decision 2026-06-16; the
  recurring-stage option was declined).
- No change to scoring, vetoes, or app-store metadata refresh.
- App linkage: reuse the existing `_linked_app_id` lookup; do **not** re-resolve
  store metadata here.

## Testing (TDD)

- `advertisers_with_page` returns every page-bearing advertiser, stalest first;
  excludes advertisers with no `page_id`.
- Vanished-ad flip: given DB ads [A active, B active] and a fresh listing
  containing only A (untruncated), B flips to `is_active=false` with
  `end_date=last_seen`; A stays active. A's `last_seen` is bumped.
- Truncation guard: when the fetch is marked truncated, B is **not** flipped even
  though it's absent.
- `refresh_one_advertiser` upserts present ads (new ad inserted, stopped ad's
  `is_active` updated from the listing) — reuse the existing keeper-history test
  fixtures/fakes (a fake extractor returning AdRecords).
- `pull_keeper_history` regression: its existing tests still pass after the
  refactor (same selection + per-advertiser behaviour).

## Coordination

The parallel session's read-time ad-lifecycle reconstruction (commit 682a70e)
*reads* `ads.is_active`/`end_date`/`start_date`; this refresh *writes* fresher
values underneath it — complementary. We honour its `end_date = last-seen`
convention in the flip. No shared file is edited by both concurrently other than
through normal explicit-path staging on `main`.
