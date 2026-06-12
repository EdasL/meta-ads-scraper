from agf.discovery.firehose_fingerprint import detect_signatures, classify_funnel_intent, parse_publicwww


def test_detect_checkout_solidgate_and_stripe():
    assert "checkout" in detect_signatures('<script src="https://cdn.solidgate.com/js/solid-form.js">')
    assert "checkout" in detect_signatures('<script src="https://js.stripe.com/v3/"></script>')
    assert "checkout" in detect_signatures('<script src="https://primer.io/sdk"></script>')


def test_detect_mmp_and_store():
    assert "mmp" in detect_signatures('<a href="https://brand.onelink.me/abc">get the app</a>')
    assert "store" in detect_signatures('<a href="https://apps.apple.com/us/app/x/id123">iOS</a>')


def test_detect_pixel_via_gtm():
    assert "pixel" in detect_signatures('<script src="https://www.googletagmanager.com/gtm.js?id=GTM-X">')
    assert "pixel" in detect_signatures("fbq('init','123')")


def test_app_side_sdks_are_not_checkout():
    # RevenueCat/Superwall/Adapty are app-side — they matched 0/6 live, must NOT be 'checkout'
    assert "checkout" not in detect_signatures('<script src="https://api.revenuecat.com/v1"></script>')


def test_classify_funnel_intent_is_checkout_and_pixel():
    html = '<script src="https://cdn.solidgate.com/js/solid-form.js"></script>' + "fbq('init')"
    assert classify_funnel_intent(html) is True
    assert classify_funnel_intent('<script src="https://js.stripe.com/v3/"></script>') is False  # pixel missing


def test_parse_publicwww_dedups_domains():
    text = ("https://getgem.com/quiz\n"
            "https://fitfunnel.io/\n"
            "https://getgem.com/other\n")
    assert parse_publicwww(text) == ["getgem.com", "fitfunnel.io"]
