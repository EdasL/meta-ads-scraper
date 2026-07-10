# Parallel Flywheel + Faster Cadence + Deep-Retest Lane — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Parallelize the dynamic-keyword flywheel sweep (K workers), space re-sweeps of proven keywords, and re-admit the 955 shallow-judged retired keywords for one forced-deep retest — per `docs/superpowers/specs/2026-07-10-parallel-flywheel-design.md`.

**Architecture:** The serial `for kw in live:` loop in `run_flywheel_stage` becomes K disjoint shards on a `ThreadPoolExecutor` (same shape as `run_parallel_sweep`), each thread running the unchanged per-keyword body (single-entry `run_sweep` + `record_keyword_sweep` in its own txn). Retest admission and active-keyword spacing are two small additions to `src/agf/db/dynamic_keywords.py` plus migration 153 (`retested_at` column). Cadence/pool changes are env defaults in `ops/prod/docker-compose.yml`.

**Tech Stack:** Python 3.12, SQLAlchemy (raw `text()` SQL), pytest with `pg_conn` (rolled-back txn) and `_migrated_engine` fixtures, ThreadPoolExecutor.

## Global Constraints

- Full-suite gate: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it pytest -q` — ZERO failed; a skip is not a pass for the tests you add (verify they RAN).
- Migrations: applied via `exec_driver_sql` with NO params — **never a bare percent character anywhere in the migration file, including comments** (write "pct"); use `make_interval` for intervals. Verify against a fresh DB (the `_migrated_engine` fixture does this).
- `git add` explicit paths only; commit per task; a parallel session may share this repo — re-read files before editing (a stale-read Edit error = collision signal).
- Keep product nouns intact; no UI copy in this increment.
- Do not touch: static taxonomy sweep, resting/wake policy, trial verdict constants, enrich/triage paths.

---

### Task 1: Migration 153 + `live_keywords` active-gap and `retested_at`

**Files:**
- Create: `db/schema/153_keyword_retest.sql`
- Modify: `src/agf/db/dynamic_keywords.py:137-147` (`live_keywords`)
- Test: `tests/db/test_dynamic_keywords.py` (append)

**Interfaces:**
- Produces: `live_keywords(conn, *, kind="app", active_gap_hours: int = 0)` returning rows with fields `id, keyword, status, sweeps_used, new_candidates_total, retested_at` (Task 3 reads `retested_at` and `sweeps_used`).
- Produces: column `discovery.dynamic_keywords.retested_at timestamptz NULL` (Task 2 stamps it).

- [ ] **Step 1: Write the migration**

`db/schema/153_keyword_retest.sql`:

```sql
-- discovery.dynamic_keywords.retested_at — stamps a deep-retest re-admission
-- (spec: docs/superpowers/specs/2026-07-10-parallel-flywheel-design.md).
-- A retired keyword judged only under pre-2026-07-08 shallow paging is
-- re-admitted as a trial exactly once; a non-null retested_at excludes it
-- from any future retest wave.
-- NOTE: applied via exec_driver_sql with NO params — never a bare percent
-- sign anywhere in this file, including comments (lesson from migration 147).
ALTER TABLE discovery.dynamic_keywords
    ADD COLUMN IF NOT EXISTS retested_at timestamptz;
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/db/test_dynamic_keywords.py`:

```python
def test_retested_at_column_exists(pg_conn):
    # migration 153
    row = pg_conn.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='discovery' AND table_name='dynamic_keywords' "
        "AND column_name='retested_at'")).one_or_none()
    assert row is not None


def test_live_keywords_active_gap_spacing(pg_conn):
    add_trial_keywords(pg_conn, ["gap fresh active", "gap stale active",
                                 "gap trial kw"])
    pg_conn.execute(text(
        "UPDATE discovery.dynamic_keywords SET status='active', "
        "last_swept_at = now() - make_interval(hours => 1) "
        "WHERE keyword='gap fresh active'"))
    pg_conn.execute(text(
        "UPDATE discovery.dynamic_keywords SET status='active', "
        "last_swept_at = now() - make_interval(hours => 13) "
        "WHERE keyword='gap stale active'"))
    names = lambda rows: {r.keyword for r in rows}
    spaced = names(live_keywords(pg_conn, active_gap_hours=12))
    assert "gap stale active" in spaced          # older than the gap -> swept
    assert "gap fresh active" not in spaced      # swept 1h ago -> spaced out
    assert "gap trial kw" in spaced              # trials always in rotation
    everyone = names(live_keywords(pg_conn))     # gap 0 = no spacing (default)
    assert {"gap fresh active", "gap stale active", "gap trial kw"} <= everyone


def test_live_keywords_returns_retested_at(pg_conn):
    add_trial_keywords(pg_conn, ["gap trial kw2"])
    row = [r for r in live_keywords(pg_conn) if r.keyword == "gap trial kw2"][0]
    assert row.retested_at is None               # column present on the row
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it pytest tests/db/test_dynamic_keywords.py -q -k "retested or gap"`
Expected: FAIL — `retested_at` column missing until the fresh fixture applies 153; `live_keywords() got an unexpected keyword argument 'active_gap_hours'`.
(If the persistent test DB has stale state, the migration applies via the migrate runner used by conftest; see `tests/db/test_migrate.py` for the mechanism.)

- [ ] **Step 4: Implement `live_keywords`**

Replace `live_keywords` in `src/agf/db/dynamic_keywords.py` (currently lines 137-147 — locate by content, a parallel session may shift lines):

```python
def live_keywords(conn, *, kind: str = "app", active_gap_hours: int = 0):
    """The keywords a flywheel pass sweeps: trials + proven actives + woken resting
    keywords (cooldown elapsed / NULL), this kind only. A resting keyword whose
    next_eligible_at is still in the future stays out of the rotation.

    active_gap_hours (0 = off): an ACTIVE keyword only re-enters the rotation when
    its last sweep is at least this old — at a fast tick cadence, re-sweeping a
    proven keyword every pass is pure proxy cost (new ads appear over days).
    Trials and woken resting keywords are always returned; they need verdicts."""
    return conn.execute(text(
        "SELECT id, keyword, status, sweeps_used, new_candidates_total, retested_at "
        "FROM discovery.dynamic_keywords "
        "WHERE kind=:k AND (status='trial' "
        "  OR (status='active' AND (:gap = 0 OR last_swept_at IS NULL "
        "      OR last_swept_at <= now() - make_interval(hours => :gap))) "
        "  OR (status='resting' AND (next_eligible_at IS NULL OR next_eligible_at <= now()))) "
        "ORDER BY CASE status WHEN 'trial' THEN 0 WHEN 'active' THEN 1 ELSE 2 END, id"),
        {"k": kind, "gap": active_gap_hours}).all()  # trials first
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it pytest tests/db/test_dynamic_keywords.py tests/workers/test_flywheel_stage.py -q`
Expected: PASS (including the pre-existing flywheel tests — the new SELECT column and kwarg are additive).

- [ ] **Step 6: Commit**

```bash
git add db/schema/153_keyword_retest.sql src/agf/db/dynamic_keywords.py tests/db/test_dynamic_keywords.py
git commit -m "feat(flywheel): migration 153 retested_at + active-gap spacing in live_keywords"
```

---

### Task 2: `admit_retest_keywords` — the deep-retest re-admission

**Files:**
- Modify: `src/agf/db/dynamic_keywords.py` (add below `add_trial_keywords`)
- Test: `tests/db/test_dynamic_keywords.py` (append)

**Interfaces:**
- Consumes: `retested_at` column (Task 1).
- Produces: `RETEST_SHALLOW_CUTOFF: str` and `admit_retest_keywords(conn, *, limit: int, kind: str = "app") -> int` (count re-admitted). Task 3 calls it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/db/test_dynamic_keywords.py`:

```python
def _mk_retired(conn, keyword, last_swept, retested_at=None):
    add_trial_keywords(conn, [keyword])
    conn.execute(text(
        "UPDATE discovery.dynamic_keywords SET status='retired', sweeps_used=4, "
        "dry_streak=3, last_swept_at=:ls, retested_at=:ra WHERE keyword=:kw"),
        {"ls": last_swept, "ra": retested_at, "kw": keyword})


def test_admit_retest_keywords(pg_conn):
    from agf.db.dynamic_keywords import admit_retest_keywords
    _mk_retired(pg_conn, "rt shallow old", "2026-06-20")
    _mk_retired(pg_conn, "rt shallow older", "2026-06-10")
    _mk_retired(pg_conn, "rt adaptive era", "2026-07-09")     # post-cutoff: stays
    _mk_retired(pg_conn, "rt already done", "2026-06-15",
                retested_at="2026-07-09")                      # once only
    # limit=1 admits the OLDEST shallow verdict first
    assert admit_retest_keywords(pg_conn, limit=1) == 1
    row = pg_conn.execute(text(
        "SELECT status, sweeps_used, dry_streak, retested_at "
        "FROM discovery.dynamic_keywords WHERE keyword='rt shallow older'")).one()
    assert row.status == "trial" and row.sweeps_used == 0 and row.dry_streak == 0
    assert row.retested_at is not None
    # the rest of the cohort on the next call; ineligibles untouched
    assert admit_retest_keywords(pg_conn, limit=10) == 1       # only 'rt shallow old'
    for kw in ("rt adaptive era", "rt already done"):
        st = pg_conn.execute(text(
            "SELECT status FROM discovery.dynamic_keywords WHERE keyword=:k"),
            {"k": kw}).scalar()
        assert st == "retired"
    # drained cohort + limit<=0 are no-ops
    assert admit_retest_keywords(pg_conn, limit=10) == 0
    assert admit_retest_keywords(pg_conn, limit=0) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it pytest tests/db/test_dynamic_keywords.py::test_admit_retest_keywords -q`
Expected: FAIL — `ImportError: cannot import name 'admit_retest_keywords'`.

- [ ] **Step 3: Implement**

Add to `src/agf/db/dynamic_keywords.py`, directly below `add_trial_keywords`:

```python
# Adaptive-depth paging shipped 2026-07-08; keywords whose last sweep predates it
# were judged on flat 2-4 page sweeps only — the deep-retest cohort (955 on prod).
RETEST_SHALLOW_CUTOFF = "2026-07-08"


def admit_retest_keywords(conn, *, limit: int, kind: str = "app") -> int:
    """Re-admit up to `limit` shallow-era retired keywords as trials (oldest verdict
    first) for one deep retest. Stamps retested_at so a keyword is never re-admitted
    twice; the forced-deep first sweep is the flywheel loop's job. Returns the count."""
    if limit <= 0:
        return 0
    return conn.execute(text(
        "UPDATE discovery.dynamic_keywords "
        "SET status='trial', sweeps_used=0, dry_streak=0, retested_at=now() "
        "WHERE id IN (SELECT id FROM discovery.dynamic_keywords "
        "  WHERE kind=:k AND status='retired' AND retested_at IS NULL "
        "    AND last_swept_at < :cutoff "
        "  ORDER BY last_swept_at ASC LIMIT :lim)"),
        {"k": kind, "cutoff": RETEST_SHALLOW_CUTOFF, "lim": limit}).rowcount
```

- [ ] **Step 4: Run test to verify it passes**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it pytest tests/db/test_dynamic_keywords.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agf/db/dynamic_keywords.py tests/db/test_dynamic_keywords.py
git commit -m "feat(flywheel): admit_retest_keywords re-admits shallow-era retired keywords once"
```

---

### Task 3: Parallel `run_flywheel_stage` + forced-deep retest sweep

**Files:**
- Modify: `src/agf/workers/main.py` (`run_flywheel_stage`, currently lines 478-530 — locate by content) + imports
- Test: `tests/workers/test_flywheel_stage.py` (append)

**Interfaces:**
- Consumes: `live_keywords(..., active_gap_hours=)` + row field `retested_at` (Task 1); `admit_retest_keywords` (Task 2); `_partition`, `SweepBreaker` from `src/agf/discovery/sweep.py`; `run_sweep(..., patience_pages=, breaker=, heartbeat=, worker_id=)` (exists).
- Produces: `run_flywheel_stage(engine, extractors, *, max_pages=2, kw_delay=3.0, mine_min_score=40.0, max_trials=40, min_advertisers=3, retest_per_pass=0, active_gap_hours=0, max_consecutive_errors=0, heartbeat=None, corpus_fn=fetch_gem_copy) -> dict`. `extractors` is a list; a bare single extractor is auto-wrapped (back-compat for existing tests/callers). Stats dict gains `"retested": int`. Task 4 wires it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/workers/test_flywheel_stage.py`:

```python
class _CountingExtractor(_FakeExtractor):
    """Yields one candidate per keyword, like _FakeExtractor, but is instance-tracked
    so a K=2 run can prove disjoint shard coverage."""


class _BarrenPagingExtractor:
    """Endless empty SERP pages — exercises patience/depth stopping rules.
    pages_by_query counts how deep the sweep actually paged per keyword."""
    def __init__(self):
        self.pages_by_query = {}

    async def search_page(self, *, query, cursor=None, **kw):
        n = self.pages_by_query.get(query, 0) + 1
        self.pages_by_query[query] = n
        return [], f"c{n}", True                     # no ads, always a next page


def test_parallel_flywheel_covers_all_keywords_disjointly(_migrated_engine):
    eng = _migrated_engine
    kws = [f"pfw kw {i}" for i in range(6)]
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.dynamic_keywords "
                          "WHERE keyword LIKE 'pfw kw %'"))
        add_trial_keywords(conn, kws)
    ex1, ex2 = _CountingExtractor(), _CountingExtractor()
    try:
        stats = run_flywheel_stage(eng, [ex1, ex2], max_pages=1, kw_delay=0,
                                   corpus_fn=lambda conn, min_score: [])
        swept = ex1.searched + ex2.searched
        assert set(kws) <= set(swept)                      # every live kw swept
        assert not (set(ex1.searched) & set(ex2.searched)) # disjoint shards
        assert stats["keywords_swept"] >= 6
        with eng.connect() as conn:                        # verdicts recorded
            used = conn.execute(text(
                "SELECT min(sweeps_used) FROM discovery.dynamic_keywords "
                "WHERE keyword LIKE 'pfw kw %'")).scalar()
        assert used == 1
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.candidates "
                              "WHERE page_id = 'FWPAGE1'"))
            conn.execute(text("DELETE FROM discovery.dynamic_keywords "
                              "WHERE keyword LIKE 'pfw kw %'"))


def test_retested_keyword_first_sweep_is_forced_deep(_migrated_engine, monkeypatch):
    eng = _migrated_engine
    monkeypatch.setenv("AGF_SWEEP_PATIENCE_PAGES", "3")
    monkeypatch.setenv("AGF_SWEEP_MAX_DEPTH", "15")
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.dynamic_keywords "
                          "WHERE keyword LIKE 'deep rt %'"))
        add_trial_keywords(conn, ["deep rt retested", "deep rt normal"])
        conn.execute(text(
            "UPDATE discovery.dynamic_keywords SET retested_at=now(), sweeps_used=0 "
            "WHERE keyword='deep rt retested'"))
    ex = _BarrenPagingExtractor()
    try:
        run_flywheel_stage(eng, [ex], max_pages=1, kw_delay=0,
                           corpus_fn=lambda conn, min_score: [])
        # normal barren trial: patience window stops it early
        assert ex.pages_by_query["deep rt normal"] <= 4
        # retested first sweep: patience disabled up to the depth cap
        assert ex.pages_by_query["deep rt retested"] == 15
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.dynamic_keywords "
                              "WHERE keyword LIKE 'deep rt %'"))


def test_retest_admission_fills_room_after_mints(_migrated_engine):
    eng = _migrated_engine
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.dynamic_keywords "
                          "WHERE keyword LIKE 'rtadm %'"))
        _mk_retired_eng(conn, "rtadm shallow", "2026-06-20")
    ex = _FakeExtractor()
    try:
        stats = run_flywheel_stage(eng, [ex], max_pages=1, kw_delay=0,
                                   retest_per_pass=5,
                                   corpus_fn=lambda conn, min_score: [])
        assert stats["retested"] >= 1
        assert "rtadm shallow" in ex.searched          # re-admitted AND swept this pass
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.candidates "
                              "WHERE page_id = 'FWPAGE1'"))
            conn.execute(text("DELETE FROM discovery.dynamic_keywords "
                              "WHERE keyword LIKE 'rtadm %'"))


def _mk_retired_eng(conn, keyword, last_swept):
    add_trial_keywords(conn, [keyword])
    conn.execute(text(
        "UPDATE discovery.dynamic_keywords SET status='retired', sweeps_used=4, "
        "last_swept_at=:ls WHERE keyword=:kw"), {"ls": last_swept, "kw": keyword})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it pytest tests/workers/test_flywheel_stage.py -q -k "parallel or forced_deep or admission"`
Expected: FAIL — `run_flywheel_stage() got an unexpected keyword argument 'retest_per_pass'` / list-extractor TypeError.

- [ ] **Step 3: Implement**

In `src/agf/workers/main.py`:

(a) imports — extend the existing `from agf.discovery.sweep import run_sweep, run_parallel_sweep` line and add ThreadPoolExecutor if not already imported:

```python
from concurrent.futures import ThreadPoolExecutor

from agf.discovery.sweep import (SweepBreaker, _partition, run_parallel_sweep,
                                 run_sweep)
```

and add `admit_retest_keywords` to the existing `from agf.db.dynamic_keywords import (...)` block.

(b) replace `run_flywheel_stage` (keep the mine/gate/admission head verbatim — only the pieces shown here change):

```python
def run_flywheel_stage(engine, extractors, *, max_pages=2, kw_delay=3.0,
                       mine_min_score=40.0, max_trials=40, min_advertisers=3,
                       retest_per_pass=0, active_gap_hours=0,
                       max_consecutive_errors=0, heartbeat=None,
                       corpus_fn=fetch_gem_copy) -> dict:
    """One mine+sweep pass of the keyword flywheel: phrases recurring across the ad
    copy of scored gems become trial sweep keywords, every live keyword (trial|active,
    plus resting keywords whose cooldown has elapsed) gets an adaptive-depth exact-phrase
    sweep into the SAME app candidate pool the static taxonomy feeds, and each keyword's
    yield decides its fate (promote, or rest with a cooldown backoff — policy constants
    in db/dynamic_keywords). A throttled keyword (search error) doesn't burn trial budget.

    PARALLEL (2026-07-10): the sweep loop runs across len(extractors) threads on
    round-robin-disjoint keyword shards (same shape as run_parallel_sweep); per-keyword
    lifecycle writes touch only that keyword's row, so shards can't race. One shared
    SweepBreaker aborts the stage on a gateway-wide outage (0 disables, the default).
    A single bare extractor is auto-wrapped — the one-shard path is exactly the old
    serial loop. `retest_per_pass` re-admits shallow-era retired keywords into spare
    trial room (see admit_retest_keywords); a re-admitted keyword's FIRST sweep runs
    with patience disabled up to AGF_SWEEP_MAX_DEPTH so a barren incumbent prefix
    can't cut it off before the deep pages its original verdict never saw.
    `corpus_fn` is injectable for tests."""
    if not isinstance(extractors, (list, tuple)):
        extractors = [extractors]
    with begin(engine) as conn:
        apply_verdicts(conn)
    with engine.connect() as conn:
        tried = tried_keywords(conn)
        room = max_trials - trial_count(conn)
        corpus = corpus_fn(conn, min_score=mine_min_score) if room > 0 else []
    static = {e.keyword for e in all_entries()}
    mined = mine_phrases(corpus, exclude=static | tried, limit=room,
                         min_advertisers=min_advertisers) if corpus else []
    mined = drop_token_supersets(mined, static | tried) if mined else []
    mined = _gate_keywords(engine, mined, limit=room) if mined else []
    if mined:
        with begin(engine) as conn:
            add_trial_keywords(conn, mined)
    retested = 0
    if retest_per_pass > 0:
        with engine.connect() as conn:
            room_left = max_trials - trial_count(conn)   # mints keep priority
        if room_left > 0:
            with begin(engine) as conn:
                retested = admit_retest_keywords(
                    conn, limit=min(retest_per_pass, room_left))
    with engine.connect() as conn:
        live = live_keywords(conn, active_gap_hours=active_gap_hours)
    shards = _partition(list(live), len(extractors))
    breaker = SweepBreaker(max_consecutive_errors, workers=len(shards) or 1)
    depth_cap = _env_int("AGF_SWEEP_MAX_DEPTH", 15)

    def _sweep_shard(extractor, shard, worker_id):
        part = {"keywords_swept": 0, "errors": 0, "candidates_new": 0,
                "candidates_seen": 0, "promoted": 0, "retired": 0}
        for kw in shard:
            if breaker.tripped():
                break
            deep_first = kw.retested_at is not None and kw.sweeps_used == 0
            entry = TaxonomyEntry(kw.keyword, "flywheel",
                                  "KEYWORD_EXACT_PHRASE", max_pages)
            s = asyncio.run(run_sweep(
                engine, extractor, entries=[entry], max_pages=max_pages,
                kw_delay=kw_delay, breaker=breaker, heartbeat=heartbeat,
                max_consecutive_errors=max_consecutive_errors,
                patience_pages=depth_cap if deep_first else None,
                worker_id=worker_id))
            if s["keywords_swept"] != 1:          # throttled — no verdict from this
                part["errors"] += 1
                continue
            with begin(engine) as conn:
                status = record_keyword_sweep(conn, kw.id,
                                              new_candidates=s["candidates_new"])
            part["keywords_swept"] += 1
            part["candidates_new"] += s["candidates_new"]
            part["candidates_seen"] += s["candidates_seen"]
            if kw.status == "trial" and status == "active":
                part["promoted"] += 1
            elif status == "retired":
                part["retired"] += 1
        return part

    if len(shards) <= 1:
        parts = [_sweep_shard(extractors[0], shards[0], 0)] if shards else []
    else:
        triples = list(zip(extractors, shards, range(len(shards))))
        with ThreadPoolExecutor(max_workers=len(triples)) as pool:
            parts = list(pool.map(lambda tr: _sweep_shard(*tr), triples))
    stats = {"mined": mined, "retested": retested, "keywords_swept": 0,
             "errors": 0, "candidates_new": 0, "candidates_seen": 0,
             "promoted": 0, "retired": 0}
    for p in parts:
        for key, val in p.items():
            stats[key] += val
    return stats
```

Notes for the implementer:
- `_partition` and `SweepBreaker` are exported names in `src/agf/discovery/sweep.py` (`_partition` at :303, `SweepBreaker` at :34) — import as shown; do not copy them.
- If the breaker trips and `run_sweep` raises, the exception propagates out of the pool into `run_once`'s existing try/except, which error-closes the flywheel run — same isolation contract as today. Per-keyword verdicts already committed are kept (each is its own txn).
- `close_flywheel_run` stores stats as jsonb, so the new `"retested"` key persists with no schema change.

- [ ] **Step 4: Run tests to verify they pass**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it pytest tests/workers/test_flywheel_stage.py -q`
Expected: PASS — including the pre-existing serial-call tests (bare-extractor auto-wrap is the regression gate).

- [ ] **Step 5: Commit**

```bash
git add src/agf/workers/main.py tests/workers/test_flywheel_stage.py
git commit -m "feat(flywheel): parallel K-shard sweep loop + forced-deep first sweep for retested keywords"
```

---

### Task 4: `run_once` wiring, env plumbing, compose defaults

**Files:**
- Modify: `src/agf/workers/main.py` (`run_once` signature + flywheel branch at ~:780-805; env kwargs block at ~:1085; the env docstring table at ~:37-54 — locate all by content)
- Modify: `ops/prod/docker-compose.yml` (worker service `environment:`, near lines 77-81)
- Test: `tests/workers/test_main.py` (append)

**Interfaces:**
- Consumes: Task 3's `run_flywheel_stage(engine, extractors, ..., retest_per_pass=, active_gap_hours=, max_consecutive_errors=, heartbeat=)`; `build_extractors(dry_run, now_epoch, k)` (main.py:169).
- Produces: `run_once(..., flywheel_workers=1, flywheel_retest_per_pass=0, flywheel_active_gap_hours=0, ...)`; envs `AGF_FLYWHEEL_WORKERS` (default 3), `AGF_FLYWHEEL_RETEST_PER_PASS` (default 0), `AGF_FLYWHEEL_ACTIVE_GAP_HOURS` (default 12).

- [ ] **Step 1: Extract the env→kwargs seam (mechanical refactor, enables the test)**

`main()` currently builds `cfg = dict(...)` inline (src/agf/workers/main.py:1063-1101 — locate by content `cfg = dict(`). Move that dict construction verbatim into a new module-level function directly above `main()`:

```python
def _cfg_from_env(*, continuous: bool) -> dict:
    """run_once kwargs from the AGF_* environment — one seam so tests can assert
    env plumbing without driving main()."""
    return dict(
        # ... the existing dict body, moved VERBATIM — do not add the new
        # flywheel keys yet; they land in Step 4 so Step 2's test fails first.
    )
```

and in `main()`: `cfg = _cfg_from_env(continuous=continuous)`. Move ONLY the dict literal — `continuous` is computed before it in `main()` and comes in as the parameter; every other value in the dict is already a pure `_env_*` read. If any dict entry references another local of `main()` besides `continuous`, pass it the same way.

- [ ] **Step 2: Write the failing test**

Append to `tests/workers/test_main.py`:

```python
def test_flywheel_env_plumbing(monkeypatch):
    """The three new flywheel envs reach run_once's kwargs (defaults + overrides)."""
    from agf.workers import main as m
    cfg = m._cfg_from_env(continuous=False)
    assert cfg["flywheel_workers"] == 3            # spec defaults
    assert cfg["flywheel_retest_per_pass"] == 0    # retest lane off unless enabled
    assert cfg["flywheel_active_gap_hours"] == 12
    monkeypatch.setenv("AGF_FLYWHEEL_WORKERS", "5")
    monkeypatch.setenv("AGF_FLYWHEEL_RETEST_PER_PASS", "7")
    monkeypatch.setenv("AGF_FLYWHEEL_ACTIVE_GAP_HOURS", "9")
    cfg = m._cfg_from_env(continuous=False)
    assert cfg["flywheel_workers"] == 5
    assert cfg["flywheel_retest_per_pass"] == 7
    assert cfg["flywheel_active_gap_hours"] == 9
```

- [ ] **Step 3: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it pytest tests/workers/test_main.py -q -k flywheel_env`
Expected: FAIL — KeyError: the three flywheel keys are not in the cfg dict yet.

- [ ] **Step 4: Implement wiring**

(a) `run_once` signature — after `flywheel_min_advertisers=3,` add:

```python
             flywheel_workers=1, flywheel_retest_per_pass=0,
             flywheel_active_gap_hours=0,
```

(b) flywheel branch (currently main.py:790-805) — replace the `run_flywheel_stage` call:

```python
            try:
                fw_extractors = build_extractors(dry_run, now_epoch,
                                                 flywheel_workers)
                flywheel = run_flywheel_stage(
                    engine, fw_extractors,
                    max_pages=flywheel_pages, kw_delay=kw_delay,
                    mine_min_score=mine_min_score,
                    max_trials=flywheel_max_trials,
                    min_advertisers=flywheel_min_advertisers,
                    retest_per_pass=flywheel_retest_per_pass,
                    active_gap_hours=flywheel_active_gap_hours,
                    max_consecutive_errors=sweep_max_consecutive_errors,
                    heartbeat=None if dry_run else _touch_heartbeat)
```

(`flywheel_due` returns False on dry-run, so the dry-run branch never reaches this; `build_extractors` collapses to one fake there anyway.)

(c) add the three env reads to `_cfg_from_env` (Step 1's extraction), next to the existing `flywheel_max_trials=_env_int("AGF_FLYWHEEL_MAX_TRIALS", 40),` line:

```python
        flywheel_workers=_env_int("AGF_FLYWHEEL_WORKERS", 3),
        flywheel_retest_per_pass=_env_int("AGF_FLYWHEEL_RETEST_PER_PASS", 0),
        flywheel_active_gap_hours=_env_int("AGF_FLYWHEEL_ACTIVE_GAP_HOURS", 12),
```

(d) module docstring env table (near line 54) — add:

```
  AGF_FLYWHEEL_WORKERS          concurrent flywheel sweep workers     (default 3)
  AGF_FLYWHEEL_RETEST_PER_PASS  shallow-retired re-admissions/pass    (default 0 = off)
  AGF_FLYWHEEL_ACTIVE_GAP_HOURS min hours between active re-sweeps    (default 12)
```

(e) `ops/prod/docker-compose.yml` worker `environment:` — change the two defaults and add four lines (keep the `${VAR:-default}` pattern):

```yaml
      AGF_SWEEP_INTERVAL_SEC: ${AGF_SWEEP_INTERVAL_SEC:-1800}
      AGF_FLYWHEEL_EVERY_SEC: ${AGF_FLYWHEEL_EVERY_SEC:-60}
      AGF_FLYWHEEL_WORKERS: ${AGF_FLYWHEEL_WORKERS:-3}
      AGF_FLYWHEEL_MAX_TRIALS: ${AGF_FLYWHEEL_MAX_TRIALS:-200}
      AGF_FLYWHEEL_ACTIVE_GAP_HOURS: ${AGF_FLYWHEEL_ACTIVE_GAP_HOURS:-12}
      AGF_FLYWHEEL_RETEST_PER_PASS: ${AGF_FLYWHEEL_RETEST_PER_PASS:-50}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it pytest tests/workers/ -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agf/workers/main.py tests/workers/test_main.py ops/prod/docker-compose.yml
git commit -m "feat(flywheel): wire parallel workers, retest lane, active gap through run_once + prod compose defaults"
```

---

## Final gates (controller-run, after all tasks)

1. Full suite bare, own Bash call, read the whole output: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it pytest -q` → 0 failed, new tests RAN (not skipped).
2. Opus whole-branch review; fix blockers; re-review.
3. Deploy: clean-worktree image build → VM compose (hand-edit the DRIFTED VM copy to match the repo's new env lines) → roll worker.
4. Live verify in the deployed container: one flywheel pass with `AGF_FLYWHEEL_RETEST_PER_PASS` small (e.g. 10) first — check `discovery.flywheel_runs` (duration well under serial, errors=0, stats.retested>0), retested keywords show `sweeps_used=1` and deep page depth; then set prod envs (200/50/60/1800) and watch the next passes.
5. Memory + spec/plan checkboxes updated.
