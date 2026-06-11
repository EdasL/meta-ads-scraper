from datetime import date, datetime, timezone
from sqlalchemy import text
from agf.parse import AdRecord
from agf.db import repo
from agf.scoring.features import build_features

AS_OF = date(2026, 6, 4)
NOW = int(datetime(2026, 6, 4, tzinfo=timezone.utc).timestamp())  # same base as AS_OF
DAY = 86_400


def _ad(ext, *, days_old, active=True):
    return AdRecord(ext, "PF", "FeatCo", active, NOW - days_old * DAY, None, 4,
                    "INSTALL_MOBILE_APP", "Install",
                    "https://apps.apple.com/us/app/x/id999", "body")


def test_build_features_accel_and_longevity_share(pg_conn):
    adv = repo.upsert_advertiser(pg_conn, _ad("F1", days_old=20))
    app = repo.upsert_app(pg_conn, {"store": "ios", "store_id": "999",
                                    "name": "Feet", "genre": "Health & Fitness",
                                    "rating_count": 120, "release_date": "2026-05-20",
                                    "is_emerging": True})
    # two active ads: one 20d old (>=14), one 5d old (<14) -> longevity_share 0.5
    repo.upsert_ad(pg_conn, _ad("F1", days_old=20), adv, app)
    repo.upsert_ad(pg_conn, _ad("F2", days_old=5), adv, app)
    # 4-week active_ad_count series 1,2,4,8 -> accelerating
    for wk, n in zip(("2026-05-11", "2026-05-18", "2026-05-25", "2026-06-01"), (1, 2, 4, 8)):
        repo.upsert_app_advertiser_metric(pg_conn, adv, wk, n, n, n * 10)

    fv = build_features(pg_conn, adv, AS_OF)

    assert fv.advertiser_id == adv
    assert fv.cohort == "Health & Fitness×US"
    assert fv.weeks_observed == 4
    assert fv.accel > 0                         # 1,2,4,8 is convex in log space
    assert fv.ewma_velocity == 2.75             # ewma of weekly deltas [1,2,4], not the level
    assert fv.max_longevity_days >= 20
    assert fv.longevity_share == 0.5            # 1 of 2 active ads >= 14d
    assert fv.rating_count == 120
    assert fv.is_emerging is True
    assert fv.install_cta is True
    assert fv.velocity_series == (1, 2, 4, 8)


def test_build_features_quality_veto_inputs(pg_conn):
    """New veto inputs (real-gems feed): app is_game/installs + advertiser meta signals."""
    adv = repo.upsert_advertiser(pg_conn, _ad("F3", days_old=20))
    app = repo.upsert_app(pg_conn, {"store": "ios", "store_id": "999",
                                    "name": "Feet", "genre": "Health & Fitness",
                                    "rating_count": 120, "installs": 12_000,
                                    "release_date": "2026-05-20", "is_emerging": True})
    repo.upsert_ad(pg_conn, _ad("F3", days_old=20), adv, app)
    # set directly: is_game persistence + meta signals are other agents' write paths
    pg_conn.execute(text("UPDATE public.apps SET is_game=FALSE WHERE id=:i"), {"i": app})
    pg_conn.execute(text(
        "UPDATE public.advertisers SET page_like_count=3500, meta_active_ads=42, "
        "meta_active_ads_capped=TRUE WHERE id=:i"), {"i": adv})

    fv = build_features(pg_conn, adv, AS_OF)

    assert fv.is_game is False
    assert fv.installs == 12_000
    assert fv.page_like_count == 3500
    assert fv.meta_active_ads == 42
    assert fv.meta_active_ads_capped is True


def test_build_features_quality_inputs_null_when_unknown(pg_conn):
    """No app row + un-enriched advertiser -> all new inputs stay None/default."""
    adv = repo.upsert_advertiser(pg_conn, _ad("F4", days_old=20))
    repo.upsert_ad(pg_conn, _ad("F4", days_old=20), adv, None)

    fv = build_features(pg_conn, adv, AS_OF)

    assert fv.has_app is False
    assert fv.is_game is None
    assert fv.installs is None
    assert fv.page_like_count is None
    assert fv.meta_active_ads is None
    assert fv.meta_active_ads_capped is False   # column default; never vetoes


def test_build_features_demand_velocity_and_iteration(pg_conn):
    """GemScore v2: app_metrics deltas -> demand velocities; ad launches -> iteration."""
    adv = repo.upsert_advertiser(pg_conn, _ad("F5", days_old=21))
    app = repo.upsert_app(pg_conn, {"store": "ios", "store_id": "999",
                                    "name": "Feet", "genre": "Health & Fitness",
                                    "rating_count": 120, "release_date": "2026-05-20",
                                    "is_emerging": True})
    repo.upsert_ad(pg_conn, _ad("F5", days_old=21), adv, app)
    repo.upsert_ad(pg_conn, _ad("F6", days_old=7), adv, app)
    # 28-day window ending AS_OF: installs 1000 -> 1700, reviews 50 -> 78
    repo.upsert_app_metric(pg_conn, app, "2026-05-07", 4.8, 50, 1000)
    repo.upsert_app_metric(pg_conn, app, "2026-06-04", 4.8, 78, 1700)

    fv = build_features(pg_conn, adv, AS_OF)

    assert fv.installs_velocity == 25.0          # +700 / 28 days
    assert fv.review_velocity == 1.0             # +28 / 28 days
    assert fv.installs_delta_28d == 700
    assert fv.rating_count_delta_28d == 28
    # launches 21d and 7d ago -> weekly counts [1, 0, 1, 0] -> ewma 0.375
    assert fv.iteration_rate == 0.375


def test_build_features_demand_ios_null_installs(pg_conn):
    """iOS apps have no installs: review velocity carries demand, installs stay None."""
    adv = repo.upsert_advertiser(pg_conn, _ad("F7", days_old=20))
    app = repo.upsert_app(pg_conn, {"store": "ios", "store_id": "999",
                                    "name": "Feet", "genre": "Health & Fitness",
                                    "rating_count": 120, "release_date": "2026-05-20",
                                    "is_emerging": True})
    repo.upsert_ad(pg_conn, _ad("F7", days_old=20), adv, app)
    repo.upsert_app_metric(pg_conn, app, "2026-05-21", 4.8, 50, None)
    repo.upsert_app_metric(pg_conn, app, "2026-06-04", 4.8, 64, None)

    fv = build_features(pg_conn, adv, AS_OF)

    assert fv.installs_velocity is None
    assert fv.installs_delta_28d is None
    assert fv.review_velocity == 1.0             # +14 / 14 days
    assert fv.rating_count_delta_28d == 14


def test_build_features_demand_none_without_snapshots(pg_conn):
    """No app_metrics rows -> demand inputs stay None (axis floors later)."""
    adv = repo.upsert_advertiser(pg_conn, _ad("F8", days_old=20))
    app = repo.upsert_app(pg_conn, {"store": "ios", "store_id": "999",
                                    "name": "Feet", "genre": "Health & Fitness",
                                    "rating_count": 120, "release_date": "2026-05-20",
                                    "is_emerging": True})
    repo.upsert_ad(pg_conn, _ad("F8", days_old=20), adv, app)

    fv = build_features(pg_conn, adv, AS_OF)

    assert fv.installs_velocity is None and fv.review_velocity is None
    assert fv.installs_delta_28d is None and fv.rating_count_delta_28d is None
    assert fv.iteration_rate > 0                 # one launch still counts
