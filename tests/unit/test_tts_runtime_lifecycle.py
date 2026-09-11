import asyncio

import pytest

from main_logic.core.tts_runtime import TtsRuntimeMixin


class _AliveThread:
    def is_alive(self):
        return True


class _Manager(TtsRuntimeMixin):
    pass


@pytest.mark.unit
@pytest.mark.asyncio
async def test_lazy_tts_restart_rebinds_handler_to_replacement_response_queue():
    old_handler_started = asyncio.Event()
    old_handler_cancelled = asyncio.Event()
    new_handler_started = asyncio.Event()
    old_response_queue = object()
    new_response_queue = object()

    async def old_handler():
        old_handler_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            old_handler_cancelled.set()

    manager = _Manager()
    manager.tts_thread = None
    manager.tts_handler_task = asyncio.create_task(old_handler())
    manager.tts_response_queue = old_response_queue
    manager._tts_excluded_provider_keys = frozenset()

    def start_tts_thread(*, preserve_provider_exclusions=False):
        assert preserve_provider_exclusions is False
        manager.tts_response_queue = new_response_queue
        manager.tts_thread = _AliveThread()

    async def tts_response_handler():
        assert manager.tts_response_queue is new_response_queue
        new_handler_started.set()
        await asyncio.Event().wait()

    manager._start_tts_thread = start_tts_thread
    manager.tts_response_handler = tts_response_handler

    await old_handler_started.wait()
    try:
        await manager.ensure_tts_pipeline_alive()
        await asyncio.wait_for(old_handler_cancelled.wait(), timeout=0.5)
        await asyncio.wait_for(new_handler_started.wait(), timeout=0.5)
    finally:
        manager.tts_handler_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await manager.tts_handler_task


@pytest.mark.unit
@pytest.mark.asyncio
async def test_live_tts_worker_keeps_current_handler():
    handler_cancelled = asyncio.Event()

    async def current_handler():
        try:
            await asyncio.Event().wait()
        finally:
            handler_cancelled.set()

    handler_task = asyncio.create_task(current_handler())
    manager = _Manager()
    manager.tts_thread = _AliveThread()
    manager.tts_handler_task = handler_task

    try:
        await manager.ensure_tts_pipeline_alive()
        await asyncio.sleep(0)
        assert manager.tts_handler_task is handler_task
        assert handler_cancelled.is_set() is False
    finally:
        handler_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await handler_task
