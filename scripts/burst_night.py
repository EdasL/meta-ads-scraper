"""One-night burst orchestrator: scale the discovery pipeline past its daily caps.

The nightly worker is tuned for steady-state (daily-gated sweep, enrich budget 600
split with keeper history). This script runs the SAME proven pieces as explicit,
budget-raised phases so an operator (or an agent) can drive them in waves:

    backlog  pull_keeper_history for every keeper missing its ad history
    sweep    full app-layer taxonomy at per-keyword page ceilings (+ extra keywords)
    triage   store-lookup triage of new candidates + resolve app metadata
    apps     daily app-store refresh (icons/metrics) for apps due today
    score    weekly snapshot + score_all + growth + matview refresh
    status   one-line funnel counts

Each phase is idempotent (upserts everywhere), so re-running after a crash is safe.
Run with the worker's env (AGF_DATABASE_URL, AGF_PROXIES, S3_*); the launchd worker
should be stopped while a burst runs so the proxy pool isn't shared.
"""
import argparse
import asyncio
import time
from datetime import date, timedelta
from functools import partial

from sqlalchemy import text

from agf.db.engine import get_engine, begin
from agf.db.candidates import close_sweep_run, open_sweep_run
from agf.discovery.keeper_history import pull_keeper_history
from agf.discovery.sweep import run_sweep
from agf.discovery.taxonomy import TaxonomyEntry, all_entries
from agf.discovery.triage import run_triage
from agf.enrich_full.appstore import (lookup_google_play, lookup_itunes_batch,
                                      resolve_pending_app_metadata)
from agf.scoring.run_scoring import run_scoring
from agf.tracker.snapshots import snapshot_advertiser_week


def log(msg: str) -> None:
    print(f"[burst {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _extractor():
    from agf.extract.facade import Extractor
    return Extractor()


def _http_client():
    import httpx
    return httpx.Client(timeout=20, follow_redirects=True)


def cmd_backlog(args) -> None:
    engine = get_engine()
    stats = asyncio.run(pull_keeper_history(
        engine, _extractor(), hist_pages=args.hist_pages,
        budget_requests=args.budget, kw_delay=args.kw_delay))
    log(f"backlog: {stats}")


def _extra_entries(path: str) -> list[TaxonomyEntry]:
    import json
    with open(path) as fh:
        rows = json.load(fh)
    return [TaxonomyEntry(r["keyword"], r.get("layer", "niche_term"),
                          r.get("search_type", "KEYWORD_UNORDERED"),
                          int(r.get("max_pages", 3))) for r in rows]


def cmd_sweep(args) -> None:
    engine = get_engine()
    entries = []
    if not args.extra_only:
        entries += all_entries(layer="niche_term") + all_entries(layer="store_fragment")
    if args.extra:
        entries += _extra_entries(args.extra)
    log(f"sweep: {len(entries)} keywords, max_pages={args.pages}")
    with begin(engine) as conn:
        run_id = open_sweep_run(conn)
    try:
        stats = asyncio.run(run_sweep(engine, _extractor(), entries=entries,
                                      max_pages=args.pages, kw_delay=args.kw_delay))
    except Exception as exc:
        with begin(engine) as conn:
            close_sweep_run(conn, run_id, error=repr(exc))
        raise
    with begin(engine) as conn:
        close_sweep_run(conn, run_id, sweep=stats)
    log(f"sweep: {stats}")


def cmd_triage(args) -> None:
    engine = get_engine()
    with _http_client() as client:
        with begin(engine) as conn:
            stats = run_triage(conn,
                               lookup_ios=partial(lookup_itunes_batch, client, pause=3.0),
                               lookup_android=lookup_google_play)
        with begin(engine) as conn:
            resolved = resolve_pending_app_metadata(conn, client)
    log(f"triage: {stats} resolved_app_metadata={resolved}")


def cmd_apps(args) -> None:
    engine = get_engine()
    as_of = date.today()
    from agf.enrich_full.app_refresh import apps_due_for_refresh, refresh_apps
    with engine.connect() as conn:
        due = apps_due_for_refresh(conn, as_of)
    log(f"apps: {len(due)} due for refresh")
    if due:
        with _http_client() as client:
            refreshed = refresh_apps(engine, only_ids=due, itunes_client=client,
                                     snapshot_date=as_of)
        log(f"apps: {refreshed}")


def cmd_score(args) -> None:
    engine = get_engine()
    as_of = date.today()
    week_start = as_of - timedelta(days=as_of.weekday())
    with begin(engine) as conn:
        weeks = snapshot_advertiser_week(conn, week_start, only_ids=None)
    scored = run_scoring(engine, as_of)
    log(f"score: weekly_snapshots={weeks} scored={scored} (matview refreshed)")


def cmd_status(args) -> None:
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT (SELECT count(*) FROM public.advertisers),
                   (SELECT count(DISTINCT advertiser_id) FROM public.ads),
                   (SELECT count(*) FROM discovery.candidates WHERE status='new'),
                   (SELECT count(*) FROM discovery.candidates WHERE status='kept'),
                   (SELECT count(*) FROM adtech.advertiser_scores),
                   (SELECT count(*) FROM adtech.advertiser_scores WHERE vetoed=false),
                   (SELECT count(*) FROM frontend.emerging_scaling_apps)
        """)).one()
    log(f"status: advertisers={row[0]} with_ads={row[1]} cand_new={row[2]} "
        f"cand_kept={row[3]} scored={row[4]} non_vetoed={row[5]} feed={row[6]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("backlog")
    p.add_argument("--budget", type=int, default=4000)
    p.add_argument("--hist-pages", type=int, default=12)
    p.add_argument("--kw-delay", type=float, default=2.0)
    p.set_defaults(fn=cmd_backlog)

    p = sub.add_parser("sweep")
    p.add_argument("--pages", type=int, default=4)
    p.add_argument("--kw-delay", type=float, default=3.0)
    p.add_argument("--extra", help="JSON file of extra keyword entries")
    p.add_argument("--extra-only", action="store_true",
                   help="sweep only the --extra keywords (skip the taxonomy)")
    p.set_defaults(fn=cmd_sweep)

    p = sub.add_parser("triage")
    p.set_defaults(fn=cmd_triage)

    p = sub.add_parser("apps")
    p.set_defaults(fn=cmd_apps)

    p = sub.add_parser("score")
    p.set_defaults(fn=cmd_score)

    p = sub.add_parser("status")
    p.set_defaults(fn=cmd_status)

    args = ap.parse_args()
    try:
        args.fn(args)
    except Exception as exc:
        log(f"FAILED {args.cmd}: {exc!r}")
        raise


if __name__ == "__main__":
    main()
