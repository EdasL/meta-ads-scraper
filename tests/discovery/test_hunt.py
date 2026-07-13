"""Hunt round executor tests — fake extractor, real DB (sentinel rows)."""
import json

import pytest
from sqlalchemy import text

from agf.db.engine import begin
from agf.discovery.hunt import HUNT_PATIENCE, run_hunt_round
from tests.discovery.conftest import FakeExtractor, make_ad

pytestmark = pytest.mark.usefixtures("_migrated_engine")

PAGE = "HUNTP1"


def _mk_round(engine, terms, competitors=()):
    with begin(engine) as conn:
        hunt_id = conn.execute(text(
            "INSERT INTO frontend.hunts (user_email, title) "
            "VALUES ('t@test.local', 'hunt-exec-test') RETURNING id")).scalar_one()
        return conn.execute(text(
            "INSERT INTO frontend.hunt_rounds (hunt_id, terms, competitor_apps) "
            "VALUES (:h, CAST(:t AS jsonb), CAST(:c AS jsonb)) RETURNING id"),
            {"h": hunt_id, "t": json.dumps(list(terms)),
             "c": json.dumps([{"name": n} for n in competitors])}).scalar_one()


@pytest.fixture
def engine(_migrated_engine):
    yield _migrated_engine
    with begin(_migrated_engine) as conn:
        # Order matters: the pipeline_jobs subselect reads hunt_results, so it must
        # run BEFORE the hunts delete cascades those rows away. Promoted advertisers/
        # apps (sentinel page_id/store_id) come out after the candidates/hunt_results
        # that FK-reference them.
        conn.execute(text(
            "DELETE FROM public.pipeline_jobs WHERE kind = 'enrich' AND advertiser_id IN "
            "(SELECT kept_advertiser_id FROM frontend.hunt_results "
            " WHERE kept_advertiser_id IS NOT NULL)"))
        conn.execute(text(
            "DELETE FROM discovery.dynamic_keywords "
            "WHERE keyword IN ('hunt mint term', 'pending term') AND kind = 'app'"))
        conn.execute(text(
            "DELETE FROM frontend.hunts WHERE user_email = 't@test.local'"))
        conn.execute(text(
            "DELETE FROM discovery.candidates WHERE page_id LIKE 'HUNTP%'"))
        conn.execute(text(
            "DELETE FROM public.advertisers WHERE page_id LIKE 'HUNTP%'"))
        conn.execute(text(
            "DELETE FROM public.apps WHERE store = 'ios' "
            "AND store_id IN ('900050', '900060', '900070')"))


def _ios_ad(store_id: str, page_id: str = PAGE):
    return make_ad(page_id=page_id, page_name=f"App {store_id}",
                   link_url=f"https://apps.apple.com/us/app/id{store_id}")


def test_round_sweeps_terms_and_records_results(engine):
    ext = FakeExtractor()
    ext.script_pages("sleep app", [[_ios_ad("900001"), _ios_ad("900002")], []])
    round_id = _mk_round(engine, ["sleep app"])
    stats = run_hunt_round(engine, [ext], round_id)
    assert stats["found"] == 2 and stats["new"] == 2
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT page_id, store_id, source_term, was_new, candidate_id "
            "FROM frontend.hunt_results WHERE round_id = :r ORDER BY store_id"),
            {"r": round_id}).all()
    assert [r.store_id for r in rows] == ["900001", "900002"]
    assert all(r.was_new and r.candidate_id and r.source_term == "sleep app"
               for r in rows)


def test_non_candidate_dispositions_counted_in_drops(engine):
    # One store-linked ad (candidate) + one web-landing ad (non_app). The
    # non_app ad isn't persisted here, but it must be counted in stats["drops"].
    non_app = make_ad(page_id=PAGE, page_name="Landing",
                      link_url="https://example.com/landing")
    ext = FakeExtractor()
    ext.script_pages("sleep app", [[_ios_ad("900050"), non_app], []])
    stats = run_hunt_round(engine, [ext], _mk_round(engine, ["sleep app"]))
    assert stats["found"] == 1
    assert stats["drops"] == {"non_app": 1}


def test_known_candidate_marked_not_new(engine):
    ext = FakeExtractor()
    ext.script_pages("sleep app", [[_ios_ad("900010")]])
    run_hunt_round(engine, [ext], _mk_round(engine, ["sleep app"]))
    ext2 = FakeExtractor()
    ext2.script_pages("sleep app", [[_ios_ad("900010")]])
    round2 = _mk_round(engine, ["sleep app"])
    stats = run_hunt_round(engine, [ext2], round2)
    assert stats["found"] == 1 and stats["new"] == 0
    with engine.connect() as conn:
        assert conn.execute(text(
            "SELECT was_new FROM frontend.hunt_results WHERE round_id = :r"),
            {"r": round2}).scalar_one() is False


def test_patience_stops_dry_paging(engine):
    dry = [[_ios_ad("900020")]] + [[] for _ in range(HUNT_PATIENCE + 5)]
    ext = FakeExtractor()
    ext.script_pages("dry term", dry)
    stats = run_hunt_round(engine, [ext], _mk_round(engine, ["dry term"]))
    assert stats["pages"] == 1 + HUNT_PATIENCE  # first live page + patience window


def test_progress_events_and_status(engine):
    ext = FakeExtractor()
    ext.script_pages("sleep app", [[_ios_ad("900030")], []])
    round_id = _mk_round(engine, ["sleep app"])
    run_hunt_round(engine, [ext], round_id)
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT status, progress FROM frontend.hunt_rounds WHERE id = :i"),
            {"i": round_id}).one()
    assert row.status == "running"  # terminal stamping is hunt_one's job (Task 4)
    assert row.progress["terms_total"] == 1
    assert row.progress["terms_done"] == 1
    assert row.progress["found"] == 1
    assert any("sleep app" in e["msg"] for e in row.progress["events"])


def test_lane_error_yields_partial_results_not_crash(engine):
    ext = FakeExtractor()
    ext.script_pages("good term", [[_ios_ad("900040")], []])
    # "bad term" is unscripted -> FakeExtractor raises ExtractError for it
    ext.error_queries = {"bad term"}
    round_id = _mk_round(engine, ["bad term", "good term"])
    stats = run_hunt_round(engine, [ext], round_id)
    assert stats["errors"] == 1
    assert stats["found"] == 1
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT progress FROM frontend.hunt_rounds WHERE id = :i"),
            {"i": round_id}).one()
    assert any("could not search" in e["msg"].lower()
               for e in row.progress["events"])


def _meta(name="Dreamy", is_game=False, rating_count=100):
    from types import SimpleNamespace
    return SimpleNamespace(name=name, seller="Dev", genre="Health",
                           release_date=None, avg_rating=4.5,
                           rating_count=rating_count, installs=None,
                           icon_url=None, screenshots=[], price_label=None,
                           description="", is_game=is_game)


def test_competitor_page_pull_records_results(engine):
    ext = FakeExtractor()
    ext.typeahead_results = {"Calm": [
        {"page_id": "HUNTP9", "page_name": "Calm", "page_like_count": 5000,
         "ig_username": None}]}
    ext.listing_ads = {"HUNTP9": [_ios_ad("900050", page_id="HUNTP9")]}
    round_id = _mk_round(engine, [], competitors=("Calm",))
    stats = run_hunt_round(engine, [ext], round_id)
    assert stats["competitor_pages"] == 1
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT source_term, page_id FROM frontend.hunt_results "
            "WHERE round_id = :r"), {"r": round_id}).one()
    assert row.source_term == "page:Calm" and row.page_id == "HUNTP9"


def test_triage_readback_enrich_and_keyword_mint(engine):
    ext = FakeExtractor()
    ext.script_pages("hunt mint term", [[_ios_ad("900060")], []])
    round_id = _mk_round(engine, ["hunt mint term"])
    lookups = lambda ids: {str(i): _meta() for i in ids}
    stats = run_hunt_round(engine, [ext], round_id,
                           lookup_ios=lookups, lookup_android=lookups)
    assert stats["kept"] == 1 and stats["enrich_enqueued"] == 1
    assert stats["keywords_minted"] == 1
    with engine.connect() as conn:
        res = conn.execute(text(
            "SELECT triage, kept_advertiser_id FROM frontend.hunt_results "
            "WHERE round_id = :r"), {"r": round_id}).one()
        assert res.triage == "kept" and res.kept_advertiser_id is not None
        kw = conn.execute(text(
            "SELECT source, status FROM discovery.dynamic_keywords "
            "WHERE keyword = 'hunt mint term' AND kind = 'app'")).one()
        assert kw.source == "hunt" and kw.status == "trial"
        job = conn.execute(text(
            "SELECT status FROM public.pipeline_jobs "
            "WHERE kind = 'enrich' AND advertiser_id = :a"),
            {"a": res.kept_advertiser_id}).scalar_one()
        assert job == "queued"


def test_no_lookups_leaves_triage_pending(engine):
    ext = FakeExtractor()
    ext.script_pages("pending term", [[_ios_ad("900070")], []])
    round_id = _mk_round(engine, ["pending term"])
    stats = run_hunt_round(engine, [ext], round_id)  # no lookups passed
    assert stats["pending"] == 1 and stats["kept"] == 0
