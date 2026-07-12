"""N-lane keeper-history consumer (worker VM). Mirrors enrich_full/pipeline_daemon.py
but drains public.pipeline_jobs (kind='keeper_history') — the queue the worker tick's
keeper producer fills — with AGF_KEEPER_LANES concurrent lanes instead of a single
poll loop. Same crash-isolation contract: each job runs the one-shot keeper_one CLI in
a subprocess; keeper_one writes its own terminal status onto the pipeline_jobs row and
marks the crawl_log (entity_type='keeper_history') on failure. The daemon adds a safety
net for a subprocess that dies before recording anything, and relies on reap_expired for
a subprocess that wedges past its lease.

Terminal-row purge is NOT duplicated here: purge_terminal is kind-blind (its DELETE
filters only on status IN ('done','error') and finished_at — no `kind` predicate, so it
sweeps keeper_history rows too), and enrich_full/pipeline_daemon._maybe_purge already
runs it for the whole table. A second purger would be redundant.
"""
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

log = logging.getLogger("agf.keeper")

KIND = "keeper_history"

# Wall-clock bound for one keeper_one subprocess. The CLI's own SIGALRM fires at
# AGF_KEEPER_ONE_TIMEOUT_SEC; the subprocess timeout is the grace-padded hard kill.
KEEPER_ONE_TIMEOUT_SEC = int(os.environ.get("AGF_KEEPER_ONE_TIMEOUT_SEC", "120"))

POLL_SEC = float(os.environ.get("AGF_KEEPER_POLL_SEC", "5"))

# Lease must outlast the subprocess hard-kill so the reaper only fires for a
# genuinely dead/wedged claim, never for a job still legitimately running.
LEASE_SEC = KEEPER_ONE_TIMEOUT_SEC + 120

# Concurrent claim lanes (each an independent poll loop over the same queue).
LANES = int(os.environ.get("AGF_KEEPER_LANES", "3"))


def run_keeper_subprocess(advertiser_id: int, job_id: int) -> None:
    """Default runner: the one-shot keeper CLI in a subprocess (crash isolation —
    a wedged pull can't take a lane down). keeper_one records its own terminal status
    on the pipeline_jobs row and marks the crawl_log on failure; on timeout we kill and
    let the safety net mark the row."""
    subprocess.run(
        [sys.executable, "-m", "agf.discovery.keeper_one",
         "--advertiser-id", str(advertiser_id),
         "--job-id", str(job_id),
         "--timeout", str(KEEPER_ONE_TIMEOUT_SEC)],
        timeout=KEEPER_ONE_TIMEOUT_SEC + 60,
        check=False,
    )


def process_once(engine, *, runner=run_keeper_subprocess) -> bool:
    """One poll tick: reap expired leases, claim + run at most one keeper job.
    Returns True if a job was claimed (poll again immediately)."""
    with begin(engine) as conn:
        reaped = reap_expired(conn, KIND)
        if reaped:
            log.warning("reaped %d expired keeper lease(s)", reaped)
        claimed = claim_next(conn, KIND, lease_sec=LEASE_SEC)
    if claimed is None:
        return False
    job_id, advertiser_id = claimed
    log.info("running keeper job %d (advertiser %d)", job_id, advertiser_id)
    err: str | None = None
    try:
        runner(advertiser_id, job_id)
    except Exception as e:  # spawn/timeout failures — the row must not stick
        log.exception("keeper job %d runner failed", job_id)
        err = str(e)[:500]
    # Safety net: if the runner exited without writing a terminal status (crash
    # before set_status), don't leave the row 'running' forever — and mark the
    # crawl_log too. A subprocess killed by the OUTER timeout (hard kill at
    # KEEPER_ONE_TIMEOUT_SEC+60) never reaches keeper_one's own error-path
    # crawl_log marker; without one, keepers_due_for_history sorts the advertiser
    # never-crawled-first and the producer re-enqueues it EVERY tick — the
    # starvation hot-loop (advertiser 10173, 2026-07-11), one wedge-path deeper
    # than the 2026-07-10 fix.
    with begin(engine) as conn:
        still_running = conn.execute(text(
            "SELECT 1 FROM public.pipeline_jobs WHERE id = :i AND status = 'running'"),
            {"i": job_id}).scalar()
        if still_running:
            set_job_status(conn, job_id, "error",
                           error=err or "keeper pull exited without recording status")
            mark_crawled(conn, KIND, advertiser_id,
                         "error:runner_killed" if err else "error:no_status")
    return True


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


if __name__ == "__main__":
    main()
