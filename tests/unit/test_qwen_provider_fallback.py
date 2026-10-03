"""Deterministic ownership and queue boundaries for Qwen pause recovery."""

import asyncio
import base64
import json
from collections import deque

import pytest

from main_logic.asr_client._infra import (
    AsrSessionConfig,
    _AsrRequestQueue,
    _AsrWorkerRequest,
    _RealtimeAsrSessionImpl,
)
from main_logic.asr_client.provider_policy import resolve_provider_policy
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


async def test_pause_before_provider_start_arms_fallback(monkeypatch):
    monkeypatch.setattr(qwen, "_QWEN_LOCAL_FINISH_GRACE_SECONDS", 0)
    state = qwen._QwenConnectionState(0, 0, 1, False)
    state.configured.set()
    finish_sent = asyncio.Event()

    async def on_send(ws, payload):
        if json.loads(payload)["type"] == "session.finish":
            finish_sent.set()

    ws = _FakeWebSocket(on_send=on_send)
    requests, responses = asyncio.Queue(), asyncio.Queue()
    sender = asyncio.create_task(
        qwen._qwen_sender(
            ws,
            requests,
            responses,
            AsrSessionConfig(endpointing_mode="provider"),
            state,
        )
    )
    receiver = asyncio.create_task(
        qwen._qwen_receiver(
            ws,
            responses,
            AsrSessionConfig(endpointing_mode="provider"),
            state,
        )
    )
    try:
        # The local detector can report pause before the provider has emitted
        # speech_started for the same buffered audio.
        await requests.put(_AsrWorkerRequest("activity", 0, speech_active=False))
        await asyncio.wait_for(requests.join(), 1)
        assert state.pending_local_pause == (0, 0)
        await ws.server_send(
            {"type": "input_audio_buffer.speech_started", "item_id": "late"}
        )
        await _next_event(responses, "utterance_started")
        await asyncio.wait_for(finish_sent.wait(), 1)
    finally:
        sender.cancel()
        receiver.cancel()
        await asyncio.gather(sender, receiver, return_exceptions=True)


async def test_finish_waits_for_provider_after_one_deferred_request():
    state = _state()
    finish_sent = asyncio.Event()

    async def on_send(ws, payload):
        if json.loads(payload)["type"] == "session.finish":
            finish_sent.set()

    ws = _FakeWebSocket(on_send=on_send)
    requests, responses = asyncio.Queue(), asyncio.Queue()
    deferred = deque()
    task = asyncio.create_task(
        qwen._qwen_finish_and_reconnect(
            ws,
            requests,
            responses,
            state,
            deferred,
        )
    )
    try:
        await asyncio.wait_for(finish_sent.wait(), 1)
        await requests.put(_AsrWorkerRequest("activity", 0, speech_active=True))
        await _wait_until(lambda: len(deferred) == 1)
        assert not task.done()
        state.finish_received.set()
        assert await asyncio.wait_for(task, 1) == ("reconnect", None)
        assert deferred[0].kind == "activity"
    finally:
        if not task.done():
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
        # The control request may be the single bounded handoff; audio remains
        # in the public queue and is still counted by normal backpressure.
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


async def test_shutdown_during_finish_closes_old_session_without_reconnect(monkeypatch):
    finish_sent = asyncio.Event()

    async def on_send(ws, payload):
        message = json.loads(payload)
        if message["type"] == "session.update":
            await ws.server_send({"type": "session.updated"})
        elif message["type"] == "session.finish":
            finish_sent.set()

    first, second = _FakeWebSocket(on_send=on_send), _FakeWebSocket(on_send=on_send)
    connector = _FakeConnector(first, second)
    monkeypatch.setattr(qwen.websockets, "connect", connector)
    monkeypatch.setattr(qwen, "_QWEN_LOCAL_FINISH_GRACE_SECONDS", 0)
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
        await requests.put(_AsrWorkerRequest("shutdown", 0, utterance_id=2))
        closed = await _next_event(responses, "closed", timeout=2)
        assert closed.utterance_id == 2
        await asyncio.wait_for(task, 1)
        await asyncio.wait_for(requests.join(), 1)
        assert len(connector.calls) == 1
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


@pytest.mark.parametrize("cancel_count", [1, 2])
async def test_external_sender_cancel_during_getter_join_propagates(cancel_count):
    state = _state()
    state.fallback_key = (0, 0, 2)
    state.fallback_due.set()
    task = None

    class EndpointAtCancelQueue(_AsrRequestQueue):
        async def get(self):
            try:
                return await super().get()
            except asyncio.CancelledError:
                state.provider_endpoint_utterance_ids.add(2)
                qwen._qwen_cancel_provider_fallback(state)
                # Cancel the owner before the real getter's completion wakes
                # it. No extra suspension is added to asyncio.wait or join.
                assert task is not None
                for _ in range(cancel_count):
                    asyncio.get_running_loop().call_soon(task.cancel)
                raise

    requests, responses = EndpointAtCancelQueue(), asyncio.Queue()
    ws = _FakeWebSocket()
    task = asyncio.create_task(qwen._qwen_sender(
        ws, requests, responses, AsrSessionConfig(endpointing_mode="provider"), state
    ))
    try:
        done, _ = await asyncio.wait({task}, timeout=1)
        assert task in done, "sender swallowed its owner's cancellation"
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()
        assert not requests._getters
        assert state.fallback_key is None
        assert ws.sent == []
        assert responses.empty()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_provider_failure_at_fallback_due_releases_sender(monkeypatch):
    monkeypatch.setattr(qwen, "_QWEN_LOCAL_FINISH_GRACE_SECONDS", 0)
    close_release = asyncio.Event()
    errors = []
    before = asyncio.all_tasks()

    class ClosingWebSocket(_FakeWebSocket):
        async def close(self):
            await super().close()
            # Model a suspended closing handshake, which can receive a second
            # worker cancellation from the session's failure cleanup.
            await close_release.wait()

    async def on_send(ws, payload):
        if json.loads(payload)["type"] == "session.update":
            await ws.server_send({"type": "session.updated"})

    ws = ClosingWebSocket(on_send=on_send)
    monkeypatch.setattr(qwen, "websockets", type(
        "Connector", (), {"connect": staticmethod(_FakeConnector(ws))}
    ))

    class FailureAtDue(asyncio.Event):
        def set(self):
            super().set()
            # Deliver ordinary provider frames after the fallback waiter is
            # ready, through the unchanged receiver and session error path.
            ws.incoming.put_nowait(json.dumps({
                "type": "input_audio_buffer.speech_stopped", "item_id": "current"
            }))
            ws.incoming.put_nowait(json.dumps({
                "type": "error", "error": {"code": "controlled_error"}
            }))

    original_state = qwen._QwenConnectionState

    def connection_state(*args, **kwargs):
        return original_state(*args, **kwargs, fallback_due=FailureAtDue())

    monkeypatch.setattr(qwen, "_QwenConnectionState", connection_state)

    async def on_final(_text):
        pass

    async def on_error(error):
        errors.append(error)

    session = _RealtimeAsrSessionImpl(
        worker_fn=qwen.qwen_asr_worker,
        api_key="key",
        config=AsrSessionConfig(endpointing_mode="provider"),
        on_input_transcript=on_final,
        on_connection_error=on_error,
        provider_policy=resolve_provider_policy("qwen", "provider"),
    )
    try:
        await session.connect()
        await ws.server_send({
            "type": "input_audio_buffer.speech_started", "item_id": "current"
        })
        await _wait_until(lambda: bool(session._active_utterance_keys))
        await session.signal_local_activity(speech_active=False)
        await asyncio.wait_for(asyncio.shield(session._response_task), 1)
        assert len(errors) == 1
        assert session._worker_task.done()
        assert ws.closed
        assert not session._request_queue._getters
        assert not [task for task in asyncio.all_tasks() - before if not task.done()]
    finally:
        close_release.set()
        await asyncio.wait_for(session.close(), 1)
