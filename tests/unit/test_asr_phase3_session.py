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


async def test_glm_and_gemini_routes_create_smart_turn_sessions(monkeypatch):
    import utils.config_manager as config_manager

    class _ConfigManager:
        def get_core_config(self):
            return {
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
