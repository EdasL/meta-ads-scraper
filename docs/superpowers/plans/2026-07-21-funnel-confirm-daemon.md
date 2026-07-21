# Continuous funnel-confirm daemon — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move funnel-confirm off the sweep tick onto a continuous `pipeline_jobs` daemon (kind `funnel_confirm`), copying the keeper-history pattern, so it drains the 24k `new` backlog and keeps pace with inflow without starving the 2-vCPU worker.

**Architecture:** A per-tick producer in `run_once` enqueues due candidate ids as `pipeline_jobs` rows (candidate id in the `advertiser_id` column, hunt-style); an N-lane consumer daemon claims them and runs the one-shot `funnel_confirm_one` CLI per candidate in a subprocess (crash isolation, SIGALRM-bounded). The one-shot reuses the shipped attempt-cap + MMP-recovery + stall-guard logic. The in-tick confirm call is gated behind `AGF_FUNNEL_CONFIRM_INLINE` (default 0).

**Tech Stack:** Python 3.13, SQLAlchemy Core (`text()` + bound params), `pipeline_jobs` queue helpers, Playwright render (`render_and_probe`) + `Extractor.resolve_deeplink`. DB tests gate on `AGF_TEST_DB_URL` (Python) — test DB `agf_it@localhost:55433` (`agf`/`agf`).

## Global Constraints

- **No migration.** `enqueue_jobs` is kind-generic; `confirm_attempts` (migration 159) already exists.
- Candidate id travels in the `pipeline_jobs.advertiser_id` column (the `(kind, advertiser_id)` active-unique index dedups per-kind — safe, ids don't collide across kinds).
- `AGF_FUNNEL_CONFIRM_INLINE` default **0** (daemon owns confirm; in-tick call skipped). `AGF_FUNNEL_CONFIRM_LANES` default **1** (2-vCPU box). `AGF_FUNNEL_CONFIRM_JOBS_PER_TICK` default **200**. `AGF_FUNNEL_CONFIRM_TTL_HOURS` default **6**.
- Keep the funnel SWEEP in the tick unchanged; only the confirm sub-step moves.
- Reuse the shipped `run_funnel_confirm` logic verbatim — do NOT re-implement MMP recovery / attempt-cap.
- Conn-first DB helpers (caller owns txn); all SQL `text()` + bound params; intervals via `make_interval`.
- Product nouns intact: Field Desk, GemScore, cohort.

---

### Task 1: `run_funnel_confirm_one` (efficient single-candidate confirm)

**Why not `run_funnel_confirm(ids=[id], limit=1)`:** `_new_rows` scans ALL `status='new'` rows (24k) then filters in Python — O(backlog) per job, O(backlog²) across the drain. This task extracts the per-candidate body into a helper and adds a single-row fetch.

**Files:**
- Modify: `src/agf/discovery/funnel_confirm.py`
- Test: `tests/discovery/test_funnel_confirm.py`

**Interfaces:**
- Consumes: existing `_is_mmp`, `_resolve_mmp_app`, `_register_probe_failure`, `classify_funnel`, `build_funnel_bridge_row`, `mark_funnel_confirmed`, `upsert_sweep_candidate`, `_DEFAULT_ATTEMPT_CAP`.
- Produces:
  - `_confirm_candidate(engine, r, *, resolve, probe, attempt_cap, stats) -> None` — the extracted per-row body (mutates `stats`).
  - `run_funnel_confirm_one(engine, candidate_id, *, resolve, probe, attempt_cap=None) -> dict` — fetch the single `status='new'` row and confirm it; returns the same stats dict shape.

- [ ] **Step 1: Write the failing test**

Append to `tests/discovery/test_funnel_confirm.py` (reuses the file's `_resolved`, `_probed`, `_ids` helpers and `_migrated_engine`):

```python
from agf.discovery.funnel_confirm import run_funnel_confirm_one

def test_run_funnel_confirm_one_confirms_single_candidate(_migrated_engine):
    eng = _migrated_engine
    from agf.db.funnel_candidates import upsert_funnel_candidate
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.funnel_candidates WHERE page_id='FONE1'"))
        upsert_funnel_candidate(conn, dict(
            page_id="FONE1", page_name="One", page_like_count=1, cta_type="LEARN_MORE",
            utm_source=None, utm_medium=None, utm_campaign=None, utm_content=None,
            ad_start_date=None, source_keyword="q", technique="funnel_phrase",
            funnel_host="quiz.", landing_url="https://fone.io/quiz/start",
            landing_domain="fone.io"))
        cid = conn.execute(text("SELECT id FROM discovery.funnel_candidates WHERE page_id='FONE1'")).scalar_one()
    try:
        stats = run_funnel_confirm_one(
            eng, cid, resolve=lambda _: _resolved("web"),
            probe=lambda _: _probed("<html>x</html>", quiz_steps=2))
        assert stats["examined"] == 1 and stats["confirmed"] == 1
        with eng.connect() as c:
            st = c.execute(text("SELECT status FROM discovery.funnel_candidates WHERE id=:i"), {"i": cid}).scalar()
        assert st == "confirmed"
        # already-processed / non-new id -> no-op, examined 0
        again = run_funnel_confirm_one(eng, cid, resolve=lambda _: _resolved("web"), probe=lambda _: _probed(""))
        assert again["examined"] == 0
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.funnel_candidates WHERE page_id='FONE1'"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/edaslakavicius/Projects/emerging-app-finder && AGF_TEST_DB_URL="postgresql+psycopg://agf:agf@localhost:55433/agf_it" .venv/bin/python -m pytest tests/discovery/test_funnel_confirm.py::test_run_funnel_confirm_one_confirms_single_candidate -q`
Expected: FAIL — `ImportError: cannot import name 'run_funnel_confirm_one'`.

- [ ] **Step 3: Extract `_confirm_candidate` and add `run_funnel_confirm_one`**

In `src/agf/discovery/funnel_confirm.py`, refactor `run_funnel_confirm`'s loop body into `_confirm_candidate`, then add the single-candidate entry. The loop body moves verbatim (MMP branch + resolve + probe + classify), with the MMP-branch `continue` becoming `return`:

```python
def _confirm_candidate(engine, r, *, resolve, probe, attempt_cap, stats) -> None:
    """Confirm ONE funnel-candidate row (mutates stats). Extracted verbatim from the
    run_funnel_confirm loop so the daemon one-shot and the batch path share logic."""
    if _is_mmp(r.landing_domain, r.landing_url):
        store, store_id = _resolve_mmp_app(engine, r)
        if store_id:
            with begin(engine) as conn:
                mark_funnel_confirmed(conn, r.id, kind="install", status="confirmed",
                                      store=store, store_id=store_id, payoff_kind="app")
                bridge_row = build_funnel_bridge_row(r, store=store, store_id=store_id,
                                                     technique="funnel_mmp")
                _, was_new = upsert_sweep_candidate(conn, bridge_row)
                stats["bridged_app_new" if was_new else "bridged_app_seen"] += 1
            stats["confirmed"] += 1
            stats["kinds"]["install"] += 1
            stats["mmp_recovered"] += 1
        else:
            _register_probe_failure(engine, r, stats, attempt_cap)
        return
    try:
        resolved = resolve(r.landing_url)
    except Exception:
        resolved = None
    try:
        probed = probe(r.landing_url)
        html, quiz_steps = probed.landing_html, probed.quiz_steps
    except Exception:
        _register_probe_failure(engine, r, stats, attempt_cap)
        return
    kind, status, store, store_id, reason, payoff_kind = classify_funnel(
        html, resolved, r.funnel_host, quiz_steps)
    with begin(engine) as conn:
        mark_funnel_confirmed(conn, r.id, kind=kind, status=status,
                              store=store, store_id=store_id, drop_reason=reason,
                              quiz_steps=quiz_steps, payoff_kind=payoff_kind)
        if status == "confirmed" and payoff_kind == "app" and store_id:
            bridge_row = build_funnel_bridge_row(r, store=store, store_id=store_id)
            _, was_new = upsert_sweep_candidate(conn, bridge_row)
            stats["bridged_app_new" if was_new else "bridged_app_seen"] += 1
    if status == "confirmed":
        stats["confirmed"] += 1
        stats["kinds"][kind] += 1
    else:
        stats["dropped"] += 1


def _empty_stats(examined: int) -> dict:
    return {"examined": examined, "confirmed": 0, "dropped": 0, "probe_failed": 0,
            "mmp_recovered": 0, "probe_capped": 0,
            "kinds": {"install": 0, "subscribe": 0, "unknown": 0},
            "bridged_app_new": 0, "bridged_app_seen": 0}


def run_funnel_confirm_one(engine, candidate_id, *, resolve, probe,
                           attempt_cap=None) -> dict:
    """Confirm exactly one candidate (the daemon one-shot unit). Fetches only that
    row (no full-'new' scan) and no-ops if it's no longer 'new'."""
    attempt_cap = _DEFAULT_ATTEMPT_CAP if attempt_cap is None else attempt_cap
    with engine.connect() as conn:
        r = conn.execute(text(
            "SELECT id, landing_url, landing_domain, funnel_host, "
            "page_id, page_name, page_like_count, source_keyword, confirm_attempts "
            "FROM discovery.funnel_candidates WHERE id = :i AND status = 'new'"),
            {"i": candidate_id}).first()
    if r is None:
        return _empty_stats(0)
    stats = _empty_stats(1)
    _confirm_candidate(engine, r, resolve=resolve, probe=probe,
                       attempt_cap=attempt_cap, stats=stats)
    return stats
```

Then replace the `run_funnel_confirm` loop body with a call, keeping its `_empty_stats` init:

```python
    stats = _empty_stats(len(rows))
    for r in rows:
        _confirm_candidate(engine, r, resolve=resolve, probe=probe,
                           attempt_cap=attempt_cap, stats=stats)
    return stats
```

(Use `_empty_stats(len(rows))` in place of the inline stats dict literal now in `run_funnel_confirm`.)

- [ ] **Step 4: Run the new test + the whole file to confirm no regression**

Run: `cd /Users/edaslakavicius/Projects/emerging-app-finder && AGF_TEST_DB_URL="postgresql+psycopg://agf:agf@localhost:55433/agf_it" .venv/bin/python -m pytest tests/discovery/test_funnel_confirm.py -q`
Expected: PASS (all prior funnel_confirm tests + the new one — the extraction is behavior-preserving).

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/funnel_confirm.py tests/discovery/test_funnel_confirm.py
git commit -m "refactor(funnel): extract _confirm_candidate + run_funnel_confirm_one"
```

---

### Task 2: `funnel_confirm_one.py` one-shot CLI

**Files:**
- Create: `src/agf/discovery/funnel_confirm_one.py`
- Test: `tests/discovery/test_funnel_confirm_one.py`

**Interfaces:**
- Consumes: `run_funnel_confirm_one` (Task 1); `set_status(engine, job_id, status, *, error_text=None, finished, status_table)` from `agf.enrich_full.enrich_one`; `mark_crawled(conn, entity_type, entity_id, result)`.
- Produces: `run(engine, candidate_id, job_id, *, resolve=None, probe=None) -> int` and `main(argv=None) -> int`.

**Design notes (judgment calls baked in):**
- One candidate per job (not batched) — matches the keeper one-id-per-job model + the dedup index; the chromium cold-start cost is acceptable at ~2k/day.
- `mark_crawled('funnel_confirm', candidate_id, ...)` is called on EVERY completion (not error-only like keeper): a probe-fail *below* the attempt-cap leaves the candidate `status='new'`, so without a TTL marker the producer re-enqueues it every tick (hot-loop). A confirmed/dropped/recovered candidate leaves `'new'` and won't be selected anyway, so the marker is harmless there.

- [ ] **Step 1: Write the failing test**

Create `tests/discovery/test_funnel_confirm_one.py`:

```python
"""One-shot funnel-confirm CLI unit (subprocess unit for funnel_confirm_daemon).
Uses the committing _migrated_engine with injected fake resolve/probe."""
from types import SimpleNamespace
from sqlalchemy import text

from agf.db.engine import begin
from agf.db.pipeline_jobs import enqueue_jobs
from agf.discovery.funnel_confirm_one import run


def _seed(engine):
    from agf.db.funnel_candidates import upsert_funnel_candidate
    with begin(engine) as conn:
        conn.execute(text("DELETE FROM discovery.funnel_candidates WHERE page_id='FC1ONE'"))
        conn.execute(text("DELETE FROM logging.crawl_log WHERE entity_type='funnel_confirm' AND entity_id >= 990000000"))
        upsert_funnel_candidate(conn, dict(
            page_id="FC1ONE", page_name="One", page_like_count=1, cta_type="LEARN_MORE",
            utm_source=None, utm_medium=None, utm_campaign=None, utm_content=None,
            ad_start_date=None, source_keyword="q", technique="funnel_phrase",
            funnel_host="quiz.", landing_url="https://fc1one.io/quiz/start",
            landing_domain="fc1one.io"))
        cid = conn.execute(text("SELECT id FROM discovery.funnel_candidates WHERE page_id='FC1ONE'")).scalar_one()
        enqueue_jobs(conn, "funnel_confirm", [cid])
        jid = conn.execute(text("SELECT id FROM public.pipeline_jobs WHERE kind='funnel_confirm' AND advertiser_id=:c ORDER BY id DESC LIMIT 1"), {"c": cid}).scalar_one()
    return cid, jid


def test_run_marks_job_done_and_crawl_log(_migrated_engine):
    eng = _migrated_engine
    cid, jid = _seed(eng)
    try:
        rc = run(eng, cid, jid,
                 resolve=lambda _: SimpleNamespace(kind="web", store_id=None),
                 probe=lambda _: SimpleNamespace(landing_html="<html>x</html>", quiz_steps=2))
        assert rc == 0
        with eng.connect() as c:
            assert c.execute(text("SELECT status FROM public.pipeline_jobs WHERE id=:j"), {"j": jid}).scalar() == "done"
            assert c.execute(text("SELECT status FROM discovery.funnel_candidates WHERE id=:i"), {"i": cid}).scalar() == "confirmed"
            mk = c.execute(text("SELECT crawl_result FROM logging.crawl_log WHERE entity_type='funnel_confirm' AND entity_id=:i ORDER BY crawled_at DESC LIMIT 1"), {"i": cid}).scalar()
        assert mk is not None
    finally:
        with begin(eng) as conn:
            conn.execute(text("DELETE FROM public.pipeline_jobs WHERE kind='funnel_confirm' AND advertiser_id=:c"), {"c": cid})
            conn.execute(text("DELETE FROM discovery.funnel_candidates WHERE id=:i"), {"i": cid})
            conn.execute(text("DELETE FROM logging.crawl_log WHERE entity_type='funnel_confirm' AND entity_id=:i"), {"i": cid})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/edaslakavicius/Projects/emerging-app-finder && AGF_TEST_DB_URL="postgresql+psycopg://agf:agf@localhost:55433/agf_it" .venv/bin/python -m pytest tests/discovery/test_funnel_confirm_one.py -q`
Expected: FAIL — module `funnel_confirm_one` does not exist.

- [ ] **Step 3: Create the CLI**

Create `src/agf/discovery/funnel_confirm_one.py` (copies `keeper_one.py`'s shape — SIGALRM, `set_status(status_table="pipeline_jobs")`, `mark_crawled`; wires resolve/probe exactly as `run_funnel_stage` in `main.py:401-406`):

```python
"""One-shot funnel-confirm (subprocess unit for the funnel_confirm daemon).

    python -m agf.discovery.funnel_confirm_one --candidate-id N --job-id M

Confirms ONE funnel candidate via run_funnel_confirm_one (shipped attempt-cap +
MMP recovery + stall-guard logic), writes the terminal status onto its
pipeline_jobs row, and marks logging.crawl_log (entity_type='funnel_confirm') so
funnel_confirm_due TTL-suppresses a candidate that stayed 'new' (probe failed
below the cap). SIGALRM bounds the render like keeper_one/enrich_one."""
from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys

from agf.db.engine import begin, get_engine
from agf.discovery.funnel_confirm import run_funnel_confirm_one
from agf.enrich_full.enrich_one import set_status
from agf.workers.scheduler import mark_crawled

_KIND = "funnel_confirm"


def run(engine, candidate_id: int, job_id: int, *, resolve=None, probe=None) -> int:
    # Wire resolve/probe exactly as run_funnel_stage does (main.py). A SIGALRM firing
    # before the try below is backstopped by the daemon's still-running safety net.
    if resolve is None:
        from agf.extract.facade import Extractor
        extractor = Extractor()
        def resolve(url):
            return asyncio.run(extractor.resolve_deeplink(url)).intent
    if probe is None:
        from agf.enrich_full.funnel_capture import render_and_probe
        probe = render_and_probe
    try:
        run_funnel_confirm_one(engine, candidate_id, resolve=resolve, probe=probe)
    except Exception as e:
        set_status(engine, job_id, "error", error_text=repr(e)[:500],
                   finished=True, status_table="pipeline_jobs")
        with begin(engine) as conn:
            mark_crawled(conn, _KIND, candidate_id, f"error:{type(e).__name__}"[:64])
        return 1
    set_status(engine, job_id, "done", finished=True, status_table="pipeline_jobs")
    # Always mark: a candidate that stayed 'new' (below-cap probe fail) needs the TTL
    # cooldown; a confirmed/dropped one left 'new' and won't be re-selected anyway.
    with begin(engine) as conn:
        mark_crawled(conn, _KIND, candidate_id, "ok")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--candidate-id", type=int, required=True)
    p.add_argument("--job-id", type=int, required=True)
    p.add_argument("--timeout", type=int,
                   default=int(os.environ.get("AGF_FUNNEL_CONFIRM_ONE_TIMEOUT_SEC", "90")))
    args = p.parse_args(argv)
    engine = get_engine()

    def _alarm(signum, frame):
        raise TimeoutError(f"funnel_confirm_one exceeded {args.timeout}s")
    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(args.timeout)
    return run(engine, args.candidate_id, args.job_id)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/edaslakavicius/Projects/emerging-app-finder && AGF_TEST_DB_URL="postgresql+psycopg://agf:agf@localhost:55433/agf_it" .venv/bin/python -m pytest tests/discovery/test_funnel_confirm_one.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/funnel_confirm_one.py tests/discovery/test_funnel_confirm_one.py
git commit -m "feat(funnel): funnel_confirm_one one-shot CLI (pipeline_jobs unit)"
```

---

### Task 3: `funnel_confirm_daemon.py` consumer

**Files:**
- Create: `src/agf/discovery/funnel_confirm_daemon.py`
- Test: `tests/discovery/test_funnel_confirm_daemon.py`

**Interfaces:**
- Consumes: `claim_next`, `reap_expired`, `set_job_status` (`agf.db.pipeline_jobs`); `mark_crawled`.
- Produces: `run_funnel_confirm_subprocess(candidate_id, job_id, kind='funnel_confirm')`, `process_once(engine, *, runner=...) -> bool`, `main()`.

This is `keeper_daemon.py` with a SINGLE kind and no corpus brake. Copy its structure exactly, dropping the multi-kind loop and `_corpus_paused`.

- [ ] **Step 1: Write the failing test**

Create `tests/discovery/test_funnel_confirm_daemon.py` (mirrors `test_keeper_daemon.py`; `advertiser_id` column holds the candidate id):

```python
"""Single-lane funnel-confirm consumer (pipeline_jobs, kind='funnel_confirm').
Candidate id travels in the advertiser_id column. Injects a fake runner."""
import pytest
from sqlalchemy import text

from agf.db.engine import begin
from agf.db.pipeline_jobs import enqueue_jobs
from agf.enrich_full.enrich_one import set_status

SENTINEL = 990_000_000
C1 = SENTINEL + 1


def _clean(engine):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM public.pipeline_jobs WHERE kind='funnel_confirm'"))
        conn.execute(text("DELETE FROM logging.crawl_log WHERE entity_type='funnel_confirm' AND entity_id >= :s"), {"s": SENTINEL})


@pytest.fixture
def engine(_migrated_engine):
    _clean(_migrated_engine)
    try:
        yield _migrated_engine
    finally:
        _clean(_migrated_engine)


def _seed(engine, cid):
    with begin(engine) as conn:
        enqueue_jobs(conn, "funnel_confirm", [cid])
        return conn.execute(text("SELECT id FROM public.pipeline_jobs WHERE kind='funnel_confirm' AND advertiser_id=:c ORDER BY id DESC LIMIT 1"), {"c": cid}).scalar_one()


def _status(engine, cid):
    with engine.connect() as conn:
        return conn.execute(text("SELECT status FROM public.pipeline_jobs WHERE kind='funnel_confirm' AND advertiser_id=:c"), {"c": cid}).scalar()


def test_process_once_claims_and_runs(engine):
    jid = _seed(engine, C1)
    calls = []
    def fake_runner(candidate_id, job_id, kind):
        calls.append((candidate_id, job_id, kind))
        set_status(engine, job_id, "done", finished=True, status_table="pipeline_jobs")
    from agf.discovery.funnel_confirm_daemon import process_once
    assert process_once(engine, runner=fake_runner) is True
    assert calls == [(C1, jid, "funnel_confirm")]
    assert _status(engine, C1) == "done"


def test_process_once_safety_net_marks_error_and_crawl_log(engine):
    _seed(engine, C1)
    def dead_runner(candidate_id, job_id, kind):
        pass
    from agf.discovery.funnel_confirm_daemon import process_once
    assert process_once(engine, runner=dead_runner) is True
    assert _status(engine, C1) == "error"
    with engine.connect() as conn:
        r = conn.execute(text("SELECT crawl_result FROM logging.crawl_log WHERE entity_type='funnel_confirm' AND entity_id=:c ORDER BY crawled_at DESC LIMIT 1"), {"c": C1}).scalar()
    assert r is not None and r.startswith("error:")


def test_process_once_empty_queue_returns_false(engine):
    from agf.discovery.funnel_confirm_daemon import process_once
    assert process_once(engine, runner=lambda c, j, k: None) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/edaslakavicius/Projects/emerging-app-finder && AGF_TEST_DB_URL="postgresql+psycopg://agf:agf@localhost:55433/agf_it" .venv/bin/python -m pytest tests/discovery/test_funnel_confirm_daemon.py -q`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Create the daemon**

Create `src/agf/discovery/funnel_confirm_daemon.py`:

```python
"""Single-kind funnel-confirm consumer (worker VM). Copies keeper_daemon.py but
drains public.pipeline_jobs kind='funnel_confirm' only (candidate id in the
advertiser_id column). Each job runs the funnel_confirm_one CLI in a subprocess
(crash isolation); the one-shot writes its own terminal status + crawl_log marker.
The daemon adds a safety net for a subprocess that dies before recording anything,
and relies on reap_expired for one that wedges past its lease. Purge is not
duplicated (pipeline_daemon._maybe_purge is kind-blind). Default 1 lane — the VM is
2 vCPU and one continuous render lane keeps pace with ~800/day inflow + drains the
backlog without starving the worker."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time

from sqlalchemy import text

from agf.db.engine import begin, get_engine
from agf.db.pipeline_jobs import claim_next, reap_expired, set_job_status
from agf.workers.scheduler import mark_crawled

log = logging.getLogger("agf.funnel_confirm")

KIND = "funnel_confirm"
ONE_TIMEOUT_SEC = int(os.environ.get("AGF_FUNNEL_CONFIRM_ONE_TIMEOUT_SEC", "90"))
POLL_SEC = float(os.environ.get("AGF_FUNNEL_CONFIRM_POLL_SEC", "5"))
LEASE_SEC = ONE_TIMEOUT_SEC + 120
LANES = int(os.environ.get("AGF_FUNNEL_CONFIRM_LANES", "1"))


def run_funnel_confirm_subprocess(candidate_id: int, job_id: int,
                                  kind: str = KIND) -> None:
    cmd = [sys.executable, "-m", "agf.discovery.funnel_confirm_one",
           "--candidate-id", str(candidate_id),
           "--job-id", str(job_id),
           "--timeout", str(ONE_TIMEOUT_SEC)]
    subprocess.run(cmd, timeout=ONE_TIMEOUT_SEC + 60, check=False)


def process_once(engine, *, runner=run_funnel_confirm_subprocess) -> bool:
    """One poll tick: reap expired leases, claim + run at most one funnel_confirm
    job. Returns True if a job was claimed (poll again immediately)."""
    claimed = None
    with begin(engine) as conn:
        reaped = reap_expired(conn, KIND)
        if reaped:
            log.warning("reaped %d expired %s lease(s)", reaped, KIND)
        row = claim_next(conn, KIND, lease_sec=LEASE_SEC)
        if row is not None:
            claimed = (row[0], row[1])
    if claimed is None:
        return False
    job_id, candidate_id = claimed
    log.info("running %s job %d (candidate %d)", KIND, job_id, candidate_id)
    err: str | None = None
    try:
        runner(candidate_id, job_id, KIND)
    except Exception as e:
        log.exception("%s job %d runner failed", KIND, job_id)
        err = str(e)[:500]
    with begin(engine) as conn:
        still_running = conn.execute(text(
            "SELECT 1 FROM public.pipeline_jobs WHERE id = :i AND status = 'running'"),
            {"i": job_id}).scalar()
        if still_running:
            set_job_status(conn, job_id, "error",
                           error=err or "confirm exited without recording status")
            mark_crawled(conn, KIND, candidate_id,
                         "error:runner_killed" if err else "error:no_status")
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    engine = get_engine()
    log.info("funnel_confirm daemon up (%d lane(s), poll %.1fs, lease %ds)",
             LANES, POLL_SEC, LEASE_SEC)

    def lane():
        while True:
            try:
                busy = process_once(engine)
            except Exception:
                log.exception("funnel_confirm lane tick failed")
                busy = False
            if not busy:
                time.sleep(POLL_SEC)

    threads = [threading.Thread(target=lane, daemon=True) for _ in range(max(1, LANES))]
    for t in threads:
        t.start()
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/edaslakavicius/Projects/emerging-app-finder && AGF_TEST_DB_URL="postgresql+psycopg://agf:agf@localhost:55433/agf_it" .venv/bin/python -m pytest tests/discovery/test_funnel_confirm_daemon.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/funnel_confirm_daemon.py tests/discovery/test_funnel_confirm_daemon.py
git commit -m "feat(funnel): funnel_confirm_daemon — single-kind pipeline_jobs consumer"
```

---

### Task 4: producer in `run_once` + `funnel_confirm_due` + INLINE gate

**Files:**
- Modify: `src/agf/discovery/funnel_confirm.py` (add `funnel_confirm_due`)
- Modify: `src/agf/workers/main.py` (producer block + gate the in-tick confirm)
- Test: `tests/discovery/test_funnel_confirm.py` (add `funnel_confirm_due` cases)

**Interfaces:**
- Produces: `funnel_confirm_due(conn, *, ttl_hours=6, limit=200) -> list[int]` — candidate ids `status='new'`, minus those with a `logging.crawl_log entity_type='funnel_confirm'` row fresher than `ttl_hours`, oldest first, bounded by limit.
- Consumes in main.py: `enqueue_jobs(conn, "funnel_confirm", ids)` (already imported), `_env_int`.

- [ ] **Step 1: Write the failing test for `funnel_confirm_due`**

Append to `tests/discovery/test_funnel_confirm.py`:

```python
from agf.discovery.funnel_confirm import funnel_confirm_due

def test_funnel_confirm_due_skips_ttl_marked(_migrated_engine):
    eng = _migrated_engine
    from agf.db.funnel_candidates import upsert_funnel_candidate
    from agf.workers.scheduler import mark_crawled
    base = dict(page_name="Due", page_like_count=1, cta_type="LEARN_MORE",
                utm_source=None, utm_medium=None, utm_campaign=None, utm_content=None,
                ad_start_date=None, source_keyword="q", technique="funnel_phrase", funnel_host=None)
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.funnel_candidates WHERE page_id LIKE 'FDUE%'"))
        for n in (1, 2):
            upsert_funnel_candidate(conn, {**base, "page_id": f"FDUE{n}",
                "landing_url": f"https://fdue{n}.io/s", "landing_domain": f"fdue{n}.io"})
        ids = [r.id for r in conn.execute(text("SELECT id FROM discovery.funnel_candidates WHERE page_id LIKE 'FDUE%' ORDER BY page_id")).all()]
        mark_crawled(conn, "funnel_confirm", ids[0], "ok")   # fresh marker -> suppressed
    try:
        with eng.connect() as conn:
            due = funnel_confirm_due(conn, ttl_hours=6, limit=200)
        assert ids[1] in due and ids[0] not in due
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.funnel_candidates WHERE page_id LIKE 'FDUE%'"))
            conn.execute(text("DELETE FROM logging.crawl_log WHERE entity_type='funnel_confirm' AND entity_id = ANY(CAST(:a AS bigint[]))"), {"a": ids})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/edaslakavicius/Projects/emerging-app-finder && AGF_TEST_DB_URL="postgresql+psycopg://agf:agf@localhost:55433/agf_it" .venv/bin/python -m pytest tests/discovery/test_funnel_confirm.py::test_funnel_confirm_due_skips_ttl_marked -q`
Expected: FAIL — `funnel_confirm_due` not defined.

- [ ] **Step 3: Add `funnel_confirm_due` (models `keepers_due_for_history`)**

Add to `src/agf/discovery/funnel_confirm.py`:

```python
def funnel_confirm_due(conn, *, ttl_hours: int = 6, limit: int = 200) -> list[int]:
    """Candidate ids status='new' minus those with a logging.crawl_log row of
    entity_type='funnel_confirm' fresher than ttl_hours (so a candidate that stayed
    'new' after a below-cap probe fail waits out the TTL instead of hot-looping the
    queue). Oldest first, bounded by limit. A confirmed/dropped candidate leaves
    'new' and drops out of the base query — no success bookkeeping needed here."""
    return [r[0] for r in conn.execute(text("""
        SELECT fc.id
        FROM discovery.funnel_candidates fc
        WHERE fc.status = 'new'
          AND NOT EXISTS (SELECT 1 FROM logging.crawl_log l
                          WHERE l.entity_type = 'funnel_confirm'
                            AND l.entity_id = fc.id
                            AND l.crawled_at > now() - make_interval(hours => :ttl))
        ORDER BY fc.first_seen_at, fc.id
        LIMIT :lim
    """), {"ttl": ttl_hours, "lim": limit}).all()]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/edaslakavicius/Projects/emerging-app-finder && AGF_TEST_DB_URL="postgresql+psycopg://agf:agf@localhost:55433/agf_it" .venv/bin/python -m pytest tests/discovery/test_funnel_confirm.py::test_funnel_confirm_due_skips_ttl_marked -q`
Expected: PASS.

- [ ] **Step 5: Add the producer block + gate the in-tick confirm in `main.py`**

In `src/agf/workers/main.py`:

(a) Add the import alongside the existing funnel_confirm import (near line 121):
```python
from agf.discovery.funnel_confirm import (run_funnel_confirm, _confirm_looks_stalled,
                                          funnel_confirm_due)
```

(b) In `run_funnel_stage`, gate the in-tick confirm (replace lines 436-445, the `confirm = run_funnel_confirm(...)` through the stall-guard `print`) with an inline-only guard. `run_funnel_stage` gains a `confirm_inline: bool = True` parameter (add it to the signature at line 388):
```python
    if confirm_inline:
        confirm = run_funnel_confirm(engine, resolve=resolve, probe=probe,
                                     limit=confirm_limit)
        _touch_heartbeat()
        if _confirm_looks_stalled(confirm):
            print(f"[worker] funnel confirm STALLED: {confirm['probe_failed']}/{confirm['examined']} "
                  f"probes failed, 0 confirmed — landing render likely broken",
                  file=sys.stderr, flush=True)
    else:
        confirm = {"skipped": "daemon"}
    return {"sweep": sweep, "deep": deep_stats, "confirm": confirm}
```

(c) At the `run_funnel_stage(...)` call site in `run_once` (line 770), pass the gate:
```python
                funnel = run_funnel_stage(engine, extractor, max_pages=funnel_pages,
                                          kw_delay=kw_delay,
                                          confirm_limit=funnel_confirm_limit,
                                          confirm_inline=_env_int("AGF_FUNNEL_CONFIRM_INLINE", 0) == 1,
                                          deep=deep_due, deep_pages=funnel_deep_pages)
```

(d) Add the producer block in `run_once`, modeled on the keeper block (after the keeper enqueue block, ~line 745), so it runs every tick regardless of the funnel daily gate:
```python
    # 1.85) funnel confirm — CONTINUOUS: each tick enqueues due 'new' funnel
    #       candidates (kind='funnel_confirm') and the funnel_confirm daemon drains
    #       them off the tick (design 2026-07-20). The in-tick confirm stays behind
    #       AGF_FUNNEL_CONFIRM_INLINE=1 as a fallback. Failure-isolated from the tick.
    funnel_confirm_enqueued = 0
    if not dry_run and _env_int("AGF_FUNNEL_CONFIRM_INLINE", 0) == 0:
        try:
            with begin(engine) as conn:
                due_ids = funnel_confirm_due(
                    conn, ttl_hours=_env_int("AGF_FUNNEL_CONFIRM_TTL_HOURS", 6),
                    limit=_env_int("AGF_FUNNEL_CONFIRM_JOBS_PER_TICK", 200))
                funnel_confirm_enqueued = enqueue_jobs(conn, "funnel_confirm", due_ids)
            if funnel_confirm_enqueued:
                print(f"[worker] funnel_confirm enqueued {funnel_confirm_enqueued}", flush=True)
        except Exception as exc:
            print(f"[worker] funnel_confirm enqueue failed: {exc!r}", file=sys.stderr, flush=True)
```

- [ ] **Step 6: Verify the funnel-stage tests + a syntax/import check**

Run: `cd /Users/edaslakavicius/Projects/emerging-app-finder && AGF_TEST_DB_URL="postgresql+psycopg://agf:agf@localhost:55433/agf_it" .venv/bin/python -m pytest tests/workers/test_funnel_stage.py tests/discovery/test_funnel_confirm.py -q && .venv/bin/python -m py_compile src/agf/workers/main.py`
Expected: PASS; compile clean. (If `test_funnel_stage.py` calls `run_funnel_stage` without `confirm_inline`, the default `True` preserves current behavior — confirm the existing tests still pass.)

- [ ] **Step 7: Commit**

```bash
git add src/agf/discovery/funnel_confirm.py src/agf/workers/main.py tests/discovery/test_funnel_confirm.py
git commit -m "feat(funnel): continuous confirm producer + AGF_FUNNEL_CONFIRM_INLINE gate"
```

---

### Task 5: prod compose `funnel-confirm` service

**Files:**
- Modify: `ops/prod/docker-compose.yml`

**Note:** config-only; verified with `docker compose config`, not a unit test. The service uses the shared worker image (already carries Playwright chromium).

- [ ] **Step 1: Add the service** (after the `keeper` block, before `hunt`):

```yaml
  # Funnel-confirm consumer: drains kind='funnel_confirm' pipeline_jobs (the
  # candidate id travels in the advertiser_id column). Single render lane — the VM
  # is 2 vCPU and one continuous lane keeps pace with inflow + drains the backlog
  # without starving the worker. Needs the Playwright/chromium runtime (funnel
  # render) + proxies (MMP deeplink resolve). design 2026-07-20.
  funnel-confirm:
    image: europe-west1-docker.pkg.dev/peon-489013/agf/worker:latest
    restart: unless-stopped
    init: true
    command: python -m agf.discovery.funnel_confirm_daemon
    environment:
      AGF_DATABASE_URL: ${AGF_DATABASE_URL}
      AGF_PROXIES: ${AGF_PROXIES}
      AGF_FUNNEL_CONFIRM_LANES: ${AGF_FUNNEL_CONFIRM_LANES:-1}
      AGF_FUNNEL_CONFIRM_ONE_TIMEOUT_SEC: ${AGF_FUNNEL_CONFIRM_ONE_TIMEOUT_SEC:-90}
      AGF_FUNNEL_CONFIRM_POLL_SEC: ${AGF_FUNNEL_CONFIRM_POLL_SEC:-5}
    depends_on:
      cloud-sql-proxy:
        condition: service_started
```

- [ ] **Step 2: Validate compose**

Run: `cd /Users/edaslakavicius/Projects/emerging-app-finder && docker compose -f ops/prod/docker-compose.yml config >/dev/null && echo "compose OK"`
Expected: `compose OK` (no YAML/interpolation errors).

- [ ] **Step 3: Commit**

```bash
git add ops/prod/docker-compose.yml
git commit -m "ops(funnel): add funnel-confirm daemon service (prod compose)"
```

---

## Deploy (after all tasks green)

1. Build the worker image (`bash ops/prod/02_build_image.sh`).
2. On the VM: `sudo docker compose -f /opt/agf/docker-compose.yml pull worker cron funnel-confirm && sudo docker compose -f /opt/agf/docker-compose.yml up -d worker cron funnel-confirm`.
3. `AGF_FUNNEL_CONFIRM_INLINE` defaults to 0 → the producer enqueues and the daemon drains; the in-tick confirm is off. (Set `=1` on the worker only as a fallback if the daemon is down.)
4. Watch: `pipeline_jobs` queued/done for kind='funnel_confirm', `discovery.funnel_candidates` status flow off `new`, `frontend.emerging_scaling_apps` web_sub adds, and the hunt web cards lighting up (feature 2026-07-21). Backlog ~24k drains at ~2k/day.

## Self-Review

**Spec coverage:** run_funnel_confirm_one (T1) ✓; funnel_confirm_one.py (T2) ✓; funnel_confirm_daemon.py (T3) ✓; producer + INLINE gate (T4) ✓; compose service (T5) ✓; no migration ✓; candidate-id-in-advertiser_id ✓ (T2/T3 tests seed via `enqueue_jobs(..., [cid])`, claim returns it as `advertiser_id`); TTL suppression ✓ (T4 `funnel_confirm_due` + T2 always-mark). Batch-size decision resolved to one-per-job (deviates from the spec's "batch of 10" recommendation — noted).

**Placeholder scan:** none — every step carries real code + exact commands.

**Type consistency:** `run_funnel_confirm_one(engine, candidate_id, *, resolve, probe, attempt_cap=None) -> dict` defined T1, called T2. `funnel_confirm_due(conn, *, ttl_hours, limit) -> list[int]` defined T4, called T4 producer. `process_once(engine, *, runner) -> bool` and `run_funnel_confirm_subprocess(candidate_id, job_id, kind)` consistent across T3 test + daemon. `set_status(..., status_table="pipeline_jobs")` matches keeper_one usage. `mark_crawled(conn, entity_type, entity_id, result)` matches scheduler.py.

**Scope:** single feature (one daemon), five tasks, one plan.
