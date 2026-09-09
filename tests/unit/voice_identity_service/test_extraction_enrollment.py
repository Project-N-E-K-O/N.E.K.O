"""TSE enrollment shares only validated reference PCM and commits transactionally."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import numpy as np
import pytest

from main_logic.voice_identity.contracts import SpeakerModelIdentity
from main_logic.voice_identity.extraction_reference import SpeakerExtractionReference
from main_logic.voice_identity.profile import SpeakerProfile
from main_logic.voice_identity.tse.assets import TseModelSnapshot
from main_logic.voice_identity.tse.contracts import TSE_ENCODER_IDENTITY
import main_logic.voice_identity.tse.worker as worker_module
from main_logic.voice_identity_service.service import VoiceIdentityServiceError
from .test_activity_enrollment import seed_previous_profile
from .test_extraction_profile_store import RAW_EMBEDDING, extraction_contract
from .test_service import _service, _verification_pcm


class ExtractionModels:
    def __init__(self, snapshot):
        self.current = snapshot
        self.busy = False
        self.started = 0
        self.closed = False

    @property
    def ready(self):
        return not self.closed and self.current is not None

    def snapshot(self):
        return self.current if self.ready else None

    def status(self):
        return {"state": "ready" if self.ready else "missing", "source_configured": True,
                "installed": self.ready, "busy": self.busy, "total_bytes": 123, "downloaded_bytes": 0}

    async def initialize(self):
        pass

    def start(self):
        self.started += 1

    async def import_stream(self, chunks):
        self.busy = True
        try:
            async for _ in chunks:
                pass
        finally:
            self.busy = False
        return self.status()

    async def close(self):
        self.closed = True


def raw_reference(identity=TSE_ENCODER_IDENTITY):
    return SpeakerExtractionReference(identity, RAW_EMBEDDING)


async def three_references(service, profile_id="new"):
    enrollment = await service.start_enrollment()
    for index in (1, 2, 3):
        # Independent amplitudes make accidental reuse of holdout PCM observable.
        pcm = np.full(48000, 3500 + index * 500, dtype="<i2").tobytes()
        await service.submit_enrollment_segment(enrollment.enrollment_id, profile_id, index, pcm)
    return enrollment


@pytest.mark.asyncio
async def test_same_enrollment_passes_only_three_reference_segments_and_wipes_them(tmp_path, monkeypatch):
    service, _, _, _ = _service(tmp_path)
    snapshot = TseModelSnapshot(tmp_path / "first-model")
    models = ExtractionModels(snapshot)
    service._extraction_models = models
    received, source_arrays = [], []
    returned = raw_reference()

    async def extract(directory, segments, *, timeout):
        assert directory == snapshot.directory
        assert 0 < timeout <= service._model_timeout_seconds
        received.extend(pcm.copy() for pcm in segments)
        source_arrays.extend(segments)
        return returned

    mock = AsyncMock(side_effect=extract)
    monkeypatch.setattr(worker_module, "extract_extraction_reference", mock)
    try:
        await service.initialize()
        enrollment = await three_references(service)
        mock.assert_not_awaited()
        models.current = TseModelSnapshot(tmp_path / "later-model")
        holdout = np.full(240000, 9000, dtype="<i2").tobytes()
        result = await service.submit_enrollment_segment(enrollment.enrollment_id, "new", 4, holdout)
        assert result.verification.passed
        mock.assert_awaited_once()
        assert len(received) == 3
        for index, pcm in enumerate(received, 1):
            assert pcm.shape == (48000,)
            np.testing.assert_array_equal(pcm, np.full(48000, (3500 + index * 500) / 32768, dtype=np.float32))
        assert all(not np.any(pcm) for pcm in source_arrays)
        assert returned.closed
        assert result.tse["reference_ready"] is True
        assert result.tse["enabled"] is False
        stored = service._profile_store.load()
        try:
            assert stored.profile.generation == "new"
            reference = stored.profile.clone_extraction_reference()
            np.testing.assert_array_equal(reference.copy_embedding(), RAW_EMBEDDING)
            reference.close()
        finally:
            stored.close()
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["model", "timeout", "identity"])
async def test_required_tse_failure_preserves_entire_old_owner(tmp_path, monkeypatch, failure):
    service, _, _, _ = _service(tmp_path)
    original, owner = await seed_previous_profile(service)
    service._extraction_models = ExtractionModels(TseModelSnapshot(tmp_path / "model"))
    wrong = raw_reference(SpeakerModelIdentity("wrong-encoder", "revision", 192))
    monkeypatch.setattr(worker_module, "extract_extraction_reference", AsyncMock(
        return_value=wrong,
        side_effect=RuntimeError("inference failed") if failure == "model" else TimeoutError() if failure == "timeout" else None,
    ))
    try:
        enrollment = await three_references(service)
        retained = list(service._enrollment.extraction_pcm)
        with pytest.raises(VoiceIdentityServiceError, match="model_unavailable"):
            await service.submit_enrollment_segment(enrollment.enrollment_id, "new", 4, _verification_pcm())
        assert service._profile is owner
        assert not owner.closed
        assert service._profile_store.path.read_bytes() == original
        assert service.status().profile_generation == "old"
        assert service._enrollment is None
        assert all(not np.any(pcm) for pcm in retained)
        if failure == "identity":
            assert wrong.closed
    finally:
        wrong.close()
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("retirement", ["cancel", "close", "caller"])
async def test_session_retirement_stops_tse_worker_and_erases_reference_pcm(tmp_path, monkeypatch, retirement):
    service, _, _, _ = _service(tmp_path)
    original, _ = await seed_previous_profile(service)
    service._extraction_models = ExtractionModels(TseModelSnapshot(tmp_path / "model"))
    entered, stopped = asyncio.Event(), asyncio.Event()

    async def extract(*_, **__):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(worker_module, "extract_extraction_reference", extract)
    enrollment = await three_references(service)
    retained = list(service._enrollment.extraction_pcm)
    submit = asyncio.create_task(service.submit_enrollment_segment(enrollment.enrollment_id, "new", 4, _verification_pcm()))
    try:
        await entered.wait()
        if retirement == "cancel":
            await service.cancel_enrollment(enrollment.enrollment_id)
        elif retirement == "close":
            await service.close()
        else:
            submit.cancel()
        expected = asyncio.CancelledError if retirement == "caller" else VoiceIdentityServiceError
        with pytest.raises(expected):
            await submit
        assert stopped.is_set()
        assert service._enrollment is None
        assert all(not np.any(pcm) for pcm in retained)
        assert service._profile_store.path.read_bytes() == original
    finally:
        await service.close()
        if not submit.done():
            submit.cancel()
            await asyncio.gather(submit, return_exceptions=True)


@pytest.mark.asyncio
async def test_late_tse_result_is_closed_and_cannot_pollute_new_enrollment(tmp_path, monkeypatch):
    service, _, _, _ = _service(tmp_path)
    original, _ = await seed_previous_profile(service)
    service._extraction_models = ExtractionModels(TseModelSnapshot(tmp_path / "model"))
    entered = asyncio.Event()
    late = raw_reference()

    async def extract(*_, **__):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return late

    monkeypatch.setattr(worker_module, "extract_extraction_reference", extract)
    old = await three_references(service)
    submit = asyncio.create_task(service.submit_enrollment_segment(old.enrollment_id, "new", 4, _verification_pcm()))
    try:
        await entered.wait()
        await service.cancel_enrollment(old.enrollment_id)
        newer = await service.start_enrollment()
        with pytest.raises(VoiceIdentityServiceError, match="stale_enrollment"):
            await submit
        assert late.closed
        assert service._enrollment.enrollment_id == newer.enrollment_id
        assert service._profile_store.path.read_bytes() == original
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_download_and_import_are_mutually_exclusive_with_enrollment(tmp_path):
    service, _, _, _ = _service(tmp_path)
    models = ExtractionModels(None)
    service._extraction_models = models
    await service.initialize()
    started, release = asyncio.Event(), asyncio.Event()

    async def uploading():
        started.set()
        await release.wait()
        yield b"zip bytes"

    try:
        await service.download_tse_model()
        assert models.started == 1
        enrollment = await service.start_enrollment()
        with pytest.raises(VoiceIdentityServiceError, match="enrollment_active"):
            await service.download_tse_model()
        with pytest.raises(VoiceIdentityServiceError, match="enrollment_active"):
            await service.import_tse_model(uploading())
        await service.cancel_enrollment(enrollment.enrollment_id)
        importing = asyncio.create_task(service.import_tse_model(uploading()))
        await started.wait()
        with pytest.raises(VoiceIdentityServiceError, match="tse_assets_busy"):
            await service.start_enrollment()
        with pytest.raises(VoiceIdentityServiceError, match="tse_assets_busy"):
            await service.download_tse_model()
        release.set()
        await importing
        assert (await service.start_enrollment()).enrollment_id != enrollment.enrollment_id
    finally:
        release.set()
        await service.close()


@pytest.mark.asyncio
async def test_wrong_tse_revision_leaves_cam_active_and_reports_incompatible(tmp_path):
    service, _, _, _ = _service(tmp_path)
    await seed_previous_profile(service)
    primary = service._profile.clone_reference()
    extraction = raw_reference()
    incompatible = SpeakerProfile(
        "old", primary, extraction_reference=extraction,
        extraction_reference_contract=replace(extraction_contract(), resource_revision="older-resource"),
    )
    primary.close()
    extraction.close()
    service._profile.close()
    service._profile = incompatible
    service._extraction_models = ExtractionModels(TseModelSnapshot(tmp_path / "model"))
    service._extraction_runtime_available = True
    try:
        status = service.status()
        assert status.state.effective_enabled
        assert status.tse["reference_state"] == "incompatible"
        assert status.tse["reference_ready"] is False
        with pytest.raises(VoiceIdentityServiceError, match="tse_reference_required"):
            await service.update_tse(True, "old")
        assert not service.status().tse["enabled"]
        assert service._profile is incompatible
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_enabling_tse_requires_explicit_current_profile_and_ready_reference(tmp_path, monkeypatch):
    service, _, _, _ = _service(tmp_path)
    service._extraction_models = ExtractionModels(TseModelSnapshot(tmp_path / "model"))
    monkeypatch.setattr(worker_module, "extract_extraction_reference", AsyncMock(side_effect=lambda *_args, **_kwargs: raw_reference()))
    try:
        await service.initialize()
        enrollment = await three_references(service)
        await service.submit_enrollment_segment(enrollment.enrollment_id, "new", 4, _verification_pcm())
        assert not service.status().tse["enabled"]
        with pytest.raises(VoiceIdentityServiceError, match="stale_profile"):
            await service.update_tse(True, "previous-owner")
        assert not service.status().tse["enabled"]
        assert service.status().tse["can_enable"] is False
        with pytest.raises(VoiceIdentityServiceError, match="tse_route_unavailable"):
            await service.update_tse(True, "new")
        assert not service.status().tse["enabled"]
        service._extraction_runtime_available = True
        assert (await service.update_tse(True, "new")).tse["enabled"] is True
        assert (await service.update_tse(False, "new")).tse["enabled"] is False
    finally:
        await service.close()
