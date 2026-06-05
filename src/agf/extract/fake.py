"""In-process, zero-network stand-in for the Plan 2 Extractor facade (§3.4).

Promoted from tests/discovery/conftest so the production image can import it: the
live worker swaps this in when AGF_DRY_RUN=1 to exercise the warm→discover→score
→enrich loop end-to-end WITHOUT making a single Meta request. Same async surface
as `agf.extract.facade.Extractor` (search / ad_details / resolve_deeplink)."""
from dataclasses import dataclass, field

from agf.parse import AdRecord
from agf.classify import classify_url
from agf.extract.deeplink import ResolvedLink

_DAY = 86_400


@dataclass
class FakeExtractor:
    eu_ads: list = field(default_factory=list)        # sort_by_impressions sweeps
    page_ads: dict = field(default_factory=dict)      # view_all_page_id -> [AdRecord]
    keyword_ads: dict = field(default_factory=dict)   # query -> [AdRecord]
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

    async def ad_details(self, ad_archive_id):
        raise NotImplementedError("FakeExtractor has no ad_details")

    async def resolve_deeplink(self, url, ua="ios"):
        return self.links.get(url, ResolvedLink(url, classify_url(url)))

    @classmethod
    def demo(cls, now_epoch: int) -> "FakeExtractor":
        """A small coherent fixture: two EU advertiser pages whose US ads are old
        enough to pass the 25-day firehose floor and the 14-day scaling confirm, one
        resolving to an iOS app (app candidate) — so a dry-run discovers + confirms
        both pages and the enrich path has store links to resolve."""
        def ad(archive, pid, name, link, days_old):
            return AdRecord(archive, pid, name, True, now_epoch - days_old * _DAY,
                            None, 3, "INSTALL_MOBILE_APP", "Install", link, "body")
        eu = [
            ad("fake-eu-1", "FAKE1", "Demo Sleep", "https://demo-sleep.example", 40),
            ad("fake-eu-2", "FAKE2", "Demo Tutor", "https://demo-tutor.example", 33),
        ]
        page_ads = {
            "FAKE1": [ad("fake-us-1", "FAKE1", "Demo Sleep",
                         "https://apps.apple.com/us/app/x/id444000", 30)],
            "FAKE2": [ad("fake-us-2", "FAKE2", "Demo Tutor",
                         "https://apps.apple.com/us/app/x/id555000", 28)],
        }
        return cls(eu_ads=eu, page_ads=page_ads)
