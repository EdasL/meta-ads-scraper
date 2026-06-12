"""Firehose C — funnel-tech fingerprinting. Find web2app/subscription funnels by their
source-code signatures: a web-checkout SDK (Solidgate/Stripe/Primer) ∩ a Meta Pixel (the
paid-acquisition tell), with an MMP smart-banner as a bonus signal. Source-code search via PublicWWW."""
import re

# Corrected by the 2026-06-12 live test (docs/research/2026-06-11-funnel-discovery-method.md §4):
# web2app funnels use WEB-checkout SDKs (Solidgate dominant), not the app-side RevenueCat/
# Superwall/Adapty. The Meta pixel loads via GTM behind a CMP, so GTM counts as the pixel tell.
SIGNATURES = {
    "checkout": ["cdn.solidgate.com", "solid-form.js", "js.stripe.com", "stripe.com/v3",
                 "primer.io", "cdn.paddle.com", "braintreegateway", "recurly",
                 "paypalobjects", "fnlfx.com", "paywalls-"],
    "pixel":    ["connect.facebook.net", "fbevents.js", "fbq(",
                 "googletagmanager.com", "gtm.js"],
    "mmp":      ["cdn.branch.io", "branch-latest.min.js", ".app.link", "onelink.me",
                 "onelink.to", "appsflyer", "adjust.com", "app.adjust", "adj.st",
                 "go.link", "sng.link"],
    "store":    ["apps.apple.com", "itunes.apple.com", "play.google.com"],
}


def detect_signatures(html: str) -> set[str]:
    """Which signature categories ('checkout'|'pixel'|'mmp'|'store') appear in the page source."""
    html = html or ""
    return {cat for cat, sigs in SIGNATURES.items() if any(s in html for s in sigs)}


def classify_funnel_intent(html: str) -> bool:
    """A page is a paid-acquisition subscription funnel when it pairs a WEB-checkout SDK
    with a Meta pixel (GTM counts). MMP/store presence is the install tell, handled separately."""
    found = detect_signatures(html)
    return "checkout" in found and "pixel" in found


_DOMAIN_RE = re.compile(r'https?://([^/"\s]+)')


def fingerprint_search(client, query: str, *, limit: int = 100) -> list[str]:
    """Query PublicWWW source-code search for a signature snippet; return matching
    domains. Free tier returns the top ~1M sites. (Live call is opt-in; the parser
    is unit-tested against a fixture.)"""
    resp = client.get("https://publicwww.com/websites/" + query + "/",
                      params={"export": "urls", "limit": limit})
    return parse_publicwww(resp.text)


def parse_publicwww(text: str) -> list[str]:
    """Extract unique domains from a PublicWWW URL export (one URL per line)."""
    seen, out = set(), []
    for line in (text or "").splitlines():
        m = _DOMAIN_RE.match(line.strip())
        host = m.group(1) if m else (line.strip() or None)
        if host and host not in seen:
            seen.add(host)
            out.append(host)
    return out
