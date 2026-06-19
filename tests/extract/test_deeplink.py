import pytest
from agf.extract.deeplink import resolve_deeplink, ResolvedLink
from agf.classify import AppIntent
from tests.extract.conftest import FakeResponse


class RedirectSession:
    """Exposes the final resolved URL via .url (as curl_cffi does)."""
    def __init__(self, final_url):
        self.final_url = final_url
        self.last_headers = None
        self.get_called = False

    def get(self, url, headers=None, **kw):
        self.get_called = True
        self.last_headers = headers or {}
        r = FakeResponse(200, "")
        r.url = self.final_url
        return r


class RaisingSession:
    def get(self, url, headers=None, **kw):
        raise RuntimeError("Unsupported URL scheme: itms-apps://")


def test_direct_store_link_classified_without_network():
    session = RedirectSession("ignored")
    out = resolve_deeplink(session, "https://apps.apple.com/us/app/x/id42")
    assert out == ResolvedLink(final_url="https://apps.apple.com/us/app/x/id42",
                               intent=AppIntent(kind="ios", store_id="42"))
    assert session.get_called is False          # no network for a direct link


def test_resolves_mmp_link_to_ios_store():
    session = RedirectSession("https://apps.apple.com/us/app/x/id42")
    out = resolve_deeplink(session, "https://myapp.app.link/abc", ua="ios")
    assert out.intent == AppIntent(kind="ios", store_id="42")
    assert "iPhone" in session.last_headers.get("User-Agent", "")


def test_mmp_scheme_error_falls_back_to_original():
    session = RaisingSession()
    out = resolve_deeplink(session, "https://myapp.app.link/abc")
    assert out.final_url == "https://myapp.app.link/abc"
    assert out.intent == AppIntent(kind="web", store_id=None)


def test_plain_web_funnel_not_chased():
    session = RedirectSession("ignored")
    out = resolve_deeplink(session, "https://getgem.com/quiz")
    assert out.intent == AppIntent(kind="web", store_id=None)
    assert session.get_called is False


# --- unit 3: extended MMP host list ---

def test_onelink_me_is_followed():
    session = RedirectSession("https://apps.apple.com/us/app/x/id99")
    out = resolve_deeplink(session, "https://brand.onelink.me/abc?pid=meta")
    assert session.get_called is True
    assert out.intent.kind == "ios"


def test_adjust_com_is_followed():
    session = RedirectSession("https://apps.apple.com/us/app/x/id88")
    out = resolve_deeplink(session, "https://app.adjust.com/abc123")
    assert session.get_called is True


def test_branch_io_is_followed():
    session = RedirectSession("https://apps.apple.com/us/app/x/id77")
    out = resolve_deeplink(session, "https://myapp.branch.io/abc")
    assert session.get_called is True


def test_bnc_lt_is_followed():
    session = RedirectSession("https://apps.apple.com/us/app/x/id66")
    out = resolve_deeplink(session, "https://bnc.lt/xyz")
    assert session.get_called is True
