from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from unittest.mock import AsyncMock
from main_logic.voice_identity_service.activation_runtime import VoiceSessionActivationRuntime
from main_logic.voice_input.activation import ActivationState
from main_logic.voice_turn.contracts import AsrSubmitResult, AsrSubmitStatus
from tests.support.asr_fakes import _Runtime

from tests.support.activation_handoff_fakes import (
    _Clock,
    _Factory,
    _Session,
    _until,
)


@dataclass
class _Harness:
    manager: _Runtime
    clock: _Clock
    factory: _Factory
    route: str
    deliveries: list[tuple[str, bytes]] = field(default_factory=list)

    @property
    def activation(self) -> VoiceSessionActivationRuntime:
        return self.factory.runtimes[0]

    @property
    def pcm(self) -> list[bytes]:
        return [pcm for _, pcm in self.deliveries]

    def session(self, name: str) -> _Session:
        return _Session(name, self.deliveries)

    async def submit(self, frame, **_kwargs) -> AsrSubmitResult:
        self.deliveries.append(("independent", frame.pcm16))
        return AsrSubmitResult(AsrSubmitStatus.ACCEPTED)

    async def feed(self, marker: int, *, voice: bool = True) -> bytes:
        pcm = marker.to_bytes(2, "little", signed=True) * 1_600
        await self.manager._route_microphone_audio(
            pcm,
            sample_rate_hz=16_000,
            speech_probability=0.9 if voice else 0.0,
            received_at=self.clock.value,
            captured_at=self.clock.value,
        )
        await asyncio.sleep(0)
        return pcm

    async def promote(self, ticket, target: _Session) -> None:
        assert self.manager._voice_activation_handoff_is_current(ticket)
        assert self.manager._mark_voice_activation_handoff_irreversible(ticket)
        self.manager.session = target
        await self.manager._reconcile_independent_asr_after_core_change()
        assert self.manager._voice_activation_handoff_is_current(
            ticket, allow_promoted=True
        )
        assert await self.manager._commit_voice_activation_handoff(ticket)


@asynccontextmanager
async def _harness(route: str, *, active: bool = True):
    manager = _Runtime()
    clock = _Clock()
    factory = _Factory(clock)
    harness = _Harness(manager, clock, factory, route)
    manager.is_active = True
    manager.core_api_type = "qwen"
    manager._independent_asr_route_key = "qwen"
    manager.session = harness.session("source")
    manager._set_microphone_route(route)
    manager._asr_runtime.submit = AsyncMock(side_effect=harness.submit)
    await manager.set_voice_session_activation_factory(
        factory, activation_generation="profile"
    )
    try:
        for index in range(15 if active else 1):
            clock.value = 100.0 + index / 10
            await harness.feed(2_000 + index)
        expected_state = ActivationState.ACTIVE if active else ActivationState.WAITING
        await _until(lambda: harness.activation.state is expected_state)
        if active:
            await _until(lambda: len(harness.deliveries) == 15)
        yield harness
    finally:
        await manager.set_voice_session_activation_factory(
            None, activation_generation="test-finished"
        )
        pending = tuple(manager._core_asr_cleanup_tasks)
        if pending:
            async with asyncio.timeout(2.0):
                await asyncio.gather(*pending, return_exceptions=True)
