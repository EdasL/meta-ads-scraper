from types import SimpleNamespace
from agf.scoring.vetoes import (
    apply_vetoes, persistence_gate, longevity_floor, disguised_incumbent,
    relaunch, seasonal_spike,
)


def _fv(**kw):
    base = dict(weeks_observed=4, max_longevity_days=30, parent_company_id=None,
               is_emerging=True, rating_count=200,
               velocity_series=(2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12))
    base.update(kw)
    return SimpleNamespace(**base)


def test_clean_gem_passes_all_vetoes():
    res = apply_vetoes(_fv())
    assert res.vetoed is False and res.reason is None


def test_persistence_gate_fires_on_single_week():
    assert persistence_gate(_fv(weeks_observed=1)) is True
    assert apply_vetoes(_fv(weeks_observed=1)).reason == "insufficient_persistence"


def test_longevity_floor_fires_below_14d():
    assert longevity_floor(_fv(max_longevity_days=10)) is True
    assert apply_vetoes(_fv(max_longevity_days=10)).reason == "longevity_below_floor"


def test_disguised_incumbent_fires_when_parent_set_tolerates_null():
    assert disguised_incumbent(_fv(parent_company_id=7)) is True
    assert disguised_incumbent(_fv(parent_company_id=None)) is False


def test_relaunch_fires_on_emerging_with_huge_rating_base():
    assert relaunch(_fv(is_emerging=True, rating_count=500_000)) is True
    assert relaunch(_fv(is_emerging=True, rating_count=200)) is False
    assert relaunch(_fv(is_emerging=False, rating_count=500_000)) is False  # not tagged emerging


def test_seasonal_spike_fires_on_pure_season_passes_on_trend():
    seasonal = [2, 1, 2, 6] * 3            # period-4 pattern, ends on a peak, no trend
    trend = list(range(1, 13))             # strictly rising, last point is the peak
    assert seasonal_spike(_fv(velocity_series=seasonal)) is True
    assert seasonal_spike(_fv(velocity_series=trend)) is False


def test_seasonal_spike_short_series_short_circuits_pass():
    assert seasonal_spike(_fv(velocity_series=(1, 5, 1))) is False   # < 2 periods -> pass
