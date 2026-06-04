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
