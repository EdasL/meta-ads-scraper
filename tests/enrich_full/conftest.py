from agf.classify import classify_url
from agf.extract.deeplink import ResolvedLink


class FakeExtractor:
    """Async stand-in for the Plan 2 facade (resolve_deeplink + search)."""
    def __init__(self, links=None, search_results=None):
        self.links = links or {}                     # url -> ResolvedLink
        self.search_results = search_results or {}    # view_all_page_id -> [AdRecord]
        self.resolved = []

    async def resolve_deeplink(self, url, ua="ios"):
        self.resolved.append(url)
        return self.links.get(url, ResolvedLink(url, classify_url(url)))

    async def search(self, *, query="", countries=("US",), active_status="ACTIVE",
                     sort_by_impressions=False, search_type="KEYWORD_UNORDERED",
                     view_all_page_id=None, max_pages=10):
        return list(self.search_results.get(view_all_page_id, []))
