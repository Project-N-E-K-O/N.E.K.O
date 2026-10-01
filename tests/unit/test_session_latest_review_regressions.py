"""Late readiness and logical handoff must survive slow external resources."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from main_logic.core import lifecycle, streaming
from tests.unit.session_handoff_harness import ConnectedSocket, drain_manager, make_full_manager
from tests.unit.test_session_handoff_lifecycle import make_manager

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_late_tts_ready_survives_slow_llm_start(monkeypatch):
    manager, created, clients = await make_full_manager(monkeypatch)
    manager._config_manager.core["DISABLE_TTS"] = False
    monkeypatch.setattr(manager, "_resolve_session_use_tts", lambda *args: True)
    timed_out, ready_seen = asyncio.Event(), asyncio.Event()
    loop = asyncio.get_running_loop()
    spoken_chunks = asyncio.Queue()
    original_start_tts = manager._start_session_start_tts_if_needed
    original_flush = manager._flush_tts_pending_chunks

    def worker(requests, responses, *_):
        # The test supplies the external readiness message only after the
        # real startup waiter has exhausted its optional TTS budget.
        while True:
            speech_id, text = requests.get()
            if speech_id == "__shutdown__":
                return
            if text is not None and speech_id is not None and not speech_id.startswith("__"):
                loop.call_soon_threadsafe(spoken_chunks.put_nowait, text)

    monkeypatch.setattr(lifecycle._core_facade, "get_tts_worker", lambda **kwargs: (worker, "key", "qwen"))
    monkeypatch.setattr(manager, "_current_start_deadline", lambda: asyncio.get_running_loop().time() + 0.2)

    async def observe_tts_start():
        try:
            return await original_start_tts()
        except TimeoutError:
            timed_out.set()
            raise

    async def observe_flush():
        await original_flush()
        if manager.tts_ready:
            ready_seen.set()

    monkeypatch.setattr(manager, "_start_session_start_tts_if_needed", observe_tts_start)
    monkeypatch.setattr(manager, "_flush_tts_pending_chunks", observe_flush)
    starting = asyncio.create_task(manager.start_session(manager.websocket, request_id="late-tts"))
    try:
        client = await asyncio.wait_for(created.get(), 2)
        await asyncio.wait_for(timed_out.wait(), 2)
        manager.tts_response_queue.put(("__ready__", True))
        await asyncio.wait_for(ready_seen.wait(), 2)
        assert manager.tts_ready and not starting.done()
        client.allow_connect.set()
        await asyncio.wait_for(starting, 2)
        assert manager.tts_ready, "gather must not overwrite readiness already published by the handler"
        assert manager.tts_thread.is_alive()
        assert any(message.get("type") == "session_started" for message in manager.websocket.messages)
        await manager.mirror_assistant_speech("late ready speech", metadata={}, mirror_text=False, emit_turn_end_after=False)
        assert "late ready speech" in await asyncio.wait_for(spoken_chunks.get(), 2)
    finally:
        await drain_manager(manager, clients, starting)
        await asyncio.gather(*manager._tts_cleanup_tasks, return_exceptions=True)


async def test_server_end_without_target_preserves_pending_start(monkeypatch):
    manager, created, clients = await make_full_manager(monkeypatch)
    manager._config_manager.core["DISABLE_TTS"] = False
    monkeypatch.setattr(manager, "_resolve_session_use_tts", lambda *args: True)

    def worker(requests, responses, *_):
        responses.put(("__ready__", True))
        while requests.get()[0] != "__shutdown__":
            pass

    monkeypatch.setattr(lifecycle._core_facade, "get_tts_worker", lambda **kwargs: (worker, "key", "qwen"))
    starting = asyncio.create_task(manager.start_session(manager.websocket, request_id="server-end"))
    try:
        client = await asyncio.wait_for(created.get(), 2)
        await asyncio.wait_for(client.connect_entered.wait(), 2)
        operation = manager._start_operation
        assert manager.session is None
        async with asyncio.timeout(2):
            while not manager.tts_ready:
                await asyncio.sleep(0)
        runtime = manager._tts_runtime
        handler = manager.tts_handler_task
        await asyncio.wait_for(manager.end_session(by_server=True), 2)
        assert operation.valid and not starting.done()
        assert manager._starting_session_count == 1
        assert not client.closed.is_set()
        assert manager._tts_runtime_is_current(runtime) and not handler.done()
        client.allow_connect.set()
        await asyncio.wait_for(starting, 2)
        assert manager.session is client and manager.is_active
        assert not any(message.get("type") == "session_failed" for message in manager.websocket.messages)
    finally:
        await drain_manager(manager, clients, starting)
        await asyncio.gather(*manager._tts_cleanup_tasks, return_exceptions=True)


@pytest.mark.parametrize("replaced_socket", [False, True])
async def test_disconnect_during_start_revokes_only_matching_socket(monkeypatch, replaced_socket):
    manager, created, clients = await make_full_manager(monkeypatch)
    disconnected = manager.websocket
    if replaced_socket:
        manager.websocket = ConnectedSocket()
    requester = manager.websocket
    manager._config_manager.core["DISABLE_TTS"] = False
    monkeypatch.setattr(manager, "_resolve_session_use_tts", lambda *args: True)

    def worker(requests, responses, *_):
        responses.put(("__ready__", True))
        while requests.get()[0] != "__shutdown__":
            pass

    monkeypatch.setattr(lifecycle._core_facade, "get_tts_worker", lambda **kwargs: (worker, "key", "qwen"))
    starting = asyncio.create_task(manager.start_session(requester, request_id="disconnect-start"))
    try:
        client = await asyncio.wait_for(created.get(), 2)
        await asyncio.wait_for(client.connect_entered.wait(), 2)
        async with asyncio.timeout(2):
            while not manager.tts_ready:
                await asyncio.sleep(0)
        operation = manager._start_operation
        runtime = manager._tts_runtime
        assert manager.session is None
        await asyncio.wait_for(manager.cleanup(expected_websocket=disconnected), 2)
        if replaced_socket:
            assert operation.valid and not starting.done()
            assert manager.websocket is requester
            assert manager._tts_runtime_is_current(runtime)
            client.allow_connect.set()
            await asyncio.wait_for(starting, 2)
            assert manager.session is client and manager.is_active
        else:
            assert not operation.valid
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(starting, 2)
            assert client.closed.is_set()
            assert manager.websocket is None and manager.session is None
            assert not manager.is_active and runtime.retired
            await asyncio.wait_for(runtime.cleanup_task, 2)
            assert not runtime.thread.is_alive()
            assert not any(message.get("type") == "session_started" for message in requester.messages)
    finally:
        await drain_manager(manager, clients, starting)
        await asyncio.gather(*manager._tts_cleanup_tasks, return_exceptions=True)


async def test_safe_close_failure_allows_text_rebuild_and_retains_capacity(monkeypatch):
    manager = make_manager()
    manager.session_start_failure_count = 0
    manager.session_start_max_failures = 3
    old = manager.session

    async def failed_close():
        raise RuntimeError("physical close uncertain")

    class Offline:
        pass

    async def start(*args, **kwargs):
        manager.session = Offline()
        manager.is_active = True

    old.close = failed_close
    manager.start_session = AsyncMock(side_effect=start)
    monkeypatch.setattr(streaming, "OmniOfflineClient", Offline)
    pending = list(manager.pending_input_data)
    assert await manager._rebuild_offline_session_for_text_input("text")
    manager.start_session.assert_awaited_once()
    assert manager.pending_input_data == pending
    record = manager._connection_record(old)
    assert record.retired and not record.closed
    with pytest.raises(RuntimeError, match="physical close uncertain"):
        record.close_task.result()


async def test_start_deadline_uses_existing_timeout_notice_with_nonempty_details(monkeypatch):
    manager, created, clients = await make_full_manager(monkeypatch)
    starting = asyncio.create_task(manager.start_session(
        manager.websocket, request_id="timeout-notice", _deadline=asyncio.get_running_loop().time() + 0.2,
    ))
    try:
        await asyncio.wait_for(created.get(), 2)
        await asyncio.wait_for(starting, 2)
        statuses = [message for message in manager.websocket.messages if message.get("type") == "status"]
        # send_status wraps its JSON code in a status envelope.
        notices = [json.loads(message["message"]) for message in statuses]
        assert any(notice["code"] == "CONNECTION_TIMEOUT" and notice["details"]["error"] for notice in notices)
        assert not any(notice["code"] == "CONNECTION_CLOSED_ABNORMAL" for notice in notices)
        assert manager.session_start_failure_count == 1
        assert any(message.get("type") == "session_failed" for message in manager.websocket.messages)
    finally:
        await drain_manager(manager, clients, starting)
