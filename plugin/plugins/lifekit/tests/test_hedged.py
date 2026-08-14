from __future__ import annotations

import asyncio

import pytest
from plugin.plugins.lifekit._hedged import ordered_hedged_first


@pytest.mark.asyncio
async def test_zero_hedge_delay_starts_each_attempt_without_busy_loop() -> None:
    started: list[int] = []

    async def attempt(index: int, value: str) -> str:
        started.append(index)
        await asyncio.sleep(0)
        return value

    outcome = await asyncio.wait_for(
        ordered_hedged_first(
            (lambda: attempt(0, ""), lambda: attempt(1, "winner")),
            accept=bool,
            hedge_delay=0,
            total_timeout=0.2,
        ),
        timeout=0.5,
    )

    assert started == [0, 1]
    assert outcome.winner == "winner"


@pytest.mark.asyncio
async def test_simultaneous_acceptable_results_preserve_provider_priority() -> None:
    release = asyncio.Event()

    async def result(value: str) -> str:
        await release.wait()
        return value

    task = asyncio.create_task(ordered_hedged_first(
        (lambda: result("primary"), lambda: result("fallback")),
        accept=lambda value: bool(value),
        hedge_delay=0.0,
        total_timeout=1.0,
    ))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    release.set()

    outcome = await task

    assert outcome.winner == "primary"
    assert outcome.winner_index == 0
