"""Provider-neutral model identity contracts for speaker references."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class VoiceIdentityActivationResult(StrEnum):
    """Outcome of applying a profile to the currently active ASR route."""

    READY = "ready"
    UNSUPPORTED_ASR_ROUTE = "unsupported_asr_route"
    RUNTIME_DEGRADED = "runtime_degraded"

    def __bool__(self) -> bool:
        """Treat a future-route install as applied even when not active yet."""

        return self is not VoiceIdentityActivationResult.RUNTIME_DEGRADED


@dataclass(frozen=True, slots=True)
class SpeakerModelIdentity:
    """Immutable identity and output shape of an embedding model."""

    model_id: str
    model_revision: str
    embedding_dimension: int

    def __post_init__(self) -> None:
        if type(self.model_id) is not str or not self.model_id.strip():
            raise ValueError("model_id must be a non-empty string")
        if type(self.model_revision) is not str or not self.model_revision.strip():
            raise ValueError("model_revision must be a non-empty string")
        if (
            type(self.embedding_dimension) is not int
            or self.embedding_dimension <= 0
        ):
            raise ValueError("embedding_dimension must be a positive integer")
