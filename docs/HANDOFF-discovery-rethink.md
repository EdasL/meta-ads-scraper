# Discovery Rethink — Handoff for a Brainstorming Session

**Date:** 2026-06-09 · **Purpose:** seed a fresh brainstorm on *how to discover **emerging** app gems* (not incumbents). Everything else in the pipeline is done and deploy-ready — **discovery candidate-selection is the one real open problem.**

**Read first:** `docs/HANDOFF.md` (full build state) → `docs/research/2026-06-04-discovery-sources.md` (the original discovery design + why the app-store "Firehose A" seed was dropped) → this doc.

---

## The problem in one sentence

Our discovery engine — an EU **impressions-sorted** Ad Library sweep — surfaces the **biggest spenders** (Nike, Alibaba, Shopify, Blinkit, Nabd: incumbents released 2012–2016), **not** small, fast-growing emerging apps. We need a discovery method that puts *emerging app advertisers* into the tracked pool in the first place.

## What is ALREADY solved this session (do NOT re-litigate — all merged to `main`, 248 tests green)

- **Meta egress: SOLVED & proven.** Datacenter proxies get rate-limited (`1675004`); **DataImpulse rotating residential ($5/5GB)** returns real ads. A real sweep landed 19 advertisers across DE/GB/FR. (`[[proxy-datacenter-meta-graphql-ratelimit]]`)
- **Resilience:** throttle detection (`raise_for_fb_error`), inter-page pacing/jitter, exponential backoff, IP-rotation-on-throttle, transport-timeout retry. A slow proxy exit no longer crashes a sweep.
- **Velocity mechanism: wired.** `snapshot_advertiser_week` was *never called in production* (the system literally could not produce a gem); now wired into every sweep, scoped to the swept advertisers.
- **App-linking: accurate, 0 gaps.** Every app-install-CTA ad links to its store app; metadata (name/genre/release/ratings) now resolved every sweep (`resolve_pending_app_metadata`).
- **Scoring discriminates correctly.** Demo gems with real velocity alert ≥70; real incumbents with no velocity score below. **The scorer is fine — the problem is the candidate POOL, not the scoring.**
- **Deploy-ready:** RUNBOOK §8 has the Hetzner + daily-cron + DataImpulse steps.

## Why the current discovery surfaces incumbents (the mechanics)

- Firehose B = `extractor.search(query="", countries=[cc], sort_by_impressions=True)` → `SORT_BY_TOTAL_IMPRESSIONS DESCENDING` → the **top of the market by spend** = incumbents.
- **Keyword-free** → no steering toward apps or niches.
- Of 19 discovered advertisers, only ~4 are apps; the rest are ecommerce/web brands — and the apps are all incumbents.
- The velocity signal can only flag a riser **if that riser is in the tracked pool**. A small/new app sits far down the impression ranking, so the impressions sweep never adds it → it can never become a gem, no matter how many weeks pass.

## Brainstorm seeds (options to explore — NOT decisions)

1. **Keyword-seeded discovery (your lead).** The Extractor *already* supports `query` + `search_type="KEYWORD_UNORDERED"` (`src/agf/extract/search.py`) — keyword search is a small addition, not new infra. Search app-category / niche / pain-point terms ("sleep sounds", "calorie tracker", "AI tutor"…) to surface app advertisers **by topic regardless of size**. Trade-off: needs a keyword taxonomy; misses apps outside the keyword set.
2. **App-install-CTA filtering.** App advertisers use `INSTALL_MOBILE_APP` / `USE_MOBILE_APP` CTAs. Filter discovery to those → app advertisers specifically (vs today's all-advertisers firehose). The data already shows 0 linking gaps on these ads.
3. **Niche impressions sweeps** = `query=<niche>` + `sort_by_impressions=True` → the biggest spenders *within a niche*, surfacing niche-leading emerging apps instead of global incumbents. (Combines #1 + the existing spend signal.)
4. **Revisit "Firehose A" (app-store new/trending as a SEED).** It was dropped (iTunes = enrichment only) — but for *emerging* apps, app-store new-releases / top-charts (iTunes RSS, Google Play) are a natural source of **small/new app candidates**. Seed candidates there, then check each one's Meta ad activity + track velocity. (PoC'd RSS feed details in the discovery-sources research.)
5. **Lower the size/longevity floors for app-CTA advertisers.** `min_running_days=25` may exclude genuinely new apps; a 14–25-day app scaling fast is exactly the target.
6. **Lookalike/seed expansion** from a few known emerging apps (similar creatives/funnels/category).

## Constraints any approach must respect

- **Residential egress + rate limits.** Keyword searches = many more requests than one impressions sweep — pace accordingly (pacing/backoff/rotation already built).
- **The gem signal stays week-over-week velocity + incumbent veto.** Discovery's only job is to get the *right candidates* (small app advertisers) into the daily-tracked pool; scoring + veto + time do the rest.
- **It must run daily and accumulate** — velocity needs weeks regardless of discovery method.
- **Official Ad Library API** is keyword-based but EU/Brazil/political-only, needs ID verification, and has zero US commercial ads — ruled out as the engine (its keyword params do mirror the internal GraphQL, though).

## Where to look (code + data)

- Discovery: `src/agf/discovery/{run_discovery,firehose_eu,bridge,confirm,firehose_fingerprint,link_apps,seeds}.py`
- Keyword search already supported: `src/agf/extract/search.py` (`query`, `search_type`, `sort_by_impressions`, `view_all_page_id`)
- Original design + why Firehose A was dropped: `docs/research/2026-06-04-discovery-sources.md`
- The proven free-egress options: `docs/research/2026-06-07-free-scraping-options.md`

## Current state to resume from

- Branch `main`, suite green (248 passed, 7 deselected). All session work merged.
- Local stack up: Postgres `:5433`, MinIO `:9010`, Go API `:8000` — demo gems + 19 real advertisers (DE/GB/FR) loaded.
- DataImpulse proxy works via `AGF_PROXIES='http://<user>:<pass>@gw.dataimpulse.com:823'` (rotate the password — it was entered in plaintext).
- Inspect the current real data:

  ```bash
  AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf PYTHONPATH=src .venv/bin/python -c "
  from sqlalchemy import text; from agf.db.engine import get_engine
  with get_engine().connect() as c:
      for r in c.execute(text(\"SELECT page_name, gem_score, app_name, cohort FROM frontend.emerging_scaling_apps m JOIN public.advertisers a ON a.id=m.advertiser_id WHERE a.page_id NOT LIKE 'DEMO%' ORDER BY gem_score DESC\")):
          print(r)"
  ```

- A single sweep is run via `/tmp/agf_sweep.py` (env `AGF_SWEEP_COUNTRIES`, `AGF_SWEEP_PAGES`); the production daily loop is `python -m agf.workers.main`.
