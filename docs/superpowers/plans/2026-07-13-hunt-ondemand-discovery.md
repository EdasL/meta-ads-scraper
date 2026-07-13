# Hunt — on-demand AI-assisted gem discovery: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Hunt page where the founder chats with Peon (Gemini) to brainstorm Ad Library terms, presses Start hunt, and a worker-side executor deep-sweeps the terms + named competitors' pages immediately, streaming results into the UI and feeding every find into the normal candidates→triage→enrich→score pipeline (terms minted as `source='hunt'` keywords).

**Architecture:** Go API (chat + Gemini + CRUD over new `frontend.hunt*` tables) → `public.pipeline_jobs` kind `'hunt'` (advertiser_id column carries the round id) → new dedicated `hunt` daemon on the worker VM (1 lane, 2s poll, always free ⇒ instant claim) → subprocess `hunt_one` → executor in `src/agf/discovery/hunt.py` (3 parallel term lanes, adaptive paging cap 40, per-page progress events) → web `/hunt` page polling every 2s.

**Tech Stack:** Python (SQLAlchemy core, asyncio-in-threads like `run_parallel_sweep`), Go (gin + gorm raw SQL + google.golang.org/genai), Next.js App Router (modified Next — read `web/AGENTS.md`), node:test for web lib tests, pytest + testify.

**Spec:** `docs/superpowers/specs/2026-07-13-hunt-ondemand-discovery-design.md`

## Global Constraints

- Test DB for Python + Go repo tests: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it`. Go integration suite gates on `AGF_IT_DB_URL` (separate). A skipped test is NOT a pass.
- Migrations: NO bare `%` anywhere in `db/schema/155_hunts.sql`, including comments (write "pct" / use `mod(a,b)`). Migration number 155 is verified free.
- Copy: plain, professional, grounded in real data. Agent name in UI copy = **Peon**; button label = **Start hunt**; product nouns Field Desk/GemScore/cohort intact. `Peon` appears ONLY in UI strings, never in schema/symbols.
- US-only sweeps (`countries=("US",)`).
- Commit per task with explicit paths (`git add <paths>` — NEVER `git add -A`).
- Web code: this is a modified Next.js — read `node_modules/next/dist/docs/` for any convention you touch; middleware file is `web/src/proxy.ts`.
- Sequential implementers only; each task's tests must pass before commit.

---

### Task 1: Migration 155 + Python hunts repo

**Files:**
- Create: `db/schema/155_hunts.sql`
- Create: `src/agf/db/hunts.py`
- Test: `tests/db/test_hunts.py`

**Interfaces:**
- Consumes: `conftest.py` fixtures `_migrated_engine`/`pg_conn`; `agf.db.pipeline_jobs.enqueue_jobs`.
- Produces (used by Tasks 2–4):
  - `load_round(conn, round_id) -> Row|None` (id, hunt_id, terms list, competitor_apps list, status)
  - `mark_round_running(conn, round_id) -> None`
  - `finish_round(conn, round_id, *, status, error=None) -> None`
  - `upsert_hunt_result(conn, row: dict) -> None`
  - `update_round_progress(conn, round_id, progress: dict) -> None`
  - `apply_triage_to_results(conn, round_id) -> dict`
  - `round_new_candidate_ids(conn, round_id) -> list[int]`

- [ ] **Step 1: Write migration `db/schema/155_hunts.sql`**

```sql
-- Hunt: on-demand AI-assisted discovery (spec 2026-07-13).
-- hunts = chat thread; hunt_messages = transcript; hunt_rounds = one approved
-- run; hunt_results = one row per (round, found page/store listing).

CREATE TABLE IF NOT EXISTS frontend.hunts (
    id          BIGSERIAL PRIMARY KEY,
    user_email  TEXT NOT NULL,
    title       TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_hunts_user ON frontend.hunts (user_email, id DESC);

CREATE TABLE IF NOT EXISTS frontend.hunt_messages (
    id          BIGSERIAL PRIMARY KEY,
    hunt_id     BIGINT NOT NULL REFERENCES frontend.hunts(id) ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content     TEXT NOT NULL DEFAULT '',
    proposal    JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_hunt_messages_hunt ON frontend.hunt_messages (hunt_id, id);

CREATE TABLE IF NOT EXISTS frontend.hunt_rounds (
    id               BIGSERIAL PRIMARY KEY,
    hunt_id          BIGINT NOT NULL REFERENCES frontend.hunts(id) ON DELETE CASCADE,
    message_id       BIGINT REFERENCES frontend.hunt_messages(id),
    terms            JSONB NOT NULL,
    competitor_apps  JSONB NOT NULL DEFAULT '[]',
    status           TEXT NOT NULL DEFAULT 'queued'
                     CHECK (status IN ('queued', 'running', 'done', 'error')),
    progress         JSONB NOT NULL DEFAULT '{}',
    error_text       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at       TIMESTAMPTZ,
    finished_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_hunt_rounds_hunt ON frontend.hunt_rounds (hunt_id, id);

CREATE TABLE IF NOT EXISTS frontend.hunt_results (
    id                 BIGSERIAL PRIMARY KEY,
    round_id           BIGINT NOT NULL REFERENCES frontend.hunt_rounds(id) ON DELETE CASCADE,
    candidate_id       BIGINT,
    page_id            TEXT NOT NULL,
    page_name          TEXT,
    store              TEXT NOT NULL,
    store_id           TEXT NOT NULL,
    link_url           TEXT,
    source_term        TEXT NOT NULL,
    was_new            BOOLEAN NOT NULL,
    triage             TEXT NOT NULL DEFAULT 'pending'
                       CHECK (triage IN ('pending', 'kept', 'vetoed')),
    kept_advertiser_id BIGINT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (round_id, page_id, store, store_id)
);

CREATE INDEX IF NOT EXISTS ix_hunt_results_round ON frontend.hunt_results (round_id, id);
```

- [ ] **Step 2: Write failing tests `tests/db/test_hunts.py`**

```python
"""frontend.hunt* repo tests (migration 155)."""
import json

import pytest
from sqlalchemy import text

from agf.db.hunts import (
    apply_triage_to_results,
    finish_round,
    load_round,
    mark_round_running,
    round_new_candidate_ids,
    update_round_progress,
    upsert_hunt_result,
)


def _seed_hunt(conn) -> int:
    return conn.execute(text(
        "INSERT INTO frontend.hunts (user_email, title) "
        "VALUES ('t@test.local', 'sleep stories') RETURNING id")).scalar_one()


def _seed_round(conn, hunt_id: int, terms=("sleep stories app",)) -> int:
    return conn.execute(text(
        "INSERT INTO frontend.hunt_rounds (hunt_id, terms, competitor_apps) "
        "VALUES (:h, CAST(:t AS jsonb), '[]') RETURNING id"),
        {"h": hunt_id, "t": json.dumps(list(terms))}).scalar_one()


def _result_row(round_id: int, **over) -> dict:
    row = {"round_id": round_id, "candidate_id": None, "page_id": "77001",
           "page_name": "Dreamy", "store": "ios", "store_id": "123456",
           "link_url": "https://apps.apple.com/us/app/id123456",
           "source_term": "sleep stories app", "was_new": True}
    row.update(over)
    return row


def test_load_round_returns_terms_and_competitors(pg_conn):
    hunt_id = _seed_hunt(pg_conn)
    round_id = _seed_round(pg_conn, hunt_id, terms=("a", "b"))
    row = load_round(pg_conn, round_id)
    assert row.id == round_id
    assert row.terms == ["a", "b"]
    assert row.competitor_apps == []
    assert row.status == "queued"
    assert load_round(pg_conn, round_id + 999_000) is None


def test_round_status_transitions(pg_conn):
    round_id = _seed_round(pg_conn, _seed_hunt(pg_conn))
    mark_round_running(pg_conn, round_id)
    assert load_round(pg_conn, round_id).status == "running"
    finish_round(pg_conn, round_id, status="error", error="boom")
    got = pg_conn.execute(text(
        "SELECT status, error_text, finished_at FROM frontend.hunt_rounds "
        "WHERE id = :i"), {"i": round_id}).one()
    assert got.status == "error" and got.error_text == "boom"
    assert got.finished_at is not None


def test_upsert_hunt_result_is_idempotent(pg_conn):
    round_id = _seed_round(pg_conn, _seed_hunt(pg_conn))
    upsert_hunt_result(pg_conn, _result_row(round_id))
    upsert_hunt_result(pg_conn, _result_row(round_id))  # same page+store+store_id
    n = pg_conn.execute(text(
        "SELECT count(*) FROM frontend.hunt_results WHERE round_id = :r"),
        {"r": round_id}).scalar_one()
    assert n == 1


def test_update_round_progress_replaces_blob(pg_conn):
    round_id = _seed_round(pg_conn, _seed_hunt(pg_conn))
    update_round_progress(pg_conn, round_id, {"found": 3, "events": [
        {"t": "2026-07-13T12:00:00Z", "msg": "Searching 'a' — page 1"}]})
    got = pg_conn.execute(text(
        "SELECT progress FROM frontend.hunt_rounds WHERE id = :i"),
        {"i": round_id}).scalar_one()
    assert got["found"] == 3
    assert got["events"][0]["msg"].startswith("Searching")


def test_apply_triage_to_results_maps_candidate_status(pg_conn):
    round_id = _seed_round(pg_conn, _seed_hunt(pg_conn))
    kept_cid = pg_conn.execute(text(
        "INSERT INTO discovery.candidates (page_id, page_name, store, store_id, "
        "source_keyword, technique, status, kept_advertiser_id) "
        "VALUES ('HTEST1', 'Kept', 'ios', 'h1', 'x', 'hunt', 'kept', 424242) "
        "RETURNING id")).scalar_one()
    drop_cid = pg_conn.execute(text(
        "INSERT INTO discovery.candidates (page_id, page_name, store, store_id, "
        "source_keyword, technique, status, drop_reason) "
        "VALUES ('HTEST2', 'Dropped', 'android', 'h2', 'x', 'hunt', 'dropped', 'game') "
        "RETURNING id")).scalar_one()
    new_cid = pg_conn.execute(text(
        "INSERT INTO discovery.candidates (page_id, page_name, store, store_id, "
        "source_keyword, technique, status) "
        "VALUES ('HTEST3', 'Pending', 'ios', 'h3', 'x', 'hunt', 'new') "
        "RETURNING id")).scalar_one()
    for cid, page, store, sid in ((kept_cid, "HTEST1", "ios", "h1"),
                                  (drop_cid, "HTEST2", "android", "h2"),
                                  (new_cid, "HTEST3", "ios", "h3")):
        upsert_hunt_result(pg_conn, _result_row(
            round_id, candidate_id=cid, page_id=page, store=store, store_id=sid))
    stats = apply_triage_to_results(pg_conn, round_id)
    assert stats == {"kept": 1, "vetoed": 1, "pending": 1}
    got = {r.page_id: (r.triage, r.kept_advertiser_id) for r in pg_conn.execute(text(
        "SELECT page_id, triage, kept_advertiser_id FROM frontend.hunt_results "
        "WHERE round_id = :r"), {"r": round_id})}
    assert got["HTEST1"] == ("kept", 424242)
    assert got["HTEST2"] == ("vetoed", None)
    assert got["HTEST3"] == ("pending", None)
    assert round_new_candidate_ids(pg_conn, round_id) == sorted(
        [kept_cid, drop_cid, new_cid])
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/python -m pytest tests/db/test_hunts.py -v`
Expected: FAIL — `ModuleNotFoundError: agf.db.hunts` (the migration applies via the session fixture automatically).

- [ ] **Step 4: Implement `src/agf/db/hunts.py`**

```python
"""Repo for frontend.hunt* — the on-demand Hunt feature (spec 2026-07-13)."""
import json

from sqlalchemy import text


def load_round(conn, round_id: int):
    """One hunt_rounds row (terms/competitor_apps as Python lists) or None."""
    return conn.execute(text(
        "SELECT id, hunt_id, terms, competitor_apps, status "
        "FROM frontend.hunt_rounds WHERE id = :i"), {"i": round_id}).first()


def mark_round_running(conn, round_id: int) -> None:
    conn.execute(text(
        "UPDATE frontend.hunt_rounds "
        "SET status = 'running', started_at = now() WHERE id = :i"),
        {"i": round_id})


def finish_round(conn, round_id: int, *, status: str, error: str | None = None) -> None:
    conn.execute(text(
        "UPDATE frontend.hunt_rounds "
        "SET status = :s, error_text = :e, finished_at = now() WHERE id = :i"),
        {"i": round_id, "s": status, "e": error})


def upsert_hunt_result(conn, row: dict) -> None:
    """One found page per round; re-finds (other term/page) keep the first row."""
    conn.execute(text(
        "INSERT INTO frontend.hunt_results "
        "  (round_id, candidate_id, page_id, page_name, store, store_id, "
        "   link_url, source_term, was_new) "
        "VALUES (:round_id, :candidate_id, :page_id, :page_name, :store, "
        "        :store_id, :link_url, :source_term, :was_new) "
        "ON CONFLICT (round_id, page_id, store, store_id) DO NOTHING"), row)


def update_round_progress(conn, round_id: int, progress: dict) -> None:
    conn.execute(text(
        "UPDATE frontend.hunt_rounds SET progress = CAST(:p AS jsonb) "
        "WHERE id = :i"), {"i": round_id, "p": json.dumps(progress)})


def apply_triage_to_results(conn, round_id: int) -> dict:
    """Copy triage outcomes from discovery.candidates onto this round's results."""
    conn.execute(text(
        "UPDATE frontend.hunt_results hr SET "
        "  triage = CASE c.status WHEN 'kept' THEN 'kept' "
        "                         WHEN 'dropped' THEN 'vetoed' "
        "                         ELSE 'pending' END, "
        "  kept_advertiser_id = c.kept_advertiser_id "
        "FROM discovery.candidates c "
        "WHERE hr.round_id = :r AND hr.candidate_id = c.id"), {"r": round_id})
    counts = {"kept": 0, "vetoed": 0, "pending": 0}
    for row in conn.execute(text(
            "SELECT triage, count(*) AS n FROM frontend.hunt_results "
            "WHERE round_id = :r GROUP BY triage"), {"r": round_id}):
        counts[row.triage] = row.n
    return counts


def round_new_candidate_ids(conn, round_id: int) -> list[int]:
    return [r.candidate_id for r in conn.execute(text(
        "SELECT candidate_id FROM frontend.hunt_results "
        "WHERE round_id = :r AND candidate_id IS NOT NULL "
        "ORDER BY candidate_id"), {"r": round_id})]
```

Check `discovery.candidates.status`/`drop_reason`/`kept_advertiser_id` column names against `db/schema` (grep `candidates` table definition) before finalizing `apply_triage_to_results`; adjust the CASE if the kept/dropped literals differ (triage.py uses `mark_triaged(status="kept"|"dropped")`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/python -m pytest tests/db/test_hunts.py tests/db/test_migrations_placeholder_safe.py -v`
Expected: ALL PASS (placeholder-lint must pass on 155 too).

- [ ] **Step 6: Commit**

```bash
git add db/schema/155_hunts.sql src/agf/db/hunts.py tests/db/test_hunts.py
git commit -m "db+repo: migration 155 hunt tables + frontend.hunt* repo (Hunt spec)"
```

---

### Task 2: Hunt round executor — term sweep lanes, streaming results, progress events

**Files:**
- Create: `src/agf/discovery/hunt.py`
- Test: `tests/discovery/test_hunt.py`

**Interfaces:**
- Consumes: Task 1 repo; `agf.discovery.sweep.classify_candidate` (dispositions `'candidate'|'no_link'|'social'|'non_app'|'no_store_id'`); `agf.db.candidates.upsert_sweep_candidate(conn, row) -> (id, was_new)`; extractor `search_page(query=, countries=, search_type=, cursor=) -> (records, next_cursor, has_next)` and `typeahead(query=) -> list[{page_id, page_name, page_like_count, ig_username}]`, `search_listing(page_id=, max_pages=) -> (records, truncated)`; `tests.discovery.conftest.FakeExtractor`.
- Produces (used by Tasks 3–4): `run_hunt_round(engine, extractors, round_id, *, lookup_ios=None, lookup_android=None, now=None, sleep=time.sleep) -> dict` (stats: terms_swept, pages, found, new, errors). Constants `HUNT_PAGE_CAP=40`, `HUNT_PATIENCE=3`, `HUNT_TERM_LANES=3`, `HUNT_LISTING_PAGES=10`, `HUNT_PAGE_DELAY=2.0`, `HUNT_KW_DELAY=3.0`, `HUNT_EVENT_CAP=50`.

**Design notes for the implementer:**
- Do NOT call `run_sweep` — the hunt needs per-find `(candidate_id, was_new)` and per-page progress, which `run_sweep`/`_process_page` don't expose. Model the per-term paging loop on `run_sweep`'s inner loop (sweep.py:258-328): same stop conditions (Meta exhausted / `consec_dry >= HUNT_PATIENCE` / `depth >= HUNT_PAGE_CAP`), `technique="hunt"`.
- Threading mirrors `keeper_daemon.main`: one `threading.Thread` per lane over a shared `collections.deque` of terms; each lane owns `extractors[i]` and calls `asyncio.run(extractor.search_page(...))` per page (the `run_parallel_sweep` lane pattern). DB writes via short `with begin(engine) as conn` transactions — never hold a txn across a network call (lesson: no I/O inside write txns).
- Web-funnel parity: dispositions `non_app` from `classify_candidate` are NOT persisted by hunt (the nightly sweep already covers funnel capture; hunt targets apps). Count them in stats only. This is a deliberate v1 simplification — note it in the module docstring.
- A `_Progress` helper owns the shared state under a `threading.Lock`; every mutation writes the full blob via `update_round_progress` (single writer per call; last-write-wins across lanes is acceptable — each write includes the whole locked state).

- [ ] **Step 1: Write failing tests `tests/discovery/test_hunt.py`**

```python
"""Hunt round executor tests — fake extractor, real DB (sentinel rows)."""
import json

import pytest
from sqlalchemy import text

from agf.db.engine import begin
from agf.discovery.hunt import HUNT_PATIENCE, run_hunt_round
from tests.discovery.conftest import FakeExtractor, make_ad

pytestmark = pytest.mark.usefixtures("_migrated_engine")

PAGE = "HUNTP1"


def _mk_round(engine, terms, competitors=()):
    with begin(engine) as conn:
        hunt_id = conn.execute(text(
            "INSERT INTO frontend.hunts (user_email, title) "
            "VALUES ('t@test.local', 'hunt-exec-test') RETURNING id")).scalar_one()
        return conn.execute(text(
            "INSERT INTO frontend.hunt_rounds (hunt_id, terms, competitor_apps) "
            "VALUES (:h, CAST(:t AS jsonb), CAST(:c AS jsonb)) RETURNING id"),
            {"h": hunt_id, "t": json.dumps(list(terms)),
             "c": json.dumps([{"name": n} for n in competitors])}).scalar_one()


@pytest.fixture
def engine(_migrated_engine):
    yield _migrated_engine
    with begin(_migrated_engine) as conn:
        conn.execute(text(
            "DELETE FROM frontend.hunts WHERE user_email = 't@test.local'"))
        conn.execute(text(
            "DELETE FROM discovery.candidates WHERE page_id LIKE 'HUNTP%'"))


def _ios_ad(store_id: str, page_id: str = PAGE):
    return make_ad(page_id=page_id, page_name=f"App {store_id}",
                   link_url=f"https://apps.apple.com/us/app/id{store_id}")


def test_round_sweeps_terms_and_records_results(engine):
    ext = FakeExtractor()
    ext.script_pages("sleep app", [[_ios_ad("900001"), _ios_ad("900002")], []])
    round_id = _mk_round(engine, ["sleep app"])
    stats = run_hunt_round(engine, [ext], round_id)
    assert stats["found"] == 2 and stats["new"] == 2
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT page_id, store_id, source_term, was_new, candidate_id "
            "FROM frontend.hunt_results WHERE round_id = :r ORDER BY store_id"),
            {"r": round_id}).all()
    assert [r.store_id for r in rows] == ["900001", "900002"]
    assert all(r.was_new and r.candidate_id and r.source_term == "sleep app"
               for r in rows)


def test_known_candidate_marked_not_new(engine):
    ext = FakeExtractor()
    ext.script_pages("sleep app", [[_ios_ad("900010")]])
    run_hunt_round(engine, [ext], _mk_round(engine, ["sleep app"]))
    ext2 = FakeExtractor()
    ext2.script_pages("sleep app", [[_ios_ad("900010")]])
    round2 = _mk_round(engine, ["sleep app"])
    stats = run_hunt_round(engine, [ext2], round2)
    assert stats["found"] == 1 and stats["new"] == 0
    with engine.connect() as conn:
        assert conn.execute(text(
            "SELECT was_new FROM frontend.hunt_results WHERE round_id = :r"),
            {"r": round2}).scalar_one() is False


def test_patience_stops_dry_paging(engine):
    dry = [[_ios_ad("900020")]] + [[] for _ in range(HUNT_PATIENCE + 5)]
    ext = FakeExtractor()
    ext.script_pages("dry term", dry)
    stats = run_hunt_round(engine, [ext], _mk_round(engine, ["dry term"]))
    assert stats["pages"] == 1 + HUNT_PATIENCE  # first live page + patience window


def test_progress_events_and_status(engine):
    ext = FakeExtractor()
    ext.script_pages("sleep app", [[_ios_ad("900030")], []])
    round_id = _mk_round(engine, ["sleep app"])
    run_hunt_round(engine, [ext], round_id)
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT status, progress FROM frontend.hunt_rounds WHERE id = :i"),
            {"i": round_id}).one()
    assert row.status == "running"  # terminal stamping is hunt_one's job (Task 4)
    assert row.progress["terms_total"] == 1
    assert row.progress["terms_done"] == 1
    assert row.progress["found"] == 1
    assert any("sleep app" in e["msg"] for e in row.progress["events"])


def test_lane_error_yields_partial_results_not_crash(engine):
    ext = FakeExtractor()
    ext.script_pages("good term", [[_ios_ad("900040")], []])
    # "bad term" is unscripted -> FakeExtractor raises ExtractError for it
    ext.error_queries = {"bad term"}
    round_id = _mk_round(engine, ["bad term", "good term"])
    stats = run_hunt_round(engine, [ext], round_id)
    assert stats["errors"] == 1
    assert stats["found"] == 1
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT progress FROM frontend.hunt_rounds WHERE id = :i"),
            {"i": round_id}).one()
    assert any("could not search" in e["msg"].lower()
               for e in row.progress["events"])
```

Check `tests/discovery/conftest.py`: if `FakeExtractor` lacks `make_ad`, an `error_queries` hook, or a `typeahead`/`search_listing` method, EXTEND the shared conftest fake (additive, keyword-defaulted, no behavior change for existing tests) rather than forking a private fake. `make_ad` must produce a real `AdRecord` with the fields `classify_candidate` reads (`page_id, page_name, page_like_count, link_url, cta_type, start_date`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/python -m pytest tests/discovery/test_hunt.py -v`
Expected: FAIL — `ModuleNotFoundError: agf.discovery.hunt`.

- [ ] **Step 3: Implement `src/agf/discovery/hunt.py`**

```python
"""Hunt round executor — on-demand deep sweep of founder-approved terms.

Runs inside the hunt_one subprocess (Task 4). Terms sweep in parallel lanes
(one extractor each); every page's finds stream into frontend.hunt_results and
discovery.candidates immediately, and hunt_rounds.progress carries a live
event feed for the UI. Web-funnel (non_app) ads are counted but not persisted
here — the nightly sweep owns funnel capture (v1 simplification).
"""
import asyncio
import collections
import logging
import threading
import time
from datetime import datetime, timezone

from agf.db.candidates import upsert_sweep_candidate
from agf.db.engine import begin
from agf.db.hunts import (load_round, mark_round_running, update_round_progress,
                          upsert_hunt_result)
from agf.discovery.sweep import classify_candidate
from agf.extract.errors import ExtractError

log = logging.getLogger("agf.hunt")

HUNT_PAGE_CAP = 40        # always-deep: gem band sits below incumbents
HUNT_PATIENCE = 3         # consecutive net-new-dry pages before early exit
HUNT_TERM_LANES = 3       # proven DataImpulse concurrency
HUNT_LISTING_PAGES = 10   # competitor page:<name> pull bound (Task 3)
HUNT_PAGE_DELAY = 2.0
HUNT_KW_DELAY = 3.0
HUNT_EVENT_CAP = 50


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Progress:
    """Thread-safe progress blob writer. Every mutation persists the whole
    locked state — short txn per write, no txn held across network calls."""

    def __init__(self, engine, round_id: int, terms_total: int):
        self._engine = engine
        self._round_id = round_id
        self._lock = threading.Lock()
        self._state = {"lanes": {}, "terms_done": 0, "terms_total": terms_total,
                       "found": 0, "new": 0, "events": []}

    def _flush(self) -> None:
        with begin(self._engine) as conn:
            update_round_progress(conn, self._round_id, self._state)

    def event(self, msg: str) -> None:
        with self._lock:
            self._state["events"] = (
                self._state["events"] + [{"t": _now_iso(), "msg": msg}]
            )[-HUNT_EVENT_CAP:]
            self._flush()

    def page(self, lane: int, term: str, depth: int, found: int, new: int) -> None:
        with self._lock:
            self._state["lanes"][str(lane)] = {"term": term, "page": depth}
            self._state["found"] += found
            self._state["new"] += new
            self._state["events"] = (self._state["events"] + [{
                "t": _now_iso(),
                "msg": f"Searching '{term}' — page {depth} · "
                       f"{found} found · {new} new to Field Desk"}]
            )[-HUNT_EVENT_CAP:]
            self._flush()

    def term_done(self, lane: int) -> None:
        with self._lock:
            self._state["terms_done"] += 1
            self._state["lanes"].pop(str(lane), None)
            self._flush()


def _persist_page(engine, round_id: int, term: str, records, depth: int,
                  stats: dict, lock: threading.Lock) -> tuple[int, int]:
    """Classify + upsert one SERP page. Returns (found, new) for the page."""
    rows, seen = [], set()
    for ad in records:
        disposition, row = classify_candidate(ad, keyword=term, technique="hunt")
        if disposition != "candidate":
            continue
        key = (row["page_id"], row["store"], row["store_id"])
        if key in seen:
            continue
        seen.add(key)
        row["first_seen_depth"] = depth
        rows.append(row)
    found = new = 0
    if rows:
        with begin(engine) as conn:
            for row in rows:
                cid, was_new = upsert_sweep_candidate(conn, row)
                upsert_hunt_result(conn, {
                    "round_id": round_id, "candidate_id": cid,
                    "page_id": row["page_id"], "page_name": row["page_name"],
                    "store": row["store"], "store_id": row["store_id"],
                    "link_url": row["link_url"], "source_term": term,
                    "was_new": was_new})
                found += 1
                new += int(was_new)
    with lock:
        stats["found"] += found
        stats["new"] += new
    return found, new


def _sweep_term(engine, extractor, round_id: int, lane: int, term: str,
                progress: _Progress, stats: dict, lock: threading.Lock,
                sleep) -> None:
    cursor = None
    depth = 0
    consec_dry = 0
    while True:
        depth += 1
        try:
            records, cursor, has_next = asyncio.run(extractor.search_page(
                query=term, countries=["US"],
                search_type="KEYWORD_UNORDERED", cursor=cursor))
        except ExtractError:
            log.warning("hunt round %d: search failed for %r", round_id, term)
            with lock:
                stats["errors"] += 1
            progress.event(f"Could not search '{term}' — skipped after an error")
            return
        with lock:
            stats["pages"] += 1
        found, new = _persist_page(engine, round_id, term, records, depth,
                                   stats, lock)
        progress.page(lane, term, depth, found, new)
        consec_dry = 0 if new > 0 else consec_dry + 1
        if not has_next or not cursor:
            return
        if consec_dry >= HUNT_PATIENCE:
            return
        if depth >= HUNT_PAGE_CAP:
            return
        sleep(HUNT_PAGE_DELAY)


def run_hunt_round(engine, extractors, round_id: int, *,
                   lookup_ios=None, lookup_android=None,
                   sleep=time.sleep) -> dict:
    """Execute one approved hunt round. Streams results as pages complete.
    Terminal round/job status is stamped by the caller (hunt_one)."""
    with begin(engine) as conn:
        rnd = load_round(conn, round_id)
        if rnd is None:
            raise ValueError(f"hunt round {round_id} not found")
        mark_round_running(conn, round_id)
    terms = list(rnd.terms)
    stats = {"terms_swept": 0, "pages": 0, "found": 0, "new": 0, "errors": 0,
             "kept": 0, "vetoed": 0, "pending": 0, "keywords_minted": 0}
    lock = threading.Lock()
    progress = _Progress(engine, round_id, terms_total=len(terms))
    progress.event(f"Hunt started — {len(terms)} terms, deep paging")

    queue = collections.deque(terms)

    def lane(lane_id: int) -> None:
        extractor = extractors[lane_id % len(extractors)]
        while True:
            try:
                term = queue.popleft()
            except IndexError:
                return
            _sweep_term(engine, extractor, round_id, lane_id, term,
                        progress, stats, lock, sleep)
            with lock:
                stats["terms_swept"] += 1
            progress.term_done(lane_id)
            sleep(HUNT_KW_DELAY)

    lanes = min(HUNT_TERM_LANES, max(1, len(terms)), max(1, len(extractors)))
    threads = [threading.Thread(target=lane, args=(i,), daemon=True)
               for i in range(lanes)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    progress.event(f"Search finished — {stats['found']} found, "
                   f"{stats['new']} new to Field Desk")
    return stats
```

Adjust the `ExtractError` import path to wherever it actually lives (grep `class ExtractError`); `begin` import path likewise (grep `from agf.db.engine import begin` in existing modules — keeper_daemon imports it).

- [ ] **Step 4: Run tests to verify they pass**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/python -m pytest tests/discovery/test_hunt.py -v`
Expected: ALL PASS. Also run `tests/discovery/test_sweep.py tests/discovery/test_parallel_sweep.py` — the conftest fake changes must not break them.

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/hunt.py tests/discovery/test_hunt.py tests/discovery/conftest.py
git commit -m "feat(hunt): round executor — parallel term lanes, streaming results, live progress"
```

---

### Task 3: Executor integration — competitor pages, triage readback, keyword mint, enrich enqueue

**Files:**
- Modify: `src/agf/discovery/hunt.py`
- Test: `tests/discovery/test_hunt.py` (append)

**Interfaces:**
- Consumes: `extractor.typeahead(query=) -> list[dict]`, `extractor.search_listing(page_id=, max_pages=) -> (records, truncated)`; `agf.discovery.triage.run_triage(conn, *, lookup_ios, lookup_android, limit=None)`; Task 1 `apply_triage_to_results`, `round_new_candidate_ids`; `agf.db.dynamic_keywords.add_trial_keywords(conn, keywords, *, source, kind)`; `agf.db.pipeline_jobs.enqueue_enrich(conn, advertiser_ids)`.
- Produces: `run_hunt_round` gains phases: (0) competitor page pulls before term lanes; (5) post-sweep triage + result readback + enrich enqueue + keyword mint. Stats keys `kept`, `vetoed`, `pending`, `keywords_minted`, `enrich_enqueued`, `competitor_pages`.

- [ ] **Step 1: Write failing tests (append to `tests/discovery/test_hunt.py`)**

```python
def _meta(name="Dreamy", is_game=False, rating_count=100):
    from types import SimpleNamespace
    return SimpleNamespace(name=name, seller="Dev", genre="Health",
                           release_date=None, avg_rating=4.5,
                           rating_count=rating_count, installs=None,
                           icon_url=None, screenshots=[], price_label=None,
                           description="", is_game=is_game)


def test_competitor_page_pull_records_results(engine):
    ext = FakeExtractor()
    ext.typeahead_results = {"Calm": [
        {"page_id": "HUNTP9", "page_name": "Calm", "page_like_count": 5000,
         "ig_username": None}]}
    ext.listing_ads = {"HUNTP9": [_ios_ad("900050", page_id="HUNTP9")]}
    round_id = _mk_round(engine, [], competitors=("Calm",))
    stats = run_hunt_round(engine, [ext], round_id)
    assert stats["competitor_pages"] == 1
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT source_term, page_id FROM frontend.hunt_results "
            "WHERE round_id = :r"), {"r": round_id}).one()
    assert row.source_term == "page:Calm" and row.page_id == "HUNTP9"


def test_triage_readback_enrich_and_keyword_mint(engine):
    ext = FakeExtractor()
    ext.script_pages("hunt mint term", [[_ios_ad("900060")], []])
    round_id = _mk_round(engine, ["hunt mint term"])
    lookups = lambda ids: {str(i): _meta() for i in ids}
    stats = run_hunt_round(engine, [ext], round_id,
                           lookup_ios=lookups, lookup_android=lookups)
    assert stats["kept"] == 1 and stats["enrich_enqueued"] == 1
    assert stats["keywords_minted"] == 1
    with engine.connect() as conn:
        res = conn.execute(text(
            "SELECT triage, kept_advertiser_id FROM frontend.hunt_results "
            "WHERE round_id = :r"), {"r": round_id}).one()
        assert res.triage == "kept" and res.kept_advertiser_id is not None
        kw = conn.execute(text(
            "SELECT source, status FROM discovery.dynamic_keywords "
            "WHERE keyword = 'hunt mint term' AND kind = 'app'")).one()
        assert kw.source == "hunt" and kw.status == "trial"
        job = conn.execute(text(
            "SELECT status FROM public.pipeline_jobs "
            "WHERE kind = 'enrich' AND advertiser_id = :a"),
            {"a": res.kept_advertiser_id}).scalar_one()
        assert job == "queued"


def test_no_lookups_leaves_triage_pending(engine):
    ext = FakeExtractor()
    ext.script_pages("pending term", [[_ios_ad("900070")], []])
    round_id = _mk_round(engine, ["pending term"])
    stats = run_hunt_round(engine, [ext], round_id)  # no lookups passed
    assert stats["pending"] == 1 and stats["kept"] == 0
```

Extend the fixture `engine` cleanup with:
```python
        conn.execute(text(
            "DELETE FROM public.pipeline_jobs WHERE kind = 'enrich' AND advertiser_id IN "
            "(SELECT kept_advertiser_id FROM frontend.hunt_results "
            " WHERE kept_advertiser_id IS NOT NULL)"))
        conn.execute(text(
            "DELETE FROM discovery.dynamic_keywords "
            "WHERE keyword IN ('hunt mint term', 'pending term') AND kind = 'app'"))
```
(and clean promoted advertisers/apps by the sentinel page_id/store_id pattern used in test_triage.py — copy its cleanup).
NOTE the cleanup runs BEFORE the hunts delete so the subselect still sees the rows — order the statements accordingly.

- [ ] **Step 2: Run tests to verify they fail**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/python -m pytest tests/discovery/test_hunt.py -v`
Expected: new tests FAIL (`competitor_pages` missing, `enrich_enqueued` missing, triage stays pending).

- [ ] **Step 3: Implement — add to `run_hunt_round` in `src/agf/discovery/hunt.py`**

Add imports:
```python
from agf.db.dynamic_keywords import add_trial_keywords
from agf.db.hunts import apply_triage_to_results
from agf.db.pipeline_jobs import enqueue_enrich
from agf.discovery.triage import run_triage
from sqlalchemy import text
```

Insert phase 0 after `progress.event("Hunt started …")` (before term lanes):
```python
    competitors = [c.get("name", "") for c in (rnd.competitor_apps or [])
                   if c.get("name")]
    stats["competitor_pages"] = 0
    for name in competitors[:3]:
        progress.event(f"Resolving {name}'s ad page")
        try:
            pages = asyncio.run(extractors[0].typeahead(query=name))
        except ExtractError:
            progress.event(f"Could not resolve {name} — skipped")
            stats["errors"] += 1
            continue
        if not pages:
            progress.event(f"No Ad Library page found for {name}")
            continue
        page = pages[0]
        try:
            records, truncated = asyncio.run(extractors[0].search_listing(
                page_id=page["page_id"], max_pages=HUNT_LISTING_PAGES))
        except ExtractError:
            progress.event(f"Could not pull {name}'s ads — skipped")
            stats["errors"] += 1
            continue
        stats["competitor_pages"] += 1
        found, new = _persist_page(engine, round_id, f"page:{name}", records,
                                   1, stats, lock)
        progress.page(0, f"page:{name}", 1, found, new)
```

Insert phase 5 after the lane `join()`s, before the final `progress.event`:
```python
    if lookup_ios is not None and lookup_android is not None:
        progress.event("Checking store listings (triage)")
        with begin(engine) as conn:
            run_triage(conn, lookup_ios=lookup_ios, lookup_android=lookup_android)
        with begin(engine) as conn:
            counts = apply_triage_to_results(conn, round_id)
            stats.update(counts)
            kept_ids = [r.kept_advertiser_id for r in conn.execute(text(
                "SELECT DISTINCT kept_advertiser_id FROM frontend.hunt_results "
                "WHERE round_id = :r AND kept_advertiser_id IS NOT NULL"),
                {"r": round_id})]
            stats["enrich_enqueued"] = enqueue_enrich(conn, kept_ids)
        progress.event(f"Triage done — {stats['kept']} kept, "
                       f"{stats['vetoed']} vetoed; "
                       f"{stats['enrich_enqueued']} queued for enrich")
    else:
        with begin(engine) as conn:
            stats.update(apply_triage_to_results(conn, round_id))
        stats["enrich_enqueued"] = 0
    if terms:
        with begin(engine) as conn:
            stats["keywords_minted"] = add_trial_keywords(
                conn, terms, source="hunt", kind="app")
```

Final event becomes:
```python
    progress.event(f"Hunt finished — {stats['found']} found, "
                   f"{stats['new']} new to Field Desk, {stats['kept']} kept")
```

Note `run_triage` is intentionally UNSCOPED (triages the whole `status='new'` backlog, exactly like the tick) — steady-state backlog is small and scoping would fork prod code. The readback then maps only this round's candidates.

- [ ] **Step 4: Run tests to verify they pass**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/python -m pytest tests/discovery/test_hunt.py tests/discovery/test_triage.py tests/db/test_dynamic_keywords.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/hunt.py tests/discovery/test_hunt.py
git commit -m "feat(hunt): competitor page pulls + inline triage readback + enrich enqueue + hunt keyword mint"
```

---

### Task 4: hunt_one subprocess + hunt daemon + compose service

**Files:**
- Create: `src/agf/discovery/hunt_one.py`
- Create: `src/agf/discovery/hunt_daemon.py`
- Modify: `ops/prod/docker-compose.yml` (add `hunt` service — repo copy; VM copy is edited at deploy time)
- Test: `tests/discovery/test_hunt_daemon.py`

**Interfaces:**
- Consumes: Tasks 1–3; `agf.db.pipeline_jobs.{claim_next, reap_expired, set_job_status, enqueue_jobs}`; keeper_daemon shape (copy its structure).
- Produces: compose service `hunt: command: python -m agf.discovery.hunt_daemon`; job kind literal `"hunt"`; env knobs `AGF_HUNT_POLL_SEC` (default 2), `AGF_HUNT_ROUND_TIMEOUT_SEC` (default 1800).

**Failure-path parity requirements (lessons 2026-07-10/13, non-negotiable):**
- Every path stamps the pipeline job terminal (`done`/`error`) AND the round row terminal. The daemon safety net covers a killed subprocess: if the job is still `running` after the runner returns, mark job `error` AND `finish_round(status='error')`.
- What moves a hunt job out of the due set after ANY outcome: `set_job_status` terminal — there is no producer re-minting hunt jobs (rounds are enqueued once by the Go API; the `(kind, advertiser_id)` partial-unique index dedups), so no hot-loop is possible. State this in the daemon docstring.

- [ ] **Step 1: Write failing tests `tests/discovery/test_hunt_daemon.py`**

```python
"""Hunt daemon claim/safety-net tests (fake runner, real pipeline_jobs)."""
import json

import pytest
from sqlalchemy import text

from agf.db.engine import begin
from agf.db.pipeline_jobs import enqueue_jobs
from agf.enrich_full.enrich_one import set_status


def _mk_round(engine) -> int:
    with begin(engine) as conn:
        hunt_id = conn.execute(text(
            "INSERT INTO frontend.hunts (user_email, title) "
            "VALUES ('d@test.local', 'daemon-test') RETURNING id")).scalar_one()
        return conn.execute(text(
            "INSERT INTO frontend.hunt_rounds (hunt_id, terms) "
            "VALUES (:h, CAST(:t AS jsonb)) RETURNING id"),
            {"h": hunt_id, "t": json.dumps(["x"])}).scalar_one()


@pytest.fixture
def engine(_migrated_engine):
    def _clean():
        with begin(_migrated_engine) as conn:
            conn.execute(text("DELETE FROM public.pipeline_jobs WHERE kind = 'hunt'"))
            conn.execute(text(
                "DELETE FROM frontend.hunts WHERE user_email = 'd@test.local'"))
    _clean()
    try:
        yield _migrated_engine
    finally:
        _clean()


def _job_status(engine, round_id):
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT status FROM public.pipeline_jobs "
            "WHERE kind = 'hunt' AND advertiser_id = :r"),
            {"r": round_id}).scalar_one()


def test_process_once_claims_hunt_job(engine):
    round_id = _mk_round(engine)
    with begin(engine) as conn:
        enqueue_jobs(conn, "hunt", [round_id])
    calls = []
    def fake_runner(round_id_arg, job_id):
        calls.append((round_id_arg, job_id))
        set_status(engine, job_id, "done", finished=True,
                   status_table="pipeline_jobs")
    from agf.discovery.hunt_daemon import process_once
    assert process_once(engine, runner=fake_runner) is True
    assert len(calls) == 1 and calls[0][0] == round_id
    assert _job_status(engine, round_id) == "done"
    assert process_once(engine, runner=fake_runner) is False  # queue drained


def test_safety_net_marks_job_and_round_on_runner_death(engine):
    round_id = _mk_round(engine)
    with begin(engine) as conn:
        enqueue_jobs(conn, "hunt", [round_id])
    def dead_runner(round_id_arg, job_id):
        raise RuntimeError("subprocess killed")
    from agf.discovery.hunt_daemon import process_once
    assert process_once(engine, runner=dead_runner) is True
    assert _job_status(engine, round_id) == "error"
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT status, error_text FROM frontend.hunt_rounds WHERE id = :i"),
            {"i": round_id}).one()
    assert row.status == "error" and "killed" in row.error_text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/python -m pytest tests/discovery/test_hunt_daemon.py -v`
Expected: FAIL — `ModuleNotFoundError: agf.discovery.hunt_daemon`.

- [ ] **Step 3: Implement `src/agf/discovery/hunt_daemon.py`**

```python
"""Hunt daemon — drains public.pipeline_jobs kind='hunt' (round_id in the
advertiser_id column). Dedicated single-lane service so a founder-started
hunt is claimed within ~AGF_HUNT_POLL_SEC even when keeper/pipeline lanes
are busy. No hot-loop risk: rounds are enqueued exactly once by the API
(partial-unique (kind, advertiser_id) dedups) and every outcome below stamps
the job terminal."""
import logging
import os
import subprocess
import sys
import threading
import time

from sqlalchemy import text

from agf.db.engine import begin, get_engine
from agf.db.hunts import finish_round
from agf.db.pipeline_jobs import claim_next, reap_expired, set_job_status

log = logging.getLogger("agf.hunt_daemon")

KIND = "hunt"
POLL_SEC = int(os.environ.get("AGF_HUNT_POLL_SEC", "2"))
ROUND_TIMEOUT_SEC = int(os.environ.get("AGF_HUNT_ROUND_TIMEOUT_SEC", "1800"))
LEASE_SEC = ROUND_TIMEOUT_SEC + 120


def run_hunt_subprocess(round_id: int, job_id: int) -> None:
    cmd = [sys.executable, "-m", "agf.discovery.hunt_one",
           "--round-id", str(round_id),
           "--job-id", str(job_id),
           "--timeout", str(ROUND_TIMEOUT_SEC)]
    subprocess.run(cmd, timeout=ROUND_TIMEOUT_SEC + 60, check=False)


def process_once(engine, *, runner=run_hunt_subprocess) -> bool:
    with begin(engine) as conn:
        reaped = reap_expired(conn, KIND)
        if reaped:
            log.warning("reaped %d expired hunt lease(s)", reaped)
        row = claim_next(conn, KIND, lease_sec=LEASE_SEC)
    if row is None:
        return False
    job_id, round_id = row
    log.info("running hunt round %d (job %d)", round_id, job_id)
    err: str | None = None
    try:
        runner(round_id, job_id)
    except Exception as e:  # noqa: BLE001 — daemon must survive any runner death
        log.exception("hunt job %d runner failed", job_id)
        err = str(e)[:500]
    with begin(engine) as conn:
        still_running = conn.execute(text(
            "SELECT 1 FROM public.pipeline_jobs "
            "WHERE id = :i AND status = 'running'"), {"i": job_id}).scalar()
        if still_running:
            msg = err or "hunt subprocess exited without recording status"
            set_job_status(conn, job_id, "error", error=msg)
            finish_round(conn, round_id, status="error", error=msg)
    return True


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    engine = get_engine()

    def lane() -> None:
        while True:
            try:
                busy = process_once(engine)
            except Exception:  # noqa: BLE001
                log.exception("hunt lane tick failed")
                busy = False
            if not busy:
                time.sleep(POLL_SEC)

    t = threading.Thread(target=lane, daemon=True)
    t.start()
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Implement `src/agf/discovery/hunt_one.py`**

```python
"""Run ONE hunt round to completion — the hunt_daemon subprocess body.
Owns terminal status for both the round row and the pipeline job on every
exit path (the daemon's safety net only catches a killed process)."""
import argparse
import logging
import signal
import sys
from functools import partial

from agf.db.engine import begin, get_engine
from agf.db.hunts import finish_round
from agf.db.pipeline_jobs import set_job_status
from agf.discovery.hunt import HUNT_TERM_LANES, run_hunt_round

log = logging.getLogger("agf.hunt_one")


class HuntTimeout(BaseException):
    """SIGALRM watchdog — BaseException so no except-Exception swallows it
    (lesson: EnrichDeadline)."""


def _build_extractors(k: int):
    from agf.extract.facade import Extractor
    from agf.extract.proxy import ProxyPool
    proxies = ProxyPool.list_from_env()
    k = max(1, k)
    if not proxies:
        return [Extractor(proxies=ProxyPool(proxies))]
    if len(proxies) >= k:
        slices = [proxies[i::k] for i in range(k)]
        return [Extractor(proxies=ProxyPool(sl)) for sl in slices]
    return [Extractor(proxies=ProxyPool(proxies)) for _ in range(k)]


def main(argv=None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--round-id", type=int, required=True)
    ap.add_argument("--job-id", type=int, required=True)
    ap.add_argument("--timeout", type=int, default=1800)
    args = ap.parse_args(argv)

    def _alarm(signum, frame):
        raise HuntTimeout(f"hunt round exceeded {args.timeout}s")

    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(args.timeout)

    engine = get_engine()
    import httpx
    from agf.enrich_full.appstore import lookup_google_play, lookup_itunes_batch
    http_client = httpx.Client(timeout=20, follow_redirects=True)
    try:
        extractors = _build_extractors(HUNT_TERM_LANES)
        stats = run_hunt_round(
            engine, extractors, args.round_id,
            lookup_ios=partial(lookup_itunes_batch, http_client, pause=3.0),
            lookup_android=lookup_google_play)
        with begin(engine) as conn:
            finish_round(conn, args.round_id, status="done")
            set_job_status(conn, args.job_id, "done")
        log.info("hunt round %d done: %s", args.round_id, stats)
        return 0
    except HuntTimeout as e:
        _fail(engine, args, str(e))
        return 1
    except Exception as e:  # noqa: BLE001 — terminal-status parity on all paths
        log.exception("hunt round %d failed", args.round_id)
        _fail(engine, args, str(e)[:500])
        return 1
    finally:
        signal.alarm(0)
        http_client.close()


def _fail(engine, args, msg: str) -> None:
    with begin(engine) as conn:
        finish_round(conn, args.round_id, status="error", error=msg)
        set_job_status(conn, args.job_id, "error", error=msg)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Add the compose service to `ops/prod/docker-compose.yml`** (next to `keeper`; same image/restart/init/depends_on/env-file block as `keeper` — copy that service verbatim and change name/command/env):

```yaml
  hunt:
    <<COPY the keeper service's image/env_file/restart/init/depends_on lines exactly>>
    command: python -m agf.discovery.hunt_daemon
    environment:
      AGF_HUNT_POLL_SEC: ${AGF_HUNT_POLL_SEC:-2}
      AGF_HUNT_ROUND_TIMEOUT_SEC: ${AGF_HUNT_ROUND_TIMEOUT_SEC:-1800}
```
(Read the actual keeper block first — it may use YAML anchors or an env_file; mirror precisely. The VM's compose has drifted from the repo copy before — deploy step edits BOTH.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/python -m pytest tests/discovery/test_hunt_daemon.py tests/discovery/test_keeper_daemon.py -v`
Expected: ALL PASS.

- [ ] **Step 7: Commit**

```bash
git add src/agf/discovery/hunt_one.py src/agf/discovery/hunt_daemon.py ops/prod/docker-compose.yml tests/discovery/test_hunt_daemon.py
git commit -m "feat(hunt): dedicated hunt daemon + hunt_one subprocess (terminal-status parity on all paths)"
```

---

### Task 5: Go — hunt models, repo, Gemini proposal helper

**Files:**
- Create: `backend/models/hunt.go`
- Create: `backend/repositories/hunt_repo.go`
- Modify: `backend/helpers/gemini.go` (append `GenerateHuntProposal`)
- Test: `backend/repositories/hunt_repo_test.go` (gated on `AGF_TEST_DB_URL` like sibling repo tests — check a sibling repo test's TestMain/skip pattern and mirror it)

**Interfaces:**
- Consumes: `db.WithReadTx`/`db.WithWriteTx`, gorm raw SQL patterns from enrich_repo.go/explainer_repo.go.
- Produces (Task 6 consumes exactly these):

```go
// models/hunt.go
type HuntRow struct {
    ID        int        `json:"id"`
    Title     string     `json:"title"`
    CreatedAt time.Time  `json:"created_at"`
    LastRoundStatus *string `json:"last_round_status"` // null when no rounds
}
type HuntMessageRow struct {
    ID        int             `json:"id"`
    Role      string          `json:"role"`
    Content   string          `json:"content"`
    Proposal  json.RawMessage `json:"proposal"` // null for user msgs
    CreatedAt time.Time       `json:"created_at"`
}
type HuntRoundRow struct {
    ID         int             `json:"id"`
    MessageID  *int            `json:"message_id"`
    Terms      json.RawMessage `json:"terms"`
    Competitors json.RawMessage `json:"competitor_apps"`
    Status     string          `json:"status"`
    Progress   json.RawMessage `json:"progress"`
    ErrorText  *string         `json:"error_text"`
    CreatedAt  time.Time       `json:"created_at"`
    FinishedAt *time.Time      `json:"finished_at"`
}
type HuntResultRow struct {
    ID               int     `json:"id"`
    RoundID          int     `json:"round_id"`
    PageID           string  `json:"page_id"`
    PageName         *string `json:"page_name"`
    Store            string  `json:"store"`
    StoreID          string  `json:"store_id"`
    LinkURL          *string `json:"link_url"`
    SourceTerm       string  `json:"source_term"`
    WasNew           bool    `json:"was_new"`
    Triage           string  `json:"triage"`
    KeptAdvertiserID *int    `json:"kept_advertiser_id"`
    GemScore         *float64 `json:"gem_score"` // joined at read time when scored
}
// repositories/hunt_repo.go
func CreateHunt(ctx, g, email, title string) (int, error)
func HuntOwner(ctx, g, huntID int) (string, error)      // "" when not found
func ListHunts(ctx, g, email string) ([]models.HuntRow, error)
func InsertHuntMessage(ctx, g, huntID int, role, content string, proposal json.RawMessage) (int, error)
func ListHuntMessages(ctx, g, huntID int) ([]models.HuntMessageRow, error)
func ListHuntRounds(ctx, g, huntID int) ([]models.HuntRoundRow, error)
func ListHuntResults(ctx, g, huntID int) ([]models.HuntResultRow, error)
func InsertHuntRound(ctx, g, huntID int, messageID *int, terms, competitors json.RawMessage) (int, error) // + enqueues pipeline_jobs('hunt', roundID) in the same txn
func HuntRoundSummaries(ctx, g, huntID int) ([]models.HuntRoundSummary, error) // terms + kept/found counts for the Gemini context
// helpers/gemini.go
type HuntTurn struct { Role string; Content string }
type HuntProposalInput struct {
    Message        string
    History        []HuntTurn        // prior chat turns, oldest first
    TriedTerms     []string          // all terms from prior rounds
    RoundSummaries []string          // "round 1: 12 terms, 40 found, 5 kept"
}
type HuntProposal struct {
    Reply          string   `json:"reply"`
    Terms          []string `json:"terms"`
    CompetitorApps []string `json:"competitor_apps"`
}
func (h *GeminiHelper) GenerateHuntProposal(ctx context.Context, in HuntProposalInput) (HuntProposal, error)
```

- [ ] **Step 1: Write failing repo test `backend/repositories/hunt_repo_test.go`** — mirror the sibling repo test's DB-skip pattern; cover: CreateHunt→ListHunts ordering + LastRoundStatus null; InsertHuntMessage/ListHuntMessages roundtrip (proposal jsonb survives); InsertHuntRound inserts BOTH the round (status `queued`) and one `pipeline_jobs` row kind `hunt` advertiser_id=roundID; double InsertHuntRound for the same live round is impossible by design (new round each call) but assert the pipeline row count matches rounds; ListHuntResults joins `adtech.advertiser_scores` for GemScore null-safe; HuntOwner returns owner email / "" when missing.

```go
package repositories

// Representative core (full file: mirror sibling test setup/teardown):
func TestInsertHuntRoundEnqueuesJob(t *testing.T) {
    g := testDB(t) // skip when AGF_TEST_DB_URL unset — copy sibling helper
    ctx := context.Background()
    huntID, err := CreateHunt(ctx, g, "repo@test.local", "sleep stories")
    require.NoError(t, err)
    terms, _ := json.Marshal([]string{"sleep stories app"})
    comps, _ := json.Marshal([]string{})
    roundID, err := InsertHuntRound(ctx, g, huntID, nil, terms, comps)
    require.NoError(t, err)
    var kind, status string
    require.NoError(t, g.Raw(
        `SELECT kind, status FROM public.pipeline_jobs
         WHERE kind = 'hunt' AND advertiser_id = ?`, roundID).
        Row().Scan(&kind, &status))
    require.Equal(t, "queued", status)
    t.Cleanup(func() {
        g.Exec(`DELETE FROM public.pipeline_jobs WHERE kind='hunt' AND advertiser_id = ?`, roundID)
        g.Exec(`DELETE FROM frontend.hunts WHERE id = ?`, huntID)
    })
}
```

- [ ] **Step 2: Run to verify failure** — `cd backend && go test ./repositories/ -run TestInsertHuntRound -v` → compile error (funcs undefined).

- [ ] **Step 3: Implement models + repo.** SQL sketches (complete in implementation):

```go
// ListHunts
`SELECT h.id, h.title, h.created_at,
        (SELECT r.status FROM frontend.hunt_rounds r
          WHERE r.hunt_id = h.id ORDER BY r.id DESC LIMIT 1) AS last_round_status
 FROM frontend.hunts h WHERE lower(h.user_email) = lower(?) ORDER BY h.id DESC`

// InsertHuntRound (one WithWriteTx; two statements)
`INSERT INTO frontend.hunt_rounds (hunt_id, message_id, terms, competitor_apps)
 VALUES (?, ?, ?::jsonb, ?::jsonb) RETURNING id`
`INSERT INTO public.pipeline_jobs (kind, advertiser_id) VALUES ('hunt', ?)
 ON CONFLICT (kind, advertiser_id) WHERE status IN ('queued','running') DO NOTHING`

// ListHuntResults (GemScore best-effort join; verify score table+column names
// against explainer/shortlist repos before writing — likely adtech.advertiser_scores.gem_score)
`SELECT hr.id, hr.round_id, hr.page_id, hr.page_name, hr.store, hr.store_id,
        hr.link_url, hr.source_term, hr.was_new, hr.triage, hr.kept_advertiser_id,
        s.gem_score
 FROM frontend.hunt_results hr
 JOIN frontend.hunt_rounds r ON r.id = hr.round_id
 LEFT JOIN adtech.advertiser_scores s ON s.advertiser_id = hr.kept_advertiser_id
 WHERE r.hunt_id = ? ORDER BY hr.id`
```
IMPORTANT: gorm's `?::jsonb` can collide with placeholder parsing — use `CAST(? AS jsonb)` instead (matches Python side and avoids the psycopg-style gotcha family).

- [ ] **Step 4: Implement `GenerateHuntProposal`** — mirror `GenerateExplainer` exactly (schema, retries, guards):

```go
const huntSystemInstruction = "You are Peon, Field Desk's discovery agent. " +
    "You brainstorm Meta Ad Library search terms to find emerging mobile apps. " +
    "Terms must be generic niche queries a user would type (e.g. 'sleep stories app', " +
    "'ai bedtime audio') — short, plain, no brand names. Brand or app names the user " +
    "mentions belong in competitor_apps, never in terms. Never repeat a term listed " +
    "as already tried. Reply text is 1-3 plain sentences on the angles you chose. " +
    "Propose 8-15 terms."
```
Schema: `reply` string, `terms` array-of-string (Items: &genai.Schema{Type: genai.TypeString}), `competitor_apps` array-of-string; Required all three; MaxOutputTokens 1024. Prompt builder concatenates: history turns ("User:"/"Peon:"), "Already tried terms: …", round summaries, then "New request: {Message}". Post-process in the helper: lowercase+trim terms, drop empties/dupes/already-tried, cap 15; cap competitors 3.

- [ ] **Step 5: Verify** — `cd backend && go build ./... && go vet ./... && AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it go test ./repositories/ ./helpers/ -v` → PASS (Gemini helper compile-only unless a key is present; do NOT call the live API in tests).

- [ ] **Step 6: Commit**

```bash
git add backend/models/hunt.go backend/repositories/hunt_repo.go backend/repositories/hunt_repo_test.go backend/helpers/gemini.go
git commit -m "feat(api): hunt models + repo (round insert enqueues pipeline job) + Peon Gemini proposal helper"
```

---

### Task 6: Go — hunt controller + routes + integration tests

**Files:**
- Create: `backend/controllers/hunt_controller.go`
- Modify: `backend/routers/routers.go`
- Test: `backend/internal/integration/hunt_test.go`

**Interfaces:**
- Consumes: Task 5 repo + helper; `middlewares.UserFrom`, `reqCtx`, `helpers.Detail`.
- Produces HTTP API (web client in Task 7 consumes):
  - `POST /api/hunts` `{message}` → 201 `{hunt_id, messages: [...]}` (creates hunt, user msg, Peon msg w/ proposal)
  - `GET /api/hunts` → 200 `[HuntRow]`
  - `GET /api/hunts/:hunt_id` → 200 `{hunt: {...}, messages, rounds, results}`; 404 foreign/missing
  - `POST /api/hunts/:hunt_id/messages` `{message}` → 201 `{messages}` (appends both turns)
  - `POST /api/hunts/:hunt_id/rounds` `{message_id?, terms[], competitor_apps[]}` → 202 round JSON; 422 on 0 or >15 terms / >3 competitors

- [ ] **Step 1: Controller design (complete code in implementation).** Constructor takes the Gemini helper: `func NewHuntController(gemini *helpers.GeminiHelper) *HuntController`; struct holds it. `routers.Init`: `hunt := controllers.NewHuntController(helpers.NewGeminiHelper())`. When the helper is nil (no `GEMINI_API_KEY`), `Create`/`Message` return 503 `"Peon is not configured."`; the read + round endpoints still work. Gemini context: `context.WithTimeout(ctx.Request.Context(), 30*time.Second)` (NOT reqCtx's 15s). On Gemini error: still persist the user message, return 502 `"Peon could not answer. Try again."` — thread stays consistent, founder retries. Ownership: every `:hunt_id` route resolves `HuntOwner` and 404s (not 403 — don't leak existence) when it doesn't match `UserFrom` email (case-insensitive). Term validation before insert: trim, lowercase, dedupe, drop empties; 1–15 terms; ≤3 competitors; terms max 80 chars each.

Routes to add inside the `api` group:
```go
api.GET("/hunts", hunt.List)
api.POST("/hunts", middlewares.RequireToken(), hunt.Create)
api.GET("/hunts/:hunt_id", hunt.Get)
api.POST("/hunts/:hunt_id/messages", middlewares.RequireToken(), hunt.Message)
api.POST("/hunts/:hunt_id/rounds", middlewares.RequireToken(), hunt.StartRound)
```

- [ ] **Step 2: Write failing integration tests `backend/internal/integration/hunt_test.go`** — inject a FAKE proposal function: give `HuntController` an internal field `propose func(ctx, in) (helpers.HuntProposal, error)` defaulting to the Gemini helper; integration tests construct the controller with a stub (exported test hook `controllers.NewHuntControllerForTest(stub)`) OR (simpler, preferred) test only the non-Gemini paths through the real router: `GET /api/hunts` empty; `POST /api/hunts/:id/rounds` happy-path (seed hunt+message via SQL) asserting 202 + `hunt_rounds` row + `pipeline_jobs` kind `hunt` row; validation 422s (0 terms, 16 terms, 4 competitors); foreign-email 404; `GET /api/hunts/:id` returns rounds+results after SQL-seeding a result row. Follow `enrich_test.go` shapes (`t.Setenv(middlewares.APITokenEnv, writeToken)` before `router()`; `authedInternal(...)` for writes). Gemini-dependent turns (`POST /hunts`, `/messages`) are covered by the live gate, not integration tests — note this in a file comment.

- [ ] **Step 3: Run to verify failure** — `cd backend && go test ./internal/integration/ -run TestHunt -v` with `AGF_IT_DB_URL` set (check how the suite is invoked in CI/scripts; if unset locally the suite exits 0 silently — set it to the same agf_it DB: `AGF_IT_DB_URL=postgresql://agf:agf@localhost:55433/agf_it`). Expected: compile failure.

- [ ] **Step 4: Implement controller + routes; run** `go build ./... && go vet ./... && AGF_IT_DB_URL=postgresql://agf:agf@localhost:55433/agf_it go test ./internal/integration/ -v` → ALL PASS (whole suite, not just hunt tests — the shared seed must stay intact).

- [ ] **Step 5: Commit**

```bash
git add backend/controllers/hunt_controller.go backend/routers/routers.go backend/internal/integration/hunt_test.go
git commit -m "feat(api): hunt endpoints — Peon chat, term validation, round start enqueues hunt job"
```

---

### Task 7: Web — API client, types, pure helpers, proxy + auth wiring

**Files:**
- Modify: `web/src/lib/api.ts` (hunt types + calls)
- Create: `web/src/lib/hunt.ts` (pure helpers) + `web/src/lib/hunt.test.ts`
- Modify: `web/src/app/backend/[...path]/route.ts` (ALLOWED_RESOURCES + "hunts")
- Modify: `web/src/lib/auth.config.ts` (PROTECTED + "/hunt")

**Interfaces:**
- Consumes: Task 6 wire shapes.
- Produces (Task 8 consumes): types `HuntSummary, HuntThread, HuntMessage, HuntRound, HuntResult, HuntProposal`; calls `getHunts(): Promise<HuntSummary[]>`, `getHunt(id: number): Promise<HuntThread>`, `postHunt(message: string): Promise<HuntThread>`, `postHuntMessage(id: number, message: string): Promise<HuntThread>`, `postHuntRound(id: number, body: {message_id: number | null; terms: string[]; competitor_apps: string[]}): Promise<HuntRound>`; helpers `parseProposal(raw: unknown): HuntProposal | null`, `huntIsActive(rounds: HuntRound[]): boolean`, `latestEvents(round: HuntRound, n?: number): {t: string; msg: string}[]`, `editTerms(terms: string[], op: {add?: string; remove?: string}): string[]` (trim/lowercase/dedupe/cap-15 — mirrors server validation so the UI can't submit a 422).

- [ ] **Step 1: Write failing tests `web/src/lib/hunt.test.ts`** (node:test, explicit `.ts` import — copy adapters.test.ts header): parseProposal accepts `{reply, terms, competitor_apps}` and rejects malformed (null, missing arrays → null); editTerms add trims/lowercases/dedupes and refuses beyond 15; editTerms remove; huntIsActive true when any round `queued|running`; latestEvents returns newest-first slice, empty for `{}` progress.

- [ ] **Step 2: Run to verify failure** — `cd web && node --test src/lib/hunt.test.ts` → module not found.

- [ ] **Step 3: Implement `hunt.ts` helpers + api.ts additions + the two wiring edits** (add `"hunts"` to ALLOWED_RESOURCES — the API resource segment is `hunts`; add `"/hunt"` to PROTECTED — the page route is `/hunt`). api.ts follows the existing `live<T>` idiom exactly.

- [ ] **Step 4: Run** — `cd web && node --test src/lib/hunt.test.ts && npx tsc --noEmit && npm run lint` → PASS/clean.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/api.ts web/src/lib/hunt.ts web/src/lib/hunt.test.ts "web/src/app/backend/[...path]/route.ts" web/src/lib/auth.config.ts
git commit -m "feat(web): hunt API client, pure helpers, proxy allowlist + route protection"
```

---

### Task 8: Web — /hunt page (Peon chat, Start hunt, activity feed, streaming results)

**Files:**
- Create: `web/src/app/hunt/page.tsx`
- Create: `web/src/components/hunt/HuntView.tsx` (client root: sidebar + thread + polling)
- Create: `web/src/components/hunt/ProposalCard.tsx` (Peon message + editable term chips + competitor checkboxes + Start hunt)
- Create: `web/src/components/hunt/RoundBlock.tsx` (activity feed + lane status + results list)
- Modify: `web/src/components/AppHeader.tsx` (add Hunt link)

**Interfaces:**
- Consumes: Task 7 client + helpers; `ProductShell` from `@/components/chrome`; RefreshDataButton polling idiom.

**Requirements (UI copy per CLAUDE.md + spec — plain, real data, no marketing):**
- `page.tsx` mirrors discover/page.tsx: `<ProductShell><Suspense><HuntView /></Suspense></ProductShell>`.
- HuntView: left rail = hunts from `getHunts()` (title, date, last-round status dot) + "New hunt"; main = message list (assistant label **Peon**), composer at bottom ("Describe a niche or a competitor app"). While any round is `queued|running`: poll `getHunt(id)` every 2s (setInterval-in-ref, resume on mount, stop when idle — clone RefreshDataButton's shape); when idle but any kept result lacks a GemScore: poll every 30s.
- ProposalCard (only on the LATEST assistant message with a proposal and no round yet): term chips with × remove, input to add (via `editTerms`), competitor checkboxes (default checked), **Start hunt** button → `postHuntRound` → optimistic round render. Disable Start hunt while terms empty or a round is active.
- RoundBlock: status line ("Queued", "3 lanes · 6 of 12 terms · 214 pages" — derive from `progress.lanes/terms_done/terms_total`), activity feed = `latestEvents(round)` newest-first in a scrollable box, then results table-list: page name, store badge, source term, "New" marker when `was_new`, triage state ("checking" for pending / "kept" / "vetoed"), GemScore link → `/gems/${kept_advertiser_id}` when scored. Show "{found} found · {new} new to Field Desk" from progress counters. Errors: round status `error` renders `error_text` plainly with no retry button (start a new round from the chat).
- AppHeader: `{ href: "/hunt", label: "Hunt", Icon: IconTargetArrow }` (pick an existing @tabler icon; verify it exists in the installed version) — insert after Discover.

- [ ] **Step 1: Implement all components.** No component unit tests (repo convention: lib-only tests); the gates are `npx tsc --noEmit`, `npm run lint`, `npm run build`, and the live verify.

- [ ] **Step 2: Run gates** — `cd web && npx tsc --noEmit && npm run lint && npm run build` → clean build.

- [ ] **Step 3: Local render check** — `npm run dev` + Playwright (browser MCP): log in is NOT available locally without creds — instead verify the page compiles and redirects cookieless `/hunt` → `/login` (this also proves PROTECTED wiring). Screenshot for the record.

- [ ] **Step 4: Commit**

```bash
git add web/src/app/hunt/page.tsx web/src/components/hunt/ web/src/components/AppHeader.tsx
git commit -m "feat(web): Hunt page — Peon chat, editable terms, Start hunt, live activity feed + streaming results"
```

---

### Task 9 (controller-run, not a subagent): Final review, deploy, live verify

Per /ship-increment §4 and the spec's live-verify section:
1. Opus whole-branch review over the hunt commit list; consolidated fixer for blockers; re-review.
2. Full suites bare: Python (`AGF_TEST_DB_URL=… pytest -q` → 0 failed), Go (`go build ./... && go vet ./... && go test ./...` + integration w/ `AGF_IT_DB_URL`), web (`node --test src/lib/*.test.ts`, `tsc --noEmit`, `lint`, `build`). Skips are not passes.
3. Deploy order: migration 155 on prod → worker image (clean-worktree Cloud Build) → VM compose: add `hunt` service to the VM's LIVE compose file (it has drifted before — diff against repo copy, edit BOTH) → `docker compose up -d hunt` (+ roll worker services) → GKE api then web (`kubectl set image`, NEG 502 wait).
4. Live gate A (worker, before UI): throwaway container from the new image, insert a real hunt+round via SQL for a known niche ("sleep stories"), enqueue the job, watch the hunt daemon claim it; verify progress events advance, hunt_results rows land with correct was_new, triage outcomes apply, enrich jobs enqueue, keywords minted `source='hunt'`.
5. Live gate B (product): Playwright on peon.works — log in, open /hunt, create a hunt ("find competitors of Calm doing sleep stories"), verify Peon proposal renders, edit a term, Start hunt, watch the activity feed stream, results appear, term saved in dynamic_keywords, cookieless probe of /hunt → 307 /login.
6. Memory + handoff updates; /self-eval; /reflect.

## Self-review notes (done)
- Spec coverage: chat→proposal→edit→Start hunt (T5/6/8), queue+priority via dedicated daemon (T4), deep adaptive paging + 3 lanes + progress events (T2), competitor pages (T3), triage/enrich/feed flow (T3), keyword mint source='hunt' (T3), history+polling UI (T8), proxy/auth wiring (T7), migration (T1), live verify (T9). Spec's "drain before keeper/corpus" is implemented as a DEDICATED always-free lane rather than reordering keeper's KINDS — strictly better for claim latency; spec intent (founder waits seconds, not minutes) preserved.
- Placeholders: none — where a detail must be verified in-repo (candidates status literals, keeper compose block, score column name, tabler icon), the task says exactly what to grep and what to do with it.
- Type consistency: round terms are `json.RawMessage` end-to-end in Go; `was_new` boolean; round_id rides pipeline_jobs.advertiser_id everywhere (T1 test, T4 daemon, T5 repo).
