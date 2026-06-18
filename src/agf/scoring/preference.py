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
