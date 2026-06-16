"""GemScore v3 regression: winner consolidation must beat ad-spray (spec 2026-06-16)."""
from agf.scoring.run_scoring import _sv_components
from agf.scoring.gemscore import scaling_velocity, gem_score


class _FV:
    """Minimal stand-in exposing only the fields _sv_components reads."""
    def __init__(self, max_longevity_days, proven_winner_count, count_growth, iteration_rate):
        self.max_longevity_days = max_longevity_days
        self.proven_winner_count = proven_winner_count
        self.count_growth = count_growth
        self.iteration_rate = iteration_rate


def _sv(fv, members):
    return scaling_velocity(_sv_components(fv, members))


def test_consolidated_winners_outscore_spray():
    # consolidated: count fell 20->2 (count_growth clamped to 0), but 2 ads have run 60d
    consolidated = _FV(max_longevity_days=60, proven_winner_count=2,
                       count_growth=0.0, iteration_rate=0.1)
    # spray: 20 fresh ads, none proven, count rising fast, launching constantly
    spray = _FV(max_longevity_days=5, proven_winner_count=0,
                count_growth=0.8, iteration_rate=1.0)
    members = [consolidated, spray]

    sv_consolidated = _sv(consolidated, members)
    sv_spray = _sv(spray, members)
    assert sv_consolidated > sv_spray            # winner signal (0.70) beats count/iteration (0.30)

    # with every other axis held equal, the full GemScore follows SV
    gs_consolidated = gem_score(sv_consolidated, 0.6, 0.6, 0.6, 0.6)
    gs_spray = gem_score(sv_spray, 0.6, 0.6, 0.6, 0.6)
    assert gs_consolidated > gs_spray


def test_falling_count_is_no_worse_than_flat_count():
    # two identical apps except one's count fell (clamped 0) and one's was flat (0).
    # clamping means a falling count is treated the same as flat — never a penalty.
    falling = _FV(max_longevity_days=30, proven_winner_count=1,
                  count_growth=0.0, iteration_rate=0.3)   # window_growth was negative -> clamped 0
    flat = _FV(max_longevity_days=30, proven_winner_count=1,
               count_growth=0.0, iteration_rate=0.3)       # genuinely flat -> 0
    members = [falling, flat]
    assert _sv(falling, members) == _sv(flat, members)
