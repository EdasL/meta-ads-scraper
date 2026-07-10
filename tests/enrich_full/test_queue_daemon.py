"""Queue-daemon tests: claim/expire/run semantics over public.enrich_jobs.

The Go API (GKE pod) inserts 'queued' rows; the daemon (worker VM) claims them
and runs the one-shot enrichment. These tests drive the state machine with an
injected runner — no subprocess, no Meta traffic.
"""
import pytest
from sqlalchemy import text

from agf.db.engine import begin, get_engine
from agf.enrich_full import queue_daemon


ADV = 999002  # sentinel advertiser for this file (999001 = test_enrich_one)


@pytest.fixture(autouse=True)
def _no_foreign_queued_rows():
    """claim_next takes the oldest queued row GLOBALLY, so leftover committed
    'queued' rows from other suites sharing this DB (the Go integration tests
    enqueue and never consume) would be claimed instead of ours. Hermetic
    clean-state: clear them before each test."""
    engine = get_engine()
    with begin(engine) as conn:
        conn.execute(text("DELETE FROM public.enrich_jobs WHERE status='queued'"))
    yield


def _insert_job(conn, *, status="queued", advertiser_id=ADV, age_sec=0):
    row = conn.execute(text(
        "INSERT INTO public.enrich_jobs (advertiser_id, status, started_at) "
        "VALUES (:a, :s, now() - make_interval(secs => :age)) RETURNING id"
    ), {"a": advertiser_id, "s": status, "age": age_sec}).first()
    return row[0]


def _row(conn, job_id):
    return conn.execute(text(
        "SELECT status, error_text, finished_at FROM public.enrich_jobs WHERE id=:i"
    ), {"i": job_id}).first()


def _cleanup(engine, job_ids):
    with begin(engine) as conn:
        conn.execute(text("DELETE FROM public.enrich_jobs WHERE id = ANY(:ids)"),
                     {"ids": list(job_ids)})


def test_claim_next_takes_oldest_queued_and_marks_running():
    engine = get_engine()
    with begin(engine) as conn:
        first = _insert_job(conn, age_sec=10)
        second = _insert_job(conn, age_sec=5)
    try:
        with begin(engine) as conn:
            claimed = queue_daemon.claim_next(conn)
        assert claimed == (first, ADV)
        with begin(engine) as conn:
            assert _row(conn, first)[0] == "running"
            assert _row(conn, second)[0] == "queued"
    finally:
        _cleanup(engine, [first, second])


def test_claim_next_returns_none_when_no_queued_rows():
    engine = get_engine()
    with begin(engine) as conn:
        running = _insert_job(conn, status="running")
    try:
        with begin(engine) as conn:
            assert queue_daemon.claim_next(conn) is None
    finally:
        _cleanup(engine, [running])


def test_expire_stale_queued_marks_old_rows_error():
    engine = get_engine()
    with begin(engine) as conn:
        stale = _insert_job(conn, age_sec=queue_daemon.QUEUED_MAX_AGE_SEC + 60)
        fresh = _insert_job(conn, age_sec=1)
    try:
        with begin(engine) as conn:
            queue_daemon.expire_stale_queued(conn)
        with begin(engine) as conn:
            status, err, finished = _row(conn, stale)
            assert status == "error"
            assert "picked up" in err
            assert finished is not None
            assert _row(conn, fresh)[0] == "queued"
    finally:
        _cleanup(engine, [stale, fresh])


def test_process_once_runs_claimed_job_through_runner():
    engine = get_engine()
    with begin(engine) as conn:
        job = _insert_job(conn)
    ran = []

    def runner(advertiser_id, job_id):
        ran.append((advertiser_id, job_id))
        # the real runner (enrich_one subprocess) writes the terminal status
        with begin(engine) as conn:
            conn.execute(text(
                "UPDATE public.enrich_jobs SET status='done', finished_at=now() WHERE id=:i"
            ), {"i": job_id})

    try:
        assert queue_daemon.process_once(engine, runner=runner) is True
        assert ran == [(ADV, job)]
        with begin(engine) as conn:
            assert _row(conn, job)[0] == "done"
    finally:
        _cleanup(engine, [job])


def test_process_once_marks_error_when_runner_leaves_row_running():
    """Subprocess crashed before writing status → daemon's safety net."""
    engine = get_engine()
    with begin(engine) as conn:
        job = _insert_job(conn)
    try:
        assert queue_daemon.process_once(engine, runner=lambda a, j: None) is True
        with begin(engine) as conn:
            status, err, finished = _row(conn, job)
            assert status == "error"
            assert err is not None
            assert finished is not None
    finally:
        _cleanup(engine, [job])


def test_process_once_returns_false_when_queue_empty():
    engine = get_engine()
    assert queue_daemon.process_once(engine, runner=lambda a, j: None) is False


def test_process_once_survives_runner_exception():
    engine = get_engine()
    with begin(engine) as conn:
        job = _insert_job(conn)

    def boom(advertiser_id, job_id):
        raise RuntimeError("subprocess spawn failed")

    try:
        assert queue_daemon.process_once(engine, runner=boom) is True
        with begin(engine) as conn:
            status, err, _ = _row(conn, job)
            assert status == "error"
            assert "subprocess spawn failed" in err
    finally:
        _cleanup(engine, [job])
