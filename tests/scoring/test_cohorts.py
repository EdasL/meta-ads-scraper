import numpy as np
from scipy.stats import median_abs_deviation
from agf.scoring.cohorts import cohort_key, robust_z, pctile


def test_cohort_key_is_category_x_country():
    assert cohort_key("Health & Fitness", "US") == "Health & Fitness×US"
    assert cohort_key(None, "US") == "unknown×US"          # web funnels / no app


def test_robust_z_matches_scipy_reference():
    vals = [10.0, 12.0, 11.0, 9.0, 100.0]                  # 100 is an outlier
    med = float(np.median(vals))
    scale = median_abs_deviation(vals, scale="normal")     # == MAD * 1.4826
    expected = (100.0 - med) / scale
    assert abs(robust_z(100.0, vals) - expected) < 1e-9


def test_robust_z_degenerate_is_zero_not_nan():
    assert robust_z(5.0, [5.0, 5.0, 5.0]) == 0.0           # MAD == 0
    assert robust_z(5.0, [5.0]) == 0.0                     # singleton


def test_pctile_is_midrank_in_unit_interval():
    vals = [1.0, 2.0, 3.0, 4.0]
    assert pctile(4.0, vals) == 0.875                      # (3 below + 0.5*1 eq)/4
    assert pctile(1.0, vals) == 0.125
    assert pctile(5.0, [5.0]) == 0.5                       # singleton -> neutral


def test_pctile_empty_is_neutral():
    assert pctile(1.0, []) == 0.5
