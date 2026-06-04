import re
from dataclasses import dataclass
from agf.bootstrap import extract_lsd  # reuse Plan 1 (raises ValueError if absent)

# Known-good fallbacks, freshest first (master-plan §9). doc_id rotates ~2-4 weeks.
# 24922295957467452 was harvested live from a JS bundle on 2026-06-04.
FALLBACK_DOC_IDS = ["24922295957467452", "27201872659451053",
                    "25464068859919530", "33730956806489539"]

_FB_DTSG_RE = re.compile(r'"DTSGInit(?:ial)?Data",\[\],\{"token":"([^"]+)"')
_DOCID_BUNDLE_RE = re.compile(
    r'AdLibrarySearchPaginationQuery_facebookRelayOperation",\[\],'
    r'\(function\([^)]*\)\{a\.exports="(\d+)"'
)
_DOCID_INLINE_RE = re.compile(
    r'AdLibrarySearchPaginationQuery.{0,400}?"doc_id":"(\d+)"', re.DOTALL
)
_SCRIPT_SRC_RE = re.compile(r'<script[^>]+src="([^"]+\.js[^"]*)"')


@dataclass
class SessionTokens:
    lsd: str
    fb_dtsg: str | None
    doc_id: str


def extract_fb_dtsg(html: str) -> str | None:
    """Extract the fb_dtsg CSRF token from page HTML (None if absent)."""
    m = _FB_DTSG_RE.search(html)
    return m.group(1) if m else None


def heal_doc_id(html: str) -> str:
    """Self-heal the AdLibrarySearchPaginationQuery doc_id from inline HTML/JS;
    fall back to the freshest known-good value if neither is present."""
    m = _DOCID_BUNDLE_RE.search(html) or _DOCID_INLINE_RE.search(html)
    return m.group(1) if m else FALLBACK_DOC_IDS[0]


def heal_doc_id_from_bundles(session, html: str, *, max_bundles: int = 12) -> str:
    """Prefer inline/HTML; else fetch linked JS bundles (capped) and grep the
    AdLibrarySearchPaginationQuery relay-operation doc_id; else fall back.

    Live-validated 2026-06-04: the doc_id is NOT in the page HTML — it lives in a
    hashed JS bundle, so the warm flow must fetch bundles, not just scan the page.
    """
    inline = _DOCID_BUNDLE_RE.search(html) or _DOCID_INLINE_RE.search(html)
    if inline:
        return inline.group(1)
    for src in _SCRIPT_SRC_RE.findall(html)[:max_bundles]:
        try:
            m = _DOCID_BUNDLE_RE.search(session.get(src).text)
        except Exception:
            m = None
        if m:
            return m.group(1)
    return FALLBACK_DOC_IDS[0]


def heal_from_html(html: str, session=None) -> SessionTokens:
    """Build SessionTokens from a real (post-challenge) page. Raises ValueError if
    the LSD token is absent (i.e. the page is still a challenge stub). When a
    `session` is given, doc_id is healed via JS bundles (the reliable path)."""
    return SessionTokens(
        lsd=extract_lsd(html),               # raises ValueError if missing
        fb_dtsg=extract_fb_dtsg(html),
        doc_id=heal_doc_id_from_bundles(session, html) if session is not None else heal_doc_id(html),
    )
