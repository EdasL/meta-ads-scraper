"""Plan 6 LIVE assembled smoke (the RUN-IT-LIVE rule). Not a pytest test — a
throwaway driver. Exercises the genuinely-live parts against real infra:
real iTunes Lookup, real Playwright chromium → real MinIO, real WHOIS, real
creative byte download → MinIO, and the assembled enrich_gem on a seeded gem.

Run:
  AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf \
  S3_ENDPOINT=http://localhost:9010 MINIO_ACCESS_KEY=minioadmin \
  MINIO_SECRET_KEY=minioadmin S3_BUCKET=agf-test \
  PYTHONPATH=src .venv/bin/python tests/enrich_full/live_smoke.py
"""
import os
import httpx
from datetime import date
from sqlalchemy import text
from agf.db.engine import get_engine, begin
from agf.db.migrate import apply_migrations
from agf.parse import AdRecord
from agf.db import repo
from agf.extract.deeplink import resolve_deeplink
from agf.storage.s3 import get_s3, put_bytes
from agf.enrich_full.appstore import lookup_itunes_batch
from agf.enrich_full.funnel_capture import capture_funnel
from agf.enrich_full.creatives import download_creative
from agf.enrich_full.company import domain_age
from agf.enrich_full.run_enrich import enrich_gem, EnrichDeps

BUCKET = os.environ["S3_BUCKET"]
APP_ID = "389801252"           # Instagram (stable iTunes result)
FUNNEL = "https://example.com"
SENT = "P6SMOKE"


class DirectExtractor:
    """resolve_deeplink for direct/web links needs no network (no FB warming)."""
    async def resolve_deeplink(self, url, ua="ios"):
        return resolve_deeplink(None, url, ua=ua)


def s3_exists(s3, key):
    try:
        s3.head_object(Bucket=BUCKET, Key=key)
        return True
    except Exception:
        return False


def main():
    eng = get_engine(os.environ["AGF_TEST_DB_URL"])
    apply_migrations(eng)
    s3 = get_s3()
    ok = []

    # 1. real iTunes Lookup
    with httpx.Client(timeout=30) as c:
        metas = lookup_itunes_batch(c, [APP_ID])
    m = metas.get(APP_ID)
    print(f"[1] iTunes  : {m.name!r} release={m.release_date} ratings={m.rating_count}")
    ok.append(bool(m and m.name and m.release_date))

    # 2. real chromium funnel screenshot -> real MinIO
    steps = capture_funnel(FUNNEL, max_steps=1, s3=s3)   # page=None -> real chromium
    shot_key = steps[0].screenshot_s3_key
    print(f"[2] chromium: {len(steps)} step(s), title={steps[0].title!r}, key={shot_key}")
    ok.append(bool(shot_key) and s3_exists(s3, shot_key))

    # 3. real WHOIS (tolerate flakiness)
    created, age, reg = domain_age("example.com")
    print(f"[3] whois   : example.com created={created} age_days={age} registrar={reg}")
    ok.append(created is not None)   # informational; example.com should resolve

    # 4. real creative byte download (apple CDN artwork) -> real MinIO
    art = _artwork(APP_ID)
    with begin(eng) as conn:
        adv0 = repo.upsert_advertiser(conn, _ad("ART", "https://x"))
        ad0 = repo.upsert_ad(conn, _ad("ART", "https://x"), adv0, None)
        ca = download_creative(conn, ad0, art, "image", s3=s3)
        print(f"[4] creative: sha={ca.sha256[:12]} key={ca.s3_key} size={ca.file_size}")
        ok.append(bool(ca.s3_key) and s3_exists(s3, ca.s3_key))
        conn.execute(text("DELETE FROM public.creatives WHERE ad_id=:i"), {"i": ad0})
        conn.execute(text("DELETE FROM public.ads WHERE id=:i"), {"i": ad0})
        conn.execute(text("DELETE FROM public.advertisers WHERE id=:i"), {"i": adv0})

    # 5. assembled enrich_gem on a seeded gem (real iTunes + real chromium + real MinIO)
    _cleanup(eng)
    try:
        with begin(eng) as conn:
            app_ad = _ad("P6SMOKE-IOS", f"https://apps.apple.com/us/app/x/id{APP_ID}")
            web_ad = _ad("P6SMOKE-WEB", FUNNEL)
            adv = repo.upsert_advertiser(conn, app_ad)
            ios_id = repo.upsert_ad(conn, app_ad, adv, None)
            web_id = repo.upsert_ad(conn, web_ad, adv, None)
            with httpx.Client(timeout=30) as c:
                deps = EnrichDeps(extractor=DirectExtractor(), itunes_client=c,
                                  fetch_text=lambda u: httpx.get(u, timeout=15).text,
                                  put=put_bytes, s3=s3, capture=True,
                                  whois_fn=lambda d: type("W", (), {"creation_date": None,
                                                                    "registrar": None})())
                import asyncio
                res = asyncio.run(enrich_gem(conn, adv, top_n=5, deps=deps))
            app_id = conn.execute(text("SELECT app_id FROM public.ads WHERE id=:i"),
                                  {"i": ios_id}).scalar()
            fr = conn.execute(text("SELECT screenshot_s3_key FROM public.funnels WHERE ad_id=:i"),
                              {"i": web_id}).scalar()
            dom = conn.execute(text("SELECT domain_id FROM public.advertisers WHERE id=:i"),
                               {"i": adv}).scalar()
            print(f"[5] enrich  : ads={res.n_ads_processed} app_linked={app_id is not None} "
                  f"funnel_key={fr} domain_id={dom} tech={res.company.tech}")
            ok.append(app_id is not None and bool(fr) and dom is not None and s3_exists(s3, fr))
    finally:
        _cleanup(eng)

    print(f"\nRESULT: {sum(ok)}/{len(ok)} live checks passed -> "
          f"{'ALL GREEN' if all(ok) else 'SOME FAILED'}")
    return 0 if all(ok) else 1


def _artwork(app_id):
    r = httpx.get("https://itunes.apple.com/lookup", params={"id": app_id}, timeout=30).json()
    res = r["results"][0]
    return res.get("artworkUrl512") or res.get("artworkUrl100") or res.get("artworkUrl60")


def _ad(ext, link):
    return AdRecord(ext, SENT, "GemCo", True, 1_714_500_000, None, 1,
                    "INSTALL_MOBILE_APP", "Install", link, "body")


def _cleanup(eng):
    with begin(eng) as conn:
        ids = conn.execute(text("SELECT id FROM public.advertisers WHERE page_id=:p"),
                           {"p": SENT}).scalars().all()
        if ids:
            conn.execute(text("DELETE FROM public.funnels f USING public.ads a "
                              "WHERE f.ad_id=a.id AND a.advertiser_id = ANY(:i)"), {"i": ids})
            conn.execute(text("DELETE FROM public.creatives c USING public.ads a "
                              "WHERE c.ad_id=a.id AND a.advertiser_id = ANY(:i)"), {"i": ids})
            conn.execute(text("DELETE FROM public.ads WHERE advertiser_id = ANY(:i)"), {"i": ids})
            conn.execute(text("DELETE FROM public.advertisers WHERE id = ANY(:i)"), {"i": ids})


if __name__ == "__main__":
    raise SystemExit(main())
