"""One-shot keeper_one CLI unit. `run` COMMITS its own transactions (terminal
status + crawl_log TTL marker), so these use the session committing engine
(`_migrated_engine`) with a KEEPERONE-sentinel advertiser and DELETE-before/after
cleanup — mirroring tests/enrich_full/test_pipeline_daemon.py."""
import pytest
from sqlalchemy import text

from agf.db import repo
from agf.db.engine import begin
from agf.db.pipeline_jobs import enqueue_jobs
from agf.extract.errors import ExtractError

PAGE_ID = "KEEPERONE1"


def _clean(engine):
    with engine.begin() as conn:
        ids = conn.execute(text(
            "SELECT id FROM public.advertisers WHERE page_id = :p "
            "OR page_name = 'Keeper One'"),
            {"p": PAGE_ID}).scalars().all()
        if ids:
            conn.execute(text(
                "DELETE FROM public.pipeline_jobs WHERE advertiser_id = ANY(:i)"),
                {"i": ids})
            conn.execute(text(
                "DELETE FROM logging.crawl_log WHERE entity_type='keeper_history' "
                "AND entity_id = ANY(:i)"), {"i": ids})
            conn.execute(text(
                "DELETE FROM public.advertisers WHERE id = ANY(:i)"), {"i": ids})


@pytest.fixture
def db_engine(_migrated_engine):
    _clean(_migrated_engine)
    try:
        yield _migrated_engine
    finally:
        _clean(_migrated_engine)


@pytest.fixture
def seeded_keeper_no_history(db_engine):
    from types import SimpleNamespace
    with begin(db_engine) as conn:
        adv = repo.upsert_advertiser(conn, SimpleNamespace(
            page_id=PAGE_ID, page_name="Keeper One", page_like_count=900))
    return adv


def _seed_job(conn, adv_id):
    enqueue_jobs(conn, "keeper_history", [adv_id])
    return conn.execute(text(
        "SELECT id FROM public.pipeline_jobs WHERE kind='keeper_history' "
        "AND advertiser_id=:a ORDER BY id DESC LIMIT 1"), {"a": adv_id}).scalar_one()


def test_run_success_marks_done(db_engine, seeded_keeper_no_history):
    adv = seeded_keeper_no_history
    with begin(db_engine) as conn:
        job = _seed_job(conn, adv)

    async def fake_refresh(engine, extractor, advertiser_id, page_id, **kw):
        return {"ads_stored": 3, "truncated": False, "flipped": 0}

    from agf.discovery.keeper_one import run
    assert run(db_engine, adv, job, refresh=fake_refresh, extractor=object()) == 0
    with db_engine.connect() as conn:
        status = conn.execute(text(
            "SELECT status FROM public.pipeline_jobs WHERE id=:i"), {"i": job}).scalar_one()
        assert status == "done"


def test_run_extract_error_marks_error_and_crawl_log(db_engine, seeded_keeper_no_history):
    adv = seeded_keeper_no_history
    with begin(db_engine) as conn:
        job = _seed_job(conn, adv)

    async def fake_refresh(*a, **kw):
        raise ExtractError("boom")

    from agf.discovery.keeper_one import run
    assert run(db_engine, adv, job, refresh=fake_refresh, extractor=object()) == 1
    with db_engine.connect() as conn:
        status = conn.execute(text(
            "SELECT status FROM public.pipeline_jobs WHERE id=:i"), {"i": job}).scalar_one()
        assert status == "error"
        marked = conn.execute(text(
            "SELECT crawl_result FROM logging.crawl_log WHERE entity_type='keeper_history' "
            "AND entity_id=:a ORDER BY crawled_at DESC LIMIT 1"), {"a": adv}).scalar_one()
        assert marked.startswith("error:")


MISSING_ADV = 999_111_222


def test_run_missing_advertiser_marks_error(db_engine):
    """No advertiser row (page_id lookup returns None) — terminal error + TTL
    marker, refresh never runs."""
    with begin(db_engine) as conn:
        conn.execute(text(
            "DELETE FROM public.pipeline_jobs WHERE advertiser_id=:a"),
            {"a": MISSING_ADV})
        conn.execute(text(
            "DELETE FROM logging.crawl_log WHERE entity_type='keeper_history' "
            "AND entity_id=:a"), {"a": MISSING_ADV})
        job = _seed_job(conn, MISSING_ADV)

    async def fake_refresh(*a, **kw):
        raise AssertionError("refresh must not run without a page_id")

    from agf.discovery.keeper_one import run
    try:
        assert run(db_engine, MISSING_ADV, job, refresh=fake_refresh,
                   extractor=object()) == 1
        with db_engine.connect() as conn:
            status = conn.execute(text(
                "SELECT status FROM public.pipeline_jobs WHERE id=:i"),
                {"i": job}).scalar_one()
            assert status == "error"
            marked = conn.execute(text(
                "SELECT crawl_result FROM logging.crawl_log "
                "WHERE entity_type='keeper_history' AND entity_id=:a "
                "ORDER BY crawled_at DESC LIMIT 1"), {"a": MISSING_ADV}).scalar_one()
            assert marked.startswith("error:")
    finally:
        with db_engine.begin() as conn:
            conn.execute(text(
                "DELETE FROM public.pipeline_jobs WHERE advertiser_id=:a"),
                {"a": MISSING_ADV})
            conn.execute(text(
                "DELETE FROM logging.crawl_log WHERE entity_type='keeper_history' "
                "AND entity_id=:a"), {"a": MISSING_ADV})
