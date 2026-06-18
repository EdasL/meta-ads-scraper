"""Repo for the keyword flywheel (discovery.dynamic_keywords + flywheel_runs).
Conn-first, callers own the txn — same conventions as db/candidates.py.

Lifecycle policy (the constants below are the single source of truth): a mined
keyword enters as 'trial' and is swept daily. After TRIAL_SWEEPS sweeps it either
earned PROMOTE_MIN_NEW new candidates ('active' — kept in the daily rotation for
good) or it didn't ('retired' — never swept or re-mined again)."""
import json
import os

from sqlalchemy import text


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


# Verdict cadence — env-backed so the pool can be made to turn over faster.
# Lower TRIAL_SWEEPS = quicker verdicts = the trial pool recycles in days, not
# weeks. Read at call time (not import) so plist/env changes take effect on
# restart without code edits.
def trial_sweeps() -> int:
    return _env_int("AGF_FLYWHEEL_TRIAL_SWEEPS", 4)


def promote_min_new() -> int:
    return _env_int("AGF_FLYWHEEL_PROMOTE_MIN_NEW", 3)


# Back-compat aliases (some callers/tests import these names). These are the
# defaults; the functions above are authoritative at runtime.
TRIAL_SWEEPS = trial_sweeps()
PROMOTE_MIN_NEW = promote_min_new()


# `kind` ('app' | 'funnel') keeps the two flywheels' keyword pools separate so a funnel
# trial keyword is never swept into the app candidate pool and vice-versa. Defaults to
# 'app' so existing app-flywheel callers are unchanged.
def tried_keywords(conn, *, kind: str = "app") -> set[str]:
    """Every dynamic keyword of this kind ever minted, any status — the never-re-mint set."""
    return {r[0] for r in conn.execute(
        text("SELECT keyword FROM discovery.dynamic_keywords WHERE kind=:k"),
        {"k": kind}).all()}


def trial_count(conn, *, kind: str = "app") -> int:
    return conn.execute(text(
        "SELECT count(*) FROM discovery.dynamic_keywords WHERE status='trial' AND kind=:k"),
        {"k": kind}).scalar()


def add_trial_keywords(conn, keywords, *, source: str = "gem_copy", kind: str = "app") -> int:
    """Mint trial keywords of this kind; existing ones (any status) are left untouched."""
    added = 0
    for kw in keywords:
        added += conn.execute(text(
            "INSERT INTO discovery.dynamic_keywords (keyword, source, kind) "
            "VALUES (:k, :s, :kind) ON CONFLICT (keyword, kind) DO NOTHING"),
            {"k": kw, "s": source, "kind": kind}).rowcount
    return added


def live_keywords(conn, *, kind: str = "app"):
    """The keywords the daily flywheel sweep runs: trials + proven actives, this kind only."""
    return conn.execute(text(
        "SELECT id, keyword, status, sweeps_used, new_candidates_total "
        "FROM discovery.dynamic_keywords WHERE status IN ('trial','active') AND kind=:k "
        "ORDER BY status DESC, id"), {"k": kind}).all()  # trials first


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
    if row.status != "trial" or row.sweeps_used < trial_sweeps():
        return row.status
    verdict = "active" if row.new_candidates_total >= promote_min_new() else "retired"
    conn.execute(text("UPDATE discovery.dynamic_keywords SET status=:s WHERE id=:i"),
                 {"s": verdict, "i": keyword_id})
    return verdict


def apply_verdicts(conn, *, kind: str | None = None) -> dict:
    """Batch-apply the current verdict cadence to every trial that has already
    used its sweep budget. Idempotent — safe to run on startup or by hand to
    recycle a backlog after lowering AGF_FLYWHEEL_TRIAL_SWEEPS. Trials that
    haven't reached the budget are left as 'trial'. `kind` scopes it to one pool
    (None = all kinds, back-compat). Returns counts."""
    rows = conn.execute(text(
        "UPDATE discovery.dynamic_keywords SET status = CASE "
        "  WHEN new_candidates_total >= :promote THEN 'active' ELSE 'retired' END "
        "WHERE status = 'trial' AND sweeps_used >= :budget "
        "  AND (CAST(:kind AS TEXT) IS NULL OR kind = :kind) "
        "RETURNING status"),
        {"promote": promote_min_new(), "budget": trial_sweeps(), "kind": kind}).all()
    promoted = sum(1 for r in rows if r.status == "active")
    return {"promoted": promoted, "retired": len(rows) - promoted}


# --- flywheel run log (the worker's daily gate, same pattern as funnel_sweep_runs) ---
def flywheel_ran_today(conn, as_of) -> bool:
    """True when a flywheel run FINISHED on `as_of` (a crashed run doesn't block)."""
    return bool(conn.execute(text(
        "SELECT EXISTS(SELECT 1 FROM discovery.flywheel_runs "
        "WHERE started_at::date = :d AND finished_at IS NOT NULL)"),
        {"d": as_of}).scalar())


def flywheel_ran_within(conn, seconds: int) -> bool:
    """True when a flywheel run FINISHED within the last `seconds`. Used for a
    time-based cadence (several runs/day) instead of the once-per-calendar-day
    gate. A crashed/open run doesn't count."""
    return bool(conn.execute(text(
        "SELECT EXISTS(SELECT 1 FROM discovery.flywheel_runs "
        "WHERE finished_at IS NOT NULL "
        "AND finished_at > now() - make_interval(secs => :s))"),
        {"s": seconds}).scalar())


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
