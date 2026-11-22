"""Failed DSP coordination must remain retryable for the same setting."""

import pytest

from main_logic.voice_identity_service.service import VoiceIdentityServiceError
from main_logic.voice_identity_service.audio_contract import (
    OWNER_CAMPPLUS_DESKTOP_CONTRACT_ID,
)
from tests.support.voice_identity_fakes import _pcm, _service, _verification_pcm


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


@pytest.mark.asyncio
async def test_audio_contract_change_invalidates_initial_enrollment_without_profile(
    tmp_path,
):
    """F0593: toggling noise reduction during initial enrollment must abort stale enrollment."""
    service, _model, _activations, events = _service(tmp_path)
    await service.initialize()
    try:
        assert service._profile is None
        assert service._requested_enabled is False
        enrollment = await service.start_enrollment()
        assert await service.prepare_runtime_audio_contract_change(False)
        assert service.status().enrollment is None
        assert service._runtime_audio_contract_transition_pending is True
        assert any(event.startswith("restore:") for event in events)

        with pytest.raises(VoiceIdentityServiceError, match="stale_enrollment"):
            await service.submit_enrollment_segment(
                enrollment.enrollment_id,
                "profile-a",
                1,
                _pcm(),
                sample_rate_hz=48_000,
                audio_contract_id=OWNER_CAMPPLUS_DESKTOP_CONTRACT_ID,
            )

        settled = await service.update_runtime_noise_reduction_enabled(
            False, runtime_ready=True
        )
        assert settled.state.effective_reason == "disabled"
        assert service._runtime_noise_reduction_enabled is False
        assert service._runtime_audio_contract_transition_pending is False

        fresh = await service.start_enrollment()
        completed = await service.complete_enrollment(
            fresh.enrollment_id, "profile-a", _pcm()
        )
        assert completed.state.effective_enabled is True
        assert completed.state.effective_reason == "ready"
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_reconcile_rejects_runtime_ready_mutation_while_enrollment_active(
    tmp_path,
):
    """F0593: direct runtime_ready=True update must reject active enrollment and preserve contract."""
    service, _model, _activations, _events = _service(tmp_path)
    await service.initialize()
    try:
        enrollment = await service.start_enrollment()
        with pytest.raises(VoiceIdentityServiceError, match="audio_contract_changed"):
            await service.update_runtime_noise_reduction_enabled(
                False, runtime_ready=True
            )
        assert service._runtime_noise_reduction_enabled is True
        assert service._runtime_audio_contract_transition_pending is True
        assert service.status().state.effective_reason == "runtime_degraded"

        # When runtime_ready=False (e.g. failed detach holding lease), degrade without throwing.
        degraded = await service.update_runtime_noise_reduction_enabled(
            False, runtime_ready=False
        )
        assert degraded.state.effective_reason == "runtime_degraded"
        assert service._runtime_noise_reduction_enabled is True
        assert service._runtime_audio_contract_transition_pending is True
        assert service._enrollment is not None
        assert enrollment.enrollment_id == service._enrollment.enrollment_id

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


@pytest.mark.asyncio
async def test_preferences_noise_reduction_toggle_protects_initial_enrollment(
    tmp_path, monkeypatch
):
    """F0593: full preferences _apply_noise_reduction_if_current flow during initial enrollment."""
    import asyncio
    from types import SimpleNamespace
    import main_routers.config_router.preferences as preferences

    service, _model, _activations, _events = _service(tmp_path)
    await service.initialize()

    async def snapshot(*, strict: bool = False):
        return SimpleNamespace(revision=1, settings={"noiseReductionEnabled": False})

    async def apply_sessions(enabled: bool) -> bool:
        return True

    async def prepare(enabled: bool) -> bool:
        return await service.prepare_runtime_audio_contract_change(enabled)

    async def reconcile(enabled: bool, *, runtime_ready: bool) -> None:
        await service.update_runtime_noise_reduction_enabled(
            enabled, runtime_ready=runtime_ready
        )

    monkeypatch.setattr(
        preferences, "aload_global_conversation_settings_snapshot", snapshot
    )
    monkeypatch.setattr(
        preferences, "_apply_noise_reduction_to_active_sessions", apply_sessions
    )
    monkeypatch.setattr(preferences, "_NOISE_REDUCTION_APPLY_LOCK", asyncio.Lock())
    preferences.configure_voice_identity_audio_contract_callbacks(
        prepare=prepare, reconcile=reconcile
    )
    try:
        enrollment = await service.start_enrollment()
        await preferences._apply_noise_reduction_if_current(False)
        assert service.status().enrollment is None
        assert service._runtime_noise_reduction_enabled is False
        assert not service._runtime_audio_contract_transition_pending

        with pytest.raises(VoiceIdentityServiceError, match="stale_enrollment"):
            await service.submit_enrollment_segment(
                enrollment.enrollment_id,
                "profile-a",
                1,
                _pcm(),
                sample_rate_hz=48_000,
                audio_contract_id=OWNER_CAMPPLUS_DESKTOP_CONTRACT_ID,
            )
        fresh = await service.start_enrollment()
        done = await service.complete_enrollment(fresh.enrollment_id, "profile-a", _pcm())
        assert done.state.effective_enabled is True
        assert done.state.effective_reason == "ready"
    finally:
        preferences.configure_voice_identity_audio_contract_callbacks()
        await service.close()


@pytest.mark.asyncio
async def test_f0593_inflight_segment_during_direct_reconcile_race(tmp_path):
    """F0593: in-flight segment 1 must raise stale_enrollment when direct reconcile sets transition_pending."""
    import asyncio

    service, _model, _activations, _events = _service(tmp_path)
    await service.initialize()
    try:
        enrollment = await service.start_enrollment()
        entered = asyncio.Event()
        release = asyncio.Event()
        original_compute = service._compute_enrollment_segment

        async def gated_compute(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original_compute(*args, **kwargs)

        service._compute_enrollment_segment = gated_compute
        seg_task = asyncio.create_task(
            service.submit_enrollment_segment(
                enrollment.enrollment_id,
                "profile-a",
                1,
                _pcm(),
                sample_rate_hz=48_000,
                audio_contract_id=OWNER_CAMPPLUS_DESKTOP_CONTRACT_ID,
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=2.0)

        with pytest.raises(VoiceIdentityServiceError, match="audio_contract_changed"):
            await service.update_runtime_noise_reduction_enabled(
                False, runtime_ready=True
            )
        assert service._runtime_audio_contract_transition_pending is True

        release.set()
        with pytest.raises(VoiceIdentityServiceError, match="stale_enrollment"):
            await seg_task
        assert service._enrollment is not None
        assert service._enrollment.in_flight_segment_index is None
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_f0593_inflight_seg4_commits_stale_profile_during_direct_reconcile_race(
    tmp_path,
):
    """F0593: in-flight segment 4 must not commit/activate a stale profile after direct reconcile rejects."""
    import asyncio

    service, _model, _activations, _events = _service(tmp_path)
    await service.initialize()
    try:
        enrollment = await service.start_enrollment()
        for segment_index in (1, 2, 3):
            await service.submit_enrollment_segment(
                enrollment.enrollment_id,
                "profile-a",
                segment_index,
                _pcm(),
                sample_rate_hz=48_000,
                audio_contract_id=OWNER_CAMPPLUS_DESKTOP_CONTRACT_ID,
            )

        entered = asyncio.Event()
        release = asyncio.Event()
        original_compute = service._compute_enrollment_segment

        async def gated_compute(*args, **kwargs):
            result = await original_compute(*args, **kwargs)
            entered.set()
            await release.wait()
            return result

        service._compute_enrollment_segment = gated_compute
        seg4_task = asyncio.create_task(
            service.submit_enrollment_segment(
                enrollment.enrollment_id,
                "profile-a",
                4,
                _verification_pcm(),
                sample_rate_hz=48_000,
                audio_contract_id=OWNER_CAMPPLUS_DESKTOP_CONTRACT_ID,
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=2.0)

        with pytest.raises(VoiceIdentityServiceError, match="audio_contract_changed"):
            await service.update_runtime_noise_reduction_enabled(
                False, runtime_ready=True
            )
        assert service._runtime_audio_contract_transition_pending is True

        release.set()
        with pytest.raises(VoiceIdentityServiceError, match="stale_enrollment"):
            await seg4_task

        status = service.status()
        assert service._profile is None
        assert service._runtime_audio_contract_transition_pending is True
        assert status.state.effective_enabled is False
        assert status.state.effective_reason == "runtime_degraded"
    finally:
        await service.close()
