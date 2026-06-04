import pytest
from agf.extract.backoff import classify_error, with_backoff
from agf.extract.errors import ErrorKind, ExtractError


def test_classify_http_status():
    assert classify_error(status=429) is ErrorKind.RATE_LIMIT
    assert classify_error(status=503) is ErrorKind.TRANSIENT
    assert classify_error(status=403) is ErrorKind.SESSION
    assert classify_error(status=200) is ErrorKind.FATAL  # 200 but called => unknown


def test_classify_fb_error_codes():
    assert classify_error(fb_code=1675004) is ErrorKind.RATE_LIMIT
    assert classify_error(fb_code=1357004) is ErrorKind.SESSION
    assert classify_error(fb_code=1357001) is ErrorKind.SESSION
    assert classify_error(fb_code=368) is ErrorKind.TRANSIENT


def test_with_backoff_retries_then_raises():
    calls = {"n": 0}

    def always_fail():
        calls["n"] += 1
        raise ExtractError("nope", ErrorKind.TRANSIENT)

    with pytest.raises(ExtractError):
        with_backoff(always_fail, max_attempts=3, base=0.0, sleep=lambda s: None)
    assert calls["n"] == 3


def test_with_backoff_returns_on_success():
    calls = {"n": 0}

    def fail_then_ok():
        calls["n"] += 1
        if calls["n"] < 2:
            raise ExtractError("transient", ErrorKind.TRANSIENT)
        return "ok"

    assert with_backoff(fail_then_ok, max_attempts=3, base=0.0, sleep=lambda s: None) == "ok"
    assert calls["n"] == 2


def test_with_backoff_does_not_retry_fatal():
    calls = {"n": 0}

    def fatal():
        calls["n"] += 1
        raise ExtractError("fatal", ErrorKind.FATAL)

    with pytest.raises(ExtractError):
        with_backoff(fatal, max_attempts=3, base=0.0, sleep=lambda s: None)
    assert calls["n"] == 1  # fatal is not retried
