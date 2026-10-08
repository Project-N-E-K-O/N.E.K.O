"""Neutral wake capability snapshot and unavailable activation authority."""

from dataclasses import dataclass

from .sherpa_backend import WakeWordBackendError


@dataclass(frozen=True, slots=True)
class WakeWordResources:
    enabled: bool
    model_dir: str | None = None
    reason: str | None = None


class UnavailableWakeWordDetector:
    """Preserve configured wake failure as a closed activation authority."""

    inference_timeout_seconds = 2.0
    runtime_info = None

    def __init__(self, reason: str):
        self.reason = reason

    async def prepare(self):
        raise WakeWordBackendError(self.reason)

    async def feed_batch(self, frames, epoch):
        raise WakeWordBackendError(self.reason)

    async def close(self):
        pass
