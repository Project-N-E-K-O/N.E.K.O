"""Repeated end requests share the retirement accepted for their target."""

import asyncio

import pytest

from main_logic import cross_server
from tests.unit.test_session_handoff_lifecycle import make_manager


@pytest.mark.unit
@pytest.mark.asyncio
async def test_duplicate_memory_end_during_input_lock_wait_runs_callback_once():
    manager = make_manager()
    client = manager.session
    client.allow_close.set()
    calls = []

    async def callback():
        calls.append("settled")

    await manager.input_cache_lock.acquire()
    first = manager.request_end_session(by_server=True, after_memory_settlement=callback)
    tasks = [first]
    consumer = None

    async def consume():
        while True:
            message = await asyncio.to_thread(manager.sync_message_queue.get)
            if message is None:
                return
            if "_memory_settlement_done" in message:
                await cross_server._complete_session_end_memory_barrier(message, manager.lanlan_name)

    try:
        await asyncio.wait_for(client.close_entered.wait(), 1)
        assert manager.session is None
        assert manager._session_retirements[0].memory_completion is None
        second = manager.request_end_session(by_server=True, after_memory_settlement=callback)
        tasks.append(second)
        manager.input_cache_lock.release()
        consumer = asyncio.create_task(consume())
        await asyncio.wait_for(asyncio.gather(*tasks), 2)
        assert calls == ["settled"]
        assert second is first
    finally:
        if manager.input_cache_lock.locked():
            manager.input_cache_lock.release()
        if consumer is None:
            consumer = asyncio.create_task(consume())
        await asyncio.gather(*tasks, return_exceptions=True)
        manager.sync_message_queue.put(None)
        await consumer
