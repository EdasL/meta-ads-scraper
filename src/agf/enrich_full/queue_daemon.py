"""Enrich-queue daemon (worker VM side of on-demand "Refresh data").

The Go API runs in a GKE pod with no Python, so it can't exec the enrichment
CLI. Instead it inserts a 'queued' row into public.enrich_jobs and this daemon
— running next to the worker with the full pipeline environment — claims the
row and runs the existing one-shot CLI:

    python -m agf.enrich_full.enrich_one --advertiser-id N --job-id M

enrich_one writes its own 'done'/'error' status onto the row; the daemon only
adds a safety net for a subprocess that dies before recording anything. The
Go stale-guard (enrichTimeout) is the UI-facing backstop and must stay larger
than QUEUED_MAX_AGE_SEC + the enrich_one --timeout.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time

from sqlalchemy import text

from agf.db.engine import begin, get_engine

log = logging.getLogger("agf.enrich_queue")

# Queued rows older than this are dead — the UI already reported them failed
# (Go stale-guard), so running them would waste a Meta pull.
QUEUED_MAX_AGE_SEC = 120

# Wall-clock bound for one enrich_one subprocess. The CLI's own SIGALRM fires
# at AGF_ENRICH_ONE_TIMEOUT_SEC; this is the grace-padded hard kill.
ENRICH_ONE_TIMEOUT_SEC = int(os.environ.get("AGF_ENRICH_ONE_TIMEOUT_SEC", "180"))

POLL_SEC = float(os.environ.get("AGF_ENRICH_POLL_SEC", "5"))


def expire_stale_queued(conn) -> int:
    """Flip queued rows nobody picked up in time to 'error'."""
    res = conn.execute(text(
        "UPDATE public.enrich_jobs "
        "SET status='error', error_text='not picked up in time', finished_at=now() "
        "WHERE status='queued' AND started_at < now() - make_interval(secs => :age)"
    ), {"age": QUEUED_MAX_AGE_SEC})
    return res.rowcount


def claim_next(conn):
    """Atomically claim the oldest queued job. Returns (job_id, advertiser_id)
    or None. SKIP LOCKED keeps a second daemon (if ever run) from double-claiming."""
    row = conn.execute(text(
        "UPDATE public.enrich_jobs SET status='running' "
        "WHERE id = (SELECT id FROM public.enrich_jobs WHERE status='queued' "
        "            ORDER BY started_at, id LIMIT 1 FOR UPDATE SKIP LOCKED) "
        "RETURNING id, advertiser_id"
    )).first()
    return (row[0], row[1]) if row else None


def run_enrich_subprocess(advertiser_id: int, job_id: int) -> None:
    """Default runner: the one-shot CLI in a subprocess (crash isolation —
    a wedged render can't take the daemon down). The CLI records its own
    terminal status; on timeout we kill and let the safety net mark the row."""
    subprocess.run(
        [sys.executable, "-m", "agf.enrich_full.enrich_one",
         "--advertiser-id", str(advertiser_id),
         "--job-id", str(job_id),
         "--timeout", str(ENRICH_ONE_TIMEOUT_SEC)],
        timeout=ENRICH_ONE_TIMEOUT_SEC + 60,
        check=False,
    )


def process_once(engine, *, runner=run_enrich_subprocess) -> bool:
    """One poll tick: expire stale rows, claim + run at most one job.
    Returns True if a job was claimed (poll again immediately)."""
    with begin(engine) as conn:
        expired = expire_stale_queued(conn)
        if expired:
            log.warning("expired %d stale queued enrich job(s)", expired)
        claimed = claim_next(conn)
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
    # Safety net: if the runner exited without writing a terminal status
    # (crash before set_status), don't leave the row 'running' forever.
    with begin(engine) as conn:
        conn.execute(text(
            "UPDATE public.enrich_jobs "
            "SET status='error', error_text=:e, finished_at=now() "
            "WHERE id=:i AND status='running'"
        ), {"e": err or "enrichment exited without recording status", "i": job_id})
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    engine = get_engine()
    log.info("enrich-queue daemon up (poll every %.1fs)", POLL_SEC)
    while True:
        try:
            busy = process_once(engine)
        except Exception:
            log.exception("poll tick failed")
            busy = False
        if not busy:
            time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
