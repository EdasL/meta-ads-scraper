"""Stage 1 of breadth-first discovery (spec 2026-06-10): a wide, shallow, US-only
keyword sweep that persists app-linked candidates using ONLY fields already present
on the SERP result — zero requests beyond the search pages themselves.

Inline drops (cheapest-first): no link -> social host -> non-app destination (web
funnels fold into discovery.funnel_candidates via fold_web_funnel since 2026-07-07)
-> store link without an id. Page likes never drop an app candidate (the 10k
incumbent ceiling was removed 2026-07-10, user-requested).

Commits per keyword (short transactions), so a mid-run throttle-kill keeps progress."""
import asyncio
import os
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from agf.classify import classify_url
from agf.db.candidates import upsert_sweep_candidate
from agf.db.engine import begin
from agf.db.funnel_candidates import upsert_funnel_candidate
from agf.discovery.funnel_classify import fold_web_funnel
from agf.discovery.page_pool import PageJob, PageOutcome, run_page_pool
from agf.discovery.taxonomy import sweep_entries
from agf.extract.errors import ErrorKind, ExtractError
from agf.scoring.vetoes import FUNNEL_INCUMBENT_PAGE_LIKES_MAX

# Destinations that are neither an app nor a funnel — a social page or ad redirect.
SOCIAL_HOSTS = {"facebook.com", "fb.me", "m.facebook.com", "l.facebook.com", "lm.facebook.com",
                "instagram.com", "api.whatsapp.com", "wa.me", "t.co", "ad.doubleclick.net",
                "youtube.com", "youtu.be", "twitter.com", "x.com", "linktr.ee", "messenger.com"}


class SweepBreaker:
    """Thread-safe dead-proxy circuit breaker shared across concurrent sweep workers.

    PER-WORKER semantics (2026-07-10 review): with disjoint proxy slices (AGF_PROXIES
    >= K) one dead slice must NOT trip the global breaker while other workers are
    healthy. Each worker keeps its OWN consecutive-error count; the cycle trips only
    when EVERY worker has independently reached `threshold` consecutive errors — i.e.
    the whole gateway looks down, not one bad slice. Any worker's success resets only
    that worker's streak. threshold=0 disables (the default single-caller case).

    Single-worker (workers=1) is byte-identical to the old shared int: worker 0 trips
    at `threshold` consecutive errors, resets on its own success, 0 disables."""

    def __init__(self, threshold: int, workers: int = 1):
        self._threshold = threshold
        self._workers = max(1, workers)
        self._lock = threading.Lock()
        self._consecutive: dict[int, int] = {}
        self._tripped = False

    def record_error(self, worker_id: int = 0) -> bool:
        with self._lock:
            self._consecutive[worker_id] = self._consecutive.get(worker_id, 0) + 1
            if self._threshold and sum(
                    1 for c in self._consecutive.values() if c >= self._threshold
            ) >= self._workers:
                self._tripped = True
            return self._tripped

    def record_ok(self, worker_id: int = 0) -> None:
        with self._lock:
            self._consecutive[worker_id] = 0

    def tripped(self) -> bool:
        with self._lock:
            return self._tripped


def _host(url: str | None) -> str:
    return urlparse(url).netloc.lower().replace("www.", "") if url else ""


def _start_date(epoch) -> object | None:
    if not epoch:
        return None
    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).date()
    except (TypeError, ValueError, OSError):
        return None


def classify_candidate(ad, *, keyword: str, technique: str) -> tuple[str, dict | None]:
    """Fold one AdRecord into (disposition, candidate-row). Dispositions:
    'candidate' | 'no_link' | 'social' | 'non_app' | 'no_store_id'."""
    if not ad.page_id or not ad.link_url:
        return "no_link", None
    if _host(ad.link_url) in SOCIAL_HOSTS:
        return "social", None
    intent = classify_url(ad.link_url)
    if intent.kind not in ("ios", "android"):
        return "non_app", None
    if not intent.store_id:
        return "no_store_id", None
    return "candidate", {
        "page_id": ad.page_id, "page_name": ad.page_name,
        "page_like_count": ad.page_like_count,
        "store": intent.kind, "store_id": str(intent.store_id), "link_url": ad.link_url,
        "cta_type": ad.cta_type, "ad_start_date": _start_date(ad.start_date),
        "source_keyword": keyword, "technique": technique,
    }


def _process_page(engine, records, *, entry, depth: int, stats: dict) -> int:
    """Classify + persist ONE SERP page and return the count of NET-NEW candidates
    (the patience window's dry/live signal). Mirrors the old per-SERP fold: within-page
    candidate dedup (same app, several ads, one page), web-funnel capture of the app
    pipeline's rejects, and short per-page transactions so a mid-paging throttle-kill
    keeps every page persisted so far. Each candidate row is tagged with its 1-based
    page `depth`; upsert_sweep_candidate keeps the shallowest via LEAST on re-sweep."""
    stats["raw_ads"] += len(records)
    rows, seen_keys = [], set()
    funnel_rows, funnel_keys = [], set()
    for ad in records:
        disposition, row = classify_candidate(ad, keyword=entry.keyword,
                                               technique=entry.layer)
        if disposition == "candidate":
            key = (row["page_id"], row["store"], row["store_id"])
            if key in seen_keys:                  # same candidate, several ads, one page
                continue
            seen_keys.add(key)
            row["first_seen_depth"] = depth       # 1-based page index this ad was found on
            rows.append(row)
            continue
        if disposition == "non_app":
            # The app pipeline rejected this ad, but its destination may be a web
            # funnel. Only the funnel ceiling gates on page size here.
            likes = ad.page_like_count
            if likes is None or likes <= FUNNEL_INCUMBENT_PAGE_LIKES_MAX:
                fdisp, frow = fold_web_funnel(ad, keyword=entry.keyword,
                                              technique=entry.layer)
                if fdisp == "candidate":
                    stats["drops"]["funnel_captured"] += 1
                    fkey = (frow["page_id"], frow["landing_domain"])
                    if fkey not in funnel_keys:
                        funnel_keys.add(fkey)
                        funnel_rows.append(frow)
                    continue
        stats["drops"][disposition] += 1
    # Deterministic upsert order: two concurrent workers on disjoint keywords can find
    # the SAME apps in opposite order and deadlock in Postgres (an OperationalError that
    # is NOT an ExtractError, so it bypasses the breaker and kills the cycle). Sorting
    # both lists on a stable key makes every worker acquire row locks in one global
    # order. Order-independent: dedup, first_seen_depth and the stats counts are unchanged.
    rows.sort(key=lambda r: (r["page_id"], r["store"], r["store_id"]))
    funnel_rows.sort(key=lambda fr: (fr["page_id"], fr["landing_domain"]))
    page_new = 0
    if rows:                                       # one short txn for this page's apps
        with begin(engine) as conn:
            for row in rows:
                _, was_new = upsert_sweep_candidate(conn, row)
                stats["candidates_new" if was_new else "candidates_seen"] += 1
                if was_new:
                    page_new += 1
    if funnel_rows:                                # second short txn: the funnel pool
        with begin(engine) as conn:
            for frow in funnel_rows:
                _, was_new = upsert_funnel_candidate(conn, frow)
                stats["funnel_new" if was_new else "funnel_seen"] += 1
    return page_new


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _new_telemetry(lane_count: int, *, peak_ready_jobs: int = 0) -> dict:
    return {
        "pages_completed": 0,
        "request_seconds": 0.0,
        "queue_wait_seconds": 0.0,
        "persistence_seconds": 0.0,
        "lane_pages": {lane_id: 0 for lane_id in range(lane_count)},
        "lane_errors": {lane_id: 0 for lane_id in range(lane_count)},
        "peak_ready_jobs": peak_ready_jobs,
    }


async def run_sweep(engine, extractor, *, entries=None, keywords=None,
                    countries=("US",), max_pages: int = 2, kw_delay: float = 3.0,
                    max_consecutive_errors: int = 0,
                    breaker: "SweepBreaker | None" = None, sleep=time.sleep,
                    heartbeat=None, patience_pages: int | None = None,
                    max_depth: int | None = None,
                    page_delay: float | None = None,
                    collect=None, track_pages: bool = False,
                    worker_id: int = 0) -> dict:
    """Sweep the taxonomy (or an explicit entry/keyword subset) and persist candidates.
    Returns counters for sweep_runs. `keywords` (list[str]) is a convenience override
    used by the worker dry-run; it implies KEYWORD_UNORDERED.

    `max_consecutive_errors` is the dead-proxy circuit-breaker: each keyword's search
    only raises after the facade exhausts its retries+IP-rotations, so N of them in a row
    means the proxy/gateway is down (or Meta is hard-throttling). Rather than grind all
    ~300 keywords at ~one timeout-cycle each (the 2026-06-19 wedge), the sweep RAISES so
    run_once fails this cycle and the loop retries next tick. 0 disables (the default, so
    the dry-run and the per-keyword flywheel sweeps are unaffected); the counter resets on
    any successful keyword, so isolated throttles never trip it.

    ADAPTIVE DEPTH (2026-07-07): a keyword is paged one SERP at a time via
    `extractor.search_page` (cursor-resumable) instead of a flat page count, and stops
    on the FIRST of three rules: (a) Meta is exhausted (`has_next` false / no cursor —
    a narrow keyword stops at page 1); (b) the patience window — `patience_pages`
    consecutive pages with zero net-new candidates (this clears an all-known prefix
    but bails once the tail goes barren); (c) the `max_depth` safety cap.
      - `patience_pages` defaults from AGF_SWEEP_PATIENCE_PAGES (3).
      - `max_depth`      defaults from AGF_SWEEP_MAX_DEPTH (15).
      - `page_delay`     inter-page pacing (search_page fetches ONE page, so the
        old per-page delay `search` applied internally is now ours) defaults from
        AGF_SWEEP_PAGE_DELAY (2.0s); the per-KEYWORD `kw_delay` still spaces entries.
    PRECEDENCE: `max_depth` supersedes the legacy `max_pages` (AGF_SWEEP_PAGES) but
    never caps BELOW it — effective depth is max(max_depth, max_pages), so an old
    caller passing a bigger max_pages still gets at least that many pages.

    `collect` (generic-deep lane): called once per raw AdRecord as pages arrive,
    BEFORE any fold/drop — the copy-harvest hook. `track_pages` appends one
    {"keyword", "pages", "ads", "ceiling_hit"} row per entry to stats["page_log"];
    ceiling_hit means the loop stopped while Meta still had pages (cap/patience),
    so a to-exhaustion caller can log it instead of silently capping."""
    if entries is None:
        entries = sweep_entries()
    if keywords is not None:
        entries = [e for e in entries if e.keyword in set(keywords)]
    if patience_pages is None:
        patience_pages = _env_int("AGF_SWEEP_PATIENCE_PAGES", 3)
    if max_depth is None:
        max_depth = _env_int("AGF_SWEEP_MAX_DEPTH", 15)
    if page_delay is None:
        page_delay = _env_float("AGF_SWEEP_PAGE_DELAY", 2.0)
    if breaker is None:
        breaker = SweepBreaker(max_consecutive_errors, workers=1)
    stats = {"keywords_swept": 0, "serp_requests": 0, "raw_ads": 0,
             "candidates_new": 0, "candidates_seen": 0, "errors": 0,
             "funnel_new": 0, "funnel_seen": 0,
             "drops": {"no_link": 0, "social": 0,
                       "non_app": 0, "no_store_id": 0, "funnel_captured": 0}}
    stats["telemetry"] = _new_telemetry(1, peak_ready_jobs=min(1, len(entries)))
    if track_pages:
        stats["page_log"] = []
    for entry in entries:
        if breaker.tripped():
            break
        # entry.max_pages is the per-keyword ceiling; max_depth supersedes the legacy
        # per-keyword flat cap but never lowers it (a caller/harness that asked for
        # deeper via max_pages still gets it).
        entry_max_depth = max(max_depth, entry.max_pages, max_pages)
        cursor = None
        has_next = False
        depth = 0
        pages_fetched = 0
        kw_ads = 0
        consec_dry = 0
        errored = False
        while True:
            depth += 1
            request_started = time.perf_counter()
            try:
                records, cursor, has_next = await extractor.search_page(
                    query=entry.keyword, countries=list(countries),
                    search_type=entry.search_type, cursor=cursor)
            except ExtractError:
                # A keyword's search_page only raises after the facade exhausts its
                # retries+IP-rotations, so treat a mid-paging raise exactly like the old
                # failed `search`: count the error, bump the breaker, move on. Pages
                # persisted before the raise stay committed (short per-page txns).
                stats["errors"] += 1
                stats["telemetry"]["lane_errors"][0] += 1
                if breaker.record_error(worker_id):
                    raise ExtractError(
                        "sweep circuit-breaker: consecutive search errors across "
                        "workers — proxy/gateway likely down, aborting cycle",
                        ErrorKind.TRANSIENT)
                sleep(kw_delay * 2)
                errored = True
                break
            finally:
                stats["telemetry"]["request_seconds"] += (
                    time.perf_counter() - request_started
                )
            pages_fetched += 1
            kw_ads += len(records)
            if collect is not None:
                for ad in records:
                    collect(ad)
            persistence_started = time.perf_counter()
            try:
                page_new = _process_page(
                    engine, records, entry=entry, depth=depth, stats=stats
                )
            finally:
                stats["telemetry"]["persistence_seconds"] += (
                    time.perf_counter() - persistence_started
                )
            stats["telemetry"]["pages_completed"] += 1
            stats["telemetry"]["lane_pages"][0] += 1
            if heartbeat is not None:
                heartbeat()        # prove liveness each PAGE so the watchdog (stale-
                                   # heartbeat kill) never fells a deep-but-progressing sweep
            consec_dry = 0 if page_new > 0 else consec_dry + 1
            if not has_next or not cursor:      # Meta exhausted (narrow kw stops at page 1)
                break
            if consec_dry >= patience_pages:    # patience: cleared a known prefix, now barren
                break
            if depth >= entry_max_depth:        # safety cap
                break
            sleep(page_delay)                   # inter-page pacing (one page per call now)
        if track_pages:
            stats["page_log"].append({"keyword": entry.keyword, "pages": pages_fetched,
                                      "ads": kw_ads,
                                      "ceiling_hit": bool(has_next and cursor)})
        stats["serp_requests"] += pages_fetched  # actual pages fetched this keyword
        if errored:
            continue
        breaker.record_ok(worker_id)              # a fully-paged keyword clears THIS worker's streak
        stats["keywords_swept"] += 1
        sleep(kw_delay)
    return stats


def _partition(items: list, k: int) -> list[list]:
    """Round-robin `items` into k shards (balanced counts; empty shards dropped)."""
    shards = [[] for _ in range(max(1, k))]
    for i, it in enumerate(items):
        shards[i % len(shards)].append(it)
    return [s for s in shards if s]


def _merge_sweep_stats(parts: list[dict]) -> dict:
    """Sum the per-worker stats dicts into one run_once/close_sweep_run-shaped dict."""
    merged = {"keywords_swept": 0, "serp_requests": 0, "raw_ads": 0,
              "candidates_new": 0, "candidates_seen": 0, "errors": 0,
              "funnel_new": 0, "funnel_seen": 0,
              "drops": {"no_link": 0, "social": 0, "non_app": 0,
                        "no_store_id": 0, "funnel_captured": 0}}
    has_page_log = any("page_log" in p for p in parts)
    if has_page_log:
        merged["page_log"] = []
    for p in parts:
        for key, val in p.items():
            if key == "drops":
                for dk, dv in val.items():
                    merged["drops"][dk] = merged["drops"].get(dk, 0) + dv
            elif key == "page_log":
                merged["page_log"].extend(val)
            elif key == "telemetry":
                telemetry = merged.setdefault("telemetry", _new_telemetry(0))
                for metric in (
                    "pages_completed",
                    "request_seconds",
                    "queue_wait_seconds",
                    "persistence_seconds",
                ):
                    telemetry[metric] += val.get(metric, 0)
                telemetry["peak_ready_jobs"] = max(
                    telemetry["peak_ready_jobs"],
                    val.get("peak_ready_jobs", 0),
                )
                for metric in ("lane_pages", "lane_errors"):
                    for lane_id, count in val.get(metric, {}).items():
                        telemetry[metric][lane_id] = (
                            telemetry[metric].get(lane_id, 0) + count
                        )
            else:
                merged[key] = merged.get(key, 0) + val
    return merged


def run_parallel_sweep(engine, extractors, *, entries=None, keywords=None,
                       countries=("US",), max_pages: int = 2, kw_delay: float = 3.0,
                       max_consecutive_errors: int = 0, heartbeat=None,
                       patience_pages: int | None = None, max_depth: int | None = None,
                       page_delay: float | None = None, track_pages: bool = False) -> dict:
    """Run the sweep across extractor-backed proxy lanes.

    With multiple extractors, unstarted keywords are claimed dynamically by the next
    free lane while cursor continuations stay on the extractor that fetched page one.
    A single extractor keeps the existing serial ``run_sweep`` path unchanged. Returns
    one merged stats dict identical in shape to ``run_sweep``'s.
    """
    if entries is None:
        entries = sweep_entries()
    if keywords is not None:
        entries = [e for e in entries if e.keyword in set(keywords)]
    if len(extractors) == 1:
        return asyncio.run(run_sweep(
            engine, extractors[0], entries=entries, countries=countries,
            max_pages=max_pages, kw_delay=kw_delay,
            max_consecutive_errors=max_consecutive_errors,
            heartbeat=heartbeat, patience_pages=patience_pages,
            max_depth=max_depth, page_delay=page_delay, track_pages=track_pages))
    if not entries:
        stats = _merge_sweep_stats([{}])
        stats["telemetry"] = _new_telemetry(len(extractors))
        return stats

    if patience_pages is None:
        patience_pages = _env_int("AGF_SWEEP_PATIENCE_PAGES", 3)
    if max_depth is None:
        max_depth = _env_int("AGF_SWEEP_MAX_DEPTH", 15)
    if page_delay is None:
        page_delay = _env_float("AGF_SWEEP_PAGE_DELAY", 2.0)

    active_lanes = min(len(entries), len(extractors))
    breaker = SweepBreaker(max_consecutive_errors, workers=active_lanes)
    telemetry = _new_telemetry(len(extractors))
    telemetry_lock = threading.Lock()

    def _new_stats():
        stats = _merge_sweep_stats([{}])
        if track_pages:
            stats["page_log"] = []
        return stats

    states = {
        index: {
            "stats": _new_stats(),
            "pages_fetched": 0,
            "kw_ads": 0,
            "consec_dry": 0,
            "cursor": None,
            "has_next": False,
        }
        for index, _entry in enumerate(entries)
    }

    def _finish(state, entry, *, completed):
        stats = state["stats"]
        if track_pages:
            stats["page_log"].append({
                "keyword": entry.keyword,
                "pages": state["pages_fetched"],
                "ads": state["kw_ads"],
                "ceiling_hit": bool(state["has_next"] and state["cursor"]),
            })
        stats["serp_requests"] += state["pages_fetched"]
        if completed:
            stats["keywords_swept"] += 1

    def _fetch(lane_id, job):
        if breaker.tripped():
            return PageOutcome(failed=True)

        entry = job.payload
        state = states[job.key]
        stats = state["stats"]
        request_started = time.perf_counter()
        try:
            records, cursor, has_next = asyncio.run(
                extractors[lane_id].search_page(
                    query=entry.keyword,
                    countries=list(countries),
                    search_type=entry.search_type,
                    cursor=job.cursor,
                )
            )
        except ExtractError:
            stats["errors"] += 1
            _finish(state, entry, completed=False)
            if breaker.record_error(lane_id):
                error = ExtractError(
                    "sweep circuit-breaker: consecutive search errors across "
                    "workers — proxy/gateway likely down, aborting cycle",
                    ErrorKind.TRANSIENT,
                )
                return PageOutcome(failed=True, error=error)
            time.sleep(kw_delay * 2)
            return PageOutcome(failed=True)
        finally:
            with telemetry_lock:
                telemetry["request_seconds"] += (
                    time.perf_counter() - request_started
                )

        state["pages_fetched"] += 1
        state["kw_ads"] += len(records)
        state["cursor"] = cursor
        state["has_next"] = has_next
        persistence_started = time.perf_counter()
        try:
            page_new = _process_page(
                engine,
                records,
                entry=entry,
                depth=job.depth + 1,
                stats=stats,
            )
        finally:
            with telemetry_lock:
                telemetry["persistence_seconds"] += (
                    time.perf_counter() - persistence_started
                )
        if heartbeat is not None:
            heartbeat()
        state["consec_dry"] = 0 if page_new > 0 else state["consec_dry"] + 1

        entry_max_depth = max(max_depth, entry.max_pages, max_pages)
        should_continue = (
            has_next
            and cursor
            and state["consec_dry"] < patience_pages
            and job.depth + 1 < entry_max_depth
        )
        if should_continue:
            return PageOutcome(
                cursor=cursor,
                ready_at=time.monotonic() + page_delay,
            )

        _finish(state, entry, completed=True)
        breaker.record_ok(lane_id)
        time.sleep(kw_delay)
        return PageOutcome()

    outcomes = run_page_pool(
        [PageJob(key=index, payload=entry) for index, entry in enumerate(entries)],
        lane_count=len(extractors),
        handler=_fetch,
        telemetry=telemetry,
    )
    for _job, outcome in outcomes:
        if outcome.error is not None:
            raise outcome.error
    stats = _merge_sweep_stats([state["stats"] for state in states.values()])
    stats["telemetry"] = telemetry
    return stats
