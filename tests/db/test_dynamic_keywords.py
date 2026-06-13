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
