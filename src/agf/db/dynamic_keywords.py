"""Repo for the keyword flywheel (discovery.dynamic_keywords + flywheel_runs).
Conn-first, callers own the txn — same conventions as db/candidates.py.

Lifecycle policy (the constants below are the single source of truth): a mined
keyword enters as 'trial' and is swept daily. After TRIAL_SWEEPS sweeps it either
earned PROMOTE_MIN_NEW new candidates ('active' — kept in the daily rotation for
good) or it didn't ('retired' — never swept or re-mined again)."""
import json
import os

from sqlalchemy import text


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def crowding_tail() -> float:
    # Crowding >= this is a "crowded" (clone-cluster) candidate. 0.80 = the measured
    # top-quartile edge where like-rate collapses (2.8% vs 8-12%). Read at call time.
    return _env_float("AGF_CROWDING_TAIL", 0.80)


def crowded_candidate_count(conn, keyword: str, *, tail: float) -> int:
    """How many of this keyword's kept candidates sit in a crowded embedding cluster
    (crowding >= tail). Degrade OPEN: a candidate with no embedding row or NULL
    crowding is NOT counted (NULL >= tail is NULL), so a keyword whose crowding has
    not been populated yet reports 0 and behaves as it does today."""
    # c.status='kept' is explicit here even though candidate_embeddings only ever holds
    # kept candidates (candidate_embed's pending query embeds status='kept' rows only) —
    # the filter keeps the crowded count aligned to new_candidates_total, which counts
    # kept candidates, if that invariant ever loosens.
    return conn.execute(text(
        "SELECT count(*) FROM discovery.candidates c "
        "JOIN discovery.candidate_embeddings e ON e.candidate_id = c.id "
        "WHERE c.source_keyword = :kw AND c.status = 'kept' AND e.crowding >= :tail"),
        {"kw": keyword, "tail": tail}).scalar_one()


# Verdict cadence — env-backed so the pool can be made to turn over faster.
# Lower TRIAL_SWEEPS = quicker verdicts = the trial pool recycles in days, not
# weeks. Read at call time (not import) so plist/env changes take effect on
# restart without code edits.
def trial_sweeps() -> int:
    return _env_int("AGF_FLYWHEEL_TRIAL_SWEEPS", 4)


def promote_min_new() -> int:
    # Default lowered 3 -> 1 (2026-06-18): at real per-keyword yields, requiring 3 new
    # candidates across the trial budget was unreachable and retired ~94% of app trials
    # and 100% of funnel trials, stalling the renewal engine. A keyword that finds even
    # one new candidate now survives; saturated keywords are retired later by the
    # novelty-decay logic (P3). Revert with AGF_FLYWHEEL_PROMOTE_MIN_NEW=3.
    return _env_int("AGF_FLYWHEEL_PROMOTE_MIN_NEW", 1)


def retire_dry_streak() -> int:
    # Consecutive 0-new sweeps before a proven ACTIVE keyword is retired. Without this an
    # active is swept forever after its basin is harvested (gem_copy: 2,250 sweeps -> 336
    # new). Read at call time so the plist/env can tune it without a code edit; set very
    # high to disable. Saturated keywords leave the daily rotation and the never-re-mint set.
    # NOTE: superseded by the never-drop resting lifecycle below (rest_dry_streak); kept for
    # apply_verdicts/back-compat callers that still reference it.
    return _env_int("AGF_FLYWHEEL_RETIRE_DRY_STREAK", 5)


# Never-drop lifecycle (migration 147): instead of terminally retiring a saturated ACTIVE
# keyword, we REST it for a cooldown and wake it later so its basin can be re-tried once it
# has had time to refill. rest_dry_streak() is the dry-sweep count that triggers a rest;
# the cooldown grows exponentially per rest (base -> 2x -> 4x ... capped) so a repeatedly
# dry basin backs off but is never abandoned.
def rest_dry_streak() -> int:
    return _env_int("AGF_FLYWHEEL_REST_DRY_STREAK", 3)


def rest_cooldown_base_days() -> int:
    return _env_int("AGF_FLYWHEEL_REST_COOLDOWN_DAYS", 7)


def rest_cooldown_max_days() -> int:
    return _env_int("AGF_FLYWHEEL_REST_COOLDOWN_MAX_DAYS", 28)


def cooldown_days(rest_count: int) -> int:
    """Exponential cooldown backoff, capped. rest_count 0->7, 1->14, 2->28, 3->28 (cap).
    Pass (new_rest_count - 1) so a keyword's FIRST rest waits the base cooldown."""
    return min(rest_cooldown_base_days() * (2 ** rest_count), rest_cooldown_max_days())


# Back-compat aliases (some callers/tests import these names). These are the
# defaults; the functions above are authoritative at runtime.
TRIAL_SWEEPS = trial_sweeps()
PROMOTE_MIN_NEW = promote_min_new()


# `kind` ('app' | 'funnel') keeps the two flywheels' keyword pools separate so a funnel
# trial keyword is never swept into the app candidate pool and vice-versa. Defaults to
# 'app' so existing app-flywheel callers are unchanged.
def tried_keywords(conn, *, kind: str = "app") -> set[str]:
    """Every dynamic keyword of this kind ever minted, any status — the never-re-mint set."""
    return {r[0] for r in conn.execute(
        text("SELECT keyword FROM discovery.dynamic_keywords WHERE kind=:k"),
        {"k": kind}).all()}


def trial_count(conn, *, kind: str = "app") -> int:
    return conn.execute(text(
        "SELECT count(*) FROM discovery.dynamic_keywords WHERE status='trial' AND kind=:k"),
        {"k": kind}).scalar()


def add_trial_keywords(conn, keywords, *, source: str = "gem_copy", kind: str = "app") -> int:
    """Mint trial keywords of this kind; existing ones (any status) are left untouched."""
    added = 0
    for kw in keywords:
        added += conn.execute(text(
            "INSERT INTO discovery.dynamic_keywords (keyword, source, kind) "
            "VALUES (:k, :s, :kind) ON CONFLICT (keyword, kind) DO NOTHING"),
            {"k": kw, "s": source, "kind": kind}).rowcount
    return added


def live_keywords(conn, *, kind: str = "app"):
    """The keywords the daily flywheel sweep runs: trials + proven actives + woken resting
    keywords (cooldown elapsed / NULL), this kind only. A resting keyword whose
    next_eligible_at is still in the future stays out of the rotation."""
    return conn.execute(text(
        "SELECT id, keyword, status, sweeps_used, new_candidates_total "
        "FROM discovery.dynamic_keywords "
        "WHERE kind=:k AND (status IN ('trial','active') "
        "  OR (status='resting' AND (next_eligible_at IS NULL OR next_eligible_at <= now()))) "
        "ORDER BY CASE status WHEN 'trial' THEN 0 WHEN 'active' THEN 1 ELSE 2 END, id"),
        {"k": kind}).all()  # trials first, then active, then woken resting


def record_keyword_sweep(conn, keyword_id: int, *, new_candidates: int) -> str:
    """Bump one keyword's counters after a sweep and apply the verdict; returns the
    (possibly new) status. A 0-new sweep lengthens dry_streak, any new candidate resets it.

    Never-drop lifecycle (migration 147): a proven ACTIVE keyword that has gone dry for
    rest_dry_streak() straight sweeps RESTS (status='resting') for a growing cooldown
    instead of retiring — it leaves the rotation but is woken later by live_keywords once
    next_eligible_at passes. A woken RESTING keyword that yields on re-sweep REVIVES to
    'active'; one that is still dry deepens its rest (rest_count++, longer cooldown). The
    trial verdict is unchanged — an unproven trial still retires."""
    row = conn.execute(text(
        "UPDATE discovery.dynamic_keywords "
        "SET sweeps_used = sweeps_used + 1, "
        "    new_candidates_total = new_candidates_total + :n, "
        "    dry_streak = CASE WHEN :n > 0 THEN 0 ELSE dry_streak + 1 END, "
        "    last_swept_at = now() "
        "WHERE id = :i "
        "RETURNING keyword, status, sweeps_used, new_candidates_total, dry_streak, rest_count"),
        {"n": new_candidates, "i": keyword_id}).one()

    def _rest(new_rc: int) -> str:
        # cooldown_days(new_rc - 1): first rest waits the base cooldown, then it doubles.
        conn.execute(text(
            "UPDATE discovery.dynamic_keywords "
            "SET status='resting', rest_count=:rc, "
            "    next_eligible_at = now() + make_interval(days => :d) WHERE id=:i"),
            {"rc": new_rc, "d": cooldown_days(new_rc - 1), "i": keyword_id})
        return "resting"

    # A woken resting keyword: revive on any yield, else deepen the rest.
    if row.status == "resting":
        if new_candidates > 0:
            conn.execute(text(
                "UPDATE discovery.dynamic_keywords "
                "SET status='active', rest_count=0, next_eligible_at=NULL WHERE id=:i"),
                {"i": keyword_id})  # dry_streak already 0 from the UPDATE above
            return "active"
        return _rest(row.rest_count + 1)

    # A proven active gone dry: REST (never retire).
    if row.status == "active" and row.dry_streak >= rest_dry_streak():
        return _rest(row.rest_count + 1)

    # Trial verdict (unchanged): an unproven trial still retires.
    if row.status != "trial" or row.sweeps_used < trial_sweeps():
        return row.status
    # Promote on UNCROWDED yield only: clone-cluster finds don't count toward the bar.
    uncrowded = row.new_candidates_total - crowded_candidate_count(
        conn, row.keyword, tail=crowding_tail())
    verdict = "active" if uncrowded >= promote_min_new() else "retired"
    conn.execute(text("UPDATE discovery.dynamic_keywords SET status=:s WHERE id=:i"),
                 {"s": verdict, "i": keyword_id})
    return verdict


def apply_verdicts(conn, *, kind: str | None = None) -> dict:
    """Batch-apply the current verdict cadence to every trial that has already
    used its sweep budget. Idempotent — safe to run on startup or by hand to
    recycle a backlog after lowering AGF_FLYWHEEL_TRIAL_SWEEPS. Trials that
    haven't reached the budget are left as 'trial'. `kind` scopes it to one pool
    (None = all kinds, back-compat). Returns counts."""
    # Uncrowded-yield metric, same as record_keyword_sweep: subtract the keyword's
    # crowded (clone-cluster) finds via a correlated subquery so this stays one
    # statement. Degrade OPEN: NULL/absent crowding contributes 0 to the count.
    rows = conn.execute(text(
        "UPDATE discovery.dynamic_keywords k SET status = CASE "
        "  WHEN k.new_candidates_total - ("
        "    SELECT count(*) FROM discovery.candidates c "
        "    JOIN discovery.candidate_embeddings e ON e.candidate_id = c.id "
        "    WHERE c.source_keyword = k.keyword AND c.status = 'kept' "
        "      AND e.crowding >= :tail"
        "  ) >= :promote THEN 'active' ELSE 'retired' END "
        "WHERE k.status = 'trial' AND k.sweeps_used >= :budget "
        "  AND (CAST(:kind AS TEXT) IS NULL OR k.kind = :kind) "
        "RETURNING status"),
        {"promote": promote_min_new(), "budget": trial_sweeps(),
         "tail": crowding_tail(), "kind": kind}).all()
    promoted = sum(1 for r in rows if r.status == "active")
    return {"promoted": promoted, "retired": len(rows) - promoted}


# --- flywheel run log (the worker's daily gate, same pattern as funnel_sweep_runs) ---
def flywheel_ran_today(conn, as_of) -> bool:
    """True when a flywheel run FINISHED on `as_of` (a crashed run doesn't block)."""
    return bool(conn.execute(text(
        "SELECT EXISTS(SELECT 1 FROM discovery.flywheel_runs "
        "WHERE started_at::date = :d AND finished_at IS NOT NULL)"),
        {"d": as_of}).scalar())


def flywheel_ran_within(conn, seconds: int) -> bool:
    """True when a flywheel run FINISHED within the last `seconds`. Used for a
    time-based cadence (several runs/day) instead of the once-per-calendar-day
    gate. A crashed/open run doesn't count."""
    return bool(conn.execute(text(
        "SELECT EXISTS(SELECT 1 FROM discovery.flywheel_runs "
        "WHERE finished_at IS NOT NULL "
        "AND finished_at > now() - make_interval(secs => :s))"),
        {"s": seconds}).scalar())


def open_flywheel_run(conn) -> int:
    return conn.execute(text(
        "INSERT INTO discovery.flywheel_runs DEFAULT VALUES RETURNING id")).scalar_one()


def close_flywheel_run(conn, run_id: int, *, stats: dict | None = None,
                       error: str | None = None) -> None:
    conn.execute(text(
        "UPDATE discovery.flywheel_runs SET finished_at = now(), "
        "stats = CAST(:stats AS jsonb), error = :err WHERE id = :id"),
        {"stats": json.dumps(stats) if stats is not None else None,
         "err": error, "id": run_id})


# --- release-mint run log (the App Store new-releases keyword-mint daily gate) ---------
def release_mint_ran_today(conn, as_of) -> bool:
    """True when a release-mint run FINISHED on `as_of` (blocks re-running that day even
    if it minted nothing; a crashed/open run does not block)."""
    return bool(conn.execute(text(
        "SELECT EXISTS(SELECT 1 FROM discovery.release_mint_runs "
        "WHERE started_at::date = :d AND finished_at IS NOT NULL)"),
        {"d": as_of}).scalar())


def open_release_mint_run(conn) -> int:
    return conn.execute(text(
        "INSERT INTO discovery.release_mint_runs DEFAULT VALUES RETURNING id")).scalar_one()


def close_release_mint_run(conn, run_id: int, *, stats: dict | None = None,
                           error: str | None = None) -> None:
    conn.execute(text(
        "UPDATE discovery.release_mint_runs SET finished_at = now(), "
        "stats = CAST(:stats AS jsonb), error = :err WHERE id = :id"),
        {"stats": json.dumps(stats) if stats is not None else None,
         "err": error, "id": run_id})
