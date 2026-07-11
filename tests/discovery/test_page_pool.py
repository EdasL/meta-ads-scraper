from threading import Event, Thread
from time import monotonic

from agf.discovery.page_pool import PageJob, PageOutcome, run_page_pool


def test_free_lane_claims_another_unassigned_job():
    slow_started = Event()
    release_slow = Event()
    slow_finished = Event()
    calls: list[tuple[int, str, float]] = []
    waits: list[bool] = []
    shared_saw_slow_lane_busy: list[bool] = []
    shared_ready_at = monotonic() + 0.05

    def handle(lane_id: int, job: PageJob) -> PageOutcome:
        calls.append((lane_id, job.key, monotonic()))
        if job.key == "slow":
            slow_started.set()
            waits.append(release_slow.wait(timeout=1))
            slow_finished.set()
        elif job.key == "lane-1-first":
            waits.append(slow_started.wait(timeout=1))
        elif job.key == "shared":
            shared_saw_slow_lane_busy.append(not slow_finished.is_set())
            release_slow.set()
        return PageOutcome()

    outcomes = run_page_pool(
        [
            PageJob("slow", {}, lane_id=0),
            PageJob("lane-1-first", {}, lane_id=1),
            PageJob("shared", {}, ready_at=shared_ready_at),
        ],
        lane_count=2,
        handler=handle,
    )

    assert waits == [True, True]
    assert [(lane_id, key) for lane_id, key, _started_at in calls] == [
        (0, "slow"),
        (1, "lane-1-first"),
        (1, "shared"),
    ]
    shared_started_at = next(
        started_at for _lane_id, key, started_at in calls if key == "shared"
    )
    assert shared_started_at >= shared_ready_at
    assert shared_saw_slow_lane_busy == [True]
    assert {job.key for job, _outcome in outcomes} == {
        "slow",
        "lane-1-first",
        "shared",
    }


def test_lane_affine_continuation_waits_until_ready_and_workers_terminate():
    calls: list[tuple[int, object, int, int | None, float]] = []
    continuation_ready_at: list[float] = []
    outcomes: list[tuple[PageJob, PageOutcome]] = []

    def handle(lane_id: int, job: PageJob) -> PageOutcome:
        calls.append((lane_id, job.cursor, job.depth, job.lane_id, monotonic()))
        if job.cursor is None:
            ready_at = monotonic() + 0.05
            continuation_ready_at.append(ready_at)
            return PageOutcome(cursor="page-2", ready_at=ready_at)
        return PageOutcome()

    def run_pool() -> None:
        outcomes.extend(
            run_page_pool(
                [PageJob("keyword", {"query": "shoes"}, lane_id=0)],
                lane_count=2,
                handler=handle,
            )
        )

    pool_thread = Thread(target=run_pool)
    pool_thread.start()
    pool_thread.join(timeout=1)

    assert not pool_thread.is_alive()
    assert len(calls) == 2
    assert calls[0][0] == calls[1][0]
    assert calls[0][1:4] == (None, 0, calls[0][0])
    assert calls[1][1:4] == ("page-2", 1, calls[0][0])
    assert calls[1][4] >= continuation_ready_at[0]
    assert len(outcomes) == 1


def test_lane_affine_continuation_cannot_be_stolen_by_free_lane():
    blocker_started = Event()
    release_blocker = Event()
    continuation_started = Event()
    continuation_ready_at: list[float] = []
    calls: list[tuple[int, str, object]] = []
    waits: list[bool] = []
    outcomes: list[tuple[PageJob, PageOutcome]] = []

    def handle(lane_id: int, job: PageJob) -> PageOutcome:
        calls.append((lane_id, job.key, job.cursor))
        if job.key == "chain" and job.cursor is None:
            ready_at = monotonic() + 0.05
            continuation_ready_at.append(ready_at)
            return PageOutcome(cursor="page-2", ready_at=ready_at)
        if job.key == "blocker":
            blocker_started.set()
            waits.append(release_blocker.wait(timeout=1))
        elif job.key == "chain":
            continuation_started.set()
        return PageOutcome()

    def run_pool() -> None:
        outcomes.extend(
            run_page_pool(
                [
                    PageJob("chain", {}, lane_id=0),
                    PageJob("blocker", {}, lane_id=0),
                ],
                lane_count=2,
                handler=handle,
            )
        )

    pool_thread = Thread(target=run_pool)
    pool_thread.start()
    assert blocker_started.wait(timeout=1)

    # Wait through the scheduler's known ready_at deadline. Lane 1 is idle, but
    # the continuation must remain queued for lane 0 while its blocker is active.
    wait_through_ready_at = max(0.0, continuation_ready_at[0] - monotonic()) + 0.02
    continuation_was_stolen = continuation_started.wait(timeout=wait_through_ready_at)
    release_blocker.set()
    pool_thread.join(timeout=1)

    assert not continuation_was_stolen
    assert not pool_thread.is_alive()
    assert waits == [True]
    assert calls == [
        (0, "chain", None),
        (0, "blocker", None),
        (0, "chain", "page-2"),
    ]
    assert len(outcomes) == 2


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
