"""Unit tests for agf.net.http_backoff.post_json."""
import urllib.error

import pytest

from agf.net.http_backoff import HttpBackoffError, post_json


class _Resp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._payload


def test_post_json_retries_on_429_then_succeeds():
    calls = {"n": 0}
    slept = []

    def fake_open(req):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(req.full_url, 429, "rate limit", {}, None)
        return _Resp(b'{"ok": 1}')

    out = post_json("http://x", {}, _open=fake_open, _sleep=slept.append)
    assert out == {"ok": 1}
    assert calls["n"] == 2   # retried once
    assert len(slept) == 1   # backed off once


def test_post_json_raises_after_exhausting_max_retries():
    def always_429(req):
        raise urllib.error.HTTPError(req.full_url, 429, "rate limit", {}, None)

    with pytest.raises(HttpBackoffError):
        post_json("http://x", {}, max_retries=2, _open=always_429,
                  _sleep=lambda _d: None)
