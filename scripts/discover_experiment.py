"""Emerging-app/funnel discovery experiment harness.

RESEARCH HARNESS ONLY — production discovery is src/agf/discovery/sweep.py + triage.py
(see ops/RUNBOOK.md). This script remains for technique experiments / HTML review.

Runs several discovery TECHNIQUES over the keyword taxonomy against the live Meta Ad Library,
records per-technique yield so we can reason about what works, dedups candidates by page_id,
vetoes incumbents by page_like_count, classifies app-vs-funnel by destination URL, and emits a
visual HTML review file (one card per candidate, with the real ad creative) for a human skim.

Techniques compared:
  S-funnel   : keyword search over funnel/quiz exact-phrases  (KEYWORD_EXACT_PHRASE, relevancy)
  S-niche    : keyword search over app-niche terms            (KEYWORD_UNORDERED, relevancy)
  S-store    : keyword search over app-store URL fragments    (KEYWORD_UNORDERED, relevancy)
  TA         : typeahead keyword->pages (page_id + likes), bounded PAGE pull for creative

Run (from repo root):
    AGF_PROXIES="http://user:pass@gw.dataimpulse.com:823" PYTHONPATH=src \
        .venv/bin/python scripts/discover_experiment.py

Env knobs: AGF_TARGET (min unique candidates, default 120), AGF_MAX_KW (cap keywords/layer,
0=all), AGF_VETO_LIKES (incumbent veto, default 500000), AGF_SWEEP_COUNTRIES (default US),
AGF_KW_DELAY (inter-keyword pause s), AGF_TA_PAGE_PULL (max typeahead pages to pull creative for).
"""
import asyncio
import hashlib
import html
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import date

from agf.classify import classify_url
from agf.discovery import taxonomy
from agf.extract.errors import ExtractError
from agf.extract.facade import Extractor

TARGET = int(os.environ.get("AGF_TARGET", "120"))
MAX_KW = int(os.environ.get("AGF_MAX_KW", "0"))             # 0 = all keywords in a layer
VETO_LIKES = int(os.environ.get("AGF_VETO_LIKES", "500000"))
COUNTRIES = [c.strip() for c in os.environ.get("AGF_SWEEP_COUNTRIES", "US").split(",") if c.strip()]
INTER_KW_DELAY = float(os.environ.get("AGF_KW_DELAY", "3.0"))
TA_PAGE_PULL = int(os.environ.get("AGF_TA_PAGE_PULL", "40"))

STAMP = date.today().isoformat()
HTML_PATH = f"/tmp/agf-candidates-{STAMP}.html"
JSON_PATH = f"/tmp/agf-candidates-{STAMP}.json"

FUNNEL_WORDS = ("quiz", "personalized", "plan", "your results", "based on your",
                "tailored", "custom", "journey", "assessment", "find your")
LEADGEN_CTAS = {"SIGN_UP", "SUBSCRIBE", "GET_OFFER", "LEARN_MORE", "GET_QUOTE",
                "BOOK_TRAVEL", "APPLY_NOW", "DOWNLOAD"}
# Destinations that are neither an app nor a funnel — a social page or ad redirect. Drop them.
SOCIAL_HOSTS = {"facebook.com", "fb.me", "m.facebook.com", "l.facebook.com", "lm.facebook.com",
                "instagram.com", "api.whatsapp.com", "wa.me", "t.co", "ad.doubleclick.net",
                "youtube.com", "youtu.be", "twitter.com", "x.com", "linktr.ee", "messenger.com"}
# Sub-domains / infra that signal a real web2app or quiz funnel even without funnel copy.
FUNNEL_HOST_HINTS = ("quiz.", "try.", "tour.", "get.", "go.", "start.", "join.", "app.",
                     "interview.", "info.", "onelink", "adjust", "adapty", "dynamicflow",
                     "lovable.app", "typeform", "involve.me", ".health", "reverse.health")


def host_of(url: str | None) -> str:
    if not url:
        return ""
    from urllib.parse import urlparse
    return urlparse(url).netloc.lower().replace("www.", "")


@dataclass
class Candidate:
    page_id: str
    page_name: str
    page_like_count: int | None
    kind: str                 # "app" | "funnel" | "web"
    store_kind: str           # "ios" | "android" | "web" | "none"
    cta_type: str | None
    link_url: str | None
    body_sample: str | None
    image_url: str | None
    video_url: str | None
    source: str               # technique id
    keyword: str
    start_age_days: float | None
    n_ads_seen: int = 1
    creative_hashes: set = field(default_factory=set)

    @property
    def distinct_creatives(self) -> int:
        return len(self.creative_hashes)


@dataclass
class TechniqueResult:
    technique: str
    keywords: int = 0
    raw_ads: int = 0
    new_pages: int = 0
    vetoed: int = 0
    apps: int = 0
    funnels: int = 0
    web: int = 0
    samples: list = field(default_factory=list)


def body_hash(text: str | None) -> str:
    return hashlib.sha1(" ".join((text or "").lower().split()[:20]).encode()).hexdigest()[:12]


def days_ago(unix_ts) -> float | None:
    if not unix_ts:
        return None
    try:
        return round((time.time() - float(unix_ts)) / 86400.0, 1)
    except Exception:  # noqa: BLE001
        return None


def funnel_signal(ad, keyword_layer: str) -> bool:
    if keyword_layer == "funnel_phrase":
        return True
    host = host_of(ad.link_url)
    if any(h in host for h in FUNNEL_HOST_HINTS):
        return True
    blob = ((ad.body_text or "") + " " + (ad.link_url or "")).lower()
    if any(w in blob for w in FUNNEL_WORDS):
        return True
    return (ad.cta_type or "") in LEADGEN_CTAS


def consider_ad(ad, technique: str, layer: str, keyword: str,
                candidates: dict, result: TechniqueResult) -> None:
    """Fold one AdRecord into the candidate pool (veto, classify, dedup)."""
    result.raw_ads += 1
    pid = ad.page_id
    if not pid or not ad.link_url:
        return
    likes = ad.page_like_count
    if likes is not None and likes > VETO_LIKES:
        result.vetoed += 1
        return
    intent = classify_url(ad.link_url)
    if intent.kind in ("ios", "android"):
        kind = "app"
    elif intent.kind == "web":
        if host_of(ad.link_url) in SOCIAL_HOSTS:
            return            # a social page / ad redirect — neither app nor funnel
        kind = "funnel" if funnel_signal(ad, layer) else "web"
    else:
        return

    existing = candidates.get(pid)
    if existing:
        existing.n_ads_seen += 1
        existing.creative_hashes.add(body_hash(ad.body_text))
        if not (existing.image_url or existing.video_url) and (ad.image_urls or ad.video_urls):
            existing.image_url = ad.image_urls[0] if ad.image_urls else None
            existing.video_url = ad.video_urls[0] if ad.video_urls else None
            existing.cta_type = existing.cta_type or ad.cta_type
        age = days_ago(ad.start_date)
        if age is not None and (existing.start_age_days is None or age < existing.start_age_days):
            existing.start_age_days = age
        # an app classification (store link) outranks a generic web tag
        if kind == "app" and existing.kind != "app":
            existing.kind, existing.store_kind, existing.link_url = "app", intent.kind, ad.link_url
        return

    cand = Candidate(
        page_id=pid, page_name=ad.page_name or "(unknown)", page_like_count=likes,
        kind=kind, store_kind=intent.kind, cta_type=ad.cta_type, link_url=ad.link_url,
        body_sample=(ad.body_text or "")[:240] or None,
        image_url=ad.image_urls[0] if ad.image_urls else None,
        video_url=ad.video_urls[0] if ad.video_urls else None,
        source=technique, keyword=keyword, start_age_days=days_ago(ad.start_date),
        creative_hashes={body_hash(ad.body_text)},
    )
    candidates[pid] = cand
    result.new_pages += 1
    result.samples = (result.samples + [cand.page_name])[:5]
    if kind == "app":
        result.apps += 1
    elif kind == "funnel":
        result.funnels += 1
    else:
        result.web += 1


def search_lane(ext, layer: str, technique: str, candidates: dict,
                all_results: list) -> TechniqueResult:
    res = TechniqueResult(technique=technique)
    all_results.append(res)
    entries = taxonomy.all_entries(layer=layer)
    if MAX_KW:
        entries = entries[:MAX_KW]
    for entry in entries:
        try:
            ads = asyncio.run(ext.search(query=entry.keyword, countries=COUNTRIES,
                                         sort_mode=None, search_type=entry.search_type,
                                         max_pages=entry.max_pages))
        except ExtractError as exc:
            print(f"   [{technique}] '{entry.keyword}' -> {exc}; skipping")
            time.sleep(INTER_KW_DELAY * 2)
            continue
        res.keywords += 1
        for ad in ads:
            consider_ad(ad, technique, layer, entry.keyword, candidates, res)
        print(f"   [{technique}] '{entry.keyword}': {len(ads)} ads | "
              f"total candidates={len(candidates)}")
        flush(candidates, all_results)
        time.sleep(INTER_KW_DELAY)
    return res


def typeahead_lane(ext, candidates: dict, all_results: list) -> TechniqueResult:
    """Typeahead keyword->pages with like-veto, then a bounded PAGE pull for creative."""
    res = TechniqueResult(technique="TA")
    all_results.append(res)
    keywords = [e.keyword for e in taxonomy.all_entries(layer="funnel_phrase")] \
        + [e.keyword for e in taxonomy.all_entries(layer="niche_term")]
    if MAX_KW:
        keywords = keywords[:MAX_KW * 2]
    pending: list[tuple[str, str, int, str]] = []   # (page_id, name, likes, keyword)
    seen_pages = set()
    for kw in keywords:
        try:
            pages = asyncio.run(ext.typeahead(query=kw, country=COUNTRIES[0]))
        except ExtractError as exc:
            print(f"   [TA] '{kw}' -> {exc}; skipping")
            time.sleep(INTER_KW_DELAY * 2)
            continue
        res.keywords += 1
        for p in pages:
            pid, likes = p["page_id"], p.get("page_like_count")
            if not pid or pid in seen_pages or pid in candidates:
                continue
            if likes is not None and likes > VETO_LIKES:
                res.vetoed += 1
                continue
            seen_pages.add(pid)
            pending.append((pid, p.get("page_name") or "(unknown)", likes or 0, kw))
        print(f"   [TA] '{kw}': {len(pages)} pages | pending new={len(pending)}")
        time.sleep(INTER_KW_DELAY)

    # Bounded follow-up: pull a couple ads per new page so it has a reviewable creative.
    pending.sort(key=lambda t: t[2])    # smallest pages first (most likely emerging)
    for pid, name, likes, kw in pending[:TA_PAGE_PULL]:
        try:
            ads = asyncio.run(ext.search(query="", countries=COUNTRIES, search_type="page",
                                         view_all_page_id=pid, max_pages=1))
        except ExtractError as exc:
            print(f"   [TA-pull] {name} -> {exc}; recording without creative")
            ads = []
        for ad in ads:        # skip pages with no active ads (no reviewable creative)
            consider_ad(ad, "TA", "typeahead", kw, candidates, res)
        flush(candidates, all_results)
        time.sleep(INTER_KW_DELAY)
    return res


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #
def render_html(candidates: dict, results: list, path: str) -> None:
    cands = list(candidates.values())
    rank = {"app": 0, "funnel": 1, "web": 2}
    cands.sort(key=lambda c: (rank.get(c.kind, 3), (c.page_like_count or 1_000_000_000)))

    def esc(x):
        return html.escape(str(x)) if x is not None else ""

    cards = []
    for c in cands:
        if c.video_url:
            media = f'<video src="{esc(c.video_url)}" muted loop playsinline></video>'
        elif c.image_url:
            media = f'<img src="{esc(c.image_url)}" loading="lazy">'
        else:
            media = '<div class="noimg">no creative</div>'
        link = f'<a href="{esc(c.link_url)}" target="_blank" rel="noopener">{esc((c.link_url or "")[:60])}</a>' \
            if c.link_url else '<span class="muted">no link</span>'
        badge = {"app": "APP", "funnel": "FUNNEL", "web": "web"}.get(c.kind, c.kind)
        cards.append(f"""
        <div class="card {esc(c.kind)}">
          <div class="media">{media}</div>
          <div class="meta">
            <div class="name">{esc(c.page_name)} <span class="badge {esc(c.kind)}">{badge}</span></div>
            <div class="row"><b>{esc(c.page_like_count)}</b> likes · {esc(c.cta_type or '')}
                 · age {esc(c.start_age_days)}d · {esc(c.n_ads_seen)} ads / {c.distinct_creatives} creatives</div>
            <div class="row link">{link}</div>
            <div class="body">{esc(c.body_sample or '')}</div>
            <div class="src">{esc(c.source)} · "{esc(c.keyword)}" · page {esc(c.page_id)}</div>
          </div>
        </div>""")

    rows = "".join(
        f"<tr><td>{esc(r.technique)}</td><td>{r.keywords}</td><td>{r.raw_ads}</td>"
        f"<td>{r.new_pages}</td><td>{r.vetoed}</td><td>{r.apps}</td><td>{r.funnels}</td>"
        f"<td>{r.web}</td><td>{esc(', '.join(r.samples))}</td></tr>"
        for r in results
    )
    n_app = sum(1 for c in cands if c.kind == "app")
    n_funnel = sum(1 for c in cands if c.kind == "funnel")
    n_web = sum(1 for c in cands if c.kind == "web")
    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<title>AGF candidates {date.today()}</title><style>
body{{font:14px/1.4 -apple-system,system-ui,sans-serif;margin:0;background:#0e0f12;color:#e8e8ea}}
header{{padding:16px 20px;background:#16181d;position:sticky;top:0;z-index:2;border-bottom:1px solid #2a2d34}}
h1{{font-size:16px;margin:0 0 6px}} .sum{{color:#9aa0aa;font-size:13px}}
table{{border-collapse:collapse;margin:10px 0;font-size:12px}} td,th{{border:1px solid #2a2d34;padding:3px 8px;text-align:left}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px;padding:16px}}
.card{{background:#16181d;border:1px solid #2a2d34;border-radius:10px;overflow:hidden}}
.card.app{{border-color:#2f6f4f}} .card.funnel{{border-color:#6a5acd}}
.media{{height:170px;background:#0a0b0d;display:flex;align-items:center;justify-content:center;overflow:hidden}}
.media img,.media video{{width:100%;height:100%;object-fit:cover}}
.noimg{{color:#555}} .meta{{padding:10px}} .name{{font-weight:600;margin-bottom:4px}}
.badge{{font-size:10px;padding:1px 6px;border-radius:6px;background:#2a2d34;margin-left:4px}}
.badge.app{{background:#2f6f4f}} .badge.funnel{{background:#6a5acd}}
.row{{color:#9aa0aa;font-size:12px;margin:2px 0}} .body{{color:#c4c8d0;font-size:12px;margin:6px 0;max-height:54px;overflow:hidden}}
.src{{color:#6b7280;font-size:11px;margin-top:6px}} a{{color:#7db1ff}} .muted{{color:#555}}
</style></head><body>
<header><h1>AGF emerging candidates — {len(cands)} total</h1>
<div class="sum">apps {n_app} · funnels {n_funnel} · web {n_web} · veto&gt;{VETO_LIKES:,} likes · countries {",".join(COUNTRIES)}</div>
<table><tr><th>technique</th><th>kw</th><th>raw ads</th><th>new pages</th><th>vetoed</th>
<th>apps</th><th>funnels</th><th>web</th><th>sample</th></tr>{rows}</table></header>
<div class="grid">{''.join(cards)}</div></body></html>"""
    with open(path, "w") as fh:
        fh.write(doc)


def dump_json(candidates: dict, path: str) -> None:
    rows = []
    for c in candidates.values():
        d = {k: v for k, v in c.__dict__.items() if k != "creative_hashes"}
        d["distinct_creatives"] = c.distinct_creatives
        rows.append(d)
    with open(path, "w") as fh:
        json.dump(rows, fh, indent=2, default=str)


def flush(candidates: dict, results: list) -> None:
    """Write the review HTML + JSON to disk so a mid-run throttle-kill keeps partial output."""
    render_html(candidates, results, HTML_PATH)
    dump_json(candidates, JSON_PATH)


def main():
    if not os.environ.get("AGF_PROXIES"):
        print("ERROR: AGF_PROXIES not set — a residential proxy is required.", file=sys.stderr)
        sys.exit(2)
    ext = Extractor(search_delay=2.0, search_jitter=1.5)
    print("Warming session ...")
    for attempt in range(6):
        try:
            ext._pool.invalidate()
            ext._pool.acquire()
            break
        except Exception as exc:  # noqa: BLE001
            print(f"  warm attempt {attempt} failed: {exc}; retry on fresh IP")
            time.sleep(2.0)

    candidates: dict[str, Candidate] = {}
    results: list[TechniqueResult] = []
    lanes = [
        ("S-funnel", "funnel_phrase"),
        ("S-niche", "niche_term"),
        ("S-store", "store_fragment"),
    ]
    for technique, layer in lanes:
        print(f"\n=== lane {technique} ({layer}) ===")
        search_lane(ext, layer, technique, candidates, results)
        print(f"  -> running total candidates: {len(candidates)}")

    if len(candidates) < TARGET:
        print(f"\n=== lane TA (typeahead) — under target ({len(candidates)}/{TARGET}) ===")
        typeahead_lane(ext, candidates, results)

    html_path, json_path = HTML_PATH, JSON_PATH
    flush(candidates, results)

    print("\n================ SUMMARY ================")
    print(f"{'technique':10} {'kw':>3} {'rawAds':>7} {'newPg':>6} {'veto':>5} "
          f"{'app':>4} {'funnel':>7} {'web':>4}")
    for r in results:
        print(f"{r.technique:10} {r.keywords:>3} {r.raw_ads:>7} {r.new_pages:>6} {r.vetoed:>5} "
              f"{r.apps:>4} {r.funnels:>7} {r.web:>4}")
    n_app = sum(1 for c in candidates.values() if c.kind == "app")
    n_funnel = sum(1 for c in candidates.values() if c.kind == "funnel")
    print(f"\nTOTAL unique candidates: {len(candidates)} "
          f"(apps {n_app}, funnels {n_funnel}, web {len(candidates) - n_app - n_funnel})")
    print(f"Review HTML : {html_path}")
    print(f"JSON dump   : {json_path}")
    if len(candidates) >= TARGET:
        print(f"TARGET MET ✓ ({len(candidates)} >= {TARGET})")
    else:
        print(f"under target ({len(candidates)} < {TARGET}) — widen taxonomy or raise AGF_MAX_KW")


if __name__ == "__main__":
    main()
