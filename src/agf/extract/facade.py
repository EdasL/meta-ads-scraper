import asyncio
import time as _time
from agf.parse import AdRecord
from agf.extract.ad_details import AdDetails, fetch_ad_details
from agf.extract.deeplink import ResolvedLink, resolve_deeplink
from agf.extract.search import search_ads
from agf.extract.session_pool import SessionPool
from agf.extract.tls_client import AdLibrarySession
from agf.extract.proxy import ProxyPool
from agf.extract.backoff import with_backoff
from agf.extract.errors import ErrorKind

# A throttle/session error means THIS IP is burned → drop the warm session so the next
# attempt re-warms on a fresh proxy (Meta rate-limits the GraphQL search by IP reputation).
_ROTATE_ON = {ErrorKind.RATE_LIMIT, ErrorKind.SESSION}


class Extractor:
    """The §3.4 facade: manages the warm-session lifecycle so Plans 4/5/6 never
    touch sessions/tokens. Sync work runs in a thread so the API stays async.

    `search` is throttle-aware: requests are paced (`search_delay` + jitter between pages),
    a rate-limit/session error triggers exponential backoff, and on each retry the session
    pool is invalidated so the next attempt egresses through a fresh proxy IP."""

    def __init__(self, *, pool: SessionPool | None = None, proxies: ProxyPool | None = None,
                 search_delay: float = 2.0, search_jitter: float = 2.0,
                 max_attempts: int = 4, backoff_base: float = 2.0, sleep=_time.sleep):
        if pool is None:
            proxies = proxies or ProxyPool.from_env()

            def make_session():
                proxy = proxies.acquire()
                return AdLibrarySession(proxy=proxy, proxy_dict=proxies.as_curl_proxies(proxy))

            pool = SessionPool(make_session=make_session)
        self._pool = pool
        self._search_delay = search_delay
        self._search_jitter = search_jitter
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base
        self._sleep = sleep

    async def search(self, *, query="", countries=["US"], active_status="ACTIVE",
                     sort_by_impressions=False, search_type="KEYWORD_UNORDERED",
                     view_all_page_id=None, max_pages=10) -> list[AdRecord]:
        def attempt():
            ws = self._pool.acquire()
            records, _cursor, _has_next = search_ads(
                ws.session, ws.tokens, query=query, countries=countries,
                active_status=active_status, sort_by_impressions=sort_by_impressions,
                search_type=search_type, view_all_page_id=view_all_page_id, max_pages=max_pages,
                delay=self._search_delay, jitter=self._search_jitter, sleep=self._sleep)
            return records

        def on_retry(err, _attempt):
            if err.kind in _ROTATE_ON:
                self._pool.invalidate()

        def run():
            return with_backoff(attempt, max_attempts=self._max_attempts,
                                base=self._backoff_base, sleep=self._sleep, on_retry=on_retry)
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
