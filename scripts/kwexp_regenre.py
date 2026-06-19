"""Re-resolve missing genres on a saved keyword_seed_experiment scorecard and
recompute the diversity metrics — OFFLINE (iTunes + node sidecar only, no Meta /
no proxy). Use when a bake-off's genre_coverage came back low because the android
sidecar timed out mid-run. Small chunks so the sidecar never times out.

    PYTHONPATH=src .venv/bin/python scripts/kwexp_regenre.py /tmp/agf-kwexp-<tech>-<date>.json
"""
import json
import math
import sys
from collections import Counter

import httpx

from agf.enrich_full.appstore import lookup_google_play, lookup_itunes_batch

path = sys.argv[1]
d = json.load(open(path))
apps = d["apps"]

missing = [a for a in apps if not a.get("genre")]
ios = [a["store_id"] for a in missing if a["store"] == "ios"]
android = [a["store_id"] for a in missing if a["store"] == "android"]
print(f"re-resolving {len(missing)} missing ({len(ios)} ios, {len(android)} android)", flush=True)

found = {}
if ios:
    with httpx.Client(timeout=20, follow_redirects=True) as client:
        for tid, m in lookup_itunes_batch(client, ios, pause=2.0).items():
            found[("ios", str(tid))] = getattr(m, "genre", None)
for j in range(0, len(android), 5):
    chunk = android[j:j + 5]
    try:
        for pid, m in lookup_google_play(chunk).items():
            found[("android", str(pid))] = getattr(m, "genre", None)
    except Exception as exc:
        print(f"  chunk {j//5} skipped: {exc}", flush=True)
    if j and j % 50 == 0:
        print(f"  ...{j}/{len(android)} android resolved", flush=True)

for a in apps:
    if not a.get("genre"):
        a["genre"] = found.get((a["store"], a["store_id"]))

genres = Counter(a["genre"] for a in apps if a["genre"])
tot = sum(genres.values())
H = -sum((n / tot) * math.log2(n / tot) for n in genres.values()) if tot else 0.0
Hmax = math.log2(len(genres)) if len(genres) > 1 else 1.0
d["genre_coverage"] = round(tot / max(1, len(apps)), 3)
d["n_genres"] = len(genres)
d["top_genre"] = genres.most_common(1)[0] if genres else None
d["top_genre_share"] = round(genres.most_common(1)[0][1] / tot, 3) if tot else None
d["top3_share"] = round(sum(n for _, n in genres.most_common(3)) / tot, 3) if tot else None
d["evenness"] = round(H / Hmax, 3) if Hmax else None
d["genre_hist"] = genres.most_common()
json.dump(d, open(path, "w"), indent=2)

print(f"\n=== RE-RESOLVED SCORECARD ({path}) ===", flush=True)
for k in ("distinct_apps", "genre_coverage", "yield_per_kw", "dead_kw_pct", "novelty",
          "n_genres", "top_genre", "top_genre_share", "top3_share", "evenness"):
    print(f"  {k:18s} {d[k]}", flush=True)
print(f"  genre_hist: {d['genre_hist']}", flush=True)
