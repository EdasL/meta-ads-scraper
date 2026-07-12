# Keeper Absorption at Discovery Speed — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** kept candidates become scored, user-visible database rows continuously (≥1,000/day capacity, ≤1h found→visible) instead of 25/day once daily; the 2,059-keeper backlog drains through the same path.

**Architecture:** new `keeper_history` job kind on the existing `public.pipeline_jobs` queue (no migration — the table is kind-generic). The worker tick enqueues due keepers each pass; a new keeper daemon (compose service on the worker VM, N=3 subprocess lanes) drains them by running the existing `refresh_one_advertiser` (12 Meta pages: ads + 16-week series) via a tiny `keeper_one` CLI. Failure marks `logging.crawl_log` under a distinct `entity_type='keeper_history'` so a permanently-failing keeper is TTL-suppressed, never hot-looped. The old in-tick `pull_keeper_history` stage is disabled by default behind a flag; its budget formula is left untouched so gem enrich keeps its current rate.

**Tech Stack:** Python 3.13, SQLAlchemy + psycopg3, pytest. DB tests REQUIRE `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it` (unset ⇒ they SKIP silently — a skip is NOT a pass).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-12-keeper-absorption-10k-design.md`
- No keyword/mint/triage/veto/GemScore/UI changes.
- No migration files; if one becomes necessary, NO bare `%` anywhere in it (incl. comments).
- Conn-first DB helpers (caller owns txn), `text()` + bound params, `make_interval` for intervals.
- Commit per task with explicit paths (never `git add -A`).
- Full suite must end 0 failed: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it pytest -q` — run bare, own Bash call, read the summary line.
- New env knobs: `AGF_KEEPER_LANES` (3), `AGF_KEEPER_ONE_TIMEOUT_SEC` (120), `AGF_KEEPER_ENQUEUE_PER_TICK` (1000), `AGF_KEEPER_TTL_HOURS` (168), `AGF_KEEPER_HISTORY_INLINE` (0).

---

### Task 1: Kind-generic enqueue + due-keeper selection

**Files:**
- Modify: `src/agf/db/pipeline_jobs.py` (generalize enqueue)
- Modify: `src/agf/discovery/keeper_history.py` (add `keepers_due_for_history`)
- Test: `tests/db/test_pipeline_jobs.py` (extend existing; create if the module's tests live elsewhere — grep `enqueue_enrich` in tests/ first and extend THAT file)
- Test: `tests/discovery/test_keeper_history.py` (extend)

**Interfaces:**
- Consumes: existing `enqueue_enrich(conn, advertiser_ids)`, `keepers_missing_history(conn)`, `logging.crawl_log` (`entity_type, entity_id, crawled_at, crawl_result`).
- Produces: `enqueue_jobs(conn, kind: str, advertiser_ids) -> int` and `keepers_due_for_history(conn, *, ttl_hours: int = 168, limit: int = 1000) -> list[tuple[int, str]]` (advertiser_id, page_id).

- [ ] **Step 1: Write failing tests**

```python
# tests/db/test_pipeline_jobs.py (add)
def test_enqueue_jobs_keeper_kind_dedups_active(db_conn, seeded_advertiser_ids):
    a1, a2 = seeded_advertiser_ids[:2]
    from agf.db.pipeline_jobs import enqueue_jobs
    assert enqueue_jobs(db_conn, "keeper_history", [a1, a2]) == 2
    # second enqueue while queued dedups
    assert enqueue_jobs(db_conn, "keeper_history", [a1]) == 0
    kinds = db_conn.execute(text(
        "SELECT DISTINCT kind FROM public.pipeline_jobs WHERE advertiser_id = ANY(:a)"),
        {"a": [a1, a2]}).scalars().all()
    assert kinds == ["keeper_history"]

def test_enqueue_enrich_still_works(db_conn, seeded_advertiser_ids):
    from agf.db.pipeline_jobs import enqueue_enrich
    assert enqueue_enrich(db_conn, [seeded_advertiser_ids[2]]) == 1
```

```python
# tests/discovery/test_keeper_history.py (add)
def test_keepers_due_excludes_fresh_crawl_log(db_conn, seeded_keeper_no_history):
    adv_id = seeded_keeper_no_history
    from agf.discovery.keeper_history import keepers_due_for_history
    due = [a for a, _pid in keepers_due_for_history(db_conn)]
    assert adv_id in due
    from agf.workers.scheduler import mark_crawled
    mark_crawled(db_conn, "keeper_history", adv_id, "error:ExtractError")
    due = [a for a, _pid in keepers_due_for_history(db_conn)]
    assert adv_id not in due          # TTL suppression
    due = [a for a, _pid in keepers_due_for_history(db_conn, ttl_hours=0)]
    assert adv_id in due              # ttl 0 = everything due

def test_keepers_due_respects_limit(db_conn, seeded_keeper_no_history):
    from agf.discovery.keeper_history import keepers_due_for_history
    assert len(keepers_due_for_history(db_conn, limit=1)) <= 1
```

Fixtures: follow the file's existing seed helpers (it already seeds keeper candidates + advertisers for `keepers_missing_history` tests — reuse the same INSERT pattern, sentinel ids, rolled-back txn).

- [ ] **Step 2: Run to verify failure**

`AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it pytest tests/db/test_pipeline_jobs.py tests/discovery/test_keeper_history.py -q` — expect FAIL: `ImportError: cannot import name 'enqueue_jobs'` / `'keepers_due_for_history'`. Confirm they FAIL, not SKIP.

- [ ] **Step 3: Implement**

```python
# src/agf/db/pipeline_jobs.py — replace enqueue_enrich body with a generic core
def enqueue_jobs(conn, kind: str, advertiser_ids) -> int:
    """Bulk-insert `kind` rows for `advertiser_ids` IN THE GIVEN ORDER (FIFO drain
    order). ON CONFLICT against the partial active-unique index dedups any
    advertiser that already has a queued/running job of this kind. Returns the
    count actually inserted."""
    ids = list(advertiser_ids)
    if not ids:
        return 0
    rows = conn.execute(text(
        "INSERT INTO public.pipeline_jobs (kind, advertiser_id) "
        "SELECT :kind, a.aid "
        "FROM unnest(CAST(:aids AS bigint[])) WITH ORDINALITY AS a(aid, ord) "
        "ORDER BY a.ord "
        "ON CONFLICT (kind, advertiser_id) WHERE status IN ('queued','running') "
        "DO NOTHING "
        "RETURNING id"),
        {"kind": kind, "aids": ids}).fetchall()
    return len(rows)


def enqueue_enrich(conn, advertiser_ids) -> int:
    """kind='enrich' wrapper — keeps every existing call site unchanged."""
    return enqueue_jobs(conn, "enrich", advertiser_ids)
```

```python
# src/agf/discovery/keeper_history.py — add below keepers_missing_history
def keepers_due_for_history(conn, *, ttl_hours: int = 168,
                            limit: int = 1000) -> list[tuple[int, str]]:
    """keepers_missing_history minus advertisers with a logging.crawl_log row of
    entity_type='keeper_history' fresher than ttl_hours (failure suppression: a
    keeper whose pull errored waits out the TTL instead of hot-looping the
    queue). Oldest triaged first, bounded by limit. Success needs no marker —
    a pulled keeper gains metrics rows and drops out of the base query."""
    return [(r[0], r[1]) for r in conn.execute(text("""
        SELECT c.kept_advertiser_id, a.page_id
        FROM discovery.candidates c
        JOIN public.advertisers a ON a.id = c.kept_advertiser_id
        WHERE c.status = 'kept' AND c.kept_advertiser_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM adtech.app_advertiser_metrics m
                          WHERE m.advertiser_id = c.kept_advertiser_id)
          AND NOT EXISTS (SELECT 1 FROM logging.crawl_log l
                          WHERE l.entity_type = 'keeper_history'
                            AND l.entity_id = c.kept_advertiser_id
                            AND l.crawled_at > now() - make_interval(hours => :ttl))
        GROUP BY c.kept_advertiser_id, a.page_id
        ORDER BY MIN(c.triaged_at), c.kept_advertiser_id
        LIMIT :lim
    """), {"ttl": ttl_hours, "lim": limit}).all()]
```

- [ ] **Step 4: Run to verify pass** — same command, expect all green, 0 skipped among the new tests.

- [ ] **Step 5: Commit**

```bash
git add src/agf/db/pipeline_jobs.py src/agf/discovery/keeper_history.py tests/db/test_pipeline_jobs.py tests/discovery/test_keeper_history.py
git commit -m "feat(pipeline): kind-generic enqueue + due-keeper selection with TTL failure suppression"
```

---

### Task 2: `keeper_one` CLI

**Files:**
- Create: `src/agf/discovery/keeper_one.py`
- Test: `tests/discovery/test_keeper_one.py`

**Interfaces:**
- Consumes: `refresh_one_advertiser(engine, extractor, advertiser_id, page_id, *, hist_pages=12, weeks=16, flip_vanished=False)` (async, raises `ExtractError`); `set_status(engine, job_id, status, *, error_text=None, finished=False, status_table="pipeline_jobs")` from `agf.enrich_full.enrich_one`; `mark_crawled(conn, entity_type, entity_id, result)`.
- Produces: CLI `python -m agf.discovery.keeper_one --advertiser-id N --job-id M [--timeout S]`; testable `run(engine, advertiser_id, job_id, *, refresh=refresh_one_advertiser, extractor=None) -> int` (0 ok / 1 error).

- [ ] **Step 1: Write failing tests**

```python
# tests/discovery/test_keeper_one.py
import pytest
from agf.extract.errors import ExtractError

def _seed_job(conn, adv_id):
    from agf.db.pipeline_jobs import enqueue_jobs
    enqueue_jobs(conn, "keeper_history", [adv_id])
    return conn.execute(text(
        "SELECT id FROM public.pipeline_jobs WHERE kind='keeper_history' "
        "AND advertiser_id=:a ORDER BY id DESC LIMIT 1"), {"a": adv_id}).scalar_one()

def test_run_success_marks_done(db_engine, seeded_keeper_no_history):
    adv = seeded_keeper_no_history
    with begin(db_engine) as conn:
        job = _seed_job(conn, adv)
    async def fake_refresh(engine, extractor, advertiser_id, page_id, **kw):
        return {"ads_stored": 3, "truncated": False, "flipped": 0}
    from agf.discovery.keeper_one import run
    assert run(db_engine, adv, job, refresh=fake_refresh, extractor=object()) == 0
    with db_engine.connect() as conn:
        status = conn.execute(text(
            "SELECT status FROM public.pipeline_jobs WHERE id=:i"), {"i": job}).scalar_one()
        assert status == "done"

def test_run_extract_error_marks_error_and_crawl_log(db_engine, seeded_keeper_no_history):
    adv = seeded_keeper_no_history
    with begin(db_engine) as conn:
        job = _seed_job(conn, adv)
    async def fake_refresh(*a, **kw):
        raise ExtractError("boom")
    from agf.discovery.keeper_one import run
    assert run(db_engine, adv, job, refresh=fake_refresh, extractor=object()) == 1
    with db_engine.connect() as conn:
        status = conn.execute(text(
            "SELECT status FROM public.pipeline_jobs WHERE id=:i"), {"i": job}).scalar_one()
        assert status == "error"
        marked = conn.execute(text(
            "SELECT crawl_result FROM logging.crawl_log WHERE entity_type='keeper_history' "
            "AND entity_id=:a ORDER BY crawled_at DESC LIMIT 1"), {"a": adv}).scalar_one()
        assert marked.startswith("error:")
```

(Adapt fixture names to the file's conftest conventions; the DB fixtures roll back — `run` commits its own txns, so use the committing engine fixture the pipeline-daemon tests use, and clean sentinel rows in teardown the way `tests/enrich_full/test_pipeline_daemon.py` does.)

- [ ] **Step 2: Run to verify failure** — `AGF_TEST_DB_URL=... pytest tests/discovery/test_keeper_one.py -q` → FAIL `No module named 'agf.discovery.keeper_one'`.

- [ ] **Step 3: Implement**

```python
# src/agf/discovery/keeper_one.py
"""One-shot keeper-history pull (subprocess unit for the keeper daemon).

    python -m agf.discovery.keeper_one --advertiser-id N --job-id M

Pulls one keeper's ads + 16-week series via refresh_one_advertiser, writes the
terminal status onto its pipeline_jobs row, and on failure marks
logging.crawl_log (entity_type='keeper_history') so keepers_due_for_history
TTL-suppresses the retry. SIGALRM bounds the pull like enrich_one."""
from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys

from sqlalchemy import text

from agf.db.engine import begin, get_engine
from agf.discovery.keeper_history import refresh_one_advertiser
from agf.enrich_full.enrich_one import set_status
from agf.extract.errors import ExtractError
from agf.workers.scheduler import mark_crawled


def _page_id(engine, advertiser_id: int) -> str | None:
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT page_id FROM public.advertisers WHERE id = :a"),
            {"a": advertiser_id}).scalar()


def run(engine, advertiser_id: int, job_id: int, *,
        refresh=refresh_one_advertiser, extractor=None) -> int:
    page_id = _page_id(engine, advertiser_id)
    if page_id is None:
        set_status(engine, job_id, "error", error_text="advertiser has no page_id",
                   finished=True, status_table="pipeline_jobs")
        with begin(engine) as conn:
            mark_crawled(conn, "keeper_history", advertiser_id, "error:no_page_id")
        return 1
    if extractor is None:
        from agf.extract.extractor import Extractor
        extractor = Extractor()
    try:
        asyncio.run(refresh(engine, extractor, advertiser_id, page_id))
    except (ExtractError, Exception) as e:  # any failure: terminal + TTL marker
        set_status(engine, job_id, "error", error_text=repr(e)[:500],
                   finished=True, status_table="pipeline_jobs")
        with begin(engine) as conn:
            mark_crawled(conn, "keeper_history", advertiser_id,
                         f"error:{type(e).__name__}"[:64])
        return 1
    set_status(engine, job_id, "done", finished=True, status_table="pipeline_jobs")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--advertiser-id", type=int, required=True)
    p.add_argument("--job-id", type=int, required=True)
    p.add_argument("--timeout", type=int,
                   default=int(os.environ.get("AGF_KEEPER_ONE_TIMEOUT_SEC", "120")))
    args = p.parse_args(argv)
    engine = get_engine()

    def _alarm(signum, frame):
        raise TimeoutError(f"keeper_one exceeded {args.timeout}s")
    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(args.timeout)
    return run(engine, args.advertiser_id, args.job_id)


if __name__ == "__main__":
    sys.exit(main())
```

NOTE for implementer: check `Extractor`'s real import path first (`grep -n "from agf.extract" src/agf/enrich_full/enrich_one.py`) and mirror it exactly; same for `ExtractError`. The bare-`Exception` catch is deliberate (any failure must reach the TTL marker — enrich-TTL lesson 2026-07-10); keep `TimeoutError` flowing into it.

- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/keeper_one.py tests/discovery/test_keeper_one.py
git commit -m "feat(keeper): one-shot keeper-history CLI with terminal status + TTL failure marker"
```

---

### Task 3: Keeper daemon (N lanes)

**Files:**
- Create: `src/agf/discovery/keeper_daemon.py`
- Test: `tests/discovery/test_keeper_daemon.py`

**Interfaces:**
- Consumes: `claim_next(conn, "keeper_history", lease_sec)`, `reap_expired(conn, "keeper_history")`, `set_job_status`, `mark_crawled`; subprocess target `agf.discovery.keeper_one`.
- Produces: `process_once(engine, *, runner=run_keeper_subprocess) -> bool` (mirrors pipeline_daemon contract), `main()` running `AGF_KEEPER_LANES` threads.

- [ ] **Step 1: Write failing tests** — copy the structure of `tests/enrich_full/test_pipeline_daemon.py` (read it first), adapted to kind='keeper_history':

```python
# tests/discovery/test_keeper_daemon.py
def test_process_once_claims_and_runs(db_engine, seeded_keeper_job):
    calls = []
    def fake_runner(advertiser_id, job_id):
        calls.append((advertiser_id, job_id))
        set_status(db_engine, job_id, "done", finished=True, status_table="pipeline_jobs")
    from agf.discovery.keeper_daemon import process_once
    assert process_once(db_engine, runner=fake_runner) is True
    assert len(calls) == 1

def test_process_once_safety_net_marks_error_and_crawl_log(db_engine, seeded_keeper_job):
    def dead_runner(advertiser_id, job_id):
        pass  # exits without writing any status
    from agf.discovery.keeper_daemon import process_once
    assert process_once(db_engine, runner=dead_runner) is True
    # row must be error, crawl_log must carry a keeper_history marker
    # (assert exactly like the pipeline_daemon safety-net test, but
    #  entity_type='keeper_history')

def test_process_once_empty_queue_returns_false(db_engine):
    from agf.discovery.keeper_daemon import process_once
    assert process_once(db_engine, runner=lambda *a: None) is False
```

- [ ] **Step 2: Verify failure** (module missing).
- [ ] **Step 3: Implement** — mirror `pipeline_daemon.py` with these deltas (otherwise copy its shape verbatim, including the safety-net comment):

```python
# src/agf/discovery/keeper_daemon.py — deltas vs enrich_full/pipeline_daemon.py
KIND = "keeper_history"
KEEPER_ONE_TIMEOUT_SEC = int(os.environ.get("AGF_KEEPER_ONE_TIMEOUT_SEC", "120"))
LEASE_SEC = KEEPER_ONE_TIMEOUT_SEC + 120
LANES = int(os.environ.get("AGF_KEEPER_LANES", "3"))

def run_keeper_subprocess(advertiser_id: int, job_id: int) -> None:
    subprocess.run(
        [sys.executable, "-m", "agf.discovery.keeper_one",
         "--advertiser-id", str(advertiser_id),
         "--job-id", str(job_id),
         "--timeout", str(KEEPER_ONE_TIMEOUT_SEC)],
        timeout=KEEPER_ONE_TIMEOUT_SEC + 60, check=False)

# process_once: identical to pipeline_daemon.process_once but kind=KIND and the
# safety-net mark_crawled uses entity_type "keeper_history".

def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    engine = get_engine()
    log.info("keeper daemon up (%d lanes, poll %.1fs, lease %ds)", LANES, POLL_SEC, LEASE_SEC)
    def lane():
        while True:
            try:
                busy = process_once(engine)
            except Exception:
                log.exception("keeper lane tick failed")
                busy = False
            if not busy:
                time.sleep(POLL_SEC)
    threads = [threading.Thread(target=lane, daemon=True) for _ in range(max(1, LANES))]
    for t in threads:
        t.start()
    while True:                      # keep PID 1 alive; threads do the work
        time.sleep(60)
```

`_maybe_purge` is NOT duplicated here — the enrich pipeline daemon already purges terminal rows for the whole table (purge_terminal is kind-blind; verify with one grep and note it in the module docstring).

- [ ] **Step 4: Verify pass.**
- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/keeper_daemon.py tests/discovery/test_keeper_daemon.py
git commit -m "feat(keeper): N-lane keeper-history consumer daemon over pipeline_jobs"
```

---

### Task 4: Producer wiring + retire the inline stage

**Files:**
- Modify: `src/agf/workers/main.py` (run_once step 1.8 area, env docstring, `_env_int` plumbing at the `run_forever` call site ~line 1139)
- Test: extend the existing run_once wiring tests (grep `pull_keeper_history` in tests/ and extend that file)

**Interfaces:**
- Consumes: `keepers_due_for_history(conn, ttl_hours, limit)`, `enqueue_jobs(conn, "keeper_history", ids)` from Task 1.
- Produces: every run_once tick enqueues due keepers; `pull_keeper_history` only runs when `AGF_KEEPER_HISTORY_INLINE=1`.

- [ ] **Step 1: Write failing tests** (in the existing wiring-test style — spies via deps factory):

```python
def test_run_once_enqueues_due_keepers(...):
    # seed one keeper missing history; run run_once in dry-run/test harness;
    # assert a pipeline_jobs row (kind='keeper_history') exists for it.

def test_inline_keeper_pull_off_by_default(...):
    # spy on pull_keeper_history; run run_once with sweep_due=True;
    # assert NOT called when env unset, called when AGF_KEEPER_HISTORY_INLINE=1.
```

- [ ] **Step 2: Verify failure.**
- [ ] **Step 3: Implement** — in run_once, replace the step 1.8 block:

```python
    # 1.8) keeper absorption — triage promotes keepers with advertiser+app rows but
    #      ZERO ads; their history pull now runs CONTINUOUSLY: each tick enqueues due
    #      keepers (kind='keeper_history') and the keeper daemon drains them within
    #      minutes (spec 2026-07-12). The old in-tick serial pull (25/day) stays
    #      available behind AGF_KEEPER_HISTORY_INLINE=1 as a fallback only; its
    #      budget half is NOT reallocated (gem enrich keeps its current rate).
    keeper_enqueued = 0
    if not dry_run:
        with begin(engine) as conn:
            due = keepers_due_for_history(
                conn, ttl_hours=_env_int("AGF_KEEPER_TTL_HOURS", 168),
                limit=_env_int("AGF_KEEPER_ENQUEUE_PER_TICK", 1000))
            keeper_enqueued = enqueue_jobs(conn, "keeper_history",
                                           [a for a, _p in due])
        if keeper_enqueued:
            print(f"[worker] keeper_history enqueued {keeper_enqueued}", flush=True)

    keeper_history = None
    if sweep_due and _env_int("AGF_KEEPER_HISTORY_INLINE", 0):
        keeper_history = asyncio.run(pull_keeper_history(
            engine, extractor, budget_requests=enrich_budget // 2,
            sleep=(lambda s: None) if dry_run else time.sleep,
            kw_delay=0.0 if dry_run else 2.0))
```

Add the three new env vars to main.py's env docstring (lines ~27-80) in the same style. Import `keepers_due_for_history` and `enqueue_jobs` at the existing import blocks.

- [ ] **Step 4: Verify pass, then run the FULL suite bare** (`AGF_TEST_DB_URL=... pytest -q`) — 0 failed required.
- [ ] **Step 5: Commit**

```bash
git add src/agf/workers/main.py tests/<the wiring test file>
git commit -m "feat(worker): enqueue keeper-history jobs every tick; inline pull behind flag"
```

---

### Task 5: Compose + deploy + live verification (controller-run, not subagent)

**Files:**
- Modify: `ops/prod/docker-compose.yml` (new `keeper` service)
- Modify (on VM, hand-edit BOTH per drift lesson): `/opt/agf/docker-compose.yml`

- [ ] **Step 1: repo compose** — add next to the `pipeline` service (copy its stanza; deltas):

```yaml
  keeper:
    image: europe-west1-docker.pkg.dev/peon-489013/agf/worker:latest
    restart: unless-stopped
    init: true
    command: python -m agf.discovery.keeper_daemon
    environment:
      AGF_DATABASE_URL: ${AGF_DATABASE_URL}
      AGF_PROXIES: ${AGF_PROXIES}
      AGF_KEEPER_LANES: ${AGF_KEEPER_LANES:-3}
      AGF_KEEPER_ONE_TIMEOUT_SEC: ${AGF_KEEPER_ONE_TIMEOUT_SEC:-120}
    depends_on:
      cloud-sql-proxy:
        condition: service_started
```

(Match the real `pipeline` service stanza for anything it also sets — network, logging — copy, don't invent.)

- [ ] **Step 2: build + roll** — clean-worktree Cloud Build (`ops/prod/02_build_image.sh` path per RUNBOOK), then on the VM: mirror the keeper stanza into `/opt/agf/docker-compose.yml`, `docker compose pull && docker compose up -d worker keeper pipeline enrich cron`. Check VM disk first (`df -h /` — image-roll layers filled it once; prune if >80%).
- [ ] **Step 3: live verification gates (spec §Verification):**
  1. `docker logs agf-keeper-1` shows lanes claiming; `SELECT status, count(*) FROM public.pipeline_jobs WHERE kind='keeper_history' GROUP BY 1` — running/done growing, queue draining.
  2. Backlog trend: `keepers_due_for_history` count falling from ~2,059; expect ~0 within ~8h at 3 lanes.
  3. DB growth: `SELECT count(*) FROM adtech.advertiser_scores` climbing past 2,073 after the next scoring cron pass; matview row count following.
  4. Latency sample: pick a candidate kept this hour → time to matview presence ≤ 1h.
  5. Scale timings at ~4k advertisers: scoring cron wall-clock and `REFRESH MATERIALIZED VIEW CONCURRENTLY` duration recorded (compare to today's); Meta challenge/error rate at baseline in keeper logs.
  6. Feed sanity: Discover renders, count grows, no 500s.
- [ ] **Step 4: Commit compose + record results**

```bash
git add ops/prod/docker-compose.yml
git commit -m "ops: keeper daemon service (3-lane continuous keeper absorption)"
```

---

## Self-Review Notes

- Spec §1 (drain) is implemented by Tasks 1+3+4 jointly: first ticks enqueue the whole backlog in 1,000-chunks, daemon drains — no separate script (simpler than spec's shape; spec allows it: same path, oldest-first preserved by `ORDER BY MIN(c.triaged_at)` + FIFO claim).
- Spec §2 knob-split satisfied: no change to `AGF_DISCOVERY_ENRICH_BUDGET` semantics; keeper capacity is lanes × subprocess rate.
- Spec §3 (sweep lanes 3→5) is deliberately NOT in this plan — separate founder decision after absorption is proven.
- Watchdog: keeper work leaves the tick loop entirely (daemon owns it), so no new heartbeat need in-tick; the enqueue step is a fast DB write.
- Hot-loop guard: TTL marker on every failure path (keeper_one error, daemon safety net) + ON CONFLICT dedup while queued/running.
