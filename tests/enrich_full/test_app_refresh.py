"""refresh_apps: store metadata refresh + daily adtech.app_metrics snapshot + recent
reviews. Engine-level (refresh_apps owns its transactions), so these tests commit
SENTINEL rows scoped via only_ids and clean them up — the suite must tolerate
whatever demo/real data shares the DB."""
import json
from datetime import date

import pytest
from sqlalchemy import text

from agf.db import repo
from agf.db.engine import begin
from agf.enrich_full.app_refresh import apps_due_for_refresh, refresh_apps

IOS_SID = "990077001"
ANDROID_SID = "com.test.refresh.sentinel"
OUTSIDE_SID = "990077002"
SNAP = date(2026, 6, 10)


class FakeStoreClient:
    """httpx-like client serving both the iTunes Lookup and the reviews RSS."""
    def __init__(self):
        self.urls = []
        self._payload = None

    def get(self, url, params=None, **kw):
        self.urls.append(url)
        if "lookup" in url:
            ids = (params or {}).get("id", "").split(",")
            self._payload = {"results": [
                {"trackId": int(i), "trackName": f"iOS {i}", "sellerName": "Dev",
                 "primaryGenreName": "Health & Fitness",
                 "releaseDate": "2026-04-01T07:00:00Z",
                 "userRatingCount": 555, "averageUserRating": 4.4,
                 "artworkUrl512": "https://icons/ios.png",
                 "screenshotUrls": ["https://shots/ios-1.png"],
                 "formattedPrice": "Free"} for i in ids if i]}
        else:                                     # customer-reviews RSS
            self._payload = {"feed": {"entry": [
                {"id": {"label": "ios-r1"}, "im:rating": {"label": "5"},
                 "author": {"name": {"label": "Fan"}}, "title": {"label": "Nice"},
                 "content": {"label": "Works"},
                 "updated": {"label": "2026-06-01T07:30:00-07:00"}}]}}
        return self

    def json(self):
        return self._payload


def _gp_runner(ids):
    return json.dumps([{"appId": i, "title": "Droid", "developer": "Acme",
                        "genre": "Tools", "releaseDateISO": "2026-03-15",
                        "score": 4.1, "ratings": 77,
                        "icon": "https://play/icon.png",
                        "screenshots": ["https://play/s1.png"],
                        "maxInstalls": 12345, "installs": "10,000+",
                        "priceText": "Free"} for i in ids])


def _gp_reviews_runner(package_id, num):
    return json.dumps([{"id": "gp-r1", "userName": "Bot", "score": 4, "title": None,
                        "text": "fine", "date": "2026-06-02T10:00:00.000Z"}])


def _seed(engine):
    with begin(engine) as conn:
        ios = repo.upsert_app(conn, {"store": "ios", "store_id": IOS_SID})
        android = repo.upsert_app(conn, {"store": "android", "store_id": ANDROID_SID})
        outside = repo.upsert_app(conn, {"store": "ios", "store_id": OUTSIDE_SID})
    return ios, android, outside


def _clean(engine):
    with begin(engine) as conn:                  # app_metrics/app_reviews CASCADE
        conn.execute(text("DELETE FROM public.apps WHERE store_id = ANY(:s)"),
                     {"s": [IOS_SID, ANDROID_SID, OUTSIDE_SID]})


@pytest.fixture
def sentinels(_migrated_engine):
    _clean(_migrated_engine)
    ids = _seed(_migrated_engine)
    try:
        yield ids
    finally:
        _clean(_migrated_engine)


def test_refresh_apps_writes_metadata_metrics_and_reviews(_migrated_engine, sentinels):
    ios, android, outside = sentinels
    summary = refresh_apps(_migrated_engine, only_ids=[ios, android],
                           itunes_client=FakeStoreClient(), gp_runner=_gp_runner,
                           gp_reviews_runner=_gp_reviews_runner, snapshot_date=SNAP,
                           pause=0)
    assert summary == {"apps": 2, "metrics": 2, "reviews": 2}
    with _migrated_engine.connect() as conn:
        ios_row = conn.execute(text(
            "SELECT name, icon_url, screenshots, price_label, installs, updated_at "
            "FROM public.apps WHERE id=:i"), {"i": ios}).first()
        assert ios_row.name == f"iOS {IOS_SID}"
        assert ios_row.icon_url == "https://icons/ios.png"
        assert ios_row.screenshots == ["https://shots/ios-1.png"]
        assert ios_row.price_label == "Free" and ios_row.installs is None
        assert ios_row.updated_at is not None

        droid = conn.execute(text(
            "SELECT installs, screenshots FROM public.apps WHERE id=:i"),
            {"i": android}).first()
        assert droid.installs == 12345           # Play maxInstalls
        assert droid.screenshots == ["https://play/s1.png"]

        metrics = conn.execute(text(
            "SELECT app_id, rating, rating_count, installs FROM adtech.app_metrics "
            "WHERE app_id = ANY(:i) AND snapshot_date=:d ORDER BY app_id"),
            {"i": [ios, android], "d": SNAP}).all()
        assert [(m.app_id, m.rating_count, m.installs) for m in metrics] == [
            (ios, 555, None), (android, 77, 12345)]

        reviews = conn.execute(text(
            "SELECT app_id, review_id FROM public.app_reviews WHERE app_id = ANY(:i) "
            "ORDER BY app_id"), {"i": [ios, android]}).all()
        assert [(r.app_id, r.review_id) for r in reviews] == [
            (ios, "ios-r1"), (android, "gp-r1")]

        # only_ids scoping: the outside sentinel was never touched
        assert conn.execute(text(
            "SELECT count(*) FROM adtech.app_metrics WHERE app_id=:i"),
            {"i": outside}).scalar() == 0


def test_refresh_apps_idempotent_per_day(_migrated_engine, sentinels):
    ios, android, _ = sentinels
    kw = dict(only_ids=[ios, android], itunes_client=FakeStoreClient(),
              gp_runner=_gp_runner, gp_reviews_runner=_gp_reviews_runner,
              snapshot_date=SNAP, pause=0)
    refresh_apps(_migrated_engine, **kw)
    refresh_apps(_migrated_engine, **kw)          # same day again -> upserts, no dupes
    with _migrated_engine.connect() as conn:
        assert conn.execute(text(
            "SELECT count(*) FROM adtech.app_metrics WHERE app_id = ANY(:i)"),
            {"i": [ios, android]}).scalar() == 2
        assert conn.execute(text(
            "SELECT count(*) FROM public.app_reviews WHERE app_id = ANY(:i)"),
            {"i": [ios, android]}).scalar() == 2


def test_apps_due_for_refresh_drops_snapshotted_apps(_migrated_engine, sentinels):
    ios, android, outside = sentinels
    with _migrated_engine.connect() as conn:
        due = apps_due_for_refresh(conn, SNAP)
    assert {ios, android, outside} <= set(due)
    refresh_apps(_migrated_engine, only_ids=[ios, android],
                 itunes_client=FakeStoreClient(), gp_runner=_gp_runner,
                 gp_reviews_runner=_gp_reviews_runner, snapshot_date=SNAP, pause=0)
    with _migrated_engine.connect() as conn:
        due = apps_due_for_refresh(conn, SNAP)
    assert ios not in due and android not in due
    assert outside in due                        # untouched -> still due
