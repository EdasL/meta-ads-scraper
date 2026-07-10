"""Continuous-enrich consumer daemon (public.pipeline_jobs). Needs real cross-txn
behavior (claim + safety-net writes), so it uses the session `_migrated_engine`
directly with SENTINEL advertiser_ids (>= 999000000) and DELETE-before/after
cleanup. The real runner is the enrich_one subprocess; tests inject a fake runner
that directly UPDATEs the row to simulate the CLI's terminal-status write."""
import pytest
from sqlalchemy import text

from agf.db.engine import begin
from agf.db.pipeline_jobs import enqueue_enrich
from agf.enrich_full.pipeline_daemon import process_once

SENTINEL = 999_000_000
A1 = SENTINEL + 1


def _clean(engine):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM public.pipeline_jobs WHERE advertiser_id >= :s"),
                     {"s": SENTINEL})


@pytest.fixture
def engine(_migrated_engine):
    _clean(_migrated_engine)
    try:
        yield _migrated_engine
    finally:
        _clean(_migrated_engine)


def _status(engine, advertiser_id):
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT status FROM public.pipeline_jobs WHERE advertiser_id = :a"),
            {"a": advertiser_id}).scalar()


def test_process_once_claims_and_completes(engine):
    with begin(engine) as conn:
        enqueue_enrich(conn, [A1])

    seen = {}

    def fake_runner(advertiser_id, job_id):
        seen["args"] = (advertiser_id, job_id)
        # simulate enrich_one writing its own terminal 'done' status on the row
        with begin(engine) as conn:
            conn.execute(text(
                "UPDATE public.pipeline_jobs SET status='done', finished_at=now(), "
                "lease_expires_at=NULL WHERE id=:i"), {"i": job_id})

    busy = process_once(engine, runner=fake_runner)
    assert busy is True
    assert seen["args"][0] == A1                # claimed row handed to the runner
    # the runner wrote 'done'; the safety net leaves a terminal row untouched
    assert _status(engine, A1) == "done"


def test_process_once_runner_raises_marks_error(engine):
    with begin(engine) as conn:
        enqueue_enrich(conn, [A1])

    def boom(advertiser_id, job_id):
        raise RuntimeError("spawn failed")

    busy = process_once(engine, runner=boom)
    assert busy is True
    # safety net: a runner that died without recording status must not leave the
    # row stuck 'running'
    assert _status(engine, A1) == "error"


def test_process_once_empty_queue_returns_false(engine):
    assert process_once(engine, runner=lambda a, j: None) is False
