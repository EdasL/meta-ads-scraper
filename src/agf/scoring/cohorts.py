"""Cohort identity + robust within-cohort normalization (master-plan §6 P5-T3).
All GemScore normalization is within a (category × country) cohort."""
import numpy as np

_MAD_TO_STD = 1.4826022185056018  # 1/Φ⁻¹(0.75); matches scipy median_abs_deviation(scale="normal")


def cohort_key(category, country) -> str:
    """Stable cohort label. NULL category (web funnels / unlinked) -> 'unknown'."""
    return f"{category or 'unknown'}×{country or 'US'}"


def robust_z(x: float, values) -> float:
    """Median/MAD z-score (outlier-robust). Degenerate (MAD==0) -> 0.0, never NaN."""
    arr = np.asarray(list(values), dtype=float)
    if arr.size == 0:
        return 0.0
    med = float(np.median(arr))
    mad = float(np.median(np.abs(arr - med)))
    if mad == 0.0:
        return 0.0
    return (x - med) / (_MAD_TO_STD * mad)


def pctile(x: float, values) -> float:
    """Midrank percentile in [0,1]: (count<x + 0.5*count==x)/n. Empty/singleton -> 0.5."""
    arr = np.asarray(list(values), dtype=float)
    n = arr.size
    if n == 0:
        return 0.5
    below = float(np.count_nonzero(arr < x))
    equal = float(np.count_nonzero(arr == x))
    return (below + 0.5 * equal) / n
