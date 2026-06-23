"""Bridge confirmed web-sub funnels into the advertiser pipeline.

A discovery.funnel_candidates row with payoff_kind='web_sub', status='confirmed'
has a page_id but no advertiser/ads. For each such page not yet an advertiser:
create the advertiser, fetch + store its web identity (OG/favicon), then scrape
its Meta ad corpus by page_id (refresh_one_advertiser). Downstream enrich/score/
matview then carry it to the feed. Bounded per cycle; idempotent."""
import sys
from types import SimpleNamespace

from sqlalchemy import text

from agf.db import repo
from agf.db.engine import begin
from agf.discovery.keeper_history import refresh_one_advertiser
from agf.discovery.web_identity import parse_web_identity

_PENDING = text("""
    SELECT DISTINCT ON (fc.page_id)
           fc.page_id, fc.page_name, fc.page_like_count, fc.landing_url
    FROM discovery.funnel_candidates fc
    WHERE fc.payoff_kind = 'web_sub' AND fc.status = 'confirmed'
      AND NOT EXISTS (SELECT 1 FROM public.advertisers a WHERE a.page_id = fc.page_id)
    ORDER BY fc.page_id, fc.page_like_count ASC NULLS FIRST, fc.first_seen_at
""")


def pending_websub_pages(conn, limit):
    rows = conn.execute(_PENDING).all()
    # emerging-first overall ordering, then cap
    rows = sorted(rows, key=lambda r: (r.page_like_count is not None,
                                       r.page_like_count or 0))
    return rows[:limit]


async def run_websub_bridge(engine, extractor, *, fetch_text, limit=10) -> dict:
    with engine.connect() as conn:
        rows = pending_websub_pages(conn, limit)
    stats = {"examined": len(rows), "bridged": 0, "errors": 0}
    for r in rows:
        try:
            try:
                html = fetch_text(r.landing_url)
            except Exception as exc:
                print(f"[websub] landing fetch failed for {r.landing_url}: {exc!r}", file=sys.stderr, flush=True)
                html = ""
            ident = parse_web_identity(html, r.landing_url, fallback_name=r.page_name)
            with begin(engine) as conn:
                advertiser_id = repo.upsert_advertiser(conn, SimpleNamespace(
                    page_id=r.page_id, page_name=r.page_name,
                    page_like_count=r.page_like_count))
                repo.set_web_identity(conn, advertiser_id, payoff_kind="web_sub",
                                      name=ident.name, description=ident.description,
                                      icon_url=ident.icon_url, landing_url=r.landing_url)
            await refresh_one_advertiser(engine, extractor, advertiser_id, r.page_id)
            stats["bridged"] += 1
        except Exception:
            stats["errors"] += 1
    return stats
