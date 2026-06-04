from agf.discovery.firehose_fingerprint import (
    detect_signatures, classify_funnel_intent, parse_publicwww,
)

PAID = ('<script src="https://cdn.superwall.me/runtime/entrypoint.js"></script>'
        '<script>fbq("init", "123")</script>'
        '<script src="https://cdn.branch.io/branch-latest.min.js"></script>')
ORGANIC = "<html><body>just a blog, no SDKs here</body></html>"


def test_detect_signatures_on_paid_funnel():
    assert {"paywall", "pixel", "mmp"} <= detect_signatures(PAID)


def test_classify_funnel_intent():
    assert classify_funnel_intent(PAID) is True
    assert classify_funnel_intent(ORGANIC) is False
    # pixel alone (no paywall SDK) is not enough
    assert classify_funnel_intent('<script>fbq("init")</script>') is False


def test_parse_publicwww_dedups_domains():
    text = ("https://getgem.com/quiz\n"
            "https://fitfunnel.io/\n"
            "https://getgem.com/other\n")
    assert parse_publicwww(text) == ["getgem.com", "fitfunnel.io"]
