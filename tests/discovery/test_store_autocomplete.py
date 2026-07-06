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
