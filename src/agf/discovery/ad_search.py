"""Bounded Meta scraping primitives. Interpretation belongs to the calling agent."""

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

from sqlalchemy import text

from agf.db.ad_searches import load_search, save_ads, update_search
from agf.db.engine import begin
from agf.extract.facade import Extractor


class ScrapeRun:
    def __init__(self, engine, search_id, *, extractor_factory=None, budget_seconds=90):
        self.engine, self.id = engine, search_id
        with begin(engine) as conn:
            row = load_search(conn, search_id)
        if row is None:
            raise ValueError("scrape not found")
        self.request = row["request"]
        self.factory = extractor_factory or (lambda: Extractor(max_attempts=2))
        self.start = time.monotonic()
        self.deadline = self.start + budget_seconds
        self.lock = threading.RLock()
        self.local = threading.local()
        self.progress = {
            "phase": "scraping",
            "sources": {},
            "partial": False,
            "ads_found": 0,
            "first_result_seconds": None,
        }

    def source(self, value):
        req = self.request
        operation = req["operation"]
        country = req["country"]
        key = ("page:" if operation == "page" else "query:") + value
        report = {
            "status": "running",
            "query": value if operation != "page" else "",
            "page_id": value if operation == "page" else "",
            "country": country,
            "ads": 0,
            "has_more": False,
        }
        try:
            if time.monotonic() >= self.deadline:
                report["status"] = "budget_exceeded"
                return report
            if not hasattr(self.local, "extractor"):
                self.local.extractor = self.factory()
            extractor = self.local.extractor
            if operation == "resolve":
                lookup_country = "US" if country == "ALL" else country
                pages = asyncio.run(
                    extractor.typeahead(query=value, country=lookup_country)
                )
                report["lookup_country"] = lookup_country
                report["advertisers"] = [
                    {
                        k: p.get(k)
                        for k in (
                            "page_id",
                            "page_name",
                            "page_like_count",
                            "ig_username",
                        )
                    }
                    for p in pages[:20]
                    if p.get("page_id")
                ]
                report["status"] = "done"
            else:
                kwargs = {
                    "countries": [country],
                    "active_status": "ACTIVE",
                    "cursor": req.get("cursor") or None,
                }
                if operation == "page":
                    kwargs.update(view_all_page_id=value, search_type="page")
                else:
                    kwargs.update(
                        query=value,
                        search_type="KEYWORD_EXACT_PHRASE"
                        if req.get("match_mode") == "exact"
                        else "KEYWORD_UNORDERED",
                    )
                ads, cursor, more = asyncio.run(extractor.search_page(**kwargs))
                # Preserve provider order and every active ad, including sparse
                # creatives. No model, category, app-store or brand relevance gate.
                ads = list(
                    {
                        a.ad_archive_id: a
                        for a in ads
                        if a.ad_archive_id and a.page_id and a.is_active
                    }.values()
                )
                with self.lock:
                    with begin(self.engine) as conn:
                        save_ads(conn, self.id, ads, source_term=key, country=country)
                        self.progress["ads_found"] = conn.execute(
                            text(
                                "SELECT count(*) FROM frontend.ad_search_results WHERE search_id=:id"
                            ),
                            {"id": self.id},
                        ).scalar_one()
                    report.update(
                        status="done",
                        ads=len(ads),
                        ad_ids=[a.ad_archive_id for a in ads],
                        has_more=bool(more and cursor and cursor != req.get("cursor")),
                        cursor=cursor if more and cursor != req.get("cursor") else None,
                    )
                    if ads and self.progress["first_result_seconds"] is None:
                        self.progress["first_result_seconds"] = round(
                            time.monotonic() - self.start, 2
                        )
        except Exception as exc:  # noqa: BLE001 — isolate failures; never expose credential-bearing messages
            # Provider exceptions may contain proxy credentials.
            report.update(status="fetch_failed", error_type=type(exc).__name__)
        finally:
            report["observed_at"] = datetime.now(UTC).isoformat()
            with self.lock:
                self.progress["sources"][key] = report
                self.progress["elapsed_seconds"] = round(
                    time.monotonic() - self.start, 2
                )
                with begin(self.engine) as conn:
                    update_search(conn, self.id, progress=self.progress)
        return report

    def run(self):
        if self.request.get("operation") not in ("resolve", "keyword", "page"):
            with begin(self.engine) as conn:
                update_search(
                    conn, self.id, status="error", error="unsupported_scrape_operation"
                )
            return "error"
        with begin(self.engine) as conn:
            update_search(conn, self.id, status="running", progress=self.progress)
        values = (
            [self.request["page_id"]]
            if self.request["operation"] == "page"
            else self.request["queries"]
        )
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(self.source, value) for value in values]
            outcomes = [f.result() for f in as_completed(futures)]
        failures = sum(o["status"] != "done" for o in outcomes)
        status = "error" if failures == len(outcomes) else "done"
        self.progress.update(
            phase="complete" if status == "done" else "failed",
            partial=bool(failures),
            elapsed_seconds=round(time.monotonic() - self.start, 2),
        )
        with begin(self.engine) as conn:
            update_search(
                conn,
                self.id,
                status=status,
                progress=self.progress,
                error="retrieval_failed" if status == "error" else None,
            )
        return status


def run_ad_search(engine, search_id, **kwargs):
    return ScrapeRun(engine, search_id, **kwargs).run()
