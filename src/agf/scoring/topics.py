"""Embedding-cluster topics: k-means over frontend.app_embeddings, stored in
frontend.app_topics (spec 2026-08-03). The topic is the DIVERSITY key — it replaces
discovery-keyword "niches" for the whitespace axis and the review-queue cap, so 294
near-identical AI photo editors share ONE topic no matter which keyword found them.
Pure-numpy k-means++ (no sklearn in the venv); embeddings are L2-normalized so
euclidean k-means ~ cosine. Assignments are recomputed whole-corpus each cycle —
consumers use per-run percentiles / per-request cap keys, so run-to-run label drift
is harmless. Follows crowding_refresh.py's engine-not-conn / no-txn-over-compute
pattern and its only_ids test-isolation convention."""
from __future__ import annotations

import os
from collections import Counter

import numpy as np
from sqlalchemy import text


def topic_k() -> int:
    try:
        return int(os.environ.get("AGF_TOPIC_K", "40"))
    except ValueError:
        return 40


def _sq_dists(m: np.ndarray, c: np.ndarray) -> np.ndarray:
    """Squared euclidean distance, rows x centers, via the gram expansion. The naive
    broadcast (m[:, None, :] - c[None, :, :]) would materialize n*k*dim floats — 3.7 GB
    at the real corpus size (30k rows, k=40, dim=768) on every iteration."""
    return (np.square(m).sum(1)[:, None] - 2.0 * (m @ c.T)
            + np.square(c).sum(1)[None, :])


def kmeans_assign(matrix: np.ndarray, k: int, seed: int = 0) -> np.ndarray:
    """k-means++ init + Lloyd iterations, returns int labels. k is capped at
    n_rows; input rows should be unit-norm (cosine = euclidean up to scale)."""
    m = np.asarray(matrix, dtype=np.float32)
    n = m.shape[0]
    k = max(1, min(k, n))
    rng = np.random.default_rng(seed)

    # k-means++ seeding: next center drawn with probability proportional to D^2, where
    # D is the distance to the nearest center chosen so far. d2 is carried forward and
    # min-folded per new center (recomputing it against all centers each round is the
    # same answer at k times the cost). float64 + clip: the gram expansion can return
    # tiny negatives, and rng.choice rejects a float32 p-vector that misses 1.0.
    first = int(rng.integers(n))
    centers = [m[first]]
    d2 = np.maximum(_sq_dists(m, m[first][None, :]).ravel().astype(np.float64), 0.0)
    for _ in range(1, k):
        total = float(d2.sum())
        if total <= 0:                       # every point sits on a center already
            nxt = int(rng.integers(n))
        else:
            nxt = int(rng.choice(n, p=d2 / total))
        centers.append(m[nxt])
        d2 = np.minimum(
            d2,
            np.maximum(_sq_dists(m, m[nxt][None, :]).ravel().astype(np.float64), 0.0))

    c = np.asarray(centers, dtype=np.float32)
    labels = np.zeros(n, dtype=np.int64)
    for it in range(50):
        new_labels = _sq_dists(m, c).argmin(axis=1)
        if it > 0 and np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for j in range(k):
            members = m[labels == j]
            if len(members):
                c[j] = members.mean(axis=0)
    return labels


def refresh_topics(engine, *, k: int | None = None, only_ids=None) -> dict:
    """Recompute every advertiser's topic and swap frontend.app_topics in one txn.
    Engine (not conn): corpus read on a short-lived connection, numpy compute holds
    no txn, one write txn at the end (crowding_refresh pattern). only_ids scopes
    both the corpus and the delete — the shared-test-DB isolation convention.

    The corpus JOINs public.advertisers because app_topics.advertiser_id is an int4 FK
    to it while app_embeddings.advertiser_id is an unconstrained bigint: an orphan or
    out-of-int4-range embedding row would otherwise abort the whole refresh."""
    if k is None:
        k = topic_k()
    sql = ("SELECT e.advertiser_id, e.embedding FROM frontend.app_embeddings e "
           "JOIN public.advertisers a ON a.id = e.advertiser_id "
           "WHERE e.dim > 0")
    params = {}
    if only_ids is not None:
        sql += " AND e.advertiser_id = ANY(:ids)"
        params = {"ids": list(only_ids)}
    sql += " ORDER BY e.advertiser_id"
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).all()
    if not rows:
        return {"rows": 0, "k": 0}

    keys = [r[0] for r in rows]
    vecs = [list(r[1]) for r in rows]
    # Ragged corpus guard (crowding_refresh): keep only the dominant embedding length.
    dim = Counter(len(v) for v in vecs).most_common(1)[0][0]
    kept = [(key, v) for key, v in zip(keys, vecs) if len(v) == dim]
    keys = [key for key, _ in kept]
    matrix = np.asarray([v for _, v in kept], dtype=np.float32)

    labels = kmeans_assign(matrix, k=k)
    sizes = Counter(int(x) for x in labels)
    payload = [{"a": int(key), "t": int(x), "s": sizes[int(x)]}
               for key, x in zip(keys, labels)]
    with engine.begin() as conn:
        if only_ids is not None:
            conn.execute(text(
                "DELETE FROM frontend.app_topics WHERE advertiser_id = ANY(:ids)"),
                {"ids": list(only_ids)})
        else:
            conn.execute(text("DELETE FROM frontend.app_topics"))
        conn.execute(text(
            "INSERT INTO frontend.app_topics (advertiser_id, topic_id, topic_size) "
            "VALUES (:a, :t, :s)"), payload)
    return {"rows": len(payload), "k": int(len(sizes))}
