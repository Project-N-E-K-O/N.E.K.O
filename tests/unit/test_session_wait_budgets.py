"""Public end waits and runtime recovery have bounded, separate budgets."""
import asyncio
from threading import Event
from unittest.mock import AsyncMock

import pytest

from main_logic import core as core_module
from main_logic.core import lifecycle, session_lifecycle
from tests.unit.session_handoff_harness import ProviderClient, drain_manager, make_full_manager
from tests.unit.test_session_handoff_lifecycle import make_manager


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def _start_connected(manager, created):
    task = asyncio.create_task(manager.start_session(manager.websocket, request_id='audit'))
    client = await asyncio.wait_for(created.get(), 3)
    client.allow_connect.set()
    await asyncio.wait_for(task, 3)
    assert manager.is_active and manager.tts_ready
    assert manager._start_operation.finished.is_set()
    return task, client


@pytest.mark.asyncio
@pytest.mark.parametrize('expired,occupied', [(False, True), (True, True), (True, False)])
async def test_runtime_fallback_real_elapsed_deadline(monkeypatch, expired, occupied):
    manager, created, clients = await make_full_manager(monkeypatch)
    manager._config_manager.core['DISABLE_TTS'] = False
    monkeypatch.setattr(manager, '_resolve_session_use_tts', lambda *args: True)
    loop = asyncio.get_running_loop()
    release_old = Event()
    replaced = asyncio.Event()
    launches = []

    def configured(requests, responses, *_):
        first = not launches
        launches.append(first)
        responses.put(('__ready__', True))
        while requests.get()[0] != '__shutdown__':
            pass
        if first:
            release_old.wait()

    def replacement(requests, responses, *_):
        responses.put(('__ready__', True))
        loop.call_soon_threadsafe(replaced.set)
        while requests.get()[0] != '__shutdown__':
            pass

    def select_worker(**kwargs):
        return (replacement, 'test', 'qwen') if 'custom' in kwargs.get('excluded_provider_keys', ()) else (configured, 'test', 'custom')

    monkeypatch.setattr(lifecycle._core_facade, 'get_tts_worker', select_worker)
    tasks = []
    try:
        first, _ = await _start_connected(manager, created)
        tasks.append(first)
        old_runtime = manager._tts_runtime
        retirement = manager.request_end_session(by_server=True)
        tasks.append(retirement)
        await asyncio.wait_for(manager._session_retirements[-1].handoff_safe.wait(), 3)
        assert old_runtime.retired and old_runtime.thread.is_alive()
        second, _ = await _start_connected(manager, created)
        tasks.append(second)
        operation = manager._start_operation
        original_deadline = operation.deadline
        assert manager._live_tts_runtime_count() == 2
        if not occupied:
            release_old.set()
            await asyncio.wait_for(asyncio.shield(retirement), 3)
            assert manager._live_tts_runtime_count() == 1
        if expired:
            # Let the unmodified, default 15-second startup budget really expire.
            reached = asyncio.Event()
            loop.call_at(original_deadline + 0.05, reached.set)
            await asyncio.wait_for(reached.wait(), 17)
        assert operation.deadline == original_deadline
        assert (loop.time() > original_deadline) == expired
        current = manager._tts_runtime
        current.response_queue.put(('__error__', 'controlled provider failure'))
        async with asyncio.timeout(3):
            while current.fallback_task is None and not manager.tts_handler_task.done() and not replaced.is_set():
                await asyncio.sleep(0)
        release_old.set()
        try:
            await asyncio.wait_for(replaced.wait(), 2)
        except TimeoutError:
            pytest.fail(f'Expired={expired}, occupied={occupied}: fallback absent; handler_done={manager.tts_handler_task.done()}, capacity_exhausted={manager._tts_capacity_exhausted}')
    finally:
        release_old.set()
        await asyncio.wait_for(drain_manager(manager, clients, *tasks), 5)
        await asyncio.wait_for(asyncio.gather(*manager._tts_cleanup_tasks), 3)


@pytest.mark.parametrize('safe', [False, True])
async def test_end_timeout_retains_owned_cleanup_and_never_reports_unsafe_success(monkeypatch, safe):
    manager = make_manager()
    manager._init_session_lifecycle_state()
    client = manager.session
    monkeypatch.setattr(session_lifecycle, 'FRONTEND_START_SESSION_TIMEOUT_SECONDS', 0.05)
    if not safe:
        await manager.lock.acquire()
    ending = asyncio.create_task(manager.end_session(by_server=True))
    try:
        async with asyncio.timeout(2):
            while not manager._session_retirements:
                await asyncio.sleep(0)
        retirement = manager._session_retirements[-1]
        if safe:
            await asyncio.wait_for(client.close_entered.wait(), 2)
            await asyncio.wait_for(ending, 2)
        else:
            with pytest.raises(TimeoutError, match='safe handoff'):
                await asyncio.wait_for(ending, 2)
        assert retirement.handoff_safe.is_set() == safe
        assert not retirement.task.done()
        assert not retirement.cleanup_complete.is_set()
        assert manager._connection_records[0].session is client
        assert not manager._connection_records[0].closed
    finally:
        if not safe:
            manager.lock.release()
        client.allow_close.set()
        await asyncio.wait_for(asyncio.gather(*manager._session_cleanup_tasks), 2)
        await asyncio.gather(ending, return_exceptions=True)
    assert client.closed.is_set()
    assert retirement.cleanup_complete.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize('slow_worker', [False, True])
async def test_full_audio_to_text_start(monkeypatch, slow_worker):
    manager, created, clients = await make_full_manager(monkeypatch)
    manager._config_manager.core['DISABLE_TTS'] = False
    monkeypatch.setattr(manager, '_resolve_session_use_tts', lambda *args: True)
    manager._init_renew_status = AsyncMock()
    release_old = Event()
    launches = []

    def configured(requests, responses, *_):
        first = not launches
        launches.append(first)
        responses.put(('__ready__', True))
        while requests.get()[0] != '__shutdown__':
            pass
        if first:
            release_old.wait()

    monkeypatch.setattr(lifecycle._core_facade, 'get_tts_worker', lambda **kwargs: (configured, 'test', 'custom'))
    offline_created = asyncio.Event()

    class OfflineService(ProviderClient, core_module.OmniOfflineClient):
        def __init__(self, **kwargs):
            ProviderClient.__init__(self, **kwargs)
            self._pending_images = []
            clients.append(self)
            self.allow_connect.set()
            offline_created.set()

        def update_max_response_length(self, *args, **kwargs):
            pass

    monkeypatch.setattr(lifecycle, 'OmniOfflineClient', OfflineService)
    tasks = []
    blocked = False
    try:
        starting, client = await _start_connected(manager, created)
        tasks.append(starting)
        runtime = manager._tts_runtime
        generation = manager._session_generation
        handoff = asyncio.create_task(manager._rebuild_offline_session_for_text_input('text'))
        tasks.append(handoff)
        async with asyncio.timeout(3):
            while not manager._session_retirements:
                await asyncio.sleep(0)
            await manager._session_retirements[-1].handoff_safe.wait()
        assert client.closed.is_set()
        assert runtime.retired and runtime.thread.is_alive()
        assert manager._live_tts_runtime_count() == 1
        assert manager._tts_capacity_limit() == 2
        if not slow_worker:
            release_old.set()
        try:
            await asyncio.wait_for(offline_created.wait(), 3)
        except TimeoutError:
            blocked = True
            assert manager._session_generation == generation, 'New startup budget has not begun'
            assert manager._starting_session_count == 1
        if slow_worker and not blocked:
            assert runtime.thread.is_alive()
            assert not runtime.cleanup_complete.is_set()
            assert runtime in manager._tts_runtimes
        release_old.set()
        assert await asyncio.wait_for(asyncio.shield(handoff), 3)
        assert isinstance(manager.session, OfflineService) and manager.is_active and manager.tts_ready
        assert not blocked, 'Full text startup succeeds only after physical TTS exit, despite safe handoff and free capacity'
    finally:
        release_old.set()
        await asyncio.wait_for(drain_manager(manager, clients, *tasks), 5)
        await asyncio.wait_for(asyncio.gather(*manager._tts_cleanup_tasks), 3)
