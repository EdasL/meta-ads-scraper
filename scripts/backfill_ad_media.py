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
import os
import sys
import time

from sqlalchemy import text

from agf.db.engine import get_engine
from agf.enrich_full.media_repair import (HIST_PAGES, MAX_MEDIA_PER_ADV,
                                          repair_advertiser)
from agf.extract.facade import Extractor


def log(msg: str) -> None:
    print(msg, flush=True)


def target_advertisers(engine, argv: list[str]) -> list[int]:
    if argv:
        return [int(a) for a in argv]
    with engine.connect() as conn:
        return [r[0] for r in conn.execute(text(
            "SELECT DISTINCT advertiser_id FROM frontend.saved_gems ORDER BY 1"))]


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
        repair_advertiser(engine, ext, aid, stats, log=log)
        time.sleep(2.0)
    log(f"DONE {stats}")


if __name__ == "__main__":
    main()
