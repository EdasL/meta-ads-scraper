"""Repo for the breadth-first discovery pool (spec 2026-06-10).

discovery.candidates is append-mostly: a re-seen candidate bumps last_seen_at /
times_seen / page_like_count but NEVER regresses status — a dropped candidate is
never re-triaged. conn-first, no commits (callers own the transaction)."""
import json
from sqlalchemy import text


def upsert_sweep_candidate(conn, row: dict) -> tuple[int, bool]:
    """Insert or bump one swept candidate. Returns (id, was_new)."""
    got = conn.execute(text("""
        INSERT INTO discovery.candidates
          (page_id, page_name, page_like_count, store, store_id, link_url,
           cta_type, ad_start_date, source_keyword, technique)
        VALUES (:page_id, :page_name, :page_like_count, :store, :store_id, :link_url,
                :cta_type, :ad_start_date, :source_keyword, :technique)
        ON CONFLICT (page_id, store, store_id) DO UPDATE SET
          times_seen      = discovery.candidates.times_seen + 1,
          last_seen_at    = now(),
          page_like_count = COALESCE(EXCLUDED.page_like_count,
                                     discovery.candidates.page_like_count)
        RETURNING id, (xmax = 0) AS was_new
    """), row).one()
    return got.id, bool(got.was_new)


def new_candidates(conn, limit: int | None = None):
    """Untriaged candidates, oldest first."""
    q = ("SELECT id, page_id, page_name, page_like_count, store, store_id, "
         "times_seen, source_keyword FROM discovery.candidates "
         "WHERE status = 'new' ORDER BY first_seen_at, id")
    if limit:
        q += f" LIMIT {int(limit)}"
    return conn.execute(text(q)).all()


def mark_triaged(conn, candidate_id: int, *, status: str,
                 drop_reason: str | None = None,
                 kept_advertiser_id: int | None = None) -> None:
    conn.execute(text(
        "UPDATE discovery.candidates SET status=:s, drop_reason=:r, "
        "kept_advertiser_id=:a, triaged_at=now() WHERE id=:i"),
        {"s": status, "r": drop_reason, "a": kept_advertiser_id, "i": candidate_id})


def kept_advertiser_ids(conn) -> list[int]:
    """Advertiser ids of kept candidates, oldest-triaged first (the enrich feed)."""
    return [r[0] for r in conn.execute(text(
        "SELECT kept_advertiser_id FROM discovery.candidates "
        "WHERE status='kept' AND kept_advertiser_id IS NOT NULL "
        "GROUP BY kept_advertiser_id ORDER BY MIN(triaged_at), kept_advertiser_id")).all()]


def sweep_ran_today(conn, as_of) -> bool:
    """True when a sweep run FINISHED on `as_of` (a crashed run doesn't block the day)."""
    return bool(conn.execute(text(
        "SELECT EXISTS(SELECT 1 FROM discovery.sweep_runs "
        "WHERE started_at::date = :d AND finished_at IS NOT NULL)"),
        {"d": as_of}).scalar())


def open_sweep_run(conn) -> int:
    return conn.execute(text(
        "INSERT INTO discovery.sweep_runs DEFAULT VALUES RETURNING id")).scalar_one()


def close_sweep_run(conn, run_id: int, *, sweep: dict | None = None,
                    triage: dict | None = None, error: str | None = None) -> None:
    sweep = sweep or {}
    triage = triage or {}
    reasons = {**(sweep.get("drops") or {}), **(triage.get("dropped") or {})}
    conn.execute(text("""
        UPDATE discovery.sweep_runs SET finished_at = now(),
          keywords_swept  = :kw, serp_requests = :req,
          candidates_new  = :new, candidates_seen = :seen,
          triaged_kept    = :kept, triaged_dropped = :dropped,
          drop_reasons    = CAST(:reasons AS jsonb), error = :err
        WHERE id = :id
    """), {"kw": sweep.get("keywords_swept", 0), "req": sweep.get("serp_requests", 0),
           "new": sweep.get("candidates_new", 0), "seen": sweep.get("candidates_seen", 0),
           "kept": triage.get("kept", 0),
           "dropped": sum((triage.get("dropped") or {}).values()),
           "reasons": json.dumps(reasons), "err": error, "id": run_id})


def reap_open_runs(conn) -> dict[str, int]:
    """Close run rows left open by a killed worker (SIGTERM on laptop sleep, crash).

    Called once at worker startup: no run can survive a process restart, so any row
    still missing finished_at is an orphan from a previous process. Closing them keeps
    sweep_ran_today/flywheel_ran_today honest (an open row never counts as "ran today",
    which is correct, but the rows otherwise accumulate forever) and clears the run
    tables for fresh diagnostics. Idempotent: a second call closes nothing.
    """
    reaped: dict[str, int] = {}
    for table in ("sweep_runs", "funnel_sweep_runs", "flywheel_runs"):
        n = conn.execute(text(
            f"UPDATE discovery.{table} SET finished_at = now(), "
            "error = COALESCE(error, 'reaped: orphaned by worker restart') "
            "WHERE finished_at IS NULL")).rowcount
        if n:
            reaped[table] = n
    return reaped

