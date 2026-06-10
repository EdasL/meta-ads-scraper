"""One-shot backfill of public.apps.is_game (migration 110; NULL = unknown).

Re-looks-up every apps row with is_game IS NULL and a store_id:
  - ios: batched iTunes Lookup (comma-separated ids, paced <=20 calls/min — the
    same lookup_itunes_batch appstore.py uses for bulk refreshes),
  - android: the gp_lookup.js sidecar, one app per call.

Classification is authoritative-source only (iTunes primaryGenreName/genreIds,
Play genreId) — apps the stores no longer list, or whose lookup carries no genre
info, stay NULL. Idempotent: only NULL rows are selected, so re-runs skip
everything already classified.

Run:
  AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf PYTHONPATH=src \
  .venv/bin/python scripts/backfill_is_game.py
"""
import os
import sys

import httpx
from sqlalchemy import text

from agf.db.engine import get_engine, begin
from agf.enrich_full.appstore import lookup_google_play, lookup_itunes_batch

ITUNES_PAUSE = 3.0   # seconds between batch calls -> <=20 calls/min Lookup limit


def log(m):
    print(f"[backfill_is_game] {m}", flush=True)


def main():
    if not os.environ.get("AGF_DATABASE_URL"):
        log("ERROR: AGF_DATABASE_URL not set")
        sys.exit(2)
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, store, store_id, name FROM public.apps "
            "WHERE is_game IS NULL AND store_id IS NOT NULL ORDER BY id")).all()
    ios = [r for r in rows if r.store == "ios"]
    android = [r for r in rows if r.store == "android"]
    log(f"{len(rows)} apps with is_game IS NULL ({len(ios)} ios, {len(android)} android)")
    if not rows:
        return

    # All store lookups happen BEFORE the write transaction opens: the iTunes batch
    # is paced (seconds) and the gp sidecar is a node subprocess (up to 90s timeout) —
    # neither belongs inside an open transaction holding row locks, and a sidecar
    # crash must not roll back already-collected updates.
    http = httpx.Client(timeout=30, follow_redirects=True)
    itunes = (lookup_itunes_batch(http, [r.store_id for r in ios], pause=ITUNES_PAUSE)
              if ios else {})
    http.close()
    gplay = lookup_google_play([r.store_id for r in android]) if android else {}

    written = 0
    with begin(engine) as conn:
        for r in rows:
            if r.store == "ios":
                meta = itunes.get(str(r.store_id))
            else:
                meta = gplay.get(str(r.store_id))
            is_game = getattr(meta, "is_game", None) if meta else None
            label = f"{r.store} {r.store_id} {r.name or '(unnamed)'}"
            if is_game is None:
                log(f"  {label}: unknown — left NULL")
                continue
            conn.execute(text(
                "UPDATE public.apps SET is_game=:g, updated_at=clock_timestamp() "
                "WHERE id=:i"), {"g": is_game, "i": r.id})
            written += 1
            log(f"  {label}: is_game={is_game}")
    log(f"wrote is_game for {written}/{len(rows)} apps")


if __name__ == "__main__":
    main()
