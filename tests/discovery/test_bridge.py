import asyncio
from agf.discovery.bridge import run_bridge
from tests.discovery.conftest import FakeExtractor, make_ad


def test_run_bridge_emits_app_and_page():
    ad = make_ad("a1", "P1", "GemCo",
                 "https://apps.apple.com/us/app/x/id6470000001", days_old=30)
    ex = FakeExtractor(page_ads={"P1": [ad]})
    cands = asyncio.run(run_bridge(ex, ["P1"]))
    assert sorted(c.kind for c in cands) == ["app", "page"]
    app = next(c for c in cands if c.kind == "app")
    assert app.store == "ios" and app.store_id == "6470000001" and app.page_id == "P1"
