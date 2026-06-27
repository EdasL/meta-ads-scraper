# Flywheel Active-Keyword Retirement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retire a proven `active` flywheel keyword after K consecutive 0-new sweeps so the sweep stops re-walking harvested basins (gem_copy is at ~0.15 new/sweep because saturated actives are never retired).

**Architecture:** Add one additive column `dry_streak` to `discovery.dynamic_keywords` (migration 143). `record_keyword_sweep` maintains the streak in its existing UPDATE (reset on any new candidate, +1 on a dry sweep) and retires an `active` once the streak crosses an env-tunable threshold, before the unchanged trial verdict. Observability and the worker call sites are untouched — they already count a `"retired"` return.

**Tech Stack:** Python 3.13, SQLAlchemy Core (`text()`), Postgres (Cloud SQL), pytest with a DB-backed `pg_conn` fixture (rolled-back per test).

## Global Constraints

- Module root is `backend/`; run with `PYTHONPATH=src`. Source lives under `src/agf/`.
- DB tests need `AGF_TEST_DB_URL` set or they skip; an ephemeral pgvector Postgres is already running at `postgresql://agf:agf@localhost:55433/agf_it`.
- Migrations are plain SQL in `db/schema/NNN_*.sql`, applied in lexical order by `agf.db.migrate.apply_migrations` and tracked in `public.schema_migrations`. The `pg_conn` fixture applies them automatically.
- DB tests use `FWTEST*` sentinel keywords only (the shared test DB holds other rows); `pg_conn` rolls back per test.
- Conn-first repo convention: functions take `conn`, never commit (the caller owns the transaction).
- Constants are read at call time via `_env_int(name, default)` (so plist/env changes take effect on restart without a code edit) — match the existing `trial_sweeps()` / `promote_min_new()` pattern.
- Commit after each task. Stage explicit paths only (never `git add -A`). Commit to `main`.

---

### Task 1: Migration 143 — `dry_streak` column

**Files:**
- Create: `db/schema/143_dynamic_keywords_dry_streak.sql`
- Test: `tests/db/test_dynamic_keywords.py` (add one test)

**Interfaces:**
- Consumes: nothing.
- Produces: column `discovery.dynamic_keywords.dry_streak INTEGER NOT NULL DEFAULT 0`, available to Task 2.

- [ ] **Step 1: Write the failing test**

Add to `tests/db/test_dynamic_keywords.py` (uses the existing `dk` import and `pg_conn` fixture):

```python
def test_dry_streak_column_defaults_zero(pg_conn):
    # migration 143: every keyword starts with an empty dry streak
    dk.add_trial_keywords(pg_conn, ["FWTEST freshcol"])
    val = pg_conn.execute(text(
        "SELECT dry_streak FROM discovery.dynamic_keywords WHERE keyword = :k"),
        {"k": "FWTEST freshcol"}).scalar()
    assert val == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/db/test_dynamic_keywords.py::test_dry_streak_column_defaults_zero -v`
Expected: FAIL with a `column "dry_streak" does not exist` (UndefinedColumn) error.

- [ ] **Step 3: Write the migration**

Create `db/schema/143_dynamic_keywords_dry_streak.sql`:

```sql
-- 143: dry_streak counter for active-keyword retirement (flywheel efficiency).
-- Counts consecutive 0-new sweeps; an ACTIVE keyword retires once the streak crosses
-- AGF_FLYWHEEL_RETIRE_DRY_STREAK so the flywheel stops re-walking harvested basins.
-- Additive and idempotent; existing actives start at 0 and retire naturally.
ALTER TABLE discovery.dynamic_keywords
  ADD COLUMN IF NOT EXISTS dry_streak INTEGER NOT NULL DEFAULT 0;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/db/test_dynamic_keywords.py::test_dry_streak_column_defaults_zero -v`
Expected: PASS (the `pg_conn` fixture applies migration 143).

- [ ] **Step 5: Commit**

```bash
git add db/schema/143_dynamic_keywords_dry_streak.sql tests/db/test_dynamic_keywords.py
git commit -m "feat(flywheel): migration 143 — dynamic_keywords.dry_streak column"
```

---

### Task 2: Active-retirement logic in `record_keyword_sweep`

**Files:**
- Modify: `src/agf/db/dynamic_keywords.py` (add `retire_dry_streak()` near `promote_min_new()` ~line 29; rewrite `record_keyword_sweep` ~lines 79-94)
- Test: `tests/db/test_dynamic_keywords.py` (add helpers + four tests)

**Interfaces:**
- Consumes: `dry_streak` column (Task 1); existing `_env_int`, `trial_sweeps()`, `promote_min_new()`.
- Produces: `retire_dry_streak() -> int`; `record_keyword_sweep(conn, keyword_id, *, new_candidates) -> str` now also maintains `dry_streak` and returns `"retired"` when an active crosses the threshold.

- [ ] **Step 1: Write the failing tests**

Add these helpers + tests to `tests/db/test_dynamic_keywords.py`:

```python
def _make_active(conn, keyword, *, dry_streak):
    dk.add_trial_keywords(conn, [keyword])
    conn.execute(text(
        "UPDATE discovery.dynamic_keywords "
        "SET status = 'active', sweeps_used = 8, new_candidates_total = 9, dry_streak = :d "
        "WHERE keyword = :k"),
        {"d": dry_streak, "k": keyword})
    return conn.execute(text(
        "SELECT id FROM discovery.dynamic_keywords WHERE keyword = :k"),
        {"k": keyword}).scalar()


def _dry(conn, keyword):
    return conn.execute(text(
        "SELECT dry_streak FROM discovery.dynamic_keywords WHERE keyword = :k"),
        {"k": keyword}).scalar()


def test_retire_dry_streak_reads_env(monkeypatch):
    monkeypatch.setenv("AGF_FLYWHEEL_RETIRE_DRY_STREAK", "3")
    assert dk.retire_dry_streak() == 3
    monkeypatch.delenv("AGF_FLYWHEEL_RETIRE_DRY_STREAK", raising=False)
    assert dk.retire_dry_streak() == 5   # default


def test_active_retires_after_dry_streak(pg_conn, monkeypatch):
    monkeypatch.setenv("AGF_FLYWHEEL_RETIRE_DRY_STREAK", "5")
    kid = _make_active(pg_conn, "FWTEST dryout", dry_streak=4)   # one short of K
    assert dk.record_keyword_sweep(pg_conn, kid, new_candidates=0) == "retired"
    assert _status(pg_conn, "FWTEST dryout") == "retired"


def test_active_new_candidate_resets_streak(pg_conn, monkeypatch):
    monkeypatch.setenv("AGF_FLYWHEEL_RETIRE_DRY_STREAK", "5")
    kid = _make_active(pg_conn, "FWTEST revived", dry_streak=4)
    assert dk.record_keyword_sweep(pg_conn, kid, new_candidates=2) == "active"
    assert _dry(pg_conn, "FWTEST revived") == 0


def test_active_below_threshold_stays_active(pg_conn, monkeypatch):
    monkeypatch.setenv("AGF_FLYWHEEL_RETIRE_DRY_STREAK", "5")
    kid = _make_active(pg_conn, "FWTEST patient", dry_streak=2)
    assert dk.record_keyword_sweep(pg_conn, kid, new_candidates=0) == "active"
    assert _dry(pg_conn, "FWTEST patient") == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/db/test_dynamic_keywords.py -k "retire or reset or below or dry_streak_reads" -v`
Expected: FAIL — `test_retire_dry_streak_reads_env` errors with `AttributeError: module ... has no attribute 'retire_dry_streak'`; the three `pg_conn` tests fail because `record_keyword_sweep` does not yet maintain `dry_streak` or retire actives.

- [ ] **Step 3: Add the `retire_dry_streak()` constant**

In `src/agf/db/dynamic_keywords.py`, immediately after the `promote_min_new()` function (~line 35):

```python
def retire_dry_streak() -> int:
    # Consecutive 0-new sweeps before a proven ACTIVE keyword is retired. Without this an
    # active is swept forever after its basin is harvested (gem_copy: 2,250 sweeps -> 336
    # new). Read at call time so the plist/env can tune it without a code edit; set very
    # high to disable. Saturated keywords leave the daily rotation and the never-re-mint set.
    return _env_int("AGF_FLYWHEEL_RETIRE_DRY_STREAK", 5)
```

- [ ] **Step 4: Rewrite `record_keyword_sweep`**

Replace the existing `record_keyword_sweep` (lines 79-94) with:

```python
def record_keyword_sweep(conn, keyword_id: int, *, new_candidates: int) -> str:
    """Bump one keyword's counters after a sweep and apply the verdict; returns the
    (possibly new) status. A 0-new sweep lengthens dry_streak, any new candidate resets
    it. An ACTIVE keyword that has gone dry for retire_dry_streak() straight sweeps is
    retired (drops out of the daily rotation). The trial verdict is unchanged."""
    row = conn.execute(text(
        "UPDATE discovery.dynamic_keywords "
        "SET sweeps_used = sweeps_used + 1, "
        "    new_candidates_total = new_candidates_total + :n, "
        "    dry_streak = CASE WHEN :n > 0 THEN 0 ELSE dry_streak + 1 END, "
        "    last_swept_at = now() "
        "WHERE id = :i RETURNING status, sweeps_used, new_candidates_total, dry_streak"),
        {"n": new_candidates, "i": keyword_id}).one()
    if row.status == "active" and row.dry_streak >= retire_dry_streak():
        conn.execute(text(
            "UPDATE discovery.dynamic_keywords SET status='retired' WHERE id=:i"),
            {"i": keyword_id})
        return "retired"
    if row.status != "trial" or row.sweeps_used < trial_sweeps():
        return row.status
    verdict = "active" if row.new_candidates_total >= promote_min_new() else "retired"
    conn.execute(text("UPDATE discovery.dynamic_keywords SET status=:s WHERE id=:i"),
                 {"s": verdict, "i": keyword_id})
    return verdict
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/db/test_dynamic_keywords.py -k "retire or reset or below or dry_streak_reads" -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Run the full dynamic_keywords + flywheel suites (regression)**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/db/test_dynamic_keywords.py tests/workers/test_flywheel_stage.py tests/discovery/test_funnel_flywheel.py -v`
Expected: all PASS — the existing trial-cadence / promotion / `apply_verdicts` tests are unaffected.

- [ ] **Step 7: Commit**

```bash
git add src/agf/db/dynamic_keywords.py tests/db/test_dynamic_keywords.py
git commit -m "feat(flywheel): retire active keywords after K dry sweeps"
```

---

### Task 3: Full-suite verification

**Files:** none (verification only).

**Interfaces:**
- Consumes: Tasks 1-2.
- Produces: a clean full-suite green confirming no regression.

- [ ] **Step 1: Run the entire suite**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it PYTHONPATH=src .venv/bin/python -m pytest -q`
Expected: `0 failed` (baseline this session was `596 passed, 3 skipped`; expect ~600 passed, 3 skipped — the new tests add to the count). Read the full output bare (do not pipe through tail/grep when deciding pass/fail).

- [ ] **Step 2: No commit** (verification only). If any test fails, fix it under TDD before proceeding to deploy.

---

## Deploy (after all tasks green — separate from the plan, run by hand)

Worker does NOT auto-migrate. Order:
1. `bash ops/prod/02_build_image.sh`
2. Apply migration 143 against prod (new image must be pulled so `143_*.sql` is in the container): on the VM, `sudo docker compose -f /opt/agf/docker-compose.yml run --rm worker python -c "from agf.db.engine import get_engine; from agf.db.migrate import apply_migrations; print(apply_migrations(get_engine()))"`
3. `sudo docker compose -f /opt/agf/docker-compose.yml pull worker cron && sudo docker compose -f /opt/agf/docker-compose.yml up -d worker cron`
4. Verify: after a flywheel/funnel-sweep cycle, `discovery.flywheel_runs.stats->>'retired'` (and `funnel_sweep_runs`) reflect active retirements; `SELECT count(*) FILTER (WHERE status='active') FROM discovery.dynamic_keywords GROUP BY source` trends down for saturated `gem_copy`.

Rollback without deploy: set `AGF_FLYWHEEL_RETIRE_DRY_STREAK` very high in `/opt/agf/.env` and `up -d worker`.
