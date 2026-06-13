"""Pure funnel-classification helpers, promoted from scripts/discover_experiment.py so the
funnel pipeline can share them. No DB, no network. The app pipeline never imports this."""
from urllib.parse import urlparse, parse_qs

from agf.classify import unwrap_fb_shim

# Destinations that are neither an app nor a funnel — a social page or ad redirect.
SOCIAL_HOSTS = {"facebook.com", "fb.me", "m.facebook.com", "l.facebook.com", "lm.facebook.com",
                "instagram.com", "api.whatsapp.com", "wa.me", "t.co", "ad.doubleclick.net",
                "youtube.com", "youtu.be", "twitter.com", "x.com", "linktr.ee", "messenger.com"}
# Sub-domains / infra that signal a real web2app or quiz funnel even without funnel copy.
# Hints ending in "." are subdomain labels and match only at a label boundary
# ("quiz.x.com", "api-quiz.x.com" — but never "lequiz.x.com"); the rest are substrings.
FUNNEL_HOST_HINTS = ("quiz.", "try.", "tour.", "get.", "go.", "start.", "join.", "app.",
                     "interview.", "info.", "signup.", "survey.", "welcome.", "my.",
                     "web.", "onelink", "adjust", "adapty", "dynamicflow",
                     "lovable.app", "typeform", "involve.me", ".health", "reverse.health")
# URL path/query fragments for funnels living on the main domain (noom.com/survey/,
# betterme-pilates.com/generated-questionary-...?flow=2117). "questionn?ary" is
# distinctive enough to match anywhere in the path; the generic words stay /-anchored.
FUNNEL_PATH_HINTS = ("/quiz", "/survey", "questionnaire", "questionary",
                     "/onboarding", "flow=")
FUNNEL_WORDS = ("quiz", "personalized", "plan", "your results", "based on your",
                "tailored", "custom", "journey", "assessment", "find your")
LEADGEN_CTAS = {"SIGN_UP", "SUBSCRIBE", "GET_OFFER", "LEARN_MORE", "GET_QUOTE",
                "BOOK_TRAVEL", "APPLY_NOW", "DOWNLOAD"}


def host_of(url: str | None) -> str:
    if not url:
        return ""
    return urlparse(url).netloc.lower().replace("www.", "")


def landing_domain(url: str | None) -> str | None:
    """Host of the true destination (FB shim unwrapped, www stripped). None if empty."""
    if not url:
        return None
    host = urlparse(unwrap_fb_shim(url)).netloc.lower().replace("www.", "")
    return host or None


def parse_utms(url: str | None) -> dict:
    """{'utm_source','utm_medium','utm_campaign','utm_content'} from the destination query
    (FB shim unwrapped); missing keys -> None."""
    q = parse_qs(urlparse(unwrap_fb_shim(url or "")).query)
    return {k: q.get(k, [None])[0]
            for k in ("utm_source", "utm_medium", "utm_campaign", "utm_content")}


def _host_hint_match(host: str, hint: str) -> bool:
    if hint.endswith(".") and not hint.startswith("."):
        # subdomain-label hint — boundary-anchored so "go." can't match "logo.com",
        # while "api-quiz.x.com" / "automation.quiz.x.com" still match "quiz."
        return host.startswith(hint) or ("." + hint) in host or ("-" + hint) in host
    return hint in host


def matched_funnel_host(url: str | None) -> str | None:
    """The first FUNNEL_HOST_HINT matching the (unwrapped) host, or None."""
    host = host_of(unwrap_fb_shim(url or ""))
    return next((h for h in FUNNEL_HOST_HINTS if _host_hint_match(host, h)), None)


def matched_funnel_path(url: str | None) -> str | None:
    """The first FUNNEL_PATH_HINT present in the (unwrapped) URL's path+query, or None."""
    parsed = urlparse(unwrap_fb_shim(url or ""))
    blob = (parsed.path + "?" + parsed.query).lower()
    return next((h for h in FUNNEL_PATH_HINTS if h in blob), None)


def funnel_signal(ad, keyword_layer: str) -> bool:
    """True when an ad's web destination looks like a quiz/web2app funnel."""
    if keyword_layer == "funnel_phrase":
        return True
    if matched_funnel_host(ad.link_url) or matched_funnel_path(ad.link_url):
        return True
    blob = ((ad.body_text or "") + " " + (ad.link_url or "")).lower()
    if any(w in blob for w in FUNNEL_WORDS):
        return True
    return (ad.cta_type or "") in LEADGEN_CTAS
