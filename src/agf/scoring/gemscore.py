"""GemScore axes + multiplicative AND-gate (master-plan §2, §6 P5-T6/T7).

gem_score = 100 · clamp01(SV)^.5 · clamp01(UR)^.35 · clamp01(FI)^.15

Each axis is a weighted sum of cohort-normalized components in [0,1]; missing
components re-normalize the remaining weights. A fully-missing (sparse) axis is
imputed to `floor` in gem_score so a data gap does not kill an otherwise-strong
gem — but a *present, genuinely low* axis (famous brand -> UR≈0) still gates it."""

# axis component weights (sum to 1 within each axis)
# `ramp` (overall multi-week growth) is the dominant scaling signal: it credits a sustained
# 30->80 / 0->40 climb even after it plateaus, where `wow` (last week only) would read flat.
SV_WEIGHTS = {"wow": 0.20, "ramp": 0.35, "accel": 0.15, "ewma": 0.15, "longevity": 0.15}
UR_WEIGHTS = {"rating": 0.40, "followers": 0.20, "reach": 0.20, "newness": 0.20}
FI_WEIGHTS = {"fingerprint": 0.50, "cta": 0.30, "monetize": 0.20}


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def weighted_axis(components: dict, weights: dict):
    """Weighted sum of present (non-None) components, weights re-normalized over
    the present subset. Returns None if every component is missing."""
    present = {k: v for k, v in components.items() if v is not None}
    total_w = sum(weights[k] for k in present)
    if total_w == 0:
        return None
    return sum(weights[k] * clamp01(present[k]) for k in present) / total_w


def scaling_velocity(components: dict):
    return weighted_axis(components, SV_WEIGHTS)


def under_radar(components: dict):
    return weighted_axis(components, UR_WEIGHTS)


def funnel_intent(components: dict):
    return weighted_axis(components, FI_WEIGHTS)


def gem_score(sv, ur, fi, floor: float = 0.5) -> float:
    """Multiplicative AND-gate: 100 · clamp01(SV)^.5 · clamp01(UR)^.35 · clamp01(FI)^.15.

    A None axis means NO DATA (e.g. a quiz funnel has no app rating / followers / EU reach,
    so under-radar is uncomputable). It is imputed to the cohort-NEUTRAL `floor` (0.5) so a
    data gap neither rewards nor punishes — the score is driven by the axes we DO have. A
    *present* low axis is clamped as-is: a famous brand whose UR genuinely computes to ~0
    still collapses the product to ~0 (the 'still unknown' hard gate). The earlier 0.05 floor
    wrongly punished data-less funnels (0.05^.35≈0.35) — exactly the emerging gems we want."""
    a = floor if sv is None else clamp01(sv)
    b = floor if ur is None else clamp01(ur)
    c = floor if fi is None else clamp01(fi)
    return 100.0 * (a ** 0.5) * (b ** 0.35) * (c ** 0.15)
