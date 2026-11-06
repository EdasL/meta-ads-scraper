"""Repo for frontend.hunt* — the on-demand Hunt feature (spec 2026-07-13).

Conn-first: every helper takes an open connection and the CALLER owns the txn
(same convention as db/pipeline_jobs.py, db/candidates.py). All SQL uses text()
with bound params.
"""
import json

from sqlalchemy import text

# Per-round scrape knobs (spec 2026-07-13-peon-agent-tools). Defaults mirror the
# executor constants; stored per-round values overlay these, bounded below.
DEFAULT_HUNT_SETTINGS = {
    "page_cap": 40, "patience": 6, "listing_pages": 10, "min_pages": 10,
    "skip_pages": 0}
# skip_pages' floor is 0 (read from page 1), unlike the other knobs: 0 is a
# meaningful setting here, not a disabled one.
_SETTINGS_BOUNDS = {
    "page_cap": (1, 100), "patience": (1, 10),
    "listing_pages": (1, 30), "min_pages": (1, 30), "skip_pages": (0, 30)}


def clamp_hunt_settings(raw: dict | None) -> dict:
    """Effective per-round knobs: defaults overlaid with bounded stored values."""
    out = dict(DEFAULT_HUNT_SETTINGS)
    for key, (lo, hi) in _SETTINGS_BOUNDS.items():
        val = (raw or {}).get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            out[key] = max(lo, min(hi, int(val)))
    return out


def load_round(conn, round_id: int):
    """One hunt_rounds row (terms/competitor_apps as Python lists) or None."""
    return conn.execute(text(
        "SELECT id, hunt_id, terms, competitor_apps, status, settings, intent "
        "FROM frontend.hunt_rounds WHERE id = :i"), {"i": round_id}).first()


def mark_round_running(conn, round_id: int) -> None:
    conn.execute(text(
        "UPDATE frontend.hunt_rounds "
        "SET status = 'running', started_at = now() WHERE id = :i"),
        {"i": round_id})


def finish_round(conn, round_id: int, *, status: str, error: str | None = None) -> None:
    conn.execute(text(
        "UPDATE frontend.hunt_rounds "
        "SET status = :s, error_text = :e, finished_at = now() WHERE id = :i"),
        {"i": round_id, "s": status, "e": error})


def upsert_hunt_result(conn, row: dict) -> None:
    """One page/store row per round; re-finds retain its identity and merge ad copy.
    triage defaults to 'pending' (apps await store triage); web-funnel rows
    insert as 'kept' — no app triage applies to them (design 2026-07-15)."""
    row = {"triage": "pending", **row}
    row["ad_evidence"] = json.dumps(row.get("ad_evidence", [])[:3])
    conn.execute(text(
        "INSERT INTO frontend.hunt_results "
        "  (round_id, candidate_id, page_id, page_name, store, store_id, "
        "   link_url, source_term, was_new, page, triage, ad_evidence) "
        "VALUES (:round_id, :candidate_id, :page_id, :page_name, :store, "
        "        :store_id, :link_url, :source_term, :was_new, :page, :triage, CAST(:ad_evidence AS jsonb)) "
        "ON CONFLICT (round_id, page_id, store, store_id) DO UPDATE SET "
        "ad_evidence = (SELECT COALESCE(jsonb_agg(sample ORDER BY ord), '[]'::jsonb) "
        " FROM (SELECT sample, ord FROM (SELECT DISTINCT ON (sample->>'body_text') sample, ord "
        " FROM jsonb_array_elements("
        " frontend.hunt_results.ad_evidence || EXCLUDED.ad_evidence) "
        " WITH ORDINALITY AS e(sample, ord) ORDER BY sample->>'body_text', ord) unique_copy "
        " ORDER BY ord LIMIT 3) kept)"), row)


def update_round_progress(conn, round_id: int, progress: dict) -> None:
    conn.execute(text(
        "UPDATE frontend.hunt_rounds SET progress = CAST(:p AS jsonb) "
        "WHERE id = :i"), {"i": round_id, "p": json.dumps(progress)})


def apply_triage_to_results(conn, round_id: int) -> dict:
    """Copy triage outcomes from discovery.candidates onto this round's results.

    candidates.status is one of 'new'/'kept'/'dropped' (db/schema/112); map
    'kept' -> kept, 'dropped' -> vetoed, everything else -> pending. Returns the
    per-triage counts for this round.
    """
    conn.execute(text(
        "UPDATE frontend.hunt_results hr SET "
        "  triage = CASE c.status WHEN 'kept' THEN 'kept' "
        "                         WHEN 'dropped' THEN 'vetoed' "
        "                         ELSE 'pending' END, "
        "  kept_advertiser_id = c.kept_advertiser_id "
        "FROM discovery.candidates c "
        "WHERE hr.round_id = :r AND hr.candidate_id = c.id"), {"r": round_id})
    counts = {"kept": 0, "vetoed": 0, "pending": 0}
    for row in conn.execute(text(
            "SELECT triage, count(*) AS n FROM frontend.hunt_results "
            "WHERE round_id = :r GROUP BY triage"), {"r": round_id}):
        counts[row.triage] = row.n
    return counts


def round_summary_counts(conn, round_id: int) -> dict:
    """Found / kept / off-target tallies for one finished round's results."""
    row = conn.execute(text(
        "SELECT count(*) AS found, "
        "count(*) FILTER (WHERE triage = 'kept') AS kept, "
        "count(*) FILTER (WHERE relevance = 'off_target') AS off_target "
        "FROM frontend.hunt_results WHERE round_id = :r"), {"r": round_id}).one()
    return {"found": row.found, "kept": row.kept, "off_target": row.off_target}


def term_outcome_stats(conn, round_id: int) -> dict:
    """Post-triage outcome tallies per source_term for one round: kept /
    relevant / off_target. Keys match progress.terms entries (competitor pulls
    appear as 'page:<name>'). NULL relevance counts as neither (fail-open
    unjudged, spec 2026-07-15)."""
    out = {}
    for row in conn.execute(text(
            "SELECT source_term, "
            "count(*) FILTER (WHERE triage = 'kept') AS kept, "
            "count(*) FILTER (WHERE relevance = 'relevant') AS relevant, "
            "count(*) FILTER (WHERE relevance = 'off_target') AS off_target "
            "FROM frontend.hunt_results WHERE round_id = :r "
            "GROUP BY source_term"), {"r": round_id}):
        out[row.source_term] = {"kept": row.kept, "relevant": row.relevant,
                                "off_target": row.off_target}
    return out


def compose_round_message(round_id: int, status: str, counts: dict, error=None) -> str:
    """Plain, honest chat copy for a finished round. No marketing voice."""
    if status != "done":
        detail = (error or "unknown error").strip()[:160]
        return f"Round {round_id} failed: {detail}."
    base = f"Round {round_id} done: {counts.get('kept', 0)} kept of {counts.get('found', 0)} found"
    off = counts.get("off_target", 0)
    return f"{base}, {off} off target." if off else f"{base}."


def assistant_message_since_finish(conn, round_id: int) -> bool:
    """True when an assistant message already landed at/after this round finished
    — i.e. the API's completion hook wrote Peon's wrap-up (even if its HTTP
    response was lost in transit). The daemon checks this before writing its
    canned fallback so the two writers can't double-message the same round."""
    row = conn.execute(text(
        "SELECT EXISTS ("
        "  SELECT 1 FROM frontend.hunt_messages m "
        "  JOIN frontend.hunt_rounds r ON r.hunt_id = m.hunt_id "
        "  WHERE r.id = :r AND m.role = 'assistant' "
        "    AND r.finished_at IS NOT NULL AND m.created_at >= r.finished_at"
        ")"), {"r": round_id}).scalar()
    return bool(row)


def insert_round_message(conn, round_id: int, content: str) -> None:
    """One assistant message into the round's hunt thread (non-fatal helper)."""
    conn.execute(text(
        "INSERT INTO frontend.hunt_messages (hunt_id, role, content) "
        "SELECT hunt_id, 'assistant', :c FROM frontend.hunt_rounds WHERE id = :r"),
        {"c": content, "r": round_id})


def round_new_candidate_ids(conn, round_id: int) -> list[int]:
    return [r.candidate_id for r in conn.execute(text(
        "SELECT candidate_id FROM frontend.hunt_results "
        "WHERE round_id = :r AND candidate_id IS NOT NULL "
        "ORDER BY candidate_id"), {"r": round_id})]
