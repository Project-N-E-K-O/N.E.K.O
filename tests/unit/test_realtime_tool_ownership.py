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


class _ClosingReceiveSocket(_QueueSocket):
    def __init__(self) -> None:
        super().__init__()
        self.receive_started = asyncio.Event()
        self.close_calls = 0

    async def __anext__(self):
        self.receive_started.set()
        return await super().__anext__()

    async def close(self) -> None:
        self.close_calls += 1
        self.finish()


class _GatedGeminiContext:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.exit_calls = 0

    async def __aexit__(self, *_exc_info) -> None:
        self.exit_calls += 1
        self.entered.set()
        await self.release.wait()


class _GeminiSession:
    def __init__(self) -> None:
        self.tool_responses: list[list] = []
        self.client_contents: list[bool] = []
        self.closed = False

    async def send_tool_response(self, *, function_responses) -> None:
        self.tool_responses.append(list(function_responses))

    async def send_client_content(self, *, turns, turn_complete) -> None:
        # A real Live session always has this; subclasses that care about the
        # ordering against tool responses override it to record more.
        self.client_contents.append(bool(turn_complete))


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


def _raw_tool_event(call_id: str = "call-1", response_id: str = "response-1") -> dict:
    return {
        "type": "response.function_call_arguments.done",
        "response_id": response_id,
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
async def test_proactive_inject_does_not_cancel_current_turn_tool_task(
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
        # The plural is what the batch collector calls; the singular is only a
        # convenience wrapper around it, so patching that one records nothing.
        client._send_tool_results_openai_realtime = AsyncMock()
    client._on_connection_attached()
    if api_type != "gemini":
        sent = asyncio.get_running_loop().create_future()
        sent.set_result(None)
        client._response_arbiter = SimpleNamespace(
            enqueue=AsyncMock(return_value=SimpleNamespace(sent=sent)),
        )
    original_scope = client._tool_scope_generation
    owner = client._capture_tool_task_owner(client.ws)
    call = ToolCall(name="lookup", arguments={}, call_id="call-1")
    if api_type == "gemini":
        client._start_gemini_tool_batch([call], owner)
    else:
        client._start_raw_tool_call(call, owner)
    await asyncio.wait_for(started.wait(), timeout=1)

    await client.inject_text_and_request_response("plugin proactive event")

    assert client._tool_scope_generation == original_scope
    assert not cancelled.is_set()
    if api_type == "gemini":
        client._gemini_session.send_client_content.assert_awaited_once()
    else:
        client._response_arbiter.enqueue.assert_awaited_once()

    release.set()
    await _wait_for_tool_tasks(client)
    if api_type == "gemini":
        client._send_tool_result_gemini.assert_awaited_once()
    else:
        client._send_tool_results_openai_realtime.assert_awaited_once()


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
async def test_send_guard_rejection_after_semaphore_is_not_reported_as_sent() -> None:
    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-realtime",
        api_type="gpt",
    )
    socket = _QueueSocket()
    client.ws = socket
    gate = _SendGate()
    client._send_semaphore = gate
    current = [True]

    sending = asyncio.create_task(
        client.send_event(
            {"type": "response.create"},
            send_guard=lambda: current[0],
        )
    )
    await asyncio.wait_for(gate.entered.wait(), timeout=1)
    current[0] = False
    gate.release.set()

    with pytest.raises(ConnectionError, match="owner is no longer current"):
        await asyncio.wait_for(sending, timeout=1)
    assert socket.sent == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transport_failure_after_owner_retirement_remains_suppressed() -> None:
    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-realtime",
        api_type="gpt",
    )
    socket = _FailingBlockingSendSocket()
    client.ws = socket
    current = [True]

    sending = asyncio.create_task(
        client.send_event(
            {"type": "response.cancel"},
            send_guard=lambda: current[0],
        )
    )
    await asyncio.wait_for(socket.send_started.wait(), timeout=1)
    current[0] = False
    socket.release_send.set()

    await asyncio.wait_for(sending, timeout=1)
    assert client._fatal_error_occurred is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failed_transport_detaches_before_waiting_for_retired_tools() -> None:
    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-realtime",
        api_type="gpt",
    )
    socket = _QueueSocket()
    client.ws = socket
    client._on_connection_attached()
    tool_started = asyncio.Event()
    cancellation_seen = asyncio.Event()
    release_cleanup = asyncio.Event()

    async def slow_cancelled_tool() -> None:
        tool_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellation_seen.set()
            await release_cleanup.wait()
            raise

    tool_task = client._create_tool_task(slow_cancelled_tool(), call_ids=("call-1",))
    await asyncio.wait_for(tool_started.wait(), timeout=1)
    abort_task = asyncio.create_task(
        client._abort_failed_transport("tool owner transport failed")
    )
    try:
        await asyncio.wait_for(cancellation_seen.wait(), timeout=1)

        assert client.ws is None
        assert client._fatal_error_occurred is True
        assert client._local_failure_recovery == (
            client._connection_generation,
            "tool owner transport failed",
        )
        assert socket.closed is False

        await client.send_event({"type": "response.create"})
        assert socket.sent == []
    finally:
        release_cleanup.set()

    await asyncio.wait_for(abort_task, timeout=1)
    await asyncio.gather(tool_task, return_exceptions=True)
    assert socket.closed is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ordinary_close_does_not_turn_receive_eof_into_fatal_close() -> None:
    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-realtime",
        api_type="gpt",
    )
    socket = _ClosingReceiveSocket()
    client.ws = socket
    receiving = asyncio.create_task(client.handle_messages())
    await asyncio.wait_for(socket.receive_started.wait(), timeout=1)

    await asyncio.wait_for(client.close(), timeout=1)
    await asyncio.wait_for(receiving, timeout=1)

    assert client._fatal_error_occurred is False
    assert socket.close_calls == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_replacement_paths_join_one_retired_gemini_context_exit() -> None:
    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gemini-live",
        api_type="gemini",
    )
    retired_context = _GatedGeminiContext()
    retired_session = object()
    client._gemini_context_manager = retired_context
    client._gemini_session = retired_session
    client.ws = retired_session

    first_close = asyncio.create_task(client._close_gemini())
    await asyncio.wait_for(retired_context.entered.wait(), timeout=1)

    replacement_context = _GatedGeminiContext()
    replacement_session = object()
    client._gemini_context_manager = replacement_context
    client._gemini_session = replacement_session
    client.ws = replacement_session
    client._on_connection_attached()

    second_close_started = asyncio.Event()

    async def close_retired_context_again() -> None:
        second_close_started.set()
        await client._close_gemini_context(retired_context, retired_session)

    second_close = asyncio.create_task(close_retired_context_again())
    await asyncio.wait_for(second_close_started.wait(), timeout=1)
    assert retired_context.exit_calls == 1

    retired_context.release.set()
    await asyncio.wait_for(asyncio.gather(first_close, second_close), timeout=1)

    assert retired_context.exit_calls == 1
    assert replacement_context.exit_calls == 0
    assert client._gemini_context_manager is replacement_context
    assert client._gemini_session is replacement_session
    assert client.ws is replacement_session


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ordinary_close_keeps_completed_gemini_context_exit_owned() -> None:
    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gemini-live",
        api_type="gemini",
    )
    retired_exit_finished = asyncio.Event()

    async def finish_retired_exit(*_args) -> None:
        retired_exit_finished.set()

    retired_context = SimpleNamespace(
        __aexit__=AsyncMock(side_effect=finish_retired_exit)
    )
    retired_session = object()
    client._gemini_context_manager = retired_context
    client._gemini_session = retired_session
    client.ws = retired_session
    retired_generation = client._connection_generation

    close_impl = client._detach_for_close()
    await asyncio.wait_for(retired_exit_finished.wait(), timeout=1)

    replacement_context = SimpleNamespace(__aexit__=AsyncMock())
    replacement_session = object()
    client._gemini_context_manager = replacement_context
    client._gemini_session = replacement_session
    client.ws = replacement_session
    client._on_connection_attached()
    assert client._connection_generation != retired_generation

    await asyncio.wait_for(close_impl, timeout=1)

    retired_context.__aexit__.assert_awaited_once_with(None, None, None)
    replacement_context.__aexit__.assert_not_awaited()
    assert client._gemini_context_manager is replacement_context
    assert client._gemini_session is replacement_session
    assert client.ws is replacement_session


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
async def test_parallel_raw_tool_results_are_answered_as_one_batch() -> None:
    """One provider response, one continuation -- no sibling left behind.

    Submitting per result enqueued a response.create with the FIRST output, so
    the arbiter sent that output and immediately started a continuation while
    the sibling's ticket was still queued. The provider saw a continuation
    with a required parallel-call output missing and answered without it.
    """

    host_turn = ["turn-1"]
    started = {"call-1": asyncio.Event(), "call-2": asyncio.Event()}
    release = {"call-1": asyncio.Event(), "call-2": asyncio.Event()}

    async def handler(call):
        started[call.call_id].set()
        await release[call.call_id].wait()
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
    client._current_turn_host_id = host_turn[0]
    receive_loop = asyncio.create_task(client.handle_messages())

    # Same response_id: these are the parallel calls of ONE provider response.
    socket.feed(_raw_tool_event("call-1", response_id="response-1"))
    socket.feed(_raw_tool_event("call-2", response_id="response-1"))
    await asyncio.wait_for(started["call-1"].wait(), timeout=1)
    await asyncio.wait_for(started["call-2"].wait(), timeout=1)

    release["call-1"].set()
    await asyncio.sleep(0.05)
    assert socket.sent == [], (
        "the finished sibling must not start a continuation while the other "
        "call of the same response is still running"
    )

    release["call-2"].set()
    await _wait_for_tool_tasks(client)

    assert [event["type"] for event in socket.sent] == [
        "conversation.item.create",
        "conversation.item.create",
        "response.create",
    ], "every output first, then exactly one continuation"
    answered = [
        event["item"]["call_id"]
        for event in socket.sent
        if event.get("type") == "conversation.item.create"
        and event.get("item", {}).get("type") == "function_call_output"
    ]
    assert answered == ["call-1", "call-2"]

    socket.finish()
    await asyncio.wait_for(receive_loop, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_raw_tool_call_without_response_identity_still_answers() -> None:
    """No response_id means no provable siblings -- answer each on its own.

    The degradation the batch key accepts: without response identity there is
    nothing to group by, and holding a result for a sibling that may never be
    announced would be worse than the per-result shape it replaced.
    """

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
    receive_loop = asyncio.create_task(client.handle_messages())

    event = _raw_tool_event("call-1")
    event.pop("response_id")
    socket.feed(event)
    await _wait_for_socket_sends(socket, 2)
    await _wait_for_tool_tasks(client)

    assert [item["type"] for item in socket.sent] == [
        "conversation.item.create",
        "response.create",
    ]
    assert socket.sent[0]["item"]["call_id"] == "call-1"

    socket.finish()
    await asyncio.wait_for(receive_loop, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sequential_tool_batches_in_one_user_turn_keep_answering() -> None:
    """A later provider response owns the host id its own tool call sampled."""

    host_turn = ["turn-1"]
    started = {"call-1": asyncio.Event(), "call-2": asyncio.Event()}
    release = {"call-1": asyncio.Event(), "call-2": asyncio.Event()}

    async def handler(call):
        started[call.call_id].set()
        await release[call.call_id].wait()
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
    client._current_turn_host_id = host_turn[0]
    receive_loop = asyncio.create_task(client.handle_messages())

    socket.feed(_raw_tool_event("call-1"))
    await asyncio.wait_for(started["call-1"].wait(), timeout=1)
    release["call-1"].set()
    await _wait_for_socket_sends(socket, 2)

    # response.done rotated the host speech id; the response the tool result
    # just created is the one that issues the NEXT call of the same user turn.
    host_turn[0] = "turn-2"
    socket.feed({"type": "response.created", "response": {"id": "tool-response-1"}})
    await asyncio.sleep(0)
    socket.feed(_raw_tool_event("call-2", response_id="tool-response-1"))
    await asyncio.wait_for(started["call-2"].wait(), timeout=1)
    assert client._current_turn_host_id == "turn-2"
    socket.feed({"type": "response.done", "response": {"id": "tool-response-1"}})
    await client._response_arbiter.wait_until_idle(timeout=1)

    release["call-2"].set()
    await _wait_for_tool_tasks(client)

    answered = [
        event["item"]["call_id"]
        for event in socket.sent
        if event.get("type") == "conversation.item.create"
        and event.get("item", {}).get("type") == "function_call_output"
    ]
    assert answered == ["call-1", "call-2"]

    socket.finish()
    await asyncio.wait_for(receive_loop, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_task_result_report_does_not_retire_in_flight_tool_ownership() -> None:
    """prime_context is transport-only: it must not cancel a running tool."""

    started = asyncio.Event()
    cancelled = asyncio.Event()
    release = asyncio.Event()

    async def handler(call):
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

    scope_before = client._tool_scope_generation
    tool_task = next(iter(client._tool_tasks))
    report = asyncio.create_task(client.prime_context("task finished", skipped=False))
    await _wait_for_socket_sends(socket, 1)

    assert socket.sent[0]["item"]["role"] == "user"
    assert client._tool_scope_generation == scope_before
    assert not cancelled.is_set()
    assert not tool_task.cancelled()

    socket.feed(
        {
            "type": "conversation.item.created",
            "item": {"id": socket.sent[0]["item"]["id"], "role": "user"},
        }
    )
    await asyncio.wait_for(report, timeout=1)
    socket.feed({"type": "response.created", "response": {"id": "report-response"}})
    socket.feed({"type": "response.done", "response": {"id": "report-response"}})
    await client._response_arbiter.wait_until_idle(timeout=1)

    release.set()
    await _wait_for_tool_tasks(client)

    answered = [
        event["item"]["call_id"]
        for event in socket.sent
        if event.get("type") == "conversation.item.create"
        and event.get("item", {}).get("type") == "function_call_output"
    ]
    assert answered == ["call-1"]

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
    client._has_server_vad = False
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
    scope_after_speech_started = client._tool_scope_generation

    socket.feed(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "same turn",
        }
    )
    await asyncio.wait_for(transcript_seen.wait(), timeout=1)
    assert not cancelled.is_set()
    assert client._tool_scope_generation == scope_after_speech_started, (
        "the transcript of an utterance speech_started already scoped must "
        "not advance the tool scope a second time"
    )

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
async def test_server_vad_transcript_without_boundary_keeps_same_turn_tool() -> None:
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
    client._has_server_vad = True
    socket = _QueueSocket()
    client.ws = socket
    client._on_connection_attached()
    receive_loop = asyncio.create_task(client.handle_messages())
    socket.feed(_raw_tool_event())
    await asyncio.wait_for(started.wait(), timeout=1)

    socket.feed(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "same server-vad turn",
        }
    )
    await asyncio.wait_for(transcript_seen.wait(), timeout=1)
    release.set()
    await _wait_for_tool_tasks(client)

    assert not cancelled.is_set()
    assert [event["type"] for event in socket.sent] == [
        "conversation.item.create",
        "response.create",
    ]
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
async def test_gemini_tool_after_message_rotation_keeps_current_owner(
    monkeypatch,
) -> None:
    import main_logic.omni_realtime_client._gemini_support as gemini_support

    monkeypatch.setattr(
        gemini_support,
        "types",
        SimpleNamespace(FunctionResponse=lambda **kwargs: SimpleNamespace(**kwargs)),
    )
    host_turn = ["turn-1"]

    async def rotate_host_turn() -> None:
        host_turn[0] = "turn-2"

    async def handler(call):
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gemini-live",
        api_type="gemini",
        get_host_turn_id=lambda: host_turn[0],
        on_new_message=rotate_host_turn,
        on_tool_call=handler,
    )
    session = _GeminiSession()
    client._gemini_session = session
    client.ws = session
    client._on_connection_attached()
    generation = client._connection_generation
    server_content = SimpleNamespace(
        input_transcription=None,
        output_transcription=None,
        model_turn=SimpleNamespace(parts=[]),
        interrupted=False,
        turn_complete=False,
    )

    await client._process_gemini_response(
        SimpleNamespace(
            tool_call=None,
            tool_call_cancellation=None,
            voice_activity_detection_signal=None,
            server_content=server_content,
        ),
        provider_session=session,
        connection_generation=generation,
    )
    assert client._current_turn_host_id == "turn-2"

    await client._process_gemini_response(
        _gemini_response(calls=(("call-1", "lookup"),)),
        provider_session=session,
        connection_generation=generation,
    )
    await _wait_for_tool_tasks(client)

    assert len(session.tool_responses) == 1
    assert [response.id for response in session.tool_responses[0]] == ["call-1"]


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
async def test_replacement_reset_retires_predecessor_arbiter_owner() -> None:
    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-realtime",
        api_type="gpt",
    )
    retired = _QueueSocket()
    client.ws = retired
    client._on_connection_attached()
    arbiter = client._response_arbiter
    retired_ticket = await arbiter.enqueue(source="retired-turn")
    await _wait_for_socket_sends(retired, 1)
    arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "retired-response"}}
    )
    await asyncio.wait_for(asyncio.shield(retired_ticket.sent), timeout=1)

    tool_started = asyncio.Event()
    tool_cancelled = asyncio.Event()
    release_tool = asyncio.Event()

    async def cancellation_resistant_tool() -> None:
        tool_started.set()
        while not release_tool.is_set():
            try:
                await release_tool.wait()
            except asyncio.CancelledError:
                tool_cancelled.set()

    tool_task = client._create_tool_task(cancellation_resistant_tool())
    await asyncio.wait_for(tool_started.wait(), timeout=1)
    closing = asyncio.create_task(client.close())
    await asyncio.wait_for(tool_cancelled.wait(), timeout=1)

    replacement = _QueueSocket()
    client.ws = replacement
    client._on_connection_attached()
    arbiter.reset_connection_state()
    assert arbiter.current_source is None
    assert arbiter._response_owner is None
    with pytest.raises(ConnectionError, match="replaced"):
        await retired_ticket.done

    replacement_ticket = await arbiter.enqueue(source="replacement-turn")
    await _wait_for_socket_sends(replacement, 1)
    arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "replacement-response"}}
    )
    arbiter.notify_response_terminal(
        {
            "type": "response.done",
            "response": {"id": "replacement-response", "status": "completed"},
        }
    )
    await asyncio.wait_for(asyncio.shield(replacement_ticket.done), timeout=1)

    release_tool.set()
    await asyncio.wait_for(closing, timeout=1)
    await asyncio.wait_for(asyncio.gather(tool_task), timeout=1)
    assert retired.closed is True
    assert replacement.closed is False


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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gemini_cancelled_call_cannot_hold_its_siblings_result(monkeypatch) -> None:
    """A handler that ignores cancellation must not stall the whole batch."""

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
        while True:
            try:
                await releases[call.name].wait()
                break
            except asyncio.CancelledError:
                if call.name != "first":
                    raise
                # Cancellation-resistant on purpose: this is the shape that
                # used to hold call-b's function_call_output forever.
                first_cancelled.set()
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

    # Bounded by _TOOL_TASK_CANCEL_TIMEOUT_S, generously: the point is that it
    # arrives at all while call-a is still parked in its handler.
    for _ in range(40):
        if session.tool_responses:
            break
        await asyncio.sleep(0.05)

    assert [r.id for r in session.tool_responses[0]] == ["call-b"]

    releases["first"].set()
    await _wait_for_tool_tasks(client)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_quarantine_does_not_exit_a_context_an_ordinary_close_finished(
    monkeypatch,
) -> None:
    """A settled outcome means the teardown belongs to whoever settled it."""

    import main_logic.omni_realtime_client._responses as responses

    monkeypatch.setattr(responses, "_GEMINI_PROACTIVE_CANCEL_GRACE_SECONDS", 0.05)

    interrupted = asyncio.Event()

    class _QuarantineSession(_GeminiSession):
        async def send_client_content(self, *, turns, turn_complete) -> None:
            interrupted.set()

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gemini-live",
        api_type="gemini",
    )
    session = _QuarantineSession()
    context = _GatedGeminiContext()
    context.release.set()
    client._gemini_session = session
    client._gemini_context_manager = context
    client._on_connection_attached()

    token = "outcome-token"
    client._gemini_proactive_outcome = (token, None, None)
    client._gemini_proactive_outcome_owner = (
        client._connection_generation,
        session,
        token,
        context,
    )

    quarantine = asyncio.create_task(
        client._interrupt_and_quarantine_gemini_proactive_outcome(
            token, error_msg="quarantined"
        )
    )
    await asyncio.wait_for(interrupted.wait(), timeout=1)

    # An ordinary close lands while the quarantine sleeps out its grace period:
    # it exits the context once, drops the session, and settles the outcome.
    await client._close_gemini_context(context, session)
    client._gemini_session = None
    client._gemini_context_manager = None
    client._gemini_proactive_outcome = None
    client._gemini_proactive_outcome_owner = None
    assert context.exit_calls == 1

    await asyncio.wait_for(quarantine, timeout=2)

    assert context.exit_calls == 1, "the one-shot SDK context was exited twice"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gemini_anonymous_parallel_calls_keep_separate_results(monkeypatch) -> None:
    """Gemini may omit ids; both are normalized to "" and must stay distinct."""

    import main_logic.omni_realtime_client._gemini_support as gemini_support

    monkeypatch.setattr(
        gemini_support,
        "types",
        SimpleNamespace(FunctionResponse=lambda **kwargs: SimpleNamespace(**kwargs)),
    )

    async def handler(call):
        return ToolResult(call_id=call.call_id, name=call.name, output={"who": call.name})

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
        _gemini_response(calls=(("", "first"), ("", "second"))),
        provider_session=session,
        connection_generation=client._connection_generation,
    )
    await _wait_for_tool_tasks(client)

    assert len(session.tool_responses) == 1
    outputs = [r.response["who"] for r in session.tool_responses[0]]
    assert outputs == ["first", "second"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gemini_proactive_waits_for_an_active_tool(monkeypatch) -> None:
    """Gemini has no arbiter, so the ordering the raw path gets free is explicit."""

    import main_logic.omni_realtime_client._responses as responses

    monkeypatch.setattr(responses, "_GEMINI_PROACTIVE_TOOL_SETTLE_SECONDS", 5.0)
    started = asyncio.Event()
    release = asyncio.Event()
    order: list[str] = []

    async def handler(call):
        started.set()
        await release.wait()
        order.append("tool")
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    class _OrderedSession(_GeminiSession):
        async def send_client_content(self, *, turns, turn_complete) -> None:
            order.append("proactive")

        async def send_tool_response(self, *, function_responses) -> None:
            order.append("tool_response")
            await super().send_tool_response(function_responses=function_responses)

    monkeypatch.setattr(
        __import__(
            "main_logic.omni_realtime_client._gemini_support",
            fromlist=["types"],
        ),
        "types",
        SimpleNamespace(FunctionResponse=lambda **kwargs: SimpleNamespace(**kwargs)),
    )

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gemini-live",
        api_type="gemini",
        on_tool_call=handler,
    )
    session = _OrderedSession()
    client._gemini_session = session
    client.ws = session
    client._on_connection_attached()

    await client._process_gemini_response(
        _gemini_response(calls=(("call-a", "lookup"),)),
        provider_session=session,
        connection_generation=client._connection_generation,
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    inject = asyncio.create_task(
        client.inject_text_and_request_response("proactive body")
    )
    await asyncio.sleep(0.05)
    assert order == [], "the inject must not overtake a running tool call"

    release.set()
    await asyncio.wait_for(inject, timeout=2)
    await _wait_for_tool_tasks(client)

    assert order[:2] == ["tool", "tool_response"]
    assert "proactive" in order
    assert order.index("tool_response") < order.index("proactive")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gemini_proactive_is_not_blocked_forever_by_a_stuck_tool(monkeypatch) -> None:
    """The removed gate had no TTL; this one must always let the message out."""

    import main_logic.omni_realtime_client._responses as responses

    monkeypatch.setattr(responses, "_GEMINI_PROACTIVE_TOOL_SETTLE_SECONDS", 0.05)
    started = asyncio.Event()
    never = asyncio.Event()
    sent: list[str] = []

    async def handler(call):
        started.set()
        await never.wait()
        return ToolResult(call_id=call.call_id, name=call.name, output={})

    class _RecordingSession(_GeminiSession):
        async def send_client_content(self, *, turns, turn_complete) -> None:
            sent.append("proactive")

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gemini-live",
        api_type="gemini",
        on_tool_call=handler,
    )
    session = _RecordingSession()
    client._gemini_session = session
    client.ws = session
    client._on_connection_attached()

    await client._process_gemini_response(
        _gemini_response(calls=(("call-a", "lookup"),)),
        provider_session=session,
        connection_generation=client._connection_generation,
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    await asyncio.wait_for(
        client.inject_text_and_request_response("proactive body"), timeout=2
    )

    assert sent == ["proactive"]

    never.set()
    await _wait_for_tool_tasks(client)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tool_captured_after_a_mid_response_host_rotation_is_withheld() -> None:
    """The host-id clause: a rotation INSIDE the owning provider response.

    Not covered by scope_generation -- nothing here starts a new user turn.
    The Gemini path resamples _current_turn_host_id after on_new_message
    precisely so a legal call does not land in this state; without a test the
    clause itself was dead code (deleting it broke nothing).
    """

    host_turn = ["turn-1"]
    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-realtime",
        api_type="gpt",
        get_host_turn_id=lambda: host_turn[0],
        on_tool_call=AsyncMock(),
    )
    socket = _QueueSocket()
    client.ws = socket
    client._on_connection_attached()
    client._current_turn_host_id = "turn-1"

    aligned = client._capture_tool_task_owner(socket)
    assert client._tool_task_owner_is_current(aligned) is True

    # The host moved on mid-response without any new user input, so the
    # provider turn's snapshot and the live id no longer agree.
    host_turn[0] = "turn-2"
    rotated = client._capture_tool_task_owner(socket)

    assert client._tool_scope_generation == aligned.scope_generation, (
        "premise: no new user turn -- scope_generation cannot be what rejects"
    )
    assert client._tool_task_owner_is_current(rotated) is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gemini_proactive_abandons_itself_if_a_user_turn_starts_while_waiting(
    monkeypatch,
) -> None:
    """The tool wait is a window the inject did not previously have.

    Waiting for tools yields for up to the settle budget, and a real user turn
    can begin inside it. Sending afterwards would put the notification into
    that turn -- Gemini treats client content as an interruption -- so the
    inject retires itself. The proactive caller catches this and keeps the
    callback queued for the next idle hook, so the message is deferred rather
    than lost.
    """

    import main_logic.omni_realtime_client._responses as responses

    monkeypatch.setattr(responses, "_GEMINI_PROACTIVE_TOOL_SETTLE_SECONDS", 5.0)
    started = asyncio.Event()
    release = asyncio.Event()
    sent: list[str] = []

    async def handler(call):
        started.set()
        await release.wait()
        return ToolResult(call_id=call.call_id, name=call.name, output={})

    class _RecordingSession(_GeminiSession):
        async def send_client_content(self, *, turns, turn_complete) -> None:
            sent.append("proactive")

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gemini-live",
        api_type="gemini",
        on_tool_call=handler,
    )
    session = _RecordingSession()
    client._gemini_session = session
    client.ws = session
    client._on_connection_attached()

    await client._process_gemini_response(
        _gemini_response(calls=(("call-a", "lookup"),)),
        provider_session=session,
        connection_generation=client._connection_generation,
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    inject = asyncio.create_task(
        client.inject_text_and_request_response("proactive body")
    )
    await asyncio.sleep(0)
    # A real user turn arrives while the inject is parked in the tool wait.
    # This also cancels the tool task, which is what ends the wait.
    client.note_user_turn_started()

    with pytest.raises(RuntimeError, match="superseded by a new user turn"):
        await asyncio.wait_for(inject, timeout=2)

    assert sent == []

    release.set()
    await _wait_for_tool_tasks(client)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gemini_proactive_still_sends_when_only_a_model_response_started(
    monkeypatch,
) -> None:
    """An active model response is NOT the supersede signal.

    Waiting for tools makes "the tool returned and generation continued" the
    normal outcome, so an active response during the wait says nothing about
    whose turn it is. Retiring the inject on it would silently drop proactive
    messages on the healthy path -- only a new USER turn (which advances
    _tool_scope_generation) may retire it. Guarding an active response is
    documented as the caller's job on is_active_response.
    """

    import main_logic.omni_realtime_client._responses as responses

    monkeypatch.setattr(responses, "_GEMINI_PROACTIVE_TOOL_SETTLE_SECONDS", 5.0)
    started = asyncio.Event()
    release = asyncio.Event()
    sent: list[str] = []

    async def handler(call):
        started.set()
        await release.wait()
        return ToolResult(call_id=call.call_id, name=call.name, output={})

    class _RecordingSession(_GeminiSession):
        async def send_client_content(self, *, turns, turn_complete) -> None:
            sent.append("proactive")

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gemini-live",
        api_type="gemini",
        on_tool_call=handler,
    )
    session = _RecordingSession()
    client._gemini_session = session
    client.ws = session
    client._on_connection_attached()

    await client._process_gemini_response(
        _gemini_response(calls=(("call-a", "lookup"),)),
        provider_session=session,
        connection_generation=client._connection_generation,
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    scope_before = client._tool_scope_generation
    inject = asyncio.create_task(
        client.inject_text_and_request_response("proactive body")
    )
    await asyncio.sleep(0)
    # The model is answering -- no new user turn, so this inject stays valid.
    client._is_responding = True
    assert client.is_active_response() is True
    release.set()

    await asyncio.wait_for(inject, timeout=2)
    await _wait_for_tool_tasks(client)

    assert client._tool_scope_generation == scope_before
    assert sent == ["proactive"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gemini_proactive_does_not_wait_out_a_provider_cancelled_call(
    monkeypatch,
) -> None:
    """A cancelled call that never exits must not tax every later inject.

    Its result is filtered out on arrival and the collector has already
    stopped waiting for it, but the task lives in _tool_tasks until the
    handler returns -- which a handler swallowing CancelledError never does.
    Waiting for it would spend the whole settle budget on an answer that
    cannot come, once per proactive message for the rest of the connection.
    """

    import main_logic.omni_realtime_client._responses as responses

    # Deliberately far larger than the assertion timeout below: the test
    # fails by TAKING the budget, not by any assertion on its value.
    monkeypatch.setattr(responses, "_GEMINI_PROACTIVE_TOOL_SETTLE_SECONDS", 30.0)
    monkeypatch.setattr(
        __import__(
            "main_logic.omni_realtime_client._gemini_support",
            fromlist=["types"],
        ),
        "types",
        SimpleNamespace(FunctionResponse=lambda **kwargs: SimpleNamespace(**kwargs)),
    )

    started = {"stuck": asyncio.Event(), "quick": asyncio.Event()}
    cancelled = asyncio.Event()
    never = asyncio.Event()
    sent: list[str] = []

    async def handler(call):
        started[call.name].set()
        if call.name == "quick":
            return ToolResult(call_id=call.call_id, name=call.name, output={})
        while True:
            try:
                await never.wait()
                break
            except asyncio.CancelledError:
                cancelled.set()  # swallowed on purpose
        return ToolResult(call_id=call.call_id, name=call.name, output={})

    class _RecordingSession(_GeminiSession):
        async def send_client_content(self, *, turns, turn_complete) -> None:
            sent.append("proactive")

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gemini-live",
        api_type="gemini",
        on_tool_call=handler,
    )
    session = _RecordingSession()
    client._gemini_session = session
    client.ws = session
    client._on_connection_attached()

    await client._process_gemini_response(
        _gemini_response(calls=(("call-a", "stuck"), ("call-b", "quick"))),
        provider_session=session,
        connection_generation=client._connection_generation,
    )
    await asyncio.wait_for(started["stuck"].wait(), timeout=1)
    await asyncio.wait_for(started["quick"].wait(), timeout=1)

    await client._process_gemini_response(
        _gemini_response(cancelled_ids=("call-a",)),
        provider_session=session,
        connection_generation=client._connection_generation,
    )
    await asyncio.wait_for(cancelled.wait(), timeout=1)

    try:
        # call-a is retired but still resident in _tool_tasks; the inject must
        # not wait for it. With an unfiltered scan this blocks for the full
        # budget instead.
        await asyncio.wait_for(
            client.inject_text_and_request_response("proactive body"), timeout=2
        )
        assert sent == ["proactive"]
    finally:
        # In a `finally` on purpose: the stuck handler swallows cancellation,
        # so on the failure path an un-released one keeps the event loop from
        # closing and this fails as a HANG rather than an assertion.
        never.set()
        await _wait_for_tool_tasks(client)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gemini_proactive_reacts_to_a_cancellation_that_lands_mid_wait(
    monkeypatch,
) -> None:
    """The retired set is recomputed, not snapshotted before the wait.

    Cancellations arrive asynchronously, so one landing INSIDE the settle wait
    must still release it. Snapshotting the filter once leaves the cancelled
    task in the wait set and spends the whole budget on a call that can no
    longer answer.
    """

    import main_logic.omni_realtime_client._responses as responses

    # Far larger than the assertion timeout: this fails by TAKING the budget.
    monkeypatch.setattr(responses, "_GEMINI_PROACTIVE_TOOL_SETTLE_SECONDS", 30.0)
    monkeypatch.setattr(
        __import__(
            "main_logic.omni_realtime_client._gemini_support",
            fromlist=["types"],
        ),
        "types",
        SimpleNamespace(FunctionResponse=lambda **kwargs: SimpleNamespace(**kwargs)),
    )

    started = asyncio.Event()
    cancelled = asyncio.Event()
    never = asyncio.Event()
    sent: list[str] = []

    async def handler(call):
        started.set()
        while True:
            try:
                await never.wait()
                break
            except asyncio.CancelledError:
                cancelled.set()  # swallowed on purpose
        return ToolResult(call_id=call.call_id, name=call.name, output={})

    class _RecordingSession(_GeminiSession):
        async def send_client_content(self, *, turns, turn_complete) -> None:
            sent.append("proactive")

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gemini-live",
        api_type="gemini",
        on_tool_call=handler,
    )
    session = _RecordingSession()
    client._gemini_session = session
    client.ws = session
    client._on_connection_attached()

    await client._process_gemini_response(
        _gemini_response(calls=(("call-a", "lookup"),)),
        provider_session=session,
        connection_generation=client._connection_generation,
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    inject = asyncio.create_task(
        client.inject_text_and_request_response("proactive body")
    )
    try:
        # Let the inject reach the settle wait BEFORE the cancellation exists,
        # so a one-shot filter cannot have seen it.
        await asyncio.sleep(0.05)
        assert not inject.done()
        await client._process_gemini_response(
            _gemini_response(cancelled_ids=("call-a",)),
            provider_session=session,
            connection_generation=client._connection_generation,
        )
        await asyncio.wait_for(cancelled.wait(), timeout=1)

        await asyncio.wait_for(inject, timeout=3)
        assert sent == ["proactive"]
    finally:
        # The handler swallows cancellation; without this a failure hangs the
        # loop teardown instead of reporting.
        never.set()
        await _wait_for_tool_tasks(client)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gemini_proactive_skips_a_tool_retired_by_a_scope_advance(
    monkeypatch,
) -> None:
    """Scope retirement leaves no call-id marker, so it must be recorded.

    _advance_tool_scope cancels every tool AND clears the cancelled-id set in
    the same breath, so a handler that swallows CancelledError survives with
    nothing pointing at it. Re-deriving retirement from ids cannot see that
    route -- only recording it where it happens can.
    """

    import main_logic.omni_realtime_client._responses as responses

    monkeypatch.setattr(responses, "_GEMINI_PROACTIVE_TOOL_SETTLE_SECONDS", 30.0)

    started = asyncio.Event()
    cancelled = asyncio.Event()
    never = asyncio.Event()
    sent: list[str] = []

    async def handler(call):
        started.set()
        while True:
            try:
                await never.wait()
                break
            except asyncio.CancelledError:
                cancelled.set()  # swallowed on purpose
        return ToolResult(call_id=call.call_id, name=call.name, output={})

    class _RecordingSession(_GeminiSession):
        async def send_client_content(self, *, turns, turn_complete) -> None:
            sent.append("proactive")

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gemini-live",
        api_type="gemini",
        on_tool_call=handler,
    )
    session = _RecordingSession()
    client._gemini_session = session
    client.ws = session
    client._on_connection_attached()

    await client._process_gemini_response(
        _gemini_response(calls=(("call-a", "lookup"),)),
        provider_session=session,
        connection_generation=client._connection_generation,
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    # Retired by a USER TURN, not by a provider cancellation -- so the
    # cancelled-id set is empty afterwards.
    client.note_user_turn_started()
    await asyncio.wait_for(cancelled.wait(), timeout=1)
    assert not client._cancelled_tool_call_ids

    try:
        await asyncio.wait_for(
            client.inject_text_and_request_response("proactive body"), timeout=2
        )
        assert sent == ["proactive"]
    finally:
        never.set()
        await _wait_for_tool_tasks(client)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_replacement_connection_retires_the_predecessors_proactive_outcome() -> None:
    """Otherwise the replacement rejects its OWN proactive work as pending."""

    rejections: list[str] = []

    def on_rejected(reason: str) -> None:
        # Sync on purpose: _settle_gemini_proactive_inject CALLS this, it does
        # not await it. An async double here silently records nothing.
        rejections.append(reason)

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gemini-live",
        api_type="gemini",
    )
    retired_session = _GeminiSession()
    client._gemini_session = retired_session
    client.ws = retired_session
    client._on_connection_attached()

    token = "predecessor-token"
    client._gemini_proactive_outcome = (token, on_rejected, None)
    client._gemini_proactive_outcome_owner = (
        client._connection_generation,
        retired_session,
        token,
        object(),
    )
    client._proactive_inject_outcome_token = token
    client._proactive_inject_awaiting_outcome = True

    replacement = _GeminiSession()
    client._gemini_session = replacement
    client.ws = replacement
    client._on_connection_attached()

    assert client._gemini_proactive_outcome is None
    assert client._gemini_proactive_outcome_owner is None
    assert client._proactive_inject_awaiting_outcome is False

    # The replacement can now register its own outcome instead of being told
    # another inject is pending.
    await client.inject_text_and_request_response(
        "replacement body", on_rejected=on_rejected
    )
    assert client._gemini_proactive_outcome is not None
    assert client._gemini_proactive_outcome_owner[1] is replacement


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_failing_retired_inject_does_not_clear_the_successors_outcome() -> None:
    """The failure settle is owner-scoped, like the cancellation one beside it.

    A retired connection's send_client_content can raise long after a
    replacement attached and registered its own outcome. Settling
    unconditionally would clear the SUCCESSOR's, and its caller would then
    never see a completion or rejection -- it would wait out its whole
    timeout instead.

    Reachable because retiring the predecessor's outcome on attach is what
    lets the replacement register one at all; before that it was refused as
    "another inject is pending", so there was nothing to clobber.
    """

    send_started = asyncio.Event()
    fail_send = asyncio.Event()

    class _StallingSession(_GeminiSession):
        async def send_client_content(self, *, turns, turn_complete) -> None:
            send_started.set()
            await fail_send.wait()
            raise RuntimeError("retired SDK send failed")

    def on_rejected(_reason: str) -> None:
        return None

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gemini-live",
        api_type="gemini",
    )
    retired = _StallingSession()
    client._gemini_session = retired
    client.ws = retired
    client._on_connection_attached()

    retired_inject = asyncio.create_task(
        client.inject_text_and_request_response("retired body", on_rejected=on_rejected)
    )
    await asyncio.wait_for(send_started.wait(), timeout=1)

    replacement = _GeminiSession()
    client._gemini_session = replacement
    client.ws = replacement
    client._on_connection_attached()

    await client.inject_text_and_request_response(
        "replacement body", on_rejected=on_rejected
    )
    successor_owner = client._gemini_proactive_outcome_owner
    assert successor_owner is not None and successor_owner[1] is replacement
    successor_outcome = client._gemini_proactive_outcome

    fail_send.set()
    with pytest.raises(RuntimeError, match="retired SDK send failed"):
        await asyncio.wait_for(retired_inject, timeout=2)

    assert client._gemini_proactive_outcome is successor_outcome
    assert client._gemini_proactive_outcome_owner is successor_owner
    assert client._proactive_inject_awaiting_outcome is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_expiring_proactive_inject_leaves_a_new_user_turn_alone(
    monkeypatch,
) -> None:
    """A stale inject's quarantine must not take the user's turn down with it.

    The connection and the Gemini session both survive a new user turn, so
    neither tells one apart -- only the tool scope moves. Both halves of the
    quarantine act on whatever is generating NOW: client_content interrupts
    it, and the retirement marks the session fatal and closes it. After a real
    user turn owns the generation, either one kills THEIR response.
    """

    import main_logic.omni_realtime_client._responses as responses

    monkeypatch.setattr(responses, "_GEMINI_PROACTIVE_CANCEL_GRACE_SECONDS", 0.01)

    rejections: list[str] = []

    def on_rejected(reason: str) -> None:
        # Sync on purpose: _settle_gemini_proactive_inject CALLS this, it does
        # not await it. An async double here silently records nothing.
        rejections.append(reason)

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gemini-live",
        api_type="gemini",
    )
    session = _GeminiSession()
    client._gemini_session = session
    client.ws = session
    client._on_connection_attached()

    await client.inject_text_and_request_response(
        "proactive body", on_rejected=on_rejected
    )
    token = client._gemini_proactive_outcome[0]
    sends_after_inject = len(session.client_contents)

    # A real user turn takes over the generation while the inject is pending.
    client.note_user_turn_started()

    await client._interrupt_and_quarantine_gemini_proactive_outcome(
        token, error_msg="timed out"
    )

    # No interrupt aimed at the user's generation...
    assert len(session.client_contents) == sends_after_inject
    # ...and the session was not retired out from under them.
    assert client._fatal_error_occurred is False
    assert client._gemini_session is session
    # The stale inject is still settled, so its caller is told and can retry.
    assert client._gemini_proactive_outcome is None
    assert rejections == ["timed out"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_user_turn_terminal_rejects_the_stale_proactive_outcome() -> None:
    """The user's turn_complete must not mark a stale notification delivered.

    Connection and session both survive a new user turn, so the settle
    predicate could not tell that terminal from the proactive inject's own.
    Reporting it as a COMPLETION drops the plugin callback on the strength of
    a response that was abandoned; it has to be rejected so the caller
    re-queues it for the live turn.
    """

    completed: list[bool] = []
    rejected: list[str] = []

    def on_completed() -> None:
        completed.append(True)

    def on_rejected(reason: str) -> None:
        rejected.append(reason)

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gemini-live",
        api_type="gemini",
    )
    session = _GeminiSession()
    client._gemini_session = session
    client.ws = session
    client._on_connection_attached()

    await client.inject_text_and_request_response(
        "proactive body", on_rejected=on_rejected, on_completed=on_completed
    )
    assert client._gemini_proactive_outcome is not None

    # A real user turn takes over; its terminal arrives on the SAME connection
    # and the SAME session, so only the scope distinguishes it.
    client.note_user_turn_started()

    terminal = SimpleNamespace(
        tool_call=None,
        tool_call_cancellation=None,
        voice_activity_detection_signal=None,
        server_content=SimpleNamespace(
            model_turn=None,
            output_transcription=None,
            input_transcription=None,
            turn_complete=True,
            interrupted=False,
        ),
    )
    await client._process_gemini_response(
        terminal,
        provider_session=session,
        connection_generation=client._connection_generation,
    )

    assert completed == [], "the abandoned response must not count as delivered"
    assert rejected and "abandoned by a new user turn" in rejected[0]
    assert client._gemini_proactive_outcome is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancelled_prompt_ephemeral_leaves_a_new_user_turn_alone() -> None:
    """The cancellation cleanup needs the same fence as the timeout path.

    With no arbiter ticket -- the Gemini shape -- the cleanup falls through to
    a raw cancel_response(), which is a client_content interrupt aimed at
    whatever is generating NOW. After a real user turn took over, that
    cancels THEIR response. Sibling branches of one function; the timeout arm
    got the guard first and this one was left behind.
    """

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gemini-live",
        api_type="gemini",
    )
    session = _GeminiSession()
    client._gemini_session = session
    client.ws = session
    client._on_connection_attached()
    client._ai_recent_activity_time = 0
    client._user_recent_activity_time = 0
    client._client_vad_active = False
    client._client_vad_last_speech_time = 0

    ephemeral = asyncio.create_task(client.prompt_ephemeral(language="zh"))
    # Let it get past the inject and park on the outcome.
    for _ in range(50):
        await asyncio.sleep(0.01)
        if session.client_contents:
            break
    assert session.client_contents, "prompt_ephemeral never sent its inject"
    sends_after_inject = len(session.client_contents)

    # A real user turn takes over the generation, then the caller is cancelled.
    client.note_user_turn_started()
    ephemeral.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(ephemeral, timeout=2)

    assert len(session.client_contents) == sends_after_inject, (
        "the cancellation cleanup interrupted the user's own generation"
    )


def _no_server_vad_client(**hooks):
    """A client whose no-server-VAD transcript fallback is really reachable.

    The lanlan.app host is load-bearing, not decoration: ``_is_free_proxy``
    keys on it, and that is what makes ``_has_server_vad`` False. With any
    other host the same ``api_type="free"`` client HAS server VAD and the
    transcript branch below never reaches the fallback at all.
    """

    client = OmniRealtimeClient(
        "wss://www.lanlan.app/realtime",
        "test-key",
        model="free-model",
        api_type="free",
        **hooks,
    )
    assert client._has_server_vad is False, (
        "this helper exists to cover the transcript-only turn boundary"
    )
    return client


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transcript_of_a_new_utterance_retires_a_dropped_turns_tool() -> None:
    """A speech_started whose transcript never arrives must not scope the NEXT turn.

    The proxy shape this covers is inconsistent on purpose, because the real
    ones are: it announces speech_started for turn N, drops turn N's
    transcript, then drops speech_started for turn N+1 and delivers ITS
    transcript. Nothing between those two turns clears a plain pending flag --
    not speech_stopped, not response.done -- so the new turn's transcript read
    as already scoped, note_user_turn_started was skipped, and a
    cancellation-resistant tool from turn N could still answer into turn N+1.
    """

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
            # Re-raised, not swallowed: a swallowed CancelledError turns a
            # failing assertion below into a hang at loop teardown instead of
            # a failure.
            raise
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    async def on_input_transcript(_transcript: str) -> None:
        transcript_seen.set()

    client = _no_server_vad_client(
        on_input_transcript=on_input_transcript,
        on_tool_call=handler,
    )
    socket = _QueueSocket()
    client.ws = socket
    client._on_connection_attached()
    receive_loop = asyncio.create_task(client.handle_messages())

    # Turn N: the proxy announces the utterance, then never transcribes it.
    socket.feed({"type": "input_audio_buffer.speech_started", "item_id": "item-a"})
    socket.feed(_raw_tool_event())
    await asyncio.wait_for(started.wait(), timeout=1)
    scope_after_turn_n = client._tool_scope_generation

    # Turn N+1: no speech_started at all, only the completed transcript -- of a
    # DIFFERENT utterance.
    socket.feed(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "item-b",
            "transcript": "the next thing the user said",
        }
    )
    await asyncio.wait_for(transcript_seen.wait(), timeout=1)

    assert client._tool_scope_generation != scope_after_turn_n, (
        "a transcript for an utterance no speech_started scoped begins a new "
        "user turn, whatever an earlier utterance left behind"
    )
    await asyncio.wait_for(cancelled.wait(), timeout=1)
    release.set()
    await _wait_for_tool_tasks(client)

    assert socket.sent == []
    socket.finish()
    await asyncio.wait_for(receive_loop, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transcript_of_its_own_utterance_does_not_rescope_the_turn() -> None:
    """The healthy no-VAD path: one turn, one note_user_turn_started."""

    transcript_seen = asyncio.Event()

    async def on_input_transcript(_transcript: str) -> None:
        transcript_seen.set()

    client = _no_server_vad_client(on_input_transcript=on_input_transcript)
    socket = _QueueSocket()
    client.ws = socket
    client._on_connection_attached()
    receive_loop = asyncio.create_task(client.handle_messages())

    socket.feed({"type": "input_audio_buffer.speech_started", "item_id": "item-a"})
    await asyncio.sleep(0)
    scope_after_speech_started = client._tool_scope_generation

    socket.feed(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "item-a",
            "transcript": "what the user just said",
        }
    )
    await asyncio.wait_for(transcript_seen.wait(), timeout=1)

    assert client._tool_scope_generation == scope_after_speech_started

    socket.finish()
    await asyncio.wait_for(receive_loop, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_late_transcript_does_not_rescope_an_already_started_turn() -> None:
    """Input transcription is its own job; its output can land out of order.

    Turn N's transcript arriving after turn N+1 has begun must not retire
    turn N+1's tool work -- which is what expiring the marker on a response
    lifecycle point would have caused.
    """

    started = asyncio.Event()
    cancelled = asyncio.Event()
    transcripts: list[str] = []
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

    async def on_input_transcript(transcript: str) -> None:
        transcripts.append(transcript)
        transcript_seen.set()

    client = _no_server_vad_client(
        on_input_transcript=on_input_transcript,
        on_tool_call=handler,
    )
    socket = _QueueSocket()
    client.ws = socket
    client._on_connection_attached()
    receive_loop = asyncio.create_task(client.handle_messages())

    socket.feed({"type": "input_audio_buffer.speech_started", "item_id": "item-a"})
    socket.feed({"type": "input_audio_buffer.speech_started", "item_id": "item-b"})
    socket.feed(_raw_tool_event())
    await asyncio.wait_for(started.wait(), timeout=1)
    scope_after_turn_b = client._tool_scope_generation

    # item-a's transcript only shows up now, a turn late.
    socket.feed(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "item-a",
            "transcript": "the previous utterance",
        }
    )
    await asyncio.wait_for(transcript_seen.wait(), timeout=1)

    assert transcripts == ["the previous utterance"]
    assert client._tool_scope_generation == scope_after_turn_b, (
        "a transcript for an utterance already scoped is not a new user turn"
    )
    assert not cancelled.is_set()

    release.set()
    await _wait_for_tool_tasks(client)
    socket.finish()
    await asyncio.wait_for(receive_loop, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gemini_proactive_answers_the_calls_it_gave_up_on(monkeypatch) -> None:
    """A tool the settle budget abandoned still gets a function_call_output.

    The inject that follows interrupts the generation that issued the call, so
    without this the conversation keeps a function_call nobody ever replied to.
    The abandoned reply goes out BEFORE the inject, and the call is retired so
    a result arriving afterwards is dropped instead of landing in the
    proactive turn.
    """

    import main_logic.omni_realtime_client._responses as responses

    monkeypatch.setattr(responses, "_GEMINI_PROACTIVE_TOOL_SETTLE_SECONDS", 0.05)
    started = asyncio.Event()
    release = asyncio.Event()
    order: list[str] = []

    async def handler(call):
        started.set()
        await release.wait()
        order.append("tool")
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    class _OrderedSession(_GeminiSession):
        async def send_client_content(self, *, turns, turn_complete) -> None:
            order.append("proactive")

        async def send_tool_response(self, *, function_responses) -> None:
            order.append("tool_response")
            await super().send_tool_response(function_responses=function_responses)

    monkeypatch.setattr(
        __import__(
            "main_logic.omni_realtime_client._gemini_support",
            fromlist=["types"],
        ),
        "types",
        SimpleNamespace(FunctionResponse=lambda **kwargs: SimpleNamespace(**kwargs)),
    )

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gemini-live",
        api_type="gemini",
        on_tool_call=handler,
    )
    session = _OrderedSession()
    client._gemini_session = session
    client.ws = session
    # Long enough that the batch collector can only wake by its call
    # COMPLETING, never by a poll timeout. That pins the ordering the retired
    # set alone cannot survive: the task finishes, its done callback drops it
    # from the retired registry, and only then does the collector look.
    client._TOOL_TASK_CANCEL_TIMEOUT_S = 5.0
    client._on_connection_attached()

    await client._process_gemini_response(
        _gemini_response(calls=(("call-a", "lookup"),)),
        provider_session=session,
        connection_generation=client._connection_generation,
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    await asyncio.wait_for(
        client.inject_text_and_request_response("proactive body"), timeout=2
    )

    assert order == ["tool_response", "proactive"], (
        "the abandoned reply must go out while the generation that issued the "
        "call is still the current one"
    )
    assert len(session.tool_responses) == 1
    abandoned = session.tool_responses[0]
    assert [r.id for r in abandoned] == ["call-a"]
    assert [r.name for r in abandoned] == ["lookup"]
    assert abandoned[0].response["abandoned"] is True

    # The real result lands late and must be dropped, not injected into the
    # proactive turn it would now be part of.
    release.set()
    await _wait_for_tool_tasks(client)
    assert len(session.tool_responses) == 1, (
        "an abandoned call is retired; its late result must not be sent"
    )
    assert order == ["tool_response", "proactive", "tool"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gemini_proactive_gives_up_a_pending_collectors_whole_batch(
    monkeypatch,
) -> None:
    """A finished-but-unflushed sibling is as abandoned as the running one.

    At the settle deadline the only unsettled task of a batch can be its
    COLLECTOR -- the calls are done, their results just have not been flushed
    yet. A collector owns no call, so retiring it answers nothing and does not
    stop it from sending those results, which after the inject below land in
    the proactive turn: the cross-turn injection this path exists to prevent.
    Whatever the collector has not flushed by the deadline is given up with
    the rest of its batch.
    """

    import main_logic.omni_realtime_client._responses as responses

    monkeypatch.setattr(responses, "_GEMINI_PROACTIVE_TOOL_SETTLE_SECONDS", 0.05)
    started_slow = asyncio.Event()
    release_slow = asyncio.Event()
    order: list[str] = []

    async def handler(call):
        if call.call_id == "call-fast":
            return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})
        started_slow.set()
        await release_slow.wait()
        order.append("slow tool")
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    class _OrderedSession(_GeminiSession):
        async def send_client_content(self, *, turns, turn_complete) -> None:
            order.append("proactive")

        async def send_tool_response(self, *, function_responses) -> None:
            order.append("tool_response")
            await super().send_tool_response(function_responses=function_responses)

    monkeypatch.setattr(
        __import__(
            "main_logic.omni_realtime_client._gemini_support",
            fromlist=["types"],
        ),
        "types",
        SimpleNamespace(FunctionResponse=lambda **kwargs: SimpleNamespace(**kwargs)),
    )

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gemini-live",
        api_type="gemini",
        on_tool_call=handler,
    )
    session = _OrderedSession()
    client._gemini_session = session
    client.ws = session
    client._on_connection_attached()

    await client._process_gemini_response(
        _gemini_response(calls=(("call-fast", "alpha"), ("call-slow", "beta"))),
        provider_session=session,
        connection_generation=client._connection_generation,
    )
    await asyncio.wait_for(started_slow.wait(), timeout=1)

    # Premise: the fast call really did finish, and its result is sitting in
    # the collector unflushed because its sibling has not settled.
    entries = client._tool_batch_by_collector_task()[
        next(iter(client._tool_batch_collector_tasks))
    ].entries
    fast_entry = next(e for e in entries if e.call.call_id == "call-fast")
    await asyncio.wait_for(fast_entry.task, timeout=1)
    assert session.tool_responses == []

    await asyncio.wait_for(
        client.inject_text_and_request_response("proactive body"), timeout=2
    )

    assert len(session.tool_responses) == 1
    abandoned = session.tool_responses[0]
    assert sorted(r.id for r in abandoned) == ["call-fast", "call-slow"]
    assert all(r.response["abandoned"] is True for r in abandoned), (
        "the finished sibling's real result must not be flushed after the "
        "inject interrupted the generation that issued it"
    )
    assert order == ["tool_response", "proactive"]

    release_slow.set()
    await _wait_for_tool_tasks(client)
    assert len(session.tool_responses) == 1
    assert order == ["tool_response", "proactive", "slow tool"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_raw_batch_waits_for_a_sibling_announced_after_the_first_finishes() -> None:
    """A fast tool must not seal the batch out from under a sibling in flight.

    The calls of one provider response arrive as separate events. If the first
    tool finishes before the receive loop has read the second event, a
    collector that answered on "nothing of mine is running" would seal a batch
    of one -- and the sibling would open a second batch, so the provider gets
    two continuations, each missing the other's parallel output. While the
    issuing response is still the tracked one, the collector waits.
    """

    started = {"call-2": asyncio.Event()}
    release = {"call-2": asyncio.Event()}

    async def handler(call):
        if call.call_id == "call-1":
            return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})
        started[call.call_id].set()
        await release[call.call_id].wait()
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

    socket.feed({"type": "response.created", "response": {"id": "response-1"}})
    socket.feed(_raw_tool_event("call-1", response_id="response-1"))
    await asyncio.sleep(0.05)
    # Premise: the fast call is done and its response is still the tracked
    # one, so the collector has no proof the batch is complete.
    assert client._current_response_id == "response-1"
    # Asserted on the BATCH, not on socket.sent. The arbiter holds a
    # tool_result ticket for as long as response-1 is live, so nothing has
    # reached the socket yet either way -- `socket.sent == []` here is true of
    # the broken behaviour too and would prove nothing. What distinguishes
    # them is whether the batch is still open for its sibling to join: sealing
    # it pops it from this registry, and call-2 would then open a second one
    # and earn its own continuation.
    assert len(client._open_tool_batches) == 1, (
        "the batch must stay open while its response can still announce "
        "another parallel call"
    )

    socket.feed(_raw_tool_event("call-2", response_id="response-1"))
    await asyncio.wait_for(started["call-2"].wait(), timeout=1)
    assert len(client._open_tool_batches) == 1, "both calls share one batch"
    # Only now does the issuing response terminate. Fed here rather than
    # earlier because it does two things at once: it closes the batch, and it
    # frees the arbiter lane this response was holding -- without it the
    # batch's own ticket would never be sent and the assertions below would
    # time out instead of failing.
    socket.feed({"type": "response.done", "response": {"id": "response-1"}})
    release["call-2"].set()
    await _wait_for_tool_tasks(client)

    assert [event["type"] for event in socket.sent] == [
        "conversation.item.create",
        "conversation.item.create",
        "response.create",
    ]
    answered = [
        event["item"]["call_id"]
        for event in socket.sent
        if event.get("type") == "conversation.item.create"
    ]
    assert answered == ["call-1", "call-2"]

    socket.finish()
    await asyncio.wait_for(receive_loop, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_raw_batch_answers_once_its_response_terminates() -> None:
    """response.done closes the batch, so the answer is not held for a grace."""

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
    receive_loop = asyncio.create_task(client.handle_messages())

    socket.feed({"type": "response.created", "response": {"id": "response-1"}})
    socket.feed(_raw_tool_event("call-1", response_id="response-1"))
    socket.feed({"type": "response.done", "response": {"id": "response-1"}})
    await _wait_for_socket_sends(socket, 2)
    await _wait_for_tool_tasks(client)

    assert [event["type"] for event in socket.sent] == [
        "conversation.item.create",
        "response.create",
    ]
    socket.finish()
    await asyncio.wait_for(receive_loop, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_identified_transcript_does_not_consume_an_idless_utterances_marker() -> None:
    """An older identified transcript must leave the newer marker alone.

    Transcription is asynchronous, so on a proxy that omitted `item_id` for
    the NEWER utterance, an older transcript carrying its own id can arrive
    first. That one is answered from the id list; consuming the id-less
    fallback marker on the way would make the newer utterance's own transcript
    read as an unscoped turn and retire tool work that belongs to it.
    """

    started = asyncio.Event()
    cancelled = asyncio.Event()
    release = asyncio.Event()
    transcripts: list[str] = []
    transcript_seen = asyncio.Event()

    async def handler(call):
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    async def on_input_transcript(transcript: str) -> None:
        transcripts.append(transcript)
        transcript_seen.set()

    client = _no_server_vad_client(
        on_input_transcript=on_input_transcript,
        on_tool_call=handler,
    )
    socket = _QueueSocket()
    client.ws = socket
    client._on_connection_attached()
    receive_loop = asyncio.create_task(client.handle_messages())

    # Utterance A is identified; utterance B is not, so B falls back to the
    # one-shot marker.
    socket.feed({"type": "input_audio_buffer.speech_started", "item_id": "item-a"})
    socket.feed({"type": "input_audio_buffer.speech_started"})
    socket.feed(_raw_tool_event())
    await asyncio.wait_for(started.wait(), timeout=1)
    scope_after_b = client._tool_scope_generation
    assert client._raw_speech_started_scope_pending_transcript is True

    # A's transcript finally lands, out of order.
    socket.feed(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "item-a",
            "transcript": "the older utterance",
        }
    )
    await asyncio.wait_for(transcript_seen.wait(), timeout=1)
    assert client._raw_speech_started_scope_pending_transcript is True, (
        "an identified transcript is answered from the id list; it must not "
        "consume the marker that belongs to the id-less utterance"
    )
    assert client._tool_scope_generation == scope_after_b

    # Now B's own id-less transcript. It was already scoped by B's
    # speech_started, so it must not start yet another turn.
    transcript_seen.clear()
    socket.feed(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "the current utterance",
        }
    )
    await asyncio.wait_for(transcript_seen.wait(), timeout=1)

    assert transcripts == ["the older utterance", "the current utterance"]
    assert client._tool_scope_generation == scope_after_b, (
        "the live utterance's own transcript must not retire its own tool"
    )
    assert not cancelled.is_set()

    release.set()
    await _wait_for_tool_tasks(client)
    socket.finish()
    await asyncio.wait_for(receive_loop, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_wedged_abandoned_reply_still_lets_the_proactive_message_out(
    monkeypatch,
) -> None:
    """The settle budget's promise survives a session that will not accept writes.

    The abandoned-call reply goes to the same session that just proved it can
    be slow. Awaiting it outright would let a wedged session hold the
    proactive notification forever -- restoring exactly the unbounded
    tool-turn gate this path replaced.
    """

    import main_logic.omni_realtime_client._responses as responses

    monkeypatch.setattr(responses, "_GEMINI_PROACTIVE_TOOL_SETTLE_SECONDS", 0.05)
    started = asyncio.Event()
    never = asyncio.Event()
    order: list[str] = []

    async def handler(call):
        started.set()
        await never.wait()
        return ToolResult(call_id=call.call_id, name=call.name, output={})

    class _WedgedSession(_GeminiSession):
        async def send_tool_response(self, *, function_responses) -> None:
            order.append("tool_response started")
            await never.wait()

        async def send_client_content(self, *, turns, turn_complete) -> None:
            order.append("proactive")

    monkeypatch.setattr(
        __import__(
            "main_logic.omni_realtime_client._gemini_support",
            fromlist=["types"],
        ),
        "types",
        SimpleNamespace(FunctionResponse=lambda **kwargs: SimpleNamespace(**kwargs)),
    )

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gemini-live",
        api_type="gemini",
        on_tool_call=handler,
    )
    session = _WedgedSession()
    client._gemini_session = session
    client.ws = session
    client._TOOL_TASK_CANCEL_TIMEOUT_S = 0.05
    client._on_connection_attached()

    await client._process_gemini_response(
        _gemini_response(calls=(("call-a", "lookup"),)),
        provider_session=session,
        connection_generation=client._connection_generation,
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    await asyncio.wait_for(
        client.inject_text_and_request_response("proactive body"), timeout=2
    )

    assert order == ["tool_response started", "proactive"], (
        "the inject must proceed even though the abandoned reply never "
        "finished writing"
    )

    never.set()
    await _wait_for_tool_tasks(client)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_identified_speech_start_does_not_arm_the_idless_marker() -> None:
    """An identified turn must not leave the fallback marker armed behind it.

    Identified transcripts are answered from the id list and never consume the
    marker, so arming it for an identified speech_started would leave it set
    for the rest of the connection. The next turn that arrives WITHOUT a
    speech_started and without a transcript id would then read as already
    scoped -- the original stale-marker bug, rebuilt one turn further along.
    """

    started = asyncio.Event()
    cancelled = asyncio.Event()
    release = asyncio.Event()
    transcript_seen = asyncio.Event()

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

    client = _no_server_vad_client(
        on_input_transcript=on_input_transcript,
        on_tool_call=handler,
    )
    socket = _QueueSocket()
    client.ws = socket
    client._on_connection_attached()
    receive_loop = asyncio.create_task(client.handle_messages())

    # A fully identified turn, start to transcript.
    socket.feed({"type": "input_audio_buffer.speech_started", "item_id": "item-a"})
    socket.feed(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "item-a",
            "transcript": "the identified turn",
        }
    )
    await asyncio.wait_for(transcript_seen.wait(), timeout=1)
    assert client._raw_speech_started_scope_pending_transcript is False, (
        "an identified speech_started is tracked by id; it must not also arm "
        "the id-less fallback marker"
    )

    socket.feed(_raw_tool_event())
    await asyncio.wait_for(started.wait(), timeout=1)
    scope_before = client._tool_scope_generation

    # The next turn arrives with neither a speech_started nor a transcript id.
    transcript_seen.clear()
    socket.feed(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "an unidentified new turn",
        }
    )
    await asyncio.wait_for(transcript_seen.wait(), timeout=1)

    assert client._tool_scope_generation != scope_before, (
        "an id-less transcript with no speech_started of its own is a new "
        "user turn and must retire the previous turn's tool work"
    )
    await asyncio.wait_for(cancelled.wait(), timeout=1)
    release.set()
    await _wait_for_tool_tasks(client)

    assert socket.sent == []
    socket.finish()
    await asyncio.wait_for(receive_loop, timeout=1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gemini_proactive_waits_for_a_tool_response_still_being_written(
    monkeypatch,
) -> None:
    """A sealed batch whose write is in flight still holds the inject back.

    Sealing marks "this batch has decided its answer", and there is no await
    between it and the provider write -- so by the time anything else can
    observe `sealed`, the write has already started and cannot be recalled.
    What keeps the ordering is that the collector task is still PENDING while
    it writes, so the settle waits for it exactly like any other unsettled
    tool work, within the same budget. This pins that; its bounded twin is
    ``test_a_wedged_abandoned_reply_still_lets_the_proactive_message_out``.
    """

    import main_logic.omni_realtime_client._responses as responses

    monkeypatch.setattr(responses, "_GEMINI_PROACTIVE_TOOL_SETTLE_SECONDS", 5.0)
    write_entered = asyncio.Event()
    release_write = asyncio.Event()
    order: list[str] = []

    async def handler(call):
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    class _SlowWriteSession(_GeminiSession):
        async def send_tool_response(self, *, function_responses) -> None:
            write_entered.set()
            await release_write.wait()
            order.append("tool_response")

        async def send_client_content(self, *, turns, turn_complete) -> None:
            order.append("proactive")

    monkeypatch.setattr(
        __import__(
            "main_logic.omni_realtime_client._gemini_support",
            fromlist=["types"],
        ),
        "types",
        SimpleNamespace(FunctionResponse=lambda **kwargs: SimpleNamespace(**kwargs)),
    )

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gemini-live",
        api_type="gemini",
        on_tool_call=handler,
    )
    session = _SlowWriteSession()
    client._gemini_session = session
    client.ws = session
    client._on_connection_attached()

    await client._process_gemini_response(
        _gemini_response(calls=(("call-a", "lookup"),)),
        provider_session=session,
        connection_generation=client._connection_generation,
    )
    await asyncio.wait_for(write_entered.wait(), timeout=1)

    # Premise: the batch really is sealed already -- there was no window
    # between sealing and the write in which anything could have suppressed it.
    batch = next(iter(client._tool_batch_collector_tasks.values()))
    assert batch.sealed is True

    inject = asyncio.create_task(
        client.inject_text_and_request_response("proactive body")
    )
    await asyncio.sleep(0.05)
    assert order == [], "the inject must not overtake a tool response mid-write"

    release_write.set()
    await asyncio.wait_for(inject, timeout=2)
    await _wait_for_tool_tasks(client)

    assert order == ["tool_response", "proactive"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_raw_batch_answers_when_its_response_done_is_stale_filtered() -> None:
    """A batch whose terminal never reaches the close hook still answers.

    ``close_raw_tool_batch`` sits after the stale-response filter, so a
    ``response.done`` for A arriving once B is current is dropped before it --
    A's batch is never explicitly closed. That is survivable because the grace
    is gated on EVIDENCE rather than on a timer: the collector waits only
    while the issuing response is still the tracked one, and here it is not.
    Without that gate this batch would sit out a grace round it can never win.
    """

    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(call):
        started.set()
        await release.wait()
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

    socket.feed({"type": "response.created", "response": {"id": "response-a"}})
    socket.feed(_raw_tool_event("call-1", response_id="response-a"))
    await asyncio.wait_for(started.wait(), timeout=1)

    # B becomes current, then A's terminal finally shows up -- and is dropped
    # by the stale filter before it can reach the close hook.
    socket.feed({"type": "response.created", "response": {"id": "response-b"}})
    socket.feed({"type": "response.done", "response": {"id": "response-a"}})
    socket.feed({"type": "response.done", "response": {"id": "response-b"}})
    await client._response_arbiter.wait_until_idle(timeout=1)
    assert client._current_response_id != "response-a", (
        "premise: A is no longer the tracked response"
    )

    release.set()
    await _wait_for_socket_sends(socket, 2)
    await _wait_for_tool_tasks(client)

    answered = [
        event["item"]["call_id"]
        for event in socket.sent
        if event.get("type") == "conversation.item.create"
    ]
    assert answered == ["call-1"], (
        "A's tool result must still be sent even though its terminal never "
        "reached the batch close hook"
    )

    socket.finish()
    await asyncio.wait_for(receive_loop, timeout=1)
