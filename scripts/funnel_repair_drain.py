# scripts/funnel_repair_drain.py
"""One-time drain of the funnel-coverage backlog: probe feed apps that run an
off-store web ad but show no Quiz link, record the funnel, and refresh the matview.

Same path the worker's per-cycle funnel-repair stage runs, just unbounded-ish here so
the existing 68-app backlog clears at once instead of a few per cycle.

Run (from repo root):
    set -a; . ops/.env; set +a            # AGF_PROXIES for Meta-side deeplink resolve (optional)
    AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf AGF_FUNNEL_DRAIN_LIMIT=5 \
    PYTHONPATH=src .venv/bin/python scripts/funnel_repair_drain.py

Needs the sandbox OFF (real network: deeplink resolve + Playwright landing render).
Renders go DIRECT (no proxy). S3 is optional — unconfirmed funnels store no screenshot.
"""
import os

from sqlalchemy import text

from agf.db.engine import get_engine
from agf.enrich_full.run_enrich import EnrichDeps
from agf.enrich_full.funnel_repair import (funnel_gap_advertisers,
                                           repair_advertiser_funnels)
from agf.extract.facade import Extractor


def _feed_funnel_count(engine) -> int:
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT count(*) FROM frontend.emerging_scaling_apps "
            "WHERE funnel_url IS NOT NULL AND funnel_url <> ''")).scalar()


def _noop_put(key, data, content_type, client=None):
    return None


def main() -> None:
    engine = get_engine()
    extractor = Extractor(search_delay=2.0, search_jitter=1.5)
    try:
        from agf.storage.s3 import get_s3, put_bytes
        s3, put = get_s3(), put_bytes
    except Exception:                      # S3 optional: unconfirmed funnels take no shot
        s3, put = None, _noop_put
    deps = EnrichDeps(extractor=extractor, put=put, s3=s3,
                      proxy=os.environ.get("AGF_FUNNEL_PROXY"), capture=True)

    limit = int(os.environ.get("AGF_FUNNEL_DRAIN_LIMIT", "10"))
    before = _feed_funnel_count(engine)
    with engine.connect() as conn:
        gap_ids = funnel_gap_advertisers(conn, limit)
    print(f"feed funnel links before: {before}")
    print(f"gap advertisers to probe: {len(gap_ids)} (limit {limit}) -> {gap_ids}")

    stats: dict = {}
    for aid in gap_ids:
        repair_advertiser_funnels(engine, deps, aid, stats,
                                  log=lambda m: print(m, flush=True))
        print(f"  advertiser {aid} done; running stats {stats}", flush=True)

    with engine.connect() as conn:
        conn.execute(text("REFRESH MATERIALIZED VIEW frontend.emerging_scaling_apps"))
        conn.commit()
    after = _feed_funnel_count(engine)
    print(f"repair stats: {stats}")
    print(f"feed funnel links after:  {after}  (+{after - before})")


if __name__ == "__main__":
    main()
