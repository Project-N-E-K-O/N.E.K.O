"""Lightweight type contracts for Core voice-session activation wiring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from main_logic.voice_turn.contracts import VoiceIngressToken

from .contracts import (
    ActivationDecision,
    ActivationGeneration,
    ActivationState,
    AudioFrame,
    OutputCommit,
)


@dataclass(frozen=True, slots=True)
class VoiceSessionActivationRouteContext:
    speech_probability: float | None
    rnnoise_available: bool | None
    rnnoise_evidence: object | None
    ingress_token: VoiceIngressToken | None
    captured_at: float | None


class VoiceSessionActivationRuntime(Protocol):
    generation: ActivationGeneration
    state: ActivationState

    @property
    def pending_output_bytes(self) -> int: ...

    @property
    def verification_inflight(self) -> bool: ...

    @property
    def last_voice_at(self) -> float | None: ...

    @property
    def idle_deadline(self) -> float | None: ...

    @property
    def output_inflight(self) -> bool: ...

    @property
    def output_paused(self) -> bool: ...

    def set_capture_progress_provider(
        self, callback: Callable[[], float | None]
    ) -> None: ...

    async def pause_output(self, owner: object, *, deadline: float) -> bool: ...

    async def resume_output(self, owner: object) -> bool: ...

    def complete_output_handoff(self, owner: object) -> bool: ...

    async def fail_output(self, owner: object, reason: str) -> None: ...

    async def prepare(self) -> ActivationDecision: ...

    async def feed(
        self,
        frame: AudioFrame,
        *,
        voice_activity: bool,
    ) -> ActivationDecision: ...

    async def close(self) -> None: ...


class VoiceSessionActivationFactory(Protocol):
    activation_generation: str

    def create(
        self,
        generation: ActivationGeneration,
        output: Callable[[AudioFrame], Awaitable[OutputCommit]],
        *,
        status_callback: Callable[[ActivationDecision], None] | None = None,
    ) -> VoiceSessionActivationRuntime: ...

    def close(self) -> None: ...


__all__ = [
    "VoiceSessionActivationFactory",
    "VoiceSessionActivationRouteContext",
    "VoiceSessionActivationRuntime",
]
