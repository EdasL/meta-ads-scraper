"""Backfill ad media for specific advertisers (default: every saved gem).

The keeper-history stage stores every ad with a text-backstop creative only,
and the enrich worker downloads media just for the top-N ads of the ACTIVE
listing — so a gem's ended ads render textless in the web ad cards. This
one-off pulls each advertiser's full ad-library listing (active_status=ALL)
and, for every stored ad that still exposes an image/video URL, downloads ONE
media file into MinIO (sha256 + phash dedup, the proven download_creative
path). When the bytes dedup to an already-stored creative, the ad still gets
its own creatives row referencing the same s3_key (sha256 left NULL — the
partial unique index owns byte identity), so the ad's card renders the media.

Meta stops serving media for most long-ended ads; those keep their honest
text-only card. Re-running is safe: ads that already have s3-backed media are
skipped.

Usage:
    AGF_PROXIES=... AGF_DATABASE_URL=... S3_ENDPOINT=... MINIO_ACCESS_KEY=... \
    MINIO_SECRET_KEY=... PYTHONPATH=src \
    python scripts/backfill_ad_media.py [advertiser_id ...]
"""
import asyncio
import os
import sys
import time

from sqlalchemy import text

from agf.db.engine import get_engine, begin
from agf.enrich_full.creatives import download_creative, link_duplicate_creative
from agf.extract.facade import Extractor

HIST_PAGES = int(os.environ.get("AGF_HIST_PAGES", "12"))
MAX_MEDIA_PER_ADV = int(os.environ.get("AGF_MEDIA_CAP", "60"))


def log(msg: str) -> None:
    print(msg, flush=True)


def target_advertisers(engine, argv: list[str]) -> list[int]:
    if argv:
        return [int(a) for a in argv]
    with engine.connect() as conn:
        return [r[0] for r in conn.execute(text(
            "SELECT DISTINCT advertiser_id FROM frontend.saved_gems ORDER BY 1"))]


def backfill_advertiser(engine, ext, aid: int, stats: dict) -> None:
    with engine.connect() as conn:
        page_id = conn.execute(text(
            "SELECT page_id FROM public.advertisers WHERE id=:a"), {"a": aid}).scalar()
    if not page_id:
        log(f"  adv {aid}: no page_id, skipping")
        return
    try:
        ads = asyncio.run(ext.search(query="", countries=["US"], search_type="page",
                                     view_all_page_id=page_id, active_status="ALL",
                                     max_pages=HIST_PAGES))
    except Exception as exc:  # noqa: BLE001 — per-advertiser isolation
        stats["pull_failed"] += 1
        log(f"  adv {aid}: listing pull failed: {exc}")
        return
    seen: set[str] = set()
    budget = MAX_MEDIA_PER_ADV
    for ad in ads:
        if not ad.ad_archive_id or ad.ad_archive_id in seen:
            continue
        seen.add(ad.ad_archive_id)
        urls = [("video", u) for u in (getattr(ad, "video_urls", None) or [])]
        urls += [("image", u) for u in (getattr(ad, "image_urls", None) or [])]
        if not urls:
            continue
        stats["ads_with_urls"] += 1
        if budget <= 0:
            stats["over_budget"] += 1
            continue
        mtype, url = urls[0]
        with begin(engine) as conn:
            ad_id = conn.execute(text(
                "SELECT id FROM public.ads WHERE advertiser_id=:adv AND external_ad_id=:e"),
                {"adv": aid, "e": str(ad.ad_archive_id)}).scalar()
            if not ad_id:
                stats["unknown_ad"] += 1
                continue
            if conn.execute(text(
                    "SELECT 1 FROM public.creatives WHERE ad_id=:a AND s3_key IS NOT NULL LIMIT 1"),
                    {"a": ad_id}).first():
                stats["already_has_media"] += 1
                continue
            try:
                asset = download_creative(conn, ad_id, url, mtype,
                                          body_text=getattr(ad, "body_text", None),
                                          title=getattr(ad, "cta_text", None) or None)
            except Exception as exc:  # noqa: BLE001 — per-ad isolation
                stats["download_failed"] += 1
                log(f"    ad {ad.ad_archive_id}: download failed: {exc}")
                continue
            budget -= 1
            if asset.s3_key:
                stats["downloaded"] += 1
            elif asset.creative_id is not None and link_duplicate_creative(
                    conn, ad_id, asset.creative_id, mtype,
                    body_text=getattr(ad, "body_text", None),
                    title=getattr(ad, "cta_text", None) or None):
                stats["linked_dup"] += 1
    log(f"  adv {aid}: listing={len(seen)} ads, stats so far {stats}")


def main() -> None:
    for need in ("AGF_PROXIES", "AGF_DATABASE_URL", "S3_ENDPOINT",
                 "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY"):
        if not os.environ.get(need):
            log(f"ERROR: {need} not set")
            sys.exit(2)
    engine = get_engine()
    adv_ids = target_advertisers(engine, sys.argv[1:])
    log(f"backfilling ad media for advertisers {adv_ids} "
        f"(pages={HIST_PAGES}, cap={MAX_MEDIA_PER_ADV}/adv)")
    ext = Extractor(search_delay=2.0, search_jitter=1.5)
    stats = {"ads_with_urls": 0, "downloaded": 0, "linked_dup": 0,
             "already_has_media": 0, "unknown_ad": 0, "download_failed": 0,
             "pull_failed": 0, "over_budget": 0}
    for aid in adv_ids:
        backfill_advertiser(engine, ext, aid, stats)
        time.sleep(2.0)
    log(f"DONE {stats}")


if __name__ == "__main__":
    main()
