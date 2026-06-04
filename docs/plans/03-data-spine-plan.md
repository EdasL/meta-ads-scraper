# Data Spine Implementation Plan (Plan 3 of 7) — as built

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (or subagent-driven-development). Built from master-plan §6 (P3-T1…T12) + the §3 frozen contracts; this doc documents the **as-built, verified** result. Steps use checkbox syntax.

**Goal:** the Postgres-as-queue data spine — schema (forked from adscrawler shape), the `conn`-first upsert/dedup repo (§3.3), the S3/MinIO client (§3.8), and the DB-as-queue worker model (PID-mutex + crawl_log staleness scheduler, §3.9). Does **not** create the product matview (§3.6 — Plan 5 owns it).

**Architecture:** PostgreSQL 17 is both datastore and queue. Pure-ish modules (`dedup`, `mutex`, `scheduler`) are unit-tested without infra; DB/S3 modules are verified against a throwaway Postgres + MinIO via `db/docker-compose.yml`. Every data-access function takes a SQLAlchemy `Connection` first and runs inside the caller's transaction (no commits) — Plans 4/5/6 call these with `conn`. Tests skip cleanly when `AGF_TEST_DB_URL` is unset.

**Tech stack:** Python 3.13 · `psycopg[binary]` 3.3 + `SQLAlchemy` 2.0 Core · `boto3` (MinIO/S3) · `Pillow`+`ImageHash` (perceptual hash) · pytest. No Redis/Celery (DB-as-queue).

**Status:** ✅ **done — 25 DB/worker tests + 1 S3 (MinIO) test green against live infra; 80 passed / 19 skip-clean without infra.**

---

## Verified results

```
# with infra (docker compose -f db/docker-compose.yml up -d):
AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf  pytest tests/db tests/workers -q   -> 25 passed
S3_ENDPOINT=... MINIO_* ... S3_BUCKET=agf-test  pytest -m integration tests/db/test_s3.py   -> 1 passed
AGF_TEST_DB_URL=...  pytest -q                                                               -> 99 passed, 6 deselected
# without infra: pytest -q -> 80 passed, 19 skipped, 6 deselected  (skip-clean, no false failures)
```

---

## Schema → schema-name map (db/schema/, Plan 3 owns 001–049)

| File | Schema | Tables (columns frozen per §3.5) |
|---|---|---|
| `001_schemas.sql` | — | `CREATE SCHEMA adtech, logging, frontend` (public exists; frontend matview is Plan 5) |
| `002_public_core.sql` | `public` | `domains`, `advertisers` (page_id UNIQUE, parent_company_id self-FK, enrichment cols), `apps` (UNIQUE(store,store_id)), `ads` (external_ad_id UNIQUE, app_id NULLABLE), `creatives` (partial UNIQUE on sha256), `funnels` (UNIQUE(ad_id) for upsert) |
| `003_metrics.sql` | `adtech` | `ad_metrics` PK(ad_id,snapshot_date), `app_advertiser_metrics` PK(advertiser_id,week_start) |
| `004_logging_crawl_log.sql` | `logging` | `crawl_log` + INDEX(entity_type,entity_id,crawled_at DESC) |

`schema_migrations` (ledger) is created by `migrate.py` itself, not a numbered file.

## Modules (src/agf/) and the contracts they satisfy

- **`db/engine.py`** — `get_engine(url?)` (forces `postgresql+psycopg://`; URL precedence arg→`AGF_DATABASE_URL`→`AGF_TEST_DB_URL`); `begin(engine?)` transactional ctx (commit/rollback). *(§3.3 `get_engine`/`begin`)*
- **`db/migrate.py`** — `apply_migrations(engine)` globs `db/schema/*.sql` lexically, runs each not in the ledger, records it; idempotent (2nd run → `[]`).
- **`db/models.py`** — SQLAlchemy Core `Table`s mirroring the schema; the drift test asserts every model column exists in the live DB.
- **`db/repo.py`** — **`conn`-first** (§3.3): `upsert_advertiser` (minimal), `upsert_app` (accepts a dict **or** Plan-1 `AppMeta`; ON CONFLICT(store,store_id)), `upsert_ad` (epoch→timestamptz, ON CONFLICT(external_ad_id)), `upsert_creative` (sha256 dedup → returns existing id), `record_ad_metric`, `upsert_app_advertiser_metric`, `upsert_funnel`, `upsert_advertiser_enrichment` (COALESCE so Plan-6 backfill never wipes). All use `COALESCE(EXCLUDED.x, table.x)` to bump-not-clobber.
- **`db/dedup.py`** — `sha256_bytes`, `phash_image` (None for non-image bytes — risk note honored), `is_duplicate_creative(conn, sha256)`.
- **`storage/s3.py`** — `get_s3()` (boto3 → MinIO, path-style, ensures bucket), `put_bytes(key, data, content_type, client?)`. *(§3.8; env S3_ENDPOINT/MINIO_ACCESS_KEY/MINIO_SECRET_KEY/S3_BUCKET)*
- **`workers/mutex.py`** — `PidFileMutex` (context manager): a second acquire while a **live** PID holds it raises `MutexHeld`; stale (dead PID) is reclaimed.
- **`workers/scheduler.py`** — `mark_crawled` (uses `clock_timestamp()`, see Gotchas) + `claim_due(conn, entity_type, candidate_ids, due_before, limit)` → never-crawled-first, then oldest, capped.
- **`workers/runner.py`** — `run_worker(name, entity_type, handler, *, source, candidate_ids, due_before, limit, mutex_dir)` (§3.9). `source` may be an `Engine` (production: one transaction per entity) or a `Connection` (tests: a SAVEPOINT per entity). `handler: Callable[[Connection,int],str]` — cross-cutting deps reach it via an adapter closure (§3.9).

## Task → file → verify map

| Task | Files | Verify (green) |
|---|---|---|
| P3-T1 | `db/docker-compose.yml`, root `conftest.py` (`pg_conn`) | `pytest tests/db/test_connection.py` |
| P3-T2 | `001/002_*.sql` | `pytest tests/db/test_schema_core.py` |
| P3-T3 | `003/004_*.sql` | `pytest tests/db/test_schema_metrics.py` |
| P3-T4 | `db/migrate.py` | `pytest tests/db/test_migrate.py` |
| P3-T5 | `db/engine.py`, `db/models.py` | `pytest tests/db/test_models_match_schema.py` |
| P3-T6 | `db/repo.py` (advertiser/app/ad) | `pytest tests/db/test_repo_upsert.py` |
| P3-T7 | `db/dedup.py` + `upsert_creative` | `pytest tests/db/test_dedup.py` |
| P3-T8 | `db/repo.py` (metrics/funnel/enrichment) | `pytest tests/db/test_repo_metrics.py` |
| P3-T9 | `storage/s3.py` | `pytest -m integration tests/db/test_s3.py` |
| P3-T10 | `workers/mutex.py` | `pytest tests/workers/test_mutex.py` |
| P3-T11 | `workers/scheduler.py` | `pytest tests/workers/test_scheduler.py` |
| P3-T12 | `workers/runner.py` | `pytest tests/workers/test_runner.py` |

## How to run / verify locally

```bash
docker compose -f db/docker-compose.yml up -d          # Postgres :5433 + MinIO :9010
.venv/bin/pip install -r requirements.txt              # installs plan-3 deps
export AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf
export S3_ENDPOINT=http://localhost:9010 MINIO_ACCESS_KEY=minioadmin MINIO_SECRET_KEY=minioadmin S3_BUCKET=agf-test
.venv/bin/pytest tests/db tests/workers -q             # 25 passed
.venv/bin/pytest -m integration tests/db/test_s3.py -q # 1 passed
```

## Gotchas caught during the build (TDD paid off)

1. **`now()` is transaction-frozen in Postgres.** `mark_crawled` originally relied on the `now()` default; inside one transaction every "just-crawled" row got the transaction-start time, so they re-flagged as due. Fixed with **`clock_timestamp()`** (real wall-clock, and the correct semantics for "when crawled").
2. **PID mutex same-process exemption.** The first cut skipped the held-check when the holder PID equalled our own, so a second acquire in one process didn't raise. Fixed to raise whenever the file's PID is alive.
3. **SQLAlchemy has no `Real` type** — used `Float` for the `rating` column (caught at write time).

## Risks / notes
- Repo functions never commit — they run in the caller's transaction (Plan 4/5/6 own the commit via `begin()` or `run_worker`). `REFRESH MATERIALIZED VIEW … CONCURRENTLY` (Plan 5) needs its own autocommit connection — not Plan 3's concern.
- `claim_due` takes explicit `candidate_ids` (the orchestrator supplies them, e.g. all advertiser ids) rather than enumerating an entity table — keeps the scheduler decoupled and testable.
- `db/docker-compose.yml` is **local-test only**; the production stack is Plan 7 `ops/docker-compose.yml` (§3.10).
- Plan 1 `AdRecord` has no `publisher_platforms`; `upsert_ad` reads it via `getattr(..., None)` so it tolerates the Plan-1 shape and a richer Plan-2 record alike.

## Phase-Verify (met)
`pytest tests/db tests/workers -q` green with infra; migrate applies 001–049 idempotently; upsert→queue(crawl_log)→worker round-trip works (`test_runner`); skips clean without `AGF_TEST_DB_URL`.
