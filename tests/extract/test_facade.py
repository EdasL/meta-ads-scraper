import asyncio
import pytest
from agf.extract.facade import Extractor
from agf.extract.token_heal import SessionTokens
from agf.extract.errors import ErrorKind, ExtractError
from agf.parse import AdRecord
from agf.classify import AppIntent
from agf.extract.ad_details import AdDetails
from agf.extract.deeplink import ResolvedLink


class StubPool:
    def __init__(self):
        self.invalidated = False
        self._ws = type("WS", (), {"session": object(),
                                   "tokens": SessionTokens("L", "D", "27201872659451053")})()

    def acquire(self):
        return self._ws

    def invalidate(self):
        self.invalidated = True


def _ad(i):
    return AdRecord(i, "P", "N", True, 1, None, 1, "X", "x",
                    "https://apps.apple.com/us/app/x/id1", "b")


def test_search_delegates_and_returns_records(monkeypatch):
    captured = {}

    def fake_search_ads(session, tokens, **kw):
        captured.update(kw)
        return [_ad("1"), _ad("2")], None, False

    monkeypatch.setattr("agf.extract.facade.search_ads", fake_search_ads)
    ex = Extractor(pool=StubPool())
    out = asyncio.run(ex.search(query="workout", countries=["US"],
                                sort_by_impressions=True, max_pages=3))
    assert [r.ad_archive_id for r in out] == ["1", "2"]
    assert captured["sort_by_impressions"] is True
    assert captured["max_pages"] == 3


def test_search_rotates_proxy_and_retries_on_rate_limit(monkeypatch):
    # Meta throttles by IP → on RATE_LIMIT the facade must invalidate the pool (forcing a
    # fresh warm on a new proxy) and retry, not just sleep on the same throttled IP.
    calls = {"n": 0}

    def flaky_search_ads(session, tokens, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ExtractError("Rate limit exceeded (code=1675004)", ErrorKind.RATE_LIMIT)
        return [_ad("1")], None, False

    monkeypatch.setattr("agf.extract.facade.search_ads", flaky_search_ads)
    pool = StubPool()
    ex = Extractor(pool=pool, max_attempts=3, backoff_base=0.0, sleep=lambda s: None)
    out = asyncio.run(ex.search(query="", countries=["DE"], sort_by_impressions=True,
                                max_pages=1))
    assert [r.ad_archive_id for r in out] == ["1"]
    assert calls["n"] == 2            # retried exactly once
    assert pool.invalidated is True   # rotated to a fresh proxy/session before the retry


def test_search_retries_on_network_timeout(monkeypatch):
    # a transport-level curl timeout (slow/dead proxy exit) must be retried, not crash the
    # sweep — but TRANSIENT doesn't force a re-warm (the rotating gateway gives a fresh IP).
    calls = {"n": 0}
    timeout_type = type("Timeout", (Exception,), {})
    timeout_type.__module__ = "curl_cffi.requests.exceptions"

    def flaky_search_ads(session, tokens, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise timeout_type("Operation timed out after 30001 ms")
        return [_ad("1")], None, False

    monkeypatch.setattr("agf.extract.facade.search_ads", flaky_search_ads)
    pool = StubPool()
    ex = Extractor(pool=pool, max_attempts=3, backoff_base=0.0, sleep=lambda s: None)
    out = asyncio.run(ex.search(query="x", countries=["US"], max_pages=1))
    assert [r.ad_archive_id for r in out] == ["1"]
    assert calls["n"] == 2             # retried after the timeout
    assert pool.invalidated is False   # transient → no re-warm; gateway rotation handles the IP


def test_count_active_ads_delegates_and_rotates_on_rate_limit(monkeypatch):
    # same resilience contract as search: back off + rotate the proxy on a throttle
    calls = {"n": 0}
    captured = {}

    def flaky_count(session, tokens, page_id, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ExtractError("Rate limit exceeded (code=1675004)", ErrorKind.RATE_LIMIT)
        captured["page_id"] = page_id
        captured.update(kw)
        return 42, False

    monkeypatch.setattr("agf.extract.facade.count_active_ads", flaky_count)
    pool = StubPool()
    ex = Extractor(pool=pool, max_attempts=3, backoff_base=0.0, sleep=lambda s: None)
    out = asyncio.run(ex.count_active_ads(page_id="98765", cap=100))
    assert out == (42, False)
    assert captured["page_id"] == "98765" and captured["cap"] == 100
    assert calls["n"] == 2 and pool.invalidated is True


def test_search_does_not_invalidate_on_success(monkeypatch):
    monkeypatch.setattr("agf.extract.facade.search_ads",
                        lambda session, tokens, **kw: ([_ad("1")], None, False))
    pool = StubPool()
    ex = Extractor(pool=pool, sleep=lambda s: None)
    asyncio.run(ex.search(query="x", countries=["US"], max_pages=1))
    assert pool.invalidated is False


def test_ad_details_delegates(monkeypatch):
    monkeypatch.setattr("agf.extract.facade.fetch_ad_details",
                        lambda s, t, aid: AdDetails(eu_total_reach=7, active=True))
    ex = Extractor(pool=StubPool())
    d = asyncio.run(ex.ad_details("999"))
    assert d.eu_total_reach == 7


def test_resolve_deeplink_delegates(monkeypatch):
    monkeypatch.setattr("agf.extract.facade.resolve_deeplink",
                        lambda s, url, ua="ios": ResolvedLink(url, AppIntent("web", None)))
    ex = Extractor(pool=StubPool())
    r = asyncio.run(ex.resolve_deeplink("https://x.com"))
    assert r.intent.kind == "web"


@pytest.mark.integration
def test_live_search_returns_real_ads():
    ex = Extractor()  # real curl_cffi + rd_challenge + pool, proxies from AGF_PROXIES
    ads = asyncio.run(ex.search(query="workout app", countries=["US"], max_pages=1))
    assert len(ads) > 0
    assert any(a.page_name for a in ads)
