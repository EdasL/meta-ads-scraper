import json

from sqlalchemy import text
from agf.enrich_full.appstore import (fetch_itunes_new_releases, gplay_developer,
                                      gplay_similar, lookup_itunes_batch,
                                      parse_itunes_new_releases,
                                      resolve_pending_app_metadata)


def test_gplay_similar_parses_to_release_shape():
    def runner(cmd, arg):
        assert cmd == "similar" and arg == "com.seed"
        return json.dumps([
            {"appId": "com.a", "title": "App A", "genre": "Health & Fitness"},
            {"appId": "com.b", "title": "App B", "genre": "Education"},
            {"error": "boom"}])                       # error rows dropped
    assert gplay_similar("com.seed", runner=runner) == [
        {"store_id": "com.a", "name": "App A", "genre": "Health & Fitness"},
        {"store_id": "com.b", "name": "App B", "genre": "Education"}]


def test_gplay_developer_passes_cmd_and_degrades_on_bad_json():
    def runner(cmd, arg):
        assert cmd == "developer" and arg == "DevCo"
        return "not json at all"
    assert gplay_developer("DevCo", runner=runner) == []


def _entry(im_id, name, genre="Health & Fitness"):
    return {"im:name": {"label": name},
            "id": {"label": f"https://apps.apple.com/app/id{im_id}",
                   "attributes": {"im:id": str(im_id), "im:bundleId": "com.x"}},
            "category": {"attributes": {"im:id": "6013", "term": genre, "label": genre}}}


def test_parse_new_releases_extracts_id_name_genre():
    payload = {"feed": {"entry": [_entry(111, "Calorie Counter"),
                                  _entry(222, "Sleep Tracker", "Health & Fitness")]}}
    rows = parse_itunes_new_releases(payload)
    assert rows == [
        {"store_id": "111", "name": "Calorie Counter", "genre": "Health & Fitness"},
        {"store_id": "222", "name": "Sleep Tracker", "genre": "Health & Fitness"}]


def test_parse_new_releases_handles_single_entry_and_empty():
    # a one-app feed arrives as a dict, not a list
    one = {"feed": {"entry": _entry(333, "Solo App")}}
    assert parse_itunes_new_releases(one) == [
        {"store_id": "333", "name": "Solo App", "genre": "Health & Fitness"}]
    assert parse_itunes_new_releases({"feed": {}}) == []
    assert parse_itunes_new_releases({}) == []


class _FakeRSS:
    """Records the requested URL; returns a fixed new-releases feed."""
    def __init__(self):
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        return _Resp({"feed": {"entry": [_entry(111, "Calorie Counter")]}})


def test_fetch_new_releases_builds_genre_url_and_parses():
    cl = _FakeRSS()
    rows = fetch_itunes_new_releases(cl, country="us", genre="6013", limit=100)
    assert cl.urls == [
        "https://itunes.apple.com/us/rss/newapplications/limit=100/genre=6013/json"]
    assert rows[0]["store_id"] == "111"


class FakeITunes:
    """Echoes back an iTunes result per requested id; records each GET's id-param."""
    def __init__(self):
        self.calls = []

    def get(self, url, params=None):
        ids = (params or {}).get("id", "").split(",")
        self.calls.append(ids)
        results = [{"trackId": int(i), "trackName": f"App {i}", "sellerName": "Dev",
                    "primaryGenreName": "Health & Fitness",
                    "releaseDate": "2026-05-20T07:00:00Z",
                    "userRatingCount": 123, "averageUserRating": 4.5,
                    "artworkUrl512": f"https://icons/{i}.png",
                    "screenshotUrls": [f"https://shots/{i}-1.png"],
                    "formattedPrice": "Free"} for i in ids if i]
        return _Resp({"resultCount": len(results), "results": results})


class _Resp:
    def __init__(self, payload): self._p = payload
    def json(self): return self._p


class GenreITunes:
    """iTunes fake with per-id genre fields (for the is_game mapping). Ids absent from
    `by_id` get a minimal result with NO genre info at all."""
    def __init__(self, by_id):
        self.by_id = by_id

    def get(self, url, params=None):
        ids = [i for i in (params or {}).get("id", "").split(",") if i]
        results = []
        for i in ids:
            r = {"trackId": int(i), "trackName": f"App {i}", "sellerName": "Dev",
                 "releaseDate": "2026-05-20T07:00:00Z",
                 "userRatingCount": 1, "averageUserRating": 4.0}
            r.update(self.by_id.get(i, {}))
            results.append(r)
        return _Resp({"resultCount": len(results), "results": results})


def test_batches_in_chunks_and_keys_by_track_id():
    cl = FakeITunes()
    out = lookup_itunes_batch(cl, ["111", "222", "333"], chunk=2)
    assert len(cl.calls) == 2                     # 3 ids, chunk 2 -> 2 GETs
    assert cl.calls[0] == ["111", "222"] and cl.calls[1] == ["333"]
    assert set(out) == {"111", "222", "333"}
    assert out["111"].name == "App 111"
    assert out["111"].release_date == "2026-05-20T07:00:00Z"   # releaseDate, not currentVersion
    assert out["222"].rating_count == 123


def test_empty_ids_no_calls():
    cl = FakeITunes()
    assert lookup_itunes_batch(cl, []) == {}
    assert cl.calls == []


def test_batch_maps_preview_fields():
    out = lookup_itunes_batch(FakeITunes(), ["111"])
    m = out["111"]
    assert m.icon_url == "https://icons/111.png"
    assert m.screenshots == ["https://shots/111-1.png"]
    assert m.price_label == "Free"


def test_batch_pause_between_chunks_not_before_first():
    slept = []
    lookup_itunes_batch(FakeITunes(), ["1", "2", "3"], chunk=2,
                        pause=3.0, sleep=slept.append)
    assert slept == [3.0]                      # 2 chunks -> exactly 1 pause


def test_ios_is_game_from_primary_genre_and_genre_ids():
    out = lookup_itunes_batch(GenreITunes({
        "1": {"primaryGenreName": "Games", "genreIds": ["7014", "6014"]},
        "2": {"primaryGenreName": "Health & Fitness", "genreIds": ["6013"]},
        "3": {"primaryGenreName": "Productivity", "genreIds": [6014, 6007]},  # int ids
        "4": {"primaryGenreName": "Education"},                               # no genreIds
    }), ["1", "2", "3", "4"])
    assert out["1"].is_game is True
    assert out["2"].is_game is False
    assert out["3"].is_game is True            # 6014 present even as int -> game
    assert out["4"].is_game is False           # authoritative non-game primary genre


def test_ios_is_game_none_without_any_genre_info():
    out = lookup_itunes_batch(GenreITunes({}), ["9"])
    assert out["9"].is_game is None            # unknown stays unknown — never guessed


def test_resolve_pending_app_metadata_fills_null_name_apps(pg_conn):
    # discovery's app-linking creates apps with NULL name; resolution fills them in so they
    # are interpretable AND feed genre/release/ratings into scoring (no Meta traffic).
    aid = pg_conn.execute(text(
        "INSERT INTO public.apps (store, store_id) VALUES ('ios','7771') RETURNING id")).scalar()
    n = resolve_pending_app_metadata(pg_conn, FakeITunes())
    assert n >= 1
    row = pg_conn.execute(text(
        "SELECT name, genre, rating_count, icon_url, screenshots, price_label "
        "FROM public.apps WHERE id=:i"), {"i": aid}).first()
    assert row.name == "App 7771"
    assert row.genre == "Health & Fitness"
    assert row.rating_count == 123
    assert row.icon_url == "https://icons/7771.png"        # preview set lands on first contact
    assert row.screenshots == ["https://shots/7771-1.png"]
    assert row.price_label == "Free"


def test_resolve_pending_app_metadata_writes_is_game(pg_conn):
    pg_conn.execute(text(
        "INSERT INTO public.apps (store, store_id) VALUES ('ios','5551'), ('ios','5552')"))
    resolve_pending_app_metadata(pg_conn, GenreITunes({
        "5551": {"primaryGenreName": "Games", "genreIds": ["6014"]},
        "5552": {"primaryGenreName": "Health & Fitness", "genreIds": ["6013"]}}))
    got = dict(pg_conn.execute(text(
        "SELECT store_id, is_game FROM public.apps WHERE store_id IN ('5551','5552')")).all())
    assert got == {"5551": True, "5552": False}


def test_resolve_pending_app_metadata_unknown_genre_leaves_is_game_null(pg_conn):
    pg_conn.execute(text(
        "INSERT INTO public.apps (store, store_id) VALUES ('ios','5553')"))
    resolve_pending_app_metadata(pg_conn, GenreITunes({}))   # result w/o genre fields
    assert pg_conn.execute(text(
        "SELECT is_game FROM public.apps WHERE store_id='5553'")).scalar() is None


def test_resolve_pending_app_metadata_noop_when_all_named(pg_conn):
    pg_conn.execute(text(
        "INSERT INTO public.apps (store, store_id, name) VALUES ('ios','8881','Named')"))
    # no NULL-name ios/android apps inserted here → the lookup client is never needed
    before = pg_conn.execute(text("SELECT count(*) FROM public.apps WHERE name IS NULL")).scalar()
    if before == 0:
        assert resolve_pending_app_metadata(pg_conn, FakeITunes()) == 0
