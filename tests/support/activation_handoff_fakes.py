from __future__ import annotations
import asyncio
from unittest.mock import AsyncMock, MagicMock
from main_logic.voice_identity_service.activation_runtime import VoiceSessionActivationRuntime
from main_logic.voice_input.activation import VoiceActivationController
from tests.support.asr_fakes import _CoreActivationScorer


class _Clock:
    value = 100.0

    def __call__(self) -> float:
        return self.value


class _Factory:
    activation_generation = "profile"

    def __init__(self, clock: _Clock) -> None:
        self.clock = clock
        self.runtimes: list[VoiceSessionActivationRuntime] = []
        self.scorers: list[_CoreActivationScorer] = []

    def create(self, generation, output, *, status_callback=None):
        scorer = _CoreActivationScorer()
        runtime = VoiceSessionActivationRuntime(
            generation,
            scorer,
            output,
            controller=VoiceActivationController(clock=self.clock),
            status_callback=status_callback,
        )
        self.scorers.append(scorer)
        self.runtimes.append(runtime)
        return runtime

    def close(self) -> None:
        pass


class _Session:
    def __init__(self, name: str, deliveries: list[tuple[str, bytes]]) -> None:
        self.name = name
        self.can_handoff_voice_input = MagicMock(return_value=True)
        self.stream_audio = AsyncMock(side_effect=self._stream)
        self._deliveries = deliveries

    async def _stream(self, pcm: bytes) -> None:
        self._deliveries.append((self.name, pcm))


async def _until(predicate) -> None:
    async with asyncio.timeout(2.0):
        while not predicate():
            await asyncio.sleep(0)
