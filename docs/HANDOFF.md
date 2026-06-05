# Emerging App Finder — Build Handoff

**Last updated:** 2026-06-05 · **Branch:** `main` (all work merged) · **Status:** ✅ **v1 critical path COMPLETE (Plans 1–7 done)** + **operational completion in progress** (see §6) · **Tests:** **231 passing**, 7 deselected (live integration smokes) — +19 since v1 (API live-wiring, GemScore backtest, worker dry-run) · live end-to-end verified (Plan 5 live Postgres e2e · Plan 6 live assembled smoke · Plan 7 live API smoke · **2026-06-05: live web↔API e2e — uvicorn+Postgres, every endpoint + CORS + review-write; worker dry-run sweep; GemScore backtest 1.0/1.0 @70**)

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
| 6 | Enrichment | ✅ done | 34 tests (pure + live Postgres/MinIO) + **live assembled smoke** (real iTunes / chromium→MinIO / WHOIS / creative bytes→MinIO / Node gplay sidecar) + 22-finding adversarial review hardening |
| 7 | Output + ops | ✅ done | 25 tests (queries/routes/metrics/cron/compose/runbook) + **live API smoke 7/7** (matview→FastAPI shortlist/dossier/review/health, RO write-denial, `docker compose config -q`) |

**Critical path remaining:** none — **v1 is complete (Plans 1–7 merged to `main`)**. Remaining work is *operational*, not critical-path (see §6 "What's next").

### What is NOT yet proven (be honest about this)
- Sustained volume / behind **residential proxies** (no creds wired; don't hammer Meta). `ProxyPool`/`backoff`/`SessionPool.invalidate` are built for it.
- The PublicWWW funnel search **live** (only its parser + the `classify_funnel_intent` logic are unit-tested).
- The live e2e was **scaled down** (1 EU sweep, 2 advertisers) — not a production crawl.
- **Plan 6 funnel capture** proven live only on a 1-step page (`example.com`); multi-step real funnels (CMP dismissal + CTA-click navigation) are unit-tested with a fake page, not yet driven on a real paywall funnel. Real **WHOIS** + **iTunes Lookup** + **Node gplay sidecar** + **creative bytes→MinIO** are all live-proven. `make_enrich_handler`'s production `deps_factory` (fresh `extractor.search` for current creative URLs) is the wiring Plan 7's worker provides; the §3.9 adapter shape itself is verified.

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
.venv/bin/pytest -q                                # 121 passed, 32 skipped, 6 deselected

# with infra (Plan 3/4/5 DB tests, and Plan 6 going forward):
docker compose -f db/docker-compose.yml up -d      # Postgres :5433 + MinIO :9010
export AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf
export S3_ENDPOINT=http://localhost:9010 MINIO_ACCESS_KEY=minioadmin MINIO_SECRET_KEY=minioadmin S3_BUCKET=agf-test
.venv/bin/pytest -q                                # 153 passed, 6 deselected
.venv/bin/pytest -m integration -q                 # live: hits real Facebook + MinIO
docker compose -f db/docker-compose.yml down       # tidy up

# Plan 5 scoring runs end-to-end against the docker Postgres:
#   from agf.scoring.run_scoring import run_scoring   # score_all (committed txn) + REFRESH ... CONCURRENTLY
#   run_scoring(get_engine(AGF_TEST_DB_URL), date.today())
# Live Meta→Postgres discovery smoke: re-create from docs/plans/04-discovery-plan.md "Live end-to-end".
```

---

## 4. Frozen contracts a new plan MUST build against (master-plan §3)

- **§3.4 `Extractor`** (Plan 2, `src/agf/extract/facade.py`): `async search(...)`, `async ad_details(ad_archive_id)`, `async resolve_deeplink(url, ua)`. **Plan 6 reuses `Extractor.resolve_deeplink` + the Plan 2 Playwright warmer — no second resolver.**
- **§3.3 repo** (Plan 3, `src/agf/db/repo.py`): `conn`-first functions, **never commit** (caller owns the transaction). **Plan 6 writes via `upsert_app`, `upsert_creative` (dedup-aware), `upsert_funnel`, `upsert_advertiser_enrichment`** (the last backfills `domain_id`/`parent_company_id`/`page_category`/`ig_*` — which Plan 5's disguised-incumbent veto reads, NULL-tolerant).
- **§3.8 S3 client** (Plan 3, `src/agf/storage/s3.py`): `get_s3()` / `put_bytes(key, data, content_type)`. **§3.6 dedup** (`src/agf/db/dedup.py`): `sha256_bytes`/`phash_image`/`is_duplicate_creative` — Plan 6's `download_creative` reuses both.
- **§3.9 worker handler adapter:** `run_worker` expects `handler(conn, entity_id)`; cross-cutting deps (`extractor`, `s3`, `client`) reach it via a `make_handler(...)` closure. Plan 6's `enrich_gem` ships such an adapter.
- **§3.7 output keys on `advertiser_id`** (`app_id` NULLABLE) — Plan 6 enriches per advertiser; gems to enrich = `frontend.emerging_scaling_apps WHERE alert` (or `gem_score ≥ 70`).
- **§3.6 matview `frontend.emerging_scaling_apps`** — **created by Plan 5** (`db/schema/051_emerging_matview.sql`, UNIQUE index on `advertiser_id`); Plan 6/7 read it, do not recreate it.
- **Schema numbering:** P3 = 001–049, P5 = 050–099 (used: 050, 051), **P7 = 100+**. `migrate.py` globs lexically. (Plan 6 adds **no** schema — it backfills existing tables.)
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

## 6. Plan 7 (✅ done — as-built) + what's next (v1 is complete)

**Plan 7 — Output + ops — COMPLETE.** Detailed/as-built doc: `docs/plans/07-output-ops-plan.md`. Built TDD on `feat/plan-7-output-ops`, merged `--no-ff`. **Owns:** `src/agf/api/` (`app`, `asgi`, `deps`, `schemas`, `queries`, `routers/{shortlist,gems,review,health}`) + `src/agf/monitoring/metrics.py` + `db/schema/100_frontend_review.sql` + `ops/` (`docker-compose.yml`, `Dockerfile`, `.env.example`, `cron/*`, `proxy/gateway.conf`, `RUNBOOK.md`) + `tests/api/`, `tests/ops/`. Pinned `# --- plan-7 ---`: fastapi, uvicorn.

Exposed: `create_app(engine)` (read-only FastAPI; `agf.api.asgi:app` is the uvicorn entrypoint) · `GET /healthz` · `GET /api/shortlist?cohort=&min_score=&limit=&offset=` · `GET /api/gems/{advertiser_id}` (dossier: gem + domain + apps + funnels + creatives) · `GET /api/gems/{advertiser_id}/scaling` · `GET /api/review/queue` + `POST /api/review/{advertiser_id}/decision` (the **only** write) · `GET /api/health/metrics` (crawl rates + doc_id-rotation/rate-limit alerts + `degraded`) · `queries.{shortlist,gem_dossier,gem_scaling,review_queue,record_decision}` · `monitoring.metrics.{compute_metrics,rotation_alerts,Alert}` · `frontend.review_decisions` (§3.7).

**As-built notes:** (1) **Read-only by default** — `deps.get_conn` issues `SET TRANSACTION READ ONLY` (writes rejected); only the review POST uses `get_write_conn`. (2) FastAPI dependency params **must** be annotated `request: Request` or FastAPI treats them as query params (a live route test caught this — the direct-call unit test didn't). (3) the cohort filter binds a NULL param → cast `CAST(:cohort AS text)` to avoid `AmbiguousParameter` (live-caught). (4) API route tests run against the **committed** matview (refresh sees only committed rows), so `tests/api/conftest.py` seeds via the migrated engine + `refresh_emerging_matview` and cleans up — mirrors Plan 5's e2e. (5) `ops/docker-compose.yml` is the prod stack (7 services: postgres/minio/minio-init/api/worker/cron/proxy), all creds from env (`ops/.env.example`), validated by `docker compose config -q`.

**Operational completion (2026-06-05 — the 4 items below; v1 was already shippable):**
- ✅ **Live warm→worker loop wired** — `src/agf/workers/main.py` (`python -m agf.workers.main`) composes build-Extractor → `run_discovery` → optional `run_scoring` → enrich flagged gems via `run_worker(make_enrich_handler(deps_factory))`, looped with env caps (`AGF_EU_MAX_PAGES`/`AGF_ENRICH_LIMIT`/…). **Safe dry-run:** `AGF_DRY_RUN=1` swaps an in-src `agf.extract.fake.FakeExtractor` (zero Meta traffic), runs one sweep, enriches the just-confirmed advertisers. The ops `worker` service now runs this (was a print-and-exit stub) + a heartbeat `--healthcheck`. Verified live: dry-run sweep discovers/confirms 2 pages + enriches both. **Still TODO (needs proxies; don't hammer Meta):** a real sustained crawl with `AGF_PROXIES`.
- ✅ **GemScore priors back-tested** — no real historical snapshots exist, so `tests/scoring/test_backtest_synthetic.py` (14 tests) + `scripts/backtest.py` validate the *shipping* path (pctile, not the dead robust_z) on a labeled synthetic corpus: precision/recall **1.0/1.0 @ threshold 70**, every archetype hits its intended veto, breakout>incumbent across exponent sets. Writeup + honest caveats: `docs/research/2026-06-05-gemscore-backtest.md`. Priors hold on archetypes; re-check the ~8pt breakout-vs-70 margin on real cohort distributions.
- 🔶 **Web `web/` dossier UI ↔ live API** — owned by a separate agent (untracked WIP). **Backend support done + verified live (this session):** `src/agf/api` now sends CORS, the review-queue velocity columns, and dossier `ig_username/ig_followers/parent_company` — exactly what `web/src/lib/adapters.ts` consumes. `scripts/seed_demo.py` seeds a realistic 9-gem corpus (real scoring, full dossiers, a vetoed disguised-incumbent, a web-only funnel) so the live stack renders. Flip the web app with `NEXT_PUBLIC_DATA_MODE=live` + `NEXT_PUBLIC_API_URL`.
- 🔶 **History purge prepared, NOT executed** — `scripts/purge_history.sh` (guarded `git filter-repo` over the 5 accident roots + verify). **Proven safe in a throwaway clone** (accident blobs gone, legit Plan-6 history + the 219-file tree intact, fsck clean) and backed up (`../emerging-app-finder-backup.git` mirror + `../emerging-app-finder-PREFILTER-*.bundle`). NOT run in-place because it rewrites shared history + resets the working tree while other agents are active. **Run it when the repo is quiet** (after committing the `.gitignore` hardening another agent added). Note: SHAs cited in this doc (`05843df`, `5724019`) change after the purge.

---

## 7. Map of the repo

- `docs/plans/00-master-plan.md` — **source of truth** (contracts, dashboard, phases, decisions).
- `docs/plans/01..07-*.md` — per-plan detailed/as-built docs (`07-output-ops-plan.md` is Plan 7, as-built).
- `docs/research/2026-06-04-{server-side-extraction,discovery-sources}.md` — the two research deep-dives.
- `docs/design-spec.md`, `README.md` (grug explainer) — original design narrative.
- `src/agf/{fbjson,bootstrap,request,parse,classify,enrich,scale,score,livesearch,run_poc}.py` — Plan 1.
- `src/agf/extract/` — Plan 2. `src/agf/db/` + `src/agf/storage/` + `src/agf/workers/` — Plan 3. `src/agf/discovery/` — Plan 4.
- `src/agf/tracker/snapshots.py` + `src/agf/scoring/{cohorts,features,gemscore,vetoes,run_scoring}.py` — **Plan 5**.
- `src/agf/enrich_full/{schemas,appstore,funnel_capture,creatives,company,run_enrich}.py` + `sidecar/gp_lookup.js` — **Plan 6**. Live smoke: `tests/enrich_full/live_smoke.py`.
- `src/agf/api/{app,asgi,deps,schemas,queries,routers/*}.py` + `src/agf/monitoring/metrics.py` + `ops/*` — **Plan 7**. Live smoke: `tests/api/live_smoke.py`.
- `db/schema/*.sql` (001–004 Plan 3, 050–051 Plan 5, 100 Plan 7; **Plan 6 adds none**), `db/migrate.py`, `db/docker-compose.yml` (local-test), `ops/docker-compose.yml` (prod).
- Project memory: `~/.claude/projects/-Users-edaslakavicius-Projects-emerging-app-finder/memory/`.

---

## 8. Continuation prompt (paste into a fresh session)

> **The v1 critical path (Plans 1–7) is COMPLETE and merged to `main`** — 212 tests passing + live e2e verified at each layer. First read, in order: `docs/HANDOFF.md`, `docs/plans/00-master-plan.md` (source of truth — §2 guardrails, §3 frozen contracts, §5 dashboard, §8 decisions), the as-built `docs/plans/0{5,6,7}-*.md`, and both `docs/research/` docs; also check the project memory dir. **What works end-to-end (offline/local):** extraction (curl_cffi + rd_challenge, Plan 2), the Postgres spine + S3 (Plan 3), discovery firehoses B/C + 14-day confirm (Plan 4), cohort-normalized GemScore + matview (Plan 5), top-N enrichment (Plan 6: iTunes/gplay/funnel-screenshots/creatives/WHOIS), and the read-only FastAPI + ops stack (Plan 7). Re-run the suite with infra up: `docker compose -f db/docker-compose.yml up -d` + the `AGF_TEST_DB_URL`/`S3_*`/`MINIO_*` env → `.venv/bin/pytest -q` (212 pass) → the two live smokes (`tests/enrich_full/live_smoke.py`, `tests/api/live_smoke.py`). **Remaining work is OPERATIONAL, not new phases** (pick per the founder's priority; see §6 "What's next"): (1) **wire the live warm→worker loop** — Plan 2 warmer + a worker driving `agf.discovery.run_discovery` and `agf.enrich_full.run_enrich.make_enrich_handler` under residential proxies (`AGF_PROXIES`), then run a *small, polite* real Meta crawl to prove sustained volume (not yet done — don't hammer Meta); (2) **back-test the GemScore priors** against known past breakouts before trusting the score; (3) **build the `web/` Next.js dossier UI** on the read-only API (grug copy per `.cursor/rules/grug-copy.mdc`); (4) optionally **`git filter-repo`** to purge the blobs the stray `git add -A` left in history (commit `5724019` already untracked them). **Carry the hard rules:** extraction is curl_cffi+rd_challenge (Playwright standby only); discovery is spend-driven; the repo is conn-first and never commits; Postgres `now()` is txn-frozen → `clock_timestamp()`; `REFRESH … CONCURRENTLY` needs its own autocommit conn + Plan 5's UNIQUE index; **never auto-call** (human-in-the-loop ≥70); **NEVER `git add -A`** (stage explicit paths); and **run the assembled thing live before claiming it works** (fakes hid real bugs in every multi-component phase — Plans 4/5/6/7 each had one only the live run caught). Use the superpowers skills and `/self-eval` before declaring any new work done.
