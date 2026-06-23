"""Post-score vetoes (master-plan §6 P5-T8). Pure functions; each returns True
when it should KILL the gem. apply_vetoes returns the first that fires."""
from dataclasses import dataclass

MIN_WEEKS = 2            # persistence: a 1-week blip is not a trend
RELAUNCH_RATING_MAX = 100_000  # "emerging" + this many ratings == a relaunch, not new
# quality vetoes (real-gems feed 2026-06-10); NULL/unknown always passes
INCUMBENT_PAGE_LIKES_MAX = 10_000    # FB audience above this == incumbent (user-set 2026-06-10)
# Funnels scale a large FB audience precisely BECAUSE they work — a proven web2app/quiz
# funnel operator (The Farmer's Dog, Spoiled Child) has 100k–1M+ likes, so the emerging-app
# 10k ceiling would discard the best funnels. Only veto true mega-brands here; size/quality
# filtering is deferred to the funnel confirm stage + scoring.
FUNNEL_INCUMBENT_PAGE_LIKES_MAX = 5_000_000
MAX_ACTIVE_ADS_FOR_GEM = 200         # an ad library bigger than this == funded growth team, not emerging (user-set 2026-06-13, was 80)
ESTABLISHED_INSTALLS_MAX = 100_000     # store installs above this == established (user-set 2026-06-10)
ESTABLISHED_RATINGS_MAX = 5_000        # rating base above this == established (user-set 2026-06-10)


@dataclass(frozen=True)
class VetoResult:
    vetoed: bool
    reason: str | None


def persistence_gate(fv) -> bool:
    return (fv.weeks_observed or 0) < MIN_WEEKS


def relaunch(fv) -> bool:
    # tagged emerging by release date but sitting on a huge rating base == rebrand/relaunch
    return bool(fv.is_emerging) and (fv.rating_count or 0) > RELAUNCH_RATING_MAX


def game_app(fv) -> bool:
    # games are out of scope for the gems feed. NULL (unknown) passes.
    return fv.is_game is True


def no_app(fv) -> bool:
    # v1 is apps-only: physical products and services are out. EXCEPTION: a
    # confirmed web-sub funnel is legitimately app-less (digital subscription
    # sold web-to-subscribe), so it is allowed through and ranked on its ad
    # corpus. getattr keeps older positional FeatureVectors working.
    if getattr(fv, "web_payoff_kind", None):
        return False
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
    (relaunch, "relaunch"),
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
