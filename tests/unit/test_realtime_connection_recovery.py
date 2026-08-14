"""Regression coverage for realtime peer/local close classification."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
import websockets

from main_logic.core import LLMSessionManager
from main_logic.omni_realtime_client import OmniRealtimeClient, TurnDetectionMode


pytestmark = pytest.mark.unit


_END = object()


def _failure_status(code: str, generation: int = 0) -> str:
    return json.dumps(
        {"code": code, "details": {"connection_generation": generation}}
    )


class _ControlledWs:
    def __init__(self) -> None:
        self._events: asyncio.Queue[object] = asyncio.Queue()
        self.close_calls = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        event = await self._events.get()
        if event is _END:
            raise StopAsyncIteration
        if isinstance(event, BaseException):
            raise event
        return event

    async def close(self) -> None:
        self.close_calls += 1
        self._events.put_nowait(_END)

    def finish_from_peer(self) -> None:
        self._events.put_nowait(_END)

    def fail_from_peer(self, exc: BaseException) -> None:
        self._events.put_nowait(exc)


def _make_client(*, on_connection_error=None) -> OmniRealtimeClient:
    return OmniRealtimeClient(
        base_url="wss://example.test/realtime",
        api_key="sk-test",
        model="qwen-omni-turbo-realtime",
        turn_detection_mode=TurnDetectionMode.MANUAL,
        api_type="qwen",
        on_connection_error=on_connection_error,
    )


async def _start_receiver(client: OmniRealtimeClient, ws: _ControlledWs):
    client.ws = ws
    task = asyncio.create_task(client.handle_messages())
    await asyncio.sleep(0)
    return task


async def _settle_background_tasks(client: OmniRealtimeClient) -> None:
    for _ in range(10):
        if not client._bg_tasks:
            return
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_expected_local_close_does_not_become_a_connection_failure():
    failures = AsyncMock()
    client = _make_client(on_connection_error=failures)
    ws = _ControlledWs()
    receiver = await _start_receiver(client, ws)

    await client.close()
    await asyncio.wait_for(receiver, timeout=2)

    assert ws.close_calls == 1
    assert client._fatal_error_occurred is False
    failures.assert_not_awaited()


@pytest.mark.asyncio
async def test_peer_stream_end_uses_existing_disconnect_recovery_marker():
    failures = AsyncMock()
    client = _make_client(on_connection_error=failures)
    ws = _ControlledWs()
    receiver = await _start_receiver(client, ws)

    ws.finish_from_peer()
    await asyncio.wait_for(receiver, timeout=2)
    await _settle_background_tasks(client)

    assert client._fatal_error_occurred is True
    failures.assert_awaited_once_with(_failure_status("CHARACTER_DISCONNECTED"))


@pytest.mark.asyncio
async def test_unclean_peer_loss_never_exposes_the_websocket_exception_text():
    failures = AsyncMock()
    client = _make_client(on_connection_error=failures)
    ws = _ControlledWs()
    receiver = await _start_receiver(client, ws)

    ws.fail_from_peer(websockets.exceptions.ConnectionClosedError(None, None))
    await asyncio.wait_for(receiver, timeout=2)
    await _settle_background_tasks(client)

    failures.assert_awaited_once_with(_failure_status("CHARACTER_DISCONNECTED"))
    assert "close frame" not in failures.await_args.args[0]


@pytest.mark.asyncio
async def test_retired_receive_loop_cannot_condemn_or_report_the_replacement():
    failures = AsyncMock()
    client = _make_client(on_connection_error=failures)
    retired = _ControlledWs()
    receiver = await _start_receiver(client, retired)

    replacement = _ControlledWs()
    client.ws = replacement
    client._on_connection_attached()
    retired.finish_from_peer()
    await asyncio.wait_for(receiver, timeout=2)

    assert client.ws is replacement
    assert client._fatal_error_occurred is False
    failures.assert_not_awaited()


@pytest.mark.asyncio
async def test_arbiter_fail_close_preserves_first_cause_without_user_status(caplog):
    failures = AsyncMock()
    client = _make_client(on_connection_error=failures)
    ws = _ControlledWs()
    client.ws = ws
    reason = "response lifecycle could not reach a terminal state"

    with caplog.at_level(
        "WARNING",
        logger="main_logic.omni_realtime_client._response_arbiter",
    ):
        await client._response_arbiter._tear_down_transport(reason)

    assert client.ws is None
    assert client._fatal_error_occurred is True
    assert f"response arbiter failing closed: {reason}" in caplog.text
    assert "sent 1000" not in caplog.text
    failures.assert_not_awaited()


@pytest.mark.asyncio
async def test_peer_recovery_callback_runs_outside_the_receive_loop_task():
    callback_task = None

    async def on_failure(_message):
        nonlocal callback_task
        callback_task = asyncio.current_task()

    client = _make_client(on_connection_error=on_failure)
    ws = _ControlledWs()
    receiver = await _start_receiver(client, ws)

    ws.finish_from_peer()
    await asyncio.wait_for(receiver, timeout=2)
    await _settle_background_tasks(client)

    assert callback_task is not None
    assert callback_task is not receiver


@pytest.mark.asyncio
async def test_connect_uses_two_second_close_handshake_timeout():
    client = _make_client()
    ws = AsyncMock()

    with patch("websockets.connect", new_callable=AsyncMock, return_value=ws) as connect:
        await client.connect(instructions="hi", native_audio=True)

    assert connect.await_args.kwargs["close_timeout"] == 2.0
    await client.close()


@pytest.mark.asyncio
async def test_peer_disconnect_marker_is_not_shown_twice_before_recovery():
    manager = object.__new__(LLMSessionManager)
    manager.lock = asyncio.Lock()
    manager.session = object()
    manager.pending_session = None
    manager.session_closed_by_server = False
    manager.send_status = AsyncMock()
    manager.disconnected_by_server = AsyncMock()

    marker = json.dumps({"code": "CHARACTER_DISCONNECTED"})
    await manager.handle_connection_error(marker, expected_session=manager.session)

    manager.send_status.assert_not_awaited()
    manager.disconnected_by_server.assert_awaited_once_with(
        expected_session=manager.session
    )


@pytest.mark.asyncio
async def test_preclassified_timeout_is_forwarded_before_existing_recovery():
    manager = object.__new__(LLMSessionManager)
    manager.lock = asyncio.Lock()
    manager.session = object()
    manager.pending_session = None
    manager.session_closed_by_server = False
    manager.send_status = AsyncMock()
    manager.disconnected_by_server = AsyncMock()

    status = json.dumps({"code": "CONNECTION_TIMEOUT"})
    await manager.handle_connection_error(status, expected_session=manager.session)

    manager.send_status.assert_awaited_once_with(status)
    manager.disconnected_by_server.assert_awaited_once_with(
        expected_session=manager.session
    )


@pytest.mark.asyncio
async def test_late_failure_from_a_retired_generation_cannot_recover_the_successor():
    manager = object.__new__(LLMSessionManager)
    manager.lock = asyncio.Lock()
    manager.session = type("Session", (), {"_connection_generation": 2})()
    manager.pending_session = None
    manager.session_closed_by_server = False
    manager.send_status = AsyncMock()
    manager.disconnected_by_server = AsyncMock()

    await manager.handle_connection_error(
        _failure_status("CHARACTER_DISCONNECTED", generation=1),
        expected_session=manager.session,
    )

    assert manager.session_closed_by_server is False
    manager.send_status.assert_not_awaited()
    manager.disconnected_by_server.assert_not_awaited()
