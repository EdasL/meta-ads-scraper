# Handoff — Scrape / Discovery Rethink v2

**Date:** 2026-06-09 · **Continues:** `docs/HANDOFF-discovery-rethink.md` (read that first for the original framing). This doc adds what was *validated live* this session, a concrete proposed design, and the open research questions to resume on.

> **STATUS UPDATE 2026-06-10** (see `docs/HANDOFF-gem-signals-2026-06-10.md`):
> - **Open question 1 (MWM data depth): RESOLVED — MWM is dead for per-app data.** Live probe: curated ~126-app incumbent directory, downloads as coarse buckets only ("500M+"), no time series, `api.mwm.ai` server-side-only. Per-app enrichment now comes from iTunes Lookup + google-play-scraper (Play `maxInstalls` = near-exact installs).
> - **Open question 3 (per-advertiser total): RESOLVED + SHIPPED.** There is no total field in the envelope (see `[[meta-no-total-count-field-2026-06-09]]`); `count_active_ads` does bounded `view_all_page_id` pagination with early exit → `advertisers.meta_active_ads` (+`_capped`). Live-proven: 1/105/220/253+.
> - **Design item 4 (true count + sampled storage): capture SHIPPED; the incumbent-veto wiring into scoring is still TODO** (`meta_active_ads_capped` is stored + shown in the UI, not yet read by `scoring/vetoes.py`).
> - `page_like_count` is now persisted on `public.advertisers` (was AdRecord-only).
> - Open questions 4–6 (funnel taxonomy, veto threshold calibration, non-game classification) and the two pending decisions remain open — but see the 2026-06-10 discovery-harness memory note (`[[discovery-harness-2026-06-10]]`): a keyword taxonomy + multi-technique harness now exists in `src/agf/discovery/taxonomy.py` + `scripts/discover_experiment.py` (separate session, uncommitted).

---

## TL;DR

The discovery **candidate-selection** problem is still the one open issue. This session we (a) proved the real scrape end-to-end via the DataImpulse proxy and got **real ads + working links** into the UI, (b) found the two concrete defects the current scrape has, and (c) brainstormed a seeded **2-branch** redesign. The design is **not approved/implemented** — pending your research on MWM + two decisions below.

---

## The two problems you flagged (this session)

1. **Wrong candidates.** We surface *games* and *non-app / non-quiz-funnel* brands (e.g. a dessert shop, an urgent-care clinic, mobile games). Target should be **emerging non-game apps** OR **web quiz funnels** — nothing else.
2. **Ad counts are wrong + storage is unbounded.** A brand showed `active_ad_count = 1` but its Ad Library has ~2,000 ads. We undercount (we only store the few ads we scraped) *and* 2k ads is far too much to store. Also: a 2k-ad brand is an **incumbent, not a gem** — the count itself should veto it.

---

## Hard constraints learned this session (don't re-discover these)

- **Meta has NO recency sort.** The only sort is `SORT_BY_TOTAL_IMPRESSIONS` (`src/agf/extract/search.py:26`). There is no "newest ads" mode. → A **keyword-free "non-stop" firehose always returns the biggest spenders first**; a small/new advertiser sits millions of rows down and is never reached. **Conclusion: discovery must be *seeded/targeted*, not a blind sweep.** This is the crux — "go through Meta non-stop" cannot work alone.
- **Per-advertiser ad count IS cheaply available, we just don't capture it.** `search_ads` supports `view_all_page_id=<page_id>` (per-advertiser query) but returns only `(records, last_cursor, has_next)` — it **discards the response's total count**. Meta's GraphQL envelope carries the true total (the "~2,000 results" the UI shows). Today `active_ad_count = len(stored active ads)` (`src/agf/scoring/features.py:141`) → undercount. Fix = read the total from one `view_all_page_id` request; store only a sample of creatives.
- **MWM (mwm.ai) is a real, usable app-rankings source.** It's an app-store-intelligence SaaS backed by `api.mwm.ai` (`/rankings/countries`, `/apps`: "Browse by category", "Top apps right now", categories, publishers, ranks). It is **geo/IP-gated**: direct fetch → **403**; via the **DataImpulse residential proxy + `curl_cffi` Chrome impersonation → 200**. Paid product (login/pricing) so deep data may need auth/API key — **not yet reverse-engineered** (no `__NEXT_DATA__`; the table loads client-side from `api.mwm.ai`). Working probe:
  ```python
  from curl_cffi import requests
  P="http://<user>:<pass>@gw.dataimpulse.com:823"
  r=requests.get("https://mwm.ai/apps", impersonate="chrome", timeout=40, proxies={"http":P,"https":P})
  # r.status_code == 200 ; references https://api.mwm.ai
  ```
- **DataImpulse proxy works.** Egress = Chile residential (`Netv SPA`), not datacenter-flagged; Meta Ad Library smoke returns ads. Format `user:pass@gw.dataimpulse.com:823`. Validate with `scripts/check_proxies.py`. (Rotate the password — it was entered in plaintext.)
- **Gemini model bump.** `gemini-2.0-flash` is **retired** (404 "no longer available"). The Go explainer helper now uses **`gemini-2.5-flash`**, overridable via `GEMINI_MODEL` env (`backend/helpers/gemini.go`). femrun's container key works (`docker exec femrun-api printenv GEMINI_API_KEY`); the app needs its own `GEMINI_API_KEY` going forward.

---

## Proposed design (brainstormed — NOT approved)

**Two seed sources → one Meta measurement + classification pipeline.** Honors your instincts (link-destination classification + MWM for apps); corrects the firehose assumption (must seed).

1. **App branch — seed = MWM rankings + app-store new-releases.** Pull app candidates from MWM (by category, **drop Games**) and from iTunes/Google-Play *new-releases* (the iTunes RSS new-apps feed was already PoC'd — genuinely new apps, ~100–120/run; see `docs/research/2026-06-04-discovery-sources.md`). These surface small/emerging apps **by name, independent of Meta impressions**. For each, resolve its Meta advertiser page (search by name / store link) and measure on Meta.
2. **Funnel branch — seed = niche keywords.** Quiz funnels have no app-store presence, so seed Meta keyword-search with quiz/funnel niche terms ("take the quiz", "manifestation", "blood sugar", …). Needs a curated taxonomy.
3. **Classify by destination (the gate).** For each seeded advertiser's ads, inspect `link_url` + `cta_type`:
   - app-store URL (`apps.apple.com` / `play.google.com`) → **app**; confirm genre ≠ Games (store lookup already exists) → keep.
   - other landing page → run the **funnel detector** (`src/agf/enrich_full/funnel_capture.py`) → if quiz funnel → keep.
   - neither → **discard** (kills dessert-shops / urgent-cares).
4. **True count, sampled storage (the 2k fix).** One `view_all_page_id` request per kept advertiser → read Meta's **real active-ad total** → store as `active_ad_count`; download only ~5 representative creatives. **Incumbent veto:** total > ~150–200 active ads → not a gem (deprioritize/exclude).
5. **Velocity/scoring unchanged.** Daily re-runs accumulate the week-over-week signal; incumbent veto + velocity sort emerging from established.

---

## Open research questions (resume here)

1. **MWM data depth (the big one).** Does MWM expose **new / rising / breakout / trending** rankings (an emerging signal), or only **top charts** (= incumbents, same problem)? Find the `api.mwm.ai` endpoint(s), auth model (does useful data need a paid login / API key?), rate limits, and fields (store id / bundle id, category, publisher, rank, *trend/delta*). Tip: load `mwm.ai/apps` **through the proxy in a real browser** and watch the network tab for `api.mwm.ai/...` calls (Playwright direct = 403; route it through the proxy).
2. **Is MWM better than the iTunes RSS new-releases feed** we already PoC'd? If MWM is top-charts-only, RSS new-releases may be the stronger *emerging* seed and MWM becomes enrichment/category only.
3. **Reading Meta's per-advertiser total.** Confirm which field in the GraphQL response envelope holds the total active-ad count for a `view_all_page_id` query (verify with one live request). This is the count fix's keystone.
4. **Funnel keyword taxonomy.** What niche/quiz terms reliably surface quiz funnels? (Or defer the funnel branch to v2.)
5. **Incumbent-veto threshold.** What active-ad count means "too big to be a gem"? (Sample real emerging vs incumbent advertisers to calibrate.)
6. **Non-game classification.** Genre from the existing app-store lookup is the signal; confirm coverage for the seeded apps.

## Two decisions pending (needed before a spec)
- **App seed:** MWM rankings + iTunes/Play new-releases (recommended), or MWM only?
- **Funnels:** seed with a curated niche-keyword list now, or **defer to v1 and nail apps first**?

---

## What this session already shipped (on the working tree — UNMERGED, branch `main`)

The UI/data overhaul is done + verified locally (not committed):
- **Card redesign**: real ad creative (image + **video**) as the hero, corner score badge, quantified growth line, semantic **color** on the dark base. Empty/no-creative advertisers are **filtered out** of the feed (`backend/repositories/shortlist_repo.go` EXISTS-creatives clause).
- **Voice inverted**: plain UI; grug confined to the expandable `GrugExplainer` (`web/`); `CLAUDE.md` + `.cursor/rules/grug-copy.mdc` updated to match.
- **Mock removed**: frontend is live-only (`web/src/lib/mock/` deleted, `NEXT_PUBLIC_DATA_MODE` gone).
- **New data**: `apps.description`, `adtech.advertiser_growth`, growth cols on the `frontend.emerging_scaling_apps` matview, `adtech.advertiser_explainers` (DB migrations 103–106). Per-advertiser **growth** + **hero_creative** + **explainer** + **page_id** now on the API (`backend/`).
- **Gemini explainer** in Go (`backend/helpers/gemini.go`, `backend/cmd/explainers`), model `gemini-2.5-flash`.
- **Real scrape proven**: `scripts/scrape_real.py` (force-enrich bypassing the alert gate) pulled 7 real advertisers with real creatives (Primal Queen, Whiteout Survival, …) + working links. Demo seed (`scripts/seed_demo.py`, `scripts/demo_media.py`) makes coherent counts + real sample media.

**Caveat:** real advertisers score low / flat growth (one sweep = no week-over-week velocity); needs daily accumulation. Demo gems still carry the high-growth story but with placeholder links.

---

## Local stack / how to resume
- Postgres `:5433` (agf/agf/agf), MinIO `:9010` (minioadmin), Go API built at `/tmp/agf-api`, web dev server `:3000`. Bring up per `[[local-stack-run-2026-06-09]]`. `NEXT_PUBLIC_API_URL` must end in `/api`.
- Inspect real (non-demo) advertisers:
  ```bash
  docker exec db-postgres-1 psql -U agf -d agf -tAc "select page_name, page_id, gem_score from frontend.emerging_scaling_apps m join public.advertisers a on a.id=m.advertiser_id where a.page_id not like 'DEMO%' order by gem_score desc;"
  ```
- Real scrape: `AGF_PROXIES=... PYTHONPATH=src .venv/bin/python scripts/scrape_real.py` (env in the script header). Validate proxy first: `scripts/check_proxies.py`.

## Code pointers
- Discovery: `src/agf/discovery/{run_discovery,firehose_eu,bridge,confirm,link_apps,seeds}.py`
- Search params (sort, `view_all_page_id`, `search_type`): `src/agf/extract/search.py`; variables: `src/agf/extract/variables.py`
- Ad parse (no total captured): `src/agf/parse.py`; count today: `src/agf/scoring/features.py:141`
- Funnel detector: `src/agf/enrich_full/funnel_capture.py`; app-store lookup/genre: `src/agf/enrich_full/appstore.py`
- iTunes RSS new-releases research: `docs/research/2026-06-04-discovery-sources.md`

## Don't re-litigate (settled)
Meta egress (DataImpulse residential), scoring/velocity mechanism, app-linking, the UI redesign + real-data wiring, the count *availability* (it's there, just uncaptured). The open work is **candidate selection (seeding + classification)** + **capturing the true count + incumbent veto**.
