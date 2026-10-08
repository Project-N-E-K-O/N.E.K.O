"""Deterministic ownership and queue boundaries for Qwen pause recovery."""

import asyncio
import base64
import json
import time
from collections import deque

import pytest

from main_logic.asr_client._infra import (
    AsrSessionConfig,
    _AsrWorkerEvent,
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


async def test_real_final_survives_receiver_cancellation_under_response_backpressure():
    state = _state()
    responses = asyncio.Queue(maxsize=1)
    responses.put_nowait(_AsrWorkerEvent("partial", 0, text="occupy queue"))
    ws = _FakeWebSocket()
    receiver = asyncio.create_task(qwen._qwen_receiver(
        ws, responses, AsrSessionConfig(endpointing_mode="provider"), state,
    ))
    try:
        await ws.server_send({
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "current", "transcript": "confirmed sentence",
        })
        await _wait_until(lambda: bool(state.final_deliveries))
        receiver.cancel()
        await asyncio.gather(receiver, return_exceptions=True)
        assert "current" in state.item_keys
        settlement = asyncio.create_task(qwen._qwen_emit_empty_finals_for_pending_items(responses, state))
        await _next_event(responses, "partial")
        final = await _next_event(responses, "final")
        await settlement
        assert final.text == "confirmed sentence"
        assert final.utterance_id == 2
        assert not state.item_keys
        assert responses.empty()
    finally:
        receiver.cancel()
        tasks = [receiver, *state.final_deliveries.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("preceding", [None, "audio", "activity"])
async def test_clear_during_finish_opens_only_one_successor(monkeypatch, preceding):
    finish = asyncio.Event()

    async def on_send(ws, payload):
        kind = json.loads(payload)["type"]
        if kind == "session.update":
            await ws.server_send({"type": "session.updated"})
        elif kind == "session.finish":
            finish.set()

    sockets = [_FakeWebSocket(on_send=on_send) for _ in range(3)]
    connector = _FakeConnector(*sockets)
    monkeypatch.setattr(qwen.websockets, "connect", connector)
    monkeypatch.setattr(qwen, "_QWEN_LOCAL_FINISH_GRACE_SECONDS", 0)
    monkeypatch.setattr(qwen, "_QWEN_FINISH_TIMEOUT_SECONDS", 0.05)
    requests, responses = _AsrRequestQueue(), asyncio.Queue()
    worker = asyncio.create_task(qwen.qwen_asr_worker(
        requests, responses, "key", AsrSessionConfig(endpointing_mode="provider"),
    ))
    try:
        await _next_event(responses, "ready")
        await sockets[0].server_send({"type": "input_audio_buffer.speech_started", "item_id": "old"})
        await _next_event(responses, "utterance_started")
        requests.put_nowait(_AsrWorkerRequest("activity", 0, speech_active=False))
        await finish.wait()
        if preceding:
            requests.put_nowait(_AsrWorkerRequest(preceding, 0, audio=b"aa", speech_active=True))
            await _wait_until(lambda: requests.qsize() == 0)
        requests.put_nowait(_AsrWorkerRequest("clear", 0, buffer_epoch=1, utterance_id=4))
        requests.put_nowait(_AsrWorkerRequest("audio", 0, buffer_epoch=1, utterance_id=4, audio=b"bb"))
        await asyncio.wait_for(requests.join(), 1)
        await _wait_until(lambda: len(connector.calls) >= 2)
        assert len(connector.calls) == 2
        assert sockets[0].closed
        assert requests.waiting_audio_bytes == 0
        appended = [json.loads(p)["audio"] for p in sockets[1].sent
                    if json.loads(p)["type"] == "input_audio_buffer.append"]
        assert appended == [base64.b64encode(b"bb").decode()]
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


async def test_permanent_response_backpressure_terminates_worker(monkeypatch):
    finish = asyncio.Event()

    async def on_send(ws, payload):
        kind = json.loads(payload)["type"]
        if kind == "session.update":
            await ws.server_send({"type": "session.updated"})
        elif kind == "session.finish":
            finish.set()
            await ws.server_send({"type": "session.finished"})

    socket = _FakeWebSocket(on_send=on_send)
    connector = _FakeConnector(socket)
    monkeypatch.setattr(qwen.websockets, "connect", connector)
    monkeypatch.setattr(qwen, "_QWEN_LOCAL_FINISH_GRACE_SECONDS", 0)
    monkeypatch.setattr(qwen, "_QWEN_FINAL_DELIVERY_TIMEOUT_SECONDS", 0.03)
    requests, responses = _AsrRequestQueue(), asyncio.Queue(maxsize=1)
    worker = asyncio.create_task(qwen.qwen_asr_worker(
        requests, responses, "key", AsrSessionConfig(endpointing_mode="provider"),
    ))
    try:
        await _next_event(responses, "ready")
        await socket.server_send({"type": "input_audio_buffer.speech_started", "item_id": "old"})
        await _wait_until(responses.full)
        requests.put_nowait(_AsrWorkerRequest("activity", 0, speech_active=False))
        await finish.wait()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(worker), 0.5)
        assert worker.done()
        assert socket.closed
        assert len(connector.calls) == 1
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


async def test_shutdown_after_clear_during_finish_balances_control_queue():
    state = _state()
    requests, responses = _AsrRequestQueue(), asyncio.Queue()
    clear = _AsrWorkerRequest("clear", 0, buffer_epoch=1)
    shutdown = _AsrWorkerRequest("shutdown", 0, buffer_epoch=1)
    requests.put_nowait(clear)
    requests.put_nowait(shutdown)

    async def on_send(_ws, _payload):
        state.finish_received.set()

    assert await qwen._qwen_finish_and_reconnect(
        _FakeWebSocket(on_send=on_send), requests, responses, state, deque(), {},
    ) == ("shutdown", shutdown)
    await asyncio.wait_for(requests.join(), 1)


@pytest.mark.parametrize("successor", [False, True])
async def test_session_setup_timeout_is_connection_failure_not_backpressure(monkeypatch, successor):
    async def on_send(ws, payload):
        kind = json.loads(payload)["type"]
        if kind == "session.update":
            await ws.server_send({"type": "session.updated"})
        elif kind == "session.finish":
            await ws.server_send({"type": "session.finished"})

    silent = _FakeWebSocket()
    first = _FakeWebSocket(on_send=on_send)
    connector = _FakeConnector(first, silent) if successor else _FakeConnector(silent)
    monkeypatch.setattr(qwen.websockets, "connect", connector)
    monkeypatch.setattr(qwen, "_QWEN_SETUP_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(qwen, "_QWEN_RECONNECT_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(qwen, "_QWEN_LOCAL_FINISH_GRACE_SECONDS", 0)
    requests, responses = _AsrRequestQueue(), asyncio.Queue()
    worker = asyncio.create_task(qwen.qwen_asr_worker(
        requests, responses, "key", AsrSessionConfig(endpointing_mode="provider"),
    ))
    try:
        if successor:
            await _next_event(responses, "ready")
            await first.server_send({"type": "input_audio_buffer.speech_started", "item_id": "old"})
            await _next_event(responses, "utterance_started")
            requests.put_nowait(_AsrWorkerRequest("activity", 0, speech_active=False))
        error = await _next_event(responses, "error")
        assert error.error_code == "ASR_QWEN_CONNECTION_FAILED"
        assert "response delivery" not in error.error_message
        await asyncio.wait_for(worker, 1)
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.parametrize("offset", [-100_000, 100_000])
async def test_recovery_capacity_deadline_ignores_loop_clock_origin(monkeypatch, offset):
    from main_logic.asr_client import _infra

    async def noop(*_args):
        pass

    session = _RealtimeAsrSessionImpl(
        worker_fn=noop, api_key="key", config=AsrSessionConfig(),
        on_input_transcript=noop, on_connection_error=noop,
    )
    session._state = _infra._SessionState.READY
    session._request_queue = _AsrRequestQueue()
    session._request_queue.put_nowait(_AsrWorkerRequest(
        "audio", 0, audio=b"\0" * _infra._ACTIVE_QUEUE_MAX_AUDIO_BYTES,
    ))
    monkeypatch.setattr(_infra, "_REQUEST_BACKPRESSURE_TIMEOUT_SECONDS", 0.01)
    session._request_queue.transport_recovery_deadline = time.monotonic() + 0.06
    loop = asyncio.get_running_loop()
    original_time = loop.time
    monkeypatch.setattr(loop, "time", lambda: original_time() + offset)
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="ASR_STREAM_BACKPRESSURE"):
        await asyncio.wait_for(session._wait_for_audio_queue_capacity(
            _AsrWorkerRequest("audio", 0, audio=b"aa"),
        ), 0.3)
    assert 0.04 <= time.monotonic() - started < 0.3


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
    monkeypatch.setattr(qwen, "_QWEN_LOCAL_FINISH_GRACE_SECONDS", 100)
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
        state.fallback_due.set()
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


@pytest.mark.parametrize("preceding", [None, "audio", "activity"])
async def test_shutdown_during_finish_preserves_accepted_tail(monkeypatch, preceding):
    finish_sent = asyncio.Event()

    async def on_send(ws, payload):
        message = json.loads(payload)
        if message["type"] == "session.update":
            await ws.server_send({"type": "session.updated"})
        elif message["type"] == "session.finish":
            finish_sent.set()
            if ws is second:
                await ws.server_send({
                    "type": "conversation.item.input_audio_transcription.completed",
                    "item_id": "tail", "transcript": "tail sentence",
                })
                await ws.server_send({"type": "session.finished"})
        elif message["type"] == "input_audio_buffer.append" and ws is second:
            await ws.server_send({"type": "input_audio_buffer.speech_started", "item_id": "tail"})

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
        if preceding is not None:
            await requests.put(_AsrWorkerRequest(
                preceding, 0, utterance_id=2, audio=b"\0" * 3200, speech_active=True,
            ))
            await _wait_until(lambda: requests.qsize() == 0)
        await requests.put(_AsrWorkerRequest("shutdown", 0, utterance_id=2))
        await asyncio.sleep(0)
        assert not task.done()
        await first.server_send({
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "old", "transcript": "last sentence",
        })
        assert (await _next_event(responses, "final")).text == "last sentence"
        await first.server_send({"type": "session.finished"})
        if preceding == "audio":
            assert (await _next_event(responses, "final")).text == "tail sentence"
        closed = await _next_event(responses, "closed", timeout=2)
        assert closed.utterance_id == 2
        await asyncio.wait_for(task, 1)
        await asyncio.wait_for(requests.join(), 1)
        assert len(connector.calls) == (2 if preceding == "audio" else 1)
        assert [base64.b64decode(json.loads(p)["audio"]) for p in second.sent
                if json.loads(p)["type"] == "input_audio_buffer.append"] == (
                    [b"\0" * 3200] if preceding == "audio" else []
                )
        assert requests.waiting_audio_bytes == 0
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
    from main_logic.asr_client import connection_cleanup
    # This integration retains the physical close owner instead of skipping a
    # suspended handshake on second cancellation. Scale its bounded budget.
    monkeypatch.setattr(connection_cleanup, "CLOSE_TIMEOUT_SECONDS", 0.1)
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


@pytest.mark.parametrize("finish_reply", ["disconnect", "acknowledge", "error"])
async def test_finish_outcome_survives_slow_close(monkeypatch, finish_reply):
    closing, release = asyncio.Event(), asyncio.Event()

    class SlowClose(_FakeWebSocket):
        async def close(self):
            await super().close()
            closing.set()
            await release.wait()

    async def on_send(ws, payload):
        kind = json.loads(payload)["type"]
        if kind == "session.update":
            await ws.server_send({"type": "session.updated"})
        elif kind == "session.finish":
            if finish_reply == "disconnect":
                await ws.server_end()
            else:
                await ws.server_send({"type": "error" if finish_reply == "error"
                                      else "session.finished"})

    first = SlowClose(on_send=on_send)
    second = _FakeWebSocket(on_send=on_send)
    connector = _FakeConnector(first, second)
    monkeypatch.setattr(qwen.websockets, "connect", connector)
    monkeypatch.setattr(qwen, "_QWEN_LOCAL_FINISH_GRACE_SECONDS", 0)
    requests, responses = _AsrRequestQueue(), asyncio.Queue()
    worker = asyncio.create_task(qwen.qwen_asr_worker(
        requests, responses, "key", AsrSessionConfig(endpointing_mode="provider")
    ))
    try:
        await _next_event(responses, "ready")
        await first.server_send({"type": "input_audio_buffer.speech_started", "item_id": "old"})
        await _next_event(responses, "utterance_started")
        await requests.put(_AsrWorkerRequest("activity", 0, speech_active=False))
        await asyncio.wait_for(closing.wait(), 1)
        # Exceed the old one-second join timeout with an actual close barrier.
        await asyncio.sleep(1.05)
        assert not worker.done()
        release.set()
        if finish_reply == "error":
            assert (await _next_event(responses, "error")).error_code == "ASR_QWEN_PROVIDER_ERROR"
            await asyncio.wait_for(worker, 1)
            assert len(connector.calls) == 1
        else:
            await _wait_until(lambda: len(connector.calls) == 2)
            await requests.put(_AsrWorkerRequest("audio", 0, utterance_id=1, audio=b"\1\2"))
            await asyncio.wait_for(requests.join(), 1)
            assert any(json.loads(p)["type"] == "input_audio_buffer.append" for p in second.sent)
            assert not any(e.kind in {"closed", "error"} for e in responses._queue)
    finally:
        release.set()
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.parametrize("region", ["cn", "intl"])
async def test_reconnect_retries_without_losing_held_audio(monkeypatch, region):
    finish, retry = asyncio.Event(), asyncio.Event()
    calls = []

    async def on_send(ws, payload):
        kind = json.loads(payload)["type"]
        if kind == "session.update":
            await ws.server_send({"type": "session.updated"})
        elif kind == "session.finish":
            finish.set()

    first, second = _FakeWebSocket(on_send=on_send), _FakeWebSocket(on_send=on_send)

    async def connect(url, **_kwargs):
        calls.append(url)
        if len(calls) == 1:
            return first
        retry.set()
        if len(calls) == 2:
            raise OSError("temporary connection failure")
        return second

    monkeypatch.setattr(qwen.websockets, "connect", connect)
    monkeypatch.setattr(qwen, "_QWEN_LOCAL_FINISH_GRACE_SECONDS", 0)
    requests, responses = _AsrRequestQueue(), asyncio.Queue()
    worker = asyncio.create_task(qwen.qwen_asr_worker(
        requests, responses, "key", AsrSessionConfig(endpointing_mode="provider"), region=region
    ))
    audio = b"\1\2" * 320
    try:
        await _next_event(responses, "ready")
        await first.server_send({"type": "input_audio_buffer.speech_started", "item_id": "old"})
        await _next_event(responses, "utterance_started")
        await requests.put(_AsrWorkerRequest("activity", 0, speech_active=False))
        await asyncio.wait_for(finish.wait(), 1)
        await requests.put(_AsrWorkerRequest("audio", 0, utterance_id=1, audio=audio))
        await _wait_until(lambda: requests.held_audio_bytes == len(audio))
        assert requests.waiting_audio_bytes == len(audio)
        await first.server_send({"type": "session.finished"})
        await asyncio.wait_for(retry.wait(), 1)
        assert requests.waiting_audio_bytes == len(audio)
        await _wait_until(lambda: len(calls) == 3)
        await asyncio.wait_for(requests.join(), 1)
        assert requests.waiting_audio_bytes == requests.held_audio_bytes == 0
        assert [base64.b64decode(json.loads(p)["audio"]) for p in second.sent
                if json.loads(p)["type"] == "input_audio_buffer.append"] == [audio]
        assert not any(e.kind in {"error", "closed"} for e in responses._queue)
        assert all(url == (qwen._QWEN_CN_URL if region == "cn" else qwen._QWEN_INTL_URL) for url in calls)
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        assert requests.held_audio_bytes == 0


async def test_audio_capacity_wait_survives_bounded_transport_recovery(monkeypatch):
    from main_logic.asr_client import _infra

    entered, release = asyncio.Event(), asyncio.Event()

    async def worker(requests, responses, _key, _config):
        await responses.put(_infra._AsrWorkerEvent("ready", 0))
        requests.transport_recovery_deadline = time.monotonic() + 1
        entered.set()
        await release.wait()
        requests.transport_recovery_deadline = 0
        while True:
            request = await requests.get()
            requests.task_done()
            if request.kind == "shutdown":
                await responses.put(_infra._AsrWorkerEvent("closed", 0))
                return

    async def callback(*_args):
        pass

    monkeypatch.setattr(_infra, "_REQUEST_BACKPRESSURE_TIMEOUT_SECONDS", 0.02)
    session = _RealtimeAsrSessionImpl(
        worker_fn=worker, api_key="key", config=AsrSessionConfig(),
        on_input_transcript=callback,
        on_connection_error=callback,
    )
    capacity_task = None
    try:
        await session.connect()
        await entered.wait()
        full = _AsrWorkerRequest("audio", 0, utterance_id=1, audio=b"\0" * _infra._ACTIVE_QUEUE_MAX_AUDIO_BYTES)
        session._request_queue.put_nowait(full)
        capacity_task = asyncio.create_task(session._wait_for_audio_queue_capacity(
            _AsrWorkerRequest("audio", 0, utterance_id=1, audio=b"\0\0")
        ))
        await asyncio.sleep(0.06)
        assert not capacity_task.done()
        assert session._request_queue.waiting_audio_bytes == _infra._ACTIVE_QUEUE_MAX_AUDIO_BYTES
        release.set()
        await asyncio.wait_for(capacity_task, 1)
        # The extension is bounded even when the transport never recovers.
        session._request_queue.put_nowait(full)
        session._request_queue.transport_recovery_deadline = time.monotonic() + 0.06
        # Stop the test worker consuming so capacity remains exhausted.
        session._worker_task.cancel()
        await asyncio.gather(session._worker_task, return_exceptions=True)
        with pytest.raises(RuntimeError, match="ASR_STREAM_BACKPRESSURE"):
            await session._wait_for_audio_queue_capacity(
                _AsrWorkerRequest("audio", 0, audio=b"\0\0")
            )
        session._request_queue.transport_recovery_deadline = time.monotonic() + 1
        capacity_task = asyncio.create_task(session._wait_for_audio_queue_capacity(
            _AsrWorkerRequest("audio", 0, audio=b"\0\0")
        ))
        await asyncio.sleep(0.04)
        assert not capacity_task.done()
        session._request_queue.transport_recovery_deadline = 0
        # Retiring recovery restores the original capacity timeout instead of
        # retaining the previous extension in the local deadline variable.
        with pytest.raises(RuntimeError, match="ASR_STREAM_BACKPRESSURE"):
            await asyncio.wait_for(capacity_task, 0.2)
    finally:
        release.set()
        if capacity_task is not None:
            capacity_task.cancel()
        await session.close()


async def test_stale_pause_cannot_finish_next_provider_turn(monkeypatch):
    state = qwen._QwenConnectionState(0, 0, 1, False)
    state.configured.set()
    requests, responses = _AsrRequestQueue(), asyncio.Queue()
    ws = _FakeWebSocket()
    sender = asyncio.create_task(qwen._qwen_sender(
        ws, requests, responses, AsrSessionConfig(endpointing_mode="provider"), state
    ))
    receiver = asyncio.create_task(qwen._qwen_receiver(
        ws, responses, AsrSessionConfig(endpointing_mode="provider"), state
    ))
    try:
        await requests.put(_AsrWorkerRequest("activity", 0, speech_active=True))
        await asyncio.wait_for(requests.join(), 1)
        await ws.server_send({"type": "input_audio_buffer.speech_started", "item_id": "old"})
        await _next_event(responses, "utterance_started")
        await ws.server_send({"type": "conversation.item.input_audio_transcription.completed",
                              "item_id": "old", "transcript": "old"})
        await _next_event(responses, "final")
        await requests.put(_AsrWorkerRequest("activity", 0, speech_active=False))
        await asyncio.wait_for(requests.join(), 1)
        await ws.server_send({"type": "input_audio_buffer.speech_started", "item_id": "new"})
        await _next_event(responses, "utterance_started")
        assert state.fallback_key is None
        assert not state.fallback_due.is_set()
    finally:
        sender.cancel()
        receiver.cancel()
        await asyncio.gather(sender, receiver, return_exceptions=True)


@pytest.mark.parametrize("final_before_hint", [False, True])
async def test_provider_first_onsets_claim_local_cycles_after_first_turn(monkeypatch, final_before_hint):
    monkeypatch.setattr(qwen, "_QWEN_LOCAL_FINISH_GRACE_SECONDS", 100)
    state = qwen._QwenConnectionState(0, 0, 1, False)
    state.configured.set()
    requests, responses = _AsrRequestQueue(), asyncio.Queue()
    ws = _FakeWebSocket()
    config = AsrSessionConfig(endpointing_mode="provider")
    sender = asyncio.create_task(qwen._qwen_sender(ws, requests, responses, config, state))
    receiver = asyncio.create_task(qwen._qwen_receiver(ws, responses, config, state))
    try:
        for cycle in range(1, 4):
            item = f"item-{cycle}"
            await ws.server_send({"type": "input_audio_buffer.speech_started", "item_id": item})
            await _next_event(responses, "utterance_started")
            if final_before_hint:
                await ws.server_send({"type": "input_audio_buffer.speech_stopped",
                                      "item_id": item, "audio_end_ms": state.wire_audio_bytes // 32})
                await ws.server_send({"type": "conversation.item.input_audio_transcription.completed",
                                      "item_id": item, "transcript": item})
                await _next_event(responses, "final")
            await requests.put(_AsrWorkerRequest("activity", 0, speech_active=True))
            await asyncio.wait_for(requests.join(), 1)
            if not final_before_hint:
                assert state.provider_speech_cycles[cycle] == cycle
                await ws.server_send({"type": "conversation.item.input_audio_transcription.completed",
                                      "item_id": item, "transcript": item})
                await _next_event(responses, "final")
            await requests.put(_AsrWorkerRequest("activity", 0, speech_active=False))
            await asyncio.wait_for(requests.join(), 1)
            assert state.last_provider_final_cycle == (-1 if final_before_hint else cycle)
            assert state.fallback_key is None
            assert state.pending_local_pause is None
        assert not any(json.loads(p)["type"] == "session.finish" for p in ws.sent)
    finally:
        sender.cancel()
        receiver.cancel()
        await asyncio.gather(sender, receiver, return_exceptions=True)


async def test_fallback_waiter_is_reused_across_audio_frames():
    class CountWaits(asyncio.Event):
        calls = 0

        async def wait(self):
            self.calls += 1
            return await super().wait()

    state = _state()
    state.fallback_due = CountWaits()
    requests, responses = _AsrRequestQueue(), asyncio.Queue()
    sender = asyncio.create_task(qwen._qwen_sender(
        _FakeWebSocket(), requests, responses,
        AsrSessionConfig(endpointing_mode="provider"), state,
    ))
    try:
        for _ in range(100):
            await requests.put(_AsrWorkerRequest("audio", 0, audio=b"\0\0"))
        await asyncio.wait_for(requests.join(), 1)
        assert state.fallback_due.calls == 1
        assert requests.waiting_audio_bytes == 0
    finally:
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)


@pytest.mark.parametrize("setup", ["no_ack", "disconnect"])
async def test_reconnect_attempt_budget_and_setup_timeout(monkeypatch, setup):
    calls = 0
    responses = asyncio.Queue()

    async def connect(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        # A TCP connection that never acknowledges session.update must be
        # retried within the same budget as an immediate connect failure.
        ws = _FakeWebSocket()
        if setup == "disconnect":
            await ws.server_end()
        return ws

    monkeypatch.setattr(qwen.websockets, "connect", connect)
    monkeypatch.setattr(qwen, "_QWEN_SETUP_TIMEOUT_SECONDS", 0.5 if setup == "disconnect" else 0.01)
    state = qwen._QwenConnectionState(0, 0, 2, False)
    with pytest.raises(asyncio.TimeoutError if setup == "no_ack" else ConnectionError):
        await qwen._qwen_open_connection(
            qwen._QWEN_CN_URL, "key", {}, responses,
            AsrSessionConfig(endpointing_mode="provider"), state,
        )
    assert calls == qwen._QWEN_RECONNECT_MAX_ATTEMPTS
    assert responses.empty()


async def test_response_backpressure_does_not_convert_finish_to_shutdown(monkeypatch):
    finish = asyncio.Event()

    async def on_send(ws, payload):
        kind = json.loads(payload)["type"]
        if kind == "session.update":
            await ws.server_send({"type": "session.updated"})
        elif kind == "session.finish":
            finish.set()
            await ws.server_send({"type": "session.finished"})

    first, second = _FakeWebSocket(on_send=on_send), _FakeWebSocket(on_send=on_send)
    connector = _FakeConnector(first, second)
    monkeypatch.setattr(qwen.websockets, "connect", connector)
    monkeypatch.setattr(qwen, "_QWEN_LOCAL_FINISH_GRACE_SECONDS", 0)
    requests, responses = _AsrRequestQueue(), asyncio.Queue(maxsize=1)
    worker = asyncio.create_task(qwen.qwen_asr_worker(
        requests, responses, "key", AsrSessionConfig(endpointing_mode="provider")
    ))
    try:
        await _next_event(responses, "ready")
        await first.server_send({"type": "input_audio_buffer.speech_started", "item_id": "old"})
        # Leave utterance_started in the response queue, blocking the empty
        # final until the actual consumer makes capacity available.
        await _wait_until(responses.full)
        await requests.put(_AsrWorkerRequest("activity", 0, speech_active=False))
        await asyncio.wait_for(finish.wait(), 1)
        await asyncio.sleep(1.05)
        assert not worker.done()
        assert len(connector.calls) == 1
        await _next_event(responses, "utterance_started")
        assert (await _next_event(responses, "final")).text == ""
        await _wait_until(lambda: len(connector.calls) == 2)
        assert not worker.done()
        assert responses.empty()
    finally:
        async def drain():
            while True:
                await responses.get()
                responses.task_done()

        consumer = asyncio.create_task(drain())
        worker.cancel()
        try:
            await asyncio.wait_for(asyncio.gather(worker, return_exceptions=True), 2)
        finally:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)


@pytest.mark.parametrize("command", ["audio", "shutdown"])
async def test_finish_getter_completion_after_wait_snapshot_is_preserved(monkeypatch, command):
    state = _state()
    requests, responses = _AsrRequestQueue(), asyncio.Queue()
    deferred, holds = deque(), {}
    original_wait = asyncio.wait
    arrived = _AsrWorkerRequest(command, 0, audio=b"\1\2" if command == "audio" else b"")

    async def on_send(_ws, _payload):
        state.finish_received.set()

    async def wait_then_complete_getter(tasks, **kwargs):
        done, pending = await original_wait(tasks, **kwargs)
        assert len(pending) == 1
        requests.put_nowait(arrived)
        await next(iter(pending))
        return done, pending

    monkeypatch.setattr(qwen.asyncio, "wait", wait_then_complete_getter)
    outcome = await qwen._qwen_finish_and_reconnect(
        _FakeWebSocket(on_send=on_send), requests, responses, state, deferred, holds
    )
    if command == "audio":
        assert outcome == ("reconnect", None)
        assert list(deferred) == [arrived]
        assert requests.waiting_audio_bytes == len(arrived.audio)
        holds.pop(id(arrived)).release()
        requests.task_done()
    else:
        assert outcome == ("shutdown", arrived)
        assert not deferred
        assert state.closed_sent.is_set()
        assert (await _next_event(responses, "closed")).generation == arrived.generation
    await requests.join()


async def test_previous_final_during_overlap_keeps_next_pause_recovery(monkeypatch):
    monkeypatch.setattr(qwen, "_QWEN_LOCAL_FINISH_GRACE_SECONDS", 100)
    state = qwen._QwenConnectionState(0, 0, 1, False)
    state.configured.set()
    requests, responses = _AsrRequestQueue(), asyncio.Queue()

    async def on_send(_ws, payload):
        if json.loads(payload)["type"] == "session.finish":
            state.finish_received.set()

    ws = _FakeWebSocket(on_send=on_send)
    config = AsrSessionConfig(endpointing_mode="provider")
    sender = asyncio.create_task(qwen._qwen_sender(ws, requests, responses, config, state))
    receiver = asyncio.create_task(qwen._qwen_receiver(ws, responses, config, state))

    async def activity(active):
        await requests.put(_AsrWorkerRequest("activity", 0, speech_active=active))
        await asyncio.wait_for(requests.join(), 1)

    try:
        await activity(True)
        await ws.server_send({"type": "input_audio_buffer.speech_started", "item_id": "previous"})
        await _next_event(responses, "utterance_started")
        await activity(False)
        await activity(True)
        await ws.server_send({"type": "conversation.item.input_audio_transcription.completed",
                              "item_id": "previous", "transcript": "previous"})
        await _next_event(responses, "final")
        await activity(False)
        timer = state.fallback_timer_task
        assert state.pending_local_pause == (0, 0)
        assert state.fallback_key == (0, 0, 2)
        await ws.server_send({"type": "input_audio_buffer.speech_started", "item_id": "overlap"})
        await _next_event(responses, "utterance_started")
        assert state.fallback_timer_task is timer
        state.fallback_due.set()
        assert await asyncio.wait_for(sender, 1) == ("reconnect", None)
        assert any(json.loads(p)["type"] == "session.finish" for p in ws.sent)
    finally:
        sender.cancel()
        receiver.cancel()
        await asyncio.gather(sender, receiver, return_exceptions=True)


async def test_pause_without_provider_start_finishes_within_grace(monkeypatch):
    monkeypatch.setattr(qwen, "_QWEN_LOCAL_FINISH_GRACE_SECONDS", 0.02)
    state = qwen._QwenConnectionState(0, 0, 1, False)
    state.configured.set()
    requests, responses = _AsrRequestQueue(), asyncio.Queue()
    config = AsrSessionConfig(endpointing_mode="provider")

    async def on_send(ws, payload):
        if json.loads(payload)["type"] == "session.finish":
            # The provider publishes the delayed start and final only when
            # finish forces settlement of its buffered audio.
            await ws.server_send({"type": "input_audio_buffer.speech_started", "item_id": "late"})
            await ws.server_send({"type": "conversation.item.input_audio_transcription.completed",
                                  "item_id": "late", "transcript": "settled"})
            await ws.server_send({"type": "session.finished"})

    ws = _FakeWebSocket(on_send=on_send)
    sender = asyncio.create_task(qwen._qwen_sender(ws, requests, responses, config, state))
    receiver = asyncio.create_task(qwen._qwen_receiver(ws, responses, config, state))
    try:
        await requests.put(_AsrWorkerRequest("activity", 0, speech_active=True))
        await requests.put(_AsrWorkerRequest("activity", 0, speech_active=False))
        assert await asyncio.wait_for(sender, 1) == ("reconnect", None)
        assert (await _next_event(responses, "final")).text == "settled"
        assert state.pending_local_pause is None
    finally:
        sender.cancel()
        receiver.cancel()
        await asyncio.gather(sender, receiver, return_exceptions=True)


@pytest.mark.parametrize("endpoint", ["speech_stopped", "committed"])
async def test_local_pause_resume_pause_keeps_current_provider_endpoint_authority(monkeypatch, endpoint):
    monkeypatch.setattr(qwen, "_QWEN_LOCAL_FINISH_GRACE_SECONDS", 100)
    state = qwen._QwenConnectionState(0, 0, 1, False)
    state.configured.set()
    requests, responses = _AsrRequestQueue(), asyncio.Queue()
    ws = _FakeWebSocket()
    config = AsrSessionConfig(endpointing_mode="provider")
    sender = asyncio.create_task(qwen._qwen_sender(ws, requests, responses, config, state))
    receiver = asyncio.create_task(qwen._qwen_receiver(ws, responses, config, state))

    async def activity(active):
        await requests.put(_AsrWorkerRequest("activity", 0, speech_active=active))
        await asyncio.wait_for(requests.join(), 1)

    try:
        await activity(True)
        await ws.server_send({"type": "input_audio_buffer.speech_started", "item_id": "current"})
        await _next_event(responses, "utterance_started")
        await activity(False)
        assert state.fallback_key == (0, 0, 1)
        await activity(True)
        assert state.fallback_key is None
        await activity(False)
        timer = state.fallback_timer_task
        assert state.local_speech_cycle == 2
        assert state.fallback_key == (0, 0, 1)
        assert state.pending_local_pause == (0, 0)
        await ws.server_send({"type": f"input_audio_buffer.{endpoint}", "item_id": "current"})
        await _wait_until(lambda: state.fallback_key is None)
        await asyncio.gather(timer, return_exceptions=True)
        assert timer.cancelled() or timer.done()
        await requests.put(_AsrWorkerRequest("audio", 0, audio=b"\0\0"))
        await asyncio.wait_for(requests.join(), 1)
        assert not any(json.loads(p)["type"] == "session.finish" for p in ws.sent)
        assert not sender.done()
    finally:
        sender.cancel()
        receiver.cancel()
        await asyncio.gather(sender, receiver, return_exceptions=True)


@pytest.mark.parametrize("endpoint", ["speech_stopped", "committed", "final"])
@pytest.mark.parametrize("resume_before_start", [False, True])
@pytest.mark.parametrize("next_start_ms", [220, 400, None])
async def test_overlap_pause_survives_old_endpoint_until_new_provider_start(
    monkeypatch, endpoint, resume_before_start, next_start_ms
):
    monkeypatch.setattr(qwen, "_QWEN_LOCAL_FINISH_GRACE_SECONDS", 100)
    state = qwen._QwenConnectionState(0, 0, 1, False)
    state.configured.set()
    requests, responses = _AsrRequestQueue(), asyncio.Queue()

    async def on_send(_ws, payload):
        if json.loads(payload)["type"] == "session.finish":
            state.finish_received.set()

    ws = _FakeWebSocket(on_send=on_send)
    config = AsrSessionConfig(endpointing_mode="provider")
    sender = asyncio.create_task(qwen._qwen_sender(ws, requests, responses, config, state))
    receiver = asyncio.create_task(qwen._qwen_receiver(ws, responses, config, state))

    async def activity(active):
        await requests.put(_AsrWorkerRequest("activity", 0, speech_active=active))
        await asyncio.wait_for(requests.join(), 1)

    try:
        await activity(True)
        await ws.server_send({"type": "input_audio_buffer.speech_started", "item_id": "old"})
        await _next_event(responses, "utterance_started")
        await requests.put(_AsrWorkerRequest("audio", 0, audio=b"\0" * 6400))
        await asyncio.wait_for(requests.join(), 1)
        await activity(False)
        await activity(True)
        await requests.put(_AsrWorkerRequest("audio", 0, audio=b"\0" * 3200))
        await asyncio.wait_for(requests.join(), 1)
        await activity(False)
        assert state.fallback_key == (0, 0, 1)
        assert state.pending_local_pause == (0, 0)
        if endpoint == "final":
            await ws.server_send({"type": "conversation.item.input_audio_transcription.completed",
                                  "item_id": "old", "transcript": "old"})
            await _next_event(responses, "final")
        else:
            await ws.server_send({"type": f"input_audio_buffer.{endpoint}", "item_id": "old"})
        await _wait_until(lambda: state.fallback_key is None)
        # Retaining an observational pause alone must not force a finish
        # after a normal provider endpoint.
        assert state.fallback_timer_task is None
        assert not state.fallback_due.is_set()
        if resume_before_start:
            await activity(True)
            assert state.pending_local_pause is None
        else:
            assert state.pending_local_pause == (0, 0)
        await requests.put(_AsrWorkerRequest("audio", 0, audio=b"\0" * 6400))
        await asyncio.wait_for(requests.join(), 1)
        start = {"type": "input_audio_buffer.speech_started", "item_id": "new"}
        if next_start_ms is not None:
            start["audio_start_ms"] = next_start_ms
        await ws.server_send(start)
        await _next_event(responses, "utterance_started")
        assert state.pending_local_pause is None
        if resume_before_start or next_start_ms != 220:
            assert state.fallback_key is None
            assert not any(json.loads(p)["type"] == "session.finish" for p in ws.sent)
        else:
            assert state.fallback_key == (0, 0, 2)
            state.fallback_due.set()
            assert await asyncio.wait_for(sender, 1) == ("reconnect", None)
    finally:
        sender.cancel()
        receiver.cancel()
        await asyncio.gather(sender, receiver, return_exceptions=True)

async def test_successor_setup_uses_remaining_total_recovery_budget(monkeypatch):
    entered = asyncio.Event()
    finish_setup = asyncio.Event()

    async def connect(*_args, **_kwargs):
        entered.set()
        await finish_setup.wait()
        return _FakeWebSocket(initial=[{"type": "session.updated"}])

    monkeypatch.setattr(qwen.websockets, "connect", connect)
    state = qwen._QwenConnectionState(0, 0, 2, False)
    task = asyncio.create_task(qwen._qwen_open_connection(
        qwen._QWEN_CN_URL, "key", {}, asyncio.Queue(),
        AsrSessionConfig(endpointing_mode="provider"), state,
        recovery_deadline=time.monotonic() + 4,
    ))
    try:
        await entered.wait()
        await asyncio.sleep(2.05)
        assert not task.done()
        finish_setup.set()
        ws, receiver = await asyncio.wait_for(task, 1)
        receiver.cancel()
        await asyncio.gather(receiver, return_exceptions=True)
        await ws.close()
    finally:
        finish_setup.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

async def test_unconfirmed_provider_noise_does_not_retire_fresh_local_speech(monkeypatch):
    monkeypatch.setattr(qwen, "_QWEN_LOCAL_FINISH_GRACE_SECONDS", 100)
    state = qwen._QwenConnectionState(0, 0, 1, False)
    state.configured.set()
    requests, responses = _AsrRequestQueue(), asyncio.Queue()
    ws = _FakeWebSocket()
    config = AsrSessionConfig(endpointing_mode="provider")
    sender = asyncio.create_task(qwen._qwen_sender(ws, requests, responses, config, state))
    receiver = asyncio.create_task(qwen._qwen_receiver(ws, responses, config, state))
    try:
        await requests.put(_AsrWorkerRequest("audio", 0, audio=b"\0" * 3200))
        await requests.join()
        await ws.server_send({"type": "input_audio_buffer.speech_started", "item_id": "noise"})
        await _next_event(responses, "utterance_started")
        await ws.server_send({"type": "conversation.item.input_audio_transcription.completed",
                              "item_id": "noise", "transcript": ""})
        await _next_event(responses, "final")
        assert state.last_provider_final_cycle == -1
        for request in (
            _AsrWorkerRequest("activity", 0, speech_active=True),
            _AsrWorkerRequest("audio", 0, audio=b"\1" * 3200),
            _AsrWorkerRequest("activity", 0, speech_active=False),
        ):
            await requests.put(request)
        await requests.join()
        assert state.local_speech_cycle == 1
        assert state.fallback_key == (0, 0, 2)
        assert state.pending_local_pause == (0, 0)
    finally:
        sender.cancel()
        receiver.cancel()
        await asyncio.gather(sender, receiver, return_exceptions=True)

@pytest.mark.parametrize("audio_end_ms", [100, None, True, 1_000_000])
@pytest.mark.parametrize("start_hint", [False, True])
async def test_old_unclaimed_final_cannot_cover_pcm_sent_before_new_pause(monkeypatch, audio_end_ms, start_hint):
    monkeypatch.setattr(qwen, "_QWEN_LOCAL_FINISH_GRACE_SECONDS", 100)
    pause_waiting, release_pause = asyncio.Event(), asyncio.Event()

    class PauseBarrierQueue(_AsrRequestQueue):
        async def get(self):
            request = await super().get()
            if request.kind == "activity" and request.speech_active is False:
                pause_waiting.set()
                await release_pause.wait()
            return request

    state = qwen._QwenConnectionState(0, 0, 1, False)
    state.configured.set()
    requests, responses = PauseBarrierQueue(), asyncio.Queue()
    ws = _FakeWebSocket()
    config = AsrSessionConfig(endpointing_mode="provider")
    sender = asyncio.create_task(qwen._qwen_sender(ws, requests, responses, config, state))
    receiver = asyncio.create_task(qwen._qwen_receiver(ws, responses, config, state))
    try:
        await requests.put(_AsrWorkerRequest("audio", 0, audio=b"\0" * 3200))
        await requests.join()
        await ws.server_send({"type": "input_audio_buffer.speech_started", "item_id": "old"})
        await _next_event(responses, "utterance_started")
        await ws.server_send({"type": "input_audio_buffer.speech_stopped", "item_id": "old",
                              "audio_end_ms": audio_end_ms})
        await _wait_until(lambda: 1 in state.provider_endpoint_utterance_ids)
        if start_hint:
            await requests.put(_AsrWorkerRequest("activity", 0, speech_active=True))
        await requests.put(_AsrWorkerRequest("audio", 0, audio=b"\1" * 3200))
        await requests.join()
        await requests.put(_AsrWorkerRequest("activity", 0, speech_active=False))
        await pause_waiting.wait()
        # The previous endpoint is old; its final arrives only after all new
        # speech PCM was sent, while the new pause is waiting in the real FIFO.
        await ws.server_send({"type": "conversation.item.input_audio_transcription.completed",
                              "item_id": "old", "transcript": "old"})
        await _next_event(responses, "final")
        release_pause.set()
        await requests.join()
        assert state.pending_local_pause == (0, 0)
        assert state.fallback_key == (0, 0, 2)
    finally:
        release_pause.set()
        sender.cancel()
        receiver.cancel()
        await asyncio.gather(sender, receiver, return_exceptions=True)
async def test_stalled_watch_failure_retires_connection(monkeypatch):
    monkeypatch.setattr(qwen, "_QWEN_STALLED_ITEM_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(qwen, "_QWEN_FINAL_DELIVERY_TIMEOUT_SECONDS", 0.02)

    async def on_send(ws, payload):
        if json.loads(payload)["type"] == "session.update":
            await ws.server_send({"type": "session.updated"})

    ws = _FakeWebSocket(on_send=on_send)
    monkeypatch.setattr(qwen.websockets, "connect", _FakeConnector(ws))
    requests, responses = _AsrRequestQueue(), asyncio.Queue(maxsize=1)
    worker = asyncio.create_task(qwen.qwen_asr_worker(
        requests, responses, "key", AsrSessionConfig(endpointing_mode="provider"),
    ))
    try:
        await _next_event(responses, "ready")
        await ws.server_send({"type": "input_audio_buffer.speech_started", "item_id": "stalled"})
        await _wait_until(responses.full)
        await ws.server_send({"type": "input_audio_buffer.speech_stopped", "item_id": "stalled"})
        await _wait_until(lambda: ws.closed)
        await asyncio.gather(worker, return_exceptions=True)
        assert worker.done()
        assert not [t for t in asyncio.all_tasks()
                    if t.get_name() in {"qwen-asr-stalled-watch", "qwen-asr-final-delivery"}]
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


async def test_configured_successor_retains_recovery_until_pcm_drains(monkeypatch):
    from main_logic.asr_client import _infra
    finish, writing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def on_send(ws, payload):
        kind = json.loads(payload)["type"]
        if kind == "session.update":
            await ws.server_send({"type": "session.updated"})
        elif kind == "session.finish":
            finish.set()
            if ws is second:
                await ws.server_send({"type": "session.finished"})
        elif kind == "input_audio_buffer.append" and ws is second:
            writing.set()
            await release.wait()

    first, second = _FakeWebSocket(on_send=on_send), _FakeWebSocket(on_send=on_send)
    monkeypatch.setattr(qwen.websockets, "connect", _FakeConnector(first, second))
    monkeypatch.setattr(qwen, "_QWEN_LOCAL_FINISH_GRACE_SECONDS", 0)
    monkeypatch.setattr(_infra, "_REQUEST_BACKPRESSURE_TIMEOUT_SECONDS", 0.01)

    async def callback(*_args):
        pass

    session = _RealtimeAsrSessionImpl(
        worker_fn=qwen.qwen_asr_worker, api_key="key",
        config=AsrSessionConfig(endpointing_mode="provider"),
        on_input_transcript=callback, on_connection_error=callback,
    )
    capacity = None
    try:
        await session.connect()
        requests = session._request_queue
        await first.server_send({"type": "input_audio_buffer.speech_started", "item_id": "old"})
        requests.put_nowait(_AsrWorkerRequest("activity", 0, speech_active=False))
        await asyncio.wait_for(finish.wait(), 1)
        requests.put_nowait(_AsrWorkerRequest("audio", 0, audio=b"\0" * _infra._ACTIVE_QUEUE_MAX_AUDIO_BYTES))
        await first.server_send({"type": "session.finished"})
        await asyncio.wait_for(writing.wait(), 1)
        assert requests.transport_recovery_deadline > time.monotonic()
        capacity = asyncio.create_task(session._wait_for_audio_queue_capacity(
            _AsrWorkerRequest("audio", 0, audio=b"aa"),
        ))
        await asyncio.sleep(0.04)
        assert not capacity.done()
        release.set()
        await asyncio.wait_for(capacity, 1)
        await requests.join()
        assert requests.transport_recovery_deadline == 0
    finally:
        release.set()
        if capacity is not None:
            capacity.cancel()
        await session.close()

async def test_clear_keeps_new_epoch_tail_before_shutdown():
    state = _state()
    requests, responses = _AsrRequestQueue(), asyncio.Queue()
    clear = _AsrWorkerRequest("clear", 0, buffer_epoch=1)
    audio = _AsrWorkerRequest("audio", 0, buffer_epoch=1, audio=b"new")
    shutdown = _AsrWorkerRequest("shutdown", 0, buffer_epoch=1)
    deferred = deque([_AsrWorkerRequest("audio", 0, audio=b"old")])
    # The deferred old request is dequeued but still unfinished.
    old = deferred[0]
    requests.put_nowait(old)
    assert requests.get_nowait() is old
    for request in (clear, audio, shutdown):
        requests.put_nowait(request)

    async def on_send(_ws, _payload):
        state.finish_received.set()

    assert await qwen._qwen_finish_and_reconnect(
        _FakeWebSocket(on_send=on_send), requests, responses, state, deferred, {},
    ) == ("clear", clear)
    assert requests.get_nowait() is audio
    requests.task_done()
    assert requests.get_nowait() is shutdown
    requests.task_done()
    await asyncio.wait_for(requests.join(), 1)

@pytest.mark.parametrize("preceding", [None, "audio", "activity"])
async def test_clear_without_tail_uses_fresh_setup_budget(monkeypatch, preceding):
    finish = asyncio.Event()

    async def on_send(ws, payload):
        if json.loads(payload)["type"] == "session.update":
            await ws.server_send({"type": "session.updated"})
        elif json.loads(payload)["type"] == "session.finish":
            finish.set()

    first, second = _FakeWebSocket(on_send=on_send), _FakeWebSocket(on_send=on_send)
    connector = _FakeConnector(first, second)
    monkeypatch.setattr(qwen.websockets, "connect", connector)
    monkeypatch.setattr(qwen, "_QWEN_LOCAL_FINISH_GRACE_SECONDS", 0)
    requests, responses = _AsrRequestQueue(), asyncio.Queue()
    original_close = qwen._qwen_close_transport
    original_open = qwen._qwen_open_connection
    budgets = []

    async def close(ws, state):
        await original_close(ws, state)
        if ws is first:
            # Model an exhausted old recovery budget before the new epoch.
            requests.transport_recovery_deadline = time.monotonic() - 1

    async def open_connection(*args, **kwargs):
        budgets.append(kwargs["recovery_deadline"])
        return await original_open(*args, **kwargs)

    monkeypatch.setattr(qwen, "_qwen_close_transport", close)
    monkeypatch.setattr(qwen, "_qwen_open_connection", open_connection)
    worker = asyncio.create_task(qwen.qwen_asr_worker(
        requests, responses, "key", AsrSessionConfig(endpointing_mode="provider"),
    ))
    try:
        await _next_event(responses, "ready")
        await first.server_send({"type": "input_audio_buffer.speech_started", "item_id": "old"})
        await _next_event(responses, "utterance_started")
        requests.put_nowait(_AsrWorkerRequest("activity", 0, speech_active=False))
        await asyncio.wait_for(finish.wait(), 1)
        if preceding is not None:
            requests.put_nowait(_AsrWorkerRequest(preceding, 0, audio=b"old", speech_active=True))
            await _wait_until(lambda: requests.qsize() == 0)
        requests.put_nowait(_AsrWorkerRequest("clear", 0, buffer_epoch=1, utterance_id=4))
        await first.server_send({"type": "session.finished"})
        await asyncio.wait_for(requests.join(), 1)
        await _wait_until(lambda: len(second.sent) == 1)
        assert budgets == [0, 0]
        assert not worker.done()
        assert requests.waiting_audio_items == 0
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
