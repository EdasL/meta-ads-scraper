from agf.scoring.gemscore import (
    clamp01, weighted_axis, scaling_velocity, under_radar, funnel_intent,
    SV_WEIGHTS, UR_WEIGHTS, FI_WEIGHTS,
)


def test_clamp01_bounds():
    assert clamp01(-1) == 0.0 and clamp01(2) == 1.0 and clamp01(0.4) == 0.4


def test_weighted_axis_basic():
    # equal weights, two present components
    assert weighted_axis({"a": 1.0, "b": 0.0}, {"a": 0.5, "b": 0.5}) == 0.5


def test_weighted_axis_missing_component_renormalizes():
    # b is None -> weight collapses onto a -> returns a's value
    assert weighted_axis({"a": 0.8, "b": None}, {"a": 0.5, "b": 0.5}) == 0.8


def test_weighted_axis_all_missing_is_none():
    assert weighted_axis({"a": None, "b": None}, {"a": 0.5, "b": 0.5}) is None


def test_under_radar_famous_is_near_zero():
    # famous brand: every "under radar" component ~0 (huge rating/followers/reach, old)
    ur = under_radar({"rating": 0.0, "followers": 0.0, "reach": 0.0, "newness": 0.0})
    assert ur == 0.0


def test_under_radar_gem_is_high():
    ur = under_radar({"rating": 1.0, "followers": 1.0, "reach": 1.0, "newness": 1.0})
    assert ur == 1.0


def test_scaling_velocity_combines_with_weights():
    sv = scaling_velocity({"wow": 1.0, "ramp": 1.0, "accel": 1.0, "ewma": 1.0,
                           "longevity": 1.0, "iteration": 1.0})
    assert sv == 1.0
    assert set(SV_WEIGHTS) == {"wow", "ramp", "accel", "ewma", "longevity", "iteration"}
    assert abs(sum(SV_WEIGHTS.values()) - 1.0) < 1e-9


def test_funnel_intent_weights_present():
    fi = funnel_intent({"fingerprint": 1.0, "cta": 0.0, "monetize": 0.0})
    assert fi == FI_WEIGHTS["fingerprint"]
