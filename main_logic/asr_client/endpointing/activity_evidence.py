"""Provider-neutral local activity evidence for voice resource throttling."""

from __future__ import annotations

from dataclasses import dataclass

from main_logic.voice_turn.activity_evidence import RnnoiseEvidence
from main_logic.voice_turn.contracts import SpeechActivityEvent


def _validate_probability(name: str, value: float | None) -> None:
    if value is not None and not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be within [0, 1]")


@dataclass(frozen=True, slots=True)
class SileroEvidence:
    """Latest asynchronous Silero activity known to the throttling policy."""

    available: bool
    activity: SpeechActivityEvent | None = None
    probability: float | None = None

    def __post_init__(self) -> None:
        _validate_probability("probability", self.probability)
        if not self.available and (
            self.activity is not None or self.probability is not None
        ):
            raise ValueError("unavailable Silero evidence cannot carry activity")


@dataclass(frozen=True, slots=True)
class ActivityEvidence:
    """Evidence bundle that advises resources but never endpoint authority."""

    rnnoise: RnnoiseEvidence
    silero: SileroEvidence
