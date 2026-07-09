# tests/scripts/test_reassign_user_email.py
# DB-backed test for the one-off founder-account reassignment script.
# Mirrors the root `pg_conn` fixture (conftest.py) — a migrated connection wrapped
# in a transaction that is rolled back after each test — plus the raw-SQL seeding
# style of tests/db/test_app_ratings.py.
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from agf.db import repo
from scripts.reassign_user_email import reassign

OLD = "founder@fielddesk.local"
NEW = "founder@gmail.com"


def _seed(conn):
    """Old user: admin, 2 ratings + 1 saved gem + 1 api key.
    New user: member, 1 rating that CONFLICTS with the old user's rating on the
    same advertiser (so the merge must keep the NEW user's row)."""
    # A real advertiser is needed because saved_gems.advertiser_id FKs advertisers(id).
    aid = repo.upsert_advertiser(
        conn,
        SimpleNamespace(page_id="REASSIGN1", page_name="Reassign Co", page_like_count=42),
    )
    conn.execute(
        text(
            "INSERT INTO frontend.users (email, password_hash, role, auth_provider) "
            "VALUES (:e, 'x', 'admin', 'password')"
        ),
        {"e": OLD},
    )
    conn.execute(
        text(
            "INSERT INTO frontend.users (email, password_hash, role, auth_provider) "
            "VALUES (:e, NULL, 'member', 'google')"
        ),
        {"e": NEW},
    )
    # Old user's two ratings — advertiser 1001 will conflict with the new user.
    conn.execute(
        text(
            "INSERT INTO frontend.app_ratings (user_email, advertiser_id, rating) "
            "VALUES (:e, 1001, 'like'), (:e, 1002, 'dislike')"
        ),
        {"e": OLD},
    )
    # Old user's saved gem (needs the real advertiser id).
    conn.execute(
        text(
            "INSERT INTO frontend.saved_gems (user_email, advertiser_id, gem_score) "
            "VALUES (:e, :a, 77.0)"
        ),
        {"e": OLD, "a": aid},
    )
    # Old user's api key (FK to users(email) — must move before the old row is deleted).
    conn.execute(
        text(
            "INSERT INTO frontend.api_keys (key_hash, user_email, label) "
            "VALUES ('hash-old', :e, 'k')"
        ),
        {"e": OLD},
    )
    # New user's CONFLICTING rating on advertiser 1001.
    conn.execute(
        text(
            "INSERT INTO frontend.app_ratings (user_email, advertiser_id, rating) "
            "VALUES (:e, 1001, 'neutral')"
        ),
        {"e": NEW},
    )
    return aid


def test_reassign_execute_merges_history_onto_new_identity(pg_conn):
    _seed(pg_conn)

    counts = reassign(pg_conn, OLD, NEW, execute=True)

    # New user inherited the admin role.
    role = pg_conn.execute(
        text("SELECT role FROM frontend.users WHERE email = :e"), {"e": NEW}
    ).scalar_one()
    assert role == "admin"

    # New user has exactly 2 ratings; the conflict kept the NEW user's row.
    ratings = pg_conn.execute(
        text(
            "SELECT advertiser_id, rating FROM frontend.app_ratings "
            "WHERE user_email = :e ORDER BY advertiser_id"
        ),
        {"e": NEW},
    ).all()
    assert len(ratings) == 2
    by_adv = {r.advertiser_id: r.rating for r in ratings}
    assert by_adv[1001] == "neutral"  # new user's action won the conflict
    assert by_adv[1002] == "dislike"  # moved over from the old user

    # Saved gem moved.
    saved = pg_conn.execute(
        text("SELECT count(*) FROM frontend.saved_gems WHERE user_email = :e"),
        {"e": NEW},
    ).scalar_one()
    assert saved == 1

    # Api key moved (and the FK survived the old-user delete).
    key_owner = pg_conn.execute(
        text("SELECT user_email FROM frontend.api_keys WHERE key_hash = 'hash-old'")
    ).scalar_one()
    assert key_owner == NEW

    # Old user row is gone.
    old_left = pg_conn.execute(
        text("SELECT count(*) FROM frontend.users WHERE email = :e"), {"e": OLD}
    ).scalar_one()
    assert old_left == 0

    # Counts reflect what moved.
    assert counts["app_ratings"] == 1  # only 1002 moved; 1001 was deleted as a conflict
    assert counts["saved_gems"] == 1
    assert counts["api_keys"] == 1


def test_reassign_dry_run_changes_nothing(pg_conn):
    _seed(pg_conn)

    reassign(pg_conn, OLD, NEW, execute=False)

    # Old user still present with its history intact.
    old_left = pg_conn.execute(
        text("SELECT count(*) FROM frontend.users WHERE email = :e"), {"e": OLD}
    ).scalar_one()
    assert old_left == 1
    old_ratings = pg_conn.execute(
        text("SELECT count(*) FROM frontend.app_ratings WHERE user_email = :e"),
        {"e": OLD},
    ).scalar_one()
    assert old_ratings == 2

    # New user is untouched (still its single original rating, still 'member').
    new_ratings = pg_conn.execute(
        text("SELECT count(*) FROM frontend.app_ratings WHERE user_email = :e"),
        {"e": NEW},
    ).scalar_one()
    assert new_ratings == 1
    role = pg_conn.execute(
        text("SELECT role FROM frontend.users WHERE email = :e"), {"e": NEW}
    ).scalar_one()
    assert role == "member"


def test_reassign_aborts_when_a_user_is_missing(pg_conn):
    # Only the old user exists; the new OAuth identity has not signed in yet.
    pg_conn.execute(
        text(
            "INSERT INTO frontend.users (email, password_hash, role) "
            "VALUES (:e, 'x', 'admin')"
        ),
        {"e": OLD},
    )
    with pytest.raises(ValueError):
        reassign(pg_conn, OLD, NEW, execute=True)
