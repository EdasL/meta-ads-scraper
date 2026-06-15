"""Funnel handling (master-plan §6 P6-T4/T5/T6/T7): resolve the landing URL (via the
Plan 2 resolver — no second resolver), dismiss CMP banners, screenshot the funnel
step-by-step → S3, and best-effort pricing extraction. Playwright is the optional
standby dep (lazy-imported)."""
import hashlib
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from html import unescape
from agf.discovery.firehose_fingerprint import detect_signatures
from agf.discovery.quiz_detect import score_quiz_screen, is_question_screen
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
    "#CybotCookiebotDialogBodyButtonAccept",                       # Cookiebot (simple)
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",      # Cookiebot (multilevel)
    "#CybotCookiebotDialogBodyButtonAcceptAll",                    # Cookiebot (alt)
    "#accept-recommended-btn-handler",
    ".fc-cta-consent",                       # Google Funding Choices
    "[aria-label='Accept all']",
    "[aria-label='Accept All']",
    "[aria-label='Allow all']",
    "button[mode='primary']",                # Quantcast
    ".cmp-btn-accept-all",
    "button.osano-cm-accept-all",            # Osano
)

# Visible-text accept buttons — last-resort fallback when no known selector matches.
_CMP_ACCEPT_TEXTS = ("Accept all", "Accept All", "Allow all", "Allow All",
                     "Accept all cookies", "I accept", "Got it", "Agree")


def autoconsent_cmp(page) -> bool:
    """Dismiss a cookie/consent banner. True if one was dismissed, False if none present.

    Clicks carry a SHORT timeout: a detached/stale element default-clicks for the full
    30s (this is what stalled the enrich batch on consent-walled funnels). Best-effort —
    per-candidate errors are swallowed."""
    def _try(el) -> bool:
        if not el:
            return False
        try:
            if not el.is_visible():
                return False
        except Exception:
            return False
        try:
            el.click(timeout=2_500)
            return True
        except Exception:
            return False

    for sel in _CMP_ACCEPT_SELECTORS:
        try:
            if _try(page.query_selector(sel)):
                return True
        except Exception:
            continue
    for t in _CMP_ACCEPT_TEXTS:
        try:
            if _try(page.query_selector(f'text="{t}"')):
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
def _playwright_page(proxy=None, timeout_ms=30_000, ua=None):
    from playwright.sync_api import sync_playwright   # lazy: optional dep
    with sync_playwright() as p:
        ctx_kw = {}
        if ua:
            ctx_kw["user_agent"] = ua
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


# iOS UA so funnel pages serve the app smart-banner / store links / MMP deep-links (the
# install tells are UA-gated — a desktop UA hides them). Verified live: betterme.world
# rendered with this UA + settle surfaces mmp+store+pixel; without it, nothing.
RENDER_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")


def _settle(p, settle_ms):
    """Wait for network-idle + a short settle and dismiss the CMP (best-effort).

    The CMP is dismissed twice: once at network-idle, and again after the settle wait.
    Async consent managers (Cookiebot, OneTrust) often mount their banner AFTER
    network-idle, so a single early sweep misses them and the banner then occludes the
    real quiz CTA — which is exactly how consent-walled funnels scored quiz_steps=0."""
    if hasattr(p, "wait_for_load_state"):
        try:
            p.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:
            pass
    autoconsent_cmp(p)
    if hasattr(p, "wait_for_timeout"):
        try:
            p.wait_for_timeout(settle_ms)
        except Exception:
            pass
    autoconsent_cmp(p)


def _content(p) -> str:
    try:
        return p.content()
    except Exception:
        return ""


def render_landing_html(start_url, *, page=None, proxy=None, ua=RENDER_UA,
                        settle_ms=2500, timeout_ms=30_000) -> str:
    """Render a funnel landing page and return its settled DOM HTML — the input to
    firehose_fingerprint.detect_signatures. Renders with an iOS UA (so the app
    smart-banner/MMP/store tells appear), waits for network-idle + a short settle (so the
    GTM-injected pixel/MMP land), and dismisses the CMP. Renders DIRECT by default — the
    residential proxy is for Meta egress, not landing pages (it times these out). Pass
    `page` to drive an injected page (tests)."""
    def _go(p):
        p.goto(start_url)
        _settle(p, settle_ms)
        return _content(p)
    if page is not None:
        return _go(page)
    with _playwright_page(proxy, timeout_ms, ua=ua) as p:
        return _go(p)


@dataclass(frozen=True)
class ProbeResult:
    """One rendered landing plus the quiz click-probe outcome."""
    landing_html: str
    screens: tuple    # rendered HTML of each distinct question screen observed
    quiz_steps: int   # distinct question screens seen, landing included (0 = no quiz)


def _click_text(page, t) -> bool:
    """Click the element matching an answer text: exact match first, then a first-words
    substring fallback — option text often carries junk appended by lazy widgets
    ("Running for men Loading...") that breaks the exact match."""
    selectors = [f'text="{t}"']
    words = t.split()
    if len(words) > 1:
        selectors.append("text=" + " ".join(words[:3]))
    for sel in selectors:
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


def _back(page) -> bool:
    try:
        page.go_back()
        return True
    except Exception:
        return False


# Start-CTA texts for quiz funnels whose landing is a splash screen, not the first
# question (BetterMe: "Quiz: ..." headline + a lone CONTINUE). Unquoted text= matches
# case-insensitively as a substring.
_START_CTA_TEXTS = ("take the quiz", "start quiz", "get started", "continue", "start")


def _click_start_cta(page) -> bool:
    for t in _START_CTA_TEXTS:
        try:
            el = page.query_selector(f"text={t}")
        except Exception:
            el = None
        if el:
            try:
                el.click()
                return True
            except Exception:
                continue
    return False


def render_and_probe(start_url, *, max_clicks=4, page=None, proxy=None, ua=RENDER_UA,
                     settle_ms=2500, timeout_ms=30_000) -> ProbeResult:
    """Render a funnel landing and, when it scores as a question screen, click answers
    (<= max_clicks total) until a SECOND distinct question screen confirms a real
    multi-step quiz. An answer that leads off the quiz (e.g. a gender screen-out like
    femrun's male -> /women-only dead end) is rolled back with history.back() and the
    next answer is tried. The probe is skipped when the landing already carries a
    payoff signature (mmp/store/checkout) — classify_funnel confirms those without
    quiz evidence, so the clicks would be wasted work. Pass `page` to drive an
    injected page (tests)."""
    def _go(p):
        p.goto(start_url)
        _settle(p, settle_ms)
        landing = _content(p)
        screens = [landing]
        cur = score_quiz_screen(landing)
        steps = 1 if is_question_screen(cur) else 0
        payoff = {"mmp", "store", "checkout"} & detect_signatures(landing)
        clicks_left = 0 if payoff else max_clicks
        if steps == 0 and clicks_left and cur.quiz_words:
            # splash landing with quiz copy but no question yet (BetterMe's
            # "Quiz: ..." headline + lone CONTINUE) — hop the start CTA once
            if _click_start_cta(p):
                clicks_left -= 1
                _settle(p, min(settle_ms, 1500))
                html = _content(p)
                nxt = score_quiz_screen(html)
                if is_question_screen(nxt):
                    screens.append(html)
                    steps = 1
                    cur = nxt
        while steps == 1 and clicks_left > 0:
            advanced = False
            for t in cur.answer_cluster:
                if clicks_left <= 0:
                    break
                if not _click_text(p, t):
                    continue
                clicks_left -= 1
                _settle(p, min(settle_ms, 1500))
                html = _content(p)
                nxt = score_quiz_screen(html)
                if is_question_screen(nxt) and set(nxt.answer_cluster) != set(cur.answer_cluster):
                    screens.append(html)
                    steps += 1
                    cur = nxt
                    advanced = True
                    break
                # dead end / no-op — roll back and try the next answer
                if not _back(p):
                    clicks_left = 0
                    break
                _settle(p, min(settle_ms, 1500))
            if not advanced:
                break
        return ProbeResult(landing_html=landing, screens=tuple(screens), quiz_steps=steps)
    if page is not None:
        return _go(page)
    with _playwright_page(proxy, timeout_ms, ua=ua) as p:
        return _go(p)
