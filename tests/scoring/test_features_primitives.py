import math
from agf.scoring.features import log_growth_wow, window_log_growth, acceleration, ewma


def test_log_growth_flat_is_zero():
    assert log_growth_wow([5, 5, 5]) == 0.0
    assert log_growth_wow([7]) == 0.0          # <2 points -> 0


def test_window_growth_credits_sustained_ramp_even_when_plateaued():
    # last week is flat (79->80) so wow ~= 0, but the overall ramp 30->80 is strong
    series = [30, 30, 31, 34, 41, 71, 79, 80]
    assert log_growth_wow(series) < 0.05                  # recent week reads flat
    assert window_log_growth(series) == math.log(81 / 31)  # base=first nonzero=30
    assert window_log_growth(series) > 0.9


def test_window_growth_from_zero_is_strong_not_undefined():
    # a 0 -> 40 ramp anchors on the first non-zero week (1), not divide-by-zero
    assert window_log_growth([0, 0, 1, 3, 8, 24, 40]) == math.log(41 / 2)


def test_window_growth_flat_and_declining():
    assert window_log_growth([5, 5, 5]) == 0.0
    assert window_log_growth([20, 16, 12]) < 0           # declining -> negative


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
