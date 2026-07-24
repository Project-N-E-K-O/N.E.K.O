"""Single-owner enrollment state machine with no provider transport authority."""

from __future__ import annotations

import asyncio
import logging
import math
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

import numpy as np

from .profile import SpeakerProfile
from .profile_store import (
    ProfileLoadState,
    ProfileStore,
    SecureStorageUnavailable,
)
from .registry import EnrollmentLeaseBusy, VoiceIdentityProfileRegistry


_LOGGER = logging.getLogger(__name__)


class EnrollmentError(RuntimeError):
    """Base class for UI-safe enrollment failures."""


class EnrollmentBusyError(EnrollmentError):
    pass


class EnrollmentNotFoundError(EnrollmentError):
    pass


class EnrollmentStateError(EnrollmentError):
    pass


class EnrollmentAudioError(EnrollmentError):
    pass


class EnrollmentModelUnavailable(EnrollmentError):
    pass


class EnrollmentPersistenceError(EnrollmentError):
    pass


class EnrollmentActivationError(EnrollmentError):
    pass


class EnrollmentStage(str, Enum):
    IDLE = "idle"
    FIXED_1 = "fixed_1"
    FIXED_2 = "fixed_2"
    FIXED_3 = "fixed_3"
    FREE_VERIFY_1 = "free_verify_1"
    FREE_VERIFY_2 = "free_verify_2"
    READY_TO_COMMIT = "ready_to_commit"


@dataclass(frozen=True, slots=True)
class EnrollmentConfig:
    model_id: str
    model_revision: str
    embedding_dimension: int
    sample_rate_hz: int = 16_000
    fixed_segment_count: int = 3
    verification_pass_count: int = 2
    minimum_audio_ms: int = 1_500
    maximum_audio_ms: int = 8_000
    verification_threshold: float = 0.40
    maximum_verification_attempts: int = 6
    ttl_seconds: float = 600.0

    def __post_init__(self) -> None:
        if not self.model_id or not self.model_revision:
            raise ValueError("speaker model identity must not be empty")
        if self.embedding_dimension <= 0:
            raise ValueError("embedding_dimension must be positive")
        if self.sample_rate_hz != 16_000:
            raise ValueError("owner enrollment requires sample_rate_hz=16000")
        if self.fixed_segment_count != 3:
            raise ValueError("owner enrollment requires exactly three fixed segments")
        if self.verification_pass_count != 2:
            raise ValueError("owner enrollment requires exactly two verification passes")
        if not 0 < self.minimum_audio_ms <= self.maximum_audio_ms:
            raise ValueError("owner enrollment audio bounds are invalid")
        if not -1.0 <= self.verification_threshold <= 1.0:
            raise ValueError("verification_threshold must be a cosine similarity")
        if self.maximum_verification_attempts < self.verification_pass_count:
            raise ValueError("maximum_verification_attempts is too small")
        if self.ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")


@dataclass(frozen=True, slots=True)
class EnrollmentStatus:
    session_id: str | None
    stage: EnrollmentStage
    fixed_completed: int
    verification_completed: int
    verification_attempts: int
    profile_state: ProfileLoadState
    profile_revision: int | None
    filter_enabled: bool
    enrollment_active: bool


@dataclass(frozen=True, slots=True)
class VerificationResult:
    passed: bool
    status: EnrollmentStatus


class _ProfileStorePort(Protocol):
    async def asave(self, profile: SpeakerProfile) -> str: ...

    async def aload(self): ...

    async def adelete(self) -> None: ...


@dataclass(slots=True)
class _EnrollmentSession:
    session_id: str
    model: Any
    previous_profile: SpeakerProfile | None
    stage: EnrollmentStage = EnrollmentStage.FIXED_1
    fixed_embeddings: list[np.ndarray] = field(default_factory=list)
    temporary_profile: SpeakerProfile | None = None
    verification_completed: int = 0
    verification_attempts: int = 0
    expiry_task: asyncio.Task[None] | None = None


class OwnerEnrollmentService:
    """Coordinate one explicit local enrollment session."""

    def __init__(
        self,
        *,
        store: ProfileStore | _ProfileStorePort,
        registry: VoiceIdentityProfileRegistry,
        model_factory: Callable[[], Any],
        prepare_input: Callable[[str], Awaitable[None]],
        activate_profile: Callable[[SpeakerProfile | None], Awaitable[None]],
        config: EnrollmentConfig,
    ) -> None:
        self._store = store
        self._registry = registry
        self._model_factory = model_factory
        self._prepare_input = prepare_input
        self._activate_profile = activate_profile
        self._config = config
        self._active_session: _EnrollmentSession | None = None
        self._persistence_state = (
            ProfileLoadState.READY
            if registry.profile_revision is not None
            else ProfileLoadState.EMPTY
        )
        self._lock = asyncio.Lock()
        self._closed = False

    async def status(self) -> EnrollmentStatus:
        async with self._lock:
            return self._status_locked()

    async def start(self) -> EnrollmentStatus:
        async with self._lock:
            self._ensure_open()
            if self._active_session is not None:
                raise EnrollmentBusyError("voice identity enrollment is busy")
            session_id = secrets.token_urlsafe(24)
            try:
                self._registry.begin_enrollment(session_id)
            except EnrollmentLeaseBusy as exc:
                raise EnrollmentBusyError(str(exc)) from exc
            model = None
            previous = self._registry.snapshot_profile()
            try:
                model = await asyncio.to_thread(self._model_factory)
                loaded = await asyncio.to_thread(model.load)
                if not bool(loaded):
                    raise EnrollmentModelUnavailable(
                        "voice identity model is unavailable"
                    )
                if (
                    str(getattr(model, "model_id", "")) != self._config.model_id
                    or str(getattr(model, "model_revision", ""))
                    != self._config.model_revision
                ):
                    raise EnrollmentModelUnavailable(
                        "voice identity model identity changed"
                    )
                await self._prepare_input(session_id)
            except Exception:
                if previous is not None:
                    previous.close()
                if model is not None:
                    await asyncio.to_thread(model.close)
                self._registry.end_enrollment(session_id)
                raise
            session = _EnrollmentSession(
                session_id=session_id,
                model=model,
                previous_profile=previous,
            )
            session.expiry_task = asyncio.create_task(
                self._expire_session(session_id)
            )
            self._active_session = session
            return self._status_locked()

    async def submit_fixed(
        self,
        session_id: str,
        pcm16: bytes,
    ) -> EnrollmentStatus:
        self._validate_pcm(pcm16)
        async with self._lock:
            session = self._require_session_locked(session_id)
            expected_stage = (
                EnrollmentStage.FIXED_1,
                EnrollmentStage.FIXED_2,
                EnrollmentStage.FIXED_3,
            )[len(session.fixed_embeddings)]
            if session.stage is not expected_stage:
                raise EnrollmentStateError("fixed enrollment stage is not active")
            embedding = await self._embed(session, pcm16)
            session.fixed_embeddings.append(embedding)
            fixed_completed = len(session.fixed_embeddings)
            if fixed_completed < self._config.fixed_segment_count:
                session.stage = (
                    EnrollmentStage.FIXED_2
                    if fixed_completed == 1
                    else EnrollmentStage.FIXED_3
                )
            else:
                stacked = np.stack(session.fixed_embeddings, axis=0)
                try:
                    mean_embedding = np.mean(
                        stacked,
                        axis=0,
                        dtype=np.float32,
                    )
                    session.temporary_profile = SpeakerProfile(
                        mean_embedding,
                        profile_revision=self._registry.next_profile_revision(),
                        model_id=self._config.model_id,
                        model_revision=self._config.model_revision,
                        embedding_dimension=self._config.embedding_dimension,
                    )
                finally:
                    stacked.fill(0)
                    for fixed_embedding in session.fixed_embeddings:
                        fixed_embedding.fill(0)
                    session.fixed_embeddings.clear()
                session.stage = EnrollmentStage.FREE_VERIFY_1
            return self._status_locked()

    async def verify(
        self,
        session_id: str,
        pcm16: bytes,
    ) -> VerificationResult:
        self._validate_pcm(pcm16)
        async with self._lock:
            session = self._require_session_locked(session_id)
            if session.stage not in {
                EnrollmentStage.FREE_VERIFY_1,
                EnrollmentStage.FREE_VERIFY_2,
            }:
                raise EnrollmentStateError("free verification stage is not active")
            if (
                session.verification_attempts
                >= self._config.maximum_verification_attempts
            ):
                raise EnrollmentStateError("free verification attempt limit reached")
            profile = session.temporary_profile
            if profile is None:
                raise EnrollmentStateError("temporary speaker profile is unavailable")
            candidate = await self._embed(session, pcm16)
            try:
                similarity = float(
                    np.dot(profile.reference_embedding, candidate)
                )
            finally:
                candidate.fill(0)
            if not math.isfinite(similarity):
                raise EnrollmentAudioError("speaker similarity is invalid")
            session.verification_attempts += 1
            passed = similarity >= self._config.verification_threshold
            if passed:
                session.verification_completed += 1
                if (
                    session.verification_completed
                    >= self._config.verification_pass_count
                ):
                    session.stage = EnrollmentStage.READY_TO_COMMIT
                else:
                    session.stage = EnrollmentStage.FREE_VERIFY_2
            return VerificationResult(
                passed=passed,
                status=self._status_locked(),
            )

    async def commit(self, session_id: str) -> EnrollmentStatus:
        async with self._lock:
            session = self._require_session_locked(session_id)
            if session.stage is not EnrollmentStage.READY_TO_COMMIT:
                raise EnrollmentStateError("enrollment is not ready to commit")
            profile = session.temporary_profile
            if profile is None:
                raise EnrollmentStateError("temporary speaker profile is unavailable")
            persisted = True
            try:
                await self._store.asave(profile)
            except SecureStorageUnavailable:
                persisted = False
                self._persistence_state = (
                    ProfileLoadState.SECURE_STORAGE_UNAVAILABLE
                )
            except Exception as exc:
                raise EnrollmentPersistenceError(
                    "voice identity profile could not be persisted"
                ) from exc
            try:
                self._registry.install_profile(profile)
                await self._activate_profile(profile)
            except Exception as exc:
                try:
                    if persisted:
                        await self._rollback_activation(session, profile)
                    else:
                        await self._restore_previous_runtime_profile(session)
                except Exception as rollback_exc:
                    await self._finish_active_locked()
                    raise EnrollmentActivationError(
                        "voice identity profile rollback could not be activated"
                    ) from rollback_exc
                self._advance_temporary_profile_revision(session)
                raise EnrollmentActivationError(
                    "voice identity profile could not be activated"
                ) from exc
            if persisted:
                self._persistence_state = ProfileLoadState.READY
            await self._finish_active_locked()
            return self._status_locked()

    async def cancel(self, session_id: str) -> EnrollmentStatus:
        async with self._lock:
            self._require_session_locked(session_id)
            await self._finish_active_locked()
            return self._status_locked()

    async def restore(self) -> EnrollmentStatus:
        async with self._lock:
            self._ensure_open()
            loaded = await self._store.aload()
            self._persistence_state = loaded.state
            profile = loaded.profile
            try:
                if loaded.state is ProfileLoadState.READY and profile is not None:
                    self._registry.install_profile(profile)
                    await self._activate_profile(profile)
                else:
                    self._registry.clear_profile()
                    await self._activate_profile(None)
            finally:
                if profile is not None:
                    profile.close()
            return self._status_locked()

    async def delete_profile(self) -> EnrollmentStatus:
        async with self._lock:
            self._ensure_open()
            if self._active_session is not None:
                raise EnrollmentBusyError(
                    "voice identity enrollment must finish before deletion"
                )
            try:
                await self._store.adelete()
            except Exception as exc:
                # Deletion is transactional from the service's perspective:
                # keep the current in-memory profile active when persistence
                # cannot confirm removal.
                raise EnrollmentPersistenceError(
                    "voice identity profile key could not be deleted"
                ) from exc
            self._persistence_state = ProfileLoadState.EMPTY
            self._registry.clear_profile()
            try:
                await self._activate_profile(None)
            except Exception:
                # VoiceIdentitySession clears its profile and blocks future
                # activation before surfacing close failures. The delete has
                # therefore reached the required fail-open state even when a
                # manager reports cleanup trouble.
                _LOGGER.warning(
                    "voice identity runtime deactivation reported an error",
                    exc_info=True,
                )
            return self._status_locked()

    async def set_filter_enabled(self, enabled: bool) -> EnrollmentStatus:
        async with self._lock:
            self._ensure_open()
            self._registry.set_filter_enabled(enabled)
            return self._status_locked()

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            if self._active_session is not None:
                await self._finish_active_locked()
            self._closed = True

    async def _rollback_activation(
        self,
        session: _EnrollmentSession,
        failed_profile: SpeakerProfile,
    ) -> None:
        previous = session.previous_profile
        if previous is None:
            try:
                await self._store.adelete()
            finally:
                self._registry.clear_profile()
                await self._activate_profile(None)
            self._persistence_state = ProfileLoadState.EMPTY
            return
        rollback = SpeakerProfile(
            previous.reference_embedding,
            profile_revision=failed_profile.profile_revision + 1,
            model_id=previous.model_id,
            model_revision=previous.model_revision,
            embedding_dimension=previous.embedding_dimension,
        )
        try:
            await self._store.asave(rollback)
            self._registry.install_profile(rollback)
            await self._activate_profile(rollback)
            self._persistence_state = ProfileLoadState.READY
        finally:
            rollback.close()

    async def _restore_previous_runtime_profile(
        self,
        session: _EnrollmentSession,
    ) -> None:
        previous = session.previous_profile
        if previous is None:
            self._registry.clear_profile()
            await self._activate_profile(None)
            return
        rollback = SpeakerProfile(
            previous.reference_embedding,
            profile_revision=self._registry.next_profile_revision(),
            model_id=previous.model_id,
            model_revision=previous.model_revision,
            embedding_dimension=previous.embedding_dimension,
        )
        try:
            self._registry.install_profile(rollback)
            await self._activate_profile(rollback)
        finally:
            rollback.close()

    def _advance_temporary_profile_revision(
        self,
        session: _EnrollmentSession,
    ) -> None:
        previous = session.temporary_profile
        if previous is None:
            return
        replacement = SpeakerProfile(
            previous.reference_embedding,
            profile_revision=self._registry.next_profile_revision(),
            model_id=previous.model_id,
            model_revision=previous.model_revision,
            embedding_dimension=previous.embedding_dimension,
        )
        session.temporary_profile = replacement
        previous.close()

    async def _embed(
        self,
        session: _EnrollmentSession,
        pcm16: bytes,
    ) -> np.ndarray:
        try:
            result = await asyncio.to_thread(
                session.model.embedding_from_pcm16,
                pcm16,
                sample_rate_hz=self._config.sample_rate_hz,
            )
        except Exception as exc:
            raise EnrollmentAudioError(
                "voice identity embedding could not be extracted"
            ) from exc
        embedding = np.array(result, dtype=np.float32, copy=True)
        if (
            embedding.shape != (self._config.embedding_dimension,)
            or not np.isfinite(embedding).all()
        ):
            embedding.fill(0)
            raise EnrollmentAudioError("voice identity embedding is invalid")
        return embedding

    def _validate_pcm(self, pcm16: bytes) -> None:
        if not isinstance(pcm16, bytes) or not pcm16 or len(pcm16) % 2:
            raise EnrollmentAudioError("valid PCM16LE audio is required")
        audio_ms = (
            len(pcm16) // 2 * 1_000 // self._config.sample_rate_hz
        )
        if not self._config.minimum_audio_ms <= audio_ms <= self._config.maximum_audio_ms:
            raise EnrollmentAudioError("PCM16LE audio duration is outside bounds")

    def _require_session_locked(self, session_id: str) -> _EnrollmentSession:
        session = self._active_session
        if session is None or not secrets.compare_digest(
            session.session_id,
            str(session_id or ""),
        ):
            raise EnrollmentNotFoundError("voice identity enrollment was not found")
        return session

    def _status_locked(self) -> EnrollmentStatus:
        session = self._active_session
        fixed_completed = 0
        if session is not None:
            if session.temporary_profile is not None:
                fixed_completed = self._config.fixed_segment_count
            else:
                fixed_completed = len(session.fixed_embeddings)
        return EnrollmentStatus(
            session_id=None if session is None else session.session_id,
            stage=EnrollmentStage.IDLE if session is None else session.stage,
            fixed_completed=fixed_completed,
            verification_completed=(
                0 if session is None else session.verification_completed
            ),
            verification_attempts=(
                0 if session is None else session.verification_attempts
            ),
            profile_state=self._persistence_state,
            profile_revision=self._registry.profile_revision,
            filter_enabled=self._registry.filter_enabled,
            enrollment_active=self._registry.enrollment_active,
        )

    async def _finish_active_locked(self) -> None:
        session, self._active_session = self._active_session, None
        if session is None:
            return
        task = session.expiry_task
        current_task = asyncio.current_task()
        if task is not None and task is not current_task and not task.done():
            task.cancel()
        for embedding in session.fixed_embeddings:
            embedding.fill(0)
        session.fixed_embeddings.clear()
        if session.temporary_profile is not None:
            session.temporary_profile.close()
            session.temporary_profile = None
        if session.previous_profile is not None:
            session.previous_profile.close()
            session.previous_profile = None
        await asyncio.to_thread(session.model.close)
        self._registry.end_enrollment(session.session_id)

    async def _expire_session(self, session_id: str) -> None:
        try:
            await asyncio.sleep(self._config.ttl_seconds)
            async with self._lock:
                session = self._active_session
                if session is not None and secrets.compare_digest(
                    session.session_id,
                    session_id,
                ):
                    await self._finish_active_locked()
        except asyncio.CancelledError:
            return

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("owner enrollment service is closed")
