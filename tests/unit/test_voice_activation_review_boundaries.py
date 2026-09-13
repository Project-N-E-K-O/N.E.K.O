"""Regressions for retained candidates and non-enforcing microphone routing."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from main_logic.voice_identity_service.activation_runtime import VoiceSessionActivationRuntime
from main_logic.voice_identity_service.session_activation_factory import OwnerVoiceSessionActivationFactory
from main_logic.voice_input.activation import ActivationState, OutputCommit, VoiceActivationController
from main_logic.voice_turn.contracts import AsrSubmitResult, AsrSubmitStatus
from tests.unit.test_core_independent_asr import _Runtime
from tests.unit.voice_identity_service.test_activation_runtime import _Scorer, _frame, _generation
from tests.unit.voice_identity_service.test_session_activation_factory import _profile


@pytest.mark.asyncio
async def test_cold_load_eviction_restarts_candidate_without_silence():
    sent = []
    scored = []

    class Scorer(_Scorer):
        async def score(self, identity, pcm16, *, sample_rate_hz):
            scored.append(pcm16)
            return await super().score(identity, pcm16, sample_rate_hz=sample_rate_hz)

    async def output(frame):
        sent.append(frame)
        return OutputCommit.TRANSPORT_WRITTEN

    controller = VoiceActivationController(clock=lambda: 11.7)
    runtime = VoiceSessionActivationRuntime(_generation(), Scorer(), output, controller=controller)
    try:
        # A 10-second cold preparation exceeds the unchanged 8-second cache.
        for sequence in range(100):
            await runtime.feed(_frame(sequence), voice_activity=True)
        assert controller.buffered_bytes <= 256_000
        assert not scored and not sent
        await runtime.prepare()
        for sequence in range(100, 117):
            await runtime.feed(_frame(sequence), voice_activity=True)
        async with asyncio.timeout(1):
            while runtime.state is not ActivationState.ACTIVE:
                await asyncio.sleep(0)
        assert len(scored) == 1
        assert scored[0] == b"".join(_frame(n).pcm for n in range(101, 116))
        sequences = [frame.sequence for frame in sent]
        assert sequences == list(range(sequences[0], 117))
        assert sequences[0] >= 97  # retained candidate plus bounded pre-roll
        assert controller.pending_output_bytes == 0
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["independent", "native"])
async def test_shadow_uses_ordinary_delivery_after_transient_rejection(route):
    manager = _Runtime()
    manager._asr_route_mode = route
    manager._asr_runtime.submit = AsyncMock(side_effect=[
        AsrSubmitResult(AsrSubmitStatus.STALE),
        AsrSubmitResult(AsrSubmitStatus.ACCEPTED),
    ])
    manager.session.stream_audio = AsyncMock(side_effect=[False, True])
    profile = _profile()
    factory = OwnerVoiceSessionActivationFactory(
        object(), profile, activation_generation="shadow", enforce=False,
    )
    profile.close()
    await manager.set_voice_session_activation_factory(factory, activation_generation="shadow")
    try:
        for sequence in range(2):
            await manager._route_microphone_audio(
                _frame(sequence).pcm, sample_rate_hz=16000,
                speech_probability=0.9, rnnoise_available=True,
            )
            await asyncio.sleep(0)
        if route == "independent":
            calls = manager._asr_runtime.submit.await_args_list
            assert len(calls) == 2
            assert all(call.kwargs.get("preserve_prefix") is None for call in calls)
        else:
            assert manager.session.stream_audio.await_count == 2
        assert manager._voice_session_activation_runtime is None
        assert getattr(manager, "_voice_activation_delivery_prefix", None) is None
        assert not manager._voice_session_activation_degraded
    finally:
        await manager.set_voice_session_activation_factory(None, activation_generation="disabled")
