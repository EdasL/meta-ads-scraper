import os
import re
import subprocess
from pathlib import Path

CRON = Path(__file__).resolve().parents[2] / "ops" / "cron"
SCRIPTS = ["snapshot.sh", "refresh_matview.sh", "prune.sh", "backup.sh"]


def test_scripts_exist_executable_and_valid_bash():
    for s in SCRIPTS:
        p = CRON / s
        assert p.exists(), p
        assert os.access(p, os.X_OK), f"{s} not executable"
        assert subprocess.run(["bash", "-n", str(p)]).returncode == 0, f"{s} syntax"
        body = p.read_text()
        assert body.startswith("#!/usr/bin/env bash") or body.startswith("#!/bin/bash")
        assert "set -euo pipefail" in body


def test_refresh_uses_concurrently():
    assert "REFRESH MATERIALIZED VIEW CONCURRENTLY" in (CRON / "refresh_matview.sh").read_text()


def test_prune_bounds_retention():
    assert re.search(r"365|interval", (CRON / "prune.sh").read_text(), re.IGNORECASE)


def test_crontab_lines_valid():
    lines = [l for l in (CRON / "crontab").read_text().splitlines()
             if l.strip() and not l.strip().startswith("#")]
    assert lines and all(len(l.split()) >= 6 for l in lines)   # 5 time fields + command
