"""Per-user similarity-to-likes ranker over content embeddings (v2, multi-centroid).

Likes are clustered (deterministic k-means, no RNG) and each cluster gets its own
direction = normalize(mean(cluster) - dis_c); score(app) = max over cluster
directions of dot(app, dir). dis_c is the weighted mean of disliked plus
AGF_NEUTRAL_WEIGHT x neutral vectors (neutrals shape the negative centroid but are
excluded from Platt calibration). A structure guard collapses the split back to
the single global direction when any two cluster directions are more similar than
AGF_TASTE_SPLIT_MAX_COS. `direction` stays the GLOBAL single
like-minus-dislike unit vector for consumers that need one axis (triage prior).
The Go port (backend/internal/reco) is pinned to this file by golden vectors —
keep the arithmetic order identical.
"""
from __future__ import annotations

import os

import numpy as np

MIN_LIKES = 8


def _env_float(key, default):
    try:
        return float(os.environ.get(key) or default)
    except ValueError:
        return default


def _env_int(key, default):
    try:
        return int(os.environ.get(key) or default)
    except ValueError:
        return default


def _l2(v):
    n = np.linalg.norm(v)
    return v if n < 1e-12 else v / n


def _fit_platt(raws, labels, iters=500, lr=0.5):
    """Tiny gradient-descent logistic of label ~ a*raw + b. Returns (a, b).
    Bounded iterations, no external optimizer (avoids statsmodels fragility)."""
    x = np.asarray(raws, float)
    y = np.asarray(labels, float)
    a, b = 1.0, 0.0
    n = max(len(x), 1)
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-(a * x + b)))
        ga = np.dot(p - y, x) / n
        gb = np.sum(p - y) / n
        a -= lr * ga
        b -= lr * gb
    if not np.isfinite(a) or not np.isfinite(b):
        return 1.0, 0.0
    return float(a), float(b)


def _cluster(vecs, k, iters=50):
    """Deterministic k-means over unit vectors. Farthest-point init from index 0
    (next center = the point with the smallest max-similarity to chosen centers,
    first-min wins); Lloyd's assignment by highest dot, first-max wins; centers
    are l2-normalized member means; empty clusters keep their old center. No RNG.
    Returns the per-vector cluster index list. The Go port must match exactly."""
    n = len(vecs)
    k = min(k, n)
    if k <= 1 or n == 0:
        return [0] * n
    V = np.asarray(vecs, float)
    chosen = [0]
    while len(chosen) < k:
        best_i, best_sim = -1, np.inf
        for i in range(n):
            if i in chosen:
                continue
            sim = max(float(V[i] @ V[c]) for c in chosen)
            if sim < best_sim:
                best_sim, best_i = sim, i
        chosen.append(best_i)
    centers = [V[c].copy() for c in chosen]
    assign = None
    for _ in range(iters):
        sims = np.stack([V @ c for c in centers], axis=1)
        new_assign = [int(a) for a in np.argmax(sims, axis=1)]
        if new_assign == assign:
            break
        assign = new_assign
        for j in range(len(centers)):
            idx = [i for i, a in enumerate(assign) if a == j]
            if idx:
                centers[j] = _l2(V[idx].mean(axis=0))
    return assign


class ContentPreferenceModel:
    def __init__(self, version, direction=None, calib=(1.0, 0.0),
                 liked_vecs=None, liked_names=None, dirs=None):
        self.version = version
        self._dir = direction
        self._a, self._b = calib
        self._liked_vecs = liked_vecs        # np.ndarray (k, dim) or None
        self._liked_names = liked_names or []
        self._dirs = dirs                    # np.ndarray (n_clusters, dim) or None

    @property
    def direction(self):
        """The fitted GLOBAL like−dislike unit vector, or None in cold-start."""
        return self._dir

    @classmethod
    def fit(cls, liked, disliked, neutral=None, min_likes=MIN_LIKES,
            neutral_weight=None, likes_per_centroid=None, max_centroids=None,
            split_max_cos=None):
        if len(liked) < min_likes:
            return cls("coldstart")
        neutral = neutral or []
        w = (_env_float("AGF_NEUTRAL_WEIGHT", 0.25)
             if neutral_weight is None else neutral_weight)
        per_c = (_env_int("AGF_TASTE_LIKES_PER_CENTROID", 12)
                 if likes_per_centroid is None else likes_per_centroid)
        max_c = (_env_int("AGF_TASTE_CENTROIDS_MAX", 4)
                 if max_centroids is None else max_centroids)

        liked = sorted(liked, key=lambda t: t[0])   # id order: parity with Go
        L = np.array([v for _, _, v in liked], float)
        dim = L.shape[1]

        denom = len(disliked) + w * len(neutral)
        dis_c = np.zeros(dim)
        if denom > 0:
            s = np.zeros(dim)
            for _, _, v in disliked:
                s = s + np.asarray(v, float)
            for _, _, v in neutral:
                s = s + w * np.asarray(v, float)
            dis_c = s / denom

        direction = _l2(L.mean(axis=0) - dis_c)

        k = max(1, min(max_c, len(liked) // max(per_c, 1)))
        assign = _cluster(list(L), k)
        dirs = []
        for j in sorted(set(assign)):
            idx = [i for i, a in enumerate(assign) if a == j]
            dirs.append(_l2(L[idx].mean(axis=0) - dis_c))
        # Structure guard: if any two cluster directions nearly coincide, the
        # split is fitting noise (measured 2026-08-04: founder's likes are one
        # blob, best-k centroid pair at cosine 0.92) — fall back to the single
        # global direction until real structure emerges.
        split_max = (_env_float("AGF_TASTE_SPLIT_MAX_COS", 0.85)
                     if split_max_cos is None else split_max_cos)
        if len(dirs) > 1:
            for i in range(len(dirs)):
                for j in range(i + 1, len(dirs)):
                    if float(np.dot(dirs[i], dirs[j])) > split_max:
                        dirs = [direction]
                        break
                if len(dirs) == 1:
                    break
        D = np.stack(dirs)

        labeled = [(v, 1) for _, _, v in liked] + [(v, 0) for _, _, v in disliked]
        raws = [float(np.max(D @ np.asarray(v, float))) for v, _ in labeled]
        labels = [y for _, y in labeled]
        calib = _fit_platt(raws, labels)
        names = [n for _, n, _ in liked]
        return cls("content-sim-v2", direction, calib, L, names, dirs=D)

    def score(self, vec) -> float:
        if self.version == "coldstart":
            return 0.0
        v = np.asarray(vec, float)
        if self._dirs is not None:
            return float(np.max(self._dirs @ v))
        return float(np.dot(v, self._dir))

    def p_like(self, raw: float) -> float:
        return float(1.0 / (1.0 + np.exp(-(self._a * raw + self._b))))

    def why(self, vec) -> str:
        if self.version == "coldstart" or self._liked_vecs is None:
            return ""
        sims = self._liked_vecs @ np.asarray(vec, float)
        i = int(np.argmax(sims))
        return f"similar to {self._liked_names[i]}"
