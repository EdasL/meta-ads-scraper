"""Repo for the web-funnel discovery pool (docs/research/2026-06-11-funnel-discovery-method).
Sibling to db/candidates.py; append-mostly, conn-first (callers own the txn). A re-seen
(page_id, landing_domain) bumps times_seen/last_seen/likes and NEVER regresses status."""
import json

from sqlalchemy import text

_COLS = ("page_id, page_name, page_like_count, landing_url, landing_domain, funnel_host, "
         "cta_type, utm_source, utm_medium, utm_campaign, utm_content, ad_start_date, "
         "source_keyword, technique, body_text")


def upsert_funnel_candidate(conn, row: dict) -> tuple[int, bool]:
    """Insert or bump one funnel candidate. Returns (id, was_new). body_text is the ad
    copy the funnel keyword flywheel mines for new phrases; backfilled on re-seen rows."""
    row = {"body_text": None, **row}                 # optional; default NULL
    got = conn.execute(text(f"""
        INSERT INTO discovery.funnel_candidates ({_COLS})
        VALUES (:page_id, :page_name, :page_like_count, :landing_url, :landing_domain,
                :funnel_host, :cta_type, :utm_source, :utm_medium, :utm_campaign,
                :utm_content, :ad_start_date, :source_keyword, :technique, :body_text)
        ON CONFLICT (page_id, landing_domain) DO UPDATE SET
          times_seen = discovery.funnel_candidates.times_seen + 1,
          last_seen_at = now(),
          page_like_count = COALESCE(EXCLUDED.page_like_count,
                                     discovery.funnel_candidates.page_like_count),
          body_text = COALESCE(discovery.funnel_candidates.body_text, EXCLUDED.body_text)
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


def mark_funnel_confirmed(conn, candidate_id: int, *, kind: str, status: str,
                          store: str | None = None, store_id: str | None = None,
                          drop_reason: str | None = None,
                          quiz_steps: int | None = None,
                          payoff_kind: str | None = None) -> None:
    """Write the confirm-stage verdict onto a funnel candidate."""
    conn.execute(text("""
        UPDATE discovery.funnel_candidates
           SET kind=:k, status=:s, store=:st, store_id=:sid,
               drop_reason=:r, quiz_steps=:qs, confirmed_at=now(),
               payoff_kind=:pk
         WHERE id=:i
    """), {"k": kind, "s": status, "st": store, "sid": store_id,
           "r": drop_reason, "qs": quiz_steps, "pk": payoff_kind, "i": candidate_id})


def funnel_sweep_ran_today(conn, as_of) -> bool:
    """True when a funnel run FINISHED on `as_of` (a crashed run doesn't block the day).
    Mirrors db/candidates.sweep_ran_today — the worker's daily gate for the funnel stage."""
    return bool(conn.execute(text(
        "SELECT EXISTS(SELECT 1 FROM discovery.funnel_sweep_runs "
        "WHERE started_at::date = :d AND finished_at IS NOT NULL)"),
        {"d": as_of}).scalar())


def funnel_sweep_ran_within(conn, seconds: int) -> bool:
    """True when a funnel run FINISHED within the last `seconds`. The time-based gate
    for a continuous cadence (several funnel runs/day) instead of once-per-calendar-day.
    A crashed/open run doesn't count. Mirrors dynamic_keywords.flywheel_ran_within."""
    return bool(conn.execute(text(
        "SELECT EXISTS(SELECT 1 FROM discovery.funnel_sweep_runs "
        "WHERE finished_at IS NOT NULL "
        "AND finished_at > now() - make_interval(secs => :s))"),
        {"s": seconds}).scalar())


def open_funnel_sweep_run(conn) -> int:
    return conn.execute(text(
        "INSERT INTO discovery.funnel_sweep_runs DEFAULT VALUES RETURNING id")).scalar_one()


def close_funnel_sweep_run(conn, run_id: int, *, stats: dict | None = None,
                           error: str | None = None) -> None:
    conn.execute(text(
        "UPDATE discovery.funnel_sweep_runs SET finished_at = now(), "
        "stats = CAST(:stats AS jsonb), error = :err WHERE id = :id"),
        {"stats": json.dumps(stats) if stats is not None else None,
         "err": error, "id": run_id})


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
