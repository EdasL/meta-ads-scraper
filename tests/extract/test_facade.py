import asyncio
import pytest
from agf.extract.facade import Extractor
from agf.extract.token_heal import SessionTokens
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
