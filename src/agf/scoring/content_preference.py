"""Per-user similarity-to-likes ranker over content embeddings.

direction = normalize(mean(liked) - mean(disliked)); score(app) = cosine(app, direction)
(vectors are unit-norm, so cosine == dot). p_like is a Platt-style 1-D calibration
of the raw score against the labels. why names the nearest liked app. Pure — no DB.
"""
from __future__ import annotations

import numpy as np

MIN_LIKES = 8


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


class ContentPreferenceModel:
    def __init__(self, version, direction=None, calib=(1.0, 0.0),
                 liked_vecs=None, liked_names=None):
        self.version = version
        self._dir = direction
        self._a, self._b = calib
        self._liked_vecs = liked_vecs        # np.ndarray (k, dim) or None
        self._liked_names = liked_names or []

    @classmethod
    def fit(cls, liked, disliked, min_likes=MIN_LIKES):
        if len(liked) < min_likes:
            return cls("coldstart")
        L = np.array([v for _, _, v in liked], float)
        like_c = L.mean(axis=0)
        if disliked:
            dis_c = np.array([v for _, _, v in disliked], float).mean(axis=0)
        else:
            dis_c = np.zeros_like(like_c)
        direction = _l2(like_c - dis_c)
        labeled = [(v, 1) for _, _, v in liked] + [(v, 0) for _, _, v in disliked]
        raws = [float(np.dot(v, direction)) for v, _ in labeled]
        labels = [y for _, y in labeled]
        calib = _fit_platt(raws, labels)
        names = [n for _, n, _ in liked]
        return cls("content-sim-v1", direction, calib, L, names)

    def score(self, vec) -> float:
        if self.version == "coldstart":
            return 0.0
        return float(np.dot(np.asarray(vec, float), self._dir))

    def p_like(self, raw: float) -> float:
        return float(1.0 / (1.0 + np.exp(-(self._a * raw + self._b))))

    def why(self, vec) -> str:
        if self.version == "coldstart" or self._liked_vecs is None:
            return ""
        sims = self._liked_vecs @ np.asarray(vec, float)
        i = int(np.argmax(sims))
        return f"similar to {self._liked_names[i]}"
