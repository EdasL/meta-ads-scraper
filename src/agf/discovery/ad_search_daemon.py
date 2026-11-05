"""Independent ad-search lane alongside Hunt, with subprocess crash isolation."""

import logging
import subprocess
import sys
import time

from sqlalchemy import text

from agf.db.ad_searches import load_search, update_search
from agf.db.engine import begin
from agf.db.pipeline_jobs import claim_next, set_job_status
from agf.discovery.ad_search_one import terminal_failure

log = logging.getLogger(__name__)
KIND = "ad_search"
TIMEOUT = 90
LEASE = TIMEOUT + 90


def run_subprocess(search_id, job_id):
    subprocess.run(
        [
            sys.executable,
            "-m",
            "agf.discovery.ad_search_one",
            "--search-id",
            str(search_id),
            "--job-id",
            str(job_id),
            "--timeout",
            str(TIMEOUT),
        ],
        timeout=TIMEOUT + 30,
        check=False,
    )


def process_once(engine, *, runner=run_subprocess):
    with begin(engine) as conn:
        expired = conn.execute(
            text("""SELECT id,advertiser_id FROM public.pipeline_jobs
            WHERE kind=:kind AND status='running' AND lease_expires_at<now()
            FOR UPDATE SKIP LOCKED"""),
            {"kind": KIND},
        ).all()
        # Preserve the original observation times and cursors on interruption.
        # The agent can retry explicitly without mixing pages from two attempts.
        for job_id, search_id in expired:
            search = load_search(conn, search_id)
            if search and search["status"] in ("queued", "running"):
                progress = dict(search["progress"] or {})
                progress.update(
                    partial=True,
                    phase="interrupted",
                    next_step="Worker lease expired. Start a new search; partial evidence is retained.",
                )
                update_search(
                    conn,
                    search_id,
                    status="error",
                    progress=progress,
                    error="lease_expired",
                )
            set_job_status(
                conn,
                job_id,
                "done" if search and search["status"] in ("done",) else "error",
                error=None
                if search and search["status"] in ("done",)
                else "lease_expired",
            )
        row = claim_next(conn, KIND, lease_sec=LEASE)
        if row is None:
            return False
        job_id, search_id = row
        search = load_search(conn, search_id)
        if search is None or search["status"] in ("done", "error"):
            set_job_status(
                conn,
                job_id,
                "done" if search and search["status"] != "error" else "error",
            )
            return True
    try:
        runner(search_id, job_id)
    except Exception as exc:  # noqa: BLE001 — isolate failures; never expose credential-bearing messages
        log.warning("Ad search %d runner stopped (%s)", search_id, type(exc).__name__)
    with begin(engine) as conn:
        running = conn.execute(
            text("SELECT status='running' FROM public.pipeline_jobs WHERE id=:id"),
            {"id": job_id},
        ).scalar()
    if running:
        terminal_failure(engine, search_id, job_id, "worker_interrupted")
    return True


def lane(engine):
    while True:
        try:
            busy = process_once(engine)
        except Exception as exc:  # noqa: BLE001 — isolate failures; never expose credential-bearing messages
            log.warning("Ad search queue unavailable (%s)", type(exc).__name__)
            busy = False
        if not busy:
            time.sleep(2)
