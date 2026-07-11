"""benchmark_sweep_pool.py — bounded live benchmark of the dynamic sweep pool.

Runs the real build_extractors()/run_parallel_sweep() path (no worker loop, no
run rows) over an explicit keyword set and prints one JSON report: elapsed time,
pages, errors, candidates, per-lane telemetry, and the derived rates used to
compare scheduler variants (pages/hour, candidates per 1,000 requests).

Comparison protocol (docs/scraping-overview.md §Benchmarking): run the SAME
keywords, --pages, proxy set, and pacing env at --workers 1 (serial baseline)
and --workers N (dynamic pool), and judge throughput ONLY at stable safety
metrics — a faster run that raises the error/challenge rate is not a win.

Refuses to run without AGF_PROXIES (a bare host IP eats a rate-limit ban for
zero concurrency benefit) unless --allow-direct is passed explicitly.

Run:
    PYTHONPATH=src AGF_DATABASE_URL=... AGF_PROXIES=... \\
    .venv/bin/python -m scripts.benchmark_sweep_pool \\
        --keywords "meal planner, habit tracker, sleep coach" --pages 3 --workers 3
"""
import argparse
import json
import sys
import time

from agf.db.engine import get_engine
from agf.discovery.sweep import run_parallel_sweep
from agf.discovery.taxonomy import TaxonomyEntry
from agf.extract.proxy import ProxyPool
from agf.workers.main import build_extractors


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Bounded live benchmark of the dynamic sweep pool.")
    p.add_argument("--keywords", required=True,
                   help="comma-separated keyword list swept as one bounded pass")
    p.add_argument("--pages", required=True, type=int,
                   help="page-depth cap per keyword (max_depth AND max_pages)")
    p.add_argument("--workers", required=True, type=int,
                   help="extractor lanes; build_extractors slices AGF_PROXIES")
    p.add_argument("--kw-delay", type=float, default=3.0,
                   help="per-lane pause after each completed keyword (default 3.0)")
    p.add_argument("--search-type", default="KEYWORD_UNORDERED",
                   choices=["KEYWORD_UNORDERED", "KEYWORD_EXACT_PHRASE"])
    p.add_argument("--allow-direct", action="store_true",
                   help="permit running without AGF_PROXIES (direct host egress)")
    args = p.parse_args(argv)
    args.keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    if not args.keywords:
        print("no keywords after parsing --keywords", file=sys.stderr)
        return 2
    if not ProxyPool.list_from_env() and not args.allow_direct:
        print("refusing to run without AGF_PROXIES (pass --allow-direct to "
              "override; direct host egress risks a rate-limit ban)",
              file=sys.stderr)
        return 2
    try:
        engine = get_engine()
    except RuntimeError as exc:
        print(f"database URL required: {exc}", file=sys.stderr)
        return 2

    entries = [TaxonomyEntry(kw, "benchmark", args.search_type, args.pages)
               for kw in args.keywords]
    extractors = build_extractors(False, int(time.time()), args.workers)

    started = time.perf_counter()
    stats = run_parallel_sweep(
        engine, extractors, entries=entries,
        max_pages=args.pages, max_depth=args.pages,
        kw_delay=args.kw_delay, track_pages=True)
    elapsed = time.perf_counter() - started

    serp = stats["serp_requests"]
    report = {
        "elapsed_seconds": round(elapsed, 2),
        "workers_requested": args.workers,
        "lanes": len(extractors),
        "keywords": len(args.keywords),
        "keywords_swept": stats["keywords_swept"],
        "serp_requests": serp,
        "errors": stats["errors"],
        "raw_ads": stats["raw_ads"],
        "candidates_new": stats["candidates_new"],
        "candidates_seen": stats["candidates_seen"],
        "funnel_new": stats["funnel_new"],
        "pages_per_hour": round(serp / elapsed * 3600, 1) if elapsed else 0.0,
        "candidates_per_1000_requests": (
            round((stats["candidates_new"] + stats["candidates_seen"])
                  / serp * 1000, 1) if serp else 0.0),
        "telemetry": stats.get("telemetry"),
        "page_log": stats.get("page_log"),
    }
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
