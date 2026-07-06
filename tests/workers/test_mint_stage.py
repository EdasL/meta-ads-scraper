"""Mint stage: autocomplete candidates flow through the redundancy gate + budget into
trials with an autocomplete: source tag; a near-duplicate of a retired basin is dropped."""
from sqlalchemy import text
from agf.discovery.frame_mint import mint_keywords_from_releases
from agf.enrich import AppMeta
import agf.discovery.keyword_redundancy as kr


def _mk_meta(track_id, name, genre, desc):
    return AppMeta(track_id=track_id, name=name, seller="s", genre=genre,
                   release_date="2026-07-01", rating_count=0, avg_rating=0.0,
                   description=desc)


def _clean(eng):
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.dynamic_keywords "
                          "WHERE source LIKE 'autocomplete:%' OR keyword LIKE 'MINTT%'"))


def test_autocomplete_source_admits_novel_and_tags_source(_migrated_engine, monkeypatch):
    eng = _migrated_engine
    _clean(eng)
    try:
        with eng.begin() as conn:
            conn.execute(text(
                "INSERT INTO discovery.dynamic_keywords (keyword, source, kind, status, dry_streak)"
                " VALUES ('MINTT retired','gem_copy','app','retired',0)"))
        # fake embed: 'MINTT twin' == retired basin; 'MINTT novel' orthogonal
        vecs = {"MINTT retired": [1.0, 0.0], "MINTT twin": [1.0, 0.0], "MINTT novel": [0.0, 1.0]}
        monkeypatch.setattr(kr, "embed_texts", lambda texts, **kw: [vecs[t] for t in texts])
        # fake autocomplete gather: one category, two candidate phrases
        monkeypatch.setattr(
            "agf.discovery.frame_mint.gather_autocomplete_candidates",
            lambda client, **kw: {"Health & Fitness": ["MINTT twin", "MINTT novel"]})

        stats = mint_keywords_from_releases(
            eng, client=None, floor=0, total_slots=40,
            fetch=lambda *a, **k: [], lookup=lambda *a, **k: {},  # no new-release path
            autocomplete=True)
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT keyword FROM discovery.dynamic_keywords "
                "WHERE source='autocomplete:Health & Fitness'")).all()
        kws = {r.keyword for r in rows}
        assert "MINTT novel" in kws          # novel admitted
        assert "MINTT twin" not in kws       # redundant dropped
        assert stats.get("autocomplete_by_category", {}).get("Health & Fitness", 0) >= 1
    finally:
        _clean(eng)


def test_brainstorm_source_admits_and_tags(_migrated_engine, monkeypatch):
    eng = _migrated_engine
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.dynamic_keywords WHERE source LIKE 'brainstorm:%'"))
    try:
        monkeypatch.setattr(kr, "embed_texts",
                            lambda texts, **kw: [[0.0, 1.0] for _ in texts])  # all novel
        # seed listings so brainstorm has grounding; fake the proposer
        monkeypatch.setattr(
            "agf.discovery.frame_mint.brainstorm_niche_keywords",
            lambda category, listings, n, **kw: ["MINTT brainstormed app"][:n])
        stats = mint_keywords_from_releases(
            eng, client=None, floor=0, total_slots=40,
            fetch=lambda *a, **k: [{"store_id": "MINTTB", "name": "S", "genre": "Lifestyle"}],
            lookup=lambda *a, **k: {"MINTTB": _mk_meta("MINTTB", "S", "Social Networking", "d")},
            autocomplete=False, brainstorm=True)
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT keyword FROM discovery.dynamic_keywords WHERE source LIKE 'brainstorm:%'")).all()
        assert any("brainstormed" in r.keyword for r in rows)
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.dynamic_keywords WHERE source LIKE 'brainstorm:%'"))
