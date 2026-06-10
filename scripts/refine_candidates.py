"""Re-render a cleaned candidate review from an existing discover_experiment JSON dump.

Applies the improved classifier (drop social/redirect-host noise, reclassify app-subdomain
web2app/quiz funnels web->funnel) WITHOUT re-scraping, so a sweep's raw output can be polished
for human review. Reuses discover_experiment's Candidate + render_html (single source of truth).

Run: PYTHONPATH=src .venv/bin/python scripts/refine_candidates.py /tmp/agf-candidates-<date>.json
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root
from scripts.discover_experiment import (Candidate, render_html, host_of,           # noqa: E402
                                         SOCIAL_HOSTS, FUNNEL_HOST_HINTS, FUNNEL_WORDS)


def refine(in_json: str) -> tuple[str, dict]:
    rows = json.loads(open(in_json).read())
    out: dict[str, Candidate] = {}
    dropped = reclassified = 0
    for r in rows:
        h = host_of(r.get("link_url"))
        if r["kind"] != "app" and h in SOCIAL_HOSTS:
            dropped += 1
            continue
        kind = r["kind"]
        if kind == "web":
            blob = ((r.get("body_sample") or "") + " " + (r.get("link_url") or "")).lower()
            if any(x in h for x in FUNNEL_HOST_HINTS) or any(w in blob for w in FUNNEL_WORDS):
                kind, reclassified = "funnel", reclassified + 1
        dc = int(r.get("distinct_creatives") or 0)
        c = Candidate(
            page_id=r["page_id"], page_name=r["page_name"], page_like_count=r["page_like_count"],
            kind=kind, store_kind=r["store_kind"], cta_type=r["cta_type"], link_url=r["link_url"],
            body_sample=r["body_sample"], image_url=r["image_url"], video_url=r["video_url"],
            source=r["source"], keyword=r["keyword"], start_age_days=r["start_age_days"],
            n_ads_seen=int(r.get("n_ads_seen") or 1), creative_hashes=set(range(dc)),
        )
        out[c.page_id] = c
    out_html = in_json.replace(".json", "-clean.html")
    render_html(out, [], out_html)
    stats = {"in": len(rows), "out": len(out), "dropped_social": dropped,
             "reclassified_web_to_funnel": reclassified,
             "apps": sum(1 for c in out.values() if c.kind == "app"),
             "funnels": sum(1 for c in out.values() if c.kind == "funnel"),
             "web": sum(1 for c in out.values() if c.kind == "web")}
    return out_html, stats


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/agf-candidates.json"
    html_path, stats = refine(path)
    print("cleaned:", json.dumps(stats, indent=2))
    print("HTML:", html_path)
