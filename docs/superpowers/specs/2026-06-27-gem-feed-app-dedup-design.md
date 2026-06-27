# Gem feed app-level dedup — design

**Date:** 2026-06-27
**Status:** Approved (brainstorm), pending implementation plan

## Problem

The gems feed shows the **same app many times**. Measured against live prod
(`frontend.emerging_scaling_apps`, 965 rows): **~95 of 965 rows (~10%) are a
duplicate surfacing of an app already in the feed.** 870 truly distinct products.

Root cause: the matview is keyed **one row per `advertiser_id` (Meta page)** —
it has a UNIQUE index on `advertiser_id` because GemScore/scaling signals are
computed per advertiser in `adtech.advertiser_scores`. Nothing collapses the
multiple advertiser rows of a single app into one gem. This is a **missing
dedup layer**, not bad pipeline data.

Two mechanisms, both verified:

1. **Same app, many Meta pages** (dominant — 92 extra rows, 59 apps). One app
   listing (identical `app_id` + `store_id`) run by many distinct `page_id`s,
   each its own row. E.g. *Football Results & Fixtures* = one Android app
   (`app_id 17587`) promoted by 5 burner-named pages; *My General RV* Android =
   12 distinct pages on the same listing. (Affiliate / reseller / burner-page
   pattern.)
2. **Same app on iOS + Android** (+3 rows). Different `app_id`/`store_id`, so an
   app_id-only dedup misses them. Only *My General RV* (12 Android + 8 iOS = 20
   rows) and *bubblegum: read korean* in current data.

## Decisions (locked with user)

- **Dedup identity:** per **product**, merging iOS + Android of the same app.
- **Winner / aggregation:** **aggregate the footprint across pages and re-rank**,
  using the **best page's GemScore** as the product score (not a pipeline
  recompute — see Aggregation).
- **Scope:** Discover feed (`/api/shortlist`) + the review/preference queue +
  a new **"Pages running this app"** section on the gem detail page (so the
  collapsed pages are visible where you drill in). The rest of the detail/
  dossier (ad cards, growth, facts) is unchanged for v1.
- **page_count is display-only** for now — surfaced as a feed badge and on the
  detail page, *not* a ranking signal or scored axis (revisit later).

### Why showing the pages matters

Running one app from many Meta pages is a **deliberate, very common ad
technique**, not a quality tell. Brands do it because a personal-looking page
(e.g. *Football Results & Fixtures*'s "Hernandez Gabriela Ingar", "Martinez
Thibaut Daffie") makes the ads read like an individual person launching them —
which converts better — and/or to keep the ad volume off their main brand
account. The geographic variant (*My General RV*'s "General RV Center Fort
Pierce / Mesa / Fort Myers / …") is the same pattern split by location.

So `page_count` is **not** a good/bad signal — that's exactly why it stays
display-only. The detail page should simply surface the full set of pages (with
each page's ad footprint and Meta Ad Library link) so a human can see the whole
spread behind one app and jump to any page's live ads.

## Approach: a product-grain SQL view (not a matview rewrite)

Add a plain view **`frontend.emerging_scaling_products`** over the existing
per-advertiser matview, grouping to one row per product. The feed/queue repos
swap their `FROM` target to this view.

Why a view over alternatives:
- **Pagination/counts just work.** `/api/shortlist` pushes `LIMIT/OFFSET` into
  SQL and reads `X-Total-Count` from `count(*)`. A view is a normal relation, so
  dedup happens *before* pagination with zero special handling. (Dedup applied
  *after* `LIMIT/OFFSET` would desync the page size and total.)
- **Always consistent** with the matview; no extra refresh wiring (vs a second
  materialized view).
- **Negligible cost** — grouping ~965 rows per query.
- **Low blast radius** — the per-advertiser matview is untouched, so detail/
  dossier pages and `adtech.advertiser_scores` keep working as-is.
- A per-repo CTE was rejected: it would duplicate the dedup logic across
  `shortlist_repo`, `review_repo`, and `preference_repo`.

### Product key

Built inside the view, joining `public.apps` for `developer` (the matview does
not expose it):

- `normalized_name = lower(btrim(app_name))`
- `normalized_dev` = developer, lowercased, with punctuation **and** trailing
  corp suffixes stripped: `llc, inc, ltd, co, gmbh, corp, company, limited`.
- `product_key = 'p:' || normalized_name || '|' || normalized_dev`
- **Fallback:** when `app_name` is null/empty (the 10 web-only rows),
  `product_key = 'adv:' || advertiser_id` (never merges).

This collapses multi-page dupes (same `app_id` ⇒ same name+dev) **and** merges
iOS+Android (verified: "general rv center" vs "general rv center **inc.**" both
strip to "general rv center"; "bubblegum languages" vs "…**llc**" both strip to
"bubblegum languages"). The `+dev` guard stops two genuinely different apps that
happen to share a name from merging — within-store name collisions are 1 in the
whole `public.apps` table, so false-merge risk is near zero.

Expected result: **965 → 870 rows** (92 from multi-page collapse, 3 from
cross-store merge).

### Representative row

Per `product_key`, pick the advertiser row with the **highest `gem_score`**
(`row_number() OVER (PARTITION BY product_key ORDER BY gem_score DESC NULLS
LAST, active_ad_count DESC, advertiser_id)`, keep `rn = 1`). Its `advertiser_id`
stays the row's id, so **existing detail deep-links and ratings still resolve**.
For iOS+Android the higher-scoring store listing represents the product (its
`app_id`, `store`, `store_id`, `icon_url`, `tagline`, install/rating stats).

### Aggregation across pages

Per product:
- **Footprint counts → `SUM`** across all pages: `active_ad_count`, `ads_seen`,
  `new_ads_4w`, `longrun_ad_count`. `max_longevity_days → MAX`.
- **Cohort-normalized axes** (`scaling_velocity`, `under_radar`, `demand`,
  `whitespace`, `funnel_intent`) and **`gem_score` → the representative (best)
  page's value.** We do **not** recompute GemScore at product grain — those axes
  are per-advertiser cohort percentiles; a true recompute means re-running the
  scoring pipeline at a new grain (a separate, larger increment).
- All other display columns (`cohort`, `niche_keyword`, `niche_advertiser_count`,
  `installs*`, `rating*`, `pct_change`, `first_*`, `weeks_observed`, `alert`,
  `incubating`, `funnel_url`, `web_only`, `tagline`) → representative row.

### New columns surfaced

- `page_count` — distinct advertiser rows merged into this product.
- `stores` — `'ios'`, `'android'`, or `'ios+android'`.

The view emits **exactly the current `mvCols` set** (so every existing
`WHERE`/`ORDER` clause keeps working unchanged) **plus** `page_count` and
`stores`, with `active_ad_count` / `ads_seen` / `new_ads_4w` /
`longrun_ad_count` / `max_longevity_days` replaced by their aggregates.

## Backend changes (`backend/`, Go)

1. **New migration** `db/schema/<next>_emerging_products_view.sql` —
   `CREATE VIEW frontend.emerging_scaling_products` per the SQL above.
2. **`repositories.go`** — add `page_count, stores` to `mvCols`; the existing
   column names are unchanged (aggregates are aliased back to the same names in
   the view).
3. **`shortlist_repo.go`** — swap the two `FROM frontend.emerging_scaling_apps`
   to `frontend.emerging_scaling_products` (the page query, the `count(*)`, the
   category-count query, and the genre-filter join — the `public.apps` join on
   `app_id` still resolves via the representative `app_id`). In
   `buildShortlistOrder`, add **`active_ad_count DESC`** as the footprint
   tiebreak (now a summed value) after the primary sort key.
4. **`models.go`** — add `PageCount int` (`page_count`) and `Stores string`
   (`stores`) to `ShortlistRow`; add the same to the scan rows.
5. **Review/preference queue** — in `preference_queue.go assembleQueue`, add an
   **`app_id`-level dedup pass** (keep the highest-`p_like` card per non-null
   `app_id`) before the per-niche cap. This removes the dominant multi-page dupe
   source (92 of 95) with a tiny, low-risk change; full cross-store name+dev
   merge stays a feed-view concern for v1.
6. **Detail page — "Pages running this app".** Endpoint stays
   `GET /api/gems/:advertiser_id` → `Dossier(advertiserID)`. Add a new query that,
   given the dossier's representative `app_id`, resolves the product's app_id set
   (all `public.apps` sharing the same `normalized_name + normalized_dev` key —
   same expression as the view, so iOS+Android siblings are included) and lists
   every advertiser that ran ads for any of them:
   ```sql
   SELECT adv.id AS advertiser_id, adv.page_id, adv.page_name, adv.page_category,
          count(a.id)                              AS total_ads,
          count(a.id) FILTER (WHERE a.is_active)   AS active_ads,
          min(a.start_date)                        AS first_ad,
          max(a.end_date)                          AS last_ad
   FROM public.ads a
   JOIN public.advertisers adv ON adv.id = a.advertiser_id
   WHERE a.app_id = ANY(:product_app_ids)
   GROUP BY adv.id, adv.page_id, adv.page_name, adv.page_category
   ORDER BY active_ads DESC NULLS LAST, total_ads DESC
   ```
   Add `Pages []AdvertiserPage` to `GemDossier` (`models.go`) — `AdvertiserPage{
   AdvertiserID, PageID, PageName, PageCategory, ActiveAds, TotalAds, FirstAd,
   LastAd}`. Verified data is clean (see probe: 12 named pages with active/total
   counts and date ranges). The Meta Ad Library link is built from `page_id`
   (reuse the helper already used in `GemHeader`). The dossier ad-card grid stays
   representative-advertiser-only for v1 (each page's full ads are one click away
   via its library link).

## Frontend changes (`web/`)

`web/src/components/discover/GemTable.tsx` — next to the `web_only` badge, render
plain muted badges grounded in real counts:
- `{page_count} pages` when `page_count > 1`
- `iOS + Android` when `stores === 'ios+android'`

Plain, professional copy per the project voice rule; no derived adjectives, no
restating an adjacent number. Add `page_count` / `stores` to the web contract
(`api.ts` `ApiShortlistRow`, `contracts.ts` `ShortlistRow`, `adapters.ts`).

**Detail page — `PagesSection.tsx`.** A new section in `GemDetail.tsx`, rendered
only when `pages.length > 1`, below `AdsSection`. A compact table, one row per
page, sorted by active ads:

| Page | Ads (active / total) | Running | |
|------|----------------------|---------|---|
| General RV Center Fort Pierce · *category* | 14 / 15 | Mar 4 – Jun 20 | Ad Library ↗ |

Heading: **"Pages running this app"** with the count (`page_count`). Plain copy,
real data only. Add the `pages` array to `ApiGemDossier` (`api.ts`),
`GemDossier` (`contracts.ts`), and `adaptDossier` (`adapters.ts`).

## Testing

- **DB (pg):** a fixture with (a) one app on 3 Meta pages → 1 product row,
  `page_count = 3`, `active_ad_count` = sum; (b) the same app on iOS+Android with
  dev "X" vs "X LLC" → 1 product row, `stores = 'ios+android'`; (c) two different
  apps sharing a normalized name but different developers → **2** rows (no false
  merge); (d) a web-only null-app row → its own row. Assert the representative is
  the highest-`gem_score` page and that its `advertiser_id`/`app_id` are the
  representative's.
- **Repo:** `Shortlist` returns deduped rows; `total` (X-Total-Count) equals the
  product count; category counts are deduped; the footprint tiebreak orders a
  many-page app above an equal-score single-page app.
- **Queue:** `assembleQueue` emits at most one card per `app_id`.
- **Dossier pages:** for a representative advertiser whose product spans iOS +
  Android, the pages query returns advertisers from **both** app_ids, grouped one
  row per advertiser with correct active/total/date aggregates, ordered by active
  ads. A single-page product returns one page (section hidden in UI).
- **Web:** feed badge renders for `page_count > 1` and `stores === 'ios+android'`,
  hidden otherwise; `PagesSection` renders the table when `pages.length > 1` and
  is hidden when ≤ 1.

## Live verification

After deploy: `/api/shortlist` total ≈ 870 (was 965); spot-check *My General RV*
appears once with `stores = 'ios+android'` and a summed `active_ad_count`;
*Football Results & Fixtures* appears once with `page_count = 5`; confirm a
known same-name/different-developer pair stays as two rows. Open the *My General
RV* detail page and confirm the "Pages running this app" section lists all ~20
pages across both stores with per-page ad counts and working Ad Library links.

## Out of scope (v1)

- Recomputing GemScore at product grain (separate increment).
- Cross-store name+dev merge inside the review/preference queue (feed only).
- Fuzzy name matching beyond exact normalized-name + dev-token guard.
