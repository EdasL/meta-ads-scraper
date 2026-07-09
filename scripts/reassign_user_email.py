"""reassign_user_email.py — one-off: move a founder account's history onto a new identity.

After the founder's first Google sign-in creates a fresh frontend.users row for his
Google email, this moves everything tied to the old founder@fielddesk.local identity
(role, api_keys, saved_gems, app_ratings, preference_scores) onto the new row.

Both user rows must already exist (the new one is created by the OAuth sign-in); the
script aborts otherwise. Conflict policy: when the new identity already has a row for
the same advertiser, the NEW row wins — the old user's row is dropped, not moved.

Dry-run by default (prints the per-table counts and rolls back). Pass --execute to
commit the move in a single transaction.

Run:
    PYTHONPATH=src AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf \\
    .venv/bin/python -m scripts.reassign_user_email OLD_EMAIL NEW_EMAIL [--execute]
"""
import argparse
import os
import sys

from sqlalchemy import text

from agf.db.engine import get_engine

# Tables keyed (user_email, advertiser_id): move rows, but drop the old user's row
# whenever the new user already has one for the same advertiser (PK conflict).
_MERGE_TABLES = ("saved_gems", "app_ratings", "preference_scores")


def reassign(conn, old_email, new_email, execute):
    """Perform the reassignment on `conn` inside a SAVEPOINT.

    Returns a dict of per-table row counts (rows that moved; for the merge tables
    this excludes conflicting old rows that were deleted). The savepoint is
    released when `execute` is True and rolled back otherwise, so a dry run leaves
    the connection's data untouched while still reporting accurate counts.

    Raises ValueError if either user row is missing.
    """
    for email, label in ((old_email, "old"), (new_email, "new")):
        exists = conn.execute(
            text("SELECT 1 FROM frontend.users WHERE email = :e"), {"e": email}
        ).first()
        if exists is None:
            raise ValueError(f"{label} user {email!r} not found in frontend.users")

    counts = {}
    sp = conn.begin_nested()  # SAVEPOINT — released on execute, rolled back on dry-run
    try:
        # 1. Carry the old row's privileges onto the new identity.
        conn.execute(
            text(
                "UPDATE frontend.users u SET role = o.role "
                "FROM frontend.users o WHERE u.email = :new AND o.email = :old"
            ),
            {"new": new_email, "old": old_email},
        )

        # 2. Move api_keys BEFORE the old users row is deleted (FK to users(email)).
        r = conn.execute(
            text(
                "UPDATE frontend.api_keys SET user_email = :new WHERE user_email = :old"
            ),
            {"new": new_email, "old": old_email},
        )
        counts["api_keys"] = r.rowcount

        # 3. Merge the (user_email, advertiser_id)-keyed tables: drop old rows that
        #    collide with an existing new row, then move the remainder.
        for tbl in _MERGE_TABLES:
            conn.execute(
                text(
                    f"DELETE FROM frontend.{tbl} o WHERE o.user_email = :old "
                    f"AND EXISTS (SELECT 1 FROM frontend.{tbl} n "
                    f"WHERE n.user_email = :new AND n.advertiser_id = o.advertiser_id)"
                ),
                {"old": old_email, "new": new_email},
            )
            r = conn.execute(
                text(
                    f"UPDATE frontend.{tbl} SET user_email = :new WHERE user_email = :old"
                ),
                {"new": new_email, "old": old_email},
            )
            counts[tbl] = r.rowcount

        # 4. Remove the now-empty old user row.
        r = conn.execute(
            text("DELETE FROM frontend.users WHERE email = :old"), {"old": old_email}
        )
        counts["users"] = r.rowcount

        if execute:
            sp.commit()
        else:
            sp.rollback()
    except Exception:
        sp.rollback()
        raise
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser(description="Move a user's history onto a new email identity.")
    ap.add_argument("old", help="existing account email whose history moves")
    ap.add_argument("new", help="new identity email (must already exist)")
    ap.add_argument(
        "--execute",
        action="store_true",
        help="commit the move (default: dry-run — print counts and roll back)",
    )
    args = ap.parse_args(argv)

    url = os.environ.get("AGF_DATABASE_URL") or os.environ.get("AGF_TEST_DB_URL")
    if not url:
        sys.exit("no database URL (set AGF_DATABASE_URL)")

    # One transaction: it commits only when reassign() released its savepoint under
    # --execute; a dry run rolled the savepoint back, so nothing persists.
    with get_engine(url).begin() as conn:
        try:
            counts = reassign(conn, args.old, args.new, execute=args.execute)
        except ValueError as e:
            sys.exit(f"abort: {e}")

    mode = "MOVED" if args.execute else "DRY-RUN (no changes written)"
    print(f"{mode}: {args.old} -> {args.new}")
    print(f"  users deleted:      {counts['users']}")
    print(f"  api_keys:           {counts['api_keys']}")
    print(f"  saved_gems:         {counts['saved_gems']}")
    print(f"  app_ratings:        {counts['app_ratings']}")
    print(f"  preference_scores:  {counts['preference_scores']}")
    if not args.execute:
        print("  (re-run with --execute to apply)")


if __name__ == "__main__":
    main()
