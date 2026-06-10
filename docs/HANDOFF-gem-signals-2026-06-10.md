# Handoff — Gem Signals Deep (2026-06-10)

**Status: SHIPPED + live-verified.** 4 commits on `main`: `0c5f366` (migrations 107–109 + spec) · `2f5ad53` (Python pipeline) · `bf90eb7` (Go API) · `e333bf6` (web UI). Frozen contract spec: `docs/superpowers/specs/2026-06-10-gem-signals-deep.md` (single source of truth for column names / JSON shapes / endpoint contracts — read it before touching any of this).

**Continues:** `docs/HANDOFF-scrape-rethink-2026-06-09.md` (this doc resolves its open questions 1–3) and the gem-relevance UI overhaul (2026-06-09, still partly uncommitted — see "Working-tree state" below).

---

## TL;DR

The gem detail page is now a full evaluation kit. For every gem you can see: the **true Meta ad-library count** (not the stored-ads undercount), **how long ads have been running**, the **weekly ad-count growth chart**, whether the product is an **iOS app / Android app / web-only funnel** with store links, an **in-UI App Store preview** (icon, rating + count, genre, price, description, screenshots, top-3 recent reviews), the **app-side growth chart** (installs / rating-count over time), **data freshness** ("ads checked 19h ago / store data checked 13m ago"), and **per-ad metadata** on each creative (start date, days running, EU reach, platforms). Screenshots from the live run: `gem-detail-signals-1220.jpeg` (Whiteout Survival), `gem-detail-signals-1424-v2.jpeg` (Whatnot, capped-count + richest-store paths) in the repo root.

---

## What shipped, by layer

### DB (migrations 107–109, applied to local pg)
- `public.apps` += `screenshots JSONB` (array of URLs), `price_label TEXT`.
- `adtech.app_metrics (app_id, snapshot_date, rating, rating_count, installs)` — one row per app per day; the app-side growth series.
- `public.app_reviews (app_id, review_id, author, rating, title, content, reviewed_at, fetched_at)`.
- `public.advertisers` += `page_like_count`, `meta_active_ads`, `meta_active_ads_capped`, `meta_active_ads_checked_at`.

### Python pipeline
- `src/agf/enrich_full/appstore.py` + sidecars: iOS lookup now maps `artworkUrl512`→icon, `screenshotUrls` (fallback `ipadScreenshotUrls`)→screenshots, `formattedPrice`→price_label; `gp_lookup.js` emits icon/screenshots/`maxInstalls`/priceText.
- `src/agf/enrich_full/reviews.py` + `sidecar/gp_reviews.js`: ~20 most-recent reviews per app (iOS RSS, Android `gplay.reviews` NEWEST).
- `src/agf/enrich_full/app_refresh.py`: `refresh_apps(engine, only_ids=None)` — batched iOS lookups (paced under ~20 calls/min), daily `adtech.app_metrics` snapshot (idempotent on PK), review refresh. CLI: `scripts/refresh_apps.py`.
- Worker step 1.7 (`src/agf/workers/main.py`): app refresh scoped to apps missing today's snapshot (hourly sweeps hit stores ≤1×/app/day) + retention prune (`AGF_APP_HISTORY_KEEP_DAYS`, default 730).
- `count_active_ads` (`src/agf/extract/search.py` + facade): bounded `view_all_page_id` pagination, dedup on archive id, early exit at cap (default 250) → `(count, capped)`. Persisted via `repo.set_meta_active_ads`. Wired into `scripts/scrape_real.py` (step 3.5) + backfill CLI `scripts/count_ads.py` (TTL-gated, DEMO-excluded).
- `repo.upsert_advertiser` persists `AdRecord.page_like_count` (non-NULL COALESCE update).

### Go API (additive JSON, snake_case)
- `AppInfo` += description, icon_url, screenshots, price_label, installs, updated_at, `reviews` (top-5 by reviewed_at).
- `GemDossier` += `ads_last_seen`, `page_like_count`, `meta_active_ads` (+`_capped`, `_checked_at`).
- `CreativeInfo` += `ad_started_on`, `ad_active`, `ad_days_running`, `publisher_platforms`, `eu_total_reach` (latest non-NULL per ad).
- New: `GET /api/gems/{advertiser_id}/app-metrics` → daily series, ASC.
- Serialization convention: absent strings → `""`, absent arrays → `[]` (StringList over JSONB / text[]); the web adapter treats those as absent.

### Web UI
- `AppStorePreview.tsx` ("THE APP"): previews the **richest** store entry (screenshots > reviews > installs > description), not `apps[0]`; app growth chart on `SketchLineChart` — iOS series is `rating_count` **labeled "download proxy"**, Android is `installs`; sparse-data early state.
- Three-way header chips (iOS APP / ANDROID APP / WEB ONLY); detail page prefers `meta_active_ads` ("253+ ads in the library" when capped, falls back to `active_ad_count`; Discover cards unchanged); GemFacts rows for page likes / ads in library / ads checked / store checked; per-creative meta line in EvidenceReel ("since 4/9 · 62d · EU reach 120K · fb ig").
- `getGem()` fetches `/app-metrics` in parallel with `/scaling`; ANY failure degrades to an empty chart (console-warned).

---

## Live-probed facts (do NOT re-litigate)

1. **mwm.ai is DEAD as a per-app source.** Probed live through the DataImpulse proxy: per-app pages exist (`/apps/SLUG/APPLEID`) but the directory is a curated ~126-app incumbent set; downloads are coarse buckets only ("500M+"); no revenue/MAU/time-series; `api.mwm.ai` is server-side-only (every public path 404s; data is embedded in `__NUXT_DATA__`). Nothing here for emerging apps.
2. **Google Play `maxInstalls` (google-play-scraper) is a near-exact live install count** (e.g. Blinkit 199,060,719) — the single strongest free traction signal. Polled daily it gives a real install-growth curve.
3. **Apple publishes NO download counts anywhere.** The honest iOS proxies: `userRatingCount` delta over time (now snapshotted daily), review velocity, chart-rank feeds. The UI labels the iOS series "download proxy" — keep it honest.
4. **Apple reviews RSS works ONLY in the lowercase paged form** `…/customerreviews/page=N/id=<id>/sortby=mostrecent/json` — the camelCase paged URL returns HTTP 200 with an EMPTY feed (silent). 50/page, hard cap 10 pages. Coverage is spotty per-app (Nike/Whatnot iOS feeds are genuinely empty; WhatsApp returns 50/page) — many iOS apps legitimately show zero reviews.
5. **iTunes Lookup**: batched comma-separated ids work (one call, many apps); official limit ~20 calls/min; `screenshotUrls` can be empty while `ipadScreenshotUrls` is populated — fallback required.
6. **Meta impressions/spend are NULL for US ads** (re-confirmed). EU ads carry `eu_total_reach`. Never render impressions/spend for US ads — the UI doesn't.
7. **True ad count live-proven through DataImpulse**: 1 / 105 / 220 / 253+ (capped) across real advertisers. `make_interval(hours => :float)` does not exist in Postgres — use `:n * interval '1 hour'` (bug found by the live run after 292 unit tests passed green).

---

## How to run / verify

```bash
# stack: pg :5433 + minio :9010 up (db/docker-compose.yml)
cd ~/Projects/emerging-app-finder

# python suite (293 passed, 1 skipped, 7 deselected as of this handoff)
AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf .venv/bin/pytest -q

# go suite + integration (scratch DB agf_it — the suite TRUNCATEs, never point it at agf)
cd backend && go build ./... && go test ./... -count=1

# web suite
cd web && node --test src/lib/*.test.ts && npx tsc --noEmit && npm run lint && npm run build

# refresh store data + snapshot (no proxy needed; Apple/Google are free)
AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf PYTHONPATH=src \
  .venv/bin/python scripts/refresh_apps.py --pause 1

# true ad counts (needs residential egress)
AGF_PROXIES=http://<user>:<pass>@gw.dataimpulse.com:823 \
AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf PYTHONPATH=src \
  .venv/bin/python scripts/count_ads.py --limit 20

# assembled: go API on :8000 (AGF_DATABASE_URL=postgres://agf:agf@localhost:5433/agf)
# + web dev on :3000 with NEXT_PUBLIC_API_URL=http://localhost:8000/api (must end in /api)
# detail pages: /gems/1220 (Whiteout), /gems/1424 (Whatnot, capped path)
```

---

## Open items / next session

1. **Growth curves need accrual.** Every chart has 1 snapshot today (2026-06-10); the worker's daily step 1.7 + `count_ads.py` on a daily cadence build the curves. Nothing to code — just run the worker daily.
2. **Incumbent veto not yet wired to scoring.** `meta_active_ads_capped` is stored + displayed ("253+") but `score_all`/vetoes don't read it yet. The scrape-rethink design said cap+ ⇒ not a gem — wiring that into `src/agf/scoring/vetoes.py` is a small, high-leverage follow-up.
3. **Candidate selection is still THE open problem** (unchanged — see `docs/HANDOFF-discovery-rethink.md` + the discovery-harness memory note of 2026-06-10).
4. **SECURITY: rotate the DataImpulse password** — it is recoverable in plaintext from `~/.claude` session logs (this session recovered it that way on purpose; an attacker could too).
5. Review-flagged, consciously deferred: network I/O inside the enrich write-txn (pre-existing, documented in `src/agf/workers/main.py` ~line 124; refactor sketch lives in the review record); Go app-metrics read is unwindowed (fine for years at 1 row/app/day).

## Working-tree state (be careful committing)

Still **uncommitted** on `main`: the rest of the 2026-06-09 gem-relevance overhaul (migrations 103–106, `backend/cmd/` + `helpers/gemini.go` + `explainer_repo.go` + shortlist/config changes, discover-feed web files, `src/agf/tracker/{growth,history}.py`, `seed_demo.py`, `api/queries.py`…) **plus another agent's discovery-harness files** (`src/agf/discovery/taxonomy.py`, `scripts/discover_experiment.py`, `scripts/{persist,refine,enrich}_candidates.py`, `scripts/probe_*.py`) — do not sweep these into unrelated commits; stage explicit paths only.
