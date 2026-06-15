"""Force the production enrich handler over an EXPLICIT advertiser set.

The daily worker only re-enriches kept + (alert AND gem_score>=min) advertisers
(eligible_gem_ids). After the funnel-gate migrations (126/127) every legacy
funnel row is kind=NULL and therefore hidden, but most funnel-owning advertisers
are below the alert/score gate — so they would never re-enrich through the loop
and their "Quiz" links would stay dark.

This bypasses the score gate by handing run_worker a fixed candidate_ids list and
due_before=now (everything due), running the SAME probe->classify->gate path as
the worker. Use it to backfill funnels.kind for a known set.

Usage:
  python -m scripts.enrich_advertisers <id> [<id> ...]
  python -m scripts.enrich_advertisers --funnel-feed     # auto-select feed advertisers with a funnels row
"""
from __future__ import annotations
import sys
from datetime import datetime, timezone

from sqlalchemy import text

from agf.db.engine import get_engine
from agf.workers.main import (build_extractor, prefetch_creative_urls,
                              production_deps_factory)
from agf.enrich_full.run_enrich import make_enrich_handler
from agf.workers.runner import run_worker


def _funnel_feed_ids(conn) -> list[int]:
    """Feed advertisers that own at least one captured funnel row — the set whose
    directory 'Quiz' links the gate just hid until reclassification."""
    return [r[0] for r in conn.execute(text(
        "SELECT DISTINCT e.advertiser_id "
        "FROM frontend.emerging_scaling_apps e "
        "JOIN public.ads a ON a.advertiser_id = e.advertiser_id "
        "JOIN public.funnels f ON f.ad_id = a.id "
        "ORDER BY e.advertiser_id")).all()]


def main() -> None:
    args = sys.argv[1:]
    skip_prefetch = "--skip-prefetch" in args
    args = [a for a in args if a != "--skip-prefetch"]
    cap = None
    if "--max" in args:
        i = args.index("--max")
        cap = int(args[i + 1])
        del args[i:i + 2]

    engine = get_engine()
    if args == ["--funnel-feed"]:
        with engine.connect() as conn:
            ids = _funnel_feed_ids(conn)
    else:
        ids = [int(x) for x in args]
    if cap is not None:
        ids = ids[:cap]
    if not ids:
        print("no advertiser ids", file=sys.stderr)
        sys.exit(2)
    print(f"[enrich-adv] forcing enrich over {len(ids)} advertisers: {ids[:12]}"
          f"{'…' if len(ids) > 12 else ''}"
          f"{' (skip-prefetch)' if skip_prefetch else ''}", flush=True)

    import httpx
    now_epoch = int(datetime.now(timezone.utc).timestamp())
    extractor = build_extractor(False, now_epoch)
    http_client = httpx.Client(timeout=20, follow_redirects=True)
    try:
        # Media prefetch front-loads one slow Meta search per advertiser BEFORE any
        # classification runs — fine for the daily media pass, but for a funnel-gate
        # backfill it just delays the probe→classify we actually care about (and one
        # bad proxy IP wedges the whole batch). --skip-prefetch goes straight to
        # per-advertiser enrich; media is recovered by the normal media-repair stage.
        if skip_prefetch:
            creative_urls: dict = {}
        else:
            with engine.connect() as conn:
                creative_urls = prefetch_creative_urls(extractor, conn, ids)
        deps_factory = production_deps_factory(extractor, http_client, creative_urls)
        # due_before = now → every row in the set is due regardless of last crawl.
        due_before = datetime.now(timezone.utc)
        enriched = run_worker(
            "enrich", "advertiser",
            make_enrich_handler(deps_factory, top_n=5, media_cap=120),
            source=engine, candidate_ids=ids,
            due_before=due_before, limit=len(ids))
    finally:
        http_client.close()
    print(f"[enrich-adv] enriched={enriched}", flush=True)


if __name__ == "__main__":
    main()
