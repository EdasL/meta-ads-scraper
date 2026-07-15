import asyncio
from sqlalchemy import text
from agf.parse import AdRecord
from agf.classify import AppIntent
from agf.extract.deeplink import ResolvedLink
from agf.db import repo
from agf.enrich_full.run_enrich import (enrich_gem, record_web_funnel, EnrichDeps,
                                        FunnelProbeBreaker)
from agf.enrich_full.schemas import ResolvedTarget
from tests.enrich_full.conftest import FakeExtractor
from tests.enrich_full.test_company import FakeWhois, _rec


_Q1 = ("<html><body><p>1-min quiz</p><div><button>Male</button>"
       "<button>Female</button></div></body></html>")
_Q2 = ("<html><body><p>1-min quiz</p><div><button>Under 30</button>"
       "<button>30-45</button><button>Over 45</button></div></body></html>")


class FakeWebFunnel:
    """A real quiz→checkout funnel that serves BOTH consumers the web branch drives:
    render_and_probe (content()/`text=` answer clicks → quiz_steps) AND capture_funnel
    (url/title/screenshot/`a.cta` step clicks). Confirms at 2 quiz steps."""

    def __init__(self):
        self.screens = [_Q1, _Q2]
        self.urls = ["https://getgem.com/quiz", "https://getgem.com/checkout"]
        self.titles = ["Quiz", "Checkout"]
        self.i = 0
        self.history = []
        self.shots = 0

    def goto(self, url):
        self.i = self.urls.index(url) if url in self.urls else 0

    @property
    def url(self): return self.urls[self.i]
    def title(self): return self.titles[self.i]
    def content(self): return self.screens[self.i]
    def go_back(self): self.i = self.history.pop()
    def wait_for_load_state(self, state, timeout=None): pass
    def wait_for_timeout(self, ms): pass

    def screenshot(self):
        self.shots += 1
        return f"png-{self.i}".encode()

    def query_selector(self, sel):
        page = self
        if sel == "a.cta":                       # capture_funnel's next-step CTA
            if self.i < len(self.urls) - 1:
                class _Cta:
                    def click(_s):
                        page.history.append(page.i)
                        page.i = min(page.i + 1, len(page.urls) - 1)
                return _Cta()
            return None
        if sel.startswith("text="):              # render_and_probe's answer clicks
            t = sel[len("text="):].strip('"')
            if t.lower() not in self.screens[self.i].lower():
                return None
            class _El:
                def click(_s):
                    if page.i == 0 and t == "Male":
                        page.history.append(page.i)
                        page.i = 1
            return _El()
        return None                              # CMP / cookie selectors


def _ad(ext, link):
    return AdRecord(ext, "PGEM", "GemCo", True, 1_714_500_000, None, 1,
                    "INSTALL_MOBILE_APP", "Install", link, "body")


class _FakeITunes:
    def get(self, url, params=None):
        return _J({"results": [{"trackId": 999, "trackName": "GemApp", "sellerName": "Acme",
                                "primaryGenreName": "Health & Fitness",
                                "releaseDate": "2026-05-20T07:00:00Z",
                                "userRatingCount": 80, "averageUserRating": 4.7}]})


class _J:
    def __init__(self, p): self._p = p
    def json(self): return self._p


def _png():
    import io
    from PIL import Image
    buf = io.BytesIO(); Image.new("RGB", (16, 16), (9, 9, 9)).save(buf, "PNG")
    return buf.getvalue()


def test_enrich_gem_assembled(pg_conn):
    # seed a gem with one iOS-app ad + one web-funnel ad
    app_ad = _ad("GEM-IOS", "https://apps.apple.com/us/app/x/id999")
    funnel_ad = _ad("GEM-WEB", "https://getgem.com/quiz")
    adv = repo.upsert_advertiser(pg_conn, app_ad)
    ad_ios = repo.upsert_ad(pg_conn, app_ad, adv, None)
    ad_web = repo.upsert_ad(pg_conn, funnel_ad, adv, None)

    ex = FakeExtractor(links={
        "https://apps.apple.com/us/app/x/id999":
            ResolvedLink("https://apps.apple.com/us/app/x/id999", AppIntent("ios", "999")),
        "https://getgem.com/quiz":
            ResolvedLink("https://getgem.com/quiz", AppIntent("web", None)),
    })
    page = FakeWebFunnel()
    puts = []
    deps = EnrichDeps(
        extractor=ex, itunes_client=_FakeITunes(),
        fetch_text=lambda u: '<script src="https://js.stripe.com/v3"></script>',
        fetch_bytes=lambda u: (_png(), "image/png"),
        put=lambda key, d, ct, client=None: (puts.append(key) or key),
        whois_fn=FakeWhois(_rec()), page=page, capture=True,
        creative_urls={"GEM-WEB": [("image", "https://cdn/c1.png")]})

    res = asyncio.run(enrich_gem(pg_conn, adv, top_n=5, deps=deps))

    assert res.n_ads_processed == 2
    # app metadata linked
    app_id = pg_conn.execute(text("SELECT app_id FROM public.ads WHERE id=:i"),
                             {"i": ad_ios}).scalar()
    assert app_id is not None
    app = pg_conn.execute(text("SELECT name, is_emerging, genre, is_game FROM public.apps "
                               "WHERE id=:i"), {"i": app_id}).first()
    assert app.name == "GemApp" and app.is_emerging is True
    assert app.is_game is False    # Health & Fitness primary genre -> authoritative non-game
    # funnel captured
    assert res.funnels == 1
    fr = pg_conn.execute(text("SELECT screenshot_s3_key, final_url_after_redirect "
                              "FROM public.funnels WHERE ad_id=:i"), {"i": ad_web}).first()
    assert fr.screenshot_s3_key and fr.final_url_after_redirect == "https://getgem.com/quiz"
    # creative deduped + stored
    assert res.creatives == 1
    # company enrichment backfilled domain_id, tech captured
    dom = pg_conn.execute(text("SELECT domain_id FROM public.advertisers WHERE id=:i"),
                          {"i": adv}).scalar()
    assert dom is not None
    assert res.company and "Stripe" in res.company.tech


class _ITunesResults:
    def __init__(self, results): self.results = results
    def get(self, url, params=None): return _J({"results": self.results})


def _ios_ad(ext, store_id):
    return _ad(ext, f"https://apps.apple.com/us/app/x/id{store_id}")


def test_enrich_gem_no_app_result_leaves_app_id_null(pg_conn):
    ar = _ios_ad("NORES-IOS", "555")
    adv = repo.upsert_advertiser(pg_conn, ar)
    ad = repo.upsert_ad(pg_conn, ar, adv, None)
    ex = FakeExtractor(links={"https://apps.apple.com/us/app/x/id555":
                              ResolvedLink("https://apps.apple.com/us/app/x/id555",
                                           AppIntent("ios", "555"))})
    deps = EnrichDeps(extractor=ex, itunes_client=_ITunesResults([]), capture=False)
    res = asyncio.run(enrich_gem(pg_conn, adv, top_n=5, deps=deps))
    assert res.n_ads_processed == 1
    assert pg_conn.execute(text("SELECT app_id FROM public.ads WHERE id=:i"),
                           {"i": ad}).scalar() is None


def test_enrich_gem_old_app_not_emerging(pg_conn):
    ar = _ios_ad("OLD-IOS", "777")
    adv = repo.upsert_advertiser(pg_conn, ar)
    ad = repo.upsert_ad(pg_conn, ar, adv, None)
    ex = FakeExtractor(links={"https://apps.apple.com/us/app/x/id777":
                              ResolvedLink("https://apps.apple.com/us/app/x/id777",
                                           AppIntent("ios", "777"))})
    old = _ITunesResults([{"trackId": 777, "trackName": "Old", "sellerName": "Inc",
                           "primaryGenreName": "Health & Fitness",
                           "releaseDate": "2019-01-01T00:00:00Z",
                           "userRatingCount": 9_000_000, "averageUserRating": 4.5}])
    deps = EnrichDeps(extractor=ex, itunes_client=old, capture=False)
    asyncio.run(enrich_gem(pg_conn, adv, top_n=5, deps=deps))
    app_id = pg_conn.execute(text("SELECT app_id FROM public.ads WHERE id=:i"), {"i": ad}).scalar()
    em = pg_conn.execute(text("SELECT is_emerging FROM public.apps WHERE id=:i"), {"i": app_id}).scalar()
    assert em is False                            # released 2019 -> not emerging


def test_enrich_gem_top_n_truncates(pg_conn):
    adv = None
    for i in range(3):
        ar = _ad(f"TRUNC-{i}", "https://getgem.com")
        adv = repo.upsert_advertiser(pg_conn, ar)
        repo.upsert_ad(pg_conn, ar, adv, None)
    deps = EnrichDeps(extractor=FakeExtractor(), capture=False)
    res = asyncio.run(enrich_gem(pg_conn, adv, top_n=2, deps=deps))
    assert res.n_ads_processed == 2               # 3 ads exist, top_n=2 -> 2 processed


def test_enrich_gem_floats_funnel_ad_past_budget(pg_conn):
    # Store ads are more numerous + sort first by recency tiebreak; the lone
    # funnel ad is inserted last (highest id) so plain "top N by recency" would
    # bury it past the probe budget. Off-store ads must float ahead so the
    # funnel still gets probed.
    store = _ad("FLOAT-IOS-0", "https://apps.apple.com/us/app/x/id999")
    adv = repo.upsert_advertiser(pg_conn, store)
    for i in range(3):
        repo.upsert_ad(pg_conn, _ad(f"FLOAT-IOS-{i}",
                                    "https://apps.apple.com/us/app/x/id999"), adv, None)
    funnel_ad = _ad("FLOAT-WEB", "https://getgem.com/quiz")
    ad_web = repo.upsert_ad(pg_conn, funnel_ad, adv, None)   # inserted last -> highest id

    ex = FakeExtractor(links={
        "https://apps.apple.com/us/app/x/id999":
            ResolvedLink("https://apps.apple.com/us/app/x/id999", AppIntent("ios", "999")),
        "https://getgem.com/quiz":
            ResolvedLink("https://getgem.com/quiz", AppIntent("web", None)),
    })
    deps = EnrichDeps(
        extractor=ex, itunes_client=_FakeITunes(),
        put=lambda key, d, ct, client=None: key,
        page=FakeWebFunnel(), capture=True)

    res = asyncio.run(enrich_gem(pg_conn, adv, top_n=2, deps=deps))

    # funnel reached the probe even though 4 store ads exist and top_n=2
    assert res.funnels == 1
    got = pg_conn.execute(text("SELECT count(*) FROM public.funnels WHERE ad_id=:i"),
                          {"i": ad_web}).scalar()
    assert got == 1


class FakeFlatLanding:
    """A single-screen web landing the quiz-probe can't confirm: no question
    screen, no checkout/store/pixel signature. This is the bulk of the real
    web-to-app funnels the strict classifier dropped at write time."""

    def __init__(self, url, html="<html><body><h1>Learn French in 10 minutes</h1></body></html>"):
        self._url, self._html = url, html

    def goto(self, url): pass
    @property
    def url(self): return self._url
    def title(self): return "Landing"
    def content(self): return self._html
    def wait_for_load_state(self, *a, **k): pass
    def wait_for_timeout(self, ms): pass
    def query_selector(self, sel): return None


def test_enrich_gem_records_w2a_funnel_even_when_probe_cannot_confirm(pg_conn):
    # A textbook web-to-app funnel (siiites builder, w2a_meta + AppsFlyer params)
    # that lives on '/' and shows no quiz — classify_funnel scores it 'dropped'.
    # The ad's web destination IS the funnel (matview 129), so the row must still
    # be written, just without paying for a screenshot.
    url = "https://10mf.siiites.com/?pid=w2a_meta&af_adset=set&af_ad=ad"
    ar = _ad("W2A-WEB", url)
    adv = repo.upsert_advertiser(pg_conn, ar)
    ad_web = repo.upsert_ad(pg_conn, ar, adv, None)
    ex = FakeExtractor(links={url: ResolvedLink(url, AppIntent("web", None))})
    deps = EnrichDeps(extractor=ex, page=FakeFlatLanding(url), capture=True,
                      put=lambda key, d, ct, client=None: key)

    res = asyncio.run(enrich_gem(pg_conn, adv, top_n=5, deps=deps))

    assert res.funnels == 1
    fr = pg_conn.execute(text("SELECT screenshot_s3_key, final_url_after_redirect "
                              "FROM public.funnels WHERE ad_id=:i"), {"i": ad_web}).first()
    assert fr is not None
    assert fr.screenshot_s3_key is None                # unconfirmed -> no screenshot cost
    assert fr.final_url_after_redirect.startswith("https://10mf.siiites.com/")


def test_enrich_gem_skips_bare_homepage_without_funnel_intent(pg_conn):
    # A plain brand homepage with no funnel/w2a markers and no quiz: NOT a funnel,
    # must not be written (so the Quiz column isn't flooded with non-funnels).
    url = "https://brandco.example/"
    ar = _ad("HOME-WEB", url)
    adv = repo.upsert_advertiser(pg_conn, ar)
    ad_web = repo.upsert_ad(pg_conn, ar, adv, None)
    ex = FakeExtractor(links={url: ResolvedLink(url, AppIntent("web", None))})
    deps = EnrichDeps(extractor=ex, page=FakeFlatLanding(url), capture=True,
                      put=lambda key, d, ct, client=None: key)

    res = asyncio.run(enrich_gem(pg_conn, adv, top_n=5, deps=deps))

    assert res.funnels == 0
    assert pg_conn.execute(text("SELECT count(*) FROM public.funnels WHERE ad_id=:i"),
                           {"i": ad_web}).scalar() == 0


def _png_colored(seed: int):
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), ((seed * 37) % 256, (seed * 11) % 256, seed % 256)).save(buf, "PNG")
    return buf.getvalue()


def test_enrich_gem_media_pass_covers_prefetched_ads_beyond_top_n(pg_conn):
    # 3 ads, top_n=1: the prefetched media URLs for the other 2 ads must still
    # be downloaded (media-only pass) — otherwise their cards render textless.
    adv = None
    for i in range(3):
        ar = _ad(f"MEDIA-{i}", "https://getgem.com")
        adv = repo.upsert_advertiser(pg_conn, ar)
        repo.upsert_ad(pg_conn, ar, adv, None)

    def fetch(url):
        return _png_colored(int(url.rsplit("/", 1)[1].split(".")[0])), "image/png"

    deps = EnrichDeps(
        extractor=FakeExtractor(), capture=False, fetch_bytes=fetch,
        put=lambda key, d, ct, client=None: key,
        creative_urls={f"MEDIA-{i}": [("image", f"https://cdn/{i}.png")] for i in range(3)})
    res = asyncio.run(enrich_gem(pg_conn, adv, top_n=1, deps=deps))

    assert res.n_ads_processed == 1
    assert res.creatives == 3
    rows = pg_conn.execute(text(
        "SELECT a.external_ad_id FROM public.ads a WHERE a.advertiser_id=:adv AND EXISTS "
        "(SELECT 1 FROM public.creatives c WHERE c.ad_id=a.id AND c.s3_key IS NOT NULL)"),
        {"adv": adv}).scalars().all()
    assert sorted(rows) == ["MEDIA-0", "MEDIA-1", "MEDIA-2"]


def test_enrich_gem_media_pass_links_duplicate_bytes(pg_conn):
    # Two beyond-top-N ads serving identical bytes: the second must still get
    # its own creatives row (shared s3_key, sha256 NULL — the partial unique
    # index owns byte identity), so its card renders the media.
    adv = None
    for i in range(3):
        ar = _ad(f"DUPM-{i}", "https://getgem.com")
        adv = repo.upsert_advertiser(pg_conn, ar)
        repo.upsert_ad(pg_conn, ar, adv, None)

    deps = EnrichDeps(
        extractor=FakeExtractor(), capture=False,
        fetch_bytes=lambda u: (_png_colored(99), "image/png"),
        put=lambda key, d, ct, client=None: key,
        creative_urls={f"DUPM-{i}": [("image", f"https://cdn/{i}.png")] for i in (1, 2)})
    asyncio.run(enrich_gem(pg_conn, adv, top_n=1, deps=deps))

    rows = pg_conn.execute(text(
        "SELECT a.external_ad_id, c.s3_key, c.sha256 FROM public.creatives c "
        "JOIN public.ads a ON a.id=c.ad_id WHERE a.advertiser_id=:adv AND c.s3_key IS NOT NULL "
        "ORDER BY c.id"), {"adv": adv}).all()
    assert [r.external_ad_id for r in rows] == ["DUPM-1", "DUPM-2"]
    assert rows[0].s3_key == rows[1].s3_key
    assert rows[0].sha256 is not None and rows[1].sha256 is None


def test_enrich_gem_media_pass_respects_cap(pg_conn):
    adv = None
    for i in range(3):
        ar = _ad(f"CAP-{i}", "https://getgem.com")
        adv = repo.upsert_advertiser(pg_conn, ar)
        repo.upsert_ad(pg_conn, ar, adv, None)

    def fetch(url):
        return _png_colored(int(url.rsplit("/", 1)[1].split(".")[0])), "image/png"

    deps = EnrichDeps(
        extractor=FakeExtractor(), capture=False, fetch_bytes=fetch,
        put=lambda key, d, ct, client=None: key,
        creative_urls={f"CAP-{i}": [("image", f"https://cdn/{i}.png")] for i in range(3)})
    res = asyncio.run(enrich_gem(pg_conn, adv, top_n=0, media_cap=2, deps=deps))

    assert res.creatives == 2


class _BoomPage:
    """A landing whose render always dies (dead host / net::ERR_ABORTED) —
    the hunt-surfaced dead-funnel shape that blew the enrich deadline."""

    def __init__(self):
        self.gotos = 0

    def goto(self, url):
        self.gotos += 1
        raise RuntimeError("net::ERR_ABORTED")


def _web_target(url):
    return ResolvedTarget(kind="web", store_id=None, landing_url=url, final_url=url)


def test_record_web_funnel_macro_url_skips_probe(pg_conn):
    # An unresolved ad-tech macro ({{ad.id}}) can never render a real page —
    # the probe must be skipped outright, while the row is still recorded
    # from URL intent (w2a markers).
    url = "https://track.example.com/c/abc?cp={{ad.id}}&pid=w2a_meta"
    ar = _ad("MACRO-WEB", url)
    adv = repo.upsert_advertiser(pg_conn, ar)
    ad_web = repo.upsert_ad(pg_conn, ar, adv, None)
    page = _BoomPage()
    deps = EnrichDeps(extractor=FakeExtractor(), page=page, capture=True)

    wrote = asyncio.run(record_web_funnel(pg_conn, ad_web, url, _web_target(url), deps))

    assert wrote == 1
    assert page.gotos == 0            # no render was paid for


def test_record_web_funnel_dead_landing_skips_render(pg_conn):
    # check_alive says the host is transport-dead: skip the browser render,
    # count a breaker failure, still record the funnel row from URL intent.
    url = "https://gone.example.com/onboarding?pid=w2a_meta"
    ar = _ad("DEADHOST-WEB", url)
    adv = repo.upsert_advertiser(pg_conn, ar)
    ad_web = repo.upsert_ad(pg_conn, ar, adv, None)
    page = FakeWebFunnel()            # would confirm + screenshot if driven
    breaker = FunnelProbeBreaker()
    deps = EnrichDeps(extractor=FakeExtractor(), page=page, capture=True,
                      check_alive=lambda u: False,
                      put=lambda key, d, ct, client=None: key)

    wrote = asyncio.run(record_web_funnel(pg_conn, ad_web, url, _web_target(url),
                                          deps, breaker=breaker))

    assert wrote == 1
    assert page.shots == 0            # render never ran
    assert breaker.fails == 1
    fr = pg_conn.execute(text("SELECT kind, screenshot_s3_key FROM public.funnels "
                              "WHERE ad_id=:i"), {"i": ad_web}).first()
    assert fr.kind is None and fr.screenshot_s3_key is None


def test_enrich_gem_stops_liveness_after_two_dead_landings(pg_conn):
    # 4 dead web ads: the breaker opens after 2 failures, so liveness is only
    # probed twice — the remaining ads' rows are written from URL intent alone.
    urls = [f"https://dead{i}.example.com/go?pid=w2a_meta" for i in range(4)]
    adv = None
    for i, url in enumerate(urls):
        ar = _ad(f"BRK-{i}", url)
        adv = repo.upsert_advertiser(pg_conn, ar)
        repo.upsert_ad(pg_conn, ar, adv, None)
    ex = FakeExtractor(links={u: ResolvedLink(u, AppIntent("web", None)) for u in urls})
    seen = []

    def check(u):
        seen.append(u)
        return False

    deps = EnrichDeps(extractor=ex, capture=True, check_alive=check)
    res = asyncio.run(enrich_gem(pg_conn, adv, top_n=5, deps=deps))

    assert len(seen) == 2             # breaker open -> 3rd/4th never probed
    assert res.funnels == 4           # every row still written


def test_enrich_gem_render_failures_open_the_breaker(pg_conn):
    # Renders that die (dead host reached the browser) count toward the breaker
    # too: after 2 failed renders the remaining ads are not rendered at all.
    urls = [f"https://boom{i}.example.com/go?pid=w2a_meta" for i in range(4)]
    adv = None
    for i, url in enumerate(urls):
        ar = _ad(f"BOOM-{i}", url)
        adv = repo.upsert_advertiser(pg_conn, ar)
        repo.upsert_ad(pg_conn, ar, adv, None)
    ex = FakeExtractor(links={u: ResolvedLink(u, AppIntent("web", None)) for u in urls})
    page = _BoomPage()
    deps = EnrichDeps(extractor=ex, capture=True, page=page)

    res = asyncio.run(enrich_gem(pg_conn, adv, top_n=5, deps=deps))

    assert page.gotos == 2            # 3rd/4th renders skipped
    assert res.funnels == 4


def test_enrich_gem_dead_creative_url_does_not_crash(pg_conn):
    ar = _ad("DEAD-WEB", "https://getgem.com")
    adv = repo.upsert_advertiser(pg_conn, ar)
    repo.upsert_ad(pg_conn, ar, adv, None)

    def boom(_url):
        raise RuntimeError("403 expired fbcdn signature")
    deps = EnrichDeps(extractor=FakeExtractor(), capture=False, fetch_bytes=boom,
                      creative_urls={"DEAD-WEB": [("image", "https://cdn/dead.png")]})
    res = asyncio.run(enrich_gem(pg_conn, adv, top_n=5, deps=deps))   # must NOT raise
    assert res.n_ads_processed == 1 and res.creatives == 0
