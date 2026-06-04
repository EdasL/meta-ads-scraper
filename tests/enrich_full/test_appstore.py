from agf.enrich_full.appstore import lookup_itunes_batch


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
                    "userRatingCount": 123, "averageUserRating": 4.5} for i in ids if i]
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
