"""Fetch + embed kept candidates' store listings into discovery.candidate_embeddings.

Run:
    PYTHONPATH=src AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf \\
    .venv/bin/python scripts/embed_candidates.py
"""
import sys

import httpx

from agf.db.engine import get_engine
from agf.discovery.candidate_embed import embed_candidates


def main() -> int:
    eng = get_engine()
    with httpx.Client(timeout=30.0, headers={"User-Agent": "Mozilla/5.0"}) as client, \
            eng.connect() as conn:
        try:
            stats = embed_candidates(conn, itunes_client=client, commit_every=25)
            conn.commit()
        except Exception as exc:
            conn.commit()
            print(f"[embed_cand] stopped early ({exc!r}); progress saved", flush=True)
            return 1
    print(f"[embed_cand] {stats}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
