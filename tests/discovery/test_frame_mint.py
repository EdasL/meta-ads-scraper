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
            {"store_id": "FMT12", "name": "FarApart", "genre": "Lifestyle"},
            {"store_id": "FMT20", "name": "BlastZone", "genre": "Games"},  # out of scope
        ]
        metas = {
            "FMT10": _meta("FMT10", "LovePing", "Social Networking",
                           "long distance couple app for partners apart"),
            "FMT11": _meta("FMT11", "MissYou", "Social Networking",
                           "long distance couple reminders for partners"),
            "FMT12": _meta("FMT12", "FarApart", "Social Networking",
                           "long distance couple countdown for partners"),
            "FMT20": _meta("FMT20", "BlastZone", "Games", "shoot aliens"),
        }

        def fake_fetch(client, *, country, genre, limit):
            return list(seeds)

        def fake_lookup(client, ids, **kw):
            return {i: metas[i] for i in ids if i in metas}

        stats = mint_keywords_from_releases(
            eng, client=None, K=15, floor=0, total_slots=40, min_advertisers=2,
            fetch=fake_fetch, lookup=fake_lookup)

        assert stats["seeds"] == 4
        assert stats["in_scope"] == 3          # Games dropped
        assert stats["looked_up"] == 3
        assert stats["minted"] >= 1
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT keyword FROM discovery.dynamic_keywords "
                "WHERE source = 'new_release:Social' AND kind='app'")).all()
        kws = {r.keyword for r in rows}
        assert any("long distance couple" in k for k in kws)
    finally:
        _clean_minted(eng)


def test_mint_from_releases_lookup_error_is_isolated(_migrated_engine):
    eng = _migrated_engine
    _clean_minted(eng)
    try:
        def fake_fetch(client, *, country, genre, limit):
            return [{"store_id": "FMTERR", "name": "X", "genre": "Lifestyle"}]

        def boom_lookup(client, ids, **kw):
            raise RuntimeError("apple down")

        stats = mint_keywords_from_releases(
            eng, client=None, floor=0, fetch=fake_fetch, lookup=boom_lookup)
        assert stats["errors"] >= 1
        assert stats["minted"] == 0   # no crash, stats returned
    finally:
        _clean_minted(eng)
