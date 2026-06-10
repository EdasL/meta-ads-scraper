"""Persist discovered + enriched candidates into Postgres + MinIO so they appear in the
local web UI (Discover feed) with REAL week-over-week growth.

Per chosen page:
  1. Pull the full ad history (active_status=ALL) -> reconstruct the weekly ad-count series.
  2. Persist active ads (advertisers / apps / ads) via the discovery upsert path.
  3. Download creatives to MinIO via the proven enrich worker (the feed's s3_key gate).
  4. Insert the reconstructed weekly series into adtech.app_advertiser_metrics (the value-add:
     real backfilled velocity, not a single snapshot).
  5. Resolve App Store / Play metadata, compute growth, run scoring, refresh the matview.

The scorer then sees a real multi-week velocity curve, so genuine scalers (WarriorBabe, Foodvisor)
score high and surface at the top of the feed.

Run:
  AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf \
  S3_ENDPOINT=http://localhost:9010 MINIO_ACCESS_KEY=minioadmin MINIO_SECRET_KEY=minioadmin \
  S3_BUCKET=agf AGF_PROXIES="http://user:pass@gw.dataimpulse.com:823" PYTHONPATH=src \
  .venv/bin/python scripts/persist_candidates.py [enriched.json|candidates.json] [page_id ...]

Env: AGF_PERSIST_LIMIT (default 25), AGF_PERSIST_KINDS (app,funnel), AGF_HIST_PAGES (8),
     AGF_PERSIST_SCALERS_ONLY (1 = only trend==scaling rows from an enriched.json),
     AGF_MAX_ADS_PER_PAGE (ads stored per page, active first, default 250),
     AGF_SWEEP_COUNTRIES (US).
"""
import asyncio
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy import text

from agf.classify import classify_url
from agf.db import repo
from agf.db.engine import get_engine, begin
from agf.discovery.seeds import Candidate, upsert_candidate
from agf.enrich_full.appstore import resolve_pending_app_metadata
from agf.enrich_full.run_enrich import make_enrich_handler
from agf.extract.facade import Extractor
from agf.scoring.run_scoring import run_scoring
from agf.tracker.history import reconstruct_weekly_ad_counts, new_ads_per_week
from agf.workers.main import prefetch_creative_urls, production_deps_factory
from agf.workers.runner import run_worker

WEEKS = 16
HIST_PAGES = int(os.environ.get("AGF_HIST_PAGES", "8"))
LIMIT = int(os.environ.get("AGF_PERSIST_LIMIT", "25"))
KINDS = [k.strip() for k in os.environ.get("AGF_PERSIST_KINDS", "app,funnel").split(",")]
SCALERS_ONLY = os.environ.get("AGF_PERSIST_SCALERS_ONLY", "") in ("1", "true", "yes")
MAX_ADS = int(os.environ.get("AGF_MAX_ADS_PER_PAGE", "250"))
COUNTRIES = [c.strip() for c in os.environ.get("AGF_SWEEP_COUNTRIES", "US").split(",") if c.strip()]


def log(m):
    print(f"[persist] {m}", flush=True)


def store_ads(conn, ads, source="persist"):
    """Persist AdRecords as advertisers/apps/ads (mirrors scrape_real._store_ad_records).
    Every persisted ad gets a text-only creative backstop (s3_key NULL) so it renders in
    the evidence reel even before the enrich worker downloads media for the top ads."""
    touched = set()
    for ad in ads:
        if not ad.page_id:
            continue
        res = upsert_candidate(conn, Candidate(source=source, kind="page", page_id=ad.page_id,
                                               page_name=ad.page_name, ad_record=ad))
        if res.get("advertiser_id"):
            touched.add(res["advertiser_id"])
        ad_id = res.get("ad_id")
        intent = classify_url(ad.link_url)
        if intent.kind in ("ios", "android") and intent.store_id:
            store = "ios" if intent.kind == "ios" else "android"
            res = upsert_candidate(conn, Candidate(
                source=source, kind="app", store=store, store_id=intent.store_id,
                page_id=ad.page_id, page_name=ad.page_name,
                app_meta={"store": store, "store_id": intent.store_id}, ad_record=ad))
            if res.get("advertiser_id"):
                touched.add(res["advertiser_id"])
            ad_id = res.get("ad_id") or ad_id
        if ad_id:
            repo.ensure_text_creative(conn, ad_id, ad)
    return touched


def choose(json_path, explicit_ids):
    rows = json.loads(open(json_path).read())
    by_id = {r["page_id"]: r for r in rows}
    if explicit_ids:
        return [by_id[i] for i in explicit_ids if i in by_id]
    sel = [r for r in rows if r.get("kind") in KINDS]
    if SCALERS_ONLY:
        sel = [r for r in sel if (r.get("wow") or {}).get("trend") == "scaling"]
    sel.sort(key=lambda r: -((r.get("wow") or {}).get("growth_4w") or r.get("n_ads_seen") or 0))
    return sel[:LIMIT]


def week_starts(today, n):
    """n Monday-aligned week_start dates, oldest -> newest (newest = this week's Monday)."""
    monday = today - timedelta(days=today.weekday())
    return [monday - timedelta(weeks=(n - 1 - i)) for i in range(n)]


def main():
    args = sys.argv[1:]
    json_path = next((a for a in args if a.endswith(".json")), None) \
        or f"/tmp/agf-enriched-{date.today().isoformat()}.json"
    explicit_ids = [a for a in args if not a.endswith(".json")]
    for need in ("AGF_PROXIES", "AGF_DATABASE_URL", "S3_ENDPOINT",
                 "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY"):
        if not os.environ.get(need):
            log(f"ERROR: {need} not set"); sys.exit(2)

    chosen = choose(json_path, explicit_ids)
    log(f"persisting {len(chosen)} pages from {json_path}")
    engine = get_engine()
    ext = Extractor(search_delay=2.0, search_jitter=1.5)
    http = httpx.Client(timeout=30, follow_redirects=True)
    for attempt in range(6):
        try:
            ext._pool.invalidate(); ext._pool.acquire(); break
        except Exception as exc:  # noqa: BLE001
            log(f"warm {attempt} failed: {exc}"); time.sleep(2)

    weekly_by_adv: dict[int, tuple] = {}
    adv_ids: list[int] = []
    for i, row in enumerate(chosen, 1):
        pid = row["page_id"]
        try:
            ads = asyncio.run(ext.search(query="", countries=COUNTRIES, search_type="page",
                                         view_all_page_id=pid, active_status="ALL",
                                         max_pages=HIST_PAGES))
        except Exception as exc:  # noqa: BLE001
            log(f"  [{i}/{len(chosen)}] {pid} history pull failed: {exc}"); continue
        # dedupe by ad id
        seen, uniq = set(), []
        for a in ads:
            if a.ad_archive_id and a.ad_archive_id not in seen:
                seen.add(a.ad_archive_id); uniq.append(a)
        # all deduped ads, active first, up to the (now generous) per-page limit
        chosen_ads = ([a for a in uniq if a.is_active]
                      + [a for a in uniq if not a.is_active])[:MAX_ADS]
        with begin(engine) as conn:
            store_ads(conn, chosen_ads)
        with engine.connect() as conn:
            adv_id = conn.execute(text("SELECT id FROM public.advertisers WHERE page_id=:p"),
                                  {"p": pid}).scalar()
        if not adv_id:
            log(f"  [{i}/{len(chosen)}] {pid} not persisted (no advertiser id)"); continue
        weekly = reconstruct_weekly_ad_counts(uniq, weeks=WEEKS)
        weekly_by_adv[adv_id] = (weekly, new_ads_per_week(uniq, weeks=WEEKS))
        adv_ids.append(adv_id)
        log(f"  [{i}/{len(chosen)}] {row.get('page_name','')[:28]:28} adv={adv_id} "
            f"ads={len(uniq)} weekly={weekly[-6:]}")
        time.sleep(2)

    if not adv_ids:
        log("nothing persisted; aborting"); return

    # --- creatives -> MinIO via the proven enrich worker (the feed's s3_key gate) -----
    log(f"downloading creatives for {len(adv_ids)} advertisers ...")
    with engine.connect() as conn:
        # 3 pages ≈ 90 ads — plenty to cover the top-N media downloads below
        creative_urls = prefetch_creative_urls(ext, conn, adv_ids, max_pages=3)
    deps = production_deps_factory(ext, http, creative_urls)
    try:
        # top_n bounds MEDIA downloads per advertiser; every ad already has at least a
        # text creative row, so the reel stays complete without downloading all media.
        res = run_worker("enrich", "advertiser",
                         make_enrich_handler(deps, top_n=int(os.environ.get("AGF_MEDIA_TOP_N", "12"))),
                         source=engine, candidate_ids=adv_ids,
                         due_before=datetime.now(timezone.utc), limit=len(adv_ids))
        log(f"enrich worker: {res}")
    except Exception as exc:  # noqa: BLE001
        log(f"enrich worker FAILED: {exc!r}")

    # --- app metadata ----------------------------------------------------------------
    try:
        with begin(engine) as conn:
            log(f"resolved app metadata for {resolve_pending_app_metadata(conn, http)} apps")
    except Exception as exc:  # noqa: BLE001
        log(f"resolve_pending_app_metadata FAILED: {exc!r}")

    # --- insert the reconstructed weekly series (authoritative; after enrich) ---------
    today = date.today()
    starts = week_starts(today, WEEKS)
    with begin(engine) as conn:
        for adv_id, (weekly, newweekly) in weekly_by_adv.items():
            for wk_start, cnt, nnew in zip(starts, weekly, newweekly):
                repo.upsert_app_advertiser_metric(conn, adv_id, wk_start, cnt, max(nnew, 1), None)
    log(f"inserted {WEEKS}-week metrics for {len(weekly_by_adv)} advertisers")

    # --- growth + scoring + matview --------------------------------------------------
    try:
        n = run_scoring(engine, date.today())
        log(f"run_scoring scored {n} advertisers (growth + matview refreshed)")
    except Exception as exc:  # noqa: BLE001
        log(f"run_scoring FAILED: {exc!r}")

    http.close()

    # --- verify: shortlist-eligible (score row + s3_key creative) --------------------
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT m.page_name, m.gem_score, m.pct_change, m.latest_count, m.weeks_observed
            FROM frontend.emerging_scaling_apps m
            JOIN public.advertisers adv ON adv.id = m.advertiser_id
            WHERE adv.id = ANY(:ids)
              AND EXISTS (SELECT 1 FROM public.creatives c JOIN public.ads a ON a.id=c.ad_id
                          WHERE a.advertiser_id = adv.id AND c.s3_key IS NOT NULL)
            ORDER BY m.gem_score DESC NULLS LAST
        """), {"ids": adv_ids}).all()
    log(f"\n==== {len(rows)} advertisers now live in the feed (score + creative) ====")
    for r in rows:
        log(f"  score={r.gem_score} pct_change={r.pct_change} latest_ads={r.latest_count} "
            f"weeks={r.weeks_observed}  {r.page_name}")


if __name__ == "__main__":
    main()
