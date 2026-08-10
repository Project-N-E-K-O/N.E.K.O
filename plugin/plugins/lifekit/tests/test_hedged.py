from __future__ import annotations

import asyncio

import pytest

from plugin.plugins.lifekit._hedged import ordered_hedged_first


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
