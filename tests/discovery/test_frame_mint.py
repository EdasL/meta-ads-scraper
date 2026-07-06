"""Keyword-minting external frame: the category-floor backbone (ensure_category_frame,
rolled-back pg_conn) and the new-release mint stage (mint_keywords_from_releases, real
engine + sentinel cleanup since it commits). Fake fetch/lookup — zero network."""
from sqlalchemy import text

from agf.discovery.coverage import CategoryCoverage
from agf.discovery.frame_mint import (ensure_category_frame,
                                      mint_keywords_from_releases)
from agf.enrich import AppMeta


def test_ensure_category_frame_seeds_under_floor_only(pg_conn):
    # floor counts static taxonomy (~9 terms/category). floor=20: Dating (0 live + 9
    # static = 9) is under; Health & Fitness (1000 live + 9 = 1009) clears it.
    coverage = {
        "Dating": CategoryCoverage("Dating", 0, 0),
        "Health & Fitness": CategoryCoverage("Health & Fitness", 40, 1000),
    }
    added = ensure_category_frame(pg_conn, coverage, floor=20)
    assert added > 0
    dating = pg_conn.execute(text(
        "SELECT count(*) FROM discovery.dynamic_keywords "
        "WHERE source = 'frame:Dating' AND kind='app'")).scalar()
    hf = pg_conn.execute(text(
        "SELECT count(*) FROM discovery.dynamic_keywords "
        "WHERE source = 'frame:Health & Fitness'")).scalar()
    assert dating > 0      # under-floor category seeded
    assert hf == 0         # at-floor category untouched
    assert ensure_category_frame(pg_conn, coverage, floor=20) == 0  # idempotent


def test_ensure_category_frame_noop_at_default_floor(pg_conn):
    # In steady state (default floor=5) the static taxonomy already clears every
    # category, so the frame-seeder adds nothing — no redundant re-seeding.
    coverage = {"Dating": CategoryCoverage("Dating", 0, 0),
                "Beauty": CategoryCoverage("Beauty", 0, 0)}
    assert ensure_category_frame(pg_conn, coverage, floor=5) == 0


def _meta(track_id, name, genre, desc):
    return AppMeta(track_id=track_id, name=name, seller="s", genre=genre,
                   release_date="2026-07-01", rating_count=0, avg_rating=0.0,
                   description=desc)


def _clean_minted(engine):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM discovery.dynamic_keywords "
                          "WHERE source LIKE 'new_release:%' OR source LIKE 'frame:%'"))


def test_mint_from_releases_adds_biased_trials(_migrated_engine):
    eng = _migrated_engine
    _clean_minted(eng)
    try:
        seeds = [
            {"store_id": "FMT10", "name": "LovePing", "genre": "Lifestyle"},
            {"store_id": "FMT11", "name": "MissYou", "genre": "Lifestyle"},
            {"store_id": "FMT20", "name": "BlastZone", "genre": "Games"},  # out of scope
        ]
        metas = {
            "FMT10": _meta("FMT10", "LovePing", "Social Networking",
                           "long distance couple app for partners apart"),
            "FMT11": _meta("FMT11", "MissYou", "Social Networking",
                           "reminders for partners apart"),
            "FMT20": _meta("FMT20", "BlastZone", "Games", "shoot aliens"),
        }

        def fake_fetch(client, *, country, genre, limit):
            return list(seeds)

        def fake_lookup(client, ids, **kw):
            return {i: metas[i] for i in ids if i in metas}

        def fake_propose(category, listings, n):
            # taste-blind LLM stub: proposes crisp niche keywords for the category
            assert category == "Social" and listings  # only in-scope Social apps reach here
            return ["long distance relationship app", "couple countdown app"][:n]

        stats = mint_keywords_from_releases(
            eng, client=None, K=15, floor=0, total_slots=40, autocomplete=False, brainstorm=False,
            fetch=fake_fetch, lookup=fake_lookup, propose=fake_propose)

        assert stats["seeds"] == 3
        assert stats["in_scope"] == 2          # Games dropped
        assert stats["looked_up"] == 2
        assert stats["minted"] >= 1
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT keyword FROM discovery.dynamic_keywords "
                "WHERE source = 'new_release:Social' AND kind='app'")).all()
        kws = {r.keyword for r in rows}
        assert "long distance relationship app" in kws
    finally:
        _clean_minted(eng)


def test_mint_from_releases_funds_fertile_covered_category(_migrated_engine):
    # The inverted-bias fix: a category ALREADY over the K coverage target but with
    # recent keeper yield must still receive mint slots via the exploit lane. Under the
    # old deficit-only rule this run minted nothing.
    eng = _migrated_engine
    _clean_minted(eng)

    def _clean_sentinels():
        with eng.begin() as conn:
            conn.execute(text(
                "DELETE FROM discovery.candidates WHERE store_id LIKE 'FMTY%'"))
            conn.execute(text("DELETE FROM public.apps WHERE store_id LIKE 'FMTY%'"))

    _clean_sentinels()
    try:
        with eng.begin() as conn:
            conn.execute(text(
                "INSERT INTO public.apps (store, store_id, name, genre) VALUES "
                "('ios','FMTY_A','A','Social Networking'),"
                "('ios','FMTY_B','B','Social Networking')"))
            conn.execute(text(
                "INSERT INTO discovery.candidates (page_id, store, store_id,"
                " source_keyword, technique, status, first_seen_at) VALUES "
                "('FMTY_p1','ios','FMTY_A','k','niche_term','kept', now()),"
                "('FMTY_p2','ios','FMTY_B','k','niche_term','kept', now())"))

        def fake_fetch(client, *, country, genre, limit):
            return [{"store_id": "FMTY_C", "name": "PingPal", "genre": "Lifestyle"}]

        def fake_lookup(client, ids, **kw):
            return {"FMTY_C": _meta("FMTY_C", "PingPal", "Social Networking",
                                    "voice notes for close friends")}

        def fake_propose(category, listings, n):
            assert category == "Social"
            return ["voice note app", "close friends app"][:n]

        # K=1: Social has found=2 >= K -> deficit 0. Yield must fund it anyway.
        stats = mint_keywords_from_releases(
            eng, client=None, K=1, floor=0, total_slots=10, autocomplete=False, brainstorm=False,
            fetch=fake_fetch, lookup=fake_lookup, propose=fake_propose)
        assert stats["minted"] >= 1
        assert stats["minted_by_category"].get("Social", 0) >= 1
    finally:
        _clean_minted(eng)
        _clean_sentinels()


def test_gate_knobs_read_flywheel_env(monkeypatch):
    # The mint gate reads the SAME env knobs as the flywheel gate so a tuned
    # threshold / dry floor never desyncs the two basin definitions.
    import agf.discovery.frame_mint as fm
    from agf.db.dynamic_keywords import retire_dry_streak
    monkeypatch.setenv("AGF_KEYWORD_REDUNDANCY_THRESHOLD", "0.5")
    monkeypatch.setenv("AGF_KEYWORD_BASIN_DRY_FLOOR", "7")
    assert fm._gate_threshold() == 0.5
    assert fm._gate_dry_floor() == 7
    monkeypatch.delenv("AGF_KEYWORD_REDUNDANCY_THRESHOLD")
    monkeypatch.delenv("AGF_KEYWORD_BASIN_DRY_FLOOR")
    assert fm._gate_threshold() == 0.90
    assert fm._gate_dry_floor() == retire_dry_streak()


def test_mint_stats_preinit_pass_buckets(_migrated_engine):
    # autocomplete/brainstorm buckets always present (review minor), even when off.
    stats = mint_keywords_from_releases(
        _migrated_engine, client=None, floor=0, autocomplete=False, brainstorm=False,
        fetch=lambda *a, **k: [], lookup=lambda *a, **k: {})
    assert stats["autocomplete_by_category"] == {}
    assert stats["brainstorm_by_category"] == {}


def test_mint_from_releases_lookup_error_is_isolated(_migrated_engine):
    eng = _migrated_engine
    _clean_minted(eng)
    try:
        def fake_fetch(client, *, country, genre, limit):
            return [{"store_id": "FMTERR", "name": "X", "genre": "Lifestyle"}]

        def boom_lookup(client, ids, **kw):
            raise RuntimeError("apple down")

        stats = mint_keywords_from_releases(
            eng, client=None, floor=0, autocomplete=False, brainstorm=False,
            fetch=fake_fetch, lookup=boom_lookup)
        assert stats["errors"] >= 1
        assert stats["minted"] == 0   # no crash, stats returned
    finally:
        _clean_minted(eng)


def test_mint_from_releases_propose_error_is_isolated(_migrated_engine):
    from agf.discovery.release_keywords import ProposeError
    eng = _migrated_engine
    _clean_minted(eng)
    try:
        def fake_fetch(client, *, country, genre, limit):
            return [{"store_id": "FMTPE", "name": "X", "genre": "Lifestyle"}]

        def fake_lookup(client, ids, **kw):
            return {"FMTPE": _meta("FMTPE", "X", "Social Networking", "desc")}

        def boom_propose(category, listings, n):
            raise ProposeError("gemini down")

        stats = mint_keywords_from_releases(
            eng, client=None, floor=0, autocomplete=False, brainstorm=False,
            fetch=fake_fetch, lookup=fake_lookup, propose=boom_propose)
        assert stats["errors"] >= 1
        assert stats["minted"] == 0   # Gemini failure isolated, no crash
    finally:
        _clean_minted(eng)
