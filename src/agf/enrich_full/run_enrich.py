"""Top-N gem enrichment orchestrator (master-plan §6 P6-T10). For a flagged gem
(advertiser), enrich its top-N ads: resolve landing → app-store metadata OR funnel
capture + pricing, download deduped creatives, then company enrichment. conn-first
(no commit); cross-cutting deps arrive via EnrichDeps (worker adapter §3.9)."""
import asyncio
import logging
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
from agf.discovery.funnel_classify import SOCIAL_HOSTS

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


def _domain_of(url):
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith("["):              # IPv6 literal — not a registrable domain
            return None
        return host[4:] if host.startswith("www.") else (host or None)
    except Exception:
        return None


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
        except Exception:
            continue
        cap -= 1
        if ca.s3_key:
            n += 1
        elif ca.creative_id is not None and link_duplicate_creative(
                conn, ad_id, ca.creative_id, ctype):
            n += 1
    return n


async def enrich_gem(conn, advertiser_id, *, top_n=5, media_cap=120,
                     deps: EnrichDeps) -> EnrichResult:
    ads = conn.execute(text(
        "SELECT id, external_ad_id, link_url FROM public.ads WHERE advertiser_id=:a "
        "ORDER BY last_seen DESC NULLS LAST, id LIMIT :n"),
        {"a": advertiser_id, "n": top_n}).all()
    now = datetime.now(timezone.utc)
    n_funnels = n_creatives = 0
    last_app_meta = None
    company_domain = company_html = None

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
            funnel_host = _domain_of(target.final_url)
            probeable = funnel_host not in SOCIAL_HOSTS and funnel_host not in STORE_HOSTS
            if deps.capture and probeable:
                # A web landing is only a FUNNEL if it proves a payoff: a verified
                # multi-step quiz (>=2 distinct question screens), a checkout/paywall,
                # or a pixel-tracked paid-traffic page. render_and_probe renders the
                # landing and click-probes the quiz; classify_funnel turns that into a
                # verdict. Homepages and deep app-links score 'dropped' and never reach
                # public.funnels — that ungated upsert was why most "Quiz" links opened
                # a homepage instead of a quiz. 'install' (store bounce) is handled by
                # the store branch above, so it's not a funnel here either.
                # Playwright's SYNC API can't run inside the asyncio loop → offload.
                try:
                    probe = await asyncio.to_thread(
                        render_and_probe, target.final_url, page=deps.page, proxy=deps.proxy)
                except Exception as e:
                    probe = None          # a slow/dead landing must not kill the advertiser
                    # ...but never silently: a swallowed probe means the whole funnel
                    # stage fails invisibly (e.g. browser launch dying on a bad TMPDIR).
                    log.warning("funnel probe failed for %s: %s: %s",
                                target.final_url, type(e).__name__, e)
                if probe is not None:
                    kind, status, _store, _sid, _drop = classify_funnel(
                        probe.landing_html, target,
                        funnel_host=funnel_host,
                        quiz_steps=probe.quiz_steps,
                        funnel_url=target.final_url)
                    if status == "confirmed" and kind in ("subscribe", "unknown"):
                        # Confirmed funnel — only now pay for the screenshot capture.
                        steps = await asyncio.to_thread(
                            capture_funnel, target.final_url, page=deps.page, put=deps.put,
                            s3=deps.s3, proxy=deps.proxy)
                        shot = steps[0].screenshot_s3_key if steps else None
                        repo.upsert_funnel(conn, ad.id, ad.link_url, target.final_url, shot,
                                           kind=kind, quiz_steps=probe.quiz_steps)
                        n_funnels += 1

        for ctype, url in (deps.creative_urls or {}).get(str(ad.external_ad_id), []):
            try:                              # fbcdn URLs expire → a dead one mustn't kill the gem
                ca = download_creative(conn, ad.id, url, ctype, fetch=deps.fetch_bytes,
                                       put=deps.put, s3=deps.s3)
            except Exception:
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
