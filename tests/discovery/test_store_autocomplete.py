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


from agf.enrich_full.appstore import gplay_suggest, gplay_suggest_batch


def test_gplay_suggest_parses_runner_json():
    runner = lambda term: '["sleep sounds", "sleep tracker", "sleep number"]'
    assert gplay_suggest("sleep", runner=runner) == ["sleep sounds", "sleep tracker", "sleep number"]


def test_gplay_suggest_bad_json_returns_empty():
    assert gplay_suggest("sleep", runner=lambda term: "boom") == []


def test_gplay_suggest_batch_parses_runner_json():
    runner = lambda terms: '{"sleep": ["sleep sounds"], "diet": ["diet plan", "diet app"]}'
    assert gplay_suggest_batch(["sleep", "diet"], runner=runner) == {
        "sleep": ["sleep sounds"], "diet": ["diet plan", "diet app"]}


def test_gplay_suggest_batch_bad_json_returns_empty():
    assert gplay_suggest_batch(["sleep"], runner=lambda terms: "boom") == {}
    assert gplay_suggest_batch(["sleep"], runner=lambda terms: '["not","a","dict"]') == {}
    assert gplay_suggest_batch([], runner=lambda terms: 1 / 0) == {}  # no terms, no spawn


from agf.discovery.store_autocomplete import gather_autocomplete_candidates


def test_gather_unions_apple_and_gplay_normalized_and_deduped():
    apple = lambda client, term: ["Sleep App", "meditation timer app"]
    # batch shape: one call, {seed: suggestions}; 'sleep app' dup vs Apple
    gplay_batch = lambda terms, **kw: {t: ["sleep app", "white noise app"] for t in terms}
    out = gather_autocomplete_candidates(
        None, categories=["Health & Fitness"], seeds_per_category=1,
        apple=apple, gplay_batch=gplay_batch)
    got = set(out["Health & Fitness"])
    assert "sleep app" in got and "white noise app" in got and "meditation timer app" in got
    # normalized (lowercased) and deduped: 'Sleep App' and 'sleep app' collapse to one
    assert sum(1 for p in out["Health & Fitness"] if p == "sleep app") == 1


def test_gather_calls_gplay_batch_once_for_all_seeds():
    # The whole point of the batch sidecar: ONE process for the run, not one per seed.
    calls = []

    def gplay_batch(terms, **kw):
        calls.append(list(terms))
        return {}

    out = gather_autocomplete_candidates(
        None, categories=["Health & Fitness", "Dating"], seeds_per_category=3,
        apple=lambda client, term: [f"{term} tracker app"], gplay_batch=gplay_batch)
    assert len(calls) == 1                     # one spawn per run
    assert "health & fitness" in calls[0] and "dating" in calls[0]
    assert set(out) == {"Health & Fitness", "Dating"}
