"""Host-turn observations used by the co-stream interaction policy."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

HostTurnState = Literal["speaking", "likely_holding", "yielded", "unknown"]
HostTurnReliability = Literal["reliable", "degraded", "unavailable"]
HostTurnSource = Literal["host_runtime", "platform", "fallback"]
DEFAULT_YIELDED_SIGNAL_TTL_SECONDS = 5.0


@dataclass(frozen=True)
class HostTurnSignal:
    """Normalized observation; it never decides whether NEKO may speak."""

    state: HostTurnState
    confidence: float
    reliability: HostTurnReliability
    observed_at: float
    source: HostTurnSource


class HostTurnSignalStore:
    """Hold the latest normalized signal supplied by the host runtime."""

    def __init__(
        self,
        *,
        now: Callable[[], float] = time.monotonic,
        yielded_ttl_seconds: float = DEFAULT_YIELDED_SIGNAL_TTL_SECONDS,
    ) -> None:
        self._now = now
        self._yielded_ttl_seconds = max(0.0, float(yielded_ttl_seconds))
        self._signal: HostTurnSignal | None = None

    def current(self) -> HostTurnSignal:
        signal = self._signal
        if signal is None:
            return self._unknown_signal()
        if signal.state == "yielded":
            age = self._now() - signal.observed_at
            if not math.isfinite(age) or age < 0.0 or age > self._yielded_ttl_seconds:
                return self._unknown_signal()
        return signal

    def update(self, signal: HostTurnSignal) -> None:
        self._signal = signal

    def reset(self) -> None:
        self._signal = None

    def _unknown_signal(self) -> HostTurnSignal:
        return HostTurnSignal(
            state="unknown",
            confidence=0.0,
            reliability="unavailable",
            observed_at=self._now(),
            source="fallback",
        )


__all__ = [
    "DEFAULT_YIELDED_SIGNAL_TTL_SECONDS",
    "HostTurnReliability",
    "HostTurnSignal",
    "HostTurnSource",
    "HostTurnState",
    "HostTurnSignalStore",
]
