import time as _time
from dataclasses import dataclass
from agf.extract.token_heal import SessionTokens, heal_from_html

_SEARCH_URL = ("https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
               "&country=US&q=&media_type=all&search_type=keyword_unordered")


@dataclass
class WarmSession:
    session: object              # AdLibrarySession
    tokens: SessionTokens
    created_at: float
    max_age: float = 1800.0      # MetaAdsCollector default: re-warm after 30 min
    now: object = _time.time

    def is_stale(self) -> bool:
        return (self.now() - self.created_at) > self.max_age


class SessionPool:
    """Warm-once / reuse-many session manager. Holds a single WarmSession and
    re-warms when it goes stale or is invalidated (after a 403/session error)."""

    def __init__(self, make_session, *, max_age: float = 1800.0,
                 warm_url: str = _SEARCH_URL):
        self._make_session = make_session
        self._max_age = max_age
        self._warm_url = warm_url
        self._current: WarmSession | None = None
        self._now = _time.time

    def _warm(self) -> WarmSession:
        session = self._make_session()
        html = session.warm_get(self._warm_url)
        tokens = heal_from_html(html, session=session)   # doc_id healed via JS bundles
        return WarmSession(session=session, tokens=tokens, created_at=self._now(),
                           max_age=self._max_age, now=self._now)

    def acquire(self) -> WarmSession:
        if self._current is None or self._current.is_stale():
            self._current = self._warm()
        return self._current

    def invalidate(self) -> None:
        """Drop the current session so the next acquire() re-warms (call on a
        403 + rd_challenge, FB session error 1357004/1357001, or HTTP 400)."""
        self._current = None
