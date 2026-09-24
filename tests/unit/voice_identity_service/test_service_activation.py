from __future__ import annotations

import asyncio
from pathlib import Path
import threading
from types import SimpleNamespace

import numpy as np
import pytest

import main_logic.voice_identity_service.profile_store as store_module
from main_logic.asr_client import VoiceIdentityActivationResult
from main_logic.asr_client.speaker_shadow.campplus import CAMPPLUS_EMBEDDING_DIM
from main_logic.voice_identity.contracts import SpeakerModelIdentity
from main_logic.voice_identity.profile import SpeakerProfile
from main_logic.voice_identity.reference import SpeakerReference
from main_logic.voice_identity_service.preference_store import (
    VoiceIdentityPreferenceStore,
    VoiceIdentityPreferenceStoreError,
)
from main_logic.voice_identity_service.enrollment import EnrollmentSpeechResult
from main_logic.voice_identity_service.audio_contract import (
    OWNER_CAMPPLUS_DESKTOP_CONTRACT_ID,
    desktop_audio_contract_snapshot,
)
from main_logic.voice_identity_service.enrollment_audio import (
    EnrollmentAudioNormalizationError,
)
from main_logic.voice_identity_service.profile_store import (
    SecureStorageUnavailableError,
    VoiceIdentityProfileCorruptError,
    VoiceIdentityProfileIncompatibleError,
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

    def __init__(
        self,
        *,
        loads: bool = True,
        embeddings: list[np.ndarray] | None = None,
    ) -> None:
        self.loads = loads
        self.closed = False
        self.embeddings = list(embeddings or [])
        self.inference_count = 0

    def load(self) -> bool:
        return self.loads

    def cancel_load(self) -> None:
        return

    def embedding_from_pcm16(
        self,
        pcm16: bytes,
        *,
        sample_rate_hz: int,
    ) -> np.ndarray:
        assert pcm16
        assert sample_rate_hz == 16_000
        self.inference_count += 1
        if self.embeddings:
            return self.embeddings.pop(0)
        result = np.zeros(CAMPPLUS_EMBEDDING_DIM, dtype=np.float32)
        result[0] = 1.0
        return result

    def cancel_inference(self) -> None:
        return

    def close(self) -> None:
        self.closed = True


class _SpeechValidator:
    def __init__(self, *, loads: bool = True) -> None:
        self.loads = loads
        self.closed = False

    async def load(self) -> bool:
        return self.loads

    async def validate_pcm16(
        self,
        pcm16: bytes,
        *,
        sample_rate_hz: int = 16_000,
    ) -> EnrollmentSpeechResult:
        assert pcm16
        assert sample_rate_hz == 16_000
        return EnrollmentSpeechResult(window_count=96, active_window_count=96)

    async def close(self) -> None:
        self.closed = True


class _AudioNormalizer:
    def __init__(self, nr_enabled: bool, *, failure_code: str | None = None) -> None:
        self.nr_enabled = nr_enabled
        self.failure_code = failure_code
        self.calls: list[tuple[int, int, int]] = []

    async def normalize(
        self,
        pcm16: bytes,
        *,
        sample_rate_hz: int,
        target_samples: int,
    ) -> bytes:
        self.calls.append((len(pcm16), sample_rate_hz, target_samples))
        if self.failure_code is not None:
            raise EnrollmentAudioNormalizationError(self.failure_code)
        assert sample_rate_hz == 48_000
        assert target_samples in (48_000, 80_000)
        required_bytes = target_samples * 2
        if len(pcm16) < required_bytes:
            raise EnrollmentAudioNormalizationError("speech_too_short")
        return pcm16[:required_bytes]


def _pcm() -> bytes:
    samples = np.full(48_000, 4_000, dtype="<i2")
    return samples.tobytes()


def _verification_pcm(milliseconds: int = 5_000) -> bytes:
    samples = np.full(48_000 * milliseconds // 1_000, 4_000, dtype="<i2")
    return samples.tobytes()


async def _wait_until(predicate, *, timeout_seconds: float = 1.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError("condition was not satisfied before timeout")
        await asyncio.sleep(0.005)


def _service(
    tmp_path: Path,
    *,
    model: _Model | None = None,
    activation_results: list[bool | VoiceIdentityActivationResult] | None = None,
    runtime_status_results: list[VoiceIdentityActivationResult] | None = None,
    enrollment_ttl_seconds: float = 30.0,
    model_timeout_seconds: float = 1.0,
    runtime_mode: str = "enforce",
    speech_validator: _SpeechValidator | None = None,
    audio_normalizer_factory=None,
    enrollment_noise_reduction_enabled: bool = True,
) -> tuple[
    VoiceIdentityService,
    _Model,
    list[tuple[SpeakerProfile | None, str]],
    list[str],
]:
    selected_model = model or _Model()
    selected_validator = speech_validator or _SpeechValidator()
    activations: list[tuple[SpeakerProfile | None, str]] = []
    results = activation_results or []
    runtime_results = runtime_status_results or []
    suppression_events: list[str] = []

    async def activate(
        profile: SpeakerProfile | None,
        generation: str,
        **_authority,
    ) -> bool:
        activations.append((profile, generation))
        return results.pop(0) if results else True

    async def suppress(reason: str) -> None:
        suppression_events.append(f"suppress:{reason}")

    async def restore(reason: str) -> None:
        suppression_events.append(f"restore:{reason}")

    def runtime_status() -> VoiceIdentityActivationResult:
        return (
            runtime_results[-1]
            if runtime_results
            else VoiceIdentityActivationResult.READY
        )

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
        runtime_status_callback=(runtime_status if runtime_status_results else None),
        speech_validator_factory=lambda: selected_validator,
        enrollment_audio_normalizer_factory=(
            audio_normalizer_factory or _AudioNormalizer
        ),
        enrollment_noise_reduction_enabled=enrollment_noise_reduction_enabled,
    )

    production_submit_enrollment_segment = service.submit_enrollment_segment

    async def submit_enrollment_segment(
        enrollment_id: str,
        profile_id: str,
        segment_index: int,
        pcm16: bytes,
        *,
        sample_rate_hz: int = 48_000,
        audio_contract_id: str = OWNER_CAMPPLUS_DESKTOP_CONTRACT_ID,
    ):
        return await production_submit_enrollment_segment(
            enrollment_id,
            profile_id,
            segment_index,
            pcm16,
            sample_rate_hz=sample_rate_hz,
            audio_contract_id=audio_contract_id,
        )

    service.submit_enrollment_segment = submit_enrollment_segment  # type: ignore[method-assign]

    async def complete_enrollment(
        enrollment_id: str,
        profile_id: str,
        pcm16: bytes,
    ):
        status = service.status()
        for segment_index in range(1, 5):
            status = await service.submit_enrollment_segment(
                enrollment_id,
                profile_id,
                segment_index,
                _verification_pcm() if segment_index == 4 else pcm16,
            )
        return status

    # Keep the legacy tests focused on transaction semantics while the production
    # service exposes only the four-segment API.
    service.complete_enrollment = complete_enrollment  # type: ignore[attr-defined]
    return service, selected_model, activations, suppression_events


def _embedding(axis: int = 0) -> np.ndarray:
    result = np.zeros(CAMPPLUS_EMBEDDING_DIM, dtype=np.float32)
    result[axis] = 1.0
    return result

@pytest.mark.unit
@pytest.mark.asyncio
async def test_normalization_result_rechecks_operation_fence_before_silero(
    tmp_path: Path,
) -> None:
    normalization_started = asyncio.Event()
    release_normalization = asyncio.Event()

    class BlockingNormalizer(_AudioNormalizer):
        async def normalize(
            self,
            pcm16: bytes,
            *,
            sample_rate_hz: int,
            target_samples: int,
        ) -> bytes:
            normalization_started.set()
            await release_normalization.wait()
            return await super().normalize(
                pcm16,
                sample_rate_hz=sample_rate_hz,
                target_samples=target_samples,
            )

    validator = _SpeechValidator()
    validation_calls = 0
    original_validate = validator.validate_pcm16

    async def count_validation(*args, **kwargs):
        nonlocal validation_calls
        validation_calls += 1
        return await original_validate(*args, **kwargs)

    validator.validate_pcm16 = count_validation  # type: ignore[method-assign]
    service, model, _activations, _events = _service(
        tmp_path,
        speech_validator=validator,
        audio_normalizer_factory=BlockingNormalizer,
    )
    await service.initialize()
    enrollment = await service.start_enrollment()
    submission = asyncio.create_task(
        service.submit_enrollment_segment(
            enrollment.enrollment_id,
            "profile-a",
            1,
            _verification_pcm(3_000),
        )
    )
    await normalization_started.wait()
    session = service._enrollment  # type: ignore[attr-defined]
    assert session is not None
    session.operation_nonce += 1
    release_normalization.set()

    with pytest.raises(VoiceIdentityServiceError, match="stale_enrollment"):
        await submission
    assert validation_calls == 0
    assert model.inference_count == 0
    await service.cancel_enrollment(enrollment.enrollment_id)
    await service.close()

@pytest.mark.unit
@pytest.mark.asyncio
async def test_reference_inconsistency_wipes_all_inputs_and_resets_round(
    tmp_path: Path,
) -> None:
    embeddings = [_embedding(), _embedding(), _embedding(1)]
    model = _Model(embeddings=embeddings)
    service, _selected, _activations, _events = _service(tmp_path, model=model)
    await service.initialize()
    enrollment = await service.start_enrollment()
    for segment_index in (1, 2):
        await service.submit_enrollment_segment(
            enrollment.enrollment_id,
            "profile-a",
            segment_index,
            _pcm(),
        )
    with pytest.raises(VoiceIdentityServiceError, match="voice_samples_inconsistent"):
        await service.submit_enrollment_segment(
            enrollment.enrollment_id,
            "profile-a",
            3,
            _pcm(),
        )

    current = service.status().enrollment
    assert current is not None
    assert current.profile_id == "profile-a"
    assert current.next_segment_index == 1
    assert current.accepted_segments == 0
    assert all(np.count_nonzero(item) == 0 for item in embeddings)
    await service.cancel_enrollment(enrollment.enrollment_id)
    await service.close()

@pytest.mark.unit
@pytest.mark.asyncio
async def test_expiry_during_validation_retires_operation_before_late_result(
    tmp_path: Path,
) -> None:
    class BlockingValidator(_SpeechValidator):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()

        async def validate_pcm16(
            self,
            pcm16: bytes,
            *,
            sample_rate_hz: int = 16_000,
        ) -> EnrollmentSpeechResult:
            self.started.set()
            await asyncio.Future()
            raise AssertionError("unreachable")

    validator = BlockingValidator()
    service, model, _activations, events = _service(
        tmp_path,
        speech_validator=validator,
        enrollment_ttl_seconds=0.03,
    )
    await service.initialize()
    enrollment = await service.start_enrollment()
    submission = asyncio.create_task(
        service.submit_enrollment_segment(
            enrollment.enrollment_id,
            "profile-a",
            1,
            _pcm(),
        )
    )
    await asyncio.wait_for(validator.started.wait(), 1.0)
    await _wait_until(lambda: service.status().enrollment is None)
    with pytest.raises(VoiceIdentityServiceError, match="stale_enrollment"):
        await submission
    assert model.inference_count == 0
    assert model.closed and validator.closed
    assert events[-1] == "restore:voice_identity_enrollment"
    await service.close()

@pytest.mark.unit
@pytest.mark.asyncio
async def test_status_reconciles_live_runtime_route_result(tmp_path: Path) -> None:
    runtime_results = [VoiceIdentityActivationResult.READY]
    service, _model, _activations, _events = _service(
        tmp_path,
        runtime_status_results=runtime_results,
    )
    await service.initialize()
    enrollment = await service.start_enrollment()
    await service.complete_enrollment(
        enrollment.enrollment_id,
        "profile-a",
        _pcm(),
    )

    runtime_results[0] = VoiceIdentityActivationResult.UNSUPPORTED_ASR_ROUTE
    unsupported = service.status()
    assert not unsupported.state.effective_enabled
    assert unsupported.state.effective_reason == "unsupported_asr_route"

    runtime_results[0] = VoiceIdentityActivationResult.READY
    assert service.status().state.effective_enabled
    await service.close()

@pytest.mark.unit
@pytest.mark.asyncio
async def test_status_stays_valid_while_filter_disable_detaches(
    tmp_path: Path,
) -> None:
    service, _model, _activations, _events = _service(tmp_path)
    await service.initialize()
    enrollment = await service.start_enrollment()
    await service.complete_enrollment(enrollment.enrollment_id, "profile-a", _pcm())
    detach_started = asyncio.Event()
    detach_release = asyncio.Event()

    async def blocking_activate(
        profile: SpeakerProfile | None,
        generation: str,
        **_authority,
    ) -> bool:
        del generation
        if profile is None:
            detach_started.set()
            await detach_release.wait()
        return True

    service._activation_callback = blocking_activate  # type: ignore[attr-defined]
    disable = asyncio.create_task(service.set_filter(False))
    await asyncio.wait_for(detach_started.wait(), 1.0)

    status = service.status()
    assert not status.state.requested_enabled
    assert not status.state.effective_enabled
    assert status.state.effective_reason == "disabled"

    detach_release.set()
    await disable
    await service.close()

@pytest.mark.unit
@pytest.mark.asyncio
async def test_completed_timed_out_embedding_is_cleared_before_model_close(
    tmp_path: Path,
) -> None:
    class RacingModel(_Model):
        def __init__(self) -> None:
            super().__init__()
            self.embedding_release = threading.Event()
            self.embedding_finished = threading.Event()
            self.embedding_result: np.ndarray | None = None

        def embedding_from_pcm16(
            self,
            pcm16: bytes,
            *,
            sample_rate_hz: int,
        ) -> np.ndarray:
            if not self.embedding_release.wait(1.0):
                raise TimeoutError("test did not release model inference")
            self.embedding_result = super().embedding_from_pcm16(
                pcm16,
                sample_rate_hz=sample_rate_hz,
            )
            self.embedding_finished.set()
            return self.embedding_result

        def close(self) -> None:
            assert self.embedding_result is not None
            assert not np.any(self.embedding_result)
            super().close()

    model = RacingModel()
    service, _selected, _activations, _suppression_events = _service(
        tmp_path,
        model=model,
        model_timeout_seconds=0.1,
    )
    await service.initialize()
    enrollment = await service.start_enrollment()
    session = service._enrollment  # type: ignore[attr-defined]
    assert session is not None
    original_lease = session.lease

    class ReleaseAfterInference:
        expires_at = original_lease.expires_at

        async def release(self) -> None:
            model.embedding_release.set()
            assert await asyncio.to_thread(model.embedding_finished.wait, 1.0)
            task = session.embedding_task
            assert task is not None
            await asyncio.wait_for(asyncio.shield(task), timeout=1.0)
            await original_lease.release()

    session.lease = ReleaseAfterInference()  # type: ignore[assignment]

    with pytest.raises(VoiceIdentityServiceError, match="model_unavailable"):
        await service.complete_enrollment(
            enrollment.enrollment_id,
            "profile",
            _pcm(),
        )

    assert model.closed
    assert model.embedding_result is not None
    assert not np.any(model.embedding_result)
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
        "last_completed_enrollment_id": None,
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
async def test_runtime_noise_reduction_same_value_does_not_reinstall(
    tmp_path: Path,
) -> None:
    service, _model, activations, _events = _service(tmp_path)
    await service.initialize()
    enrollment = await service.start_enrollment()
    await service.complete_enrollment(
        enrollment.enrollment_id,
        "profile-a",
        _pcm(),
    )
    activation_count = len(activations)

    assert await service.prepare_runtime_audio_contract_change(True)
    unchanged = await service.update_runtime_noise_reduction_enabled(True)

    assert unchanged.state.effective_enabled
    assert unchanged.state.effective_reason == "ready"
    assert len(activations) == activation_count
    await service.close()

@pytest.mark.unit
@pytest.mark.asyncio
async def test_runtime_noise_reduction_aba_reinstalls_after_stale_prepare(
    tmp_path: Path,
) -> None:
    service, _model, activations, _events = _service(tmp_path)
    await service.initialize()
    enrollment = await service.start_enrollment()
    await service.complete_enrollment(
        enrollment.enrollment_id,
        "profile-a",
        _pcm(),
    )
    activation_count = len(activations)

    # A stale False task revoked the authority, then a newer settings write
    # returned to the Service's original True snapshot before reconcile.
    assert await service.prepare_runtime_audio_contract_change(False)
    assert activations[-1][0] is None
    assert await service.prepare_runtime_audio_contract_change(True)
    restored = await service.update_runtime_noise_reduction_enabled(True)

    assert restored.state.effective_enabled
    assert restored.state.effective_reason == "ready"
    assert activations[-1][0] is not None
    assert len(activations) == activation_count + 3
    assert not service._runtime_audio_contract_transition_pending  # type: ignore[attr-defined]
    await service.close()

pytestmark = pytest.mark.unit_fast
