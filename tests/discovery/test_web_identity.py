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
