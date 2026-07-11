# Stage Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `python -m agf.workers.run_stage <stage>` runs any single pipeline stage on demand through the exact code paths `run_once` uses, gates bypassed, test-mode (no run rows) by default — per `docs/superpowers/specs/2026-07-11-stage-runner-design.md`.

**Architecture:** One new module `src/agf/workers/run_stage.py`: an argparse CLI with one subcommand per stage; each subcommand is a thin adapter that builds the same deps `run_once` builds (engine via `get_engine()`, extractors via `build_extractors`, httpx client where needed) and calls the existing stage function. Plus one additive param (`only_keywords`) on `run_flywheel_stage`.

**Tech Stack:** Python 3.12, argparse, existing stage functions in `agf.workers.main` / `agf.discovery.*`; pytest with `pg_conn` / `_migrated_engine` fixtures.

## Global Constraints

- Full-suite gate: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it pytest -q` — ZERO failed; verify your new tests RAN (not skipped).
- `git add` explicit paths only; commit per task; commits go directly to main (repo convention). Re-read before editing — a parallel session may share the repo.
- NO change to scheduled worker behavior: `run_once` and the stage functions keep byte-identical behavior for existing callers (the `only_keywords` param must default to None = current behavior).
- No new env vars, no migration.
- The adapters must call the SAME functions run_once calls — read `run_once` in `src/agf/workers/main.py` (stage blocks ~lines 627-960) and mirror each call's argument plumbing exactly (env-derived defaults via the same `_env_int`/`_env_float` names `_cfg_from_env` uses). Do not re-implement any stage logic.

---

### Task 1: `only_keywords` filter on `run_flywheel_stage`

**Files:**
- Modify: `src/agf/workers/main.py` (`run_flywheel_stage`, ~line 484 — locate by content)
- Test: `tests/workers/test_flywheel_stage.py` (append)

**Interfaces:**
- Produces: `run_flywheel_stage(..., only_keywords: list[str] | None = None)`. When set: skip apply_verdicts, mining, mint admission, and retest admission entirely (a scoped test sweep must not mutate the trial pool); fetch `live_keywords()` as usual then filter rows to `kw.keyword in set(only_keywords)`; sweep + `record_keyword_sweep` verdicts still apply to the swept keywords. When None: byte-identical current behavior.

- [ ] **Step 1: Write the failing test** (append to `tests/workers/test_flywheel_stage.py`; reuse `_NoAdExtractor` and the file's conventions):

```python
def test_only_keywords_scopes_the_sweep_and_skips_admission(_migrated_engine):
    # run_stage's --keywords: sweep ONLY the named live keywords; do not mine,
    # mint, or retest-admit (a scoped test run must not mutate the trial pool).
    eng = _migrated_engine

    def _clean():
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.dynamic_keywords "
                              "WHERE keyword LIKE 'onlykw %'"))
    _clean()
    try:
        with eng.begin() as conn:
            add_trial_keywords(conn, ["onlykw target", "onlykw other"])
        ex = _NoAdExtractor()
        corpus = [(41, "the amber kite planner everyone loves"),
                  (42, "the amber kite planner works")]
        stats = run_flywheel_stage(eng, [ex], max_pages=1, kw_delay=0,
                                   min_advertisers=2, retest_per_pass=5,
                                   only_keywords=["onlykw target"],
                                   corpus_fn=lambda conn, min_score: corpus)
        assert ex.searched == ["onlykw target"]      # scoped: nothing else swept
        assert stats["mined"] == [] and stats["retested"] == 0   # pool untouched
        with eng.connect() as conn:
            used = conn.execute(text(
                "SELECT sweeps_used FROM discovery.dynamic_keywords "
                "WHERE keyword='onlykw target'")).scalar()
            other = conn.execute(text(
                "SELECT sweeps_used FROM discovery.dynamic_keywords "
                "WHERE keyword='onlykw other'")).scalar()
        assert used == 1 and other == 0              # verdict recorded only for target
    finally:
        _clean()
```

- [ ] **Step 2: Run it** — `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it pytest tests/workers/test_flywheel_stage.py -q -k only_keywords`. Expected: FAIL (unexpected kwarg).

- [ ] **Step 3: Implement.** In `run_flywheel_stage`: add `only_keywords=None` keyword param. Wrap the pre-loop block (apply_verdicts → mining → add_trial_keywords → retest admission) in `if only_keywords is None:` (when scoped, set `mined=[]`, `retested=0`). After `live = live_keywords(...)`, add:

```python
    if only_keywords is not None:
        wanted = set(only_keywords)
        live = [kw for kw in live if kw.keyword in wanted]
```

Update the docstring with one sentence about the scoped-test mode.

- [ ] **Step 4: Run tests** — same command plus the whole file: `pytest tests/workers/test_flywheel_stage.py -q`. Expected: PASS, all pre-existing tests green.

- [ ] **Step 5: Commit** — `git add src/agf/workers/main.py tests/workers/test_flywheel_stage.py && git commit -m "feat(flywheel): only_keywords scoped-sweep mode for the stage runner"`

---

### Task 2: the `run_stage` CLI module

**Files:**
- Create: `src/agf/workers/run_stage.py`
- Test: `tests/workers/test_run_stage.py` (new)

**Interfaces:**
- Consumes: Task 1's `only_keywords`; `build_extractors`, `run_parallel_sweep`, `run_triage`, `run_flywheel_stage`, `run_funnel_stage`, `refresh_corpus`, `run_generic_deep_stage`, `mint_keywords_from_releases`, `_touch_heartbeat`, `_env_int`/`_env_float`, run-row open/close helpers — all existing.
- Produces: `python -m agf.workers.run_stage {sweep,flywheel,funnel,corpus,generic-deep,mint,triage,compute,score} [options]`; also `main(argv) -> int` for tests, and per-stage `run_<stage>(engine, args) -> dict` adapter functions so tests can call adapters directly with fakes.

Module skeleton (the implementer fleshes out each adapter by mirroring the corresponding `run_once` block — this is the required shape, not pseudocode to improvise around):

```python
"""On-demand single-stage runner (spec: docs/superpowers/specs/2026-07-11-stage-runner-design.md).

Runs ONE pipeline stage through the exact functions run_once uses, date gates
bypassed. TEST MODE by default: no *_runs row is written, so a manual run never
satisfies a daily/weekly gate — pass --record to write the real run row.
Defaults come from the same AGF_* envs the worker reads, so inside the prod
container a bare `run_stage <stage>` is a faithful production pass.

Concurrency: does NOT take the worker daemon's pidfile mutex — a small scoped
run alongside the live worker is fine (DataImpulse rotates IPs per connection);
a full-size manual pass while a tick is sweeping doubles Meta traffic.
"""
import argparse, json, sys, time

def _stage_sweep(engine, args, *, extractor_factory=None): ...
def _stage_flywheel(engine, args, *, extractor_factory=None): ...
# ... one adapter per stage; each returns the stage's stats dict.

STAGES = {"sweep": _stage_sweep, "flywheel": _stage_flywheel, ...}

def main(argv=None) -> int:
    args = _parse(argv)
    engine = get_engine()
    try:
        stats = STAGES[args.stage](engine, args)
    except Exception:
        traceback.print_exc()
        return 1
    print(json.dumps(stats, default=str))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

Adapter requirements (each mirrors its run_once block — read it first):
- **sweep**: `build_extractors(False, int(time.time()), args.workers)` → `run_parallel_sweep(engine, extractors, max_pages=args.pages, kw_delay=..., keywords=args.keywords or None, max_consecutive_errors=..., heartbeat=_touch_heartbeat)`; then `run_triage` with real lookups (httpx client, `lookup_itunes_batch`/`lookup_google_play`) unless `--no-triage`. `--record` wraps in `open_sweep_run`/`close_sweep_run` exactly like run_once (error path closes with `error=repr(exc)` then re-raises).
- **flywheel**: extractors as above (`--workers`, default env `AGF_FLYWHEEL_WORKERS`); `run_flywheel_stage(engine, extractors, max_pages=..., kw_delay=..., max_trials=args.max_trials, mint_per_pass=..., retest_per_pass=..., active_gap_hours=..., max_consecutive_errors=..., heartbeat=_touch_heartbeat, only_keywords=args.keywords or None)`; `--record` → `open_flywheel_run`/`close_flywheel_run`.
- **funnel**: `run_funnel_stage(engine, extractor, max_pages=args.pages, kw_delay=..., confirm_limit=..., deep=args.deep, deep_pages=...)` with a single `build_extractor(False, now)`; `--record` → funnel run helpers.
- **corpus**: `asyncio.run(refresh_corpus(engine, extractor, budget_requests=args.budget, hist_pages=12, offset=args.offset, limit=args.limit, heartbeat=_touch_heartbeat))`; `--record` → corpus run helpers.
- **generic-deep**: mirror the run_once call incl. the `gate=lambda ph, lim: _gate_keywords(engine, ph, limit=lim)` and `max_trials` plumbing; `--record` → generic-deep run helpers.
- **mint**: `mint_keywords_from_releases(engine, http_client, K=..., floor=..., total_slots=args.slots, min_advertisers=..., explore_share=..., autocomplete=not args.no_autocomplete, brainstorm=not args.no_brainstorm, new_release=False)`; `--record` → release-mint run helpers. Needs an httpx client (mirror how main() builds it — grep `httpx.Client` in main.py).
- **triage**: httpx client + `run_triage(conn, lookup_ios=partial(lookup_itunes_batch, client, pause=3.0), lookup_android=lookup_google_play)` in one `begin(engine)` txn. No `--record` (triage has no run table).
- **compute** / **score**: dispatch to the existing standalone entry points the cron uses — grep `ops/prod/crontab` for the exact module/script invocations and `runpy`/subprocess them with absolute interpreter semantics preserved. If an entry point cannot be invoked in-process cleanly, `os.execvp` the same command line the crontab uses.
- Every adapter accepts an injectable `extractor_factory`/client for tests (kwargs with production defaults), so tests never hit the network.

- [ ] **Step 1: Write failing tests** (`tests/workers/test_run_stage.py`, new file — follow the repo's fixture conventions; module docstring; skips cleanly without AGF_TEST_DB_URL like siblings):

```python
"""run_stage CLI: adapters call the SAME stage functions run_once uses, gates
bypassed, no run rows unless --record. Fakes only — zero network."""
import json

from sqlalchemy import text

from agf.workers import run_stage as rs


class _RecordingStage:
    def __init__(self, ret):
        self.calls, self.ret = [], ret
    def __call__(self, *a, **kw):
        self.calls.append((a, kw))
        return self.ret


def test_flywheel_adapter_scopes_and_passes_env_defaults(_migrated_engine, monkeypatch):
    spy = _RecordingStage({"keywords_swept": 1, "errors": 0})
    monkeypatch.setattr(rs, "run_flywheel_stage", spy)
    monkeypatch.setattr(rs, "build_extractors", lambda d, n, k: [object()] * k)
    monkeypatch.setattr(rs, "get_engine", lambda: _migrated_engine)
    monkeypatch.setenv("AGF_FLYWHEEL_MAX_TRIALS", "77")
    rc = rs.main(["flywheel", "--keywords", "a,b"])
    assert rc == 0
    (_, kw), = spy.calls
    assert kw["only_keywords"] == ["a", "b"]
    assert kw["max_trials"] == 77                  # env-true default
    assert kw["heartbeat"] is not None


def test_default_writes_no_run_row_and_record_writes_one(_migrated_engine, monkeypatch, capsys):
    eng = _migrated_engine
    spy = _RecordingStage({"keywords_swept": 0, "errors": 0, "mined": [], "retested": 0})
    monkeypatch.setattr(rs, "run_flywheel_stage", spy)
    monkeypatch.setattr(rs, "build_extractors", lambda d, n, k: [object()])
    monkeypatch.setattr(rs, "get_engine", lambda: eng)
    with eng.connect() as conn:
        before = conn.execute(text("SELECT count(*) FROM discovery.flywheel_runs")).scalar()
    assert rs.main(["flywheel"]) == 0              # default: test mode
    with eng.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM discovery.flywheel_runs")).scalar() == before
    assert rs.main(["flywheel", "--record"]) == 0  # --record: one finished row
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT finished_at, stats FROM discovery.flywheel_runs "
            "ORDER BY id DESC LIMIT 1")).one()
    assert row.finished_at is not None and row.stats is not None
    out = capsys.readouterr().out.strip().splitlines()[-1]
    assert json.loads(out)["keywords_swept"] == 0  # stats JSON on stdout


def test_stage_exception_exits_nonzero_and_records_error_row(_migrated_engine, monkeypatch):
    eng = _migrated_engine

    def _boom(*a, **kw):
        raise RuntimeError("meta throttled")
    monkeypatch.setattr(rs, "run_flywheel_stage", _boom)
    monkeypatch.setattr(rs, "build_extractors", lambda d, n, k: [object()])
    monkeypatch.setattr(rs, "get_engine", lambda: eng)
    assert rs.main(["flywheel"]) == 1              # no row in test mode
    assert rs.main(["flywheel", "--record"]) == 1
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT error FROM discovery.flywheel_runs ORDER BY id DESC LIMIT 1")).one()
    assert "meta throttled" in row.error           # error lands on the row


def test_unknown_stage_is_an_argparse_error():
    try:
        rs.main(["warp-drive"])
        raise AssertionError("should have raised SystemExit")
    except SystemExit as e:
        assert e.code == 2
```

Also one spy test per remaining adapter (sweep incl. `--no-triage`, funnel `--deep`, corpus `--budget/--limit`, generic-deep, mint `--no-autocomplete`, triage) asserting the right stage fn is called with the right kwargs — same `_RecordingStage` pattern, keep each to ~6 lines.

- [ ] **Step 2: Run** — expected FAIL (module missing).
- [ ] **Step 3: Implement `src/agf/workers/run_stage.py`** per the skeleton + adapter requirements. Read each run_once block FIRST and mirror it; read `ops/prod/crontab` for compute/score.
- [ ] **Step 4: Run** `pytest tests/workers/test_run_stage.py tests/workers/ -q` — PASS.
- [ ] **Step 5: Commit** — `git add src/agf/workers/run_stage.py tests/workers/test_run_stage.py && git commit -m "feat(ops): run_stage CLI — on-demand single-stage runs, gates bypassed, test-mode by default"`

---

## Final gates (controller-run)

1. Full suite bare: `AGF_TEST_DB_URL=... pytest -q` → 0 failed.
2. Task review (sonnet) per task + one opus pass over both commits.
3. Deploy: clean-worktree image build → VM roll (worker + cron).
4. Live verify in the container: `run_stage flywheel --keywords "<a live keyword>"` → stats JSON with keywords_swept=1, NO new flywheel_runs row; then `run_stage triage` drains any 'new' backlog. Update memory + grug doc pointer.
