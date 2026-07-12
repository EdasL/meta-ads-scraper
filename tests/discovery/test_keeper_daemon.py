"""N-lane keeper-history consumer daemon (public.pipeline_jobs, kind='keeper_history').
Mirrors tests/enrich_full/test_pipeline_daemon.py: needs real cross-txn behavior
(claim + safety-net writes), so it uses the session committing engine
(`_migrated_engine`) directly with a SENTINEL advertiser_id and DELETE-before/after
cleanup. The real runner is the keeper_one subprocess; tests inject a fake runner
that directly writes the row's terminal status (or dies without writing one)."""
import pytest
from sqlalchemy import text

from agf.db.engine import begin
from agf.db.pipeline_jobs import enqueue_jobs
from agf.enrich_full.enrich_one import set_status

SENTINEL = 999_000_000
A1 = SENTINEL + 1


def _clean(engine):
    # process_once claims the GLOBAL oldest queued keeper_history row, so the test
    # must control the whole keeper_history queue — wipe every keeper_history job
    # (transient queue table, nothing live runs against the test DB) plus the
    # sentinel crawl_log markers the safety net writes.
    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM public.pipeline_jobs WHERE kind='keeper_history'"))
        conn.execute(text(
            "DELETE FROM logging.crawl_log WHERE entity_type='keeper_history' "
            "AND entity_id >= :s"), {"s": SENTINEL})


@pytest.fixture
def engine(_migrated_engine):
    _clean(_migrated_engine)
    try:
        yield _migrated_engine
    finally:
        _clean(_migrated_engine)


def _seed_job(engine, advertiser_id):
    with begin(engine) as conn:
        enqueue_jobs(conn, "keeper_history", [advertiser_id])
        return conn.execute(text(
            "SELECT id FROM public.pipeline_jobs WHERE kind='keeper_history' "
            "AND advertiser_id=:a ORDER BY id DESC LIMIT 1"),
            {"a": advertiser_id}).scalar_one()


def _status(engine, advertiser_id):
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT status FROM public.pipeline_jobs "
            "WHERE kind='keeper_history' AND advertiser_id = :a"),
            {"a": advertiser_id}).scalar()


def test_process_once_claims_and_runs(engine):
    job_id = _seed_job(engine, A1)
    calls = []

    def fake_runner(advertiser_id, job_id):
        calls.append((advertiser_id, job_id))
        # simulate keeper_one writing its own terminal 'done' status on the row
        set_status(engine, job_id, "done", finished=True, status_table="pipeline_jobs")

    from agf.discovery.keeper_daemon import process_once
    assert process_once(engine, runner=fake_runner) is True
    assert calls == [(A1, job_id)]
    # the runner wrote 'done'; the safety net leaves a terminal row untouched
    assert _status(engine, A1) == "done"


def test_process_once_safety_net_marks_error_and_crawl_log(engine):
    _seed_job(engine, A1)

    def dead_runner(advertiser_id, job_id):
        pass  # exits without writing any status

    from agf.discovery.keeper_daemon import process_once
    assert process_once(engine, runner=dead_runner) is True
    # safety net: a runner that died without recording status must not leave the
    # row stuck 'running'
    assert _status(engine, A1) == "error"
    # ... AND must mark the crawl_log (entity_type='keeper_history') so the TTL
    # applies — an OUTER-killed subprocess never reaches keeper_one's own error-path
    # marker, and without this row keepers_due_for_history re-enqueues it every tick.
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT crawl_result FROM logging.crawl_log "
            "WHERE entity_type='keeper_history' AND entity_id=:a "
            "ORDER BY crawled_at DESC LIMIT 1"), {"a": A1}).scalar()
    assert result is not None and result.startswith("error:")


def test_process_once_empty_queue_returns_false(engine):
    from agf.discovery.keeper_daemon import process_once
    assert process_once(engine, runner=lambda a, j: None) is False
