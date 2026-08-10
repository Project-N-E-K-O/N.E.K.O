"""Shared ordered hedging for external provider fallbacks."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class HedgedOutcome(Generic[T]):
    winner: T | None
    winner_index: int | None
    completed: tuple[tuple[int, T], ...]
    timed_out_indices: tuple[int, ...]


async def ordered_hedged_first(
    attempts: Sequence[Callable[[], Awaitable[T]]],
    *,
    accept: Callable[[T], bool],
    hedge_delay: float,
    total_timeout: float,
) -> HedgedOutcome[T]:
    """Preserve provider priority while hedging a slow attempt after a delay."""
    if not attempts:
        return HedgedOutcome(None, None, (), ())

    hedge_delay = max(0.0, hedge_delay)
    tasks: dict[asyncio.Task[T], int] = {}
    pending: set[asyncio.Task[T]] = set()
    completed: list[tuple[int, T]] = []
    next_index = 0

    def start_next() -> bool:
        nonlocal next_index
        if next_index >= len(attempts):
            return False
        index = next_index
        next_index += 1
        task = asyncio.create_task(attempts[index]())
        tasks[task] = index
        pending.add(task)
        return True

    start_next()
    deadline = asyncio.get_running_loop().time() + total_timeout
    winner: T | None = None
    winner_index: int | None = None
    timed_out_indices: tuple[int, ...] = ()
    try:
        while pending:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            has_more_attempts = next_index < len(attempts)
            wait_timeout = (
                min(remaining, hedge_delay)
                if has_more_attempts
                else remaining
            )
            done, pending = await asyncio.wait(
                pending,
                timeout=wait_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                start_next()
                continue
            for task in sorted(done, key=tasks.__getitem__):
                index = tasks[task]
                value = task.result()
                completed.append((index, value))
                if accept(value):
                    winner = value
                    winner_index = index
                    return HedgedOutcome(
                        winner,
                        winner_index,
                        tuple(sorted(completed)),
                        (),
                    )
            if not pending:
                start_next()

        timed_out_indices = tuple(sorted(tasks[task] for task in pending))
        return HedgedOutcome(
            winner,
            winner_index,
            tuple(sorted(completed)),
            timed_out_indices,
        )
    finally:
        for task in pending:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
