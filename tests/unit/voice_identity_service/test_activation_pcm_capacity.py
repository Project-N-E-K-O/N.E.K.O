"""Exercise the activation window through the real spawned scoring transport."""

from __future__ import annotations

import asyncio

import pytest

from main_logic.asr_client.speaker_shadow.contracts import (
    MAX_SPEAKER_BACKEND_PCM_BYTES,
    MAX_SPEAKER_SHADOW_CANDIDATE_PCM_BYTES,
)
from main_logic.asr_client.speaker_shadow.runtime import (
    _BackendHostError,
    _BackendProcessHost,
)
from main_logic.voice_identity_service import activation_scoring
from main_logic.voice_identity_service.activation_runtime import VoiceSessionActivationRuntime
from main_logic.voice_identity_service.activation_scoring import (
    ActivationScoreIdentity,
    ActivationScoreStatus,
    CampPlusActivationScorer,
)
from main_logic.voice_input.activation import (
    ActivationState,
    OutputCommit,
    VoiceActivationConfig,
    VoiceActivationController,
)
from tests.unit.voice_identity_service.test_activation_runtime import _frame, _generation
from tests.unit.voice_identity_service.test_activation_scoring import _profile


class _LengthBackend:
    def __init__(self, owner: bool) -> None:
        self.owner = owner

    def load(self) -> bool:
        return True

    def score(self, pcm16: bytes, sample_rate_hz: int) -> float:
        assert sample_rate_hz == 16_000
        # First checkpoint is deliberately inconclusive. Only the longer
        # second candidate can activate, so the 4.4-second IPC path must run.
        return 0.8 if self.owner and len(pcm16) >= 96_000 else 0.1

    def close(self) -> None:
        pass


class _LengthFactory:
    def __init__(self, owner: bool = True) -> None:
        self.owner = owner

    def __call__(self) -> _LengthBackend:
        return _LengthBackend(self.owner)

    def close(self) -> None:
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", [False, True])
async def test_short_pauses_preserve_full_scoring_and_replay(monkeypatch, owner) -> None:
    monkeypatch.setattr(
        activation_scoring, "CampPlusBackendFactory", lambda *a, **k: _LengthFactory(owner)
    )
    profile = _profile()
    scorer = CampPlusActivationScorer(profile, scorer_generation=1, timeout_seconds=10)
    delivered = []

    async def output(frame):
        delivered.append(frame)
        return OutputCommit.TRANSPORT_WRITTEN

    runtime = VoiceSessionActivationRuntime(
        _generation(), scorer, output,
        controller=VoiceActivationController(clock=lambda: 4.4),
    )
    try:
        await runtime.prepare()
        frames = []
        for sequence, voiced in enumerate(([True, True, False] * 15)[:-1]):
            frame = _frame(sequence)
            frames.append(frame)
            await runtime.feed(frame, voice_activity=voiced)
            if runtime._verification_task is not None:
                await runtime._verification_task
        for _ in range(20):
            await asyncio.sleep(0)
        assert runtime.state is (ActivationState.ACTIVE if owner else ActivationState.WAITING)
        assert scorer._host.alive
        # No silence removal, tail cropping, duplicate replay, or fail-open.
        assert delivered == (frames if owner else [])
    finally:
        await runtime.close()
        assert scorer._host is None
        profile.close()


@pytest.mark.asyncio
async def test_capacity_matches_activation_buffer_and_oversize_is_recoverable(monkeypatch):
    config = VoiceActivationConfig()
    assert MAX_SPEAKER_BACKEND_PCM_BYTES >= config.buffer_bytes
    assert MAX_SPEAKER_BACKEND_PCM_BYTES >= config.buffer_seconds * config.sample_rate * 2
    monkeypatch.setattr(
        activation_scoring, "CampPlusBackendFactory", lambda *a, **k: _LengthFactory()
    )
    profile = _profile()
    scorer = CampPlusActivationScorer(profile, scorer_generation=1, timeout_seconds=10)
    identity = ActivationScoreIdentity(profile.generation, 1, 1)
    try:
        for size in (128_000, 128_002, 256_000):
            result = await scorer.score(identity, bytes(size), sample_rate_hz=16_000)
            assert result.status is ActivationScoreStatus.READY
            assert scorer._host.pcm_bytes_in_use == 0
            assert not any(scorer._host._pcm_buffer)
        oversized = await scorer.score(identity, bytes(256_002), sample_rate_hz=16_000)
        assert oversized.status is ActivationScoreStatus.INVALID_AUDIO
        assert not scorer.closed
        assert scorer._host.alive
        recovered = await scorer.score(identity, bytes(48_000), sample_rate_hz=16_000)
        assert recovered.status is ActivationScoreStatus.READY
    finally:
        await scorer.close()
        profile.close()


@pytest.mark.asyncio
async def test_legacy_host_retains_four_second_limit():
    host = await asyncio.to_thread(
        _BackendProcessHost.create_started,
        factory=_LengthFactory(), terminate_timeout_seconds=0.25,
    )
    try:
        assert await host.load(timeout_seconds=10)
        assert len(host._pcm_buffer) == MAX_SPEAKER_SHADOW_CANDIDATE_PCM_BYTES
        with pytest.raises(_BackendHostError, match="exceeds host buffer"):
            await host.score(bytes(128_002), timeout_seconds=10)
        assert host.alive
        assert await host.score(bytes(96_000), timeout_seconds=10) == 0.8
    finally:
        await host.close(timeout_seconds=1)


@pytest.mark.parametrize("capacity", [0, -2, True, 3, 256_002])
def test_invalid_host_capacity_is_rejected_before_allocation(capacity):
    with pytest.raises(ValueError, match="capacity"):
        _BackendProcessHost(
            factory=_LengthFactory(), terminate_timeout_seconds=0.25,
            max_pcm_bytes=capacity,
        )
