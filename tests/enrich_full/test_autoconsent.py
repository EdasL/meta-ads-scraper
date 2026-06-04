from agf.enrich_full.funnel_capture import autoconsent_cmp, _CMP_ACCEPT_SELECTORS


class FakeEl:
    def __init__(self): self.clicked = False
    def click(self): self.clicked = True


class FakePage:
    def __init__(self, present):       # present: set of selectors that exist
        self.present = set(present)
        self.els = {}

    def query_selector(self, sel):
        if sel in self.present:
            self.els.setdefault(sel, FakeEl())
            return self.els[sel]
        return None


def test_dismisses_known_cmp():
    sel = _CMP_ACCEPT_SELECTORS[0]
    page = FakePage({sel})
    assert autoconsent_cmp(page) is True
    assert page.els[sel].clicked is True


def test_no_banner_returns_false():
    assert autoconsent_cmp(FakePage(set())) is False
