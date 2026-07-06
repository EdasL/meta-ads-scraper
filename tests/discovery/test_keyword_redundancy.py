"""Redundancy gate: scoring (pure, fake embed) and novelty admission (pure)."""
from sqlalchemy import text

from agf.discovery.keyword_redundancy import (
    admit_by_novelty,
    exhausted_basin_embeddings,
    redundancy_scores,
)


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
    calls = {"n": 0}

    def embed(texts, **kw):
        calls["n"] += len(texts)
        return [[1.0, 0.0] for _ in texts]

    vecs = exhausted_basin_embeddings(pg_conn, dry_floor=5, embed=embed)
    assert len(vecs) == 2                      # retired + dry-live, not fresh-live
    assert calls["n"] == 2                     # both embedded on first call
    # second call reads from cache — no new embeds
    vecs2 = exhausted_basin_embeddings(pg_conn, dry_floor=5, embed=embed)
    assert len(vecs2) == 2 and calls["n"] == 2
