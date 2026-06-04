# Emerging Meta-Ads App & Funnel Finder — Design Spec

**Date:** 2026-06-04
**Status:** Design (pre-implementation) — pending user review, then `writing-plans`
**Companion diagram:** `2026-06-04-emerging-meta-ads-app-finder-architecture.excalidraw`

---

## 1. Goal

Continuously discover **under-the-radar software products — mobile apps, web apps, and quiz/subscription funnels — that are actively SCALING on Meta ads**, and for each confirmed find, capture its **product, full funnel, live ads, and company info**. The emphasis is on **emerging hidden gems**, explicitly *not* the well-known incumbents everyone already tracks.

This is a standalone intelligence pipeline, not part of the FemRun app. It lives in its own workspace/repo.

---

## 2. Locked scope decisions

| Decision | Choice | Rationale |
|---|---|---|
| **What counts** | Any vertical, but the target must be a **software app or web funnel** (not physical goods/services) | User decision — broadest discovery surface |
| **Geography reported** | **US** (primary target market) | User decision |
| **Extraction method** | **One method: scrape the Meta Ad Library web UI in Chrome** (Playwright, GraphQL interception). No official API as backbone. | User decision — simplicity; official `ads_archive` API can't see commercial US ads anyway |
| **Discovery seed** | **Keyword-free, multi-firehose** (not keyword-sweep) — see §5 | The hard problem is finding *unknowns*; keyword-sweep only finds the already-visible |
| **Plan format** | Excalidraw architecture diagram + `writing-plans` doc | User decision |

---

## 3. The one fact that shapes everything

There are **two different things** both called "Ad Library":

1. **Official Ad Library *API*** (`graph.facebook.com/…/ads_archive`) — gated to **political/issue ads globally + commercial ads only for EU/EEA/UK-delivered ads** (Digital Services Act). It returns **no commercial US ads**, **no spend, no impressions** for commercial ads, and has **no sort-by-impressions**. *Verified verbatim from Meta v25.0 docs.* → **Not usable as the backbone.**
2. **Ad Library *website*** (`facebook.com/ads/library`) — shows **every commercial ad worldwide, US included**, no token, powered by an internal **GraphQL endpoint** (`facebook.com/api/graphql/`). This is what every ad-spy tool (Foreplay, GetHookd, AdSpy) actually scrapes. → **This is our backbone.**

**Honest consequence:** for **US** commercial ads, Meta exposes **no spend / impressions / demographics** (those are EU-DSA-only). So "scaling" is **always a proxy** — exactly the proxy every ad-spy tool uses: **ad longevity + active-creative-count velocity + creative duplication**. We do not get ground-truth spend; nobody does.

---

## 4. Extraction method (the single method)

**Drive a real Chrome via Playwright against `facebook.com/ads/library`, intercept the page's own `api/graphql` responses** (`page.route('**/graphql')`), and read structured JSON — *not* DOM parsing.

Verified, concrete artifacts (from research, cross-confirmed across 3+ repos + a live HTTP 200 on 2026-06-04):

- **Endpoint:** `POST https://www.facebook.com/api/graphql/` (form-encoded).
- **Operation:** `AdLibrarySearchPaginationQuery`, keyed by a rotating `doc_id`.
- **`doc_id` self-heal:** regex-scrape the current `doc_id` + `LSD` token from `/ads/library/` page HTML each session. **Never hardcode** (rotates ~weekly). Live-verified fallback: `27201872659451053`.
- **Variables:** `{activeStatus:"ACTIVE", adType:"ALL", countries:["US"], queryString:"", searchType:"KEYWORD_UNORDERED", first:30, sessionID, collationToken, …}`. Sort (EU only): `sortData:{mode:"SORT_BY_TOTAL_IMPRESSIONS", direction:"DESCENDING"}`.
- **Response path:** `data.ad_library_main.search_results_connection.edges[].node.collated_results[]`. Per-ad: `ad_archive_id, page_id, page_name, start_date, end_date, is_active, publisher_platform[], collation_count, snapshot.{link_url, cta_type, cta_text, images[].original_image_url, videos[].video_hd_url, body.text}`.
- **Pagination:** reuse `sessionID`+`collationToken`, feed `page_info.end_cursor`. Strip leading `for (;;);` before parse.
- **Per-ad EU detail** (optional, EU-delivered only): `AdLibraryAdDetailsV2Query` → `aaa_info.eu_total_reach` + reach breakdowns.

**Fork base:** `ChrisFeldmeier/fb_ad_scraper` (Python; token bootstrap, request builder, EU-reach parse) — read-and-rewrite (no license). Add: real pagination loop, live cookie/token capture, residential proxy rotation + backoff, Postgres upsert.

**Honest risks:** ToS-gray (automating the web UI breaches Meta ToS; *Meta v. Bright Data* weakened Meta's position but it's contested); `doc_id`/token rotation (mitigated by self-heal); anti-bot at scale (needs residential proxies + backoff — the real recurring cost); CDN media URLs are signed and **expire — download bytes at scrape time**.

---

## 5. Discovery — the hidden-gem methodology (the hard part)

Keyword-sweep and top-charts only surface what's already visible. To find **unknowns**, run **three parallel, no-seed firehoses** that converge into one tracker.

### Firehose A — New-app firehose (free, build first)
- Apple **legacy iTunes RSS** `https://itunes.apple.com/{cc}/rss/newapplications/limit=100/json` + `/newfreeapplications/`, polled **daily** per country (live-verified: ~108 fresh apps/country, `userRatingCount=0` on launch day). Dedupe vs yesterday.
- **MWM database** (`mwm.ai/apps`, 266k iOS apps) — filter to recent `first-released date`, extract App Store IDs as a backfill/long-tail catalog lane.
- Google Play via `google-play-scraper` (`TOP_FREE`/`GROSSING`; note: `NEW`/`MOVERS` collections were removed — throttle 5–10/s).
- **Why hidden gems:** lists apps *before any chart rank exists* — small-base by construction.

### Firehose B — EU impressions firehose (free-to-scrape, the direct paid-scaling surface)
- Crawl the Ad Library web-GraphQL for **`country=DE` + `GB`**, **empty search box**, **sort = impressions DESC**, no seed. Collapse ad-level → advertiser by `page_id`. Keep Running-Days ≥ 25, drop the sub-100 "Low Impression Count" badge.
- **Why hidden gems:** no seed means a no-name app burning budget competes with Nike on the *same delivery axis* → unknowns float to the top.
- **De-noise:** impressions = **reach, not spend** (warm retargeting inflates it). Confirm with EU DSA EUR spend bands or longevity.

### Firehose C — Funnel-tech fingerprint firehose (mostly free)
- **Source-code search** (PublicWWW free top-3M domains; NerdyData $75/report for JS-rendered) for paywall SDKs and the paid-acquisition signature:
  - Paywall SDKs: `fnlfx.com`+`paywalls-` (FunnelFox/Adapty web2app), `cdn.superwall.me/runtime/entrypoint.js`, `api.revenuecat.com`+`@revenuecat/purchases-js`.
  - **Paid-acquisition classifier (strongest signal):** Meta Pixel (`connect.facebook.net/.../fbevents.js`+`fbq(`) **∩** an MMP smart-banner (`cdn.branch.io/branch-latest.min.js`, AppsFlyer OneLink, Adjust). Co-presence ≈ "this domain exists to buy Meta installs."
  - Quiz builders: `embed.typeform.com/next/embed.js`, Funnelish/Unbounce/Involve.me; custom Next.js = `/_next/static/` ∩ paywall SDK ∩ pixel.
- **Why hidden gems:** source-code search returns the **long tail**, not famous brands. Surfaces **US-only + web-native funnels** the EU radar can't see.

### EU→US bridge (Stage 3)
For each advertiser surfaced in Firehose B, take `page_id` and **re-query `country=US`**: `…/ads/library/?active_status=active&ad_type=all&country=US&search_type=page&view_all_page_id=PAGE_ID` → pull the **full, richer US creative set**. *Viable but probabilistic:* a serious multi-geo US scaler almost always runs *some* EU delivery (DSA payer disclosure on worldwide campaigns), so DE/GB surfaces most of them — but a **strictly US-geofenced** advertiser carries no impression label and is **invisible** to B. Firehoses A and C patch that hole.

### Non-negotiable cross-check
A seed rising on installs may be growing **organically (TikTok/UGC) with zero Meta spend**. **Every** candidate must be confirmed against the Ad Library for **active, longevity-cleared (≥14d) paid creatives** before it counts as "scaling on Meta." Discovery and scaling-confirmation are two separate steps.

---

## 6. Scoring — the GemScore (emerging × scaling × intent)

Normalize every signal **within a cohort** (category × country), never globally. Score **derivatives, not levels** (log-growth WoW + positive acceleration) — incumbents sit on a flat mature S-curve and self-filter.

**Stage 1 — three sub-scores (weighted sum within axis = tolerant of one missing signal):**
- **ScalingVelocity** = 0.40·z(active-creative *acceleration*) + 0.25·z(≥14d longevity share) + 0.20·z(rating/install velocity) + 0.15·z(web-traffic accel)
- **UnderRadar** (incumbent-exclusion; inverted) = 0.45·(1−pctile branded_search) + 0.25·(1−pctile rating_count) + 0.20·notInTopCharts + 0.10·(1−pctile domain_age/traffic)
- **FunnelIntent** (0–1) = paywall SDK + Pixel/CAPI present + fast landing iteration + real checkout

**Stage 2 — multiplicative AND-gate (weights as exponents):**

```
GemScore = 100 · clamp01(ScalingVelocity)^0.5
              · clamp01(UnderRadar)^0.35
              · clamp01(FunnelIntent)^0.15
```

**Why multiplicative is the whole point:** a famous brand has high branded-search → UnderRadar ≈ 0 → GemScore ≈ 0. "Still unknown" becomes a **hard requirement**, not a weighted term a strong ad signal can mask. *Correction:* the AND-gate's zero-collapse will also kill genuine gems with sparse data — use a **penalized geometric mean / per-axis floor / imputation**; "robust to one missing signal" holds **within-axis only**.

**Hard thresholds feeding ScalingVelocity:** 14+ days = proven creative; 3–5 days = failed test (discount); 6–12 variations of one hook = scaling a winner; jump 3–4 → ~15 ads/week = velocity spike. Weight count by ≥14-day-survivor share so dropshipper ad-spam scores ~0.

**False-positive vetoes (post-score):** STL deseasonalize (kill seasonal/viral spikes) · longevity-share floor · disguised-incumbent (shared WHOIS/pixel/payer/dev-account → known parent) · relaunch (require young release/domain) · **persistence gate** (positive acceleration ≥2–3 consecutive snapshots).

**Alert** when GemScore ≥ 70 AND acceleration sustained ≥2 weeks AND all vetoes pass → **ranked human-review shortlist, never auto-call** (true gems are rare; base-rate fallacy guarantees absolute false positives).

> Weights (0.5/0.35/0.15), ≥70, and the ad heuristics are **unvalidated starter priors** — back-test against known past breakouts; ideally replace hand-weights with a fitted logistic/learning-to-rank model.

---

## 7. Enrichment (per confirmed gem)

- **Product:** iTunes Lookup (`itunes.apple.com/lookup?id=`, free, batch 200) + `google-play-scraper` → release date, ratings/installs, publisher, genre, screenshots.
- **Funnel:** Playwright drives the resolved landing page, per-step full-page screenshots, schema-driven LLM extraction of pricing/paywall, residential proxy in target geo, CMP autoconsent. App-classify via `snapshot.link_url`/`cta_type` → resolve deeplinks (`app.link`/`onelink.to`/`apple.co`) with an iOS UA → App Store / Play / web SaaS.
- **Ads:** creative gallery from `ad_creative_*`; **download image + video bytes** from `*.fbcdn.net` at scrape time (signed URLs expire); SHA-256 + perceptual-hash dedup.
- **Company:** app-store publisher + WHOIS (domain age) + BuiltWith (tech). Paid only for funding/headcount (Apollo/Crunchbase). Don't scrape LinkedIn — license it.

---

## 8. Architecture, data model, stack

**Pattern (single-VPS, Postgres-as-queue — proven by `ddxv/adscrawler`, MIT, powers a live 6M-app system):**
`discovery scheduler (cron + SQL staleness) → list-fetch workers → app-intent filter → store-enrich workers → (optional) EU-reach detail worker → creative/funnel worker (high-value only) → scoring matviews → read-only API`.

**Data model (fork adscrawler schema):**
`advertisers(page_id UNIQUE, parent_company_id self-FK)` · `apps(UNIQUE(store,store_id), release_date, rating_count, is_emerging)` · `ads(external_ad_id UNIQUE, link_url, cta_type, is_active, start/end, collation_count, first/last_seen)` · `creatives(s3_key, sha256)` · `ad_metrics(ad_id, snapshot_date, eu_total_reach)` · `app_advertiser_metrics(advertiser_id, week_start, active_ad_count, distinct_creative_count)` ← the scaling-velocity table · `funnels(landing_url, screenshot_s3_key)` · `logging.crawl_log` (staleness queue) · `frontend.emerging_scaling_apps` (matview = product output).

**Stack:** Python 3.13 workers + Node sidecar for `google-play-scraper` · Playwright (funnel capture + token bootstrap, not hot path) · PostgreSQL 17/18 (spine **and** queue) · MinIO/S3 (bytes; PG holds only keys) · aria2c/ffmpeg/SHA-256 · cron + N worker processes + PID-file mutex (**no Redis/Celery day-1**) · docker-compose on an 8–16GB VPS · FastAPI read-only output. Upgrade path (only when PG-as-queue saturates): Redis/BullMQ + proxy gateway + Prometheus/Grafana.

**Feasibility verdict:** Buildable by one engineer to first useful output in ~1 week; a **~3-hour one-day PoC** proves the full chain (bootstrap → list scrape → app-classify → emerging-enrich → scaling proxy → ranked CSV).

---

## 9. Honest risks & caveats (carry into the plan)

1. **ToS / legal** — automating the web Ad Library breaches Meta ToS; private competitive analysis is generally defensible, redistribution/reuse-in-own-ads/model-training is not. Business-risk decision, not just engineering.
2. **No true spend/impressions for US commercial ads** — scaling is a proxy (longevity + creative-count velocity + duplication).
3. **EU→US bridge has a structural hole** — strictly US-only advertisers are invisible to Firehose B; A and C patch it; quantify the gap empirically.
4. **Maintenance treadmill** — `doc_id`/token rotation, anti-bot, UI churn, store-scraper breakage, PublicWWW homepage-only/paywall limits. Budget ongoing upkeep + residential proxies as the recurring cost.
5. **Unvalidated thresholds** — back-test before trusting GemScore precision; keep a mandatory human-review queue.
6. **Cohort discipline** — normalize within category × country (relevant if we later want women's-health/fitness specifically); a single US/all-category scan buries small risers.

---

## 10. Proposed build phases (detail → `writing-plans`)

- **P0 — One-day PoC:** single script, no DB/proxies, proves bootstrap → US list scrape → app-classify → iTunes emerging-enrich → scaling proxy → ranked CSV.
- **P1 — Extraction core:** fork ChrisFeldmeier, `doc_id`/token self-heal, real pagination, proxy + backoff, Postgres upsert.
- **P2 — Data spine:** adapt adscrawler schema, dedup, staleness queue, worker model.
- **P3 — Discovery firehoses:** A (RSS + MWM) → B (EU firehose + US bridge) → C (fingerprinting).
- **P4 — Velocity tracker + scoring:** daily snapshots, GemScore + vetoes + persistence gate, matview output.
- **P5 — Enrichment:** funnel capture, creative byte download, company info.
- **P6 — Output + ops:** FastAPI read model / shortlist UI, monitoring, proxy ops.

---

## 11. Open calibration questions (resolve during planning)

- Cohorts to seed first (all-vertical, or bias toward a niche?).
- Snapshot cadence + warmup tolerance (3–4 weeks before acceleration is trustworthy).
- Proxy vendor + budget (IPRoyal / Bright Data).
- How much funnel/company enrichment depth at v1 (it's the most brittle, memory-heavy part — scope to top-N).
