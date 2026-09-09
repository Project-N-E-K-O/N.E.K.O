"""Target activity observations, deliberately without identity/ASR authority.

The legacy speaker worker transports a scalar score and display milliseconds.
It cannot carry sample-exact coverage. Consequently its pVAD observations are
research evidence only, even when activity is detected. A future validated
negative-evidence rule and interval-aware transport are release prerequisites.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math

from main_logic.asr_client.speaker_shadow.contracts import SpeakerShadowObservation

MINIMUM_SHORT_SAMPLES = 3_200
FIRST_CHECKPOINT_SAMPLES = 24_000
PVAD_FRAME_SAMPLES = 160
PVAD_OBSERVATION_SCOPES = ("provider_candidate", "smart_turn_turn")
PVAD_ACTIVITY_THRESHOLD = 0.5  # Activity suggestion, never a nonowner boundary.


class PvadMode(StrEnum):
    OFF = "off"
    OBSERVE = "observe"
    ENFORCE = "enforce"


class PvadEvidenceKind(StrEnum):
    OWNER_ACTIVITY = "owner_activity"
    # Reserved for a separately validated rule; absence of activity is not this.
    NEGATIVE_EVIDENCE = "negative_evidence"
    INSUFFICIENT = "insufficient"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class PvadActivityEvidence:
    kind: PvadEvidenceKind
    reason: str
    target_activity_score: float | None = None
    input_sample_count: int | None = None
    # Half-open offsets in this candidate only. None explicitly means unknown;
    # never reconstruct a sample range from rounded/truncated display audio_ms.
    covered_start_sample: int | None = None
    covered_end_sample: int | None = None

    @property
    def uncovered_tail_samples(self) -> int | None:
        if self.input_sample_count is None or self.covered_end_sample is None:
            return None
        return self.input_sample_count - self.covered_end_sample


def _finite_unit_interval(value: object) -> bool:
    return (
        type(value) in {int, float}
        and math.isfinite(float(value))
        and 0.0 <= float(value) <= 1.0
    )


def observe_pvad_score(
    score: object,
    *,
    input_sample_count: int | None = None,
    rms: float | None = None,
    clipping: float | None = None,
) -> PvadActivityEvidence:
    """Describe measured activity; no result grants an identity verdict."""

    if input_sample_count is not None and (
        type(input_sample_count) is not int
        or not MINIMUM_SHORT_SAMPLES <= input_sample_count < FIRST_CHECKPOINT_SAMPLES
    ):
        return PvadActivityEvidence(
            PvadEvidenceKind.UNAVAILABLE, "pvad_unsupported_samples"
        )
    if not _finite_unit_interval(score) or any(
        value is not None and not _finite_unit_interval(value)
        for value in (rms, clipping)
    ):
        return PvadActivityEvidence(
            PvadEvidenceKind.UNAVAILABLE, "invalid_pvad_observation"
        )
    coverage = (
        {}
        if input_sample_count is None
        else {
            "input_sample_count": input_sample_count,
            "covered_start_sample": 0,
            "covered_end_sample": input_sample_count
            // PVAD_FRAME_SAMPLES
            * PVAD_FRAME_SAMPLES,
        }
    )
    if (rms is not None and rms < 0.008) or (clipping is not None and clipping > 0.05):
        return PvadActivityEvidence(
            PvadEvidenceKind.INSUFFICIENT,
            "pvad_audio_quality",
            float(score),
            **coverage,
        )
    if float(score) < PVAD_ACTIVITY_THRESHOLD:
        return PvadActivityEvidence(
            PvadEvidenceKind.INSUFFICIENT,
            "pvad_no_validated_negative_evidence",
            float(score),
            **coverage,
        )
    return PvadActivityEvidence(
        PvadEvidenceKind.OWNER_ACTIVITY,
        "pvad_target_activity_observed",
        float(score),
        **coverage,
    )


def classify_pvad_observation(event: SpeakerShadowObservation) -> PvadActivityEvidence:
    """Read the legacy scalar callback without inventing interval coverage.

    The backend checks duration from actual PCM. audio_ms is display metadata;
    it is intentionally not accepted as proof of the scoring sample range.
    """

    if (
        type(event) is not SpeakerShadowObservation
        or event.observation_kind != "terminal_short"
        or event.checkpoint_ms is not None
        or event.candidate.scope not in PVAD_OBSERVATION_SCOPES
    ):
        return PvadActivityEvidence(
            PvadEvidenceKind.UNAVAILABLE, "invalid_pvad_observation"
        )
    if not event.evidence_available:
        return PvadActivityEvidence(
            PvadEvidenceKind.UNAVAILABLE,
            "pvad_unsupported_samples"
            if event.unavailable_reason == "unsupported"
            else "pvad_model_unavailable",
        )
    return observe_pvad_score(event.similarity, rms=event.rms, clipping=event.clipping)


__all__ = [
    "FIRST_CHECKPOINT_SAMPLES",
    "MINIMUM_SHORT_SAMPLES",
    "PVAD_FRAME_SAMPLES",
    "PVAD_OBSERVATION_SCOPES",
    "PvadMode",
    "PvadEvidenceKind",
    "PvadActivityEvidence",
    "classify_pvad_observation",
    "observe_pvad_score",
]
