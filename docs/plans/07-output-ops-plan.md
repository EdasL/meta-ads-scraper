# Plan 7 — Output + ops — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the read-only product surface — a FastAPI over `frontend.emerging_scaling_apps` (ranked shortlist + per-gem dossier + scaling series + an **advertiser_id-keyed** human review queue, never auto-call) — plus the full ops layer (production docker-compose, cron snapshots/refresh/prune/backup, proxy gateway, health/metrics + rotation alerts, RUNBOOK).

**Architecture:** Three layers. (1) **Pure-ish data layer** (`api/queries.py` parameterized SELECTs over the frozen §3.6 matview + dossier joins; `monitoring/metrics.py` over `logging.crawl_log`). (2) **FastAPI app** (`api/app.py` `create_app(engine)`, `api/deps.py` a **read-only** `get_conn` (`SET TRANSACTION READ ONLY`) + a write conn for the single review POST, Pydantic `api/schemas.py`, routers shortlist/gems/review/health). (3) **Ops** (`ops/docker-compose.yml` prod stack, `ops/cron/*` jobs, `ops/proxy/gateway.conf`, `ops/monitoring/*`, `ops/RUNBOOK.md`). Adds one schema file `db/schema/100_frontend_review.sql` (P7 range = 100+).

**Tech Stack:** Python 3.13 · FastAPI + Starlette `TestClient` · SQLAlchemy Core + psycopg · PostgreSQL 17 (matview + review table) · pytest. Read-only by default; the only write path is `POST /api/review/{advertiser_id}/decision`.

---

## Frozen inputs this plan builds against (do not change)

- **§3.6 matview `frontend.emerging_scaling_apps`** (created by Plan 5; columns FROZEN): `advertiser_id, cohort, page_name, app_id, app_name, gem_score, scaling_velocity, under_radar, funnel_intent, active_ad_count, max_longevity_days, alert, refreshed_at`. UNIQUE index on `advertiser_id`. **Plan 7 reads it; never recreates it.**
- **§3.7 review keyed on `advertiser_id`** (`app_id` NULLABLE — web funnels stay in). `review_decisions(advertiser_id FK, verdict CHECK in('accept','reject','hold'), reviewer, note, decided_at, PK(advertiser_id, decided_at))` — **this plan creates it** as `db/schema/100_frontend_review.sql`.
- **§3.5 tables for the dossier:** `public.advertisers(id, page_name, page_category, domain_id, parent_company_id, ig_username, ig_followers)`, `public.domains(id, domain)`, `public.apps(store, store_id, name, developer, genre, release_date, rating, rating_count, is_emerging)`, `public.ads(id, advertiser_id, app_id, link_url, cta_type)`, `public.creatives(ad_id, type, s3_key, body_text, title)`, `public.funnels(ad_id, landing_url, final_url_after_redirect, screenshot_s3_key)`, `adtech.app_advertiser_metrics(advertiser_id, week_start, active_ad_count, distinct_creative_count, total_eu_reach)`.
- **`logging.crawl_log(id, entity_type, entity_id, crawled_at, crawl_result)`** (Plan 3) + index — health/metrics read it.
- **`agf.db.engine`**: `get_engine(url=None)` (psycopg driver, `AGF_DATABASE_URL`→`AGF_TEST_DB_URL`), `begin(engine)`.
- **§3.10 ops:** `ops/docker-compose.yml` is the **production stack** (pins PG/MinIO image tags once, env-only creds); single proxy env contract `AGF_PROXIES`. Schema range **P7 = 100+**; `requirements.txt` append-only `# --- plan-7 ---`.
- **Plan 5 `run_scoring` / `refresh_emerging_matview(engine)`** — used by the live API tests to populate the matview, and by the cron `refresh` job.
- **Plan 6 `make_enrich_handler(deps_factory)`** (§3.9) — referenced by the worker/cron wiring + RUNBOOK (the `deps_factory` does a fresh `extractor.search` for current creative URLs since fbcdn URLs expire).

## Hard rules carried from prior plans (each cost us something)

- **conn-first, never commit** inside `queries.py` write helpers — the FastAPI dependency owns the transaction (the write conn commits after the handler returns; the RO conn rolls back).
- **Postgres `now()` is txn-frozen** → `clock_timestamp()` for `decided_at` / crawl timestamps.
- **`REFRESH MATERIALIZED VIEW … CONCURRENTLY`** needs its own autocommit conn + Plan 5's UNIQUE index (the cron refresh job + the test fixture both honor this; reuse `refresh_emerging_matview`).
- **Never auto-call** — the review queue surfaces gems for a human; the only mutation is recording a human verdict.
- **Read-only default-deny:** GET routes use a `SET TRANSACTION READ ONLY` connection; test that a write through it fails.
- **Don't recreate the matview** (§3.6, Plan 5 owns it) and don't edit other phases' files. Schema numbering: this plan's only schema file is `100_frontend_review.sql`.
- **RUN THE ASSEMBLED THING LIVE before claiming done** — seed the matview (Plan 5 `run_scoring`) and hit the real routes via `TestClient`; `docker compose -f ops/docker-compose.yml config -q` must exit 0.

## Files created by this plan

| File | Responsibility |
|---|---|
| `src/agf/api/__init__.py` | package marker |
| `src/agf/api/queries.py` | `shortlist`, `gem_dossier`, `gem_scaling`, `review_queue`, `record_decision` |
| `src/agf/api/schemas.py` | Pydantic: `ShortlistRow`, `GemDossier`, `ScalingPoint`, `ReviewQueueRow`, `ReviewDecisionIn`, `AppInfo`, `FunnelInfo`, `CreativeInfo` |
| `src/agf/api/deps.py` | `get_conn` (read-only), `get_write_conn` |
| `src/agf/api/app.py` | `create_app(engine)` + `/healthz` |
| `src/agf/api/routers/__init__.py` | package marker |
| `src/agf/api/routers/shortlist.py` | `GET /api/shortlist` |
| `src/agf/api/routers/gems.py` | `GET /api/gems/{advertiser_id}` + `/scaling` |
| `src/agf/api/routers/review.py` | `GET /api/review/queue` + `POST /api/review/{advertiser_id}/decision` |
| `src/agf/api/routers/health.py` | `GET /api/health/metrics` |
| `src/agf/monitoring/__init__.py` + `metrics.py` | `compute_metrics`, `Alert`, `rotation_alerts` |
| `db/schema/100_frontend_review.sql` | `frontend.review_decisions` |
| `ops/docker-compose.yml` | prod stack (7 services, env-only creds) |
| `ops/cron/{snapshot,refresh_matview,prune,backup}.sh` + `crontab` | scheduled jobs |
| `ops/proxy/gateway.conf` | proxy gateway template (`AGF_PROXIES`) |
| `ops/RUNBOOK.md` | doc_id rotation / proxy refresh / restore runbook |
| `tests/api/*`, `tests/ops/*` | tests |
| `requirements.txt` | append `# --- plan-7 ---` (fastapi, uvicorn) |

## Task ↔ master-plan mapping (built leaves → API → ops → live)

| Plan task | master-plan | what |
|---|---|---|
| Task 0 | — | scaffold packages + pin fastapi/uvicorn |
| Task 1 | P7-T5(SQL) | `100_frontend_review.sql` + matview-shape sanity |
| Task 2 | P7-T1 | `queries.py` (shortlist/dossier/scaling/review_queue/record_decision) |
| Task 3 | P7-T2 | `schemas.py` Pydantic |
| Task 4 | P7-T3 | `app.py` `create_app` + `deps.py` (RO) + `/healthz` |
| Task 5 | P7-T4 | shortlist + gems routers |
| Task 6 | P7-T5 | review router (queue + decision) |
| Task 7 | P7-T9 | `monitoring/metrics.py` + alerts |
| Task 8 | P7-T6 | health/metrics router |
| Task 9 | P7-T7 | cron scripts + crontab |
| Task 10 | P7-T8 | `ops/docker-compose.yml` |
| Task 11 | P7-T10 | proxy gateway + RUNBOOK + ops smoke |
| Task 12 | — | full suite + live API smoke + docs + merge |

---

## Task 0: Scaffold + pin fastapi/uvicorn

**Files:** create `src/agf/api/__init__.py`, `src/agf/api/routers/__init__.py`, `src/agf/monitoring/__init__.py`, `tests/api/__init__.py`, `tests/ops/__init__.py` (all empty); modify `requirements.txt`.

- [ ] **Step 1:** empty package markers.
- [ ] **Step 2:** append to `requirements.txt`:
```
# --- plan-7 ---
fastapi==0.115.6
uvicorn==0.34.0    # ASGI server for production (ops/docker-compose api service)
```
- [ ] **Step 3:** `.venv/bin/pip install -r requirements.txt && .venv/bin/python -c "import fastapi, uvicorn; print('ok')"` → `ok`
- [ ] **Step 4:** commit `chore(plan-7): scaffold api/monitoring packages, pin fastapi/uvicorn`

---

## Task 1 (P7-T5 SQL): `100_frontend_review.sql`

**Files:** create `db/schema/100_frontend_review.sql`; test `tests/api/test_schema_review.py`.

- [ ] **Step 1: failing test**
```python
# tests/api/test_schema_review.py
from sqlalchemy import text


def test_review_decisions_table_and_check(pg_conn):
    cols = set(pg_conn.execute(text(
        "SELECT * FROM frontend.review_decisions LIMIT 0")).keys())
    assert {"advertiser_id", "verdict", "reviewer", "note", "decided_at"} <= cols
    # CHECK constraint rejects bad verdicts
    import pytest
    from sqlalchemy.exc import IntegrityError
    from agf.parse import AdRecord
    from agf.db import repo
    adv = repo.upsert_advertiser(pg_conn, AdRecord("RVW", "RVW", "G", True, 1, None, 1,
                                                   "x", "x", "https://x", "b"))
    with pytest.raises(IntegrityError):
        pg_conn.execute(text("INSERT INTO frontend.review_decisions "
                             "(advertiser_id, verdict, reviewer) VALUES (:a,'maybe','me')"),
                        {"a": adv})
```
- [ ] **Step 2:** run → FAIL (relation does not exist).
- [ ] **Step 3:** write the schema:
```sql
-- db/schema/100_frontend_review.sql
-- Plan 7 — human review decisions (schema `frontend`). Keyed on advertiser_id
-- (master-plan §3.7); app_id may be NULL (web funnels). PK(advertiser_id, decided_at)
-- keeps a full decision history; the review queue excludes any advertiser with a row.
CREATE TABLE IF NOT EXISTS frontend.review_decisions (
    advertiser_id INTEGER NOT NULL REFERENCES public.advertisers(id),
    verdict       TEXT NOT NULL CHECK (verdict IN ('accept', 'reject', 'hold')),
    reviewer      TEXT NOT NULL,
    note          TEXT,
    decided_at    TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (advertiser_id, decided_at)
);
```
- [ ] **Step 4:** run (DB env) → PASS (the session `apply_migrations` picks up `100_*` lexically).
- [ ] **Step 5:** commit `feat(plan-7): frontend.review_decisions schema (advertiser_id-keyed, verdict CHECK)`

---

## Task 2 (P7-T1): `queries.py`

**Files:** create `src/agf/api/queries.py`; create `tests/api/conftest.py` (seeds a committed matview); test `tests/api/test_queries.py`.

- [ ] **Step 1: seed fixture + failing test**
```python
# tests/api/conftest.py
"""Committed-matview fixtures for API tests. The matview only reflects COMMITTED
rows after REFRESH, so these use the migrated engine directly (not the rolled-back
pg_conn) and clean up their sentinels — mirrors Plan 5's e2e."""
import pytest
from datetime import date, timedelta
from sqlalchemy import text
from agf.db.engine import begin
from agf.scoring.run_scoring import refresh_emerging_matview

SENT = ("P7A", "P7B", "P7C")          # cohort-shared sentinel page_ids
WEEK0 = date(2026, 3, 2)

# advertiser_id-by-pageid map is built at seed time
def _seed(engine):
    ids = {}
    # (page_id, cohort, page_name, gem_score, sv, ur, fi, active, longd, alert)
    specs = [
        ("P7A", "Health & Fitness×US", "Alpha", 88.0, 0.9, 0.9, 0.8, 6, 30, True),
        ("P7B", "Health & Fitness×US", "Bravo", 74.0, 0.8, 0.7, 0.7, 4, 22, True),
        ("P7C", "Education×US",         "Charlie", 40.0, 0.5, 0.3, 0.4, 2, 18, False),
    ]
    with begin(engine) as conn:
        for pid, cohort, name, gs, sv, ur, fi, active, longd, alert in specs:
            adv = conn.execute(text(
                "INSERT INTO public.advertisers (page_id, page_name, page_category) "
                "VALUES (:p,:n,'Software') ON CONFLICT (page_id) DO UPDATE "
                "SET page_name=EXCLUDED.page_name RETURNING id"),
                {"p": pid, "n": name}).scalar_one()
            ids[pid] = adv
            conn.execute(text("""
                INSERT INTO adtech.advertiser_scores
                  (advertiser_id, cohort, gem_score, scaling_velocity, under_radar,
                   funnel_intent, active_ad_count, max_longevity_days, alert, vetoed, veto_reason)
                VALUES (:a,:c,:gs,:sv,:ur,:fi,:aac,:mld,:al,FALSE,NULL)
                ON CONFLICT (advertiser_id) DO UPDATE SET gem_score=EXCLUDED.gem_score,
                  alert=EXCLUDED.alert, cohort=EXCLUDED.cohort"""),
                {"a": adv, "c": cohort, "gs": gs, "sv": sv, "ur": ur, "fi": fi,
                 "aac": active, "mld": longd, "al": alert})
            # a weekly series for the scaling endpoint
            for i, n in enumerate((1, 2, 4, active)):
                conn.execute(text("""INSERT INTO adtech.app_advertiser_metrics
                    (advertiser_id, week_start, active_ad_count, distinct_creative_count, total_eu_reach)
                    VALUES (:a,:w,:n,:n,:r) ON CONFLICT (advertiser_id, week_start) DO NOTHING"""),
                    {"a": adv, "w": WEEK0 + timedelta(weeks=i), "n": n, "r": n * 100})
    refresh_emerging_matview(engine)
    return ids


def _cleanup(engine):
    with begin(engine) as conn:
        ids = conn.execute(text("SELECT id FROM public.advertisers WHERE page_id = ANY(:p)"),
                           {"p": list(SENT)}).scalars().all()
        if ids:
            for t in ("frontend.review_decisions", "adtech.advertiser_scores",
                      "adtech.app_advertiser_metrics"):
                conn.execute(text(f"DELETE FROM {t} WHERE advertiser_id = ANY(:i)"), {"i": ids})
            conn.execute(text("DELETE FROM public.advertisers WHERE id = ANY(:i)"), {"i": ids})
    refresh_emerging_matview(engine)


@pytest.fixture
def seeded(_migrated_engine):
    _cleanup(_migrated_engine)
    ids = _seed(_migrated_engine)
    try:
        yield _migrated_engine, ids
    finally:
        _cleanup(_migrated_engine)
```
```python
# tests/api/test_queries.py
from agf.api import queries


def test_shortlist_ranked_and_filtered(seeded):
    engine, ids = seeded
    with engine.connect() as conn:
        rows = queries.shortlist(conn, min_score=70)
        got = [r["advertiser_id"] for r in rows if r["advertiser_id"] in ids.values()]
        assert got == [ids["P7A"], ids["P7B"]]            # ranked by gem_score DESC, ≥70
        hf = queries.shortlist(conn, cohort="Health & Fitness×US", min_score=0)
        assert ids["P7C"] not in [r["advertiser_id"] for r in hf]   # cohort filter excludes Edu
        assert queries.shortlist(conn, min_score=70, limit=1)[0]["advertiser_id"] == ids["P7A"]


def test_dossier_and_scaling_and_missing(seeded):
    engine, ids = seeded
    with engine.connect() as conn:
        d = queries.gem_dossier(conn, ids["P7A"])
        assert d["gem"]["page_name"] == "Alpha" and d["page_category"] == "Software"
        assert queries.gem_dossier(conn, -999) is None
        series = queries.gem_scaling(conn, ids["P7A"])
        assert [p["active_ad_count"] for p in series] == [1, 2, 4, 6]


def test_review_queue_and_record_decision(seeded):
    engine, ids = seeded
    from agf.db.engine import begin
    with engine.connect() as conn:
        q = [r["advertiser_id"] for r in queries.review_queue(conn, min_score=70)]
        assert ids["P7A"] in q and ids["P7B"] in q and ids["P7C"] not in q
    with begin(engine) as conn:
        queries.record_decision(conn, ids["P7A"], "accept", "founder", "looks great")
    with engine.connect() as conn:
        q2 = [r["advertiser_id"] for r in queries.review_queue(conn, min_score=70)]
        assert ids["P7A"] not in q2 and ids["P7B"] in q2   # decided -> out of queue
```
- [ ] **Step 2:** run → FAIL (no module `agf.api.queries`).
- [ ] **Step 3:** implement:
```python
# src/agf/api/queries.py
"""Read queries over the frozen §3.6 matview + dossier joins, and the single review
write. conn-first; the read conn is SET READ ONLY by the dependency, the write conn
commits in the dependency."""
from sqlalchemy import text

_MV_COLS = ("advertiser_id, cohort, page_name, app_id, app_name, gem_score, "
            "scaling_velocity, under_radar, funnel_intent, active_ad_count, "
            "max_longevity_days, alert, refreshed_at")


def shortlist(conn, *, cohort=None, min_score=0.0, limit=50, offset=0):
    return conn.execute(text(f"""
        SELECT {_MV_COLS} FROM frontend.emerging_scaling_apps
        WHERE gem_score >= :min_score AND (:cohort IS NULL OR cohort = :cohort)
        ORDER BY gem_score DESC, advertiser_id
        LIMIT :limit OFFSET :offset"""),
        {"cohort": cohort, "min_score": min_score, "limit": limit, "offset": offset}
    ).mappings().all()


def gem_dossier(conn, advertiser_id):
    gem = conn.execute(text(f"SELECT {_MV_COLS} FROM frontend.emerging_scaling_apps "
                            "WHERE advertiser_id = :a"), {"a": advertiser_id}).mappings().first()
    if gem is None:
        return None
    domain = conn.execute(text(
        "SELECT d.domain FROM public.advertisers a "
        "LEFT JOIN public.domains d ON d.id = a.domain_id WHERE a.id = :a"),
        {"a": advertiser_id}).scalar()
    apps = conn.execute(text(
        "SELECT DISTINCT ap.store, ap.store_id, ap.name, ap.developer, ap.genre, "
        "       ap.release_date, ap.rating, ap.rating_count, ap.is_emerging "
        "FROM public.apps ap JOIN public.ads a ON a.app_id = ap.id "
        "WHERE a.advertiser_id = :a"), {"a": advertiser_id}).mappings().all()
    funnels = conn.execute(text(
        "SELECT f.landing_url, f.final_url_after_redirect, f.screenshot_s3_key "
        "FROM public.funnels f JOIN public.ads a ON a.id = f.ad_id "
        "WHERE a.advertiser_id = :a"), {"a": advertiser_id}).mappings().all()
    creatives = conn.execute(text(
        "SELECT c.type, c.s3_key, c.body_text, c.title "
        "FROM public.creatives c JOIN public.ads a ON a.id = c.ad_id "
        "WHERE a.advertiser_id = :a"), {"a": advertiser_id}).mappings().all()
    page_category = conn.execute(text(
        "SELECT page_category FROM public.advertisers WHERE id = :a"),
        {"a": advertiser_id}).scalar()
    return {"gem": dict(gem), "domain": domain, "page_category": page_category,
            "apps": [dict(r) for r in apps], "funnels": [dict(r) for r in funnels],
            "creatives": [dict(r) for r in creatives]}


def gem_scaling(conn, advertiser_id):
    return conn.execute(text(
        "SELECT week_start, active_ad_count, distinct_creative_count, total_eu_reach "
        "FROM adtech.app_advertiser_metrics WHERE advertiser_id = :a ORDER BY week_start"),
        {"a": advertiser_id}).mappings().all()


def review_queue(conn, *, min_score=70.0, limit=50, offset=0):
    return conn.execute(text("""
        SELECT advertiser_id, cohort, page_name, app_name, gem_score
        FROM frontend.emerging_scaling_apps e
        WHERE e.alert = TRUE AND e.gem_score >= :min_score
          AND NOT EXISTS (SELECT 1 FROM frontend.review_decisions r
                          WHERE r.advertiser_id = e.advertiser_id)
        ORDER BY e.gem_score DESC, e.advertiser_id LIMIT :limit OFFSET :offset"""),
        {"min_score": min_score, "limit": limit, "offset": offset}).mappings().all()


def record_decision(conn, advertiser_id, verdict, reviewer, note=None):
    conn.execute(text("""
        INSERT INTO frontend.review_decisions (advertiser_id, verdict, reviewer, note, decided_at)
        VALUES (:a, :v, :r, :n, clock_timestamp())"""),
        {"a": advertiser_id, "v": verdict, "r": reviewer, "n": note})
```
- [ ] **Step 4:** run (DB env) → PASS.
- [ ] **Step 5:** commit `feat(plan-7): api/queries.py — shortlist/dossier/scaling/review_queue/record_decision over the matview`

---

## Task 3 (P7-T2): `schemas.py` Pydantic

**Files:** create `src/agf/api/schemas.py`; test `tests/api/test_schemas.py`.

- [ ] **Step 1: failing test**
```python
# tests/api/test_schemas.py
import pytest
from pydantic import ValidationError
from agf.api.schemas import ShortlistRow, GemDossier, ReviewDecisionIn, ReviewQueueRow


def test_shortlist_row_validates_and_requires_score():
    row = ShortlistRow(advertiser_id=1, cohort="C", page_name="P", app_id=None,
                       app_name=None, gem_score=80.0, scaling_velocity=0.9,
                       under_radar=0.8, funnel_intent=0.7, active_ad_count=5,
                       max_longevity_days=30, alert=True)
    assert row.gem_score == 80.0 and row.app_id is None
    with pytest.raises(ValidationError):
        ShortlistRow(advertiser_id=1, cohort="C", page_name="P")   # missing gem_score


def test_review_decision_in_rejects_bad_verdict():
    assert ReviewDecisionIn(verdict="accept", reviewer="me").verdict == "accept"
    with pytest.raises(ValidationError):
        ReviewDecisionIn(verdict="maybe", reviewer="me")


def test_queue_row_and_dossier_construct():
    ReviewQueueRow(advertiser_id=1, cohort="C", page_name="P", app_name=None, gem_score=75.0)
    GemDossier(gem=ShortlistRow(advertiser_id=1, cohort="C", page_name="P", app_id=None,
        app_name=None, gem_score=80.0, scaling_velocity=0.9, under_radar=0.8,
        funnel_intent=0.7, active_ad_count=5, max_longevity_days=30, alert=True),
        domain="x.com", page_category="Software", apps=[], funnels=[], creatives=[])
```
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement:
```python
# src/agf/api/schemas.py
"""Pydantic response/request models (master-plan §6 P7-T2). Mirror the frozen §3.6
matview columns; the review input enforces the §3.7 verdict enum."""
from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel


class ShortlistRow(BaseModel):
    advertiser_id: int
    cohort: str
    page_name: str | None
    app_id: int | None = None
    app_name: str | None = None
    gem_score: float
    scaling_velocity: float | None = None
    under_radar: float | None = None
    funnel_intent: float | None = None
    active_ad_count: int | None = None
    max_longevity_days: int | None = None
    alert: bool = False
    refreshed_at: datetime | None = None


class AppInfo(BaseModel):
    store: str
    store_id: str
    name: str | None = None
    developer: str | None = None
    genre: str | None = None
    release_date: date | None = None
    rating: float | None = None
    rating_count: int | None = None
    is_emerging: bool | None = None


class FunnelInfo(BaseModel):
    landing_url: str | None = None
    final_url_after_redirect: str | None = None
    screenshot_s3_key: str | None = None


class CreativeInfo(BaseModel):
    type: str | None = None
    s3_key: str | None = None
    body_text: str | None = None
    title: str | None = None


class GemDossier(BaseModel):
    gem: ShortlistRow
    domain: str | None = None
    page_category: str | None = None
    apps: list[AppInfo] = []
    funnels: list[FunnelInfo] = []
    creatives: list[CreativeInfo] = []


class ScalingPoint(BaseModel):
    week_start: date
    active_ad_count: int | None = None
    distinct_creative_count: int | None = None
    total_eu_reach: int | None = None


class ReviewQueueRow(BaseModel):
    advertiser_id: int
    cohort: str
    page_name: str | None
    app_name: str | None = None
    gem_score: float


class ReviewDecisionIn(BaseModel):
    verdict: Literal["accept", "reject", "hold"]
    reviewer: str
    note: str | None = None
```
- [ ] **Step 4:** run → PASS.
- [ ] **Step 5:** commit `feat(plan-7): api/schemas.py — Pydantic shortlist/dossier/queue/decision models`

---

## Task 4 (P7-T3): `app.py` `create_app` + `deps.py` (read-only) + `/healthz`

**Files:** create `src/agf/api/deps.py`, `src/agf/api/app.py`; test `tests/api/test_app.py`.

- [ ] **Step 1: failing test**
```python
# tests/api/test_app.py
from fastapi.testclient import TestClient
from agf.api.app import create_app


def test_healthz_and_openapi_is_read_only(_migrated_engine):
    client = TestClient(create_app(_migrated_engine))
    assert client.get("/healthz").json()["status"] == "ok"
    paths = client.get("/openapi.json").json()["paths"]
    methods = {(p, m.upper()) for p, ms in paths.items() for m in ms}
    writes = {(p, m) for (p, m) in methods if m in {"POST", "PUT", "PATCH", "DELETE"}}
    # the ONLY write is the human review decision
    assert writes == {("/api/review/{advertiser_id}/decision", "POST")}, writes


def test_read_only_conn_denies_writes(_migrated_engine):
    import pytest
    from sqlalchemy import text
    from sqlalchemy.exc import InternalError
    from agf.api.deps import get_conn
    app = create_app(_migrated_engine)
    gen = get_conn.__wrapped__(_RequestStub(app)) if hasattr(get_conn, "__wrapped__") else None
    # iterate the generator dependency manually
    from agf.api import deps
    it = deps.get_conn(_RequestStub(app))
    conn = next(it)
    try:
        with pytest.raises((InternalError, Exception)):
            conn.execute(text("CREATE TEMP TABLE _x(i int)"))   # write -> read-only txn rejects
    finally:
        try: next(it)
        except StopIteration: pass


class _RequestStub:
    def __init__(self, app):
        self.app = app
```
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement:
```python
# src/agf/api/deps.py
"""FastAPI connection dependencies (master-plan §6 P7-T3). GET routes get a
SET-TRANSACTION-READ-ONLY connection (default-deny writes); the single review POST
gets a writable connection that commits on success."""
from sqlalchemy import text


def get_conn(request):
    """Read-only connection: the transaction is marked READ ONLY, so any write
    (INSERT/UPDATE/DDL) is rejected by Postgres."""
    engine = request.app.state.engine
    conn = engine.connect()
    try:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        yield conn
    finally:
        conn.rollback()
        conn.close()


def get_write_conn(request):
    """Writable connection for the human review decision; commits on success."""
    engine = request.app.state.engine
    conn = engine.connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
```
```python
# src/agf/api/app.py
"""Read-only FastAPI over the product matview (master-plan §6 Plan 7). create_app
takes an Engine so tests inject the migrated test engine; production wires the
AGF_DATABASE_URL engine. Never auto-calls — the only mutation is a human verdict."""
from fastapi import FastAPI
from agf.api.routers import shortlist, gems, review, health


def create_app(engine) -> FastAPI:
    app = FastAPI(title="Field Desk — find idea that already work", version="1.0")
    app.state.engine = engine
    app.include_router(shortlist.router)
    app.include_router(gems.router)
    app.include_router(review.router)
    app.include_router(health.router)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app
```
> The routers are created in Tasks 5/6/8; to make Task 4 import-clean, create **stub routers** now (empty `APIRouter`) and fill them in their tasks:
```python
# src/agf/api/routers/shortlist.py (stub now, filled in Task 5)
from fastapi import APIRouter
router = APIRouter(prefix="/api", tags=["shortlist"])
```
```python
# src/agf/api/routers/gems.py (stub now, filled in Task 5)
from fastapi import APIRouter
router = APIRouter(prefix="/api", tags=["gems"])
```
```python
# src/agf/api/routers/review.py (stub now, filled in Task 6)
from fastapi import APIRouter
router = APIRouter(prefix="/api", tags=["review"])
```
```python
# src/agf/api/routers/health.py (stub now, filled in Task 8)
from fastapi import APIRouter
router = APIRouter(prefix="/api", tags=["health"])
```
- [ ] **Step 4:** run → PASS (OpenAPI has only `/healthz` GET + no writes yet; **update the assertion to the final write-set once Task 6 adds the POST** — at Task 4 the write-set is empty, so assert `writes == set()`, then tighten to the review POST after Task 6). *(Build note: write the Task-4 test with `assert writes == set()`, and in Task 6 change it to the review POST.)*
- [ ] **Step 5:** commit `feat(plan-7): create_app(engine) + read-only get_conn (SET TRANSACTION READ ONLY) + /healthz`

---

## Task 5 (P7-T4): shortlist + gems routers

**Files:** fill `src/agf/api/routers/shortlist.py`, `gems.py`; test `tests/api/test_routes_shortlist.py`, `test_routes_gems.py`.

- [ ] **Step 1: failing tests**
```python
# tests/api/test_routes_shortlist.py
from fastapi.testclient import TestClient
from agf.api.app import create_app


def test_shortlist_ranked_filtered(seeded):
    engine, ids = seeded
    c = TestClient(create_app(engine))
    r = c.get("/api/shortlist", params={"min_score": 70})
    got = [row["advertiser_id"] for row in r.json() if row["advertiser_id"] in ids.values()]
    assert got == [ids["P7A"], ids["P7B"]]
    r2 = c.get("/api/shortlist", params={"cohort": "Education×US", "min_score": 0})
    assert ids["P7A"] not in [row["advertiser_id"] for row in r2.json()]
```
```python
# tests/api/test_routes_gems.py
from fastapi.testclient import TestClient
from agf.api.app import create_app


def test_dossier_scaling_and_404(seeded):
    engine, ids = seeded
    c = TestClient(create_app(engine))
    d = c.get(f"/api/gems/{ids['P7A']}").json()
    assert d["gem"]["page_name"] == "Alpha" and d["page_category"] == "Software"
    s = c.get(f"/api/gems/{ids['P7A']}/scaling").json()
    assert [p["active_ad_count"] for p in s] == [1, 2, 4, 6]
    assert c.get("/api/gems/-999").status_code == 404
```
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement:
```python
# src/agf/api/routers/shortlist.py
from fastapi import APIRouter, Depends
from agf.api import queries
from agf.api.deps import get_conn
from agf.api.schemas import ShortlistRow

router = APIRouter(prefix="/api", tags=["shortlist"])


@router.get("/shortlist", response_model=list[ShortlistRow])
def get_shortlist(cohort: str | None = None, min_score: float = 0.0,
                  limit: int = 50, offset: int = 0, conn=Depends(get_conn)):
    return [ShortlistRow(**dict(r)) for r in
            queries.shortlist(conn, cohort=cohort, min_score=min_score,
                              limit=limit, offset=offset)]
```
```python
# src/agf/api/routers/gems.py
from fastapi import APIRouter, Depends, HTTPException
from agf.api import queries
from agf.api.deps import get_conn
from agf.api.schemas import GemDossier, ScalingPoint, ShortlistRow

router = APIRouter(prefix="/api", tags=["gems"])


@router.get("/gems/{advertiser_id}", response_model=GemDossier)
def get_gem(advertiser_id: int, conn=Depends(get_conn)):
    d = queries.gem_dossier(conn, advertiser_id)
    if d is None:
        raise HTTPException(status_code=404, detail="gem not found. loosen filter.")
    d["gem"] = ShortlistRow(**d["gem"])
    return GemDossier(**d)


@router.get("/gems/{advertiser_id}/scaling", response_model=list[ScalingPoint])
def get_gem_scaling(advertiser_id: int, conn=Depends(get_conn)):
    return [ScalingPoint(**dict(p)) for p in queries.gem_scaling(conn, advertiser_id)]
```
- [ ] **Step 4:** run → PASS.
- [ ] **Step 5:** commit `feat(plan-7): shortlist + gems routers (ranked shortlist, dossier, scaling, 404)`

---

## Task 6 (P7-T5): review router (queue + decision)

**Files:** fill `src/agf/api/routers/review.py`; update `tests/api/test_app.py` write-set assertion to the review POST; test `tests/api/test_routes_review.py`.

- [ ] **Step 1: failing test**
```python
# tests/api/test_routes_review.py
from fastapi.testclient import TestClient
from agf.api.app import create_app


def test_queue_then_decision_removes_from_queue(seeded):
    engine, ids = seeded
    c = TestClient(create_app(engine))
    q = [r["advertiser_id"] for r in c.get("/api/review/queue").json()]
    assert ids["P7A"] in q and ids["P7C"] not in q
    ok = c.post(f"/api/review/{ids['P7A']}/decision",
                json={"verdict": "accept", "reviewer": "founder", "note": "go"})
    assert ok.status_code == 200
    q2 = [r["advertiser_id"] for r in c.get("/api/review/queue").json()]
    assert ids["P7A"] not in q2 and ids["P7B"] in q2
    bad = c.post(f"/api/review/{ids['P7B']}/decision",
                 json={"verdict": "nope", "reviewer": "x"})
    assert bad.status_code == 422            # verdict enum enforced by Pydantic
```
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement + tighten the Task-4 OpenAPI write-set assertion to `{("/api/review/{advertiser_id}/decision", "POST")}`:
```python
# src/agf/api/routers/review.py
from fastapi import APIRouter, Depends
from agf.api import queries
from agf.api.deps import get_conn, get_write_conn
from agf.api.schemas import ReviewQueueRow, ReviewDecisionIn

router = APIRouter(prefix="/api", tags=["review"])


@router.get("/review/queue", response_model=list[ReviewQueueRow])
def get_queue(min_score: float = 70.0, limit: int = 50, offset: int = 0,
              conn=Depends(get_conn)):
    return [ReviewQueueRow(**dict(r)) for r in
            queries.review_queue(conn, min_score=min_score, limit=limit, offset=offset)]


@router.post("/review/{advertiser_id}/decision")
def post_decision(advertiser_id: int, body: ReviewDecisionIn, conn=Depends(get_write_conn)):
    queries.record_decision(conn, advertiser_id, body.verdict, body.reviewer, body.note)
    return {"status": "ok", "advertiser_id": advertiser_id, "verdict": body.verdict}
```
- [ ] **Step 4:** run `tests/api/test_routes_review.py` + `tests/api/test_app.py` → PASS.
- [ ] **Step 5:** commit `feat(plan-7): review router — queue (undecided≥70) + advertiser_id-keyed decision POST`

---

## Task 7 (P7-T9): `monitoring/metrics.py` + alerts

**Files:** create `src/agf/monitoring/metrics.py`; test `tests/ops/test_metrics.py`.

- [ ] **Step 1: failing test**
```python
# tests/ops/test_metrics.py
from datetime import datetime, timezone, timedelta
from sqlalchemy import text
from agf.monitoring.metrics import compute_metrics, rotation_alerts, Alert


def _log(conn, etype, result, when):
    conn.execute(text("INSERT INTO logging.crawl_log (entity_type, entity_id, crawled_at, crawl_result) "
                      "VALUES (:t, 1, :w, :r)"), {"t": etype, "w": when, "r": result})


def test_metrics_and_rotation_alerts(pg_conn):
    now = datetime.now(timezone.utc)
    for _ in range(5):
        _log(pg_conn, "advertiser", "ok", now)
    for _ in range(4):
        _log(pg_conn, "advertiser", "error: HTTP 400 doc_id stale", now)
    _log(pg_conn, "advertiser", "error: 429 rate_limit", now)

    m = compute_metrics(pg_conn, window=timedelta(hours=1))
    assert m["total"] >= 10 and m["errors"] >= 5

    alerts = rotation_alerts(pg_conn, window=timedelta(hours=1),
                             doc_id_threshold=3, rate_limit_threshold=1)
    kinds = {a.kind for a in alerts}
    assert "doc_id_rotation" in kinds and "rate_limit" in kinds
    assert all(isinstance(a, Alert) for a in alerts)


def test_no_alerts_when_clean(pg_conn):
    now = datetime.now(timezone.utc)
    pg_conn.execute(text("INSERT INTO logging.crawl_log (entity_type, entity_id, crawl_result) "
                         "VALUES ('advertiser', 1, 'ok')"))
    assert rotation_alerts(pg_conn, doc_id_threshold=3, rate_limit_threshold=5) == []
```
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement:
```python
# src/agf/monitoring/metrics.py
"""Ops metrics + rotation alerts over logging.crawl_log (master-plan §6 P7-T9).
doc_id rotation and rate-limit are the two maintenance-treadmill signals (§2)."""
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from sqlalchemy import text


@dataclass(frozen=True)
class Alert:
    kind: str          # 'doc_id_rotation' | 'rate_limit' | 'stale_snapshot'
    message: str
    severity: str = "warning"


def _since(window):
    return datetime.now(timezone.utc) - window


def compute_metrics(conn, *, window=timedelta(hours=24)) -> dict:
    since = _since(window)
    rows = conn.execute(text(
        "SELECT crawl_result, count(*) FROM logging.crawl_log "
        "WHERE crawled_at >= :s GROUP BY crawl_result"), {"s": since}).all()
    total = sum(c for _, c in rows)
    errors = sum(c for r, c in rows if r and r.lower().startswith("error"))
    return {"window_hours": window.total_seconds() / 3600, "total": total,
            "errors": errors, "ok": total - errors}


def _count(conn, since, needles):
    rows = conn.execute(text(
        "SELECT crawl_result FROM logging.crawl_log WHERE crawled_at >= :s "
        "AND crawl_result IS NOT NULL"), {"s": since}).scalars().all()
    return sum(1 for r in rows if any(n in r.lower() for n in needles))


def rotation_alerts(conn, *, window=timedelta(hours=1), doc_id_threshold=5,
                    rate_limit_threshold=10) -> list:
    since = _since(window)
    alerts = []
    docid = _count(conn, since, ("doc_id", "http 400", "400 ", "1357004", "1357001"))
    if docid >= doc_id_threshold:
        alerts.append(Alert("doc_id_rotation",
                            f"{docid} doc_id/400 failures in {window} — re-heal token/doc_id",
                            "critical"))
    rl = _count(conn, since, ("429", "rate_limit", "1675004"))
    if rl >= rate_limit_threshold:
        alerts.append(Alert("rate_limit",
                            f"{rl} rate-limit hits in {window} — back off / rotate proxies"))
    return alerts
```
- [ ] **Step 4:** run (DB env) → PASS.
- [ ] **Step 5:** commit `feat(plan-7): monitoring.metrics — crawl_log metrics + doc_id-rotation/rate-limit alerts`

---

## Task 8 (P7-T6): health/metrics router

**Files:** fill `src/agf/api/routers/health.py`; test `tests/api/test_routes_health.py`.

- [ ] **Step 1: failing test**
```python
# tests/api/test_routes_health.py
from fastapi.testclient import TestClient
from sqlalchemy import text
from agf.api.app import create_app
from agf.db.engine import begin


def test_health_metrics_endpoint(_migrated_engine):
    with begin(_migrated_engine) as conn:
        conn.execute(text("INSERT INTO logging.crawl_log (entity_type, entity_id, crawl_result) "
                          "VALUES ('healthcheck_sentinel', 1, 'ok')"))
    try:
        c = TestClient(create_app(_migrated_engine))
        body = c.get("/api/health/metrics").json()
        assert "total" in body and "errors" in body and "alerts" in body
        assert isinstance(body["alerts"], list)
    finally:
        with begin(_migrated_engine) as conn:
            conn.execute(text("DELETE FROM logging.crawl_log WHERE entity_type='healthcheck_sentinel'"))
```
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement:
```python
# src/agf/api/routers/health.py
from dataclasses import asdict
from fastapi import APIRouter, Depends
from agf.api.deps import get_conn
from agf.monitoring.metrics import compute_metrics, rotation_alerts

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health/metrics")
def health_metrics(conn=Depends(get_conn)):
    m = compute_metrics(conn)
    m["alerts"] = [asdict(a) for a in rotation_alerts(conn)]
    m["degraded"] = bool(m["alerts"])
    return m
```
- [ ] **Step 4:** run → PASS.
- [ ] **Step 5:** commit `feat(plan-7): health/metrics router over crawl_log (alerts + degraded flag)`

---

## Task 9 (P7-T7): cron scripts + crontab

**Files:** create `ops/cron/{snapshot.sh,refresh_matview.sh,prune.sh,backup.sh}` + `ops/cron/crontab`; test `tests/ops/test_cron_scripts.py`.

- [ ] **Step 1: failing test**
```python
# tests/ops/test_cron_scripts.py
import os, re, subprocess
from pathlib import Path

CRON = Path(__file__).resolve().parents[2] / "ops" / "cron"
SCRIPTS = ["snapshot.sh", "refresh_matview.sh", "prune.sh", "backup.sh"]


def test_scripts_exist_executable_and_valid_bash():
    for s in SCRIPTS:
        p = CRON / s
        assert p.exists(), p
        assert os.access(p, os.X_OK), f"{s} not executable"
        assert subprocess.run(["bash", "-n", str(p)]).returncode == 0, f"{s} syntax"
        body = p.read_text()
        assert body.startswith("#!/usr/bin/env bash") or body.startswith("#!/bin/bash")
        assert "set -euo pipefail" in body


def test_refresh_uses_concurrently():
    assert "REFRESH MATERIALIZED VIEW CONCURRENTLY" in (CRON / "refresh_matview.sh").read_text()


def test_prune_bounds_retention():
    assert re.search(r"365|interval", (CRON / "prune.sh").read_text(), re.IGNORECASE)


def test_crontab_lines_valid():
    lines = [l for l in (CRON / "crontab").read_text().splitlines()
             if l.strip() and not l.strip().startswith("#")]
    assert lines and all(len(l.split()) >= 6 for l in lines)   # 5 time fields + command
```
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement (all `chmod +x`). Each begins `#!/usr/bin/env bash` + `set -euo pipefail`, uses env vars, absolute-ish via `$PSQL`/`$PG_DUMP` (default to plain names). Example refresh:
```bash
#!/usr/bin/env bash
# ops/cron/refresh_matview.sh — REFRESH the product matview CONCURRENTLY (needs the
# Plan 5 UNIQUE index; cannot run in a txn block, so psql -c is one autocommit stmt).
set -euo pipefail
: "${AGF_DATABASE_URL:?set AGF_DATABASE_URL}"
PSQL="${PSQL:-psql}"
"$PSQL" "$AGF_DATABASE_URL" -v ON_ERROR_STOP=1 \
  -c "REFRESH MATERIALIZED VIEW CONCURRENTLY frontend.emerging_scaling_apps;"
```
```bash
#!/usr/bin/env bash
# ops/cron/snapshot.sh — daily ad-metrics + weekly rollup + re-score (Plan 5).
set -euo pipefail
: "${AGF_DATABASE_URL:?}"
cd "$(dirname "$0")/../.."
PY="${PY:-.venv/bin/python}"
PYTHONPATH=src "$PY" -m agf.scoring.run_scoring   # score_all + refresh (see module __main__)
```
```bash
#!/usr/bin/env bash
# ops/cron/prune.sh — drop crawl_log + ad_metrics older than the 365-day retention.
set -euo pipefail
: "${AGF_DATABASE_URL:?}"
PSQL="${PSQL:-psql}"
"$PSQL" "$AGF_DATABASE_URL" -v ON_ERROR_STOP=1 \
  -c "DELETE FROM logging.crawl_log WHERE crawled_at < now() - interval '365 days';" \
  -c "DELETE FROM adtech.ad_metrics WHERE snapshot_date < (now() - interval '365 days')::date;"
```
```bash
#!/usr/bin/env bash
# ops/cron/backup.sh — nightly pg_dump to $BACKUP_DIR (timestamped, gz).
set -euo pipefail
: "${AGF_DATABASE_URL:?}"
PG_DUMP="${PG_DUMP:-pg_dump}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/agf}"
mkdir -p "$BACKUP_DIR"
TS="$(date +%Y%m%d-%H%M%S)"
"$PG_DUMP" "$AGF_DATABASE_URL" | gzip > "$BACKUP_DIR/agf-$TS.sql.gz"
```
```cron
# ops/cron/crontab — times in UTC. All jobs read AGF_DATABASE_URL from the env file.
15 2 * * *  /app/ops/cron/snapshot.sh        >> /var/log/agf/snapshot.log 2>&1
0  * * * *  /app/ops/cron/refresh_matview.sh >> /var/log/agf/refresh.log 2>&1
30 3 * * *  /app/ops/cron/prune.sh           >> /var/log/agf/prune.log 2>&1
0  4 * * *  /app/ops/cron/backup.sh          >> /var/log/agf/backup.log 2>&1
```
> Add `__main__` to `agf/scoring/run_scoring.py`? It already exposes `run_scoring(engine, as_of)`; if no `__main__`, snapshot.sh can `python -c "from agf.db.engine import get_engine; from agf.scoring.run_scoring import run_scoring; from datetime import date; run_scoring(get_engine(), date.today())"`. Keep snapshot.sh using that inline form to avoid editing the Plan-5 file.
- [ ] **Step 4:** run `pytest tests/ops/test_cron_scripts.py -q && bash -n ops/cron/*.sh` → PASS.
- [ ] **Step 5:** commit `feat(plan-7): cron scripts (snapshot/refresh-CONCURRENTLY/prune-365d/backup) + crontab`

---

## Task 10 (P7-T8): `ops/docker-compose.yml` (prod stack)

**Files:** create `ops/docker-compose.yml`, `ops/.env.example`; test `tests/ops/test_compose.py`.

- [ ] **Step 1: failing test**
```python
# tests/ops/test_compose.py
import shutil, subprocess
from pathlib import Path
import pytest

COMPOSE = Path(__file__).resolve().parents[2] / "ops" / "docker-compose.yml"


def test_compose_has_seven_services_and_validates():
    text = COMPOSE.read_text()
    for svc in ("postgres", "minio", "minio-init", "api", "worker", "cron", "proxy"):
        assert f"  {svc}:" in text, svc
    assert "${POSTGRES_PASSWORD}" in text and "password:" not in text.lower().replace("postgres_password", "")
    if shutil.which("docker"):
        r = subprocess.run(["docker", "compose", "-f", str(COMPOSE), "config", "-q"])
        assert r.returncode == 0
    else:
        pytest.skip("docker not available — static checks only")


def test_compose_depends_on_edges():
    text = COMPOSE.read_text()
    assert "depends_on" in text and "pg_data" in text and "minio_data" in text
```
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** write `ops/docker-compose.yml` — 7 services (`postgres` pinned `postgres:17`, `minio` pinned `minio/minio:RELEASE...`, `minio-init` creates the bucket, `api` runs uvicorn, `worker` runs discovery+enrich+snapshot loop, `cron` runs the crontab, `proxy` is the residential-proxy gateway), named volumes `pg_data`/`minio_data`, all creds from `${...}` env (with `ops/.env.example` listing the vars), `depends_on` edges (api/worker/cron→postgres; worker→proxy; minio-init→minio). This is the **single source** for PG/MinIO tags (§3.10).
- [ ] **Step 4:** run `pytest tests/ops/test_compose.py -q` + `docker compose -f ops/docker-compose.yml config -q` → PASS.
- [ ] **Step 5:** commit `feat(plan-7): ops/docker-compose.yml prod stack (7 services, env-only creds, pinned PG/MinIO)`

---

## Task 11 (P7-T10): proxy gateway + RUNBOOK + ops smoke

**Files:** create `ops/proxy/gateway.conf`, `ops/RUNBOOK.md`; test `tests/ops/test_runbook.py`.

- [ ] **Step 1: failing test**
```python
# tests/ops/test_runbook.py
from pathlib import Path
OPS = Path(__file__).resolve().parents[2] / "ops"


def test_runbook_covers_recovery_paths():
    rb = (OPS / "RUNBOOK.md").read_text()
    for needle in ("doc_id", "rd_challenge", "token_heal", "AGF_PROXIES",
                   "refresh_matview.sh", "backup.sh", "restore",
                   "27201872659451053", "make_enrich_handler"):
        assert needle in rb, needle


def test_proxy_gateway_references_proxy_env():
    assert "AGF_PROXIES" in (OPS / "proxy" / "gateway.conf").read_text()
```
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** write `ops/proxy/gateway.conf` (a documented residential-proxy gateway template referencing `AGF_PROXIES`, one upstream per line) and `ops/RUNBOOK.md` covering: doc_id rotation symptom (`/metrics` doc_id_rotation alert) → re-heal via Plan 2 `token_heal`/`rd_challenge` with the fallback doc_ids (`27201872659451053`, `25464068859919530`, `33730956806489539`) → verify a US search returns ads; proxy refresh (`AGF_PROXIES`); restore from `backup.sh`; the cron jobs (`snapshot.sh`/`refresh_matview.sh`/`prune.sh`/`backup.sh`); and the Plan 6 enrichment worker wiring (`make_enrich_handler` + a `deps_factory` doing a fresh `extractor.search` for current creative URLs).
- [ ] **Step 4:** run `pytest tests/ops -q` → PASS.
- [ ] **Step 5:** commit `feat(plan-7): proxy gateway template + RUNBOOK (doc_id re-heal, proxy refresh, restore)`

---

## Task 12: Full suite + LIVE API smoke + docs + merge

- [ ] **Step 1:** full suite with infra up:
```bash
docker compose -f db/docker-compose.yml up -d
export AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf
export S3_ENDPOINT=http://localhost:9010 MINIO_ACCESS_KEY=minioadmin MINIO_SECRET_KEY=minioadmin S3_BUCKET=agf-test
.venv/bin/pytest -q
```
Expected: all prior + new api/ops tests green; integration deselected. Record the count.
- [ ] **Step 2: LIVE API smoke** — a throwaway driver: migrate → seed a gem/brand via Plan 5 `score_all` + `refresh_emerging_matview` → `uvicorn`-less `TestClient(create_app(engine))` (or run uvicorn and curl) → GET `/healthz`, `/api/shortlist?min_score=70` (gem present, brand absent), `/api/gems/{id}` dossier, POST a review decision, confirm it leaves the queue; assert read-only conn rejects a write; `docker compose -f ops/docker-compose.yml config -q` exits 0. Clean up sentinels.
- [ ] **Step 3:** `/self-eval`; update `docs/plans/00-master-plan.md` §5 (Plan 7 ✅ done — **project complete**), tick P7-T1…T10 in §6; update `docs/HANDOFF.md` (Plan 7 ✅, critical path complete, test count). Commit `docs(plan-7): mark Plan 7 done — v1 critical path complete`.
- [ ] **Step 4:** merge:
```bash
docker compose -f db/docker-compose.yml down
git checkout main
git merge --no-ff feat/plan-7-output-ops -m "merge(plan-7): read-only API + ops (compose/cron/proxy/runbook/metrics)"
```

---

## Self-review (run against master-plan §6 Plan 7 + §3)

- **P7-T1 queries** → Task 2 (ranked shortlist, filters, dossier, scaling, review_queue, record_decision). ✓
- **P7-T2 schemas** → Task 3 (Pydantic, cohort+app_name, verdict enum rejects bad). ✓
- **P7-T3 create_app + RO conn** → Task 4 (`SET TRANSACTION READ ONLY`, /healthz, OpenAPI GET-only + one POST). ✓
- **P7-T4 shortlist+gems routers** → Task 5 (advertiser_id-keyed, 404 unknown). ✓
- **P7-T5 review schema + router** → Tasks 1 + 6 (advertiser_id PK, queue=undecided≥70, POST inserts + removes). ✓
- **P7-T6 health/metrics** → Tasks 7 + 8 (crawl_log rates, degraded flag). ✓
- **P7-T7 cron** → Task 9 (snapshot/refresh-CONCURRENTLY/prune-365d/backup; executable; `bash -n`; valid cron). ✓
- **P7-T8 compose** → Task 10 (7 services, env-only creds, `config -q`). ✓
- **P7-T9 metrics + alerts** → Task 7 (doc_id-rotation + rate-limit thresholds fire). ✓
- **P7-T10 RUNBOOK** → Task 11 (doc_id re-heal w/ fallback doc_id, proxy refresh, restore, names each script/module). ✓
- **advertiser_id-keyed, app_id NULLABLE** → §3.7 schema + queries. ✓
- **Read-only default-deny; never auto-call** → `get_conn` RO + only the review POST writes. ✓
- **Don't recreate the matview; P7 schema = 100+** → reads §3.6 matview; only `100_frontend_review.sql`. ✓
- **fastapi/uvicorn pinned `# --- plan-7 ---`** → Task 0. ✓
- **RUN IT LIVE** → Task 12 (seed via Plan 5 → real routes via TestClient + `compose config -q`). ✓
