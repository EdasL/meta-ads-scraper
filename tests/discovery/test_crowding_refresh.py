"""DB glue for the per-cycle crowding recompute (crowding_refresh.refresh_crowding).

refresh_crowding takes the ENGINE and commits its UPDATE in its own txn (it must NOT
hold a transaction over the ~2 s numpy compute — the read connection is closed before
the compute, the write is a separate short txn). That means the pg_conn rollback
fixture can't isolate these tests: the writes persist. So every fixture seeds sentinel
ids in a high, collision-proof range on a committed connection and deletes them in a
try/finally teardown, per the shared-DB convention.

Assertions are relative (clone cluster ranks above the loner) plus a generous absolute
bar, so they hold whether or not the DB already carries real embedding rows: a mutually
near-identical clone cluster dominates any real neighbor, an orthogonal loner matches
neither.
"""
import numpy as np
import pytest
from sqlalchemy import text

from agf.discovery.crowding_refresh import refresh_crowding

DIM = 768
ADV_BASE = 9_100_000_000          # sentinel advertiser ids (app_embeddings)
CAND_BASE = 9_100_000_000         # sentinel candidate ids (candidates + embeddings)
N_CLONES = 12


def _unit(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(DIM).astype(np.float32)
    return v / np.linalg.norm(v)


def _clone(base: np.ndarray, seed: int) -> list[float]:
    """A near-identical twin of `base`: tiny perturbation, re-normalized."""
    rng = np.random.default_rng(seed)
    v = base + 1e-3 * rng.standard_normal(DIM).astype(np.float32)
    return (v / np.linalg.norm(v)).tolist()


def _loner(seed: int) -> list[float]:
    return _unit(seed).tolist()


def _delete_sentinels(engine):
    """Remove every sentinel row this module inserts. Idempotent — also runs before a
    test in case a prior crashed run left rows behind (writes are committed now)."""
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM frontend.app_embeddings "
                          "WHERE advertiser_id >= :b"), {"b": ADV_BASE})
        conn.execute(text("DELETE FROM discovery.candidate_embeddings "
                          "WHERE candidate_id >= :b"), {"b": CAND_BASE})
        conn.execute(text("DELETE FROM discovery.candidates WHERE id >= :b"),
                     {"b": CAND_BASE})


@pytest.fixture
def engine(_migrated_engine):
    """The migrated test engine with sentinel rows cleaned before and after each test."""
    _delete_sentinels(_migrated_engine)
    try:
        yield _migrated_engine
    finally:
        _delete_sentinels(_migrated_engine)


def _seed_app_embeddings(engine):
    base = _unit(1)
    ins = text("INSERT INTO frontend.app_embeddings "
               "(advertiser_id, embedding, content_hash, dim, model) "
               "VALUES (:a, :e, 'sentinel', :d, 'test')")
    clone_ids = [ADV_BASE + i for i in range(N_CLONES)]
    loner_id = ADV_BASE + 900
    with engine.begin() as conn:
        for i in clone_ids:
            conn.execute(ins, {"a": i, "e": _clone(base, 100 + i), "d": DIM})
        conn.execute(ins, {"a": loner_id, "e": _loner(999), "d": DIM})
    return clone_ids, loner_id


def _crowding(engine, table, key_col, ids):
    """Read back crowding on a FRESH connection — proves refresh_crowding committed its
    UPDATE independently (the seeding txn is long closed)."""
    with engine.connect() as conn:
        rows = conn.execute(text(
            f"SELECT {key_col}, crowding FROM {table} WHERE {key_col} = ANY(:ids)"),
            {"ids": list(ids)}).all()
    return {k: c for k, c in rows}


def test_app_embeddings_recompute(engine):
    clone_ids, loner_id = _seed_app_embeddings(engine)

    n = refresh_crowding(engine, table="frontend.app_embeddings")
    assert n >= N_CLONES + 1

    got = _crowding(engine, "frontend.app_embeddings", "advertiser_id",
                    clone_ids + [loner_id])
    clone_scores = [got[i] for i in clone_ids]
    assert all(c is not None for c in clone_scores)
    assert min(clone_scores) >= 0.95            # a clone cluster is maximally crowded
    assert got[loner_id] is not None
    assert got[loner_id] < 0.80                 # orthogonal loner is not crowded
    assert got[loner_id] < min(clone_scores)


def test_app_embeddings_idempotent(engine):
    clone_ids, loner_id = _seed_app_embeddings(engine)
    ids = clone_ids + [loner_id]

    refresh_crowding(engine, table="frontend.app_embeddings")
    first = _crowding(engine, "frontend.app_embeddings", "advertiser_id", ids)
    refresh_crowding(engine, table="frontend.app_embeddings")
    second = _crowding(engine, "frontend.app_embeddings", "advertiser_id", ids)

    for i in ids:
        assert second[i] == pytest.approx(first[i], abs=1e-5)


def test_refresh_commits_from_fresh_connection(engine):
    """Regression for the txn-scope fix: refresh_crowding must READ, compute with no txn
    open, then COMMIT its UPDATE in a separate short txn. We seed, refresh, then read on
    a brand-new connection — a non-zero committed crowding proves the UPDATE landed
    without any caller-held transaction to commit it (the old conn-taking shape relied
    on the caller committing)."""
    clone_ids, loner_id = _seed_app_embeddings(engine)

    refresh_crowding(engine, table="frontend.app_embeddings")

    # Fully independent connection: sees the write only if refresh_crowding committed.
    with engine.connect() as conn:
        vals = conn.execute(text(
            "SELECT crowding FROM frontend.app_embeddings "
            "WHERE advertiser_id = ANY(:ids)"),
            {"ids": clone_ids + [loner_id]}).scalars().all()
    assert len(vals) == N_CLONES + 1
    assert all(v is not None for v in vals)


def _seed_candidates(engine):
    base = _unit(2)
    cand = text("INSERT INTO discovery.candidates "
                "(id, page_id, page_name, store, store_id, status, source_keyword) "
                "VALUES (:id, :pg, :nm, 'ios', :sid, :st, 'sentinel-kw')")
    emb = text("INSERT INTO discovery.candidate_embeddings "
               "(candidate_id, embedding, content_hash, dim, model, crowding) "
               "VALUES (:id, :e, 'sentinel', :d, 'test', :cr)")
    clone_ids = [CAND_BASE + i for i in range(N_CLONES)]
    loner_id = CAND_BASE + 900
    dropped_id = CAND_BASE + 901
    with engine.begin() as conn:
        for i in clone_ids:
            conn.execute(cand, {"id": i, "pg": f"pg{i}", "nm": f"clone{i}",
                                "sid": f"s{i}", "st": "kept"})
            conn.execute(emb, {"id": i, "e": _clone(base, 200 + i), "d": DIM, "cr": None})
        conn.execute(cand, {"id": loner_id, "pg": f"pg{loner_id}", "nm": "loner",
                            "sid": f"s{loner_id}", "st": "kept"})
        conn.execute(emb, {"id": loner_id, "e": _loner(777), "d": DIM, "cr": None})
        # a NON-kept candidate with a pre-set crowding: outside the recompute set.
        conn.execute(cand, {"id": dropped_id, "pg": f"pg{dropped_id}", "nm": "dropped",
                            "sid": f"s{dropped_id}", "st": "dropped"})
        conn.execute(emb, {"id": dropped_id, "e": _clone(base, 300), "d": DIM, "cr": -1.0})
    return clone_ids, loner_id, dropped_id


def test_candidate_embeddings_recompute(engine):
    clone_ids, loner_id, dropped_id = _seed_candidates(engine)

    refresh_crowding(engine, table="discovery.candidate_embeddings")

    got = _crowding(engine, "discovery.candidate_embeddings", "candidate_id",
                    clone_ids + [loner_id, dropped_id])
    clone_scores = [got[i] for i in clone_ids]
    assert min(clone_scores) >= 0.95
    assert got[loner_id] < 0.80
    assert got[loner_id] < min(clone_scores)
    # the dropped (non-kept) candidate is outside the join → untouched.
    assert got[dropped_id] == pytest.approx(-1.0, abs=1e-6)
