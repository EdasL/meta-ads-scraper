"""One-shot REAL Meta scrape: get genuine ad creatives (image AND video) + real
advertiser identifiers (page_id, domain, app store_id) into Postgres + MinIO so the
Discover feed shows real ads — not the synthetic DEMO corpus.

This is a bounded, reusable operator script. It does NOT run the worker's normal
production path because that only enriches `eligible_gem_ids` (advertisers with
alert=TRUE, i.e. velocity history). A single fresh sweep produces zero alerts, so the
normal worker downloads zero creatives. We therefore FORCE enrichment on discovered
advertisers, bypassing the alert gate.

Pipeline:
  1. Build a real Extractor (curl_cffi + ProxyPool.from_env residential proxy).
  2. Get real advertisers into the DB:
       a. run_discovery (Firehose B EU impressions -> EU->US bridge -> 14d confirm).
       b. If discovery yields too few app-advertisers, FALL BACK to keyword searches
          for app-install verticals (US), persisting AdRecords via the same
          upsert_candidate store path discovery uses.
  3. Pick <=5 advertisers (prefer ones with a page_id + app-install intent / apps),
     FORCE-enrich them: prefetch_creative_urls (READ conn) then run_worker(enrich)
     with candidate_ids = those ids and due_before = now (so they're all "due").
  4. resolve_pending_app_metadata so app names/genres/store ids populate.
  5. run_scoring(today) -> scores + growth + matview refresh so the API surfaces them.

Bounds: max 2 pages/search, <=5 advertisers enriched, <=5 ads/advertiser. No looping.
Idempotent-ish: re-running re-enriches the same advertisers (dedup keeps S3 clean).

Run (from repo root, with the task ENV exported):
    AGF_DATABASE_URL=... S3_ENDPOINT=... MINIO_ACCESS_KEY=... MINIO_SECRET_KEY=... \
    S3_BUCKET=agf AGF_PROXIES=... PYTHONPATH=src \
    .venv/bin/python scripts/scrape_real.py
"""
import asyncio
import os
import traceback
from datetime import date, datetime, timezone

import httpx
from sqlalchemy import text

from agf.db import repo
from agf.db.engine import get_engine, begin
from agf.discovery.run_discovery import run_discovery
from agf.discovery.seeds import Candidate, upsert_candidate
from agf.classify import classify_url
from agf.enrich_full.run_enrich import make_enrich_handler
from agf.enrich_full.appstore import resolve_pending_app_metadata
from agf.scoring.run_scoring import run_scoring
from agf.tracker.growth import compute_advertiser_growth
from agf.workers.main import prefetch_creative_urls, production_deps_factory
from agf.workers.runner import run_worker

# App-install-intent keyword verticals for the fallback (US Ad Library). Kept short so
# the run stays bounded; the first that yields advertisers-with-ads is enough.
KEYWORD_QUERIES = [
    "workout app",
    "language learning app",
    "meditation app",
    "budget app",
    "habit tracker app",
]
MAX_PAGES = 2
MAX_ENRICH = 5
MIN_APP_ADVERTISERS = 3   # below this from discovery -> trigger the keyword fallback
AD_COUNT_CAP = 250        # count_active_ads early-exit ("250+" = incumbent territory)


def log(msg):
    print(f"[scrape_real] {msg}", flush=True)


# --- store path for keyword AdRecords (reuses the discovery upsert) -------------
def _store_ad_records(conn, ads, source):
    """Persist a batch of AdRecords (from extractor.search) via upsert_candidate — the
    same store path discovery's bridge uses. Emits a page Candidate (carrying the ad) for
    every ad, plus an app Candidate when the link resolves to a store id. Returns the set
    of advertiser ids touched."""
    touched = set()
    for ad in ads:
        if not ad.page_id:
            continue
        page_cand = Candidate(source=source, kind="page", page_id=ad.page_id,
                              page_name=ad.page_name, ad_record=ad)
        res = upsert_candidate(conn, page_cand)
        if res.get("advertiser_id"):
            touched.add(res["advertiser_id"])
        intent = classify_url(ad.link_url)
        if intent.kind in ("ios", "android") and intent.store_id:
            store = "ios" if intent.kind == "ios" else "android"
            app_cand = Candidate(source=source, kind="app", store=store,
                                store_id=intent.store_id, page_id=ad.page_id,
                                page_name=ad.page_name,
                                app_meta={"store": store, "store_id": intent.store_id},
                                ad_record=ad)
            res = upsert_candidate(conn, app_cand)
            if res.get("advertiser_id"):
                touched.add(res["advertiser_id"])
    return touched


# --- candidate selection --------------------------------------------------------
def _select_advertiser_ids(conn, prefer_ids, limit):
    """Pick up to `limit` advertiser ids to enrich. Prefer advertisers that have at least
    one ad with app-install intent (a store link) or a linked app, then any advertiser
    touched this run that has a page_id + ads. Never returns DEMO advertisers."""
    prefer = list(prefer_ids)
    # rank: advertisers with an app-store link or linked app first
    ranked = conn.execute(text("""
        SELECT adv.id,
               bool_or(a.app_id IS NOT NULL
                       OR a.link_url ILIKE '%apps.apple.com%'
                       OR a.link_url ILIKE '%play.google.com%'
                       OR a.cta_type ILIKE '%APP%') AS app_intent,
               count(a.id) AS n_ads
        FROM public.advertisers adv
        JOIN public.ads a ON a.advertiser_id = adv.id
        WHERE adv.id = ANY(:ids)
          AND adv.page_id IS NOT NULL
          AND adv.page_id NOT LIKE 'DEMO%'
        GROUP BY adv.id
        ORDER BY app_intent DESC, n_ads DESC, adv.id
    """), {"ids": prefer or [-1]}).all()
    ids = [r[0] for r in ranked]
    return ids[:limit]


# --- verification ---------------------------------------------------------------
def _report(engine):
    with engine.connect() as conn:
        summary = conn.execute(text("""
            SELECT count(DISTINCT adv.id) AS advertisers,
                   count(*) FILTER (WHERE lower(c.type) IN ('image','img','photo')) AS images,
                   count(*) FILTER (WHERE lower(c.type) = 'video') AS videos
            FROM public.advertisers adv
            JOIN public.ads a ON a.advertiser_id = adv.id
            JOIN public.creatives c ON c.ad_id = a.id
            WHERE c.s3_key IS NOT NULL
              AND adv.page_id IS NOT NULL
              AND adv.page_id NOT LIKE 'DEMO%'
        """)).one()
        log(f"NON-DEMO advertisers with >=1 s3_key creative: {summary.advertisers} "
            f"(creatives: {summary.images} image / {summary.videos} video)")

        examples = conn.execute(text("""
            SELECT adv.id, adv.page_name, adv.page_id,
                   d.domain AS domain,
                   (SELECT string_agg(DISTINCT ap.store || ':' || ap.store_id, ', ')
                    FROM public.ads a2 JOIN public.apps ap ON ap.id = a2.app_id
                    WHERE a2.advertiser_id = adv.id AND a2.app_id IS NOT NULL) AS apps,
                   count(*) FILTER (WHERE lower(c.type) IN ('image','img','photo')) AS images,
                   count(*) FILTER (WHERE lower(c.type) = 'video') AS videos
            FROM public.advertisers adv
            JOIN public.ads a ON a.advertiser_id = adv.id
            JOIN public.creatives c ON c.ad_id = a.id
            LEFT JOIN public.domains d ON d.id = adv.domain_id
            WHERE c.s3_key IS NOT NULL
              AND adv.page_id IS NOT NULL
              AND adv.page_id NOT LIKE 'DEMO%'
            GROUP BY adv.id, adv.page_name, adv.page_id, d.domain
            ORDER BY (count(*) FILTER (WHERE lower(c.type)='video')) DESC, adv.id
            LIMIT 3
        """)).all()
        log("example advertisers:")
        for r in examples:
            log(f"  id={r.id} page_name={r.page_name!r} page_id={r.page_id} "
                f"domain={r.domain} apps={r.apps} creatives={r.images}img/{r.videos}vid")

        # new S3 keys recorded this run (non-demo)
        keys = conn.execute(text("""
            SELECT DISTINCT c.s3_key
            FROM public.creatives c
            JOIN public.ads a ON a.id = c.ad_id
            JOIN public.advertisers adv ON adv.id = a.advertiser_id
            WHERE c.s3_key IS NOT NULL
              AND adv.page_id IS NOT NULL
              AND adv.page_id NOT LIKE 'DEMO%'
        """)).scalars().all()
        log(f"distinct non-demo creative s3_keys in DB: {len(keys)}")
    return summary, examples


def _verify_minio():
    """Count objects under creatives/ in the agf bucket (real keys are creatives/<sha>.ext;
    demo keys are creatives/DEMO*/...)."""
    try:
        from agf.storage.s3 import get_s3
        s3 = get_s3()
        bucket = os.environ.get("S3_BUCKET", "agf")
        paginator = s3.get_paginator("list_objects_v2")
        total = real = 0
        for page in paginator.paginate(Bucket=bucket, Prefix="creatives/"):
            for obj in page.get("Contents", []):
                total += 1
                # real creative keys are creatives/<64hex>.<ext>; demo are creatives/DEMO*
                rest = obj["Key"][len("creatives/"):]
                if "/" not in rest:           # flat sha key == real download_creative output
                    real += 1
        log(f"MinIO bucket {bucket}: {total} objects under creatives/ "
            f"({real} flat-sha real-download keys)")
    except Exception as exc:
        log(f"minio listing failed: {exc!r}")


def _verify_api():
    """Hit the running Go API /api/shortlist (min_score=0) and report whether any
    non-demo advertiser surfaces with a hero_creative."""
    base = os.environ.get("AGF_API_BASE", "http://localhost:8000")
    try:
        r = httpx.get(f"{base}/api/shortlist", params={"min_score": 0, "limit": 100},
                      timeout=30)
        rows = r.json()
    except Exception as exc:
        log(f"API check failed: {exc!r}")
        return
    demo_names = {"SleepWave", "CalorieCam", "PostureFix", "FlexLabs", "MegaFit",
                  "TutorGenie", "LinguaLoop", "ChemQuest", "QuizWhiz"}
    with_hero = [row for row in rows if row.get("hero_creative")]
    real_with_hero = [row for row in with_hero
                      if row.get("page_name") not in demo_names]
    log(f"/api/shortlist: {len(rows)} rows, {len(with_hero)} with hero_creative, "
        f"{len(real_with_hero)} of those non-demo")
    for row in real_with_hero[:3]:
        hc = row.get("hero_creative") or {}
        log(f"  REAL hero: page_name={row.get('page_name')!r} "
            f"gem_score={row.get('gem_score')} hero_type={hc.get('type')} "
            f"s3_key={hc.get('s3_key')}")


# --- main -----------------------------------------------------------------------
def main():
    from agf.extract.facade import Extractor
    from agf.extract.proxy import ProxyPool

    pool = ProxyPool.from_env()
    log(f"proxy pool size: {len(pool)}")
    extractor = Extractor(proxies=pool)
    engine = get_engine()
    now_epoch = int(datetime.now(timezone.utc).timestamp())
    http_client = httpx.Client(timeout=30, follow_redirects=True)

    # --- step 2a: discovery ---------------------------------------------------
    disc = {"candidates": 0, "pages": 0, "confirmed": []}
    discovery_touched = set()
    try:
        with begin(engine) as conn:
            disc = asyncio.run(run_discovery(
                conn, extractor, now_epoch, eu_countries=("DE", "GB"),
                eu_max_pages=MAX_PAGES, min_running_days=25, min_longevity_days=14))
            # advertisers stored this run = those whose page_id is among confirmed/candidates
            if disc["confirmed"]:
                discovery_touched = set(conn.execute(text(
                    "SELECT id FROM public.advertisers WHERE page_id = ANY(:p)"),
                    {"p": list(disc["confirmed"])}).scalars().all())
        log(f"discovery: candidates={disc['candidates']} pages={disc['pages']} "
            f"confirmed={len(disc['confirmed'])} advertisers_touched={len(discovery_touched)}")
    except Exception as exc:
        log(f"discovery FAILED (continuing to keyword fallback): {exc!r}")
        traceback.print_exc()

    # --- step 2b: keyword fallback -------------------------------------------
    # Count how many discovered advertisers actually have app-install intent. Discovery
    # confirms pages but the confirm step does not persist the page's ads, so app-intent
    # may be thin — fall back to keyword search to guarantee real ads-with-creatives.
    with engine.connect() as conn:
        app_adv = _select_advertiser_ids(conn, discovery_touched, MAX_ENRICH)
    keyword_touched = set()
    if len(app_adv) < MIN_APP_ADVERTISERS:
        log(f"discovery yielded {len(app_adv)} usable app-advertisers (<{MIN_APP_ADVERTISERS}) "
            f"— running keyword fallback")
        for q in KEYWORD_QUERIES:
            try:
                ads = asyncio.run(extractor.search(query=q, countries=["US"],
                                                   max_pages=MAX_PAGES))
            except Exception as exc:
                log(f"  keyword {q!r} search FAILED: {exc!r}")
                continue
            with begin(engine) as conn:
                touched = _store_ad_records(conn, ads, source="keyword")
            keyword_touched |= touched
            log(f"  keyword {q!r}: {len(ads)} ads -> {len(touched)} advertisers stored "
                f"(running total {len(keyword_touched)})")
            if len(keyword_touched) >= MAX_ENRICH * 2:   # enough to pick from
                break
    else:
        log(f"discovery yielded {len(app_adv)} app-advertisers — skipping keyword fallback")

    # --- step 3: select + FORCE-enrich ---------------------------------------
    with engine.connect() as conn:
        cand_ids = _select_advertiser_ids(
            conn, discovery_touched | keyword_touched, MAX_ENRICH)
    log(f"force-enrich candidates ({len(cand_ids)}): {cand_ids}")

    enriched = {}
    if cand_ids:
        with engine.connect() as conn:
            try:
                creative_urls = prefetch_creative_urls(extractor, conn, cand_ids)
            except Exception as exc:
                log(f"prefetch_creative_urls FAILED: {exc!r}")
                creative_urls = {}
        n_urls = sum(len(v) for v in creative_urls.values())
        log(f"prefetched creative URLs for {len(creative_urls)} advertisers "
            f"({n_urls} ads with media)")
        deps_factory = production_deps_factory(extractor, http_client, creative_urls)
        try:
            enriched = run_worker(
                "enrich", "advertiser",
                make_enrich_handler(deps_factory, top_n=MAX_ENRICH),
                source=engine, candidate_ids=cand_ids,
                due_before=datetime.now(timezone.utc), limit=MAX_ENRICH)
            log(f"enrich results: {enriched}")
        except Exception as exc:
            log(f"enrich run_worker FAILED: {exc!r}")
            traceback.print_exc()

    # --- step 3.5: true active-ad counts --------------------------------------
    # Bounded view_all_page_id pagination per enriched advertiser (early exit at
    # AD_COUNT_CAP) -> advertisers.meta_active_ads. The stored-ads count is a known
    # undercount; this is the real "N ads in library" signal the detail page shows.
    if cand_ids:
        with engine.connect() as conn:
            pages = conn.execute(text(
                "SELECT id, page_id FROM public.advertisers "
                "WHERE id = ANY(:i) AND page_id IS NOT NULL"), {"i": cand_ids}).all()
        for adv_id, page_id in pages:
            try:
                n_ads, capped = asyncio.run(
                    extractor.count_active_ads(page_id=page_id, cap=AD_COUNT_CAP))
            except Exception as exc:
                log(f"  count_active_ads(page {page_id}) FAILED: {exc!r}")
                continue
            with begin(engine) as conn:
                repo.set_meta_active_ads(conn, adv_id, n_ads, capped)
            log(f"  advertiser {adv_id}: {n_ads}{'+' if capped else ''} active ads in library")

    # --- step 4: app metadata -------------------------------------------------
    try:
        with begin(engine) as conn:
            n_meta = resolve_pending_app_metadata(conn, http_client)
        log(f"resolved app metadata for {n_meta} apps")
    except Exception as exc:
        log(f"resolve_pending_app_metadata FAILED: {exc!r}")

    # --- step 5: scoring + growth + matview refresh --------------------------
    try:
        n_scored = run_scoring(engine, date.today())   # also computes growth + refreshes
        log(f"run_scoring scored {n_scored} advertisers (growth + matview refreshed)")
    except Exception as exc:
        log(f"run_scoring FAILED: {exc!r}")
        traceback.print_exc()
        # try growth alone so a scoring hiccup doesn't leave growth stale
        try:
            with begin(engine) as conn:
                compute_advertiser_growth(conn)
        except Exception as exc2:
            log(f"compute_advertiser_growth fallback FAILED: {exc2!r}")

    http_client.close()

    # --- verify ---------------------------------------------------------------
    log("=" * 60)
    log("VERIFICATION")
    log("=" * 60)
    _report(engine)
    _verify_minio()
    _verify_api()
    log("done.")


if __name__ == "__main__":
    main()
