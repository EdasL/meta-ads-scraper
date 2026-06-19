import os

from agf.extract.errors import ErrorKind, ExtractError
from agf.extract.rd_challenge import solve_rd_challenge, extract_challenge_path

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_LIBRARY = "https://www.facebook.com/ads/library/"
_GRAPHQL = "https://www.facebook.com/api/graphql/"


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def session_timeout() -> tuple[float, float]:
    """The (connect, read) socket timeout for the curl_cffi Meta session.

    curl_cffi maps a tuple to CONNECTTIMEOUT + a total (connect+read) cap, so a dead or
    stalled DataImpulse proxy raises curl_cffi.Timeout instead of blocking the sweep — the
    facade's backoff then rotates to a fresh IP (classify_exception treats Timeout as
    TRANSIENT). Defaults keep the historical 30s total (10 connect + 20 read) but add an
    explicit connect bound; both are env-tunable so a degraded proxy can be made to fail
    fast (AGF_META_CONNECT_TIMEOUT / AGF_META_READ_TIMEOUT)."""
    return (_env_float("AGF_META_CONNECT_TIMEOUT", 10.0),
            _env_float("AGF_META_READ_TIMEOUT", 20.0))


def build_session_headers() -> dict:
    """Browser-shaped headers to accompany the curl_cffi Chrome TLS fingerprint."""
    return {
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }


def _new_curl_transport(proxy_dict: dict | None):
    """Build a real curl_cffi Session impersonating Chrome (TLS/JA3)."""
    from curl_cffi import requests as cffi  # lazy so unit tests need no network
    return cffi.Session(impersonate="chrome", timeout=session_timeout(),
                        headers=build_session_headers(), proxies=proxy_dict)


class AdLibrarySession:
    """Thin wrapper over a curl_cffi Session: warms past rd_challenge and exposes
    `.get`/`.post`. `transport` is injectable for unit tests (duck-typed)."""

    LIBRARY = _LIBRARY
    GRAPHQL = _GRAPHQL

    def __init__(self, *, transport=None, proxy: str | None = None,
                 proxy_dict: dict | None = None):
        self.proxy = proxy
        self.transport = transport if transport is not None else _new_curl_transport(proxy_dict)

    @property
    def cookies(self):
        return getattr(self.transport, "cookies", {})

    def get(self, url, **kw):
        return self.transport.get(url, **kw)

    def post(self, url, data=None, headers=None, **kw):
        return self.transport.post(url, data=data, headers=headers, **kw)

    def warm_get(self, url: str, *, max_solves: int = 2) -> str:
        """GET `url`, solving rd_challenge as needed; return the real page HTML."""
        resp = self.get(url)
        for _ in range(max_solves + 1):
            if resp.status_code != 403 and extract_challenge_path(resp.text) is None:
                return resp.text
            if not solve_rd_challenge(self.transport, resp.text):
                break
            resp = self.get(url)
        raise ExtractError("rd_challenge not cleared after retries", ErrorKind.SESSION)
