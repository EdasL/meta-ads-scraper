"""Hunt daemon claim/safety-net tests (fake runner, real pipeline_jobs)."""
import json

import pytest
from sqlalchemy import text

from agf.db.engine import begin
from agf.db.pipeline_jobs import enqueue_jobs
from agf.enrich_full.enrich_one import set_status


def _mk_round(engine) -> int:
    with begin(engine) as conn:
        hunt_id = conn.execute(text(
            "INSERT INTO frontend.hunts (user_email, title) "
            "VALUES ('d@test.local', 'daemon-test') RETURNING id")).scalar_one()
        return conn.execute(text(
            "INSERT INTO frontend.hunt_rounds (hunt_id, terms) "
            "VALUES (:h, CAST(:t AS jsonb)) RETURNING id"),
            {"h": hunt_id, "t": json.dumps(["x"])}).scalar_one()


@pytest.fixture
def engine(_migrated_engine):
    def _clean():
        with begin(_migrated_engine) as conn:
            conn.execute(text("DELETE FROM public.pipeline_jobs WHERE kind = 'hunt'"))
            conn.execute(text(
                "DELETE FROM frontend.hunts WHERE user_email = 'd@test.local'"))
    _clean()
    try:
        yield _migrated_engine
    finally:
        _clean()


def _job_status(engine, round_id):
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT status FROM public.pipeline_jobs "
            "WHERE kind = 'hunt' AND advertiser_id = :r"),
            {"r": round_id}).scalar_one()


def test_process_once_claims_hunt_job(engine):
    round_id = _mk_round(engine)
    with begin(engine) as conn:
        enqueue_jobs(conn, "hunt", [round_id])
    calls = []
    def fake_runner(round_id_arg, job_id):
        calls.append((round_id_arg, job_id))
        set_status(engine, job_id, "done", finished=True,
                   status_table="pipeline_jobs")
    from agf.discovery.hunt_daemon import process_once
    assert process_once(engine, runner=fake_runner) is True
    assert len(calls) == 1 and calls[0][0] == round_id
    assert _job_status(engine, round_id) == "done"
    assert process_once(engine, runner=fake_runner) is False  # queue drained


def test_safety_net_marks_job_and_round_on_runner_death(engine):
    round_id = _mk_round(engine)
    with begin(engine) as conn:
        enqueue_jobs(conn, "hunt", [round_id])
    def dead_runner(round_id_arg, job_id):
        raise RuntimeError("subprocess killed")
    from agf.discovery.hunt_daemon import process_once
    assert process_once(engine, runner=dead_runner) is True
    assert _job_status(engine, round_id) == "error"
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT status, error_text FROM frontend.hunt_rounds WHERE id = :i"),
            {"i": round_id}).one()
    assert row.status == "error" and "killed" in row.error_text
