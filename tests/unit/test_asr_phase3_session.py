import asyncio
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock

import pytest

from main_logic.asr_client import create_asr_session
from main_logic.asr_client._infra import (
    AsrSessionConfig,
    _AsrWorkerEvent,
    _RealtimeAsrSessionImpl,
)


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
    await session.close()
    assert adapter.closed


async def test_voice_turn_commit_is_identity_checked_and_commits_once():
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
    assert adapter is not None
    await adapter.on_commit(0, 0, 1)
    await adapter.on_commit(0, 0, 1)
    assert session._request_queue is not None
    await asyncio.wait_for(session._request_queue.join(), 1)
    assert [item.kind for item in observed].count("commit") == 1
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


async def test_openai_glm_and_gemini_routes_create_smart_turn_sessions(monkeypatch):
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
    for core_type in ("openai", "glm", "gemini"):
        session = create_asr_session(
            core_type,
            on_input_transcript=AsyncMock(),
            on_connection_error=AsyncMock(),
        )
        assert session._voice_turn_factory is not None


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
