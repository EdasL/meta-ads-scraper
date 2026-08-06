"""One-shot add_page_one CLI unit — the pasted-link job body. `run` COMMITS its
own transactions (phase writes, terminal status, crawl_log marker), so these use
the session committing engine (`_migrated_engine`) with an ADDPAGE-sentinel
advertiser and DELETE-before/after cleanup, mirroring tests/discovery/test_keeper_one.py.

Every collaborator (scrape, triage, enrich) is injected, so these assert
PERSISTED STATE — what the DB looks like afterwards — not call counts.
"""
import pytest
from sqlalchemy import text

from agf.db.engine import begin
from agf.discovery import add_page_one

PAGE_ID = "ADDPAGE0001"
OTHER_PAGE_ID = "ADDPAGE0002"
STORE_ID = "com.addpage.probe"


def _clean(engine):
    with engine.begin() as conn:
        ids = conn.execute(text(
            "SELECT id FROM public.advertisers WHERE page_id IN (:p, :o)"),
            {"p": PAGE_ID, "o": OTHER_PAGE_ID}).scalars().all()
        if ids:
            conn.execute(text(
                "DELETE FROM public.pipeline_jobs WHERE advertiser_id = ANY(:i)"), {"i": ids})
            conn.execute(text(
                "DELETE FROM logging.crawl_log WHERE entity_type = 'add_page' "
                "AND entity_id = ANY(:i)"), {"i": ids})
            conn.execute(text(
                "DELETE FROM adtech.advertiser_scores WHERE advertiser_id = ANY(:i)"), {"i": ids})
            conn.execute(text("DELETE FROM public.ads WHERE advertiser_id = ANY(:i)"), {"i": ids})
            conn.execute(text(
                "DELETE FROM discovery.candidates WHERE page_id IN (:p, :o)"),
                {"p": PAGE_ID, "o": OTHER_PAGE_ID})
            conn.execute(text("DELETE FROM public.advertisers WHERE id = ANY(:i)"), {"i": ids})
        conn.execute(text("DELETE FROM public.apps WHERE store_id = :s"), {"s": STORE_ID})


@pytest.fixture
def db_engine(_migrated_engine):
    _clean(_migrated_engine)
    try:
        yield _migrated_engine
    finally:
        _clean(_migrated_engine)


@pytest.fixture
def seeded(db_engine):
    """An advertiser + a running add_page job, the state the API leaves behind."""
    with begin(db_engine) as conn:
        aid = conn.execute(text(
            "INSERT INTO public.advertisers (page_id) VALUES (:p) RETURNING id"),
            {"p": PAGE_ID}).scalar_one()
        job = conn.execute(text(
            "INSERT INTO public.pipeline_jobs (kind, advertiser_id, status) "
            "VALUES ('add_page', :a, 'running') RETURNING id"), {"a": aid}).scalar_one()
    return db_engine, aid, job


def _job(engine, job_id):
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT status, error_text, payload->>'phase' AS phase, "
            "       payload->>'triage_note' AS note "
            "FROM public.pipeline_jobs WHERE id = :i"), {"i": job_id}).mappings().one()


def _manual_add(engine, advertiser_id):
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT manual_add FROM public.advertisers WHERE id = :a"),
            {"a": advertiser_id}).scalar()


def _store_ad(engine, advertiser_id, link_url, external_ad_id):
    """One ad whose destination is a Play Store listing."""
    with begin(engine) as conn:
        conn.execute(text(
            "INSERT INTO public.ads (external_ad_id, advertiser_id, link_url, is_active) "
            "VALUES (:e, :a, :l, TRUE)"),
            {"e": external_ad_id, "a": advertiser_id, "l": link_url})


async def _noop_refresh(engine, extractor, advertiser_id, page_id, flip_vanished=False):
    return {"ads_stored": 0, "truncated": False, "flipped": 0}


def test_finishes_done_and_marks_the_advertiser_as_a_manual_add(seeded):
    engine, aid, job = seeded
    seen = {}

    async def fake_refresh(eng, extractor, advertiser_id, page_id, flip_vanished=False):
        seen["args"] = (advertiser_id, page_id, flip_vanished)
        return {"ads_stored": 3}

    rc = add_page_one.run(engine, aid, job,
                          refresh=fake_refresh,
                          triage=lambda conn, **kw: {"examined": 1, "kept": 1, "dropped": {}},
                          enrich=lambda **kw: None,
                          extractor=object())

    assert rc == 0
    assert seen["args"] == (aid, PAGE_ID, False), "the scrape must not flip vanished ads"
    assert _manual_add(engine, aid) is True
    assert _job(engine, job)["status"] == "done"


def test_a_kept_candidate_links_the_stored_ads_to_the_app(seeded):
    """The ordering bug this module exists to avoid: ads are stored before triage
    can resolve the app, so without the backfill they keep app_id NULL and the
    gem renders with no app name, icon or ratings."""
    engine, aid, job = seeded
    _store_ad(engine, aid,
              f"https://play.google.com/store/apps/details?id={STORE_ID}", "ADDPAGE_AD_1")

    # A triage stand-in that does what the real one does on a keep: create the
    # app row and point the candidate at this advertiser.
    def fake_triage(conn, *, lookup_ios, lookup_android, only_ids=(), limit=None):
        only_ids = list(only_ids or ())
        app_id = conn.execute(text(
            "INSERT INTO public.apps (store, store_id, name) "
            "VALUES ('android', :s, 'Probe App') "
            "ON CONFLICT (store, store_id) DO UPDATE SET name = EXCLUDED.name "
            "RETURNING id"), {"s": STORE_ID}).scalar_one()
        conn.execute(text(
            "UPDATE discovery.candidates SET status = 'kept', kept_advertiser_id = :a, "
            "triaged_at = now() WHERE id = ANY(:i)"), {"a": aid, "i": only_ids})
        assert app_id
        return {"examined": len(only_ids), "kept": len(only_ids), "dropped": {}}

    rc = add_page_one.run(engine, aid, job, refresh=_noop_refresh,
                          triage=fake_triage, enrich=lambda **kw: None,
                          extractor=object(), lookup_ios=lambda ids: {},
                          lookup_android=lambda ids: {})
    assert rc == 0

    with engine.connect() as conn:
        cand = conn.execute(text(
            "SELECT store, store_id, technique, source_keyword FROM discovery.candidates "
            "WHERE page_id = :p"), {"p": PAGE_ID}).mappings().all()
        linked = conn.execute(text(
            "SELECT count(*) FROM public.ads a JOIN public.apps ap ON ap.id = a.app_id "
            "WHERE a.advertiser_id = :a AND ap.store_id = :s"),
            {"a": aid, "s": STORE_ID}).scalar()

    assert len(cand) == 1
    assert cand[0]["store"] == "android" and cand[0]["store_id"] == STORE_ID
    assert cand[0]["technique"] == "manual_add", "provenance must not read as sweep yield"
    assert cand[0]["source_keyword"] is None
    assert linked == 1, "the stored ad must end up linked to the triaged app"


def test_a_veto_is_recorded_as_a_note_and_the_advertiser_is_still_added(seeded):
    engine, aid, job = seeded
    _store_ad(engine, aid,
              f"https://play.google.com/store/apps/details?id={STORE_ID}", "ADDPAGE_AD_2")

    rc = add_page_one.run(
        engine, aid, job, refresh=_noop_refresh,
        triage=lambda conn, **kw: {"examined": 1, "kept": 0, "dropped": {"no_app": 1}},
        enrich=lambda **kw: None, extractor=object(),
        lookup_ios=lambda ids: {}, lookup_android=lambda ids: {})

    assert rc == 0
    assert _manual_add(engine, aid) is True, "a veto must not stop the add"
    row = _job(engine, job)
    assert row["status"] == "done"
    assert row["note"] and "no_app" in row["note"]


def test_an_advertiser_with_no_store_links_still_completes_with_a_note(seeded):
    engine, aid, job = seeded
    _store_ad(engine, aid, "https://sleepwave.app/start", "ADDPAGE_AD_3")

    rc = add_page_one.run(engine, aid, job, refresh=_noop_refresh,
                          enrich=lambda **kw: None, extractor=object())

    assert rc == 0
    assert _manual_add(engine, aid) is True
    row = _job(engine, job)
    assert row["status"] == "done"
    assert "app store listing" in (row["note"] or "")
    with engine.connect() as conn:
        assert conn.execute(text(
            "SELECT count(*) FROM discovery.candidates WHERE page_id = :p"),
            {"p": PAGE_ID}).scalar() == 0


def test_a_scrape_failure_writes_a_terminal_error_and_a_crawl_marker(seeded):
    engine, aid, job = seeded

    async def boom(eng, extractor, advertiser_id, page_id, flip_vanished=False):
        raise RuntimeError("meta said no")

    rc = add_page_one.run(engine, aid, job, refresh=boom,
                          enrich=lambda **kw: None, extractor=object())

    assert rc == 1
    row = _job(engine, job)
    assert row["status"] == "error"
    assert "meta said no" in (row["error_text"] or "")
    assert _manual_add(engine, aid) is False, "a failed scrape must not add the advertiser"
    with engine.connect() as conn:
        marked = conn.execute(text(
            "SELECT count(*) FROM logging.crawl_log "
            "WHERE entity_type = 'add_page' AND entity_id = :a"), {"a": aid}).scalar()
    assert marked >= 1, "without the TTL marker a dead page is re-claimed forever"


def test_an_advertiser_with_a_blank_page_id_fails_cleanly(seeded):
    """page_id is NOT NULL, so the unusable case is a blank one — the guard is
    `not row.page_id`, not `row.page_id is None`."""
    engine, aid, job = seeded
    with begin(engine) as conn:
        conn.execute(text("UPDATE public.advertisers SET page_id = '' WHERE id = :a"), {"a": aid})
    try:
        rc = add_page_one.run(engine, aid, job, extractor=object())
    finally:
        # page_id is UNIQUE, so a leaked '' row collides with the NEXT run of
        # this test. Restore the sentinel here rather than teaching _clean about
        # a value it cannot attribute to this module.
        with begin(engine) as conn:
            conn.execute(text("UPDATE public.advertisers SET page_id = :p WHERE id = :a"),
                         {"p": PAGE_ID, "a": aid})

    assert rc == 1
    row = _job(engine, job)
    assert row["status"] == "error"
    assert "page_id" in (row["error_text"] or "")


def test_a_job_pointing_at_no_advertiser_fails_cleanly(seeded):
    """The daemon must never wedge on a job whose advertiser was deleted."""
    engine, _aid, job = seeded

    rc = add_page_one.run(engine, 999999999, job, extractor=object())

    assert rc == 1
    assert _job(engine, job)["status"] == "error"


def test_manual_add_survives_a_hard_veto_in_the_matview(seeded):
    """Migration 165's escape hatch, end to end: a manual_add advertiser whose
    score row is vetoed is in the gem matview; an identical non-manual one is not."""
    from agf.scoring.run_scoring import refresh_emerging_matview

    engine, aid, _job_id = seeded
    with begin(engine) as conn:
        other = conn.execute(text(
            "INSERT INTO public.advertisers (page_id) VALUES (:o) RETURNING id"),
            {"o": OTHER_PAGE_ID}).scalar_one()
        conn.execute(text(
            "UPDATE public.advertisers SET manual_add = TRUE WHERE id = :a"), {"a": aid})
        for adv in (aid, other):
            conn.execute(text(
                "INSERT INTO adtech.advertiser_scores "
                "(advertiser_id, cohort, gem_score, vetoed, veto_reason) "
                "VALUES (:a, 'test', 10, TRUE, 'no_app') "
                "ON CONFLICT (advertiser_id) DO UPDATE "
                "SET vetoed = TRUE, veto_reason = 'no_app'"), {"a": adv})

    refresh_emerging_matview(engine)
    with engine.connect() as conn:
        present = {r[0] for r in conn.execute(text(
            "SELECT advertiser_id FROM frontend.emerging_scaling_apps "
            "WHERE advertiser_id IN (:a, :b)"), {"a": aid, "b": other})}

    assert aid in present, "a manual add must surface despite the veto"
    assert other not in present, "the veto must still hold for everyone else"
