import math


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def gem_score_poc(active_count: int, longevity_days: int, rating_count: int,
                  is_emerging: bool) -> float:
    """Simplified PoC GemScore: multiplicative AND-gate of scaling x under-radar x emerging.

    Mirrors the design's core property — a famous brand (huge rating base)
    drives under_radar -> 0, collapsing the score. Returns 0..100.
    Full cohort z-scores, acceleration, FunnelIntent and vetoes are Plan 5.
    """
    if not is_emerging:
        return 0.0
    # scaling: more active creatives + longer longevity = higher (saturating)
    scaling = _clamp01(math.log1p(active_count) / math.log1p(50)) * _clamp01(longevity_days / 60)
    # under-radar: small rating base = high; log-scaled so 300 ~ high, 5M ~ 0
    under_radar = _clamp01(1 - math.log1p(rating_count) / math.log1p(1_000_000))
    score = 100 * (scaling ** 0.6) * (under_radar ** 0.4)
    return round(score, 1)
