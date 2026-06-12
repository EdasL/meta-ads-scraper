"""Repo for the web-funnel discovery pool (docs/research/2026-06-11-funnel-discovery-method).
Sibling to db/candidates.py; append-mostly, conn-first (callers own the txn). A re-seen
(page_id, landing_domain) bumps times_seen/last_seen/likes and NEVER regresses status."""
from sqlalchemy import text

_COLS = ("page_id, page_name, page_like_count, landing_url, landing_domain, funnel_host, "
         "cta_type, utm_source, utm_medium, utm_campaign, utm_content, ad_start_date, "
         "source_keyword, technique")


def upsert_funnel_candidate(conn, row: dict) -> tuple[int, bool]:
    """Insert or bump one funnel candidate. Returns (id, was_new)."""
    got = conn.execute(text(f"""
        INSERT INTO discovery.funnel_candidates ({_COLS})
        VALUES (:page_id, :page_name, :page_like_count, :landing_url, :landing_domain,
                :funnel_host, :cta_type, :utm_source, :utm_medium, :utm_campaign,
                :utm_content, :ad_start_date, :source_keyword, :technique)
        ON CONFLICT (page_id, landing_domain) DO UPDATE SET
          times_seen = discovery.funnel_candidates.times_seen + 1,
          last_seen_at = now(),
          page_like_count = COALESCE(EXCLUDED.page_like_count,
                                     discovery.funnel_candidates.page_like_count)
        RETURNING id, (xmax = 0) AS was_new
    """), row).one()
    return got.id, bool(got.was_new)


def new_funnel_candidates(conn, limit: int | None = None):
    """Unconfirmed funnel candidates, oldest first (the confirm-stage feed)."""
    q = ("SELECT id, page_id, page_name, page_like_count, landing_url, landing_domain, "
         "funnel_host, cta_type, times_seen, source_keyword "
         "FROM discovery.funnel_candidates WHERE status = 'new' ORDER BY first_seen_at, id")
    if limit:
        q += f" LIMIT {int(limit)}"
    return conn.execute(text(q)).all()


def cluster_by_domain(conn, min_advertisers: int = 2):
    """Landing domains pushed by >= min_advertisers distinct pages — shared-funnel /
    affiliate-network clusters. Ordered by advertiser count desc."""
    return conn.execute(text("""
        SELECT landing_domain,
               count(DISTINCT page_id) AS n_advertisers,
               array_agg(DISTINCT page_id) AS page_ids
        FROM discovery.funnel_candidates
        GROUP BY landing_domain
        HAVING count(DISTINCT page_id) >= :m
        ORDER BY n_advertisers DESC, landing_domain
    """), {"m": min_advertisers}).all()
