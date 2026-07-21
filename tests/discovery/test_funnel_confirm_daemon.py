"""Single-lane funnel-confirm consumer (pipeline_jobs, kind='funnel_confirm').
Candidate id travels in the advertiser_id column. Injects a fake runner."""
import pytest
from sqlalchemy import text

from agf.db.engine import begin
from agf.db.pipeline_jobs import enqueue_jobs
from agf.enrich_full.enrich_one import set_status

SENTINEL = 990_000_000
C1 = SENTINEL + 1


def _clean(engine):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM public.pipeline_jobs WHERE kind='funnel_confirm'"))
        conn.execute(text("DELETE FROM logging.crawl_log WHERE entity_type='funnel_confirm' AND entity_id >= :s"), {"s": SENTINEL})


@pytest.fixture
def engine(_migrated_engine):
    _clean(_migrated_engine)
    try:
        yield _migrated_engine
    finally:
        _clean(_migrated_engine)


def _seed(engine, cid):
    with begin(engine) as conn:
        enqueue_jobs(conn, "funnel_confirm", [cid])
        return conn.execute(text("SELECT id FROM public.pipeline_jobs WHERE kind='funnel_confirm' AND advertiser_id=:c ORDER BY id DESC LIMIT 1"), {"c": cid}).scalar_one()


def _status(engine, cid):
    with engine.connect() as conn:
        return conn.execute(text("SELECT status FROM public.pipeline_jobs WHERE kind='funnel_confirm' AND advertiser_id=:c"), {"c": cid}).scalar()


def test_process_once_claims_and_runs(engine):
    jid = _seed(engine, C1)
    calls = []
    def fake_runner(candidate_id, job_id, kind):
        calls.append((candidate_id, job_id, kind))
        set_status(engine, job_id, "done", finished=True, status_table="pipeline_jobs")
    from agf.discovery.funnel_confirm_daemon import process_once
    assert process_once(engine, runner=fake_runner) is True
    assert calls == [(C1, jid, "funnel_confirm")]
    assert _status(engine, C1) == "done"


def test_process_once_safety_net_marks_error_and_crawl_log(engine):
    _seed(engine, C1)
    def dead_runner(candidate_id, job_id, kind):
        pass
    from agf.discovery.funnel_confirm_daemon import process_once
    assert process_once(engine, runner=dead_runner) is True
    assert _status(engine, C1) == "error"
    with engine.connect() as conn:
        r = conn.execute(text("SELECT crawl_result FROM logging.crawl_log WHERE entity_type='funnel_confirm' AND entity_id=:c ORDER BY crawled_at DESC LIMIT 1"), {"c": C1}).scalar()
    assert r is not None and r.startswith("error:")


def test_process_once_empty_queue_returns_false(engine):
    from agf.discovery.funnel_confirm_daemon import process_once
    assert process_once(engine, runner=lambda c, j, k: None) is False
