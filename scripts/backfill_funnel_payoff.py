"""Re-classify payoff_kind for existing confirmed funnels and bridge app-backed ones.

Skips rows where payoff_kind IS NOT NULL (idempotent / resumable). Re-renders each
landing page with an iOS UA, applies classify_payoff + store_ref_from_html, writes
payoff_kind (and store/store_id when found), then upserts a discovery.candidates row
for funnels classified as 'app'. Physical/service/leadgen are tagged only — not bridged.

Usage:
    AGF_DATABASE_URL=... PYTHONPATH=src python scripts/backfill_funnel_payoff.py [--limit N]

Smoke-test with --limit 2 before running against all 260.
"""
import argparse
import sys
import time

from sqlalchemy import text

from agf.db.candidates import upsert_sweep_candidate
from agf.db.engine import begin, get_engine
from agf.discovery.firehose_fingerprint import classify_payoff
from agf.discovery.funnel_classify import store_ref_from_html
from agf.discovery.funnel_confirm import build_funnel_bridge_row


def _fetch_unclassified(conn, limit):
    q = text("""
        SELECT id, landing_url, page_id, page_name, page_like_count, source_keyword,
               store, store_id
        FROM discovery.funnel_candidates
        WHERE status = 'confirmed' AND payoff_kind IS NULL
        ORDER BY confirmed_at, id
        LIMIT :lim
    """)
    return conn.execute(q, {"lim": limit}).all()


def _write_payoff(conn, candidate_id, *, payoff_kind, store, store_id):
    conn.execute(text("""
        UPDATE discovery.funnel_candidates
           SET payoff_kind = :pk,
               store       = COALESCE(:st, store),
               store_id    = COALESCE(:sid, store_id)
         WHERE id = :i
    """), {"pk": payoff_kind, "st": store, "sid": store_id, "i": candidate_id})


def run(limit: int, dry_run: bool = False):
    from agf.enrich_full.funnel_capture import render_landing_html

    engine = get_engine()
    with engine.connect() as conn:
        rows = _fetch_unclassified(conn, limit)

    print(f"backfill: {len(rows)} confirmed funnels to classify", flush=True)
    counts = {"app": 0, "web_sub": 0, "physical": 0, "service": 0, "leadgen": 0,
              "error": 0, "bridged_new": 0, "bridged_seen": 0}

    for r in rows:
        try:
            html = render_landing_html(r.landing_url)
        except Exception as exc:
            print(f"  RENDER ERROR {r.landing_url}: {exc}", flush=True)
            counts["error"] += 1
            continue

        dom_ref = store_ref_from_html(html)
        store = dom_ref[0] if dom_ref else r.store
        store_id = dom_ref[1] if dom_ref else r.store_id
        payoff_kind = classify_payoff(html, store_id=store_id)
        counts[payoff_kind] = counts.get(payoff_kind, 0) + 1

        print(f"  {r.page_id} → {payoff_kind}"
              + (f" store_id={store_id}" if store_id else ""), flush=True)

        if dry_run:
            continue

        with begin(engine) as conn:
            _write_payoff(conn, r.id, payoff_kind=payoff_kind, store=store, store_id=store_id)
            if payoff_kind == "app" and store_id:
                bridge_row = build_funnel_bridge_row(r, store=store, store_id=store_id)
                _, was_new = upsert_sweep_candidate(conn, bridge_row)
                counts["bridged_new" if was_new else "bridged_seen"] += 1

        time.sleep(1)  # gentle pacing — landing pages, not Meta

    print(f"\nDone: {counts}", flush=True)
    return counts


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=260, help="max rows to process (default 260)")
    ap.add_argument("--dry-run", action="store_true", help="render + classify but don't write")
    args = ap.parse_args()
    run(args.limit, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
