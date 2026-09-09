"""An in-flight worker notification is a producer owned by its runtime."""

import asyncio
from queue import Queue
from types import SimpleNamespace

import pytest

from main_logic.core import LLMSessionManager
from tests.unit.test_session_notification_delivery import Socket


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tts_handoff_drains_warning_send_already_in_flight():
    manager = LLMSessionManager.__new__(LLMSessionManager)
    manager.websocket = Socket(block=True)
    manager.sync_message_queue = Queue()
    manager._bg_tasks = set()
    manager.tts_thread = SimpleNamespace(is_alive=lambda: False)
    manager.tts_request_queue = Queue()
    manager.tts_response_queue = Queue()
    manager.tts_handler_task = None
    runtime = manager._snapshot_tts_runtime()
    handler = asyncio.create_task(manager.tts_response_handler())
    runtime.handler = manager.tts_handler_task = handler
    manager.tts_response_queue.put(("__warning__", '{"code":"TTS_RECONNECTING"}'))
    try:
        await asyncio.wait_for(manager.websocket.entered.wait(), 2)
        manager._retire_tts_runtime(runtime)
        await asyncio.wait_for(runtime.handoff_safe.wait(), 2)
        assert all(task.done() for task in manager._bg_tasks), (
            "handoff cannot be safe while an old worker can still send a warning"
        )
    finally:
        manager.websocket.release.set()
        manager._retire_tts_runtime(runtime)
        await asyncio.gather(handler, *manager._bg_tasks, return_exceptions=True)
        if runtime.cleanup_task:
            await runtime.cleanup_task
