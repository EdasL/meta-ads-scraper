from dataclasses import dataclass
from agf.classify import classify_url, AppIntent

_IOS_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
           "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")
_DESKTOP_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
# MMP / smart-banner wrappers that need an iOS-UA redirect to reveal the store link
_MMP_HOSTS = ("app.link", "onelink.me", "onelink.to", "apple.co", "go.link", "adj.st",
              "lnk.to", "app.adjust.com", "adjust.com", "branch.io", "bnc.lt")


@dataclass
class ResolvedLink:
    final_url: str
    intent: AppIntent


def resolve_deeplink(session, url: str, ua: str = "ios") -> ResolvedLink:
    """Resolve an ad's destination to an AppIntent. Direct store URLs need no
    network; only MMP/deeplink wrappers are followed (with an iOS UA so they serve
    the App Store branch). The redirect chain can terminate at a non-HTTP scheme
    (itms-apps://, market://) which the HTTP client rejects — fall back to the
    last good URL in that case (live-validated 2026-06-04)."""
    direct = classify_url(url)
    if direct.kind in ("ios", "android"):              # already a store link — no network
        return ResolvedLink(final_url=url, intent=direct)
    if not any(h in (url or "") for h in _MMP_HOSTS):  # plain web funnel — don't chase
        return ResolvedLink(final_url=url, intent=direct)
    headers = {"User-Agent": _IOS_UA if ua == "ios" else _DESKTOP_UA}
    try:
        resp = session.get(url, headers=headers, allow_redirects=True, max_redirects=10)
        final_url = str(getattr(resp, "url", None) or url)
    except Exception:
        final_url = url                                # scheme error → keep last good URL
    return ResolvedLink(final_url=final_url, intent=classify_url(final_url))
