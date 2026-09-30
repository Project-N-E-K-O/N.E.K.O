"""Deterministic ownership and queue boundaries for Qwen pause recovery."""

import asyncio
import base64
import json

import pytest

from main_logic.asr_client._infra import (
    AsrSessionConfig,
    _AsrRequestQueue,
    _AsrWorkerRequest,
)
from main_logic.asr_client.workers import qwen
from tests.unit.test_asr_workers import (
    _FakeConnector,
    _FakeWebSocket,
    _next_event,
    _stop_worker,
    _wait_until,
)

pytestmark = pytest.mark.unit_fast


def _state():
    state = qwen._QwenConnectionState(0, 0, 3, False)
    state.configured.set()
    state.current_provider_utterance_id = 2
    state.last_utterance_id = 1
    state.item_keys["current"] = (0, 0, 2)
    return state


async def test_silent_audio_preserves_pause_timer_and_finishes(monkeypatch):
    monkeypatch.setattr(qwen, "_QWEN_LOCAL_FINISH_GRACE_SECONDS", 100)
    state = _state()

    async def on_send(ws, payload):
        if json.loads(payload)["type"] == "session.finish":
            state.finish_received.set()

    ws = _FakeWebSocket(on_send=on_send)
    requests, responses = asyncio.Queue(), asyncio.Queue()
    task = asyncio.create_task(qwen._qwen_sender(
        ws, requests, responses, AsrSessionConfig(endpointing_mode="provider"), state
    ))
    try:
        await requests.put(_AsrWorkerRequest("activity", 0, speech_active=False))
        await asyncio.wait_for(requests.join(), 1)
        timer = state.fallback_timer_task
        assert state.fallback_key == (0, 0, 2)
        for _ in range(5):
            await requests.put(_AsrWorkerRequest("audio", 0, utterance_id=1, audio=b"\0\0"))
        await asyncio.wait_for(requests.join(), 1)
        assert state.fallback_timer_task is timer
        assert state.fallback_key == (0, 0, 2)
        state.fallback_due.set()
        assert await asyncio.wait_for(task, 1) == ("reconnect", None)
        assert [json.loads(p)["type"] for p in ws.sent] == [
            *(["input_audio_buffer.append"] * 5), "session.finish"
        ]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("endpoint", ["speech_stopped", "committed"])
async def test_repeated_turn_uses_provider_id_and_late_endpoint_is_scoped(monkeypatch, endpoint):
    monkeypatch.setattr(qwen, "_QWEN_LOCAL_FINISH_GRACE_SECONDS", 100)
    state = qwen._QwenConnectionState(0, 0, 1, False)
    state.configured.set()
    ws = _FakeWebSocket()
    requests, responses = asyncio.Queue(), asyncio.Queue()
    sender = asyncio.create_task(qwen._qwen_sender(
        ws, requests, responses, AsrSessionConfig(endpointing_mode="provider"), state
    ))
    receiver = asyncio.create_task(qwen._qwen_receiver(
        ws, responses, AsrSessionConfig(endpointing_mode="provider"), state
    ))
    try:
        for item_id in ("previous", "current"):
            await ws.server_send({"type": "input_audio_buffer.speech_started", "item_id": item_id})
            await _next_event(responses, "utterance_started")
        assert state.current_provider_utterance_id == 2
        # PCM still has local id=1 on the second provider turn.
        await requests.put(_AsrWorkerRequest("audio", 0, utterance_id=1, audio=b"\0\0"))
        await requests.put(_AsrWorkerRequest("activity", 0, speech_active=False))
        await asyncio.wait_for(requests.join(), 1)
        assert state.fallback_key == (0, 0, 2)
        await ws.server_send({"type": f"input_audio_buffer.{endpoint}", "item_id": "previous"})
        await _wait_until(lambda: 1 in state.provider_endpoint_utterance_ids)
        assert state.fallback_key == (0, 0, 2)
        await ws.server_send({"type": f"input_audio_buffer.{endpoint}", "item_id": "current"})
        await _wait_until(lambda: 2 in state.provider_endpoint_utterance_ids)
        await requests.put(_AsrWorkerRequest("activity", 0, speech_active=False))
        await asyncio.wait_for(requests.join(), 1)
        assert state.fallback_key is None
        await ws.server_send({
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "previous", "transcript": "first",
        })
        assert (await _next_event(responses, "final")).text == "first"
        assert state.current_provider_utterance_id == 2
        await ws.server_send({
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "current", "transcript": "second",
        })
        assert (await _next_event(responses, "final")).text == "second"
        assert state.current_provider_utterance_id is None
        await requests.put(_AsrWorkerRequest("activity", 0, speech_active=False))
        await asyncio.wait_for(requests.join(), 1)
        assert state.fallback_key is None
        assert not any(json.loads(p)["type"] == "session.finish" for p in ws.sent)
    finally:
        sender.cancel()
        receiver.cancel()
        await asyncio.gather(sender, receiver, return_exceptions=True)


@pytest.mark.parametrize("command", ["audio", "activity", "clear", "shutdown"])
async def test_timer_snapshot_does_not_drop_a_completed_getter(monkeypatch, command):
    state = _state()
    state.fallback_key = (0, 0, 2)
    state.fallback_due.set()
    requests, responses = asyncio.Queue(), asyncio.Queue()
    original_wait = asyncio.wait
    injected = asyncio.Event()
    request = _AsrWorkerRequest(command, 0, utterance_id=1, audio=b"\x01\x02", speech_active=True)

    async def wait_with_late_getter(tasks, **kwargs):
        done, pending = await original_wait(tasks, **kwargs)
        if not injected.is_set() and len(tasks) == 2:
            assert len(pending) == 1
            requests.put_nowait(request)
            # Complete the real getter after wait() captured its result set.
            await next(iter(pending))
            injected.set()
        return done, pending

    async def on_send(ws, payload):
        if json.loads(payload)["type"] == "session.finish":
            state.finish_received.set()

    ws = _FakeWebSocket(on_send=on_send)
    monkeypatch.setattr(qwen.asyncio, "wait", wait_with_late_getter)
    task = asyncio.create_task(qwen._qwen_sender(
        ws, requests, responses, AsrSessionConfig(endpointing_mode="provider"), state
    ))
    try:
        await asyncio.wait_for(injected.wait(), 1)
        await asyncio.wait_for(requests.join(), 1)
        if command == "audio":
            assert json.loads(ws.sent[0])["audio"] == base64.b64encode(request.audio).decode()
        elif command == "activity":
            assert state.fallback_key is None
            assert ws.sent == []
        else:
            assert (await asyncio.wait_for(task, 1))[0] == command
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_endpoint_cancelling_timer_during_getter_join_keeps_sender_alive(monkeypatch):
    state = _state()
    state.fallback_key = (0, 0, 2)
    state.fallback_due.set()
    cancelled = asyncio.Event()

    class EndpointAtCancelQueue(asyncio.Queue):
        async def get(self):
            try:
                return await super().get()
            except asyncio.CancelledError:
                state.provider_endpoint_utterance_ids.add(2)
                qwen._qwen_cancel_provider_fallback(state)
                cancelled.set()
                raise

    requests, responses = EndpointAtCancelQueue(), asyncio.Queue()
    ws = _FakeWebSocket()
    task = asyncio.create_task(qwen._qwen_sender(
        ws, requests, responses, AsrSessionConfig(endpointing_mode="provider"), state
    ))
    try:
        await asyncio.wait_for(cancelled.wait(), 1)
        await requests.put(_AsrWorkerRequest("audio", 0, utterance_id=1, audio=b"\0\0"))
        await asyncio.wait_for(requests.join(), 1)
        assert not task.done()
        assert [json.loads(p)["type"] for p in ws.sent] == ["input_audio_buffer.append"]
        assert responses.empty()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("acknowledge", [True, False])
async def test_audio_arriving_after_finish_stays_bounded_and_reaches_new_connection(monkeypatch, acknowledge):
    finish_sent = asyncio.Event()
    first_finish_waiting = asyncio.Event()
    original_state = qwen._QwenConnectionState

    class ObservedFinishEvent(asyncio.Event):
        async def wait(self):
            first_finish_waiting.set()
            return await super().wait()

    def create_state(**kwargs):
        state = original_state(**kwargs)
        if kwargs["emit_ready"]:
            state.finish_received = ObservedFinishEvent()
        return state

    monkeypatch.setattr(qwen, "_QwenConnectionState", create_state)

    async def on_send(ws, payload):
        message = json.loads(payload)
        if message["type"] == "session.update":
            await ws.server_send({"type": "session.updated"})
        elif message["type"] == "session.finish":
            finish_sent.set()
            if ws is second:
                await ws.server_send({"type": "session.finished"})

    first, second = _FakeWebSocket(on_send=on_send), _FakeWebSocket(on_send=on_send)
    connector = _FakeConnector(first, second)
    monkeypatch.setattr(qwen.websockets, "connect", connector)
    monkeypatch.setattr(qwen, "_QWEN_LOCAL_FINISH_GRACE_SECONDS", 0)
    # Timeout is bounded but generous enough to put requests behind the send barrier.
    monkeypatch.setattr(qwen, "_QWEN_FINISH_TIMEOUT_SECONDS", 0.2)
    requests, responses = _AsrRequestQueue(), asyncio.Queue()
    task = asyncio.create_task(qwen.qwen_asr_worker(
        requests, responses, "key", AsrSessionConfig(endpointing_mode="provider")
    ))
    try:
        await _next_event(responses, "ready")
        await first.server_send({"type": "input_audio_buffer.speech_started", "item_id": "old"})
        await _next_event(responses, "utterance_started")
        await requests.put(_AsrWorkerRequest("activity", 0, speech_active=False))
        await asyncio.wait_for(finish_sent.wait(), 1)
        await requests.put(_AsrWorkerRequest("activity", 0, speech_active=True))
        chunks = [b"\x01\x02" * 160, b"\x03\x04" * 160]
        for chunk in chunks:
            await requests.put(_AsrWorkerRequest("audio", 0, utterance_id=1, audio=chunk))
        await asyncio.wait_for(first_finish_waiting.wait(), 1)
        assert requests.waiting_audio_bytes == sum(map(len, chunks))
        assert not any(json.loads(p)["type"] == "input_audio_buffer.append" for p in first.sent)
        if acknowledge:
            await first.server_send({
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "old", "transcript": "old result",
            })
            await first.server_send({"type": "session.finished"})
        old_final = await _next_event(responses, "final")
        assert old_final.text == ("old result" if acknowledge else "")
        await _wait_until(lambda: len(connector.calls) == 2)
        await asyncio.wait_for(requests.join(), 1)
        assert [base64.b64decode(json.loads(p)["audio"]) for p in second.sent
                if json.loads(p)["type"] == "input_audio_buffer.append"] == chunks
        assert requests.waiting_audio_bytes == 0
        await first.server_send({
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "old", "transcript": "late duplicate",
        })
        await second.server_send({"type": "input_audio_buffer.speech_started", "item_id": "new"})
        assert (await _next_event(responses, "utterance_started")).utterance_id == 2
        await second.server_send({
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "new", "transcript": "new result",
        })
        assert (await _next_event(responses, "final")).text == "new result"
        await _stop_worker(task, requests, responses, utterance_id=3)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_sender_cancellation_releases_getter_and_grace_timer(monkeypatch):
    monkeypatch.setattr(qwen, "_QWEN_LOCAL_FINISH_GRACE_SECONDS", 100)
    state = _state()
    requests, responses = asyncio.Queue(), asyncio.Queue()
    task = asyncio.create_task(qwen._qwen_sender(
        _FakeWebSocket(), requests, responses, AsrSessionConfig(endpointing_mode="provider"), state
    ))
    await requests.put(_AsrWorkerRequest("activity", 0, speech_active=False))
    await asyncio.wait_for(requests.join(), 1)
    timer = state.fallback_timer_task
    await _wait_until(lambda: bool(requests._getters))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.gather(timer, return_exceptions=True)
    assert not requests._getters
    assert state.fallback_key is None
    assert timer.done()
