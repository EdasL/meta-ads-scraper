"""Hunt round executor tests — fake extractor, real DB (sentinel rows)."""
import json
import threading

import pytest
from sqlalchemy import text

from agf.db.engine import begin
from agf.db.hunts import clamp_hunt_settings, term_outcome_stats
from agf.discovery.hunt import (HUNT_COMPETITOR_COUNTRIES, HUNT_PATIENCE,
                                _name_similarity, _Progress, _pull_competitor,
                                run_hunt_round)
from tests.discovery.conftest import FakeExtractor, make_ad


def fresh_stats() -> dict:
    """A stats accumulator with the keys _pull_competitor / _persist_page touch."""
    return {"found": 0, "new": 0, "errors": 0, "competitor_pages": 0, "drops": {}}

pytestmark = pytest.mark.usefixtures("_migrated_engine")

PAGE = "HUNTP1"


def _mk_round(engine, terms, competitors=(), *, competitor_json=None, settings=None):
    # competitor_json overrides the default {"name": ...} dict shape so a test can
    # store the EXACT jsonb the Go API writes (a plain array of strings, ["calm"]).
    comp = (competitor_json if competitor_json is not None
            else [{"name": n} for n in competitors])
    with begin(engine) as conn:
        hunt_id = conn.execute(text(
            "INSERT INTO frontend.hunts (user_email, title) "
            "VALUES ('t@test.local', 'hunt-exec-test') RETURNING id")).scalar_one()
        return conn.execute(text(
            "INSERT INTO frontend.hunt_rounds (hunt_id, terms, competitor_apps, settings) "
            "VALUES (:h, CAST(:t AS jsonb), CAST(:c AS jsonb), CAST(:s AS jsonb)) "
            "RETURNING id"),
            {"h": hunt_id, "t": json.dumps(list(terms)),
             "c": json.dumps(comp), "s": json.dumps(settings or {})}).scalar_one()


def _progress(engine, round_id):
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT progress FROM frontend.hunt_rounds WHERE id = :i"),
            {"i": round_id}).scalar_one()


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
            "DELETE FROM discovery.candidates WHERE page_id LIKE 'HUNTP%' "
            "OR page_id LIKE 'HUNTFOREIGN%'"))
        conn.execute(text(
            "DELETE FROM public.advertisers WHERE page_id LIKE 'HUNTP%' "
            "OR page_id LIKE 'HUNTFOREIGN%'"))
        conn.execute(text(
            "DELETE FROM public.apps WHERE store = 'ios' "
            "AND store_id IN ('900050', '900060', '900070', '900099')"))


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
    # per-term telemetry: page 1 finds 2, page 2 empty -> Meta exhausted.
    # Phase 5 (no lookups here) merges per-term outcome tallies onto the entry:
    # both results stay pending, so kept/relevant/off_target are all 0.
    assert _progress(engine, round_id)["terms"]["sleep app"] == {
        "pages": 2, "found": 2, "new": 2, "stop": "exhausted",
        "kept": 0, "relevant": 0, "off_target": 0}


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


def test_clamp_settings_min_pages_and_patience_default():
    knobs = clamp_hunt_settings(None)
    assert knobs["patience"] == 6
    assert knobs["min_pages"] == 10
    assert clamp_hunt_settings({"min_pages": 99})["min_pages"] == 30
    assert clamp_hunt_settings({"min_pages": 0})["min_pages"] == 1


def test_dry_exit_waits_for_min_pages(engine):
    # Page 1 yields a net-new ad; pages 2..N re-find that same ad (new=0 each).
    # patience 2 would stop at page 3 under the OLD rule, but the min_pages 6
    # floor holds the dry exit until depth >= 6. has_next stays true throughout.
    ad = _ios_ad("900202")
    ext = FakeExtractor()
    ext.script_pages("floored term", [[ad] for _ in range(8)])
    round_id = _mk_round(engine, ["floored term"],
                         settings={"patience": 2, "min_pages": 6})
    run_hunt_round(engine, [ext], round_id)
    term = _progress(engine, round_id)["terms"]["floored term"]
    assert term["stop"] == "dry"
    assert term["pages"] == 6


def test_results_carry_page_depth(engine):
    # A new ad on page 1 and another new ad on page 3; the two hunt_results rows
    # must record page = 1 and page = 3. Page 4 is empty -> Meta exhausted.
    ext = FakeExtractor()
    ext.script_pages("depth term",
                     [[_ios_ad("900204")], [], [_ios_ad("900206")], []])
    round_id = _mk_round(engine, ["depth term"])
    run_hunt_round(engine, [ext], round_id)
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT store_id, page FROM frontend.hunt_results "
            "WHERE round_id = :r ORDER BY store_id"), {"r": round_id}).all()
    assert [(r.store_id, r.page) for r in rows] == [
        ("900204", 1), ("900206", 3)]


def test_patience_stops_dry_paging(engine):
    dry = [[_ios_ad("900020")]] + [[] for _ in range(HUNT_PATIENCE + 5)]
    ext = FakeExtractor()
    ext.script_pages("dry term", dry)
    # min_pages 1 disables the depth floor so patience alone drives the early exit.
    stats = run_hunt_round(engine, [ext],
                           _mk_round(engine, ["dry term"], settings={"min_pages": 1}))
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


def test_string_competitor_apps_runs_phase0(engine):
    # frontend.hunt_rounds.competitor_apps is a jsonb array of STRINGS (["calm"])
    # — the real contract the Go API writes. Phase 0 must accept that shape, not
    # only the legacy [{"name": ...}] dicts.
    ext = FakeExtractor()
    ext.typeahead_results = {"calm": [
        {"page_id": "HUNTP9", "page_name": "Calm", "page_like_count": 5000,
         "ig_username": None}]}
    ext.listing_ads = {"HUNTP9": [_ios_ad("900050", page_id="HUNTP9")]}
    round_id = _mk_round(engine, [], competitor_json=["calm"])
    stats = run_hunt_round(engine, [ext], round_id)
    assert stats["competitor_pages"] == 1
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT source_term, page_id FROM frontend.hunt_results "
            "WHERE round_id = :r"), {"r": round_id}).one()
    assert row.source_term == "page:calm" and row.page_id == "HUNTP9"


def test_round_triage_scoped_to_own_candidates(engine):
    # A round's phase-5 triage must touch ONLY its own candidates — never drain the
    # global status='new' backlog (which risks SIGALRM-killing the round at 1800s).
    from agf.db.candidates import upsert_sweep_candidate
    with begin(engine) as conn:
        upsert_sweep_candidate(conn, {
            "page_id": "HUNTFOREIGN1", "page_name": "Foreign",
            "page_like_count": 100, "store": "ios", "store_id": "900099",
            "link_url": "https://apps.apple.com/us/app/id900099",
            "cta_type": "INSTALL_MOBILE_APP", "ad_start_date": None,
            "source_keyword": "foreign", "technique": "sweep",
            "first_seen_depth": 1})
    ext = FakeExtractor()
    ext.script_pages("scoped term", [[_ios_ad("900060")], []])
    round_id = _mk_round(engine, ["scoped term"])
    lookups = lambda ids: {str(i): _meta() for i in ids}
    stats = run_hunt_round(engine, [ext], round_id,
                           lookup_ios=lookups, lookup_android=lookups)
    assert stats["kept"] == 1
    with engine.connect() as conn:
        own = conn.execute(text(
            "SELECT status FROM discovery.candidates "
            "WHERE page_id = 'HUNTP1' AND store_id = '900060'")).scalar_one()
        foreign = conn.execute(text(
            "SELECT status FROM discovery.candidates "
            "WHERE page_id = 'HUNTFOREIGN1'")).scalar_one()
    assert own == "kept"
    assert foreign == "new"   # untouched: outside the round's candidate set


def test_settings_page_cap_limits_paging(engine):
    # settings page_cap=2 stops a 5-live-page term at 2 pages with stop 'cap'.
    # patience=1 must not fire first: every page carries a net-new ad.
    pages = [[_ios_ad(f"90010{i}")] for i in range(5)]
    ext = FakeExtractor()
    ext.script_pages("cap term", pages)
    round_id = _mk_round(engine, ["cap term"],
                         settings={"page_cap": 2, "patience": 1})
    run_hunt_round(engine, [ext], round_id)
    assert _progress(engine, round_id)["terms"]["cap term"]["pages"] == 2
    assert _progress(engine, round_id)["terms"]["cap term"]["stop"] == "cap"


def test_settings_out_of_bounds_accepted_by_executor(engine):
    # page_cap 999 clamps to 100; the executor accepts it and the 2-page term
    # exhausts long before the clamped cap.
    ext = FakeExtractor()
    ext.script_pages("wide term", [[_ios_ad("900108")], []])
    round_id = _mk_round(engine, ["wide term"], settings={"page_cap": 999})
    stats = run_hunt_round(engine, [ext], round_id)
    assert stats["found"] == 1
    assert _progress(engine, round_id)["terms"]["wide term"]["stop"] == "exhausted"


def test_term_stop_dry_recorded(engine):
    dry = [[_ios_ad("900021")]] + [[] for _ in range(HUNT_PATIENCE + 5)]
    ext = FakeExtractor()
    ext.script_pages("dry term", dry)
    # min_pages 1 disables the depth floor so patience alone drives the early exit.
    round_id = _mk_round(engine, ["dry term"], settings={"min_pages": 1})
    run_hunt_round(engine, [ext], round_id)
    term = _progress(engine, round_id)["terms"]["dry term"]
    assert term["stop"] == "dry"
    assert term["pages"] == 1 + HUNT_PATIENCE


def test_term_stop_error_recorded(engine):
    ext = FakeExtractor()
    ext.error_queries = {"bad term"}
    round_id = _mk_round(engine, ["bad term"])
    run_hunt_round(engine, [ext], round_id)
    assert _progress(engine, round_id)["terms"]["bad term"]["stop"] == "error"


def test_competitor_pull_records_listing_stop(engine):
    ext = FakeExtractor()
    ext.typeahead_results = {"Calm": [
        {"page_id": "HUNTP9", "page_name": "Calm", "page_like_count": 5000,
         "ig_username": None}]}
    ext.listing_ads = {"HUNTP9": [_ios_ad("900050", page_id="HUNTP9")]}
    round_id = _mk_round(engine, [], competitors=("Calm",))
    run_hunt_round(engine, [ext], round_id)
    term = _progress(engine, round_id)["terms"]["page:Calm"]
    assert term["stop"] == "listing_ok" and term["found"] == 1


def test_pull_competitor_prefers_name_match_over_first_hit(engine):
    # Typeahead ranks a big look-alike page first (the live 'Femmi' -> fashion
    # page failure); the fuzzy name match must pick the exact-name page instead.
    round_id = _mk_round(engine, [])
    ext = FakeExtractor()
    ext.typeahead_results = {"Femmi": [
        {"page_id": "HUNTPFASH", "page_name": "Femmira Fashion",
         "page_like_count": 2019, "ig_username": None},
        {"page_id": "HUNTPFEMMI", "page_name": "Femmi",
         "page_like_count": 107, "ig_username": None}]}
    ext.listing_ads = {
        "HUNTPFEMMI": [_ios_ad("900050", page_id="HUNTPFEMMI")],
        "HUNTPFASH": [_ios_ad("900060", page_id="HUNTPFASH")]}
    progress = _Progress(engine, round_id, terms_total=0)
    _pull_competitor(engine, ext, round_id, "Femmi", progress, fresh_stats(),
                     threading.Lock(), listing_pages=2)
    entry = progress._state["terms"]["page:Femmi"]
    assert entry["stop"] == "listing_ok"
    assert entry["found"] == 1
    assert ext.listing_calls[0] == "HUNTPFEMMI"   # best name match tried first


def test_pull_competitor_falls_through_empty_pages(engine):
    round_id = _mk_round(engine, [])
    ext = FakeExtractor()
    ext.typeahead_results = {"Joggo": [
        {"page_id": "HUNTPJ1", "page_name": "Joggo",
         "page_like_count": 48310, "ig_username": None},
        {"page_id": "HUNTPJ2", "page_name": "Joggo Fan Club",
         "page_like_count": 10, "ig_username": None}]}
    ext.listing_ads = {"HUNTPJ1": [], "HUNTPJ2": []}
    progress = _Progress(engine, round_id, terms_total=0)
    _pull_competitor(engine, ext, round_id, "Joggo", progress, fresh_stats(),
                     threading.Lock(), listing_pages=2)
    entry = progress._state["terms"]["page:Joggo"]
    assert entry["stop"] == "no_results"
    assert entry["pages"] == 2                     # both candidates attempted


def test_pull_competitor_no_typeahead_match(engine):
    round_id = _mk_round(engine, [])
    ext = FakeExtractor(typeahead_results={}, listing_ads={})
    progress = _Progress(engine, round_id, terms_total=0)
    _pull_competitor(engine, ext, round_id, "Gazella", progress, fresh_stats(),
                     threading.Lock(), listing_pages=2)
    assert progress._state["terms"]["page:Gazella"]["stop"] == "no_match"


def test_run_hunt_round_pulls_up_to_ten_competitors(engine):
    # 12 named competitors -> 10 attempted (was 3). Empty typeahead: no pulls,
    # just records the resolution attempts in order.
    ext = FakeExtractor()
    round_id = _mk_round(engine, [], competitors=[f"brand{i}" for i in range(12)])
    run_hunt_round(engine, [ext], round_id)
    assert ext.typeahead_queries == [f"brand{i}" for i in range(10)]


def test_competitor_listing_uses_global_countries(engine):
    round_id = _mk_round(engine, [])
    ext = FakeExtractor()
    ext.typeahead_results = {"Movana": [
        {"page_id": "HUNTPMOV", "page_name": "Movana",
         "page_like_count": 17, "ig_username": None}]}
    ext.listing_ads = {"HUNTPMOV": []}
    _pull_competitor(engine, ext, round_id, "Movana",
                     _Progress(engine, round_id, terms_total=0), fresh_stats(),
                     threading.Lock(), listing_pages=2)
    assert ext.listing_countries[0] == HUNT_COMPETITOR_COUNTRIES


def test_name_similarity_ranks_exact_over_lookalike():
    assert _name_similarity("Femmi", "Femmi") == 1.0
    assert (_name_similarity("Femmi", "Femmi")
            > _name_similarity("Femmi", "Femmira Fashion"))
    assert _name_similarity("", "anything") == 0.0


def _seed_result(conn, round_id, term, store_id, *, triage, relevance):
    conn.execute(text(
        "INSERT INTO frontend.hunt_results "
        "  (round_id, page_id, page_name, store, store_id, source_term, "
        "   was_new, triage, relevance) "
        "VALUES (:r, :pid, 'App', 'ios', :sid, :term, true, :tri, :rel)"),
        {"r": round_id, "pid": f"HUNTP{store_id}", "sid": store_id,
         "term": term, "tri": triage, "rel": relevance})


@pytest.fixture
def round_id(engine):
    return _mk_round(engine, ["run coach"])


@pytest.fixture
def hunt_round_with_results(engine):
    rid = _mk_round(engine, ["run coach"], competitors=("joggo",))
    with begin(engine) as conn:
        _seed_result(conn, rid, "run coach", "910001",
                     triage="kept", relevance="relevant")
        _seed_result(conn, rid, "run coach", "910002",
                     triage="vetoed", relevance=None)
        _seed_result(conn, rid, "page:joggo", "910003",
                     triage="kept", relevance="off_target")
    return engine, rid


def test_term_outcome_stats_groups_by_source_term(hunt_round_with_results):
    engine, rid = hunt_round_with_results
    with begin(engine) as conn:
        out = term_outcome_stats(conn, rid)
    assert out["run coach"] == {"kept": 1, "relevant": 1, "off_target": 0}
    assert out["page:joggo"] == {"kept": 1, "relevant": 0, "off_target": 1}


def test_progress_merge_term_outcomes_preserves_sweep_telemetry(engine, round_id):
    p = _Progress(engine, round_id, terms_total=1)
    p.term_stats("run coach", pages=4, found=2, new=1, stop="dry")
    p.merge_term_outcomes({"run coach": {"kept": 1, "relevant": 1, "off_target": 0},
                           "page:joggo": {"kept": 1, "relevant": 0, "off_target": 1}})
    terms = p._state["terms"]
    assert terms["run coach"] == {"pages": 4, "found": 2, "new": 1, "stop": "dry",
                                  "kept": 1, "relevant": 1, "off_target": 0}
    assert terms["page:joggo"] == {"kept": 1, "relevant": 0, "off_target": 1}
