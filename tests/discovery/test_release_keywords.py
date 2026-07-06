"""Taste-blind Gemini keyword proposer: prompt shaping, JSON parse, normalization, and
error isolation. Gemini is injected (_post) — zero network."""
import pytest

from agf.discovery.release_keywords import ProposeError, propose_release_keywords


def _gemini_reply(phrases):
    return {"candidates": [{"content": {"parts": [
        {"text": '{"phrases": %s}' % __import__("json").dumps(phrases)}]}}]}


def test_proposes_and_normalizes_phrases():
    captured = {}

    def fake_post(url, body):
        captured["url"] = url
        captured["prompt"] = body["contents"][0]["parts"][0]["text"]
        return _gemini_reply(["Intermittent Fasting App", "meal timing app",
                              "fasting", "meal  timing  app"])  # brand-case, single, dup

    out = propose_release_keywords(
        "Health & Fitness",
        [{"name": "Fastic", "description": "intermittent fasting timer"}],
        n=5, api_key="k", _post=fake_post)
    # lowercased, single-word "fasting" dropped, dedup collapsed
    assert out == ["intermittent fasting app", "meal timing app"]
    # prompt is taste-blind: names the category + example, never "liked"/"preference"
    assert "Health & Fitness" in captured["prompt"]
    assert "Fastic" in captured["prompt"]
    assert "liked" not in captured["prompt"].lower()


def test_limit_respected():
    def fake_post(url, body):
        return _gemini_reply([f"niche {i} app" for i in range(10)])
    out = propose_release_keywords("Finance", [{"name": "X", "description": "y"}],
                                   n=3, api_key="k", _post=fake_post)
    assert len(out) == 3


def test_empty_inputs_return_empty_without_calling():
    def boom(url, body):
        raise AssertionError("should not call Gemini")
    assert propose_release_keywords("X", [], n=5, api_key="k", _post=boom) == []
    assert propose_release_keywords("X", [{"name": "a"}], n=0, api_key="k", _post=boom) == []


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ProposeError):
        propose_release_keywords("X", [{"name": "a"}], n=5, api_key="")


def test_unparseable_reply_raises():
    def bad_post(url, body):
        return {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}
    with pytest.raises(ProposeError):
        propose_release_keywords("X", [{"name": "a"}], n=5, api_key="k", _post=bad_post)


from agf.discovery.release_keywords import brainstorm_niche_keywords


def test_brainstorm_returns_normalized_phrases():
    def fake_post(url, body):
        # prompt must mention the category and an example listing name
        prompt = body["contents"][0]["parts"][0]["text"]
        assert "Sleep" in prompt or "sleep" in prompt
        assert "DreamTrack" in prompt
        return {"candidates": [{"content": {"parts": [
            {"text": '{"phrases": ["lucid dreaming app", "snore detection app"]}'}]}}]}

    out = brainstorm_niche_keywords(
        "Health & Fitness",
        [{"name": "DreamTrack", "description": "sleep cycle tracker"}],
        5, api_key="k", _post=fake_post)
    assert out == ["lucid dreaming app", "snore detection app"]


from agf.discovery.release_keywords import generalize_autocomplete_keywords


def test_generalize_converts_app_specific_queries_to_niches():
    # The 2026-07-06 live finding: raw autocomplete is mostly app/brand names.
    def fake_post(url, body):
        prompt = body["contents"][0]["parts"][0]["text"]
        assert "capcut: photo & video editor" in prompt
        assert "Photo & Video" in prompt
        return _gemini_reply(["video editing app", "photo collage app"])

    out = generalize_autocomplete_keywords(
        "Photo & Video", ["capcut: photo & video editor", "photo collage maker"],
        5, api_key="k", _post=fake_post)
    assert out == ["video editing app", "photo collage app"]


def test_generalize_empty_inputs_return_empty_without_calling():
    def boom(url, body):
        raise AssertionError("should not call Gemini")
    assert generalize_autocomplete_keywords("X", [], 5, api_key="k", _post=boom) == []
    assert generalize_autocomplete_keywords("X", ["a b"], 0, api_key="k", _post=boom) == []


def test_generalize_bad_reply_raises():
    def bad_post(url, body):
        return {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}
    with pytest.raises(ProposeError):
        generalize_autocomplete_keywords("X", ["a b"], 5, api_key="k", _post=bad_post)


def test_brainstorm_no_key_raises(monkeypatch):
    import pytest
    from agf.discovery.release_keywords import ProposeError
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ProposeError):
        brainstorm_niche_keywords("Health & Fitness", [{"name": "X", "description": "y"}],
                                  3, api_key=None)
