"""Operator one-shot: re-scrape EVERY advertiser's live Meta listing to refresh
GemScore v3 running-days (is_active/start_date/end_date), reusing
discovery.corpus_refresh.refresh_corpus. Loads the live worker's production env (proxy
+ MinIO + localhost DB) from its launchd plist so it mirrors the daemon exactly.

PAUSE the worker first (it shares the proxy):
    launchctl bootout gui/$(id -u)/com.fielddesk.worker

Then (from repo root):
    PYTHONPATH=src .venv/bin/python -m scripts.refresh_corpus_ads               # whole corpus
    PYTHONPATH=src .venv/bin/python -m scripts.refresh_corpus_ads --limit 10    # probe
    PYTHONPATH=src .venv/bin/python -m scripts.refresh_corpus_ads --offset 300  # resume

Restart the worker + rescore after:
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.fielddesk.worker.plist
    PYTHONPATH=src .venv/bin/python -c "from agf.db.engine import get_engine; \
      from agf.scoring.run_scoring import run_scoring; from datetime import date; \
      print('scored', run_scoring(get_engine(), date.today()))"
"""
import argparse
import asyncio
import os
import plistlib
import sys
import time

_PLIST = os.path.expanduser("~/Library/LaunchAgents/com.fielddesk.worker.plist")


def _load_plist_env() -> None:
    with open(_PLIST, "rb") as f:
        env = plistlib.load(f).get("EnvironmentVariables", {})
    for k, v in env.items():
        os.environ[k] = str(v)
    os.environ["AGF_DATABASE_URL"] = "postgresql://agf:agf@localhost:5433/agf"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--budget", type=int, default=12_000, help="max page requests")
    ap.add_argument("--hist-pages", type=int, default=12)
    args = ap.parse_args()

    _load_plist_env()
    from agf.db.engine import get_engine
    from agf.workers.main import build_extractor
    from agf.discovery.corpus_refresh import refresh_corpus

    now_epoch = int(time.time())
    extractor = build_extractor(False, now_epoch)
    stats = asyncio.run(refresh_corpus(
        get_engine(), extractor, hist_pages=args.hist_pages,
        budget_requests=args.budget, offset=args.offset, limit=args.limit))
    print(f"[refresh-corpus] {stats}", flush=True)


if __name__ == "__main__":
    main()
