"""Finalize the local feed to show ONLY this session's discovered gems, scored fairly.

1. Resolve the discovery advertiser ids (those whose page_id came from this run's candidate set).
2. Re-score JUST that cohort with the fixed scorer (score_all only_ids -> cohort percentiles are
   computed among the real discovered gems, so genuine scalers rank at the top) + recompute growth
   (fixed first-non-zero pct_change).
3. Remove everyone else (8 demo gems + old game/urgent-care/dessert noise) from the FEED by
   deleting their regenerable adtech.advertiser_scores rows — the matview INNER-JOINs scores, so
   they vanish. NON-destructive: advertisers/ads/creatives stay; re-running run_scoring restores
   them, and the demo seed is re-runnable via scripts/seed_demo.py.
4. Refresh the matview and print the resulting feed.

Run: AGF_DATABASE_URL=... PYTHONPATH=src .venv/bin/python scripts/finalize_feed.py [candidates.json]
"""
import json
import sys
from datetime import date

from sqlalchemy import text

from agf.db.engine import begin, get_engine
from agf.scoring.run_scoring import refresh_emerging_matview, score_all
from agf.tracker.growth import compute_advertiser_growth


def main():
    cand_path = next((a for a in sys.argv[1:] if a.endswith(".json")), None) \
        or f"/tmp/agf-candidates-{date.today().isoformat()}.json"
    page_ids = [r["page_id"] for r in json.loads(open(cand_path).read()) if r.get("page_id")]
    engine = get_engine()

    with begin(engine) as conn:
        ids = [r[0] for r in conn.execute(text(
            "SELECT id FROM public.advertisers WHERE page_id = ANY(:p)"), {"p": page_ids}).all()]
        if not ids:
            print("no discovery advertisers found in DB — nothing to finalize"); return
        score_all(conn, date.today(), only_ids=ids)
        compute_advertiser_growth(conn, only_ids=ids)
        removed = conn.execute(text(
            "DELETE FROM adtech.advertiser_scores WHERE advertiser_id <> ALL(:ids)"),
            {"ids": ids}).rowcount
    refresh_emerging_matview(engine)
    print(f"kept {len(ids)} discovery advertisers; removed {removed} demo/old score rows from feed")

    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT round(m.gem_score::numeric, 1) AS score, m.latest_count, m.pct_change,
                   m.weeks_observed, m.page_name,
                   EXISTS (SELECT 1 FROM public.creatives c JOIN public.ads a ON a.id=c.ad_id
                           WHERE a.advertiser_id=m.advertiser_id AND c.s3_key IS NOT NULL) AS has_creative
            FROM frontend.emerging_scaling_apps m
            ORDER BY m.gem_score DESC NULLS LAST
        """)).all()
    print(f"\n==== feed now has {len(rows)} gems (all real) ====")
    for r in rows:
        flag = "" if r.has_creative else "  (no creative — hidden from Discover feed)"
        print(f"  score={r.score} ads={r.latest_count} pct={r.pct_change} wk={r.weeks_observed} "
              f"{r.page_name}{flag}")


if __name__ == "__main__":
    main()
