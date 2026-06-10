"""Seed a small, realistic demo corpus into Postgres so the live stack (Plan 7
read-only FastAPI + the Next.js dossier UI in NEXT_PUBLIC_DATA_MODE=live) has
something to render. Inserts advertisers/apps/ads/funnels/creatives/weekly
metrics + enrichment (domain, Instagram, parent company), then runs the REAL
scorer (run_scoring) so gem_score/alert/vetoes are internally consistent — not
hand-faked. Idempotent; all rows carry the DEMO* page_id sentinel.

    export AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf
    .venv/bin/python scripts/seed_demo.py            # seed + score + refresh
    .venv/bin/python scripts/seed_demo.py --clean    # remove the demo corpus
"""
import os
import sys
from datetime import date, datetime, timezone, timedelta

from sqlalchemy import text

from agf.db.engine import get_engine, begin
from agf.db import repo
from agf.db.dedup import sha256_bytes, phash_image
from agf.parse import AdRecord
from agf.scoring.run_scoring import run_scoring, refresh_emerging_matview
from agf.storage.s3 import get_s3, put_bytes

import demo_media  # type: ignore[reportMissingImports]  # sibling script; resolved via sys.path[0] when run as `python scripts/seed_demo.py`

AS_OF = date(2026, 6, 4)
NOW = int(datetime(2026, 6, 4, tzinfo=timezone.utc).timestamp())
DAY = 86_400
WEEK0 = date(2026, 4, 6)                       # 9 weekly points up to early June
SENT_PREFIX = "DEMO"

# Each spec is a self-contained gem. `series` is the weekly active-ad-count
# trajectory (oldest→newest); `creatives`/`funnel` enrich the dossier.
SPECS = [
    {
        # breakout that should clearly dominate the Health & Fitness cohort → ALERT
        "pid": "DEMO1", "page_name": "SleepWave", "genre": "Health & Fitness",
        "app_name": "SleepWave: deep sleep sounds", "store_id": "id1693820",
        "rating_count": 40, "rating": 4.8, "release": "2026-05-18", "emerging": True,
        "peak": 13,
        "longevity_days": 31, "series": [1, 2, 4, 8, 15, 27, 46, 70, 100],
        "reach": 12_000, "ig": ("sleepwave.app", 900), "domain": "sleepwave.app",
        "funnel": ("https://sleepwave.app/tt", "https://apps.apple.com/app/id1693820"),
        "creatives": [("video", "cant sleep? brain go quiet", "POV: your brain finally shuts up"),
                      ("image", "7 day calm", "people sleep in 7 day. real review.")],
    },
    {
        "pid": "DEMO2", "page_name": "CalorieCam", "genre": "Health & Fitness",
        "app_name": "CalorieCam: snap your food", "store_id": "id1771002",
        "rating_count": 3_500, "rating": 4.6, "release": "2026-03-30", "emerging": True,
        "peak": 9,
        "longevity_days": 27, "series": [8, 9, 11, 12, 14, 16, 19, 22, 25],
        "reach": 200_000, "ig": ("caloriecam", 40_000), "domain": "caloriecam.io",
        "funnel": ("https://caloriecam.io/start", "https://apps.apple.com/app/id1771002"),
        "creatives": [("video", "snap food. see number.", "grug eat. grug see calorie. easy.")],
    },
    {
        "pid": "DEMO3", "page_name": "PostureFix", "genre": "Health & Fitness",
        "app_name": "PostureFix daily", "store_id": "id1822333",
        "rating_count": 1_500, "rating": 4.9, "release": "2026-04-12", "emerging": True,
        "peak": 11,
        "longevity_days": 22, "series": [3, 4, 5, 6, 7, 8, 10, 12, 14],
        "reach": 120_000, "ig": ("posturefix", 18_000), "domain": "posturefix.co",
        "funnel": ("https://posturefix.co/quiz", None),
        "creatives": [("image", "stand up straight", "back hurt? do this 2 min.")],
    },
    {
        # disguised incumbent: owned by a parent → dossier shows the owner, scorer vetoes it
        "pid": "DEMO4", "page_name": "FlexLabs", "genre": "Health & Fitness",
        "app_name": "FlexLabs stretch", "store_id": "id1900111",
        "rating_count": 1_200, "rating": 4.3, "release": "2026-04-15", "emerging": True,
        "peak": 12,
        "longevity_days": 30, "series": [4, 6, 9, 12, 16, 21, 27, 34, 42],
        "reach": 60_000, "ig": ("flexlabs", 5_400), "domain": "flexlabs.fit",
        "parent": "DEMO1",
        "funnel": ("https://flexlabs.fit/go", "https://apps.apple.com/app/id1900111"),
        "creatives": [("video", "stretch with us", "10 min stretch. feel good.")],
    },
    {
        # famous brand incumbent: flat high volume + huge rating base → UR≈0, low score
        "pid": "DEMO5", "page_name": "MegaFit", "genre": "Health & Fitness",
        "app_name": "MegaFit", "store_id": "id00099",
        "rating_count": 8_400_000, "rating": 4.7, "release": "2015-06-01", "emerging": False,
        "peak": 14,
        "longevity_days": 800, "series": [120, 119, 121, 118, 122, 120, 119, 121, 120],
        "reach": 9_000_000, "ig": ("megafit", 2_400_000), "domain": "megafit.com",
        "funnel": None, "creatives": [],
    },
    {
        # breakout that should clearly dominate the Education cohort → ALERT
        "pid": "DEMO6", "page_name": "TutorGenie", "genre": "Education",
        "app_name": "TutorGenie: homework help", "store_id": "id2010777",
        "rating_count": 40, "rating": 4.7, "release": "2026-05-15", "emerging": True,
        "peak": 10,
        "longevity_days": 24, "series": [1, 2, 4, 7, 12, 20, 30, 44, 60],
        "reach": 15_000, "ig": ("tutorgenie", 800), "domain": "tutorgenie.app",
        "funnel": ("https://tutorgenie.app/try", "https://apps.apple.com/app/id2010777"),
        "creatives": [("video", "homework done fast", "snap homework. get answer. learn.")],
    },
    {
        # web-only funnel (no app) in Education — exercises app_id NULL / "web only"
        "pid": "DEMO7", "page_name": "LinguaLoop", "genre": "Education",
        "app_name": None, "store_id": None,
        "rating_count": None, "rating": None, "release": None, "emerging": False,
        "peak": 8,
        "longevity_days": 20, "series": [1, 2, 3, 5, 7, 10, 14, 19, 25],
        "reach": 30_000, "ig": ("lingualoop", 2_200), "domain": "lingualoop.com",
        "funnel": ("https://lingualoop.com/learn", "https://lingualoop.com/checkout"),
        "creatives": [("image", "learn lang in car", "30 day talk new language. just listen.")],
    },
    {
        "pid": "DEMO8", "page_name": "ChemQuest", "genre": "Education",
        "app_name": "ChemQuest", "store_id": "id2099888",
        "rating_count": 3_000, "rating": 4.5, "release": "2026-03-20", "emerging": True,
        "peak": 6,
        "longevity_days": 18, "series": [2, 3, 4, 5, 6, 7, 8, 9, 11],
        "reach": 180_000, "ig": ("chemquest", 30_000), "domain": "chemquest.edu",
        "funnel": ("https://chemquest.edu/play", None),
        "creatives": [("video", "chemistry game", "learn chem by play. no boring book.")],
    },
    {
        "pid": "DEMO9", "page_name": "QuizWhiz", "genre": "Education",
        "app_name": "QuizWhiz trivia", "store_id": "id2155444",
        "rating_count": 9_000, "rating": 4.2, "release": "2025-11-01", "emerging": False,
        "peak": 7,
        "longevity_days": 40, "series": [22, 21, 23, 22, 24, 23, 22, 24, 23],
        "reach": 400_000, "ig": ("quizwhiz", 120_000), "domain": "quizwhiz.io",
        "funnel": None,
        "creatives": [("image", "trivia night", "play trivia. win nothing. still fun.")],
    },
]

ALL_SENT = [s["pid"] for s in SPECS]


def _domain_id(conn, domain: str) -> int:
    return conn.execute(text(
        "INSERT INTO public.domains (domain) VALUES (:d) "
        "ON CONFLICT (domain) DO UPDATE SET domain = EXCLUDED.domain RETURNING id"),
        {"d": domain}).scalar_one()


def _scale_series(series: list[int], peak: int) -> list[int]:
    """Rescale a weekly active-ad trajectory so its MAX lands on `peak`, keeping
    the growth shape. Every point clamps to >=1 and the last week is pinned to
    `peak` so the matview's active_ad_count (== number of active ads we seed)
    equals the latest weekly active_ad_count. Monotonic-ish input stays so."""
    hi = max(series) or 1
    scaled = [max(1, round(n * peak / hi)) for n in series]
    scaled[-1] = peak                       # pin the latest week to the true peak
    return scaled


def _ad_record(s, archive_suffix: str, start_epoch: int, *, body_text=None) -> "AdRecord":
    """Build one ACTIVE ad row for a spec (end_date=None). Distinct ad_archive_id
    per index so upsert_ad's ON CONFLICT(external_ad_id) inserts N rows, not 1."""
    return AdRecord(
        ad_archive_id=f"{s['pid']}-AD-{archive_suffix}", page_id=s["pid"],
        page_name=s["page_name"], is_active=True,
        start_date=start_epoch, end_date=None,
        collation_count=max(s["series"][-1] // 3, 3),
        cta_type="INSTALL_MOBILE_APP" if s["store_id"] else "SIGN_UP",
        cta_text="Install" if s["store_id"] else "Sign up",
        link_url=(f"https://apps.apple.com/app/{s['store_id']}" if s["store_id"]
                  else s["funnel"][0] if s["funnel"] else None),
        body_text=body_text,
        image_urls=[], video_urls=[],
    )


def _upload_creative_media(s3, s, ctype, idx, title) -> tuple[str, str, str, bytes]:
    """Generate + upload REAL bytes for one creative; return
    (final_type, s3_key, content_type, data). Falls back to a PNG poster (and a
    flipped 'image' type) for video when ffmpeg is unavailable."""
    seed = s["pid"]
    app_name = s["app_name"] or s["page_name"]
    if ctype == "video":
        if demo_media.ffmpeg_available():
            data = demo_media.render_clip_mp4(seed, app_name, title)
            key = f"creatives/{s['pid']}/{idx}.mp4"
            put_bytes(key, data, "video/mp4", client=s3)
            return "video", key, "video/mp4", data
        # fallback: real PNG poster, demoted to an image creative
        data = demo_media.render_tile_png(seed, app_name, title)
        key = f"creatives/{s['pid']}/{idx}.png"
        put_bytes(key, data, "image/png", client=s3)
        print(f"  note: ffmpeg missing — {s['pid']} creative {idx} served as PNG "
              f"poster (real video demo media needs ffmpeg on PATH).")
        return "image", key, "image/png", data
    data = demo_media.render_tile_png(seed, app_name, title)
    key = f"creatives/{s['pid']}/{idx}.png"
    put_bytes(key, data, "image/png", client=s3)
    return "image", key, "image/png", data


# A handful of extra title/body variants so each advertiser can reach 3–6
# creatives without 1-creative specs all looking the same.
_VARIANTS = [
    (" — try it free", "free for 7 day. then you decide."),
    (" (new)", "grug just ship this. tell grug if bad."),
    (" v2", "made it better. faster. less annoy."),
    (" — limited", "this week only. then gone."),
]


def _expand_creatives(s) -> list[tuple[str, str, str]]:
    """Grow a spec's seed creatives to 3–6 (type, title, body) entries, cycling
    the originals with title/body variants. Empty creative lists stay empty
    (e.g. the famous-brand incumbent that intentionally has no demo media)."""
    base = list(s["creatives"])
    if not base:
        return []
    target = min(6, max(3, len(base) + 2))     # 3..6, modest bump per advertiser
    out = list(base)
    vi = 0
    while len(out) < target:
        ctype, title, _ = base[len(out) % len(base)]
        suffix, alt_body = _VARIANTS[vi % len(_VARIANTS)]
        out.append((ctype, f"{title}{suffix}", alt_body))
        vi += 1
    return out


def _seed(engine) -> dict:
    ids = {}
    s3 = get_s3()                            # bucket=agf (S3_BUCKET); creates it if absent
    with begin(engine) as conn:
        # pass 1: advertisers + apps + ads + funnels + creatives + weekly metrics
        for s in SPECS:
            scaled = _scale_series(s["series"], s["peak"])
            n_ads = scaled[-1]               # active_ad_count == latest weekly count
            creatives = _expand_creatives(s)

            # advertiser + app from a representative active ad
            head = _ad_record(s, "1", NOW - s["longevity_days"] * DAY,
                              body_text=creatives[0][2] if creatives else None)
            adv = repo.upsert_advertiser(conn, head)
            ids[s["pid"]] = adv
            app_id = None
            if s["store_id"]:
                app_id = repo.upsert_app(conn, {
                    "store": "ios", "store_id": s["store_id"], "name": s["app_name"],
                    "developer": s["page_name"], "genre": s["genre"],
                    "release_date": s["release"], "rating": s["rating"],
                    "rating_count": s["rating_count"], "is_emerging": s["emerging"]})

            # N ACTIVE ad rows with staggered start_dates so longevity is sensible:
            # oldest ad ~longevity_days ago, newest ~a few days ago.
            first_ad_id = None
            span = max(s["longevity_days"] - 3, 1)
            for k in range(n_ads):
                offset = s["longevity_days"] - round(span * k / max(n_ads - 1, 1))
                ar = _ad_record(s, str(k + 1), NOW - offset * DAY,
                                body_text=creatives[0][2] if creatives else None)
                ad_id = repo.upsert_ad(conn, ar, adv, app_id)
                if first_ad_id is None:
                    first_ad_id = ad_id
            if s["funnel"]:
                repo.upsert_funnel(conn, first_ad_id, s["funnel"][0], s["funnel"][1],
                                   f"funnels/{s['pid']}/step1.png")

            # creatives: REAL media uploaded to S3 (bucket agf), keyed so the Go
            # media proxy serves them; attached to the first ad (dossier reads all
            # creatives per advertiser).
            for i, (ctype, title, body) in enumerate(creatives):
                final_type, key, content_type, data = _upload_creative_media(
                    s3, s, ctype, i, title)
                sha = sha256_bytes(data)
                ph = phash_image(data)
                cid = repo.upsert_creative(conn, first_ad_id, final_type, body, title,
                                           None, key, sha, ph)
                conn.execute(text(
                    "UPDATE public.creatives SET content_type=:ct, file_size=:fs WHERE id=:i"),
                    {"ct": content_type, "fs": len(data), "i": cid})

            # weekly series: latest active_ad_count == n_ads; distinct_creative_count
            # ramps up to the ACTUAL number of creatives seeded (not a flat 50).
            n_creatives = len(creatives)
            weeks = len(scaled)
            for i, n in enumerate(scaled):
                if n_creatives:
                    ccount = max(1, round(n_creatives * (i + 1) / weeks))
                    ccount = n_creatives if i == weeks - 1 else min(ccount, n_creatives)
                else:
                    ccount = 0
                repo.upsert_app_advertiser_metric(
                    conn, adv, WEEK0 + timedelta(weeks=i), n, ccount, s["reach"])
            # enrichment: domain + Instagram (parent linked in pass 2)
            repo.upsert_advertiser_enrichment(
                conn, adv, domain_id=_domain_id(conn, s["domain"]),
                page_category="Health/Wellness" if s["genre"].startswith("Health") else "Education",
                ig_username=s["ig"][0], ig_followers=s["ig"][1])
        # pass 2: parent links (now that every advertiser id exists)
        for s in SPECS:
            if s.get("parent"):
                repo.upsert_advertiser_enrichment(
                    conn, ids[s["pid"]], parent_company_id=ids[s["parent"]])
    # real scoring + matview refresh (own committed txn inside run_scoring)
    scored = run_scoring(engine, AS_OF)
    return {"advertisers": ids, "scored": scored}


def _clean(engine) -> None:
    with begin(engine) as conn:
        ids = conn.execute(text("SELECT id FROM public.advertisers WHERE page_id = ANY(:p)"),
                           {"p": ALL_SENT}).scalars().all()
        if ids:
            for t in ("frontend.review_decisions", "frontend.saved_gems",
                      "adtech.advertiser_scores", "adtech.advertiser_growth",
                      "adtech.advertiser_explainers", "adtech.app_advertiser_metrics"):
                conn.execute(text(f"DELETE FROM {t} WHERE advertiser_id = ANY(:i)"), {"i": ids})
            conn.execute(text("DELETE FROM adtech.ad_metrics m USING public.ads a "
                              "WHERE m.ad_id=a.id AND a.advertiser_id = ANY(:i)"), {"i": ids})
            conn.execute(text("DELETE FROM public.funnels f USING public.ads a "
                              "WHERE f.ad_id=a.id AND a.advertiser_id = ANY(:i)"), {"i": ids})
            conn.execute(text("DELETE FROM public.creatives c USING public.ads a "
                              "WHERE c.ad_id=a.id AND a.advertiser_id = ANY(:i)"), {"i": ids})
            conn.execute(text("DELETE FROM public.ads WHERE advertiser_id = ANY(:i)"), {"i": ids})
            conn.execute(text("UPDATE public.advertisers SET parent_company_id=NULL, domain_id=NULL "
                              "WHERE id = ANY(:i)"), {"i": ids})
            conn.execute(text("DELETE FROM public.advertisers WHERE id = ANY(:i)"), {"i": ids})
        conn.execute(text("DELETE FROM public.apps WHERE store_id = ANY(:p)"),
                     {"p": [s["store_id"] for s in SPECS if s["store_id"]]})
        # drop the demo domains too, but only if nothing else still references them
        conn.execute(text(
            "DELETE FROM public.domains WHERE domain = ANY(:d) AND id NOT IN "
            "(SELECT domain_id FROM public.advertisers WHERE domain_id IS NOT NULL)"),
            {"d": [s["domain"] for s in SPECS]})
    refresh_emerging_matview(engine)


def _db_url() -> str:
    url = os.environ.get("AGF_DATABASE_URL") or os.environ.get("AGF_TEST_DB_URL")
    if not url:
        sys.exit("set AGF_DATABASE_URL (or AGF_TEST_DB_URL), e.g. "
                 "postgresql://agf:agf@localhost:5433/agf")
    return url


def main() -> None:
    engine = get_engine(_db_url())
    if "--clean" in sys.argv:
        _clean(engine)
        print("demo corpus removed.")
        return
    _clean(engine)                       # idempotent: clear any prior demo run first
    out = _seed(engine)
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT e.page_name, e.app_name, ROUND(e.gem_score::numeric, 1) AS gem_score, "
            "e.alert, e.cohort FROM frontend.emerging_scaling_apps e "
            "JOIN public.advertisers a ON a.id = e.advertiser_id "
            "WHERE a.page_id = ANY(:p) ORDER BY e.gem_score DESC"), {"p": ALL_SENT}).all()
        vetoed = conn.execute(text(
            "SELECT a.page_name, s.veto_reason FROM adtech.advertiser_scores s "
            "JOIN public.advertisers a ON a.id = s.advertiser_id "
            "WHERE a.page_id = ANY(:p) AND s.vetoed"), {"p": ALL_SENT}).all()
    print(f"scored {out['scored']} advertisers; {len(rows)} in the emerging matview:")
    for r in rows:
        flag = " ALERT" if r.alert else ""
        print(f"  {r.gem_score:>5}  {r.page_name:<12} {r.cohort:<22} "
              f"{r.app_name or '(web only)'}{flag}")
    if vetoed:
        print("vetoed (not in matview):")
        for v in vetoed:
            print(f"  {v.page_name:<12} {v.veto_reason}")


if __name__ == "__main__":
    main()
