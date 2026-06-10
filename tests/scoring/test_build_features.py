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
