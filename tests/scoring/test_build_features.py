from datetime import date, datetime, timezone
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
