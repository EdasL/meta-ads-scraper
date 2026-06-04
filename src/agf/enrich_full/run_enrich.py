"""Top-N gem enrichment orchestrator (master-plan §6 P6-T10). For a flagged gem
(advertiser), enrich its top-N ads: resolve landing → app-store metadata OR funnel
capture + pricing, download deduped creatives, then company enrichment. conn-first
(no commit); cross-cutting deps arrive via EnrichDeps (worker adapter §3.9)."""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse
from sqlalchemy import text
from agf.db import repo
from agf.enrich import emerging_from_release
from agf.enrich_full.schemas import EnrichResult
from agf.enrich_full.funnel_capture import resolve_landing, capture_funnel
from agf.enrich_full.creatives import download_creative
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


async def enrich_gem(conn, advertiser_id, *, top_n=5, deps: EnrichDeps) -> EnrichResult:
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
            meta = await _lookup_app(deps, target.kind, target.store_id)
            if meta:
                app_id = repo.upsert_app(conn, {
                    "store": target.kind, "store_id": meta.track_id, "name": meta.name,
                    "developer": meta.seller, "genre": meta.genre,
                    "release_date": meta.release_date, "rating": meta.avg_rating,
                    "rating_count": meta.rating_count,
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
                # capture_funnel uses Playwright's SYNC API, which cannot run inside an
                # asyncio loop — offload to a worker thread (mirrors the Plan 2 facade).
                steps = await asyncio.to_thread(
                    capture_funnel, target.final_url, page=deps.page, put=deps.put,
                    s3=deps.s3, proxy=deps.proxy)
                if steps:
                    repo.upsert_funnel(conn, ad.id, ad.link_url, steps[-1].url,
                                       steps[0].screenshot_s3_key)
                    n_funnels += 1

        for ctype, url in (deps.creative_urls or {}).get(str(ad.external_ad_id), []):
            ca = download_creative(conn, ad.id, url, ctype, fetch=deps.fetch_bytes,
                                   put=deps.put, s3=deps.s3)
            if ca.dedup_of is None:
                n_creatives += 1

    company = enrich_company(conn, advertiser_id, domain=company_domain,
                             html=company_html, app_meta=last_app_meta,
                             whois_fn=deps.whois_fn)
    return EnrichResult(advertiser_id=advertiser_id, n_ads_processed=len(ads),
                        funnels=n_funnels, creatives=n_creatives, company=company)


def make_enrich_handler(deps_factory, *, top_n=5):
    """Adapter (§3.9): handler(conn, advertiser_id) for run_worker. deps_factory(conn,
    advertiser_id)->EnrichDeps builds per-gem deps (e.g. a fresh extractor.search for
    current creative URLs — fbcdn signed URLs expire)."""

    def handler(conn, advertiser_id):
        deps = deps_factory(conn, advertiser_id)
        res = asyncio.run(enrich_gem(conn, advertiser_id, top_n=top_n, deps=deps))
        return f"ads={res.n_ads_processed} funnels={res.funnels} creatives={res.creatives}"
    return handler
