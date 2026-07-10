import pytest
from agf.extract.backoff import classify_error, with_backoff, raise_for_fb_error, classify_exception
from agf.extract.errors import ErrorKind, ExtractError


def _curl_exc(name):
    """A stand-in for a curl_cffi transport exception (module-tagged, no curl_cffi import)."""
    exc_type = type(name, (Exception,), {})
    exc_type.__module__ = "curl_cffi.requests.exceptions"
    return exc_type("boom")


def test_classify_exception_curl_network_is_transient():
    assert classify_exception(_curl_exc("Timeout")) is ErrorKind.TRANSIENT
    assert classify_exception(_curl_exc("ConnectionError")) is ErrorKind.TRANSIENT


def test_classify_exception_passes_through_non_transport():
    # a real programming error must NOT be swallowed as retryable
    assert classify_exception(ValueError("a bug")) is None
    assert classify_exception(KeyError("k")) is None


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


# --- throttle detection: the GraphQL search returns 200 with an errors[] body ---

def test_raise_for_fb_error_rate_limit():
    body = {"errors": [{"message": "Rate limit exceeded", "severity": "CRITICAL",
                        "code": 1675004}], "extensions": {"is_final": True}}
    with pytest.raises(ExtractError) as ei:
        raise_for_fb_error(body)
    assert ei.value.kind is ErrorKind.RATE_LIMIT


def test_raise_for_fb_error_session_code():
    with pytest.raises(ExtractError) as ei:
        raise_for_fb_error({"errors": [{"message": "bad session", "code": 1357004}]})
    assert ei.value.kind is ErrorKind.SESSION


def test_raise_for_fb_error_passes_clean_response():
    # a normal data payload must NOT raise
    raise_for_fb_error({"data": {"ad_library_main": {"search_results_connection": {}}}})


def test_raise_for_fb_error_ignores_non_dict():
    raise_for_fb_error(None)
    raise_for_fb_error("for (;;);")


# --- with_backoff on_retry hook (used to rotate the proxy between attempts) ---

def test_with_backoff_calls_on_retry_before_each_retry():
    seen = []

    def fail_then_ok():
        if len(seen) < 2:
            raise ExtractError("throttled", ErrorKind.RATE_LIMIT)
        return "ok"

    def on_retry(err, attempt):
        seen.append((err.kind, attempt))

    out = with_backoff(fail_then_ok, max_attempts=5, base=0.0,
                       sleep=lambda s: None, on_retry=on_retry)
    assert out == "ok"
    assert seen == [(ErrorKind.RATE_LIMIT, 0), (ErrorKind.RATE_LIMIT, 1)]


def test_with_backoff_on_retry_not_called_on_fatal():
    seen = []

    def fatal():
        raise ExtractError("fatal", ErrorKind.FATAL)

    with pytest.raises(ExtractError):
        with_backoff(fatal, max_attempts=3, base=0.0, sleep=lambda s: None,
                     on_retry=lambda e, a: seen.append(a))
    assert seen == []  # fatal isn't retried, so no rotation happens


def test_classify_exception_json_decode_is_session():
    # Meta answered 200 with a non-JSON body (challenge page / empty throttle response):
    # the SESSION/IP is burned, not the code — rotate + retry. Raw JSONDecodeError used to
    # bypass the whole ExtractError machinery and crash the calling stage (first live
    # generic-deep pass, 2026-07-10). Plain ValueError must STAY unclassified (real bug).
    import json
    with pytest.raises(json.JSONDecodeError):
        json.loads("<html>challenge</html>")
    try:
        json.loads("")
    except json.JSONDecodeError as exc:
        assert classify_exception(exc) is ErrorKind.SESSION
    assert classify_exception(ValueError("a bug")) is None
