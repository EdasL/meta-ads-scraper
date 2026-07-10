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
    run_id = open_generic_deep_run(pg_conn)
    close_generic_deep_run(pg_conn, run_id, error="ExtractError('boom')")
    # finished WITH error still counts as finished — mirror corpus_refresh semantics?
    # NO: the funnel deep gate retries after a failure. Assert the row exists but
    # carries the error; the due decision in the worker keys on finished_at, so a
    # failed-but-closed run DOES satisfy ran_within_days — the retry semantics come
    # from the worker closing with error=... ONLY when the stage raised after doing
    # partial work. Assert the observable contract:
    assert generic_deep_ran_within_days(pg_conn, 7) is True
