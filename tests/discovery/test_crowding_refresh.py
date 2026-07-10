"""DB glue for the per-cycle crowding recompute (crowding_refresh.refresh_crowding).

Shared test DB: every fixture is scoped to sentinel ids in a high, collision-proof
range and the pg_conn fixture rolls the whole thing back. Assertions are relative
(clone cluster ranks above the loner) plus a generous absolute bar, so they hold
whether or not the DB already carries real embedding rows: a mutually near-identical
clone cluster dominates any real neighbor, an orthogonal loner matches neither.
"""
import numpy as np
import pytest
from sqlalchemy import text

from agf.discovery.crowding_refresh import refresh_crowding

pytestmark = pytest.mark.usefixtures("pg_conn")

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


def _seed_app_embeddings(conn):
    base = _unit(1)
    ins = text("INSERT INTO frontend.app_embeddings "
               "(advertiser_id, embedding, content_hash, dim, model) "
               "VALUES (:a, :e, 'sentinel', :d, 'test')")
    clone_ids = [ADV_BASE + i for i in range(N_CLONES)]
    for i in clone_ids:
        conn.execute(ins, {"a": i, "e": _clone(base, 100 + i), "d": DIM})
    loner_id = ADV_BASE + 900
    conn.execute(ins, {"a": loner_id, "e": _loner(999), "d": DIM})
    return clone_ids, loner_id


def _crowding(conn, table, key_col, ids):
    rows = conn.execute(text(
        f"SELECT {key_col}, crowding FROM {table} WHERE {key_col} = ANY(:ids)"),
        {"ids": list(ids)}).all()
    return {k: c for k, c in rows}


def test_app_embeddings_recompute(pg_conn):
    clone_ids, loner_id = _seed_app_embeddings(pg_conn)

    n = refresh_crowding(pg_conn, table="frontend.app_embeddings")
    assert n >= N_CLONES + 1

    got = _crowding(pg_conn, "frontend.app_embeddings", "advertiser_id",
                    clone_ids + [loner_id])
    clone_scores = [got[i] for i in clone_ids]
    assert all(c is not None for c in clone_scores)
    assert min(clone_scores) >= 0.95            # a clone cluster is maximally crowded
    assert got[loner_id] is not None
    assert got[loner_id] < 0.80                 # orthogonal loner is not crowded
    assert got[loner_id] < min(clone_scores)


def test_app_embeddings_idempotent(pg_conn):
    clone_ids, loner_id = _seed_app_embeddings(pg_conn)
    ids = clone_ids + [loner_id]

    refresh_crowding(pg_conn, table="frontend.app_embeddings")
    first = _crowding(pg_conn, "frontend.app_embeddings", "advertiser_id", ids)
    refresh_crowding(pg_conn, table="frontend.app_embeddings")
    second = _crowding(pg_conn, "frontend.app_embeddings", "advertiser_id", ids)

    for i in ids:
        assert second[i] == pytest.approx(first[i], abs=1e-5)


def _seed_candidates(conn):
    base = _unit(2)
    cand = text("INSERT INTO discovery.candidates "
                "(id, page_id, page_name, store, store_id, status, source_keyword) "
                "VALUES (:id, :pg, :nm, 'ios', :sid, :st, 'sentinel-kw')")
    emb = text("INSERT INTO discovery.candidate_embeddings "
               "(candidate_id, embedding, content_hash, dim, model, crowding) "
               "VALUES (:id, :e, 'sentinel', :d, 'test', :cr)")
    clone_ids = [CAND_BASE + i for i in range(N_CLONES)]
    for i in clone_ids:
        conn.execute(cand, {"id": i, "pg": f"pg{i}", "nm": f"clone{i}",
                            "sid": f"s{i}", "st": "kept"})
        conn.execute(emb, {"id": i, "e": _clone(base, 200 + i), "d": DIM, "cr": None})
    loner_id = CAND_BASE + 900
    conn.execute(cand, {"id": loner_id, "pg": f"pg{loner_id}", "nm": "loner",
                        "sid": f"s{loner_id}", "st": "kept"})
    conn.execute(emb, {"id": loner_id, "e": _loner(777), "d": DIM, "cr": None})
    # a NON-kept candidate with a pre-set crowding: outside the recompute set.
    dropped_id = CAND_BASE + 901
    conn.execute(cand, {"id": dropped_id, "pg": f"pg{dropped_id}", "nm": "dropped",
                        "sid": f"s{dropped_id}", "st": "dropped"})
    conn.execute(emb, {"id": dropped_id, "e": _clone(base, 300), "d": DIM, "cr": -1.0})
    return clone_ids, loner_id, dropped_id


def test_candidate_embeddings_recompute(pg_conn):
    clone_ids, loner_id, dropped_id = _seed_candidates(pg_conn)

    refresh_crowding(pg_conn, table="discovery.candidate_embeddings")

    got = _crowding(pg_conn, "discovery.candidate_embeddings", "candidate_id",
                    clone_ids + [loner_id, dropped_id])
    clone_scores = [got[i] for i in clone_ids]
    assert min(clone_scores) >= 0.95
    assert got[loner_id] < 0.80
    assert got[loner_id] < min(clone_scores)
    # the dropped (non-kept) candidate is outside the join → untouched.
    assert got[dropped_id] == pytest.approx(-1.0, abs=1e-6)
