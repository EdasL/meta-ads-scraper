"""Backfill public.apps.description for apps that predate the description column.

Re-looks-up every app whose description IS NULL, batched per store (iTunes Lookup for
iOS, the google-play sidecar for Android), and UPDATEs the row (capped, COALESCE so a
NULL lookup never wipes an existing value). Idempotent — re-running only touches rows
still missing a description.

    export AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf
    .venv/bin/python scripts/backfill_descriptions.py
"""
import os
import sys

import httpx
from sqlalchemy import text

from agf.db.engine import get_engine, begin
from agf.db.repo import _cap_description
from agf.enrich_full.appstore import lookup_itunes_batch, lookup_google_play


def _db_url() -> str:
    url = os.environ.get("AGF_DATABASE_URL") or os.environ.get("AGF_TEST_DB_URL")
    if not url:
        sys.exit("set AGF_DATABASE_URL (or AGF_TEST_DB_URL), e.g. "
                 "postgresql://agf:agf@localhost:5433/agf")
    return url


def backfill(engine) -> int:
    """UPDATE apps with a NULL description from a fresh store lookup. Returns rows filled."""
    with begin(engine) as conn:
        rows = conn.execute(text(
            "SELECT id, store, store_id FROM public.apps WHERE description IS NULL")).all()
        if not rows:
            return 0
        ios = [sid for _, store, sid in rows if store == "ios"]
        android = [sid for _, store, sid in rows if store == "android"]
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            itunes = lookup_itunes_batch(client, ios) if ios else {}
        gplay = lookup_google_play(android) if android else {}
        n = 0
        for app_id, store, store_id in rows:
            meta = itunes.get(str(store_id)) if store == "ios" else gplay.get(str(store_id))
            desc = _cap_description(getattr(meta, "description", None)) if meta else None
            if desc is None:
                continue
            conn.execute(text(
                "UPDATE public.apps SET description=:desc, updated_at=clock_timestamp() "
                "WHERE id=:id"), {"desc": desc, "id": app_id})
            n += 1
    return n


def main() -> None:
    engine = get_engine(_db_url())
    n = backfill(engine)
    print(f"backfilled descriptions for {n} app(s).")


if __name__ == "__main__":
    main()
