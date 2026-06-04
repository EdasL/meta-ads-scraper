from agf.score import gem_score_poc


def test_famous_app_scores_near_zero():
    # huge rating base -> under_radar ~0 -> AND-gate collapses score
    s = gem_score_poc(active_count=40, longevity_days=120,
                      rating_count=5_000_000, is_emerging=True)
    assert s < 5


def test_emerging_scaler_scores_high():
    s = gem_score_poc(active_count=20, longevity_days=45,
                      rating_count=300, is_emerging=True)
    assert s > 50


def test_not_emerging_zeroes_score():
    s = gem_score_poc(active_count=20, longevity_days=45,
                      rating_count=300, is_emerging=False)
    assert s == 0.0


def test_no_longevity_means_no_scaling():
    s = gem_score_poc(active_count=20, longevity_days=2,
                      rating_count=300, is_emerging=True)
    assert s < 20
