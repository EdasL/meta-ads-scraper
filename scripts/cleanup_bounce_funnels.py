"""One-time backfill: drop store-bounce rows from public.funnels (AppsFlyer/Branch/
appstack/onelink/lnk.to redirects mis-recorded as funnels) and refresh the feed matview.
The write gate (record_web_funnel + is_store_bounce) keeps them out on re-enrich, and
migration 138's funnel_url regex already hides them from the feed — this cleans the
underlying rows too. Idempotent: a second run deletes 0.

Run:
    PYTHONPATH=src AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf \\
    .venv/bin/python scripts/cleanup_bounce_funnels.py
"""
import sys

from sqlalchemy import text

from agf.db.engine import begin, get_engine

# Mirrors agf.discovery.funnel_classify.STORE_BOUNCE_HOSTS and migration 138's regex.
_BOUNCE_RE = (
    r'^https?://([a-z0-9.-]+\.)?(appstack\.link|onelink\.me|onelink\.to|app\.link|'
    r'go\.link|adj\.st|bnc\.lt|adjust\.com|appsflyer\.com|kochava\.com|singular\.net|'
    r'lnk\.to|apple\.co)')


def main() -> int:
    eng = get_engine()
    with begin(eng) as conn:
        deleted = conn.execute(text(
            "DELETE FROM public.funnels "
            "WHERE coalesce(final_url_after_redirect, landing_url) ~* :re"),
            {"re": _BOUNCE_RE}).rowcount
    # CONCURRENTLY needs its own (autocommit) connection, outside a txn block.
    with eng.connect() as conn:
        conn.execute(text("COMMIT"))
        conn.execute(text(
            "REFRESH MATERIALIZED VIEW CONCURRENTLY frontend.emerging_scaling_apps"))
    print(f"[cleanup] deleted {deleted} store-bounce funnel rows; matview refreshed",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
