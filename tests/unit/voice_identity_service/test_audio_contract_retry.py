"""Failed DSP coordination must remain retryable for the same setting."""

import pytest

from main_logic.voice_identity_service.service import VoiceIdentityServiceError
from main_logic.voice_identity_service.audio_contract import (
    OWNER_CAMPPLUS_DESKTOP_CONTRACT_ID,
)
from tests.support.voice_identity_fakes import _pcm, _service


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("already_matching", [False, True])
async def test_failed_audio_contract_reconcile_retries_same_setting(
    tmp_path, enabled, already_matching
):
    service, _model, activations, _events = _service(
        tmp_path, enrollment_noise_reduction_enabled=enabled
    )
    await service.initialize()
    try:
        enrollment = await service.start_enrollment()
        await service.complete_enrollment(enrollment.enrollment_id, "profile-a", _pcm())
        if not already_matching:
            assert await service.prepare_runtime_audio_contract_change(not enabled)
            await service.update_runtime_noise_reduction_enabled(not enabled)
        previous = service._runtime_noise_reduction_enabled
        assert await service.prepare_runtime_audio_contract_change(enabled)
        failed = await service.update_runtime_noise_reduction_enabled(
            enabled, runtime_ready=False
        )
        assert failed.state.effective_reason == "runtime_degraded"
        assert not failed.state.effective_enabled
        # A degraded DSP runtime keeps the compatible profile resident so a
        # same-value reconciliation can reactivate it after recovery.
        assert activations[-1][0] is not None
        assert service._runtime_noise_reduction_enabled is previous
        assert service._runtime_audio_contract_transition_pending

        with pytest.raises(VoiceIdentityServiceError, match="runtime_degraded"):
            await service.start_enrollment()

        assert await service.prepare_runtime_audio_contract_change(enabled)
        restored = await service.update_runtime_noise_reduction_enabled(
            enabled, runtime_ready=True
        )
        assert restored.state.effective_enabled
        assert restored.state.effective_reason == "ready"
        assert activations[-1][0] is not None
        assert service._runtime_noise_reduction_enabled is enabled
        assert not service._runtime_audio_contract_transition_pending
        count = len(activations)
        await service.update_runtime_noise_reduction_enabled(enabled)
        assert len(activations) == count
    finally:
        await service.close()

pytestmark = pytest.mark.runtime


@pytest.mark.asyncio
async def test_audio_contract_change_invalidates_active_enrollment(tmp_path):
    service, _model, _activations, _events = _service(tmp_path)
    await service.initialize()
    try:
        initial = await service.start_enrollment()
        await service.complete_enrollment(initial.enrollment_id, "profile-a", _pcm())
        await service.set_filter(True)
        enrollment = await service.start_enrollment()
        detach_observed_enrollment = []
        original_activate = service._activate

        async def observe_detach(profile, generation, **kwargs):
            if profile is None:
                current = service._enrollment
                detach_observed_enrollment.append(
                    current is not None
                    and current.enrollment_id == enrollment.enrollment_id
                )
            return await original_activate(profile, generation, **kwargs)

        service._activate = observe_detach
        assert await service.prepare_runtime_audio_contract_change(False)
        assert detach_observed_enrollment[0] is True
        assert service.status().enrollment is None
        with pytest.raises(VoiceIdentityServiceError, match="stale_enrollment"):
            await service.submit_enrollment_segment(
                enrollment.enrollment_id,
                "profile-a",
                1,
                _pcm(),
                sample_rate_hz=48_000,
                audio_contract_id=OWNER_CAMPPLUS_DESKTOP_CONTRACT_ID,
            )
    finally:
        await service.close()
