"""An unretired native encoder must never permit a replacement enrollment."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from main_logic.voice_identity.tse.assets import TseModelSnapshot
from main_logic.voice_identity.tse.worker import TseEncoderRetirementError
import main_logic.voice_identity.tse.worker as worker_module
from main_logic.voice_identity_service.service import VoiceIdentityServiceError
from .test_activity_enrollment import seed_previous_profile
from .test_extraction_enrollment import ExtractionModels, three_references
from .test_service import _service, _verification_pcm, _wait_until


@pytest.mark.asyncio
@pytest.mark.parametrize("late_failure", [False, True])
async def test_retirement_owner_blocks_new_enrollment_until_native_exit(tmp_path, monkeypatch, late_failure):
    service, _, _, _ = _service(tmp_path)
    original, profile = await seed_previous_profile(service)
    service._extraction_models = ExtractionModels(TseModelSnapshot(tmp_path / "model"))
    exited, publish_failure = asyncio.Event(), asyncio.Event()
    owner = SimpleNamespace(confirmed_stopped=False)

    async def confirm_exit():
        await exited.wait()
        owner.confirmed_stopped = True

    owner.retirement_task = asyncio.create_task(confirm_exit())
    failure = TseEncoderRetirementError(owner)

    async def extract(*args, **kwargs):
        if late_failure:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await publish_failure.wait()
        raise failure

    monkeypatch.setattr(worker_module, "extract_extraction_reference", extract)
    try:
        enrollment = await three_references(service)
        session = service._enrollment
        pcm_owners = list(session.extraction_pcm)
        if late_failure:
            service._model_timeout_seconds = 0.25
        with pytest.raises(VoiceIdentityServiceError, match="model_unavailable"):
            await service.submit_enrollment_segment(enrollment.enrollment_id, "new", 4, _verification_pcm())
        if late_failure:
            assert service._model_inference_cleanup_task is not None
            publish_failure.set()
            await _wait_until(lambda: service._extraction_retirement_owner is owner)
        assert service._extraction_retirement_owner is owner
        assert service._profile is profile
        assert service._profile_store.path.read_bytes() == original
        assert all(not pcm.any() for pcm in pcm_owners)
        with pytest.raises(VoiceIdentityServiceError, match="model_unavailable"):
            await service.start_enrollment()
        exited.set()
        await owner.retirement_task
        await _wait_until(lambda: service._extraction_retirement_owner is None)
        service._model_timeout_seconds = 1
        next_enrollment = await service.start_enrollment()
        await service.cancel_enrollment(next_enrollment.enrollment_id)
    finally:
        exited.set()
        publish_failure.set()
        await owner.retirement_task
        await service.close()


@pytest.mark.asyncio
async def test_cancelled_retirement_waiter_never_reopens_admission(tmp_path, monkeypatch):
    service, _, _, _ = _service(tmp_path)
    await service.initialize()
    service._extraction_models = ExtractionModels(TseModelSnapshot(tmp_path / "model"))
    owner = SimpleNamespace(confirmed_stopped=False)
    owner.retirement_task = asyncio.create_task(asyncio.Event().wait())

    async def extract(*args, **kwargs):
        raise TseEncoderRetirementError(owner)

    monkeypatch.setattr(worker_module, "extract_extraction_reference", extract)
    try:
        enrollment = await three_references(service)
        with pytest.raises(VoiceIdentityServiceError):
            await service.submit_enrollment_segment(enrollment.enrollment_id, "new", 4, _verification_pcm())
        owner.retirement_task.cancel()
        await asyncio.gather(owner.retirement_task, return_exceptions=True)
        await asyncio.sleep(0)
        assert service._extraction_retirement_owner is owner
        with pytest.raises(VoiceIdentityServiceError, match="model_unavailable"):
            await service.start_enrollment()
    finally:
        await service.close()
