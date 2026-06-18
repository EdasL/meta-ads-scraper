import urllib.error

import pytest

from agf.discovery.liked_keyword_mining import (
    Candidate, MiningError, _http_post, dedup_candidates, propose_phrases)


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._payload


def test_http_post_retries_on_429_then_succeeds():
    calls = {"n": 0}
    slept = []

    def fake_open(req):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(req.full_url, 429, "rate limit", {}, None)
        return _Resp(b'{"ok": 1}')

    out = _http_post("http://x", {}, _open=fake_open, _sleep=slept.append)
    assert out == {"ok": 1}
    assert calls["n"] == 2 and len(slept) == 1


def test_http_post_raises_mining_error_after_exhausting_retries():
    def always_429(req):
        raise urllib.error.HTTPError(req.full_url, 429, "rate limit", {}, None)

    with pytest.raises(MiningError):
        _http_post("http://x", {}, max_retries=2, _open=always_429, _sleep=lambda _d: None)


def test_dedup_is_case_insensitive():
    cands = [Candidate("AI Vocal Remover", "r", [1]),
             Candidate("stem splitter", "r", [2])]
    out = dedup_candidates(cands, existing={"ai vocal remover"})
    assert [c.phrase for c in out] == ["stem splitter"]


def test_propose_parses_gemini_json():
    def _post(url, body):
        # emulate a ResponseSchema JSON reply in the candidates text part
        return {"candidates": [{"content": {"parts": [{"text":
            '{"phrases":[{"phrase":"ai stem separation","rationale":"like VocalSplit",'
            '"inspired_by":[7953]}]}'}]}}]}
    out = propose_phrases([{"advertiser_id": 7953, "name": "VocalSplit",
                            "description": "remove vocals", "ad_copy": ["AI stems"]}],
                          api_key="k", _post=_post)
    assert len(out) == 1
    assert out[0].phrase == "ai stem separation"
    assert out[0].inspired_by == [7953]
