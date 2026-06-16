from dataclasses import dataclass, field
from agf.parse import AdRecord
from agf.classify import classify_url
from agf.extract.deeplink import ResolvedLink

# epoch helpers for deterministic longevity in tests
NOW_EPOCH = 1_749_000_000           # ~2026-06-04
DAY = 86_400


def make_ad(archive, page_id, page_name, link_url, *, days_old=30, active=True):
    """An AdRecord whose start_date is `days_old` days before NOW_EPOCH."""
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

    async def search(self, *, query="", countries=("US",), active_status="ACTIVE",
                     sort_by_impressions=False, search_type="KEYWORD_UNORDERED",
                     view_all_page_id=None, max_pages=10):
        if view_all_page_id is not None:
            return list(self.page_ads.get(view_all_page_id, []))
        if sort_by_impressions:
            return list(self.eu_ads)
        return list(self.keyword_ads.get(query, self.default_ads))

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
