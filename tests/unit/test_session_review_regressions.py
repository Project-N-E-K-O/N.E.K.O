"""Regression evidence for PR 3089's retirement review boundaries."""

import asyncio
from queue import Queue
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from main_logic.core import lifecycle
from main_logic.core.session_lifecycle import SessionOwnershipMixin
from main_logic.omni_realtime_client._response_arbiter import RealtimeResponseArbiter

from tests.unit.session_handoff_harness import drain_manager, make_full_manager
from tests.unit.test_realtime_close_ownership import _make_client
from tests.unit.test_session_handoff_lifecycle import make_manager

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize("outcome", ["recover", "timeout", "cancel", "handler_exit"])
async def test_start_waits_for_owned_configured_tts_fallback(monkeypatch, outcome):
    manager, created, clients = await make_full_manager(monkeypatch)
    manager._config_manager.core["DISABLE_TTS"] = False
    monkeypatch.setattr(manager, "_resolve_session_use_tts", lambda *args: True)
    socket = manager.websocket
    release_old = Event()
    retired_poll = asyncio.Event()
    workers = []

    def configured_worker(requests, responses, *_):
        workers.append("configured")
        responses.put(("__ready__", False))
        while requests.get()[0] != "__shutdown__":
            pass
        release_old.wait()

    configured_worker.supports_runtime_overlap = False

    def replacement_worker(requests, responses, *_):
        workers.append("replacement")
        responses.put(("__ready__", True))
        while requests.get()[0] != "__shutdown__":
            pass

    def select_worker(**kwargs):
        if "custom" in kwargs.get("excluded_provider_keys", ()):
            return replacement_worker, "test", "qwen"
        return configured_worker, "test", "custom"

    # Only supply external workers; startup, handler, fallback, retirement and
    # capacity admission all run their real implementations.
    monkeypatch.setattr(lifecycle._core_facade, "get_tts_worker", select_worker)
    check_operation = manager._check_start_operation

    def observe_start_poll(*args, **kwargs):
        check_operation(*args, **kwargs)
        task = asyncio.current_task()
        runtime = manager._tts_runtime
        if (task.get_coro().__name__ == "_start_session_start_tts_if_needed"
                and runtime is not None and runtime.retired):
            retired_poll.set()

    monkeypatch.setattr(manager, "_check_start_operation", observe_start_poll)
    starting = asyncio.create_task(manager.start_session(
        socket, request_id="tts-fallback",
        _deadline=asyncio.get_running_loop().time() + (1 if outcome == "timeout" else 5),
    ))
    try:
        client = await asyncio.wait_for(created.get(), 2)
        client.allow_connect.set()
        await asyncio.wait_for(retired_poll.wait(), 2)
        old = manager._tts_runtime
        assert old.retired and old.thread.is_alive()
        assert not old.cleanup_complete.is_set()
        assert old.fallback_task is manager.tts_handler_task
        if outcome == "recover":
            release_old.set()
        elif outcome == "cancel":
            manager.request_end_session(by_server=False)
        elif outcome == "handler_exit":
            manager.tts_handler_task.cancel()
            await asyncio.gather(manager.tts_handler_task, return_exceptions=True)

        if outcome == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(starting, 2)
        else:
            await asyncio.wait_for(starting, 2)
        started = [m for m in socket.messages if m.get("type") == "session_started"]
        failed = [m for m in socket.messages if m.get("type") == "session_failed"]
        if outcome == "recover":
            assert workers == ["configured", "replacement"]
            assert len(started) == 1 and not failed
            assert manager.tts_ready
            assert manager._tts_runtime is not old
        else:
            assert workers == ["configured"]
            assert not started
            assert len(failed) == (0 if outcome == "cancel" else 1)
            assert manager._live_tts_runtime_count() == 1
            assert not old.cleanup_task.cancelled()
        assert manager.session_start_failure_count == (outcome in {"timeout", "handler_exit"})
        if failed:
            assert failed[0]["request_id"] == "tts-fallback"
    finally:
        release_old.set()
        await asyncio.wait_for(drain_manager(manager, clients, starting), 4)
        await asyncio.wait_for(asyncio.gather(*manager._tts_cleanup_tasks), 3)
    assert old.fallback_task is None


@pytest.mark.parametrize("cancel_start", [False, True])
async def test_tts_retirement_reports_failure_unless_start_was_cancelled(monkeypatch, cancel_start):
    manager, created, clients = await make_full_manager(monkeypatch)
    socket = manager.websocket
    entered = asyncio.Event()
    release = asyncio.Event()
    monkeypatch.setattr(manager, "_resolve_session_use_tts", lambda *args: True)

    async def prepare_worker(**kwargs):
        # External worker lifecycle is controlled; start/gather/notification and
        # runtime retirement use their real implementations.
        manager.tts_thread = SimpleNamespace(is_alive=lambda: False)
        manager.tts_request_queue = Queue()
        manager.tts_response_queue = Queue()
        runtime = manager._snapshot_tts_runtime()
        entered.set()
        await release.wait()
        manager._retire_tts_runtime(runtime)

    monkeypatch.setattr(manager, "ensure_tts_pipeline_alive", prepare_worker)
    starting = asyncio.create_task(manager.start_session(socket, request_id="tts-retired"))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        client = await asyncio.wait_for(created.get(), 2)
        client.allow_connect.set()
        if cancel_start:
            await manager.end_session(by_server=False)
        release.set()
        if cancel_start:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(starting, 2)
            assert manager.session_start_failure_count == 0
        else:
            await asyncio.wait_for(starting, 2)
            assert manager.session_start_failure_count == 1
            failures = [m for m in socket.messages if m.get("type") == "session_failed"]
            assert len(failures) == 1
            assert failures[0]["request_id"] == "tts-retired"
        assert not any(m.get("type") == "session_started" for m in socket.messages)
    finally:
        release.set()
        await drain_manager(manager, clients, starting)


@pytest.mark.parametrize("replace_socket", [False, True])
async def test_disconnect_cleanup_unbinds_only_its_socket_after_close_error(replace_socket, monkeypatch):
    manager = make_manager()
    old = manager.session
    socket = manager.websocket
    successor = object()
    entered = asyncio.Event()
    release = asyncio.Event()
    warning = Mock()
    monkeypatch.setattr(lifecycle.logger, "warning", warning)

    async def close():
        entered.set()
        await release.wait()
        raise RuntimeError("controlled close failure")

    old.close = close
    task = asyncio.create_task(manager.cleanup(expected_websocket=socket))
    await asyncio.wait_for(entered.wait(), 2)
    if replace_socket:
        manager.websocket = successor
    release.set()
    await asyncio.wait_for(task, 2)
    assert manager.websocket is (successor if replace_socket else None)
    warning.assert_called_once()
    assert str(warning.call_args.args[1]) == "controlled close failure"
    assert not manager._connection_records[0].closed


@pytest.mark.parametrize("entrypoint", ["close", "_close_gemini"])
async def test_failed_gemini_exit_after_replacement_keeps_physical_retry(entrypoint):
    client = _make_client()
    client._is_gemini = True
    entered = asyncio.Event()
    release = asyncio.Event()

    class Context:
        async def __aexit__(self, *args):
            entered.set()
            await release.wait()
            raise RuntimeError("old context exit failed")

    context = Context()
    session = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("old transport failed")))
    client._gemini_context_manager = context
    client._gemini_session = session
    client.ws = session
    closing = asyncio.create_task(getattr(client, entrypoint)())
    await asyncio.wait_for(entered.wait(), 2)
    replacement_context = AsyncMock()
    replacement_session = object()
    client._gemini_context_manager = replacement_context
    client._gemini_session = replacement_session
    client.ws = replacement_session
    client._on_connection_attached()
    release.set()
    with pytest.raises(RuntimeError, match="old context exit failed"):
        await asyncio.wait_for(closing, 2)
    replacement_context.__aexit__.assert_not_awaited()
    assert client.ws is replacement_session

    session.close.side_effect = None
    await asyncio.wait_for(getattr(client, entrypoint)(), 2)
    assert session.close.await_count == 2, "retry must reach the retained old SDK session"
    replacement_context.__aexit__.assert_awaited_once()
    assert not client._gemini_close_retry_contexts


@pytest.mark.parametrize("serial", [False, True])
@pytest.mark.parametrize("retry_succeeds", [False, True])
async def test_capacity_admission_retries_retired_close_once(serial, retry_succeeds):
    manager = SessionOwnershipMixin()
    old = SimpleNamespace(
        supports_session_overlap=not serial,
        close=AsyncMock(side_effect=RuntimeError("physical close uncertain")),
    )
    with pytest.raises(RuntimeError, match="physical close uncertain"):
        await manager._close_owned_session(old)
    record = manager._connection_record(old)
    if retry_succeeds:
        old.close.side_effect = None
    manager._current_start_deadline = lambda: asyncio.get_running_loop().time() + 0.1
    new = SimpleNamespace(connect=AsyncMock(), close=AsyncMock())
    if serial and not retry_succeeds:
        with pytest.raises(TimeoutError):
            await manager._connect_owned_session(new)
        new.connect.assert_not_awaited()
    else:
        await manager._connect_owned_session(new)
        new.connect.assert_awaited_once()
    await asyncio.gather(record.close_task, return_exceptions=True)
    assert old.close.await_count == 2
    assert record.closed is retry_succeeds
    if manager._connection_record(new) is not None:
        await manager._close_owned_session(new)


@pytest.mark.parametrize("preparing", [False, True])
async def test_cancelled_queued_ticket_settles_while_dispatch_stays_paused(preparing):
    send = AsyncMock()
    arbiter = RealtimeResponseArbiter(send)
    arbiter._dispatch_pause_timeout = 0
    parked = asyncio.Event()
    original_clear = arbiter._dispatch_wakeup.clear

    def clear():
        original_clear()
        parked.set()

    arbiter._dispatch_wakeup.clear = clear
    token = arbiter.begin_turn_preparation("held") if preparing else None
    if not preparing:
        arbiter.pause_dispatch("held")
    try:
        ticket = await arbiter.enqueue(source="proactive")
        queued = arbiter._queued_by_ticket[id(ticket)]
        await asyncio.wait_for(parked.wait(), 1)
        assert await arbiter.cancel_ticket(ticket, wait=False)
        for future in (ticket.sent, ticket.done):
            with pytest.raises(RuntimeError, match="response dispatch interrupted"):
                await asyncio.wait_for(asyncio.shield(future), 1)
        await asyncio.wait_for(asyncio.shield(queued.completed), 1)
        assert not arbiter._dispatch_allowed.is_set()
        assert arbiter._turn_preparations == int(preparing)
        send.assert_not_awaited()
    finally:
        if token is not None:
            arbiter.end_turn_preparation(token)
        await arbiter.shutdown()


@pytest.mark.parametrize("entrypoint", ["close", "_close_gemini"])
@pytest.mark.parametrize("cancel_retry", [False, True])
async def test_gemini_retry_without_current_context_cannot_close_late_replacement(entrypoint, cancel_retry):
    client = _make_client()
    client._is_gemini = True
    entered = asyncio.Event()
    release = asyncio.Event()

    class Context:
        calls = 0

        async def __aexit__(self, *args):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("first exit failed")
            entered.set()
            await release.wait()

    context = Context()
    session = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("first close failed")))
    client._gemini_context_manager = context
    client._gemini_session = session
    client.ws = session
    with pytest.raises(RuntimeError, match="first exit failed"):
        await getattr(client, entrypoint)()
    # The retained owner must remain retryable without any current SDK fields.
    client._gemini_context_manager = None
    client._gemini_session = None
    client.ws = None
    client._on_connection_attached()
    session.close.side_effect = None
    retry = asyncio.create_task(getattr(client, entrypoint)())
    await asyncio.wait_for(entered.wait(), 2)
    owned_task = client._close_task if entrypoint == "close" else client._gemini_close_task
    replacement_context = AsyncMock()
    replacement = object()
    client._gemini_context_manager = replacement_context
    client._gemini_session = replacement
    client.ws = replacement
    client._on_connection_attached()
    if cancel_retry:
        retry.cancel()
        with pytest.raises(asyncio.CancelledError):
            await retry
    release.set()
    await asyncio.wait_for(asyncio.shield(owned_task), 2)
    if not cancel_retry:
        await retry
    assert session.close.await_count == 2
    assert not client._gemini_close_retry_contexts
    assert client.ws is replacement
    assert client._gemini_context_manager is replacement_context
    replacement_context.__aexit__.assert_not_awaited()
