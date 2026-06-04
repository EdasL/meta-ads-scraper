import asyncio
import os
import subprocess
import sys
from agf.discovery.run_discovery import run_discovery
from tests.discovery.conftest import FakeExtractor, make_ad, NOW_EPOCH


def test_run_discovery_counts_only_confirmed(pg_conn):
    # EU sweep: P1 (30d, kept by collapse) + P2 (5d, dropped)
    eu = [make_ad("e1", "P1", "GemA", "https://x.com", days_old=30),
          make_ad("e2", "P2", "GemB", "https://y.com", days_old=5)]
    # bridge/confirm for P1: a 20d active US ad linking to an app
    bridge_ad = make_ad("b1", "P1", "GemA",
                        "https://apps.apple.com/us/app/x/id111", days_old=20)
    ex = FakeExtractor(eu_ads=eu, page_ads={"P1": [bridge_ad]},
                       keyword_ads={"111": [bridge_ad]})
    res = asyncio.run(run_discovery(pg_conn, ex, NOW_EPOCH,
                                    eu_countries=("DE",), min_running_days=25))
    assert res["pages"] == 1                  # only P1 survived the ≥25d collapse
    assert res["confirmed"] == ["P1"]         # P1 confirmed scaling (20d active ≥14d)


def test_dry_run_cli():
    env = {**os.environ, "PYTHONPATH": "src"}
    out = subprocess.run([sys.executable, "-m", "agf.discovery.run_discovery", "--dry-run"],
                         capture_output=True, text=True, env=env)
    assert out.returncode == 0
    assert "scaling-confirm" in out.stdout
