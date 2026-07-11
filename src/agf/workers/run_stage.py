"""On-demand single-stage runner (spec: docs/superpowers/specs/2026-07-11-stage-runner-design.md).

Runs ONE pipeline stage through the exact functions run_once uses, date gates
bypassed. TEST MODE by default: no *_runs row is written, so a manual run never
satisfies a daily/weekly gate — pass --record to write the real run row.
Defaults come from the same AGF_* envs the worker reads, so inside the prod
container a bare `run_stage <stage>` is a faithful production pass.

Concurrency: does NOT take the worker daemon's pidfile mutex — a small scoped
run alongside the live worker is fine (DataImpulse rotates IPs per connection);
a full-size manual pass while a tick is sweeping doubles Meta traffic.
"""
import argparse
import asyncio
import inspect
import json
import sys
import time
import traceback
from datetime import date
from functools import partial

from agf.db.engine import get_engine, begin
from agf.db.candidates import close_sweep_run, open_sweep_run
from agf.db.dynamic_keywords import (close_flywheel_run, close_release_mint_run,
                                     open_flywheel_run, open_release_mint_run)
from agf.db.funnel_candidates import (close_funnel_sweep_run,
                                      open_funnel_sweep_run)
from agf.db.corpus_refresh_runs import (close_corpus_refresh_run,
                                        open_corpus_refresh_run)
from agf.db.generic_deep_runs import (close_generic_deep_run,
                                      open_generic_deep_run)
from agf.discovery.sweep import run_parallel_sweep
from agf.discovery.triage import run_triage
from agf.discovery.corpus_refresh import refresh_corpus
from agf.discovery.generic_deep import run_generic_deep_stage
from agf.discovery.frame_mint import mint_keywords_from_releases
from agf.enrich_full.appstore import lookup_google_play, lookup_itunes_batch
from agf.scoring.run_scoring import run_scoring
from agf.workers.compute_cycle import run_compute_cycle
from agf.workers.main import (_env_float, _env_int, _gate_keywords,
                             _touch_heartbeat, build_extractor,
                             build_extractors, run_flywheel_stage,
                             run_funnel_stage)


# --- helpers -----------------------------------------------------------------
def _new_http_client():
    """The same sweep-scoped client main() builds for the production triage/enrich/
    mint path (grep `httpx.Client` in main.py)."""
    import httpx
    return httpx.Client(timeout=20, follow_redirects=True)


def _call(fn, *a, **kw):
    """Call a stage fn, awaiting it when it's a coroutine (refresh_corpus is the one
    `async def` stage; run_once wraps it in asyncio.run). Test spies return a plain
    dict — passed straight through."""
    r = fn(*a, **kw)
    if inspect.iscoroutine(r):
        return asyncio.run(r)
    return r


def _maybe_record(engine, record, open_fn, close_fn, body):
    """TEST MODE (default) just runs body(). --record wraps body() in the real
    open/close run-row helpers exactly like run_once: on success close with stats,
    on exception close with error=repr(exc) then re-raise (main() maps that to exit
    1). close_fn(conn, run_id, stats, error) adapts each stage's close signature."""
    if not record:
        return body()
    with begin(engine) as conn:
        run_id = open_fn(conn)
    try:
        stats = body()
    except Exception as exc:
        with begin(engine) as conn:
            close_fn(conn, run_id, None, repr(exc))
        raise
    with begin(engine) as conn:
        close_fn(conn, run_id, stats, None)
    return stats


# --- stage adapters ----------------------------------------------------------
def _stage_sweep(engine, args, *, extractor_factory=None, http_client=None):
    factory = extractor_factory or build_extractors
    extractors = factory(False, int(time.time()), args.workers)

    def body():
        sweep_stats = run_parallel_sweep(
            engine, extractors, max_pages=args.pages, kw_delay=args.kw_delay,
            keywords=args.keywords or None,
            max_consecutive_errors=args.max_consecutive_errors,
            heartbeat=_touch_heartbeat)
        triage_stats = None
        if not args.no_triage:
            client = http_client or _new_http_client()
            try:
                with begin(engine) as conn:
                    triage_stats = run_triage(
                        conn,
                        lookup_ios=partial(lookup_itunes_batch, client, pause=3.0),
                        lookup_android=lookup_google_play)
            finally:
                if http_client is None:
                    client.close()
        return {"sweep": sweep_stats, "triage": triage_stats}

    def _close(conn, run_id, stats, error):
        stats = stats or {}
        close_sweep_run(conn, run_id, sweep=stats.get("sweep"),
                        triage=stats.get("triage"), error=error)

    return _maybe_record(engine, args.record, open_sweep_run, _close, body)


def _stage_flywheel(engine, args, *, extractor_factory=None):
    factory = extractor_factory or build_extractors
    extractors = factory(False, int(time.time()), args.workers)

    def body():
        return run_flywheel_stage(
            engine, extractors,
            max_pages=_env_int("AGF_FLYWHEEL_MAX_PAGES", 2), kw_delay=args.kw_delay,
            mine_min_score=_env_float("AGF_MINE_MIN_SCORE", 40.0),
            max_trials=args.max_trials,
            min_advertisers=_env_int("AGF_MINE_MIN_ADVERTISERS", 3),
            mint_per_pass=args.mint_per_pass,
            retest_per_pass=args.retest_per_pass,
            active_gap_hours=args.active_gap_hours,
            max_consecutive_errors=args.max_consecutive_errors,
            heartbeat=_touch_heartbeat,
            only_keywords=args.keywords or None)

    def _close(conn, run_id, stats, error):
        close_flywheel_run(conn, run_id, stats=stats, error=error)

    return _maybe_record(engine, args.record, open_flywheel_run, _close, body)


def _stage_funnel(engine, args, *, extractor_factory=None):
    factory = extractor_factory or build_extractor
    extractor = factory(False, int(time.time()))

    def body():
        return run_funnel_stage(
            engine, extractor, max_pages=args.pages, kw_delay=args.kw_delay,
            confirm_limit=args.confirm_limit, deep=args.deep,
            deep_pages=_env_int("AGF_FUNNEL_DEEP_PAGES", 100))

    def _close(conn, run_id, stats, error):
        close_funnel_sweep_run(conn, run_id, stats=stats, error=error)

    return _maybe_record(engine, args.record, open_funnel_sweep_run, _close, body)


def _stage_corpus(engine, args, *, extractor_factory=None):
    factory = extractor_factory or build_extractor
    extractor = factory(False, int(time.time()))

    def body():
        return _call(refresh_corpus, engine, extractor,
                     budget_requests=args.budget, hist_pages=12,
                     offset=args.offset, limit=args.limit,
                     heartbeat=_touch_heartbeat)

    def _close(conn, run_id, stats, error):
        close_corpus_refresh_run(conn, run_id, stats=stats, error=error)

    return _maybe_record(engine, args.record, open_corpus_refresh_run, _close, body)


def _stage_generic_deep(engine, args, *, extractor_factory=None):
    factory = extractor_factory or build_extractor
    extractor = factory(False, int(time.time()))

    def body():
        return run_generic_deep_stage(
            engine, extractor, deep_pages=args.pages, kw_delay=args.kw_delay,
            mint_slots=args.mint_slots,
            max_trials=_env_int("AGF_FLYWHEEL_MAX_TRIALS", 40),
            gate=lambda ph, lim: _gate_keywords(engine, ph, limit=lim),
            heartbeat=_touch_heartbeat)

    def _close(conn, run_id, stats, error):
        close_generic_deep_run(conn, run_id, stats=stats, error=error)

    return _maybe_record(engine, args.record, open_generic_deep_run, _close, body)


def _stage_mint(engine, args, *, http_client=None):
    client = http_client or _new_http_client()

    def body():
        return mint_keywords_from_releases(
            engine, client,
            K=_env_int("AGF_FRAME_MINT_K", 15),
            floor=_env_int("AGF_FRAME_MINT_FLOOR", 5),
            total_slots=args.slots,
            min_advertisers=_env_int("AGF_FRAME_MINT_MIN_ADVERTISERS", 2),
            explore_share=_env_float("AGF_FRAME_MINT_EXPLORE_SHARE", 0.25),
            autocomplete=not args.no_autocomplete,
            brainstorm=not args.no_brainstorm,
            new_release=False)

    def _close(conn, run_id, stats, error):
        close_release_mint_run(conn, run_id, stats=stats, error=error)

    try:
        return _maybe_record(engine, args.record, open_release_mint_run, _close, body)
    finally:
        if http_client is None:
            client.close()


def _stage_triage(engine, args, *, http_client=None):
    """No --record: triage has no run table (mirrors run_once's inline triage txn)."""
    client = http_client or _new_http_client()
    try:
        with begin(engine) as conn:
            return run_triage(
                conn, lookup_ios=partial(lookup_itunes_batch, client, pause=3.0),
                lookup_android=lookup_google_play)
    finally:
        if http_client is None:
            client.close()


def _stage_compute(engine, args, *, http_client=None):
    """Thin dispatch to the */15 cron entry point (agf.workers.compute_cycle).
    No run table."""
    client = http_client or _new_http_client()
    try:
        return _call(run_compute_cycle, engine, client, date.today())
    finally:
        if http_client is None:
            client.close()


def _stage_score(engine, args):
    """Thin dispatch to the */30 cron entry point (agf.scoring.run_scoring).
    No run table."""
    return {"scored": run_scoring(engine, date.today())}


STAGES = {
    "sweep": _stage_sweep,
    "flywheel": _stage_flywheel,
    "funnel": _stage_funnel,
    "corpus": _stage_corpus,
    "generic-deep": _stage_generic_deep,
    "mint": _stage_mint,
    "triage": _stage_triage,
    "compute": _stage_compute,
    "score": _stage_score,
}


# --- CLI ---------------------------------------------------------------------
def _parse(argv):
    p = argparse.ArgumentParser(
        prog="run_stage",
        description="Run ONE pipeline stage on demand, gates bypassed.")
    sub = p.add_subparsers(dest="stage", required=True)

    # --record on every stage (accepted after the stage name), so a bare `run_stage
    # flywheel` is test mode and `run_stage flywheel --record` writes the real run row.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--record", action="store_true",
                        help="write the real *_runs row (default: test mode, no row)")

    def _add(name):
        return sub.add_parser(name, parents=[common])

    def _kw(a):
        return [x.strip() for x in a.split(",") if x.strip()]

    sweep = _add("sweep")
    sweep.add_argument("--pages", type=int, default=_env_int("AGF_SWEEP_PAGES", 2))
    sweep.add_argument("--keywords", type=_kw, default=None)
    sweep.add_argument("--workers", type=int, default=_env_int("AGF_SWEEP_WORKERS", 3))
    sweep.add_argument("--no-triage", action="store_true")

    fw = _add("flywheel")
    fw.add_argument("--keywords", type=_kw, default=None)
    fw.add_argument("--max-trials", type=int,
                    default=_env_int("AGF_FLYWHEEL_MAX_TRIALS", 40))
    fw.add_argument("--mint-per-pass", type=int,
                    default=_env_int("AGF_FLYWHEEL_MINT_PER_PASS", 25))
    fw.add_argument("--retest-per-pass", type=int,
                    default=_env_int("AGF_FLYWHEEL_RETEST_PER_PASS", 0))
    fw.add_argument("--active-gap-hours", type=int,
                    default=_env_int("AGF_FLYWHEEL_ACTIVE_GAP_HOURS", 12))
    fw.add_argument("--workers", type=int,
                    default=_env_int("AGF_FLYWHEEL_WORKERS", 3))

    fn = _add("funnel")
    fn.add_argument("--pages", type=int, default=_env_int("AGF_FUNNEL_MAX_PAGES", 8))
    fn.add_argument("--confirm-limit", type=int,
                    default=_env_int("AGF_FUNNEL_CONFIRM_LIMIT", 50))
    fn.add_argument("--deep", action="store_true")

    cp = _add("corpus")
    cp.add_argument("--budget", type=int,
                    default=_env_int("AGF_CORPUS_REFRESH_BUDGET", 3000))
    cp.add_argument("--limit", type=int, default=None)
    cp.add_argument("--offset", type=int, default=0)

    gd = _add("generic-deep")
    gd.add_argument("--pages", type=int, default=_env_int("AGF_GENERIC_DEEP_PAGES", 150))
    gd.add_argument("--mint-slots", type=int,
                    default=_env_int("AGF_GENERIC_DEEP_MINT_SLOTS", 25))

    mint = _add("mint")
    mint.add_argument("--slots", type=int, default=_env_int("AGF_FRAME_MINT_SLOTS", 40))
    mint.add_argument("--no-autocomplete", action="store_true")
    mint.add_argument("--no-brainstorm", action="store_true")

    _add("triage")
    _add("compute")
    _add("score")

    args = p.parse_args(argv)
    # shared knobs read from env once (not per-stage flags — every stage's sweep uses
    # the same worker cadence/breaker/delay that run_once feeds run_once's stages)
    args.kw_delay = _env_float("AGF_KW_DELAY", 3.0)
    args.max_consecutive_errors = _env_int("AGF_SWEEP_MAX_CONSECUTIVE_ERRORS", 5)
    return args


def main(argv=None) -> int:
    args = _parse(sys.argv[1:] if argv is None else argv)
    engine = get_engine()
    try:
        stats = STAGES[args.stage](engine, args)
    except Exception:
        traceback.print_exc()
        return 1
    print(json.dumps(stats, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
