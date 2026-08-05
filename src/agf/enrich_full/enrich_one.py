"""On-demand single-app enrichment (one-shot CLI).

Go spawns this per "Refresh data" click:
    python -m agf.enrich_full.enrich_one --advertiser-id N --job-id M

It mirrors the worker's production deps (main.py production_deps_factory),
runs the existing enrich_gem, re-scores just this advertiser, refreshes the
matview, and records its own status on the public.enrich_jobs row so the API
can report live progress. enrich_gem itself is unchanged.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from datetime import date

from sqlalchemy import text

from agf.db.engine import begin, get_engine
from agf.discovery.keeper_history import refresh_one_advertiser
from agf.enrich_full.run_enrich import EnrichDeps, enrich_gem
from agf.scoring.run_scoring import refresh_emerging_matview, score_all
from agf.workers.main import prefetch_creative_urls
from agf.workers.scheduler import mark_crawled

# Allowlist mapping the CLI arg to a vetted literal table name. The status/
# error_text/finished_at columns are identical on both, so one UPDATE serves
# both. NEVER interpolate the raw arg into SQL — always map through this dict.
_STATUS_TABLES = {
    "enrich_jobs": "public.enrich_jobs",
    "pipeline_jobs": "public.pipeline_jobs",
}


def set_status(engine, job_id, status, *, error_text=None, finished=False,
               status_table="enrich_jobs"):
    """Write a terminal/in-flight status onto the job row (own committed txn)."""
    table = _STATUS_TABLES[status_table]  # KeyError on anything off-allowlist
    with begin(engine) as conn:
        conn.execute(
            text(
                f"UPDATE {table} "
                "SET status=:s, error_text=:e, "
                "    finished_at = CASE WHEN :fin THEN now() ELSE finished_at END "
                "WHERE id=:i"
            ),
            {"s": status, "e": error_text, "fin": finished, "i": job_id},
        )


def _build_deps(extractor, http_client, creative_urls):
    """EnrichDeps mirroring workers.main.production_deps_factory."""
    from agf.storage.s3 import get_s3, put_bytes

    try:
        import whois as _whois
        whois_fn = _whois.whois
    except Exception:
        whois_fn = None

    def fetch_text(url):
        return http_client.get(url).text

    def fetch_bytes(url):
        r = http_client.get(url)
        return r.content, r.headers.get("content-type", "")

    return EnrichDeps(
        extractor=extractor,
        itunes_client=http_client,
        fetch_text=fetch_text,
        fetch_bytes=fetch_bytes,
        put=put_bytes,
        s3=get_s3(),
        whois_fn=whois_fn,
        page=None,  # Playwright launches per-funnel on demand
        proxy=os.environ.get("AGF_FUNNEL_PROXY"),
        capture=True,
        creative_urls=creative_urls,
    )


def _refresh_ads_listing(engine, advertiser_id, *, extractor,
                         pages=None, timeout=None):
    """Fresh-listing phase for "Refresh data": re-scrape the advertiser's Ad
    Library listing so new ads appear and vanished ones flip inactive, before
    enrich_gem reads public.ads. Fail-soft by design — a Meta hiccup must never
    fail the enrich job (it could not before this phase existed). The wait_for
    budget keeps the phase inside enrich_one's 300s wall clock; EnrichDeadline
    is a BaseException, so the broad `except Exception` cannot swallow the
    process-wide deadline."""
    pages = pages if pages is not None else int(os.environ.get("AGF_ENRICH_ADS_PAGES", "12"))
    timeout = timeout if timeout is not None else float(os.environ.get("AGF_ENRICH_ADS_TIMEOUT", "120"))
    with begin(engine) as conn:
        page_id = conn.execute(
            text("SELECT page_id FROM public.advertisers WHERE id=:a"),
            {"a": advertiser_id}).scalar()
    if not page_id:
        return None
    try:
        return asyncio.run(asyncio.wait_for(
            refresh_one_advertiser(engine, extractor, advertiser_id, page_id,
                                   hist_pages=pages, flip_vanished=True),
            timeout))
    except Exception as e:
        print(f"[enrich_one] ads listing refresh skipped: {type(e).__name__}: {e}",
              file=sys.stderr)
        return None


def _default_enrich(engine, advertiser_id, *, extractor, http_client,
                    top_n=5, media_cap=120):
    """Production enrichment for one advertiser — same call shape as
    make_enrich_handler (asyncio.run around the async enrich_gem)."""
    _refresh_ads_listing(engine, advertiser_id, extractor=extractor)
    # fbcdn signed URLs expire — prefetch fresh, OUTSIDE the enrich write txn.
    with begin(engine) as conn:
        creative_urls = prefetch_creative_urls(extractor, conn, [advertiser_id])
    deps = _build_deps(extractor, http_client, creative_urls.get(advertiser_id, {}))
    with begin(engine) as conn:
        asyncio.run(enrich_gem(conn, advertiser_id, top_n=top_n,
                               media_cap=media_cap, deps=deps))


class EnrichDeadline(BaseException):
    """Raised by the SIGALRM wall-clock guard. Deliberately NOT an Exception (and
    NOT a TimeoutError): per-stage network-failure handlers — prefetch's
    `except (ExtractError, TimeoutError)` skip-and-continue — must never swallow
    the process-wide deadline. When the alarm raised a plain TimeoutError, a
    deadline that fired during creative prefetch was logged as a routine per-gem
    skip, the one-shot alarm was spent, and the rest of the enrich ran unguarded
    to the daemon's hard kill (advertiser 10173 hot-loop, 2026-07-11). Only
    run_job's BaseException handler catches this, records the row + crawl_log,
    and re-raises."""


def _default_score(engine, advertiser_id, as_of):
    """Re-score just this advertiser, then refresh the matview (mirrors
    run_scoring but scoped via only_ids)."""
    from agf.tracker.growth import compute_advertiser_growth
    with begin(engine) as conn:
        score_all(conn, as_of, only_ids=[advertiser_id])
        compute_advertiser_growth(conn)
    refresh_emerging_matview(engine)


def run_job(engine, *, advertiser_id, job_id, enrich_call, score_call,
            as_of=None, status_table="enrich_jobs",
            mark_crawled_on_success=False):
    """Orchestrate one enrichment job and record its status. enrich_call and
    score_call are injected so the state machine is testable in isolation.

    status_table selects the vetted job table set_status writes to.
    mark_crawled_on_success, when set (queue-consumer path), appends a crawl_log
    row for the advertiser on BOTH outcomes: result="ok" on success and
    result="error:<type>" on failure. Marking the failure too is load-bearing —
    without it a failing advertiser (dead landing, delisted app, SIGALRM) has no
    crawl_log row, so claim_due (never-crawled first) re-enqueues it every worker
    tick and starves healthy keepers. The on-demand default (False) NEVER marks
    crawl_log, keeping "Refresh data" byte-identical.
    """
    as_of = as_of or date.today()
    try:
        enrich_call(engine, advertiser_id)
        score_call(engine, advertiser_id, as_of)
        if mark_crawled_on_success:
            with begin(engine) as conn:
                mark_crawled(conn, "advertiser", advertiser_id, "ok")
        set_status(engine, job_id, "done", finished=True,
                   status_table=status_table)
    except BaseException as e:  # record ANY failure (incl. timeout) on the row
        if mark_crawled_on_success:
            # Same TTL discipline as the old in-loop batch (runner.py): a failing
            # advertiser records a crawl_log row so it isn't re-claimed next tick.
            try:
                with begin(engine) as conn:
                    mark_crawled(conn, "advertiser", advertiser_id,
                                 f"error:{type(e).__name__}")
            except Exception:
                pass  # best-effort; never mask the original failure
        try:
            set_status(engine, job_id, "error", error_text=str(e)[:500],
                       finished=True, status_table=status_table)
        except Exception:
            pass  # best-effort; the Go stale-guard (540s) is the backstop
        raise


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--advertiser-id", type=int, required=True)
    ap.add_argument("--job-id", type=int, required=True)
    ap.add_argument("--top-n", type=int, default=5)
    ap.add_argument("--media-cap", type=int, default=120)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--status-table", default="enrich_jobs",
                    choices=tuple(_STATUS_TABLES))
    ap.add_argument("--mark-crawled", action="store_true", default=False)
    args = ap.parse_args(argv)

    engine = get_engine()

    # Best-effort wall-clock guard: SIGALRM interrupts a wedged render so the
    # row lands in 'error' rather than 'running' forever. The API stale-guard
    # is the UI-facing backstop if the signal can't interrupt a blocked call.
    def _on_alarm(signum, frame):
        raise EnrichDeadline(f"enrich exceeded {args.timeout}s")

    signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(args.timeout)

    import httpx
    from agf.extract.facade import Extractor
    extractor = Extractor()
    http_client = httpx.Client(timeout=20, follow_redirects=True)
    try:
        run_job(
            engine,
            advertiser_id=args.advertiser_id,
            job_id=args.job_id,
            enrich_call=lambda eng, aid: _default_enrich(
                eng, aid, extractor=extractor, http_client=http_client,
                top_n=args.top_n, media_cap=args.media_cap),
            score_call=_default_score,
            status_table=args.status_table,
            mark_crawled_on_success=args.mark_crawled,
        )
    except BaseException:
        sys.exit(1)
    finally:
        signal.alarm(0)
        http_client.close()


if __name__ == "__main__":
    main()
