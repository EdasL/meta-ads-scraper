from dataclasses import dataclass, field
from agf.parse import AdRecord
from agf.classify import classify_url
from agf.extract.deeplink import ResolvedLink
from agf.extract.errors import ExtractError

# epoch helpers for deterministic longevity in tests
NOW_EPOCH = 1_749_000_000           # ~2026-06-04
DAY = 86_400


def make_ad(archive="fake-ad", page_id="", page_name="", link_url="",
            *, days_old=30, active=True):
    """An AdRecord whose start_date is `days_old` days before NOW_EPOCH.

    All positional args are keyword-defaulted so callers may pass only the
    fields they care about (the hunt tests build ads by page_id/link_url)."""
    return AdRecord(archive, page_id, page_name, active, NOW_EPOCH - days_old * DAY,
                    None, 1, "INSTALL_MOBILE_APP", "Install", link_url, "body")


@dataclass
class FakeExtractor:
    """Async stand-in for the Plan 2 Extractor facade."""
    eu_ads: list = field(default_factory=list)        # for sort_by_impressions sweeps
    page_ads: dict = field(default_factory=dict)      # view_all_page_id -> [AdRecord]
    page_truncated: dict = field(default_factory=dict)   # view_all_page_id -> bool
    keyword_ads: dict = field(default_factory=dict)   # query -> [AdRecord] (link_app)
    links: dict = field(default_factory=dict)         # url -> ResolvedLink
    default_ads: list = field(default_factory=list)
    page_scripts: dict = field(default_factory=dict)  # query -> [[AdRecord], ...] (search_page)
    error_queries: set = field(default_factory=set)   # queries that raise ExtractError

    async def search(self, *, query="", countries=("US",), active_status="ACTIVE",
                     sort_by_impressions=False, search_type="KEYWORD_UNORDERED",
                     view_all_page_id=None, max_pages=10, stats=None):
        if query in self.error_queries:
            raise ExtractError(f"fake search failure for {query!r}")
        if stats is not None:
            stats.update(pages=1, has_next=False)
        if view_all_page_id is not None:
            return list(self.page_ads.get(view_all_page_id, []))
        if sort_by_impressions:
            return list(self.eu_ads)
        return list(self.keyword_ads.get(query, self.default_ads))

    def script_pages(self, query: str, pages: list) -> "FakeExtractor":
        """Register the per-page record-lists successive search_page(query) calls return
        (page 0 first). Mirrors the production fake so the adaptive-depth sweep tests can
        drive multi-page paging. Returns self so it chains off the constructor."""
        self.page_scripts[query] = [list(p) for p in pages]
        return self

    async def search_page(self, *, query="", countries=("US",), active_status="ACTIVE",
                          sort_by_impressions=False, search_type="KEYWORD_UNORDERED",
                          view_all_page_id=None, cursor=None, stats=None):
        """Single-page fetch: (records, next_cursor, has_next). Scripted pages
        (script_pages) win; otherwise fall back to keyword_ads/default_ads as ONE page
        (has_next False) so the existing single-SERP fold tests still pass. Cursor is an
        opaque "p<idx>" token for the NEXT page index (None => page 0, None at exhaustion)."""
        if query in self.error_queries:
            raise ExtractError(f"fake search failure for {query!r}")
        if query in self.page_scripts:
            pages = self.page_scripts[query]
            idx = 0 if cursor is None else int(str(cursor)[1:])
            if idx >= len(pages):
                if stats is not None:
                    stats.update(pages=0, has_next=False)
                return [], None, False
            records = list(pages[idx])
            has_next = (idx + 1) < len(pages)
            next_cursor = f"p{idx + 1}" if has_next else None
            if stats is not None:
                stats.update(pages=1, has_next=has_next)
            return records, next_cursor, has_next
        if cursor is not None:                    # single-page fallback is already exhausted
            return [], None, False
        records = list(self.keyword_ads.get(query, self.default_ads))
        if stats is not None:
            stats.update(pages=1, has_next=False)
        return records, None, False

    async def search_listing(self, *, page_id, countries=("US",), max_pages=12):
        # delegate to self.search so subclasses that override search (e.g. to raise
        # ExtractError) still drive search_listing; truncation comes from page_truncated.
        ads = await self.search(view_all_page_id=page_id, search_type="page",
                                active_status="ALL", max_pages=max_pages)
        return list(ads), bool(self.page_truncated.get(page_id, False))

    async def ad_details(self, ad_archive_id):
        raise NotImplementedError

    async def resolve_deeplink(self, url, ua="ios"):
        return self.links.get(url, ResolvedLink(url, classify_url(url)))
