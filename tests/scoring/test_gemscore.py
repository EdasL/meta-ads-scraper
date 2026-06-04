from agf.scoring.gemscore import gem_score


def test_all_axes_max_is_100():
    assert gem_score(1.0, 1.0, 1.0) == 100.0


def test_incumbent_present_low_axis_gates_to_near_zero():
    # UR genuinely 0 (famous brand) -> AND-gate collapses the score to exactly 0
    assert gem_score(1.0, 0.0, 1.0) == 0.0
    # a low (but present) UR is gated hard relative to a high UR at equal SV/FI
    low_ur = gem_score(0.9, 0.02, 0.9)
    high_ur = gem_score(0.9, 0.9, 0.9)
    assert low_ur < 0.5 * high_ur


def test_sparse_axis_is_imputed_to_floor_not_zero():
    # UR is None (no rating/followers/reach data) -> imputed to floor, gem survives
    s_missing = gem_score(0.9, None, 0.9, floor=0.05)
    s_zero = gem_score(0.9, 0.0, 0.9)
    assert s_zero == 0.0
    assert s_missing > 0.0                      # not killed by a data gap
    # FI has the smallest exponent (.15) -> a missing FI barely dents the score
    assert gem_score(0.9, 0.9, None, floor=0.05) > gem_score(0.9, None, 0.9, floor=0.05)


def test_floor_only_applies_to_missing_axes():
    # a present 0.0 is NOT lifted to the floor
    assert gem_score(1.0, 0.0, 1.0, floor=0.05) == 0.0


def test_monotonic_in_each_axis():
    base = gem_score(0.5, 0.5, 0.5)
    assert gem_score(0.6, 0.5, 0.5) > base
    assert gem_score(0.5, 0.6, 0.5) > base
    assert gem_score(0.5, 0.5, 0.6) > base
