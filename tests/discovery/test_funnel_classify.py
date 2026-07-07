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
    got = fc.parse_utms("https://quiz.example.com/?utm_source=fb&utm_campaign=wallpilates_es")
    assert got["utm_source"] == "fb"
    assert got["utm_campaign"] == "wallpilates_es"
    assert got["utm_medium"] is None and got["utm_content"] is None


def test_matched_funnel_host():
    assert fc.matched_funnel_host("https://quiz.example.com/x") == "quiz."
    assert fc.matched_funnel_host("https://example.com/x") is None


def test_matched_funnel_host_subdomain_hints_are_boundary_anchored():
    assert fc.matched_funnel_host("https://signup.ruutlabs.com/en/run") == "signup."
    assert fc.matched_funnel_host("https://api-quiz.betterme.world/x") == "quiz."
    assert fc.matched_funnel_host("https://automation.quiz.betterme.world/x") == "quiz."
    # substrings inside a label must NOT match ("go." in "logo.com", "my." in "dummy.")
    assert fc.matched_funnel_host("https://logo.com/x") is None
    assert fc.matched_funnel_host("https://dummy.example.com/x") is None


def test_matched_funnel_path():
    assert fc.matched_funnel_path("https://www.noom.com/survey/") == "/survey"
    assert fc.matched_funnel_path(
        "https://betterme-pilates.com/generated-questionary-x?flow=2117") == "questionary"
    assert fc.matched_funnel_path("https://example.com/pricing") is None


def test_funnel_signal_from_path_hint():
    assert fc.funnel_signal(_ad("https://www.noom.com/survey/", cta="NO", body=""), "niche_term") is True


def test_funnel_signal_true_paths():
    assert fc.funnel_signal(_ad("https://example.com/p", cta="NO"), "funnel_phrase") is True
    assert fc.funnel_signal(_ad("https://try.example.com/p", cta="NO", body=""), "niche_term") is True
    assert fc.funnel_signal(_ad("https://example.com/p", cta="NO", body="take the quiz"), "niche_term") is True
    assert fc.funnel_signal(_ad("https://example.com/p", cta="SUBSCRIBE", body=""), "niche_term") is True


def test_funnel_signal_false_for_plain_web():
    assert fc.funnel_signal(_ad("https://example.com/p", cta="NO", body="just words"), "niche_term") is False


# --- unit 2: store_ref_from_html ---

def test_store_ref_from_html_ios():
    html = '<a href="https://apps.apple.com/us/app/betterme/id1234567890">Download</a>'
    assert fc.store_ref_from_html(html) == ("ios", "1234567890")


def test_store_ref_from_html_itunes():
    html = '<a href="https://itunes.apple.com/app/id9876543">get it</a>'
    assert fc.store_ref_from_html(html) == ("ios", "9876543")


def test_store_ref_from_html_android():
    html = '<a href="https://play.google.com/store/apps/details?id=com.betterme.health">Android</a>'
    assert fc.store_ref_from_html(html) == ("android", "com.betterme.health")


def test_store_ref_from_html_ios_wins_over_android():
    # iOS link appears first — first match wins
    html = ('<a href="https://apps.apple.com/us/app/x/id111">ios</a>'
            '<a href="https://play.google.com/store/apps/details?id=com.x">android</a>')
    assert fc.store_ref_from_html(html) == ("ios", "111")


def test_store_ref_from_html_none_when_absent():
    assert fc.store_ref_from_html("<html>no app links here</html>") is None
    assert fc.store_ref_from_html("") is None
    assert fc.store_ref_from_html(None) is None


# --- unit 3: fold_web_funnel (shared web-funnel classifier) ---

from agf.discovery.funnel_classify import fold_web_funnel

_NOW = 1_749_000_000


def _fad(link, cta="LEARN_MORE", body="body", likes=500):
    return AdRecord("a1", "FOLDTEST1", "Fold Test", True, _NOW - 5 * 86_400, None, 1,
                    cta, "x", link, body, page_like_count=likes)


def test_fold_web_funnel_candidate_row():
    d, row = fold_web_funnel(_fad("https://quiz.example.com/start?utm_source=fb"),
                             keyword="hair loss app", technique="niche_term")
    assert d == "candidate"
    assert row["landing_domain"] == "quiz.example.com"
    assert row["funnel_host"] == "quiz." and row["utm_source"] == "fb"
    assert row["source_keyword"] == "hair loss app" and row["technique"] == "niche_term"
    assert row["body_text"] == "body"


def test_fold_web_funnel_store_link_is_app_link():
    d, row = fold_web_funnel(_fad("https://apps.apple.com/us/app/x/id12345"),
                             keyword="k", technique="niche_term")
    assert (d, row) == ("app_link", None)


def test_fold_web_funnel_plain_site_is_not_funnel():
    d, row = fold_web_funnel(_fad("https://plainsite.com/x", cta="NO", body="nothing"),
                             keyword="k", technique="niche_term")
    assert (d, row) == ("not_funnel", None)


def test_fold_web_funnel_phrase_layer_auto_passes_gate():
    # funnel_phrase layer bypasses funnel_signal — a plain site still folds
    d, row = fold_web_funnel(_fad("https://plainsite.com/x", cta="NO", body="nothing"),
                             keyword="take the quiz", technique="funnel_phrase")
    assert d == "candidate" and row["landing_domain"] == "plainsite.com"
