"""Namespace-bound registrar used by trusted host integration bridges."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .contracts import (
    VoiceInputConsumer,
    VoiceInputConsumerCapabilities,
    VoiceInputConsumerIdentity,
    VoiceInputRegistration,
)

if TYPE_CHECKING:
    from .registry import VoiceInputRegistry


class VoiceInputRegistrar:
    """Register one consumer without exposing the underlying registry."""

    def __init__(
        self,
        registry: VoiceInputRegistry,
        identity: VoiceInputConsumerIdentity,
    ) -> None:
        self._registry = registry
        self._identity = identity
        self._registration: VoiceInputRegistration | None = None

    def register_consumer(
        self,
        consumer: VoiceInputConsumer,
        *,
        capabilities: VoiceInputConsumerCapabilities | None = None,
    ) -> VoiceInputRegistration:
        registration = self._registration
        if registration is not None and not registration.closed:
            raise RuntimeError("VOICE_INPUT_CONSUMER_ALREADY_REGISTERED")
        registration = self._registry._register_plugin(
            self._identity,
            consumer,
            capabilities or VoiceInputConsumerCapabilities(),
        )
        self._registration = registration
        return registration
