import math
from agf.scoring.features import log_growth_wow, acceleration, ewma


def test_log_growth_flat_is_zero():
    assert log_growth_wow([5, 5, 5]) == 0.0
    assert log_growth_wow([7]) == 0.0          # <2 points -> 0


def test_log_growth_rising_is_positive():
    g = log_growth_wow([2, 4])                  # log((4+1)/(2+1)) = log(5/3)
    assert g == math.log(5 / 3)
    assert g > 0


def test_acceleration_flat_is_zero():
    assert acceleration([5, 5, 5]) == 0.0
    assert acceleration([1, 2]) == 0.0         # <3 points -> 0


def test_acceleration_convex_positive_concave_negative():
    assert acceleration([2, 4, 8]) > 0         # growth rate increasing
    assert acceleration([1, 4, 5]) < 0         # growth rate decreasing


def test_ewma_constant_series_returns_constant():
    assert ewma([3, 3, 3], alpha=0.5) == 3.0


def test_ewma_weights_recent_more():
    # alpha=0.5: acc=1 -> 0.5*5+0.5*1=3
    assert ewma([1, 5], alpha=0.5) == 3.0
    assert ewma([], alpha=0.5) == 0.0
