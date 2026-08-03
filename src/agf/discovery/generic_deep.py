"""The weekly generic-deep stage (spec docs/superpowers/specs/
2026-07-10-generic-deep-keyword-lane-design.md): deep-sweep ~19 static copy phrases
to Meta's real pagination ceiling, then mine the collected ad copy into new trial
keywords.

Two payoffs per pass, one set of requests:
  1. candidates -> discovery.candidates tagged technique='generic_deep' (plus the
     sweep's normal web-funnel folds) — search matches ad COPY, so these phrases
     surface advertisers the niche-noun taxonomy never matches;
  2. every raw ad's body_text -> mine_phrases (its home domain: cross-advertiser
     repetition in ad copy) -> superset drop -> injected redundancy gate ->
     dynamic_keywords trials (source='generic_deep').

This weekly pass also carries the redundancy-grading cadence (spec 2026-08-03): it
retires clone-magnet dynamic keywords and refreshes discovery.keyword_suppressions
before building its own entries, so both this lane and the daily sweep stop paging
phrases whose kept catch has gone all-clone.

Patience is disabled (the harvest must not stop on candidate-dry pages — copy and
funnel folds still accrue); depth stops on Meta exhaustion or the deep_pages cap,
and every cap hit is logged, never silent."""
import asyncio
import sys

from agf.db.dynamic_keywords import add_trial_keywords, tried_keywords, trial_count
from agf.db.engine import begin
from agf.discovery.keyword_mining import mine_phrases
from agf.discovery.keyword_redundancy import drop_token_supersets
from agf.discovery.sweep import run_sweep
from agf.discovery.taxonomy import (GENERIC_DEEP_TERMS, all_entries,
                                    generic_deep_entries)

_NO_PATIENCE = 1_000_000   # a harvest lane never stops on candidate-dry pages


def run_generic_deep_stage(engine, extractor, *, deep_pages=150, kw_delay=3.0,
                           mint_slots=25, max_trials=40, min_advertisers=3,
                           gate=None, heartbeat=None, entries=None) -> dict:
    """One weekly pass. `gate` is the redundancy gate (phrases, limit) -> phrases —
    injected by the worker (workers.main._gate_keywords) to avoid a circular import;
    None passes through capped. `entries` is test-injectable."""
    from agf.db.dynamic_keywords import retire_redundant_keywords
    from agf.db.keyword_suppressions import (drop_suppressed_entries,
                                             suppress_redundant_static_keywords)
    from agf.discovery.taxonomy import NICHE_TERMS, STORE_FRAGMENTS

    # Weekly redundancy grading (spec 2026-08-03): this pass is the cadence — retire
    # clone-magnet dynamic keywords and refresh the static suppressions BEFORE the
    # entries are built, so a phrase graded out today is skipped today. Fail-soft:
    # grading is an optimization and must never cost the harvest its pass.
    retired_redundant: list[str] = []
    suppressed: list[str] = []
    try:
        with begin(engine) as conn:
            retired_redundant = retire_redundant_keywords(conn)
            suppressed = suppress_redundant_static_keywords(
                conn, NICHE_TERMS + STORE_FRAGMENTS + GENERIC_DEEP_TERMS)
    except Exception as exc:
        print(f"[worker] redundancy grading failed (continuing): {exc!r}",
              file=sys.stderr, flush=True)
    if entries is None:
        entries = generic_deep_entries(deep_pages)
    entries = drop_suppressed_entries(engine, entries)
    copy_rows: list[tuple[str, str]] = []

    def _collect(ad):
        if ad.page_id and ad.body_text:
            copy_rows.append((str(ad.page_id), ad.body_text))

    sweep = asyncio.run(run_sweep(
        engine, extractor, entries=entries, max_pages=deep_pages,
        max_depth=deep_pages, patience_pages=_NO_PATIENCE, kw_delay=kw_delay,
        heartbeat=heartbeat, track_pages=True, collect=_collect))
    for row in sweep.get("page_log", []):
        if row["ceiling_hit"]:
            print(f"[worker] generic deep sweep: {row['keyword']!r} hit the "
                  f"{deep_pages}-page ceiling ({row['ads']} ads) — more pages exist",
                  file=sys.stderr, flush=True)

    with engine.connect() as conn:
        tried = tried_keywords(conn)
        room = min(mint_slots, max(0, max_trials - trial_count(conn)))
    known = {e.keyword for e in all_entries()} | set(GENERIC_DEEP_TERMS) | tried
    mined = (mine_phrases(copy_rows, exclude=known, limit=room,
                          min_advertisers=min_advertisers)
             if copy_rows and room > 0 else [])
    if mined:
        mined = drop_token_supersets(mined, known)
    if mined:
        mined = gate(mined, room) if gate is not None else list(mined)[:room]
    trials_added = 0
    if mined:
        with begin(engine) as conn:
            trials_added = add_trial_keywords(conn, mined, source="generic_deep")
    return {"sweep": sweep, "mined": list(mined), "trials_added": trials_added,
            "copy_rows": len(copy_rows), "retired_redundant": retired_redundant,
            "suppressed": suppressed}
