import pytest
from agf.extract.tls_client import build_session_headers, AdLibrarySession
from tests.extract.conftest import FakeResponse


def test_build_session_headers_have_browser_shape():
    h = build_session_headers()
    assert "Mozilla/5.0" in h["User-Agent"]
    assert h["Sec-Fetch-Mode"] == "navigate"
    assert h["Accept-Language"].startswith("en-US")


def test_warm_solves_challenge_then_returns_real_html():
    stub = ("<script>fetch('/__rd_verify_TOK?challenge=3',{method:'POST'})"
            ".finally(()=>location.reload());</script>")
    real = '<script>["LSD",[],{"token":"AVlive"}]</script>'
    seq = [FakeResponse(403, stub), FakeResponse(200, real)]
    posted = {"verify": False}

    class FakeTransport:
        cookies = {}

        def get(self, url, **kw):
            return seq.pop(0)

        def post(self, url, data=None, headers=None, **kw):
            posted["verify"] = "/__rd_verify_" in url
            self.cookies["rd_challenge"] = "C"
            return FakeResponse(200)

    sess = AdLibrarySession(transport=FakeTransport(), proxy=None)
    html = sess.warm_get("https://www.facebook.com/ads/library/?q=x")
    assert posted["verify"] is True
    assert '"LSD"' in html
    assert sess.transport.cookies.get("rd_challenge") == "C"


def test_warm_raises_if_challenge_never_clears():
    stub = "<script>fetch('/__rd_verify_T?challenge=3',{method:'POST'})</script>"

    class StubbornTransport:
        cookies = {}

        def get(self, url, **kw):
            return FakeResponse(403, stub)           # always the stub

        def post(self, url, data=None, headers=None, **kw):
            return FakeResponse(200)

    sess = AdLibrarySession(transport=StubbornTransport(), proxy=None)
    with pytest.raises(Exception):
        sess.warm_get("https://www.facebook.com/ads/library/?q=x", max_solves=2)


@pytest.mark.integration
def test_live_warm_get_returns_real_page():
    sess = AdLibrarySession(proxy=None)  # real curl_cffi transport
    html = sess.warm_get(
        "https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
        "&country=US&q=workout%20app&media_type=all&search_type=keyword_unordered"
    )
    assert len(html) > 100_000           # real page, not the ~481-byte stub
    assert '"LSD"' in html
