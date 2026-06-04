from pathlib import Path
from agf.enrich_full.funnel_capture import extract_pricing

FIX = Path(__file__).parent / "fixtures"


def _read(name): return (FIX / name).read_text()


def test_jsonld_offers_and_trial():
    p = extract_pricing(_read("paywall_jsonld.html"))
    assert p is not None
    assert p.prices == ("9.99", "59.99")
    assert p.currency == "USD"
    assert "7-day free" in (p.trial or "")


def test_text_fallback_prices_and_trial():
    p = extract_pricing(_read("paywall_text.html"))
    assert p is not None
    assert "12.99" in p.prices and "99.00" in p.prices
    assert p.currency == "USD"            # from the $ symbol
    assert p.trial and "free trial" in p.trial.lower()


def test_no_pricing_returns_none():
    assert extract_pricing(_read("no_pricing.html")) is None
    assert extract_pricing("") is None
