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
async def test_cancel_during_speech_validation_invalidates_old_operation(
    tmp_path: Path,
) -> None:
    class BlockingValidator(_SpeechValidator):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.cancelled = asyncio.Event()

        async def validate_pcm16(
            self,
            pcm16: bytes,
            *,
            sample_rate_hz: int = 16_000,
        ) -> EnrollmentSpeechResult:
            self.started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise

    validator = BlockingValidator()
    service, model, _activations, events = _service(
        tmp_path,
        speech_validator=validator,
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
    assert await service.cancel_enrollment(enrollment.enrollment_id)
    await asyncio.wait_for(validator.cancelled.wait(), 1.0)
    with pytest.raises(VoiceIdentityServiceError, match="stale_enrollment"):
        await submission
    assert service.status().enrollment is None
    assert model.inference_count == 0
    assert model.closed and validator.closed
    assert events[-1] == "restore:voice_identity_enrollment"
    replacement = await service.start_enrollment()
    assert replacement.enrollment_id != enrollment.enrollment_id
    await service.cancel_enrollment(replacement.enrollment_id)
    await service.close()

@pytest.mark.unit
@pytest.mark.asyncio
async def test_late_inference_is_wiped_and_cannot_commit_after_cancel(
    tmp_path: Path,
) -> None:
    class LateModel(_Model):
        def __init__(self) -> None:
            super().__init__()
            self.started = threading.Event()
            self.release = threading.Event()
            self.result = _embedding()

        def embedding_from_pcm16(
            self,
            pcm16: bytes,
            *,
            sample_rate_hz: int,
        ) -> np.ndarray:
            self.started.set()
            assert self.release.wait(2.0)
            return self.result

        def cancel_inference(self) -> None:
            return

    model = LateModel()
    service, _selected, _activations, _events = _service(
        tmp_path,
        model=model,
        model_timeout_seconds=1.0,
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
    assert await asyncio.to_thread(model.started.wait, 1.0)
    service._model_timeout_seconds = 0.05  # type: ignore[attr-defined]
    assert await service.cancel_enrollment(enrollment.enrollment_id)
    with pytest.raises(VoiceIdentityServiceError, match="model_unavailable"):
        await service.start_enrollment()
    model.release.set()
    with pytest.raises(VoiceIdentityServiceError, match="stale_enrollment"):
        await submission
    await _wait_until(
        lambda: service._model_inference_cleanup_task is None,  # type: ignore[attr-defined]
    )
    assert np.count_nonzero(model.result) == 0
    assert service.status().profile_generation is None
    replacement = await service.start_enrollment()
    await service.cancel_enrollment(replacement.enrollment_id)
    await service.close()

@pytest.mark.unit
@pytest.mark.asyncio
async def test_invalid_embedding_is_terminal_and_wiped(tmp_path: Path) -> None:
    invalid = _embedding()
    invalid[3] = np.nan
    model = _Model(embeddings=[invalid])
    service, _selected, _activations, events = _service(tmp_path, model=model)
    await service.initialize()
    enrollment = await service.start_enrollment()
    with pytest.raises(VoiceIdentityServiceError, match="model_unavailable"):
        await service.submit_enrollment_segment(
            enrollment.enrollment_id,
            "profile-a",
            1,
            _pcm(),
        )
    assert service.status().enrollment is None
    assert np.count_nonzero(invalid) == 0
    assert model.closed
    assert events[-1] == "restore:voice_identity_enrollment"
    await service.close()

@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_while_waiting_for_cas_wipes_computed_embedding(
    tmp_path: Path,
) -> None:
    class BlockingValidator(_SpeechValidator):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def validate_pcm16(
            self,
            pcm16: bytes,
            *,
            sample_rate_hz: int = 16_000,
        ) -> EnrollmentSpeechResult:
            self.started.set()
            await self.release.wait()
            return await super().validate_pcm16(
                pcm16,
                sample_rate_hz=sample_rate_hz,
            )

    computed_embedding = _embedding()
    model = _Model(embeddings=[computed_embedding])
    validator = BlockingValidator()
    service, _selected, _activations, _events = _service(
        tmp_path,
        model=model,
        speech_validator=validator,
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
    await service._operation_lock.acquire()  # type: ignore[attr-defined]
    validator.release.set()
    await _wait_until(lambda: model.inference_count == 1)
    submission.cancel()
    await asyncio.sleep(0)
    service._operation_lock.release()  # type: ignore[attr-defined]

    with pytest.raises(asyncio.CancelledError):
        await submission
    assert np.count_nonzero(computed_embedding) == 0
    assert service.status().enrollment is None
    assert model.closed and validator.closed
    await service.close()

@pytest.mark.unit
@pytest.mark.asyncio
async def test_late_validation_cannot_start_inference_after_session_cancel(
    tmp_path: Path,
) -> None:
    class LateValidator(_SpeechValidator):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.cancel_seen = asyncio.Event()
            self.release = asyncio.Event()

        async def validate_pcm16(
            self,
            pcm16: bytes,
            *,
            sample_rate_hz: int = 16_000,
        ) -> EnrollmentSpeechResult:
            self.started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                self.cancel_seen.set()
                await self.release.wait()
                return EnrollmentSpeechResult(window_count=96, active_window_count=96)

    validator = LateValidator()
    service, model, _activations, _events = _service(
        tmp_path,
        speech_validator=validator,
        model_timeout_seconds=1.0,
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
    service._model_timeout_seconds = 0.05  # type: ignore[attr-defined]
    assert await service.cancel_enrollment(enrollment.enrollment_id)
    await asyncio.wait_for(validator.cancel_seen.wait(), 1.0)
    assert model.closed
    validator.release.set()
    with pytest.raises(VoiceIdentityServiceError, match="stale_enrollment"):
        await submission
    assert model.inference_count == 0
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
async def test_cancelled_expired_completion_retains_cleanup_to_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, model, _activations, suppression_events = _service(tmp_path)
    await service.initialize()
    enrollment = await service.start_enrollment()
    session = service._enrollment  # type: ignore[attr-defined]
    assert session is not None
    session.expires_at = asyncio.get_running_loop().time() - 1.0
    cleanup_started = asyncio.Event()
    cleanup_release = asyncio.Event()
    cleanup_completed = False
    original_cleanup = service._cleanup_session  # type: ignore[attr-defined]

    async def blocking_cleanup(cleanup_session) -> bool:
        nonlocal cleanup_completed
        cleanup_started.set()
        await cleanup_release.wait()
        cleanup_ok = await original_cleanup(cleanup_session)
        cleanup_completed = True
        return cleanup_ok

    monkeypatch.setattr(service, "_cleanup_session", blocking_cleanup)
    completion = asyncio.create_task(
        service.complete_enrollment(enrollment.enrollment_id, "profile", _pcm())
    )
    await asyncio.wait_for(cleanup_started.wait(), 1.0)
    completion.cancel()
    cleanup_release.set()

    with pytest.raises(asyncio.CancelledError):
        await completion

    assert cleanup_completed
    assert model.closed
    assert suppression_events[-1] == "restore:voice_identity_enrollment"
    assert not service._suppression_controller.snapshot().active  # type: ignore[attr-defined]
    assert service._enrollment is None  # type: ignore[attr-defined]
    await service.close()

@pytest.mark.unit
@pytest.mark.asyncio
async def test_timed_out_embedding_is_cancelled_and_model_is_released(
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

        def cancel_inference(self) -> None:
            self.embedding_release.set()

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
    assert model.closed
    assert suppression_events[-1] == "restore:voice_identity_enrollment"
    assert await asyncio.to_thread(model.close_finished.wait, 1.0)
    assert service._model_inference_cleanup_task is None  # type: ignore[attr-defined]
    retry = await service.start_enrollment()
    assert model.load_calls == 2
    assert await service.cancel_enrollment(retry.enrollment_id)
    await service.close()

@pytest.mark.unit
@pytest.mark.asyncio
async def test_post_prepare_snapshot_failure_clears_pending_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main_routers.config_router.preferences as preferences

    service, _model, activations, _events = _service(tmp_path)
    await service.initialize()
    enrollment = await service.start_enrollment()
    await service.complete_enrollment(
        enrollment.enrollment_id,
        "profile-a",
        _pcm(),
    )
    snapshot_count = 0

    async def snapshot(*, strict: bool = False):
        nonlocal snapshot_count
        snapshot_count += 1
        if snapshot_count == 1:
            return SimpleNamespace(
                revision=1,
                settings={"noiseReductionEnabled": False},
            )
        raise RuntimeError("snapshot failed")

    async def prepare(enabled: bool) -> bool:
        return await service.prepare_runtime_audio_contract_change(enabled)

    async def reconcile(enabled: bool, *, runtime_ready: bool) -> None:
        await service.update_runtime_noise_reduction_enabled(
            enabled,
            runtime_ready=runtime_ready,
        )

    monkeypatch.setattr(
        preferences,
        "aload_global_conversation_settings_snapshot",
        snapshot,
    )
    monkeypatch.setattr(preferences, "_NOISE_REDUCTION_APPLY_LOCK", asyncio.Lock())
    preferences.configure_voice_identity_audio_contract_callbacks(
        prepare=prepare,
        reconcile=reconcile,
    )

    try:
        with pytest.raises(RuntimeError, match="snapshot failed"):
            await preferences._apply_noise_reduction_if_current(False)
    finally:
        preferences.configure_voice_identity_audio_contract_callbacks()

    # Snapshot failure degrades runtime readiness but does not discard the
    # profile.  Its old DSP contract is incompatible with the pending target,
    # so partial recovery remains detached until the committed contract is
    # restored.
    assert activations[-1][0] is None
    assert not service.status().state.effective_enabled
    assert service.status().state.effective_reason == "runtime_degraded"
    # Failure keeps coordination pending, including a retry of the old value.
    assert service._runtime_audio_contract_transition_pending  # type: ignore[attr-defined]
    assert service._runtime_noise_reduction_enabled is True  # type: ignore[attr-defined]

    assert await service.prepare_runtime_audio_contract_change(True)
    restored = await service.update_runtime_noise_reduction_enabled(True)
    assert restored.state.effective_enabled
    assert restored.state.effective_reason == "ready"
    await service.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_partial_dsp_recovery_uses_target_contract_without_committing_it(
    tmp_path: Path,
) -> None:
    service, _model, _activations, _events = _service(tmp_path)
    await service.initialize()
    enrollment = await service.start_enrollment()
    await service.complete_enrollment(
        enrollment.enrollment_id,
        "profile-a",
        _pcm(),
    )

    # Model a profile already enrolled in the target DSP domain while the
    # service still retains the previously committed runtime value.
    service._profile_audio_contract = desktop_audio_contract_snapshot(  # type: ignore[attr-defined]
        noise_reduction_enabled=False,
    )
    captured: dict[str, object] = {}
    original_activate = service._activation_callback  # type: ignore[attr-defined]

    async def capture_activation(profile, generation, **authority):
        captured.update(authority)
        return await original_activate(profile, generation, **authority)

    service._activation_callback = capture_activation  # type: ignore[attr-defined]
    status = await service.update_runtime_noise_reduction_enabled(
        False,
        runtime_ready=False,
    )

    assert captured["noise_reduction_enabled"] is False
    assert service._runtime_noise_reduction_enabled is True  # type: ignore[attr-defined]
    assert status.state.effective_reason == "runtime_degraded"
    assert service._runtime_audio_contract_transition_pending  # type: ignore[attr-defined]
    await service.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_partial_dsp_recovery_rejects_incompatible_profile_contract(
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

    status = await service.update_runtime_noise_reduction_enabled(
        False,
        runtime_ready=False,
    )

    assert len(activations) == activation_count
    assert status.state.effective_reason == "runtime_degraded"
    assert service._runtime_noise_reduction_enabled is True  # type: ignore[attr-defined]
    await service.close()

@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancelled_close_retains_cleanup_to_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _model, activations, _events = _service(tmp_path)
    await service.initialize()
    first = await service.start_enrollment()
    await service.complete_enrollment(first.enrollment_id, "profile-a", _pcm())
    old_profile = service._profile  # type: ignore[attr-defined]
    assert old_profile is not None
    await service.start_enrollment()
    cleanup_started = asyncio.Event()
    cleanup_release = asyncio.Event()
    cleanup_completed = False

    async def blocking_cleanup(session) -> bool:
        nonlocal cleanup_completed
        cleanup_started.set()
        await cleanup_release.wait()
        cleanup_completed = True
        return True

    monkeypatch.setattr(service, "_cleanup_session", blocking_cleanup)
    shutdown = asyncio.create_task(service.close())
    await asyncio.wait_for(cleanup_started.wait(), 1.0)
    shutdown.cancel()
    cleanup_release.set()

    with pytest.raises(asyncio.CancelledError):
        await shutdown

    assert cleanup_completed
    assert service._enrollment is None  # type: ignore[attr-defined]
    assert old_profile.closed
    assert activations[-1][0] is None
    status = service.status()
    assert not status.state.has_profile
    assert not status.state.effective_enabled
    assert status.state.effective_reason == "disabled"

pytestmark = pytest.mark.runtime
