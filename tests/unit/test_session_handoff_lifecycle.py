"""Behavior regressions for retiring one session without touching its successor.

Real manager end/cleanup logic, locks and queues run against event-controlled
external clients. No sleep duration is used to select the interleaving.
"""

import asyncio
from queue import Queue
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic.core import LLMSessionManager
from tests.unit.test_session_start_guard import _make_active_manager


class ControlledClient:
    def __init__(self):
        self.close_entered = asyncio.Event()
        self.allow_close = asyncio.Event()
        self.closed = asyncio.Event()
        self.close_count = 0

    async def close(self):
        self.close_count += 1
        self.close_entered.set()
        await self.allow_close.wait()
        self.closed.set()


def make_manager():
    manager = _make_active_manager()
    # Restore the real teardown; only the remote provider is controlled here.
    del manager._teardown_tts_runtime
    manager.tts_cache_lock = asyncio.Lock()
    manager.tts_ready = False
    manager.tts_pending_chunks = []
    manager.sync_message_queue = Queue()
    manager.lanlan_name = "handoff-test"
    manager.websocket = object()
    manager.send_status = AsyncMock()
    manager.session = ControlledClient()
    manager._activity_tracker = SimpleNamespace(on_voice_mode=lambda _enabled: None)
    manager._master_emotion = SimpleNamespace(reset=lambda: None)
    manager._focus_scorer = SimpleNamespace(reset=lambda: None)
    return manager


async def finish_end(task, client):
    client.allow_close.set()
    try:
        await asyncio.wait_for(asyncio.shield(task), 2.0)
    except asyncio.CancelledError:
        pass


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retired_connection_leaves_current_slot_before_slow_close():
    manager = make_manager()
    old = manager.session
    ending = asyncio.create_task(manager.end_session(by_server=True))
    try:
        await asyncio.wait_for(old.close_entered.wait(), 2.0)
        assert manager.session is not old, "a closing client must not win the next install CAS"
    finally:
        await finish_end(ending, old)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_end_target_is_captured_before_waiting_for_manager_lock():
    manager = make_manager()
    old = manager.session
    successor = ControlledClient()
    successor.allow_close.set()
    await manager.lock.acquire()
    ending = asyncio.create_task(manager.end_session(by_server=True))
    # Queue end_session on the held lock before installing the successor.
    await asyncio.sleep(0)
    manager.session = successor
    manager.is_active = True
    manager.lock.release()
    old.allow_close.set()
    await asyncio.wait_for(ending, 2.0)
    assert manager.session is successor
    assert successor.close_count == 0
    assert manager.is_active is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancelling_end_caller_does_not_abandon_owned_connection_close():
    manager = make_manager()
    old = manager.session
    ending = asyncio.create_task(manager.end_session(by_server=True))
    await asyncio.wait_for(old.close_entered.wait(), 2.0)
    ending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await ending
    old.allow_close.set()
    await asyncio.wait_for(old.closed.wait(), 2.0)
    assert old.close_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_repeated_end_reuses_retirement_and_preserves_single_memory_boundary():
    manager = make_manager()
    old = manager.session
    first = asyncio.create_task(manager.end_session(by_server=True))
    await asyncio.wait_for(old.close_entered.wait(), 2.0)
    second = asyncio.create_task(manager.end_session(by_server=True))
    try:
        old.allow_close.set()
        await asyncio.wait_for(asyncio.gather(first, second), 2.0)
        assert old.close_count == 1
        boundaries = list(manager.sync_message_queue.queue)
        assert boundaries == [{"type": "system", "data": "session end"}]
    finally:
        await finish_end(first, old)
        await finish_end(second, old)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_explicit_stale_provider_end_preserves_live_connection_and_input():
    manager = make_manager()
    live = manager.session
    pending = list(manager.pending_input_data)
    await manager.end_session(by_server=True, expected_session=ControlledClient())
    assert manager.session is live
    assert manager.pending_input_data == pending
    assert live.close_count == 0
    assert manager.sync_message_queue.empty()
