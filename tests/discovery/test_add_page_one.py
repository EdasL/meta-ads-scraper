"""One-shot add_page_one CLI unit — the pasted-link job body. `run` COMMITS its
own transactions (phase writes, terminal status, crawl_log marker), so these use
the session committing engine (`_migrated_engine`) with an ADDPAGE-sentinel
advertiser and DELETE-before/after cleanup, mirroring tests/discovery/test_keeper_one.py.

The scrape and the enrich are injected; the STORE LOOKUPS are stubbed at the same
seam production uses, so these assert persisted state — what the DB looks like
afterwards — rather than call counts.
"""
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from agf.db.engine import begin
from agf.discovery import add_page_one

PAGE_ID = "ADDPAGE0001"
OTHER_PAGE_ID = "ADDPAGE0002"
STORE_ID = "com.addpage.probe"
PLAY_URL = f"https://play.google.com/store/apps/details?id={STORE_ID}"


def _meta(*, name="Probe App", rating_count=10, is_game=False):
    """The AppMeta shape triage_decision + _app_dict read."""
    return SimpleNamespace(
        name=name, seller="Probe Ltd", genre="Education", release_date=None,
        avg_rating=4.5, rating_count=rating_count, installs=1000,
        icon_url="https://example.com/i.png", screenshots=None,
        price_label="Free", description="A probe app.", is_game=is_game)


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
        if ids:
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
    """An advertiser + a running add_page job — the state the API leaves behind."""
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


def _linked_ads(engine, advertiser_id):
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT count(*) FROM public.ads a JOIN public.apps ap ON ap.id = a.app_id "
            "WHERE a.advertiser_id = :a AND ap.store_id = :s"),
            {"a": advertiser_id, "s": STORE_ID}).scalar()


def _store_ad(engine, advertiser_id, link_url, external_ad_id):
    with begin(engine) as conn:
        conn.execute(text(
            "INSERT INTO public.ads (external_ad_id, advertiser_id, link_url, is_active) "
            "VALUES (:e, :a, :l, TRUE)"),
            {"e": external_ad_id, "a": advertiser_id, "l": link_url})


async def _noop_refresh(engine, extractor, advertiser_id, page_id, flip_vanished=False):
    return {"ads_stored": 0, "truncated": False, "flipped": 0}


def _android(meta):
    """A google-play lookup stub keyed the way run_triage's callers key it."""
    return lambda ids: {STORE_ID: meta} if STORE_ID in list(ids) else {}


def test_finishes_done_and_marks_the_advertiser_as_a_manual_add(seeded):
    engine, aid, job = seeded
    seen = {}

    async def fake_refresh(eng, extractor, advertiser_id, page_id, flip_vanished=False):
        seen["args"] = (advertiser_id, page_id, flip_vanished)
        return {"ads_stored": 3}

    rc = add_page_one.run(engine, aid, job, refresh=fake_refresh,
                          enrich=lambda **kw: None, extractor=object(),
                          lookup_ios=lambda ids: {}, lookup_android=lambda ids: {})

    assert rc == 0
    assert seen["args"] == (aid, PAGE_ID, False), "the scrape must not flip vanished ads"
    assert _manual_add(engine, aid) is True
    assert _job(engine, job)["status"] == "done"


def test_a_resolved_listing_links_the_stored_ads_to_the_app(seeded):
    """Ads are stored before any app exists to link them to, so the backfill is
    what gives the gem its name, icon and ratings."""
    engine, aid, job = seeded
    _store_ad(engine, aid, PLAY_URL, "ADDPAGE_AD_1")

    rc = add_page_one.run(engine, aid, job, refresh=_noop_refresh,
                          enrich=lambda **kw: None, extractor=object(),
                          lookup_ios=lambda ids: {}, lookup_android=_android(_meta()))
    assert rc == 0

    with engine.connect() as conn:
        cand = conn.execute(text(
            "SELECT store, store_id, status, kept_advertiser_id, technique, source_keyword "
            "FROM discovery.candidates WHERE page_id = :p"), {"p": PAGE_ID}).mappings().all()

    assert len(cand) == 1
    assert cand[0]["store"] == "android" and cand[0]["store_id"] == STORE_ID
    assert cand[0]["status"] == "kept" and cand[0]["kept_advertiser_id"] == aid
    assert cand[0]["technique"] == "manual_add", "provenance must not read as sweep yield"
    assert cand[0]["source_keyword"] is None
    assert _linked_ads(engine, aid) == 1
    assert _job(engine, job)["note"] is None, "a clean keep needs no note"


def test_a_page_the_sweep_already_dropped_is_still_promoted(seeded):
    """THE prod bug (2026-08-06, 'Zutobi Drivers Ed'): discovery.candidates never
    regresses status, and run_triage only reads status='new'. A page the sweep
    already dropped therefore got zero triage, zero promotion, and every scraped
    ad kept app_id NULL — a gem with no name, icon or ratings. Pasting a
    previously-rejected page is the single most likely use of this feature, so it
    must promote anyway and say why it was flagged."""
    engine, aid, job = seeded
    _store_ad(engine, aid, PLAY_URL, "ADDPAGE_AD_2")
    with begin(engine) as conn:
        conn.execute(text(
            "INSERT INTO discovery.candidates "
            "(page_id, store, store_id, status, drop_reason, triaged_at) "
            "VALUES (:p, 'android', :s, 'dropped', 'ratings_over_threshold', now())"),
            {"p": PAGE_ID, "s": STORE_ID})

    rc = add_page_one.run(
        engine, aid, job, refresh=_noop_refresh, enrich=lambda **kw: None,
        extractor=object(), lookup_ios=lambda ids: {},
        lookup_android=_android(_meta(rating_count=5_000_000)))

    assert rc == 0
    assert _manual_add(engine, aid) is True
    assert _linked_ads(engine, aid) == 1, "the previously-dropped page must still link its app"
    with engine.connect() as conn:
        status, kept = conn.execute(text(
            "SELECT status, kept_advertiser_id FROM discovery.candidates WHERE page_id = :p"),
            {"p": PAGE_ID}).one()
    assert (status, kept) == ("kept", aid)
    note = _job(engine, job)["note"]
    assert note and "too many ratings" in note, "the veto reason must still be shown"


def test_a_game_is_added_with_its_reason_shown(seeded):
    engine, aid, job = seeded
    _store_ad(engine, aid, PLAY_URL, "ADDPAGE_AD_3")

    rc = add_page_one.run(engine, aid, job, refresh=_noop_refresh,
                          enrich=lambda **kw: None, extractor=object(),
                          lookup_ios=lambda ids: {},
                          lookup_android=_android(_meta(is_game=True)))

    assert rc == 0
    assert _manual_add(engine, aid) is True
    assert _linked_ads(engine, aid) == 1
    assert "is a game" in (_job(engine, job)["note"] or "")


def test_an_advertiser_with_no_store_links_still_completes_with_a_note(seeded):
    engine, aid, job = seeded
    _store_ad(engine, aid, "https://sleepwave.app/start", "ADDPAGE_AD_4")

    rc = add_page_one.run(engine, aid, job, refresh=_noop_refresh,
                          enrich=lambda **kw: None, extractor=object(),
                          lookup_ios=lambda ids: {}, lookup_android=lambda ids: {})

    assert rc == 0
    assert _manual_add(engine, aid) is True
    row = _job(engine, job)
    assert row["status"] == "done"
    assert "app store listing" in (row["note"] or "")
    with engine.connect() as conn:
        assert conn.execute(text(
            "SELECT count(*) FROM discovery.candidates WHERE page_id = :p"),
            {"p": PAGE_ID}).scalar() == 0


def test_a_store_outage_does_not_block_the_add(seeded):
    """A failing store lookup must not cost the founder the advertiser."""
    engine, aid, job = seeded
    _store_ad(engine, aid, PLAY_URL, "ADDPAGE_AD_5")

    def boom(ids):
        raise RuntimeError("play store down")

    rc = add_page_one.run(engine, aid, job, refresh=_noop_refresh,
                          enrich=lambda **kw: None, extractor=object(),
                          lookup_ios=lambda ids: {}, lookup_android=boom)

    assert rc == 0
    assert _manual_add(engine, aid) is True
    assert _job(engine, job)["status"] == "done"


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
        # page_id is UNIQUE, so a leaked '' row collides with the NEXT run.
        with begin(engine) as conn:
            conn.execute(text("UPDATE public.advertisers SET page_id = :p WHERE id = :a"),
                         {"p": PAGE_ID, "a": aid})

    assert rc == 1
    row = _job(engine, job)
    assert row["status"] == "error"
    assert "page_id" in (row["error_text"] or "")


def test_a_job_pointing_at_no_advertiser_fails_cleanly(seeded):
    engine, _aid, job = seeded
    rc = add_page_one.run(engine, 999999999, job, extractor=object())
    assert rc == 1
    assert _job(engine, job)["status"] == "error"


def test_every_triage_drop_reason_has_plain_english_copy():
    """The mapping and triage_decision must not drift apart: a reason added to
    the triage rules with no copy here would surface as no note at all."""
    from agf.discovery.triage import triage_decision

    reasons = {"no_us_listing", "game", "ratings_over_threshold"}
    assert set(add_page_one._DROP_REASONS) == reasons

    for reason in sorted(reasons):
        note = add_page_one._note_for([reason])
        # The tell of a leaked identifier is the snake_case shape, not the words:
        # "its app is a game" legitimately contains "game".
        assert note, f"{reason} produced no note"
        assert "_" not in note, f"{reason} leaked into the note as a raw code"
        assert note.endswith(".")

    assert add_page_one._note_for([]) is None
    assert triage_decision(None) == ("dropped", "no_us_listing")


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
