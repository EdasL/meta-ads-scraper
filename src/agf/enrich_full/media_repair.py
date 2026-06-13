"""Repair ad-media coverage: pull an advertiser's full ad-library listing and
download ONE served media file for every stored ad that still lacks one.

The keeper-history stage stores ads with a text-backstop creative only, and the
enrich worker downloads media just for the top-N ads of the ACTIVE listing — so
older active ads, and a gem's ended ads, render textless. This module is the one
shared implementation of the fix: the worker calls it on a small bounded set of
textless feed advertisers every cycle (so the backlog drains and can't grow
unbounded), and `scripts/backfill_ad_media.py` calls it for one-off bulk runs.

Meta stops serving media for most long-ended ads; those keep their honest
text-only card. Re-running is safe: ads that already have s3-backed media are
skipped (sha256 + phash dedup, the proven download_creative path).
"""
import asyncio
import os

from sqlalchemy import text

from agf.db.engine import begin
from agf.enrich_full.creatives import download_creative, link_duplicate_creative

HIST_PAGES = int(os.environ.get("AGF_HIST_PAGES", "12"))
MAX_MEDIA_PER_ADV = int(os.environ.get("AGF_MEDIA_CAP", "60"))


def _noop(_msg: str) -> None:
    return None


def textless_feed_advertisers(conn, limit: int) -> list[int]:
    """Confirmed-feed advertisers that have active ads but NOT one downloaded
    creative on any of them — the media-coverage gap, oldest-seen first so the
    longest-textless gems get repaired before fresh arrivals."""
    rows = conn.execute(text(
        """SELECT m.advertiser_id
             FROM frontend.emerging_scaling_apps m
            WHERE NOT m.incubating
              AND EXISTS (SELECT 1 FROM public.ads a
                          WHERE a.advertiser_id = m.advertiser_id AND a.is_active)
              AND NOT EXISTS (SELECT 1 FROM public.ads a
                              JOIN public.creatives c ON c.ad_id = a.id
                              WHERE a.advertiser_id = m.advertiser_id
                                AND a.is_active AND c.s3_key IS NOT NULL)
            ORDER BY m.refreshed_at NULLS FIRST
            LIMIT :lim"""),
        {"lim": limit}).all()
    return [r[0] for r in rows]


def repair_advertiser(engine, ext, aid: int, stats: dict, *,
                      hist_pages: int = HIST_PAGES,
                      max_media: int = MAX_MEDIA_PER_ADV, log=_noop) -> None:
    """Download up to `max_media` served media files for advertiser `aid`'s ads
    that lack one. Per-advertiser and per-ad failures are isolated into `stats`,
    never raised — one bad listing must not abort a batch."""
    with engine.connect() as conn:
        page_id = conn.execute(text(
            "SELECT page_id FROM public.advertisers WHERE id=:a"), {"a": aid}).scalar()
    if not page_id:
        log(f"  adv {aid}: no page_id, skipping")
        return
    try:
        ads = asyncio.run(ext.search(query="", countries=["US"], search_type="page",
                                     view_all_page_id=page_id, active_status="ALL",
                                     max_pages=hist_pages))
    except Exception as exc:  # noqa: BLE001 — per-advertiser isolation
        stats["pull_failed"] = stats.get("pull_failed", 0) + 1
        log(f"  adv {aid}: listing pull failed: {exc}")
        return
    seen: set[str] = set()
    budget = max_media
    for ad in ads:
        if not ad.ad_archive_id or ad.ad_archive_id in seen:
            continue
        seen.add(ad.ad_archive_id)
        urls = [("video", u) for u in (getattr(ad, "video_urls", None) or [])]
        urls += [("image", u) for u in (getattr(ad, "image_urls", None) or [])]
        if not urls:
            continue
        stats["ads_with_urls"] = stats.get("ads_with_urls", 0) + 1
        if budget <= 0:
            stats["over_budget"] = stats.get("over_budget", 0) + 1
            continue
        mtype, url = urls[0]
        with begin(engine) as conn:
            ad_id = conn.execute(text(
                "SELECT id FROM public.ads WHERE advertiser_id=:adv AND external_ad_id=:e"),
                {"adv": aid, "e": str(ad.ad_archive_id)}).scalar()
            if not ad_id:
                stats["unknown_ad"] = stats.get("unknown_ad", 0) + 1
                continue
            if conn.execute(text(
                    "SELECT 1 FROM public.creatives WHERE ad_id=:a AND s3_key IS NOT NULL LIMIT 1"),
                    {"a": ad_id}).first():
                stats["already_has_media"] = stats.get("already_has_media", 0) + 1
                continue
            try:
                asset = download_creative(conn, ad_id, url, mtype,
                                          body_text=getattr(ad, "body_text", None),
                                          title=getattr(ad, "cta_text", None) or None)
            except Exception as exc:  # noqa: BLE001 — per-ad isolation
                stats["download_failed"] = stats.get("download_failed", 0) + 1
                log(f"    ad {ad.ad_archive_id}: download failed: {exc}")
                continue
            budget -= 1
            if asset.s3_key:
                stats["downloaded"] = stats.get("downloaded", 0) + 1
            elif asset.creative_id is not None and link_duplicate_creative(
                    conn, ad_id, asset.creative_id, mtype,
                    body_text=getattr(ad, "body_text", None),
                    title=getattr(ad, "cta_text", None) or None):
                stats["linked_dup"] = stats.get("linked_dup", 0) + 1
    log(f"  adv {aid}: listing={len(seen)} ads, stats so far {stats}")
