# Emerging App Finder — Build Handoff

**Last updated:** 2026-06-04 · **Branch:** `main` (all work merged) · **Tests:** 153 passing + live end-to-end verified (incl. Plan 5 live Postgres e2e + assembled smoke)

This is the single orientation doc for picking the build back up. The **source of truth** is `docs/plans/00-master-plan.md` (§3 frozen contracts, §5 dashboard, §6 phases, §8 decision log). Read this, then that.

---

## 1. Where we are

| Plan | Subsystem | Status | Verified how |
|---|---|---|---|
| 1 | PoC | ✅ done | 30 unit + live iTunes; FB-via-httpx is `xfail` (anti-bot — superseded by Plan 2) |
| 2 | Extraction core (curl_cffi + rd_challenge) | ✅ done | 44 unit + **2 live** against real Facebook (real ads, paginated) |
| 3 | Data spine (Postgres-as-queue) | ✅ done | 25 DB/worker + 1 S3 against **real Postgres 17 + MinIO** |
| 4 | Discovery firehoses (B + C) | ✅ done | 16 unit (fakes + live Postgres) |
| **—** | **Full 4-plan pipeline** | ✅ **proven** | **live e2e: real Meta Ad Library → discovery → bridge → 14-day confirm → rows in Postgres** |
| 5 | Velocity tracker + GemScore | ✅ done | 38 tests: pure-stats unit + **live Postgres** e2e + assembled smoke (gem alerts ≥70, brand UR-gated, spike STL-vetoed; REFRESH … CONCURRENTLY ok) |
| 6 | Enrichment | ⬜ **NEXT** (deps 2,3,5 ✅) | — |
| 7 | Output + ops | ⬜ blocked (needs 1–6) | — |

**Critical path remaining:** 6 → 7.

### What is NOT yet proven (be honest about this)
- Sustained volume / behind **residential proxies** (no creds wired; don't hammer Meta). `ProxyPool`/`backoff`/`SessionPool.invalidate` are built for it.
- The PublicWWW funnel search **live** (only its parser + the `classify_funnel_intent` logic are unit-tested).
- The live e2e was **scaled down** (1 EU sweep, 2 advertisers) — not a production crawl.

---

## 2. The two load-bearing discoveries (don't re-litigate — see `docs/research/`)

1. **Extraction is browserless.** The Meta 403 is the `rd_challenge` (Jan 2026): regex `/__rd_verify_…` from the stub → empty POST → `rd_challenge` cookie (24h) → retry. Plain `httpx` also fails on TLS fingerprint → **`curl_cffi` (`impersonate="chrome"`)**. Playwright is a *standby warmer only*. Proven on-machine. Details: `docs/research/2026-06-04-server-side-extraction.md`.
2. **Discovery is spend-driven.** Meta Ad Library (Firehose B) is the engine; funnel-fingerprint (C) covers web-native targets. **Firehose A (app-store new-apps seed) was DROPPED** — iTunes is **enrichment only** (release-date/ratings/genre via Lookup). Details: `docs/research/2026-06-04-discovery-sources.md`.

---

## 3. How to run & verify

```bash
cd ~/Projects/emerging-app-finder
.venv/bin/pip install -r requirements.txt          # Python 3.13 venv already exists

# fast: unit suite (DB/integration tests skip clean without env)
.venv/bin/pytest -q                                # 80 passed, 19 skipped, 6 deselected

# with infra (Plan 3/4 DB tests, and Plan 5 going forward):
docker compose -f db/docker-compose.yml up -d      # Postgres :5433 + MinIO :9010
export AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf
export S3_ENDPOINT=http://localhost:9010 MINIO_ACCESS_KEY=minioadmin MINIO_SECRET_KEY=minioadmin S3_BUCKET=agf-test
.venv/bin/pytest -q                                # 115 passed, 6 deselected
.venv/bin/pytest -m integration -q                 # live: hits real Facebook + MinIO
docker compose -f db/docker-compose.yml down       # tidy up

# the live end-to-end smoke (real Meta → Postgres): /tmp/e2e.py was a scratch script;
# re-create from docs/plans/04-discovery-plan.md "Live end-to-end" section if needed.
```

---

## 4. Frozen contracts a new plan MUST build against (master-plan §3)

- **§3.4 `Extractor`** (Plan 2, `src/agf/extract/facade.py`): `async search(...)`, `async ad_details(ad_archive_id)`, `async resolve_deeplink(url, ua)`. Plan 5 calls `extractor.ad_details` for `eu_total_reach` + `active`.
- **§3.3 repo** (Plan 3, `src/agf/db/repo.py`): `conn`-first functions, **never commit** (caller owns the transaction). Includes `record_ad_metric`, `upsert_app_advertiser_metric`.
- **§3.6 matview `frontend.emerging_scaling_apps`** — **Plan 5 OWNS IT ALONE.** Create `db/schema/051_emerging_matview.sql` with a **UNIQUE index on `advertiser_id`** (required for `REFRESH … CONCURRENTLY`). Frozen columns are listed in §3.6. One row per advertiser; web funnels with `app_id` NULL still appear.
- **Schema numbering:** P3 = 001–049, **P5 = 050–099**, P7 = 100+. `migrate.py` globs lexically.
- **`pg_conn` fixture lives only in root `conftest.py`.** `requirements.txt` is append-only with a `# --- plan-N ---` section per phase.

---

## 5. Hard-won rules (carry forward — each cost us something)

- **RUN THE ASSEMBLED THING LIVE before claiming it works.** Fake/mock unit tests + frozen-contract adherence *all passed* while discovery silently dropped `ads` rows; only the live e2e caught it.
- Spec/plan code is **illustrative, not guaranteed correct** — expect to fix issues (we hit: LSD fixture regex, `Real`→`Float`, `now()`→`clock_timestamp()`, mutex same-pid).
- **Postgres `now()` is transaction-frozen** → use `clock_timestamp()` for event timestamps.
- **`REFRESH MATERIALIZED VIEW … CONCURRENTLY`** needs its own autocommit connection **and** the UNIQUE index (Plan 5 creates it).
- Commit per logical unit: `<type>(plan-N): <desc>`, end messages with the `Co-Authored-By: Claude …` trailer. Branch off `main`, build, verify, merge `--no-ff`.
- Use the superpowers skills: `writing-plans` → `executing-plans`/`subagent-driven-development` → `verification-before-completion`; run `/self-eval` before declaring done.

---

## 6. Plan 5 (✅ done — as-built) + what Plan 6 picks up

**Plan 5 — Velocity tracker + GemScore — COMPLETE.** Detailed/as-built doc: `docs/plans/05-scoring-plan.md`. Built TDD on `feat/plan-5-scoring`, merged `--no-ff`. **Owns:** `src/agf/tracker/snapshots.py`, `src/agf/scoring/` (`cohorts`, `features`, `gemscore`, `vetoes`, `run_scoring`), `db/schema/050_scoring.sql` (`adtech.advertiser_scores`), `db/schema/051_emerging_matview.sql` (`frontend.emerging_scaling_apps`, UNIQUE index on `advertiser_id`), `tests/tracker/`, `tests/scoring/`. Pinned `# --- plan-5 ---`: numpy/scipy/statsmodels.

Exposed: `snapshot_ad_metrics(conn, extractor, date)` (async; active ads → `ad_metrics` via `extractor.ad_details`) · `snapshot_advertiser_week(conn, week)` (SQL rollup; reach summed in its own CTE to avoid creative fan-out) · `cohort_key`/`robust_z`(MAD, matches scipy)/`pctile` · `log_growth_wow`/`acceleration`/`ewma`/`build_features(conn, advertiser_id, as_of)→FeatureVector` · `scaling_velocity`/`under_radar`/`funnel_intent`/`gem_score(sv,ur,fi,floor=.05)` (`100·clamp01(SV)^.5·UR^.35·FI^.15`; None axis→floor, present-low axis still gates) · `apply_vetoes` (persistence, longevity-floor, disguised-incumbent via `parent_company_id`, relaunch, STL seasonal-spike) · `score_all(conn, as_of)` + `refresh_emerging_matview(engine)` (autocommit, `REFRESH … CONCURRENTLY`) + `run_scoring(engine, as_of)`. Cohort = category × country, normalized within cohort.

**Two as-built notes for downstream:** (1) SV normalizes on **velocity** signals — `ewma_velocity` smooths weekly *deltas* (not the level) and SV-longevity uses bounded `longevity_share` — so incumbents don't win SV on size (the live e2e forced this). (2) **GemScore weights/thresholds are starter priors** (SV/UR/FI .5/.35/.15, floor .05, ALERT≥70, RELAUNCH_RATING_MAX 100k, STL period 4) — **back-test against known past breakouts before trusting the score in production.**

**Plan 6 — Enrichment (NEXT, deps 2,3,5 ✅).** Scope in master-plan §6 Plan 6 (P6-T1…T10): app-store metadata, funnel screenshots + pricing, deduped creative bytes → S3, company/WHOIS/tech, for **top-N flagged gems only** (`enrich_gem(conn, advertiser_id, top_n=5)`). Reuses Plan 2 `Extractor.resolve_deeplink` + Playwright; backfills `upsert_advertiser_enrichment` (feeds Plan 5's disguised-incumbent veto). Gems to enrich = `frontend.emerging_scaling_apps WHERE alert` (or `gem_score ≥ 70`).

---

## 7. Map of the repo

- `docs/plans/00-master-plan.md` — **source of truth** (contracts, dashboard, phases, decisions).
- `docs/plans/01..04-*.md` — per-plan detailed/as-built docs.
- `docs/research/2026-06-04-{server-side-extraction,discovery-sources}.md` — the two research deep-dives.
- `docs/design-spec.md`, `README.md` (grug explainer) — original design narrative.
- `src/agf/{fbjson,bootstrap,request,parse,classify,enrich,scale,score,livesearch,run_poc}.py` — Plan 1.
- `src/agf/extract/` — Plan 2. `src/agf/db/` + `src/agf/storage/` + `src/agf/workers/` — Plan 3. `src/agf/discovery/` — Plan 4.
- `db/schema/*.sql` (001–004 so far), `db/migrate.py`, `db/docker-compose.yml`.
- Project memory: `~/.claude/projects/-Users-edaslakavicius-Projects-emerging-app-finder/memory/`.

---

## 8. Continuation prompt (paste into a fresh session)

> Continue building the **Emerging App Finder** at `~/Projects/emerging-app-finder`. First read, in order: `docs/HANDOFF.md`, `docs/plans/00-master-plan.md` (source of truth — §3 frozen contracts, §5 dashboard, §6 phases, §8 decisions), and both files in `docs/research/`; also check the project memory dir. **State:** Plans 1–4 are done, merged to `main`, and verified (115 tests + a live end-to-end against real Facebook + Postgres). **Build Plan 5 — Velocity tracker + GemScore** next (deps all met). Do it the way the others were built: (1) write a detailed bite-sized TDD plan at `docs/plans/05-scoring-plan.md` from master-plan §6 Plan 5 + the §3 contracts; (2) build TDD on a feature branch; (3) **verify for real** — pure stats unit-tested, and snapshots/matview against a live Postgres (`docker compose -f db/docker-compose.yml up -d`; `export AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf`); (4) commit per logical unit (`<type>(plan-5): …`, end messages with the `Co-Authored-By: Claude …` trailer), merge `--no-ff` to `main`, update the §5 dashboard. **Plan 5 is the SOLE owner of `frontend.emerging_scaling_apps`** — create `db/schema/051_emerging_matview.sql` with a UNIQUE index on `advertiser_id` (so `REFRESH … CONCURRENTLY` works; schema range P5 = 050–099). Snapshots call `extractor.ad_details` (§3.4). `GemScore = 100·clamp01(SV)^.5·clamp01(UR)^.35·clamp01(FI)^.15` — multiplicative AND-gate with a per-axis floor, cohort-normalized within (category × country). Pin `numpy`/`statsmodels`/`scipy` under a `# --- plan-5 ---` section. **Carry the hard rules:** extraction is curl_cffi+rd_challenge (Playwright standby only); discovery is spend-driven (Firehose A dropped, iTunes = enrichment only); the repo is conn-first and never commits; Postgres `now()` is txn-frozen so use `clock_timestamp()`; `REFRESH … CONCURRENTLY` needs its own autocommit connection; and **run the assembled thing live before claiming it works** (fakes + contract-adherence hid a real bug last time). Use the superpowers skills and run `/self-eval` before declaring done.
