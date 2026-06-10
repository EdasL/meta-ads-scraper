from sqlalchemy import text
from agf.enrich_full.appstore import lookup_itunes_batch, resolve_pending_app_metadata


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


def test_resolve_pending_app_metadata_noop_when_all_named(pg_conn):
    pg_conn.execute(text(
        "INSERT INTO public.apps (store, store_id, name) VALUES ('ios','8881','Named')"))
    # no NULL-name ios/android apps inserted here → the lookup client is never needed
    before = pg_conn.execute(text("SELECT count(*) FROM public.apps WHERE name IS NULL")).scalar()
    if before == 0:
        assert resolve_pending_app_metadata(pg_conn, FakeITunes()) == 0
