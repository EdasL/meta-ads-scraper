"""One bounded ad-search process; durable partial results survive termination."""

import argparse
import logging
import signal

from agf.db.ad_searches import load_search, update_search
from agf.db.engine import begin, get_engine
from agf.db.pipeline_jobs import set_job_status
from agf.discovery.ad_search import run_ad_search

log = logging.getLogger(__name__)


class SearchDeadline(BaseException):
    pass


def terminal_failure(engine, search_id, job_id, reason):
    with begin(engine) as conn:
        search = load_search(conn, search_id)
        if search and search["status"] in ("done",):
            set_job_status(conn, job_id, "done")
            return
        progress = dict(search["progress"] or {}) if search else {}
        progress.update(partial=True, phase="interrupted")
        update_search(conn, search_id, status="error", progress=progress, error=reason)
        set_job_status(conn, job_id, "error", error=reason)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--search-id", type=int, required=True)
    parser.add_argument("--job-id", type=int, required=True)
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args(argv)
    engine = get_engine()

    def alarm(*_):
        raise SearchDeadline()

    signal.signal(signal.SIGALRM, alarm)
    signal.alarm(args.timeout)
    try:
        status = run_ad_search(
            engine, args.search_id, budget_seconds=max(1, args.timeout - 15)
        )
        signal.alarm(0)
        with begin(engine) as conn:
            set_job_status(conn, args.job_id, "error" if status == "error" else "done")
        return int(status == "error")
    except SearchDeadline:
        terminal_failure(
            engine, args.search_id, args.job_id, "search_deadline_exceeded"
        )
        return 1
    except Exception as exc:  # noqa: BLE001 — isolate failures; never expose credential-bearing messages
        # Never log exception messages which may contain proxy or API credentials.
        log.error("Ad search %d failed (%s)", args.search_id, type(exc).__name__)
        terminal_failure(engine, args.search_id, args.job_id, "search_failed")
        return 1
    finally:
        signal.alarm(0)
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
