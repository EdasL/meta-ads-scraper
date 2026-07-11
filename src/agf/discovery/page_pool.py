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
) -> list[TerminalOutcome]:
    """Process page chains dynamically while keeping continuations lane-affine.

    ``ready_at`` values are absolute ``time.monotonic()`` timestamps. Unassigned
    jobs can be claimed by any free lane. A live outcome cursor creates the next
    page on the same lane; a missing cursor or failed outcome terminates the chain.
    Handler exceptions are captured as failed terminal outcomes.
    """
    if lane_count < 1:
        raise ValueError("lane_count must be at least 1")

    sequence = itertools.count()
    unassigned: list[tuple[float, int, PageJob]] = []
    ready_by_lane: list[list[tuple[float, int, PageJob]]] = [
        [] for _ in range(lane_count)
    ]
    terminal: list[TerminalOutcome] = []
    condition = threading.Condition()
    remaining = 0

    for job in jobs:
        if job.lane_id is None:
            heapq.heappush(unassigned, (job.ready_at, next(sequence), job))
        else:
            if not 0 <= job.lane_id < lane_count:
                raise ValueError(f"invalid lane_id {job.lane_id}")
            heapq.heappush(
                ready_by_lane[job.lane_id],
                (job.ready_at, next(sequence), job),
            )
        remaining += 1

    def claim(lane_id: int) -> PageJob | None:
        nonlocal remaining
        lane_ready = ready_by_lane[lane_id]
        with condition:
            while remaining:
                now = time.monotonic()
                if lane_ready and lane_ready[0][0] <= now:
                    return heapq.heappop(lane_ready)[2]
                if unassigned and unassigned[0][0] <= now:
                    job = heapq.heappop(unassigned)[2]
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
        nonlocal remaining
        while (job := claim(lane_id)) is not None:
            try:
                outcome = handler(lane_id, job)
                if not isinstance(outcome, PageOutcome):
                    raise TypeError("page handler must return PageOutcome")
            except Exception as error:
                outcome = PageOutcome(failed=True, error=error)

            with condition:
                if outcome.failed or outcome.cursor is None:
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
                        (continuation.ready_at, next(sequence), continuation),
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
