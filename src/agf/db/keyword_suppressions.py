"""Static-keyword suppression on redundancy grounds (spec 2026-08-03). The static
taxonomy lists stay founder-approved code; this table is the data-driven overlay
that pulls a mined-out phrase from rotation. DELETE a row to un-suppress. Sweep
entry builders read load_suppressed_keywords FAIL-OPEN — suppression is an
optimization and must never block a sweep. Measured 2026-08-03: high-crowding
victims are "app", "ai", "try the app free", "install the app"; high-exclusive-
yield abstract phrases ("on the app store", "get the app") stay — the sweep is
taste-blind by standing decision 2026-06-19."""
import sys

from sqlalchemy import text

from agf.db.dynamic_keywords import (crowding_tail, redundant_min_kept,
                                     redundant_share)


def load_suppressed_keywords(conn) -> set[str]:
    return {r.keyword for r in conn.execute(
        text("SELECT keyword FROM discovery.keyword_suppressions"))}


def drop_suppressed_entries(engine, entries):
    """Filter TaxonomyEntry lists through the suppression table, fail-open."""
    try:
        with engine.connect() as conn:
            suppressed = load_suppressed_keywords(conn)
    except Exception as exc:
        print(f"[suppress] read failed (fail-open): {exc!r}", file=sys.stderr,
              flush=True)
        return list(entries)
    return [e for e in entries if e.keyword not in suppressed]


def suppress_redundant_static_keywords(conn, keywords) -> list[str]:
    """Grade each static keyword's kept catch; upsert redundant ones. Same rule
    as the dynamic pass; idempotent (ON CONFLICT refreshes the metrics). Returns
    the keywords suppressed by THIS call that were not suppressed before."""
    keywords = list(keywords)
    if not keywords:
        return []
    already = load_suppressed_keywords(conn)
    rows = conn.execute(text(
        "SELECT c.source_keyword AS kw, count(e.candidate_id) AS kept, "
        "       count(*) FILTER (WHERE e.crowding >= :tail) AS crowded "
        "FROM discovery.candidates c "
        "JOIN discovery.candidate_embeddings e ON e.candidate_id = c.id "
        "WHERE c.status = 'kept' AND c.source_keyword = ANY(:kws) "
        "GROUP BY 1 "
        "HAVING count(e.candidate_id) >= :min_kept "
        "   AND count(*) FILTER (WHERE e.crowding >= :tail)::numeric "
        "       / count(e.candidate_id) >= :share"),
        {"tail": crowding_tail(), "min_kept": redundant_min_kept(),
         "share": redundant_share(), "kws": keywords}).all()
    fresh = []
    for r in rows:
        conn.execute(text(
            "INSERT INTO discovery.keyword_suppressions "
            "(keyword, reason, kept_n, crowded_share) "
            "VALUES (:kw, 'redundant_catch', :n, :s) "
            "ON CONFLICT (keyword) DO UPDATE SET kept_n = EXCLUDED.kept_n, "
            "  crowded_share = EXCLUDED.crowded_share, suppressed_at = now()"),
            {"kw": r.kw, "n": int(r.kept),
             "s": float(r.crowded) / float(r.kept)})
        if r.kw not in already:
            fresh.append(r.kw)
    return sorted(fresh)
