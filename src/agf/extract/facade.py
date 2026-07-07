import asyncio
import time as _time
from agf.parse import AdRecord
from agf.extract.ad_details import AdDetails, fetch_ad_details
from agf.extract.deeplink import ResolvedLink, resolve_deeplink
from agf.extract.search import count_active_ads, search_ads, search_pages_typeahead
from agf.extract.session_pool import SessionPool
from agf.extract.tls_client import AdLibrarySession
from agf.extract.proxy import ProxyPool
from agf.extract.backoff import with_backoff, classify_exception
from agf.extract.errors import ErrorKind, ExtractError

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

    def _run_resilient(self, attempt_fn):
        """Run a session-bound operation with transport-error translation, exponential
        backoff, and proxy rotation on rate-limit/session errors. A curl_cffi transport
        error (timeout / dead exit) becomes a retryable TRANSIENT — the rotating gateway
        hands out a fresh IP on retry. Synchronous; call inside asyncio.to_thread."""
        def guarded():
            try:
                return attempt_fn()
            except ExtractError:
                raise
            except Exception as exc:                       # transport-level (curl_cffi) error
                kind = classify_exception(exc)
                if kind is None:
                    raise                                  # a real bug — don't mask it
                raise ExtractError(f"{type(exc).__name__}: {exc}", kind) from exc

        def on_retry(err, _attempt):
            if err.kind in _ROTATE_ON:                     # burned IP → re-warm on a fresh proxy
                self._pool.invalidate()

        return with_backoff(guarded, max_attempts=self._max_attempts,
                            base=self._backoff_base, sleep=self._sleep, on_retry=on_retry)

    async def search(self, *, query="", countries=["US"], active_status="ACTIVE",
                     sort_by_impressions=False, sort_mode: str | None = None,
                     search_type="KEYWORD_UNORDERED",
                     view_all_page_id=None, max_pages=10,
                     stats: dict | None = None) -> list[AdRecord]:
        def attempt():
            ws = self._pool.acquire()
            records, _cursor, _has_next = search_ads(
                ws.session, ws.tokens, query=query, countries=countries,
                active_status=active_status, sort_by_impressions=sort_by_impressions,
                sort_mode=sort_mode, search_type=search_type,
                view_all_page_id=view_all_page_id, max_pages=max_pages,
                delay=self._search_delay, jitter=self._search_jitter, sleep=self._sleep,
                stats=stats)
            return records
        return await asyncio.to_thread(lambda: self._run_resilient(attempt))

    async def search_page(self, *, query="", countries=["US"], active_status="ACTIVE",
                          sort_by_impressions=False, sort_mode: str | None = None,
                          search_type="KEYWORD_UNORDERED",
                          view_all_page_id=None, cursor: str | None = None,
                          stats: dict | None = None
                          ) -> tuple[list[AdRecord], str | None, bool]:
        """Fetch a SINGLE search page from `cursor` (None => first page) and return
        (records, next_cursor, has_next) so a caller (the adaptive-depth sweep) can
        decide page-by-page whether to keep going. `next_cursor` feeds the next call.
        Same resilience path as search()/search_listing(): paced, backs off + rotates
        the proxy IP on a rate-limit/session error."""
        def attempt():
            ws = self._pool.acquire()
            records, next_cursor, has_next = search_ads(
                ws.session, ws.tokens, query=query, countries=countries,
                active_status=active_status, sort_by_impressions=sort_by_impressions,
                sort_mode=sort_mode, search_type=search_type,
                view_all_page_id=view_all_page_id, max_pages=1, cursor=cursor,
                delay=self._search_delay, jitter=self._search_jitter, sleep=self._sleep,
                stats=stats)
            return records, next_cursor, bool(has_next)
        return await asyncio.to_thread(lambda: self._run_resilient(attempt))

    async def count_active_ads(self, *, page_id, countries=["US"], cap=250,
                               max_pages=40) -> tuple[int, bool]:
        """True active-ad count for a Page (bounded view_all_page_id pagination, early
        exit at `cap` -> capped=True). Throttle-aware: paced between pages, backs off +
        rotates IP on rate-limit, same as `search`."""
        def attempt():
            ws = self._pool.acquire()
            return count_active_ads(
                ws.session, ws.tokens, page_id, countries=countries, cap=cap,
                max_pages=max_pages, delay=self._search_delay,
                jitter=self._search_jitter, sleep=self._sleep)
        return await asyncio.to_thread(lambda: self._run_resilient(attempt))

    async def search_listing(self, *, page_id, countries=["US"],
                             max_pages=12) -> tuple[list, bool]:
        """An advertiser's full ALL-status ad listing + whether pagination was
        truncated at max_pages (more pages remained). Mirrors search() but returns the
        has_next flag search() discards, so the corpus refresh only reconciles is_active
        on a COMPLETE listing."""
        def attempt():
            ws = self._pool.acquire()
            records, _cursor, has_next = search_ads(
                ws.session, ws.tokens, query="", countries=countries,
                active_status="ALL", search_type="page", view_all_page_id=page_id,
                max_pages=max_pages, delay=self._search_delay,
                jitter=self._search_jitter, sleep=self._sleep)
            return records, bool(has_next)
        return await asyncio.to_thread(lambda: self._run_resilient(attempt))

    async def typeahead(self, *, query: str, country: str = "US") -> list[dict]:
        """Keyword -> candidate Pages (page_id + page_like_count + name). Throttle-aware:
        rotates IP + backs off on rate-limit, same as `search`."""
        def attempt():
            ws = self._pool.acquire()
            return search_pages_typeahead(ws.session, ws.tokens, query=query, country=country)
        return await asyncio.to_thread(lambda: self._run_resilient(attempt))

    async def ad_details(self, ad_archive_id) -> AdDetails:
        def attempt():
            ws = self._pool.acquire()
            return fetch_ad_details(ws.session, ws.tokens, ad_archive_id)
        return await asyncio.to_thread(lambda: self._run_resilient(attempt))

    async def resolve_deeplink(self, url, ua="ios") -> ResolvedLink:
        def attempt():
            ws = self._pool.acquire()
            return resolve_deeplink(ws.session, url, ua=ua)
        return await asyncio.to_thread(lambda: self._run_resilient(attempt))
