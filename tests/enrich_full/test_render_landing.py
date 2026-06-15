# tests/enrich_full/test_render_landing.py
from agf.enrich_full.funnel_capture import RENDER_UA, render_landing_html


class FakePage:
    """Records the call sequence so the test can assert: goto -> networkidle ->
    consent -> settle -> content (content captured only after the page settled)."""
    def __init__(self, html):
        self._html = html
        self.url = "https://quiz.example.com/start"
        self.consented = False
        self.log = []

    def goto(self, url):
        self.url = url
        self.log.append("goto")

    def wait_for_load_state(self, state, timeout=None):
        self.log.append(f"wait:{state}")

    def wait_for_timeout(self, ms):
        self.log.append(f"settle:{ms}")

    def set_default_timeout(self, ms):
        pass

    def query_selector(self, sel):
        if sel == "#CybotCookiebotDialogBodyButtonAccept":
            page = self
            class _El:
                def is_visible(self_inner):
                    return True
                def click(self_inner, timeout=None):
                    page.consented = True
                    page.log.append("consent")
            return _El()
        return None

    def content(self):
        self.log.append("content")
        return self._html


def test_render_landing_settles_and_dismisses_cmp_in_order():
    page = FakePage("<html><body>paywall + fbq</body></html>")
    html = render_landing_html("https://quiz.example.com/start", page=page, settle_ms=2500)
    assert html == "<html><body>paywall + fbq</body></html>"
    assert page.consented is True
    assert page.url == "https://quiz.example.com/start"
    # networkidle BEFORE consent; settle AFTER consent; content LAST (settled DOM).
    # CMP is dismissed twice — once at networkidle, once post-settle — to catch
    # banners (e.g. Cookiebot) that mount late, after the first network-idle.
    assert page.log == ["goto", "wait:networkidle", "consent", "settle:2500", "consent", "content"]


def test_render_ua_default_is_ios():
    assert "iPhone" in RENDER_UA
