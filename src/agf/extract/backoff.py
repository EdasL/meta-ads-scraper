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


def with_backoff(fn: Callable, *, max_attempts: int = 5, base: float = 0.5,
                 cap: float = 30.0, sleep: Callable[[float], None] = _time.sleep):
    """Call fn(); retry retryable ExtractErrors with jittered exponential backoff."""
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
            delay = min(cap, base * (2 ** attempt))
            sleep(delay + random.uniform(0, delay))
    raise last if last else ExtractError("backoff exhausted")
