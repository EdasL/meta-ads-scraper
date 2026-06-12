"""Stage 2 of the FUNNEL pipeline — confirm + label each new funnel candidate.
Renders the landing page, fingerprints the funnel tech (firehose_fingerprint), resolves the
deeplink (MMP->store), and writes a verdict: install | subscribe | unknown (confirmed) or
dropped (generic web, no funnel tech). v1 renders the landing page only.

The orchestrator is SYNC and takes injectable `resolve(url)->obj{kind,store_id}` and
`render(url)->html` callables — the runnable script wires the async Extractor.resolve_deeplink
(via asyncio.run) and the sync Playwright render. This keeps the orchestrator network-free and
testable with fakes."""
from sqlalchemy import text

from agf.db.engine import begin
from agf.db.funnel_candidates import mark_funnel_confirmed
from agf.discovery.firehose_fingerprint import detect_signatures


def classify_funnel(html, resolved, funnel_host=None):
    """(kind, status, store, store_id, drop_reason) for one funnel candidate.
    resolved: obj with .kind ('ios'|'android'|'web'|'none') and .store_id."""
    sigs = detect_signatures(html or "")
    if resolved is not None and resolved.kind in ("ios", "android") and resolved.store_id:
        return ("install", "confirmed", resolved.kind, resolved.store_id, None)
    if "store" in sigs or "mmp" in sigs:
        return ("install", "confirmed", None, None, None)   # store/app handoff present, unresolved
    if "checkout" in sigs:
        return ("subscribe", "confirmed", None, None, None)
    if "pixel" in sigs or funnel_host:
        return ("unknown", "confirmed", None, None, None)    # a paid funnel, payoff unclear
    return ("unknown", "dropped", None, None, "no_funnel_tech")


def _new_rows(conn, ids):
    rows = conn.execute(text(
        "SELECT id, landing_url, landing_domain, funnel_host FROM discovery.funnel_candidates "
        "WHERE status='new' ORDER BY first_seen_at, id")).all()
    if ids is not None:                      # filter in Python — driver-agnostic, avoids ANY/IN binding
        keep = set(ids)
        rows = [r for r in rows if r.id in keep]
    return rows


def run_funnel_confirm(engine, *, resolve, render, ids=None, limit=None) -> dict:
    """Confirm every 'new' funnel candidate (optionally restricted to `ids`). Commits per row.
    resolve(landing_url) -> obj{kind,store_id}; render(landing_url) -> html str."""
    with engine.connect() as conn:
        rows = _new_rows(conn, ids)
    if limit:
        rows = rows[:limit]
    stats = {"examined": len(rows), "confirmed": 0, "dropped": 0,
             "kinds": {"install": 0, "subscribe": 0, "unknown": 0}}
    for r in rows:
        try:
            resolved = resolve(r.landing_url)
        except Exception:
            resolved = None
        try:
            html = render(r.landing_url)
        except Exception:
            html = ""
        kind, status, store, store_id, reason = classify_funnel(html, resolved, r.funnel_host)
        with begin(engine) as conn:
            mark_funnel_confirmed(conn, r.id, kind=kind, status=status,
                                  store=store, store_id=store_id, drop_reason=reason)
        if status == "confirmed":
            stats["confirmed"] += 1
            stats["kinds"][kind] += 1
        else:
            stats["dropped"] += 1
    return stats
