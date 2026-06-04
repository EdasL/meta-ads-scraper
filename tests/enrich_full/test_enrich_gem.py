import asyncio
from sqlalchemy import text
from agf.parse import AdRecord
from agf.classify import AppIntent
from agf.extract.deeplink import ResolvedLink
from agf.db import repo
from agf.enrich_full.run_enrich import enrich_gem, EnrichDeps
from tests.enrich_full.conftest import FakeExtractor
from tests.enrich_full.test_capture_funnel import FakeFunnelPage
from tests.enrich_full.test_company import FakeWhois, _rec


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
    page = FakeFunnelPage(["https://getgem.com/quiz", "https://getgem.com/checkout"],
                          ["Quiz", "Checkout"])
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
    app = pg_conn.execute(text("SELECT name, is_emerging, genre FROM public.apps WHERE id=:i"),
                          {"i": app_id}).first()
    assert app.name == "GemApp" and app.is_emerging is True
    # funnel captured
    assert res.funnels == 1
    fr = pg_conn.execute(text("SELECT screenshot_s3_key, final_url_after_redirect "
                              "FROM public.funnels WHERE ad_id=:i"), {"i": ad_web}).first()
    assert fr.screenshot_s3_key and fr.final_url_after_redirect == "https://getgem.com/checkout"
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
