"""Live one-keyword smoke of sweep -> triage (spec 2026-06-10). Run before calling
the pipeline done — fakes have missed assembly breaks 4x in this repo.

    AGF_PROXIES=http://...@gw.dataimpulse.com:823 \
    AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf \
    PYTHONPATH=src .venv/bin/python scripts/sweep_smoke.py
"""
import asyncio
import os
import sys
from functools import partial

import httpx
from sqlalchemy import text

from agf.db.engine import begin, get_engine
from agf.discovery.sweep import run_sweep
from agf.discovery.taxonomy import sweep_entries
from agf.discovery.triage import run_triage
from agf.enrich_full.appstore import lookup_google_play, lookup_itunes_batch
from agf.extract.facade import Extractor

KEYWORD = os.environ.get("AGF_SMOKE_KEYWORD", "pelvic floor app")

if not os.environ.get("AGF_PROXIES"):
    sys.exit("AGF_PROXIES required (residential).")
engine = get_engine()
entries = [e for e in sweep_entries() if e.keyword == KEYWORD]
assert entries, f"{KEYWORD!r} not in sweep taxonomy"

print(f"sweeping {KEYWORD!r} (US, 2 pages) ...")
stats = asyncio.run(run_sweep(engine, Extractor(), entries=entries))
print("sweep:", stats)

with httpx.Client(timeout=20, follow_redirects=True) as client, begin(engine) as conn:
    tstats = run_triage(conn, lookup_ios=partial(lookup_itunes_batch, client, pause=3.0),
                        lookup_android=lookup_google_play)
print("triage:", tstats)

with engine.connect() as conn:
    rows = conn.execute(text(
        "SELECT page_name, store, store_id, page_like_count, status, drop_reason "
        "FROM discovery.candidates WHERE source_keyword=:k "
        "ORDER BY last_seen_at DESC LIMIT 12"), {"k": KEYWORD}).all()
for r in rows:
    print(f"  {r.status:8} {r.drop_reason or '-':24} {r.store}:{r.store_id:30} "
          f"likes={r.page_like_count} {r.page_name}")
assert stats["candidates_new"] + stats["candidates_seen"] >= 1, "sweep found nothing"
print("SMOKE OK")
