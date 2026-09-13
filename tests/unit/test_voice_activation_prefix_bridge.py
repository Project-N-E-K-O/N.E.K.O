"""Authorized prefix ownership is local acceptance, never a wire receipt."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from main_logic.voice_input.activation import (
    ActivationState,
    OutputCommit,
    VerificationResultKind,
)
from main_logic.voice_turn.contracts import AsrSubmitResult, AsrSubmitStatus
from tests.unit.test_core_independent_asr import _CoreActivationFactory, _Runtime
from tests.unit.test_voice_session_activation import _frame, _generation, _waiting_controller


def test_local_acceptance_releases_exact_lease_without_claiming_wire() -> None:
    controller, generation = _waiting_controller(), _generation()
    frame = _frame(0, generation=generation)
    controller.ingest(frame, voice_activity=True)
    request = controller.request_verification(
        candidate_start_sequence=0, candidate_end_sequence=0
    ).verification_request
    assert request is not None
    assert controller.claim_verification_input(request) is not None
    controller.apply_verification_result(request, VerificationResultKind.OWNER)
    lease = controller.claim_output()
    assert lease is not None
    decision = controller.complete_output(lease, OutputCommit.LOCAL_ACCEPTED)
    assert decision.reason == "replay_handed_off"
    assert controller.pending_output_bytes == 0
    assert controller.claim_output() is None


def test_unknown_output_value_cannot_acknowledge_a_frame() -> None:
    controller, generation = _waiting_controller(), _generation()
    controller.ingest(_frame(0, generation=generation), voice_activity=True)
    request = controller.request_verification(
        candidate_start_sequence=0, candidate_end_sequence=0
    ).verification_request
    assert request is not None
    assert controller.claim_verification_input(request) is not None
    controller.apply_verification_result(request, VerificationResultKind.OWNER)
    lease = controller.claim_output()
    assert lease is not None
    decision = controller.complete_output(lease, "not-a-delivery-result")
    assert decision.state is ActivationState.UNAVAILABLE
    assert decision.reason == "output_delivery_invalid"


@pytest.mark.asyncio
async def test_core_marks_first_replay_frame_and_keeps_batch_on_live_tail() -> None:
    manager = _Runtime()
    manager._asr_route_mode = "independent"
    manager._asr_runtime.submit = AsyncMock(
        return_value=AsrSubmitResult(AsrSubmitStatus.ACCEPTED)
    )
    factory = _CoreActivationFactory()
    await manager.set_voice_session_activation_factory(
        factory, activation_generation="profile"
    )
    frames = [number.to_bytes(2, "little") * 1600 for number in range(1, 17)]
    try:
        for frame in frames[:15]:
            await manager._route_microphone_audio(
                frame, sample_rate_hz=16000, speech_probability=0.9,
                rnnoise_available=True,
            )
            await asyncio.sleep(0)
        async with asyncio.timeout(2):
            while manager._asr_runtime.submit.await_count < 15:
                await asyncio.sleep(0)
        await manager._route_microphone_audio(
            frames[15], sample_rate_hz=16000, speech_probability=0.9,
            rnnoise_available=True,
        )
        async with asyncio.timeout(2):
            while manager._asr_runtime.submit.await_count < 16:
                await asyncio.sleep(0)
        calls = manager._asr_runtime.submit.await_args_list
        assert [call.args[0].pcm16 for call in calls] == frames
        prefix = calls[0].kwargs["preserve_prefix"]
        assert prefix.start_sequence == 0
        assert all(call.kwargs["preserve_prefix"] is prefix for call in calls)
        assert all(call.kwargs["ingress_token"] == prefix.ingress for call in calls)
        assert factory.runtimes[0].state is ActivationState.ACTIVE
        assert manager._voice_session_activation_status[2] in {
            "replay_handed_off", "output_committed"
        }
    finally:
        await manager.set_voice_session_activation_factory(
            None, activation_generation="disabled"
        )
