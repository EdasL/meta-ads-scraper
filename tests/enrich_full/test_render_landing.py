# tests/enrich_full/test_render_landing.py
from agf.enrich_full.funnel_capture import render_landing_html


class FakePage:
    def __init__(self, html):
        self._html = html
        self.url = "https://quiz.example.com/start"
        self.consented = False
        self.default_timeout = None

    def goto(self, url):
        self.url = url

    def set_default_timeout(self, ms):
        self.default_timeout = ms

    def query_selector(self, sel):
        # pretend a Cookiebot accept button exists; clicking it sets consented
        if sel == "#CybotCookiebotDialogBodyButtonAccept":
            page = self
            class _El:
                def click(self_inner):
                    page.consented = True
            return _El()
        return None

    def content(self):
        return self._html


def test_render_landing_dismisses_cmp_and_returns_html():
    page = FakePage("<html><body>paywall + fbq</body></html>")
    html = render_landing_html("https://quiz.example.com/start", page=page)
    assert html == "<html><body>paywall + fbq</body></html>"
    assert page.consented is True          # CMP was dismissed before reading content
    assert page.url == "https://quiz.example.com/start"
