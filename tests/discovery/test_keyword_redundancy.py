"""Redundancy gate: scoring (pure, fake embed) and novelty admission (pure)."""
from sqlalchemy import text

from agf.discovery.keyword_redundancy import (
    admit_by_novelty,
    drop_token_supersets,
    exhausted_basin_embeddings,
    load_basin_embeddings,
    redundancy_scores,
)


def test_drop_token_supersets_drops_strict_superset():
    # candidate = existing head-phrase + an extra modifier word -> dropped
    existing = ["gratitude journal app"]
    cands = ["daily gratitude journal app", "gratitude journal app"]
    # the over-specified one goes; the exact head-phrase stays (exact dup handled elsewhere)
    assert drop_token_supersets(cands, existing) == ["gratitude journal app"]


def test_drop_token_supersets_keeps_niche_word_superset():
    # A candidate whose EXTRA token beyond an existing generic is a real niche word (not a
    # noise modifier) is a genuine niche discriminator and MUST be kept — dropping it would
    # collapse breadth. 'student focus app' ⊃ 'focus app' (extra={student}); 'route weather
    # forecast app' ⊃ 'weather forecast app' (extra={route}) — both kept.
    existing = ["focus app", "weather forecast app"]
    cands = ["student focus app", "route weather forecast app"]
    assert drop_token_supersets(cands, existing) == cands
    # but stacking ONLY a noise modifier onto the same generic IS dropped
    assert drop_token_supersets(["daily focus app", "best focus app"], ["focus app"]) == []


def test_drop_token_supersets_keeps_subset_and_disjoint():
    existing = ["daily gratitude journal app"]
    # subset (broader) and disjoint candidates are both KEPT
    cands = ["gratitude journal app", "sleep tracker app"]
    assert drop_token_supersets(cands, existing) == cands


def test_drop_token_supersets_exact_set_match_kept():
    # equal token-set (not a STRICT superset) is not dropped by this gate
    assert drop_token_supersets(["meal timing app"], ["meal timing app"]) == ["meal timing app"]


def test_drop_token_supersets_app_suffix_and_case_normalized():
    # trailing 'app' stripped both sides + case-insensitive:
    # {daily,gratitude,journal} strictly ⊇ {gratitude,journal} -> drop
    assert drop_token_supersets(["Daily Gratitude Journal"], ["gratitude journal app"]) == []
    # candidate that only adds 'app' is NOT a real extra modifier -> kept
    assert drop_token_supersets(["gratitude journal app"], ["gratitude journal"]) \
        == ["gratitude journal app"]


def test_drop_token_supersets_empty_existing_is_passthrough():
    cands = ["anything app", "another one app"]
    assert drop_token_supersets(cands, []) == cands


def _fake_embed(mapping):
    # returns an embed() stub that yields the mapped unit vector per text, in order
    def embed(texts, **kw):
        return [mapping[t] for t in texts]
    return embed


def test_redundancy_scores_max_over_basins():
    # 2-d unit vectors; cosine = dot product (already normalized).
    basins = [[1.0, 0.0], [0.0, 1.0]]
    embed = _fake_embed({"north": [0.0, 1.0], "diag": [0.6, 0.8]})
    scores = redundancy_scores(["north", "diag"], basins, embed=embed)
    assert scores["north"] == 1.0                    # identical to basin 2
    assert abs(scores["diag"] - 0.8) < 1e-9          # max(0.6, 0.8)


def test_redundancy_scores_empty_basin_is_zero():
    embed = _fake_embed({"anything": [1.0, 0.0]})
    assert redundancy_scores(["anything"], [], embed=embed) == {"anything": 0.0}


def test_admit_by_novelty_drops_redundant_and_orders_novel_first():
    scored = {"stale": 0.95, "fresh": 0.10, "mid": 0.50}
    out = admit_by_novelty(scored, threshold=0.90, limit=10)
    assert out == ["fresh", "mid"]                   # stale dropped, ascending score


def test_admit_by_novelty_respects_limit():
    scored = {"a": 0.1, "b": 0.2, "c": 0.3}
    assert admit_by_novelty(scored, threshold=0.9, limit=2) == ["a", "b"]


def test_admit_by_novelty_threshold_is_inclusive_drop():
    assert admit_by_novelty({"x": 0.90}, threshold=0.90, limit=5) == []


def test_exhausted_basins_select_retired_and_dry_and_cache(pg_conn):
    pg_conn.execute(text(
        "INSERT INTO discovery.dynamic_keywords (id, keyword, source, kind, status, dry_streak)"
        " VALUES "
        "(90001,'dead one','gem_copy','app','retired',0),"
        "(90002,'dry live','gem_copy','app','active',5),"
        "(90003,'fresh live','gem_copy','app','active',0)"))  # excluded: live, low dry
    embedded = []                              # records every text embed() was asked for

    def embed(texts, **kw):
        embedded.extend(texts)
        return [[1.0, 0.0] for _ in texts]

    # Scope assertions to our sentinel rows (90001-90003): exhausted_basin_embeddings
    # reads retired/high-dry keywords DB-WIDE, so other tests' committed basin rows may
    # be present. A DB-wide count is fragile; assert the sentinels' behavior instead.
    exhausted_basin_embeddings(pg_conn, dry_floor=5, embed=embed)
    assert "dead one" in embedded              # retired -> exhausted basin, embedded
    assert "dry live" in embedded              # live but dry_streak>=floor, embedded
    assert "fresh live" not in embedded        # live, low dry -> excluded, never embedded
    # second call reads our sentinels from cache — neither is embedded again
    before = len(embedded)
    exhausted_basin_embeddings(pg_conn, dry_floor=5, embed=embed)
    new_texts = embedded[before:]
    assert "dead one" not in new_texts
    assert "dry live" not in new_texts
    # cached rows carry a real content hash, not the '' placeholder
    hashes = pg_conn.execute(text(
        "SELECT e.content_hash FROM discovery.dynamic_keyword_embeddings e "
        "WHERE e.keyword_id IN (90001, 90002)")).scalars().all()
    assert hashes and all(h for h in hashes)


def _clean_lbasin(eng):
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.dynamic_keywords "
                          "WHERE keyword LIKE 'LBASIN%'"))  # embeddings cascade


def test_load_basin_embeddings_lazy_limit_bounds_embedding(_migrated_engine):
    """load_basin_embeddings does the network embed OUTSIDE any transaction and never
    embeds more than lazy_limit uncached basin keywords per call (the rest catch up on
    later runs / the pre-warm). lazy_limit=0 -> pure cache read, no embed at all."""
    eng = _migrated_engine
    _clean_lbasin(eng)
    try:
        with eng.begin() as conn:
            conn.execute(text(
                "INSERT INTO discovery.dynamic_keywords (keyword, source, kind, status, dry_streak)"
                " VALUES ('LBASIN one','gem_copy','app','retired',0),"
                "('LBASIN two','gem_copy','app','retired',0)"))
        embedded = []

        def embed(texts, **kw):
            embedded.extend(texts)
            return [[1.0, 0.0] for _ in texts]

        load_basin_embeddings(eng, dry_floor=5, embed=embed, lazy_limit=0)
        assert not any(t.startswith("LBASIN") for t in embedded)  # bound respected
        with eng.connect() as conn:
            cached = conn.execute(text(
                "SELECT count(*) FROM discovery.dynamic_keyword_embeddings e "
                "JOIN discovery.dynamic_keywords k ON k.id = e.keyword_id "
                "WHERE k.keyword LIKE 'LBASIN%'")).scalar()
        assert cached == 0

        load_basin_embeddings(eng, dry_floor=5, embed=embed, lazy_limit=10_000)
        assert {"LBASIN one", "LBASIN two"} <= set(embedded)
        with eng.connect() as conn:
            hashes = conn.execute(text(
                "SELECT e.content_hash FROM discovery.dynamic_keyword_embeddings e "
                "JOIN discovery.dynamic_keywords k ON k.id = e.keyword_id "
                "WHERE k.keyword LIKE 'LBASIN%'")).scalars().all()
        assert len(hashes) == 2 and all(h for h in hashes)  # cached, real hash

        before = len(embedded)
        load_basin_embeddings(eng, dry_floor=5, embed=embed, lazy_limit=10_000)
        assert not any(t.startswith("LBASIN") for t in embedded[before:])  # cache hit
    finally:
        _clean_lbasin(eng)
