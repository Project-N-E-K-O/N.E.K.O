"""Provider-neutral local activity evidence produced by voice audio input."""

from __future__ import annotations

from dataclasses import dataclass


def _validate_probability(name: str, value: float | None) -> None:
    if value is not None and not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be within [0, 1]")


@dataclass(frozen=True, slots=True)
class RnnoiseEvidence:
    """Statistics from only the RNNoise frames processed for one PCM chunk."""

    available: bool
    frame_count: int
    peak: float | None
    mean: float | None
    last: float | None
    ema: float | None
    baseline: float | None = None

    def __post_init__(self) -> None:
        if self.frame_count < 0:
            raise ValueError("frame_count must not be negative")
        for name in ("peak", "mean", "last", "ema", "baseline"):
            _validate_probability(name, getattr(self, name))
        if not self.available and any(
            value is not None
            for value in (self.peak, self.mean, self.last, self.ema)
        ):
            raise ValueError("unavailable RNNoise evidence cannot carry probabilities")
        if self.frame_count == 0 and any(
            value is not None for value in (self.peak, self.mean, self.last, self.ema)
        ):
            raise ValueError("an empty RNNoise chunk cannot reuse prior probabilities")

    @classmethod
    def unavailable(cls) -> "RnnoiseEvidence":
        return cls(False, 0, None, None, None, None, None)

    @classmethod
    def from_legacy_probability(
        cls,
        probability: float | None,
        *,
        available: bool,
    ) -> "RnnoiseEvidence":
        if not available or probability is None:
            return cls.unavailable()
        value = float(probability)
        return cls(True, 1, value, value, value, value, None)

    def with_baseline(self, baseline: float | None) -> "RnnoiseEvidence":
        return RnnoiseEvidence(
            self.available,
            self.frame_count,
            self.peak,
            self.mean,
            self.last,
            self.ema,
            baseline,
        )
