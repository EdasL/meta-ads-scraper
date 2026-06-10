# Growth signals redesign — design

Date: 2026-06-10
Status: approved for implementation (autonomous session; user goal statement is the requirement)

## Problem

The user flagged three issues with how we show app growth:

1. **Zero-baseline artifact.** Most feed advertisers show "0 → N ads in 16
   weeks" with percents like +1150%. Verified in the live DB: 9 of the top-10
   feed advertisers have `first_count = 0` in `adtech.advertiser_growth`.
   They genuinely had no ads 16 weeks ago — they are new — but
   `growth.py` anchors pct_change on the first *non-zero* week, so a 2→25
   launch ramp renders as a giant percent. The percent is hypersensitive to a
   tiny base and reads as fake.
2. **Arbitrary 16-week window.** `reconstruct_weekly_ad_counts(weeks=16)` is
   a hardcoded default (`src/agf/tracker/history.py:25`); the entire
   `adtech.app_advertiser_metrics` table is exactly 16 week_starts. The window
   also *hides* history: Simbo has 120 stored ads, 119 ended before the window
   opens, so its chart is a flat line at 1 instead of the real story
   ("tested a wave of ads, kept one winner").
3. **Survivorship / creative testing.** Advertisers launch many ads, kill
   losers, keep winners (median ended ad lives 16 days; p75 = 41). A bare
   active-count line cannot distinguish "scaling spend" from "mid-test churn".
   Active-ad count alone is a weak growth measure.

### Verified data facts (live DB, 2026-06-10)

- `public.ads`: ~5.7k ads, 170 advertisers; **100% have start_date AND
  end_date** (for active ads end_date = last day seen delivering, p50 1.5d
  ago). Ended = 57%.
- The stored weekly series is interval-reconstructed (verified: recount via
  `start_date < week_end AND end_date >= week_start` reproduces it). Ended
  ads ARE included; the bias is the fixed window + the percent math, not the
  reconstruction.
- Observed (snapshot) history starts 2026-06-09; everything earlier is
  reconstruction. `adtech.ad_metrics` is empty.
- Reconstruction reaches back fine: 30.6% of ads started ≥6 months ago.
  Censoring: per-advertiser scrape cap of 120 ads; Meta delists ended US ads.

## Approaches considered

1. **Display-only patch (web).** Hide the garbage percent, relabel the
   caption. Cheap but leaves the fixed window, no lifecycle insight; the
   chart still manufactures hockey sticks for window-relative reasons.
2. **Read-time computation in Go from `public.ads` + lifecycle stats +
   UI redesign.** (CHOSEN) The intervals are the source of truth and are
   already complete; compute the weekly series per-request with an
   advertiser-relative window, add launched/ended decomposition and a
   lifecycle summary. Pipeline untouched except a percent-floor hygiene fix,
   so scoring (which is fine) keeps its series.
3. **Pipeline rewrite.** Extend the backfill table with launched/ended
   columns and longer adaptive history. More moving parts, worker redeploy,
   stale until rerun, and it perturbs scoring inputs for no display benefit.

## Design

### Principles

- Never show a percent computed from a base < 5 ads.
- Window is advertiser-relative: from the week of their first stored ad,
  capped at 26 weeks. Always say when the first ad was seen.
- Show composition, not just level: ads launched and ended per week, plus
  how many current ads have survived 30+ days (the user's "winners left
  running" — that's a *signal*, surface it).
- Copy stays plain and grounded: counts from real ads, no interpretive
  labels ("proven", "steady").

### Backend (Go) — read-time series + lifecycle

**`backend/repositories/scaling_repo.go`** — replace the
`app_advertiser_metrics` read with an interval reconstruction over
`public.ads`:

```sql
WITH bounds AS (
  SELECT GREATEST(date_trunc('week', min(start_date)),
                  date_trunc('week', now()) - interval '25 weeks') AS w0,
         date_trunc('week', now()) AS wn
  FROM public.ads WHERE advertiser_id = @id
), weeks AS (
  SELECT generate_series(w0, wn, interval '1 week') AS week_start FROM bounds
)
SELECT w.week_start::date AS week_start,
  count(a.id) FILTER (WHERE a.start_date < w.week_start + interval '7 days'
    AND (CASE WHEN a.is_active THEN now() ELSE a.end_date END) >= w.week_start)
    AS active_ad_count,
  count(a.id) FILTER (WHERE a.start_date >= w.week_start
    AND a.start_date < w.week_start + interval '7 days') AS launched_count,
  count(a.id) FILTER (WHERE NOT a.is_active AND a.end_date >= w.week_start
    AND a.end_date < w.week_start + interval '7 days') AS ended_count
FROM weeks w LEFT JOIN public.ads a ON a.advertiser_id = @id
GROUP BY 1 ORDER BY 1
```

Notes: active ads treat end as `now()` (their stored end_date is merely
"last seen delivering"). `distinct_creative_count` is unused by the chart —
keep the API field for contract compatibility, serve 0, mark deprecated in
the contract comment.

**New lifecycle summary** on the gem detail payload (`ad_lifecycle`):

```json
{
  "ads_seen": 120,
  "active_now": 1,
  "running_30d_plus": 1,
  "median_run_days": 16,
  "first_ad_at": "2025-07-10",
  "launched_4w": 0,
  "ended_4w": 2
}
```

One aggregate over `public.ads` per advertiser: `running_30d_plus` = active
AND start_date ≤ now()−30d; `median_run_days` = percentile_cont(0.5) of
(end−start) over ended ads, null when ended < 5; `launched_4w`/`ended_4w`
use a 28-day window.

**`ScalingPoint`** (backend/models) gains `launched_count`, `ended_count`.

### Card-level fields — matview migration 114

`db/schema/114_emerging_matview_ad_lifecycle.sql` redefines
`frontend.emerging_scaling_apps` (follow 111's pattern) adding a LATERAL
over `public.ads`: `first_ad_at` (date), `longrun_ad_count` (active ≥30d),
`new_ads_4w`, `ads_seen`. Feed repo/models pass them through.

### Pipeline hygiene (Python, minimal)

`src/agf/tracker/growth.py`: pct_change becomes NULL when the non-zero base
< 5 (floor constant, e.g. `MIN_PCT_BASE = 5`). The display layer guards too —
old rows keep flowing until the worker recomputes. No other pipeline change:
the 16-week backfill default stays (it feeds scoring, which is out of scope).

### Web UI

**Detail page (`EvidenceReel.tsx` AdGrowth → "Ad activity")**

- Chart: keep the weekly active-ads line; add thin muted bars at the bottom
  for `launched_count` (own scale). Tooltip keeps naming launched ads; add
  an "N ended this week" line when `ended_count > 0`.
- Caption (replaces "N → M ads over X weeks · MxY"):
  `First ad <Mon YYYY> · <ads_seen> ads seen · <K> running 30+ days`
  (omit clauses whose data is missing). Optional percent only when base ≥ 5.
- A short muted note clarifies what the chart counts:
  "Ads running each week, from <ads_seen> ads in the Ad Library record,
  including ended ones." Plus `median ad runs <X> days` chip when ended ≥ 5.

**Discover card (`GemCard.tsx` + `why.ts` growthLine)**

- No percent when `first_count < 5`. Replace "0→25 ads in 16 weeks" with
  window-honest copy anchored on first_ad_at:
  `↗ 2→25 ads since Mar` (delta glyph from latest−first).
- Secondary muted line when `longrun_ad_count > 0`:
  `<K> running 30+ days`.

**Saved card**: unchanged (snapshot-at-save semantics are correct).

**Contracts/adapters**: extend `ApiScalingPoint` (launched_count,
ended_count — optional, default 0), gem detail `ad_lifecycle`, feed row
(first_ad_at, longrun_ad_count, new_ads_4w, ads_seen). Mirror in UI types.

### Why this answers the goal

- 0→N hockey sticks: window starts at the advertiser's first ad; percents
  from tiny bases are gone; "since <month>" makes recency explicit —
  newness reads as newness, not 1150% growth.
- "Why 16 weeks": the window is now advertiser-relative, capped at 26 weeks
  for readability, and labeled with the first-ad date.
- Testing-then-winners: launched/ended bars make culls visible; "K running
  30+ days" and median run length quantify surviving winners — churn and
  scaling no longer look identical. Combined with the existing store-side
  growth section, ad count is no longer presented as a standalone measure.

## Testing

- Go: repo-level test for the reconstruction SQL semantics if a test harness
  exists for repositories; otherwise build + vet.
- Python: unit test for the pct floor in growth tests.
- Web: type-check + existing vitest suite; extend why.ts tests for the new
  growthLine rules.
- Manual: run live stack, screenshot detail (Simbo — the decline story;
  iFIT — the ramp story) and Discover cards.

## Rollout

1. Land code; apply migration 114 to the live DB; refresh matview.
2. One-shot recompute of advertiser_growth (or let the worker cycle do it;
   display guard covers the gap).
