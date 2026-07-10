"""Continuous-enrich consumer (worker VM). Mirrors enrich_full/queue_daemon.py but
drains public.pipeline_jobs (kind='enrich') — the queue the worker tick's run_once
fills — instead of the on-demand public.enrich_jobs. Same crash-isolation contract:
each job runs the one-shot enrich_one CLI in a subprocess; enrich_one writes its own
terminal status onto the pipeline_jobs row (--status-table pipeline_jobs) and marks
the crawl_log on success (--mark-crawled). The daemon adds a safety net for a
subprocess that dies before recording anything, and relies on reap_expired for a
subprocess that wedges past its lease.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time

from sqlalchemy import text

from agf.db.engine import begin, get_engine
from agf.db.pipeline_jobs import (claim_next, purge_done, reap_expired,
                                  set_job_status)

log = logging.getLogger("agf.pipeline")

# Wall-clock bound for one enrich_one subprocess. The CLI's own SIGALRM fires at
# AGF_ENRICH_ONE_TIMEOUT_SEC; the subprocess timeout is the grace-padded hard kill.
ENRICH_ONE_TIMEOUT_SEC = int(os.environ.get("AGF_ENRICH_ONE_TIMEOUT_SEC", "180"))

POLL_SEC = float(os.environ.get("AGF_PIPELINE_POLL_SEC", "5"))

# Lease must outlast the subprocess hard-kill so the reaper only fires for a
# genuinely dead/wedged claim, never for a job still legitimately running.
LEASE_SEC = ENRICH_ONE_TIMEOUT_SEC + 120

# Opportunistic bloat control for the churn table: delete done rows older than a
# week, at most ~once/day (guarded by _last_purge).
PURGE_EVERY_SEC = 24 * 3600
PURGE_OLDER_THAN_DAYS = 7


def run_pipeline_subprocess(advertiser_id: int, job_id: int) -> None:
    """Default runner: the one-shot enrich CLI in a subprocess (crash isolation —
    a wedged render can't take the daemon down). enrich_one records its own terminal
    status on the pipeline_jobs row and marks the crawl_log on success; on timeout we
    kill and let the safety net mark the row."""
    subprocess.run(
        [sys.executable, "-m", "agf.enrich_full.enrich_one",
         "--advertiser-id", str(advertiser_id),
         "--job-id", str(job_id),
         "--status-table", "pipeline_jobs",
         "--mark-crawled",
         "--timeout", str(ENRICH_ONE_TIMEOUT_SEC)],
        timeout=ENRICH_ONE_TIMEOUT_SEC + 60,
        check=False,
    )


def process_once(engine, *, runner=run_pipeline_subprocess) -> bool:
    """One poll tick: reap expired leases, claim + run at most one enrich job.
    Returns True if a job was claimed (poll again immediately)."""
    with begin(engine) as conn:
        reaped = reap_expired(conn, "enrich")
        if reaped:
            log.warning("reaped %d expired enrich lease(s)", reaped)
        claimed = claim_next(conn, "enrich", lease_sec=LEASE_SEC)
    if claimed is None:
        return False
    job_id, advertiser_id = claimed
    log.info("running enrich job %d (advertiser %d)", job_id, advertiser_id)
    err: str | None = None
    try:
        runner(advertiser_id, job_id)
    except Exception as e:  # spawn/timeout failures — the row must not stick
        log.exception("enrich job %d runner failed", job_id)
        err = str(e)[:500]
    # Safety net: if the runner exited without writing a terminal status (crash
    # before set_status), don't leave the row 'running' forever.
    with begin(engine) as conn:
        still_running = conn.execute(text(
            "SELECT 1 FROM public.pipeline_jobs WHERE id = :i AND status = 'running'"),
            {"i": job_id}).scalar()
        if still_running:
            set_job_status(conn, job_id, "error",
                           error=err or "enrichment exited without recording status")
    return True


_last_purge = 0.0


def _maybe_purge(engine, *, now=time.time) -> None:
    """Delete old 'done' rows at most ~once/day (churn-table bloat control)."""
    global _last_purge
    t = now()
    if t - _last_purge < PURGE_EVERY_SEC:
        return
    _last_purge = t
    try:
        with begin(engine) as conn:
            deleted = purge_done(conn, older_than_days=PURGE_OLDER_THAN_DAYS)
        if deleted:
            log.info("purged %d old done enrich job(s)", deleted)
    except Exception:
        log.exception("purge_done failed")


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    engine = get_engine()
    log.info("pipeline enrich daemon up (poll every %.1fs, lease %ds)", POLL_SEC, LEASE_SEC)
    while True:
        try:
            _maybe_purge(engine)
            busy = process_once(engine)
        except Exception:
            log.exception("poll tick failed")
            busy = False
        if not busy:
            time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
