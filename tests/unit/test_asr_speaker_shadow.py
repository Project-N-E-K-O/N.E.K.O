from __future__ import annotations

import asyncio
import threading
import time

import pytest

from main_logic.asr_client.detector_runtime import DetectorRuntime
from main_logic.asr_client.speaker_shadow import SpeakerShadowCandidateKey
from main_logic.voice_identity.contracts import (
    SpeakerShadowConfig,
    SpeakerShadowObservation,
)
from main_logic.voice_identity.runtime import SpeakerShadowRuntime


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
        self.score_finished = threading.Event()
        self.close_during_score = False

    def score(self, pcm16: bytes, sample_rate_hz: int) -> float:
        self.score_started.set()
        try:
            self.score_release.wait(timeout=2)
            return super().score(pcm16, sample_rate_hz)
        finally:
            self.score_finished.set()

    def close(self) -> None:
        if self.score_started.is_set() and not self.score_finished.is_set():
            self.close_during_score = True
        super().close()


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
        "similarity_thresholds": (0.40, 0.44, 0.48, 0.52, 0.55),
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
            would_block=(
                (0.40, True),
                (0.44, True),
                (0.48, True),
                (0.52, True),
                (0.55, True),
            ),
            audio_ms=20,
        )
    ]
    assert runtime.snapshot()["would_block_count"] == 1
    assert runtime.snapshot()["would_block_at_040_count"] == 1
    assert runtime.snapshot()["would_block_at_044_count"] == 1
    assert runtime.snapshot()["would_block_at_048_count"] == 1
    assert runtime.snapshot()["would_block_at_052_count"] == 1
    assert runtime.snapshot()["would_block_at_055_count"] == 1
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


async def test_shadow_accepts_negative_cosine_as_a_valid_observation() -> None:
    observations: list[SpeakerShadowObservation] = []
    runtime = SpeakerShadowRuntime(
        backend_factory=lambda: _Backend(score=-0.25),
        config=_config(minimum_audio_ms=10),
        on_observation=observations.append,
    )

    assert runtime.submit(_pcm(10), sample_rate_hz=16_000, candidate=(1, 2))
    await runtime.wait_idle()

    assert observations[0].similarity == pytest.approx(-0.25)
    assert all(blocked for _, blocked in observations[0].would_block)
    assert runtime.snapshot()["inference_failure_count"] == 0
    await runtime.close()


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
    runtime.submit(_pcm(10), sample_rate_hz=16_000, candidate=(2, 1))
    await runtime.wait_idle()
    assert [observation.candidate for observation in observations] == [(2, 1)]
    await runtime.close()


async def test_duplicate_finish_and_late_pcm_do_not_duplicate_observation() -> None:
    observations: list[SpeakerShadowObservation] = []
    backend = _Backend()
    runtime = SpeakerShadowRuntime(
        backend_factory=lambda: backend,
        config=_config(minimum_audio_ms=10),
        on_observation=observations.append,
    )
    candidate = SpeakerShadowCandidateKey(1, 1, "provider_pause")

    runtime.submit(_pcm(10), sample_rate_hz=16_000, candidate=candidate)
    runtime.finish_candidate(candidate)
    runtime.finish_candidate(candidate)
    runtime.submit(_pcm(10), sample_rate_hz=16_000, candidate=candidate)
    await runtime.wait_idle()

    metrics = runtime.snapshot()
    assert len(backend.score_calls) == 1
    assert len(observations) == 1
    assert metrics["evaluated_candidate_count"] == 1
    assert metrics["finished_candidate_count"] == 1
    assert metrics["insufficient_candidate_count"] == 0
    await runtime.close()


async def test_close_returns_within_grace_and_defers_backend_cleanup() -> None:
    backend = _BlockingBackend()
    runtime = SpeakerShadowRuntime(
        backend_factory=lambda: backend,
        config=_config(minimum_audio_ms=10, shutdown_grace_seconds=0.05),
    )
    runtime.submit(_pcm(10), sample_rate_hz=16_000, candidate=(1, 1))
    assert await asyncio.to_thread(backend.score_started.wait, 1)

    started = time.perf_counter()
    await runtime.close()
    elapsed = time.perf_counter() - started

    assert elapsed < 0.15
    assert backend.close_calls == 0
    assert runtime.snapshot()["shutdown_timeout_count"] == 1

    backend.score_release.set()
    async with asyncio.timeout(1):
        while backend.close_calls == 0:
            await asyncio.sleep(0)
    assert backend.close_calls == 1
    assert backend.close_during_score is False


async def test_wait_closed_joins_deferred_backend_cleanup() -> None:
    backend = _BlockingBackend()
    runtime = SpeakerShadowRuntime(
        backend_factory=lambda: backend,
        config=_config(minimum_audio_ms=10, shutdown_grace_seconds=0.05),
    )
    runtime.submit(_pcm(10), sample_rate_hz=16_000, candidate=(1, 1))
    assert await asyncio.to_thread(backend.score_started.wait, 1)

    await runtime.close()
    wait_task = asyncio.create_task(runtime.wait_closed())
    await asyncio.sleep(0)

    assert wait_task.done() is False
    assert backend.close_calls == 0

    backend.score_release.set()
    await asyncio.wait_for(wait_task, 1)

    assert backend.close_calls == 1
    assert backend.close_during_score is False


async def test_detector_close_is_not_blocked_by_shadow_inference() -> None:
    backend = _BlockingBackend()
    shadow = SpeakerShadowRuntime(
        backend_factory=lambda: backend,
        config=_config(minimum_audio_ms=10, shutdown_grace_seconds=0.05),
    )
    detector = DetectorRuntime(vad=_Vad(), gate=_Gate(), speaker_shadow=shadow)

    await detector.feed(_pcm(10), speech_probability=0.9, rnnoise_available=True)
    assert await asyncio.to_thread(backend.score_started.wait, 1)
    started = time.perf_counter()
    await detector.close()

    assert time.perf_counter() - started < 0.15
    assert backend.close_calls == 0
    backend.score_release.set()
    async with asyncio.timeout(1):
        while backend.close_calls == 0:
            await asyncio.sleep(0)
    assert backend.close_during_score is False


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


async def test_backend_load_retries_after_exponential_backoff(monkeypatch) -> None:
    from main_logic.voice_identity import runtime as speaker_shadow_module

    now = [100.0]
    monkeypatch.setattr(speaker_shadow_module.time, "monotonic", lambda: now[0])
    unavailable = _Backend(load_ok=False)
    available = _Backend()
    backends = iter((unavailable, available))
    runtime = SpeakerShadowRuntime(
        backend_factory=lambda: next(backends),
        config=_config(
            minimum_audio_ms=10,
            load_retry_initial_seconds=5.0,
            load_retry_max_seconds=60.0,
        ),
    )

    runtime.submit(_pcm(10), sample_rate_hz=16_000, candidate=(1, 1))
    await runtime.wait_idle()
    runtime.submit(_pcm(10), sample_rate_hz=16_000, candidate=(1, 2))
    await runtime.wait_idle()
    assert runtime.snapshot()["load_retry_suppressed_count"] == 1
    assert available.load_calls == 0

    now[0] += 5.0
    runtime.submit(_pcm(10), sample_rate_hz=16_000, candidate=(1, 3))
    await runtime.wait_idle()

    metrics = runtime.snapshot()
    assert unavailable.load_calls == 1
    assert available.load_calls == 1
    assert metrics["load_failure_count"] == 1
    assert metrics["load_count"] == 1
    assert metrics["evaluated_candidate_count"] == 1
    await runtime.close()


async def test_reset_clears_load_backoff_without_unloading_warm_backend(
    monkeypatch,
) -> None:
    from main_logic.voice_identity import runtime as speaker_shadow_module

    monkeypatch.setattr(speaker_shadow_module.time, "monotonic", lambda: 100.0)
    unavailable = _Backend(load_ok=False)
    available = _Backend()
    backends = iter((unavailable, available))
    runtime = SpeakerShadowRuntime(
        backend_factory=lambda: next(backends),
        config=_config(minimum_audio_ms=10),
    )

    runtime.submit(_pcm(10), sample_rate_hz=16_000, candidate=(1, 1))
    await runtime.wait_idle()
    await runtime.reset()
    runtime.submit(_pcm(10), sample_rate_hz=16_000, candidate=(2, 1))
    await runtime.wait_idle()
    await runtime.reset()

    assert unavailable.load_calls == 1
    assert available.load_calls == 1
    assert available.close_calls == 0
    await runtime.close()
    assert available.close_calls == 1


@pytest.mark.parametrize("callback_kind", ["sync", "async"])
async def test_observation_callback_timeout_does_not_stall_worker(
    callback_kind: str,
) -> None:
    release = threading.Event()
    async_release = asyncio.Event()

    def sync_callback(_observation: SpeakerShadowObservation) -> None:
        release.wait(timeout=1)

    async def async_callback(_observation: SpeakerShadowObservation) -> None:
        await async_release.wait()

    callback = sync_callback if callback_kind == "sync" else async_callback
    runtime = SpeakerShadowRuntime(
        backend_factory=lambda: _Backend(),
        config=_config(minimum_audio_ms=10, callback_timeout_seconds=0.02),
        on_observation=callback,
    )

    runtime.submit(_pcm(10), sample_rate_hz=16_000, candidate=(1, 1))
    await asyncio.wait_for(runtime.wait_idle(), 0.15)

    assert runtime.snapshot()["callback_failure_count"] == 1
    release.set()
    async_release.set()
    await runtime.close()


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


async def test_finished_short_candidate_releases_pcm_without_scoring() -> None:
    backend = _Backend()
    runtime = SpeakerShadowRuntime(
        backend_factory=lambda: backend,
        config=_config(minimum_audio_ms=20),
    )
    candidate = SpeakerShadowCandidateKey(1, 1, "provider_pause")

    assert runtime.submit(_pcm(10), sample_rate_hz=16_000, candidate=candidate)
    assert runtime.finish_candidate(candidate)
    await runtime.wait_idle()

    metrics = runtime.snapshot()
    assert backend.score_calls == []
    assert metrics["finished_candidate_count"] == 1
    assert metrics["insufficient_candidate_count"] == 1
    assert metrics["buffered_candidate_count"] == 0
    assert metrics["buffered_audio_bytes"] == 0
    await runtime.close()


async def test_scored_candidate_releases_pcm_and_rejects_late_frames() -> None:
    backend = _Backend(score_error=RuntimeError("score failed"))
    runtime = SpeakerShadowRuntime(
        backend_factory=lambda: backend,
        config=_config(minimum_audio_ms=10),
    )
    candidate = SpeakerShadowCandidateKey(1, 1, "provider_pause")

    assert runtime.submit(_pcm(10), sample_rate_hz=16_000, candidate=candidate)
    await runtime.wait_idle()
    assert runtime.submit(_pcm(10), sample_rate_hz=16_000, candidate=candidate)
    assert runtime.finish_candidate(candidate)
    await runtime.wait_idle()

    metrics = runtime.snapshot()
    assert len(backend.score_calls) == 1
    assert metrics["inference_failure_count"] == 1
    assert metrics["insufficient_candidate_count"] == 0
    assert metrics["buffered_candidate_count"] == 0
    assert metrics["buffered_audio_bytes"] == 0
    await runtime.close()


async def test_candidate_storage_stays_bounded_across_ten_thousand_finishes() -> None:
    backend = _Backend()
    runtime = SpeakerShadowRuntime(
        backend_factory=lambda: backend,
        config=_config(
            minimum_audio_ms=1,
            queue_capacity=32,
            finalized_candidate_capacity=1_024,
        ),
    )

    for start in range(0, 10_000, 8):
        for generation in range(start, start + 8):
            candidate = SpeakerShadowCandidateKey(1, generation, "provider_pause")
            assert runtime.submit(_pcm(1), sample_rate_hz=16_000, candidate=candidate)
            assert runtime.finish_candidate(candidate)
        await runtime.wait_idle()

    metrics = runtime.snapshot()
    assert len(backend.score_calls) == 10_000
    assert metrics["buffered_candidate_count"] == 0
    assert metrics["buffered_audio_bytes"] == 0
    assert metrics["finalized_tombstone_count"] <= 1_024
    assert metrics["finished_candidate_count"] == 10_000
    assert metrics["insufficient_candidate_count"] == 0
    await runtime.close()


async def test_unfinished_candidate_buffers_never_exceed_queue_capacity() -> None:
    runtime = SpeakerShadowRuntime(
        backend_factory=lambda: _Backend(),
        config=_config(
            minimum_audio_ms=20,
            queue_capacity=2,
            finalized_candidate_capacity=8,
        ),
    )

    for generation in range(10):
        candidate = SpeakerShadowCandidateKey(1, generation, "provider_pause")
        assert runtime.submit(_pcm(1), sample_rate_hz=16_000, candidate=candidate)
        await runtime.wait_idle()

    metrics = runtime.snapshot()
    assert metrics["buffered_candidate_count"] <= 2
    assert metrics["dropped_candidate_count"] == 8
    assert metrics["dropped_audio_ms"] == 8
    await runtime.close()


async def test_full_queue_finish_marker_invalidates_candidate_without_leaking() -> None:
    backend = _Backend()
    runtime = SpeakerShadowRuntime(
        backend_factory=lambda: backend,
        config=_config(queue_capacity=1, minimum_audio_ms=20),
    )
    candidate = SpeakerShadowCandidateKey(1, 1, "provider_pause")

    assert runtime.submit(_pcm(10), sample_rate_hz=16_000, candidate=candidate)
    assert runtime.finish_candidate(candidate) is False
    await runtime.wait_idle()

    metrics = runtime.snapshot()
    assert backend.score_calls == []
    assert metrics["dropped_candidate_count"] == 1
    assert metrics["buffered_candidate_count"] == 0
    assert metrics["finalized_tombstone_count"] == 1
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
    with pytest.raises(ValueError, match="similarity_thresholds"):
        SpeakerShadowConfig(enabled=True, similarity_thresholds=(0.4, 1.1))
    with pytest.raises(ValueError, match="similarity_thresholds"):
        SpeakerShadowConfig(enabled=True, similarity_thresholds=(0.44, 0.40))
    with pytest.raises(ValueError, match="maximum_audio_ms"):
        SpeakerShadowConfig(
            enabled=True, minimum_audio_ms=2_000, maximum_audio_ms=1_000
        )
    with pytest.raises(ValueError, match="finalized_candidate_capacity"):
        SpeakerShadowConfig(
            enabled=True,
            queue_capacity=32,
            finalized_candidate_capacity=16,
        )
    with pytest.raises(ValueError, match="load_retry_max_seconds"):
        SpeakerShadowConfig(
            load_retry_initial_seconds=10,
            load_retry_max_seconds=5,
        )
    with pytest.raises(ValueError, match="shutdown_grace_seconds"):
        SpeakerShadowConfig(shutdown_grace_seconds=0)
    with pytest.raises(ValueError, match="callback_timeout_seconds"):
        SpeakerShadowConfig(callback_timeout_seconds=0)
