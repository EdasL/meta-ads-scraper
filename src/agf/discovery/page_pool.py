"""Generic page scheduler with one serial worker per proxy lane."""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass, replace
import heapq
import itertools
import threading
import time
from typing import Any


@dataclass(frozen=True, slots=True)
class PageJob:
    """One independently schedulable page request."""

    key: Hashable
    payload: Any
    cursor: Any = None
    depth: int = 0
    ready_at: float = 0.0
    lane_id: int | None = None


@dataclass(frozen=True, slots=True)
class PageOutcome:
    """Handler decision for the page that just finished."""

    cursor: Any = None
    ready_at: float = 0.0
    failed: bool = False
    error: Exception | None = None


PageHandler = Callable[[int, PageJob], PageOutcome]
TerminalOutcome = tuple[PageJob, PageOutcome]


def run_page_pool(
    jobs: Iterable[PageJob],
    *,
    lane_count: int,
    handler: PageHandler,
    telemetry: dict | None = None,
) -> list[TerminalOutcome]:
    """Process page chains dynamically while keeping continuations lane-affine.

    ``ready_at`` values are absolute ``time.monotonic()`` timestamps. Unassigned
    jobs can be claimed by any free lane. A live outcome cursor creates the next
    page on the same lane; a missing cursor or failed outcome terminates the chain.
    Handler exceptions are captured as failed terminal outcomes and cancel the pool.
    When supplied, ``telemetry`` is updated with aggregate queue and lane metrics.
    """
    if lane_count < 1:
        raise ValueError("lane_count must be at least 1")

    sequence = itertools.count()
    unassigned: list[tuple[float, int, float, PageJob]] = []
    ready_by_lane: list[list[tuple[float, int, float, PageJob]]] = [
        [] for _ in range(lane_count)
    ]
    terminal: list[TerminalOutcome] = []
    condition = threading.Condition()
    remaining = 0
    cancelled = False

    if telemetry is not None:
        telemetry.setdefault("pages_completed", 0)
        telemetry.setdefault("queue_wait_seconds", 0.0)
        telemetry.setdefault("lane_pages", {})
        telemetry.setdefault("lane_errors", {})
        telemetry.setdefault("peak_ready_jobs", 0)
        for lane_id in range(lane_count):
            telemetry["lane_pages"].setdefault(lane_id, 0)
            telemetry["lane_errors"].setdefault(lane_id, 0)

    for job in jobs:
        queued_at = time.perf_counter()
        if job.lane_id is None:
            heapq.heappush(
                unassigned,
                (job.ready_at, next(sequence), queued_at, job),
            )
        else:
            if not 0 <= job.lane_id < lane_count:
                raise ValueError(f"invalid lane_id {job.lane_id}")
            heapq.heappush(
                ready_by_lane[job.lane_id],
                (job.ready_at, next(sequence), queued_at, job),
            )
        remaining += 1
    if telemetry is not None:
        telemetry["peak_ready_jobs"] = max(
            telemetry["peak_ready_jobs"],
            remaining,
        )

    def pop_job(queue) -> PageJob:
        _ready_at, _sequence, queued_at, job = heapq.heappop(queue)
        if telemetry is not None:
            telemetry["queue_wait_seconds"] += time.perf_counter() - queued_at
        return job

    def claim(lane_id: int) -> PageJob | None:
        nonlocal remaining
        lane_ready = ready_by_lane[lane_id]
        with condition:
            while remaining and not cancelled:
                now = time.monotonic()
                if lane_ready and lane_ready[0][0] <= now:
                    return pop_job(lane_ready)
                if unassigned and unassigned[0][0] <= now:
                    job = pop_job(unassigned)
                    return replace(job, lane_id=lane_id)

                wake_at = min(
                    (
                        queue[0][0]
                        for queue in (lane_ready, unassigned)
                        if queue
                    ),
                    default=None,
                )
                timeout = None if wake_at is None else max(0.0, wake_at - now)
                condition.wait(timeout=timeout)
            return None

    def worker(lane_id: int) -> None:
        nonlocal cancelled, remaining
        while (job := claim(lane_id)) is not None:
            try:
                outcome = handler(lane_id, job)
                if not isinstance(outcome, PageOutcome):
                    raise TypeError("page handler must return PageOutcome")
            except Exception as error:
                with condition:
                    if telemetry is not None:
                        telemetry["lane_errors"][lane_id] += 1
                    terminal.append(
                        (job, PageOutcome(failed=True, error=error))
                    )
                    cancelled = True
                    remaining = 0
                    condition.notify_all()
                return

            with condition:
                if cancelled:
                    return
                if outcome.failed:
                    if telemetry is not None:
                        telemetry["lane_errors"][lane_id] += 1
                    terminal.append((job, outcome))
                    remaining -= 1
                else:
                    if telemetry is not None:
                        telemetry["pages_completed"] += 1
                        telemetry["lane_pages"][lane_id] += 1
                    if outcome.cursor is None:
                        terminal.append((job, outcome))
                        remaining -= 1
                    else:
                        continuation = replace(
                            job,
                            cursor=outcome.cursor,
                            depth=job.depth + 1,
                            ready_at=outcome.ready_at,
                            lane_id=lane_id,
                        )
                        heapq.heappush(
                            ready_by_lane[lane_id],
                            (
                                continuation.ready_at,
                                next(sequence),
                                time.perf_counter(),
                                continuation,
                            ),
                        )
                        if telemetry is not None:
                            ready_jobs = len(unassigned) + sum(
                                len(queue) for queue in ready_by_lane
                            )
                            telemetry["peak_ready_jobs"] = max(
                                telemetry["peak_ready_jobs"],
                                ready_jobs,
                            )
                condition.notify_all()

    workers = [
        threading.Thread(target=worker, args=(lane_id,))
        for lane_id in range(lane_count)
    ]
    for thread in workers:
        thread.start()
    for thread in workers:
        thread.join()

    return terminal
