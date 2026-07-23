"""Controlled ASR consumer registration and transcript routing."""

from .contracts import (
    BuiltinVoiceInputConsumer,
    VoiceInputConsumer,
    VoiceInputConsumerCapabilities,
    VoiceInputConsumerHandle,
    VoiceInputConsumerIdentity,
    VoiceInputRegistration,
)
from .registry import VoiceInputHandleError, VoiceInputRegistry

__all__ = [
    "BuiltinVoiceInputConsumer",
    "VoiceInputConsumer",
    "VoiceInputConsumerCapabilities",
    "VoiceInputConsumerHandle",
    "VoiceInputConsumerIdentity",
    "VoiceInputHandleError",
    "VoiceInputRegistration",
    "VoiceInputRegistry",
]
