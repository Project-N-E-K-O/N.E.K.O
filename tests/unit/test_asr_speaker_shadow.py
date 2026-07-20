from __future__ import annotations

import asyncio
import threading

import pytest

from main_logic.asr_client.detector_runtime import DetectorRuntime
from main_logic.asr_client.speaker_shadow import (
    SpeakerShadowConfig,
    SpeakerShadowObservation,
    SpeakerShadowRuntime,
)


class _Backend:
    def __init__(
        self,
        *,
        score: float = 0.9,
        load_ok: bool = True,
        score_error: Exception | None = None,
    ) -> None:
        self.score_value = score
        self.load_ok = load_ok
        self.score_error = score_error
        self.load_calls = 0
        self.score_calls: list[tuple[bytes, int]] = []
        self.close_calls = 0

    def load(self) -> bool:
        self.load_calls += 1
        return self.load_ok

    def score(self, pcm16: bytes, sample_rate_hz: int) -> float:
        self.score_calls.append((pcm16, sample_rate_hz))
        if self.score_error is not None:
            raise self.score_error
        return self.score_value

    def close(self) -> None:
        self.close_calls += 1


class _BlockingBackend(_Backend):
    def __init__(self) -> None:
        super().__init__(score=0.1)
        self.score_started = threading.Event()
        self.score_release = threading.Event()

    def score(self, pcm16: bytes, sample_rate_hz: int) -> float:
        self.score_started.set()
        self.score_release.wait(timeout=2)
        return super().score(pcm16, sample_rate_hz)


class _Vad:
    def load(self) -> bool:
        return True

    def close(self) -> None:
        return None


class _Gate:
    def __init__(self) -> None:
        self.inputs: list[bytes] = []

    def feed(self, pcm16: bytes):
        self.inputs.append(pcm16)
        return ()

    def reset(self) -> None:
        return None


def _pcm(duration_ms: int, *, sample_rate_hz: int = 16_000) -> bytes:
    return b"\x01\x00" * (sample_rate_hz * duration_ms // 1_000)


def _config(**overrides) -> SpeakerShadowConfig:
    values = {
        "enabled": True,
        "similarity_threshold": 0.44,
        "minimum_audio_ms": 20,
        "maximum_audio_ms": 100,
        "idle_unload_seconds": 60.0,
        "queue_capacity": 8,
    }
    values.update(overrides)
    return SpeakerShadowConfig(**values)


async def test_shadow_scores_once_and_records_hypothetical_block() -> None:
    backend = _Backend(score=0.2)
    observations: list[SpeakerShadowObservation] = []
    runtime = SpeakerShadowRuntime(
        backend_factory=lambda: backend,
        config=_config(),
        on_observation=observations.append,
    )

    assert runtime.submit(_pcm(10), sample_rate_hz=16_000, candidate=(1, 1))
    assert runtime.submit(_pcm(10), sample_rate_hz=16_000, candidate=(1, 1))
    assert runtime.submit(_pcm(10), sample_rate_hz=16_000, candidate=(1, 1))
    await runtime.wait_idle()

    assert backend.load_calls == 1
    assert len(backend.score_calls) == 1
    assert observations == [
        SpeakerShadowObservation(
            candidate=(1, 1),
            similarity=pytest.approx(0.2),
            would_block=True,
            audio_ms=20,
        )
    ]
    assert runtime.snapshot()["would_block_count"] == 1
    assert runtime.snapshot()["evaluated_candidate_count"] == 1
    await runtime.close()


async def test_shadow_is_disabled_without_loading_a_backend() -> None:
    backend = _Backend()
    runtime = SpeakerShadowRuntime(
        backend_factory=lambda: backend,
        config=SpeakerShadowConfig(enabled=False),
    )

    assert runtime.submit(_pcm(20), sample_rate_hz=16_000, candidate=(1, 1)) is False
    await runtime.close()

    assert backend.load_calls == 0
    assert runtime.snapshot()["submitted_frame_count"] == 0


async def test_shadow_failures_and_low_scores_never_change_detector_results() -> None:
    backend = _Backend(score_error=RuntimeError("model failed"))
    shadow = SpeakerShadowRuntime(
        backend_factory=lambda: backend,
        config=_config(minimum_audio_ms=10),
    )
    gate = _Gate()
    detector = DetectorRuntime(vad=_Vad(), gate=gate, speaker_shadow=shadow)

    first = await detector.feed(
        _pcm(10), speech_probability=0.9, rnnoise_available=True
    )
    await shadow.wait_idle()
    second = await detector.feed(
        _pcm(10), speech_probability=0.9, rnnoise_available=True
    )

    assert first.throttle_available is True
    assert second.throttle_available is True
    assert gate.inputs == [_pcm(10), _pcm(10)]
    assert shadow.snapshot()["inference_failure_count"] == 1
    await detector.close()


async def test_reset_invalidates_an_in_flight_shadow_result() -> None:
    backend = _BlockingBackend()
    observations: list[SpeakerShadowObservation] = []
    runtime = SpeakerShadowRuntime(
        backend_factory=lambda: backend,
        config=_config(minimum_audio_ms=10),
        on_observation=observations.append,
    )
    runtime.submit(_pcm(10), sample_rate_hz=16_000, candidate=(1, 1))
    assert await asyncio.to_thread(backend.score_started.wait, 1)

    await runtime.reset()
    backend.score_release.set()
    await runtime.wait_idle()

    assert observations == []
    assert runtime.snapshot()["stale_result_count"] == 1
    await runtime.close()


async def test_close_waits_for_inference_before_releasing_backend() -> None:
    backend = _BlockingBackend()
    runtime = SpeakerShadowRuntime(
        backend_factory=lambda: backend,
        config=_config(minimum_audio_ms=10),
    )
    runtime.submit(_pcm(10), sample_rate_hz=16_000, candidate=(1, 1))
    assert await asyncio.to_thread(backend.score_started.wait, 1)

    close_task = asyncio.create_task(runtime.close())
    await asyncio.sleep(0.02)
    assert close_task.done() is False
    assert backend.close_calls == 0

    backend.score_release.set()
    await asyncio.wait_for(close_task, 1)
    assert backend.close_calls == 1


async def test_load_and_callback_failures_stay_inside_shadow_metrics() -> None:
    unavailable = _Backend(load_ok=False)
    load_failed = SpeakerShadowRuntime(
        backend_factory=lambda: unavailable,
        config=_config(minimum_audio_ms=10),
    )
    load_failed.submit(_pcm(10), sample_rate_hz=16_000, candidate=(1, 1))
    await load_failed.wait_idle()
    assert load_failed.snapshot()["load_failure_count"] == 1
    await load_failed.close()

    def bad_callback(_observation: SpeakerShadowObservation) -> None:
        raise RuntimeError("observer failed")

    callback_failed = SpeakerShadowRuntime(
        backend_factory=lambda: _Backend(),
        config=_config(minimum_audio_ms=10),
        on_observation=bad_callback,
    )
    callback_failed.submit(_pcm(10), sample_rate_hz=16_000, candidate=(1, 1))
    await callback_failed.wait_idle()
    assert callback_failed.snapshot()["callback_failure_count"] == 1
    await callback_failed.close()


async def test_queue_backpressure_is_observational_only() -> None:
    backend = _Backend()
    runtime = SpeakerShadowRuntime(
        backend_factory=lambda: backend,
        config=_config(queue_capacity=1),
    )

    assert runtime.submit(_pcm(10), sample_rate_hz=16_000, candidate=(1, 1))
    assert runtime.submit(_pcm(10), sample_rate_hz=16_000, candidate=(1, 1)) is False
    await runtime.wait_idle()

    assert runtime.snapshot()["dropped_frame_count"] == 1
    await runtime.close()


async def test_idle_unload_releases_and_later_reloads_backend() -> None:
    backends: list[_Backend] = []

    def factory() -> _Backend:
        backend = _Backend()
        backends.append(backend)
        return backend

    runtime = SpeakerShadowRuntime(
        backend_factory=factory,
        config=_config(minimum_audio_ms=10, idle_unload_seconds=0.02),
    )
    runtime.submit(_pcm(10), sample_rate_hz=16_000, candidate=(1, 1))
    await runtime.wait_idle()
    await asyncio.sleep(0.05)
    runtime.submit(_pcm(10), sample_rate_hz=16_000, candidate=(1, 2))
    await runtime.wait_idle()

    assert len(backends) == 2
    assert backends[0].close_calls == 1
    assert runtime.snapshot()["load_count"] == 2
    assert runtime.snapshot()["unload_count"] == 1
    await runtime.close()


def test_shadow_config_rejects_unsafe_bounds() -> None:
    with pytest.raises(ValueError, match="similarity_threshold"):
        SpeakerShadowConfig(enabled=True, similarity_threshold=1.1)
    with pytest.raises(ValueError, match="maximum_audio_ms"):
        SpeakerShadowConfig(
            enabled=True, minimum_audio_ms=2_000, maximum_audio_ms=1_000
        )
