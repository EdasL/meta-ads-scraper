import random
import time as _time
from collections.abc import Callable
from agf.extract.errors import ErrorKind, ExtractError

_RETRYABLE = {ErrorKind.RATE_LIMIT, ErrorKind.TRANSIENT, ErrorKind.SESSION}


def classify_error(*, status: int | None = None, fb_code: int | None = None) -> ErrorKind:
    """Map an HTTP status or a Facebook GraphQL error code to an ErrorKind."""
    if fb_code is not None:
        if fb_code == 1675004:
            return ErrorKind.RATE_LIMIT
        if fb_code in (1357004, 1357001):
            return ErrorKind.SESSION
        if fb_code == 368:
            return ErrorKind.TRANSIENT
        return ErrorKind.FATAL
    if status is not None:
        if status == 429:
            return ErrorKind.RATE_LIMIT
        if status == 403:
            return ErrorKind.SESSION
        if 500 <= status < 600:
            return ErrorKind.TRANSIENT
    return ErrorKind.FATAL


def raise_for_fb_error(parsed) -> None:
    """Surface a Facebook GraphQL error carried in a 200 response body.

    Meta returns HTTP 200 with an ``errors`` block when it throttles (e.g. code 1675004
    "Rate limit exceeded") — the data parser would otherwise silently yield zero ads. Raise
    a classified ExtractError so the caller can back off / rotate. No-op for clean payloads."""
    if not isinstance(parsed, dict):
        return
    errors = parsed.get("errors")
    if not errors:
        return
    first = errors[0] if isinstance(errors, list) and errors else {}
    code = first.get("code") if isinstance(first, dict) else None
    message = (first.get("message") if isinstance(first, dict) else None) or "Facebook GraphQL error"
    raise ExtractError(f"{message} (code={code})", classify_error(fb_code=code))


def with_backoff(fn: Callable, *, max_attempts: int = 5, base: float = 0.5,
                 cap: float = 30.0, sleep: Callable[[float], None] = _time.sleep,
                 on_retry: Callable[[ExtractError, int], None] | None = None):
    """Call fn(); retry retryable ExtractErrors with jittered exponential backoff.

    ``on_retry(error, attempt)`` fires before each backoff sleep (not on success or a
    non-retryable error) — used to rotate the proxy/session before the next attempt."""
    last: ExtractError | None = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except ExtractError as e:
            last = e
            if e.kind not in _RETRYABLE:
                raise
            if attempt == max_attempts - 1:
                break
            if on_retry is not None:
                on_retry(e, attempt)
            delay = min(cap, base * (2 ** attempt))
            sleep(delay + random.uniform(0, delay))
    raise last if last else ExtractError("backoff exhausted")
