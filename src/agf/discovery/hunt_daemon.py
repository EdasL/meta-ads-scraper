"""Hunt daemon — drains public.pipeline_jobs kind='hunt' (round_id in the
advertiser_id column). Dedicated single-lane service so a founder-started
hunt is claimed within ~AGF_HUNT_POLL_SEC even when keeper/pipeline lanes
are busy. No hot-loop risk: rounds are enqueued exactly once by the API
(partial-unique (kind, advertiser_id) dedups) and every outcome below stamps
the job terminal."""
import logging
import os
import subprocess
import sys
import threading
import time

from sqlalchemy import text

from agf.db.engine import begin, get_engine
from agf.db.hunts import compose_round_message, finish_round, insert_round_message
from agf.db.pipeline_jobs import claim_next, reap_expired, set_job_status

log = logging.getLogger("agf.hunt_daemon")

KIND = "hunt"
POLL_SEC = int(os.environ.get("AGF_HUNT_POLL_SEC", "2"))
ROUND_TIMEOUT_SEC = int(os.environ.get("AGF_HUNT_ROUND_TIMEOUT_SEC", "1800"))
LEASE_SEC = ROUND_TIMEOUT_SEC + 120


def run_hunt_subprocess(round_id: int, job_id: int) -> None:
    cmd = [sys.executable, "-m", "agf.discovery.hunt_one",
           "--round-id", str(round_id),
           "--job-id", str(job_id),
           "--timeout", str(ROUND_TIMEOUT_SEC)]
    subprocess.run(cmd, timeout=ROUND_TIMEOUT_SEC + 60, check=False)


def process_once(engine, *, runner=run_hunt_subprocess) -> bool:
    with begin(engine) as conn:
        reaped = reap_expired(conn, KIND)
        if reaped:
            log.warning("reaped %d expired hunt lease(s)", reaped)
        row = claim_next(conn, KIND, lease_sec=LEASE_SEC)
    if row is None:
        return False
    job_id, round_id = row
    log.info("running hunt round %d (job %d)", round_id, job_id)
    err: str | None = None
    try:
        runner(round_id, job_id)
    except Exception as e:  # noqa: BLE001 — daemon must survive any runner death
        log.exception("hunt job %d runner failed", job_id)
        err = str(e)[:500]
    with begin(engine) as conn:
        still_running = conn.execute(text(
            "SELECT 1 FROM public.pipeline_jobs "
            "WHERE id = :i AND status = 'running'"), {"i": job_id}).scalar()
        if still_running:
            msg = err or "hunt subprocess exited without recording status"
            set_job_status(conn, job_id, "error", error=msg)
            finish_round(conn, round_id, status="error", error=msg)
            try:
                insert_round_message(conn, round_id,
                                     compose_round_message(round_id, "error", {}, error=msg))
            except Exception:  # message is a convenience; the round row is the truth
                log.exception("round failure message failed")
    return True


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    engine = get_engine()

    def lane() -> None:
        while True:
            try:
                busy = process_once(engine)
            except Exception:  # noqa: BLE001
                log.exception("hunt lane tick failed")
                busy = False
            if not busy:
                time.sleep(POLL_SEC)

    t = threading.Thread(target=lane, daemon=True)
    t.start()
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
