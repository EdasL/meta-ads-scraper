# Gem signals, deep — frozen contract spec (2026-06-10)

Goal: the detail page must let the user judge "is this a hidden gem, and why" from
first-party signals: true ad count, weeks running, ad-count growth, store presence +
in-UI App Store preview (info / screenshots / reviews), app-side growth (installs /
rating-count over time), data freshness, and per-ad metrics where they exist.

Live-probed facts this spec is built on (do NOT re-litigate):
- Meta exposes NO impressions/spend for US ads (NULL); EU ads carry `eu_total_reach`.
  `page_like_count` IS present at `snapshot.page_like_count` (already parsed into
  `AdRecord.page_like_count`, currently dropped).
- Meta has no search-level total count; the true per-advertiser active-ad count comes
  from `view_all_page_id` bounded pagination with early exit (`src/agf/extract/search.py`
  already supports `view_all_page_id` + `max_pages`).
- iTunes Lookup API: batched ids work; carries `artworkUrl512`, `screenshotUrls`
  (CAN be empty while `ipadScreenshotUrls` is populated — fall back), `description`,
  `averageUserRating`, `userRatingCount`, `formattedPrice`, `primaryGenreName`,
  `releaseDate`, `trackViewUrl`. ~20 calls/min official limit; batch ids per call.
- Apple reviews RSS works ONLY in the lowercase paged form:
  `https://itunes.apple.com/us/rss/customerreviews/page=N/id=<id>/sortby=mostrecent/json`
  (camelCase `sortBy` on paged URLs silently returns an empty feed). 50/page, 10-page cap.
  Per-entry fields (under `.label`): `title`, `content`, `im:rating`, `author.name`,
  `updated`, `id`.
- google-play-scraper (already used by `src/agf/enrich_full/sidecar/gp_lookup.js`)
  also returns `maxInstalls` (near-exact live install count), `installs` bucket string,
  `reviews` count, `histogram`, `screenshots`, `icon`, `priceText`; `gplay.reviews()`
  gives recent reviews. Zero new infrastructure.
- Apple publishes NO download counts anywhere. iOS download-trend proxy =
  `userRatingCount` delta over time. Label it as a proxy in the UI.
- mwm.ai is OUT: curated ~126-app incumbent directory, downloads only as coarse
  buckets ("500M+"), no time series, proxy-gated. Not usable for emerging apps.

## DB (migrations 107–109 — ALREADY WRITTEN AND APPLIED; do not edit)

- `public.apps` += `screenshots JSONB` (JSON array of URL strings), `price_label TEXT`.
- `adtech.app_metrics (app_id, snapshot_date DATE, rating REAL, rating_count BIGINT,
  installs BIGINT, PK(app_id, snapshot_date))` — one row per app per day.
- `public.app_reviews (app_id, review_id TEXT, author, rating SMALLINT, title, content,
  reviewed_at TIMESTAMPTZ, fetched_at, PK(app_id, review_id))`.
- `public.advertisers` += `page_like_count BIGINT`, `meta_active_ads INTEGER`,
  `meta_active_ads_capped BOOLEAN DEFAULT FALSE`, `meta_active_ads_checked_at TIMESTAMPTZ`.

## Layer 1 — Python pipeline (owns: `src/agf/**`, `scripts/**`, `tests/**`, `ops/RUNBOOK.md`)

1. **Store metadata enrichment** (`src/agf/enrich_full/appstore.py` + sidecar):
   - iOS lookup also maps: `artworkUrl512` → `apps.icon_url`; `screenshotUrls`
     (fallback `ipadScreenshotUrls`) → `apps.screenshots`; `formattedPrice` →
     `apps.price_label`.
   - `gp_lookup.js` additionally emits: `icon`, `screenshots`, `maxInstalls`,
     `installs` (bucket string), `priceText`; python maps them to `icon_url`,
     `screenshots`, `apps.installs` (= `maxInstalls`), `price_label`.
   - `upsert_app` (`src/agf/db/repo.py`) extended for the new columns; sets
     `updated_at = now()` on refresh.
2. **Reviews**: fetch ~20 most-recent reviews per app (iOS: reviews RSS page 1,
   lowercase form, country=us; Android: `gplay.reviews()` NEWEST via sidecar — a small
   `gp_reviews.js` or a flag on `gp_lookup.js`). Upsert into `public.app_reviews`
   keyed (app_id, review_id). Missing/empty feeds are fine (emerging apps may have 0).
3. **App metric snapshots** (new `src/agf/enrich_full/app_refresh.py`):
   `refresh_apps(engine, only_ids=None)` — for every app (or `only_ids`, which
   DB-backed tests MUST use): re-fetch store metadata (BATCH iOS lookups,
   comma-separated ids, stay under ~20 calls/min), update `public.apps`, insert
   today's `adtech.app_metrics` row (rating, rating_count, installs), refresh reviews.
   Idempotent per day (upsert on PK). CLI: `scripts/refresh_apps.py`.
4. **True ad count** (`src/agf/extract/search.py`): `count_active_ads(session, page_id,
   *, cap=250, ...) -> tuple[int, bool]` — paginate `view_all_page_id`, count distinct
   ad archive ids, early-exit when count ≥ cap → `(count, True)`. Reuse existing
   pacing/jitter/backoff conventions (facade/backoff). Persist via new
   `repo.set_meta_active_ads(conn, advertiser_id, count, capped)` (sets checked_at).
   Wire into the per-advertiser enrich path (`scripts/scrape_real.py`) + backfill CLI
   `scripts/count_ads.py`.
5. **page_like_count**: `repo.upsert_advertiser` persists `AdRecord.page_like_count`
   (update only when incoming value is non-NULL).
6. **Worker wiring**: add app refresh to the daily loop where ad snapshots run
   (follow existing worker/runbook conventions; update `ops/RUNBOOK.md`).
7. Tests: unit tests for parsing/mapping; DB tests scoped via `only_ids` sentinel rows
   (NEVER global). Live one-shot verification against 1–2 real apps is required
   (Apple/Google endpoints are free; no proxy needed).

## Layer 2 — Go API (owns: `backend/**`)

JSON contract (snake_case, additive only):

- `AppInfo` += `description string`, `icon_url string`, `screenshots []string`,
  `price_label string`, `installs *int64`, `updated_at *time`, and
  `reviews []AppReview` (top 5 by `reviewed_at` DESC).
- `AppReview = {author string, rating *int, title string, content string,
  reviewed_at *time}`.
- `GemDossier` top level += `ads_last_seen *time` (advertisers.last_seen),
  `page_like_count *int64`, `meta_active_ads *int`, `meta_active_ads_capped bool`,
  `meta_active_ads_checked_at *time`.
- `CreativeInfo` += `ad_started_on *date` (ads.start_date), `ad_active *bool`,
  `ad_days_running *int` (active: today−start_date; ended: end_date−start_date),
  `publisher_platforms []string`, `eu_total_reach *int64` (LATEST
  `adtech.ad_metrics.eu_total_reach` for that creative's ad, NULL if none).
- New endpoint `GET /api/gems/{advertiser_id}/app-metrics` →
  `[]AppMetricPoint = {app_id int, store string, snapshot_date date, rating *float,
  rating_count *int64, installs *int64}` ordered by snapshot_date ASC.

Conventions: GORM `tx.Raw` with `IN (?)` (never `ANY(?)`); follow existing repo/
controller layout (`shortlist_repo.go` / `dossier_repo.go` / gems controller).
`go build ./... && go test ./...` must pass; verify endpoints with curl against the
local stack (AGF_DATABASE_URL, pg on :5433).

## Layer 3 — Web UI (owns: `web/**`)

Data plumbing: extend `types/api.ts` + `types/contracts.ts` with ALL new fields
(stop dropping `rating`/`rating_count`/`developer`/`genre`/`release_date` in
`adapters.ts` — carry the full AppInfo). `lib/api.ts getGem()` also fetches
`/app-metrics` (parallel to `/scaling`) and merges into the dossier as
`app_metrics: AppMetricPoint[]`. Null-safe adaptation; update adapter tests.

UI (GemDetail page; plain professional voice everywhere, grug ONLY inside
GrugExplainer; follow design tokens — paper surfaces, ink borders, shadow-hard,
mono caps section headers like the existing "GO LOOK" / "THE ADS"):

1. **THE APP section** (new `AppStorePreview` component; render only when
   `apps.length > 0`): icon + name + developer; rating as stars glyph + numeric +
   `(rating_count formatted)`; genre, price_label, release date; installs when
   present ("≈116M installs · Play"); description clamped (~4 lines) with
   plain-voice expand; horizontal screenshots strip (lazy `<img>`, fixed height,
   scroll-x; skip section row if no screenshots); store link button (reuse
   buildGemLinks); recent reviews (top 3: stars, title, content clamp, author,
   relative date; empty state: "No written reviews yet — common for new apps.");
   freshness footer: "Store data checked {timeAgo(updated_at)}".
2. **App growth chart**: reuse `SketchLineChart` — iOS: rating_count series labeled
   "RATINGS OVER TIME (download proxy)"; Android with installs: installs series
   labeled "INSTALLS OVER TIME". <2 points → keep chart area with existing
   early-state copy pattern ("Early — first snapshot {date}; trend appears after a
   few days.").
3. **Store-presence badge** in the header line: `iOS APP` / `ANDROID APP` /
   `WEB ONLY` (web-only exists today — make it a consistent three-way chip).
4. **True ad count**: where ad count is shown on the detail page (GemVerdict line /
   grug stats), prefer `meta_active_ads` ("N ads in library", "{cap}+ ads in
   library" when capped) and fall back to `active_ad_count`. Discover cards keep
   `active_ad_count` (matview) as today.
5. **GemFacts** new rows (conditional, right-aligned mono values like existing):
   `page likes` (formatted page_like_count), `ads checked` (timeAgo(ads_last_seen)),
   `store checked` (timeAgo(apps[0].updated_at)), `ads in library`
   (meta_active_ads w/ "+" when capped).
6. **EvidenceReel per-ad meta**: under each creative polaroid add a small mono meta
   line: "since {M/D} · {N}d" + " · EU reach {compact}" when eu_total_reach present
   + platforms when present. US ads have no impressions/spend — never fake them.
7. Tests: update/extend `adapters.test.ts`, `why.test.ts` as needed;
   typecheck + lint + unit tests + `next build` must pass.

## Verification (assembled, after all layers)

Local stack: pg :5433 / minio :9010 up; Go API rebuilt → `/tmp/agf-api` with
`AGF_DATABASE_URL`; web dev `:3000` with `NEXT_PUBLIC_API_URL=http://localhost:8080/api`
(must end in `/api`). Live e2e: refresh a real linked app, hit dossier + app-metrics
endpoints, load the detail page, screenshot. This gate is owned by the orchestrator,
not the layer agents.
