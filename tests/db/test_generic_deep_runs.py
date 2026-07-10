"""Run-log helpers for the weekly generic-deep stage (migration 151). Uses the
rolled-back pg engine fixture; rows never persist past the test."""
from agf.db.generic_deep_runs import (close_generic_deep_run,
                                      generic_deep_ran_within_days,
                                      open_generic_deep_run)


def test_open_close_and_weekly_gate(pg_conn):
    assert generic_deep_ran_within_days(pg_conn, 7) is False   # empty table
    run_id = open_generic_deep_run(pg_conn)
    # an OPEN (unfinished) run does not satisfy the gate
    assert generic_deep_ran_within_days(pg_conn, 7) is False
    close_generic_deep_run(pg_conn, run_id, stats={"mined": ["x"], "trials_added": 1})
    assert generic_deep_ran_within_days(pg_conn, 7) is True


def test_failed_run_does_not_block_the_week(pg_conn):
    # A run closed WITH an error must not satisfy the weekly gate — the pass retries
    # next tick (funnel-deep semantics). The original contract counted any finished
    # row; the first live pass (2026-07-10) died mid-pass on a Meta anomaly and would
    # have silently skipped a whole week.
    run_id = open_generic_deep_run(pg_conn)
    close_generic_deep_run(pg_conn, run_id, error="ExtractError('boom')")
    assert generic_deep_ran_within_days(pg_conn, 7) is False
    ok_id = open_generic_deep_run(pg_conn)
    close_generic_deep_run(pg_conn, ok_id, stats={"trials_added": 0})
    assert generic_deep_ran_within_days(pg_conn, 7) is True
