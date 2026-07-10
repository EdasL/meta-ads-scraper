"""Flywheel verdict semantics — env-configurable cadence + batch apply_verdicts,
the fix for trials stranded at sweeps_used=2 under the old hardcoded 14-sweep budget.
Rolled-back pg_conn; FWTEST* sentinel keywords only (shared agf_it DB holds other rows)."""
from sqlalchemy import text

from agf.db import dynamic_keywords as dk


def _status(conn, keyword):
    return conn.execute(text(
        "SELECT status FROM discovery.dynamic_keywords WHERE keyword = :k"),
        {"k": keyword}).scalar()


def _set_sweeps(conn, keyword, *, sweeps_used, new_total):
    conn.execute(text(
        "UPDATE discovery.dynamic_keywords SET sweeps_used = :s, new_candidates_total = :n "
        "WHERE keyword = :k"),
        {"s": sweeps_used, "n": new_total, "k": keyword})


def test_trial_cadence_reads_env(monkeypatch):
    monkeypatch.setenv("AGF_FLYWHEEL_TRIAL_SWEEPS", "4")
    monkeypatch.setenv("AGF_FLYWHEEL_PROMOTE_MIN_NEW", "3")
    assert dk.trial_sweeps() == 4
    assert dk.promote_min_new() == 3
    monkeypatch.delenv("AGF_FLYWHEEL_TRIAL_SWEEPS")
    assert dk.trial_sweeps() == 4   # default


def test_promote_bar_default_is_one(monkeypatch):
    # The renewal-engine fix: a starved flywheel must keep any keyword that found
    # at least one new candidate. The default bar was 3 (unreachable at real yields,
    # which retired ~94% of app trials and 100% of funnel trials).
    monkeypatch.delenv("AGF_FLYWHEEL_PROMOTE_MIN_NEW", raising=False)
    assert dk.promote_min_new() == 1


def test_single_new_candidate_promotes_under_default(pg_conn, monkeypatch):
    monkeypatch.setenv("AGF_FLYWHEEL_TRIAL_SWEEPS", "4")
    monkeypatch.delenv("AGF_FLYWHEEL_PROMOTE_MIN_NEW", raising=False)   # default = 1
    dk.add_trial_keywords(pg_conn, ["FWTEST barely", "FWTEST barren"])
    _set_sweeps(pg_conn, "FWTEST barely", sweeps_used=3, new_total=1)   # one short of budget
    _set_sweeps(pg_conn, "FWTEST barren", sweeps_used=3, new_total=0)

    # The 4th sweep pushes both to the budget; only the one with a candidate survives.
    barely_id = pg_conn.execute(text(
        "SELECT id FROM discovery.dynamic_keywords WHERE keyword='FWTEST barely'")).scalar()
    barren_id = pg_conn.execute(text(
        "SELECT id FROM discovery.dynamic_keywords WHERE keyword='FWTEST barren'")).scalar()
    assert dk.record_keyword_sweep(pg_conn, barely_id, new_candidates=0) == "active"
    assert dk.record_keyword_sweep(pg_conn, barren_id, new_candidates=0) == "retired"


def test_dry_streak_column_defaults_zero(pg_conn):
    # migration 143: every keyword starts with an empty dry streak
    dk.add_trial_keywords(pg_conn, ["FWTEST freshcol"])
    val = pg_conn.execute(text(
        "SELECT dry_streak FROM discovery.dynamic_keywords WHERE keyword = :k"),
        {"k": "FWTEST freshcol"}).scalar()
    assert val == 0


def _make_active(conn, keyword, *, dry_streak):
    dk.add_trial_keywords(conn, [keyword])
    conn.execute(text(
        "UPDATE discovery.dynamic_keywords "
        "SET status = 'active', sweeps_used = 8, new_candidates_total = 9, dry_streak = :d "
        "WHERE keyword = :k"),
        {"d": dry_streak, "k": keyword})
    return conn.execute(text(
        "SELECT id FROM discovery.dynamic_keywords WHERE keyword = :k"),
        {"k": keyword}).scalar()


def _dry(conn, keyword):
    return conn.execute(text(
        "SELECT dry_streak FROM discovery.dynamic_keywords WHERE keyword = :k"),
        {"k": keyword}).scalar()


def test_retire_dry_streak_reads_env(monkeypatch):
    monkeypatch.setenv("AGF_FLYWHEEL_RETIRE_DRY_STREAK", "3")
    assert dk.retire_dry_streak() == 3
    monkeypatch.delenv("AGF_FLYWHEEL_RETIRE_DRY_STREAK", raising=False)
    assert dk.retire_dry_streak() == 5   # default


def test_active_rests_after_dry_streak(pg_conn, monkeypatch):
    # Never-drop (migration 147): a saturated active RESTS (cooldown), it does NOT retire.
    monkeypatch.setenv("AGF_FLYWHEEL_REST_DRY_STREAK", "5")
    kid = _make_active(pg_conn, "FWTEST dryout", dry_streak=4)   # one short of K
    assert dk.record_keyword_sweep(pg_conn, kid, new_candidates=0) == "resting"
    assert _status(pg_conn, "FWTEST dryout") == "resting"


def test_active_new_candidate_resets_streak(pg_conn, monkeypatch):
    monkeypatch.setenv("AGF_FLYWHEEL_RETIRE_DRY_STREAK", "5")
    kid = _make_active(pg_conn, "FWTEST revived", dry_streak=4)
    assert dk.record_keyword_sweep(pg_conn, kid, new_candidates=2) == "active"
    assert _dry(pg_conn, "FWTEST revived") == 0


def test_active_below_threshold_stays_active(pg_conn, monkeypatch):
    monkeypatch.setenv("AGF_FLYWHEEL_REST_DRY_STREAK", "5")
    kid = _make_active(pg_conn, "FWTEST patient", dry_streak=2)
    assert dk.record_keyword_sweep(pg_conn, kid, new_candidates=0) == "active"
    assert _dry(pg_conn, "FWTEST patient") == 3


def test_trial_with_high_dry_streak_stays_trial(pg_conn, monkeypatch):
    # Guard: the dry-streak retirement is ACTIVE-only. A trial under its sweep budget keeps
    # earning its verdict even if its streak already crossed the retire threshold — the
    # retire branch must never fire on a trial (locks the branch ordering against edits).
    monkeypatch.setenv("AGF_FLYWHEEL_RETIRE_DRY_STREAK", "5")
    monkeypatch.setenv("AGF_FLYWHEEL_TRIAL_SWEEPS", "4")
    dk.add_trial_keywords(pg_conn, ["FWTEST stubborn"])
    pg_conn.execute(text(
        "UPDATE discovery.dynamic_keywords SET sweeps_used = 1, dry_streak = 9 "
        "WHERE keyword = :k"), {"k": "FWTEST stubborn"})
    kid = pg_conn.execute(text(
        "SELECT id FROM discovery.dynamic_keywords WHERE keyword = :k"),
        {"k": "FWTEST stubborn"}).scalar()
    assert dk.record_keyword_sweep(pg_conn, kid, new_candidates=0) == "trial"
    assert _status(pg_conn, "FWTEST stubborn") == "trial"


def test_apply_verdicts_frees_budget_reached_trials(pg_conn, monkeypatch):
    # A lowered budget (the actual fix): trials stuck at sweeps_used=2 must now verdict.
    monkeypatch.setenv("AGF_FLYWHEEL_TRIAL_SWEEPS", "2")
    monkeypatch.setenv("AGF_FLYWHEEL_PROMOTE_MIN_NEW", "3")
    dk.add_trial_keywords(pg_conn, ["FWTEST winner", "FWTEST dud", "FWTEST young"])
    _set_sweeps(pg_conn, "FWTEST winner", sweeps_used=2, new_total=5)   # >= budget, productive
    _set_sweeps(pg_conn, "FWTEST dud", sweeps_used=2, new_total=0)      # >= budget, barren
    _set_sweeps(pg_conn, "FWTEST young", sweeps_used=1, new_total=9)    # under budget — untouched

    dk.apply_verdicts(pg_conn)

    assert _status(pg_conn, "FWTEST winner") == "active"
    assert _status(pg_conn, "FWTEST dud") == "retired"
    assert _status(pg_conn, "FWTEST young") == "trial"   # still earning its verdict


def test_retested_at_column_exists(pg_conn):
    # migration 153
    row = pg_conn.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='discovery' AND table_name='dynamic_keywords' "
        "AND column_name='retested_at'")).one_or_none()
    assert row is not None


def test_live_keywords_active_gap_spacing(pg_conn):
    dk.add_trial_keywords(pg_conn, ["gap fresh active", "gap stale active",
                                    "gap trial kw"])
    pg_conn.execute(text(
        "UPDATE discovery.dynamic_keywords SET status='active', "
        "last_swept_at = now() - make_interval(hours => 1) "
        "WHERE keyword='gap fresh active'"))
    pg_conn.execute(text(
        "UPDATE discovery.dynamic_keywords SET status='active', "
        "last_swept_at = now() - make_interval(hours => 13) "
        "WHERE keyword='gap stale active'"))
    names = lambda rows: {r.keyword for r in rows}
    spaced = names(dk.live_keywords(pg_conn, active_gap_hours=12))
    assert "gap stale active" in spaced          # older than the gap -> swept
    assert "gap fresh active" not in spaced      # swept 1h ago -> spaced out
    assert "gap trial kw" in spaced              # trials always in rotation
    everyone = names(dk.live_keywords(pg_conn))  # gap 0 = no spacing (default)
    assert {"gap fresh active", "gap stale active", "gap trial kw"} <= everyone


def test_live_keywords_returns_retested_at(pg_conn):
    dk.add_trial_keywords(pg_conn, ["gap trial kw2"])
    row = [r for r in dk.live_keywords(pg_conn) if r.keyword == "gap trial kw2"][0]
    assert row.retested_at is None               # column present on the row
