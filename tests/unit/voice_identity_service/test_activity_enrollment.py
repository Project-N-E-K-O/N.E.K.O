from __future__ import annotations

import asyncio
from pathlib import Path
import threading
from unittest.mock import AsyncMock

import numpy as np
import pytest

from main_logic.voice_identity.contracts import SpeakerModelIdentity
from main_logic.voice_identity.pvad.assets import EcapaModelSnapshot, ECAPA_IDENTITY
from main_logic.voice_identity.reference import SpeakerReference
from main_logic.asr_client import VoiceIdentityActivationResult
import main_logic.voice_identity_service.profile_store as store_module
from main_logic.voice_identity_service.profile_store import VoiceIdentityProfileStoreError
import main_logic.voice_identity_service.service as service_module
from main_logic.voice_identity_service.service import VoiceIdentityServiceError
from .test_service import _embedding, _Model, _pcm, _service, _verification_pcm, _wait_until


class ActivityModels:
    def __init__(self, snapshot: EcapaModelSnapshot | None) -> None:
        self.current = snapshot
        self.started = 0
        self.closed = False

    def snapshot(self):
        return None if self.closed else self.current

    def status(self):
        return {"state": "ready" if self.snapshot() is not None else "missing"}

    async def initialize(self):
        pass

    def start(self):
        self.started += 1

    async def close(self):
        self.closed = True


def activity_reference() -> SpeakerReference:
    return SpeakerReference(ECAPA_IDENTITY, _embedding())


async def reference_segments(service, *, profile_id="new"):
    enrollment = await service.start_enrollment()
    for index in (1, 2, 3):
        await service.submit_enrollment_segment(enrollment.enrollment_id, profile_id, index, _pcm())
    return enrollment


async def seed_previous_profile(service):
    await service.initialize()
    enrollment = await service.start_enrollment()
    await service.complete_enrollment(enrollment.enrollment_id, "old", _pcm())
    return service._profile_store.path.read_bytes(), service._profile


@pytest.mark.unit
@pytest.mark.asyncio
async def test_download_is_explicit_and_rejected_during_enrollment(tmp_path: Path) -> None:
    service, _, _, _ = _service(tmp_path)
    models = ActivityModels(None)
    service._activity_models = models
    try:
        await service.initialize()
        assert service.status().short_speech == {"state": "missing", "profile_ready": False}
        assert models.started == 0
        await service.download_activity_model()
        assert models.started == 1
        enrollment = await service.start_enrollment()
        with pytest.raises(VoiceIdentityServiceError, match="enrollment_active"):
            await service.download_activity_model()
        assert models.started == 1
        await service.cancel_enrollment(enrollment.enrollment_id)
        await service.download_activity_model()
        assert models.started == 2
    finally:
        await service.close()
    assert models.closed
    with pytest.raises(VoiceIdentityServiceError):
        await service.download_activity_model()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_download_unavailable_does_not_change_existing_profile(tmp_path: Path) -> None:
    service, _, _, _ = _service(tmp_path)
    original, profile = await seed_previous_profile(service)
    try:
        with pytest.raises(VoiceIdentityServiceError, match="model_unavailable"):
            await service.download_activity_model()
        assert service._profile is profile
        assert service._profile_store.path.read_bytes() == original
    finally:
        await service.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_download_start_failure_maps_safe_error_and_keeps_old_state(tmp_path: Path) -> None:
    service, _, _, _ = _service(tmp_path)
    original, profile = await seed_previous_profile(service)
    models = ActivityModels(None)

    def fail_start():
        raise RuntimeError("private filesystem detail")

    models.start = fail_start
    service._activity_models = models
    try:
        with pytest.raises(VoiceIdentityServiceError, match="^model_unavailable$"):
            await service.download_activity_model()
        assert service._profile is profile
        assert service._profile_store.path.read_bytes() == original
        assert service.status().state.effective_enabled
    finally:
        await service.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_download_close_failure_still_cleans_enrollment_profile_and_suppression(tmp_path: Path) -> None:
    service, model, activations, events = _service(tmp_path)
    original, profile = await seed_previous_profile(service)
    models = ActivityModels(None)
    models.close = AsyncMock(side_effect=OSError("private path"))
    service._activity_models = models
    await service.start_enrollment()
    with pytest.raises(VoiceIdentityServiceError, match="^model_unavailable$"):
        await service.close()
    assert model.closed
    assert service._enrollment is None
    assert service._profile is None
    assert profile.closed
    assert service._profile_store.path.read_bytes() == original
    assert activations[-1][0] is None
    assert events[-1] == "restore:voice_identity_enrollment"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_download_finishing_during_start_does_not_change_frozen_protocol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    started, release = threading.Event(), threading.Event()

    class SlowModel(_Model):
        def load(self):
            started.set()
            return release.wait(2)

    service, _, _, _ = _service(tmp_path, model=SlowModel())
    models = ActivityModels(None)
    service._activity_models = models
    extraction = AsyncMock(side_effect=AssertionError("must not upgrade in flight"))
    monkeypatch.setattr(service_module, "extract_activity_reference", extraction)
    await service.initialize()
    start = asyncio.create_task(service.start_enrollment())
    try:
        await _wait_until(started.is_set)
        models.current = EcapaModelSnapshot(tmp_path / "downloaded-after-start")
        release.set()
        enrollment = await start
        assert service._enrollment.activity_model_snapshot is None
        result = await service.complete_enrollment(enrollment.enrollment_id, "cam-only", _pcm())
        assert result.profile_generation == "cam-only"
        assert not service._profile.has_activity_reference
        extraction.assert_not_awaited()
    finally:
        release.set()
        await service.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_enrollment_uses_start_snapshot_and_commits_both_references_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _, _ = _service(tmp_path)
    snapshot = EcapaModelSnapshot(tmp_path / "first-immutable-bundle")
    models = ActivityModels(snapshot)
    service._activity_models = models
    extracted = activity_reference()
    extraction = AsyncMock(return_value=extracted)
    monkeypatch.setattr(service_module, "extract_activity_reference", extraction)
    try:
        await service.initialize()
        enrollment = await reference_segments(service)
        models.current = EcapaModelSnapshot(tmp_path / "later-bundle")
        status = await service.submit_enrollment_segment(
            enrollment.enrollment_id, "new", 4, _verification_pcm(),
        )
        assert status.verification.passed
        assert status.short_speech["profile_ready"] is True
        extraction.assert_awaited_once()
        args = extraction.await_args
        assert args.args[0] == snapshot.directory
        assert len(args.args[1]) == 160_000
        assert args.kwargs["timeout"] <= service._model_timeout_seconds
        assert extracted.closed
        stored = service._profile_store.load()
        assert stored is not None
        try:
            assert stored.profile.generation == "new"
            assert stored.profile.has_activity_reference
            assert stored.profile.activity_reference_contract.resource_revision == snapshot.resource_revision
            assert stored.profile.activity_reference_contract.reference_method == snapshot.reference_method
            assert stored.profile.activity_reference_contract.noise_reduction_enabled is True
        finally:
            stored.close()
        await service.submit_enrollment_segment(enrollment.enrollment_id, "new", 4, _verification_pcm())
        extraction.assert_awaited_once()
    finally:
        await service.close()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["model", "timeout", "identity"])
async def test_required_activity_failure_preserves_old_profile_and_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    service, _, _, _ = _service(tmp_path)
    original, profile = await seed_previous_profile(service)
    service._activity_models = ActivityModels(EcapaModelSnapshot(tmp_path / "model"))
    wrong_reference = SpeakerReference(SpeakerModelIdentity("wrong", "wrong", 192), _embedding())
    extraction = AsyncMock(
        return_value=wrong_reference,
        side_effect=(RuntimeError("ecapa failed") if failure == "model" else TimeoutError() if failure == "timeout" else None),
    )
    monkeypatch.setattr(service_module, "extract_activity_reference", extraction)
    try:
        enrollment = await reference_segments(service)
        with pytest.raises(VoiceIdentityServiceError, match="model_unavailable"):
            await service.submit_enrollment_segment(enrollment.enrollment_id, "new", 4, _verification_pcm())
        assert service._enrollment is None
        assert service._profile is profile
        assert not profile.closed
        assert service._profile_store.path.read_bytes() == original
        assert service.status().state.effective_enabled
        assert service.status().profile_generation == "old"
        if failure == "identity":
            assert wrong_reference.closed
    finally:
        wrong_reference.close()
        await service.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failed_camplus_holdout_does_not_attempt_activity_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, model, _, _ = _service(tmp_path)
    original, profile = await seed_previous_profile(service)
    service._activity_models = ActivityModels(EcapaModelSnapshot(tmp_path / "model"))
    extraction = AsyncMock(side_effect=AssertionError("holdout rejected"))
    monkeypatch.setattr(service_module, "extract_activity_reference", extraction)
    try:
        enrollment = await reference_segments(service)
        model.embeddings = [-_embedding(), -_embedding(), -_embedding()]
        status = await service.submit_enrollment_segment(enrollment.enrollment_id, "new", 4, _verification_pcm())
        assert not status.verification.passed
        extraction.assert_not_awaited()
        assert service._profile is profile
        assert service._profile_store.path.read_bytes() == original
    finally:
        await service.close()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("retirement", ["cancel", "expiry", "delete", "close", "caller"])
async def test_activity_worker_is_cancelled_for_each_session_retirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, retirement: str,
) -> None:
    service, _, _, _ = _service(tmp_path)
    original, _ = await seed_previous_profile(service)
    service._activity_models = ActivityModels(EcapaModelSnapshot(tmp_path / "model"))
    started, stopped = asyncio.Event(), asyncio.Event()

    async def extracting(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(service_module, "extract_activity_reference", extracting)
    enrollment = await reference_segments(service)
    submit = asyncio.create_task(service.submit_enrollment_segment(
        enrollment.enrollment_id, "new", 4, _verification_pcm(),
    ))
    try:
        await started.wait()
        if retirement == "cancel":
            await service.cancel_enrollment(enrollment.enrollment_id)
        elif retirement == "expiry":
            await service._expire_enrollment(enrollment.enrollment_id, 0)
        elif retirement == "delete":
            await service.delete_profile()
        elif retirement == "close":
            await service.close()
        else:
            submit.cancel()
        if retirement == "caller":
            with pytest.raises(asyncio.CancelledError):
                await submit
        else:
            with pytest.raises(VoiceIdentityServiceError, match="stale_enrollment"):
                await submit
        assert stopped.is_set()
        assert service._enrollment is None
        if retirement == "delete":
            assert not service._profile_store.path.exists()
        else:
            assert service._profile_store.path.read_bytes() == original
    finally:
        await service.close()
        if not submit.done():
            submit.cancel()
            await asyncio.gather(submit, return_exceptions=True)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_late_activity_result_is_wiped_and_cannot_retire_new_enrollment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _, _ = _service(tmp_path)
    original, _ = await seed_previous_profile(service)
    service._activity_models = ActivityModels(EcapaModelSnapshot(tmp_path / "model"))
    started = asyncio.Event()
    late = activity_reference()

    async def extracting(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return late

    monkeypatch.setattr(service_module, "extract_activity_reference", extracting)
    old = await reference_segments(service)
    submit = asyncio.create_task(service.submit_enrollment_segment(old.enrollment_id, "new", 4, _verification_pcm()))
    try:
        await started.wait()
        await service.cancel_enrollment(old.enrollment_id)
        newer = await service.start_enrollment()
        with pytest.raises(VoiceIdentityServiceError, match="stale_enrollment"):
            await submit
        assert late.closed
        assert service._enrollment.enrollment_id == newer.enrollment_id
        assert service._profile_store.path.read_bytes() == original
    finally:
        await service.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_slow_activity_retirement_blocks_another_model_until_cleanup_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _, _ = _service(tmp_path, model_timeout_seconds=0.05)
    original, _ = await seed_previous_profile(service)
    service._activity_models = ActivityModels(EcapaModelSnapshot(tmp_path / "model"))
    cancelled, release = asyncio.Event(), asyncio.Event()
    late = activity_reference()

    async def extracting(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
            return late

    monkeypatch.setattr(service_module, "extract_activity_reference", extracting)
    enrollment = await reference_segments(service)
    try:
        with pytest.raises(VoiceIdentityServiceError, match="model_unavailable"):
            await service.submit_enrollment_segment(enrollment.enrollment_id, "new", 4, _verification_pcm())
        assert cancelled.is_set()
        with pytest.raises(VoiceIdentityServiceError, match="model_unavailable"):
            await service.start_enrollment()
        assert service._profile_store.path.read_bytes() == original
        release.set()
        await _wait_until(lambda: service._model_inference_cleanup_task is None)
        assert late.closed
        assert (await service.start_enrollment()).enrollment_id != enrollment.enrollment_id
    finally:
        release.set()
        await service.close()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["stage", "activation", "replace", "cancel_after_stage"])
async def test_dual_reference_transaction_failure_closes_new_material_and_keeps_old_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    service, _, _, _ = _service(tmp_path)
    original, old_profile = await seed_previous_profile(service)
    service._activity_models = ActivityModels(EcapaModelSnapshot(tmp_path / "model"))
    extracted = activity_reference()
    monkeypatch.setattr(service_module, "extract_activity_reference", AsyncMock(return_value=extracted))
    stage_started, release_stage = asyncio.Event(), asyncio.Event()
    staged_profiles = []
    original_stage = service._profile_store.astage

    async def staging(profile, *, audio_contract):
        staged_profiles.append(profile)
        if failure == "stage":
            raise VoiceIdentityProfileStoreError("disk failure")
        staged = await original_stage(profile, audio_contract=audio_contract)
        stage_started.set()
        if failure == "cancel_after_stage":
            await release_stage.wait()
        return staged

    monkeypatch.setattr(service._profile_store, "astage", staging)
    if failure == "activation":
        async def activate(profile, generation):
            return (
                VoiceIdentityActivationResult.RUNTIME_DEGRADED
                if generation == "new" else VoiceIdentityActivationResult.READY
            )
        service._activation_callback = activate
    if failure == "replace":
        def fail_replace(*args):
            raise OSError("disk full")
        monkeypatch.setattr(store_module, "_replace", fail_replace)
    enrollment = await reference_segments(service)
    submit = asyncio.create_task(service.submit_enrollment_segment(
        enrollment.enrollment_id, "new", 4, _verification_pcm(),
    ))
    try:
        if failure == "cancel_after_stage":
            await stage_started.wait()
            submit.cancel()
            release_stage.set()
            with pytest.raises(asyncio.CancelledError):
                await submit
        else:
            with pytest.raises(VoiceIdentityServiceError, match="runtime_degraded"):
                await submit
        assert extracted.closed
        assert staged_profiles and all(profile.closed for profile in staged_profiles)
        assert all(profile._activity_reference.closed for profile in staged_profiles)
        assert service._profile is old_profile
        assert not old_profile.closed
        usable_reference = old_profile.clone_reference()
        usable_embedding = usable_reference.copy_embedding()
        try:
            assert float(np.dot(usable_embedding, _embedding())) == pytest.approx(1.0)
        finally:
            usable_embedding.fill(0)
            usable_reference.close()
        assert service._profile_store.path.read_bytes() == original
        assert service.status().profile_generation == "old"
        assert service.status().state.effective_enabled
        assert not list(tmp_path.glob(".*.tmp"))
    finally:
        release_stage.set()
        await service.close()
