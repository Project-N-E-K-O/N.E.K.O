"""Failed DSP coordination must remain retryable for the same setting."""

import pytest

from tests.unit.voice_identity_service.test_service import _pcm, _service


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
        assert activations[-1][0] is None
        assert service._runtime_noise_reduction_enabled is previous
        assert service._runtime_audio_contract_transition_pending

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
