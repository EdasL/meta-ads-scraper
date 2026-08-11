"""Top-N gem enrichment orchestrator (master-plan §6 P6-T10). For a flagged gem
(advertiser), enrich its top-N ads: resolve landing → app-store metadata OR funnel
capture + pricing, download deduped creatives, then company enrichment. conn-first
(no commit); cross-cutting deps arrive via EnrichDeps (worker adapter §3.9)."""
import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

log = logging.getLogger(__name__)
from sqlalchemy import text
from agf.db import repo
from agf.enrich import emerging_from_release
from agf.enrich_full.schemas import EnrichResult
from agf.enrich_full.funnel_capture import resolve_landing, capture_funnel, render_and_probe
from agf.discovery.funnel_confirm import classify_funnel
from agf.discovery.funnel_classify import SOCIAL_HOSTS, is_store_bounce

# A funnel landing is never a store or social page. apps.apple.com/play.google.com
# are the install branch; facebook.com/<page_id> is the advertiser's own page and
# hangs Playwright for the full timeout. Skip the probe for these hosts entirely.
STORE_HOSTS = frozenset({"apps.apple.com", "itunes.apple.com", "play.google.com",
                         "chromewebstore.google.com", "chrome.google.com",
                         "microsoftedge.microsoft.com", "addons.mozilla.org"})
from agf.enrich_full.creatives import download_creative, link_duplicate_creative
from agf.enrich_full.appstore import lookup_itunes_batch, lookup_google_play
from agf.enrich_full.company import enrich_company


@dataclass
class EnrichDeps:
    """Cross-cutting deps for enrich_gem (all injectable; production wiring lives in
    make_enrich_handler)."""
    extractor: object                  # Plan 2 facade (resolve_deeplink, search)
    itunes_client: object = None       # httpx-like client for iTunes Lookup
    gp_runner: object = None           # node runner for the gplay sidecar
    fetch_text: object = None          # (url) -> html str  (pricing + tech)
    fetch_bytes: object = None         # (url) -> (bytes, content_type)  (creatives)
    put: object = None                 # put_bytes-compatible
    s3: object = None                  # boto3/MinIO client
    whois_fn: object = None            # whois.whois-compatible
    page: object = None                # Playwright page or None (open real chromium)
    proxy: str | None = None
    capture: bool = True               # run funnel screenshots
    creative_urls: dict | None = None  # {external_ad_id: [(type, url)]}
    check_alive: object = None         # (url) -> bool landing liveness; None = default
                                       # httpx probe, applied only when a real chromium
                                       # would be launched (page is None)


def _domain_of(url):
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith("["):              # IPv6 literal — not a registrable domain
            return None
        return host[4:] if host.startswith("www.") else (host or None)
    except Exception:
        return None


# Web-to-app funnel markers an advertiser stamps on the ad's destination. A paid
# Meta ad pointing off-store to a web page carrying any of these (or a deep path)
# is a conversion funnel even when the landing is a single screen the quiz-probe
# can't confirm — exactly the rows the strict classifier dropped at write time
# (e.g. 10mf.siiites.com/?pid=w2a_meta&af_ad=...). A bare brand homepage with none
# of these stays out, so the directory's Quiz column isn't flooded with non-funnels.
_FUNNEL_URL_MARKERS = ("w2a", "web2app", "af_ad", "af_adset", "af_channel",
                       "pid=", "utm_", "deep_link", "campaign")


def _looks_like_funnel(link_url, final_url, confirmed) -> bool:
    """Whether an off-store web landing should be recorded as the app's funnel.
    True when the classifier confirmed it, or the ad's link/landing shows
    web-to-app intent (a funnel marker or a deep, non-homepage path)."""
    if confirmed:
        return True
    for u in (link_url, final_url):
        if not u:
            continue
        low = u.lower()
        if any(m in low for m in _FUNNEL_URL_MARKERS):
            return True
        try:
            if urlparse(u).path.strip("/"):       # deep path beyond the bare homepage
                return True
        except Exception:
            pass
    return False


def _is_emerging(release_date, now):
    try:
        return emerging_from_release(release_date, now) if release_date else None
    except (ValueError, TypeError):
        return None


async def _lookup_app(deps, kind, store_id):
    if kind == "ios" and deps.itunes_client is not None:
        return lookup_itunes_batch(deps.itunes_client, [store_id]).get(str(store_id))
    if kind == "android":
        return lookup_google_play([store_id], runner=deps.gp_runner).get(str(store_id))
    return None


def _media_only_pass(conn, advertiser_id, deps: EnrichDeps, *, done, cap) -> int:
    """Download ONE media file for every other prefetched ad of this gem. The
    listing pull already paid for these URLs (prefetch_creative_urls); without
    this pass every ad beyond top-N renders a textless card. Ads that already
    have s3-backed media are skipped; duplicate bytes are linked, not re-stored.
    Returns the number of ads that gained media."""
    n = 0
    for ext_id, urls in (deps.creative_urls or {}).items():
        if cap <= 0:
            break
        if ext_id in done or not urls:
            continue
        ad_id = conn.execute(text(
            "SELECT id FROM public.ads WHERE advertiser_id=:a AND external_ad_id=:e"),
            {"a": advertiser_id, "e": ext_id}).scalar()
        if not ad_id:
            continue
        if conn.execute(text(
                "SELECT 1 FROM public.creatives WHERE ad_id=:a AND s3_key IS NOT NULL LIMIT 1"),
                {"a": ad_id}).first():
            continue
        ctype, url = urls[0]
        try:                                  # fbcdn URLs expire → per-ad isolation
            ca = download_creative(conn, ad_id, url, ctype, fetch=deps.fetch_bytes,
                                   put=deps.put, s3=deps.s3)
        except Exception as exc:
            # A dead fbcdn URL is routine; a storage failure is not. Bare
            # continue hid 5 days of SignatureDoesNotMatch (2026-08-06..11).
            log.warning("media download failed for ad %s: %s", ad_id, exc)
            continue
        cap -= 1
        if ca.s3_key:
            n += 1
        elif ca.creative_id is not None and link_duplicate_creative(
                conn, ad_id, ca.creative_id, ctype):
            n += 1
    return n


# Unresolved ad-tech URL macro (e.g. sng.link "...&cp={{ad.id}}") — the ad server
# never substituted it, so the landing can only 404/abort; rendering it is pure waste.
_URL_MACRO = re.compile(r"\{\{[^{}]*\}\}")

_LIVENESS_TIMEOUT_SEC = 6.0


def _url_alive(url, timeout=_LIVENESS_TIMEOUT_SEC) -> bool:
    """Transport-level landing liveness, headers only (the render pays for the body).
    ANY HTTP response — including 403/405/500 — counts as alive, because bot walls
    front real funnels; dead means DNS failure, refused connection, or a hang past
    `timeout`. Cheap insurance before each ~30s-timeout chromium render."""
    import httpx
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as c:
            with c.stream("GET", url):
                pass
        return True
    except Exception:
        return False


class FunnelProbeBreaker:
    """Per-advertiser dead-funnel circuit breaker. Hunt-surfaced advertisers carry
    dead/slow landings; each failed render burns up to ~40s of the enrich deadline
    (30s goto + settles), and top_n=5 of them pinned ~46% of enrich jobs at the
    SIGALRM wall (2026-07-13..15 regression). After max_fails failed probes the
    advertiser's remaining probes are skipped — funnel rows are still written from
    URL intent, and the store/creative/company enrichment is untouched."""

    def __init__(self, max_fails=2):
        self.max_fails = max_fails
        self.fails = 0

    def allow(self) -> bool:
        return self.fails < self.max_fails

    def fail(self) -> None:
        self.fails += 1


async def record_web_funnel(conn, ad_id, link_url, target, deps: EnrichDeps,
                            breaker: FunnelProbeBreaker | None = None) -> int:
    """Probe a resolved web landing and record it as the app's funnel when it shows
    web-to-app intent. `target` is an already-resolved ResolvedTarget. Returns 1 if a
    public.funnels row was written, else 0.

    The ad's web destination IS the funnel (matview 129's directive): record it for
    any landing that shows web-to-app intent and let the matview's store/social/MMP
    regex be the only gate. render_and_probe still runs to look for a payoff — a
    verified multi-step quiz, a checkout/paywall, or a pixel-tracked page — which both
    EARNS a screenshot (the only added cost, so unconfirmed funnels stay cheap) and
    stores kind/quiz_steps for transparency. The earlier write-gate required a
    confirmed payoff and silently dropped the bulk of real funnels (single-screen w2a
    landings that trip no quiz). Shared by enrich_gem and the funnel-repair stage."""
    if target.kind != "web" or not target.final_url:
        return 0
    funnel_host = _domain_of(target.final_url)
    if (funnel_host in SOCIAL_HOSTS or funnel_host in STORE_HOSTS
            or is_store_bounce(funnel_host)):
        return 0
    kind = quiz_steps = shot = None
    confirmed = False
    probe = None
    skip = None
    if _URL_MACRO.search(target.final_url):
        skip = "unresolved ad-tech macro in URL"
    elif breaker is not None and not breaker.allow():
        skip = "dead-funnel breaker open for this advertiser"
    else:
        check = deps.check_alive
        if check is None and deps.page is None:   # about to pay for a real chromium
            check = _url_alive
        if check is not None and not await asyncio.to_thread(check, target.final_url):
            skip = "landing transport-dead (no HTTP response)"
            if breaker is not None:
                breaker.fail()
    if skip is not None:
        log.info("funnel probe skipped for %s: %s", target.final_url, skip)
    else:
        # Playwright's SYNC API can't run inside the asyncio loop → offload.
        try:
            probe = await asyncio.to_thread(
                render_and_probe, target.final_url, page=deps.page, proxy=deps.proxy)
        except Exception as e:
            probe = None          # a slow/dead landing must not kill the advertiser
            if breaker is not None:
                breaker.fail()
            # ...but never silently: a swallowed probe means the funnel probe fails
            # invisibly (e.g. browser launch dying on a bad TMPDIR).
            log.warning("funnel probe failed for %s: %s: %s",
                        target.final_url, type(e).__name__, e)
    if probe is not None:
        kind, status, _store, _sid, _drop, _payoff = classify_funnel(
            probe.landing_html, target, funnel_host=funnel_host,
            quiz_steps=probe.quiz_steps, funnel_url=target.final_url)
        quiz_steps = probe.quiz_steps
        confirmed = status == "confirmed" and kind in ("subscribe", "unknown")
        if confirmed:
            # Confirmed funnel — only now pay for the screenshot capture.
            steps = await asyncio.to_thread(
                capture_funnel, target.final_url, page=deps.page, put=deps.put,
                s3=deps.s3, proxy=deps.proxy)
            shot = steps[0].screenshot_s3_key if steps else None
    if confirmed or _looks_like_funnel(link_url, target.final_url, confirmed):
        repo.upsert_funnel(conn, ad_id, link_url, target.final_url, shot,
                           kind=kind, quiz_steps=quiz_steps)
        return 1
    return 0


async def enrich_gem(conn, advertiser_id, *, top_n=5, media_cap=120,
                     deps: EnrichDeps) -> EnrichResult:
    rows = conn.execute(text(
        "SELECT id, external_ad_id, link_url FROM public.ads WHERE advertiser_id=:a "
        "ORDER BY last_seen DESC NULLS LAST, id"),
        {"a": advertiser_id}).all()
    # Store/social ads all resolve to the same app and would otherwise eat the
    # whole top_n probe budget by recency, burying a lone funnel ad past the cut
    # (the reason most gems showed no Quiz link). Float off-store ads — the funnel
    # candidates — ahead; sorted() is stable so recency order holds within each
    # group, and store ads still fill the remaining slots for app metadata.
    def _off_store(link):
        host = _domain_of(link)
        return bool(host) and host not in STORE_HOSTS and host not in SOCIAL_HOSTS
    ads = sorted(rows, key=lambda r: not _off_store(r.link_url))[:top_n]
    now = datetime.now(timezone.utc)
    n_funnels = n_creatives = 0
    last_app_meta = None
    company_domain = company_html = None
    breaker = FunnelProbeBreaker()     # shared across this advertiser's ads

    for ad in ads:
        target = await resolve_landing(deps.extractor, ad.link_url)
        if target.kind in ("ios", "android") and target.store_id:
            try:                              # app-store lookups hit the network; degrade
                meta = await _lookup_app(deps, target.kind, target.store_id)
            except Exception:
                meta = None
            if meta:
                app_id = repo.upsert_app(conn, {
                    "store": target.kind, "store_id": meta.track_id, "name": meta.name,
                    "developer": meta.seller, "genre": meta.genre,
                    "release_date": meta.release_date, "rating": meta.avg_rating,
                    "rating_count": meta.rating_count,
                    "installs": getattr(meta, "installs", None),
                    "icon_url": getattr(meta, "icon_url", None),
                    "screenshots": getattr(meta, "screenshots", None),
                    "price_label": getattr(meta, "price_label", None),
                    "is_game": getattr(meta, "is_game", None),
                    "is_emerging": _is_emerging(meta.release_date, now)})
                conn.execute(text("UPDATE public.ads SET app_id=:p WHERE id=:i"),
                             {"p": app_id, "i": ad.id})
                last_app_meta = meta
        elif target.kind == "web":
            company_domain = company_domain or _domain_of(target.final_url)
            if deps.fetch_text is not None and company_html is None:
                try:
                    company_html = deps.fetch_text(target.final_url)
                except Exception:
                    company_html = None
            if deps.capture:
                n_funnels += await record_web_funnel(conn, ad.id, ad.link_url, target,
                                                     deps, breaker=breaker)

        for ctype, url in (deps.creative_urls or {}).get(str(ad.external_ad_id), []):
            try:                              # fbcdn URLs expire → a dead one mustn't kill the gem
                ca = download_creative(conn, ad.id, url, ctype, fetch=deps.fetch_bytes,
                                       put=deps.put, s3=deps.s3)
            except Exception as exc:
                log.warning("media download failed for ad %s: %s", ad.id, exc)
                continue
            if ca.dedup_of is None:
                n_creatives += 1

    n_creatives += _media_only_pass(conn, advertiser_id, deps,
                                    done={str(ad.external_ad_id) for ad in ads},
                                    cap=media_cap)

    company = enrich_company(conn, advertiser_id, domain=company_domain,
                             html=company_html, app_meta=last_app_meta,
                             whois_fn=deps.whois_fn)
    return EnrichResult(advertiser_id=advertiser_id, n_ads_processed=len(ads),
                        funnels=n_funnels, creatives=n_creatives, company=company)


def make_enrich_handler(deps_factory, *, top_n=5, media_cap=120):
    """Adapter (§3.9): handler(conn, advertiser_id) for run_worker. deps_factory(conn,
    advertiser_id)->EnrichDeps builds per-gem deps (e.g. a fresh extractor.search for
    current creative URLs — fbcdn signed URLs expire)."""

    def handler(conn, advertiser_id):
        deps = deps_factory(conn, advertiser_id)
        res = asyncio.run(enrich_gem(conn, advertiser_id, top_n=top_n,
                                     media_cap=media_cap, deps=deps))
        return f"ads={res.n_ads_processed} funnels={res.funnels} creatives={res.creatives}"
    return handler
