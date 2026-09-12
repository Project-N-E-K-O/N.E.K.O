"""Live PCM must satisfy the enrolled DSP contract before Owner scoring."""

import asyncio
import json

import pytest

from main_logic.voice_input.activation import ActivationState
from main_logic.voice_turn.audio_input import VoiceInputAudioPipeline
from tests.unit.test_voice_activation_handoff import _Factory, _harness, _until
from utils import audio_processor


pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_contract_failure_blocks_frame_already_waiting_for_activation_lock(route):
    async with _harness(route, active=False) as h:
        h.factory.noise_reduction_enabled = True
        lock = h.manager._voice_session_activation_lock
        await lock.acquire()
        pending = asyncio.create_task(h.manager._route_microphone_audio(
            b"\x34\x12" * 1600, sample_rate_hz=16000,
            rnnoise_available=True, speech_probability=0.9,
        ))
        try:
            await asyncio.sleep(0)
            assert not pending.done()
            await h.manager._route_microphone_audio(
                b"\x56\x12" * 1600, sample_rate_hz=16000,
                rnnoise_available=False, speech_probability=0.9,
            )
        finally:
            lock.release()
        await pending
        assert h.manager._voice_session_activation_runtime is None
        assert len(h.factory.runtimes) == 1
        assert h.deliveries == []


@pytest.mark.parametrize("route", ["native", "independent"])
@pytest.mark.parametrize("required", [True, False])
async def test_missing_native_rnnoise_respects_profile_contract(monkeypatch, route, required):
    monkeypatch.setattr(audio_processor, "_get_rnnoise", lambda: None)
    async with _harness(route, active=False) as h:
        factory = _Factory(h.clock)
        factory.noise_reduction_enabled = required
        h.manager._voice_input_noise_reduction_enabled = required
        await h.manager.set_voice_session_activation_factory(
            factory, activation_generation="profile", activation_required=True
        )
        pipeline = VoiceInputAudioPipeline(nr_enabled=required)
        try:
            for index in range(30):
                h.clock.value += 0.1
                pcm = (3000 + index).to_bytes(2, "little", signed=True) * 4800
                frame = await pipeline.process(pcm, sample_rate_hz=48000)
                assert not frame.rnnoise_available
                if not frame.pcm16:
                    continue
                await h.manager._route_microphone_audio(
                    frame.pcm16, sample_rate_hz=frame.sample_rate_hz,
                    speech_probability=frame.speech_probability,
                    rnnoise_available=frame.rnnoise_available,
                    rnnoise_evidence=frame.rnnoise_evidence,
                    received_at=h.clock.value, captured_at=h.clock.value,
                )
                await asyncio.sleep(0)
            if required:
                assert factory.runtimes == []
                assert factory.scorers == []
                assert h.deliveries == []
                assert h.manager._voice_session_activation_degraded
                await _until(lambda: any(
                    json.loads(call.args[0]).get("details", {}).get("state") == "unavailable"
                    for call in h.manager.send_status.await_args_list
                ))
            else:
                await _until(lambda: bool(h.deliveries))
                assert factory.runtimes[0].state is ActivationState.ACTIVE
                assert factory.scorers[0].calls == 1
        finally:
            await pipeline.close()


@pytest.mark.parametrize("route", ["native", "independent"])
@pytest.mark.parametrize("available", [False, None])
async def test_lost_rnnoise_revokes_active_authority_until_explicit_retry(route, available):
    async with _harness(route) as h:
        h.factory.noise_reduction_enabled = True
        old_runtime = h.activation
        original = h.pcm.copy()
        h.clock.value += 0.1
        bad_pcm = b"\x34\x12" * 1600
        await h.manager._route_microphone_audio(
            bad_pcm, sample_rate_hz=16000, rnnoise_available=available,
            speech_probability=0.9, received_at=h.clock.value,
        )
        assert h.manager._voice_session_activation_degraded
        assert h.manager._voice_session_activation_runtime is None
        await _until(lambda: old_runtime.state is ActivationState.CLOSED)
        await h.feed(4000)
        assert h.pcm == original
        replacement = _Factory(h.clock)
        replacement.noise_reduction_enabled = True
        await h.manager.set_voice_session_activation_factory(
            replacement, activation_generation="profile", activation_required=True
        )
        expected = []
        for index in range(15):
            h.clock.value += 0.1
            pcm = (5000 + index).to_bytes(2, "little", signed=True) * 1600
            expected.append(pcm)
            await h.manager._route_microphone_audio(
                pcm, sample_rate_hz=16000, rnnoise_available=True,
                speech_probability=0.9, received_at=h.clock.value,
            )
            await asyncio.sleep(0)
        await _until(lambda: h.pcm == original + expected)
        assert replacement.runtimes[0].state is ActivationState.ACTIVE
        assert bad_pcm not in h.pcm


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_contract_failure_cannot_publish_late_verification_or_reopen_disabled_input(route):
    async with _harness(route, active=False) as h:
        h.factory.noise_reduction_enabled = True
        scorer = h.factory.scorers[0]
        entered, release = asyncio.Event(), asyncio.Event()
        original_score = scorer.score

        async def delayed_score(*args, **kwargs):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return await original_score(*args, **kwargs)

        scorer.score = delayed_score
        try:
            for _ in range(14):
                h.clock.value += 0.1
                await h.manager._route_microphone_audio(
                    b"\x34\x12" * 1600, sample_rate_hz=16000,
                    rnnoise_available=True, speech_probability=0.9,
                    received_at=h.clock.value,
                )
                await asyncio.sleep(0)
            await _until(entered.is_set)
            await h.manager._route_microphone_audio(
                b"\x56\x12" * 1600, sample_rate_hz=16000,
                rnnoise_available=False, speech_probability=0.9,
            )
            assert h.manager._voice_session_activation_degraded
            await h.manager.set_voice_session_activation_factory(
                None, activation_generation="explicitly-disabled"
            )
            release.set()
            await _until(lambda: h.activation.state is ActivationState.CLOSED)
            assert not h.manager._voice_session_activation_degraded
            assert h.deliveries == []
            await h.feed(6000)
            assert len(h.deliveries) == 1
        finally:
            release.set()
