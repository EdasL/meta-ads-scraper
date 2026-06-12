# scripts/run_funnel_confirm.py
"""Confirm + label the new funnel candidates (render landing -> fingerprint -> verdict).
Run (from repo root):
    set -a; . ops/.env; set +a   # AGF_PROXIES for render + resolve
    AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf AGF_CONFIRM_LIMIT=20 \
    PYTHONPATH=src .venv/bin/python scripts/run_funnel_confirm.py
Needs the sandbox OFF (real network). Wires the async Extractor.resolve_deeplink and the
sync Playwright render into the sync orchestrator."""
import asyncio
import os

from sqlalchemy import text

from agf.db.engine import get_engine
from agf.discovery.funnel_confirm import run_funnel_confirm
from agf.enrich_full.funnel_capture import render_landing_html
from agf.extract.facade import Extractor

PROXY = os.environ.get("AGF_PROXIES", "").split(",")[0] or None


def main() -> None:
    engine = get_engine()
    extractor = Extractor(search_delay=2.0, search_jitter=1.5)

    def resolve(url):
        return asyncio.run(extractor.resolve_deeplink(url)).intent  # -> AppIntent{kind,store_id}

    def render(url):
        return render_landing_html(url, proxy=PROXY)

    limit = int(os.environ.get("AGF_CONFIRM_LIMIT", "0")) or None
    stats = run_funnel_confirm(engine, resolve=resolve, render=render, limit=limit)
    print("confirm:", stats)
    with engine.connect() as conn:
        for r in conn.execute(text(
            "SELECT kind, status, landing_domain, store, store_id FROM discovery.funnel_candidates "
            "WHERE confirmed_at IS NOT NULL ORDER BY confirmed_at DESC LIMIT 30")).all():
            print(f"  {r.status:9s} {r.kind:9s} {r.landing_domain:34s} {r.store or ''} {r.store_id or ''}")


if __name__ == "__main__":
    main()
