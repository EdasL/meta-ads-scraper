"""Repo for the keyword flywheel (discovery.dynamic_keywords + flywheel_runs).
Conn-first, callers own the txn — same conventions as db/candidates.py.

Lifecycle policy (the constants below are the single source of truth): a mined
keyword enters as 'trial' and is swept daily. After TRIAL_SWEEPS sweeps it either
earned PROMOTE_MIN_NEW new candidates ('active' — kept in the daily rotation for
good) or it didn't ('retired' — never swept or re-mined again)."""
import json

from sqlalchemy import text

TRIAL_SWEEPS = 14        # daily sweeps a trial keyword gets before the verdict
PROMOTE_MIN_NEW = 3      # new candidates a trial must earn to go active


def tried_keywords(conn) -> set[str]:
    """Every dynamic keyword ever minted, any status — the never-re-mint set."""
    return {r[0] for r in conn.execute(
        text("SELECT keyword FROM discovery.dynamic_keywords")).all()}


def trial_count(conn) -> int:
    return conn.execute(text(
        "SELECT count(*) FROM discovery.dynamic_keywords WHERE status='trial'")).scalar()


def add_trial_keywords(conn, keywords, *, source: str = "gem_copy") -> int:
    """Mint trial keywords; existing ones (any status) are left untouched."""
    added = 0
    for kw in keywords:
        added += conn.execute(text(
            "INSERT INTO discovery.dynamic_keywords (keyword, source) "
            "VALUES (:k, :s) ON CONFLICT (keyword) DO NOTHING"),
            {"k": kw, "s": source}).rowcount
    return added


def live_keywords(conn):
    """The keywords the daily flywheel sweep runs: trials + proven actives."""
    return conn.execute(text(
        "SELECT id, keyword, status, sweeps_used, new_candidates_total "
        "FROM discovery.dynamic_keywords WHERE status IN ('trial','active') "
        "ORDER BY status DESC, id")).all()           # trials first


def record_keyword_sweep(conn, keyword_id: int, *, new_candidates: int) -> str:
    """Bump one keyword's counters after a sweep and apply the trial verdict.
    Returns the (possibly new) status."""
    row = conn.execute(text(
        "UPDATE discovery.dynamic_keywords "
        "SET sweeps_used = sweeps_used + 1, "
        "    new_candidates_total = new_candidates_total + :n, "
        "    last_swept_at = now() "
        "WHERE id = :i RETURNING status, sweeps_used, new_candidates_total"),
        {"n": new_candidates, "i": keyword_id}).one()
    if row.status != "trial" or row.sweeps_used < TRIAL_SWEEPS:
        return row.status
    verdict = "active" if row.new_candidates_total >= PROMOTE_MIN_NEW else "retired"
    conn.execute(text("UPDATE discovery.dynamic_keywords SET status=:s WHERE id=:i"),
                 {"s": verdict, "i": keyword_id})
    return verdict


# --- flywheel run log (the worker's daily gate, same pattern as funnel_sweep_runs) ---
def flywheel_ran_today(conn, as_of) -> bool:
    """True when a flywheel run FINISHED on `as_of` (a crashed run doesn't block)."""
    return bool(conn.execute(text(
        "SELECT EXISTS(SELECT 1 FROM discovery.flywheel_runs "
        "WHERE started_at::date = :d AND finished_at IS NOT NULL)"),
        {"d": as_of}).scalar())


def open_flywheel_run(conn) -> int:
    return conn.execute(text(
        "INSERT INTO discovery.flywheel_runs DEFAULT VALUES RETURNING id")).scalar_one()


def close_flywheel_run(conn, run_id: int, *, stats: dict | None = None,
                       error: str | None = None) -> None:
    conn.execute(text(
        "UPDATE discovery.flywheel_runs SET finished_at = now(), "
        "stats = CAST(:stats AS jsonb), error = :err WHERE id = :id"),
        {"stats": json.dumps(stats) if stats is not None else None,
         "err": error, "id": run_id})
