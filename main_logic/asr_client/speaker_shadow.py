"""Compatibility facade for the provider-neutral voice identity runtime."""

from main_logic.voice_identity.contracts import (
    SpeakerShadowBackend,
    SpeakerShadowCandidateKey,
    SpeakerShadowConfig,
    SpeakerShadowObservation,
    SpeakerVerifierFactory,
    SpeakerVerifierRuntime,
)
from main_logic.voice_identity.runtime import (
    SpeakerShadowMetrics,
    SpeakerShadowRuntime,
)

__all__ = [
    "SpeakerShadowBackend",
    "SpeakerShadowCandidateKey",
    "SpeakerShadowConfig",
    "SpeakerShadowMetrics",
    "SpeakerShadowObservation",
    "SpeakerShadowRuntime",
    "SpeakerVerifierFactory",
    "SpeakerVerifierRuntime",
]
