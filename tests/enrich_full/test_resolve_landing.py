import asyncio
from agf.classify import AppIntent
from agf.extract.deeplink import ResolvedLink
from agf.enrich_full.schemas import ResolvedTarget
from agf.enrich_full.funnel_capture import resolve_landing
from tests.enrich_full.conftest import FakeExtractor


def test_resolves_mmp_to_ios_target():
    ex = FakeExtractor(links={"https://x.app.link/a":
                              ResolvedLink("https://apps.apple.com/us/app/x/id77",
                                           AppIntent("ios", "77"))})
    out = asyncio.run(resolve_landing(ex, "https://x.app.link/a"))
    assert out == ResolvedTarget(kind="ios", store_id="77",
                                 landing_url="https://x.app.link/a",
                                 final_url="https://apps.apple.com/us/app/x/id77")


def test_plain_web_funnel_target():
    ex = FakeExtractor()
    out = asyncio.run(resolve_landing(ex, "https://getgem.com/quiz"))
    assert out.kind == "web" and out.store_id is None
    assert out.landing_url == "https://getgem.com/quiz"
    assert ex.resolved == ["https://getgem.com/quiz"]   # delegated to the extractor
