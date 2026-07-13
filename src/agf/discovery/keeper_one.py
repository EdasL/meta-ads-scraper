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
from agf.extract.errors import ExtractError  # noqa: F401  (kept explicit; flows into bare-except)
from agf.workers.scheduler import mark_crawled


def _page_id(engine, advertiser_id: int) -> str | None:
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT page_id FROM public.advertisers WHERE id = :a"),
            {"a": advertiser_id}).scalar()


def run(engine, advertiser_id: int, job_id: int, *, kind: str = "keeper_history",
        flip_vanished: bool = False, refresh=refresh_one_advertiser,
        extractor=None) -> int:
    # One shared path for both job kinds (spec 2026-07-13): keeper_history pulls a
    # keeper's first history; corpus_refresh re-scrapes a known advertiser with
    # flip_vanished=True. The crawl_log marker uses the JOB's kind so each producer's
    # TTL suppression sees only its own failures.
    # A SIGALRM firing before the try below (during the page_id read or Extractor
    # construction) escapes uncaught — that path is backstopped by the keeper
    # daemon: outer subprocess timeout + still-running safety net writes both the
    # error status and the crawl_log marker (see keeper_daemon.process_once).
    page_id = _page_id(engine, advertiser_id)
    if page_id is None:
        set_status(engine, job_id, "error", error_text="advertiser has no page_id",
                   finished=True, status_table="pipeline_jobs")
        with begin(engine) as conn:
            mark_crawled(conn, kind, advertiser_id, "error:no_page_id")
        return 1
    if extractor is None:
        from agf.extract.facade import Extractor
        extractor = Extractor()
    try:
        asyncio.run(refresh(engine, extractor, advertiser_id, page_id,
                            flip_vanished=flip_vanished))
    except Exception as e:  # any failure: terminal status + TTL marker (enrich-TTL lesson 2026-07-10)
        set_status(engine, job_id, "error", error_text=repr(e)[:500],
                   finished=True, status_table="pipeline_jobs")
        with begin(engine) as conn:
            mark_crawled(conn, kind, advertiser_id,
                         f"error:{type(e).__name__}"[:64])
        return 1
    set_status(engine, job_id, "done", finished=True, status_table="pipeline_jobs")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--advertiser-id", type=int, required=True)
    p.add_argument("--job-id", type=int, required=True)
    p.add_argument("--kind", default="keeper_history",
                   choices=("keeper_history", "corpus_refresh"))
    p.add_argument("--flip-vanished", action="store_true")
    p.add_argument("--timeout", type=int,
                   default=int(os.environ.get("AGF_KEEPER_ONE_TIMEOUT_SEC", "120")))
    args = p.parse_args(argv)
    engine = get_engine()

    def _alarm(signum, frame):
        raise TimeoutError(f"keeper_one exceeded {args.timeout}s")
    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(args.timeout)
    return run(engine, args.advertiser_id, args.job_id, kind=args.kind,
               flip_vanished=args.flip_vanished)


if __name__ == "__main__":
    sys.exit(main())
