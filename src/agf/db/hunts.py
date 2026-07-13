"""Repo for frontend.hunt* — the on-demand Hunt feature (spec 2026-07-13).

Conn-first: every helper takes an open connection and the CALLER owns the txn
(same convention as db/pipeline_jobs.py, db/candidates.py). All SQL uses text()
with bound params.
"""
import json

from sqlalchemy import text


def load_round(conn, round_id: int):
    """One hunt_rounds row (terms/competitor_apps as Python lists) or None."""
    return conn.execute(text(
        "SELECT id, hunt_id, terms, competitor_apps, status "
        "FROM frontend.hunt_rounds WHERE id = :i"), {"i": round_id}).first()


def mark_round_running(conn, round_id: int) -> None:
    conn.execute(text(
        "UPDATE frontend.hunt_rounds "
        "SET status = 'running', started_at = now() WHERE id = :i"),
        {"i": round_id})


def finish_round(conn, round_id: int, *, status: str, error: str | None = None) -> None:
    conn.execute(text(
        "UPDATE frontend.hunt_rounds "
        "SET status = :s, error_text = :e, finished_at = now() WHERE id = :i"),
        {"i": round_id, "s": status, "e": error})


def upsert_hunt_result(conn, row: dict) -> None:
    """One found page per round; re-finds (other term/page) keep the first row."""
    conn.execute(text(
        "INSERT INTO frontend.hunt_results "
        "  (round_id, candidate_id, page_id, page_name, store, store_id, "
        "   link_url, source_term, was_new) "
        "VALUES (:round_id, :candidate_id, :page_id, :page_name, :store, "
        "        :store_id, :link_url, :source_term, :was_new) "
        "ON CONFLICT (round_id, page_id, store, store_id) DO NOTHING"), row)


def update_round_progress(conn, round_id: int, progress: dict) -> None:
    conn.execute(text(
        "UPDATE frontend.hunt_rounds SET progress = CAST(:p AS jsonb) "
        "WHERE id = :i"), {"i": round_id, "p": json.dumps(progress)})


def apply_triage_to_results(conn, round_id: int) -> dict:
    """Copy triage outcomes from discovery.candidates onto this round's results.

    candidates.status is one of 'new'/'kept'/'dropped' (db/schema/112); map
    'kept' -> kept, 'dropped' -> vetoed, everything else -> pending. Returns the
    per-triage counts for this round.
    """
    conn.execute(text(
        "UPDATE frontend.hunt_results hr SET "
        "  triage = CASE c.status WHEN 'kept' THEN 'kept' "
        "                         WHEN 'dropped' THEN 'vetoed' "
        "                         ELSE 'pending' END, "
        "  kept_advertiser_id = c.kept_advertiser_id "
        "FROM discovery.candidates c "
        "WHERE hr.round_id = :r AND hr.candidate_id = c.id"), {"r": round_id})
    counts = {"kept": 0, "vetoed": 0, "pending": 0}
    for row in conn.execute(text(
            "SELECT triage, count(*) AS n FROM frontend.hunt_results "
            "WHERE round_id = :r GROUP BY triage"), {"r": round_id}):
        counts[row.triage] = row.n
    return counts


def round_new_candidate_ids(conn, round_id: int) -> list[int]:
    return [r.candidate_id for r in conn.execute(text(
        "SELECT candidate_id FROM frontend.hunt_results "
        "WHERE round_id = :r AND candidate_id IS NOT NULL "
        "ORDER BY candidate_id"), {"r": round_id})]
