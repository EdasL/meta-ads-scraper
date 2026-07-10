"""Corpus-scoring compute stages, decoupled from the sweep and run on the */15
cron (spec 2026-07-10). Moved out of the worker loop so Discover-feed /
review-queue freshness no longer waits behind the ~40-min discovery+enrich
cycle. Scoring itself stays on its own */30 run_scoring cron.

Every stage is best-effort and isolated: a failure logs to stderr and CONTINUES
so one stage (e.g. a Gemini 429) can never abort the others. The two embed
stages persist progress incrementally (commit_every=25 + an explicit
conn.commit()) so a mid-cycle failure keeps the already-embedded batch.
"""
import os
import sys
from datetime import date, timedelta

from agf.db.engine import get_engine, begin
from agf.tracker.snapshots import snapshot_advertiser_week
from agf.discovery.crowding_refresh import refresh_crowding


def _added(stats) -> bool:
    """True iff an embed step reported new rows (so crowding needs recompute).

    embed_apps/embed_candidates both return a dict keyed on "embedded"; guard
    against None/other shapes so the crowding recompute is skipped on a no-op
    cycle (the crowding-skip guard)."""
    if isinstance(stats, dict):
        return stats.get("embedded", 0) > 0
    return bool(stats)


def run_compute_cycle(engine, http_client, as_of) -> dict:
    out = {"snapshot_weeks": 0, "embed": None, "preference": None,
           "candidate_embed": None}

    # 1) velocity snapshot — roll THIS week's active-ad counts into the weekly
    #    app_advertiser_metrics series the */30 scoring cron reads. Pure DB.
    try:
        week_start = as_of - timedelta(days=as_of.weekday())
        with begin(engine) as conn:
            out["snapshot_weeks"] = snapshot_advertiser_week(conn, week_start,
                                                             only_ids=None)
    except Exception as exc:
        print(f"[compute] velocity snapshot failed: {exc!r}", file=sys.stderr,
              flush=True)

    # 2) content embeddings + crowding (frontend.app_embeddings). Gemini.
    #    engine.connect() + commit_every=25 so a 429 mid-cycle persists the
    #    already-embedded batch instead of rolling the whole cycle back.
    if os.environ.get("AGF_EMBED_APPS", "1") != "0":
        try:
            from scripts.embed_apps import embed_apps
            with engine.connect() as conn:
                try:
                    emb = embed_apps(conn, commit_every=25)
                    conn.commit()
                except Exception:
                    conn.commit()  # persist progress before the handler logs
                    raise
            out["embed"] = emb
            if _added(emb):  # skip the full recompute on a no-op cycle
                out["embed_crowding"] = refresh_crowding(
                    engine, table="frontend.app_embeddings")
            print(f"[compute] embeddings: {emb} "
                  f"crowding={out.get('embed_crowding')}", flush=True)
        except Exception as exc:
            print(f"[compute] embedding stage failed: {exc!r}", file=sys.stderr,
                  flush=True)

    # 3) preference re-scoring — per-user similarity-to-likes over embeddings.
    #    Pure DB.
    if os.environ.get("AGF_SCORE_PREFERENCES", "1") != "0":
        try:
            from scripts.score_content_preferences import score_all_users
            with begin(engine) as conn:
                out["preference"] = score_all_users(conn)
            print(f"[compute] preference scoring: {out['preference']}", flush=True)
        except Exception as exc:
            print(f"[compute] preference scoring failed: {exc!r}",
                  file=sys.stderr, flush=True)

    # 4) candidate embeddings + crowding (discovery.candidate_embeddings).
    #    Gemini/iTunes.
    if os.environ.get("AGF_TRIAGE_PRIOR", "1") != "0":
        try:
            from agf.discovery.candidate_embed import embed_candidates
            with engine.connect() as conn:
                cst = embed_candidates(
                    conn, itunes_client=http_client, commit_every=25,
                    limit=int(os.environ.get("AGF_TRIAGE_EMBED_PER_CYCLE", "200")))
                conn.commit()
            out["candidate_embed"] = cst
            if _added(cst):
                out["cand_crowding"] = refresh_crowding(
                    engine, table="discovery.candidate_embeddings")
            print(f"[compute] candidate embeddings: {cst} "
                  f"crowding={out.get('cand_crowding')}", flush=True)
        except Exception as exc:
            print(f"[compute] candidate embed stage failed: {exc!r}",
                  file=sys.stderr, flush=True)

    return out


if __name__ == "__main__":
    import httpx
    engine = get_engine()
    with httpx.Client(timeout=20) as http_client:
        summary = run_compute_cycle(engine, http_client, date.today())
    print(f"[compute] cycle done: {summary}", flush=True)
