from __future__ import annotations

import asyncio
import threading

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


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_plugin_operation_lock_waits_for_thread_work_after_cancellation() -> None:
    thread_started = threading.Event()
    release_thread = threading.Event()
    observed: list[str] = []

    def _blocking_work() -> None:
        thread_started.set()
        release_thread.wait(timeout=2)

    @serialized_plugin_operation
    async def blocked() -> None:
        await asyncio.to_thread(_blocking_work)

    @serialized_plugin_operation
    async def second() -> None:
        observed.append("second")

    first_task = asyncio.create_task(blocked())
    await asyncio.to_thread(thread_started.wait)
    first_task.cancel()
    await asyncio.sleep(0)
    second_task = asyncio.create_task(second())
    await asyncio.sleep(0)
    assert observed == []

    release_thread.set()
    with pytest.raises(asyncio.CancelledError):
        await first_task
    await second_task
    assert observed == ["second"]
