"""OPTIONAL standby warmer. Uses a real browser to load the Ad Library page and
harvest tokens when the pure-HTTP rd_challenge path (rd_challenge.py / tls_client.py)
fails. Playwright is imported lazily so the package works without it installed."""
from agf.extract.token_heal import heal_from_html


def browser_warm(url: str, *, proxy: str | None = None, timeout_ms: int = 30_000):
    """Launch a headless browser, load `url` (the browser runs the rd_challenge JS),
    and return SessionTokens parsed from the rendered HTML.

    Requires `playwright` + `playwright install chromium`. Swap in Camoufox here if
    Chromium gets detected (see research doc §2)."""
    from playwright.sync_api import sync_playwright  # lazy: optional dependency

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx_kw = {}
        if proxy:
            host = proxy.split("@")[-1]
            ctx_kw["proxy"] = {"server": f"http://{host}"}
            if "@" in proxy:
                user, _, pw = proxy.split("@")[0].partition(":")
                ctx_kw["proxy"].update(username=user, password=pw)
        context = browser.new_context(**ctx_kw)
        page = context.new_page()
        page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)  # let the rd_challenge JS run + reload
        html = page.content()
        browser.close()
    # the browser ran the challenge JS, so the DOM has LSD/fb_dtsg; doc_id may be in a
    # bundle, but heal_from_html (no session) falls back to a known-good doc_id here.
    return heal_from_html(html)
