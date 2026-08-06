"""One-shot pasted-link add (subprocess unit for the keeper daemon).

    python -m agf.discovery.add_page_one --advertiser-id N --job-id M

The founder pastes a Meta Ad Library link on /discover; the API resolves it to a
page id, upserts the advertiser and queues this job. Here we pull the page's US
ads, derive app candidates from them, triage those candidates, mark the
advertiser as a manual add, then run the normal enrich path — which scores this
one advertiser and refreshes the gem matview, so the gem is live by the time
this job reports done rather than waiting on the */15 compute cycle.

ORDER MATTERS. keeper_history._linked_app_id resolves an advertiser's app via
`discovery.candidates WHERE kept_advertiser_id = :adv AND status = 'kept'`, and
refresh_one_advertiser reads it BEFORE storing ads. In the normal sweep the
candidate is triaged first so the link already exists. A pasted link starts from
a bare page id with no candidate at all, so the ads land with app_id NULL and
the matview's app LATERAL (`WHERE ... AND a.app_id IS NOT NULL`) finds nothing —
the gem would render with no app name, icon, installs or rating. Rather than pay
for a second Meta listing pull, step 4 backfills ads.app_id from the now-kept
candidate in one statement.

Triage NEVER rejects here. The vetoes exist to filter breadth-sweep noise, and
the founder asked for this page by name: the verdict is recorded as a note and
advertisers.manual_add carries the advertiser past the matview's veto filter
(migration 165).

Terminal-status contract (same as keeper_one): steps 1-4 write terminal status
only for their own failures and return immediately; enrich_one.run_job is the
last writer on the happy path and its 'done' is the job's 'done'. Exactly one
terminal write per run. mark_crawled_on_success stays False — this module writes
its own entity_type='add_page' marker, and a second entity_type='advertiser'
marker would let a founder's paste suppress the enrich lane's TTL scheduling.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys

from sqlalchemy import text

from agf.classify import classify_url
from agf.db import repo
from agf.db.candidates import mark_triaged, upsert_sweep_candidate
from agf.db.engine import begin, get_engine
from agf.db.pipeline_jobs import set_job_note, set_job_phase, set_job_status
from agf.discovery.keeper_history import refresh_one_advertiser
from agf.discovery.triage import _app_dict, triage_decision
from agf.workers.scheduler import mark_crawled

log = logging.getLogger("agf.add_page")

TIMEOUT_SEC = int(os.environ.get("AGF_ADD_PAGE_TIMEOUT_SEC", "600"))


class AddPageTimeout(BaseException):
    """SIGALRM wall-clock guard. Deliberately NOT an Exception so no per-stage
    `except Exception` handler can swallow the process-wide deadline (the
    EnrichDeadline lesson)."""


def _advertiser(engine, advertiser_id: int):
    """(page_id, page_name, page_like_count) for the advertiser, or None."""
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT page_id, page_name, page_like_count "
            "FROM public.advertisers WHERE id = :a"),
            {"a": advertiser_id}).one_or_none()


def _phase(engine, job_id: int, label: str) -> None:
    with begin(engine) as conn:
        set_job_phase(conn, job_id, label)


def _fail(engine, advertiser_id: int, job_id: int, msg: str) -> int:
    """Terminal error + the TTL marker, so a page that cannot be scraped is not
    re-claimed forever."""
    try:
        with begin(engine) as conn:
            set_job_status(conn, job_id, "error", msg[:500])
    except Exception:  # noqa: BLE001 — best-effort; the daemon reaper backstops
        log.exception("add_page %d: could not record the error status", advertiser_id)
    try:
        with begin(engine) as conn:
            mark_crawled(conn, "add_page", advertiser_id, "error")
    except Exception:  # noqa: BLE001 — never mask the original failure
        log.exception("add_page %d: could not mark the crawl log", advertiser_id)
    return 1


def _derive_candidates(conn, advertiser_id: int, page_id: str,
                       page_name, page_like_count) -> list[int]:
    """Turn this advertiser's stored ad destinations into app candidates.

    Reads public.ads rather than the AdRecords so the scrape stays a single Meta
    call. Only store links produce a candidate; a web-only advertiser yields
    none and still stands (the existing web-funnel path handles it downstream).
    Returns the candidate ids to triage.
    """
    rows = conn.execute(text(
        "SELECT DISTINCT link_url, cta_type FROM public.ads "
        "WHERE advertiser_id = :a AND link_url IS NOT NULL"),
        {"a": advertiser_id}).all()
    ids: list[int] = []
    seen: set[tuple[str, str]] = set()
    for link_url, cta_type in rows:
        intent = classify_url(link_url)
        if intent.kind not in ("ios", "android") or not intent.store_id:
            continue
        key = (intent.kind, str(intent.store_id))
        if key in seen:                      # one candidate per (store, store_id)
            continue
        seen.add(key)
        cid, _was_new = upsert_sweep_candidate(conn, {
            "page_id": page_id,
            "page_name": page_name,
            "page_like_count": page_like_count,
            "store": intent.kind,
            "store_id": intent.store_id,
            "link_url": link_url,
            "cta_type": cta_type,
            "ad_start_date": None,
            # Provenance: these rows came from a pasted link, not a sweep, so
            # keyword-yield analysis must not count them.
            "source_keyword": None,
            "technique": "manual_add",
            "first_seen_depth": 0,
        })
        ids.append(cid)
    return ids


def _link_ads_to_app(conn, advertiser_id: int) -> int:
    """Backfill public.ads.app_id from the advertiser's kept candidate.

    refresh_one_advertiser stored the ads before triage existed to link them, so
    without this the matview's app LATERAL finds nothing and the gem renders
    with no app name, icon or ratings. Idempotent (app_id IS NULL guard).
    """
    return conn.execute(text("""
        UPDATE public.ads a
           SET app_id = ap.id
          FROM discovery.candidates c
          JOIN public.apps ap ON ap.store = c.store AND ap.store_id = c.store_id
         WHERE c.kept_advertiser_id = :adv
           AND c.status = 'kept'
           AND a.advertiser_id = :adv
           AND a.app_id IS NULL
    """), {"adv": advertiser_id}).rowcount


# triage_decision's drop reasons, in the words a founder reads on the card. A
# raw enum ('ratings_over_threshold') is not copy; the note is the only place
# the veto is ever explained, so it says what was actually found.
_DROP_REASONS = {
    "no_us_listing": "its app has no US store listing",
    "game": "its app is a game",
    "ratings_over_threshold": "its app already has too many ratings to count as emerging",
}


def _note_for(reasons: list[str]) -> str | None:
    """The sentence shown on the card. None when nothing was flagged."""
    known = [_DROP_REASONS[r] for r in reasons if r in _DROP_REASONS]
    if not known:
        return None
    return f"Kept even though {known[0]}."


def promote_candidates(conn, advertiser_id: int, candidate_ids: list[int], *,
                       lookup_ios, lookup_android) -> tuple[str | None, int]:
    """Resolve each candidate's store listing and link it to this advertiser
    REGARDLESS of the triage verdict. Returns (note, promoted).

    This deliberately does NOT call run_triage. Two reasons, both found on the
    first live prod run (2026-08-06, page 1680462865414117 'Zutobi Drivers Ed'):

    1. `discovery.candidates` status never regresses, and run_triage only reads
       status='new'. The pages a founder pastes are very often ones the sweep
       already DROPPED, so triage examined zero candidates, promoted nothing,
       and all 43 scraped ads stayed app_id NULL — a gem with no name, icon or
       ratings. A paste is the sanctioned exception to never-regress.
    2. run_triage only promotes 'kept' candidates. A pasted page that the vetoes
       reject still has to end up with its app linked, or "add it anyway" adds a
       nameless shell. The verdict becomes the note; it never blocks the link.

    The advertiser row already exists (the API created it), so this only needs
    the app upsert plus the candidate's kept_advertiser_id.
    """
    if not candidate_ids:
        return "None of this advertiser's ads link to an app store listing.", 0

    rows = conn.execute(text(
        "SELECT id, store, store_id FROM discovery.candidates WHERE id = ANY(:i)"),
        {"i": candidate_ids}).mappings().all()

    resolved: dict[str, dict] = {}
    for store, lookup in (("ios", lookup_ios), ("android", lookup_android)):
        ids = [r["store_id"] for r in rows if r["store"] == store]
        if not ids:
            continue
        try:
            resolved[store] = lookup(ids) or {}
        except Exception:  # noqa: BLE001 — a store outage must not block the add
            log.exception("add_page %d: %s store lookup failed", advertiser_id, store)
            resolved[store] = {}

    reasons: list[str] = []
    promoted = 0
    for row in rows:
        meta = resolved.get(row["store"], {}).get(str(row["store_id"]))
        _verdict, reason = triage_decision(meta)
        if reason:
            reasons.append(reason)
        if meta is None:
            # No listing to link. Leave the candidate's own status alone.
            continue
        repo.upsert_app(conn, _app_dict(row["store"], row["store_id"], meta))
        mark_triaged(conn, row["id"], status="kept", kept_advertiser_id=advertiser_id)
        promoted += 1

    if not promoted:
        return "Field Desk found no app store listing for this advertiser.", 0
    return _note_for(reasons), promoted


def run(engine, advertiser_id: int, job_id: int, *,
        refresh=refresh_one_advertiser, enrich=None,
        extractor=None, lookup_ios=None, lookup_android=None) -> int:
    """Execute one pasted-link add. Returns 0 on success, 1 on failure — the
    same contract as keeper_one.run. Every collaborator is injectable so the
    state machine is testable without Meta or the app stores."""
    row = _advertiser(engine, advertiser_id)
    if row is None or not row.page_id:
        return _fail(engine, advertiser_id, job_id, "advertiser has no page_id")
    page_id, page_name, page_like_count = row.page_id, row.page_name, row.page_like_count

    if extractor is None:
        from agf.extract.facade import Extractor
        extractor = Extractor()

    # 1. Pull the page's US ads (+ the 16-week series). For a page we have never
    #    seen this IS the initial pull; it also fills in page_name.
    _phase(engine, job_id, "Pulling this advertiser's ads")
    try:
        asyncio.run(refresh(engine, extractor, advertiser_id, page_id,
                            flip_vanished=False))
    except Exception as e:  # noqa: BLE001 — terminal status on every failure path
        log.exception("add_page %d: scrape failed", advertiser_id)
        return _fail(engine, advertiser_id, job_id, repr(e))

    # 2. Derive app candidates from the ads we just stored.
    _phase(engine, job_id, "Reading where the ads go")
    try:
        with begin(engine) as conn:
            page_name = conn.execute(text(
                "SELECT page_name FROM public.advertisers WHERE id = :a"),
                {"a": advertiser_id}).scalar() or page_name
            candidate_ids = _derive_candidates(
                conn, advertiser_id, page_id, page_name, page_like_count)
    except Exception as e:  # noqa: BLE001
        log.exception("add_page %d: could not read the ads", advertiser_id)
        return _fail(engine, advertiser_id, job_id, repr(e))

    # 3. Resolve each candidate's store listing and link it to this advertiser,
    #    whatever the vetoes say. See promote_candidates for why this cannot be
    #    run_triage.
    _phase(engine, job_id, "Checking the app store listing")
    note = None
    try:
        if lookup_ios is None or lookup_android is None:
            from functools import partial

            import httpx

            from agf.enrich_full.appstore import (lookup_google_play,
                                                  lookup_itunes_batch)
            store_client = httpx.Client(timeout=20, follow_redirects=True)
            lookup_ios = lookup_ios or partial(lookup_itunes_batch, store_client, pause=3.0)
            lookup_android = lookup_android or lookup_google_play
        with begin(engine) as conn:
            note, promoted = promote_candidates(
                conn, advertiser_id, candidate_ids,
                lookup_ios=lookup_ios, lookup_android=lookup_android)
        log.info("add_page %d: promoted %d candidate(s)", advertiser_id, promoted)
    except Exception:  # noqa: BLE001 — advisory, never fatal
        log.exception("add_page %d: store lookup failed (continuing)", advertiser_id)
        note = "Field Desk could not check the app store listing."

    # 4. Mark the manual add (this is what carries it past the matview's veto
    #    filter) and link the ads we stored in step 1 to the app triage resolved.
    _phase(engine, job_id, "Adding to your feed")
    try:
        with begin(engine) as conn:
            conn.execute(text(
                "UPDATE public.advertisers SET manual_add = TRUE WHERE id = :a"),
                {"a": advertiser_id})
            linked = _link_ads_to_app(conn, advertiser_id)
            if note:
                set_job_note(conn, job_id, note)
        log.info("add_page %d: linked %d ads to the resolved app", advertiser_id, linked)
    except Exception as e:  # noqa: BLE001
        log.exception("add_page %d: could not record the add", advertiser_id)
        return _fail(engine, advertiser_id, job_id, repr(e))

    # 5. The normal enrich path: enrich, score THIS advertiser, refresh the
    #    matview. run_job writes the terminal status on this same job row, so it
    #    is the last writer on the happy path.
    _phase(engine, job_id, "Filling in the details")
    if enrich is not None:
        enrich(engine=engine, advertiser_id=advertiser_id, job_id=job_id)
        with begin(engine) as conn:
            set_job_status(conn, job_id, "done")
        return 0

    import httpx

    from agf.enrich_full.enrich_one import _default_enrich, _default_score, run_job
    http_client = httpx.Client(timeout=30, follow_redirects=True)
    try:
        run_job(
            engine,
            advertiser_id=advertiser_id,
            job_id=job_id,
            # ads_refresh=False: step 1 already pulled this page's listing, and
            # a second Meta call would double the proxy spend for nothing.
            enrich_call=lambda eng, aid: _default_enrich(
                eng, aid, extractor=extractor, http_client=http_client,
                ads_refresh=False),
            score_call=_default_score,
            status_table="pipeline_jobs",
            mark_crawled_on_success=False)
    except BaseException as e:  # noqa: BLE001 — run_job already recorded the row
        log.exception("add_page %d: enrich failed", advertiser_id)
        with begin(engine) as conn:
            mark_crawled(conn, "add_page", advertiser_id, f"error:{type(e).__name__}"[:64])
        return 1
    finally:
        http_client.close()
    return 0


def main(argv=None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--advertiser-id", type=int, required=True)
    ap.add_argument("--job-id", type=int, required=True)
    ap.add_argument("--timeout", type=int, default=TIMEOUT_SEC)
    args = ap.parse_args(argv)

    def _alarm(signum, frame):
        raise AddPageTimeout(f"add_page exceeded {args.timeout}s")

    signal.signal(signal.SIGALRM, _alarm)
    engine = get_engine()
    signal.alarm(args.timeout)
    try:
        return run(engine, args.advertiser_id, args.job_id)
    except AddPageTimeout as e:
        return _fail(engine, args.advertiser_id, args.job_id, str(e))
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    sys.exit(main())
