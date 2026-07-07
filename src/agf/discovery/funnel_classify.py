"""Pure funnel-classification helpers, promoted from scripts/discover_experiment.py so the
funnel pipeline can share them. No DB, no network. Shared by BOTH pipelines since the
2026-07-07 unification (the app sweep folds its web rejects through fold_web_funnel)."""
import re
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs

_IOS_ID_RE = re.compile(r'(?:apps\.apple\.com|itunes\.apple\.com)/[^"\'<\s]*/id(\d+)', re.IGNORECASE)
_ANDROID_PKG_RE = re.compile(r'play\.google\.com/store/apps/details[^"\'<\s]*[?&]id=([A-Za-z0-9._]+)', re.IGNORECASE)


def store_ref_from_html(html: str | None) -> tuple[str, str] | None:
    """Extract the first app-store reference from rendered HTML.
    Returns ("ios", "<app_id>") or ("android", "<package>"), or None."""
    if not html:
        return None
    m = _IOS_ID_RE.search(html)
    if m:
        return ("ios", m.group(1))
    m = _ANDROID_PKG_RE.search(html)
    if m:
        return ("android", m.group(1))
    return None

from agf.classify import classify_url, unwrap_fb_shim

# Destinations that are neither an app nor a funnel — a social page or ad redirect.
SOCIAL_HOSTS = {"facebook.com", "fb.me", "m.facebook.com", "l.facebook.com", "lm.facebook.com",
                "instagram.com", "api.whatsapp.com", "wa.me", "t.co", "ad.doubleclick.net",
                "youtube.com", "youtu.be", "twitter.com", "x.com", "linktr.ee", "messenger.com"}

# MMP / attribution / store-redirect hosts. A paid ad pointing here is a store BOUNCE
# (AppsFlyer/Branch/Adjust deep-link → straight to the store), NOT a real web2app
# funnel — so it must not count toward has_funnel even though it resolves to an http(s)
# URL carrying pid=/af_ad=/a deep path. Authoritative list (was scattered across
# funnel_repair/deeplink/firehose_fingerprint). Matched by host suffix at a label
# boundary by is_store_bounce(); the matview funnel_url regex (migration 138) mirrors it.
STORE_BOUNCE_HOSTS = frozenset({
    "appstack.link", "onelink.me", "onelink.to", "app.link", "go.link", "adj.st",
    "bnc.lt", "adjust.com", "appsflyer.com", "kochava.com", "singular.net",
    "lnk.to", "apple.co",
})


def is_store_bounce(host: str | None) -> bool:
    """True when host is (or is a subdomain of) an MMP/store-redirect host — a store
    bounce, not a real funnel. Suffix match at a label boundary: 'x.onelink.me' and
    'onelink.me' match; 'notonelink.me' does not."""
    if not host:
        return False
    host = host.lower().replace("www.", "")
    return any(host == h or host.endswith("." + h) for h in STORE_BOUNCE_HOSTS)


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


def start_date_of(epoch) -> object | None:
    """Ad start epoch -> UTC date; None when missing or garbage."""
    if not epoch:
        return None
    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).date()
    except (TypeError, ValueError, OSError):
        return None


def fold_web_funnel(ad, *, keyword: str, technique: str) -> tuple[str, dict | None]:
    """Fold one SERP ad's WEB destination into a discovery.funnel_candidates row.

    Shared by the funnel sweep (whose funnel_phrase layer auto-passes funnel_signal)
    and the app sweep (where the gate really bites). The CALLER owns the no-link /
    social-host / incumbent-likes drops; this owns store-link routing, the signal
    gate, and the landing-domain check.
    Dispositions: 'candidate' (with row) | 'app_link' | 'not_funnel' | 'no_link'."""
    intent = classify_url(ad.link_url)
    if intent.kind in ("ios", "android"):
        return "app_link", None
    if not funnel_signal(ad, technique):
        return "not_funnel", None
    domain = landing_domain(ad.link_url)
    if not domain:
        return "no_link", None
    return "candidate", {
        "page_id": ad.page_id, "page_name": ad.page_name,
        "page_like_count": ad.page_like_count,
        "landing_url": ad.link_url, "landing_domain": domain,
        "funnel_host": matched_funnel_host(ad.link_url),
        "cta_type": ad.cta_type, "ad_start_date": start_date_of(ad.start_date),
        "source_keyword": keyword, "technique": technique,
        "body_text": ad.body_text,
        **parse_utms(ad.link_url),
    }
