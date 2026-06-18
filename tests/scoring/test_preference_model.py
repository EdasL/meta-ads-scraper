import math

from agf.scoring.preference import PreferenceModel, uncertainty


def _synthetic(n_like=40, n_dis=40):
    """Funnel + high whitespace → like; no funnel + low whitespace → dislike."""
    rows = []
    for _ in range(n_like):
        rows.append(("like", {"has_funnel": True, "whitespace": 0.9,
                               "funnel_intent": 0.8, "scaling_velocity": 0.6,
                               "under_radar": 0.6, "demand": 0.5}))
    for _ in range(n_dis):
        rows.append(("dislike", {"has_funnel": False, "whitespace": 0.1,
                                 "funnel_intent": 0.5, "scaling_velocity": 0.5,
                                 "under_radar": 0.5, "demand": 0.5}))
    return rows


def test_uncertainty_peaks_at_half():
    assert uncertainty(0.5) == 1.0
    assert uncertainty(1.0) == 0.0
    assert uncertainty(0.0) == 0.0
    assert abs(uncertainty(0.75) - 0.5) < 1e-9


def test_logit_learns_funnel_preference():
    m = PreferenceModel.fit(_synthetic())
    assert m.version == "logit-v1"
    p_funnel = m.predict({"has_funnel": True, "whitespace": 0.9,
                          "funnel_intent": 0.8, "scaling_velocity": 0.6,
                          "under_radar": 0.6, "demand": 0.5})
    p_none = m.predict({"has_funnel": False, "whitespace": 0.1,
                        "funnel_intent": 0.5, "scaling_velocity": 0.5,
                        "under_radar": 0.5, "demand": 0.5})
    assert p_funnel > p_none
    assert 0.0 <= p_none <= 1.0 and 0.0 <= p_funnel <= 1.0


def test_cold_start_falls_back_to_heuristic():
    m = PreferenceModel.fit([("like", {"has_funnel": True, "whitespace": 0.9})])
    assert m.version == "heuristic-v1"
    p_hi = m.predict({"has_funnel": True, "whitespace": 0.9})
    p_lo = m.predict({"has_funnel": False, "whitespace": 0.1})
    assert p_hi > p_lo
