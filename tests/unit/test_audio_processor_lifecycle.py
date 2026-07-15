from __future__ import annotations

import asyncio
import importlib
import threading

import numpy as np
import pytest

from main_logic.omni_realtime_client import OmniRealtimeClient
from utils.audio_processor import AudioProcessor, _LiteDenoiser


client_module = importlib.import_module("main_logic.omni_realtime_client._client")


class _FakeRnnoise:
    def __init__(self) -> None:
        self.destroyed: list[object] = []

    def create(self) -> object:
        return object()

    def destroy(self, state: object) -> None:
        self.destroyed.append(state)


def test_lite_denoiser_close_destroys_native_state_once() -> None:
    library = _FakeRnnoise()
    denoiser = _LiteDenoiser(library)
    state = denoiser._state

    denoiser.close()
    denoiser.close()

    assert library.destroyed == [state]
    assert denoiser._state is None


def test_audio_processor_close_releases_owned_buffers_and_denoiser() -> None:
    class _Denoiser:
        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    processor = object.__new__(AudioProcessor)
    denoiser = _Denoiser()
    processor._denoiser = denoiser
    processor._downsample_resampler = object()
    processor._frame_buffer = np.ones(4, dtype=np.int16)
    processor._debug_audio_before = [np.ones(1, dtype=np.int16)]
    processor._debug_audio_after = [np.ones(1, dtype=np.int16)]

    processor.close()
    processor.close()

    assert denoiser.close_calls == 1
    assert processor._denoiser is None
    assert processor._downsample_resampler is None
    assert processor._frame_buffer.size == 0
    assert processor._debug_audio_before == []
    assert processor._debug_audio_after == []


def test_disabling_noise_reduction_releases_native_denoiser() -> None:
    class _Denoiser:
        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    processor = object.__new__(AudioProcessor)
    denoiser = _Denoiser()
    processor.noise_reduce_enabled = True
    processor._denoiser = denoiser
    processor._frame_buffer = np.ones(4, dtype=np.int16)
    processor._agc_gain = 2.0

    processor.set_enabled(False)

    assert denoiser.close_calls == 1
    assert processor._denoiser is None
    assert processor._frame_buffer.size == 0


@pytest.mark.asyncio
async def test_audio_close_waits_for_executor_chunk_processing() -> None:
    processing_started = threading.Event()
    release_processing = threading.Event()

    class _Processor:
        def __init__(self) -> None:
            self.close_calls = 0

        def process_chunk(self, audio_chunk: bytes) -> bytes:
            processing_started.set()
            assert release_processing.wait(timeout=2.0)
            return audio_chunk

        def save_debug_audio(self) -> None:
            return None

        def close(self) -> None:
            self.close_calls += 1

    client = object.__new__(OmniRealtimeClient)
    processor = _Processor()
    client._noise_reduction_enabled = True
    client._audio_processor = processor
    client._audio_processing_lock = asyncio.Lock()

    process_task = asyncio.create_task(client.process_audio_chunk_async(b"chunk"))
    assert await asyncio.to_thread(processing_started.wait, 2.0)
    close_task = asyncio.create_task(client._close_audio_processor())
    await asyncio.sleep(0)

    assert processor.close_calls == 0
    release_processing.set()

    assert await process_task == b"chunk"
    assert await close_task is None
    assert processor.close_calls == 1
    assert client._audio_processor is None


@pytest.mark.asyncio
async def test_live_noise_reduction_toggle_waits_for_chunk_processing() -> None:
    processing_started = threading.Event()
    release_processing = threading.Event()

    class _Processor:
        def __init__(self) -> None:
            self.enabled_calls: list[bool] = []

        def process_chunk(self, audio_chunk: bytes) -> bytes:
            processing_started.set()
            assert release_processing.wait(timeout=2.0)
            return audio_chunk

        def set_enabled(self, enabled: bool) -> None:
            self.enabled_calls.append(enabled)

    client = object.__new__(OmniRealtimeClient)
    processor = _Processor()
    client._audio_processor = processor
    client._audio_processing_lock = asyncio.Lock()

    process_task = asyncio.create_task(client.process_audio_chunk_async(b"chunk"))
    assert await asyncio.to_thread(processing_started.wait, 2.0)
    toggle_task = asyncio.create_task(client.set_audio_noise_reduction_enabled(False))
    await asyncio.sleep(0)

    assert processor.enabled_calls == []
    release_processing.set()

    assert await process_task == b"chunk"
    assert await toggle_task is None
    assert processor.enabled_calls == [False]
    assert client._noise_reduction_enabled is False


def test_recreated_audio_processor_preserves_noise_reduction_preference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[dict[str, object]] = []

    def _create_processor(**kwargs):
        created.append(kwargs)
        return object()

    monkeypatch.setattr(client_module, "AudioProcessor", _create_processor)
    client = object.__new__(OmniRealtimeClient)
    client._noise_reduction_enabled = False
    client._on_silence_reset = lambda: None

    processor = client._create_audio_processor()

    assert processor is not None
    assert created[0]["noise_reduce_enabled"] is False


def test_initial_audio_processor_honors_noise_reduction_preference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[dict[str, object]] = []

    def _create_processor(**kwargs):
        created.append(kwargs)
        return object()

    monkeypatch.setattr(client_module, "AudioProcessor", _create_processor)

    client = OmniRealtimeClient(
        base_url="",
        api_key="",
        noise_reduction_enabled=False,
    )

    assert client._noise_reduction_enabled is False
    assert created[0]["noise_reduce_enabled"] is False


@pytest.mark.asyncio
async def test_audio_close_failure_does_not_escape_cleanup() -> None:
    class _Processor:
        def save_debug_audio(self) -> None:
            return None

        def close(self) -> None:
            raise RuntimeError("native close failed")

    client = object.__new__(OmniRealtimeClient)
    client._audio_processor = _Processor()
    client._audio_processing_lock = asyncio.Lock()

    assert await client._close_audio_processor() is None
    assert client._audio_processor is None
