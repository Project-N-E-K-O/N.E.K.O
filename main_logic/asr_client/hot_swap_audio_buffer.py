"""Turn-safe microphone buffering while the Core/ASR route is hot-swapped."""

from __future__ import annotations

from dataclasses import dataclass

from .audio_ring_buffer import AudioRingBuffer
from .lifecycle_contracts import VoiceIngressToken


@dataclass(frozen=True, slots=True)
class HotSwapAudioFrame:
    """One normalized PCM16 frame with the identity captured at ingress."""

    pcm16: bytes
    token: VoiceIngressToken
    speech_probability: float | None = None
    rnnoise_available: bool = False


class HotSwapAudioBuffer:
    """Bound hot-swap PCM by duration without silently dropping middle audio."""

    def __init__(self, *, capacity_ms: int = 8_000) -> None:
        self._audio = AudioRingBuffer(capacity_ms=capacity_ms)
        self._frames: list[HotSwapAudioFrame] = []

    @property
    def duration_ms(self) -> int:
        return self._audio.duration_ms

    def append(self, frame: HotSwapAudioFrame) -> bool:
        """Append a frame, or clear the whole candidate when capacity overflows."""

        dropped = self._audio.append(frame.pcm16, sample_rate_hz=16_000)
        if dropped:
            self.clear()
            return False
        self._frames.append(frame)
        return True

    def drain(self) -> tuple[HotSwapAudioFrame, ...]:
        frames = tuple(self._frames)
        self._frames.clear()
        self._audio.clear()
        return frames

    def clear(self) -> None:
        self._frames.clear()
        self._audio.clear()

    def __bool__(self) -> bool:
        return bool(self._frames)

    def __len__(self) -> int:
        return len(self._frames)
