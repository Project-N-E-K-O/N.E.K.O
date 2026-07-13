from __future__ import annotations

import asyncio
import gc
import weakref
from unittest.mock import AsyncMock

import pytest

import main_logic.asr_client as asr_client
import main_logic.asr_client._infra as asr_infra
from main_logic.asr_client import AsrSessionConfig, create_asr_session
from main_logic.asr_client._infra import (
    _AsrWorkerEvent,
    _AsrWorkerRequest,
    _RealtimeAsrSessionImpl,
)
from main_logic.asr_client.workers.dummy import dummy_asr_worker


async def _scripted_worker(request_queue, response_queue, api_key, config):
    del config
    await response_queue.put(_AsrWorkerEvent(kind="ready", generation=0))
    while True:
        request = await request_queue.get()
        if request.kind == "commit":
            if api_key == "events":
                common = {
                    "generation": request.generation,
                    "buffer_epoch": request.buffer_epoch,
                    "utterance_id": request.utterance_id,
                }
                await response_queue.put(
                    _AsrWorkerEvent(kind="partial", text="draft", **common)
                )
                await response_queue.put(
                    _AsrWorkerEvent(kind="final", text="   ", **common)
                )
                await response_queue.put(
                    _AsrWorkerEvent(kind="final", text=" first ", **common)
                )
                await response_queue.put(
                    _AsrWorkerEvent(kind="final", text="conflict", **common)
                )
            elif api_key == "error":
                await response_queue.put(
                    _AsrWorkerEvent(
                        kind="error",
                        generation=request.generation,
                        buffer_epoch=request.buffer_epoch,
                        utterance_id=request.utterance_id,
                        error_code="ASR_WORKER_FAILED",
                        error_message="provider rejected Authorization: Bearer sk-secret",
                    )
                )
            elif api_key == "provider":
                await response_queue.put(
                    _AsrWorkerEvent(
                        kind="final",
                        generation=request.generation,
                        buffer_epoch=request.buffer_epoch,
                        utterance_id=request.utterance_id,
                        text="unexpected commit",
                    )
                )
        elif request.kind == "shutdown":
            await response_queue.put(
                _AsrWorkerEvent(kind="closed", generation=request.generation)
            )
            return


async def _delayed_error_worker(request_queue, response_queue, api_key, config):
    del api_key, config
    pending = set()
    await response_queue.put(_AsrWorkerEvent(kind="ready", generation=0))
    try:
        while True:
            request = await request_queue.get()
            if request.kind == "commit":
                async def emit_error(committed_request=request):
                    await asyncio.sleep(0.02)
                    await response_queue.put(
                        _AsrWorkerEvent(
                            kind="error",
                            generation=committed_request.generation,
                            buffer_epoch=committed_request.buffer_epoch,
                            utterance_id=committed_request.utterance_id,
                            error_code="ASR_WORKER_FAILED",
                            error_message="stale utterance failure",
                        )
                    )

                task = asyncio.create_task(emit_error())
                pending.add(task)
                task.add_done_callback(pending.discard)
            elif request.kind == "shutdown":
                await response_queue.put(
                    _AsrWorkerEvent(kind="closed", generation=request.generation)
                )
                return
    finally:
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


async def _non_consuming_worker(request_queue, response_queue, api_key, config):
    del request_queue, api_key, config
    await response_queue.put(_AsrWorkerEvent(kind="ready", generation=0))
    await asyncio.Event().wait()


async def _wrong_generation_ready_worker(
    request_queue, response_queue, api_key, config
):
    del request_queue, api_key, config
    await response_queue.put(_AsrWorkerEvent(kind="ready", generation=99))
    await asyncio.Event().wait()


def test_public_exports_are_frozen():
    assert asr_client.__all__ == [
        "AsrSessionConfig",
        "RealtimeAsrSession",
        "create_asr_session",
    ]
    assert not hasattr(asr_client, "get_asr_worker")
    assert not hasattr(asr_client, "AsrWorkerFn")
    assert not hasattr(asr_client, "ASR_PROVIDER_REGISTRY")
    assert not hasattr(asr_client, "CORE_ASR_ROUTES")
    assert not hasattr(asr_client, "dummy_asr_worker")


def test_routes_fail_synchronously_without_dummy(monkeypatch):
    monkeypatch.delenv("ASR_PROVIDER", raising=False)
    callback = AsyncMock()

    with pytest.raises(RuntimeError, match="ASR_UNKNOWN_CORE"):
        create_asr_session(
            "unknown",
            on_input_transcript=callback,
            on_connection_error=callback,
        )
    with pytest.raises(RuntimeError, match="ASR_BACKEND_NOT_IMPLEMENTED"):
        create_asr_session(
            "qwen",
            on_input_transcript=callback,
            on_connection_error=callback,
        )
    with pytest.raises(RuntimeError, match="ASR_BACKEND_BLOCKED"):
        create_asr_session(
            "free",
            on_input_transcript=callback,
            on_connection_error=callback,
        )


def test_dummy_requires_explicit_override_and_manual_mode(monkeypatch):
    callback = AsyncMock()
    monkeypatch.setenv("ASR_PROVIDER", "soniox")
    with pytest.raises(RuntimeError, match="ASR_INVALID_CONFIG"):
        create_asr_session(
            "qwen",
            on_input_transcript=callback,
            on_connection_error=callback,
        )
    with pytest.raises(TypeError, match="ASR_INVALID_CONFIG"):
        create_asr_session(
            "qwen",
            config={"endpointing_mode": "manual"},
            on_input_transcript=callback,
            on_connection_error=callback,
        )

    monkeypatch.setenv("ASR_PROVIDER", "dummy")
    session = create_asr_session(
        "qwen",
        on_input_transcript=callback,
        on_connection_error=callback,
    )
    assert session is not None
    with pytest.raises(RuntimeError, match="ASR_INVALID_CONFIG"):
        create_asr_session(
            "qwen",
            config=AsrSessionConfig(endpointing_mode="provider"),
            on_input_transcript=callback,
            on_connection_error=callback,
        )


async def test_connect_ready_status_and_idempotent_close(monkeypatch):
    monkeypatch.setenv("ASR_PROVIDER", "dummy")
    statuses: asyncio.Queue[str] = asyncio.Queue()
    session = create_asr_session(
        "qwen",
        on_input_transcript=AsyncMock(),
        on_connection_error=AsyncMock(),
        on_status_message=statuses.put,
    )

    await session.connect()
    await session.connect()
    assert session.is_ready is True
    assert await asyncio.wait_for(statuses.get(), 1) == "ASR_CONNECTING"
    assert await asyncio.wait_for(statuses.get(), 1) == "ASR_READY"

    await session.close()
    await session.close()
    assert session.is_ready is False
    assert await asyncio.wait_for(statuses.get(), 1) == "ASR_CLOSED"
    assert statuses.empty()


async def test_dummy_handles_multiple_utterances(monkeypatch):
    monkeypatch.setenv("ASR_PROVIDER", "dummy")
    monkeypatch.setenv("ASR_DUMMY_TRANSCRIPT", "测试识别文本")
    transcripts: asyncio.Queue[str] = asyncio.Queue()
    session = create_asr_session(
        "qwen",
        on_input_transcript=transcripts.put,
        on_connection_error=AsyncMock(),
    )
    await session.connect()
    await session.signal_user_activity_end()
    await asyncio.sleep(0)
    assert transcripts.empty()

    for _ in range(2):
        await session.stream_audio(b"\x00\x00" * 160, sample_rate_hz=16_000)
        await session.signal_user_activity_end()

    assert await asyncio.wait_for(transcripts.get(), 1) == "测试识别文本"
    assert await asyncio.wait_for(transcripts.get(), 1) == "测试识别文本"
    assert session.is_ready is True
    await session.close()


async def test_pcm_16k_and_48k_are_accepted_and_rate_is_locked(monkeypatch):
    monkeypatch.setenv("ASR_PROVIDER", "dummy")
    for sample_rate, sample_count in ((16_000, 320), (48_000, 960)):
        transcripts: asyncio.Queue[str] = asyncio.Queue()
        session = create_asr_session(
            "qwen",
            config=AsrSessionConfig(input_sample_rate_hz=sample_rate),
            on_input_transcript=transcripts.put,
            on_connection_error=AsyncMock(),
        )
        await session.connect()
        await session.stream_audio(b"\x00\x00" * sample_count)
        with pytest.raises((RuntimeError, ValueError), match="ASR_SAMPLE_RATE_CHANGED"):
            await session.stream_audio(
                b"\x00\x00" * 16,
                sample_rate_hz=48_000 if sample_rate == 16_000 else 16_000,
            )
        await session.signal_user_activity_end()
        assert await asyncio.wait_for(transcripts.get(), 1)
        await session.close()


async def test_48k_pcm_is_resampled_to_16k_before_worker():
    normalized_sizes: asyncio.Queue[int] = asyncio.Queue()

    async def capture_worker(request_queue, response_queue, api_key, config):
        del api_key, config
        chunks = []
        await response_queue.put(_AsrWorkerEvent(kind="ready", generation=0))
        while True:
            request = await request_queue.get()
            if request.kind == "audio":
                chunks.append(request.audio)
            elif request.kind == "commit":
                await normalized_sizes.put(sum(map(len, chunks)))
            elif request.kind == "shutdown":
                await response_queue.put(
                    _AsrWorkerEvent(kind="closed", generation=request.generation)
                )
                return

    session = _RealtimeAsrSessionImpl(
        worker_fn=capture_worker,
        api_key="",
        config=AsrSessionConfig(input_sample_rate_hz=48_000),
        on_input_transcript=AsyncMock(),
        on_connection_error=AsyncMock(),
    )
    await session.connect()
    await session.stream_audio(b"\x00\x00" * 48_000)
    await session.signal_user_activity_end()
    assert await asyncio.wait_for(normalized_sizes.get(), 1) == 16_000 * 2
    await session.close()


async def test_pcm_validation_and_empty_chunk(monkeypatch):
    monkeypatch.setenv("ASR_PROVIDER", "dummy")
    session = create_asr_session(
        "qwen",
        on_input_transcript=AsyncMock(),
        on_connection_error=AsyncMock(),
    )
    await session.connect()
    await session.stream_audio(b"")
    with pytest.raises((RuntimeError, ValueError), match="ASR_INVALID_PCM"):
        await session.stream_audio(b"\x00")
    with pytest.raises((RuntimeError, ValueError), match="ASR_AUDIO_CHUNK_TOO_LARGE"):
        await session.stream_audio(b"\x00\x00" * 16_001, sample_rate_hz=16_000)
    await session.close()


async def test_duplicate_final_is_delivered_once(monkeypatch):
    monkeypatch.setenv("ASR_PROVIDER", "dummy")
    monkeypatch.setenv("ASR_DUMMY_MODE", "duplicate")
    transcripts: asyncio.Queue[str] = asyncio.Queue()
    session = create_asr_session(
        "qwen",
        on_input_transcript=transcripts.put,
        on_connection_error=AsyncMock(),
    )
    await session.connect()
    await session.stream_audio(b"\x00\x00" * 160)
    await session.signal_user_activity_end()
    assert await asyncio.wait_for(transcripts.get(), 1)
    await asyncio.sleep(0.05)
    assert transcripts.empty()
    await session.close()


async def test_delayed_final_is_dropped_after_clear_and_close(monkeypatch):
    monkeypatch.setenv("ASR_PROVIDER", "dummy")
    monkeypatch.setenv("ASR_DUMMY_MODE", "delayed")
    monkeypatch.setenv("ASR_DUMMY_DELAY_MS", "50")
    transcripts: asyncio.Queue[str] = asyncio.Queue()
    session = create_asr_session(
        "qwen",
        on_input_transcript=transcripts.put,
        on_connection_error=AsyncMock(),
    )
    await session.connect()
    await session.stream_audio(b"\x00\x00" * 160)
    await session.signal_user_activity_end()
    await session.clear_audio_buffer()
    await asyncio.sleep(0.1)
    assert transcripts.empty()

    await session.stream_audio(b"\x00\x00" * 160)
    await session.signal_user_activity_end()
    await session.close()
    await asyncio.sleep(0.1)
    assert transcripts.empty()


async def test_callback_failure_does_not_break_session(monkeypatch):
    monkeypatch.setenv("ASR_PROVIDER", "dummy")
    callback_started: asyncio.Queue[str] = asyncio.Queue()

    async def failing_callback(text):
        await callback_started.put(text)
        raise RuntimeError("downstream failure")

    session = create_asr_session(
        "qwen",
        on_input_transcript=failing_callback,
        on_connection_error=AsyncMock(),
    )
    await session.connect()
    for _ in range(2):
        await session.stream_audio(b"\x00\x00" * 160)
        await session.signal_user_activity_end()
        assert await asyncio.wait_for(callback_started.get(), 1)
    assert session.is_ready is True
    await session.close()


async def test_worker_error_is_terminal_reported_once_and_sanitized():
    errors: asyncio.Queue[str] = asyncio.Queue()
    session = _RealtimeAsrSessionImpl(
        worker_fn=_scripted_worker,
        api_key="error",
        config=AsrSessionConfig(),
        on_input_transcript=AsyncMock(),
        on_connection_error=errors.put,
    )
    await session.connect()
    await session.stream_audio(b"\x00\x00" * 160)
    await session.signal_user_activity_end()
    message = await asyncio.wait_for(errors.get(), 1)
    assert message.startswith("ASR_WORKER_FAILED:")
    assert "sk-secret" not in message
    assert session.is_ready is False
    with pytest.raises(RuntimeError, match="ASR_SESSION_NOT_READY"):
        await session.stream_audio(b"\x00\x00")
    await asyncio.sleep(0.05)
    assert errors.empty()
    assert session._worker_task is not None and session._worker_task.done()
    assert session._response_task is not None and session._response_task.done()
    assert session._callback_task is not None and session._callback_task.done()
    await session.close()


async def test_update_session_is_locked_after_connect(monkeypatch):
    monkeypatch.setenv("ASR_PROVIDER", "dummy")
    session = create_asr_session(
        "qwen",
        on_input_transcript=AsyncMock(),
        on_connection_error=AsyncMock(),
    )
    await session.update_session({"language": "en-US", "instructions": "ignored"})
    with pytest.raises((RuntimeError, ValueError), match="ASR_INVALID_CONFIG"):
        await session.update_session({"unknown_asr_field": True})
    await session.connect()
    await session.update_session({"instructions": "ignored", "tools": []})
    with pytest.raises(RuntimeError, match="ASR_SESSION_CONFIG_LOCKED"):
        await session.update_session({"language": "ja"})
    await session.close()


async def test_provider_mode_does_not_commit():
    transcripts: asyncio.Queue[str] = asyncio.Queue()
    session = _RealtimeAsrSessionImpl(
        worker_fn=_scripted_worker,
        api_key="provider",
        config=AsrSessionConfig(endpointing_mode="provider"),
        on_input_transcript=transcripts.put,
        on_connection_error=AsyncMock(),
    )
    await session.connect()
    await session.stream_audio(b"\x00\x00" * 160)
    await session.signal_user_activity_end()
    await asyncio.sleep(0.05)
    assert transcripts.empty()
    await session.close()


async def test_partial_empty_duplicate_and_conflicting_finals_are_filtered():
    transcripts: asyncio.Queue[str] = asyncio.Queue()
    session = _RealtimeAsrSessionImpl(
        worker_fn=_scripted_worker,
        api_key="events",
        config=AsrSessionConfig(),
        on_input_transcript=transcripts.put,
        on_connection_error=AsyncMock(),
    )
    await session.connect()
    await session.stream_audio(b"\x00\x00" * 160)
    await session.signal_user_activity_end()
    assert await asyncio.wait_for(transcripts.get(), 1) == "first"
    await asyncio.sleep(0.05)
    assert transcripts.empty()
    await session.close()


async def test_stale_utterance_error_is_dropped_after_clear():
    errors: asyncio.Queue[str] = asyncio.Queue()
    session = _RealtimeAsrSessionImpl(
        worker_fn=_delayed_error_worker,
        api_key="",
        config=AsrSessionConfig(),
        on_input_transcript=AsyncMock(),
        on_connection_error=errors.put,
    )
    await session.connect()
    await session.stream_audio(b"\x00\x00" * 160)
    await session.signal_user_activity_end()
    await session.clear_audio_buffer()
    await asyncio.sleep(0.05)
    assert errors.empty()
    assert session.is_ready is True
    await session.close()


async def test_close_unblocks_request_backpressure(monkeypatch):
    monkeypatch.setattr(asr_infra, "_WORKER_CLOSE_TIMEOUT_SECONDS", 0.02)
    session = _RealtimeAsrSessionImpl(
        worker_fn=_non_consuming_worker,
        api_key="",
        config=AsrSessionConfig(),
        on_input_transcript=AsyncMock(),
        on_connection_error=AsyncMock(),
    )
    await session.connect()
    for _ in range(asr_infra._REQUEST_QUEUE_SIZE):
        await session.stream_audio(b"\x00\x00")

    blocked_producer = asyncio.create_task(session.stream_audio(b"\x00\x00"))
    await asyncio.sleep(0)
    assert blocked_producer.done() is False
    await asyncio.wait_for(session.close(), 1)
    with pytest.raises(RuntimeError, match="ASR_SESSION_NOT_READY"):
        await blocked_producer


async def test_invalid_ready_generation_times_out(monkeypatch):
    monkeypatch.setattr(asr_infra, "_READY_TIMEOUT_SECONDS", 0.02)
    errors: asyncio.Queue[str] = asyncio.Queue()
    session = _RealtimeAsrSessionImpl(
        worker_fn=_wrong_generation_ready_worker,
        api_key="",
        config=AsrSessionConfig(),
        on_input_transcript=AsyncMock(),
        on_connection_error=errors.put,
    )
    with pytest.raises(RuntimeError, match="ASR_CONNECT_TIMEOUT"):
        await session.connect()
    assert (await asyncio.wait_for(errors.get(), 1)).startswith("ASR_CONNECT_TIMEOUT:")
    await session.close()


async def test_worker_normal_exit_is_terminal():
    release_worker = asyncio.Event()
    errors: asyncio.Queue[str] = asyncio.Queue()

    async def exiting_worker(request_queue, response_queue, api_key, config):
        del request_queue, api_key, config
        await response_queue.put(_AsrWorkerEvent(kind="ready", generation=0))
        await release_worker.wait()

    session = _RealtimeAsrSessionImpl(
        worker_fn=exiting_worker,
        api_key="",
        config=AsrSessionConfig(),
        on_input_transcript=AsyncMock(),
        on_connection_error=errors.put,
    )
    await session.connect()
    release_worker.set()
    message = await asyncio.wait_for(errors.get(), 1)
    assert message == "ASR_WORKER_FAILED: worker closed unexpectedly"
    assert session._response_task is not None
    await asyncio.wait_for(asyncio.shield(session._response_task), 1)
    assert session.is_ready is False
    assert session._worker_task is not None and session._worker_task.done()
    assert session._response_task.done()
    assert session._callback_task is not None and session._callback_task.done()
    await session.close()

    async def immediately_exiting_worker(
        request_queue, response_queue, api_key, config
    ):
        del request_queue, api_key, config
        await response_queue.put(_AsrWorkerEvent(kind="ready", generation=0))

    immediate_errors: asyncio.Queue[str] = asyncio.Queue()
    immediate_session = _RealtimeAsrSessionImpl(
        worker_fn=immediately_exiting_worker,
        api_key="",
        config=AsrSessionConfig(),
        on_input_transcript=AsyncMock(),
        on_connection_error=immediate_errors.put,
    )
    with pytest.raises(RuntimeError, match="ASR_WORKER_FAILED"):
        await immediate_session.connect()
    assert (await asyncio.wait_for(immediate_errors.get(), 1)).startswith(
        "ASR_WORKER_FAILED:"
    )
    assert immediate_session.is_ready is False
    await immediate_session.close()


async def test_cancelled_close_still_finishes_cleanup():
    session = _RealtimeAsrSessionImpl(
        worker_fn=_scripted_worker,
        api_key="",
        config=AsrSessionConfig(),
        on_input_transcript=AsyncMock(),
        on_connection_error=AsyncMock(),
    )
    await session.connect()
    await session._operation_lock.acquire()
    try:
        close_waiter = asyncio.create_task(session.close())
        await asyncio.sleep(0)
        assert session.is_ready is False
        close_waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await close_waiter
    finally:
        session._operation_lock.release()

    await asyncio.wait_for(session.close(), 1)
    assert session.is_ready is False
    assert session._worker_task is not None and session._worker_task.done()
    assert session._response_task is not None and session._response_task.done()
    assert session._callback_task is not None and session._callback_task.done()


async def test_dummy_does_not_retain_pcm_requests(monkeypatch):
    class Payload:
        pass

    monkeypatch.setenv("ASR_DUMMY_MODE", "normal")
    request_queue: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    events: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    worker_task = asyncio.create_task(
        dummy_asr_worker(
            request_queue,
            events,
            "",
            AsrSessionConfig(),
        )
    )
    ready = await asyncio.wait_for(events.get(), 1)
    assert ready.kind == "ready"

    first_payload = Payload()
    first_payload_ref = weakref.ref(first_payload)
    first_request = _AsrWorkerRequest(
        kind="audio",
        generation=0,
        buffer_epoch=0,
        utterance_id=1,
        audio=first_payload,  # type: ignore[arg-type]
    )
    second_request = _AsrWorkerRequest(
        kind="audio",
        generation=0,
        buffer_epoch=0,
        utterance_id=1,
        audio=Payload(),  # type: ignore[arg-type]
    )

    try:
        await request_queue.put(first_request)
        await request_queue.put(second_request)
        for _ in range(20):
            if request_queue.empty():
                break
            await asyncio.sleep(0)
        assert request_queue.empty()
        await asyncio.sleep(0)
        del first_request, first_payload
        gc.collect()
        assert first_payload_ref() is None
    finally:
        await request_queue.put(
            _AsrWorkerRequest(kind="shutdown", generation=1)
        )
        await asyncio.wait_for(worker_task, 1)
