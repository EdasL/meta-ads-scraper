"""Repair funnel coverage: feed apps below the enrich/alert threshold are never
enriched, so their funnel ads never get a public.funnels row and the directory
shows no Quiz link. This is the funnel sibling of media_repair — the worker drains
a small bounded set each cycle (so the backlog clears without ever hammering Meta or
Playwright), and per-advertiser/per-ad failures are isolated into a stats dict,
never raised.

The write policy (which web landings count as a funnel, when a screenshot is paid
for) lives in one place — run_enrich.record_web_funnel — so enrich and repair can't
drift apart.
"""
import asyncio
import os

from sqlalchemy import text

from agf.db.engine import begin
from agf.discovery.funnel_classify import SOCIAL_HOSTS
from agf.enrich_full.funnel_capture import resolve_landing
from agf.enrich_full.run_enrich import (STORE_HOSTS, _domain_of, record_web_funnel)

# Store / social / MMP destinations are not web funnels: store + social are handled
# elsewhere and MMP links bounce to a store. Skip them in SQL so the bounded repair
# budget is spent only on genuine off-store web landings worth rendering.
_NON_FUNNEL_HOSTS = (r'apps\.apple\.com|itunes\.apple\.com|play\.google\.com|'
                     r'chromewebstore|appstore-download|facebook\.com|instagram\.com|'
                     r'fb\.com|fb\.me|twitter\.com|x\.com|tiktok\.com|youtube\.com|'
                     r'youtu\.be|linkedin\.com|t\.me|linktr\.ee|wa\.me|whatsapp\.com|'
                     r'appsflyer|adjust\.com|app\.link|go\.link|onelink|bnc\.lt')

MAX_ADS_PER_ADV = int(os.environ.get("AGF_FUNNEL_REPAIR_MAX_ADS", "8"))


def _noop(_msg: str) -> None:
    return None


def funnel_gap_advertisers(conn, limit: int) -> list[int]:
    """Confirmed-feed advertisers that show no funnel link but DO run an off-store
    web ad — the funnel-coverage gap. Oldest-refreshed first so the longest-uncovered
    apps get probed before fresh arrivals."""
    rows = conn.execute(text(
        """SELECT m.advertiser_id
             FROM frontend.emerging_scaling_apps m
            WHERE NOT m.incubating
              AND m.funnel_url IS NULL
              AND EXISTS (SELECT 1 FROM public.ads a
                          WHERE a.advertiser_id = m.advertiser_id
                            AND a.link_url ~* '^https?://'
                            AND a.link_url !~* :excl)
            ORDER BY m.refreshed_at NULLS FIRST
            LIMIT :lim"""),
        {"excl": _NON_FUNNEL_HOSTS, "lim": limit}).all()
    return [r[0] for r in rows]


def repair_advertiser_funnels(engine, deps, aid: int, stats: dict, *,
                              max_ads: int = MAX_ADS_PER_ADV, log=_noop) -> dict:
    """Resolve + probe each off-store web ad of advertiser `aid` and record a funnel
    row when the landing shows web-to-app intent (record_web_funnel decides). Commits
    per ad. Per-ad failures are isolated into `stats`, never raised — one dead landing
    must not abort the batch."""
    with engine.connect() as conn:
        ads = conn.execute(text(
            """SELECT id, link_url FROM public.ads
                WHERE advertiser_id = :a
                  AND link_url ~* '^https?://'
                  AND link_url !~* :excl
                ORDER BY last_seen DESC NULLS LAST
                LIMIT :n"""),
            {"a": aid, "excl": _NON_FUNNEL_HOSTS, "n": max_ads}).all()
    seen: set[str] = set()
    for ad in ads:
        if ad.link_url in seen:
            continue
        seen.add(ad.link_url)
        stats["ads_probed"] = stats.get("ads_probed", 0) + 1
        try:
            target = asyncio.run(resolve_landing(deps.extractor, ad.link_url))
        except Exception as exc:  # noqa: BLE001 — per-ad isolation
            stats["resolve_failed"] = stats.get("resolve_failed", 0) + 1
            log(f"    ad {ad.id}: resolve failed: {exc}")
            continue
        host = _domain_of(target.final_url)
        if (target.kind != "web" or not host
                or host in SOCIAL_HOSTS or host in STORE_HOSTS):
            continue
        try:
            with begin(engine) as conn:
                wrote = asyncio.run(record_web_funnel(conn, ad.id, ad.link_url, target, deps))
        except Exception as exc:  # noqa: BLE001 — per-ad isolation
            stats["probe_failed"] = stats.get("probe_failed", 0) + 1
            log(f"    ad {ad.id}: probe/write failed: {exc}")
            continue
        if wrote:
            stats["recorded"] = stats.get("recorded", 0) + 1
    return stats
