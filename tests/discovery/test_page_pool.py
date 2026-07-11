from threading import Event

from agf.discovery.page_pool import PageJob, PageOutcome, run_page_pool


def test_free_lane_claims_another_unassigned_job():
    slow_started = Event()
    release_slow = Event()
    calls: list[tuple[int, str]] = []

    def handle(lane_id: int, job: PageJob) -> PageOutcome:
        calls.append((lane_id, job.key))
        if job.key == "slow":
            slow_started.set()
            assert release_slow.wait(timeout=1)
        elif job.key == "second":
            assert slow_started.wait(timeout=1)
            release_slow.set()
        return PageOutcome()

    outcomes = run_page_pool(
        [
            PageJob("slow", {}, lane_id=0),
            PageJob("first", {}),
            PageJob("second", {}),
        ],
        lane_count=2,
        handler=handle,
    )

    assert calls.index((1, "first")) < calls.index((1, "second"))
    assert (0, "second") not in calls
    assert {job.key for job, _outcome in outcomes} == {"slow", "first", "second"}


def test_continuation_pages_stay_on_the_claiming_lane():
    calls: list[tuple[int, object, int, int | None]] = []

    def handle(lane_id: int, job: PageJob) -> PageOutcome:
        calls.append((lane_id, job.cursor, job.depth, job.lane_id))
        if job.cursor is None:
            return PageOutcome(cursor="page-2")
        return PageOutcome()

    run_page_pool(
        [PageJob("keyword", {"query": "shoes"})],
        lane_count=2,
        handler=handle,
    )

    assert len(calls) == 2
    assert calls[0][0] == calls[1][0]
    assert calls[0][1:] == (None, 0, calls[0][0])
    assert calls[1][1:] == ("page-2", 1, calls[0][0])


def test_page_is_requeued_only_while_cursor_remains_live():
    calls: list[tuple[str, object]] = []

    def handle(_lane_id: int, job: PageJob) -> PageOutcome:
        calls.append((job.key, job.cursor))
        if job.key == "live" and job.cursor is None:
            return PageOutcome(cursor="next")
        return PageOutcome(cursor=None)

    outcomes = run_page_pool(
        [PageJob("live", {}), PageJob("done", {})],
        lane_count=1,
        handler=handle,
    )

    assert calls == [("live", None), ("live", "next"), ("done", None)]
    assert len(outcomes) == 2
    assert all(outcome.cursor is None for _job, outcome in outcomes)
