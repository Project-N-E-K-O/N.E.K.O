import asyncio
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock

import pytest

from main_logic.asr_client import create_asr_session
from main_logic.asr_client._infra import (
    AsrSessionConfig,
    _AsrWorkerEvent,
    _CallbackItem,
    _RealtimeAsrSessionImpl,
)
from main_logic.asr_client.provider_policy import AsrProviderPolicy


pytestmark = pytest.mark.asyncio


class _FakeVoiceTurnAdapter:
    def __init__(
        self,
        on_commit: Callable[[int, int, int], Awaitable[None]],
    ) -> None:
        self.on_commit = on_commit
        self.started = False
        self.closed = False
        self.audio: list[tuple[int, int, int, bytes]] = []
        self.resets: list[tuple[int, int, int]] = []
        self.failure = asyncio.get_running_loop().create_future()

    async def start(self) -> None:
        self.started = True

    async def push_audio(
        self,
        *,
        generation: int,
        buffer_epoch: int,
        utterance_id: int,
        pcm16: bytes,
    ) -> None:
        self.audio.append((generation, buffer_epoch, utterance_id, pcm16))

    async def reset(
        self,
        *,
        generation: int,
        buffer_epoch: int,
        utterance_id: int,
    ) -> None:
        self.resets.append((generation, buffer_epoch, utterance_id))

    async def close(self) -> None:
        self.closed = True

    async def wait_failure(self):
        return await self.failure

    def report_failure(self, failure) -> None:
        if not self.failure.done():
            self.failure.set_result(failure)


class _FailingVoiceTurnAdapter(_FakeVoiceTurnAdapter):
    def __init__(
        self,
        on_commit: Callable[[int, int, int], Awaitable[None]],
        *,
        fail_start: bool = False,
        fail_close: bool = False,
    ) -> None:
        super().__init__(on_commit)
        self.fail_start = fail_start
        self.fail_close = fail_close
        self.close_calls = 0

    async def start(self) -> None:
        self.started = True
        if self.fail_start:
            raise RuntimeError("adapter start failed")

    async def close(self) -> None:
        self.close_calls += 1
        self.closed = True
        if self.fail_close:
            raise RuntimeError("adapter close failed")


async def _recording_worker(request_queue, response_queue, api_key, config):
    del api_key, config
    await response_queue.put(_AsrWorkerEvent(kind="ready", generation=0))
    while True:
        request = await request_queue.get()
        try:
            if request.kind == "shutdown":
                await response_queue.put(
                    _AsrWorkerEvent(kind="closed", generation=request.generation)
                )
                return
        finally:
            request_queue.task_done()


async def test_session_fans_same_normalized_pcm_to_worker_and_voice_turn():
    adapter = None

    def factory(on_commit):
        nonlocal adapter
        adapter = _FakeVoiceTurnAdapter(on_commit)
        return adapter

    session = _RealtimeAsrSessionImpl(
        worker_fn=_recording_worker,
        api_key="",
        config=AsrSessionConfig(),
        on_input_transcript=AsyncMock(),
        on_connection_error=AsyncMock(),
        voice_turn_factory=factory,
    )
    await session.connect()
    assert adapter is not None and adapter.started

    pcm = b"\x01\x00" * 160
    await session.stream_audio(pcm)
    assert session._request_queue is not None
    await asyncio.wait_for(session._request_queue.join(), 1)
    assert adapter.audio == [(0, 0, 1, pcm)]
    assert session.provider_wire_audio_ms == 10
    await session.close()
    assert adapter.closed


async def test_voice_turn_commit_is_identity_checked_and_commits_once():
    observed = []
    endpointed: list[str] = []

    async def worker(request_queue, response_queue, api_key, config):
        del api_key, config
        await response_queue.put(_AsrWorkerEvent(kind="ready", generation=0))
        while True:
            request = await request_queue.get()
            try:
                observed.append(request)
                if request.kind == "shutdown":
                    await response_queue.put(
                        _AsrWorkerEvent(kind="closed", generation=request.generation)
                    )
                    return
            finally:
                request_queue.task_done()

    adapter = None

    def factory(on_commit):
        nonlocal adapter
        adapter = _FakeVoiceTurnAdapter(on_commit)
        return adapter

    session = _RealtimeAsrSessionImpl(
        worker_fn=worker,
        api_key="",
        config=AsrSessionConfig(),
        on_input_transcript=AsyncMock(),
        on_connection_error=AsyncMock(),
        on_turn_endpointed=AsyncMock(side_effect=lambda: endpointed.append("sealed")),
        voice_turn_factory=factory,
    )
    await session.connect()
    await session.stream_audio(b"\x00\x00" * 160)
    assert adapter is not None
    await adapter.on_commit(0, 0, 1)
    await adapter.on_commit(0, 0, 1)
    assert session._request_queue is not None
    await asyncio.wait_for(session._request_queue.join(), 1)
    assert [item.kind for item in observed].count("commit") == 1
    assert endpointed == ["sealed"]
    assert adapter.resets[-1] == (0, 0, 2)
    await session.close()


async def test_clear_invalidates_late_voice_turn_commit():
    observed = []

    async def worker(request_queue, response_queue, api_key, config):
        del api_key, config
        await response_queue.put(_AsrWorkerEvent(kind="ready", generation=0))
        while True:
            request = await request_queue.get()
            try:
                observed.append(request)
                if request.kind == "shutdown":
                    await response_queue.put(
                        _AsrWorkerEvent(kind="closed", generation=request.generation)
                    )
                    return
            finally:
                request_queue.task_done()

    adapter = None

    def factory(on_commit):
        nonlocal adapter
        adapter = _FakeVoiceTurnAdapter(on_commit)
        return adapter

    session = _RealtimeAsrSessionImpl(
        worker_fn=worker,
        api_key="",
        config=AsrSessionConfig(),
        on_input_transcript=AsyncMock(),
        on_connection_error=AsyncMock(),
        voice_turn_factory=factory,
    )
    await session.connect()
    await session.stream_audio(b"\x00\x00" * 160)
    await session.clear_audio_buffer()
    assert adapter is not None
    await adapter.on_commit(0, 0, 1)
    assert session._request_queue is not None
    await asyncio.wait_for(session._request_queue.join(), 1)
    assert [item.kind for item in observed].count("commit") == 0
    assert adapter.resets[-1] == (0, 1, 2)
    await session.close()


async def test_segmented_routes_use_smart_turn_and_openai_uses_provider_vad(
    monkeypatch,
):
    import utils.config_manager as config_manager

    class _ConfigManager:
        def get_core_config(self):
            return {
                "ASSIST_API_KEY_OPENAI": "openai-key",
                "ASSIST_API_KEY_GLM": "glm-key",
                "ASSIST_API_KEY_GEMINI": "gemini-key",
            }

    monkeypatch.delenv("ASR_PROVIDER", raising=False)
    monkeypatch.setattr(
        config_manager,
        "get_config_manager",
        lambda: _ConfigManager(),
    )
    for core_type in ("glm", "gemini"):
        session = create_asr_session(
            core_type,
            on_input_transcript=AsyncMock(),
            on_connection_error=AsyncMock(),
        )
        assert session._voice_turn_factory is not None
    openai_session = create_asr_session(
        "openai",
        on_input_transcript=AsyncMock(),
        on_connection_error=AsyncMock(),
    )
    assert openai_session._config.endpointing_mode == "provider"
    assert openai_session._voice_turn_factory is None


async def test_voice_turn_start_failure_fails_session_and_releases_adapter():
    adapter = None
    on_error = AsyncMock()

    def factory(on_commit):
        nonlocal adapter
        adapter = _FailingVoiceTurnAdapter(on_commit, fail_start=True)
        return adapter

    session = _RealtimeAsrSessionImpl(
        worker_fn=_recording_worker,
        api_key="",
        config=AsrSessionConfig(),
        on_input_transcript=AsyncMock(),
        on_connection_error=on_error,
        voice_turn_factory=factory,
    )

    with pytest.raises(RuntimeError, match="ASR_VOICE_TURN_START_FAILED"):
        await session.connect()

    assert adapter is not None
    assert adapter.close_calls == 1
    assert adapter.closed
    assert session._state.value == "failed"
    assert session._worker_task is not None and session._worker_task.done()
    on_error.assert_awaited_once_with(
        "ASR_VOICE_TURN_START_FAILED: voice turn adapter failed to start"
    )


async def test_voice_turn_close_failure_does_not_block_session_cleanup():
    adapter = None

    def factory(on_commit):
        nonlocal adapter
        adapter = _FailingVoiceTurnAdapter(on_commit, fail_close=True)
        return adapter

    session = _RealtimeAsrSessionImpl(
        worker_fn=_recording_worker,
        api_key="",
        config=AsrSessionConfig(),
        on_input_transcript=AsyncMock(),
        on_connection_error=AsyncMock(),
        voice_turn_factory=factory,
    )
    await session.connect()

    await session.close()

    assert adapter is not None and adapter.close_calls == 1
    assert session._state.value == "closed"
    assert session._worker_task is not None and session._worker_task.done()


async def test_worker_failure_unloads_voice_turn_even_when_adapter_close_fails():
    emit_error = asyncio.Event()
    error_reported = asyncio.Event()
    adapter = None

    async def worker(request_queue, response_queue, api_key, config):
        del request_queue, api_key, config
        await response_queue.put(_AsrWorkerEvent(kind="ready", generation=0))
        await emit_error.wait()
        await response_queue.put(
            _AsrWorkerEvent(
                kind="error",
                generation=0,
                error_code="ASR_PROVIDER_FAILED",
                error_message="provider failed",
            )
        )
        await asyncio.Event().wait()

    async def on_error(error: str) -> None:
        assert error == "ASR_PROVIDER_FAILED: provider failed"
        error_reported.set()

    def factory(on_commit):
        nonlocal adapter
        adapter = _FailingVoiceTurnAdapter(on_commit, fail_close=True)
        return adapter

    session = _RealtimeAsrSessionImpl(
        worker_fn=worker,
        api_key="",
        config=AsrSessionConfig(),
        on_input_transcript=AsyncMock(),
        on_connection_error=on_error,
        voice_turn_factory=factory,
    )
    await session.connect()
    emit_error.set()
    await asyncio.wait_for(error_reported.wait(), 1)

    assert adapter is not None and adapter.close_calls == 1
    assert session._voice_turn_adapter is None
    assert session._state.value == "failed"


async def test_voice_turn_terminal_failure_fails_session_and_closes_once():
    adapter = None
    on_error = AsyncMock()

    def factory(on_commit):
        nonlocal adapter
        adapter = _FailingVoiceTurnAdapter(on_commit)
        return adapter

    session = _RealtimeAsrSessionImpl(
        worker_fn=_recording_worker,
        api_key="",
        config=AsrSessionConfig(),
        on_input_transcript=AsyncMock(),
        on_connection_error=on_error,
        voice_turn_factory=factory,
    )
    await session.connect()
    assert adapter is not None

    failure = type(
        "Failure",
        (),
        {"kind": "unavailable", "stage": "vad_load"},
    )()
    adapter.report_failure(failure)
    await asyncio.wait_for(
        asyncio.create_task(_wait_until(lambda: session._state.value == "failed")),
        1,
    )

    assert adapter.close_calls == 1
    assert session._voice_turn_adapter is None
    assert session._voice_turn_watch_task is None
    on_error.assert_awaited_once_with(
        "ASR_ENDPOINTING_FAILED: required voice turn endpointing failed"
    )
    await asyncio.wait_for(session.close(), 1)
    assert adapter.close_calls == 1
    assert session._state.value == "closed"


async def test_segmented_forced_splits_wait_for_logical_turn_completion():
    adapter = None
    callbacks: list[str] = []
    requests = []
    endpointed: list[str] = []

    async def worker(request_queue, response_queue, api_key, config):
        del api_key, config
        await response_queue.put(_AsrWorkerEvent(kind="ready", generation=0))
        while True:
            request = await request_queue.get()
            try:
                requests.append(request)
                if request.kind == "commit":
                    await response_queue.put(
                        _AsrWorkerEvent(
                            kind="final",
                            generation=request.generation,
                            buffer_epoch=request.buffer_epoch,
                            utterance_id=request.utterance_id,
                            text=f"part-{request.utterance_id}",
                        )
                    )
                if request.kind == "shutdown":
                    await response_queue.put(
                        _AsrWorkerEvent(
                            kind="closed",
                            generation=request.generation,
                        )
                    )
                    return
            finally:
                request_queue.task_done()

    def factory(on_commit):
        nonlocal adapter
        adapter = _FakeVoiceTurnAdapter(on_commit)
        return adapter

    async def on_transcript(text: str) -> None:
        callbacks.append(text)

    session = _RealtimeAsrSessionImpl(
        worker_fn=worker,
        api_key="",
        config=AsrSessionConfig(),
        on_input_transcript=on_transcript,
        on_connection_error=AsyncMock(),
        on_turn_endpointed=AsyncMock(side_effect=lambda: endpointed.append("sealed")),
        voice_turn_factory=factory,
        provider_policy=AsrProviderPolicy(
            transport="segmented",
            endpoint_authority="smart_turn",
            smart_turn_required=True,
            max_segment_ms=10,
            warm_transport_ms=0,
            replay_policy="none",
        ),
    )
    await session.connect()
    assert adapter is not None

    await session.stream_audio(b"\x01\x00" * 160)
    await session.stream_audio(b"\x02\x00" * 160)
    assert session._request_queue is not None
    await asyncio.wait_for(session._request_queue.join(), 1)
    await asyncio.sleep(0)

    assert callbacks == []
    assert endpointed == []
    assert [request.utterance_id for request in requests if request.kind == "commit"] == [
        1,
        2,
    ]
    assert {item[2] for item in adapter.audio} == {1}

    await adapter.on_commit(0, 0, 1)
    await asyncio.wait_for(_wait_until(lambda: bool(callbacks)), 1)
    assert endpointed == ["sealed"]
    assert callbacks == ["part-1 part-2"]
    await session.close()


async def test_segmented_single_chunk_is_split_before_provider_enqueue():
    adapter = None
    requests = []

    async def worker(request_queue, response_queue, api_key, config):
        del api_key, config
        await response_queue.put(_AsrWorkerEvent(kind="ready", generation=0))
        while True:
            request = await request_queue.get()
            try:
                requests.append(request)
                if request.kind == "commit":
                    await response_queue.put(
                        _AsrWorkerEvent(
                            kind="final",
                            generation=request.generation,
                            buffer_epoch=request.buffer_epoch,
                            utterance_id=request.utterance_id,
                            text=f"part-{request.utterance_id}",
                        )
                    )
                if request.kind == "shutdown":
                    await response_queue.put(
                        _AsrWorkerEvent(kind="closed", generation=request.generation)
                    )
                    return
            finally:
                request_queue.task_done()

    def factory(on_commit):
        nonlocal adapter
        adapter = _FakeVoiceTurnAdapter(on_commit)
        return adapter

    callback = AsyncMock()
    session = _RealtimeAsrSessionImpl(
        worker_fn=worker,
        api_key="",
        config=AsrSessionConfig(),
        on_input_transcript=callback,
        on_connection_error=AsyncMock(),
        voice_turn_factory=factory,
        provider_policy=AsrProviderPolicy(
            transport="segmented",
            endpoint_authority="smart_turn",
            smart_turn_required=True,
            max_segment_ms=10,
            warm_transport_ms=0,
            replay_policy="none",
        ),
    )
    await session.connect()

    await session.stream_audio(b"\x01\x00" * 400)
    assert session._request_queue is not None
    await asyncio.wait_for(session._request_queue.join(), 1)

    audio_by_utterance: dict[int, int] = {}
    for request in requests:
        if request.kind == "audio":
            assert request.utterance_id is not None
            audio_by_utterance[request.utterance_id] = (
                audio_by_utterance.get(request.utterance_id, 0) + len(request.audio)
            )
    assert audio_by_utterance == {1: 320, 2: 320, 3: 160}
    assert callback.await_count == 0

    assert adapter is not None
    await adapter.on_commit(0, 0, 1)
    await asyncio.wait_for(_wait_until(lambda: callback.await_count == 1), 1)
    callback.assert_awaited_once_with("part-1 part-2 part-3")
    await session.close()


async def test_segmented_final_segment_is_aggregated_with_forced_segments():
    adapter = None
    callbacks: list[str] = []

    async def on_transcript(text: str) -> None:
        callbacks.append(text)

    async def worker(request_queue, response_queue, api_key, config):
        del api_key, config
        await response_queue.put(_AsrWorkerEvent(kind="ready", generation=0))
        while True:
            request = await request_queue.get()
            try:
                if request.kind == "commit":
                    await response_queue.put(
                        _AsrWorkerEvent(
                            kind="final",
                            generation=request.generation,
                            buffer_epoch=request.buffer_epoch,
                            utterance_id=request.utterance_id,
                            text=f"segment-{request.utterance_id}",
                        )
                    )
                if request.kind == "shutdown":
                    await response_queue.put(
                        _AsrWorkerEvent(kind="closed", generation=request.generation)
                    )
                    return
            finally:
                request_queue.task_done()

    def factory(on_commit):
        nonlocal adapter
        adapter = _FakeVoiceTurnAdapter(on_commit)
        return adapter

    session = _RealtimeAsrSessionImpl(
        worker_fn=worker,
        api_key="",
        config=AsrSessionConfig(),
        on_input_transcript=on_transcript,
        on_connection_error=AsyncMock(),
        voice_turn_factory=factory,
        provider_policy=AsrProviderPolicy(
            transport="segmented",
            endpoint_authority="smart_turn",
            smart_turn_required=True,
            max_segment_ms=10,
            warm_transport_ms=0,
            replay_policy="none",
        ),
    )
    await session.connect()
    assert adapter is not None

    await session.stream_audio(b"\x01\x00" * 160)
    await session.stream_audio(b"\x02\x00" * 80)
    await adapter.on_commit(0, 0, 1)
    assert session._request_queue is not None
    await asyncio.wait_for(session._request_queue.join(), 1)
    assert session._callback_queue is not None
    await asyncio.wait_for(session._callback_queue.join(), 1)

    assert callbacks == ["segment-1 segment-2"]
    assert adapter.resets[-1] == (0, 0, 2)
    await session.close()


async def test_segmented_local_buffering_is_not_counted_as_provider_wire_audio():
    adapter = None

    def factory(on_commit):
        nonlocal adapter
        adapter = _FakeVoiceTurnAdapter(on_commit)
        return adapter

    session = _RealtimeAsrSessionImpl(
        worker_fn=_recording_worker,
        api_key="",
        config=AsrSessionConfig(),
        on_input_transcript=AsyncMock(),
        on_connection_error=AsyncMock(),
        voice_turn_factory=factory,
        provider_policy=AsrProviderPolicy(
            transport="segmented",
            endpoint_authority="smart_turn",
            smart_turn_required=True,
            max_segment_ms=1_000,
            warm_transport_ms=0,
            replay_policy="none",
        ),
    )
    await session.connect()
    await session.stream_audio(b"\x01\x00" * 160)

    assert session.provider_wire_audio_ms == 0
    assert adapter is not None
    await adapter.on_commit(0, 0, 1)
    assert session.provider_wire_audio_ms == 10
    await session.close()


async def test_clear_drops_final_already_waiting_in_callback_queue():
    callback = AsyncMock()
    session = _RealtimeAsrSessionImpl(
        worker_fn=_recording_worker,
        api_key="",
        config=AsrSessionConfig(),
        on_input_transcript=callback,
        on_connection_error=AsyncMock(),
    )
    await session.connect()
    assert session._callback_task is not None
    session._callback_task.cancel()
    await asyncio.gather(session._callback_task, return_exceptions=True)
    assert session._callback_queue is not None
    await session._callback_queue.put(
        _CallbackItem(
            text="stale final",
            generation=session._generation,
            buffer_epoch=session._buffer_epoch,
        )
    )

    await session.clear_audio_buffer()
    session._callback_task = asyncio.create_task(session._dispatch_callbacks())
    await asyncio.wait_for(session._callback_queue.join(), 1)

    callback.assert_not_awaited()
    await session.close()


async def test_resampler_tail_is_included_in_streaming_provider_wire_metric():
    session = _RealtimeAsrSessionImpl(
        worker_fn=_recording_worker,
        api_key="",
        config=AsrSessionConfig(),
        on_input_transcript=AsyncMock(),
        on_connection_error=AsyncMock(),
    )
    await session.connect()
    await session.stream_audio(b"\x01\x00" * 160)
    before_ms = session.provider_wire_audio_ms
    session._flush_resampler = MagicMock(return_value=b"\x02\x00" * 160)

    await session.signal_user_activity_end()

    assert session.provider_wire_audio_ms == before_ms + 10
    await session.close()


async def test_voice_turn_push_failure_fails_session_instead_of_staying_ready():
    on_error = AsyncMock()

    class _PushFailAdapter(_FakeVoiceTurnAdapter):
        async def push_audio(self, **kwargs) -> None:
            del kwargs
            raise RuntimeError("push failed")

    adapter = None

    def factory(on_commit):
        nonlocal adapter
        adapter = _PushFailAdapter(on_commit)
        return adapter

    session = _RealtimeAsrSessionImpl(
        worker_fn=_recording_worker,
        api_key="",
        config=AsrSessionConfig(),
        on_input_transcript=AsyncMock(),
        on_connection_error=on_error,
        voice_turn_factory=factory,
    )
    await session.connect()

    with pytest.raises(RuntimeError, match="ASR_ENDPOINTING_FAILED"):
        await session.stream_audio(b"\x01\x00" * 160)

    assert session._state.value == "failed"
    assert adapter is not None and adapter.closed
    on_error.assert_awaited_once_with(
        "ASR_ENDPOINTING_FAILED: required voice turn endpointing failed"
    )


async def test_voice_turn_reset_failure_fails_session_instead_of_staying_ready():
    on_error = AsyncMock()

    class _ResetFailAdapter(_FakeVoiceTurnAdapter):
        async def reset(self, **kwargs) -> None:
            del kwargs
            raise RuntimeError("reset failed")

    adapter = None

    def factory(on_commit):
        nonlocal adapter
        adapter = _ResetFailAdapter(on_commit)
        return adapter

    session = _RealtimeAsrSessionImpl(
        worker_fn=_recording_worker,
        api_key="",
        config=AsrSessionConfig(),
        on_input_transcript=AsyncMock(),
        on_connection_error=on_error,
        voice_turn_factory=factory,
    )
    await session.connect()

    await session.stream_audio(b"\x01\x00" * 160)
    await session.signal_user_activity_end()
    await asyncio.wait_for(
        _wait_until(lambda: session._state.value == "failed"),
        1,
    )

    assert session._state.value == "failed"
    assert adapter is not None and adapter.closed
    on_error.assert_awaited_once_with(
        "ASR_ENDPOINTING_FAILED: required voice turn endpointing failed"
    )


async def test_close_waits_for_managed_voice_turn_reset_before_adapter_close():
    class _BlockingResetAdapter(_FakeVoiceTurnAdapter):
        def __init__(self, on_commit):
            super().__init__(on_commit)
            self.reset_started = asyncio.Event()
            self.release_reset = asyncio.Event()

        async def reset(self, **kwargs) -> None:
            self.reset_started.set()
            await self.release_reset.wait()
            await super().reset(**kwargs)

    adapter = None

    def factory(on_commit):
        nonlocal adapter
        adapter = _BlockingResetAdapter(on_commit)
        return adapter

    session = _RealtimeAsrSessionImpl(
        worker_fn=_recording_worker,
        api_key="",
        config=AsrSessionConfig(),
        on_input_transcript=AsyncMock(),
        on_connection_error=AsyncMock(),
        voice_turn_factory=factory,
    )
    await session.connect()
    await session.stream_audio(b"\x01\x00" * 160)
    await session.signal_user_activity_end()
    assert adapter is not None
    await asyncio.wait_for(adapter.reset_started.wait(), 1)

    close_task = asyncio.create_task(session.close())
    await asyncio.sleep(0)
    assert adapter.closed is False
    adapter.release_reset.set()
    await asyncio.wait_for(close_task, 1)

    assert adapter.closed is True
    assert session._voice_turn_reset_task is None


async def _wait_until(predicate) -> None:
    while not predicate():
        await asyncio.sleep(0)
