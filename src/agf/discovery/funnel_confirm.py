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
from urllib.parse import urlparse, parse_qs, unquote

from sqlalchemy import text

from agf.classify import classify_url
from agf.db.candidates import upsert_sweep_candidate
from agf.db.engine import begin
from agf.db.funnel_candidates import mark_funnel_confirmed
from agf.discovery.firehose_fingerprint import detect_signatures, classify_payoff
from agf.discovery.funnel_classify import store_ref_from_html

# MMP / app-attribution deeplink hosts. These gate on a real device + real ad-click, so a
# headless browser only ERR_ABORTs on them — we recover the app WITHOUT rendering (see
# _resolve_mmp_app) rather than churning the probe on them forever.
_MMP_HOSTS = ("onelink.me", "onelink.to", "app.adjust.com", "adjust.com", "adj.st",
              "app.link", "bnc.lt", "branch.io", "go.link", "apple.co", "sng.link",
              "smart.link", "singular", "tlnk.io", "kochava", "lnk.to")
# Query params an MMP link uses to carry the destination store URL (fallback / deep-link).
_STORE_PARAM_KEYS = ("af_dp", "deep_link_value", "af_web_dp", "af_ios_url", "af_android_url",
                     "af_r", "redirect", "fallback", "url", "$ios_url", "$android_url",
                     "$fallback_url")
# After this many failed probe attempts a candidate is dropped 'probe_unreachable' so a
# permanently-dead URL stops re-selecting at the head of the oldest-first queue.
_DEFAULT_ATTEMPT_CAP = 4


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
    """(kind, status, store, store_id, drop_reason, payoff_kind) for one funnel candidate.
    resolved: obj with .kind ('ios'|'android'|'web'|'none') and .store_id.
    quiz_steps: distinct question screens the probe observed (None = not probed).
    funnel_url: the resolved landing URL — a bare-homepage path can't confirm on pixel."""
    sigs = detect_signatures(html or "")

    # Resolve store reference: deeplink resolver wins (genuine ad destination).
    # DOM extraction is a fallback ONLY when the page is not physical — a Shopify/cart
    # page may embed an App Store badge without being an app funnel.
    if resolved is not None and resolved.kind in ("ios", "android") and resolved.store_id:
        store, store_id = resolved.kind, resolved.store_id
    else:
        if "physical" not in sigs:
            dom_ref = store_ref_from_html(html)
            store, store_id = (dom_ref[0], dom_ref[1]) if dom_ref else (None, None)
        else:
            store, store_id = None, None

    payoff_kind = classify_payoff(html or "", store_id=store_id)

    if store_id:
        return ("install", "confirmed", store, store_id, None, payoff_kind)
    if "store" in sigs or "mmp" in sigs:
        return ("install", "confirmed", None, None, None, payoff_kind)
    if "checkout" in sigs:
        return ("subscribe", "confirmed", None, None, None, payoff_kind)
    if (quiz_steps or 0) >= 2:
        return ("unknown", "confirmed", None, None, None, payoff_kind)
    if "pixel" in sigs and funnel_host and _has_funnel_path(funnel_url):
        return ("unknown", "confirmed", None, None, None, payoff_kind)
    return ("unknown", "dropped", None, None, "no_quiz_no_payoff", payoff_kind)


def build_funnel_bridge_row(r, *, store, store_id, technique="funnel_confirm"):
    """Return a discovery.candidates upsert dict for a funnel candidate bridged to an app.
    `r` must have page_id, page_name, page_like_count, source_keyword, landing_url attrs."""
    return {
        "page_id": r.page_id,
        "page_name": r.page_name,
        "page_like_count": r.page_like_count,
        "store": store,
        "store_id": store_id,
        "link_url": r.landing_url,
        "cta_type": None,
        "ad_start_date": None,
        "source_keyword": r.source_keyword,
        "technique": technique,
    }


def _new_rows(conn, ids):
    rows = conn.execute(text(
        "SELECT id, landing_url, landing_domain, funnel_host, "
        "page_id, page_name, page_like_count, source_keyword, confirm_attempts "
        "FROM discovery.funnel_candidates "
        "WHERE status='new' ORDER BY first_seen_at, id")).all()
    if ids is not None:                      # filter in Python — driver-agnostic, avoids ANY/IN binding
        keep = set(ids)
        rows = [r for r in rows if r.id in keep]
    return rows


def _is_mmp(landing_domain, landing_url) -> bool:
    hay = f"{landing_domain or ''} {landing_url or ''}"
    return any(h in hay for h in _MMP_HOSTS)


def _store_from_mmp_params(landing_url):
    """An MMP link's store-fallback params often carry the destination store URL directly.
    Returns (store, store_id) or (None, None) — network-free."""
    try:
        qs = parse_qs(urlparse(landing_url or "").query)
    except Exception:
        return (None, None)
    for key in _STORE_PARAM_KEYS:
        for raw in qs.get(key, ()):
            intent = classify_url(unquote(raw))
            if intent.kind in ("ios", "android") and intent.store_id:
                return (intent.kind, intent.store_id)
    return (None, None)


def _store_from_page(conn, page_id):
    """If this advertiser's Meta page already maps to a known app candidate, reuse it — an
    MMP deeplink we can't render still belongs to that known app (its own app-store ad
    resolved earlier). Most reliable of the two recovery signals."""
    if not page_id:
        return (None, None)
    row = conn.execute(text(
        "SELECT store, store_id FROM discovery.candidates "
        "WHERE page_id = :p AND store IN ('ios','android') AND store_id IS NOT NULL "
        "ORDER BY first_seen_at, id LIMIT 1"), {"p": page_id}).first()
    return (row.store, row.store_id) if row else (None, None)


def _resolve_mmp_app(engine, r):
    """Cheap, network-free app recovery for an MMP deeplink: page->app map first, then the
    link's store-fallback params. Returns (store, store_id) or (None, None)."""
    with engine.connect() as conn:
        store, store_id = _store_from_page(conn, r.page_id)
    if store_id:
        return (store, store_id)
    return _store_from_mmp_params(r.landing_url)


def _register_probe_failure(engine, r, stats, attempt_cap) -> None:
    """A probe that couldn't reach/render the landing. Count the attempt; once it caps,
    DROP as 'probe_unreachable' so a permanently-dead URL (NXDOMAIN, 30s timeout, opaque
    MMP hop) stops re-selecting at the head of the oldest-first queue and blocking newer
    candidates. Below the cap the row stays 'new' and a healthy future run retries it —
    a single crash still proves nothing (the 06-26..07-06 misfiling incident)."""
    stats["probe_failed"] += 1
    attempts = (r.confirm_attempts or 0) + 1
    with begin(engine) as conn:
        if attempts >= attempt_cap:
            mark_funnel_confirmed(conn, r.id, kind="unknown", status="dropped",
                                  drop_reason="probe_unreachable")
            stats["dropped"] += 1
            stats["probe_capped"] += 1
        else:
            conn.execute(text("UPDATE discovery.funnel_candidates "
                              "SET confirm_attempts = :a WHERE id = :i"),
                         {"a": attempts, "i": r.id})


def _confirm_looks_stalled(stats) -> bool:
    """True when a confirm pass rendered almost nothing successfully — a signal the landing
    probe itself is broken (dead browser/proxy) rather than just a low-yield night. MMP
    page->app recoveries count as confirmed, so a page-map-only night doesn't false-alarm."""
    ex = stats.get("examined", 0)
    return (ex >= 10 and stats.get("confirmed", 0) == 0
            and stats.get("probe_failed", 0) >= int(0.9 * ex))


def funnel_confirm_due(conn, *, ttl_hours: int = 6, limit: int = 200) -> list[int]:
    """Candidate ids status='new' minus those with a logging.crawl_log row of
    entity_type='funnel_confirm' fresher than ttl_hours (so a candidate that stayed
    'new' after a below-cap probe fail waits out the TTL instead of hot-looping the
    queue). Oldest first, bounded by limit. A confirmed/dropped candidate leaves
    'new' and drops out of the base query — no success bookkeeping needed here."""
    return [r[0] for r in conn.execute(text("""
        SELECT fc.id
        FROM discovery.funnel_candidates fc
        WHERE fc.status = 'new'
          AND NOT EXISTS (SELECT 1 FROM logging.crawl_log l
                          WHERE l.entity_type = 'funnel_confirm'
                            AND l.entity_id = fc.id
                            AND l.crawled_at > now() - make_interval(hours => :ttl))
        ORDER BY fc.first_seen_at, fc.id
        LIMIT :lim
    """), {"ttl": ttl_hours, "lim": limit}).all()]


def _confirm_candidate(engine, r, *, resolve, probe, attempt_cap, stats) -> None:
    """Confirm ONE funnel-candidate row (mutates stats). Extracted verbatim from the
    run_funnel_confirm loop so the daemon one-shot and the batch path share logic."""
    # MMP app deeplinks (onelink/adjust/branch/…) can't be rendered by a headless
    # browser — they device-gate and only ERR_ABORT. Recover the app WITHOUT probing
    # via the page->app map or the link's store-fallback params; if neither resolves,
    # don't hand it to the probe (a guaranteed 30s timeout) — count it unreachable so
    # the attempt cap drops it. This keeps a valuable app-advertiser class the probe
    # would otherwise churn on forever.
    if _is_mmp(r.landing_domain, r.landing_url):
        store, store_id = _resolve_mmp_app(engine, r)
        if store_id:
            with begin(engine) as conn:
                mark_funnel_confirmed(conn, r.id, kind="install", status="confirmed",
                                      store=store, store_id=store_id, payoff_kind="app")
                bridge_row = build_funnel_bridge_row(r, store=store, store_id=store_id,
                                                     technique="funnel_mmp")
                _, was_new = upsert_sweep_candidate(conn, bridge_row)
                stats["bridged_app_new" if was_new else "bridged_app_seen"] += 1
            stats["confirmed"] += 1
            stats["kinds"]["install"] += 1
            stats["mmp_recovered"] += 1
        else:
            _register_probe_failure(engine, r, stats, attempt_cap)
        return

    try:
        resolved = resolve(r.landing_url)
    except Exception:
        resolved = None
    try:
        probed = probe(r.landing_url)
        html, quiz_steps = probed.landing_html, probed.quiz_steps
    except Exception:
        _register_probe_failure(engine, r, stats, attempt_cap)
        return
    kind, status, store, store_id, reason, payoff_kind = classify_funnel(
        html, resolved, r.funnel_host, quiz_steps)
    with begin(engine) as conn:
        mark_funnel_confirmed(conn, r.id, kind=kind, status=status,
                              store=store, store_id=store_id, drop_reason=reason,
                              quiz_steps=quiz_steps, payoff_kind=payoff_kind)
        if status == "confirmed" and payoff_kind == "app" and store_id:
            bridge_row = build_funnel_bridge_row(r, store=store, store_id=store_id)
            _, was_new = upsert_sweep_candidate(conn, bridge_row)
            stats["bridged_app_new" if was_new else "bridged_app_seen"] += 1
    if status == "confirmed":
        stats["confirmed"] += 1
        stats["kinds"][kind] += 1
    else:
        stats["dropped"] += 1


def _empty_stats(examined: int) -> dict:
    return {"examined": examined, "confirmed": 0, "dropped": 0, "probe_failed": 0,
            "mmp_recovered": 0, "probe_capped": 0,
            "kinds": {"install": 0, "subscribe": 0, "unknown": 0},
            "bridged_app_new": 0, "bridged_app_seen": 0}


def run_funnel_confirm(engine, *, resolve, probe, ids=None, limit=None,
                       attempt_cap=None) -> dict:
    """Confirm every 'new' funnel candidate (optionally restricted to `ids`). Commits per row.
    resolve(landing_url) -> obj{kind,store_id}; probe(landing_url) -> obj{landing_html,quiz_steps}."""
    attempt_cap = _DEFAULT_ATTEMPT_CAP if attempt_cap is None else attempt_cap
    with engine.connect() as conn:
        rows = _new_rows(conn, ids)
    if limit is not None:
        rows = rows[:limit]
    stats = _empty_stats(len(rows))
    for r in rows:
        _confirm_candidate(engine, r, resolve=resolve, probe=probe,
                           attempt_cap=attempt_cap, stats=stats)
    return stats


def run_funnel_confirm_one(engine, candidate_id, *, resolve, probe,
                           attempt_cap=None) -> dict:
    """Confirm exactly one candidate (the daemon one-shot unit). Fetches only that
    row (no full-'new' scan) and no-ops if it's no longer 'new'."""
    attempt_cap = _DEFAULT_ATTEMPT_CAP if attempt_cap is None else attempt_cap
    with engine.connect() as conn:
        r = conn.execute(text(
            "SELECT id, landing_url, landing_domain, funnel_host, "
            "page_id, page_name, page_like_count, source_keyword, confirm_attempts "
            "FROM discovery.funnel_candidates WHERE id = :i AND status = 'new'"),
            {"i": candidate_id}).first()
    if r is None:
        return _empty_stats(0)
    stats = _empty_stats(1)
    _confirm_candidate(engine, r, resolve=resolve, probe=probe,
                       attempt_cap=attempt_cap, stats=stats)
    return stats
