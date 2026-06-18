import math
import urllib.error
import pytest
from agf.scoring.embeddings import embed_texts, EmbedError, _http_post


def _fake_post_factory(values):
    def _post(url, body):
        assert body["outputDimensionality"] == 768
        return {"embedding": {"values": values}}
    return _post


def test_embed_texts_normalizes():
    out = embed_texts(["hi"], api_key="k", _post=_fake_post_factory([3.0, 4.0] + [0.0] * 766))
    assert len(out) == 1
    assert math.isclose(math.sqrt(sum(x * x for x in out[0])), 1.0, rel_tol=1e-6)


def test_embed_texts_dim_mismatch_raises():
    with pytest.raises(EmbedError):
        embed_texts(["hi"], api_key="k", _post=_fake_post_factory([1.0, 2.0]))  # only 2 dims


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
    assert calls["n"] == 2          # retried once
    assert len(slept) == 1          # backed off once


def test_http_post_raises_after_exhausting_retries():
    def always_429(req):
        raise urllib.error.HTTPError(req.full_url, 429, "rate limit", {}, None)

    with pytest.raises(EmbedError):
        _http_post("http://x", {}, max_retries=2, _open=always_429, _sleep=lambda _d: None)
