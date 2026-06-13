from types import SimpleNamespace
from agf.scoring.vetoes import (
    apply_vetoes, persistence_gate,
    relaunch, game_app, no_app, unverified_app, incumbent_ad_volume,
    incumbent_audience, established_traction,
)


def _fv(**kw):
    base = dict(weeks_observed=4, max_longevity_days=30, parent_company_id=None,
               is_emerging=True, rating_count=200,
               velocity_series=(2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12),
               # quality-veto inputs (real-gems feed): clean-gem defaults
               has_app=True, is_game=None, installs=None,
               page_like_count=None, meta_active_ads=None,
               meta_active_ads_capped=None, active_ad_count=10,
               app_id=11, app_name='Clean Gem App')
    base.update(kw)
    return SimpleNamespace(**base)


def test_clean_gem_passes_all_vetoes():
    res = apply_vetoes(_fv())
    assert res.vetoed is False and res.reason is None


def test_persistence_gate_fires_on_single_week():
    assert persistence_gate(_fv(weeks_observed=1)) is True
    assert apply_vetoes(_fv(weeks_observed=1)).reason == "insufficient_persistence"


def test_short_longevity_no_longer_vetoed():
    # The 14-day longevity floor was dropped (user 2026-06-13): brand-new apps
    # with very short ad histories must reach the feed, not be held back.
    assert apply_vetoes(_fv(max_longevity_days=1)).vetoed is False
    assert apply_vetoes(_fv(max_longevity_days=0)).vetoed is False


def test_relaunch_fires_on_emerging_with_huge_rating_base():
    assert relaunch(_fv(is_emerging=True, rating_count=500_000)) is True
    assert relaunch(_fv(is_emerging=True, rating_count=200)) is False
    assert relaunch(_fv(is_emerging=False, rating_count=500_000)) is False  # not tagged emerging


def test_removed_vetoes_no_longer_fire():
    # GemScore v2 (spec 2026-06-11): ownership is irrelevant to copy-worthiness,
    # and the seasonal STL check is gone — a parent company or a seasonal-looking
    # series must not kill an otherwise-clean gem.
    assert apply_vetoes(_fv(parent_company_id=7)).vetoed is False
    assert apply_vetoes(_fv(velocity_series=(2, 1, 2, 6) * 3)).vetoed is False


def test_game_app_fires_only_on_true():
    assert game_app(_fv(is_game=True)) is True
    assert apply_vetoes(_fv(is_game=True)).reason == "game"
    assert game_app(_fv(is_game=False)) is False
    assert game_app(_fv(is_game=None)) is False     # unknown passes


def test_no_app_fires_when_no_linked_app():
    assert no_app(_fv(has_app=False)) is True
    assert apply_vetoes(_fv(has_app=False)).reason == "no_app"
    assert no_app(_fv(has_app=True)) is False


def test_incumbent_ad_volume_fires_on_capped_true():
    assert incumbent_ad_volume(_fv(meta_active_ads_capped=True)) is True
    assert apply_vetoes(_fv(meta_active_ads_capped=True)).reason == "incumbent_ad_volume"
    assert incumbent_ad_volume(_fv(meta_active_ads_capped=False)) is False
    assert incumbent_ad_volume(_fv(meta_active_ads_capped=None)) is False  # unknown passes


def test_incumbent_ad_volume_fires_above_200_active_ads():
    # true library count when measured (ceiling 200, user-set 2026-06-13)
    assert incumbent_ad_volume(_fv(meta_active_ads=201)) is True
    assert apply_vetoes(_fv(meta_active_ads=201)).reason == "incumbent_ad_volume"
    assert incumbent_ad_volume(_fv(meta_active_ads=200)) is False
    # stored active-ad observation as fallback when meta count unmeasured
    assert incumbent_ad_volume(_fv(meta_active_ads=None, active_ad_count=210)) is True
    assert incumbent_ad_volume(_fv(meta_active_ads=None, active_ad_count=190)) is False
    # a measured-small library wins over a stale larger stored count
    assert incumbent_ad_volume(_fv(meta_active_ads=30, active_ad_count=210)) is False


def test_incumbent_audience_fires_above_10k_likes():
    assert incumbent_audience(_fv(page_like_count=10_001)) is True
    assert apply_vetoes(_fv(page_like_count=10_001)).reason == "incumbent_audience"
    assert incumbent_audience(_fv(page_like_count=10_000)) is False  # at threshold passes
    assert incumbent_audience(_fv(page_like_count=None)) is False     # unknown passes


def test_established_traction_sees_all_linked_listings():
    # a huge Android sibling can't hide behind the latest (small) iOS listing
    assert established_traction(_fv(installs=None, max_installs=10_000_000)) is True
    assert established_traction(_fv(rating_count=1_000, max_rating_count=900_000)) is True
    assert established_traction(_fv(installs=12_000, max_installs=40_000)) is False


def test_established_traction_fires_on_installs_or_ratings():
    assert established_traction(_fv(installs=100_001)) is True
    assert established_traction(_fv(rating_count=5_001, is_emerging=False)) is True
    assert apply_vetoes(_fv(installs=100_001)).reason == "established_traction"
    assert established_traction(_fv(installs=100_000, rating_count=5_000)) is False
    assert established_traction(_fv(installs=None, rating_count=None)) is False  # unknown passes


def test_clean_gem_still_passes_with_new_vetoes():
    res = apply_vetoes(_fv(is_game=False, installs=12_000, page_like_count=3_000,
                           meta_active_ads=40, meta_active_ads_capped=False))
    assert res.vetoed is False and res.reason is None


def test_unverified_app_fires_when_store_lookup_never_resolved():
    # linked app whose store page is dead (lookup left name NULL) -> veto
    assert unverified_app(_fv(app_id=5, app_name=None)) is True
    assert apply_vetoes(_fv(app_id=5, app_name=None)).reason == "unverified_app"
    # resolved listing passes
    assert unverified_app(_fv(app_id=5, app_name="Live App")) is False
    # app-less advertisers are no_app's concern, not this veto's
    assert unverified_app(_fv(has_app=False, app_id=None, app_name=None)) is False
