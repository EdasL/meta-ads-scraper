"""Run-log helpers for the recurring corpus-refresh worker stage (docs/superpowers/
specs/2026-07-09-corpus-refresh-stage-design.md). Sibling to db/funnel_candidates.py's
funnel_sweep_run helpers; conn-first (callers own the txn). A FINISHED row per calendar
day gates the daily cadence; corpus_refresh_ran_within gates a continuous cadence."""
import json

from sqlalchemy import text


def open_corpus_refresh_run(conn) -> int:
    return conn.execute(text(
        "INSERT INTO discovery.corpus_refresh_runs DEFAULT VALUES RETURNING id")).scalar_one()


def close_corpus_refresh_run(conn, run_id: int, *, stats: dict | None = None,
                             error: str | None = None) -> None:
    conn.execute(text(
        "UPDATE discovery.corpus_refresh_runs SET finished_at = now(), "
        "stats = CAST(:stats AS jsonb), error = :err WHERE id = :id"),
        {"stats": json.dumps(stats) if stats is not None else None,
         "err": error, "id": run_id})


def corpus_refresh_ran_today(conn, as_of) -> bool:
    """True when a corpus-refresh run FINISHED on `as_of` (a crashed run doesn't block the
    day). Mirrors funnel_candidates.funnel_sweep_ran_today — the worker's daily gate."""
    return bool(conn.execute(text(
        "SELECT EXISTS(SELECT 1 FROM discovery.corpus_refresh_runs "
        "WHERE started_at::date = :d AND finished_at IS NOT NULL)"),
        {"d": as_of}).scalar())


def corpus_refresh_ran_within(conn, seconds: int) -> bool:
    """True when a corpus-refresh run FINISHED within the last `seconds`. The time-based
    gate for a continuous cadence instead of once-per-calendar-day. A crashed/open run
    doesn't count. Mirrors funnel_candidates.funnel_sweep_ran_within."""
    return bool(conn.execute(text(
        "SELECT EXISTS(SELECT 1 FROM discovery.corpus_refresh_runs "
        "WHERE finished_at IS NOT NULL "
        "AND finished_at > now() - make_interval(secs => :s))"),
        {"s": seconds}).scalar())
