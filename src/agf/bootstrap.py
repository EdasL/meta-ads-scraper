import re

_LSD_RE = re.compile(r'"LSD",\[\],\{"token":"([^"]+)"')
_DOCID_RE = re.compile(
    r'AdLibrarySearchPaginationQuery.{0,400}?"doc_id":"(\d+)"', re.DOTALL
)


def extract_lsd(html: str) -> str:
    """Extract the LSD CSRF token from Ad Library page HTML."""
    m = _LSD_RE.search(html)
    if not m:
        raise ValueError("LSD token not found in page HTML")
    return m.group(1)


def extract_doc_id(html: str, fallback: str) -> str:
    """Self-heal the AdLibrarySearchPaginationQuery doc_id; fall back if absent.

    NOTE: in production (Plan 2) the doc_id often lives in a separate JS bundle,
    so the durable scraper fetches bundles too. For the PoC, the inline match
    plus a known-good fallback (live-verified 27201872659451053 on 2026-06-04)
    is sufficient.
    """
    m = _DOCID_RE.search(html)
    return m.group(1) if m else fallback
