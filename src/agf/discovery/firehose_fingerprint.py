"""Firehose C — funnel-tech fingerprinting. Find web2app/subscription funnels by their
source-code signatures: a paywall/web2app SDK ∩ a Meta Pixel (the paid-acquisition tell),
with an MMP smart-banner as a bonus signal. Source-code search via PublicWWW."""
import re

SIGNATURES = {
    "paywall": ["api.revenuecat.com", "@revenuecat/purchases-js", "cdn.superwall.me",
                "superwall", "fnlfx.com", "paywalls-", "adapty"],
    "pixel":   ["connect.facebook.net", "fbevents.js", "fbq("],
    "mmp":     ["cdn.branch.io", "branch-latest.min.js", ".app.link", "onelink.to",
                "appsflyer", "adjust.com"],
}


def detect_signatures(html: str) -> set[str]:
    """Which signature categories ('paywall'|'pixel'|'mmp') appear in the page source."""
    html = html or ""
    return {cat for cat, sigs in SIGNATURES.items() if any(s in html for s in sigs)}


def classify_funnel_intent(html: str) -> bool:
    """A page is a paid-acquisition funnel when it pairs a paywall SDK with a Meta Pixel.
    (MMP presence is a strengthening bonus, not required.)"""
    found = detect_signatures(html)
    return "paywall" in found and "pixel" in found


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
