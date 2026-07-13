"""Hunt round executor — on-demand deep sweep of founder-approved terms.

Runs inside the hunt_one subprocess (Task 4). Terms sweep in parallel lanes
(one extractor each); every page's finds stream into frontend.hunt_results and
discovery.candidates immediately, and hunt_rounds.progress carries a live
event feed for the UI. Web-funnel (non_app) ads are counted but not persisted
here — the nightly sweep owns funnel capture (v1 simplification).
"""
import asyncio
import collections
import logging
import threading
import time
from datetime import datetime, timezone

from agf.db.candidates import upsert_sweep_candidate
from agf.db.engine import begin
from agf.db.hunts import (load_round, mark_round_running, update_round_progress,
                          upsert_hunt_result)
from agf.discovery.sweep import classify_candidate
from agf.extract.errors import ExtractError

log = logging.getLogger("agf.hunt")

HUNT_PAGE_CAP = 40        # always-deep: gem band sits below incumbents
HUNT_PATIENCE = 3         # consecutive net-new-dry pages before early exit
HUNT_TERM_LANES = 3       # proven DataImpulse concurrency
HUNT_LISTING_PAGES = 10   # competitor page:<name> pull bound (Task 3)
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
                       "found": 0, "new": 0, "events": []}

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


def _persist_page(engine, round_id: int, term: str, records, depth: int,
                  stats: dict, lock: threading.Lock) -> tuple[int, int]:
    """Classify + upsert one SERP page. Returns (found, new) for the page."""
    rows, seen = [], set()
    for ad in records:
        disposition, row = classify_candidate(ad, keyword=term, technique="hunt")
        if disposition != "candidate":
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
                    "was_new": was_new})
                found += 1
                new += int(was_new)
    with lock:
        stats["found"] += found
        stats["new"] += new
    return found, new


def _sweep_term(engine, extractor, round_id: int, lane: int, term: str,
                progress: _Progress, stats: dict, lock: threading.Lock,
                sleep) -> None:
    cursor = None
    depth = 0
    consec_dry = 0
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
            return
        with lock:
            stats["pages"] += 1
        found, new = _persist_page(engine, round_id, term, records, depth,
                                   stats, lock)
        progress.page(lane, term, depth, found, new)
        consec_dry = 0 if new > 0 else consec_dry + 1
        if not has_next or not cursor:
            return
        if consec_dry >= HUNT_PATIENCE:
            return
        if depth >= HUNT_PAGE_CAP:
            return
        sleep(HUNT_PAGE_DELAY)


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
    stats = {"terms_swept": 0, "pages": 0, "found": 0, "new": 0, "errors": 0,
             "kept": 0, "vetoed": 0, "pending": 0, "keywords_minted": 0}
    lock = threading.Lock()
    progress = _Progress(engine, round_id, terms_total=len(terms))
    progress.event(f"Hunt started — {len(terms)} terms, deep paging")

    queue = collections.deque(terms)

    def lane(lane_id: int) -> None:
        extractor = extractors[lane_id % len(extractors)]
        while True:
            try:
                term = queue.popleft()
            except IndexError:
                return
            _sweep_term(engine, extractor, round_id, lane_id, term,
                        progress, stats, lock, sleep)
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
    return stats
