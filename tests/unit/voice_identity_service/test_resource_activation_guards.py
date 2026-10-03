"""Resource completion must preserve the saved identity's activation fences."""

from __future__ import annotations

import pytest
import asyncio

from main_logic.voice_identity.contracts import SpeakerModelIdentity
from main_logic.voice_identity.profile import SpeakerProfile
from main_logic.voice_identity.reference import SpeakerReference
from main_logic.voice_identity_service import resource_manager
from main_logic.voice_identity_service.audio_contract import desktop_audio_contract_snapshot
from tests.support.voice_identity_fakes import _embedding, _pcm, _service

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize("ready", [True, False])
async def test_cancelled_resource_refresh_publishes_actual_activation_state(tmp_path, monkeypatch, ready):
    service, *_ = _service(tmp_path)
    await service.initialize()
    enrollment = await service.start_enrollment()
    await service.complete_enrollment(enrollment.enrollment_id, "owner", _pcm())
    entered, finish = asyncio.Event(), asyncio.Event()
    async def activate(*args, **kwargs):
        entered.set()
        await finish.wait()
        return ready
    service._activation_callback = activate
    async def prepared(*args, **kwargs):
        return {}
    monkeypatch.setattr(resource_manager, "_run_worker", prepared)
    operation = service.start_resource_operation("prepare")
    await entered.wait()
    cancellation = asyncio.create_task(service.cancel_resource_operation(operation["operation_id"]))
    await asyncio.sleep(0)
    assert not cancellation.done()
    finish.set()
    try:
        assert (await cancellation)["state"] == "cancelled"
        state = service.status().state
        assert state.effective_enabled is ready
        assert state.effective_reason == ("ready" if ready else "runtime_degraded")
    finally:
        finish.set()
        await service.close()


@pytest.mark.parametrize(
    ("condition", "reason"),
    [
        ("no_profile", "no_profile"),
        ("incompatible", "profile_incompatible"),
        ("mismatch", "audio_contract_mismatch"),
        ("pending", "runtime_degraded"),
        ("off", "runtime_degraded"),
        ("disabled", "disabled"),
        ("valid", "ready"),
    ],
)
async def test_resource_completion_cannot_promote_ineligible_identity(
    tmp_path, monkeypatch, condition, reason,
):
    service, _, activations, _ = _service(tmp_path)
    await service.initialize()
    if condition == "no_profile":
        await service.set_filter(True)
    else:
        enrollment = await service.start_enrollment()
        await service.complete_enrollment(enrollment.enrollment_id, "owner", _pcm())
    original_profile = service._profile
    original_contract = service._profile_audio_contract
    profile_path = tmp_path / "voice_identity.profile"
    preference_path = tmp_path / "voice_identity.preference"
    if condition == "disabled":
        await service.set_filter(False)
    elif condition == "incompatible":
        reference = SpeakerReference(
            SpeakerModelIdentity("future-model", "future-revision", 192), _embedding(),
        )
        try:
            service._profile = SpeakerProfile("incompatible", reference)
        finally:
            reference.close()
    elif condition == "mismatch":
        service._profile_audio_contract = desktop_audio_contract_snapshot(
            noise_reduction_enabled=not service._runtime_noise_reduction_enabled,
        )
    elif condition == "pending":
        service._runtime_audio_contract_transition_pending = True
    elif condition == "off":
        service._runtime_mode = "off"
    before_profile = profile_path.read_bytes() if profile_path.exists() else None
    before_preference = preference_path.read_bytes()
    activations.clear()

    async def prepared(*args, **kwargs):
        return {}

    monkeypatch.setattr(resource_manager, "_run_worker", prepared)
    try:
        operation = service.start_resource_operation("prepare")
        await service._resource_manager._current.task
        assert service.resource_operation(operation["operation_id"])["state"] == "succeeded"
        assert len(activations) == 1
        candidate, generation = activations[0]
        assert candidate is (original_profile if condition == "valid" else None)
        assert generation
        status = service.status()
        assert status.state.effective_reason == reason
        assert status.state.effective_enabled is (condition == "valid")
        assert status.state.requested_enabled is (condition != "disabled")
        assert (profile_path.read_bytes() if profile_path.exists() else None) == before_profile
        assert preference_path.read_bytes() == before_preference
    finally:
        if condition == "incompatible":
            service._profile.close()
        service._profile = original_profile
        service._profile_audio_contract = original_contract
        await service.close()
