"""Stage 2 of the FUNNEL pipeline — confirm + label each new funnel candidate.
Renders the landing page, fingerprints the funnel tech (firehose_fingerprint), probes the
quiz (quiz_detect via the probe callable), resolves the deeplink (MMP->store), and writes
a verdict: install | subscribe | unknown (confirmed) or dropped. Confirmation requires
payoff evidence (store/MMP/checkout), a verified multi-step quiz (quiz_steps >= 2), or
pixel + a funnel-looking host — a pixel alone no longer confirms (half the web runs GTM).

The orchestrator is SYNC and takes injectable `resolve(url)->obj{kind,store_id}` and
`probe(url)->obj{landing_html,quiz_steps}` callables — the runnable script wires the async
Extractor.resolve_deeplink (via asyncio.run) and the sync Playwright render_and_probe.
This keeps the orchestrator network-free and testable with fakes."""
from urllib.parse import urlparse

from sqlalchemy import text

from agf.db.engine import begin
from agf.db.funnel_candidates import mark_funnel_confirmed
from agf.discovery.firehose_fingerprint import detect_signatures


def _has_funnel_path(url):
    """True when the URL points past the bare homepage (e.g. /quiz, /f/ab12).

    A pixel on a ROOT path ('' or '/') is just a tracked homepage — calorii.com/
    runs a Meta pixel but is not a funnel. A pixel on a deep path is paid traffic
    landing on a dedicated quiz/checkout page. None = caller didn't supply a URL,
    so fall back to the old behaviour (don't block the discovery pipeline)."""
    if url is None:
        return True
    try:
        return bool(urlparse(url).path.strip("/"))
    except Exception:
        return True


def classify_funnel(html, resolved, funnel_host=None, quiz_steps=None, funnel_url=None):
    """(kind, status, store, store_id, drop_reason) for one funnel candidate.
    resolved: obj with .kind ('ios'|'android'|'web'|'none') and .store_id.
    quiz_steps: distinct question screens the probe observed (None = not probed).
    funnel_url: the resolved landing URL — a bare-homepage path can't confirm on pixel."""
    sigs = detect_signatures(html or "")
    if resolved is not None and resolved.kind in ("ios", "android") and resolved.store_id:
        return ("install", "confirmed", resolved.kind, resolved.store_id, None)
    if "store" in sigs or "mmp" in sigs:
        return ("install", "confirmed", None, None, None)   # store/app handoff present, unresolved
    if "checkout" in sigs:
        return ("subscribe", "confirmed", None, None, None)
    if (quiz_steps or 0) >= 2:
        return ("unknown", "confirmed", None, None, None)    # verified quiz; payoff behind it
    if "pixel" in sigs and funnel_host and _has_funnel_path(funnel_url):
        return ("unknown", "confirmed", None, None, None)    # paid traffic to a funnel page
    return ("unknown", "dropped", None, None, "no_quiz_no_payoff")


def _new_rows(conn, ids):
    rows = conn.execute(text(
        "SELECT id, landing_url, landing_domain, funnel_host FROM discovery.funnel_candidates "
        "WHERE status='new' ORDER BY first_seen_at, id")).all()
    if ids is not None:                      # filter in Python — driver-agnostic, avoids ANY/IN binding
        keep = set(ids)
        rows = [r for r in rows if r.id in keep]
    return rows


def run_funnel_confirm(engine, *, resolve, probe, ids=None, limit=None) -> dict:
    """Confirm every 'new' funnel candidate (optionally restricted to `ids`). Commits per row.
    resolve(landing_url) -> obj{kind,store_id}; probe(landing_url) -> obj{landing_html,quiz_steps}."""
    with engine.connect() as conn:
        rows = _new_rows(conn, ids)
    if limit is not None:
        rows = rows[:limit]
    stats = {"examined": len(rows), "confirmed": 0, "dropped": 0,
             "kinds": {"install": 0, "subscribe": 0, "unknown": 0}}
    for r in rows:
        try:
            resolved = resolve(r.landing_url)
        except Exception:
            resolved = None
        try:
            probed = probe(r.landing_url)
            html, quiz_steps = probed.landing_html, probed.quiz_steps
        except Exception:
            html, quiz_steps = "", None
        kind, status, store, store_id, reason = classify_funnel(
            html, resolved, r.funnel_host, quiz_steps)
        with begin(engine) as conn:
            mark_funnel_confirmed(conn, r.id, kind=kind, status=status,
                                  store=store, store_id=store_id, drop_reason=reason,
                                  quiz_steps=quiz_steps)
        if status == "confirmed":
            stats["confirmed"] += 1
            stats["kinds"][kind] += 1
        else:
            stats["dropped"] += 1
    return stats
