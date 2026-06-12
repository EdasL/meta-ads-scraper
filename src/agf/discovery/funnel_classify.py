"""Pure funnel-classification helpers, promoted from scripts/discover_experiment.py so the
funnel pipeline can share them. No DB, no network. The app pipeline never imports this."""
from urllib.parse import urlparse, parse_qs, unquote

# Destinations that are neither an app nor a funnel — a social page or ad redirect.
SOCIAL_HOSTS = {"facebook.com", "fb.me", "m.facebook.com", "l.facebook.com", "lm.facebook.com",
                "instagram.com", "api.whatsapp.com", "wa.me", "t.co", "ad.doubleclick.net",
                "youtube.com", "youtu.be", "twitter.com", "x.com", "linktr.ee", "messenger.com"}
# Sub-domains / infra that signal a real web2app or quiz funnel even without funnel copy.
FUNNEL_HOST_HINTS = ("quiz.", "try.", "tour.", "get.", "go.", "start.", "join.", "app.",
                     "interview.", "info.", "onelink", "adjust", "adapty", "dynamicflow",
                     "lovable.app", "typeform", "involve.me", ".health", "reverse.health")
FUNNEL_WORDS = ("quiz", "personalized", "plan", "your results", "based on your",
                "tailored", "custom", "journey", "assessment", "find your")
LEADGEN_CTAS = {"SIGN_UP", "SUBSCRIBE", "GET_OFFER", "LEARN_MORE", "GET_QUOTE",
                "BOOK_TRAVEL", "APPLY_NOW", "DOWNLOAD"}

_FB_SHIMS = {"l.facebook.com", "lm.facebook.com"}


def _unwrap(url: str) -> str:
    """Unwrap an l.facebook.com/?u=<real> click-shim to the real destination."""
    p = urlparse(url)
    if p.netloc in _FB_SHIMS:
        u = parse_qs(p.query).get("u", [None])[0]
        if u:
            return unquote(u)
    return url


def host_of(url: str | None) -> str:
    if not url:
        return ""
    return urlparse(url).netloc.lower().replace("www.", "")


def landing_domain(url: str | None) -> str | None:
    """Host of the true destination (FB shim unwrapped, www stripped). None if empty."""
    if not url:
        return None
    host = urlparse(_unwrap(url)).netloc.lower().replace("www.", "")
    return host or None


def parse_utms(url: str | None) -> dict:
    """{'utm_source','utm_medium','utm_campaign','utm_content'} from the destination query
    (FB shim unwrapped); missing keys -> None."""
    q = parse_qs(urlparse(_unwrap(url or "")).query)
    return {k: q.get(k, [None])[0]
            for k in ("utm_source", "utm_medium", "utm_campaign", "utm_content")}


def matched_funnel_host(url: str | None) -> str | None:
    """The first FUNNEL_HOST_HINT present in the (unwrapped) host, or None."""
    host = host_of(_unwrap(url or ""))
    return next((h for h in FUNNEL_HOST_HINTS if h in host), None)


def funnel_signal(ad, keyword_layer: str) -> bool:
    """True when an ad's web destination looks like a quiz/web2app funnel."""
    if keyword_layer == "funnel_phrase":
        return True
    if matched_funnel_host(ad.link_url):
        return True
    blob = ((ad.body_text or "") + " " + (ad.link_url or "")).lower()
    if any(w in blob for w in FUNNEL_WORDS):
        return True
    return (ad.cta_type or "") in LEADGEN_CTAS
