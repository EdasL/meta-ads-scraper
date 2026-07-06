"""External keyword vocabulary from app-store search autocomplete (Apple hints + Google
Play suggest). Uncorrelated with our found-ad corpus by construction — this is Pipe 2's
breadth source. Returns normalized 'X app' style phrases. All fetches degrade to [] on
failure (per-seed isolation). See the 2026-07-06 novelty-engine spec."""
import plistlib

_APPLE_HINTS = ("https://search.itunes.apple.com/WebObjects/MZSearchHints.woa/wa/hints"
                "?clientApplication=Software&term={term}")
_APPLE_HEADERS = {"X-Apple-Store-Front": "143441-1,29", "User-Agent": "iTunes/12.11"}


def apple_hints(client, term: str) -> list[str]:
    """Apple App Store search autocomplete terms for `term`. [] on any failure."""
    from urllib.parse import quote
    try:
        resp = client.get(_APPLE_HINTS.format(term=quote(term)), headers=_APPLE_HEADERS)
        data = plistlib.loads(resp.content)
        return [h["term"] for h in data.get("hints", []) if h.get("term")]
    except Exception:
        return []
