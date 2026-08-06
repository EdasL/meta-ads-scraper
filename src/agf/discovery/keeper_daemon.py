"""N-lane keeper-history + corpus-refresh consumer (worker VM). Mirrors
enrich_full/pipeline_daemon.py but drains public.pipeline_jobs with AGF_KEEPER_LANES
concurrent lanes instead of a single poll loop, claiming kinds in strict priority:
'keeper_history' (found→visible path) first, 'corpus_refresh' (spec 2026-07-13) only
when no keeper job is claimable — so the lanes the worker tick's producers fill stay
a fixed number of Meta streams no matter how much corpus backlog exists. Same
crash-isolation contract: each job runs the one-shot keeper_one CLI in a subprocess;
keeper_one writes its own terminal status onto the pipeline_jobs row and marks the
crawl_log (entity_type=<job kind>) on failure. The daemon adds a safety net for a
subprocess that dies before recording anything, and relies on reap_expired for a
subprocess that wedges past its lease. A corpus error spike (>= AGF_CORPUS_ERROR_PAUSE
errors in 30 min) pauses corpus claims only — the manual Meta-pressure dial order
(keeper lanes → corpus cadence → sweep) stays a human decision.

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

# Claim priority: a pasted-link add (kind='add_page', someone is watching a
# spinner on /discover) beats a keeper's first history, which beats corpus
# refresh (re-scrape of a known advertiser). add_page rides this daemon rather
# than getting its own compose service — one fewer place for a prod compose file
# to drift out of sync with dev (the missing-enrich-service incident, 2026-08-05).
KINDS = ("add_page", "keeper_history", "corpus_refresh")

# Wall-clock bound for one keeper_one subprocess. The CLI's own SIGALRM fires at
# AGF_KEEPER_ONE_TIMEOUT_SEC; the subprocess timeout is the grace-padded hard kill.
KEEPER_ONE_TIMEOUT_SEC = int(os.environ.get("AGF_KEEPER_ONE_TIMEOUT_SEC", "120"))

# add_page does the whole scrape → triage → enrich → score chain in one process,
# so it needs a far longer budget than a keeper pull. Must match add_page_one's
# own SIGALRM default (AGF_ADD_PAGE_TIMEOUT_SEC).
ADD_PAGE_TIMEOUT_SEC = int(os.environ.get("AGF_ADD_PAGE_TIMEOUT_SEC", "600"))

POLL_SEC = float(os.environ.get("AGF_KEEPER_POLL_SEC", "5"))

# Lease must outlast the LONGEST subprocess hard-kill so the reaper only fires
# for a genuinely dead/wedged claim, never for a job still legitimately running.
# Derived from the max across kinds — an add_page job outlives a keeper pull by
# minutes, and a lease sized for the keeper would reap it mid-enrich.
LEASE_SEC = max(KEEPER_ONE_TIMEOUT_SEC, ADD_PAGE_TIMEOUT_SEC) + 120

# Concurrent claim lanes (each an independent poll loop over the same queue).
LANES = int(os.environ.get("AGF_KEEPER_LANES", "3"))


def run_keeper_subprocess(advertiser_id: int, job_id: int,
                          kind: str = "keeper_history") -> None:
    """Default runner: the matching one-shot CLI in a subprocess (crash isolation —
    a wedged pull can't take a lane down). Each CLI records its own terminal status
    on the pipeline_jobs row and marks the crawl_log on failure; on timeout we kill and
    let the safety net mark the row. corpus_refresh jobs flip vanished ads (the whole
    point of a re-scrape); keeper first-pulls never do.

    add_page runs a different CLI: it scrapes, triages AND enriches, so it needs
    the longer budget and takes neither --kind nor --flip-vanished."""
    if kind == "add_page":
        cmd = [sys.executable, "-m", "agf.discovery.add_page_one",
               "--advertiser-id", str(advertiser_id),
               "--job-id", str(job_id),
               "--timeout", str(ADD_PAGE_TIMEOUT_SEC)]
        subprocess.run(cmd, timeout=ADD_PAGE_TIMEOUT_SEC + 120, check=False)
        return
    cmd = [sys.executable, "-m", "agf.discovery.keeper_one",
           "--advertiser-id", str(advertiser_id),
           "--job-id", str(job_id),
           "--kind", kind,
           "--timeout", str(KEEPER_ONE_TIMEOUT_SEC)]
    if kind == "corpus_refresh":
        cmd.append("--flip-vanished")
    subprocess.run(cmd, timeout=KEEPER_ONE_TIMEOUT_SEC + 60, check=False)


def _corpus_paused(conn) -> bool:
    """The brake: pause corpus claims while corpus errors spike (>= threshold in the
    last 30 min) — a Meta challenge wave should not burn the whole backlog through
    the error-TTL. Keeper claims are never braked here. Threshold <= 0 disables."""
    threshold = int(os.environ.get("AGF_CORPUS_ERROR_PAUSE", "10"))
    if threshold <= 0:
        return False
    n = conn.execute(text(
        "SELECT count(*) FROM public.pipeline_jobs "
        "WHERE kind = 'corpus_refresh' AND status = 'error' "
        "AND finished_at > now() - make_interval(mins => 30)")).scalar_one()
    return n >= threshold


def process_once(engine, *, runner=run_keeper_subprocess) -> bool:
    """One poll tick: reap expired leases (both kinds), claim + run at most one job —
    keeper_history first, corpus_refresh only when no keeper job is claimable.
    Returns True if a job was claimed (poll again immediately)."""
    claimed = None
    with begin(engine) as conn:
        for kind in KINDS:
            reaped = reap_expired(conn, kind)
            if reaped:
                log.warning("reaped %d expired %s lease(s)", reaped, kind)
        for kind in KINDS:
            if kind == "corpus_refresh" and _corpus_paused(conn):
                continue
            row = claim_next(conn, kind, lease_sec=LEASE_SEC)
            if row is not None:
                claimed = (kind, row[0], row[1])
                break
    if claimed is None:
        return False
    kind, job_id, advertiser_id = claimed
    log.info("running %s job %d (advertiser %d)", kind, job_id, advertiser_id)
    err: str | None = None
    try:
        runner(advertiser_id, job_id, kind)
    except Exception as e:  # spawn/timeout failures — the row must not stick
        log.exception("%s job %d runner failed", kind, job_id)
        err = str(e)[:500]
    # Safety net: if the runner exited without writing a terminal status (crash
    # before set_status), don't leave the row 'running' forever — and mark the
    # crawl_log too, UNDER THE JOB'S KIND. A subprocess killed by the OUTER timeout
    # (hard kill at KEEPER_ONE_TIMEOUT_SEC+60) never reaches keeper_one's own
    # error-path crawl_log marker; without one, the kind's producer sorts the
    # advertiser never-crawled-first and re-enqueues it EVERY tick — the
    # starvation hot-loop (advertiser 10173, 2026-07-11), one wedge-path deeper
    # than the 2026-07-10 fix.
    with begin(engine) as conn:
        still_running = conn.execute(text(
            "SELECT 1 FROM public.pipeline_jobs WHERE id = :i AND status = 'running'"),
            {"i": job_id}).scalar()
        if still_running:
            set_job_status(conn, job_id, "error",
                           error=err or "pull exited without recording status")
            mark_crawled(conn, kind, advertiser_id,
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
