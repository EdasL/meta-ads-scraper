"""DB helpers for public.pipeline_jobs (migration 152). These need real cross-txn
behavior (SKIP-LOCKED claim, lease reaping), so they use the session-scoped
`_migrated_engine` directly with SENTINEL advertiser_ids (>= 999000000) and
DELETE-before/after cleanup — the SWEEPTEST/sentinel convention. Rows are
committed (not rolled back) so concurrent claims are visible across connections.
"""
import pytest
from sqlalchemy import text

from agf.db.pipeline_jobs import (claim_next, enqueue_enrich, purge_done,
                                  reap_expired, set_job_status)

SENTINEL = 999_000_000
A1, A2, A3 = SENTINEL + 1, SENTINEL + 2, SENTINEL + 3


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


def _rows(engine):
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT advertiser_id, status, attempts, lease_expires_at "
            "FROM public.pipeline_jobs WHERE advertiser_id >= :s "
            "ORDER BY id"), {"s": SENTINEL}).fetchall()


def test_enqueue_inserts_in_order(engine):
    with engine.begin() as conn:
        n = enqueue_enrich(conn, [A3, A1, A2])
    assert n == 3
    # insertion order == argument order (FIFO drain = taste priority)
    assert [r[0] for r in _rows(engine)] == [A3, A1, A2]
    assert all(r[1] == "queued" for r in _rows(engine))


def test_reenqueue_of_queued_is_noop(engine):
    with engine.begin() as conn:
        assert enqueue_enrich(conn, [A1, A2]) == 2
    with engine.begin() as conn:
        # A1 still queued -> deduped by the active-uq index; A3 is new
        assert enqueue_enrich(conn, [A1, A3]) == 1
    assert [r[0] for r in _rows(engine)] == [A1, A2, A3]


def test_enqueue_again_after_done(engine):
    with engine.begin() as conn:
        enqueue_enrich(conn, [A1])
        job_id = conn.execute(text(
            "SELECT id FROM public.pipeline_jobs WHERE advertiser_id = :a"),
            {"a": A1}).scalar_one()
        set_job_status(conn, job_id, "done")
    # once the prior job is terminal, the advertiser is no longer "active" -> re-enqueues
    with engine.begin() as conn:
        assert enqueue_enrich(conn, [A1]) == 1
    statuses = [r[1] for r in _rows(engine)]
    assert sorted(statuses) == ["done", "queued"]


def test_claim_next_returns_oldest_and_sets_lease(engine):
    with engine.begin() as conn:
        enqueue_enrich(conn, [A1, A2])
    with engine.begin() as conn:
        claimed = claim_next(conn, "enrich", lease_sec=180)
    assert claimed is not None
    job_id, advertiser_id = claimed
    assert advertiser_id == A1          # oldest first
    rows = {r[0]: r for r in _rows(engine)}
    assert rows[A1][1] == "running" and rows[A1][2] == 1 and rows[A1][3] is not None
    assert rows[A2][1] == "queued"


def test_claim_next_none_when_empty(engine):
    with engine.begin() as conn:
        assert claim_next(conn, "enrich", lease_sec=180) is None


def test_claim_next_skips_locked_row(engine):
    with engine.begin() as conn:
        enqueue_enrich(conn, [A1])       # exactly one queued row
    conn_a = engine.connect()
    conn_b = engine.connect()
    try:
        txn_a = conn_a.begin()
        txn_b = conn_b.begin()
        claimed_a = claim_next(conn_a, "enrich", lease_sec=180)   # locks the row (uncommitted)
        assert claimed_a is not None and claimed_a[1] == A1
        # B must SKIP the locked row -> nothing else queued -> None
        assert claim_next(conn_b, "enrich", lease_sec=180) is None
        txn_a.commit()
        txn_b.rollback()
    finally:
        conn_a.close()
        conn_b.close()


def test_reap_expired_requeues_then_errors(engine):
    with engine.begin() as conn:
        enqueue_enrich(conn, [A1])
        # negative lease -> lease_expires_at is already in the past (attempts -> 1)
        claim_next(conn, "enrich", lease_sec=-100)
    with engine.begin() as conn:
        assert reap_expired(conn, "enrich") == 1
    row = _rows(engine)[0]
    assert row[1] == "queued" and row[3] is None      # requeued, lease cleared, attempts preserved
    assert row[2] == 1

    # push attempts to the ceiling, expire the lease again -> reap errors it
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE public.pipeline_jobs SET status='running', attempts=max_attempts, "
            "lease_expires_at = now() - make_interval(secs => 10) WHERE advertiser_id = :a"),
            {"a": A1})
    with engine.begin() as conn:
        assert reap_expired(conn, "enrich") == 1
    row = _rows(engine)[0]
    assert row[1] == "error" and row[3] is None


def test_purge_done_deletes_only_old_done(engine):
    with engine.begin() as conn:
        enqueue_enrich(conn, [A1, A2, A3])
        ids = {r[0]: r[1] for r in conn.execute(text(
            "SELECT advertiser_id, id FROM public.pipeline_jobs WHERE advertiser_id >= :s"),
            {"s": SENTINEL}).fetchall()}
        # A1: done & OLD -> purged; A2: done & RECENT -> kept; A3: queued -> kept
        set_job_status(conn, ids[A1], "done")
        set_job_status(conn, ids[A2], "done")
        conn.execute(text(
            "UPDATE public.pipeline_jobs SET finished_at = now() - make_interval(days => 40) "
            "WHERE advertiser_id = :a"), {"a": A1})
    with engine.begin() as conn:
        assert purge_done(conn, older_than_days=30) == 1
    remaining = {r[0]: r[1] for r in _rows(engine)}
    assert A1 not in remaining
    assert remaining[A2] == "done" and remaining[A3] == "queued"
