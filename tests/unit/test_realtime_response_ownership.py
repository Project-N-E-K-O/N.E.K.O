"""Regression coverage for server-response ownership and lifetime isolation."""

import asyncio
import json
import logging
from types import SimpleNamespace

import pytest

from main_logic.omni_realtime_client import OmniRealtimeClient
from main_logic.omni_realtime_client._response_arbiter import (
    RealtimeResponseArbiter,
    ResponseCreatedKind,
)
from main_logic.omni_realtime_client._shared import ToolResult
from main_logic.core.turn import TurnMixin


class _QueueSocket:
    def __init__(self):
        self.frames: asyncio.Queue[dict] = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        return json.dumps(await self.frames.get())

    async def send(self, payload):
        self.sent.append(json.loads(payload))

    async def close(self, *_args, **_kwargs):
        self.closed = True

    def push(self, event: dict) -> None:
        self.frames.put_nowait(event)


async def _cancel_task(task: asyncio.Task) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.unit
def test_response_id_zero_is_preserved_but_empty_identity_is_not():
    read = RealtimeResponseArbiter._event_response_id
    assert read({"response": {"id": 0}}) == "0"
    assert read({"response": {"id": ""}}) is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_idless_orphan_releases_the_lane_at_its_stale_deadline():
    sent: list[dict] = []

    async def send(event):
        sent.append(dict(event))

    arbiter = RealtimeResponseArbiter(send)
    try:
        # An unowned, id-less response is a real live response, not merely an
        # "active" flag. Its lifetime must have the same bounded stale release
        # as an id-bearing orphan, otherwise the lane wedges forever when its
        # terminal frame is lost.
        arbiter._server_response_max_age = 0.03
        arbiter.notify_response_created({"type": "response.created"})
        successor = await arbiter.enqueue(
            source="successor",
            response_done_timeout=0.3,
        )
        # Keep the deliberately short orphan allowance after enqueue ratchets
        # the production allowance up to the ticket's completion bound.
        arbiter._server_response_max_age = 0.03

        await asyncio.wait_for(successor.sent, 0.2)
        assert [event["type"] for event in sent] == ["response.create"]

        arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-next"}}
        )
        arbiter.notify_response_terminal(
            {"type": "response.done", "response": {"id": "resp-next"}}
        )
        await asyncio.wait_for(successor.done, 0.2)
    finally:
        await arbiter.shutdown()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_late_created_from_the_retired_generation_cannot_claim_the_next_owner():
    sent: list[dict] = []

    async def send(event):
        sent.append(dict(event))

    arbiter = RealtimeResponseArbiter(send)
    try:
        previous = await arbiter.enqueue(source="previous")
        await asyncio.wait_for(previous.sent, 0.2)

        # The previous lifecycle ends before its announcement arrives. This
        # retires that owner's created window, but a delayed created frame from
        # the same generation can still be in flight on the connection.
        arbiter.notify_response_terminal(
            {"type": "response.done", "response": {"status": "completed"}}
        )
        with pytest.raises(RuntimeError, match="before response.created"):
            await asyncio.wait_for(previous.done, 0.2)

        successor = await arbiter.enqueue(source="successor")
        await asyncio.sleep(0.02)
        assert successor.sent.done() is False
        arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-old-late"}}
        )
        await asyncio.wait_for(successor.sent, 0.2)
        arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-new"}}
        )
        arbiter.notify_response_terminal(
            {
                "type": "response.done",
                "response": {"id": "resp-new", "status": "completed"},
            }
        )

        result = await asyncio.wait_for(successor.done, 0.2)
        assert result is not None
        assert successor.started.exception() is None
    finally:
        await arbiter.shutdown()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retiring_an_owner_retires_only_its_inflight_cancel_send():
    sent: list[dict] = []
    cancel_entered = asyncio.Event()
    cancel_gate = asyncio.Event()

    async def send(event):
        if event["type"] == "response.cancel":
            cancel_entered.set()
            await cancel_gate.wait()
        sent.append(dict(event))

    arbiter = RealtimeResponseArbiter(send)
    try:
        ticket = await arbiter.enqueue(
            source="owner",
            events_before_response=(
                {
                    "type": "conversation.item.create",
                    "event_id": "item-event",
                    "item": {"id": "item-target", "role": "user"},
                },
            ),
            response_event={
                "type": "response.create",
                "event_id": "response-event",
            },
            ack_expected=True,
            expected_item_id="item-target",
            expected_item_role="user",
            item_ack_timeout=0.01,
        )
        await asyncio.wait_for(ticket.sent, 0.2)

        # A late pre-response error spawns a best-effort cancel task. Hold the
        # transport write open until after the same owner's terminal retires it.
        arbiter.notify_error("item-event", "item rejected late")
        await asyncio.wait_for(cancel_entered.wait(), 0.2)
        assert len(arbiter._cancel_send_tasks) == 1

        arbiter.notify_response_terminal(
            {
                "type": "response.done",
                "response": {"id": "resp-owner", "status": "completed"},
            }
        )
        for _ in range(20):
            if arbiter._response_owner is None and not arbiter._cancel_send_tasks:
                break
            await asyncio.sleep(0)
        assert arbiter._response_owner is None
        assert not arbiter._cancel_send_tasks

        cancel_gate.set()
        for _ in range(5):
            await asyncio.sleep(0)
        assert "response.cancel" not in [event["type"] for event in sent]
    finally:
        cancel_gate.set()
        await arbiter.shutdown()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_idless_orphan_survives_an_id_bearing_owner_terminal():
    sent: list[dict] = []

    async def send(event):
        sent.append(dict(event))

    arbiter = RealtimeResponseArbiter(send)
    try:
        owner = await arbiter.enqueue(source="owner")
        await asyncio.wait_for(owner.sent, 0.2)
        arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-owner"}}
        )
        await asyncio.wait_for(owner.started, 0.2)

        # A second, unowned response has no id but is still live. Completing
        # the named owner must not erase that separate lifetime or open the
        # lane under it.
        arbiter.notify_response_created({"type": "response.created"})
        successor = await arbiter.enqueue(source="successor")
        arbiter.notify_response_terminal(
            {
                "type": "response.done",
                "response": {"id": "resp-owner", "status": "completed"},
            }
        )
        await asyncio.wait_for(owner.done, 0.2)
        await asyncio.sleep(0.03)
        assert successor.sent.done() is False
        assert [event["type"] for event in sent] == ["response.create"]

        # The id-less orphan's own terminal now releases the lane.
        arbiter.notify_response_terminal(
            {"type": "response.done", "response": {"status": "completed"}}
        )
        await asyncio.wait_for(successor.sent, 0.2)
        arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-next"}}
        )
        arbiter.notify_response_terminal(
            {"type": "response.done", "response": {"id": "resp-next"}}
        )
        await asyncio.wait_for(successor.done, 0.2)
    finally:
        await arbiter.shutdown()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_id_bearing_terminal_keeps_its_created_window_out_of_the_successor():
    sent: list[dict] = []

    async def send(event):
        sent.append(dict(event))

    arbiter = RealtimeResponseArbiter(send)
    try:
        previous = await arbiter.enqueue(source="previous")
        await asyncio.wait_for(previous.sent, 0.2)
        terminal = arbiter.notify_response_terminal(
            {
                "type": "response.done",
                "response": {"id": "resp-old", "status": "completed"},
            }
        )
        await asyncio.wait_for(previous.done, 0.2)

        successor = await arbiter.enqueue(source="successor")
        await asyncio.sleep(0.02)
        assert successor.sent.done() is False

        late = arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-old"}}
        )
        assert late.kind is ResponseCreatedKind.RETIRED
        assert late.generation == terminal.generation
        await asyncio.wait_for(successor.sent, 0.2)

        created = arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-new"}}
        )
        assert created.kind is ResponseCreatedKind.OWNER
        arbiter.notify_response_terminal(
            {"type": "response.done", "response": {"id": "resp-new"}}
        )
        await asyncio.wait_for(successor.done, 0.2)
    finally:
        await arbiter.shutdown()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retired_and_server_vad_created_claims_pay_one_debt_each():
    sent: list[dict] = []

    async def send(event):
        sent.append(dict(event))

    arbiter = RealtimeResponseArbiter(send)
    try:
        previous = await arbiter.enqueue(source="previous")
        await asyncio.wait_for(previous.sent, 0.2)
        arbiter.notify_response_terminal(
            {"type": "response.done", "response": {"status": "completed"}}
        )
        with pytest.raises(RuntimeError, match="before response.created"):
            await asyncio.wait_for(previous.done, 0.2)

        arbiter.notify_server_vad_response_pending()
        created = arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-ambiguous"}}
        )
        assert created.kind is ResponseCreatedKind.RETIRED
        assert arbiter._server_vad_response_pending is True
        assert len(arbiter._retired_created_windows) == 1

        successor = await arbiter.enqueue(source="successor")
        await asyncio.sleep(0.02)
        assert successor.sent.done() is False
        vad_created = arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-vad"}}
        )
        assert vad_created.kind is ResponseCreatedKind.SERVER_VAD
        assert arbiter._server_vad_response_pending is False
        assert not arbiter._retired_created_windows
        arbiter.notify_response_terminal(
            {"type": "response.done", "response": {"id": "resp-vad"}}
        )
        await asyncio.wait_for(successor.sent, 0.2)
    finally:
        await arbiter.shutdown()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transport_does_not_adopt_a_created_frame_the_arbiter_retired():
    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-test",
        api_type="gpt",
    )
    socket = _QueueSocket()
    client.ws = socket
    receiver = asyncio.create_task(client.handle_messages())
    try:
        previous = await client._response_arbiter.enqueue(source="previous")
        await asyncio.wait_for(previous.sent, 0.2)
        socket.push(
            {
                "type": "response.done",
                "response": {"id": "resp-old", "status": "completed"},
            }
        )
        await asyncio.wait_for(previous.done, 0.2)

        successor = await client._response_arbiter.enqueue(source="successor")
        socket.push(
            {"type": "response.created", "response": {"id": "resp-old"}}
        )
        await asyncio.wait_for(successor.sent, 0.2)
        assert client._current_response_id is None
        assert client._current_response_generation is None

        socket.push(
            {"type": "response.created", "response": {"id": "resp-new"}}
        )
        await asyncio.wait_for(successor.started, 0.2)
        assert client._current_response_id == "resp-new"
        socket.push(
            {"type": "response.done", "response": {"id": "resp-new"}}
        )
        await asyncio.wait_for(successor.done, 0.2)
    finally:
        await _cancel_task(receiver)
        await client.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_intermittently_announcing_provider_finalizes_the_unannounced_turn():
    finished: list[str] = []
    text: list[str] = []

    async def on_done():
        finished.append("done")

    async def on_text(delta, _is_first):
        text.append(delta)

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-test",
        api_type="gpt",
        on_response_done=on_done,
        on_text_delta=on_text,
    )
    socket = _QueueSocket()
    client.ws = socket
    receiver = asyncio.create_task(client.handle_messages())
    try:
        first = await client._response_arbiter.enqueue(source="first")
        await asyncio.wait_for(first.sent, 0.2)
        socket.push(
            {"type": "response.created", "response": {"id": "resp-first"}}
        )
        socket.push(
            {"type": "response.done", "response": {"id": "resp-first"}}
        )
        await asyncio.wait_for(first.done, 0.2)

        second = await client._response_arbiter.enqueue(source="second")
        await asyncio.wait_for(second.sent, 0.2)
        socket.push(
            {
                "type": "response.text.delta",
                "response_id": "resp-second",
                "delta": "second turn",
            }
        )
        await asyncio.wait_for(second.started, 0.2)
        socket.push(
            {"type": "response.done", "response": {"id": "resp-second"}}
        )
        await asyncio.wait_for(second.done, 0.2)
        for _ in range(10):
            if len(finished) == 2:
                break
            await asyncio.sleep(0)
        assert finished == ["done", "done"]
        assert text == ["second turn"]
        assert not client._response_arbiter._retired_created_windows
        assert client._current_response_id is None
    finally:
        await _cancel_task(receiver)
        await client.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_old_tool_task_cannot_send_its_result_into_a_new_connection():
    tool_started = asyncio.Event()

    async def on_tool_call(call):
        tool_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # A defensive generation check is still required when user code
            # swallows cancellation and returns a result.
            return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-test",
        api_type="gpt",
        on_tool_call=on_tool_call,
    )
    old_socket = _QueueSocket()
    client.ws = old_socket
    receiver = asyncio.create_task(client.handle_messages())
    old_socket.push(
        {
            "type": "response.function_call_arguments.done",
            "call_id": "old-call",
            "name": "old-tool",
            "arguments": "{}",
        }
    )
    await asyncio.wait_for(tool_started.wait(), 0.2)

    await client.close()
    new_socket = _QueueSocket()
    client.ws = new_socket
    client._response_arbiter.reset_connection_state()
    for _ in range(10):
        await asyncio.sleep(0)
    assert new_socket.sent == []
    assert not client._connection_tasks
    await _cancel_task(receiver)
    await client.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_old_gemini_tool_execution_cannot_target_a_replacement_session():
    tool_started = asyncio.Event()
    tool_gate = asyncio.Event()
    sent: list[list[ToolResult]] = []

    client = OmniRealtimeClient(
        "gemini://test",
        "test-key",
        model="gemini-test",
        api_type="gemini",
        on_tool_call=lambda _call: None,
    )

    async def execute(call):
        tool_started.set()
        await tool_gate.wait()
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    async def send(results, **_kwargs):
        sent.append(results)

    client._execute_tool_call = execute
    client._send_tool_result_gemini = send
    response = SimpleNamespace(
        tool_call=SimpleNamespace(
            function_calls=[
                SimpleNamespace(id="old-call", name="old-tool", args={})
            ]
        ),
        server_content=None,
    )
    processing = asyncio.create_task(client._process_gemini_response(response))
    await asyncio.wait_for(tool_started.wait(), 0.2)
    await client._begin_connection_generation()
    tool_gate.set()
    await asyncio.wait_for(processing, 0.2)
    for _ in range(5):
        await asyncio.sleep(0)
    assert sent == []
    await client.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reconnect_clears_transport_identity_and_tool_accumulators():
    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-test",
        api_type="gpt",
    )
    client._current_response_id = "resp-old"
    client._current_response_generation = 41
    client._is_responding = True
    client._interrupted = True
    client._inflight_tool_args["reused-call"] = {
        "name": "old-tool",
        "arguments": '{"old":',
    }
    client.ws = _QueueSocket()
    await client.close()

    assert client._current_response_id is None
    assert client._current_response_generation is None
    assert client._is_responding is False
    assert client._interrupted is False
    assert client._inflight_tool_args == {}

    new_socket = _QueueSocket()
    client.ws = new_socket
    client._response_arbiter.reset_connection_state()
    receiver = asyncio.create_task(client.handle_messages())
    new_socket.push({"type": "input_audio_buffer.speech_started"})
    for _ in range(10):
        await asyncio.sleep(0)
    assert [event["type"] for event in new_socket.sent] == []
    await _cancel_task(receiver)
    await client.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delayed_close_from_old_generation_cannot_close_new_socket():
    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-test",
        api_type="gpt",
    )
    old_generation = client._client_connection_generation
    await client._begin_connection_generation()
    new_socket = _QueueSocket()
    client.ws = new_socket

    await client._close_if_current_connection(old_generation)
    assert client.ws is new_socket
    assert new_socket.closed is False
    await client.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_response_send_failure_retires_the_owners_inflight_cancel():
    sent: list[dict] = []
    create_entered = asyncio.Event()
    create_gate = asyncio.Event()
    cancel_entered = asyncio.Event()
    cancel_gate = asyncio.Event()

    async def send(event):
        if event["type"] == "response.create":
            create_entered.set()
            await create_gate.wait()
            raise RuntimeError("response.create write failed")
        if event["type"] == "response.cancel":
            cancel_entered.set()
            await cancel_gate.wait()
        sent.append(dict(event))

    arbiter = RealtimeResponseArbiter(send)
    try:
        ticket = await arbiter.enqueue(
            source="owner",
            events_before_response=(
                {
                    "type": "conversation.item.create",
                    "event_id": "item-event",
                    "item": {"id": "item-target", "role": "user"},
                },
            ),
            response_event={
                "type": "response.create",
                "event_id": "response-event",
            },
            ack_expected=True,
            expected_item_id="item-target",
            expected_item_role="user",
            item_ack_timeout=0.01,
        )
        await asyncio.wait_for(create_entered.wait(), 0.2)
        arbiter.notify_error("item-event", "item rejected late")
        await asyncio.wait_for(cancel_entered.wait(), 0.2)

        create_gate.set()
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(ticket.done, 0.2)
        for _ in range(20):
            if arbiter._response_owner is None and not arbiter._cancel_send_tasks:
                break
            await asyncio.sleep(0)
        assert arbiter._response_owner is None
        assert not arbiter._cancel_send_tasks

        cancel_gate.set()
        for _ in range(5):
            await asyncio.sleep(0)
        assert "response.cancel" not in [event["type"] for event in sent]
    finally:
        create_gate.set()
        cancel_gate.set()
        await arbiter.shutdown()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_terminal_transcript_callback_failure_does_not_kill_the_receive_loop():
    finished = asyncio.Event()

    async def broken_transcript(_text, _is_first):
        raise RuntimeError("frontend transcript failed")

    async def on_done():
        finished.set()

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-test",
        api_type="gpt",
        on_output_transcript=broken_transcript,
        on_response_done=on_done,
    )
    socket = _QueueSocket()
    client.ws = socket
    receiver = asyncio.create_task(client.handle_messages())
    try:
        ticket = await client._response_arbiter.enqueue(source="owner")
        await asyncio.wait_for(ticket.sent, 0.2)
        socket.push(
            {"type": "response.created", "response": {"id": "resp-owner"}}
        )
        socket.push(
            {
                "type": "response.audio_transcript.delta",
                "response_id": "resp-owner",
                "delta": "buffered",
            }
        )
        client._audio_delta_count = 1
        client._print_input_transcript = False
        socket.push(
            {"type": "response.done", "response": {"id": "resp-owner"}}
        )
        await asyncio.wait_for(ticket.done, 0.2)
        await asyncio.wait_for(finished.wait(), 0.2)
        assert receiver.done() is False
    finally:
        await _cancel_task(receiver)
        await client.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancelled_sid_rotation_cannot_leave_half_successor_state():
    class _Resampler:
        def __init__(self):
            self.clears = 0

        def clear(self):
            self.clears += 1

    lock = asyncio.Lock()
    await lock.acquire()
    target = SimpleNamespace(
        _takeover_active=False,
        audio_resampler=_Resampler(),
        _tts_done_queued_for_turn=True,
        _tts_done_pending_until_ready=True,
        current_speech_id="old-sid",
        lock=lock,
    )
    rotation = asyncio.create_task(
        TurnMixin.rotate_speech_id_for_response_done(target)
    )
    await asyncio.sleep(0)
    rotation.cancel()
    await asyncio.gather(rotation, return_exceptions=True)
    lock.release()

    assert target.current_speech_id == "old-sid"
    assert target._tts_done_queued_for_turn is True
    assert target._tts_done_pending_until_ready is True
    assert target.audio_resampler.clears == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_old_terminal_hooks_do_not_land_on_a_new_host_turn():
    repetition_entered = asyncio.Event()
    repetition_gate = asyncio.Event()
    finished: list[str] = []

    async def on_done():
        finished.append("done")

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-test",
        api_type="gpt",
        on_response_done=on_done,
    )

    async def gated_repetition(_transcript):
        repetition_entered.set()
        await repetition_gate.wait()

    client._record_response_repetition = gated_repetition
    socket = _QueueSocket()
    client.ws = socket
    receiver = asyncio.create_task(client.handle_messages())
    try:
        ticket = await client._response_arbiter.enqueue(source="owner")
        await asyncio.wait_for(ticket.sent, 0.2)
        socket.push(
            {"type": "response.created", "response": {"id": "resp-old"}}
        )
        await asyncio.wait_for(ticket.started, 0.2)
        socket.push(
            {"type": "response.done", "response": {"id": "resp-old"}}
        )
        await asyncio.wait_for(repetition_entered.wait(), 0.2)

        client.notify_host_turn_started()
        repetition_gate.set()
        await asyncio.wait_for(ticket.done, 0.2)
        for _ in range(10):
            await asyncio.sleep(0)
        assert finished == []
    finally:
        repetition_gate.set()
        await _cancel_task(receiver)
        await client.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_core_new_message_advances_the_transport_host_turn_token():
    class _Session:
        def __init__(self):
            self.starts = 0

        def notify_host_turn_started(self):
            self.starts += 1

    class _Resampler:
        def clear(self):
            return None

    class _State:
        def mark_user_input_preempt(self):
            return None

        async def fire(self, *_args, **_kwargs):
            return None

    async def noop():
        return None

    target = SimpleNamespace(
        _takeover_active=False,
        session=_Session(),
        audio_resampler=_Resampler(),
        _clear_tts_pipeline=noop,
        _tts_done_queued_for_turn=True,
        _tts_done_pending_until_ready=True,
        _current_ai_turn_text="old",
        send_user_activity=noop,
        lock=asyncio.Lock(),
        current_speech_id="old-sid",
        state=_State(),
    )
    await TurnMixin.handle_new_message(target)
    assert target.session.starts == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fail_open_release_does_not_clear_successor_scoped_suppression():
    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-test",
        api_type="gpt",
    )
    client._skip_until_next_response = True
    client._current_response_id = "resp-abandoned"
    client._is_responding = True
    client._current_turn_epoch = client._turn_epoch
    client.notify_host_turn_started()

    await client._on_arbiter_stuck_release("stalled", "resp-abandoned")
    assert client._skip_until_next_response is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_deferred_no_vad_sid_rotation_finishes_before_successor_dispatch():
    order: list[str] = []

    async def rotate():
        order.append("rotate")

    class _OrderedSocket(_QueueSocket):
        async def send(self, payload):
            order.append(json.loads(payload)["type"])
            await super().send(payload)

    client = OmniRealtimeClient(
        "wss://www.lanlan.app/core",
        "free-access",
        model="free-model",
        api_type="free",
        on_sid_rotate=rotate,
    )
    socket = _OrderedSocket()
    client.ws = socket
    client._sid_rotation_required_before_dispatch = True
    ticket = await client._response_arbiter.enqueue(source="successor")
    try:
        await asyncio.wait_for(ticket.sent, 0.2)
        assert order[:2] == ["rotate", "response.create"]
        assert client._sid_rotation_required_before_dispatch is False
    finally:
        await client.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_vad_successor_waits_for_terminal_hooks_and_sid_rotation():
    hook_entered = asyncio.Event()
    hook_gate = asyncio.Event()
    order: list[str] = []

    async def on_done():
        order.append("done-start")
        hook_entered.set()
        await hook_gate.wait()
        order.append("done-end")

    async def rotate():
        order.append("rotate")

    class _OrderedSocket(_QueueSocket):
        async def send(self, payload):
            event = json.loads(payload)
            if event["type"] == "response.create":
                order.append("create")
            await super().send(payload)

    client = OmniRealtimeClient(
        "wss://www.lanlan.app/core",
        "free-access",
        model="free-model",
        api_type="free",
        on_response_done=on_done,
        on_sid_rotate=rotate,
    )
    socket = _OrderedSocket()
    client.ws = socket
    receiver = asyncio.create_task(client.handle_messages())
    try:
        first = await client._response_arbiter.enqueue(source="first")
        await asyncio.wait_for(first.sent, 0.2)
        successor = await client._response_arbiter.enqueue(source="successor")
        socket.push(
            {"type": "response.done", "response": {"id": "resp-first"}}
        )
        await asyncio.wait_for(hook_entered.wait(), 0.2)
        await asyncio.sleep(0.02)
        assert [event["type"] for event in socket.sent] == ["response.create"]

        hook_gate.set()
        await asyncio.wait_for(successor.sent, 0.2)
        assert order == ["create", "done-start", "done-end", "rotate", "create"]
    finally:
        hook_gate.set()
        await _cancel_task(receiver)
        await client.close()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("created_response", "terminal_response"),
    [
        ({}, {"id": "resp-enriched", "status": "completed"}),
        ({"id": "resp-known"}, {"status": "completed"}),
    ],
)
async def test_owner_identity_can_transition_between_idless_and_id_bearing(
    created_response,
    terminal_response,
):
    sent: list[dict] = []

    async def send(event):
        sent.append(dict(event))

    arbiter = RealtimeResponseArbiter(send)
    try:
        ticket = await arbiter.enqueue(source="owner")
        await asyncio.wait_for(ticket.sent, 0.2)
        created = arbiter.notify_response_created(
            {"type": "response.created", "response": created_response}
        )
        terminal = arbiter.notify_response_terminal(
            {"type": "response.done", "response": terminal_response}
        )

        assert created.kind is ResponseCreatedKind.OWNER
        assert terminal.generation == created.generation
        await asyncio.wait_for(ticket.done, 0.2)
        assert arbiter.is_busy is False
    finally:
        await arbiter.shutdown()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_idless_orphan_accepts_an_id_bearing_terminal_before_successor():
    sent: list[dict] = []

    async def send(event):
        sent.append(dict(event))

    arbiter = RealtimeResponseArbiter(send)
    try:
        created = arbiter.notify_response_created(
            {"type": "response.created", "response": {}}
        )
        successor = await arbiter.enqueue(source="successor")
        await asyncio.sleep(0)
        assert successor.sent.done() is False

        terminal = arbiter.notify_response_terminal(
            {
                "type": "response.done",
                "response": {"id": "resp-orphan", "status": "completed"},
            }
        )
        assert terminal.generation == created.generation
        await asyncio.wait_for(successor.sent, 0.2)
        arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-next"}}
        )
        arbiter.notify_response_terminal(
            {"type": "response.done", "response": {"id": "resp-next"}}
        )
        await asyncio.wait_for(successor.done, 0.2)
    finally:
        await arbiter.shutdown()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_ticket_send_is_retired_with_its_lifecycle():
    wire: list[dict] = []
    cancel_entered = asyncio.Event()
    cancel_gate = asyncio.Event()

    async def send(event):
        if event["type"] == "response.cancel":
            cancel_entered.set()
            await cancel_gate.wait()
        wire.append(dict(event))

    arbiter = RealtimeResponseArbiter(send)
    try:
        ticket = await arbiter.enqueue(source="owner")
        await asyncio.wait_for(ticket.sent, 0.2)
        arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-owner"}}
        )
        cancellation = asyncio.create_task(
            arbiter.cancel_ticket(ticket, wait=False)
        )
        await asyncio.wait_for(cancel_entered.wait(), 0.2)

        arbiter.notify_response_terminal(
            {"type": "response.done", "response": {"id": "resp-owner"}}
        )
        assert await asyncio.wait_for(cancellation, 0.2) is True
        cancel_gate.set()
        await asyncio.sleep(0)
        assert [event["type"] for event in wire] == ["response.create"]
    finally:
        cancel_gate.set()
        await arbiter.shutdown()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_late_vad_created_after_pending_timeout_stays_a_live_orphan():
    sent: list[dict] = []

    async def send(event):
        sent.append(dict(event))

    arbiter = RealtimeResponseArbiter(send)
    try:
        arbiter.notify_server_vad_response_pending(arm_timeout=False)
        pending_generation = arbiter._server_vad_pending_generation
        arbiter._server_vad_pending_expired()
        successor = await arbiter.enqueue(source="successor")
        await asyncio.sleep(0)
        assert successor.sent.done() is False

        created = arbiter.notify_response_created(
            {
                "type": "response.created",
                "response": {"id": "resp-vad-late"},
            }
        )
        assert created.kind is ResponseCreatedKind.UNOWNED_SERVER
        assert created.generation == pending_generation
        assert successor.sent.done() is False

        arbiter.notify_response_terminal(
            {"type": "response.done", "response": {"id": "resp-vad-late"}}
        )
        await asyncio.wait_for(successor.sent, 0.2)
        arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-next"}}
        )
        arbiter.notify_response_terminal(
            {"type": "response.done", "response": {"id": "resp-next"}}
        )
        await asyncio.wait_for(successor.done, 0.2)
    finally:
        await arbiter.shutdown()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_duplicate_created_cannot_claim_a_successor_owner():
    async def send(_event):
        return None

    arbiter = RealtimeResponseArbiter(send)
    try:
        previous = await arbiter.enqueue(source="previous")
        await asyncio.wait_for(previous.sent, 0.2)
        arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-old"}}
        )
        arbiter.notify_response_terminal(
            {"type": "response.done", "response": {"id": "resp-old"}}
        )
        await asyncio.wait_for(previous.done, 0.2)

        successor = await arbiter.enqueue(source="successor")
        await asyncio.wait_for(successor.sent, 0.2)
        duplicate = arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-old"}}
        )
        assert duplicate.kind is ResponseCreatedKind.RETIRED
        assert successor.started.done() is False

        arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-new"}}
        )
        arbiter.notify_response_terminal(
            {"type": "response.done", "response": {"id": "resp-new"}}
        )
        await asyncio.wait_for(successor.done, 0.2)
    finally:
        await arbiter.shutdown()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stale_orphan_expiry_clears_transport_before_barge_in():
    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="qwen-test",
        api_type="qwen",
    )
    socket = _QueueSocket()
    client.ws = socket
    created = client._response_arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-orphan"}}
    )
    assert created.generation is not None
    client._activate_response_state(created.response_id, created.generation)

    client._response_arbiter._server_response_max_age = 0
    client._response_arbiter._release_lane_if_clear()
    assert client._is_responding is False
    assert client._current_response_generation is None
    assert client._interrupted is True

    await client.handle_interruption()
    assert socket.sent == []
    await client.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_barge_in_cancels_an_idless_live_lifecycle():
    client = OmniRealtimeClient(
        "wss://www.lanlan.app/core",
        "free-access",
        model="free-model",
        api_type="free",
    )
    socket = _QueueSocket()
    client.ws = socket
    client._activate_unannounced_response_state()

    assert client._current_response_id is None
    assert client._current_response_generation is not None
    assert client._is_responding is True

    try:
        await client.handle_interruption()
        assert [event["type"] for event in socket.sent] == ["response.cancel"]
    finally:
        await client.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_adoption_keeps_transport_generation_and_ticket_suppression():
    finished: list[str] = []
    delivered: list[str] = []

    async def on_done():
        finished.append("done")

    async def on_text(delta, _first):
        delivered.append(delta)

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="qwen-test",
        api_type="qwen",
        on_response_done=on_done,
        on_text_delta=on_text,
    )
    socket = _QueueSocket()
    client.ws = socket
    receiver = asyncio.create_task(client.handle_messages())
    try:
        ticket = await client._response_arbiter.enqueue(
            source="adopted",
            events_before_response=(
                {
                    "type": "conversation.item.create",
                    "item": {"id": "item-adopt", "role": "user"},
                },
            ),
            ack_expected=True,
            expected_item_id="item-adopt",
            expected_item_role="user",
            response_started_timeout=0.03,
            suppress_output=True,
        )
        for _ in range(20):
            if socket.sent:
                break
            await asyncio.sleep(0.005)
        socket.push(
            {"type": "response.created", "response": {"id": "resp-adopt"}}
        )
        for _ in range(20):
            if client._current_response_generation is not None:
                break
            await asyncio.sleep(0.005)
        assert client._skip_until_next_response is True
        committed_before = client._input_audio_committed_total
        socket.push(
            {
                "type": "response.text.delta",
                "response_id": "resp-adopt",
                "delta": "MUST_STAY_SILENT",
            }
        )
        # The receive loop consumes socket events in order.  Observing this
        # marker proves the preceding delta was handled before we assert that
        # suppression kept it out of the delivery callback.
        socket.push({"type": "input_audio_buffer.committed"})
        for _ in range(40):
            if client._input_audio_committed_total > committed_before:
                break
            await asyncio.sleep(0.005)
        assert client._input_audio_committed_total == committed_before + 1
        assert delivered == []

        socket.push(
            {"type": "conversation.item.created", "item": {"id": "item-adopt", "role": "user"}}
        )
        await asyncio.wait_for(ticket.sent, 0.2)
        await asyncio.wait_for(ticket.started, 0.2)
        adopted_generation = client._current_response_generation
        assert adopted_generation is not None
        assert client._skip_until_next_response is True

        socket.push(
            {"type": "response.done", "response": {"id": "resp-adopt"}}
        )
        await asyncio.wait_for(ticket.done, 0.2)
        for _ in range(20):
            if finished:
                break
            await asyncio.sleep(0.005)
        assert finished == ["done"]
        assert client._current_response_generation is None
    finally:
        await _cancel_task(receiver)
        await client.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_old_receive_loop_cannot_execute_an_idless_tool_on_new_socket():
    calls: list[str] = []

    async def on_tool_call(call):
        calls.append(call.name)
        return "ok"

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="qwen-test",
        api_type="qwen",
        on_tool_call=on_tool_call,
    )
    old_socket = _QueueSocket()
    new_socket = _QueueSocket()
    client.ws = old_socket
    receiver = asyncio.create_task(client.handle_messages())
    await asyncio.sleep(0)
    client.ws = new_socket
    await client._begin_connection_generation()
    old_socket.push(
        {
            "type": "response.function_call_arguments.done",
            "name": "danger",
            "call_id": "call-old",
            "arguments": "{}",
        }
    )
    await asyncio.wait_for(receiver, 0.2)
    await asyncio.sleep(0)
    assert calls == []
    assert new_socket.sent == []
    await client.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancelled_host_start_does_not_advance_turn_ownership():
    class _Session:
        starts = 0

        def notify_host_turn_started(self):
            self.starts += 1

    class _Resampler:
        def clear(self):
            return None

    class _State:
        def mark_user_input_preempt(self):
            return None

        async def fire(self, *_args, **_kwargs):
            return None

    async def noop():
        return None

    lock = asyncio.Lock()
    await lock.acquire()
    target = SimpleNamespace(
        _takeover_active=False,
        session=_Session(),
        audio_resampler=_Resampler(),
        _clear_tts_pipeline=noop,
        _tts_done_queued_for_turn=True,
        _tts_done_pending_until_ready=True,
        _current_ai_turn_text="old",
        send_user_activity=noop,
        lock=lock,
        current_speech_id="old-sid",
        state=_State(),
    )
    start = asyncio.create_task(TurnMixin.handle_new_message(target))
    await asyncio.sleep(0)
    start.cancel()
    await asyncio.gather(start, return_exceptions=True)
    lock.release()

    assert target.session.starts == 0
    assert target.current_speech_id == "old-sid"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_duplicate_created_does_not_pay_a_new_retired_owner_debt():
    sent: list[dict] = []

    async def send(event):
        sent.append(dict(event))

    arbiter = RealtimeResponseArbiter(send)
    try:
        old = arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-seen"}}
        )
        arbiter.notify_response_terminal(
            {"type": "response.done", "response": {"id": "resp-seen"}}
        )
        assert old.generation is not None

        previous = await arbiter.enqueue(source="previous")
        await asyncio.wait_for(previous.sent, 0.2)
        arbiter.notify_response_terminal(
            {"type": "response.done", "response": {"status": "completed"}}
        )
        with pytest.raises(RuntimeError, match="before response.created"):
            await asyncio.wait_for(previous.done, 0.2)
        assert len(arbiter._retired_created_windows) == 1

        duplicate = arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-seen"}}
        )
        assert duplicate.kind is ResponseCreatedKind.RETIRED
        assert duplicate.generation is None
        assert len(arbiter._retired_created_windows) == 1

        successor = await arbiter.enqueue(source="successor")
        await asyncio.sleep(0)
        assert successor.sent.done() is False
        late = arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-old-late"}}
        )
        assert late.kind is ResponseCreatedKind.RETIRED
        await asyncio.wait_for(successor.sent, 0.2)
        new = arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-new"}}
        )
        assert new.kind is ResponseCreatedKind.OWNER
        arbiter.notify_response_terminal(
            {"type": "response.done", "response": {"id": "resp-new"}}
        )
        await asyncio.wait_for(successor.done, 0.2)
    finally:
        await arbiter.shutdown()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_duplicate_terminal_does_not_consume_pending_vad_debt():
    async def send(_event):
        return None

    arbiter = RealtimeResponseArbiter(send)
    try:
        arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-seen"}}
        )
        arbiter.notify_response_terminal(
            {"type": "response.done", "response": {"id": "resp-seen"}}
        )
        arbiter.notify_server_vad_response_pending(arm_timeout=False)
        pending_generation = arbiter._server_vad_pending_generation

        duplicate = arbiter.notify_response_terminal(
            {"type": "response.done", "response": {"id": "resp-seen"}}
        )
        assert duplicate.kind.name == "STALE"
        assert arbiter._server_vad_response_pending is True
        assert arbiter._server_vad_pending_generation == pending_generation

        created = arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-vad"}}
        )
        assert created.kind is ResponseCreatedKind.SERVER_VAD
        assert created.generation == pending_generation
    finally:
        await arbiter.shutdown()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_vad_terminal_after_pending_expiry_pairs_with_late_created():
    sent: list[dict] = []

    async def send(event):
        sent.append(dict(event))

    arbiter = RealtimeResponseArbiter(send)
    try:
        arbiter.notify_server_vad_response_pending(arm_timeout=False)
        generation = arbiter._server_vad_pending_generation
        arbiter._server_vad_pending_expired()
        terminal = arbiter.notify_response_terminal(
            {
                "type": "response.done",
                "response": {"id": "resp-vad", "status": "completed"},
            }
        )
        assert terminal.generation == generation
        successor = await arbiter.enqueue(source="successor")
        await asyncio.sleep(0)
        assert successor.sent.done() is False

        created = arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-vad"}}
        )
        assert created.kind is ResponseCreatedKind.RETIRED
        assert not arbiter._response_lifetimes
        await asyncio.wait_for(successor.sent, 0.2)
    finally:
        await arbiter.shutdown()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fail_open_release_quarantines_late_idless_output():
    delivered: list[str] = []

    async def on_text(delta, _first):
        delivered.append(delta)

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-test",
        api_type="gpt",
        on_text_delta=on_text,
    )
    client._current_response_id = "resp-abandoned"
    client._is_responding = True
    client._current_turn_epoch = client._turn_epoch
    await client._on_arbiter_stuck_release("stalled", "resp-abandoned")
    assert client._idless_quarantine is True
    assert client._interrupted is True

    socket = _QueueSocket()
    client.ws = socket
    receiver = asyncio.create_task(client.handle_messages())
    socket.push({"type": "response.text.delta", "delta": "LEAK"})
    await asyncio.sleep(0.02)
    assert delivered == []
    await _cancel_task(receiver)
    await client.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_suppressed_unannounced_turn_is_silent_before_owner_assignment():
    delivered: list[str] = []

    async def on_text(delta, _first):
        delivered.append(delta)

    client = OmniRealtimeClient(
        "wss://www.lanlan.app/core",
        "free-access",
        model="free-model",
        api_type="free",
        on_text_delta=on_text,
    )
    socket = _QueueSocket()
    client.ws = socket
    receiver = asyncio.create_task(client.handle_messages())
    try:
        ticket = await client._response_arbiter.enqueue(
            source="silent",
            events_before_response=(
                {
                    "type": "conversation.item.create",
                    "item": {"id": "item-silent", "role": "user"},
                },
            ),
            ack_expected=True,
            expected_item_id="item-silent",
            expected_item_role="user",
            suppress_output=True,
        )
        for _ in range(20):
            if socket.sent:
                break
            await asyncio.sleep(0.005)
        assert client._response_arbiter._response_owner is None
        socket.push({"type": "response.text.delta", "delta": "SHOULD_BE_SILENT"})
        await asyncio.sleep(0.02)
        assert client._skip_until_next_response is True
        assert delivered == []

        socket.push(
            {
                "type": "conversation.item.created",
                "item": {"id": "item-silent", "role": "user"},
            }
        )
        await asyncio.wait_for(ticket.sent, 0.2)
        socket.push(
            {
                "type": "response.done",
                "response": {"id": "resp-silent", "status": "completed"},
            }
        )
        await asyncio.wait_for(ticket.done, 0.2)
    finally:
        await _cancel_task(receiver)
        await client.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_never_announcing_ownerless_response_has_a_cancellable_lifetime():
    client = OmniRealtimeClient(
        "wss://www.lanlan.app/core",
        "free-access",
        model="free-model",
        api_type="free",
    )
    socket = _QueueSocket()
    client.ws = socket
    receiver = asyncio.create_task(client.handle_messages())
    try:
        # This is an automatic/provider response: no queued request and no
        # response.created frame. Its first id-less activity must still create
        # one bounded generation so response.cancel is neither lost nor
        # detached from the response it intends to stop.
        socket.push({"type": "response.text.delta", "delta": "provider output"})
        for _ in range(20):
            if client._current_response_generation is not None:
                break
            await asyncio.sleep(0.005)
        assert client._current_response_generation is not None

        await client.cancel_response(wait=False)
        assert [event["type"] for event in socket.sent] == ["response.cancel"]

        socket.push(
            {
                "type": "response.done",
                "response": {"status": "cancelled"},
            }
        )
        for _ in range(20):
            if client._current_response_generation is None:
                break
            await asyncio.sleep(0.005)
        assert client._current_response_generation is None
    finally:
        await _cancel_task(receiver)
        await client.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hung_host_turn_barrier_fails_connection_closed(monkeypatch):
    monkeypatch.setattr(
        "main_logic.omni_realtime_client._transport."
        "_HOST_TURN_RELEASE_BARRIER_TIMEOUT",
        0.02,
    )
    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-test",
        api_type="gpt",
    )
    socket = _QueueSocket()
    client.ws = socket
    client._host_turn_release_ready.clear()
    ticket = await client._response_arbiter.enqueue(source="blocked-successor")
    with pytest.raises(ConnectionError, match="host turn finalization"):
        await asyncio.wait_for(ticket.done, 0.2)
    assert socket.closed is True
    assert client.ws is None
    await client.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_two_vad_boundaries_keep_two_created_obligations():
    sent: list[dict] = []

    async def send(event):
        sent.append(dict(event))

    arbiter = RealtimeResponseArbiter(send)
    try:
        arbiter.notify_server_vad_response_pending(arm_timeout=False)
        first_generation = arbiter._server_vad_pending_generation
        arbiter.notify_server_vad_response_pending(arm_timeout=False)
        assert len(arbiter._retired_created_windows) == 2

        first = arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-vad-1"}}
        )
        assert first.generation == first_generation
        assert arbiter._server_vad_response_pending is True
        assert len(arbiter._retired_created_windows) == 1
        arbiter.notify_response_terminal(
            {"type": "response.done", "response": {"id": "resp-vad-1"}}
        )

        successor = await arbiter.enqueue(source="successor")
        await asyncio.sleep(0)
        assert successor.sent.done() is False
        second = arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-vad-2"}}
        )
        assert second.kind is ResponseCreatedKind.SERVER_VAD
        arbiter.notify_response_terminal(
            {"type": "response.done", "response": {"id": "resp-vad-2"}}
        )
        await asyncio.wait_for(successor.sent, 0.2)
    finally:
        await arbiter.shutdown()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ambiguous_unscoped_cancel_is_not_sent():
    sent: list[dict] = []
    aborted: list[str] = []

    async def send(event):
        sent.append(dict(event))

    async def abort(reason):
        aborted.append(reason)

    arbiter = RealtimeResponseArbiter(send, abort_transport=abort)
    arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-a"}}
    )
    arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-b"}}
    )
    await arbiter.cancel_current(wait=False)
    assert not any(event["type"] == "response.cancel" for event in sent)
    assert aborted == []
    assert arbiter._connection_available is True
    await arbiter.shutdown()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_connection_task_retirement_is_bounded_when_handler_ignores_cancel(
    monkeypatch,
):
    monkeypatch.setattr(
        "main_logic.omni_realtime_client._client."
        "_CONNECTION_TASK_RETIRE_TIMEOUT",
        0.01,
    )
    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-test",
        api_type="gpt",
    )
    started = asyncio.Event()
    release = asyncio.Event()

    async def stubborn_handler():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    task = client._fire_connection_task(stubborn_handler())
    await asyncio.wait_for(started.wait(), 0.2)
    await asyncio.wait_for(client._begin_connection_generation(), 0.2)
    assert task.done() is False
    assert task not in client._connection_tasks
    release.set()
    await asyncio.wait_for(task, 0.2)
    await client.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_connection_task_failure_is_retrieved_without_logging_payload(caplog):
    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-test",
        api_type="gpt",
    )

    async def fail():
        raise RuntimeError("private provider payload")

    with caplog.at_level(logging.WARNING):
        task = client._fire_connection_task(fail())
        await asyncio.wait({task})
        await asyncio.sleep(0)

    assert "connection-scoped task failed: RuntimeError" in caplog.text
    assert "private provider payload" not in caplog.text
    await client.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_connection_task_can_close_its_own_socket_without_self_cancel():
    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-test",
        api_type="gpt",
    )
    socket = _QueueSocket()
    client.ws = socket
    generation = client._client_connection_generation

    task = client._fire_connection_task(
        client._close_if_current_connection(generation)
    )
    await asyncio.wait_for(task, 0.2)

    assert task.cancelled() is False
    assert socket.closed is True
    assert client.ws is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_deferred_sid_rotation_timeout_is_logged_and_keeps_retry_state(
    monkeypatch,
    caplog,
):
    monkeypatch.setattr(
        "main_logic.omni_realtime_client._transport."
        "_STUCK_RELEASE_STEP_TIMEOUT",
        0.01,
    )
    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-test",
        api_type="gpt",
    )
    client._has_server_vad = False
    client._sid_rotation_required_before_dispatch = True

    async def stuck_rotation():
        await asyncio.Event().wait()

    client.on_sid_rotate = stuck_rotation
    with caplog.at_level(logging.ERROR):
        with pytest.raises(asyncio.TimeoutError):
            await client._before_response_dispatch()

    assert "deferred sid rotation exceeded" in caplog.text
    assert client._sid_rotation_required_before_dispatch is True
    await client.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_vad_terminal_does_not_identity_upgrade_an_unrelated_idless_orphan():
    sent: list[dict] = []

    async def send(event):
        sent.append(dict(event))

    arbiter = RealtimeResponseArbiter(send)
    try:
        orphan = arbiter.notify_response_created({"type": "response.created"})
        arbiter.notify_server_vad_response_pending(arm_timeout=False)
        arbiter._server_vad_pending_expired()
        terminal = arbiter.notify_response_terminal(
            {"type": "response.done", "response": {"id": "resp-vad"}}
        )
        assert terminal.generation != orphan.generation
        assert any(
            lifetime.generation == orphan.generation
            and lifetime.response_id is None
            for lifetime in arbiter._response_lifetimes
        )

        retired = arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-vad"}}
        )
        assert retired.kind is ResponseCreatedKind.RETIRED
        successor = await arbiter.enqueue(source="successor")
        await asyncio.sleep(0)
        assert successor.sent.done() is False
        arbiter.notify_response_terminal({"type": "response.done"})
        await asyncio.wait_for(successor.sent, 0.2)
    finally:
        await arbiter.shutdown()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_noncurrent_terminal_keeps_current_tool_argument_stream():
    client = OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-test",
        api_type="gpt",
    )
    older = client._response_arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-older"}}
    )
    current = client._response_arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-current"}}
    )
    assert older.generation is not None
    assert current.generation is not None
    client._activate_response_state("resp-current", current.generation)
    client._inflight_tool_args["call-current"] = {
        "name": "tool",
        "arguments": '{"x":',
        "response_id": "resp-current",
    }
    socket = _QueueSocket()
    client.ws = socket
    receiver = asyncio.create_task(client.handle_messages())
    socket.push(
        {"type": "response.done", "response": {"id": "resp-older"}}
    )
    await asyncio.sleep(0.02)
    assert "call-current" in client._inflight_tool_args
    assert client._current_response_generation == current.generation
    await _cancel_task(receiver)
    await client.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_idless_lifetime_overflow_fails_connection_closed():
    aborted: list[str] = []

    async def send(_event):
        return None

    async def abort(reason):
        aborted.append(reason)

    arbiter = RealtimeResponseArbiter(send, abort_transport=abort)
    for _ in range(32):
        arbiter.notify_response_created({"type": "response.created"})
    with pytest.raises(ConnectionError, match="too many live idless|unowned"):
        arbiter.notify_response_created({"type": "response.created"})
    await asyncio.sleep(0)
    assert aborted == ["too many live unowned server responses"]
    assert arbiter._connection_available is False
    await arbiter.shutdown()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_late_identified_activity_pays_retired_created_debt_without_leaking():
    async def send(_event):
        return None

    arbiter = RealtimeResponseArbiter(send)
    try:
        previous = await arbiter.enqueue(source="previous")
        await asyncio.wait_for(previous.sent, 0.2)
        arbiter.notify_response_terminal(
            {"type": "response.done", "response": {"status": "completed"}}
        )
        with pytest.raises(RuntimeError, match="before response.created"):
            await asyncio.wait_for(previous.done, 0.2)

        assert arbiter.notify_response_activity("resp-old-late") is None
        assert not arbiter._retired_created_windows
        assert not arbiter._response_lifetimes

        successor = await arbiter.enqueue(source="successor")
        await asyncio.wait_for(successor.sent, 0.2)
        generation = arbiter.notify_response_activity("resp-new")
        assert generation is not None
        arbiter.notify_response_terminal(
            {"type": "response.done", "response": {"id": "resp-new"}}
        )
        await asyncio.wait_for(successor.done, 0.2)
    finally:
        await arbiter.shutdown()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_owned_cancel_with_concurrent_orphan_is_not_sent():
    sent: list[dict] = []
    aborted: list[str] = []

    async def send(event):
        sent.append(dict(event))

    async def abort(reason):
        aborted.append(reason)

    arbiter = RealtimeResponseArbiter(send, abort_transport=abort)
    owner = await arbiter.enqueue(source="owner")
    await asyncio.wait_for(owner.sent, 0.2)
    arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-owner"}}
    )
    arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-orphan"}}
    )
    await arbiter.cancel_current(wait=False)
    assert not any(event["type"] == "response.cancel" for event in sent)
    assert aborted == []
    assert arbiter._connection_available is True
    await arbiter.shutdown()
