"""One-shot funnel-confirm (subprocess unit for the funnel_confirm daemon).

    python -m agf.discovery.funnel_confirm_one --candidate-id N --job-id M

Confirms ONE funnel candidate via run_funnel_confirm_one (shipped attempt-cap +
MMP recovery + stall-guard logic), writes the terminal status onto its
pipeline_jobs row, and marks logging.crawl_log (entity_type='funnel_confirm') so
funnel_confirm_due TTL-suppresses a candidate that stayed 'new' (probe failed
below the cap). SIGALRM bounds the render like keeper_one/enrich_one."""
from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys

from agf.db.engine import begin, get_engine
from agf.discovery.funnel_confirm import run_funnel_confirm_one
from agf.enrich_full.enrich_one import set_status
from agf.workers.scheduler import mark_crawled

_KIND = "funnel_confirm"


def run(engine, candidate_id: int, job_id: int, *, resolve=None, probe=None) -> int:
    # Wire resolve/probe exactly as run_funnel_stage does (main.py). A SIGALRM firing
    # before the try below is backstopped by the daemon's still-running safety net.
    if resolve is None:
        from agf.extract.facade import Extractor
        extractor = Extractor()
        def resolve(url):
            return asyncio.run(extractor.resolve_deeplink(url)).intent
    if probe is None:
        from agf.enrich_full.funnel_capture import render_and_probe
        probe = render_and_probe
    try:
        run_funnel_confirm_one(engine, candidate_id, resolve=resolve, probe=probe)
    except Exception as e:
        set_status(engine, job_id, "error", error_text=repr(e)[:500],
                   finished=True, status_table="pipeline_jobs")
        with begin(engine) as conn:
            mark_crawled(conn, _KIND, candidate_id, f"error:{type(e).__name__}"[:64])
        return 1
    set_status(engine, job_id, "done", finished=True, status_table="pipeline_jobs")
    # Always mark: a candidate that stayed 'new' (below-cap probe fail) needs the TTL
    # cooldown; a confirmed/dropped one left 'new' and won't be re-selected anyway.
    with begin(engine) as conn:
        mark_crawled(conn, _KIND, candidate_id, "ok")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--candidate-id", type=int, required=True)
    p.add_argument("--job-id", type=int, required=True)
    p.add_argument("--timeout", type=int,
                   default=int(os.environ.get("AGF_FUNNEL_CONFIRM_ONE_TIMEOUT_SEC", "90")))
    args = p.parse_args(argv)
    engine = get_engine()

    def _alarm(signum, frame):
        raise TimeoutError(f"funnel_confirm_one exceeded {args.timeout}s")
    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(args.timeout)
    return run(engine, args.candidate_id, args.job_id)


if __name__ == "__main__":
    sys.exit(main())
