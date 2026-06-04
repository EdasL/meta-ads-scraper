import asyncio
from agf.parse import AdRecord
from agf.extract.ad_details import AdDetails, fetch_ad_details
from agf.extract.deeplink import ResolvedLink, resolve_deeplink
from agf.extract.search import search_ads
from agf.extract.session_pool import SessionPool
from agf.extract.tls_client import AdLibrarySession
from agf.extract.proxy import ProxyPool


class Extractor:
    """The §3.4 facade: manages the warm-session lifecycle so Plans 4/5/6 never
    touch sessions/tokens. Sync work runs in a thread so the API stays async."""

    def __init__(self, *, pool: SessionPool | None = None, proxies: ProxyPool | None = None):
        if pool is None:
            proxies = proxies or ProxyPool.from_env()

            def make_session():
                proxy = proxies.acquire()
                return AdLibrarySession(proxy=proxy, proxy_dict=proxies.as_curl_proxies(proxy))

            pool = SessionPool(make_session=make_session)
        self._pool = pool

    async def search(self, *, query="", countries=["US"], active_status="ACTIVE",
                     sort_by_impressions=False, search_type="KEYWORD_UNORDERED",
                     view_all_page_id=None, max_pages=10) -> list[AdRecord]:
        def run():
            ws = self._pool.acquire()
            records, _cursor, _has_next = search_ads(
                ws.session, ws.tokens, query=query, countries=countries,
                active_status=active_status, sort_by_impressions=sort_by_impressions,
                search_type=search_type, view_all_page_id=view_all_page_id, max_pages=max_pages)
            return records
        return await asyncio.to_thread(run)

    async def ad_details(self, ad_archive_id) -> AdDetails:
        def run():
            ws = self._pool.acquire()
            return fetch_ad_details(ws.session, ws.tokens, ad_archive_id)
        return await asyncio.to_thread(run)

    async def resolve_deeplink(self, url, ua="ios") -> ResolvedLink:
        def run():
            ws = self._pool.acquire()
            return resolve_deeplink(ws.session, url, ua=ua)
        return await asyncio.to_thread(run)
