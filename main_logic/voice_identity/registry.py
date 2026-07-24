"""Process-local owner profile and enrollment lease registry."""

from __future__ import annotations

from threading import Lock

from .profile import SpeakerProfile


class EnrollmentLeaseBusy(RuntimeError):
    """Raised when another enrollment session already owns microphone input."""


class VoiceIdentityProfileRegistry:
    """Own one process-local profile snapshot and one enrollment lease."""

    def __init__(self) -> None:
        self._profile: SpeakerProfile | None = None
        self._last_profile_revision = -1
        self._filter_enabled = False
        self._enrollment_session_id: str | None = None
        self._lock = Lock()

    @property
    def profile_revision(self) -> int | None:
        with self._lock:
            profile = self._profile
            return None if profile is None else profile.profile_revision

    @property
    def filter_enabled(self) -> bool:
        with self._lock:
            return self._filter_enabled

    @property
    def enrollment_active(self) -> bool:
        with self._lock:
            return self._enrollment_session_id is not None

    def enrollment_owned_by(self, session_id: str) -> bool:
        normalized = str(session_id or "").strip()
        with self._lock:
            return bool(normalized and self._enrollment_session_id == normalized)

    def next_profile_revision(self) -> int:
        with self._lock:
            return self._last_profile_revision + 1

    def snapshot_profile(self) -> SpeakerProfile | None:
        with self._lock:
            profile = self._profile
            return None if profile is None else self._copy_profile(profile)

    def install_profile(self, profile: SpeakerProfile) -> None:
        snapshot = self._copy_profile(profile)
        with self._lock:
            previous, self._profile = self._profile, snapshot
            self._last_profile_revision = max(
                self._last_profile_revision,
                profile.profile_revision,
            )
            self._filter_enabled = False
        if previous is not None:
            previous.close()

    def clear_profile(self) -> None:
        with self._lock:
            previous, self._profile = self._profile, None
            self._filter_enabled = False
        if previous is not None:
            previous.close()

    def set_filter_enabled(self, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise TypeError("voice identity filter enabled must be boolean")
        with self._lock:
            if enabled and self._profile is None:
                raise RuntimeError("voice identity profile is unavailable")
            self._filter_enabled = enabled

    def begin_enrollment(self, session_id: str) -> None:
        normalized = str(session_id or "").strip()
        if not normalized:
            raise ValueError("enrollment session_id must not be empty")
        with self._lock:
            if self._enrollment_session_id is not None:
                raise EnrollmentLeaseBusy("voice identity enrollment is busy")
            self._enrollment_session_id = normalized

    def end_enrollment(self, session_id: str) -> bool:
        normalized = str(session_id or "").strip()
        with self._lock:
            if self._enrollment_session_id != normalized:
                return False
            self._enrollment_session_id = None
            return True

    def close(self) -> None:
        with self._lock:
            profile, self._profile = self._profile, None
            self._filter_enabled = False
            self._enrollment_session_id = None
        if profile is not None:
            profile.close()

    @staticmethod
    def _copy_profile(profile: SpeakerProfile) -> SpeakerProfile:
        return SpeakerProfile(
            profile.reference_embedding,
            profile_revision=profile.profile_revision,
            model_id=profile.model_id,
            model_revision=profile.model_revision,
            embedding_dimension=profile.embedding_dimension,
        )


_VOICE_IDENTITY_PROFILE_REGISTRY = VoiceIdentityProfileRegistry()


def get_voice_identity_profile_registry() -> VoiceIdentityProfileRegistry:
    return _VOICE_IDENTITY_PROFILE_REGISTRY
