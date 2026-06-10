# Real-gems feed: classification, vetoes, full ad sets (2026-06-10)

**Goal (user):** working scrape→DB→web pipeline where the Discover feed shows real
emerging apps ("hidden gems") — no games, no physical-product/service advertisers,
no broken links — and a gem's detail page shows all (or nearly all) of the brand's ads.

## Root causes (diagnosed live 2026-06-10)

1. **Matview has no veto filter.** `frontend.emerging_scaling_apps`
   (db/schema/105_emerging_matview_growth.sql) joins ALL `adtech.advertiser_scores`
   rows — `vetoed` is written by scoring but never read. Every scored advertiser
   surfaces in the feed.
2. **Demo seed pollutes the live feed.** All 7 broken store links in the feed are
   fabricated demo apps (`page_id LIKE 'DEMO%'`; e.g. TutorGenie ios id2010777 → 404).
   Real apps' links and media all resolve (verified by batch link check).
3. **No game / non-app classification.** Physical-product and service advertisers
   (Primal Queen, Tire Agent, Mills Urgent Care…) score fine via funnel_intent;
   games (Whiteout Survival, Jungle Marble Blast 2) pass because `apps.genre`
   is never consulted.
4. **Incumbent signals stored but unused.** `advertisers.meta_active_ads_capped`,
   `page_like_count` exist (migrations 106–109 work) but no veto reads them.
5. **Ad persistence capped at 8/advertiser.** `scripts/persist_candidates.py`
   `AGF_MAX_ADS_PER_PAGE=8` and `scripts/scrape_real.py` MAX_ADS=8 — vs 105–250+
   ads actually in the library (Primal Queen 250, Whatnot 253+).
6. Web: `pct_change` rendered raw ("+166.6999969482422%").

## Frozen contract

### DB (applied by orchestrator BEFORE implementation agents run)

- `db/schema/110_apps_is_game.sql`:
  `ALTER TABLE public.apps ADD COLUMN IF NOT EXISTS is_game BOOLEAN;` (NULL = unknown)
- `db/schema/111_emerging_matview_quality.sql`: DROP/CREATE
  `frontend.emerging_scaling_apps` copying 105 verbatim, adding ONLY
  `WHERE s.vetoed = FALSE AND adv.page_id NOT LIKE 'DEMO%'`
  + the same `uq_emerging_advertiser` unique index (required for CONCURRENTLY).
  Demo exclusion is structural: seeding can never pollute the real feed again.

### Python — enrichment (`is_game`)

- `sidecar/gp_lookup.js` additionally emits `genreId` (google-play-scraper field).
- `src/agf/enrich_full/appstore.py` computes:
  - ios: `is_game = (primaryGenreName == "Games") or (6014 in genreIds)` (iTunes Lookup)
  - android: `is_game = genreId.startswith("GAME_")`
  - unknown → leave NULL (never guess from genre display names).
- `repo.upsert_app` persists `is_game` with the same COALESCE-preserve semantics as
  other enrichment columns.
- New one-shot `scripts/backfill_is_game.py`: re-lookup all `public.apps` rows with
  `is_game IS NULL` (batched iTunes lookup ≤20 calls/min; gp sidecar per Android app).

### Python — scoring vetoes

- `FeatureVector` (src/agf/scoring/features.py) += `is_game: bool | None`,
  `installs: int | None`, `page_like_count: int | None`,
  `meta_active_ads: int | None`, `meta_active_ads_capped: bool | None`.
  build_features: app lateral join += `ap.is_game, ap.installs`; advertisers
  select += `page_like_count, meta_active_ads, meta_active_ads_capped`.
- `src/agf/scoring/vetoes.py` — new vetoes appended to `_VETOES` (after existing five),
  with module-level threshold constants:
  - `game_app` → reason `"game"`: `fv.is_game is True`
  - `no_app` → reason `"no_app"`: `not fv.has_app` (v1 is apps-only — physical
    products, services and web-only funnels are out; funnels return in v2)
  - `incumbent_ad_volume` → reason `"incumbent_ad_volume"`: `fv.meta_active_ads_capped is True`
  - `incumbent_audience` → reason `"incumbent_audience"`: `(fv.page_like_count or 0) > 500_000`
  - `established_traction` → reason `"established_traction"`:
    `(fv.installs or 0) > 5_000_000 or (fv.rating_count or 0) > 200_000`
  - NULL/unknown values always PASS (no veto on missing data).

### Python — persistence (full ad sets)

- `scripts/persist_candidates.py`: `AGF_MAX_ADS_PER_PAGE` default 8 → 250; persist
  ALL deduped ads (active first, same preference logic, just un-capped to the limit).
- `scripts/scrape_real.py`: MAX_ADS default 8 → 250 (same env var).
- Every persisted ad must be visible in the dossier reel: if an ad has no media
  creative, insert a text-only `public.creatives` row (s3_key NULL, body_text/title/
  type from the AdRecord) — the web EvidenceReel already renders text tiles for
  creatives without `snapshot_image_url`. Media download stays bounded
  (enrich `top_n` default 12 per advertiser).
- Dedup guard: do not duplicate text-creative rows on re-run (match on ad_id where
  s3_key IS NULL).

### Web

- Format `pct_change` to a whole percent (e.g. `+167%`) wherever rendered
  (GemCard / Trend). No other UI changes.

### Go API

- No changes. It already serves what lands in the DB.

## Data ops (orchestrator, after code lands)

1. Apply 110 + 111.
2. Delete demo data: advertisers `page_id LIKE 'DEMO%'` + cascade (ads, creatives,
   funnels, app_advertiser_metrics, advertiser_scores, advertiser_growth, saved_gems,
   review_decisions) + demo-only apps (linked only to demo ads).
3. `scripts/backfill_is_game.py`.
4. Live sweep (DataImpulse residential via AGF_PROXIES): persist `kind=app`
   candidates from `/tmp/agf-candidates-2026-06-10.json` (~40, by n_ads_seen) via
   persist_candidates.py (full ad history, 16-week series); `scripts/count_ads.py`
   for persisted set; `scripts/refresh_apps.py`; `run_scoring`; matview refresh.
5. Re-run the batch link check + browser walk.

## Verification gates

- Full suites green: pytest (AGF_TEST_DB_URL …5433/agf), `go test ./...` (agf_it),
  web (`node --test`, tsc, lint, build).
- Feed invariants (SQL): 0 rows with `page_id LIKE 'DEMO%'`; 0 vetoed; 0 app-less;
  0 `is_game IS TRUE`.
- Detail page of a top gem shows ≥20 ads when the library count is ≥20.
- Batch link check: 0 broken store links.
- Known breakage to fix, not ignore: tests asserting demo/vetoed/app-less rows
  appear in the matview or shortlist (tests/scoring/test_schema_matview.py,
  tests/scoring/test_run_scoring_e2e.py, tests/api contract tests,
  backend/internal/integration) — update fixtures to satisfy the new invariants
  (non-DEMO page ids, vetoed=false, linked app) rather than weakening the filter.

## File ownership (parallel agents — disjoint)

- **Agent A (scoring):** src/agf/scoring/features.py, src/agf/scoring/vetoes.py,
  tests/scoring/*.
- **Agent B (enrichment+persist):** sidecar/gp_lookup.js, src/agf/enrich_full/*,
  src/agf/db/repo.py, scripts/persist_candidates.py, scripts/scrape_real.py,
  scripts/backfill_is_game.py, tests/enrich_full/*, tests/db/*, tests/api/* (if
  contract fixtures need app links).
- **Agent C (web):** web/src/* (pct format + its test).
- Migrations + seed_demo.py adjustments + data ops: orchestrator.
