from agf.discovery.web_identity import parse_web_identity

HTML = '''<html><head>
<meta property="og:title" content="Sleep Tight" />
<meta property="og:description" content="Better sleep in 7 days." />
<link rel="icon" href="/favicon.ico" />
</head><body></body></html>'''

def test_extracts_og_and_favicon():
    wi = parse_web_identity(HTML, "https://trysleeptight.app/start")
    assert wi.name == "Sleep Tight"
    assert wi.description == "Better sleep in 7 days."
    assert wi.icon_url == "https://trysleeptight.app/favicon.ico"

def test_falls_back_to_meta_description_and_title_tag():
    html = ('<html><head><title>Bossed AI</title>'
            '<meta name="description" content="AI for founders."></head></html>')
    wi = parse_web_identity(html, "https://app.bossed.ai/")
    assert wi.name == "Bossed AI"
    assert wi.description == "AI for founders."
    # no explicit icon link -> default to /favicon.ico at the origin
    assert wi.icon_url == "https://app.bossed.ai/favicon.ico"

def test_uses_fallback_name_when_no_title():
    wi = parse_web_identity("<html><head></head></html>", "https://x.co/",
                            fallback_name="Page Name")
    assert wi.name == "Page Name"
    assert wi.description is None


def test_og_image_used_as_icon_when_no_link_rel_icon():
    html = ('<html><head>'
            '<meta property="og:title" content="Bossed AI" />'
            '<meta property="og:image" content="/og-image.png" />'
            '</head></html>')
    wi = parse_web_identity(html, "https://app.bossed.ai/")
    # og:image should be preferred over the generic /favicon.ico default
    assert wi.icon_url == "https://app.bossed.ai/og-image.png"


def test_link_rel_icon_wins_over_og_image():
    html = ('<html><head>'
            '<meta property="og:image" content="/og-image.png" />'
            '<link rel="icon" href="/logo.ico" />'
            '</head></html>')
    wi = parse_web_identity(html, "https://app.bossed.ai/")
    # explicit link rel=icon wins over og:image
    assert wi.icon_url == "https://app.bossed.ai/logo.ico"


def test_default_favicon_when_neither_link_nor_og_image():
    html = '<html><head><meta property="og:title" content="Foo" /></head></html>'
    wi = parse_web_identity(html, "https://foo.example.com/page")
    assert wi.icon_url == "https://foo.example.com/favicon.ico"
