"""Human-approval gate for taste-mined keywords. Lists pending phrases (with
rationale + inspired-by app names) and promotes approved ones to 'trial', which
admits them into the existing keyword flywheel.

Run (list):     PYTHONPATH=src AGF_DATABASE_URL=... .venv/bin/python scripts/review_mined_keywords.py
Run (promote):  ... scripts/review_mined_keywords.py --promote "ai stem separation"
"""
import sys

from sqlalchemy import text

from agf.db.engine import begin, get_engine

_PENDING = text(
    "SELECT keyword, status, rationale, inspired_by FROM discovery.dynamic_keywords "
    "WHERE source='liked-mining' AND status='pending' ORDER BY created_at DESC")

_PROMOTE = text(
    "UPDATE discovery.dynamic_keywords SET status='trial' "
    "WHERE keyword=:k AND status='pending'")


def list_pending(conn) -> list[dict]:
    return [{"keyword": r.keyword, "status": r.status, "rationale": r.rationale,
             "inspired_by": list(r.inspired_by or [])}
            for r in conn.execute(_PENDING)]


def promote(conn, keyword: str) -> int:
    return conn.execute(_PROMOTE, {"k": keyword}).rowcount


def main() -> int:
    with begin(get_engine()) as conn:
        if len(sys.argv) >= 3 and sys.argv[1] == "--promote":
            n = promote(conn, sys.argv[2])
            print(f"promoted {n} keyword(s) to trial", flush=True)
        else:
            for p in list_pending(conn):
                print(f"  {p['keyword']!r:40s}  {p['rationale']}  (apps={p['inspired_by']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
