"""Size the DIGITAL-APP reserve hiding in our confirmed-funnel inventory, using REAL
rendering (static grep misses JS-injected store links). Renders each funnel landing with
the existing iOS-UA settled renderer, greps the settled DOM for an app-store link, and
splits digital-app (keep) vs physical/booking (exclude, per user scope) vs web-other.

Read-only; renders DIRECT (no proxy). Usage:
  AGF_DATABASE_URL=... PYTHONPATH=src python scripts/probe_funnel_render_reserve.py
"""
import os
import re

from sqlalchemy import text

from agf.db.engine import get_engine
from agf.enrich_full.funnel_capture import render_landing_html

N = int(os.environ.get("N", "25"))
IOS = re.compile(r"(?:apps|itunes)\.apple\.com/[^\s\"'<>\\]*?/id(\d+)")
AND = re.compile(r"play\.google\.com/store/apps/details\?[^\s\"'<>\\]*?id=([A-Za-z0-9_.]+)")
PHYS = re.compile(r"cdn\.shopify|myshopify|calendly\.com|acuityscheduling|\bas\.me\b|"
                  r"squareup|/cart|add[-_]to[-_]cart|/products?/", re.I)


def store_of(blob):
    m = IOS.search(blob)
    if m:
        return ("ios", m.group(1))
    m = AND.search(blob)
    return ("android", m.group(1)) if m else None


def main():
    eng = get_engine()
    with eng.connect() as c:
        known = {r[0] for r in c.execute(text(
            "SELECT page_id::text FROM public.advertisers "
            "UNION SELECT page_id::text FROM discovery.candidates WHERE store IS NOT NULL")).all()}
        rows = c.execute(text(
            "SELECT page_id, page_name, kind, landing_url FROM discovery.funnel_candidates "
            "WHERE status='confirmed' AND NULLIF(landing_url,'') IS NOT NULL "
            "ORDER BY first_seen_at DESC LIMIT :n"), {"n": N}).all()

    digital_new, digital_have, physical, web_other, errs = set(), set(), 0, 0, 0
    for pid, name, kind, url in rows:
        try:
            html = render_landing_html(url, settle_ms=2500, timeout_ms=25_000)
        except Exception as exc:
            errs += 1
            print(f"  ERROR   [{(name or '')[:20]:<20}] {type(exc).__name__}")
            continue
        blob = str(url) + html
        hit = store_of(blob)
        if hit:
            (digital_new if str(pid) not in known else digital_have).add(str(pid))
            tag = f"DIGITAL {hit[0]}:{hit[1]} " + ("NEW" if str(pid) not in known else "have")
        elif PHYS.search(blob):
            physical += 1
            tag = "physical/booking"
        else:
            web_other += 1
            tag = "web-other"
        print(f"  {tag:<22} [{(name or '')[:20]:<20}] {str(url)[:55]}")

    n = len(rows)
    print(f"\n==== rendered {n - errs}/{n} confirmed funnels ====")
    print(f"DIGITAL-APP new (recoverable, not in app DB): {len(digital_new)}")
    print(f"DIGITAL-APP already have:                     {len(digital_have)}")
    print(f"physical/booking (EXCLUDE per scope):         {physical}")
    print(f"web-other (no store link even rendered):      {web_other}")
    print(f"render errors:                                {errs}")
    if n - errs:
        print(f"\n=> digital-app rate among confirmed funnels: "
              f"{round(100*(len(digital_new)+len(digital_have))/(n-errs))}%  "
              f"| extrapolated NEW across 260 confirmed: "
              f"~{round(260*len(digital_new)/(n-errs))}")


if __name__ == "__main__":
    main()
