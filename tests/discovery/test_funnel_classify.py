from agf.discovery import funnel_classify as fc
from agf.parse import AdRecord


def _ad(link, cta="LEARN_MORE", body="hello"):
    return AdRecord("a1", "P1", "Page", True, None, None, 1, cta, "x", link, body)


def test_landing_domain_unwraps_fb_shim_and_strips_www():
    shim = "https://l.facebook.com/l.php?u=https%3A%2F%2Fwww.quiz.betterme.world%2Fstart%3Fa%3D1"
    assert fc.landing_domain(shim) == "quiz.betterme.world"
    assert fc.landing_domain("https://www.simple.life/") == "simple.life"
    assert fc.landing_domain(None) is None


def test_parse_utms():
    got = fc.parse_utms("https://quiz.x.com/?utm_source=fb&utm_campaign=wallpilates_es")
    assert got["utm_source"] == "fb"
    assert got["utm_campaign"] == "wallpilates_es"
    assert got["utm_medium"] is None and got["utm_content"] is None


def test_matched_funnel_host():
    assert fc.matched_funnel_host("https://quiz.example.com/x") == "quiz."
    assert fc.matched_funnel_host("https://example.com/x") is None


def test_funnel_signal_true_paths():
    assert fc.funnel_signal(_ad("https://example.com/p", cta="NO"), "funnel_phrase") is True
    assert fc.funnel_signal(_ad("https://try.example.com/p", cta="NO", body=""), "niche_term") is True
    assert fc.funnel_signal(_ad("https://example.com/p", cta="NO", body="take the quiz"), "niche_term") is True
    assert fc.funnel_signal(_ad("https://example.com/p", cta="SUBSCRIBE", body=""), "niche_term") is True


def test_funnel_signal_false_for_plain_web():
    assert fc.funnel_signal(_ad("https://example.com/p", cta="NO", body="just words"), "niche_term") is False
