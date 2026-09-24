"""Voice-session activation state, buffering, and delivery contracts."""

from .buffer import BoundedAudioFrameBuffer, FrameRangeUnavailable
from .contracts import (
    ActivationDecision,
    ActivationGeneration,
    ActivationState,
    AudioFrame,
    AudioFrameSink,
    CandidateWindow,
    OutputCommit,
    OutputLease,
    OutputOrigin,
    SpeakerVerifier,
    VerificationInput,
    VerificationRequest,
    VerificationResultKind,
)
from .controller import VoiceActivationConfig, VoiceActivationController

__all__ = [
    "ActivationDecision",
    "ActivationGeneration",
    "ActivationState",
    "AudioFrame",
    "AudioFrameSink",
    "BoundedAudioFrameBuffer",
    "CandidateWindow",
    "FrameRangeUnavailable",
    "OutputCommit",
    "OutputLease",
    "OutputOrigin",
    "SpeakerVerifier",
    "VerificationInput",
    "VerificationRequest",
    "VerificationResultKind",
    "VoiceActivationConfig",
    "VoiceActivationController",
]
