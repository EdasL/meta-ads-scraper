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
# quality vetoes (real-gems feed 2026-06-10); NULL/unknown always passes
INCUMBENT_PAGE_LIKES_MAX = 10_000    # FB audience above this == incumbent (user-set 2026-06-10)
MAX_ACTIVE_ADS_FOR_GEM = 50          # an ad library bigger than this == funded growth team, not emerging
ESTABLISHED_INSTALLS_MAX = 100_000     # store installs above this == established (user-set 2026-06-10)
ESTABLISHED_RATINGS_MAX = 5_000        # rating base above this == established (user-set 2026-06-10)


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


def game_app(fv) -> bool:
    # games are out of scope for the gems feed. NULL (unknown) passes.
    return fv.is_game is True


def no_app(fv) -> bool:
    # v1 is apps-only: physical products, services and web-only funnels are out.
    return not fv.has_app


def unverified_app(fv) -> bool:
    # ads link to a store page, but the store lookup never resolved a listing
    # (name stayed NULL) -> delisted/dead app. A gem must be live in a store.
    # Resolution runs in the same pipeline pass as scoring, so live apps have
    # names by the time this fires; transient lookup misses heal on the next run.
    return bool(fv.has_app) and fv.app_id is not None and fv.app_name is None


def incumbent_ad_volume(fv) -> bool:
    """Too many active ads to be emerging. Uses the true library count when measured;
    falls back to our stored active-ad observation (a LOWER bound on truth, so missing
    data still never vetoes). Cap-hit pagination always vetoes."""
    if fv.meta_active_ads_capped is True:
        return True
    observed = fv.meta_active_ads if fv.meta_active_ads is not None else fv.active_ad_count
    return (observed or 0) > MAX_ACTIVE_ADS_FOR_GEM


def incumbent_audience(fv) -> bool:
    # huge FB page audience -> incumbent. NULL passes.
    return (fv.page_like_count or 0) > INCUMBENT_PAGE_LIKES_MAX


def established_traction(fv) -> bool:
    # already-huge store traction -> established, not emerging. NULLs pass.
    # Checks the maxima across ALL linked listings (when available) so a 10M-install
    # Android sibling can't hide behind a small-rating-base iOS listing.
    installs = max(fv.installs or 0, getattr(fv, "max_installs", None) or 0)
    ratings = max(fv.rating_count or 0, getattr(fv, "max_rating_count", None) or 0)
    return installs > ESTABLISHED_INSTALLS_MAX or ratings > ESTABLISHED_RATINGS_MAX


_VETOES = [
    (persistence_gate, "insufficient_persistence"),
    (longevity_floor, "longevity_below_floor"),
    (disguised_incumbent, "disguised_incumbent"),
    (relaunch, "relaunch"),
    (seasonal_spike, "seasonal_spike"),
    (game_app, "game"),
    (no_app, "no_app"),
    (unverified_app, "unverified_app"),
    (incumbent_ad_volume, "incumbent_ad_volume"),
    (incumbent_audience, "incumbent_audience"),
    (established_traction, "established_traction"),
]


def apply_vetoes(fv) -> VetoResult:
    for fn, reason in _VETOES:
        if fn(fv):
            return VetoResult(True, reason)
    return VetoResult(False, None)
