from agf.scoring.gemscore import demand, gem_score, whitespace


def test_all_axes_max_is_100():
    assert gem_score(1.0, 1.0, 1.0, 1.0, 1.0) == 100.0


def test_missing_demand_and_whitespace_floor_to_neutral():
    # three-axis callers (no D/W data yet) land at exactly 100 * 0.5^(0.38+0.05)
    assert abs(gem_score(1.0, 1.0, 1.0) - 100 * 0.5 ** 0.43) < 1e-9


def test_incumbent_present_low_axis_gates_to_near_zero():
    # UR genuinely 0 (famous brand) -> AND-gate collapses the score to exactly 0
    assert gem_score(1.0, 0.0, 1.0, 1.0, 1.0) == 0.0
    # a low (but present) UR still drags the score down, just less hard than v2
    low_ur = gem_score(0.9, 0.02, 0.9, 0.9, 0.9)
    high_ur = gem_score(0.9, 0.9, 0.9, 0.9, 0.9)
    assert low_ur < 0.7 * high_ur


def test_present_zero_gates_every_axis():
    for i in range(5):
        axes = [1.0] * 5
        axes[i] = 0.0
        assert gem_score(*axes) == 0.0


def test_sparse_axis_is_imputed_to_floor_not_zero():
    # UR is None (no rating/followers/reach data) -> imputed to floor, gem survives
    s_missing = gem_score(0.9, None, 0.9, 0.9, 0.9, floor=0.05)
    s_zero = gem_score(0.9, 0.0, 0.9, 0.9, 0.9)
    assert s_zero == 0.0
    assert s_missing > 0.0                      # not killed by a data gap
    # FI has the smallest exponent (.05) -> a missing FI barely dents the score
    assert (gem_score(0.9, 0.9, None, 0.9, 0.9, floor=0.05)
            > gem_score(0.9, None, 0.9, 0.9, 0.9, floor=0.05))


def test_floor_only_applies_to_missing_axes():
    # a present 0.0 is NOT lifted to the floor
    assert gem_score(1.0, 0.0, 1.0, 1.0, 1.0, floor=0.05) == 0.0


def test_monotonic_in_each_axis():
    base = gem_score(0.5, 0.5, 0.5, 0.5, 0.5)
    assert gem_score(0.6, 0.5, 0.5, 0.5, 0.5) > base
    assert gem_score(0.5, 0.6, 0.5, 0.5, 0.5) > base
    assert gem_score(0.5, 0.5, 0.6, 0.5, 0.5) > base
    assert gem_score(0.5, 0.5, 0.5, 0.6, 0.5) > base
    assert gem_score(0.5, 0.5, 0.5, 0.5, 0.6) > base


def test_demand_axis_renormalizes_over_present_components():
    # iOS app: installs None -> review velocity carries the whole axis
    assert abs(demand({"installs": None, "reviews": 0.8}) - 0.8) < 1e-9
    assert abs(demand({"installs": 0.6, "reviews": 0.6}) - 0.6) < 1e-9
    assert demand({"installs": None, "reviews": None}) is None


def test_whitespace_axis_single_component():
    assert whitespace({"crowding": 0.9}) == 0.9
    assert whitespace({"crowding": None}) is None
