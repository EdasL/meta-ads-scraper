"""Funnel handling (master-plan §6 P6-T4/T5/T6/T7): resolve the landing URL (via the
Plan 2 resolver — no second resolver), dismiss CMP banners, screenshot the funnel
step-by-step → S3, and best-effort pricing extraction. Playwright is the optional
standby dep (lazy-imported)."""
import hashlib
import json
import re
from contextlib import contextmanager
from html import unescape
from agf.storage.s3 import put_bytes
from agf.enrich_full.schemas import ResolvedTarget, PricingExtract, FunnelStep


async def resolve_landing(extractor, url, ua="ios") -> ResolvedTarget:
    """Resolve an ad's destination via the Plan 2 Extractor.resolve_deeplink (§3.4).
    Returns a ResolvedTarget carrying both the original landing URL and the final URL."""
    resolved = await extractor.resolve_deeplink(url, ua=ua)
    return ResolvedTarget(kind=resolved.intent.kind, store_id=resolved.intent.store_id,
                          landing_url=url, final_url=resolved.final_url)


_JSONLD = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL)
_PRICE = re.compile(r'[$€£]\s?(\d{1,4}(?:[.,]\d{2})?)')
_CCY_SYM = {"$": "USD", "€": "EUR", "£": "GBP"}
_CCY_WORD = re.compile(r'\b(USD|EUR|GBP)\b', re.IGNORECASE)
_TRIAL = re.compile(
    r'(\d+[\s-]?(?:day|week|month)s?\s+free(?:\s+trial)?'
    r'|free\s+(?:\d+[\s-]?(?:day|week|month)s?\s+)?trial)', re.IGNORECASE)


def _walk_jsonld(obj, prices, ccy):
    if isinstance(obj, dict):
        if "price" in obj and obj["price"] is not None:
            prices.append(str(obj["price"]))
        if obj.get("priceCurrency"):
            ccy.append(str(obj["priceCurrency"]))
        for v in obj.values():
            _walk_jsonld(v, prices, ccy)
    elif isinstance(obj, list):
        for v in obj:
            _walk_jsonld(v, prices, ccy)


def extract_pricing(html) -> PricingExtract | None:
    """Pull pricing/trial from a paywall page. JSON-LD Offer first, regex fallback.
    None when nothing pricing-like is found."""
    html = html or ""
    prices, ccy = [], []
    for block in _JSONLD.findall(html):
        try:
            _walk_jsonld(json.loads(block.strip()), prices, ccy)
        except (ValueError, TypeError):
            continue
    currency = ccy[0].upper() if ccy else None
    text = unescape(html)
    if not prices:                                    # fallback: visible-text prices
        prices = [m.group(1).replace(",", ".") for m in _PRICE.finditer(text)]
        if currency is None:
            sym = next((c for c in _CCY_SYM if c in text), None)
            mw = _CCY_WORD.search(text)
            currency = (_CCY_SYM[sym] if sym else None) or (mw.group(1).upper() if mw else None)
    seen, uniq = set(), []
    for p in prices:
        if p not in seen:
            seen.add(p); uniq.append(p)
    mt = _TRIAL.search(text)
    trial = mt.group(0).strip() if mt else None
    if not uniq and not trial:
        return None
    return PricingExtract(prices=tuple(uniq), currency=currency, trial=trial)


_CMP_ACCEPT_SELECTORS = (
    "#onetrust-accept-btn-handler",
    "#CybotCookiebotDialogBodyButtonAccept",
    "#accept-recommended-btn-handler",
    ".fc-cta-consent",                       # Google Funding Choices
    "[aria-label='Accept all']",
    "[aria-label='Accept All']",
    "button[mode='primary']",                # Quantcast
)


def autoconsent_cmp(page) -> bool:
    """Dismiss a cookie/consent banner (selector sweep). True if one was dismissed,
    False if none present. Best-effort: per-selector errors are swallowed."""
    for sel in _CMP_ACCEPT_SELECTORS:
        try:
            el = page.query_selector(sel)
        except Exception:
            el = None
        if el:
            try:
                el.click()
                return True
            except Exception:
                continue
    return False


_NEXT_SELECTORS = ("a.cta", "button[type='submit']", ".btn-primary", "a.button",
                   "button.primary", "[data-testid='continue']")


def _url_hash(url: str) -> str:
    return hashlib.sha256((url or "").encode()).hexdigest()[:16]


def _next_element(page):
    for sel in _NEXT_SELECTORS:
        try:
            el = page.query_selector(sel)
        except Exception:
            el = None
        if el:
            return el
    return None


def _safe_title(page):
    try:
        return page.title()
    except Exception:
        return None


def _run_capture(page, start_url, max_steps, put, s3):
    steps = []
    page.goto(start_url)
    for i in range(max_steps):
        autoconsent_cmp(page)
        key = None
        try:
            shot = page.screenshot()
            key = f"funnels/{_url_hash(start_url)}/step{i + 1}.png"
            put(key, shot, "image/png", client=s3)
        except Exception:
            key = None
        steps.append(FunnelStep(step=i + 1, url=getattr(page, "url", start_url),
                                screenshot_s3_key=key, title=_safe_title(page)))
        if i == max_steps - 1:
            break
        nxt = _next_element(page)
        if not nxt:
            break
        try:
            nxt.click()
            if hasattr(page, "wait_for_load_state"):
                page.wait_for_load_state("domcontentloaded")
        except Exception:
            break
    return steps


@contextmanager
def _playwright_page(proxy=None, timeout_ms=30_000):
    from playwright.sync_api import sync_playwright   # lazy: optional dep
    with sync_playwright() as p:
        ctx_kw = {}
        if proxy:
            host = proxy.split("@")[-1]
            ctx_kw["proxy"] = {"server": f"http://{host}"}
            if "@" in proxy:
                user, _, pw = proxy.split("@")[0].partition(":")
                ctx_kw["proxy"].update(username=user, password=pw)
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(**ctx_kw)
        page = context.new_page()
        page.set_default_timeout(timeout_ms)
        try:
            yield page
        finally:
            browser.close()


def capture_funnel(start_url, *, max_steps=3, put=put_bytes, s3=None,
                   page=None, proxy=None) -> list:
    """Screenshot a funnel step-by-step (reuses the Plan 2 Playwright standby; one
    context, capped at max_steps). Each step's PNG → S3; returns FunnelSteps. Pass
    `page` to drive an existing page (tests); otherwise a headless chromium is opened."""
    if page is not None:
        return _run_capture(page, start_url, max_steps, put, s3)
    with _playwright_page(proxy) as p:
        return _run_capture(p, start_url, max_steps, put, s3)
