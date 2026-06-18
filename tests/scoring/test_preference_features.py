import math

from agf.scoring.preference import FEATURES, collapse_target, feature_vector


def test_collapse_target_like_vs_rest():
    assert collapse_target("like") == 1
    assert collapse_target("neutral") == 0
    assert collapse_target("dislike") == 0


def test_feature_vector_order_and_has_funnel_coercion():
    f = {
        "has_funnel": True, "whitespace": 0.8, "funnel_intent": 0.7,
        "scaling_velocity": 0.6, "under_radar": 0.5, "demand": 0.4,
    }
    v = feature_vector(f)
    assert v == [1.0, 0.8, 0.7, 0.6, 0.5, 0.4]
    assert FEATURES[0] == "has_funnel"


def test_feature_vector_missing_continuous_is_nan():
    v = feature_vector({"has_funnel": False})
    assert v[0] == 0.0
    assert all(math.isnan(x) for x in v[1:])
