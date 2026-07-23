"""Provider-neutral in-memory voice identity foundation."""

from .contracts import (
    SpeakerObservationCallback,
    SpeakerShadowCandidateKey,
    SpeakerShadowConfig,
    SpeakerShadowObservation,
    SpeakerVerifierFactory,
    SpeakerVerifierRuntime,
)
from .profile import SpeakerProfile
from .runtime import VoiceIdentitySession


def create_voice_identity_session(
    *,
    asset_dir=None,
    on_observation: SpeakerObservationCallback | None = None,
) -> VoiceIdentitySession:
    """Create the configured verifier without exposing model details to Core."""

    from .campplus import create_campplus_voice_identity_session as create

    return create(asset_dir=asset_dir, on_observation=on_observation)

__all__ = [
    "SpeakerProfile",
    "SpeakerObservationCallback",
    "SpeakerShadowCandidateKey",
    "SpeakerShadowConfig",
    "SpeakerShadowObservation",
    "SpeakerVerifierFactory",
    "SpeakerVerifierRuntime",
    "VoiceIdentitySession",
    "create_voice_identity_session",
]
