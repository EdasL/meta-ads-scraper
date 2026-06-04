# Emerging App Finder — MASTER PLAN (source of truth)

> **This is the single source of truth for the whole project.** Multiple agents work from it in parallel. Read **§1 How agents use this doc** before touching anything. Keep it updated: when you finish a task, tick its box and bump the status dashboard (§5).

**One-line goal:** Discover under-the-radar software apps / web funnels SCALING on Meta ads (US), and enrich each with product, funnel, ads, and company info — surfacing *emerging hidden gems*, not known incumbents.

**Grug version:** throw three nets to catch unknown apps → prove they really pay facebook (14-day-old ads) → multiply three scores so famous apps become zero → human looks at what survives → grab their product, funnel, ads. (Full grug doc: `../../README.md`.)

**Companion docs:**
- Design rationale & research: `../design-spec.md`
- Architecture diagram: `../architecture.excalidraw`
- Grug explainer: `../../README.md`
- Detailed per-phase plans: `./01-poc-plan.md` (Plan 1, complete) · Plans 2–7 expand from §6 here.

---

## 1. How agents use this doc (coordination protocol)

1. **Pick a task only from a phase whose dependencies are all `✅ done`** (see §4 dependency graph + §5 dashboard). Never start a phase whose deps are unfinished.
2. **Claim a task** by setting its row in §5 to `🟡 <your-agent-id>` before starting. One agent per task.
3. **Stay inside your phase's owned files** (§6 "Owns"). Do **not** write into another phase's directory. Shared files have explicit rules in §3 (requirements.txt, db/schema/, conftest.py).
4. **Honor the shared contracts in §3 exactly** — the function signatures, table columns, and names there are frozen. If you must change one, change it *in §3 first*, flag it in §8, and ping dependent phases. A silent rename is a build break for someone else.
5. **TDD per task:** write the failing test → run it (fails) → implement → run it (passes) → commit. Pure functions get unit tests with fixtures; live network / DB / browser get `@integration`-marked opt-in tests.
6. **Each task lists a `done:` criterion and a `verify:` command.** A task is done only when its verify command is green. Tick the checkbox and update §5.
7. **Commit format:** `<type>(plan-N): <desc>` e.g. `feat(plan-3): add crawl_log staleness scheduler`. Commit per task.
8. **When a whole phase passes its Phase-Verify, mark it `✅` in §5 and unblock its dependents.**

---

## 2. North star & guardrails (never violate)

- **One extraction method:** scrape the Meta Ad Library **web GraphQL** (Plan 2) via **`curl_cffi` (Chrome-TLS impersonation) + a pure-HTTP `rd_challenge` solver + token/`doc_id` self-heal**, replaying `AdLibrarySearchPaginationQuery` through residential proxies in a warm-once/reuse pool. A real browser (Playwright/Camoufox) is a **standby warmer fallback only**, never the hot path. Never the official `ads_archive` API (EU/political only, no US commercial, no sort). *(Revised 2026-06-04 after research + on-machine proof — see `../research/2026-06-04-server-side-extraction.md`; supersedes the original "Playwright interception is the single method".)*
- **Scaling is a proxy** (US has no spend/impressions): ad **longevity ≥14d** + **active-creative-count velocity** + **collation duplication**. Never claim true spend.
- **Non-negotiable cross-check:** a candidate is "scaling on Meta" only after the Ad Library confirms **active creatives, longevity ≥14d**. Organic TikTok risers (0 Meta spend) are dropped. Discovery and scaling-confirmation are two separate steps.
- **"Still unknown" is a hard multiplicative gate:** `GemScore = 100·SV^.5·UR^.35·FI^.15` with per-axis floor. Famous brand → UnderRadar≈0 → score≈0.
- **Human-in-the-loop:** GemScore ≥70 → ranked review queue. **Never auto-call.**
- **ToS reality:** scraping the web Ad Library is ToS-gray; `doc_id`/tokens rotate (~weekly); needs residential proxies. Maintenance treadmill is expected — see Plan 7 runbook.

---

## 3. Shared contracts (FROZEN — every phase builds against these)

These resolve the cross-phase consistency issues. Treat as law.

### 3.1 Tech stack
Python 3.13 · **`curl_cffi`** (Chrome-TLS impersonation — primary Ad Library transport) · httpx (iTunes/light JSON APIs only) · **Playwright (Python) — standby session-warmer fallback only, not the hot path** · PostgreSQL 17/18 (datastore **and** queue) via psycopg + SQLAlchemy Core · MinIO/S3 · Node sidecar **only** for `facundoolano/google-play-scraper` · pytest (TDD) · numpy + statsmodels (Plan 5 stats) · Pillow/imagehash (perceptual hash) · python-whois · aria2c/ffmpeg (media) · FastAPI (read-only output) · cron + worker processes + PID-file mutex (**no Redis/Celery/Kafka day 1**; Redis optional later as the warm-session token store) · docker-compose on an 8–16GB VPS · residential rotating proxies.

### 3.2 Repo root & layout
Project root: `~/Projects/emerging-app-finder/`. All file paths below are relative to it.

### 3.3 Data-access contract — **free functions, `conn`-first** (resolves the `repo` vs `conn` clash)
Plan 3 exposes `src/agf/db/repo.py` as **module-level functions whose first arg is a SQLAlchemy `Connection`**. **All phases (4, 5, 6) call these with `conn`, never a `repo` object.**
```
upsert_advertiser(conn, ar) -> advertiser_id:int          # minimal insert: page_id, page_name only
upsert_app(conn, meta) -> app_id:int                      # ON CONFLICT(store, store_id)
upsert_ad(conn, ar, advertiser_id, app_id|None) -> ad_id:int
upsert_creative(conn, ad_id, type, body_text, title, snapshot_image_url, s3_key, sha256, phash) -> creative_id:int
record_ad_metric(conn, ad_id, snapshot_date, eu_total_reach, active) -> None
upsert_app_advertiser_metric(conn, advertiser_id, week_start, active_ad_count, distinct_creative_count, total_eu_reach) -> None
upsert_funnel(conn, ad_id, landing_url, final_url_after_redirect, screenshot_s3_key) -> None
upsert_advertiser_enrichment(conn, advertiser_id, domain_id|None, parent_company_id|None, page_category|None, ig_username|None, ig_followers|None) -> None   # Plan 6 backfills these
```
`upsert_advertiser` inserts a **minimal** advertiser; `domain_id`/`parent_company_id`/`ig_*` are **enrichment-populated by Plan 6** via `upsert_advertiser_enrichment`. The disguised-incumbent veto (Plan 5) reads them and tolerates NULL.

### 3.4 Extraction facade — `Extractor` (resolves the undefined `extractor`)
Plan 2 exposes a high-level facade that manages the `AdLibrarySession` lifecycle. **Plans 4, 5, 6 take an `extractor` and never touch sessions directly.**
```
class Extractor:                       # src/agf/extract/facade.py
    async def search(self, *, query='', countries=['US'], active_status='ACTIVE',
                     sort_by_impressions=False, search_type='KEYWORD_UNORDERED',
                     view_all_page_id=None, max_pages=10) -> list[AdRecord]
    async def ad_details(self, ad_archive_id) -> AdDetails
    async def resolve_deeplink(self, url, ua='ios') -> ResolvedLink
```
`search()` **must** support `sort_by_impressions` (Firehose B) and `search_type='page'` + `view_all_page_id` (EU→US bridge). This is a Plan 2 contract requirement (extends P2-T6).

### 3.5 Data model — table column lists are frozen
Tables (schemas `public` / `adtech` / `logging` / `frontend`): `advertisers(page_id UNIQUE, page_name, domain_id FK, parent_company_id self-FK, page_category, ig_username, ig_followers, first_seen, last_seen)` · `apps(UNIQUE(store,store_id), name, developer, genre, release_date, rating, rating_count, installs, free, icon_url, is_emerging, first_seen, updated_at)` · `ads(external_ad_id UNIQUE, advertiser_id FK, app_id FK NULL, link_url, cta_type, cta_text, publisher_platforms[], is_active, start_date, end_date, collation_count, first_seen, last_seen)` · `creatives(ad_id FK, type, s3_key, content_type, file_size, sha256, phash, body_text, title, snapshot_image_url)` · `ad_metrics(ad_id, snapshot_date, eu_total_reach, active, PK(ad_id,snapshot_date))` · `app_advertiser_metrics(advertiser_id, week_start, active_ad_count, distinct_creative_count, total_eu_reach, PK(advertiser_id,week_start))` · `funnels(ad_id FK, landing_url, final_url_after_redirect, screenshot_s3_key, captured_at)` · `logging.crawl_log(entity_type, entity_id, crawled_at, crawl_result, INDEX(entity_type,entity_id,crawled_at DESC))`.

### 3.6 Product matview — **Plan 5 owns it, alone** (resolves double-create + build-order)
`frontend.emerging_scaling_apps` is created **only by Plan 5** (`db/schema/051_emerging_matview.sql`). Plan 3 does **not** create it. Frozen columns (Plan 7 builds against these exactly):
```
advertiser_id, cohort, page_name, app_id (NULLABLE), app_name, gem_score,
scaling_velocity, under_radar, funnel_intent, active_ad_count,
max_longevity_days, alert (bool), refreshed_at
+ UNIQUE INDEX on advertiser_id (one row per gem; required for REFRESH … CONCURRENTLY)
```
`cohort` (category × country) is persisted (GemScore is cohort-normalized). One row **per advertiser/gem** — **web funnels with `app_id` NULL still appear.**

### 3.7 Output/review keyed on `advertiser_id` (resolves web-funnel-drops-out)
Plan 7 routes and `frontend.review_decisions` are keyed on **`advertiser_id`** (the matview's unique key), not `app_id`. `app_id` may be NULL. `review_decisions(advertiser_id FK, verdict CHECK in(accept,reject,hold), reviewer, note, decided_at, PK(advertiser_id, decided_at))`.

### 3.8 S3/MinIO client — **Plan 3 owns** (resolves the missing uploader)
`src/agf/storage/s3.py`: `get_s3()` · `put_bytes(key, data, content_type) -> key`. Env: `S3_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `S3_BUCKET`. Plan 6 passes this client into `download_creative`/`capture_funnel`.

### 3.9 Worker handler adapter (resolves handler-signature mismatch)
`run_worker` (Plan 3) expects `handler: Callable[[Connection, int], str]`. Cross-cutting deps (`extractor`, `client`, `s3`) reach a handler via an **adapter closure**: `def make_handler(extractor, s3): def handler(conn, entity_id): … ; return handler`. Plan 4's `confirm`/orchestrator and Plan 6's `enrich_gem` ship such adapters.

### 3.10 Shared-file rules
- **`requirements.txt`** — **shared, append-only**, with a labelled section per phase (`# --- plan-N ---`). Created in the bootstrap task; any phase may append its own section only.
- **`db/schema/` numbering** — reserved ranges: **Plan 3 = 001–049**, **Plan 5 = 050–099**, **Plan 7 = 100+**. Every file numbered (`migrate.py` globs in lexical order). Plan 7's review schema = `100_frontend_review.sql`.
- **`conftest.py`** — the shared `pg_conn` / tmp-schema fixtures live in **root `conftest.py` only**; all `tests/*` inherit it. No phase redefines `pg_conn`.
- **proxy env** — single contract `AGF_PROXIES` (newline/comma list of `user:pass@host:port`) read by Plan 2 `ProxyPool.from_env`; Plan 7's gateway config uses the same.
- **docker-compose** — **Plan 7 `ops/docker-compose.yml` is the production stack** (pins Postgres/MinIO image tags once). Plan 3 `db/docker-compose.yml` is local-test only.

---

## 4. Dependency graph (build order)

```
Plan 1 PoC ──┬─► Plan 2 Extraction ──┬─► Plan 4 Discovery ──► Plan 5 Scoring ──► Plan 6 Enrichment ──► Plan 7 Output+Ops
             └─► Plan 3 Data spine ───┘         ▲                    ▲                    ▲                    ▲
                  (1)                  (1,2,3)  │            (1,2,3,4)            (2,3,5)        (1,2,3,4,5,6)
                                                └── Plan 2 + 3 both feed Plan 4 ──┘
```
Critical path: **1 → 2 → 4 → 5 → 6 → 7**. Plan 3 runs parallel to Plan 2 (both depend only on 1). Plan 4 needs 2 **and** 3.

---

## 5. Status dashboard (agents update this)

| Plan | Phase | Depends | Status | Owner | Detailed plan |
|---|---|---|---|---|---|
| 1 | PoC | — | ✅ done | claude | `01-poc-plan.md` ✅ written · 30/30 unit green · live-verified 2026-06-04 (GraphQL 200 + real ads parsed; httpx bootstrap anti-bot-blocked → Plan 2 Playwright) |
| 2 | Extraction core | 1 | ⬜ ready (deps met) | — | `02-extraction-plan.md` (curl_cffi + rd_challenge) |
| 3 | Data spine | 1 | ⬜ ready (deps met) | — | TBD |
| 4 | Discovery firehoses | 1,2,3 | ⬜ blocked | — | TBD |
| 5 | Velocity + GemScore | 1,2,3,4 | ⬜ blocked | — | TBD |
| 6 | Enrichment | 2,3,5 | ⬜ blocked | — | TBD |
| 7 | Output + ops | 1,2,3,4,5,6 | ⬜ blocked | — | TBD |

Status legend: ⬜ not started · 🟡 in progress (`agent-id`) · ✅ done · 🔴 blocked/issue.
Estimate (sequential): ~31 dev-days; with Plan 2 ∥ Plan 3 parallel, ~27.

---

## 6. Phases

> Plan 1 is fully detailed in `01-poc-plan.md`. Plans 2–7 below are task-level with done-criteria + verify commands; each should get its own detailed `0N-…-plan.md` (bite-sized TDD steps) before execution, generated from this section + §3 contracts.

### Plan 1 — PoC  ·  deps: 1  ·  ~1d  ·  → `01-poc-plan.md`
Prove the full chain (US search → app-classify → emerging-enrich → scaling proxy → ranked CSV) in one package. 12 TDD tasks. **Exposes (reused by later phases):** `fbjson`, `bootstrap`, `request`, `parse.parse_search_response`+`AdRecord`, `classify.classify_url`+`AppIntent`, `enrich.parse_lookup`+`AppMeta`+`emerging_from_release`, `scale.aggregate_advertisers`. Later phases **import** these, never redeclare.

---

### Plan 2 — Extraction core (curl_cffi + rd_challenge; browser as standby)  ·  deps: 1  ·  ~4d  ·  → `02-extraction-plan.md`
**Goal:** durable Ad Library scraper built on **`curl_cffi` (Chrome-TLS)** + a pure-HTTP **`rd_challenge` solver** (yt-dlp #15577/#15582 pattern), self-heal `doc_id`+LSD+fb_dtsg (HTML + JS bundle), paginate via `end_cursor`, residential-proxy + backoff, deeplink resolver, a **session-warming pool** (warm once → reuse, refresh on staleness), and the **`Extractor` facade** (§3.4) every later phase calls. Real browser (Playwright/Camoufox) = standby warmer fallback only. Rationale + on-machine proof: `../research/2026-06-04-server-side-extraction.md`. Full TDD task breakdown: `02-extraction-plan.md`.
**Owns:** `src/agf/extract/` (`backoff.py`, `proxy.py`, `tls_client.py` [curl_cffi session], `rd_challenge.py`, `token_heal.py`, `search.py`, `ad_details.py`, `deeplink.py`, `session_pool.py`, `warmer.py` [optional browser], `facade.py`), `tests/extract/`. Appends `# --- plan-2 ---` deps to `requirements.txt` (`curl_cffi`; `playwright` optional/standby).
**Exposes:** §3.4 `Extractor`; `classify_error`/`with_backoff`; `ProxyPool.from_env` (`AGF_PROXIES`); `solve_rd_challenge`; `SessionTokens`/`heal_from_html`/`heal_doc_id_from_bundle`; `AdLibrarySession` (curl_cffi-backed) + `SessionPool`; `AdDetails`; `ResolvedLink`.

> **Note:** the original P2-T1…T9 below predate the 2026-06-04 method revision (they assumed Playwright interception). They are superseded by the curl_cffi/rd_challenge task breakdown in `02-extraction-plan.md`; the `Extractor` facade contract (§3.4) is unchanged — only the internals differ.

- [ ] **P2-T1** Scaffold `extract/`, pin playwright, `playwright install chromium` — done: `import agf.extract` ok · verify: `pytest -m "not integration" tests/extract -q`
- [ ] **P2-T2** `backoff.py`: classify 429/1675004→rate_limit, 368→transient; jittered exponential `with_backoff` — done: retries then raises `ExtractError` after max · verify: `pytest tests/extract/test_backoff.py -q`
- [ ] **P2-T3** `proxy.py`: `ProxyPool.from_env(AGF_PROXIES)` round-robin, `as_playwright()` — done: rotation + empty→None · verify: `pytest tests/extract/test_proxy.py -q`
- [ ] **P2-T4** `token_heal.py`: `extract_fb_dtsg`, `extract_doc_id_from_bundle`, `heal_from_html` (reuse Plan 1 bootstrap) — done: fixtures heal lsd+doc_id+fb_dtsg, bundle fallback · verify: `pytest tests/extract/test_token_heal.py -q`
- [ ] **P2-T5** `playwright_session.py`: async `AdLibrarySession`, `page.route('**/graphql')`, `post_graphql` — done: unit `build_session_headers` green; `@integration` opens page · verify: `pytest tests/extract/test_playwright_session.py -q`
- [ ] **P2-T6** `search.py`: `search_ads` paginated, **with `sort_by_impressions`, `search_type`, `view_all_page_id` params** (§3.4) — done: reuses one sessionID+collationToken, cursor fed; impressions-sort + page-scoped fixtures parse · verify: `pytest tests/extract/test_search.py -q`
- [ ] **P2-T7** `ad_details.py`: `AdLibraryAdDetailsV2Query` → `AdDetails(eu_total_reach, active)` — done: parses `aaa_info.eu_total_reach`, absent→None · verify: `pytest tests/extract/test_ad_details.py -q`
- [ ] **P2-T8** `deeplink.py`: `resolve_deeplink` (app.link/onelink.to/apple.co, iOS UA) → `ResolvedLink` via classify — done: mock redirect chain → `intent.kind=='ios'` · verify: `pytest tests/extract/test_deeplink.py -q`
- [ ] **P2-T9** `facade.py`: `Extractor` wrapping session lifecycle + search/ad_details/resolve_deeplink — done: unit with fake session; `@integration` live smoke search→details · verify: `pytest tests/extract -q`

**Phase-verify:** `pytest tests/extract -q` green (integration deselected); opt-in `pytest -m integration tests/extract` does live bootstrap → token heal → US search ≥1 ad → ad_details.
**Risks:** doc_id/token rotation (~weekly, may live only in hashed JS bundle); Meta anti-bot at scale; `page.route` interception timing races.

---

### Plan 3 — Data spine  ·  deps: 1  ·  ~4d
**Goal:** Postgres schema (fork adscrawler), `conn`-first upsert/dedup repo (§3.3), S3 client (§3.8), and the DB-as-queue worker model (PID-mutex + crawl_log scheduler). **Does NOT create the product matview** (§3.6).
**Owns:** `db/schema/001–049_*.sql`, `db/migrate.py`, `db/docker-compose.yml` (local-test only), `src/agf/db/` (`engine`, `models`, `repo`, `dedup`), `src/agf/storage/s3.py`, `src/agf/workers/` (`mutex`, `scheduler`, `runner`), `tests/db/`, `tests/workers/`, **root `conftest.py`** (`pg_conn` only). Appends `# --- plan-3 ---` (psycopg, sqlalchemy, boto3/minio, Pillow, imagehash).
**Exposes:** schemas + tables (§3.5); `get_engine`/`begin`; repo functions (§3.3); `dedup.sha256_bytes`/`phash_image`/`is_duplicate_creative`; `storage.s3` (§3.8); `workers.PidFileMutex`; `claim_due`/`mark_crawled`; `run_worker(name, entity_type, handler: Callable[[Connection,int],str], …)`; `migrate.apply_migrations`.

- [ ] **P3-T1** `db/docker-compose.yml` (PG17+MinIO) + root `conftest.py` `pg_conn` (skip if no `AGF_TEST_DB_URL`) — done: SELECT 1 integration passes / skips clean · verify: `pytest tests/db/test_connection.py -q`
- [ ] **P3-T2** `001_schemas.sql` + `002_public_core.sql` (advertisers/apps/ads/creatives, UNIQUE keys, self-FK) — done: tables+constraints exist · verify: `pytest tests/db/test_schema_core.py -q`
- [ ] **P3-T3** `003_metrics.sql` + `004_logging_crawl_log.sql` (composite PKs, crawl_log DESC index) — done: PKs+index present · verify: `pytest tests/db/test_schema_metrics.py -q`
- [ ] **P3-T4** `migrate.py` ordered + idempotent + `schema_migrations` ledger — done: 2nd run returns `[]` · verify: `pytest tests/db/test_migrate.py -q`
- [ ] **P3-T5** `engine.py` + `models.py` (Core Tables mirror schema) — done: drift test passes; begin() commit/rollback · verify: `pytest tests/db/test_models_match_schema.py -q`
- [ ] **P3-T6** `repo.py` upsert advertiser/app/ad (`conn`-first, §3.3; advertiser minimal) — done: re-upsert keeps one row, bumps last_seen · verify: `pytest tests/db/test_repo_upsert.py -q`
- [ ] **P3-T7** `dedup.py` sha256 + phash wired into `upsert_creative` — done: identical bytes → one row, 2nd returns id · verify: `pytest tests/db/test_dedup.py -q`
- [ ] **P3-T8** metric repo: `record_ad_metric`, `upsert_app_advertiser_metric`, `upsert_funnel`, `upsert_advertiser_enrichment` — done: conflict-update idempotent · verify: `pytest tests/db/test_repo_metrics.py -q`
- [ ] **P3-T9** `storage/s3.py` `get_s3`/`put_bytes` (§3.8) — done: put→get round-trips against MinIO (integration) · verify: `pytest tests/db/test_s3.py -q`
- [ ] **P3-T10** `workers/mutex.py` `PidFileMutex` — done: 2nd acquire raises, stale reclaimed · verify: `pytest tests/workers/test_mutex.py -q`
- [ ] **P3-T11** `workers/scheduler.py` `claim_due`/`mark_crawled` over crawl_log — done: oldest-first, drops after mark · verify: `pytest tests/workers/test_scheduler.py -q`
- [ ] **P3-T12** `workers/runner.py` `run_worker` (mutex+scheduler+handler, §3.9) — done: each due entity processed once, crawl_log row written · verify: `pytest tests/workers/test_runner.py -q`

**Phase-verify:** migrate applies 001–049 idempotently; `pytest tests/db tests/workers -q` green; upsert→queue→worker round-trip works.
**Risks:** import Plan 1 dataclasses (don't redeclare → drift); `REFRESH … CONCURRENTLY` needs its own autocommit conn (Plan 5); phash lib tolerant of non-image bytes.

---

### Plan 4 — Discovery firehoses  ·  deps: 1,2,3  ·  ~6d
**Goal:** three keyword-free firehoses + the mandatory scaling-confirm, all dumping `Candidate`s into the spine via §3.3 (`conn`) using the §3.4 `Extractor`. **Includes the app→advertiser linkage** (classify `link_url`→`store_id` match → set `ads.app_id`+`advertiser_id` → derive `page_id`).

> **Discovery sources (revised 2026-06-04 after research + PoC — see `../research/2026-06-04-discovery-sources.md`):** Discovery is **spend-driven**: Firehose **B (Meta Ad Library)** is the PRIMARY engine; the app-store lane is a cheap **seed + release-date post-filter**, not the engine. **Firehose A rebuild:** the legacy iTunes RSS new-apps feed is **live** (PoC-verified) but returns only **~100–120 globally-newest apps per run — genre fan-out does NOT multiply** (drop the "26-genre→thousands" assumption); implement as a **daily-polled accumulator** + iTunes `releaseDate` post-filter (use `releaseDate`, never `currentVersionReleaseDate`) + a **health check** (it has frozen historically). The modern `rss.applemarketingtools.com` v2 generator has **no "new" feed** (404). **Android has no free "new" feed** (Google removed NEW/MOVERS) → seed Android from Meta + iOS→Android matching; `google-play-scraper.app()` for dates; Play sitemap only if full enumeration is needed. **Firehose C:** PublicWWW free top-1M for funnel fingerprints. **Optional cheap convenience layers:** ScrapeCreators ($10/mo) for the Meta side, PublicWWW (~$49–79/mo) for funnels at scale. Skip enterprise app-intel (Sensor Tower/Apptopia/42matters→Similarweb); Appfigures (~$350/mo) only if iOS seed coverage proves insufficient.
**Owns:** `src/agf/discovery/` (`seeds`, `firehose_rss`, `firehose_eu`, `bridge`, `firehose_fingerprint`, `link_apps`, `confirm`, `run_discovery`), `src/agf/discovery/sidecar/gplay_collect.js`, `data/seeds/`, `tests/discovery/`. Appends `# --- plan-4 ---` (lxml/beautifulsoup4, feedparser).
**Exposes:** `Candidate` + `upsert_candidate(conn, c)`; `run_rss_firehose`/`fetch_mwm_catalog`/`gplay_sidecar_collect`; `run_eu_impressions_firehose(extractor,…)`+`collapse_to_advertisers`; `bridge_eu_to_us(extractor,page_id)`; `fingerprint_search`+signatures+`classify_funnel_intent`; **`link_app_to_advertiser(conn, extractor, candidate)`**; `confirm_scaling_on_meta(extractor,page_id,min_longevity_days=14)`; `run_all_firehoses(conn, extractor, client, config)`.

- [ ] **P4-T1** `seeds.py` `Candidate` + `upsert_candidate(conn,c)` router (§3.3) — done: app→upsert_app, page→upsert_advertiser, dedup-safe · verify: `pytest tests/discovery/test_seeds.py -q`
- [ ] **P4-T2** Firehose A `fetch_itunes_rss` + `diff_against_seen` — done: fixture parse + delta drop · verify: `pytest tests/discovery/test_firehose_rss.py -q`
- [ ] **P4-T3** Firehose A `fetch_mwm_catalog` + Node `gplay_collect.js` sidecar bridge — done: MWM since-date filter + gplay stdout→entries · verify: `pytest tests/discovery/test_firehose_rss_mwm_gplay.py -q`
- [ ] **P4-T4** Firehose B `run_eu_impressions_firehose` (`extractor`, DE/GB, `sort_by_impressions`) + `collapse_to_advertisers` (≥25 running days) — done: collapse to page_ids, drop <25d · verify: `pytest tests/discovery/test_firehose_eu.py -q`
- [ ] **P4-T5** `bridge_eu_to_us` (`search_type='page'`, `view_all_page_id`) + `run_bridge` — done: queries country=US with page id, emits Candidates · verify: `pytest tests/discovery/test_bridge.py -q`
- [ ] **P4-T6** Firehose C `fingerprint_search` + `classify_funnel_intent` (paywall SDK ∩ pixel ∩ MMP) — done: paid fixture→True, organic→False · verify: `pytest tests/discovery/test_firehose_fingerprint.py -q`
- [ ] **P4-T7** **`link_app_to_advertiser`**: search Meta by app, resolve deeplinks (Plan 2), match `store_id`, set `ads.app_id`+`advertiser_id`, return `page_id` — done: matching ad linked, non-match skipped · verify: `pytest tests/discovery/test_link_apps.py -q`
- [ ] **P4-T8** `confirm_scaling_on_meta` (active + longevity≥14d) + `run_scaling_confirm` (sets `is_emerging`/eligible) — done: 20d-old pass, 3d-old fail · verify: `pytest tests/discovery/test_confirm.py -q`
- [ ] **P4-T9** `run_discovery.py` orchestrator A→link→B(+bridge)→C→confirm, wired to crawl_log via handler adapter (§3.9) — done: fakes → only confirmed counted · verify: `pytest tests/discovery/test_run_discovery.py -q && python -m agf.discovery.run_discovery --dry-run`

**Phase-verify:** `pytest tests/discovery -q` green; orchestrator upserts from all firehoses, links apps→advertisers, bridges EU→US, gates on 14d confirm.
**Risks:** mock Plan 2/3 collaborators against §3 signatures (drift if they finalize differently — they won't if §3 is honored); scrape-target HTML/JSON churn (fixture refresh); EU→US bridge misses strictly-US-only advertisers (patched by A + C).

---

### Plan 5 — Velocity tracker + GemScore  ·  deps: 1,2,3,4  ·  ~6d
**Goal:** daily/weekly snapshots → cohort-normalized derivative features → full multiplicative GemScore + 5 vetoes → **sole owner of `frontend.emerging_scaling_apps`** (§3.6).
**Owns:** `src/agf/tracker/snapshots.py`, `src/agf/scoring/` (`cohorts`, `features`, `gemscore`, `vetoes`, `run_scoring`), `db/schema/050_scoring.sql`, `db/schema/051_emerging_matview.sql`, `tests/tracker/`, `tests/scoring/`. Appends `# --- plan-5 ---` (numpy, statsmodels, scipy).
**Exposes:** `snapshot_ad_metrics(conn,date)` (calls `extractor.ad_details` for `eu_total_reach`/active), `snapshot_advertiser_week(conn,week)`; `cohort_key`/`robust_z`/`pctile`; `log_growth_wow`/`acceleration`/`ewma`/`build_features`; `scaling_velocity`/`under_radar`/`funnel_intent`/`gem_score(sv,ur,fi,floor=.05)`; `apply_vetoes`; `score_all(conn,as_of)`; `frontend.emerging_scaling_apps` (§3.6 columns).

- [ ] **P5-T1** `snapshot_ad_metrics` (active ads → ad_metrics, eu_total_reach via `extractor.ad_details`) — done: idempotent on PK, active flag correct · verify: `pytest tests/tracker/test_snapshot_ad_metrics.py -q`
- [ ] **P5-T2** `snapshot_advertiser_week` (roll into app_advertiser_metrics, reuse Plan 1 `aggregate`) — done: counts correct, idempotent · verify: `pytest tests/tracker/test_snapshot_advertiser_week.py -q`
- [ ] **P5-T3** `cohorts.py` `cohort_key`/`robust_z`(MAD)/`pctile` — done: matches scipy ref, degenerate→0 not NaN · verify: `pytest tests/scoring/test_cohorts.py -q`
- [ ] **P5-T4** `features.py` `log_growth_wow`/`acceleration`/`ewma` — done: flat→0, accelerating→positive · verify: `pytest tests/scoring/test_features_primitives.py -q`
- [ ] **P5-T5** `build_features(conn, advertiser_id, as_of)` → `FeatureVector` — done: 4-week fixture → expected accel/longevity_share · verify: `pytest tests/scoring/test_build_features.py -q`
- [ ] **P5-T6** sub-scores `scaling_velocity`/`under_radar`/`funnel_intent` (weighted-sum within axis) — done: famous fixture → UR≈0; missing within-axis re-normalizes · verify: `pytest tests/scoring/test_subscores.py -q`
- [ ] **P5-T7** `gem_score` multiplicative AND-gate + per-axis floor — done: (1,1,1)→100; incumbent→small; sparse-axis imputed not 0 · verify: `pytest tests/scoring/test_gemscore.py -q`
- [ ] **P5-T8** `vetoes.py` (STL deseasonalize, longevity-floor, disguised-incumbent via parent_company_id/pixel, relaunch, persistence-gate) — done: each veto fires on its fixture, clean gem passes · verify: `pytest tests/scoring/test_vetoes.py -q`
- [ ] **P5-T9** `050_scoring.sql` + `051_emerging_matview.sql` (§3.6 columns, UNIQUE index, `DROP … IF EXISTS` first) — done: `REFRESH … CONCURRENTLY` succeeds · verify: `pytest tests/scoring/test_schema_matview.py -q`
- [ ] **P5-T10** `run_scoring.score_all` (features→score→vetoes→write→refresh) — done: gem alert=true, brand low, spike vetoed · verify: `pytest tests/scoring/test_run_scoring_e2e.py -q`

**Phase-verify:** `pytest tests/tracker tests/scoring -q` green; `score_all` on seeded gem+brand+spike → matview with alert=true only for the gem; `REFRESH … CONCURRENTLY` succeeds.
**Risks:** 3–4 week warmup before acceleration trustworthy (degrade gracefully); small cohorts (<3) need fallback; STL needs ≥2 seasonal periods (short-circuit pass).

---

### Plan 6 — Enrichment  ·  deps: 2,3,5  ·  ~6d
**Goal:** for flagged gems only (top-N, never all ads): app-store metadata, funnel screenshots + pricing extraction, deduped creative bytes → S3 (§3.8), company/WHOIS/tech. **Reuses Plan 2 `Extractor.resolve_deeplink` + Playwright** (no second resolver), backfills advertiser enrichment via §3.3.
**Owns:** `src/agf/enrich_full/` (`appstore`, `funnel_capture`, `creatives`, `company`, `schemas`, `run_enrich`), `src/agf/enrich_full/sidecar/` (Node gp_lookup), `tests/enrich_full/`. Appends `# --- plan-6 ---` (python-whois). (aria2c/ffmpeg system deps documented in Plan 7 compose.)
**Exposes:** `lookup_itunes_batch`/`lookup_google_play`; `resolve_landing` (delegates to Plan 2 `resolve_deeplink`); `capture_funnel`/`extract_pricing`/`autoconsent_cmp`; `download_creative`/`is_duplicate` (reuse Plan 3 dedup+s3); `domain_age`/`builtwith_tech`/`publisher_from_app`; `enrich_gem(conn, advertiser_id, *, top_n=5, deps)` worker job (adapter §3.9).

- [ ] **P6-T1** `schemas.py` frozen dataclasses (ResolvedTarget, FunnelStep, PricingExtract, CreativeAsset, CompanyInfo, EnrichResult) — done: import+construct+frozen · verify: `pytest tests/enrich_full/test_schemas.py -q`
- [ ] **P6-T2** `lookup_itunes_batch` (200-chunk, reuse Plan 1 `parse_lookup`) — done: 2-chunk fixture, correct count · verify: `pytest tests/enrich_full/test_appstore.py -q`
- [ ] **P6-T3** Node `gp_lookup.js` sidecar + `lookup_google_play` — done: mock stdout→AppMeta; opt-in live · verify: `pytest tests/enrich_full/test_google_play.py -q`
- [ ] **P6-T4** `resolve_landing` **delegating to Plan 2 `Extractor.resolve_deeplink`** — done: branch chain → ResolvedTarget kind+store_id · verify: `pytest tests/enrich_full/test_resolve_landing.py -q`
- [ ] **P6-T5** `autoconsent_cmp(page)` — done: CMP fixture dismissed, no-banner→False · verify: `pytest tests/enrich_full/test_autoconsent.py -q`
- [ ] **P6-T6** `capture_funnel` (reuse Plan 2 Playwright + geo proxy, per-step screenshots → S3) — done: 3-step local fixture → 3 FunnelSteps · verify: `pytest tests/enrich_full/test_capture_funnel.py -q`
- [ ] **P6-T7** `extract_pricing` (JSON-LD Offer + regex fallback) — done: 2 paywall fixtures → plans/trial; none→None · verify: `pytest tests/enrich_full/test_extract_pricing.py -q`
- [ ] **P6-T8** `download_creative` (aria2c/ffmpeg, sha256+phash via Plan 3 dedup, → S3) — done: near-dup phash hamming < threshold → deduped · verify: `pytest tests/enrich_full/test_creatives.py -q`
- [ ] **P6-T9** `company.py` `domain_age`(whois)/`builtwith_tech`/`publisher_from_app` — done: whois+builtwith fixtures parsed; writes `upsert_advertiser_enrichment` · verify: `pytest tests/enrich_full/test_company.py -q`
- [ ] **P6-T10** `enrich_gem(conn, advertiser_id, top_n)` worker job (top-N gated, adapter §3.9) — done: only top_n ads processed, funnels/creatives/apps rows written · verify: `pytest tests/enrich_full -q`

**Phase-verify:** `pytest tests/enrich_full -q` green; `enrich_gem` on a seeded GemScore≥70 gem produces updated app metadata, ≥1 funnel row w/ screenshot key, deduped creatives, advertiser enrichment.
**Risks:** fbcdn signed URLs expire → download at scrape time; Playwright funnel capture is brittle/memory-heavy → cap contexts + top-N; pricing extraction fragile across vendors (keep raw screenshot as ground truth).

---

### Plan 7 — Output + ops  ·  deps: 1,2,3,4,5,6  ·  ~5d
**Goal:** read-only FastAPI over `frontend.emerging_scaling_apps` (shortlist + dossier + **advertiser_id-keyed** review queue, never auto-call) + full ops (compose, cron snapshots/refresh/prune/backup, proxy gateway, health/metrics + rotation alerts, runbook).
**Owns:** `src/agf/api/` (`app`, `deps`, `routers/{shortlist,gems,review,health}`, `schemas`, `queries`), `db/schema/100_frontend_review.sql`, `ops/` (`docker-compose.yml`, `cron/*`, `proxy/gateway.conf`, `monitoring/*`, `RUNBOOK.md`), `tests/api/`, `tests/ops/`. Appends `# --- plan-7 ---` (fastapi, uvicorn).
**Exposes:** `GET /api/shortlist?cohort=&min_score=&limit=&offset=`; `GET /api/gems/{advertiser_id}` dossier; `GET /api/gems/{advertiser_id}/scaling`; `GET /api/review/queue` + `POST /api/review/{advertiser_id}/decision`; `GET /healthz` + `/metrics`; `frontend.review_decisions` (§3.7); `create_app(engine)`; ops scripts; `RUNBOOK.md`.

- [ ] **P7-T1** `queries.py` parameterized SELECTs over matview (§3.6 columns) — done: 3 fake gems, ranked by gem_score DESC, filters · verify: `pytest tests/api/test_queries.py -q`
- [ ] **P7-T2** `schemas.py` Pydantic (`ShortlistRow` w/ `cohort`+`app_name`, `GemDossier`, `ReviewQueueRow`, `ReviewDecisionIn`) — done: sample dict validates, missing gem_score rejected · verify: `pytest tests/api/test_schemas.py -q`
- [ ] **P7-T3** `app.py` `create_app(engine)` + read-only `get_conn` (SET TRANSACTION READ ONLY) — done: `/healthz` 200, OpenAPI lists only GETs + one POST review · verify: `pytest tests/api/test_app.py -q`
- [ ] **P7-T4** shortlist + gems routers (advertiser_id-keyed) — done: ranked filtered shortlist, dossier, 404 unknown · verify: `pytest tests/api/test_routes_shortlist.py tests/api/test_routes_gems.py -q`
- [ ] **P7-T5** `100_frontend_review.sql` (§3.7, advertiser_id PK) + review router — done: queue=undecided≥70, POST inserts + removes from queue · verify: `pytest tests/api/test_routes_review.py -q`
- [ ] **P7-T6** health/metrics over crawl_log — done: seeded rows → correct rates, degraded flips on stale snapshot · verify: `pytest tests/api/test_routes_health.py tests/ops/test_health_checks_sql.py -q`
- [ ] **P7-T7** cron scripts (snapshot, `REFRESH … CONCURRENTLY`, 365d prune, backup) + crontab — done: executable, absolute psql/pg_dump, CONCURRENTLY, valid cron · verify: `pytest tests/ops/test_cron_scripts.py -q && bash -n ops/cron/*.sh`
- [ ] **P7-T8** `ops/docker-compose.yml` full stack (7 services, env-only creds, single source for PG/MinIO §3.10) — done: 7 services + depends_on edges; `docker compose config -q` ok · verify: `pytest tests/ops/test_compose.py -q`
- [ ] **P7-T9** `monitoring/metrics.py` + doc_id-rotation & rate-limit alerts — done: each threshold fires its Alert · verify: `pytest tests/ops/test_metrics.py -q`
- [ ] **P7-T10** `RUNBOOK.md` (doc_id rotation → re-heal via Plan 2 → verify; proxy refresh; restore) + ops smoke — done: mentions each script/module by name incl fallback doc_id · verify: `pytest tests/ops/test_runbook.py -q`

**Phase-verify:** `pytest tests/api tests/ops -q` green; `docker compose -f ops/docker-compose.yml config -q` exits 0; stack up → `/healthz` ok, `/api/shortlist?min_score=70` returns matview rows.
**Risks:** matview/crawl_log schema drift breaks queries (gate on §3.5/§3.6 frozen); `REFRESH … CONCURRENTLY` needs Plan 5's UNIQUE index; read-only enforcement easy to bypass — test default-deny on writes.

---

## 7. Resolved coordination decisions (from the consistency pass — do not re-litigate)

1. **Product matview owned by Plan 5 only** (§3.6); Plan 3 dropped `005_frontend_matview.sql`.
2. **Data access = `conn`-first free functions** (§3.3); Plan 4 uses `conn`, not `repo`.
3. **`Extractor` facade** is the Plan 2 export Plans 4/5/6 consume (§3.4).
4. **`search_ads`/`Extractor.search` carry `sort_by_impressions`, `search_type`, `view_all_page_id`** (Firehose B + bridge).
5. **App→advertiser linkage is Plan 4 P4-T7** (`link_app_to_advertiser`) — closes the Firehose-A→page_id gap.
6. **Dependencies corrected:** Plan 5 = [1,2,3,4]; Plan 6 = [2,3,5]; Plan 7 = [1..6].
7. **S3 client owned by Plan 3** (`src/agf/storage/s3.py`, §3.8).
8. **Output/review keyed on `advertiser_id`, `app_id` NULLABLE** (§3.7) — web funnels stay in.
9. **Matview columns frozen incl `cohort` + `app_name`** (§3.6); Plan 7 builds against them.
10. **Advertiser enrichment columns are Plan-6-populated** via `upsert_advertiser_enrichment`; veto tolerates NULL.
11. **numpy/statsmodels pinned by Plan 5.**
12. **`requirements.txt` shared & append-only, per-phase sections** (§3.10).
13. **`db/schema/` numbering ranges: P3 001–049, P5 050–099, P7 100+** (§3.10).
14. **Worker handler adapter closures** carry cross-cutting deps into `handler(conn,entity_id)` (§3.9).
15. **`pg_conn` in root `conftest.py` only** (§3.10).
16. **Single proxy env contract `AGF_PROXIES`** (§3.10).
17. **Plan 7 `ops/docker-compose.yml` is the prod stack; Plan 3 compose is local-test** (§3.10).

---

## 8. Open questions / decisions log

- **Cohorts to seed first:** all-vertical, or bias to a niche (e.g. Health & Fitness) for sharper cohort normalization? *(owner: founder)*
- **Snapshot cadence + warmup:** daily ad-metrics, weekly advertiser rollup; ~3–4 weeks before acceleration is trustworthy — acceptable? *(owner: founder)*
- **Proxy vendor/budget:** IPRoyal vs Bright Data; monthly bandwidth ceiling? *(owner: founder)*
- **Enrichment depth at v1:** funnel capture is the brittle/expensive part — top-N = 5 ok to start? *(owner: founder)*
- **ToS posture:** private analysis only; no redistribution/reuse-in-own-ads/model-training. Confirm. *(owner: founder)*
- **PoC scope:** ship Plan 1 single-keyword PoC first, or fold Firehose B into the PoC? *(default: ship PoC first)*

**Decisions logged:**
- **[2026-06-04, claude+founder] Extraction method revised (§2, §3.1, Plan 2).** Research + on-machine proof showed the 403 is Facebook's `rd_challenge` (browserless-solvable) and that the real blocker for plain `httpx` is also TLS fingerprinting. Primary transport is now **`curl_cffi` + `rd_challenge` solver + token self-heal + residential proxy** (warm-once/reuse pool); Playwright demoted to standby warmer. Founder chose the self-hosted local-now/hosted-later path. Full analysis: `../research/2026-06-04-server-side-extraction.md`. Proxy vendor question above now has researched options (Evomi ~$0.49/GB, IPRoyal ~$1.75/GB; budget ~$10–40/mo ex-media) — still pending founder's final pick.

- **[2026-06-04, claude+founder] Discovery sources researched + PoC'd (Plan 4 / Firehose A).** Confirmed spend-driven discovery (Meta Ad Library) is the right primary; app-store lane is a seed + release-date post-filter. PoC proved the legacy iTunes RSS new-apps feed is live but ~100–120 newest/run (no genre multiplication) → daily-polled accumulator. Android has no free "new" feed. Web funnels via PublicWWW. Cheap convenience layers: ScrapeCreators ($10/mo), PublicWWW (~$49–79/mo). Full analysis: `../research/2026-06-04-discovery-sources.md`. Closes the "Cohorts to seed first" / discovery-source open questions partially; proxy-vendor + paid-app-intel-spend decisions still pending founder.

Log changes here with date + who, when a §3 contract changes.

---

## 9. Glossary

- **doc_id** — rotating Relay persisted-query hash for the Ad Library GraphQL query; lives in a hashed JS bundle (not inline), rotates ~2–4 weeks, self-healed each session. Known-good fallbacks: `27201872659451053` (live-verified 2026-06-04), `25464068859919530`, `33730956806489539`.
- **rd_challenge** — Facebook's "Client challenge" (shipped ~Jan 14 2026): a 403 + ~481-byte JS stub that POSTs to `/__rd_verify_<token>?challenge=N` to mint an `rd_challenge` cookie (24h). Solvable in pure HTTP (no browser) — regex the path, empty-POST it, retry. The reason plain GETs 403; see Plan 2 + `../research/2026-06-04-server-side-extraction.md`.
- **Firehose A/B/C** — new-app RSS+MWM / EU-impressions radar / funnel-tech fingerprint discovery nets.
- **EU→US bridge** — re-query an EU-discovered `page_id` with `country=US` to get its richer US ads.
- **Scaling proxy** — longevity + active-creative-count velocity + collation duplication (US has no real spend).
- **GemScore** — `100·SV^.5·UR^.35·FI^.15`; multiplicative AND-gate so incumbents → 0.
- **SV / UR / FI** — ScalingVelocity / UnderRadar / FunnelIntent axes.
- **Veto** — post-score filter (STL deseasonalize, longevity-floor, disguised-incumbent, relaunch, persistence-gate).
- **Cohort** — category × country; all normalization is within-cohort.
- **Cross-check / scaling-confirm** — mandatory Ad-Library check that a candidate has active ≥14d creatives.
