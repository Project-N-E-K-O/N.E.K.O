"""Stateless Owner-speaker classification for independent-ASR evidence."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from main_logic.asr_client.speaker_shadow.contracts import (
    SpeakerShadowObservationKind,
)

from .calibration import (
    CalibrationFeatures,
    CalibrationOutcome,
    CalibrationPackage,
    CalibrationProtocol,
    RegisteredCalibration,
    calibration_package_artifact_sha256,
)


class OwnerVoiceClassification(StrEnum):
    LOW = "low"
    HIGH = "high"
    INSUFFICIENT = "insufficient"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class OwnerVoicePolicyResult:
    classification: OwnerVoiceClassification
    reason: str
    calibration_outcome: CalibrationOutcome | None = None


class OwnerVoicePolicy:
    """Classify one immutable score without retaining candidate state."""

    FIRST_CHECKPOINT_MS = 1_500
    SECOND_CHECKPOINT_MS = 3_000
    SIMILARITY_THRESHOLD = 0.40

    @classmethod
    def classify(
        cls,
        *,
        checkpoint_ms: int | None,
        similarity: float,
        observation_kind: SpeakerShadowObservationKind = "checkpoint",
        audio_ms: int | None = None,
        calibration_package: CalibrationPackage | None = None,
        registered_calibration: RegisteredCalibration | None = None,
        runtime_protocol: CalibrationProtocol | None = None,
        rms: float | None = None,
        peak: float | None = None,
        near_silence: float | None = None,
        clipping: float | None = None,
    ) -> OwnerVoicePolicyResult:
        if (
            type(observation_kind) is not str
            or observation_kind
            not in ("checkpoint", "completion_confirmation", "terminal_short")
            or type(similarity) not in {int, float}
            or not math.isfinite(float(similarity))
            or not -1.0 <= float(similarity) <= 1.0
        ):
            return OwnerVoicePolicyResult(
                OwnerVoiceClassification.UNAVAILABLE,
                "invalid_observation",
            )

        if observation_kind == "terminal_short":
            if not (
                checkpoint_ms is None
                and type(audio_ms) is int
                and 0 < audio_ms < cls.FIRST_CHECKPOINT_MS
            ):
                return OwnerVoicePolicyResult(
                    OwnerVoiceClassification.UNAVAILABLE,
                    "invalid_observation",
                )
            if calibration_package is None:
                if registered_calibration is not None:
                    return OwnerVoicePolicyResult(
                        OwnerVoiceClassification.UNAVAILABLE,
                        "registered_calibration_mismatch",
                    )
                return OwnerVoicePolicyResult(
                    OwnerVoiceClassification.INSUFFICIENT,
                    "terminal_short_observation_only",
                )
            if runtime_protocol is None:
                return OwnerVoicePolicyResult(
                    OwnerVoiceClassification.UNAVAILABLE,
                    "calibration_protocol_unavailable",
                )
            try:
                calibrated = calibration_package.classify(
                    CalibrationFeatures(
                        raw_similarity=float(similarity),
                        audio_ms=float(audio_ms),
                        rms=rms,
                        peak=peak,
                        near_silence=near_silence,
                        clipping=clipping,
                    ),
                    runtime_protocol,
                )
            except Exception:
                return OwnerVoicePolicyResult(
                    OwnerVoiceClassification.UNAVAILABLE,
                    "calibration_failure",
                )
            if registered_calibration is None:
                return OwnerVoicePolicyResult(
                    OwnerVoiceClassification.INSUFFICIENT,
                    "terminal_short_observation_only",
                    calibrated.outcome,
                )
            if (
                type(registered_calibration) is not RegisteredCalibration
                or registered_calibration.package != calibration_package
                or registered_calibration.artifact_sha256
                != calibration_package_artifact_sha256(calibration_package)
            ):
                return OwnerVoicePolicyResult(
                    OwnerVoiceClassification.UNAVAILABLE,
                    "registered_calibration_mismatch",
                    calibrated.outcome,
                )
            if calibrated.outcome is CalibrationOutcome.OWNER:
                classification = OwnerVoiceClassification.HIGH
            elif calibrated.outcome is CalibrationOutcome.NONOWNER:
                classification = OwnerVoiceClassification.LOW
            elif calibrated.outcome is CalibrationOutcome.UNCERTAIN:
                classification = OwnerVoiceClassification.INSUFFICIENT
            else:
                classification = OwnerVoiceClassification.UNAVAILABLE
            return OwnerVoicePolicyResult(
                classification,
                calibrated.reason,
                calibrated.outcome,
            )

        if observation_kind == "completion_confirmation":
            valid_checkpoint = bool(
                type(checkpoint_ms) is int
                and checkpoint_ms == cls.FIRST_CHECKPOINT_MS
                and type(audio_ms) is int
                and cls.FIRST_CHECKPOINT_MS
                < audio_ms
                < cls.SECOND_CHECKPOINT_MS
            )
        else:
            valid_checkpoint = bool(
                type(checkpoint_ms) is int
                and checkpoint_ms
                in {cls.FIRST_CHECKPOINT_MS, cls.SECOND_CHECKPOINT_MS}
            )
        if not valid_checkpoint:
            return OwnerVoicePolicyResult(
                OwnerVoiceClassification.UNAVAILABLE,
                "invalid_observation",
            )
        if float(similarity) < cls.SIMILARITY_THRESHOLD:
            return OwnerVoicePolicyResult(
                OwnerVoiceClassification.LOW,
                "clear_mismatch",
            )
        return OwnerVoicePolicyResult(
            OwnerVoiceClassification.HIGH,
            "owner_or_uncertain",
        )


__all__ = [
    "OwnerVoiceClassification",
    "OwnerVoicePolicy",
    "OwnerVoicePolicyResult",
]
