"""Migration 147 lifecycle: the 'resting' columns/constraint (schema), plus the
never-drop active<->resting behavior in db/dynamic_keywords.record_keyword_sweep /
live_keywords — cooldown backoff on rest, wake+revive on re-sweep, and the unchanged
trial verdict. Uses the rolled-back `pg_conn` fixture."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from agf.db import dynamic_keywords as dk


def test_dynamic_keyword_can_rest(pg_conn):
    # status='resting' must satisfy the extended CHECK, and next_eligible_at + rest_count
    # must be writable. A row that FAILS to insert here means 147 did not apply.
    eligible = datetime.now(timezone.utc) + timedelta(hours=6)
    pg_conn.execute(text(
        "INSERT INTO discovery.dynamic_keywords "
        "(id, keyword, source, kind, status, next_eligible_at, rest_count) VALUES "
        "(94701, 'lifecycle rest one', 'gem_copy', 'app', 'resting', :elig, 2)"),
        {"elig": eligible})
    row = pg_conn.execute(text(
        "SELECT status, next_eligible_at, rest_count FROM discovery.dynamic_keywords "
        "WHERE id = 94701")).one()
    assert row.status == "resting"
    assert row.rest_count == 2
    assert row.next_eligible_at is not None


def test_candidates_accepts_first_seen_depth(pg_conn):
    pg_conn.execute(text(
        "INSERT INTO discovery.candidates "
        "(id, page_id, store, store_id, source_keyword, first_seen_depth) VALUES "
        "(94702, 'LIFECYCLETEST1', 'ios', '94702', 'lifecycle depth kw', 3)"))
    depth = pg_conn.execute(text(
        "SELECT first_seen_depth FROM discovery.candidates WHERE id = 94702")).scalar_one()
    assert depth == 3


# --- helpers ----------------------------------------------------------------------------

def _insert(conn, kid, *, status, dry_streak=0, rest_count=0, sweeps_used=0,
            new_total=0, next_eligible_at=None, keyword=None):
    conn.execute(text(
        "INSERT INTO discovery.dynamic_keywords "
        "(id, keyword, source, kind, status, dry_streak, rest_count, sweeps_used, "
        " new_candidates_total, next_eligible_at) VALUES "
        "(:id, :kw, 'gem_copy', 'app', :st, :ds, :rc, :su, :nt, :elig)"),
        {"id": kid, "kw": keyword or f"lifecycle kw {kid}", "st": status, "ds": dry_streak,
         "rc": rest_count, "su": sweeps_used, "nt": new_total, "elig": next_eligible_at})


def _cooldown_delta_days(conn, kid):
    """Days between the row's next_eligible_at and the transaction's now() (both are
    txn-constant, so this is exact modulo make_interval rounding)."""
    row = conn.execute(text(
        "SELECT next_eligible_at, now() AS nowts FROM discovery.dynamic_keywords "
        "WHERE id = :i"), {"i": kid}).one()
    return (row.next_eligible_at - row.nowts).total_seconds() / 86400.0


# --- lifecycle behavior -----------------------------------------------------------------

def test_active_gone_dry_rests_with_base_cooldown(pg_conn, monkeypatch):
    monkeypatch.setenv("AGF_FLYWHEEL_REST_DRY_STREAK", "2")
    monkeypatch.setenv("AGF_FLYWHEEL_REST_COOLDOWN_DAYS", "7")
    _insert(pg_conn, 94710, status="active", dry_streak=0)
    # Two successive dry sweeps drive dry_streak 1 -> 2 (== rest_dry_streak()).
    assert dk.record_keyword_sweep(pg_conn, 94710, new_candidates=0) == "active"
    assert dk.record_keyword_sweep(pg_conn, 94710, new_candidates=0) == "resting"
    row = pg_conn.execute(text(
        "SELECT status, rest_count FROM discovery.dynamic_keywords WHERE id=94710")).one()
    assert row.status == "resting"
    assert row.rest_count == 1
    assert 6.9 < _cooldown_delta_days(pg_conn, 94710) < 7.1  # base cooldown ~7d


def test_active_gone_dry_never_retires(pg_conn, monkeypatch):
    # The whole point of 147: an active going dry NEVER returns/sets 'retired'.
    monkeypatch.setenv("AGF_FLYWHEEL_REST_DRY_STREAK", "1")
    _insert(pg_conn, 94711, status="active", dry_streak=0)
    status = dk.record_keyword_sweep(pg_conn, 94711, new_candidates=0)
    assert status == "resting"
    assert status != "retired"
    db_status = pg_conn.execute(text(
        "SELECT status FROM discovery.dynamic_keywords WHERE id=94711")).scalar_one()
    assert db_status == "resting"


def test_resting_revives_on_yield(pg_conn, monkeypatch):
    monkeypatch.setenv("AGF_FLYWHEEL_REST_COOLDOWN_DAYS", "7")
    _insert(pg_conn, 94712, status="resting", rest_count=2, dry_streak=5,
            next_eligible_at=datetime.now(timezone.utc) - timedelta(days=1))
    assert dk.record_keyword_sweep(pg_conn, 94712, new_candidates=3) == "active"
    row = pg_conn.execute(text(
        "SELECT status, rest_count, next_eligible_at, dry_streak "
        "FROM discovery.dynamic_keywords WHERE id=94712")).one()
    assert row.status == "active"
    assert row.rest_count == 0
    assert row.next_eligible_at is None
    assert row.dry_streak == 0


def test_resting_dry_deepens_cooldown_backoff(pg_conn, monkeypatch):
    monkeypatch.setenv("AGF_FLYWHEEL_REST_COOLDOWN_DAYS", "7")
    monkeypatch.setenv("AGF_FLYWHEEL_REST_COOLDOWN_MAX_DAYS", "28")
    # Start already resting at rest_count=1 (first rest). Successive dry re-sweeps must
    # deepen: 2nd rest ~14d, 3rd ~28d, 4th ~28d (capped).
    _insert(pg_conn, 94713, status="resting", rest_count=1,
            next_eligible_at=datetime.now(timezone.utc) - timedelta(days=1))
    for expected_rc, expected_days in [(2, 14), (3, 28), (4, 28)]:
        assert dk.record_keyword_sweep(pg_conn, 94713, new_candidates=0) == "resting"
        rc = pg_conn.execute(text(
            "SELECT rest_count FROM discovery.dynamic_keywords WHERE id=94713")).scalar_one()
        assert rc == expected_rc
        delta = _cooldown_delta_days(pg_conn, 94713)
        assert expected_days - 0.1 < delta < expected_days + 0.1


def test_live_keywords_wakes_only_elapsed_resting(pg_conn):
    _insert(pg_conn, 94720, status="active", keyword="live active kw")
    _insert(pg_conn, 94721, status="resting", keyword="live woken past kw",
            next_eligible_at=datetime.now(timezone.utc) - timedelta(days=1))
    _insert(pg_conn, 94722, status="resting", keyword="live woken null kw",
            next_eligible_at=None)
    _insert(pg_conn, 94723, status="resting", keyword="live sleeping future kw",
            next_eligible_at=datetime.now(timezone.utc) + timedelta(days=10))
    ids = {r.id for r in dk.live_keywords(pg_conn)}
    assert 94720 in ids            # active always live
    assert 94721 in ids            # woken (cooldown elapsed)
    assert 94722 in ids            # woken (NULL next_eligible_at)
    assert 94723 not in ids        # still resting (future cooldown)


def test_trial_verdict_unchanged(pg_conn, monkeypatch):
    # Regression: the trial verdict is untouched — a producing trial promotes, an
    # unproven trial still RETIRES (only active/resting are never-drop).
    monkeypatch.setenv("AGF_FLYWHEEL_TRIAL_SWEEPS", "1")
    monkeypatch.setenv("AGF_FLYWHEEL_PROMOTE_MIN_NEW", "1")
    _insert(pg_conn, 94730, status="trial", sweeps_used=0)
    assert dk.record_keyword_sweep(pg_conn, 94730, new_candidates=2) == "active"

    _insert(pg_conn, 94731, status="trial", sweeps_used=0)
    assert dk.record_keyword_sweep(pg_conn, 94731, new_candidates=0) == "retired"
