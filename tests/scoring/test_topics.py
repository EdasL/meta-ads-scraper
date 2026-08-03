"""Topic clustering: pure k-means + the DB refresh (spec 2026-08-03).

refresh_topics takes the ENGINE and commits its swap in its own txn (crowding_refresh
pattern), so the pg_conn rollback fixture cannot isolate it. Sentinel advertisers are
seeded on a committed connection under a collision-proof page_id prefix and deleted in
a try/finally teardown, per the shared-DB convention in
tests/discovery/test_crowding_refresh.py.

Sentinels are REAL advertiser rows: frontend.app_topics.advertiser_id is an int4 FK to
public.advertisers(id). Their ids are written EXPLICITLY in a high int4-safe range —
the advertisers serial sequence trails the explicit ids other suites insert, so
RETURNING id handed back an id whose app_embeddings row already existed.
"""
import numpy as np
import pytest
from sqlalchemy import text

from agf.scoring.topics import kmeans_assign, refresh_topics

DIM = 8
ADV_BASE = 2_000_000_000          # sentinel advertiser ids, collision-proof and < int4 max
PAGE_PREFIX = "sentinel-topics-"


def _blob(center, n, rng, dim=DIM):
    v = np.tile(np.asarray(center, dtype=np.float32), (n, 1))
    v += rng.normal(0, 0.01, size=(n, dim)).astype(np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def _delete_sentinels(engine):
    """Remove every sentinel row this module inserts. Idempotent — also runs before a
    test in case a prior crashed run left rows behind (writes are committed)."""
    with engine.begin() as conn:
        ids = conn.execute(text("SELECT id FROM public.advertisers "
                                "WHERE page_id LIKE :p"),
                           {"p": PAGE_PREFIX + "%"}).scalars().all()
        if ids:
            conn.execute(text("DELETE FROM frontend.app_embeddings "
                              "WHERE advertiser_id = ANY(:ids)"), {"ids": list(ids)})
            # app_topics rows go with the advertiser (ON DELETE CASCADE).
            conn.execute(text("DELETE FROM public.advertisers WHERE id = ANY(:ids)"),
                         {"ids": list(ids)})


@pytest.fixture
def engine(_migrated_engine):
    """The migrated test engine with sentinel rows cleaned before and after each test."""
    _delete_sentinels(_migrated_engine)
    try:
        yield _migrated_engine
    finally:
        _delete_sentinels(_migrated_engine)


@pytest.fixture
def seeded_advertisers(engine):
    """Four sentinel advertisers with app_embeddings: two near [1,0,...], two near
    [0,1,...] — two obvious blobs for k=2. Returns their (serial) advertiser ids."""
    rng = np.random.default_rng(11)
    vecs = list(_blob([1, 0, 0, 0, 0, 0, 0, 0], 2, rng)) + \
        list(_blob([0, 1, 0, 0, 0, 0, 0, 0], 2, rng))
    ins_adv = text("INSERT INTO public.advertisers (id, page_id, page_name) "
                   "VALUES (:id, :pg, :nm)")
    ins_emb = text("INSERT INTO frontend.app_embeddings "
                   "(advertiser_id, embedding, content_hash, dim, model) "
                   "VALUES (:a, :e, 'sentinel', :d, 'test')")
    ids = [ADV_BASE + i for i in range(len(vecs))]
    with engine.begin() as conn:
        for adv_id, v in zip(ids, vecs):
            conn.execute(ins_adv, {"id": adv_id, "pg": f"{PAGE_PREFIX}{adv_id}",
                                   "nm": f"sentinel topic app {adv_id}"})
            conn.execute(ins_emb, {"a": adv_id, "e": [float(x) for x in v], "d": DIM})
    return ids


def test_kmeans_separates_two_obvious_blobs():
    rng = np.random.default_rng(7)
    a = _blob([1, 0, 0, 0, 0, 0, 0, 0], 20, rng)
    b = _blob([0, 1, 0, 0, 0, 0, 0, 0], 20, rng)
    labels = kmeans_assign(np.vstack([a, b]), k=2)
    assert len(set(labels[:20])) == 1
    assert len(set(labels[20:])) == 1
    assert labels[0] != labels[20]


def test_kmeans_k_capped_at_rows():
    rng = np.random.default_rng(7)
    labels = kmeans_assign(_blob([1, 0, 0, 0, 0, 0, 0, 0], 3, rng), k=40)
    assert len(labels) == 3


def test_refresh_topics_writes_rows_and_sizes(engine, seeded_advertisers):
    ids = seeded_advertisers
    out = refresh_topics(engine, k=2, only_ids=ids)
    assert out == {"rows": 4, "k": 2}
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT advertiser_id, topic_id, topic_size FROM frontend.app_topics "
            "WHERE advertiser_id = ANY(:ids) ORDER BY advertiser_id"),
            {"ids": ids}).all()
    assert len(rows) == 4
    by_topic = {}
    for r in rows:
        by_topic.setdefault(r.topic_id, []).append(r)
    assert sorted(len(v) for v in by_topic.values()) == [2, 2]
    assert all(r.topic_size == len(by_topic[r.topic_id]) for r in rows)


def test_refresh_topics_empty_corpus_noop(engine):
    assert refresh_topics(engine, only_ids=[-1]) == {"rows": 0, "k": 0}


def test_refresh_topics_skips_orphan_embeddings(engine, seeded_advertisers):
    """frontend.app_embeddings has no FK to public.advertisers (bigint key, sentinel
    rows live at 9.1e9), but app_topics does. An orphan embedding must be skipped, not
    abort the whole refresh with an FK violation / int4 overflow."""
    orphan = 9_100_000_000
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO frontend.app_embeddings "
                          "(advertiser_id, embedding, content_hash, dim, model) "
                          "VALUES (:a, :e, 'sentinel', :d, 'test')"),
                     {"a": orphan, "e": [1.0] + [0.0] * (DIM - 1), "d": DIM})
    try:
        out = refresh_topics(engine, k=2, only_ids=seeded_advertisers + [orphan])
        assert out["rows"] == 4
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM frontend.app_embeddings "
                              "WHERE advertiser_id = :a"), {"a": orphan})
