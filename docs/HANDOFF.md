# Emerging App Finder — Build Handoff

**Last updated:** 2026-06-04 · **Branch:** `main` (all work merged) · **Tests:** 187 passing + live end-to-end verified (incl. Plan 5 live Postgres e2e + Plan 6 live assembled smoke: real iTunes / chromium→MinIO / WHOIS / creative bytes→MinIO / Node gplay sidecar)

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
| 7 | Output + ops | ⬜ **NEXT** (deps 1–6 ✅) | — |

**Critical path remaining:** 7 (last phase).

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

## 6. Plan 6 (✅ done — as-built) + what Plan 7 picks up

**Plan 6 — Enrichment — COMPLETE.** Detailed/as-built doc: `docs/plans/06-enrichment-plan.md`. Built TDD on `feat/plan-6-enrichment`, merged `--no-ff`. **Owns:** `src/agf/enrich_full/` (`schemas`, `appstore`, `funnel_capture`, `creatives`, `company`, `run_enrich`) + `src/agf/enrich_full/sidecar/` (Node `gp_lookup.js` + `package.json`) + `tests/enrich_full/`. Pinned `# --- plan-6 ---`: python-whois. **Adds NO schema** (backfills existing tables). Sidecar `node_modules/` + `package-lock.json` are git-ignored (`cd src/agf/enrich_full/sidecar && npm install`).

Exposed: `lookup_itunes_batch(client, ids, *, chunk=200)` (reuses Plan 1 `parse_lookup`; **`releaseDate`** not currentVersion) · `lookup_google_play(ids, *, runner)` (Node sidecar, ISO release date, malformed-JSON-tolerant) · `resolve_landing(extractor, url)` (delegates to Plan 2 `Extractor.resolve_deeplink` — no second resolver) · `extract_pricing(html)` (JSON-LD Offer + regex fallback) · `autoconsent_cmp(page)` (selector-sweep CMP dismiss) · `capture_funnel(start_url, *, max_steps=3, page=None)` (Playwright, one context, per-step PNG → S3) · `is_duplicate`/`download_creative` (Plan 3 sha256 exact + **phash near-dup** → S3; `snapshot_image_url` left NULL since fbcdn URLs expire) · `domain_age`(WHOIS)/`builtwith_tech`(source fingerprint)/`publisher_from_app`/`enrich_company` (backfills `upsert_advertiser_enrichment` `domain_id`) · `enrich_gem(conn, advertiser_id, *, top_n=5, deps: EnrichDeps)` + `make_enrich_handler(deps_factory)` (§3.9 adapter).

**As-built notes for downstream (Plan 7):** (1) **`capture_funnel` uses Playwright's SYNC API → `enrich_gem` calls it via `asyncio.to_thread`** (sync Playwright can't run in the asyncio loop — the live smoke caught this; do NOT call it directly inside a coroutine). (2) **`enrich_gem` is best-effort per external call** — creative download + app-store lookup are wrapped (a dead fbcdn URL / flaky iTunes won't crash the gem); `capture_funnel`/`resolve_landing` deliberately surface errors (so real bugs aren't masked). (3) **`parent_company_id` is left NULL** (no company graph; the Plan 5 veto tolerates NULL) — Plan 6 reliably backfills `domain_id` + (caller-supplied) `page_category`. (4) **Plan 3 `upsert_creative` has no `content_type`/`file_size` params** → `download_creative` does a follow-up `UPDATE` (a Plan-3 contract gap, defensible workaround). (5) Production worker wiring: `make_enrich_handler(deps_factory)` where `deps_factory(conn, advertiser_id)` does a fresh `extractor.search(view_all_page_id=page_id)` to get current creative URLs (fbcdn expires) and builds `EnrichDeps`; **Plan 7's cron/worker provides this `deps_factory`.**

**Plan 7 — Output + ops (NEXT, deps 1–6 ✅).** Scope in master-plan §6 Plan 7 (P7-T1…T10): read-only FastAPI over `frontend.emerging_scaling_apps` (shortlist + dossier + **advertiser_id-keyed** review queue, never auto-call) + ops (compose, cron snapshots/refresh/prune/backup, proxy gateway, health/metrics + rotation alerts, `RUNBOOK.md`). Schema range **P7 = 100+** (`100_frontend_review.sql`). The prod stack `ops/docker-compose.yml` pins PG/MinIO once (§3.10). Build against the **frozen §3.6 matview columns** + §3.7 `review_decisions(advertiser_id …)`.

---

## 7. Map of the repo

- `docs/plans/00-master-plan.md` — **source of truth** (contracts, dashboard, phases, decisions).
- `docs/plans/01..06-*.md` — per-plan detailed/as-built docs (`06-enrichment-plan.md` is Plan 6, as-built).
- `docs/research/2026-06-04-{server-side-extraction,discovery-sources}.md` — the two research deep-dives.
- `docs/design-spec.md`, `README.md` (grug explainer) — original design narrative.
- `src/agf/{fbjson,bootstrap,request,parse,classify,enrich,scale,score,livesearch,run_poc}.py` — Plan 1.
- `src/agf/extract/` — Plan 2. `src/agf/db/` + `src/agf/storage/` + `src/agf/workers/` — Plan 3. `src/agf/discovery/` — Plan 4.
- `src/agf/tracker/snapshots.py` + `src/agf/scoring/{cohorts,features,gemscore,vetoes,run_scoring}.py` — **Plan 5**.
- `src/agf/enrich_full/{schemas,appstore,funnel_capture,creatives,company,run_enrich}.py` + `sidecar/gp_lookup.js` — **Plan 6**. Live smoke driver: `tests/enrich_full/live_smoke.py`.
- `db/schema/*.sql` (001–004 Plan 3, 050–051 Plan 5; **Plan 6 adds none**), `db/migrate.py`, `db/docker-compose.yml`.
- Project memory: `~/.claude/projects/-Users-edaslakavicius-Projects-emerging-app-finder/memory/`.

---

## 8. Continuation prompt (paste into a fresh session)

> Continue building the **Emerging App Finder** at `~/Projects/emerging-app-finder`. First read, in order: `docs/HANDOFF.md`, `docs/plans/00-master-plan.md` (source of truth — §3 frozen contracts, §5 dashboard, §6 phases, §8 decisions), `docs/plans/06-enrichment-plan.md` (the most recent as-built plan — mirror its style; Plan 5's is also a good reference), and both files in `docs/research/`; also check the project memory dir. **State:** Plans 1–6 are done, merged to `main`, and verified (187 tests + live end-to-end, incl. Plan 6's live assembled smoke: real iTunes / chromium→MinIO / WHOIS / creative bytes→MinIO / Node gplay sidecar). **Build Plan 7 — Output + ops** next, the **last phase** (deps 1–6 ✅). Do it the way the others were built: (1) write a detailed bite-sized TDD plan at `docs/plans/07-output-ops-plan.md` from master-plan §6 Plan 7 (P7-T1…T10) + the §3 contracts; (2) build TDD on a feature branch `feat/plan-7-output-ops`; (3) **verify for real** — pure query/schema units, FastAPI routes via `TestClient`, and the live parts against real deps (`docker compose -f db/docker-compose.yml up -d` + `AGF_TEST_DB_URL`/`S3_*`/`MINIO_*`; seed the matview via Plan 5 `run_scoring` then hit `/api/shortlist?min_score=70`); `docker compose -f ops/docker-compose.yml config -q` must exit 0; (4) commit per logical unit (`<type>(plan-7): …`, end messages with the `Co-Authored-By: Claude …` trailer), merge `--no-ff` to `main`, update the §5 dashboard + this handoff. **Plan 7 owns `src/agf/api/` (`app`, `deps`, `routers/{shortlist,gems,review,health}`, `schemas`, `queries`) + `db/schema/100_frontend_review.sql` + `ops/` (`docker-compose.yml`, `cron/*`, `proxy/gateway.conf`, `monitoring/*`, `RUNBOOK.md`) + `tests/api/`, `tests/ops/`; appends `# --- plan-7 ---` (fastapi, uvicorn); schema range P7 = 100+.** **Build against frozen contracts:** §3.6 matview columns (`advertiser_id, cohort, page_name, app_id, app_name, gem_score, scaling_velocity, under_radar, funnel_intent, active_ad_count, max_longevity_days, alert, refreshed_at`); §3.7 `review_decisions(advertiser_id FK, verdict CHECK in(accept,reject,hold), reviewer, note, decided_at, PK(advertiser_id, decided_at))` keyed on **`advertiser_id`** (`app_id` NULLABLE — web funnels stay in); §3.10 ops compose is the prod stack (pins PG/MinIO tags once). **Read-only API:** `SET TRANSACTION READ ONLY`, only GETs + the one review POST; test default-deny on writes. The RUNBOOK must cover doc_id rotation → re-heal via Plan 2 → verify, proxy refresh, restore — and reference the Plan 6 enrichment worker wiring (`make_enrich_handler` + a `deps_factory` doing a fresh `extractor.search` for current creative URLs, since fbcdn URLs expire). **Carry the hard rules:** the repo is conn-first and never commits; Postgres `now()` is txn-frozen → `clock_timestamp()`; `REFRESH … CONCURRENTLY` needs its own autocommit conn + the UNIQUE index (Plan 5 owns it); **never auto-call** (human-in-the-loop ≥70); and **run the assembled thing live before claiming it works** (fakes hid real bugs in Plans 4/5, and a sync-Playwright-in-asyncio bug in Plan 6 — only the live run caught them). Use the superpowers skills (`writing-plans` → TDD per task → `verification-before-completion`) and run `/self-eval` before declaring done. After Plan 7 merges, the **critical path is complete** — update this handoff to reflect a finished v1.
