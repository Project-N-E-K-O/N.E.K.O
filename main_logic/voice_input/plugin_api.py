"""Core-side plugin voice-input SPI; no SDK or process wiring lives here."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .contracts import (
    VoiceInputConsumer,
    VoiceInputConsumerCapabilities,
    VoiceInputRegistration,
)


@runtime_checkable
class PluginVoiceInputConsumer(VoiceInputConsumer, Protocol):
    """Consumer shape implemented by a future trusted plugin bridge."""


@runtime_checkable
class PluginVoiceInputRegistrar(Protocol):
    """Plugin-facing registration surface with a host-fixed identity."""

    def register_consumer(
        self,
        consumer: PluginVoiceInputConsumer,
        *,
        capabilities: VoiceInputConsumerCapabilities | None = None,
    ) -> VoiceInputRegistration: ...
