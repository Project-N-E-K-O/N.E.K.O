"""Real realtime client dispatch remains live under manager ownership guards."""

import asyncio
import json

import pytest

from main_logic.core.session_lifecycle import SessionOwnershipMixin
from main_logic.omni_realtime_client import OmniRealtimeClient
from main_logic.omni_realtime_client import _transport


class _Manager(SessionOwnershipMixin):
    pass


class _ProtocolSocket:
    def __init__(self):
        self.frames = asyncio.Queue()
        self.sent = []

    async def send(self, payload):
        event = json.loads(payload)
        self.sent.append(event)
        if event["type"] == "conversation.item.create":
            self.frames.put_nowait({"type": "conversation.item.created", "item": event["item"]})
        elif event["type"] == "response.create":
            self.frames.put_nowait({"type": "response.created", "response": {"id": "reply"}})
            self.frames.put_nowait({"type": "response.audio_transcript.delta", "response_id": "reply", "delta": "reply text"})
            self.frames.put_nowait({"type": "response.audio_transcript.done", "response_id": "reply", "transcript": "reply text"})
            self.frames.put_nowait({"type": "response.done", "response": {"id": "reply", "status": "completed"}})

    def __aiter__(self):
        return self

    async def __anext__(self):
        event = await self.frames.get()
        if event is None:
            raise StopAsyncIteration
        return json.dumps(event)

    async def close(self):
        self.frames.put_nowait(None)


@pytest.mark.asyncio
@pytest.mark.parametrize("bind_owned", [False, True])
async def test_qwen_external_voice_dispatch_with_real_client_and_owned_callbacks(bind_owned, monkeypatch):
    outputs = []
    completed = asyncio.Event()

    async def output(text, *args):
        outputs.append(text)

    async def done(*args):
        completed.set()

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime", "test-key",
        model="qwen3.5-omni-flash-realtime", api_type="qwen",
        on_output_transcript=output, on_response_done=done,
    )
    socket = _ProtocolSocket()

    async def connect(*args, **kwargs):
        return socket

    monkeypatch.setattr(_transport.websockets, "connect", connect)
    manager = _Manager()
    manager.session = None
    if bind_owned:
        manager._bind_owned_output_callbacks(client)
    await manager._connect_owned_session(client, "Test system instructions", native_audio=True)
    manager.session = client
    receiver = asyncio.create_task(client.handle_messages())
    try:
        await asyncio.wait_for(client.prepare_external_voice_turn(turn_id="voice-one"), 1)
        await asyncio.wait_for(client.submit_external_voice_turn("test input", turn_id="voice-one"), 1)
        await asyncio.wait_for(completed.wait(), 1)
        assert [event["type"] for event in socket.sent if event["type"] not in {"response.cancel", "session.update"}] == [
            "conversation.item.create", "response.create",
        ]
        assert outputs == ["reply text"]
        assert not client._response_arbiter.is_busy
    finally:
        await asyncio.wait_for(client.close(), 2)
        await asyncio.wait_for(receiver, 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_prepare", [False, True])
async def test_completed_ticket_waits_for_interruption_cleanup_even_with_a_permit(cancel_prepare):
    client = OmniRealtimeClient(
        "wss://example.invalid/realtime", "test-key",
        model="qwen3.5-omni-flash-realtime", api_type="qwen",
    )
    cancelling = asyncio.Event()
    allow_cancel = asyncio.Event()
    selected = asyncio.Event()

    class CancellingSocket(_ProtocolSocket):
        async def send(self, payload):
            if json.loads(payload)["type"] == "response.cancel":
                cancelling.set()
                await allow_cancel.wait()
            await super().send(payload)

    class ObservedQueue(asyncio.PriorityQueue):
        async def get(self):
            result = await super().get()
            selected.set()
            return result

    socket = CancellingSocket()
    client.ws = socket
    client._is_responding = True
    client._current_response_id = "previous-response"
    client._response_arbiter._queue = ObservedQueue()
    receiver = asyncio.create_task(client.handle_messages())
    preparing = asyncio.create_task(client.prepare_external_voice_turn(turn_id="voice-new"))
    submitting = None
    try:
        await asyncio.wait_for(cancelling.wait(), 1)
        submitting = asyncio.create_task(client.submit_external_voice_turn("completed input", turn_id="voice-old"))
        await asyncio.wait_for(selected.wait(), 1)
        assert not any(event["type"] == "conversation.item.create" for event in socket.sent)
        if cancel_prepare:
            preparing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await preparing
        else:
            allow_cancel.set()
            await preparing
        await asyncio.wait_for(submitting, 1)
        assert any(event["type"] == "response.create" for event in socket.sent)
    finally:
        allow_cancel.set()
        preparing.cancel()
        if submitting is not None:
            submitting.cancel()
        await asyncio.gather(preparing, *([submitting] if submitting is not None else []), return_exceptions=True)
        await asyncio.wait_for(client.close(), 2)
        await asyncio.wait_for(receiver, 2)


@pytest.mark.asyncio
async def test_completed_external_turn_crosses_later_prepare_but_proactive_stays_paused():
    client = OmniRealtimeClient(
        "wss://example.invalid/realtime", "test-key",
        model="qwen3.5-omni-flash-realtime", api_type="qwen",
    )
    socket = _ProtocolSocket()
    client.ws = socket
    selected = asyncio.Event()
    allow_selection = asyncio.Event()

    class SelectionBarrierQueue(asyncio.PriorityQueue):
        async def get(self):
            selected.set()
            await allow_selection.wait()
            return await super().get()

    arbiter = client._response_arbiter
    arbiter._queue = SelectionBarrierQueue()
    receiver = asyncio.create_task(client.handle_messages())
    submitting = None
    try:
        await client.prepare_external_voice_turn(turn_id="voice-old")
        submitting = asyncio.create_task(client.submit_external_voice_turn("completed input", turn_id="voice-old"))
        await asyncio.wait_for(selected.wait(), 1)
        await client.prepare_external_voice_turn(turn_id="voice-new")
        proactive = await arbiter.enqueue(source="proactive")
        allow_selection.set()
        await asyncio.wait_for(asyncio.shield(submitting), 0.3)
        assert any(event["type"] == "response.create" for event in socket.sent)
        assert client._external_voice_turn_pause_id == "voice-new"
        assert not proactive.sent.done(), "the newer speech turn must still block proactive work"
    finally:
        allow_selection.set()
        if submitting is not None:
            submitting.cancel()
            await asyncio.gather(submitting, return_exceptions=True)
        await asyncio.wait_for(client.close(), 2)
        await asyncio.wait_for(receiver, 2)


@pytest.mark.asyncio
async def test_late_duplicate_prepare_cannot_repause_an_already_submitted_turn():
    client = OmniRealtimeClient(
        "wss://example.invalid/realtime", "test-key",
        model="qwen3.5-omni-flash-realtime", api_type="qwen",
    )
    socket = _ProtocolSocket()
    client.ws = socket
    selected = asyncio.Event()
    allow_selection = asyncio.Event()

    class SelectionBarrierQueue(asyncio.PriorityQueue):
        async def get(self):
            selected.set()
            await allow_selection.wait()
            return await super().get()

    client._response_arbiter._queue = SelectionBarrierQueue()
    receiver = asyncio.create_task(client.handle_messages())
    submitting = None
    try:
        await client.prepare_external_voice_turn(turn_id="voice-one")
        submitting = asyncio.create_task(client.submit_external_voice_turn("test input", turn_id="voice-one"))
        await asyncio.wait_for(selected.wait(), 1)
        assert client._external_voice_turn_pause_id is None
        # The same speech-start is delivered late, after its final was queued.
        # The real arbiter has not selected the queued request yet.
        await client.prepare_external_voice_turn(turn_id="voice-one")
        allow_selection.set()
        await asyncio.wait_for(submitting, 1)
        assert any(event["type"] == "response.create" for event in socket.sent)
        # Core's final-dispatch finally releases this exact pause identity.
        client.abandon_external_voice_turn("voice-one")
        assert client._external_voice_turn_pause_id is None
    finally:
        allow_selection.set()
        if submitting is not None:
            submitting.cancel()
            await asyncio.gather(submitting, return_exceptions=True)
        await asyncio.wait_for(client.close(), 2)
        await asyncio.wait_for(receiver, 2)
