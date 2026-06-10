"""Backfill true active-ad counts (advertisers.meta_active_ads) via bounded
view_all_page_id pagination with early exit (the "N ads in library" detail-page
signal; capped=TRUE means "cap+", the incumbent veto).

Talks to the Meta Ad Library through the throttle-aware Extractor facade, so it
needs residential egress: export AGF_PROXIES (see ops/RUNBOOK.md §3). Re-checks an
advertiser at most once per --ttl-hours unless --ids forces it.

    export AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf
    export AGF_PROXIES=http://user:pass@gw.dataimpulse.com:823
    PYTHONPATH=src .venv/bin/python scripts/count_ads.py --limit 20
    PYTHONPATH=src .venv/bin/python scripts/count_ads.py --ids 12,34 --cap 250
"""
import argparse
import asyncio
import os
import sys

from sqlalchemy import text

from agf.db import repo
from agf.db.engine import get_engine, begin


def _db_url() -> str:
    url = os.environ.get("AGF_DATABASE_URL") or os.environ.get("AGF_TEST_DB_URL")
    if not url:
        sys.exit("set AGF_DATABASE_URL (or AGF_TEST_DB_URL), e.g. "
                 "postgresql://agf:agf@localhost:5433/agf")
    return url


def _select_targets(conn, ids, limit, ttl_hours):
    """(id, page_id) rows to count. --ids forces those rows; otherwise non-DEMO
    advertisers whose count is missing or older than the TTL, freshest-seen first."""
    if ids:
        return conn.execute(text(
            "SELECT id, page_id FROM public.advertisers "
            "WHERE id = ANY(:ids) AND page_id IS NOT NULL AND page_id NOT LIKE 'DEMO%'"),
            {"ids": ids}).all()
    return conn.execute(text("""
        SELECT id, page_id FROM public.advertisers
        WHERE page_id IS NOT NULL AND page_id NOT LIKE 'DEMO%'
          AND (meta_active_ads_checked_at IS NULL
               OR meta_active_ads_checked_at < now() - :ttl * interval '1 hour')
        ORDER BY last_seen DESC NULLS LAST, id
        LIMIT :limit
    """), {"ttl": ttl_hours, "limit": limit}).all()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ids", help="comma-separated advertiser ids (forces re-check)")
    ap.add_argument("--limit", type=int, default=20,
                    help="max advertisers per run (default 20)")
    ap.add_argument("--cap", type=int, default=250,
                    help="early-exit ad-count cap (default 250)")
    ap.add_argument("--ttl-hours", type=float, default=24.0,
                    help="skip advertisers checked within this window (default 24)")
    args = ap.parse_args()

    from agf.extract.facade import Extractor
    from agf.extract.proxy import ProxyPool

    pool = ProxyPool.from_env()
    print(f"[count_ads] proxy pool size: {len(pool)}", flush=True)
    extractor = Extractor(proxies=pool)
    engine = get_engine(_db_url())

    ids = [int(x) for x in args.ids.split(",") if x.strip()] if args.ids else None
    with engine.connect() as conn:
        targets = _select_targets(conn, ids, args.limit, args.ttl_hours)
    print(f"[count_ads] {len(targets)} advertiser(s) to count", flush=True)

    done = failed = 0
    for adv_id, page_id in targets:
        try:
            n_ads, capped = asyncio.run(
                extractor.count_active_ads(page_id=page_id, cap=args.cap))
        except Exception as exc:
            failed += 1
            print(f"[count_ads]   advertiser {adv_id} (page {page_id}) FAILED: {exc!r}",
                  flush=True)
            continue
        with begin(engine) as conn:
            repo.set_meta_active_ads(conn, adv_id, n_ads, capped)
        done += 1
        print(f"[count_ads]   advertiser {adv_id}: {n_ads}{'+' if capped else ''} "
              f"active ads in library", flush=True)
    print(f"[count_ads] done: {done} counted, {failed} failed", flush=True)


if __name__ == "__main__":
    main()
