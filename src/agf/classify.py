import re
from dataclasses import dataclass
from urllib.parse import urlparse, parse_qs, unquote

_IOS_ID = re.compile(r"/id(\d+)")
_FB_SHIMS = {"l.facebook.com", "lm.facebook.com"}


@dataclass(frozen=True)
class AppIntent:
    kind: str          # "ios" | "android" | "web" | "none"
    store_id: str | None


def _unwrap(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc in _FB_SHIMS:
        u = parse_qs(parsed.query).get("u", [None])[0]
        if u:
            return unquote(u)
    return url


def classify_url(url: str | None) -> AppIntent:
    """Classify a destination URL as ios/android/web/none and extract store id."""
    if not url:
        return AppIntent("none", None)
    url = _unwrap(url)
    host = urlparse(url).netloc.lower()
    if host in {"apps.apple.com", "itunes.apple.com"}:
        m = _IOS_ID.search(urlparse(url).path)
        return AppIntent("ios", m.group(1) if m else None)
    if host in {"play.google.com"}:
        pkg = parse_qs(urlparse(url).query).get("id", [None])[0]
        return AppIntent("android", pkg)
    return AppIntent("web", None)
