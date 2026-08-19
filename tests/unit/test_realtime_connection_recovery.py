"""Regression coverage for realtime peer/local close classification."""

import asyncio
import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import websockets
from websockets.frames import Close

from main_logic.core import LLMSessionManager
from main_logic.omni_realtime_client import OmniRealtimeClient, TurnDetectionMode
from main_logic.omni_realtime_client._transport import _classify_peer_close
from main_logic.provider_failure_signals import CODES_REQUIRING_MSG_DETAIL


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
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 2.0
    while client._bg_tasks:
        pending = set(client._bg_tasks)
        await asyncio.wait_for(
            asyncio.gather(*pending),
            timeout=max(0.0, deadline - loop.time()),
        )


def _make_manager(session=None) -> LLMSessionManager:
    manager = object.__new__(LLMSessionManager)
    manager.lock = asyncio.Lock()
    manager.session = session if session is not None else object()
    manager.pending_session = None
    manager.session_closed_by_server = False
    manager.send_status = AsyncMock()
    manager.disconnected_by_server = AsyncMock()
    return manager


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
    await _settle_background_tasks(client)

    assert client.ws is replacement
    assert client._fatal_error_occurred is False
    failures.assert_not_awaited()


@pytest.mark.asyncio
async def test_retired_receive_loop_drops_buffered_frame_before_dispatch():
    failures = AsyncMock()
    event_handler = AsyncMock()
    client = _make_client(on_connection_error=failures)
    client.extra_event_handlers["test.buffered"] = event_handler
    retired = _ControlledWs()
    receiver = await _start_receiver(client, retired)

    replacement = _ControlledWs()
    client.ws = replacement
    client._on_connection_attached()
    retired._events.put_nowait(json.dumps({"type": "test.buffered"}))
    await asyncio.wait_for(receiver, timeout=2)
    await _settle_background_tasks(client)

    assert client.ws is replacement
    assert client._fatal_error_occurred is False
    event_handler.assert_not_awaited()
    failures.assert_not_awaited()


@pytest.mark.asyncio
async def test_retired_handler_exception_cannot_close_the_replacement():
    failures = AsyncMock()
    client = _make_client(on_connection_error=failures)
    retired = _ControlledWs()
    replacement = _ControlledWs()

    async def attach_replacement_then_fail(_event) -> None:
        client.ws = replacement
        client._on_connection_attached()
        raise RuntimeError("retired handler failed")

    client.extra_event_handlers["test.replace_then_fail"] = (
        attach_replacement_then_fail
    )
    receiver = await _start_receiver(client, retired)
    retired._events.put_nowait(json.dumps({"type": "test.replace_then_fail"}))
    await asyncio.wait_for(receiver, timeout=2)
    await _settle_background_tasks(client)

    assert client.ws is replacement
    assert replacement.close_calls == 0
    assert client._fatal_error_occurred is False
    failures.assert_not_awaited()


@pytest.mark.asyncio
async def test_peer_policy_close_preserves_existing_1008_classification():
    failures = AsyncMock()
    client = _make_client(on_connection_error=failures)
    ws = _ControlledWs()
    receiver = await _start_receiver(client, ws)

    ws.fail_from_peer(
        websockets.exceptions.ConnectionClosedError(
            Close(1008, "provider-controlled reason"),
            None,
        )
    )
    await asyncio.wait_for(receiver, timeout=2)
    await _settle_background_tasks(client)

    payload = json.loads(failures.await_args.args[0])
    assert payload == {
        "code": "API_1008_FALLBACK",
        "details": {
            "msg": "WebSocket close code 1008",
            "connection_generation": 0,
        },
    }
    assert "provider-controlled reason" not in failures.await_args.args[0]


@pytest.mark.parametrize(
    ("reason", "expected_code", "expected_details"),
    (
        ("insufficient standing", "API_ARREARS", {}),
        ("quota exceeded", "API_QUOTA_TIME", {}),
        ("too many requests", "API_RATE_LIMIT", {}),
        ("unauthorized", "API_KEY_REJECTED", {}),
        # Every code whose i18n string interpolates {{msg}} must carry one, or
        # the toast renders the raw placeholder. The substitute names the close
        # by its code, never by the peer-controlled reason text.
        (
            "safety policy violation",
            "API_POLICY_VIOLATION",
            {"msg": "WebSocket close code 4000"},
        ),
        ("unclassified provider close", "CHARACTER_DISCONNECTED", {}),
    ),
)
@pytest.mark.asyncio
async def test_application_close_reason_preserves_existing_classification(
    reason: str,
    expected_code: str,
    expected_details: dict,
):
    failures = AsyncMock()
    client = _make_client(on_connection_error=failures)
    ws = _ControlledWs()
    receiver = await _start_receiver(client, ws)

    ws.fail_from_peer(
        websockets.exceptions.ConnectionClosedError(
            Close(4000, reason),
            None,
        )
    )
    await asyncio.wait_for(receiver, timeout=2)
    await _settle_background_tasks(client)

    assert json.loads(failures.await_args.args[0]) == {
        "code": expected_code,
        "details": {"connection_generation": 0, **expected_details},
    }
    assert reason not in failures.await_args.args[0]


@pytest.mark.parametrize(
    "code",
    sorted(CODES_REQUIRING_MSG_DETAIL),
)
def test_every_msg_interpolating_code_is_given_a_msg_by_the_close_classifier(code):
    # The frontend renders these through i18next with skipOnVariables on, so a
    # missing `msg` ships a literal "{{msg}}" to the user. Any close reason
    # that maps to one of them must therefore arrive with the detail attached.
    reasons = {
        "API_POLICY_VIOLATION": (1000, "blocked for safety"),
        "API_1008_FALLBACK": (1008, "unclassifiable"),
        "API_UNKNOWN_ERROR": None,
    }
    probe = reasons[code]
    if probe is None:
        # Not produced by the close classifier; the manager owns this one and
        # supplies the upstream text it is already holding.
        return
    received_code, reason = probe
    classified, details = _classify_peer_close(received_code, reason)
    assert classified == code
    assert details and details.get("msg"), (
        f"{code} interpolates {{{{msg}}}} and must not be emitted without one"
    )
    assert reason not in details["msg"]


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
    # The fail-close itself never reports to the user: no receive loop is
    # running here, so nothing consumes the latch it armed. The companion
    # test below covers the live case, where the loop that owned the aborted
    # socket is the one that requests recovery.
    failures.assert_not_awaited()
    assert client._local_failure_recovery == (0, reason)


@pytest.mark.asyncio
async def test_arbiter_fail_close_still_reaches_the_manager_via_receive_loop():
    # An arbiter fail-close detaches the socket out from under the receive
    # loop, so the loop wakes up on a transport it no longer owns. Without the
    # local-abort latch it would exit silently, leaving the manager on a live
    # session over a dead socket: send_event drops every later frame at its
    # `_fatal_error_occurred` guard, so the microphone goes nowhere with no
    # toast and no rebuild.
    failures = AsyncMock()
    client = _make_client(on_connection_error=failures)
    ws = _ControlledWs()
    receiver = await _start_receiver(client, ws)

    reason = "response lifecycle could not reach a terminal state"
    await client._response_arbiter._tear_down_transport(reason)
    await asyncio.wait_for(receiver, timeout=2)
    await _settle_background_tasks(client)

    assert client.ws is None
    assert client._fatal_error_occurred is True
    # Routed through the ordinary disconnect recovery, NOT reclassified from
    # the local CLOSE 1000 handshake result.
    failures.assert_awaited_once_with(_failure_status("CHARACTER_DISCONNECTED"))
    assert "1000" not in failures.await_args.args[0]
    assert client._local_failure_recovery is None


@pytest.mark.asyncio
async def test_a_retired_loop_cannot_claim_a_successors_local_abort():
    failures = AsyncMock()
    client = _make_client(on_connection_error=failures)
    retired = _ControlledWs()
    receiver = await _start_receiver(client, retired)

    replacement = _ControlledWs()
    client.ws = replacement
    client._on_connection_attached()
    # The SUCCESSOR's transport is aborted locally. Only the loop that owned
    # that socket may report it; this retired one must stay silent and leave
    # the latch for its rightful claimant.
    await client._abort_failed_transport("successor abort")
    retired.finish_from_peer()
    await asyncio.wait_for(receiver, timeout=2)
    await _settle_background_tasks(client)

    failures.assert_not_awaited()
    assert client._local_failure_recovery == (1, "successor abort")


@pytest.mark.asyncio
async def test_manager_initiated_close_disarms_the_local_abort_latch():
    # close() means the manager already knows the session is over. An abort
    # latched just before it must not also fire a recovery request.
    failures = AsyncMock()
    client = _make_client(on_connection_error=failures)
    ws = _ControlledWs()
    receiver = await _start_receiver(client, ws)

    client._local_failure_recovery = (0, "aborted just before the close")
    await client.close()
    await asyncio.wait_for(receiver, timeout=2)
    await _settle_background_tasks(client)

    assert client._local_failure_recovery is None
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
    manager = _make_manager()

    marker = json.dumps({"code": "CHARACTER_DISCONNECTED"})
    await manager.handle_connection_error(marker, expected_session=manager.session)

    manager.send_status.assert_not_awaited()
    manager.disconnected_by_server.assert_awaited_once_with(
        expected_session=manager.session
    )


@pytest.mark.asyncio
async def test_preclassified_timeout_is_forwarded_before_existing_recovery():
    manager = _make_manager()

    status = json.dumps({"code": "CONNECTION_TIMEOUT"})
    await manager.handle_connection_error(status, expected_session=manager.session)

    manager.send_status.assert_awaited_once_with(status)
    manager.disconnected_by_server.assert_awaited_once_with(
        expected_session=manager.session
    )


@pytest.mark.parametrize(
    ("diagnostic", "expected_code"),
    (
        ("account is not in good standing", "API_ARREARS"),
        ("欠费了", "API_ARREARS"),
        ("quota exceeded for this project", "API_QUOTA_TIME"),
        ("HTTP 429 Too Many Requests", "API_RATE_LIMIT"),
        ("unauthorized: incorrect api key", "API_KEY_REJECTED"),
        ("blocked by content filter", "API_POLICY_VIOLATION"),
        ("recitation", "API_POLICY_VIOLATION"),
    ),
)
@pytest.mark.asyncio
async def test_both_failure_paths_classify_the_same_text_identically(
    diagnostic: str,
    expected_code: str,
):
    # The manager's error chain and the realtime close classifier read the
    # same provider vocabulary. They used to hold private copies of the
    # keywords AND of the ordering between them, so a keyword added for one
    # silently went missing on the other. Assert the two agree on real text,
    # not merely that a shared helper exists — a call site that stops using
    # it is exactly the regression this is here to catch.
    manager = _make_manager()
    await manager.handle_connection_error(
        diagnostic, expected_session=manager.session
    )
    manager_code = json.loads(manager.send_status.await_args.args[0])["code"]

    close_code, _ = _classify_peer_close(4000, diagnostic)

    assert manager_code == expected_code
    assert close_code == expected_code


def test_provider_failure_keywords_are_defined_in_exactly_one_place():
    # The drift this guards against is additive: someone needing the table in
    # a third module copies it rather than importing it, and from then on a
    # keyword added to one copy is honoured by one caller only.
    import main_logic

    root = Path(main_logic.__file__).resolve().parent
    pattern = re.compile(r"^_SAFETY_VIOLATION_KEYWORDS\s*=\s*\(", re.MULTILINE)
    definitions = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if pattern.search(path.read_text(encoding="utf-8"))
    )

    assert definitions == ["provider_failure_signals.py"], (
        "the safety keyword table must have exactly one definition; "
        f"found {definitions}"
    )


@pytest.mark.asyncio
async def test_late_failure_from_a_retired_generation_cannot_recover_the_successor():
    manager = _make_manager(
        type("Session", (), {"_connection_generation": 2})()
    )

    await manager.handle_connection_error(
        _failure_status("CHARACTER_DISCONNECTED", generation=1),
        expected_session=manager.session,
    )

    assert manager.session_closed_by_server is False
    manager.send_status.assert_not_awaited()
    manager.disconnected_by_server.assert_not_awaited()
