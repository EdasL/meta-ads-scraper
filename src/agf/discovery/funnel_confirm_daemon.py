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
