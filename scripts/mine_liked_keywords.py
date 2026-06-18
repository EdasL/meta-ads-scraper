"""Mine taste-aligned seed keywords from a user's liked apps' content. Proposed
phrases land as discovery.dynamic_keywords status='pending' (the sweep ignores
them) until promoted by scripts/review_mined_keywords.py.

Run:
    PYTHONPATH=src AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf \\
    .venv/bin/python scripts/mine_liked_keywords.py [user_email]
"""
import sys

from sqlalchemy import text

from agf.db.engine import begin, get_engine
from agf.discovery.liked_keyword_mining import dedup_candidates, propose_phrases

_LIKED = text(
    "SELECT m.advertiser_id, m.app_name AS name, a.description "
    "FROM frontend.app_ratings r "
    "JOIN frontend.emerging_scaling_apps m ON m.advertiser_id = r.advertiser_id "
    "LEFT JOIN public.apps a ON a.id = m.app_id "
    "WHERE r.user_email = :e AND r.rating = 'like'")

_COPY = text(
    "SELECT c.body_text FROM public.ads ad JOIN public.creatives c ON c.ad_id=ad.id "
    "WHERE ad.advertiser_id = :a AND c.body_text IS NOT NULL AND c.body_text <> '' "
    "ORDER BY ad.meta_rank ASC NULLS LAST, ad.last_seen DESC LIMIT 5")

_EXISTING = text("SELECT keyword FROM discovery.dynamic_keywords")

_INSERT = text(
    "INSERT INTO discovery.dynamic_keywords "
    "(keyword, source, status, kind, rationale, inspired_by, created_at) "
    "VALUES (:k, 'liked-mining', 'pending', 'app', :r, :ib, now()) "
    "ON CONFLICT (keyword, kind) DO NOTHING")


def _liked_apps(conn, user_email):
    apps = []
    for r in conn.execute(_LIKED, {"e": user_email}):
        copy = [b for (b,) in conn.execute(_COPY, {"a": r.advertiser_id})]
        apps.append({"advertiser_id": r.advertiser_id, "name": r.name or "",
                     "description": r.description or "", "ad_copy": copy})
    return apps


def mine(conn, user_email: str, *, propose_fn=propose_phrases) -> int:
    apps = _liked_apps(conn, user_email)
    if not apps:
        print(f"[mine] {user_email}: no liked apps", flush=True)
        return 0
    cands = propose_fn(apps)
    existing = {k for (k,) in conn.execute(_EXISTING)}
    fresh = dedup_candidates(cands, existing)
    for c in fresh:
        conn.execute(_INSERT, {"k": c.phrase, "r": c.rationale, "ib": c.inspired_by})
    print(f"[mine] {user_email}: proposed={len(cands)} new_pending={len(fresh)}", flush=True)
    return len(fresh)


def main() -> int:
    email = sys.argv[1] if len(sys.argv) > 1 else "founder@fielddesk.local"
    with begin(get_engine()) as conn:
        mine(conn, email)
    return 0


if __name__ == "__main__":
    sys.exit(main())
