"""Explicit finishing must preserve final delivery without becoming a new endpoint."""

import asyncio
import base64
import json
from unittest.mock import AsyncMock

import pytest

from main_logic.asr_client._infra import (
    AsrSessionConfig,
    _AsrWorkerEvent,
    _AsrWorkerRequest,
    _RealtimeAsrSessionImpl,
)
from main_logic.asr_client.provider_policy import resolve_provider_policy
from main_logic.asr_client.transcript import TranscriptDispatcher, TranscriptEnvelope
from main_logic.asr_client.workers import qwen
from main_logic.voice_turn.contracts import VoiceIngressToken, VoiceTurnToken


pytestmark = pytest.mark.asyncio


class FinishWorker:
    def __init__(self, *, finish=True, final=True):
        self.requests = []
        self.finish = finish
        self.final = final
        self.finished_requested = asyncio.Event()

    async def __call__(self, requests, responses, api_key, config):
        await responses.put(_AsrWorkerEvent(kind="ready", generation=0))
        while True:
            request = await requests.get()
            self.requests.append(request)
            try:
                if request.kind == "finish":
                    self.finished_requested.set()
                    if not self.finish:
                        await asyncio.Event().wait()
                    identity = dict(
                        generation=request.generation,
                        buffer_epoch=request.buffer_epoch,
                        utterance_id=1,
                    )
                    if self.final:
                        await responses.put(_AsrWorkerEvent(kind="utterance_started", **identity))
                        await responses.put(_AsrWorkerEvent(kind="final", text="hello", **identity))
                    await responses.put(_AsrWorkerEvent(kind="finished", **identity))
                    # Immediate transport closure cannot invalidate queued final callbacks.
                    await responses.put(_AsrWorkerEvent(kind="closed", **identity))
                    return
                if request.kind == "shutdown":
                    return
            finally:
                requests.task_done()


async def make_session(worker, callback=None, *, provider="qwen"):
    callback = callback or AsyncMock()
    errors = AsyncMock()
    session = _RealtimeAsrSessionImpl(
        worker_fn=worker,
        api_key="",
        config=AsrSessionConfig(endpointing_mode="provider"),
        on_input_transcript=callback,
        on_connection_error=errors,
        provider_policy=resolve_provider_policy(provider, "provider"),
    )
    await session.connect()
    return session, callback, errors


async def test_finish_serializes_audio_and_waits_for_final_callback():
    worker = FinishWorker()
    entered, release = asyncio.Event(), asyncio.Event()
    delivered = []

    async def callback(text):
        entered.set()
        await release.wait()
        delivered.append(text)

    session, _, errors = await make_session(worker, callback)
    await session.stream_audio(b"\x01\x00" * 512)
    task = asyncio.create_task(session.finish_and_drain(deadline=asyncio.get_running_loop().time() + 2))
    await asyncio.wait_for(entered.wait(), 1)
    assert not task.done()
    with pytest.raises(RuntimeError, match="ASR_SESSION_NOT_READY"):
        await session.stream_audio(b"\x00\x00" * 512)
    release.set()
    await task
    assert delivered == ["hello"]
    assert [r.kind for r in worker.requests] == ["audio", "finish"]
    assert not session.is_ready
    errors.assert_not_called()


@pytest.mark.parametrize("blocked_callback", [False, True])
async def test_finish_deadline_retires_worker_and_cancels_unsettled_callback(blocked_callback):
    worker = FinishWorker(finish=blocked_callback)
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def callback(text):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    session, _, _ = await make_session(worker, callback)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            session.finish_and_drain(deadline=asyncio.get_running_loop().time() + 0.05),
            0.5,
        )
    assert session._worker_task.done()
    assert session._callback_task.done()
    assert not session.is_ready
    if blocked_callback:
        assert entered.is_set() and cancelled.is_set()


async def test_finish_cancel_does_not_finish_callback_or_reopen_session():
    worker = FinishWorker()
    entered = asyncio.Event()
    delivered = []

    async def callback(text):
        entered.set()
        await asyncio.Event().wait()
        delivered.append(text)

    session, _, _ = await make_session(worker, callback)
    task = asyncio.create_task(session.finish_and_drain(deadline=asyncio.get_running_loop().time() + 2))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 0.5)
    assert delivered == []
    assert not session.is_ready


async def test_unsupported_finish_keeps_normal_session_usable():
    worker = FinishWorker()
    session, _, _ = await make_session(worker, provider="openai")
    try:
        with pytest.raises(RuntimeError, match="ASR_FINISH_NOT_SUPPORTED"):
            await session.finish_and_drain(deadline=asyncio.get_running_loop().time() + 1)
        assert session.is_ready
        assert worker.requests == []
    finally:
        await session.close()


async def test_normal_close_does_not_request_result_preserving_finish():
    worker = FinishWorker()
    session, callback, _ = await make_session(worker)
    await session.stream_audio(b"\x00\x00" * 512)
    await session.close()
    assert "finish" not in [r.kind for r in worker.requests]
    callback.assert_not_called()


@pytest.mark.parametrize("mode", ["manual", "provider"])
@pytest.mark.parametrize("sample_rate", [16000, 48000])
async def test_qwen_actual_worker_finish_preserves_result_and_resampler_tail(monkeypatch, mode, sample_rate):
    class Socket:
        def __init__(self):
            self.incoming = asyncio.Queue()
            self.messages = []
            self.audio = b""
            self.closed = False

        async def send(self, raw):
            message = json.loads(raw)
            self.messages.append(message["type"])
            if message["type"] == "session.update":
                await self.incoming.put({"type": "session.updated"})
            elif message["type"] == "input_audio_buffer.append":
                if not self.audio and mode == "provider":
                    await self.incoming.put({"type": "input_audio_buffer.speech_started", "item_id": "one"})
                self.audio += base64.b64decode(message["audio"])
            elif message["type"] == "input_audio_buffer.commit":
                await self.incoming.put({"type": "conversation.item.created", "item": {"id": "one"}})
            elif message["type"] == "session.finish":
                await self.incoming.put({
                    "type": "conversation.item.input_audio_transcription.completed",
                    "item_id": "one", "transcript": "stop",
                })
                await self.incoming.put({"type": "session.finished"})

        def __aiter__(self):
            return self

        async def __anext__(self):
            event = await self.incoming.get()
            if event is None:
                raise StopAsyncIteration
            return json.dumps(event)

        async def close(self):
            self.closed = True
            await self.incoming.put(None)

    socket = Socket()
    monkeypatch.setattr(qwen.websockets, "connect", AsyncMock(return_value=socket))
    callback, errors = AsyncMock(), AsyncMock()
    session = _RealtimeAsrSessionImpl(
        worker_fn=qwen.qwen_asr_worker, api_key="test",
        config=AsrSessionConfig(endpointing_mode=mode, input_sample_rate_hz=sample_rate),
        on_input_transcript=callback, on_connection_error=errors,
        provider_policy=resolve_provider_policy("qwen", mode),
    )
    await session.connect()
    await session.stream_audio(b"\x01\x00" * (sample_rate // 10))
    await session.finish_and_drain(deadline=asyncio.get_running_loop().time() + 1)
    assert len(socket.audio) == 3200
    assert socket.messages[-1] == "session.finish"
    callback.assert_awaited_once_with("stop")
    errors.assert_not_called()


async def test_explicit_close_wakes_finish_without_waiting_for_deadline():
    worker = FinishWorker(finish=False)
    session, callback, _ = await make_session(worker)
    finish = asyncio.create_task(session.finish_and_drain(deadline=asyncio.get_running_loop().time() + 60))
    await worker.finished_requested.wait()
    await asyncio.wait_for(session.close(), 0.5)
    with pytest.raises(RuntimeError, match="cancelled by close"):
        await asyncio.wait_for(finish, 0.5)
    callback.assert_not_called()


async def test_finish_reports_callback_failure_instead_of_success():
    session, _, _ = await make_session(FinishWorker(), AsyncMock(side_effect=ValueError("test")))
    with pytest.raises(RuntimeError, match="ASR_FINISH_CALLBACK_FAILED"):
        await session.finish_and_drain(deadline=asyncio.get_running_loop().time() + 1)


async def test_cancelled_final_callback_cannot_be_reported_as_settled():
    session, _, _ = await make_session(FinishWorker(), AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(RuntimeError, match="ASR_FINISH_CALLBACK_FAILED"):
        await session.finish_and_drain(deadline=asyncio.get_running_loop().time() + 1)


async def test_accepted_final_settles_before_disconnect_recovery_notification():
    entered, release, errored = asyncio.Event(), asyncio.Event(), asyncio.Event()
    actions = []

    async def worker(requests, responses, api_key, config):
        await responses.put(_AsrWorkerEvent(kind="ready", generation=0))
        request = await requests.get()
        try:
            for event in (
                _AsrWorkerEvent(kind="utterance_started", generation=0, utterance_id=1),
                _AsrWorkerEvent(kind="final", generation=0, utterance_id=1, text="accepted"),
                _AsrWorkerEvent(kind="error", generation=0, error_code="ASR_QWEN_READ_DISCONNECTED"),
            ):
                await responses.put(event)
            await asyncio.Event().wait()
        finally:
            requests.task_done()

    async def transcript(text):
        entered.set()
        await release.wait()
        actions.append(text)

    async def error(message):
        actions.append("error")
        errored.set()

    session, _, _ = await make_session(worker, transcript)
    session._on_connection_error = error
    await session.stream_audio(b"\x00\x00" * 512)
    await entered.wait()
    assert not errored.is_set()
    release.set()
    await asyncio.wait_for(errored.wait(), 0.5)
    assert actions == ["accepted", "error"]
    assert session.last_failure_code == "ASR_QWEN_READ_DISCONNECTED"
    await session.close()


async def test_qwen_finish_timeout_is_not_a_successful_finished_event(monkeypatch):
    monkeypatch.setattr(qwen, "_QWEN_FINISH_TIMEOUT_SECONDS", 0.01)
    requests, responses = asyncio.Queue(), asyncio.Queue()
    state = qwen._QwenConnectionState(0, 0, 1, True)
    state.configured.set()
    socket = type("Socket", (), {"send": AsyncMock(), "close": AsyncMock()})()
    await requests.put(_AsrWorkerRequest(kind="finish", generation=0))
    await qwen._qwen_sender(socket, requests, responses, AsrSessionConfig(), state)
    events = []
    while not responses.empty():
        events.append(responses.get_nowait())
    assert events[0].kind == "error"
    assert events[0].error_code == "ASR_FINISH_TIMEOUT"
    assert all(event.kind != "finished" for event in events)


async def test_qwen_read_disconnect_has_a_distinct_recovery_code():
    class Socket:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    responses = asyncio.Queue()
    state = qwen._QwenConnectionState(0, 0, 1, True)
    await qwen._qwen_receiver(Socket(), responses, AsrSessionConfig(), state)
    event = responses.get_nowait()
    assert event.kind == "error"
    assert event.error_code == "ASR_QWEN_READ_DISCONNECTED"


async def test_finish_handoff_survives_provider_close_in_real_transcript_dispatcher():
    entered, release = asyncio.Event(), asyncio.Event()
    delivered = []

    async def dispatch(envelope):
        entered.set()
        await release.wait()
        delivered.append(envelope.text)

    dispatcher = TranscriptDispatcher(dispatch)
    token = VoiceTurnToken(
        ingress=VoiceIngressToken(1, "connection", 1, 1, 1), turn_id=1,
    )

    async def accept_final(text):
        envelope = TranscriptEnvelope(token, "qwen", text)
        assert dispatcher.try_reserve(envelope.final_key)
        dispatcher.submit(envelope)

    session, _, _ = await make_session(FinishWorker(), accept_final)
    try:
        await session.finish_and_drain(deadline=asyncio.get_running_loop().time() + 1)
        await entered.wait()
        assert session._state.value == "closed"
        assert delivered == [] and dispatcher.has_pending_delivery
        release.set()
        await asyncio.wait_for(dispatcher.wait_idle(), 0.5)
        assert delivered == ["hello"]
    finally:
        dispatcher.invalidate_all()


async def test_finish_resource_retirement_cannot_extend_caller_deadline():
    release_worker = asyncio.Event()

    async def worker(requests, responses, api_key, config):
        await responses.put(_AsrWorkerEvent(kind="ready", generation=0))
        request = await requests.get()
        requests.task_done()
        assert request.kind == "finish"
        await responses.put(_AsrWorkerEvent(kind="finished", generation=0))
        await release_worker.wait()

    session, callback, _ = await make_session(worker)
    try:
        with pytest.raises(TimeoutError, match="ASR_FINISH_RETIRE_TIMEOUT"):
            await asyncio.wait_for(
                session.finish_and_drain(deadline=asyncio.get_running_loop().time() + 0.03),
                0.5,
            )
        assert session._state.value == "closing"
        assert session._generation == 1
        callback.assert_not_called()
    finally:
        release_worker.set()
        await session.close()
