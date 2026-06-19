from agf.discovery.firehose_fingerprint import (
    detect_signatures, classify_funnel_intent, parse_publicwww,
    _has_billing_language, classify_payoff,
)


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


# --- unit 1: physical/service signatures + _has_billing_language + classify_payoff ---

def test_detect_physical_shopify():
    assert "physical" in detect_signatures('<script src="https://cdn.shopify.com/s/files/1/0/shop.js">')
    assert "physical" in detect_signatures("myshopify.com is used here")
    assert "physical" in detect_signatures('<a href="/cart">cart</a>')
    assert "physical" in detect_signatures('<button class="add-to-cart">add</button>')
    assert "physical" in detect_signatures('/products/protein-powder')
    assert "physical" in detect_signatures('/collections/supplements')


def test_detect_service_calendly():
    assert "service" in detect_signatures("calendly.com/embed")
    assert "service" in detect_signatures("acuityscheduling.com/schedule")
    assert "service" in detect_signatures("as.me/myslot")
    assert "service" in detect_signatures("squareup.com/appointments")
    assert "service" in detect_signatures("Book a call with us today")
    assert "service" in detect_signatures("free consultation available")
    assert "service" in detect_signatures("schedule a call now")
    assert "service" in detect_signatures("get a quote")


def test_has_billing_language():
    assert _has_billing_language("cancel anytime during trial")
    assert _has_billing_language("$9.99 per month")
    assert _has_billing_language("$4.99/mo billed annually")
    assert _has_billing_language("$8 a month")
    assert _has_billing_language("billed to your card")
    assert _has_billing_language("subscription required")
    assert _has_billing_language("7 day trial then $19.99")
    assert _has_billing_language("then $14.99/month")
    assert not _has_billing_language("buy now one-time")
    assert not _has_billing_language("free shipping")


def test_classify_payoff_store_id_wins():
    # store_id kwarg overrides everything — app
    assert classify_payoff("", store_id="12345") == "app"
    assert classify_payoff("myshopify.com", store_id="99") == "app"


def test_classify_payoff_physical_beats_checkout():
    # Shopify also loads Stripe checkout — physical must win
    html = "cdn.shopify.com js.stripe.com/v3 fbq('init')"
    assert classify_payoff(html) == "physical"


def test_classify_payoff_service():
    html = "calendly.com/embed"
    assert classify_payoff(html) == "service"


def test_classify_payoff_store_signal_is_app():
    html = '<a href="https://apps.apple.com/us/app/betterme/id1". Download'
    assert classify_payoff(html) == "app"


def test_classify_payoff_mmp_is_app():
    html = '<a href="https://brand.onelink.me/abc">get the app</a>'
    assert classify_payoff(html) == "app"


def test_classify_payoff_web_sub_checkout_plus_billing():
    html = 'js.stripe.com/v3 fbq("init") cancel anytime'
    assert classify_payoff(html) == "web_sub"


def test_classify_payoff_web_sub_checkout_plus_pixel():
    html = 'js.stripe.com/v3 fbq("init")'
    assert classify_payoff(html) == "web_sub"


def test_classify_payoff_leadgen_fallback():
    html = "<html>generic site with nothing special</html>"
    assert classify_payoff(html) == "leadgen"
