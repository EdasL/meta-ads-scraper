"""Live funnel-phrase sweep -> discovery.funnel_candidates, then print domain clusters.

Run (from repo root):
    set -a; . ops/.env; set +a   # loads AGF_PROXIES (DataImpulse)
    AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf \
    AGF_FUNNEL_MAX_KW=8 AGF_SWEEP_PAGES=2 PYTHONPATH=src \
        .venv/bin/python scripts/run_funnel_sweep.py

Env knobs: AGF_FUNNEL_MAX_KW (cap keywords, 0=all), AGF_SWEEP_PAGES (max pages/keyword,
default 3), AGF_KW_DELAY (inter-keyword pause s, default 3). Mirrors the Extractor
construction in scripts/discover_experiment.py."""
import asyncio
import os

from agf.db.engine import get_engine
from agf.db.funnel_candidates import cluster_by_domain, new_funnel_candidates
from agf.discovery.funnel_sweep import run_funnel_sweep
from agf.discovery.taxonomy import all_entries
from agf.extract.facade import Extractor


async def main() -> None:
    engine = get_engine()
    entries = all_entries(layer="funnel_phrase")
    max_kw = int(os.environ.get("AGF_FUNNEL_MAX_KW", "0"))
    if max_kw:
        entries = entries[:max_kw]
    pages = int(os.environ.get("AGF_SWEEP_PAGES", "3"))
    kw_delay = float(os.environ.get("AGF_KW_DELAY", "3.0"))

    extractor = Extractor(search_delay=2.0, search_jitter=1.5)   # reads AGF_PROXIES from env
    print(f"sweeping {len(entries)} funnel-phrase keywords, max_pages={pages} ...")
    stats = await run_funnel_sweep(engine, extractor, entries=entries,
                                   max_pages=pages, kw_delay=kw_delay)
    print("sweep:", stats)

    with engine.connect() as conn:
        rows = new_funnel_candidates(conn)
        clusters = cluster_by_domain(conn, min_advertisers=2)
    print(f"\n{len(rows)} funnel candidates in the pool. Sample:")
    for r in rows[:25]:
        print(f"  {r.landing_domain:38s}  host={r.funnel_host or '-':12s}  {r.page_name}")
    print(f"\nshared-domain clusters ({len(clusters)}):")
    for c in clusters[:40]:
        print(f"  {c.n_advertisers:3d}  {c.landing_domain}")


if __name__ == "__main__":
    asyncio.run(main())
