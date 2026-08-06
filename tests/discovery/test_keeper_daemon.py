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
    # process_once claims the GLOBAL oldest queued row of its kinds, so the test
    # must control the whole queue for BOTH kinds — wipe every keeper_history and
    # corpus_refresh job (transient queue table, nothing live runs against the test
    # DB) plus the sentinel crawl_log markers the safety net writes.
    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM public.pipeline_jobs "
            "WHERE kind IN ('keeper_history', 'corpus_refresh')"))
        conn.execute(text(
            "DELETE FROM logging.crawl_log "
            "WHERE entity_type IN ('keeper_history', 'corpus_refresh') "
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

    def fake_runner(advertiser_id, job_id, kind):
        calls.append((advertiser_id, job_id, kind))
        # simulate keeper_one writing its own terminal 'done' status on the row
        set_status(engine, job_id, "done", finished=True, status_table="pipeline_jobs")

    from agf.discovery.keeper_daemon import process_once
    assert process_once(engine, runner=fake_runner) is True
    assert calls == [(A1, job_id, "keeper_history")]
    # the runner wrote 'done'; the safety net leaves a terminal row untouched
    assert _status(engine, A1) == "done"


def test_process_once_safety_net_marks_error_and_crawl_log(engine):
    _seed_job(engine, A1)

    def dead_runner(advertiser_id, job_id, kind):
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
    assert process_once(engine, runner=lambda a, j, k: None) is False


A2 = SENTINEL + 2


def _seed_corpus_job(engine, advertiser_id):
    with begin(engine) as conn:
        enqueue_jobs(conn, "corpus_refresh", [advertiser_id])
        return conn.execute(text(
            "SELECT id FROM public.pipeline_jobs WHERE kind='corpus_refresh' "
            "AND advertiser_id=:a ORDER BY id DESC LIMIT 1"),
            {"a": advertiser_id}).scalar_one()


def test_keeper_jobs_claimed_before_corpus(engine):
    """Multi-kind priority (spec 2026-07-13): keeper_history (found→visible path)
    always drains before corpus_refresh, regardless of enqueue order."""
    corpus_job = _seed_corpus_job(engine, A2)   # enqueued FIRST (older run_after)
    keeper_job = _seed_job(engine, A1)
    calls = []

    def fake_runner(advertiser_id, job_id, kind):
        calls.append((advertiser_id, job_id, kind))
        set_status(engine, job_id, "done", finished=True, status_table="pipeline_jobs")

    from agf.discovery.keeper_daemon import process_once
    assert process_once(engine, runner=fake_runner) is True
    assert process_once(engine, runner=fake_runner) is True
    assert calls == [(A1, keeper_job, "keeper_history"),
                     (A2, corpus_job, "corpus_refresh")]


def test_safety_net_marks_crawl_log_with_job_kind(engine):
    """A corpus subprocess that dies without recording status must TTL-mark
    entity_type='corpus_refresh' — a keeper-kind marker would let the corpus
    producer re-enqueue it every tick (starvation hot-loop, 2026-07-11)."""
    _seed_corpus_job(engine, A2)

    def dead_runner(advertiser_id, job_id, kind):
        pass  # exits without writing any status

    from agf.discovery.keeper_daemon import process_once
    assert process_once(engine, runner=dead_runner) is True
    with engine.connect() as conn:
        status = conn.execute(text(
            "SELECT status FROM public.pipeline_jobs "
            "WHERE kind='corpus_refresh' AND advertiser_id=:a"), {"a": A2}).scalar()
        assert status == "error"
        result = conn.execute(text(
            "SELECT crawl_result FROM logging.crawl_log "
            "WHERE entity_type='corpus_refresh' AND entity_id=:a "
            "ORDER BY crawled_at DESC LIMIT 1"), {"a": A2}).scalar()
    assert result is not None and result.startswith("error:")


def test_corpus_error_spike_pauses_corpus_claims_not_keepers(engine, monkeypatch):
    """The brake: at >= AGF_CORPUS_ERROR_PAUSE corpus errors in the last 30 min,
    corpus claims pause; keeper claims are unaffected."""
    monkeypatch.setenv("AGF_CORPUS_ERROR_PAUSE", "2")
    # two fresh corpus error rows (sentinel advertisers to stay clean-able)
    with begin(engine) as conn:
        for i in (3, 4):
            enqueue_jobs(conn, "corpus_refresh", [SENTINEL + i])
        conn.execute(text(
            "UPDATE public.pipeline_jobs SET status='error', finished_at=now() "
            "WHERE kind='corpus_refresh' AND advertiser_id IN (:a, :b)"),
            {"a": SENTINEL + 3, "b": SENTINEL + 4})
    _seed_corpus_job(engine, A2)
    keeper_job = _seed_job(engine, A1)
    calls = []

    def fake_runner(advertiser_id, job_id, kind):
        calls.append((advertiser_id, job_id, kind))
        set_status(engine, job_id, "done", finished=True, status_table="pipeline_jobs")

    from agf.discovery.keeper_daemon import process_once
    assert process_once(engine, runner=fake_runner) is True    # keeper still drains
    assert process_once(engine, runner=fake_runner) is False   # corpus claim paused
    assert calls == [(A1, keeper_job, "keeper_history")]
    with engine.connect() as conn:
        corpus_status = conn.execute(text(
            "SELECT status FROM public.pipeline_jobs "
            "WHERE kind='corpus_refresh' AND advertiser_id=:a"), {"a": A2}).scalar()
    assert corpus_status == "queued"                           # untouched, not lost


# --- add_page routing (spec 2026-08-06 pasted-link add) ---------------------

def test_add_page_is_claimed_before_keeper_and_corpus():
    """A founder is watching a spinner for an add_page job; the background lanes
    can wait a tick."""
    from agf.discovery import keeper_daemon
    assert keeper_daemon.KINDS[0] == "add_page"
    assert set(keeper_daemon.KINDS) == {"add_page", "keeper_history", "corpus_refresh"}


def test_the_lease_outlasts_the_longest_subprocess():
    """A lease sized for a keeper pull would reap an add_page job mid-enrich."""
    from agf.discovery import keeper_daemon
    assert keeper_daemon.LEASE_SEC > keeper_daemon.ADD_PAGE_TIMEOUT_SEC + 120 - 1
    assert keeper_daemon.LEASE_SEC > keeper_daemon.KEEPER_ONE_TIMEOUT_SEC


def test_add_page_kind_runs_the_add_page_cli(monkeypatch):
    from agf.discovery import keeper_daemon
    seen = {}
    monkeypatch.setattr(keeper_daemon.subprocess, "run",
                        lambda cmd, **kw: seen.update(cmd=cmd, kw=kw))
    keeper_daemon.run_keeper_subprocess(7, 11, kind="add_page")
    assert "agf.discovery.add_page_one" in seen["cmd"]
    assert seen["cmd"][seen["cmd"].index("--advertiser-id") + 1] == "7"
    assert seen["cmd"][seen["cmd"].index("--job-id") + 1] == "11"
    # add_page_one takes neither flag; passing them would abort the CLI on argparse.
    assert "--kind" not in seen["cmd"]
    assert "--flip-vanished" not in seen["cmd"]
    assert seen["kw"]["timeout"] > keeper_daemon.ADD_PAGE_TIMEOUT_SEC


def test_keeper_kinds_still_run_keeper_one(monkeypatch):
    from agf.discovery import keeper_daemon
    seen = {}
    monkeypatch.setattr(keeper_daemon.subprocess, "run",
                        lambda cmd, **kw: seen.update(cmd=cmd, kw=kw))
    keeper_daemon.run_keeper_subprocess(7, 11, kind="keeper_history")
    assert "agf.discovery.keeper_one" in seen["cmd"]
    assert "--flip-vanished" not in seen["cmd"]

    keeper_daemon.run_keeper_subprocess(7, 11, kind="corpus_refresh")
    assert "agf.discovery.keeper_one" in seen["cmd"]
    assert "--flip-vanished" in seen["cmd"]
