"""Store autocomplete: Apple-hints + GPlay-suggest parsing (fake clients, no network)."""
import plistlib
from agf.discovery.store_autocomplete import apple_hints


class _Resp:
    def __init__(self, content): self.content = content

class _Client:
    def __init__(self, content): self._c = content
    def get(self, url, headers=None): return _Resp(self._c)


def _plist(terms):
    return plistlib.dumps({"hints": [{"term": t} for t in terms]})


def test_apple_hints_parses_terms():
    client = _Client(_plist(["sleep app", "sleep tracker app"]))
    assert apple_hints(client, "sleep") == ["sleep app", "sleep tracker app"]


def test_apple_hints_bad_payload_returns_empty():
    client = _Client(b"not a plist")
    assert apple_hints(client, "sleep") == []


from agf.enrich_full.appstore import gplay_suggest


def test_gplay_suggest_parses_runner_json():
    runner = lambda term: '["sleep sounds", "sleep tracker", "sleep number"]'
    assert gplay_suggest("sleep", runner=runner) == ["sleep sounds", "sleep tracker", "sleep number"]


def test_gplay_suggest_bad_json_returns_empty():
    assert gplay_suggest("sleep", runner=lambda term: "boom") == []


from agf.discovery.store_autocomplete import gather_autocomplete_candidates


def test_gather_unions_apple_and_gplay_normalized_and_deduped():
    apple = lambda client, term: ["Sleep App", "meditation timer app"]
    gplay = lambda term, **kw: ["sleep app", "white noise app"]  # 'sleep app' dup vs Apple
    out = gather_autocomplete_candidates(
        None, categories=["Health & Fitness"], seeds_per_category=1,
        apple=apple, gplay=gplay)
    got = set(out["Health & Fitness"])
    assert "sleep app" in got and "white noise app" in got and "meditation timer app" in got
    # normalized (lowercased) and deduped: 'Sleep App' and 'sleep app' collapse to one
    assert sum(1 for p in out["Health & Fitness"] if p == "sleep app") == 1
