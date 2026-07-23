"""Consumer-neutral contracts for routing Core voice-input transcripts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

from main_logic.voice_turn.contracts import (
    VoicePartialEvent,
    VoiceTranscriptEvent,
    VoiceTurnToken,
)


class BuiltinVoiceInputConsumer(str, Enum):
    """Host-owned consumers that may be selected without plugin authority."""

    CORE_CHAT = "core_chat"
    GAME = "game"


@dataclass(frozen=True, slots=True)
class VoiceInputConsumerIdentity:
    """Namespaced display identity; routing authority lives in the handle."""

    namespace: str
    name: str


@dataclass(frozen=True, slots=True)
class VoiceInputConsumerCapabilities:
    """Transcript event kinds accepted by one registered consumer."""

    accepts_partial: bool = False
    accepts_final: bool = True


@dataclass(frozen=True, slots=True)
class VoiceInputConsumerHandle:
    """Opaque registry-issued capability used for activation."""

    identity: VoiceInputConsumerIdentity
    _registry_token: object = field(repr=False, compare=False)
    _registration_token: object = field(repr=False, compare=False)


@runtime_checkable
class VoiceInputConsumer(Protocol):
    """Core-side delivery contract shared by built-ins and future plugins."""

    def is_available(self) -> bool: ...

    async def prepare_turn(self, token: VoiceTurnToken) -> bool: ...

    async def on_partial(self, event: VoicePartialEvent) -> None: ...

    async def on_final(self, event: VoiceTranscriptEvent) -> None: ...

    async def on_cancelled(self, token: VoiceTurnToken, reason: str) -> None: ...


class VoiceInputRegistration:
    """Lifecycle owner for one registry-issued consumer handle."""

    __slots__ = ("_close_callback", "_closed", "handle")

    def __init__(
        self,
        handle: VoiceInputConsumerHandle,
        close_callback: Callable[[], bool],
    ) -> None:
        self.handle = handle
        self._close_callback = close_callback
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> bool:
        if self._closed:
            return False
        self._closed = True
        return self._close_callback()


ConsumerPrepareCallback = Callable[[VoiceTurnToken], Awaitable[bool]]
ConsumerPartialCallback = Callable[[VoicePartialEvent], Awaitable[None]]
ConsumerFinalCallback = Callable[[VoiceTranscriptEvent], Awaitable[None]]
ConsumerCancelledCallback = Callable[[VoiceTurnToken, str], Awaitable[None]]
