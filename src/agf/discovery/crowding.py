"""Embedding-space crowding: mean cosine similarity to the k nearest corpus
neighbors. High crowding = the app sits in a clone cluster (measured 2026-07-10:
top-quartile crowding >=0.80 -> like-rate 2.8% vs 8-12%). Pure numpy, no DB."""
from __future__ import annotations

import numpy as np


def _normalize(m: np.ndarray) -> np.ndarray:
    m = np.asarray(m, dtype=np.float32)
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return m / n


def _topk_mean(sims: np.ndarray, k: int) -> np.ndarray:
    """Mean of the k largest values in each row of `sims`."""
    topk = np.sort(sims, axis=1)[:, -k:]
    return topk.mean(axis=1).astype(np.float32)


def crowding_scores(vectors: np.ndarray, corpus: np.ndarray, k: int = 10) -> np.ndarray:
    """Crowding of each row of `vectors` vs `corpus` (which must NOT contain the row
    itself). corpus with 0 rows -> all NaN. k capped at corpus size."""
    vectors = np.asarray(vectors, dtype=np.float32)
    n_v = vectors.shape[0]
    n_c = np.asarray(corpus).shape[0]
    if n_c == 0:
        return np.full(n_v, np.nan, dtype=np.float32)
    k = min(k, n_c)
    sims = _normalize(vectors) @ _normalize(corpus).T
    return _topk_mean(sims, k)


def crowding_scores_self(corpus: np.ndarray, k: int = 10) -> np.ndarray:
    """Leave-one-out: crowding of every corpus row vs the others (diagonal masked
    to -inf before top-k). corpus of 0/1 rows -> all NaN."""
    corpus = np.asarray(corpus, dtype=np.float32)
    n = corpus.shape[0]
    if n <= 1:
        return np.full(n, np.nan, dtype=np.float32)
    k = min(k, n - 1)
    unit = _normalize(corpus)
    sims = unit @ unit.T
    np.fill_diagonal(sims, -np.inf)
    return _topk_mean(sims, k)
