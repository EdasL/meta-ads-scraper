#!/usr/bin/env bash
# ops/cron/snapshot.sh — daily re-score: build features → GemScore → vetoes → write
# adtech.advertiser_scores → REFRESH the matview (Plan 5 run_scoring). Inline python
# form avoids editing the Plan-5 module just to add a __main__.
set -euo pipefail
: "${AGF_DATABASE_URL:?set AGF_DATABASE_URL}"
cd "$(dirname "$0")/../.."
PY="${PY:-.venv/bin/python}"
PYTHONPATH=src "$PY" -c "from datetime import date; \
from agf.db.engine import get_engine; \
from agf.scoring.run_scoring import run_scoring; \
print('scored', run_scoring(get_engine(), date.today()), 'advertisers')"
