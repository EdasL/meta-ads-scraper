from agf.enrich_full.funnel_capture import capture_funnel


class FakeEl:
    def __init__(self, page): self.page = page
    def click(self): self.page._advance()


class FakeFunnelPage:
    """Scripted multi-step page. goto sets step 0; the next-CTA exists until the last."""
    def __init__(self, urls, titles):
        self.urls, self.titles, self.i = urls, titles, 0
        self.shots = 0

    def goto(self, url):
        self.i = self.urls.index(url) if url in self.urls else 0

    @property
    def url(self): return self.urls[self.i]

    def title(self): return self.titles[self.i]

    def screenshot(self):
        self.shots += 1
        return f"png-{self.i}".encode()

    def query_selector(self, sel):
        if sel == "a.cta" and self.i < len(self.urls) - 1:
            return FakeEl(self)
        return None

    def _advance(self): self.i = min(self.i + 1, len(self.urls) - 1)


def test_captures_three_steps_to_s3():
    urls = ["https://f/1", "https://f/2", "https://f/3"]
    page = FakeFunnelPage(urls, ["One", "Two", "Three"])
    puts = []
    fake_put = lambda key, d, ct, client=None: (puts.append((key, d)) or key)

    steps = capture_funnel("https://f/1", max_steps=3, page=page, put=fake_put)

    assert [s.step for s in steps] == [1, 2, 3]
    assert [s.url for s in steps] == urls
    assert [s.title for s in steps] == ["One", "Two", "Three"]
    assert all(s.screenshot_s3_key for s in steps)
    assert len(puts) == 3 and page.shots == 3


def test_stops_when_no_next_cta():
    page = FakeFunnelPage(["https://f/1"], ["Only"])
    steps = capture_funnel("https://f/1", max_steps=3, page=page,
                           put=lambda *a, **k: "key")
    assert len(steps) == 1


class FlakyShotPage(FakeFunnelPage):
    """Screenshot raises on the given (0-based) step index."""
    def __init__(self, urls, titles, fail_at):
        super().__init__(urls, titles)
        self.fail_at = fail_at

    def screenshot(self):
        if self.i == self.fail_at:
            raise RuntimeError("screenshot timeout")
        return super().screenshot()


def test_screenshot_failure_yields_null_key_but_continues():
    page = FlakyShotPage(["https://f/1", "https://f/2"], ["One", "Two"], fail_at=1)
    steps = capture_funnel("https://f/1", max_steps=2, page=page, put=lambda *a, **k: "key")
    assert len(steps) == 2                       # capture continues past the failed step
    assert steps[0].screenshot_s3_key is not None
    assert steps[1].screenshot_s3_key is None    # step 2 screenshot failed -> NULL key
    assert steps[1].url == "https://f/2"
