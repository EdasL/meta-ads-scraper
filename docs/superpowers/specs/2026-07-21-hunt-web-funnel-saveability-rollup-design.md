# Hunt web-funnel saveability + per-page rollup — design (2026-07-21)

## Problem

In the hunt results grid, web-funnel cards render grey and un-saveable, and the same
advertiser appears as many duplicate cards (e.g. `runna` as three store rows; `Coopah
Running` five times in one round). Root cause, from the 2026-07-20 investigation:

- **Everything on a card hangs off `hr.kept_advertiser_id`.** `ListHuntResults`
  (`backend/repositories/hunt_repo.go`) LEFT JOINs `advertiser_scores`,
  `emerging_scaling_apps` (icon/name/tagline), `saved_gems`, and `app_ratings` all on
  `hr.kept_advertiser_id`. Web hunt rows have `candidate_id = NULL`, so
  `apply_triage_to_results` (`src/agf/db/hunts.py`) never backfills `kept_advertiser_id` —
  it stays null even after the row's funnel is later confirmed and an advertiser is minted
  on the **same `page_id`**. Null id → every join misses → grey, un-saveable card.
- **No rollup anywhere.** `ListHuntResults` is `ORDER BY hr.id` with no grouping; the
  frontend (`web/src/components/hunt/RoundBlock.tsx`, one block per round via
  `HuntView.tsx`) only splits on-/off-target and renders every row as its own card. One
  page found across stores/creatives in a round shows as N duplicate cards.

The hunt view is **one `RoundBlock` per round**, each rendering that round's results
(`resultsFor(roundId)`), so the duplicates in the screenshot are within a single round.

## Scope

Two coupled changes, one plan. Approved decisions:
- Rollup granularity: **per `page_id`, within each round** (rounds stay separate blocks).
- Resolve a web row to its advertiser **whenever an advertiser exists for the page**
  (triage/relevance stays a separate axis).
- Keep the existing on-/off-target split; roll up **within** each bucket.

Out of scope: cross-round dedup; the continuous confirm daemon (separate spec,
`2026-07-20-funnel-confirm-daemon-design.md`); any schema change.

## Part (b) — resolve advertiser by `page_id` (backend, read-time)

Edit `ListHuntResults` in `backend/repositories/hunt_repo.go`.

Compute an **effective advertiser id** and drive all downstream joins off it:

```
eff_adv_id = COALESCE(
  hr.kept_advertiser_id,
  (SELECT a.id FROM public.advertisers a WHERE a.page_id = hr.page_id LIMIT 1)
)
```

- Use a **scalar subquery** (LIMIT 1), NOT a JOIN, so a `page_id` that maps to more than
  one advertiser row can never fan a hunt row into duplicates. (`advertisers.page_id` is
  effectively 1:1, but the subquery is safe regardless.)
- Replace `hr.kept_advertiser_id` with `eff_adv_id` in the four LEFT JOIN conditions
  (`advertiser_scores`, `emerging_scaling_apps`, `saved_gems`, `app_ratings`) and return
  `eff_adv_id AS kept_advertiser_id` in the SELECT list.
- Structure it so `eff_adv_id` is computed once (a `LEFT JOIN LATERAL` resolving the
  page→advertiser id, or a CTE/subselect), then joined — keep it a single readable query.

Effect: any web row whose funnel has been confirmed/minted now returns the advertiser's id,
so icon + GemScore + heart + save + rating all light up. Read-time → self-heals as the
confirm daemon mints more advertisers. No migration; `apply_triage_to_results` unchanged.

Frontend already reads `kept_advertiser_id` for the heart gate (`RoundBlock.tsx`) and the
`/gems/{id}` link, so a resolved id flows through with no frontend change for (b).

## Part (c) — per-page rollup within a round (frontend)

Edit `web/src/components/hunt/RoundBlock.tsx`.

After `splitByRelevance`, group each bucket's rows by `page_id` and render one card per
page. A small pure helper (unit-testable) collapses a page's rows into a view model:

- **name / icon_url / gem_score / kept_advertiser_id / cohort / tagline / relevance**: from
  the representative row — the group's row that resolved to an advertiser
  (`kept_advertiser_id != null`), else the first row.
- **stores**: distinct `store` values in the group → platform badges (App Store · Play
  Store · Web), each badge linking to that store row's `link_url`.
- **fold_count**: number of rows folded (`> 1` shows a `·N ads` chip).
- **triage**: `kept` if any row in the group is kept, else the representative's triage.
- **page_id / page / source_term / Ads link**: from the representative.

`ResultCard` renders the collapsed view model: heart shows when `kept_advertiser_id != null
&& userEmail` (unchanged gate, now satisfied for resolved web pages); save keys on the
resolved `advertiser_id`. All rows for a `page_id` resolve to the same advertiser, so a page
collapses to exactly one card.

## Data flow

`ListHuntResults` (resolve eff_adv_id per row) → API rows (one per hunt_result row, now with
`kept_advertiser_id` populated for resolved web pages) → `RoundBlock` groups by `page_id`
within each relevance bucket → one `ResultCard` per page.

## Testing

- **Go** (`backend/repositories/hunt_repo_test.go` or equivalent): seed a web `hunt_result`
  with `kept_advertiser_id = NULL` and an `advertisers` row on the same `page_id` that has an
  `emerging_scaling_apps` + `saved_gems` entry; assert the returned row carries that
  advertiser id, `icon_url`, `gem_score`, and `saved = true`. Assert no row fan-out when a
  `page_id` appears once.
- **Frontend** (RoundBlock helper unit test): given 3 rows for one `page_id` (App Store
  resolved + Play + Web) and 2 rows for another, assert two cards; the first card has the
  union of 3 store badges, `fold_count = 3`, and the resolved row's icon/gem_score/advertiser
  id; heart gate satisfied. A page with no resolved advertiser → one grey card, no heart.

## Risks / edge cases

- **Fan-out**: mitigated by the scalar subquery (Part b) — verify in the Go test.
- **Mixed triage within a page**: collapses to `kept` if any kept (spec above); acceptable.
- **Two different pages, same advertiser** (rare — multi-page-per-app): stays two cards
  (rollup is by `page_id`, not advertiser). Consistent with the approved per-page decision.

## Status
Design approved 2026-07-21. Not yet built. Prereq (advertisers being minted from web
funnels) shipped 2026-07-20; more will resolve as the confirm daemon drains the backlog.
