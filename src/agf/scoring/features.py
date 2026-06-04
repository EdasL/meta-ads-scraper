"""Derivative velocity features (master-plan §6 P5-T4/T5).
Primitives operate on an ordered weekly series (oldest -> newest)."""
import math

_EPS = 1.0  # log smoothing so a 0 -> N week doesn't blow up


def log_growth_wow(series) -> float:
    """Latest week-over-week log growth. Flat or <2 points -> 0.0."""
    s = list(series)
    if len(s) < 2:
        return 0.0
    return math.log((s[-1] + _EPS) / (s[-2] + _EPS))


def acceleration(series) -> float:
    """Change in WoW log-growth (2nd derivative). Convex>0, concave<0, flat/<3 ->0."""
    s = list(series)
    if len(s) < 3:
        return 0.0
    g_prev = math.log((s[-2] + _EPS) / (s[-3] + _EPS))
    g_last = math.log((s[-1] + _EPS) / (s[-2] + _EPS))
    return g_last - g_prev


def ewma(series, alpha: float = 0.5) -> float:
    """Exponentially weighted moving average (latest value). Empty -> 0.0."""
    s = list(series)
    if not s:
        return 0.0
    acc = float(s[0])
    for x in s[1:]:
        acc = alpha * float(x) + (1 - alpha) * acc
    return acc
