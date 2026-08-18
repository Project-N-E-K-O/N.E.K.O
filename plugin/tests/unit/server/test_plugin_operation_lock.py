from __future__ import annotations

import asyncio

import pytest

from plugin.server.application.plugins.operation_lock import serialized_plugin_operation


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_plugin_operation_lock_serializes_tasks_and_allows_reentry() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    observed: list[str] = []

    @serialized_plugin_operation
    async def nested() -> None:
        observed.append("nested")

    @serialized_plugin_operation
    async def first() -> None:
        observed.append("first")
        await nested()
        entered.set()
        await release.wait()

    @serialized_plugin_operation
    async def second() -> None:
        observed.append("second")

    first_task = asyncio.create_task(first())
    await entered.wait()
    second_task = asyncio.create_task(second())
    await asyncio.sleep(0)
    assert observed == ["first", "nested"]

    release.set()
    await asyncio.gather(first_task, second_task)
    assert observed == ["first", "nested", "second"]
