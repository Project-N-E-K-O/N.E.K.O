import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic.omni_realtime_client import OmniRealtimeClient
from main_logic.tool_calling import ToolCall, ToolResult


class _QueueSocket:
    def __init__(self) -> None:
        self._messages: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.send_observed = asyncio.Event()
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        message = await self._messages.get()
        if message is None:
            raise StopAsyncIteration
        return json.dumps(message)

    def feed(self, event: dict) -> None:
        self._messages.put_nowait(event)

    def finish(self) -> None:
        self._messages.put_nowait(None)

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))
        self.send_observed.set()

    async def close(self) -> None:
        self.closed = True


class _BlockingSendSocket(_QueueSocket):
    def __init__(self) -> None:
        super().__init__()
        self.send_started = asyncio.Event()
        self.release_send = asyncio.Event()

    async def send(self, payload: str) -> None:
        self.send_started.set()
        await self.release_send.wait()
        await super().send(payload)


class _FailingBlockingSendSocket(_BlockingSendSocket):
    async def send(self, _payload: str) -> None:
        self.send_started.set()
        await self.release_send.wait()
        raise ConnectionError("1006 retired transport failed")


class _GeminiSession:
    def __init__(self) -> None:
        self.tool_responses: list[list] = []
        self.closed = False

    async def send_tool_response(self, *, function_responses) -> None:
        self.tool_responses.append(list(function_responses))


class _GeminiReceiveSession(_GeminiSession):
    def __init__(self) -> None:
        super().__init__()
        self._responses: asyncio.Queue = asyncio.Queue()
        self.receive_started = asyncio.Event()

    def receive(self):
        async def responses():
            self.receive_started.set()
            response = await self._responses.get()
            if response is not None:
                yield response

        return responses()

    def finish_receive(self) -> None:
        self._responses.put_nowait(None)


class _SendGate:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def __aenter__(self):
        self.entered.set()
        await self.release.wait()

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


def _raw_tool_event(call_id: str = "call-1") -> dict:
    return {
        "type": "response.function_call_arguments.done",
        "response_id": "response-1",
        "call_id": call_id,
        "name": "lookup",
        "arguments": "{}",
    }


def _gemini_response(
    *,
    calls=(),
    cancelled_ids=(),
    vad_signal_type=None,
):
    tool_call = None
    if calls:
        tool_call = SimpleNamespace(
            function_calls=[
                SimpleNamespace(id=call_id, name=name, args={})
                for call_id, name in calls
            ]
        )
    cancellation = None
    if cancelled_ids:
        cancellation = SimpleNamespace(ids=list(cancelled_ids))
    vad_signal = None
    if vad_signal_type is not None:
        vad_signal = SimpleNamespace(vad_signal_type=vad_signal_type)
    return SimpleNamespace(
        tool_call=tool_call,
        tool_call_cancellation=cancellation,
        voice_activity_detection_signal=vad_signal,
        server_content=None,
    )


async def _wait_for_tool_tasks(client: OmniRealtimeClient) -> None:
    while client._tool_tasks:
        tasks = tuple(client._tool_tasks)
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=1,
        )


async def _wait_for_socket_sends(socket: _QueueSocket, count: int) -> None:
    while len(socket.sent) < count:
        socket.send_observed.clear()
        if len(socket.sent) >= count:
            return
        await asyncio.wait_for(socket.send_observed.wait(), timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("api_type", ["gpt", "gemini"])
async def test_proactive_inject_waits_for_current_turn_tool_task(
    api_type: str,
    monkeypatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    cancelled = asyncio.Event()

    async def handler(call: ToolCall) -> ToolResult:
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gemini-live" if api_type == "gemini" else "gpt-realtime",
        api_type=api_type,
        on_tool_call=handler,
    )
    if api_type == "gemini":
        import main_logic.omni_realtime_client._gemini_support as gemini_support

        monkeypatch.setattr(
            gemini_support,
            "types",
            SimpleNamespace(FunctionResponse=lambda **kwargs: SimpleNamespace(**kwargs)),
        )
        client._gemini_session = AsyncMock()
        client.ws = client._gemini_session
        client._send_tool_result_gemini = AsyncMock()
    else:
        client.ws = _QueueSocket()
        client._send_tool_result_openai_realtime = AsyncMock()
    client._on_connection_attached()
    original_scope = client._tool_scope_generation
    owner = client._capture_tool_task_owner(client.ws)
    call = ToolCall(name="lookup", arguments={}, call_id="call-1")
    if api_type == "gemini":
        client._start_gemini_tool_batch([call], owner)
    else:
        client._start_raw_tool_call(call, owner)
    await asyncio.wait_for(started.wait(), timeout=1)

    with pytest.raises(RuntimeError, match="tool turn is in flight"):
        await client.inject_text_and_request_response("plugin proactive event")

    assert client._tool_scope_generation == original_scope
    assert not cancelled.is_set()
    if api_type == "gemini":
        client._gemini_session.send_client_content.assert_not_awaited()
    else:
        assert client.ws.sent == []

    release.set()
    await _wait_for_tool_tasks(client)
    assert client.has_inflight_tool_turn() is False
    if api_type == "gemini":
        client._send_tool_result_gemini.assert_awaited_once()
        await client.inject_text_and_request_response("plugin proactive event")
        client._gemini_session.send_client_content.assert_awaited_once()
    else:
        client._send_tool_result_openai_realtime.assert_awaited_once()
        sent = asyncio.get_running_loop().create_future()
        sent.set_result(None)
        client._response_arbiter = SimpleNamespace(
            enqueue=AsyncMock(return_value=SimpleNamespace(sent=sent)),
        )
        await client.inject_text_and_request_response("plugin proactive event")
        client._response_arbiter.enqueue.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_true_user_turn_still_cancels_current_tool_task() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocked_tool() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-realtime",
        api_type="gpt",
    )
    task = client._create_tool_task(blocked_tool(), call_ids=("call-1",))
    await asyncio.wait_for(started.wait(), timeout=1)

    client.note_user_turn_started()

    await asyncio.wait_for(cancelled.wait(), timeout=1)
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("api_type", ["gpt", "gemini"])
async def test_proactive_inject_waits_for_tool_result_continuation(
    api_type: str,
) -> None:
    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gemini-live" if api_type == "gemini" else "gpt-realtime",
        api_type=api_type,
    )
    if api_type == "gemini":
        session = AsyncMock()
        client._gemini_session = session
        client.ws = session
        client._on_connection_attached()
        owner = client._capture_tool_task_owner(session)
        client._gemini_tool_continuation_owner = owner
    else:
        client.ws = object()
        client._on_connection_attached()
        continuation = asyncio.get_running_loop().create_future()
        client._track_raw_tool_continuation(continuation)

    with pytest.raises(RuntimeError, match="tool turn is in flight"):
        await client.inject_text_and_request_response("plugin proactive event")

    if api_type == "gemini":
        session.send_client_content.assert_not_awaited()
        client._settle_gemini_tool_continuation(
            connection_generation=client._connection_generation,
            provider_session=session,
        )
        await client.inject_text_and_request_response("plugin proactive event")
        session.send_client_content.assert_awaited_once()
    else:
        continuation.set_result(None)
        sent = asyncio.get_running_loop().create_future()
        sent.set_result(None)
        client._response_arbiter = SimpleNamespace(
            enqueue=AsyncMock(return_value=SimpleNamespace(sent=sent)),
        )
        await client.inject_text_and_request_response("plugin proactive event")
        client._response_arbiter.enqueue.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tool_task_failure_logs_identity_without_exception_payload(
    monkeypatch,
) -> None:
    import main_logic.omni_realtime_client._tools as tooling

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-realtime",
        api_type="gpt",
    )
    logged = asyncio.Event()
    records: list[str] = []

    def observe_error(message, *args, **kwargs) -> None:
        del kwargs
        records.append(message % args)
        logged.set()

    monkeypatch.setattr(tooling.logger, "error", observe_error)

    async def fail() -> None:
        raise RuntimeError("private tool payload")

    task = client._create_tool_task(fail(), call_ids=("call-safe-id",))
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.wait_for(logged.wait(), timeout=1)

    assert len(records) == 1
    assert "call_fingerprints=" in records[0]
    assert "error_type=RuntimeError" in records[0]
    assert "call-safe-id" not in records[0]
    assert "private tool payload" not in records[0]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_raw_tool_result_for_the_current_owner_still_sends() -> None:
    handler_called = asyncio.Event()

    async def handler(call):
        handler_called.set()
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-realtime",
        api_type="gpt",
        on_tool_call=handler,
    )
    socket = _QueueSocket()
    client.ws = socket
    client._on_connection_attached()
    receive_loop = asyncio.create_task(client.handle_messages())
    socket.feed(_raw_tool_event())
    await asyncio.wait_for(handler_called.wait(), timeout=1)
    await _wait_for_tool_tasks(client)

    assert [event["type"] for event in socket.sent] == [
        "conversation.item.create",
        "response.create",
    ]
    assert socket.sent[0]["item"]["call_id"] == "call-1"
    socket.feed({"type": "response.created", "response": {"id": "tool-response"}})
    socket.feed({"type": "response.done", "response": {"id": "tool-response"}})
    await client._response_arbiter.wait_until_idle(timeout=1)
    socket.finish()
    await asyncio.wait_for(receive_loop, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_replacement_without_response_created_drops_retired_turn_guard() -> None:
    host_turn = ["retired-turn"]
    handler_called = asyncio.Event()

    async def handler(call):
        handler_called.set()
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-realtime",
        api_type="gpt",
        get_host_turn_id=lambda: host_turn[0],
        on_tool_call=handler,
    )
    retired = _QueueSocket()
    client.ws = retired
    client._on_connection_attached()
    client._current_turn_host_id = host_turn[0]

    host_turn[0] = "replacement-turn"
    replacement = _QueueSocket()
    client.ws = replacement
    client._on_connection_attached()
    assert client._current_turn_host_id is None

    receive_loop = asyncio.create_task(client.handle_messages())
    replacement.feed(_raw_tool_event())
    await asyncio.wait_for(handler_called.wait(), timeout=1)
    await _wait_for_tool_tasks(client)

    assert [event["type"] for event in replacement.sent] == [
        "conversation.item.create",
        "response.create",
    ]
    replacement.feed({"type": "response.created", "response": {"id": "tool-response"}})
    replacement.feed({"type": "response.done", "response": {"id": "tool-response"}})
    await client._response_arbiter.wait_until_idle(timeout=1)
    replacement.finish()
    await asyncio.wait_for(receive_loop, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_raw_tool_result_cannot_cross_connection() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()
    release = asyncio.Event()

    async def handler(call):
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-realtime",
        api_type="gpt",
        on_tool_call=handler,
    )
    retired = _QueueSocket()
    client.ws = retired
    client._on_connection_attached()
    receive_loop = asyncio.create_task(client.handle_messages())
    retired.feed(_raw_tool_event())
    await asyncio.wait_for(started.wait(), timeout=1)

    replacement = _QueueSocket()
    client.ws = replacement
    client._on_connection_attached()
    await asyncio.wait_for(cancelled.wait(), timeout=1)
    release.set()
    await _wait_for_tool_tasks(client)

    assert retired.sent == []
    assert replacement.sent == []
    retired.finish()
    await asyncio.wait_for(receive_loop, timeout=1)
    assert retired.closed is True
    assert client.ws is replacement
    assert replacement.closed is False
    assert client._fatal_error_occurred is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_queued_raw_tool_result_cannot_resume_on_replacement_socket() -> None:
    handler_finished = asyncio.Event()

    async def handler(call):
        handler_finished.set()
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-realtime",
        api_type="gpt",
        on_tool_call=handler,
    )
    retired = _QueueSocket()
    client.ws = retired
    client._on_connection_attached()
    send_gate = _SendGate()
    client._send_semaphore = send_gate
    receive_loop = asyncio.create_task(client.handle_messages())
    retired.feed(_raw_tool_event())
    await asyncio.wait_for(handler_finished.wait(), timeout=1)
    await asyncio.wait_for(send_gate.entered.wait(), timeout=1)

    replacement = _QueueSocket()
    client.ws = replacement
    client._on_connection_attached()
    send_gate.release.set()
    await _wait_for_tool_tasks(client)
    await client._response_arbiter.wait_until_idle(timeout=1)

    assert retired.sent == []
    assert replacement.sent == []
    retired.finish()
    await asyncio.wait_for(receive_loop, timeout=1)
    assert retired.closed is True
    assert client.ws is replacement
    assert replacement.closed is False
    assert client._fatal_error_occurred is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_raw_tool_cancel_survives_tool_scope_replacement() -> None:
    async def handler(call):
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-realtime",
        api_type="gpt",
        on_tool_call=handler,
    )
    socket = _QueueSocket()
    client.ws = socket
    client._on_connection_attached()
    captured = {}
    original_enqueue = client._response_arbiter.enqueue

    async def observed_enqueue(**kwargs):
        ticket = await original_enqueue(**kwargs)
        if kwargs.get("source") == "tool_result":
            captured["ticket"] = ticket
        return ticket

    client._response_arbiter.enqueue = observed_enqueue
    receive_loop = asyncio.create_task(client.handle_messages())
    socket.feed(_raw_tool_event())
    await _wait_for_socket_sends(socket, 2)
    ticket = captured["ticket"]
    await asyncio.wait_for(asyncio.shield(ticket.sent), timeout=1)
    socket.feed({"type": "response.created", "response": {"id": "tool-response"}})
    await asyncio.wait_for(asyncio.shield(ticket.started), timeout=1)

    client.note_user_turn_started()
    cancelling = asyncio.create_task(client._response_arbiter.cancel_current())
    await _wait_for_socket_sends(socket, 3)
    assert socket.sent[-1]["type"] == "response.cancel"
    socket.feed({"type": "response.done", "response": {"id": "tool-response"}})
    await asyncio.wait_for(cancelling, timeout=1)

    socket.finish()
    await asyncio.wait_for(receive_loop, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_raw_tool_result_cannot_cross_host_turn_on_same_connection() -> None:
    host_turn = ["turn-1"]
    started = asyncio.Event()
    cancelled = asyncio.Event()
    release = asyncio.Event()

    async def handler(call):
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-realtime",
        api_type="gpt",
        get_host_turn_id=lambda: host_turn[0],
        on_tool_call=handler,
    )
    socket = _QueueSocket()
    client.ws = socket
    client._on_connection_attached()
    receive_loop = asyncio.create_task(client.handle_messages())
    socket.feed(_raw_tool_event())
    await asyncio.wait_for(started.wait(), timeout=1)

    host_turn[0] = "turn-2"
    client.note_user_turn_started()
    await asyncio.wait_for(cancelled.wait(), timeout=1)
    release.set()
    await _wait_for_tool_tasks(client)

    assert socket.sent == []
    socket.finish()
    await asyncio.wait_for(receive_loop, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_raw_transcript_only_turn_cancels_previous_tool() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()
    transcript_seen = asyncio.Event()
    release = asyncio.Event()

    async def handler(call):
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    async def on_input_transcript(_transcript: str) -> None:
        transcript_seen.set()

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="free-model",
        api_type="free",
        on_input_transcript=on_input_transcript,
        on_tool_call=handler,
    )
    socket = _QueueSocket()
    client.ws = socket
    client._on_connection_attached()
    receive_loop = asyncio.create_task(client.handle_messages())
    socket.feed(_raw_tool_event())
    await asyncio.wait_for(started.wait(), timeout=1)

    socket.feed(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "next turn",
        }
    )
    await asyncio.wait_for(transcript_seen.wait(), timeout=1)
    await asyncio.wait_for(cancelled.wait(), timeout=1)
    release.set()
    await _wait_for_tool_tasks(client)

    assert socket.sent == []
    socket.finish()
    await asyncio.wait_for(receive_loop, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_raw_transcript_does_not_advance_scope_twice_after_speech_started() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()
    transcript_seen = asyncio.Event()
    release = asyncio.Event()

    async def handler(call):
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    async def on_input_transcript(_transcript: str) -> None:
        transcript_seen.set()

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-realtime",
        api_type="gpt",
        on_input_transcript=on_input_transcript,
        on_tool_call=handler,
    )
    socket = _QueueSocket()
    client.ws = socket
    client._on_connection_attached()
    receive_loop = asyncio.create_task(client.handle_messages())
    socket.feed({"type": "input_audio_buffer.speech_started"})
    socket.feed(_raw_tool_event())
    await asyncio.wait_for(started.wait(), timeout=1)

    socket.feed(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "same turn",
        }
    )
    await asyncio.wait_for(transcript_seen.wait(), timeout=1)
    assert not cancelled.is_set()
    assert len(client._tool_tasks) == 1

    release.set()
    await _wait_for_tool_tasks(client)
    assert [event["type"] for event in socket.sent] == [
        "conversation.item.create",
        "response.create",
    ]
    socket.feed({"type": "response.created", "response": {"id": "tool-response"}})
    socket.feed({"type": "response.done", "response": {"id": "tool-response"}})
    await client._response_arbiter.wait_until_idle(timeout=1)
    socket.finish()
    await asyncio.wait_for(receive_loop, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_vad_response_done_rotation_keeps_legal_raw_tool_result() -> None:
    host_turn = ["turn-1"]
    handler_started = asyncio.Event()
    release_handler = asyncio.Event()
    sid_rotated = asyncio.Event()

    async def handler(call):
        handler_started.set()
        await release_handler.wait()
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    async def rotate_sid() -> None:
        host_turn[0] = "turn-2"
        sid_rotated.set()

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="free-model",
        api_type="free",
        get_host_turn_id=lambda: host_turn[0],
        on_sid_rotate=rotate_sid,
        on_tool_call=handler,
    )
    client._has_server_vad = False
    socket = _QueueSocket()
    client.ws = socket
    client._on_connection_attached()
    receive_loop = asyncio.create_task(client.handle_messages())
    socket.feed(_raw_tool_event())
    await asyncio.wait_for(handler_started.wait(), timeout=1)
    socket.feed({"type": "response.done", "response": {"id": "response-1"}})
    await asyncio.wait_for(sid_rotated.wait(), timeout=1)
    release_handler.set()
    await _wait_for_tool_tasks(client)

    assert [event["type"] for event in socket.sent] == [
        "conversation.item.create",
        "response.create",
    ]
    socket.feed({"type": "response.created", "response": {"id": "tool-response"}})
    socket.feed({"type": "response.done", "response": {"id": "tool-response"}})
    await client._response_arbiter.wait_until_idle(timeout=1)
    socket.finish()
    await asyncio.wait_for(receive_loop, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gemini_tool_result_cannot_cross_connection() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()
    release = asyncio.Event()

    async def handler(call):
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gemini-live",
        api_type="gemini",
        on_tool_call=handler,
    )
    retired = _GeminiSession()
    client._gemini_session = retired
    client.ws = retired
    client._on_connection_attached()
    generation = client._connection_generation
    await client._process_gemini_response(
        _gemini_response(calls=(("call-1", "lookup"),)),
        provider_session=retired,
        connection_generation=generation,
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    replacement = _GeminiSession()
    client._gemini_session = replacement
    client.ws = replacement
    client._on_connection_attached()
    await asyncio.wait_for(cancelled.wait(), timeout=1)
    release.set()
    await _wait_for_tool_tasks(client)

    assert retired.tool_responses == []
    assert replacement.tool_responses == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retired_gemini_receive_loop_preserves_replacement_outcome() -> None:
    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gemini-live",
        api_type="gemini",
    )
    retired = _GeminiReceiveSession()
    client._gemini_session = retired
    client.ws = retired
    client._on_connection_attached()
    receive_loop = asyncio.create_task(client.handle_messages())
    await asyncio.wait_for(retired.receive_started.wait(), timeout=1)

    replacement = _GeminiReceiveSession()
    client._gemini_session = replacement
    client.ws = replacement
    client._on_connection_attached()
    token = "replacement-outcome"
    rejected: list[str] = []
    client._gemini_proactive_outcome = (token, rejected.append, None)
    client._gemini_proactive_outcome_owner = (
        client._connection_generation,
        replacement,
        token,
        None,
    )
    retired.finish_receive()
    await asyncio.wait_for(receive_loop, timeout=1)

    assert client._gemini_proactive_outcome == (token, rejected.append, None)
    assert rejected == []
    assert client._gemini_session is replacement
    assert client.ws is replacement
    assert replacement.closed is False
    assert client._fatal_error_occurred is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gemini_event_callback_cannot_settle_replacement_outcome() -> None:
    callback_started = asyncio.Event()
    release_callback = asyncio.Event()

    async def on_input_transcript(_text: str) -> None:
        callback_started.set()
        await release_callback.wait()

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gemini-live",
        api_type="gemini",
        on_input_transcript=on_input_transcript,
    )
    retired = _GeminiSession()
    client._gemini_session = retired
    client.ws = retired
    client._on_connection_attached()
    retired_generation = client._connection_generation
    response = _gemini_response()
    response.server_content = SimpleNamespace(
        input_transcription=SimpleNamespace(text="retired input"),
        output_transcription=None,
        model_turn=SimpleNamespace(parts=[]),
        turn_complete=True,
        interrupted=False,
    )
    processing = asyncio.create_task(
        client._process_gemini_response(
            response,
            provider_session=retired,
            connection_generation=retired_generation,
        )
    )
    await asyncio.wait_for(callback_started.wait(), timeout=1)

    replacement = _GeminiSession()
    replacement_context = object()
    client._gemini_session = replacement
    client._gemini_context_manager = replacement_context
    client.ws = replacement
    client._on_connection_attached()
    completed: list[bool] = []
    token = "replacement-event-outcome"
    owner = (
        client._connection_generation,
        replacement,
        token,
        replacement_context,
    )
    client._gemini_proactive_outcome = (token, None, lambda: completed.append(True))
    client._gemini_proactive_outcome_owner = owner
    client._gemini_user_transcript = "replacement transcript"
    client._is_responding = False
    release_callback.set()
    await asyncio.wait_for(processing, timeout=1)

    assert client._gemini_proactive_outcome is not None
    assert client._gemini_proactive_outcome[0] == token
    assert client._gemini_proactive_outcome_owner == owner
    assert completed == []
    assert client._gemini_user_transcript == "replacement transcript"
    assert client._is_responding is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_raw_speech_stopped_callback_cannot_rearm_replacement_arbiter() -> None:
    callback_started = asyncio.Event()
    release_callback = asyncio.Event()

    async def on_new_message() -> None:
        callback_started.set()
        await release_callback.wait()

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-realtime",
        api_type="gpt",
        on_new_message=on_new_message,
    )
    retired = _QueueSocket()
    client.ws = retired
    client._on_connection_attached()
    receive_loop = asyncio.create_task(client.handle_messages())
    retired.feed({"type": "input_audio_buffer.speech_stopped"})
    await asyncio.wait_for(callback_started.wait(), timeout=1)

    replacement = _QueueSocket()
    client.ws = replacement
    client._on_connection_attached()
    client._response_arbiter.reset_connection_state()
    armed: list[bool] = []
    client._response_arbiter.arm_server_vad_response_pending_timeout = (
        lambda: armed.append(True)
    )
    release_callback.set()
    await asyncio.wait_for(receive_loop, timeout=1)

    assert armed == []
    assert retired.closed is True
    assert client.ws is replacement
    assert replacement.closed is False
    assert client._fatal_error_occurred is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_raw_transcript_callback_cannot_clear_replacement_buffer() -> None:
    callback_started = asyncio.Event()
    release_callback = asyncio.Event()

    async def on_output_transcript(_text: str, _is_first: bool) -> None:
        callback_started.set()
        await release_callback.wait()

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-realtime",
        api_type="gpt",
        on_output_transcript=on_output_transcript,
    )
    retired = _QueueSocket()
    client.ws = retired
    client._on_connection_attached()
    client._output_transcript_buffer = "retired transcript"
    receive_loop = asyncio.create_task(client.handle_messages())
    retired.feed(
        {
            "type": "response.audio_transcript.done",
            "transcript": "retired transcript",
        }
    )
    await asyncio.wait_for(callback_started.wait(), timeout=1)

    replacement = _QueueSocket()
    client.ws = replacement
    client._on_connection_attached()
    client._output_transcript_buffer = "replacement transcript"
    client._is_first_transcript_chunk = True
    release_callback.set()
    await asyncio.wait_for(receive_loop, timeout=1)

    assert client._output_transcript_buffer == "replacement transcript"
    assert client._is_first_transcript_chunk is True
    assert client.ws is replacement
    assert replacement.closed is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_raw_audio_callback_cannot_mutate_replacement_turn() -> None:
    callback_started = asyncio.Event()
    release_callback = asyncio.Event()

    async def on_audio_delta(_audio: bytes) -> None:
        callback_started.set()
        await release_callback.wait()

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-realtime",
        api_type="gpt",
        on_audio_delta=on_audio_delta,
    )
    retired = _QueueSocket()
    client.ws = retired
    client._on_connection_attached()
    receive_loop = asyncio.create_task(client.handle_messages())
    retired.feed({"type": "response.audio.delta", "delta": "AAE="})
    await asyncio.wait_for(callback_started.wait(), timeout=1)

    replacement = _QueueSocket()
    client.ws = replacement
    client._on_connection_attached()
    client._audio_delta_count = 7
    client._ai_recent_activity_time = 123.0
    release_callback.set()
    await asyncio.wait_for(receive_loop, timeout=1)

    assert client._audio_delta_count == 7
    assert client._ai_recent_activity_time == 123.0
    assert client.ws is replacement
    assert replacement.closed is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_raw_response_done_flush_cannot_reset_replacement_turn() -> None:
    callback_started = asyncio.Event()
    release_callback = asyncio.Event()
    response_created = asyncio.Event()

    async def on_output_transcript(_text: str, _is_first: bool) -> None:
        callback_started.set()
        await release_callback.wait()

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-realtime",
        api_type="gpt",
        on_output_transcript=on_output_transcript,
    )
    retired = _QueueSocket()
    client.ws = retired
    client._on_connection_attached()
    original_created = client._response_arbiter.notify_response_created

    def observe_created(event):
        result = original_created(event)
        response_created.set()
        return result

    client._response_arbiter.notify_response_created = observe_created
    receive_loop = asyncio.create_task(client.handle_messages())
    retired.feed({"type": "response.created", "response": {"id": "retired"}})
    await asyncio.wait_for(response_created.wait(), timeout=1)
    client._output_transcript_buffer = "retired transcript"
    client._audio_delta_count = 1
    retired.feed({"type": "response.done", "response": {"id": "retired"}})
    await asyncio.wait_for(callback_started.wait(), timeout=1)

    replacement = _QueueSocket()
    client.ws = replacement
    client._on_connection_attached()
    client._response_arbiter.reset_connection_state()
    client._output_transcript_buffer = "replacement transcript"
    client._audio_delta_count = 9
    client._is_first_transcript_chunk = True
    release_callback.set()
    await asyncio.wait_for(receive_loop, timeout=1)

    assert client._output_transcript_buffer == "replacement transcript"
    assert client._audio_delta_count == 9
    assert client._is_first_transcript_chunk is True
    assert client.ws is replacement
    assert replacement.closed is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_raw_interruption_cannot_cancel_or_clear_replacement_turn() -> None:
    response_created = asyncio.Event()
    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-realtime",
        api_type="gpt",
    )
    retired = _BlockingSendSocket()
    client.ws = retired
    client._on_connection_attached()
    original_created = client._response_arbiter.notify_response_created

    def observe_created(event):
        result = original_created(event)
        response_created.set()
        return result

    client._response_arbiter.notify_response_created = observe_created
    receive_loop = asyncio.create_task(client.handle_messages())
    retired.feed({"type": "response.created", "response": {"id": "retired"}})
    await asyncio.wait_for(response_created.wait(), timeout=1)
    client._current_item_id = "retired-item"
    client._output_transcript_buffer = "retired transcript"
    client._is_first_transcript_chunk = False
    retired.feed({"type": "input_audio_buffer.speech_started"})
    await asyncio.wait_for(retired.send_started.wait(), timeout=1)

    replacement = _QueueSocket()
    client.ws = replacement
    client._on_connection_attached()
    assert client._interrupted is False
    assert client._is_responding is False
    assert client._current_response_id is None
    assert client._current_item_id is None
    client._response_arbiter.reset_connection_state()
    client._is_responding = True
    client._current_response_id = "replacement"
    client._current_item_id = "replacement-item"
    client._output_transcript_buffer = "replacement transcript"
    client._is_first_transcript_chunk = False
    retired.release_send.set()
    await asyncio.wait_for(receive_loop, timeout=1)

    assert [event["type"] for event in retired.sent] == ["response.cancel"]
    assert replacement.sent == []
    assert client._is_responding is True
    assert client._current_response_id == "replacement"
    assert client._current_item_id == "replacement-item"
    assert client._output_transcript_buffer == "replacement transcript"
    assert client._is_first_transcript_chunk is False
    assert client._interrupted is False
    assert retired.closed is True
    assert client.ws is replacement
    assert replacement.closed is False
    assert client._fatal_error_occurred is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_queued_raw_interruption_cancel_cannot_use_replacement_socket() -> None:
    response_created = asyncio.Event()
    replacement_speech_started = asyncio.Event()
    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-realtime",
        api_type="gpt",
    )
    retired = _QueueSocket()
    client.ws = retired
    client._on_connection_attached()
    original_created = client._response_arbiter.notify_response_created

    def observe_created(event):
        result = original_created(event)
        response_created.set()
        return result

    client._response_arbiter.notify_response_created = observe_created
    send_gate = _SendGate()
    client._send_semaphore = send_gate
    receive_loop = asyncio.create_task(client.handle_messages())
    retired.feed({"type": "response.created", "response": {"id": "retired"}})
    await asyncio.wait_for(response_created.wait(), timeout=1)
    retired.feed({"type": "input_audio_buffer.speech_started"})
    await asyncio.wait_for(send_gate.entered.wait(), timeout=1)

    replacement = _QueueSocket()
    client.ws = replacement
    client._on_connection_attached()
    assert client._interrupted is False
    assert client._is_responding is False
    assert client._current_response_id is None
    assert client._current_item_id is None
    client._response_arbiter.reset_connection_state()
    assert client._output_transcript_buffer == ""
    assert client._is_first_transcript_chunk is True
    assert client._audio_delta_count == 0
    send_gate.release.set()
    await asyncio.wait_for(receive_loop, timeout=1)

    assert retired.sent == []
    assert replacement.sent == []
    assert client._is_responding is False
    assert client._current_response_id is None
    assert client._current_item_id is None
    assert client._output_transcript_buffer == ""
    assert client._is_first_transcript_chunk is True
    assert client._interrupted is False
    assert retired.closed is True
    assert client.ws is replacement
    assert replacement.closed is False
    assert client._fatal_error_occurred is False

    original_vad_started = client._response_arbiter.notify_server_vad_started

    def observe_replacement_speech_started() -> None:
        original_vad_started()
        replacement_speech_started.set()

    client._response_arbiter.notify_server_vad_started = (
        observe_replacement_speech_started
    )
    replacement_loop = asyncio.create_task(client.handle_messages())
    replacement.feed({"type": "input_audio_buffer.speech_started"})
    await asyncio.wait_for(replacement_speech_started.wait(), timeout=1)

    assert replacement.sent == []
    assert client._is_responding is False
    assert client._current_response_id is None
    assert client._current_item_id is None
    replacement.finish()
    await asyncio.wait_for(replacement_loop, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retired_raw_interruption_send_failure_preserves_replacement() -> None:
    response_created = asyncio.Event()
    connection_errors: list[str] = []
    background_coroutines: list = []

    async def on_connection_error(error: str) -> None:
        connection_errors.append(error)

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-realtime",
        api_type="gpt",
        on_connection_error=on_connection_error,
    )

    def observe_background_coroutine(coroutine) -> None:
        background_coroutines.append(coroutine)
        coroutine.close()

    client._fire_task = observe_background_coroutine
    retired = _FailingBlockingSendSocket()
    client.ws = retired
    client._on_connection_attached()
    original_created = client._response_arbiter.notify_response_created

    def observe_created(event):
        result = original_created(event)
        response_created.set()
        return result

    client._response_arbiter.notify_response_created = observe_created
    receive_loop = asyncio.create_task(client.handle_messages())
    retired.feed({"type": "response.created", "response": {"id": "retired"}})
    await asyncio.wait_for(response_created.wait(), timeout=1)
    retired.feed({"type": "input_audio_buffer.speech_started"})
    await asyncio.wait_for(retired.send_started.wait(), timeout=1)

    replacement = _QueueSocket()
    client.ws = replacement
    client._on_connection_attached()
    assert client._interrupted is False
    assert client._is_responding is False
    assert client._current_response_id is None
    assert client._current_item_id is None
    client._response_arbiter.reset_connection_state()
    client._is_responding = True
    client._current_response_id = "replacement"
    client._current_item_id = "replacement-item"
    client._output_transcript_buffer = "replacement transcript"
    retired.release_send.set()
    await asyncio.wait_for(receive_loop, timeout=1)

    assert connection_errors == []
    assert background_coroutines == []
    assert client._fatal_error_occurred is False
    assert client.ws is replacement
    assert replacement.sent == []
    assert replacement.closed is False
    assert retired.closed is True
    assert client._interrupted is False
    assert client._is_responding is True
    assert client._current_response_id == "replacement"
    assert client._current_item_id == "replacement-item"
    assert client._output_transcript_buffer == "replacement transcript"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_raw_turn_finished_callback_cannot_rotate_replacement_sid() -> None:
    response_created = asyncio.Event()
    callback_started = asyncio.Event()
    release_callback = asyncio.Event()
    rotations: list[str] = []

    async def on_response_done() -> None:
        callback_started.set()
        await release_callback.wait()

    async def on_sid_rotate() -> None:
        rotations.append("rotated")

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="free-model",
        api_type="free",
        on_response_done=on_response_done,
        on_sid_rotate=on_sid_rotate,
    )
    client._has_server_vad = False
    retired = _QueueSocket()
    client.ws = retired
    client._on_connection_attached()
    original_created = client._response_arbiter.notify_response_created

    def observe_created(event):
        result = original_created(event)
        response_created.set()
        return result

    client._response_arbiter.notify_response_created = observe_created
    receive_loop = asyncio.create_task(client.handle_messages())
    retired.feed({"type": "response.created", "response": {"id": "retired"}})
    await asyncio.wait_for(response_created.wait(), timeout=1)
    retired.feed({"type": "response.done", "response": {"id": "retired"}})
    await asyncio.wait_for(callback_started.wait(), timeout=1)

    replacement = _QueueSocket()
    client.ws = replacement
    client._on_connection_attached()
    client._response_arbiter.reset_connection_state()
    client._is_responding = True
    client._current_response_id = "replacement"
    client._current_item_id = "replacement-item"
    client._output_transcript_buffer = "replacement transcript"
    client._is_first_transcript_chunk = False
    release_callback.set()
    await asyncio.wait_for(receive_loop, timeout=1)

    assert rotations == []
    assert client._is_responding is True
    assert client._current_response_id == "replacement"
    assert client._current_item_id == "replacement-item"
    assert client._output_transcript_buffer == "replacement transcript"
    assert client._is_first_transcript_chunk is False
    assert retired.closed is True
    assert client.ws is replacement
    assert replacement.closed is False
    assert client._fatal_error_occurred is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gemini_vad_sos_retires_the_previous_turns_tool() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()
    release = asyncio.Event()

    async def handler(call):
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gemini-live",
        api_type="gemini",
        on_tool_call=handler,
    )
    session = _GeminiSession()
    client._gemini_session = session
    client.ws = session
    client._on_connection_attached()
    generation = client._connection_generation
    await client._process_gemini_response(
        _gemini_response(calls=(("call-1", "lookup"),)),
        provider_session=session,
        connection_generation=generation,
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    await client._process_gemini_response(
        _gemini_response(
            vad_signal_type=SimpleNamespace(value="VAD_SIGNAL_TYPE_SOS")
        ),
        provider_session=session,
        connection_generation=generation,
    )
    await asyncio.wait_for(cancelled.wait(), timeout=1)
    release.set()
    await _wait_for_tool_tasks(client)

    assert session.tool_responses == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gemini_cancellation_ids_cancel_only_the_named_call(monkeypatch) -> None:
    import main_logic.omni_realtime_client._gemini_support as gemini_support

    monkeypatch.setattr(
        gemini_support,
        "types",
        SimpleNamespace(FunctionResponse=lambda **kwargs: SimpleNamespace(**kwargs)),
    )
    started = {"first": asyncio.Event(), "second": asyncio.Event()}
    releases = {"first": asyncio.Event(), "second": asyncio.Event()}
    first_cancelled = asyncio.Event()

    async def handler(call):
        started[call.name].set()
        try:
            await releases[call.name].wait()
        except asyncio.CancelledError:
            if call.name == "first":
                first_cancelled.set()
            raise
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gemini-live",
        api_type="gemini",
        on_tool_call=handler,
    )
    session = _GeminiSession()
    client._gemini_session = session
    client.ws = session
    client._on_connection_attached()
    await client._process_gemini_response(
        _gemini_response(calls=(("call-a", "first"), ("call-b", "second"))),
        provider_session=session,
        connection_generation=client._connection_generation,
    )
    await asyncio.wait_for(started["first"].wait(), timeout=1)
    await asyncio.wait_for(started["second"].wait(), timeout=1)

    await client._process_gemini_response(
        _gemini_response(cancelled_ids=("call-a",)),
        provider_session=session,
        connection_generation=client._connection_generation,
    )
    await asyncio.wait_for(first_cancelled.wait(), timeout=1)
    releases["second"].set()
    await _wait_for_tool_tasks(client)

    assert len(session.tool_responses) == 1
    responses = session.tool_responses[0]
    assert [response.id for response in responses] == ["call-b"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gemini_cancellation_suppresses_a_completed_unsent_call(monkeypatch) -> None:
    import main_logic.omni_realtime_client._gemini_support as gemini_support

    monkeypatch.setattr(
        gemini_support,
        "types",
        SimpleNamespace(FunctionResponse=lambda **kwargs: SimpleNamespace(**kwargs)),
    )
    first_finished = asyncio.Event()
    second_started = asyncio.Event()
    release_second = asyncio.Event()

    async def handler(call):
        if call.name == "first":
            first_finished.set()
        else:
            second_started.set()
            await release_second.wait()
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gemini-live",
        api_type="gemini",
        on_tool_call=handler,
    )
    session = _GeminiSession()
    client._gemini_session = session
    client.ws = session
    client._on_connection_attached()
    await client._process_gemini_response(
        _gemini_response(calls=(("call-a", "first"), ("call-b", "second"))),
        provider_session=session,
        connection_generation=client._connection_generation,
    )
    first_retired = asyncio.Event()
    first_task = next(iter(client._tool_tasks_by_call_id["call-a"]))
    first_task.add_done_callback(lambda _task: first_retired.set())
    await asyncio.wait_for(first_finished.wait(), timeout=1)
    await asyncio.wait_for(second_started.wait(), timeout=1)
    await asyncio.wait_for(first_retired.wait(), timeout=1)
    assert "call-a" not in client._tool_tasks_by_call_id

    await client._process_gemini_response(
        _gemini_response(cancelled_ids=("call-a",)),
        provider_session=session,
        connection_generation=client._connection_generation,
    )
    release_second.set()
    await _wait_for_tool_tasks(client)

    assert len(session.tool_responses) == 1
    assert [response.id for response in session.tool_responses[0]] == ["call-b"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_close_cancels_and_awaits_raw_tool_tasks() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def handler(_call):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-realtime",
        api_type="gpt",
        on_tool_call=handler,
    )
    socket = _QueueSocket()
    client.ws = socket
    client._on_connection_attached()
    receive_loop = asyncio.create_task(client.handle_messages())
    socket.feed(_raw_tool_event())
    await asyncio.wait_for(started.wait(), timeout=1)

    await asyncio.wait_for(client.close(), timeout=1)

    assert cancelled.is_set()
    assert client._tool_tasks == set()
    assert socket.closed is True
    socket.finish()
    await asyncio.wait_for(receive_loop, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_raw_tool_task_cancellation_retires_its_arbiter_ticket() -> None:
    handler_finished = asyncio.Event()

    async def handler(call):
        handler_finished.set()
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-realtime",
        api_type="gpt",
        on_tool_call=handler,
    )
    socket = _QueueSocket()
    client.ws = socket
    client._on_connection_attached()
    arbiter = client._response_arbiter
    arbiter.pause_dispatch()
    ticket_enqueued = asyncio.Event()
    captured = {}
    original_enqueue = arbiter.enqueue

    async def observed_enqueue(**kwargs):
        ticket = await original_enqueue(**kwargs)
        if kwargs.get("source") == "tool_result":
            captured["ticket"] = ticket
            ticket_enqueued.set()
        return ticket

    arbiter.enqueue = observed_enqueue
    receive_loop = asyncio.create_task(client.handle_messages())
    socket.feed(_raw_tool_event())
    await asyncio.wait_for(handler_finished.wait(), timeout=1)
    await asyncio.wait_for(ticket_enqueued.wait(), timeout=1)

    client.note_user_turn_started()
    await _wait_for_tool_tasks(client)
    arbiter.resume_dispatch()
    await arbiter.wait_until_idle(timeout=1)

    ticket = captured["ticket"]
    assert ticket.sent.done()
    assert ticket.sent.exception() is not None
    assert socket.sent == []
    socket.finish()
    await asyncio.wait_for(receive_loop, timeout=1)
