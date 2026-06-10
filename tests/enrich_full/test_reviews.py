"""Reviews fetchers: iOS customer-reviews RSS (lowercase paged form) + the
gp_reviews.js sidecar parse, and the public.app_reviews upsert."""
import json
from datetime import datetime, timezone, timedelta

from sqlalchemy import text

from agf.db import repo
from agf.enrich_full.reviews import (ReviewRecord, fetch_gplay_reviews,
                                     fetch_itunes_reviews, parse_gplay_reviews,
                                     parse_itunes_reviews)


def _ios_entry(rid="9001", rating="5", author="GemFan", title="Great",
               content="Love it", updated="2026-06-01T07:30:00-07:00"):
    return {"id": {"label": rid}, "im:rating": {"label": rating},
            "author": {"name": {"label": author}}, "title": {"label": title},
            "content": {"label": content, "attributes": {"type": "text"}},
            "updated": {"label": updated}}


class FakeRssClient:
    """Records the URL and returns a canned RSS JSON payload (or raises on .json())."""
    def __init__(self, payload):
        self.payload = payload
        self.urls = []

    def get(self, url, **kw):
        self.urls.append(url)
        return self

    def json(self):
        if self.payload is None:
            raise ValueError("not json")          # Apple error page for a bad id
        return self.payload


# --- iOS parsing -----------------------------------------------------------------
def test_parse_itunes_reviews_maps_label_fields():
    payload = {"feed": {"entry": [_ios_entry()]}}
    (r,) = parse_itunes_reviews(payload)
    assert r == ReviewRecord(
        review_id="9001", author="GemFan", rating=5, title="Great", content="Love it",
        reviewed_at=datetime(2026, 6, 1, 7, 30,
                             tzinfo=timezone(timedelta(hours=-7))))


def test_parse_itunes_reviews_skips_entries_without_rating():
    # the app-summary entry carries no im:rating — it is metadata, not a review
    payload = {"feed": {"entry": [{"id": {"label": "app"}, "title": {"label": "FitGem"}},
                                  _ios_entry(rid="9002")]}}
    out = parse_itunes_reviews(payload)
    assert [r.review_id for r in out] == ["9002"]


def test_parse_itunes_reviews_single_entry_dict():
    # a one-review feed serializes entry as a dict, not a list
    payload = {"feed": {"entry": _ios_entry(rid="9003")}}
    assert [r.review_id for r in parse_itunes_reviews(payload)] == ["9003"]


def test_parse_itunes_reviews_empty_feed():
    assert parse_itunes_reviews({"feed": {}}) == []
    assert parse_itunes_reviews({}) == []
    assert parse_itunes_reviews(None) == []


def test_fetch_itunes_reviews_uses_lowercase_paged_url():
    # camelCase sortBy on the paged URL silently returns an empty feed (live-probed)
    cl = FakeRssClient({"feed": {"entry": [_ios_entry()]}})
    out = fetch_itunes_reviews(cl, "1095459556")
    assert len(out) == 1
    (url,) = cl.urls
    assert url == ("https://itunes.apple.com/us/rss/customerreviews/"
                   "page=1/id=1095459556/sortby=mostrecent/json")
    assert "sortBy" not in url


def test_fetch_itunes_reviews_trims_to_limit():
    entries = [_ios_entry(rid=str(i)) for i in range(30)]
    cl = FakeRssClient({"feed": {"entry": entries}})
    assert len(fetch_itunes_reviews(cl, "1", limit=20)) == 20


def test_fetch_itunes_reviews_non_json_degrades_to_empty():
    assert fetch_itunes_reviews(FakeRssClient(None), "idBAD") == []


# --- Android parsing ---------------------------------------------------------------
def test_parse_gplay_reviews_maps_sidecar_rows():
    rows = [{"id": "gp:1", "userName": "A", "score": 4, "title": None,
             "text": "solid", "date": "2026-06-02T10:00:00.000Z"}]
    (r,) = parse_gplay_reviews(rows)
    assert r.review_id == "gp:1" and r.author == "A" and r.rating == 4
    assert r.content == "solid" and r.title is None
    assert r.reviewed_at == datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)


def test_parse_gplay_reviews_skips_error_and_idless_rows():
    rows = [{"appId": "com.x", "error": "App not found"},
            {"userName": "no-id"},
            {"id": "gp:2", "userName": "B", "score": 5, "text": "ok", "date": None}]
    assert [r.review_id for r in parse_gplay_reviews(rows)] == ["gp:2"]


def test_fetch_gplay_reviews_runner_and_trim():
    rows = [{"id": f"gp:{i}", "userName": "U", "score": 5, "text": "x",
             "date": "2026-06-01T00:00:00.000Z"} for i in range(25)]
    calls = []

    def runner(package_id, num):
        calls.append((package_id, num))
        return json.dumps(rows)

    out = fetch_gplay_reviews("com.gem.app", num=20, runner=runner)
    assert calls == [("com.gem.app", 20)]
    assert len(out) == 20


def test_fetch_gplay_reviews_malformed_degrades_to_empty():
    assert fetch_gplay_reviews("com.x", runner=lambda p, n: "<<boom>>") == []
    assert fetch_gplay_reviews("com.x", runner=lambda p, n: "") == []
    assert fetch_gplay_reviews("com.x", runner=lambda p, n: '{"error": "x"}') == []


# --- DB upsert ----------------------------------------------------------------------
def test_upsert_app_reviews_idempotent_and_updating(pg_conn):
    app_id = repo.upsert_app(pg_conn, {"store": "ios", "store_id": "rev-sentinel-1",
                                       "name": "RevApp"})
    when = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    reviews = [ReviewRecord("r1", "A", 5, "t", "c", when),
               ReviewRecord("r2", "B", 3, "t2", "c2", when)]
    assert repo.upsert_app_reviews(pg_conn, app_id, reviews) == 2
    # same PK again, content updated -> still 2 rows, content reflects the re-fetch
    reviews[0] = ReviewRecord("r1", "A", 5, "t", "c EDITED", when)
    assert repo.upsert_app_reviews(pg_conn, app_id, reviews) == 2
    rows = pg_conn.execute(text(
        "SELECT review_id, content FROM public.app_reviews WHERE app_id=:a "
        "ORDER BY review_id"), {"a": app_id}).all()
    assert [(r.review_id, r.content) for r in rows] == [("r1", "c EDITED"), ("r2", "c2")]


def test_upsert_app_reviews_skips_idless_records(pg_conn):
    app_id = repo.upsert_app(pg_conn, {"store": "ios", "store_id": "rev-sentinel-2",
                                       "name": "RevApp2"})
    bad = ReviewRecord("", "A", 5, None, None, None)
    assert repo.upsert_app_reviews(pg_conn, app_id, [bad]) == 0
