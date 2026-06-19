"""Decisive test of the classifier-fix hypothesis: do GENERIC funnel/intent terms +
DESTINATION RESOLUTION recover real app advertisers the current classifier DROPS?

For each generic funnel-intent keyword, sweep the Ad Library, then for every ad whose
link is NOT a direct store/social link, fetch the landing page (iOS UA, follow redirects)
and static-grep the final URL + HTML for a store link / MMP smart-banner (apps.apple.com,
play.google.com, af_ios_url, etc.). Count distinct advertisers recovered as apps, how many
are NEW, and what fraction of funnel/web destinations hide an app.

Read-only; bounded (SWEEP terms x RESOLVE_CAP landing fetches). Usage:
  AGF_DATABASE_URL=... AGF_PROXIES=... PYTHONPATH=src python scripts/probe_funnel_to_app.py
"""
import asyncio
import os
import re
from collections import defaultdict

import httpx
from sqlalchemy import text

from agf.classify import classify_url
from agf.db.engine import get_engine
from agf.discovery.sweep import SOCIAL_HOSTS, _host
from agf.extract.facade import Extractor

# Generic, cross-category funnel/intent phrases (the "breadth like funnels" net).
TERMS = [
    "take the quiz", "personalized plan", "answer a few questions",
    "based on your answers", "get your custom plan", "build your plan",
]
RESOLVE_CAP = int(os.environ.get("RESOLVE_CAP", "12"))   # landing fetches per term

_IOS_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
           "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")
_IOS_RE = re.compile(r"(?:apps|itunes)\.apple\.com/[^\s\"'<>\\]*?/id(\d+)")
_AND_RE = re.compile(r"play\.google\.com/store/apps/details\?[^\s\"'<>\\]*?id=([A-Za-z0-9_.]+)")
_MMP_PARAM = re.compile(r"(?:af_ios_url|af_android_url|\$ios_url|\$android_url|"
                        r"af_dp|deep_link_value)=([^&\s\"'<>]+)", re.I)


def store_from(text_blob: str):
    """First store link found in a URL or HTML blob -> ('ios'|'android', id) or None."""
    m = _IOS_RE.search(text_blob)
    if m:
        return ("ios", m.group(1))
    m = _AND_RE.search(text_blob)
    if m:
        return ("android", m.group(1))
    return None


def resolve_landing(client, url: str):
    """Fetch the landing (iOS UA, follow redirects) and static-grep final URL + HTML for a
    store link. Returns ('ios'|'android', id) or None. Network-bounded, best-effort."""
    hit = store_from(url)                                 # MMP param embedded in the ad URL
    if hit:
        return hit
    try:
        r = client.get(url, headers={"User-Agent": _IOS_UA}, timeout=12)
    except Exception:
        return None
    return store_from(str(r.url)) or store_from(r.text[:400_000])


def main():
    eng = get_engine()
    with eng.connect() as conn:
        known = {r[0] for r in conn.execute(text(
            "SELECT page_id::text FROM discovery.candidates "
            "UNION SELECT page_id::text FROM public.advertisers")).all()}
    ext = Extractor()
    client = httpx.Client(follow_redirects=True)

    tot = defaultdict(int)
    recovered_pids, direct_pids = set(), set()
    for term in TERMS:
        try:
            ads = asyncio.run(ext.search(query=term, countries=["US"],
                                         search_type="KEYWORD_UNORDERED", max_pages=2))
        except Exception as exc:
            print(f"[{term!r}] sweep ERROR {exc!r}")
            continue
        direct = funnel_app = web_only = social = resolved = 0
        tried = 0
        for ad in ads:
            link = ad.link_url
            pid = str(ad.page_id or "")
            if not link or not pid:
                continue
            intent = classify_url(link)
            if intent.kind in ("ios", "android"):
                direct += 1
                direct_pids.add(pid)
                continue
            if _host(link) in SOCIAL_HOSTS:
                social += 1
                continue
            # web / funnel / MMP destination — the bucket we currently DROP
            if tried >= RESOLVE_CAP:
                continue
            tried += 1
            hit = resolve_landing(client, link)
            if hit:
                funnel_app += 1
                resolved += 1
                recovered_pids.add(pid)
            else:
                web_only += 1
        print(f"[{term!r:<26}] ads={len(ads):>3} direct_store={direct:>2} "
              f"resolved_from_funnel={funnel_app:>2} web_only={web_only:>2} "
              f"social={social:>2} (tried {tried} landings)")
        tot["direct"] += direct
        tot["funnel_app"] += funnel_app
        tot["web_only"] += web_only

    new_recovered = recovered_pids - known - direct_pids
    print("\n==== TOTALS ====")
    print(f"direct store-link app ads:           {tot['direct']}")
    print(f"apps RECOVERED from funnel/MMP:      {tot['funnel_app']}  "
          f"(of {tot['funnel_app'] + tot['web_only']} non-store destinations tried)")
    print(f"genuine web-only (no app behind):    {tot['web_only']}")
    print(f"distinct advertisers recovered:      {len(recovered_pids)}")
    print(f"  of which NEW (not in DB, not direct): {len(new_recovered)}")
    client.close()


if __name__ == "__main__":
    main()
