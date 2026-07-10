"""Run-log helpers for the weekly generic-deep copy-phrase sweep (docs/superpowers/
specs/2026-07-10-generic-deep-keyword-lane-design.md). Sibling to
db/corpus_refresh_runs.py; conn-first (callers own the txn). A FINISHED row within
the last N days gates the weekly cadence."""
import json

from sqlalchemy import text


def open_generic_deep_run(conn) -> int:
    return conn.execute(text(
        "INSERT INTO discovery.generic_deep_runs DEFAULT VALUES RETURNING id")).scalar_one()


def close_generic_deep_run(conn, run_id: int, *, stats: dict | None = None,
                           error: str | None = None) -> None:
    conn.execute(text(
        "UPDATE discovery.generic_deep_runs SET finished_at = now(), "
        "stats = CAST(:stats AS jsonb), error = :err WHERE id = :id"),
        {"stats": json.dumps(stats) if stats is not None else None,
         "err": error, "id": run_id})


def generic_deep_ran_within_days(conn, days: int) -> bool:
    """True when a generic-deep run finished CLEANLY within the last `days` days. An
    open (crashed mid-flight) run doesn't count, and neither does a run closed with an
    error — a failed weekly pass must retry on the next tick, not skip the whole week
    (the first live pass died on a mid-pass Meta anomaly, 2026-07-10; same retry
    semantics as the funnel deep lane)."""
    return bool(conn.execute(text(
        "SELECT EXISTS(SELECT 1 FROM discovery.generic_deep_runs "
        "WHERE finished_at IS NOT NULL AND error IS NULL "
        "AND finished_at > now() - make_interval(days => :d))"),
        {"d": days}).scalar())
