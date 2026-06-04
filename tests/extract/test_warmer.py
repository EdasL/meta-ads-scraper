import importlib
import pytest


def test_warmer_module_imports_without_playwright():
    # The module must import even when playwright is absent (lazy import inside fn).
    mod = importlib.import_module("agf.extract.warmer")
    assert hasattr(mod, "browser_warm")


@pytest.mark.integration
def test_browser_warm_returns_tokens():
    from agf.extract.warmer import browser_warm
    tokens = browser_warm(
        "https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
        "&country=US&q=workout%20app&media_type=all&search_type=keyword_unordered"
    )
    assert tokens.lsd and tokens.doc_id
