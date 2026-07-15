"""Hunt round executor — on-demand deep sweep of founder-approved terms.

Runs inside the hunt_one subprocess (Task 4). Terms sweep in parallel lanes
(one extractor each); every page's finds stream into frontend.hunt_results and
discovery.candidates immediately, and hunt_rounds.progress carries a live
event feed for the UI. Web-funnel (non_app) ads are counted but not persisted
here — the nightly sweep owns funnel capture (v1 simplification).
"""
import asyncio
import collections
import difflib
import logging
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import text

from agf.db.candidates import upsert_sweep_candidate
from agf.db.dynamic_keywords import add_trial_keywords
from agf.db.engine import begin
from agf.db.hunts import (apply_triage_to_results, clamp_hunt_settings,
                          load_round, mark_round_running,
                          round_new_candidate_ids, term_outcome_stats,
                          update_round_progress, upsert_hunt_result)
from agf.db.pipeline_jobs import enqueue_enrich
from agf.discovery.hunt_judge import judge_round
from agf.discovery.sweep import classify_candidate
from agf.discovery.triage import run_triage
from agf.extract.errors import ExtractError

log = logging.getLogger("agf.hunt")

HUNT_PAGE_CAP = 40        # always-deep: gem band sits below incumbents
HUNT_PATIENCE = 6         # consecutive net-new-dry pages before early exit — raised from 3, design 2026-07-15
HUNT_MIN_PAGES = 10       # dry exit may not fire before this depth (incumbent-flooded early pages fed the dry counter)
HUNT_TERM_LANES = 3       # proven DataImpulse concurrency
HUNT_LISTING_PAGES = 10   # competitor page:<name> pull bound (Task 3)
HUNT_COMPETITOR_CAP = 10            # matches the Go proposal cap (gemini.go)
HUNT_COMPETITOR_TYPEAHEAD_TOP = 5   # typeahead rows considered for disambiguation
HUNT_COMPETITOR_ATTEMPTS = 3        # candidate pages tried before giving up
# Competitor pulls are GLOBAL: founders name small niche apps that often run
# only EU/AU campaigns (design 2026-07-15). Term sweeps stay US-only.
HUNT_COMPETITOR_COUNTRIES = ["US", "GB", "CA", "AU", "DE", "FR", "ES", "IT",
                             "NL", "SE", "PL", "LT", "BR", "MX", "IN"]
HUNT_PAGE_DELAY = 2.0
HUNT_KW_DELAY = 3.0
HUNT_EVENT_CAP = 50


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Progress:
    """Thread-safe progress blob writer. Every mutation persists the whole
    locked state — short txn per write, no txn held across network calls."""

    def __init__(self, engine, round_id: int, terms_total: int):
        self._engine = engine
        self._round_id = round_id
        self._lock = threading.Lock()
        self._state = {"lanes": {}, "terms_done": 0, "terms_total": terms_total,
                       "found": 0, "new": 0, "events": [], "terms": {}}

    def _flush(self) -> None:
        with begin(self._engine) as conn:
            update_round_progress(conn, self._round_id, self._state)

    def event(self, msg: str) -> None:
        with self._lock:
            self._state["events"] = (
                self._state["events"] + [{"t": _now_iso(), "msg": msg}]
            )[-HUNT_EVENT_CAP:]
            self._flush()

    def page(self, lane: int, term: str, depth: int, found: int, new: int) -> None:
        with self._lock:
            self._state["lanes"][str(lane)] = {"term": term, "page": depth}
            self._state["found"] += found
            self._state["new"] += new
            self._state["events"] = (self._state["events"] + [{
                "t": _now_iso(),
                "msg": f"Searching '{term}' — page {depth} · "
                       f"{found} found · {new} new to Field Desk"}]
            )[-HUNT_EVENT_CAP:]
            self._flush()

    def term_done(self, lane: int) -> None:
        with self._lock:
            self._state["terms_done"] += 1
            self._state["lanes"].pop(str(lane), None)
            self._flush()

    def term_stats(self, term: str, pages: int, found: int, new: int, stop: str) -> None:
        with self._lock:
            self._state.setdefault("terms", {})[term] = {
                "pages": pages, "found": found, "new": new, "stop": stop}
            self._flush()

    def merge_term_outcomes(self, outcomes: dict) -> None:
        """Merge post-triage tallies (kept/relevant/off_target) into the
        existing terms entries so Peon's get_round_report sees outcome per
        term, not just sweep telemetry."""
        with self._lock:
            terms = self._state.setdefault("terms", {})
            for term, vals in outcomes.items():
                terms.setdefault(term, {}).update(vals)
            self._flush()


def _persist_page(engine, round_id: int, term: str, records, depth: int,
                  stats: dict, lock: threading.Lock) -> tuple[int, int]:
    """Classify + upsert one SERP page. Returns (found, new) for the page."""
    rows, seen, drops = [], set(), []
    for ad in records:
        disposition, row = classify_candidate(ad, keyword=term, technique="hunt")
        if disposition != "candidate":
            # Non-candidate dispositions ('no_link'/'social'/'non_app'/
            # 'no_store_id') aren't persisted here (the nightly sweep owns
            # funnel capture), but they must show up in the round stats.
            drops.append(disposition)
            continue
        key = (row["page_id"], row["store"], row["store_id"])
        if key in seen:
            continue
        seen.add(key)
        row["first_seen_depth"] = depth
        rows.append(row)
    found = new = 0
    if rows:
        with begin(engine) as conn:
            for row in rows:
                cid, was_new = upsert_sweep_candidate(conn, row)
                upsert_hunt_result(conn, {
                    "round_id": round_id, "candidate_id": cid,
                    "page_id": row["page_id"], "page_name": row["page_name"],
                    "store": row["store"], "store_id": row["store_id"],
                    "link_url": row["link_url"], "source_term": term,
                    "was_new": was_new, "page": depth})
                found += 1
                new += int(was_new)
    with lock:
        stats["found"] += found
        stats["new"] += new
        for disposition in drops:
            stats["drops"][disposition] = stats["drops"].get(disposition, 0) + 1
    return found, new


def _sweep_term(engine, extractor, round_id: int, lane: int, term: str,
                progress: _Progress, stats: dict, lock: threading.Lock,
                sleep, patience: int = HUNT_PATIENCE,
                page_cap: int = HUNT_PAGE_CAP,
                min_pages: int = HUNT_MIN_PAGES) -> tuple[int, int, int, str]:
    """Deep-page one term. Returns (pages, found, new, stop) for lane telemetry;
    the shared `stats` accumulators are updated as before. stop is one of
    'exhausted' (Meta no next page), 'dry' (patience), 'cap' (page_cap),
    'error' (search failed)."""
    cursor = None
    depth = 0
    consec_dry = 0
    term_found = term_new = 0
    while True:
        depth += 1
        try:
            records, cursor, has_next = asyncio.run(extractor.search_page(
                query=term, countries=["US"],
                search_type="KEYWORD_UNORDERED", cursor=cursor))
        except ExtractError:
            log.warning("hunt round %d: search failed for %r", round_id, term)
            with lock:
                stats["errors"] += 1
            progress.event(f"Could not search '{term}' — skipped after an error")
            return depth - 1, term_found, term_new, "error"
        with lock:
            stats["pages"] += 1
        found, new = _persist_page(engine, round_id, term, records, depth,
                                   stats, lock)
        term_found += found
        term_new += new
        progress.page(lane, term, depth, found, new)
        consec_dry = 0 if new > 0 else consec_dry + 1
        if not has_next or not cursor:
            return depth, term_found, term_new, "exhausted"
        if consec_dry >= patience and depth >= min_pages:
            return depth, term_found, term_new, "dry"
        if depth >= page_cap:
            return depth, term_found, term_new, "cap"
        sleep(HUNT_PAGE_DELAY)


def _name_similarity(a: str, b: str) -> float:
    """Case/whitespace-insensitive fuzzy ratio for typeahead disambiguation."""
    na = " ".join((a or "").lower().split())
    nb = " ".join((b or "").lower().split())
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _pull_competitor(engine, extractor, round_id: int, name: str,
                     progress: "_Progress", stats: dict,
                     lock: threading.Lock, listing_pages: int) -> None:
    """Resolve one competitor name to its Ad Library page and pull its ads.

    Typeahead matches page NAME and ranks big pages first, so the top hit is
    often a look-alike (live finding 2026-07-15: 'Femmi' -> a fashion page;
    the real app sat at index 3). Rank the top rows by fuzzy similarity to the
    entered name (likes break ties) and try the best few until one yields
    results. Telemetry: the 'pages' slot counts candidate pages ATTEMPTED;
    stop is one of listing_ok | no_match | no_results | error."""
    key = f"page:{name}"
    progress.event(f"Resolving {name}'s ad page")
    try:
        pages = asyncio.run(extractor.typeahead(query=name))
    except ExtractError:
        progress.event(f"Could not resolve {name} — skipped")
        with lock:
            stats["errors"] += 1
        progress.term_stats(key, 0, 0, 0, "error")
        return
    ranked = sorted(
        pages[:HUNT_COMPETITOR_TYPEAHEAD_TOP],
        key=lambda p: (_name_similarity(name, p.get("page_name")),
                       p.get("page_like_count") or 0),
        reverse=True)
    if not ranked:
        progress.event(f"No Ad Library page found for {name}")
        progress.term_stats(key, 0, 0, 0, "no_match")
        return
    attempts = 0
    for page in ranked[:HUNT_COMPETITOR_ATTEMPTS]:
        label = page.get("page_name") or name
        progress.event(f"Pulling ads from '{label}'")
        try:
            records, _truncated = asyncio.run(extractor.search_listing(
                page_id=page["page_id"],
                countries=HUNT_COMPETITOR_COUNTRIES,
                max_pages=listing_pages))
        except ExtractError:
            with lock:
                stats["errors"] += 1
            progress.term_stats(key, attempts, 0, 0, "error")
            return
        attempts += 1
        # _persist_page called without funnel_autopass (Task 5 flips this call site).
        found, new = _persist_page(engine, round_id, key, records, 1,
                                   stats, lock)
        if found:
            with lock:
                stats["competitor_pages"] += 1
            progress.page(0, key, 1, found, new)
            progress.term_stats(key, attempts, found, new, "listing_ok")
            return
    progress.term_stats(key, attempts, 0, 0, "no_results")


def run_hunt_round(engine, extractors, round_id: int, *,
                   lookup_ios=None, lookup_android=None,
                   sleep=time.sleep) -> dict:
    """Execute one approved hunt round. Streams results as pages complete.
    Terminal round/job status is stamped by the caller (hunt_one)."""
    with begin(engine) as conn:
        rnd = load_round(conn, round_id)
        if rnd is None:
            raise ValueError(f"hunt round {round_id} not found")
        mark_round_running(conn, round_id)
    terms = list(rnd.terms)
    knobs = clamp_hunt_settings(rnd.settings)
    stats = {"terms_swept": 0, "pages": 0, "found": 0, "new": 0, "errors": 0,
             "kept": 0, "vetoed": 0, "pending": 0, "keywords_minted": 0,
             "drops": {}}
    lock = threading.Lock()
    progress = _Progress(engine, round_id, terms_total=len(terms))
    progress.event(f"Hunt started — {len(terms)} terms, deep paging")

    # Phase 0 — competitor page pulls. Each name is resolved through fuzzy
    # typeahead disambiguation (not blind result [0]) and pulled GLOBALLY
    # (niche apps often advertise only in EU/AU). typeahead/search_listing run
    # OUTSIDE any begin() block (no txn held across a network call); only
    # _persist_page opens short txns for the writes.
    # competitor_apps is a jsonb array of STRINGS (["calm"]) as the Go API writes it;
    # accept that shape AND legacy [{"name": ...}] dicts.
    raw = rnd.competitor_apps or []
    competitors = [(c.get("name", "") if isinstance(c, dict) else str(c)) for c in raw]
    competitors = [c for c in competitors if c]
    stats["competitor_pages"] = 0
    for name in competitors[:HUNT_COMPETITOR_CAP]:
        _pull_competitor(engine, extractors[0], round_id, name, progress,
                         stats, lock, knobs["listing_pages"])

    queue = collections.deque(terms)

    def lane(lane_id: int) -> None:
        extractor = extractors[lane_id % len(extractors)]
        while True:
            try:
                term = queue.popleft()
            except IndexError:
                return
            pages, found, new, stop = _sweep_term(
                engine, extractor, round_id, lane_id, term,
                progress, stats, lock, sleep,
                knobs["patience"], knobs["page_cap"], knobs["min_pages"])
            progress.term_stats(term, pages, found, new, stop)
            with lock:
                stats["terms_swept"] += 1
            progress.term_done(lane_id)
            sleep(HUNT_KW_DELAY)

    lanes = min(HUNT_TERM_LANES, max(1, len(terms)), max(1, len(extractors)))
    threads = [threading.Thread(target=lane, args=(i,), daemon=True)
               for i in range(lanes)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    progress.event(f"Search finished — {stats['found']} found, "
                   f"{stats['new']} new to Field Desk")

    # Phase 5 — triage readback + enrich enqueue + hunt keyword mint. run_triage is
    # SCOPED to this round's own candidates (only_ids) so a founder's round never blocks
    # behind — and can't be SIGALRM-killed by — the global status='new' backlog; the
    # nightly tick still drains that backlog unscoped.
    if lookup_ios is not None and lookup_android is not None:
        progress.event("Checking store listings (triage)")
        with begin(engine) as conn:
            own_ids = round_new_candidate_ids(conn, round_id)
            run_triage(conn, lookup_ios=lookup_ios, lookup_android=lookup_android,
                       only_ids=own_ids)
        with begin(engine) as conn:
            counts = apply_triage_to_results(conn, round_id)
            stats.update(counts)
        # Relevance judge runs BETWEEN triage and enqueue: it makes network calls,
        # so it must hold no txn (prod incident: network-inside-txn). Fail-open —
        # NULL relevance is treated as relevant, so a judge outage hides nothing.
        progress.event("Judging relevance against the hunt intent")
        stats.update(judge_round(engine, round_id, getattr(rnd, "intent", None)))
        with begin(engine) as conn:
            kept_ids = [r.kept_advertiser_id for r in conn.execute(text(
                "SELECT DISTINCT kept_advertiser_id FROM frontend.hunt_results "
                "WHERE round_id = :r AND kept_advertiser_id IS NOT NULL "
                "AND COALESCE(relevance, 'relevant') <> 'off_target'"),
                {"r": round_id})]
            stats["enrich_enqueued"] = enqueue_enrich(conn, kept_ids)
        progress.event(f"Triage done — {stats['kept']} kept, "
                       f"{stats['vetoed']} vetoed; "
                       f"{stats['enrich_enqueued']} queued for enrich")
        # Read outcomes in a short txn, then merge (merge opens its own txn to
        # flush progress — never nest the two).
        with begin(engine) as conn:
            outcomes = term_outcome_stats(conn, round_id)
        progress.merge_term_outcomes(outcomes)
    else:
        with begin(engine) as conn:
            stats.update(apply_triage_to_results(conn, round_id))
        stats["enrich_enqueued"] = 0
        with begin(engine) as conn:
            outcomes = term_outcome_stats(conn, round_id)
        progress.merge_term_outcomes(outcomes)
    if terms:
        with begin(engine) as conn:
            stats["keywords_minted"] = add_trial_keywords(
                conn, terms, source="hunt", kind="app")

    progress.event(f"Hunt finished — {stats['found']} found, "
                   f"{stats['new']} new to Field Desk, {stats['kept']} kept")
    return stats
