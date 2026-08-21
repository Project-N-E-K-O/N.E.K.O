"""Fail-open application controller for the single local Owner profile."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import math
from typing import Literal, Protocol, TypeVar
import uuid

import numpy as np

from main_logic.asr_client import VoiceIdentityActivationResult
from main_logic.asr_client.speaker_shadow.asset_manifest import (
    CAMPPLUS_MODEL_ID,
    CAMPPLUS_MODEL_REVISION,
    CAMPPLUS_SAMPLE_RATE_HZ,
)
from main_logic.asr_client.speaker_shadow.campplus import CAMPPLUS_EMBEDDING_DIM
from main_logic.voice_identity.contracts import SpeakerModelIdentity
from main_logic.voice_identity.profile import SpeakerProfile
from main_logic.voice_identity.reference import SpeakerReference
from main_logic.voice_input.suppression import (
    VoiceInputSuppressionController,
    VoiceInputSuppressionLease,
)

from .enrollment import EnrollmentAudioError, validate_enrollment_pcm16
from .preference_store import (
    VoiceIdentityPreferenceStore,
    VoiceIdentityPreferenceStoreError,
)
from .profile_store import (
    SecureStorageUnavailableError,
    VoiceIdentityProfileCorruptError,
    VoiceIdentityProfileStore,
    VoiceIdentityProfileStoreError,
    VoiceIdentityProfileWrite,
)
from .state import VoiceIdentityEffectiveReason, VoiceIdentityState


class EnrollmentEmbeddingModel(Protocol):
    model_id: str
    model_revision: str

    def load(self) -> bool: ...

    def cancel_load(self) -> None: ...

    def embedding_from_pcm16(
        self,
        pcm16: bytes,
        *,
        sample_rate_hz: int,
    ) -> np.ndarray: ...

    def cancel_inference(self) -> None: ...

    def close(self) -> None: ...


EnrollmentModelFactory = Callable[[], EnrollmentEmbeddingModel]
ActivationCallback = Callable[
    [SpeakerProfile | None, str],
    Awaitable[bool | VoiceIdentityActivationResult],
]
RuntimeStatusCallback = Callable[[], VoiceIdentityActivationResult]
VoiceIdentityRuntimeMode = Literal["off", "shadow", "enforce"]
_ResultT = TypeVar("_ResultT")


async def _await_cancellation_safe(
    awaitable: Awaitable[_ResultT],
    *,
    name: str,
    cancellations: list[asyncio.CancelledError],
) -> _ResultT:
    task = asyncio.create_task(awaitable, name=name)
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            if not cancellations:
                cancellations.append(exc)
    return task.result()


class VoiceIdentityServiceError(RuntimeError):
    """A stable, UI-safe control-plane failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class EnrollmentStatus:
    enrollment_id: str
    expires_at: float

    def as_dict(self) -> dict[str, str | float]:
        return {
            "enrollment_id": self.enrollment_id,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True, slots=True)
class VoiceIdentityServiceStatus:
    state: VoiceIdentityState
    enrollment: EnrollmentStatus | None
    profile_generation: str | None
    runtime_mode: VoiceIdentityRuntimeMode

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = self.state.as_dict()
        result["enrollment"] = (
            None if self.enrollment is None else self.enrollment.as_dict()
        )
        result["profile_generation"] = self.profile_generation
        result["runtime_mode"] = self.runtime_mode
        return result


@dataclass(slots=True)
class _EnrollmentSession:
    enrollment_id: str
    expires_at: float
    model: EnrollmentEmbeddingModel
    lease: VoiceInputSuppressionLease
    expiry_task: asyncio.Task[None]
    embedding_task: asyncio.Task[np.ndarray] | None = None


class VoiceIdentityService:
    """Own persistence, enrollment, activation, and fail-open state."""

    def __init__(
        self,
        profile_store: VoiceIdentityProfileStore,
        preference_store: VoiceIdentityPreferenceStore,
        suppression_controller: VoiceInputSuppressionController,
        model_factory: EnrollmentModelFactory,
        activation_callback: ActivationCallback,
        *,
        runtime_mode: VoiceIdentityRuntimeMode = "enforce",
        enrollment_ttl_seconds: float = 30.0,
        model_timeout_seconds: float = 30.0,
        activation_timeout_seconds: float = 5.0,
        runtime_status_callback: RuntimeStatusCallback | None = None,
    ) -> None:
        if not isinstance(profile_store, VoiceIdentityProfileStore):
            raise TypeError("profile_store must be VoiceIdentityProfileStore")
        if not isinstance(preference_store, VoiceIdentityPreferenceStore):
            raise TypeError("preference_store must be VoiceIdentityPreferenceStore")
        if not isinstance(
            suppression_controller,
            VoiceInputSuppressionController,
        ):
            raise TypeError(
                "suppression_controller must be VoiceInputSuppressionController"
            )
        if not callable(model_factory) or not callable(activation_callback):
            raise TypeError("model_factory and activation_callback must be callable")
        if runtime_status_callback is not None and not callable(
            runtime_status_callback
        ):
            raise TypeError("runtime_status_callback must be callable or None")
        if runtime_mode not in ("off", "shadow", "enforce"):
            raise ValueError("runtime_mode must be off, shadow, or enforce")
        for name, value in (
            ("enrollment_ttl_seconds", enrollment_ttl_seconds),
            ("model_timeout_seconds", model_timeout_seconds),
            ("activation_timeout_seconds", activation_timeout_seconds),
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if enrollment_ttl_seconds > 30.0:
            raise ValueError("enrollment_ttl_seconds cannot exceed 30 seconds")

        self._profile_store = profile_store
        self._preference_store = preference_store
        self._suppression_controller = suppression_controller
        self._model_factory = model_factory
        self._activation_callback = activation_callback
        self._runtime_status_callback = runtime_status_callback
        self._runtime_mode: VoiceIdentityRuntimeMode = runtime_mode
        self._enrollment_ttl_seconds = float(enrollment_ttl_seconds)
        self._model_timeout_seconds = float(model_timeout_seconds)
        self._activation_timeout_seconds = float(activation_timeout_seconds)
        self._operation_lock = asyncio.Lock()
        self._profile: SpeakerProfile | None = None
        self._requested_enabled = False
        self._effective_enabled = False
        self._effective_reason = VoiceIdentityEffectiveReason.DISABLED
        self._enrollment: _EnrollmentSession | None = None
        self._last_completed: tuple[str, str] | None = None
        self._model_load_cleanup_task: asyncio.Task[None] | None = None
        self._model_inference_cleanup_task: asyncio.Task[None] | None = None
        self._initialized = False
        self._closed = False

    async def initialize(self) -> VoiceIdentityServiceStatus:
        async with self._operation_lock:
            self._require_open()
            if self._initialized:
                return self.status()
            try:
                requested_enabled = await self._preference_store.aload()
            except VoiceIdentityPreferenceStoreError:
                self._set_ineffective(VoiceIdentityEffectiveReason.RUNTIME_DEGRADED)
                self._initialized = True
                return self.status()
            try:
                profile = await self._profile_store.aload()
            except SecureStorageUnavailableError:
                self._requested_enabled = requested_enabled
                self._set_ineffective(
                    VoiceIdentityEffectiveReason.SECURE_STORAGE_UNAVAILABLE
                )
                self._initialized = True
                return self.status()
            except VoiceIdentityProfileCorruptError:
                self._requested_enabled = requested_enabled
                self._set_ineffective(VoiceIdentityEffectiveReason.PROFILE_INCOMPATIBLE)
                self._initialized = True
                return self.status()
            except VoiceIdentityProfileStoreError:
                self._requested_enabled = requested_enabled
                self._set_ineffective(VoiceIdentityEffectiveReason.RUNTIME_DEGRADED)
                self._initialized = True
                return self.status()

            self._requested_enabled = requested_enabled
            self._profile = profile
            if profile is None:
                self._set_ineffective(
                    VoiceIdentityEffectiveReason.NO_PROFILE
                    if requested_enabled
                    else VoiceIdentityEffectiveReason.DISABLED
                )
            elif not self._profile_is_compatible(profile):
                self._set_ineffective(VoiceIdentityEffectiveReason.PROFILE_INCOMPATIBLE)
            elif not requested_enabled:
                self._set_ineffective(VoiceIdentityEffectiveReason.DISABLED)
            elif self._runtime_mode == "off":
                self._set_ineffective(VoiceIdentityEffectiveReason.RUNTIME_DEGRADED)
            else:
                self._apply_activation_result(
                    await self._activate(profile, profile.generation)
                )
            self._initialized = True
            return self.status()

    def status(self) -> VoiceIdentityServiceStatus:
        if (
            self._runtime_status_callback is not None
            and self._requested_enabled
            and self._profile is not None
            and self._runtime_mode != "off"
            and self._effective_reason
            in {
                VoiceIdentityEffectiveReason.READY,
                VoiceIdentityEffectiveReason.RUNTIME_DEGRADED,
                VoiceIdentityEffectiveReason.UNSUPPORTED_ASR_ROUTE,
            }
        ):
            try:
                self._apply_activation_result(self._runtime_status_callback())
            except Exception:
                self._set_ineffective(VoiceIdentityEffectiveReason.RUNTIME_DEGRADED)
        enrollment = self._enrollment
        enrollment_status = (
            None
            if enrollment is None
            else EnrollmentStatus(
                enrollment.enrollment_id,
                enrollment.expires_at,
            )
        )
        return VoiceIdentityServiceStatus(
            VoiceIdentityState(
                requested_enabled=self._requested_enabled,
                effective_enabled=self._effective_enabled,
                effective_reason=self._effective_reason,
                has_profile=self._profile is not None,
            ),
            enrollment_status,
            None if self._profile is None else self._profile.generation,
            self._runtime_mode,
        )

    async def start_enrollment(self) -> EnrollmentStatus:
        async with self._operation_lock:
            self._require_initialized()
            if self._enrollment is not None:
                return EnrollmentStatus(
                    self._enrollment.enrollment_id,
                    self._enrollment.expires_at,
                )
            cleanup_task = self._model_load_cleanup_task
            if cleanup_task is not None:
                if not cleanup_task.done():
                    self._record_failure(VoiceIdentityEffectiveReason.MODEL_UNAVAILABLE)
                    raise VoiceIdentityServiceError("model_unavailable")
                self._model_load_cleanup_task = None
            cleanup_task = self._model_inference_cleanup_task
            if cleanup_task is not None:
                if not cleanup_task.done():
                    self._record_failure(VoiceIdentityEffectiveReason.MODEL_UNAVAILABLE)
                    raise VoiceIdentityServiceError("model_unavailable")
                self._model_inference_cleanup_task = None
            try:
                model = self._model_factory()
            except Exception as exc:
                self._record_failure(VoiceIdentityEffectiveReason.MODEL_UNAVAILABLE)
                raise VoiceIdentityServiceError("model_unavailable") from exc
            load_task = asyncio.create_task(
                asyncio.to_thread(model.load),
                name="voice-identity-model-load",
            )
            try:
                loaded = bool(
                    await asyncio.wait_for(
                        asyncio.shield(load_task),
                        timeout=self._model_timeout_seconds,
                    )
                )
            except TimeoutError:
                try:
                    model.cancel_load()
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(
                        asyncio.shield(load_task),
                        timeout=self._model_timeout_seconds,
                    )
                except TimeoutError:
                    self._retain_timed_out_model_load(model, load_task)
                except asyncio.CancelledError:
                    self._retain_timed_out_model_load(model, load_task)
                    raise
                except Exception:
                    await self._close_model(model)
                else:
                    await self._close_model(model)
                self._record_failure(VoiceIdentityEffectiveReason.MODEL_UNAVAILABLE)
                raise VoiceIdentityServiceError("model_unavailable")
            except asyncio.CancelledError:
                try:
                    model.cancel_load()
                except Exception:
                    pass
                self._retain_timed_out_model_load(model, load_task)
                raise
            except Exception:
                loaded = False
            if not loaded:
                await self._close_model(model)
                self._record_failure(VoiceIdentityEffectiveReason.MODEL_UNAVAILABLE)
                raise VoiceIdentityServiceError("model_unavailable")

            try:
                lease = await self._suppression_controller.acquire(
                    "voice_identity_enrollment",
                    ttl_seconds=self._enrollment_ttl_seconds,
                )
            except asyncio.CancelledError as exc:
                cancellations = [exc]
                await _await_cancellation_safe(
                    self._close_model(model),
                    name="voice-identity-cancelled-acquire-model-close",
                    cancellations=cancellations,
                )
                raise cancellations[0]
            except Exception as exc:
                await self._close_model(model)
                self._record_failure(VoiceIdentityEffectiveReason.RUNTIME_DEGRADED)
                raise VoiceIdentityServiceError("runtime_degraded") from exc

            enrollment_id = str(uuid.uuid4())
            loop = asyncio.get_running_loop()
            expiry_delay = max(0.0, lease.expires_at - loop.time())
            expiry_task = asyncio.create_task(
                self._expire_enrollment(
                    enrollment_id,
                    expiry_delay,
                ),
                name="voice-identity-enrollment-expiry",
            )
            self._enrollment = _EnrollmentSession(
                enrollment_id=enrollment_id,
                expires_at=lease.expires_at,
                model=model,
                lease=lease,
                expiry_task=expiry_task,
            )
            if not self._effective_enabled:
                self._set_ineffective(VoiceIdentityEffectiveReason.ENROLLMENT_ACTIVE)
            return EnrollmentStatus(enrollment_id, lease.expires_at)

    async def complete_enrollment(
        self,
        enrollment_id: str,
        profile_id: str,
        pcm16: bytes,
    ) -> VoiceIdentityServiceStatus:
        _require_identifier("enrollment_id", enrollment_id)
        _require_identifier("profile_id", profile_id)
        async with self._operation_lock:
            self._require_initialized()
            if self._last_completed == (enrollment_id, profile_id):
                return self.status()
            session = self._enrollment
            if session is None or session.enrollment_id != enrollment_id:
                raise VoiceIdentityServiceError("stale_enrollment")
            if asyncio.get_running_loop().time() >= session.expires_at:
                self._enrollment = None
                session.expiry_task.cancel()
                cancellations: list[asyncio.CancelledError] = []
                cleanup_ok = await _await_cancellation_safe(
                    self._cleanup_session(session),
                    name="voice-identity-expired-enrollment-cleanup",
                    cancellations=cancellations,
                )
                if not self._effective_enabled:
                    self._set_ineffective(
                        self._idle_reason()
                        if cleanup_ok
                        else VoiceIdentityEffectiveReason.RUNTIME_DEGRADED
                    )
                if cancellations:
                    raise cancellations[0]
                raise VoiceIdentityServiceError("stale_enrollment")
            self._enrollment = None
            session.expiry_task.cancel()

            old_profile = self._profile
            old_requested = self._requested_enabled
            old_effective = self._effective_enabled
            old_activation_requested = (
                old_requested and old_profile is not None and self._runtime_mode != "off"
            )
            desired_requested = True if old_profile is None else old_requested
            new_profile: SpeakerProfile | None = None
            staged: VoiceIdentityProfileWrite | None = None
            activation_changed = False
            activation_result = VoiceIdentityActivationResult.READY
            preference_changed = False
            succeeded = False
            commit_cancellation: asyncio.CancelledError | None = None
            old_activation_restore_result: VoiceIdentityActivationResult | None = None
            failure_reason = VoiceIdentityEffectiveReason.RUNTIME_DEGRADED
            try:
                await asyncio.to_thread(validate_enrollment_pcm16, pcm16)
                try:
                    embedding_task = asyncio.create_task(
                        asyncio.to_thread(
                            session.model.embedding_from_pcm16,
                            pcm16,
                            sample_rate_hz=CAMPPLUS_SAMPLE_RATE_HZ,
                        ),
                        name="voice-identity-model-inference",
                    )
                    session.embedding_task = embedding_task
                    embedding = await asyncio.wait_for(
                        asyncio.shield(embedding_task),
                        timeout=self._model_timeout_seconds,
                    )
                    session.embedding_task = None
                except Exception as exc:
                    failure_reason = VoiceIdentityEffectiveReason.MODEL_UNAVAILABLE
                    raise VoiceIdentityServiceError("model_unavailable") from exc
                try:
                    reference = SpeakerReference(
                        SpeakerModelIdentity(
                            CAMPPLUS_MODEL_ID,
                            CAMPPLUS_MODEL_REVISION,
                            CAMPPLUS_EMBEDDING_DIM,
                        ),
                        embedding,
                    )
                    try:
                        new_profile = SpeakerProfile(profile_id, reference)
                    finally:
                        reference.close()
                finally:
                    if isinstance(embedding, np.ndarray) and embedding.flags.writeable:
                        embedding.fill(0.0)

                staging_cancellations: list[asyncio.CancelledError] = []
                staged = await _await_cancellation_safe(
                    self._profile_store.astage(new_profile),
                    name="voice-identity-profile-stage",
                    cancellations=staging_cancellations,
                )
                if staging_cancellations:
                    raise staging_cancellations[0]
                if desired_requested and self._runtime_mode != "off":
                    activation_cancellations: list[asyncio.CancelledError] = []
                    activation_result = await _await_cancellation_safe(
                        self._activate(new_profile, profile_id),
                        name="voice-identity-enrollment-activation",
                        cancellations=activation_cancellations,
                    )
                    activation_changed = True
                    if activation_cancellations:
                        raise activation_cancellations[0]
                    if not activation_result:
                        raise VoiceIdentityServiceError("runtime_degraded")
                if desired_requested != old_requested:
                    preference_cancellations: list[asyncio.CancelledError] = []
                    await _await_cancellation_safe(
                        self._preference_store.asave(desired_requested),
                        name="voice-identity-enrollment-preference-save",
                        cancellations=preference_cancellations,
                    )
                    preference_changed = True
                    if preference_cancellations:
                        raise preference_cancellations[0]
                commit_task = asyncio.create_task(
                    staged.acommit(),
                    name="voice-identity-profile-commit",
                )
                while not commit_task.done():
                    try:
                        await asyncio.shield(commit_task)
                    except asyncio.CancelledError as exc:
                        if commit_cancellation is None:
                            commit_cancellation = exc
                await commit_task

                self._profile = new_profile
                new_profile = None
                self._requested_enabled = desired_requested
                if desired_requested and self._runtime_mode != "off":
                    self._apply_activation_result(activation_result)
                elif desired_requested:
                    self._set_ineffective(VoiceIdentityEffectiveReason.RUNTIME_DEGRADED)
                else:
                    self._set_ineffective(VoiceIdentityEffectiveReason.DISABLED)
                self._last_completed = (enrollment_id, profile_id)
                succeeded = True
                if old_profile is not None:
                    old_profile.close()
                if commit_cancellation is not None:
                    raise commit_cancellation
            except EnrollmentAudioError as exc:
                failure_reason = self._idle_reason()
                raise VoiceIdentityServiceError(exc.code) from exc
            except VoiceIdentityServiceError:
                raise
            except VoiceIdentityPreferenceStoreError as exc:
                raise VoiceIdentityServiceError("runtime_degraded") from exc
            except SecureStorageUnavailableError as exc:
                failure_reason = VoiceIdentityEffectiveReason.SECURE_STORAGE_UNAVAILABLE
                raise VoiceIdentityServiceError("secure_storage_unavailable") from exc
            except VoiceIdentityProfileStoreError as exc:
                raise VoiceIdentityServiceError("runtime_degraded") from exc
            except Exception as exc:
                raise VoiceIdentityServiceError("runtime_degraded") from exc
            finally:
                if not succeeded:
                    if staged is not None:
                        try:
                            await staged.aabort()
                        except Exception:
                            pass
                    if activation_changed:
                        rollback_profile = (
                            old_profile if old_activation_requested else None
                        )
                        rollback_generation = (
                            old_profile.generation
                            if rollback_profile is not None
                            else str(uuid.uuid4())
                        )
                        old_activation_restore_result = await self._activate(
                            rollback_profile,
                            rollback_generation,
                        )
                        if not old_activation_restore_result:
                            failure_reason = (
                                VoiceIdentityEffectiveReason.RUNTIME_DEGRADED
                            )
                    if preference_changed:
                        try:
                            await self._preference_store.asave(old_requested)
                        except VoiceIdentityPreferenceStoreError:
                            failure_reason = (
                                VoiceIdentityEffectiveReason.RUNTIME_DEGRADED
                            )
                    if new_profile is not None:
                        new_profile.close()
                    if (
                        old_activation_requested
                        and old_activation_restore_result is not None
                        and old_activation_restore_result
                    ):
                        self._apply_activation_result(old_activation_restore_result)
                    elif old_effective:
                        self._set_ineffective(
                            VoiceIdentityEffectiveReason.RUNTIME_DEGRADED
                        )
                    else:
                        self._set_ineffective(failure_reason)
                cleanup_ok = await self._cleanup_session(session)
                if not cleanup_ok and not self._effective_enabled:
                    self._set_ineffective(VoiceIdentityEffectiveReason.RUNTIME_DEGRADED)
            return self.status()

    async def cancel_enrollment(self, enrollment_id: str | None = None) -> bool:
        async with self._operation_lock:
            self._require_initialized()
            session = self._enrollment
            if session is None:
                return False
            if enrollment_id is not None and session.enrollment_id != enrollment_id:
                return False
            self._enrollment = None
            cancellations: list[asyncio.CancelledError] = []
            cleanup_ok = await _await_cancellation_safe(
                self._cleanup_session(session),
                name="voice-identity-cancel-enrollment-cleanup",
                cancellations=cancellations,
            )
            if not self._effective_enabled:
                self._set_ineffective(
                    self._idle_reason()
                    if cleanup_ok
                    else VoiceIdentityEffectiveReason.RUNTIME_DEGRADED
                )
            if cancellations:
                raise cancellations[0]
            return True

    async def set_filter(self, enabled: bool) -> VoiceIdentityServiceStatus:
        if type(enabled) is not bool:
            raise TypeError("enabled must be bool")
        async with self._operation_lock:
            self._require_initialized()
            cancellations: list[asyncio.CancelledError] = []
            try:
                await _await_cancellation_safe(
                    self._preference_store.asave(enabled),
                    name="voice-identity-filter-preference-save",
                    cancellations=cancellations,
                )
            except VoiceIdentityPreferenceStoreError as exc:
                self._record_failure(VoiceIdentityEffectiveReason.RUNTIME_DEGRADED)
                if cancellations:
                    raise cancellations[0]
                raise VoiceIdentityServiceError("runtime_degraded") from exc
            self._requested_enabled = enabled
            if not enabled:
                self._set_ineffective(VoiceIdentityEffectiveReason.DISABLED)
                detached = await _await_cancellation_safe(
                    self._activate(None, str(uuid.uuid4())),
                    name="voice-identity-filter-disable",
                    cancellations=cancellations,
                )
                self._set_ineffective(
                    VoiceIdentityEffectiveReason.DISABLED
                    if detached
                    else VoiceIdentityEffectiveReason.RUNTIME_DEGRADED
                )
            elif self._profile is None:
                self._set_ineffective(VoiceIdentityEffectiveReason.NO_PROFILE)
            elif not self._profile_is_compatible(self._profile):
                self._set_ineffective(VoiceIdentityEffectiveReason.PROFILE_INCOMPATIBLE)
            elif self._runtime_mode == "off":
                self._set_ineffective(VoiceIdentityEffectiveReason.RUNTIME_DEGRADED)
            else:
                activated = await _await_cancellation_safe(
                    self._activate(self._profile, self._profile.generation),
                    name="voice-identity-filter-enable",
                    cancellations=cancellations,
                )
                self._apply_activation_result(activated)
            status = self.status()
            if cancellations:
                raise cancellations[0]
            return status

    async def delete_profile(self) -> VoiceIdentityServiceStatus:
        async with self._operation_lock:
            self._require_initialized()
            cancellations: list[asyncio.CancelledError] = []
            session = self._enrollment
            self._enrollment = None
            cleanup_ok = True
            if session is not None:
                cleanup_ok = await _await_cancellation_safe(
                    self._cleanup_session(session),
                    name="voice-identity-delete-enrollment-cleanup",
                    cancellations=cancellations,
                )
                if not cleanup_ok:
                    self._set_ineffective(VoiceIdentityEffectiveReason.RUNTIME_DEGRADED)
            old_profile = self._profile
            try:
                await _await_cancellation_safe(
                    self._profile_store.adelete(),
                    name="voice-identity-profile-delete",
                    cancellations=cancellations,
                )
            except VoiceIdentityProfileStoreError as exc:
                if cancellations:
                    raise cancellations[0]
                raise VoiceIdentityServiceError("runtime_degraded") from exc
            try:
                await _await_cancellation_safe(
                    self._preference_store.asave(False),
                    name="voice-identity-delete-preference-save",
                    cancellations=cancellations,
                )
            except VoiceIdentityPreferenceStoreError as exc:
                rollback_failed = False
                if old_profile is not None:
                    try:
                        await _await_cancellation_safe(
                            self._profile_store.asave(old_profile),
                            name="voice-identity-delete-profile-rollback",
                            cancellations=cancellations,
                        )
                    except VoiceIdentityProfileStoreError:
                        rollback_failed = True
                if rollback_failed:
                    await _await_cancellation_safe(
                        self._activate(None, str(uuid.uuid4())),
                        name="voice-identity-delete-failed-rollback-detach",
                        cancellations=cancellations,
                    )
                    self._profile = None
                    if old_profile is not None:
                        old_profile.close()
                    self._set_ineffective(VoiceIdentityEffectiveReason.RUNTIME_DEGRADED)
                if cancellations:
                    raise cancellations[0]
                raise VoiceIdentityServiceError("runtime_degraded") from exc
            self._requested_enabled = False
            self._set_ineffective(VoiceIdentityEffectiveReason.DISABLED)
            detached = await _await_cancellation_safe(
                self._activate(None, str(uuid.uuid4())),
                name="voice-identity-delete-profile-detach",
                cancellations=cancellations,
            )
            self._profile = None
            self._set_ineffective(
                VoiceIdentityEffectiveReason.DISABLED
                if detached and cleanup_ok
                else VoiceIdentityEffectiveReason.RUNTIME_DEGRADED
            )
            if old_profile is not None:
                old_profile.close()
            status = self.status()
            if cancellations:
                raise cancellations[0]
            return status

    async def close(self) -> None:
        async with self._operation_lock:
            if self._closed:
                return
            self._closed = True
            cancellations: list[asyncio.CancelledError] = []
            session = self._enrollment
            self._enrollment = None
            if session is not None:
                await _await_cancellation_safe(
                    self._cleanup_session(session),
                    name="voice-identity-close-enrollment-cleanup",
                    cancellations=cancellations,
                )
            await _await_cancellation_safe(
                self._activate(None, str(uuid.uuid4())),
                name="voice-identity-close-profile-detach",
                cancellations=cancellations,
            )
            try:
                await _await_cancellation_safe(
                    self._suppression_controller.close(),
                    name="voice-identity-close-suppression-controller",
                    cancellations=cancellations,
                )
            except Exception:
                pass
            if self._profile is not None:
                self._profile.close()
                self._profile = None
            self._effective_enabled = False
            self._effective_reason = VoiceIdentityEffectiveReason.DISABLED
            if cancellations:
                raise cancellations[0]

    async def _expire_enrollment(
        self,
        enrollment_id: str,
        ttl_seconds: float,
    ) -> None:
        try:
            await asyncio.sleep(ttl_seconds)
            await self.cancel_enrollment(enrollment_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _cleanup_session(self, session: _EnrollmentSession) -> bool:
        current = asyncio.current_task()
        if session.expiry_task is not current:
            session.expiry_task.cancel()
        ok = True
        try:
            await session.lease.release()
        except Exception:
            ok = False
        embedding_task = session.embedding_task
        session.embedding_task = None
        if embedding_task is not None:
            if not embedding_task.done():
                try:
                    session.model.cancel_inference()
                except Exception:
                    ok = False
                try:
                    await asyncio.wait_for(
                        asyncio.shield(embedding_task),
                        timeout=self._model_timeout_seconds,
                    )
                except TimeoutError:
                    self._retain_timed_out_model_inference(
                        session.model,
                        embedding_task,
                    )
                    return False
                except asyncio.CancelledError:
                    if not embedding_task.done():
                        self._retain_timed_out_model_inference(
                            session.model,
                            embedding_task,
                        )
                    raise
                except Exception:
                    pass
            try:
                embedding = embedding_task.result()
            except BaseException:
                pass
            else:
                if isinstance(embedding, np.ndarray) and embedding.flags.writeable:
                    embedding.fill(0.0)
        await self._close_model(session.model)
        return ok

    async def _close_model(self, model: EnrollmentEmbeddingModel) -> None:
        try:
            await asyncio.wait_for(
                asyncio.to_thread(model.close),
                timeout=self._model_timeout_seconds,
            )
        except Exception:
            pass

    def _retain_timed_out_model_load(
        self,
        model: EnrollmentEmbeddingModel,
        load_task: asyncio.Task[bool],
    ) -> None:
        async def finish_and_close() -> None:
            try:
                await load_task
            except BaseException:
                pass
            await self._close_model(model)

        cleanup_task = asyncio.create_task(
            finish_and_close(),
            name="voice-identity-model-load-cleanup",
        )
        self._model_load_cleanup_task = cleanup_task

        def clear_finished(task: asyncio.Task[None]) -> None:
            if self._model_load_cleanup_task is task:
                self._model_load_cleanup_task = None

        cleanup_task.add_done_callback(clear_finished)

    def _retain_timed_out_model_inference(
        self,
        model: EnrollmentEmbeddingModel,
        inference_task: asyncio.Task[np.ndarray],
    ) -> None:
        async def finish_and_close() -> None:
            try:
                embedding = await inference_task
            except BaseException:
                pass
            else:
                if isinstance(embedding, np.ndarray) and embedding.flags.writeable:
                    embedding.fill(0.0)
            await self._close_model(model)

        cleanup_task = asyncio.create_task(
            finish_and_close(),
            name="voice-identity-model-inference-cleanup",
        )
        self._model_inference_cleanup_task = cleanup_task

        def clear_finished(task: asyncio.Task[None]) -> None:
            if self._model_inference_cleanup_task is task:
                self._model_inference_cleanup_task = None

        cleanup_task.add_done_callback(clear_finished)

    async def _activate(
        self,
        profile: SpeakerProfile | None,
        generation: str,
    ) -> VoiceIdentityActivationResult:
        try:
            result = await asyncio.wait_for(
                self._activation_callback(profile, generation),
                timeout=self._activation_timeout_seconds,
            )
            if isinstance(result, VoiceIdentityActivationResult):
                return result
            return (
                VoiceIdentityActivationResult.READY
                if result
                else VoiceIdentityActivationResult.RUNTIME_DEGRADED
            )
        except Exception:
            return VoiceIdentityActivationResult.RUNTIME_DEGRADED

    def _apply_activation_result(
        self,
        result: VoiceIdentityActivationResult,
    ) -> None:
        if result is VoiceIdentityActivationResult.READY:
            if self._runtime_mode == "shadow":
                self._set_ineffective(VoiceIdentityEffectiveReason.SHADOW_MODE)
                return
            self._set_ready()
            return
        self._set_ineffective(VoiceIdentityEffectiveReason(result.value))

    def _profile_is_compatible(self, profile: SpeakerProfile) -> bool:
        identity = profile.model_identity
        return identity == SpeakerModelIdentity(
            CAMPPLUS_MODEL_ID,
            CAMPPLUS_MODEL_REVISION,
            CAMPPLUS_EMBEDDING_DIM,
        )

    def _set_ready(self) -> None:
        self._effective_enabled = True
        self._effective_reason = VoiceIdentityEffectiveReason.READY

    def _set_ineffective(self, reason: VoiceIdentityEffectiveReason) -> None:
        self._effective_enabled = False
        self._effective_reason = reason

    def _record_failure(self, reason: VoiceIdentityEffectiveReason) -> None:
        if not self._effective_enabled:
            self._set_ineffective(reason)

    def _idle_reason(self) -> VoiceIdentityEffectiveReason:
        if not self._requested_enabled:
            return VoiceIdentityEffectiveReason.DISABLED
        if self._profile is None:
            return VoiceIdentityEffectiveReason.NO_PROFILE
        return VoiceIdentityEffectiveReason.RUNTIME_DEGRADED

    def _require_open(self) -> None:
        if self._closed:
            raise VoiceIdentityServiceError("service_closed")

    def _require_initialized(self) -> None:
        self._require_open()
        if not self._initialized:
            raise VoiceIdentityServiceError("service_not_initialized")


def _require_identifier(name: str, value: str) -> None:
    if type(value) is not str or not value.strip() or len(value) > 128:
        raise VoiceIdentityServiceError(f"invalid_{name}")
