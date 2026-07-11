from threading import Barrier

from agf.discovery.sweep import run_parallel_sweep
from agf.discovery.taxonomy import TaxonomyEntry
from agf.extract.errors import ErrorKind, ExtractError
from agf.workers.main import _summary_line
from tests.discovery.conftest import FakeExtractor


def _entries(n):
    return [
        TaxonomyEntry(f"kw{i}", "niche_term", "KEYWORD_UNORDERED", 2)
        for i in range(n)
    ]


def test_two_lane_sweep_reports_telemetry_without_changing_counters():
    started = Barrier(2)

    class _OnePage(FakeExtractor):
        async def search_page(self, **_kwargs):
            started.wait(timeout=1)
            return [], None, False

    stats = run_parallel_sweep(
        None,
        [_OnePage(), _OnePage()],
        entries=_entries(2),
        kw_delay=0,
        page_delay=0,
    )

    assert stats["keywords_swept"] == 2
    assert stats["serp_requests"] == 2
    assert stats["raw_ads"] == 0
    assert stats["candidates_new"] == 0
    assert stats["candidates_seen"] == 0
    assert stats["errors"] == 0
    assert stats["drops"] == {
        "no_link": 0,
        "social": 0,
        "non_app": 0,
        "no_store_id": 0,
        "funnel_captured": 0,
    }

    telemetry = stats["telemetry"]
    assert telemetry["pages_completed"] == stats["serp_requests"]
    assert telemetry["lane_pages"] == {0: 1, 1: 1}
    assert telemetry["lane_errors"] == {0: 0, 1: 0}
    assert telemetry["peak_ready_jobs"] == 2
    assert telemetry["request_seconds"] >= 0
    assert telemetry["queue_wait_seconds"] >= 0
    assert telemetry["persistence_seconds"] >= 0


def test_lane_error_telemetry_matches_existing_error_counter():
    started = Barrier(2)

    class _Healthy(FakeExtractor):
        async def search_page(self, **_kwargs):
            started.wait(timeout=1)
            return [], None, False

    class _Failing(FakeExtractor):
        async def search_page(self, **_kwargs):
            started.wait(timeout=1)
            raise ExtractError("proxy down", ErrorKind.TRANSIENT)

    stats = run_parallel_sweep(
        None,
        [_Healthy(), _Failing()],
        entries=_entries(2),
        kw_delay=0,
        page_delay=0,
    )

    assert stats["keywords_swept"] == 1
    assert stats["serp_requests"] == 1
    assert stats["errors"] == 1
    assert stats["telemetry"]["pages_completed"] == 1
    assert sum(stats["telemetry"]["lane_pages"].values()) == 1
    assert sum(stats["telemetry"]["lane_errors"].values()) == 1


def test_worker_summary_reports_sweep_throughput_telemetry():
    summary = _summary_line({
        "sweep": {
            "candidates_new": 2,
            "candidates_seen": 3,
            "keywords_swept": 4,
            "telemetry": {
                "pages_completed": 5,
                "request_seconds": 1.25,
                "queue_wait_seconds": 0.5,
                "persistence_seconds": 0.25,
                "lane_pages": {0: 3, 1: 2},
                "lane_errors": {0: 0, 1: 1},
                "peak_ready_jobs": 4,
            },
        },
        "triage": None,
        "enrich_candidates": [],
        "enriched": [],
    })

    assert "pages=5" in summary
    assert "request_s=1.25" in summary
    assert "queue_s=0.50" in summary
    assert "persist_s=0.25" in summary
    assert "lane_errors=1" in summary
