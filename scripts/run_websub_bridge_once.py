"""One-shot driver: bridge confirmed web_sub funnels into the advertiser pipeline
against the LIVE DB. Verification helper for the web-sub feed-surfacing feature
(docs/superpowers/plans/2026-06-23-websub-feed-surfacing.md, Task 12)."""
import asyncio
import os
import sys

import httpx

from agf.db.engine import get_engine
from agf.extract.facade import Extractor
from agf.discovery.websub_bridge import run_websub_bridge


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 25
    engine = get_engine()
    extractor = Extractor()
    client = httpx.Client(timeout=20.0, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0 (FieldDesk websub-bridge)"})

    def fetch_text(url):
        return client.get(url).text

    stats = asyncio.run(run_websub_bridge(engine, extractor,
                                          fetch_text=fetch_text, limit=limit))
    client.close()
    print("websub bridge stats:", stats, flush=True)


if __name__ == "__main__":
    main()
