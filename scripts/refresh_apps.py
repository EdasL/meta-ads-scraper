"""Refresh app-store signals for linked apps: metadata (icon / screenshots / price /
installs / ratings), today's adtech.app_metrics snapshot row, and the ~20 most-recent
store reviews (public.app_reviews). Apple/Google endpoints only — no Meta traffic,
no proxy needed. Idempotent per day (all writes upsert on their PK).

Default scope: apps missing today's adtech.app_metrics snapshot (cron-safe).

    export AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf
    PYTHONPATH=src .venv/bin/python scripts/refresh_apps.py            # due today
    PYTHONPATH=src .venv/bin/python scripts/refresh_apps.py --ids 141,77
    PYTHONPATH=src .venv/bin/python scripts/refresh_apps.py --all
"""
import argparse
import os
import sys

from agf.db.engine import get_engine
from agf.enrich_full.app_refresh import apps_due_for_refresh, refresh_apps


def _db_url() -> str:
    url = os.environ.get("AGF_DATABASE_URL") or os.environ.get("AGF_TEST_DB_URL")
    if not url:
        sys.exit("set AGF_DATABASE_URL (or AGF_TEST_DB_URL), e.g. "
                 "postgresql://agf:agf@localhost:5433/agf")
    return url


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ids", help="comma-separated public.apps ids to refresh")
    ap.add_argument("--all", action="store_true",
                    help="refresh every app (default: only apps missing today's snapshot)")
    ap.add_argument("--pause", type=float, default=3.0,
                    help="seconds between store calls (default 3.0)")
    args = ap.parse_args()

    engine = get_engine(_db_url())
    if args.ids:
        only_ids = [int(x) for x in args.ids.split(",") if x.strip()]
    elif args.all:
        only_ids = None
    else:
        with engine.connect() as conn:
            only_ids = apps_due_for_refresh(conn)
        if not only_ids:
            print("refresh_apps: nothing due (all apps have today's snapshot).")
            return
    summary = refresh_apps(engine, only_ids=only_ids, pause=args.pause)
    print(f"refresh_apps: apps={summary['apps']} metrics={summary['metrics']} "
          f"reviews={summary['reviews']}")


if __name__ == "__main__":
    main()
