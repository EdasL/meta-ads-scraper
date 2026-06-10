"""Enrich discovered candidate pages: app/review/download info + week-over-week ad activity.

For each selected page from a discover_experiment JSON dump:
  1. Deep-pull the page's FULL ad history (active_status=ALL, view_all_page_id) — active AND
     inactive ads, each with start_date/end_date.
  2. Reconstruct the weekly active-ad-count curve + new-ads-per-week + WoW velocity metrics
     (backfilled from one scrape — no waiting weeks). See agf.tracker.history.
  3. Resolve the app behind the page (scan its ads for a store link) -> App Store / Google Play
     metadata: name, developer, genre, release date, rating, rating count, installs.
  4. Emit an enriched HTML, SORTED SCALERS-FIRST (rising ad-count on a small page = the gem).

Run: AGF_PROXIES=... PYTHONPATH=src .venv/bin/python scripts/enrich_candidates.py \
        [candidates.json] [page_id ...]
Env: AGF_ENRICH_LIMIT (default 25), AGF_ENRICH_KINDS (default app,funnel), AGF_HIST_PAGES
(history pages/page, default 12), AGF_SWEEP_COUNTRIES (default US), AGF_KW_DELAY.
"""
import asyncio
import html
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import date

import httpx

from agf.classify import classify_url
from agf.extract.errors import ExtractError
from agf.extract.facade import Extractor
from agf.enrich_full.appstore import lookup_itunes_batch, lookup_google_play
from agf.tracker.history import (reconstruct_weekly_ad_counts, new_ads_per_week, wow_metrics)

WEEKS = 16
HIST_PAGES = int(os.environ.get("AGF_HIST_PAGES", "12"))
LIMIT = int(os.environ.get("AGF_ENRICH_LIMIT", "25"))
KINDS = [k.strip() for k in os.environ.get("AGF_ENRICH_KINDS", "app,funnel").split(",")]
COUNTRIES = [c.strip() for c in os.environ.get("AGF_SWEEP_COUNTRIES", "US").split(",") if c.strip()]
DELAY = float(os.environ.get("AGF_KW_DELAY", "2.5"))
DAY = 86400


@dataclass
class Enriched:
    page_id: str
    page_name: str
    page_like_count: int | None
    kind: str
    keyword: str
    total_ads: int = 0
    active_ads: int = 0
    oldest_start_days: int | None = None
    weekly: list = field(default_factory=list)
    new_weekly: list = field(default_factory=list)
    wow: dict = field(default_factory=dict)
    store_kind: str | None = None
    store_id: str | None = None
    app: dict | None = None
    creatives: list = field(default_factory=list)


def select(rows):
    rows = [r for r in rows if r["kind"] in KINDS]
    rows.sort(key=lambda r: -(r.get("n_ads_seen") or 0))   # most active first as a starting proxy
    return rows[:LIMIT]


def pull_history(ext, page_id):
    try:
        ads = asyncio.run(ext.search(query="", countries=COUNTRIES, search_type="page",
                                     view_all_page_id=page_id, active_status="ALL",
                                     max_pages=HIST_PAGES))
    except ExtractError as exc:
        print(f"   history pull {page_id} -> {exc}")
        return []
    seen, uniq = set(), []
    for a in ads:
        if a.ad_archive_id and a.ad_archive_id not in seen:
            seen.add(a.ad_archive_id)
            uniq.append(a)
    return uniq


def store_ref_from_ads(ads, fallback_link):
    """First app-store id found among a page's ads (or the candidate's own link)."""
    for a in ads:
        intent = classify_url(a.link_url)
        if intent.kind in ("ios", "android") and intent.store_id:
            return intent.kind, intent.store_id
    intent = classify_url(fallback_link)
    if intent.kind in ("ios", "android") and intent.store_id:
        return intent.kind, intent.store_id
    return None, None


def build_one(ext, row):
    e = Enriched(page_id=row["page_id"], page_name=row["page_name"],
                 page_like_count=row.get("page_like_count"), kind=row["kind"],
                 keyword=row.get("keyword", ""))
    ads = pull_history(ext, e.page_id)
    e.total_ads = len(ads)
    e.active_ads = sum(1 for a in ads if a.is_active)
    starts = [a.start_date for a in ads if a.start_date]
    if starts:
        e.oldest_start_days = round((time.time() - min(starts)) / DAY)
    e.weekly = reconstruct_weekly_ad_counts(ads, weeks=WEEKS)
    e.new_weekly = new_ads_per_week(ads, weeks=WEEKS)
    e.wow = wow_metrics(e.weekly)
    e.store_kind, e.store_id = store_ref_from_ads(ads, row.get("link_url"))
    # most recent creatives (with media) for the review card
    for a in sorted(ads, key=lambda a: -(a.start_date or 0)):
        if a.image_urls or a.video_urls:
            e.creatives.append({"img": a.image_urls[0] if a.image_urls else None,
                                "vid": a.video_urls[0] if a.video_urls else None})
        if len(e.creatives) >= 3:
            break
    return e


def attach_app_meta(rows: list):
    """Batch the store lookups across all rows (one iTunes batch + one Play batch)."""
    ios = {r.store_id for r in rows if r.store_kind == "ios" and r.store_id}
    android = {r.store_id for r in rows if r.store_kind == "android" and r.store_id}
    meta = {}
    if ios:
        try:
            client = httpx.Client(timeout=30, follow_redirects=True)
            meta.update({("ios", k): v for k, v in
                         lookup_itunes_batch(client, list(ios)).items()})
        except Exception as exc:  # noqa: BLE001
            print(f"   iTunes lookup failed: {exc}")
    if android:
        try:
            meta.update({("android", k): v for k, v in
                         lookup_google_play(list(android)).items()})
        except Exception as exc:  # noqa: BLE001
            print(f"   Google Play lookup failed: {exc}")
    for r in rows:
        m = meta.get((r.store_kind, r.store_id))
        if m:
            r.app = {"name": m.name, "developer": m.seller, "genre": m.genre,
                     "release_date": m.release_date, "rating": m.avg_rating,
                     "rating_count": m.rating_count, "installs": m.installs,
                     "icon": m.icon_url, "price": m.price_label}


def sparkline(series, width=180, height=40, color="#7db1ff"):
    if not series or max(series) == 0:
        return '<span class="muted">no data</span>'
    n, mx = len(series), max(series)
    pts = " ".join(f"{round(i*width/(n-1),1)},{round(height-(v/mx)*(height-4)-2,1)}"
                   for i, v in enumerate(series))
    return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{pts}"/></svg>')


def render(rows: list, path: str):
    order = {"scaling": 0, "steady": 1, "flat": 2, "declining": 3}
    rows = sorted(rows, key=lambda r: (order.get(r.wow.get("trend"), 4),
                                       -(r.wow.get("growth_4w") or 0)))

    def esc(x):
        return html.escape(str(x)) if x is not None else ""

    cards = []
    for r in rows:
        a = r.app or {}
        icon = f'<img class="icon" src="{esc(a.get("icon"))}">' if a.get("icon") else \
            '<div class="icon noicon"></div>'
        rating = f'★ {a["rating"]:.1f} ({a["rating_count"]:,})' if a.get("rating") else ""
        installs = f'{a["installs"]:,}+ installs' if a.get("installs") else ""
        wow = r.wow
        trend = wow.get("trend", "flat")
        g4 = wow.get("growth_4w")
        crea = "".join(
            (f'<video src="{esc(c["vid"])}" muted loop playsinline></video>' if c.get("vid")
             else f'<img src="{esc(c["img"])}">') for c in r.creatives) or \
            '<span class="muted">—</span>'
        cards.append(f"""
        <div class="card {esc(trend)}">
          <div class="hd">{icon}
            <div><div class="nm">{esc(a.get("name") or r.page_name)}
              <span class="badge {esc(r.kind)}">{esc(r.kind)}</span>
              <span class="badge t-{esc(trend)}">{esc(trend)}</span></div>
              <div class="sub">{esc(a.get("genre") or "")} {('· '+esc(a.get("developer"))) if a.get("developer") else ''}</div>
            </div></div>
          <div class="wow">
            <div class="big">{wow.get('current',0)} <span>active ads</span>
              {('· ×'+str(g4)+' / 4wk') if g4 else ''}
              {('· '+str(wow.get('wow_pct'))+'% wk/wk') if wow.get('wow_pct') is not None else ''}</div>
            {sparkline(r.weekly)}
            <div class="series">{esc(r.weekly)}</div>
          </div>
          <div class="meta">{esc(rating)} {('· '+installs) if installs else ''}
            {('· release '+esc(a.get('release_date'))) if a.get('release_date') else ''}</div>
          <div class="meta">{esc(r.page_like_count)} page likes · {r.total_ads} ads in history
            · oldest {esc(r.oldest_start_days)}d · found via "{esc(r.keyword)}"</div>
          <div class="crea">{crea}</div>
        </div>""")

    n_scaling = sum(1 for r in rows if r.wow.get("trend") == "scaling")
    doc = f"""<!doctype html><html><head><meta charset="utf-8"><title>AGF enriched {date.today()}</title>
<style>
body{{font:14px/1.45 -apple-system,system-ui,sans-serif;margin:0;background:#0e0f12;color:#e8e8ea}}
header{{padding:14px 20px;background:#16181d;border-bottom:1px solid #2a2d34;position:sticky;top:0;z-index:2}}
h1{{font-size:16px;margin:0}} .sum{{color:#9aa0aa;font-size:13px;margin-top:4px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:14px;padding:16px}}
.card{{background:#16181d;border:1px solid #2a2d34;border-radius:12px;padding:14px}}
.card.scaling{{border-color:#2f9e6f;box-shadow:0 0 0 1px #2f9e6f33}}
.card.declining{{opacity:.72}}
.hd{{display:flex;gap:10px;align-items:center;margin-bottom:8px}}
.icon{{width:48px;height:48px;border-radius:11px;object-fit:cover;background:#0a0b0d}}
.noicon{{border:1px solid #2a2d34}} .nm{{font-weight:600}}
.sub{{color:#9aa0aa;font-size:12px}}
.badge{{font-size:10px;padding:1px 6px;border-radius:6px;background:#2a2d34;margin-left:4px}}
.badge.app{{background:#2f6f4f}} .badge.funnel{{background:#6a5acd}}
.t-scaling{{background:#2f9e6f}} .t-declining{{background:#7a3a3a}} .t-steady{{background:#3a4252}}
.wow{{margin:8px 0;padding:8px;background:#0d0f13;border-radius:8px}}
.big{{font-size:16px;font-weight:600}} .big span{{font-size:12px;color:#9aa0aa;font-weight:400}}
.series{{color:#6b7280;font-size:11px;margin-top:2px;word-break:break-all}}
.meta{{color:#9aa0aa;font-size:12px;margin:3px 0}}
.crea{{display:flex;gap:6px;margin-top:8px}} .crea img,.crea video{{width:72px;height:72px;object-fit:cover;border-radius:8px}}
.muted{{color:#555}}
</style></head><body>
<header><h1>AGF enriched candidates — {len(rows)} pages · {n_scaling} scaling</h1>
<div class="sum">week-over-week ad-count reconstructed from full history · sorted scalers-first · countries {",".join(COUNTRIES)}</div>
</header><div class="grid">{''.join(cards)}</div></body></html>"""
    with open(path, "w") as fh:
        fh.write(doc)


def dump_json(rows, path):
    with open(path, "w") as fh:
        json.dump([r.__dict__ for r in rows], fh, indent=2, default=str)


def main():
    args = [a for a in sys.argv[1:]]
    json_path = next((a for a in args if a.endswith(".json")), None) \
        or f"/tmp/agf-candidates-{date.today().isoformat()}.json"
    explicit_ids = [a for a in args if not a.endswith(".json")]
    if not os.environ.get("AGF_PROXIES"):
        print("ERROR: AGF_PROXIES not set.", file=sys.stderr)
        sys.exit(2)

    rows = json.loads(open(json_path).read())
    by_id = {r["page_id"]: r for r in rows}
    selected = [by_id[i] for i in explicit_ids if i in by_id] if explicit_ids else select(rows)
    print(f"Enriching {len(selected)} pages (history pages={HIST_PAGES}) ...")

    ext = Extractor(search_delay=2.0, search_jitter=1.5)
    for attempt in range(6):
        try:
            ext._pool.invalidate(); ext._pool.acquire(); break
        except Exception as exc:  # noqa: BLE001
            print(f"  warm {attempt} failed: {exc}"); time.sleep(2)

    out_html = f"/tmp/agf-enriched-{date.today().isoformat()}.html"
    out_json = f"/tmp/agf-enriched-{date.today().isoformat()}.json"
    enriched = []
    for i, row in enumerate(selected, 1):
        e = build_one(ext, row)
        enriched.append(e)
        print(f"  [{i}/{len(selected)}] {e.page_name[:32]:32} ads={e.total_ads:>3} "
              f"active={e.active_ads:>3} trend={e.wow.get('trend')} "
              f"x4w={e.wow.get('growth_4w')} store={e.store_kind}")
        attach_app_meta(enriched)        # incremental so partial output is enriched too
        render(enriched, out_html)
        dump_json(enriched, out_json)
        time.sleep(DELAY)

    scaling = [e for e in enriched if e.wow.get("trend") == "scaling"]
    print(f"\nDONE: {len(enriched)} enriched, {len(scaling)} scaling.")
    print(f"Review HTML: {out_html}")
    print(f"JSON       : {out_json}")


if __name__ == "__main__":
    main()
