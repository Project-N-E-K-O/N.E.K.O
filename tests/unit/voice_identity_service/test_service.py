from __future__ import annotations

import asyncio
from pathlib import Path
import threading

import numpy as np
import pytest

import main_logic.voice_identity_service.profile_store as store_module
from main_logic.asr_client.speaker_shadow.campplus import CAMPPLUS_EMBEDDING_DIM
from main_logic.voice_identity.contracts import SpeakerModelIdentity
from main_logic.voice_identity.profile import SpeakerProfile
from main_logic.voice_identity.reference import SpeakerReference
from main_logic.voice_identity_service.preference_store import (
    VoiceIdentityPreferenceStore,
    VoiceIdentityPreferenceStoreError,
)
from main_logic.voice_identity_service.profile_store import (
    SecureStorageUnavailableError,
    VoiceIdentityProfileCorruptError,
    VoiceIdentityProfileStore,
    VoiceIdentityProfileStoreError,
)
from main_logic.voice_identity_service.service import (
    VoiceIdentityService,
    VoiceIdentityServiceError,
)
from main_logic.voice_input.suppression import VoiceInputSuppressionController

from .test_profile_store import _TestKeyProtector


class _Model:
    model_id = "3d-speaker-campplus-zh-en"
    model_revision = "2025-06-16-sherpa-onnx-campplus"

    def __init__(self, *, loads: bool = True) -> None:
        self.loads = loads
        self.closed = False

    def load(self) -> bool:
        return self.loads

    def embedding_from_pcm16(
        self,
        pcm16: bytes,
        *,
        sample_rate_hz: int,
    ) -> np.ndarray:
        assert pcm16
        assert sample_rate_hz == 16_000
        result = np.zeros(CAMPPLUS_EMBEDDING_DIM, dtype=np.float32)
        result[0] = 1.0
        return result

    def close(self) -> None:
        self.closed = True


def _pcm() -> bytes:
    samples = np.full(24_000, 4_000, dtype="<i2")
    return samples.tobytes()


def _service(
    tmp_path: Path,
    *,
    model: _Model | None = None,
    activation_results: list[bool] | None = None,
    enrollment_ttl_seconds: float = 30.0,
    model_timeout_seconds: float = 1.0,
    runtime_mode: str = "enforce",
) -> tuple[
    VoiceIdentityService,
    _Model,
    list[tuple[SpeakerProfile | None, str]],
    list[str],
]:
    selected_model = model or _Model()
    activations: list[tuple[SpeakerProfile | None, str]] = []
    results = activation_results or []
    suppression_events: list[str] = []

    async def activate(
        profile: SpeakerProfile | None,
        generation: str,
    ) -> bool:
        activations.append((profile, generation))
        return results.pop(0) if results else True

    async def suppress(reason: str) -> None:
        suppression_events.append(f"suppress:{reason}")

    async def restore(reason: str) -> None:
        suppression_events.append(f"restore:{reason}")

    service = VoiceIdentityService(
        VoiceIdentityProfileStore(
            tmp_path / "voice_identity.profile",
            key_protector=_TestKeyProtector(),
        ),
        VoiceIdentityPreferenceStore(tmp_path / "voice_identity.preference"),
        VoiceInputSuppressionController(
            suppress,
            restore,
            default_ttl_seconds=enrollment_ttl_seconds,
            hard_ttl_seconds=max(1.0, enrollment_ttl_seconds),
        ),
        lambda: selected_model,
        activate,
        runtime_mode=runtime_mode,  # type: ignore[arg-type]
        enrollment_ttl_seconds=enrollment_ttl_seconds,
        model_timeout_seconds=model_timeout_seconds,
        activation_timeout_seconds=1.0,
    )
    return service, selected_model, activations, suppression_events


@pytest.mark.unit
@pytest.mark.asyncio
async def test_first_enrollment_loads_before_suppression_and_enables(
    tmp_path: Path,
) -> None:
    service, model, activations, suppression_events = _service(tmp_path)
    await service.initialize()

    enrollment = await service.start_enrollment()
    assert suppression_events == ["suppress:voice_identity_enrollment"]
    status = await service.complete_enrollment(
        enrollment.enrollment_id,
        "profile-a",
        _pcm(),
    )

    assert status.state.requested_enabled
    assert status.state.effective_enabled
    assert status.state.has_profile
    assert status.profile_generation == "profile-a"
    assert activations[-1][1] == "profile-a"
    assert suppression_events[-1] == "restore:voice_identity_enrollment"
    assert model.closed
    await service.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_model_failure_never_suppresses_input(tmp_path: Path) -> None:
    service, model, _activations, suppression_events = _service(
        tmp_path,
        model=_Model(loads=False),
    )
    await service.initialize()

    with pytest.raises(VoiceIdentityServiceError, match="model_unavailable"):
        await service.start_enrollment()

    assert suppression_events == []
    assert model.closed
    assert service.status().state.effective_reason == "model_unavailable"
    await service.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_is_idempotent_and_releases_model_and_lease(
    tmp_path: Path,
) -> None:
    service, model, _activations, suppression_events = _service(tmp_path)
    await service.initialize()
    enrollment = await service.start_enrollment()

    assert await service.cancel_enrollment(enrollment.enrollment_id)
    assert not await service.cancel_enrollment(enrollment.enrollment_id)
    assert model.closed
    assert suppression_events[-1] == "restore:voice_identity_enrollment"
    await service.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_session_timeout_releases_model_and_lease(tmp_path: Path) -> None:
    service, model, _activations, suppression_events = _service(
        tmp_path,
        enrollment_ttl_seconds=0.02,
    )
    await service.initialize()
    await service.start_enrollment()

    await asyncio.sleep(0.08)

    assert service.status().enrollment is None
    assert model.closed
    assert suppression_events[-1] == "restore:voice_identity_enrollment"
    await service.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_commit_failure_rolls_back_old_activation_and_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _model, activations, _events = _service(tmp_path)
    await service.initialize()
    first = await service.start_enrollment()
    await service.complete_enrollment(first.enrollment_id, "profile-a", _pcm())
    second = await service.start_enrollment()

    async def fail_commit() -> None:
        raise RuntimeError("commit failed")

    original_stage = service._profile_store.astage  # type: ignore[attr-defined]

    async def staged_with_failed_commit(profile: SpeakerProfile):
        staged = await original_stage(profile)
        monkeypatch.setattr(staged, "acommit", fail_commit)
        return staged

    monkeypatch.setattr(service._profile_store, "astage", staged_with_failed_commit)  # type: ignore[attr-defined]
    with pytest.raises(VoiceIdentityServiceError, match="runtime_degraded"):
        await service.complete_enrollment(second.enrollment_id, "profile-b", _pcm())

    assert activations[-1][1] == "profile-a"
    assert service.status().state.effective_enabled
    await service.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_filter_toggle_delete_and_completion_retry(tmp_path: Path) -> None:
    service, _model, activations, _events = _service(tmp_path)
    await service.initialize()
    enrollment = await service.start_enrollment()
    first = await service.complete_enrollment(
        enrollment.enrollment_id,
        "profile-a",
        _pcm(),
    )
    retry = await service.complete_enrollment(
        enrollment.enrollment_id,
        "profile-a",
        b"not reprocessed",
    )
    assert retry == first

    disabled = await service.set_filter(False)
    assert not disabled.state.requested_enabled
    assert not disabled.state.effective_enabled
    assert activations[-1][0] is None
    enabled = await service.set_filter(True)
    assert enabled.state.effective_enabled

    deleted = await service.delete_profile()
    assert not deleted.state.has_profile
    assert not deleted.state.requested_enabled
    await service.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_invalid_pcm_ends_session_and_restores_input(tmp_path: Path) -> None:
    service, model, _activations, events = _service(tmp_path)
    await service.initialize()
    enrollment = await service.start_enrollment()

    with pytest.raises(VoiceIdentityServiceError, match="speech_too_short"):
        await service.complete_enrollment(
            enrollment.enrollment_id,
            "profile-a",
            b"\x00\x00",
        )

    assert service.status().enrollment is None
    assert service.status().state.effective_reason == "disabled"
    assert model.closed
    assert events[-1] == "restore:voice_identity_enrollment"
    await service.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_activation_failure_aborts_first_profile_transaction(
    tmp_path: Path,
) -> None:
    service, model, _activations, events = _service(
        tmp_path,
        activation_results=[False],
    )
    await service.initialize()
    enrollment = await service.start_enrollment()

    with pytest.raises(VoiceIdentityServiceError, match="runtime_degraded"):
        await service.complete_enrollment(
            enrollment.enrollment_id,
            "profile-a",
            _pcm(),
        )

    status = service.status()
    assert not status.state.has_profile
    assert not status.state.requested_enabled
    assert status.state.effective_reason == "runtime_degraded"
    assert not (tmp_path / "voice_identity.profile").exists()
    assert model.closed
    assert events[-1] == "restore:voice_identity_enrollment"
    await service.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failed_reenrollment_marks_degraded_when_old_activation_cannot_restore(
    tmp_path: Path,
) -> None:
    service, _model, activations, _events = _service(
        tmp_path,
        activation_results=[True, False, False],
    )
    await service.initialize()
    first = await service.start_enrollment()
    await service.complete_enrollment(first.enrollment_id, "profile-a", _pcm())
    second = await service.start_enrollment()

    with pytest.raises(VoiceIdentityServiceError, match="runtime_degraded"):
        await service.complete_enrollment(second.enrollment_id, "profile-b", _pcm())

    status = service.status()
    assert status.profile_generation == "profile-a"
    assert not status.state.effective_enabled
    assert status.state.effective_reason == "runtime_degraded"
    assert [generation for _profile, generation in activations[-2:]] == [
        "profile-b",
        "profile-a",
    ]
    await service.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_timed_out_model_load_is_owned_until_worker_finishes(
    tmp_path: Path,
) -> None:
    class BlockingModel(_Model):
        def __init__(self) -> None:
            super().__init__()
            self.load_started = threading.Event()
            self.load_release = threading.Event()
            self.close_finished = threading.Event()
            self.load_calls = 0

        def load(self) -> bool:
            self.load_calls += 1
            self.load_started.set()
            if not self.load_release.wait(1.0):
                raise TimeoutError("test did not release model load")
            return True

        def close(self) -> None:
            assert self.load_release.is_set()
            super().close()
            self.close_finished.set()

    model = BlockingModel()
    service, _selected, _activations, _events = _service(
        tmp_path,
        model=model,
        model_timeout_seconds=0.1,
    )
    await service.initialize()

    with pytest.raises(VoiceIdentityServiceError, match="model_unavailable"):
        await service.start_enrollment()
    assert await asyncio.to_thread(model.load_started.wait, 1.0)
    assert not model.closed

    with pytest.raises(VoiceIdentityServiceError, match="model_unavailable"):
        await service.start_enrollment()
    assert model.load_calls == 1

    model.load_release.set()
    assert await asyncio.to_thread(model.close_finished.wait, 1.0)
    assert model.closed
    await service.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_timed_out_embedding_is_owned_until_worker_finishes(
    tmp_path: Path,
) -> None:
    class BlockingModel(_Model):
        def __init__(self) -> None:
            super().__init__()
            self.embedding_started = threading.Event()
            self.embedding_release = threading.Event()
            self.close_finished = threading.Event()
            self.load_calls = 0

        def load(self) -> bool:
            self.load_calls += 1
            return True

        def embedding_from_pcm16(
            self,
            pcm16: bytes,
            *,
            sample_rate_hz: int,
        ) -> np.ndarray:
            self.embedding_started.set()
            if not self.embedding_release.wait(1.0):
                raise TimeoutError("test did not release model inference")
            return super().embedding_from_pcm16(
                pcm16,
                sample_rate_hz=sample_rate_hz,
            )

        def close(self) -> None:
            assert self.embedding_release.is_set()
            super().close()
            self.close_finished.set()

    model = BlockingModel()
    service, _selected, _activations, suppression_events = _service(
        tmp_path,
        model=model,
        model_timeout_seconds=0.1,
    )
    await service.initialize()
    enrollment = await service.start_enrollment()

    with pytest.raises(VoiceIdentityServiceError, match="model_unavailable"):
        await service.complete_enrollment(
            enrollment.enrollment_id,
            "profile",
            _pcm(),
        )
    assert await asyncio.to_thread(model.embedding_started.wait, 1.0)
    assert not model.closed
    assert suppression_events[-1] == "restore:voice_identity_enrollment"

    with pytest.raises(VoiceIdentityServiceError, match="model_unavailable"):
        await service.start_enrollment()
    assert model.load_calls == 1

    model.embedding_release.set()
    assert await asyncio.to_thread(model.close_finished.wait, 1.0)
    assert model.closed
    await service.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reenrollment_while_disabled_keeps_user_preference(
    tmp_path: Path,
) -> None:
    service, _model, activations, _events = _service(tmp_path)
    await service.initialize()
    first = await service.start_enrollment()
    await service.complete_enrollment(first.enrollment_id, "profile-a", _pcm())
    await service.set_filter(False)
    activation_count = len(activations)

    second = await service.start_enrollment()
    status = await service.complete_enrollment(
        second.enrollment_id,
        "profile-b",
        _pcm(),
    )

    assert not status.state.requested_enabled
    assert not status.state.effective_enabled
    assert status.state.has_profile
    assert len(activations) == activation_count
    await service.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_initialize_restores_encrypted_profile_and_preference(
    tmp_path: Path,
) -> None:
    first_service, _model, _activations, _events = _service(tmp_path)
    await first_service.initialize()
    enrollment = await first_service.start_enrollment()
    await first_service.complete_enrollment(
        enrollment.enrollment_id,
        "profile-a",
        _pcm(),
    )
    await first_service.close()

    restored, _restored_model, activations, _restored_events = _service(tmp_path)
    status = await restored.initialize()

    assert status.state.requested_enabled
    assert status.state.effective_enabled
    assert status.state.has_profile
    assert activations[-1][1] == "profile-a"
    await restored.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_off_mode_records_profile_without_runtime_activation(
    tmp_path: Path,
) -> None:
    service, _model, activations, _events = _service(
        tmp_path,
        runtime_mode="off",
    )
    await service.initialize()
    enrollment = await service.start_enrollment()

    status = await service.complete_enrollment(
        enrollment.enrollment_id,
        "profile-a",
        _pcm(),
    )

    assert status.runtime_mode == "off"
    assert status.state.requested_enabled
    assert not status.state.effective_enabled
    assert status.state.effective_reason == "runtime_degraded"
    assert activations == []
    await service.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_suppression_failure_closes_loaded_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, model, _activations, _events = _service(tmp_path)
    await service.initialize()

    async def fail_acquire(*_args, **_kwargs):
        raise RuntimeError("suppression unavailable")

    monkeypatch.setattr(
        service._suppression_controller,  # type: ignore[attr-defined]
        "acquire",
        fail_acquire,
    )
    with pytest.raises(VoiceIdentityServiceError, match="runtime_degraded"):
        await service.start_enrollment()

    assert model.closed
    assert service.status().enrollment is None
    await service.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_public_guards_and_status_shape(tmp_path: Path) -> None:
    service, _model, _activations, _events = _service(tmp_path)
    with pytest.raises(VoiceIdentityServiceError, match="not_initialized"):
        await service.start_enrollment()
    with pytest.raises(TypeError, match="enabled"):
        await service.set_filter(1)  # type: ignore[arg-type]

    initial = await service.initialize()
    assert await service.initialize() == initial
    assert initial.as_dict() == {
        "requested_enabled": False,
        "effective_enabled": False,
        "effective_reason": "disabled",
        "has_profile": False,
        "enrollment": None,
        "profile_generation": None,
        "runtime_mode": "enforce",
    }
    enrollment = await service.start_enrollment()
    duplicate = await service.start_enrollment()
    assert duplicate == enrollment
    assert duplicate.as_dict()["enrollment_id"] == enrollment.enrollment_id
    assert not await service.cancel_enrollment("different-enrollment")
    assert await service.cancel_enrollment(enrollment.enrollment_id)
    with pytest.raises(VoiceIdentityServiceError, match="invalid_profile_id"):
        await service.complete_enrollment("enrollment", "", _pcm())
    with pytest.raises(VoiceIdentityServiceError, match="stale_enrollment"):
        await service.complete_enrollment("enrollment", "profile", _pcm())

    await service.close()
    await service.close()
    with pytest.raises(VoiceIdentityServiceError, match="service_closed"):
        await service.start_enrollment()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancelled_profile_commit_keeps_memory_and_disk_on_new_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _model, activations, _events = _service(tmp_path)
    await service.initialize()
    first = await service.start_enrollment()
    await service.complete_enrollment(first.enrollment_id, "profile-a", _pcm())
    second = await service.start_enrollment()
    replace_started = threading.Event()
    replace_release = threading.Event()
    original_replace = store_module._replace

    def blocking_replace(source: Path, destination: Path) -> None:
        replace_started.set()
        if not replace_release.wait(1.0):
            raise TimeoutError("test did not release profile commit")
        original_replace(source, destination)

    monkeypatch.setattr(store_module, "_replace", blocking_replace)
    completion = asyncio.create_task(
        service.complete_enrollment(second.enrollment_id, "profile-b", _pcm())
    )
    assert await asyncio.to_thread(replace_started.wait, 1.0)
    completion.cancel()
    replace_release.set()

    with pytest.raises(asyncio.CancelledError):
        await completion

    status = service.status()
    assert status.state.effective_enabled
    assert status.profile_generation == "profile-b"
    assert activations[-1][1] == "profile-b"
    stored = await service._profile_store.aload()  # type: ignore[attr-defined]
    assert stored is not None
    try:
        assert stored.generation == "profile-b"
    finally:
        stored.close()
    await service.close()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (
            SecureStorageUnavailableError("secure_storage_unavailable"),
            "secure_storage_unavailable",
        ),
        (
            VoiceIdentityProfileCorruptError("corrupt"),
            "profile_incompatible",
        ),
    ],
)
async def test_initialize_maps_profile_storage_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    reason: str,
) -> None:
    service, _model, _activations, _events = _service(tmp_path)

    async def fail_load():
        raise failure

    monkeypatch.setattr(
        service._profile_store,  # type: ignore[attr-defined]
        "aload",
        fail_load,
    )
    status = await service.initialize()
    assert status.state.effective_reason == reason
    await service.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_initialize_maps_preference_failure_and_incompatible_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken, _model, _activations, _events = _service(tmp_path / "broken")

    async def fail_preference():
        raise VoiceIdentityPreferenceStoreError("corrupt")

    monkeypatch.setattr(
        broken._preference_store,  # type: ignore[attr-defined]
        "aload",
        fail_preference,
    )
    assert (await broken.initialize()).state.effective_reason == "runtime_degraded"
    await broken.close()

    service, _model, _activations, _events = _service(tmp_path / "incompatible")
    reference = SpeakerReference(
        SpeakerModelIdentity("other-model", "v1", 2),
        [1.0, 0.0],
    )
    try:
        profile = SpeakerProfile("incompatible", reference)
    finally:
        reference.close()
    try:
        await service._profile_store.asave(profile)  # type: ignore[attr-defined]
        await service._preference_store.asave(True)  # type: ignore[attr-defined]
    finally:
        profile.close()

    status = await service.initialize()
    assert status.state.effective_reason == "profile_incompatible"
    await service.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_failure_still_disables_requested_filter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _model, activations, _events = _service(tmp_path)
    await service.initialize()
    enrollment = await service.start_enrollment()
    await service.complete_enrollment(
        enrollment.enrollment_id,
        "profile-a",
        _pcm(),
    )

    async def fail_delete() -> bool:
        raise VoiceIdentityProfileStoreError("delete failed")

    monkeypatch.setattr(
        service._profile_store,  # type: ignore[attr-defined]
        "adelete",
        fail_delete,
    )
    with pytest.raises(VoiceIdentityServiceError, match="runtime_degraded"):
        await service.delete_profile()

    status = service.status()
    assert status.state.requested_enabled
    assert status.state.has_profile
    assert status.state.effective_enabled
    assert activations[-1][0] is not None
    await service.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_rolls_back_profile_when_preference_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _model, activations, _events = _service(tmp_path)
    await service.initialize()
    enrollment = await service.start_enrollment()
    await service.complete_enrollment(
        enrollment.enrollment_id,
        "profile-a",
        _pcm(),
    )

    async def fail_preference(_enabled: bool) -> None:
        raise VoiceIdentityPreferenceStoreError("write failed")

    monkeypatch.setattr(
        service._preference_store,  # type: ignore[attr-defined]
        "asave",
        fail_preference,
    )
    with pytest.raises(VoiceIdentityServiceError, match="runtime_degraded"):
        await service.delete_profile()

    restored = await service._profile_store.aload()  # type: ignore[attr-defined]
    assert restored is not None
    try:
        assert restored.generation == "profile-a"
    finally:
        restored.close()
    status = service.status()
    assert status.state.requested_enabled
    assert status.state.has_profile
    assert status.state.effective_enabled
    assert activations[-1][0] is not None
    await service.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_revokes_activation_when_profile_rollback_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _model, activations, _events = _service(tmp_path)
    await service.initialize()
    enrollment = await service.start_enrollment()
    await service.complete_enrollment(
        enrollment.enrollment_id,
        "profile-a",
        _pcm(),
    )
    old_profile = service._profile  # type: ignore[attr-defined]
    assert old_profile is not None

    async def fail_preference(_enabled: bool) -> None:
        raise VoiceIdentityPreferenceStoreError("write failed")

    async def fail_restore(_profile: SpeakerProfile) -> None:
        raise VoiceIdentityProfileStoreError("restore failed")

    monkeypatch.setattr(
        service._preference_store,  # type: ignore[attr-defined]
        "asave",
        fail_preference,
    )
    monkeypatch.setattr(
        service._profile_store,  # type: ignore[attr-defined]
        "asave",
        fail_restore,
    )
    with pytest.raises(VoiceIdentityServiceError, match="runtime_degraded"):
        await service.delete_profile()

    assert activations[-1][0] is None
    assert old_profile.closed
    assert await service._profile_store.aload() is None  # type: ignore[attr-defined]
    status = service.status()
    assert status.state.requested_enabled
    assert not status.state.has_profile
    assert not status.state.effective_enabled
    assert status.state.effective_reason == "runtime_degraded"
    await service.close()
