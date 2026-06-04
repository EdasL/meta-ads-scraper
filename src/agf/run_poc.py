import csv
import sys
import time
import httpx
from agf.livesearch import bootstrap, search_us, itunes_lookup
from agf.classify import classify_url
from agf.enrich import parse_lookup, emerging_from_release
from agf.scale import aggregate_advertisers
from agf.score import gem_score_poc
from datetime import datetime, timezone


def run(queries: list[str], out_path: str = "gems.csv") -> int:
    now = datetime.now(timezone.utc)
    now_epoch = int(time.time())
    rows = []
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        lsd, doc_id = bootstrap(client)
        print(f"bootstrapped lsd={lsd[:6]}… doc_id={doc_id}", file=sys.stderr)
        for q in queries:
            ads = search_us(client, q, lsd, doc_id)
            print(f"  '{q}': {len(ads)} ads", file=sys.stderr)
            app_ads = [a for a in ads if classify_url(a.link_url).kind in ("ios", "android")]
            advertisers = {a.page_id: a for a in aggregate_advertisers(app_ads, now_epoch)}
            for ad in app_ads:
                intent = classify_url(ad.link_url)
                if intent.kind != "ios" or not intent.store_id:
                    continue
                meta = parse_lookup(itunes_lookup(client, intent.store_id))
                time.sleep(3)  # iTunes ~20 req/min
                if not meta:
                    continue
                emerging = emerging_from_release(meta.release_date, now)
                scale = advertisers[ad.page_id]
                score = gem_score_poc(scale.active_creative_count,
                                      scale.max_longevity_days, meta.rating_count, emerging)
                rows.append({
                    "score": score, "app": meta.name, "advertiser": ad.page_name,
                    "store_id": meta.track_id, "release_date": meta.release_date,
                    "rating_count": meta.rating_count,
                    "active_creatives": scale.active_creative_count,
                    "max_longevity_days": scale.max_longevity_days,
                    "emerging": emerging, "query": q,
                })
    # dedupe by store_id keeping highest score
    best = {}
    for r in rows:
        if r["store_id"] not in best or r["score"] > best[r["store_id"]]["score"]:
            best[r["store_id"]] = r
    ranked = sorted(best.values(), key=lambda r: r["score"], reverse=True)
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(ranked[0].keys()) if ranked else ["score"])
        w.writeheader()
        w.writerows(ranked)
    print(f"wrote {len(ranked)} apps to {out_path}", file=sys.stderr)
    return len(ranked)


if __name__ == "__main__":
    seeds = sys.argv[1:] or ["workout app", "calorie tracker", "habit tracker"]
    run(seeds)
