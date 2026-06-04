import asyncio
from agf.discovery.confirm import confirm_scaling_on_meta, run_scaling_confirm
from tests.discovery.conftest import FakeExtractor, make_ad, NOW_EPOCH


def test_confirm_passes_for_old_active_ad():
    ex = FakeExtractor(page_ads={"P1": [make_ad("a1", "P1", "GemCo", "https://x.com", days_old=20)]})
    assert asyncio.run(confirm_scaling_on_meta(ex, "P1", NOW_EPOCH, 14)) is True


def test_confirm_fails_for_fresh_ad():
    ex = FakeExtractor(page_ads={"P1": [make_ad("a1", "P1", "GemCo", "https://x.com", days_old=3)]})
    assert asyncio.run(confirm_scaling_on_meta(ex, "P1", NOW_EPOCH, 14)) is False


def test_run_scaling_confirm_filters_to_confirmed():
    ex = FakeExtractor(page_ads={
        "P1": [make_ad("a1", "P1", "A", "https://x.com", days_old=20)],
        "P2": [make_ad("a2", "P2", "B", "https://x.com", days_old=3)],
    })
    confirmed = asyncio.run(run_scaling_confirm(ex, ["P1", "P2"], NOW_EPOCH, 14))
    assert confirmed == ["P1"]
