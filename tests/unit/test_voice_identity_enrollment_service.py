from __future__ import annotations

import asyncio
from dataclasses import dataclass, fields

import numpy as np
import pytest

from main_logic.voice_identity.enrollment_service import (
    EnrollmentAudioError,
    EnrollmentActivationError,
    EnrollmentBusyError,
    EnrollmentConfig,
    EnrollmentPersistenceError,
    EnrollmentStage,
    OwnerEnrollmentService,
)
from main_logic.voice_identity.profile import SpeakerProfile
from main_logic.voice_identity.profile_store import (
    ProfileLoadResult,
    ProfileLoadState,
    SecureStorageUnavailable,
)
from main_logic.voice_identity.registry import VoiceIdentityProfileRegistry


pytestmark = pytest.mark.asyncio


def _pcm16(value: int, *, duration_ms: int = 1_500) -> bytes:
    samples = 16_000 * duration_ms // 1_000
    return np.full(samples, value, dtype="<i2").tobytes()


class _FakeModel:
    model_id = "test-speaker-model"
    model_revision = "model-v1"

    def __init__(self, *, load_result: bool = True) -> None:
        self.load_result = load_result
        self.closed = False

    def load(self) -> bool:
        return self.load_result

    def embedding_from_pcm16(
        self,
        pcm16: bytes,
        *,
        sample_rate_hz: int,
    ) -> np.ndarray:
        assert sample_rate_hz == 16_000
        first = int(np.frombuffer(pcm16, dtype="<i2", count=1)[0])
        if first >= 0:
            return np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        return np.array([-1.0, 0.0, 0.0, 0.0], dtype=np.float32)

    def close(self) -> None:
        self.closed = True


@dataclass
class _FakeStore:
    profile: SpeakerProfile | None = None
    state: ProfileLoadState = ProfileLoadState.EMPTY
    save_error: Exception | None = None
    delete_error: Exception | None = None
    deleted: bool = False

    async def asave(self, profile: SpeakerProfile) -> str:
        if self.save_error is not None:
            raise self.save_error
        if self.profile is not None:
            self.profile.close()
        self.profile = _copy_profile(profile)
        self.state = ProfileLoadState.READY
        return "2026-07-24T00:00:00+00:00"

    async def aload(self) -> ProfileLoadResult:
        return ProfileLoadResult(
            self.state,
            profile=None if self.profile is None else _copy_profile(self.profile),
            created_at=(
                None
                if self.profile is None
                else "2026-07-24T00:00:00+00:00"
            ),
        )

    async def adelete(self) -> None:
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted = True
        if self.profile is not None:
            self.profile.close()
        self.profile = None
        self.state = ProfileLoadState.EMPTY


def _copy_profile(profile: SpeakerProfile) -> SpeakerProfile:
    return SpeakerProfile(
        profile.reference_embedding,
        profile_revision=profile.profile_revision,
        model_id=profile.model_id,
        model_revision=profile.model_revision,
        embedding_dimension=profile.embedding_dimension,
    )


def _profile(revision: int) -> SpeakerProfile:
    return SpeakerProfile(
        np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32),
        profile_revision=revision,
        model_id="test-speaker-model",
        model_revision="model-v1",
        embedding_dimension=4,
    )


def _config(*, ttl_seconds: float = 60.0) -> EnrollmentConfig:
    return EnrollmentConfig(
        model_id="test-speaker-model",
        model_revision="model-v1",
        embedding_dimension=4,
        sample_rate_hz=16_000,
        fixed_segment_count=3,
        verification_pass_count=2,
        minimum_audio_ms=1_500,
        maximum_audio_ms=8_000,
        verification_threshold=0.40,
        maximum_verification_attempts=6,
        ttl_seconds=ttl_seconds,
    )


def _service(
    *,
    store: _FakeStore | None = None,
    registry: VoiceIdentityProfileRegistry | None = None,
    model: _FakeModel | None = None,
):
    selected_store = store or _FakeStore()
    selected_registry = registry or VoiceIdentityProfileRegistry()
    selected_model = model or _FakeModel()
    prepared: list[str] = []
    activated: list[SpeakerProfile | None] = []

    async def prepare(session_id: str) -> None:
        prepared.append(session_id)

    async def activate(profile: SpeakerProfile | None) -> None:
        activated.append(None if profile is None else _copy_profile(profile))

    service = OwnerEnrollmentService(
        store=selected_store,
        registry=selected_registry,
        model_factory=lambda: selected_model,
        prepare_input=prepare,
        activate_profile=activate,
        config=_config(),
    )
    return (
        service,
        selected_store,
        selected_registry,
        selected_model,
        prepared,
        activated,
    )


async def _collect_fixed(service: OwnerEnrollmentService, session_id: str) -> None:
    for _index in range(3):
        await service.submit_fixed(session_id, _pcm16(100))


async def test_enrollment_requires_three_fixed_and_two_free_passes_before_commit() -> (
    None
):
    service, store, registry, model, prepared, activated = _service()

    started = await service.start()
    session_id = started.session_id
    assert started.stage is EnrollmentStage.FIXED_1
    assert registry.enrollment_active is True
    assert prepared == [session_id]

    await _collect_fixed(service, session_id)
    after_fixed = await service.status()
    assert after_fixed.stage is EnrollmentStage.FREE_VERIFY_1
    assert after_fixed.fixed_completed == 3
    assert not any(
        isinstance(value, bytes)
        for field in fields(service._active_session)
        if (value := getattr(service._active_session, field.name, None)) is not None
    )

    first = await service.verify(session_id, _pcm16(100))
    assert first.passed is True
    assert first.status.stage is EnrollmentStage.FREE_VERIFY_2

    second = await service.verify(session_id, _pcm16(100))
    assert second.passed is True
    assert second.status.stage is EnrollmentStage.READY_TO_COMMIT

    committed = await service.commit(session_id)
    assert committed.profile_state is ProfileLoadState.READY
    assert committed.profile_revision == 0
    assert committed.enrollment_active is False
    assert registry.enrollment_active is False
    assert registry.profile_revision == 0
    assert registry.filter_enabled is False
    assert store.profile is not None
    assert store.profile.profile_revision == 0
    assert [p.profile_revision for p in activated if p is not None] == [0]
    assert model.closed is True

    for profile in activated:
        if profile is not None:
            profile.close()
    await service.close()


async def test_failed_free_verification_does_not_advance_or_replace_profile() -> None:
    old_profile = _profile(4)
    store = _FakeStore(profile=_copy_profile(old_profile), state=ProfileLoadState.READY)
    registry = VoiceIdentityProfileRegistry()
    registry.install_profile(old_profile)
    service, _, _, _, _, _ = _service(store=store, registry=registry)

    started = await service.start()
    await _collect_fixed(service, started.session_id)

    failed = await service.verify(started.session_id, _pcm16(-100))

    assert failed.passed is False
    assert failed.status.stage is EnrollmentStage.FREE_VERIFY_1
    assert failed.status.verification_completed == 0
    assert store.profile is not None
    assert store.profile.profile_revision == 4
    assert registry.profile_revision == 4

    await service.cancel(started.session_id)
    old_profile.close()
    await service.close()


async def test_only_one_enrollment_session_can_hold_the_lease() -> None:
    service, _, registry, _, _, _ = _service()
    started = await service.start()

    with pytest.raises(EnrollmentBusyError):
        await service.start()

    await service.cancel(started.session_id)
    assert registry.enrollment_active is False
    await service.close()


@pytest.mark.parametrize(
    "pcm16",
    [
        b"",
        b"\x00",
        _pcm16(100, duration_ms=1_499),
        _pcm16(100, duration_ms=8_001),
    ],
    ids=["empty", "odd-byte", "too-short", "too-long"],
)
async def test_enrollment_rejects_invalid_or_unbounded_pcm(pcm16: bytes) -> None:
    service, _, _, _, _, _ = _service()
    started = await service.start()

    with pytest.raises(EnrollmentAudioError):
        await service.submit_fixed(started.session_id, pcm16)

    await service.cancel(started.session_id)
    await service.close()


async def test_secure_storage_failure_activates_new_profile_in_memory_only() -> None:
    old_profile = _profile(3)
    store = _FakeStore(
        profile=_copy_profile(old_profile),
        state=ProfileLoadState.READY,
        save_error=SecureStorageUnavailable("keyring unavailable"),
    )
    registry = VoiceIdentityProfileRegistry()
    registry.install_profile(old_profile)
    service, _, _, model, _, activated = _service(
        store=store,
        registry=registry,
    )
    started = await service.start()
    await _collect_fixed(service, started.session_id)
    await service.verify(started.session_id, _pcm16(100))
    await service.verify(started.session_id, _pcm16(100))

    current = await service.commit(started.session_id)

    assert current.stage is EnrollmentStage.IDLE
    assert current.enrollment_active is False
    assert current.profile_state is ProfileLoadState.SECURE_STORAGE_UNAVAILABLE
    assert current.profile_revision == 4
    assert registry.profile_revision == 4
    assert store.profile is not None
    assert store.profile.profile_revision == 3
    assert [profile.profile_revision for profile in activated if profile] == [4]
    assert model.closed is True

    old_profile.close()
    for profile in activated:
        if profile is not None:
            profile.close()
    await service.close()


async def test_expired_session_releases_model_and_input_lease() -> None:
    registry = VoiceIdentityProfileRegistry()
    model = _FakeModel()
    service = OwnerEnrollmentService(
        store=_FakeStore(),
        registry=registry,
        model_factory=lambda: model,
        prepare_input=lambda _session_id: asyncio.sleep(0),
        activate_profile=lambda _profile: asyncio.sleep(0),
        config=_config(ttl_seconds=0.01),
    )

    await service.start()
    await asyncio.sleep(0.03)

    status = await service.status()
    assert status.stage is EnrollmentStage.IDLE
    assert status.enrollment_active is False
    assert registry.enrollment_active is False
    assert model.closed is True
    await service.close()


async def test_restore_and_delete_keep_registry_and_runtime_in_sync() -> None:
    stored = _profile(8)
    store = _FakeStore(profile=_copy_profile(stored), state=ProfileLoadState.READY)
    registry = VoiceIdentityProfileRegistry()
    service, _, _, _, _, activated = _service(
        store=store,
        registry=registry,
    )

    restored = await service.restore()
    assert restored.profile_state is ProfileLoadState.READY
    assert restored.profile_revision == 8
    assert registry.profile_revision == 8

    deleted = await service.delete_profile()
    assert deleted.profile_state is ProfileLoadState.EMPTY
    assert deleted.profile_revision is None
    assert store.deleted is True
    assert registry.profile_revision is None
    assert activated[-1] is None

    stored.close()
    for profile in activated:
        if profile is not None:
            profile.close()
    await service.close()


async def test_failed_delete_keeps_persisted_and_runtime_profile_active() -> None:
    stored = _profile(8)
    store = _FakeStore(
        profile=_copy_profile(stored),
        state=ProfileLoadState.READY,
        delete_error=OSError("profile is locked"),
    )
    registry = VoiceIdentityProfileRegistry()
    service, _, _, _, _, activated = _service(
        store=store,
        registry=registry,
    )
    await service.restore()

    with pytest.raises(EnrollmentPersistenceError):
        await service.delete_profile()

    current = await service.status()
    assert current.profile_state is ProfileLoadState.READY
    assert current.profile_revision == 8
    assert store.deleted is False
    assert store.profile is not None
    assert store.profile.profile_revision == 8
    assert registry.profile_revision == 8
    assert [profile.profile_revision for profile in activated if profile] == [8]

    stored.close()
    for profile in activated:
        if profile is not None:
            profile.close()
    await service.close()


async def test_memory_only_activation_rollback_advances_retry_revision() -> None:
    old_profile = _profile(3)
    store = _FakeStore(
        profile=_copy_profile(old_profile),
        state=ProfileLoadState.READY,
        save_error=SecureStorageUnavailable("keyring unavailable"),
    )
    registry = VoiceIdentityProfileRegistry()
    registry.install_profile(old_profile)
    model = _FakeModel()
    activated: list[SpeakerProfile] = []
    failed_once = False

    async def activate(profile: SpeakerProfile | None) -> None:
        nonlocal failed_once
        assert profile is not None
        activated.append(_copy_profile(profile))
        if profile.profile_revision == 4 and not failed_once:
            failed_once = True
            raise RuntimeError("one session manager rejected activation")

    service = OwnerEnrollmentService(
        store=store,
        registry=registry,
        model_factory=lambda: model,
        prepare_input=lambda _session_id: asyncio.sleep(0),
        activate_profile=activate,
        config=_config(),
    )
    started = await service.start()
    await _collect_fixed(service, started.session_id)
    await service.verify(started.session_id, _pcm16(100))
    await service.verify(started.session_id, _pcm16(100))

    with pytest.raises(EnrollmentActivationError, match="could not be activated"):
        await service.commit(started.session_id)

    assert [profile.profile_revision for profile in activated] == [4, 5]
    assert registry.profile_revision == 5
    assert store.profile is not None
    assert store.profile.profile_revision == 3

    committed = await service.commit(started.session_id)
    assert committed.stage is EnrollmentStage.IDLE
    assert committed.profile_revision == 6
    assert [profile.profile_revision for profile in activated] == [4, 5, 6]

    old_profile.close()
    for profile in activated:
        profile.close()
    await service.close()


async def test_failed_rollback_discards_enrollment_session_and_keeps_registry_snapshot() -> (
    None
):
    old_profile = _profile(3)
    store = _FakeStore(profile=_copy_profile(old_profile), state=ProfileLoadState.READY)
    registry = VoiceIdentityProfileRegistry()
    registry.install_profile(old_profile)
    model = _FakeModel()

    async def reject_activation(profile: SpeakerProfile | None) -> None:
        if profile is not None and profile.profile_revision >= 4:
            raise RuntimeError("manager remains fail-open")

    service = OwnerEnrollmentService(
        store=store,
        registry=registry,
        model_factory=lambda: model,
        prepare_input=lambda _session_id: asyncio.sleep(0),
        activate_profile=reject_activation,
        config=_config(),
    )
    started = await service.start()
    await _collect_fixed(service, started.session_id)
    await service.verify(started.session_id, _pcm16(100))
    await service.verify(started.session_id, _pcm16(100))

    with pytest.raises(EnrollmentActivationError, match="rollback"):
        await service.commit(started.session_id)

    current = await service.status()
    assert current.stage is EnrollmentStage.IDLE
    assert current.enrollment_active is False
    assert current.profile_revision == 5
    assert registry.profile_revision == 5
    assert store.profile is not None
    assert store.profile.profile_revision == 5
    assert model.closed is True

    old_profile.close()
    await service.close()


async def test_delete_commits_fail_open_when_runtime_deactivation_reports_error() -> (
    None
):
    stored = _profile(8)
    store = _FakeStore(profile=_copy_profile(stored), state=ProfileLoadState.READY)
    registry = VoiceIdentityProfileRegistry()
    fail_deactivation = False

    async def activate(profile: SpeakerProfile | None) -> None:
        if profile is None and fail_deactivation:
            raise RuntimeError("manager entered fail-open mode")

    service = OwnerEnrollmentService(
        store=store,
        registry=registry,
        model_factory=_FakeModel,
        prepare_input=lambda _session_id: asyncio.sleep(0),
        activate_profile=activate,
        config=_config(),
    )
    await service.restore()
    fail_deactivation = True

    deleted = await service.delete_profile()

    assert deleted.profile_state is ProfileLoadState.EMPTY
    assert deleted.profile_revision is None
    assert store.deleted is True
    assert registry.profile_revision is None

    stored.close()
    await service.close()


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("model_id", ""),
        ("model_revision", ""),
        ("embedding_dimension", 0),
        ("sample_rate_hz", 48_000),
        ("fixed_segment_count", 2),
        ("verification_pass_count", 1),
        ("minimum_audio_ms", 9_000),
        ("verification_threshold", 2.0),
        ("maximum_verification_attempts", 1),
        ("ttl_seconds", 0),
    ],
)
async def test_enrollment_config_rejects_contract_drift(
    field_name: str,
    value: object,
) -> None:
    values = {
        "model_id": "test-speaker-model",
        "model_revision": "model-v1",
        "embedding_dimension": 4,
        "sample_rate_hz": 16_000,
        "fixed_segment_count": 3,
        "verification_pass_count": 2,
        "minimum_audio_ms": 1_500,
        "maximum_audio_ms": 8_000,
        "verification_threshold": 0.40,
        "maximum_verification_attempts": 6,
        "ttl_seconds": 60.0,
    }
    values[field_name] = value

    with pytest.raises(ValueError):
        EnrollmentConfig(**values)


async def test_model_unavailable_releases_enrollment_lease() -> None:
    registry = VoiceIdentityProfileRegistry()
    model = _FakeModel(load_result=False)
    service, _, _, _, _, _ = _service(registry=registry, model=model)

    with pytest.raises(Exception, match="model is unavailable"):
        await service.start()

    assert registry.enrollment_active is False
    assert model.closed is True
    await service.close()


async def test_non_secure_persistence_failure_keeps_ready_session() -> None:
    store = _FakeStore(save_error=OSError("disk full"))
    service, _, registry, model, _, _ = _service(store=store)
    started = await service.start()
    await _collect_fixed(service, started.session_id)
    await service.verify(started.session_id, _pcm16(100))
    await service.verify(started.session_id, _pcm16(100))

    with pytest.raises(EnrollmentPersistenceError):
        await service.commit(started.session_id)

    assert (await service.status()).stage is EnrollmentStage.READY_TO_COMMIT
    assert registry.enrollment_active is True
    assert model.closed is False
    await service.cancel(started.session_id)
    await service.close()
