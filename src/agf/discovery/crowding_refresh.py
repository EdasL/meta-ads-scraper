"""DB glue for the per-cycle crowding recompute (kept out of the pure crowding
module). Reads a whole embedding corpus, computes leave-one-out crowding in numpy
OUTSIDE any write transaction, then writes it back in one batched UPDATE. A full
recompute every cycle IS the backfill — crowding drifts as the corpus grows, and
recompute is cheaper than staleness bookkeeping (3.4k x 3.4k x 768 ~ 2 s numpy).

Degrade open: NaN (empty/one-row corpus) -> NULL -> treated as not crowded."""
from __future__ import annotations

import os
from collections import Counter

import numpy as np
from sqlalchemy import text

from agf.discovery.crowding import crowding_scores_self

# Each table's key column and the SELECT of the corpus to score. Candidates are
# scored only while status='kept' (that is the set promotion/enrich read); app
# embeddings are all scored. dim > 0 skips any degenerate row.
_TABLES = {
    "frontend.app_embeddings": {
        "key": "advertiser_id",
        "select": ("SELECT advertiser_id, embedding FROM frontend.app_embeddings "
                   "WHERE dim > 0 ORDER BY advertiser_id"),
    },
    "discovery.candidate_embeddings": {
        "key": "candidate_id",
        "select": ("SELECT e.candidate_id, e.embedding "
                   "FROM discovery.candidate_embeddings e "
                   "JOIN discovery.candidates c ON c.id = e.candidate_id "
                   "WHERE c.status = 'kept' AND e.dim > 0 "
                   "ORDER BY e.candidate_id"),
    },
}


def crowding_k() -> int:
    try:
        return int(os.environ.get("AGF_CROWDING_K", "10"))
    except ValueError:
        return 10


def refresh_crowding(conn, *, table: str, k: int | None = None) -> int:
    """Recompute leave-one-out crowding for every row of `table`'s corpus and write
    it back. Returns the number of rows updated. Best-effort caller owns the commit."""
    cfg = _TABLES.get(table)
    if cfg is None:
        raise ValueError(f"refresh_crowding: unknown table {table!r}")
    if k is None:
        k = crowding_k()

    # Read the whole corpus, then compute in numpy before any write. A plain SELECT
    # takes no row locks (MVCC), so no write transaction is held over the bulk
    # compute — the only write is the batched UPDATE below.
    rows = conn.execute(text(cfg["select"])).all()
    if not rows:
        return 0

    keys = [r[0] for r in rows]
    vecs = [list(r[1]) for r in rows]

    # Guard against a ragged corpus (a stray wrong-dimension row would crash the
    # whole recompute): keep only the dominant embedding length.
    dim = Counter(len(v) for v in vecs).most_common(1)[0][0]
    kept = [(key, v) for key, v in zip(keys, vecs) if len(v) == dim]
    keys = [key for key, _ in kept]
    matrix = np.asarray([v for _, v in kept], dtype=np.float32)

    scores = crowding_scores_self(matrix, k=k)

    key_col = cfg["key"]
    params = [{"k": key, "c": (None if not np.isfinite(s) else float(s))}
              for key, s in zip(keys, scores)]
    conn.execute(
        text(f"UPDATE {table} SET crowding = :c WHERE {key_col} = :k"), params)
    return len(params)
