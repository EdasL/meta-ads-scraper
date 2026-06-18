"""Content-based per-user preference model (statsmodels Logit).

Pure model logic — no DB. scripts/score_preferences.py does the DB I/O and
calls into here. The fitted coefficients also double as a GemScore-reweight
proposal (funnel/whitespace are the user's strongest signals but GemScore
weights them ~0).
"""
from __future__ import annotations

import math

# Feature order is fixed; everything downstream relies on it.
FEATURES = ["has_funnel", "whitespace", "funnel_intent",
            "scaling_velocity", "under_radar", "demand"]
CONTINUOUS = FEATURES[1:]  # has_funnel is binary, the rest are [0,1] axis scores

# Friendly labels for the "why shown" string.
WHY_LABELS = {
    "has_funnel": "has funnel",
    "whitespace": "low saturation",
    "funnel_intent": "strong funnel signal",
    "scaling_velocity": "scaling fast",
    "under_radar": "under the radar",
    "demand": "growing demand",
}


def collapse_target(rating: str) -> int:
    """Like vs rest: like=1, neutral/dislike=0."""
    return 1 if rating == "like" else 0


def feature_vector(features: dict) -> list[float]:
    """Values in FEATURES order. has_funnel → 0/1; missing continuous → NaN."""
    out: list[float] = []
    for name in FEATURES:
        v = features.get(name)
        if name == "has_funnel":
            out.append(1.0 if v else 0.0)
        elif v is None:
            out.append(float("nan"))
        else:
            out.append(float(v))
    return out


import numpy as np

MIN_LABELS = 30
L1_ALPHA = 1.0


def uncertainty(p: float) -> float:
    """Distance from the decision boundary, peaking at p=0.5."""
    return 1.0 - abs(2.0 * p - 1.0)


def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


class PreferenceModel:
    def __init__(self, version, coefficients, means, stds, intercept):
        self.version = version
        self.coefficients = coefficients          # feature -> coef (logit only)
        self._means = means                       # per CONTINUOUS feature
        self._stds = stds
        self._intercept = intercept

    @classmethod
    def fit(cls, rows):
        ys = [collapse_target(r) for r, _ in rows]
        n, n_pos = len(ys), sum(ys)
        if n < MIN_LABELS or n_pos == 0 or n_pos == n:
            return cls._heuristic()
        X_raw = np.array([feature_vector(f) for _, f in rows], dtype=float)
        # Column means over CONTINUOUS (index 1..) ignoring NaN, for impute+standardize.
        cont = X_raw[:, 1:]
        means = np.nanmean(cont, axis=0)
        means = np.where(np.isnan(means), 0.0, means)
        stds = np.nanstd(cont, axis=0)
        stds = np.where(stds < 1e-9, 1.0, stds)
        Xs = cls._transform(X_raw, means, stds)
        try:
            import types
            from statsmodels.discrete.discrete_model import Logit as _Logit
            from statsmodels.tools import add_constant as _add_constant
            Xc = _add_constant(Xs, has_constant="add")
            alpha = np.array([0.0] + [L1_ALPHA] * len(FEATURES))
            model = _Logit(np.array(ys), Xc)
            # Patch cov_params_func_l1 to use pinv instead of inv — avoids
            # LinAlgError on perfectly-separable / small datasets (numpy 2+).
            def _safe_cov(self, lm, xopt, retvals):
                H = lm.hessian(xopt)
                trimmed = retvals["trimmed"]
                nz = np.nonzero(~trimmed)[0]
                cov = np.nan * np.ones(H.shape)
                if nz.size:
                    cov[nz[:, None], nz] = np.linalg.pinv(-H[nz[:, None], nz])
                return cov
            model.cov_params_func_l1 = types.MethodType(_safe_cov, model)
            res = model.fit_regularized(method="l1", alpha=alpha, disp=0)
            params = np.asarray(res.params, dtype=float)
        except Exception:
            return cls._heuristic()
        intercept = float(params[0])
        coefs = {name: float(params[i + 1]) for i, name in enumerate(FEATURES)}
        return cls("logit-v1", coefs,
                   {f: float(means[i]) for i, f in enumerate(CONTINUOUS)},
                   {f: float(stds[i]) for i, f in enumerate(CONTINUOUS)},
                   intercept)

    @classmethod
    def _heuristic(cls):
        return cls("heuristic-v1", {}, {}, {}, 0.0)

    @staticmethod
    def _transform(X_raw, means, stds):
        X = X_raw.copy()
        cont = X[:, 1:]
        inds = np.where(np.isnan(cont))
        cont[inds] = np.take(means, inds[1])      # impute with column mean
        X[:, 1:] = (cont - means) / stds          # standardize
        return X

    def predict(self, features: dict) -> float:
        if self.version == "heuristic-v1":
            hf = 1.0 if features.get("has_funnel") else 0.0
            ws = features.get("whitespace")
            ws = 0.0 if ws is None else float(ws)
            return _clamp01(0.6 * hf + 0.4 * ws)
        v = feature_vector(features)
        z = self._intercept + self.coefficients["has_funnel"] * v[0]
        for i, name in enumerate(CONTINUOUS):
            x = v[i + 1]
            if math.isnan(x):
                x = self._means[name]
            xs = (x - self._means[name]) / self._stds[name]
            z += self.coefficients[name] * xs
        return 1.0 / (1.0 + math.exp(-z))
