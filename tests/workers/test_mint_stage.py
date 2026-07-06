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


def test_mint_all_io_happens_before_the_write_txn(_migrated_engine, monkeypatch):
    """Regression for the live-verify wedge (2026-07-06): every network / LLM /
    subprocess step — fetch, lookup, propose, autocomplete gather, brainstorm,
    embedding — must run BEFORE the write transaction that inserts keywords, never
    inside it. Probe: record event order; all inserts must come last."""
    import agf.discovery.frame_mint as fm
    import agf.discovery.keyword_redundancy as kr
    eng = _migrated_engine
    events = []

    def _clean():
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.dynamic_keywords "
                              "WHERE keyword LIKE 'MINTIO%'"))

    _clean()
    try:
        def fake_embed(texts, **kw):
            events.append("embed")
            return [[0.0, 1.0] for _ in texts]

        monkeypatch.setattr(kr, "embed_texts", fake_embed)

        def fake_gather(client, **kw):
            events.append("gather")
            return {"Health & Fitness": ["MINTIO widget app"]}

        monkeypatch.setattr(fm, "gather_autocomplete_candidates", fake_gather)

        def fake_brainstorm(category, listings, n, **kw):
            events.append("brainstorm")
            return ["MINTIO brainstorm app"][:n]

        monkeypatch.setattr(fm, "brainstorm_niche_keywords", fake_brainstorm)

        real_add = fm.add_trial_keywords

        def spy_add(conn, keywords, **kw):
            events.append("insert")
            return real_add(conn, keywords, **kw)

        monkeypatch.setattr(fm, "add_trial_keywords", spy_add)

        def fake_fetch(client, *, country, genre, limit):
            events.append("fetch")
            return [{"store_id": "MINTIO1", "name": "S", "genre": "Lifestyle"}]

        def fake_lookup(client, ids, **kw):
            events.append("lookup")
            return {"MINTIO1": _mk_meta("MINTIO1", "S", "Social Networking", "d")}

        def fake_propose(category, listings, n):
            events.append("propose")
            return ["MINTIO release app"][:n]

        fm.mint_keywords_from_releases(
            eng, client=None, floor=0, total_slots=40,
            fetch=fake_fetch, lookup=fake_lookup, propose=fake_propose,
            autocomplete=True, brainstorm=True)

        assert "insert" in events
        first_insert = events.index("insert")
        done_before = set(events[:first_insert])
        assert {"fetch", "lookup", "propose", "gather", "brainstorm",
                "embed"} <= done_before
        assert all(e == "insert" for e in events[first_insert:])  # writes come last
    finally:
        _clean()


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
