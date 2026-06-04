"""Post-score vetoes (master-plan §6 P5-T8). Pure functions; each returns True
when it should KILL the gem. apply_vetoes returns the first that fires."""
from dataclasses import dataclass
import numpy as np
from statsmodels.tsa.seasonal import STL

MIN_WEEKS = 2            # persistence: a 1-week blip is not a trend
LONGEVITY_FLOOR = 14     # days; mirrors the discovery 14-day scaling gate
RELAUNCH_RATING_MAX = 100_000  # "emerging" + this many ratings == a relaunch, not new
_STL_PERIOD = 4          # weekly series, ~monthly seasonal cycle
_REAL_GROWTH_EPS = 1e-6


@dataclass(frozen=True)
class VetoResult:
    vetoed: bool
    reason: str | None


def persistence_gate(fv) -> bool:
    return (fv.weeks_observed or 0) < MIN_WEEKS


def longevity_floor(fv) -> bool:
    return (fv.max_longevity_days or 0) < LONGEVITY_FLOOR


def disguised_incumbent(fv) -> bool:
    # owned by a parent (self-FK) -> not an independent gem. NULL tolerated (passes).
    return fv.parent_company_id is not None


def relaunch(fv) -> bool:
    # tagged emerging by release date but sitting on a huge rating base == rebrand/relaunch
    return bool(fv.is_emerging) and (fv.rating_count or 0) > RELAUNCH_RATING_MAX


def seasonal_spike(fv) -> bool:
    """STL-deseasonalize the velocity series; veto if the latest spike disappears
    once seasonality is removed. <2 seasonal periods -> short-circuit pass."""
    s = np.asarray(list(fv.velocity_series or ()), dtype=float)
    if s.size < 2 * _STL_PERIOD:
        return False
    seasonal = STL(s, period=_STL_PERIOD, robust=True).fit().seasonal
    deseasonalized = s - seasonal
    is_spike = s[-1] > float(np.mean(s[:-1]))
    real_growth = deseasonalized[-1] - float(np.mean(deseasonalized[:-1]))
    return bool(is_spike and real_growth <= _REAL_GROWTH_EPS)


_VETOES = [
    (persistence_gate, "insufficient_persistence"),
    (longevity_floor, "longevity_below_floor"),
    (disguised_incumbent, "disguised_incumbent"),
    (relaunch, "relaunch"),
    (seasonal_spike, "seasonal_spike"),
]


def apply_vetoes(fv) -> VetoResult:
    for fn, reason in _VETOES:
        if fn(fv):
            return VetoResult(True, reason)
    return VetoResult(False, None)
