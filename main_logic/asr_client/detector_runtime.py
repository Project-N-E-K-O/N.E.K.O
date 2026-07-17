"""Session-level local activity detector kept alive across ASR transport idle."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from main_logic.voice_turn.contracts import SmartTurnConfig, SpeechActivityEvent
from main_logic.voice_turn.silero_vad import SileroActivityGate, SileroVad


@dataclass(frozen=True, slots=True)
class DetectorFeedResult:
    events: tuple[SpeechActivityEvent, ...]
    throttle_available: bool


class DetectorRuntime:
    """Serialize Silero loading and inference without owning an ASR session."""

    def __init__(
        self,
        *,
        vad: SileroVad | None = None,
        gate: SileroActivityGate | None = None,
    ) -> None:
        if vad is None:
            config = SmartTurnConfig(enabled=True)
            vad = SileroVad(
                enabled=True,
                inference_error_limit=config.inference_error_limit,
            )
            gate = SileroActivityGate(vad, config)
        if gate is None:
            raise ValueError("DetectorRuntime gate is required with a custom VAD")
        self._vad = vad
        self._gate = gate
        self._lock = asyncio.Lock()
        self._load_attempted = False
        self._available = True
        self._closed = False

    async def feed(self, pcm16: bytes) -> DetectorFeedResult:
        if not isinstance(pcm16, bytes) or len(pcm16) % 2:
            raise ValueError("DetectorRuntime requires complete PCM16 bytes")
        if not pcm16:
            return DetectorFeedResult((), self._available)
        async with self._lock:
            if self._closed or not self._available:
                return DetectorFeedResult((), False)
            if not self._load_attempted:
                self._load_attempted = True
                try:
                    self._available = bool(await asyncio.to_thread(self._vad.load))
                except Exception:
                    self._available = False
                if not self._available:
                    return DetectorFeedResult((), False)
            try:
                events = tuple(await asyncio.to_thread(self._gate.feed, pcm16))
            except Exception:
                self._available = False
                return DetectorFeedResult((), False)
        return DetectorFeedResult(events, True)

    async def reset(self) -> None:
        async with self._lock:
            if self._closed:
                return
            await asyncio.to_thread(self._gate.reset)

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            await asyncio.to_thread(self._vad.close)
