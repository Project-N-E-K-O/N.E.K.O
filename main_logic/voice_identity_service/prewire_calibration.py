"""Registered calibration adapted to provider-neutral pre-ASR evidence."""

from __future__ import annotations

from .calibration import (
    CalibrationError,
    CalibrationFeatures,
    CalibrationProtocol,
    RegisteredCalibration,
    _nonempty,
    _validated_artifact_sha256,
)
from .prewire_gate.decision import (
    CalibratedIdentityEvidence,
    CalibratedIdentityOutcome,
    PrewireScoreObservation,
)


class PrewireCalibrationClassifier:
    """Adapt one registered package to the prewire evidence contract.

    The adapter is intentionally fail-closed.  A score is only calibrated when
    all session/model/config identity fences and the scoring-parameter digest
    match the values captured when this classifier was constructed.  Missing
    quality evidence is represented by the package's explicit missing-feature
    indicators, while a missing duration or a discontinuous event is rejected
    before calibration.

    This sibling adapter joins calibration and prewire contracts without
    making either foundational package depend on the other.
    """

    def __init__(
        self,
        registered: RegisteredCalibration,
        *,
        runtime_protocol: CalibrationProtocol,
        profile_generation: str,
        model_generation: str,
        config_generation: str,
        parameters_digest: str,
        sample_rate: int = 16_000,
    ) -> None:
        if type(registered) is not RegisteredCalibration:
            raise TypeError("registered must be RegisteredCalibration")
        if registered.package.release_status != "registered":
            raise CalibrationError("calibration package is not released for runtime use")
        if type(runtime_protocol) is not CalibrationProtocol:
            raise TypeError("runtime_protocol must be CalibrationProtocol")
        for name, value in (
            ("profile_generation", profile_generation),
            ("model_generation", model_generation),
            ("config_generation", config_generation),
        ):
            _nonempty(name, value)
        _validated_artifact_sha256("parameters_digest", parameters_digest)
        if type(sample_rate) is not int or sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        self._registered = registered
        self._runtime_protocol = runtime_protocol
        self._profile_generation = profile_generation
        self._model_generation = model_generation
        self._config_generation = config_generation
        self._parameters_digest = parameters_digest
        self._sample_rate = sample_rate

    @property
    def registered(self) -> RegisteredCalibration:
        return self._registered

    @property
    def parameters_digest(self) -> str:
        return self._parameters_digest

    @property
    def calibration_digest(self) -> str:
        """Digest of the immutable package admitted by the app allowlist."""

        return self._registered.artifact_sha256

    def classify(self, observation: "PrewireScoreObservation") -> "CalibratedIdentityEvidence":
        """Classify one prewire observation without ever authorizing on error."""

        def failure(reason: str) -> CalibratedIdentityEvidence:
            return CalibratedIdentityEvidence(CalibratedIdentityOutcome.FAILURE, reason)

        try:
            for name, expected in (
                ("profile_generation", self._profile_generation),
                ("model_generation", self._model_generation),
                ("config_generation", self._config_generation),
                ("parameters_digest", self._parameters_digest),
            ):
                if getattr(observation, name) != expected:
                    return failure(f"{name}_mismatch")
            quality = observation.quality
            if not quality.continuous:
                return failure("discontinuous_scoring_audio")
            samples = quality.speech_samples
            if samples is None or type(samples) is not int or samples <= 0:
                return failure("missing_speech_samples")
            scoring_samples = observation.scoring_range.sample_count
            if samples > scoring_samples:
                return failure("speech_samples_exceed_scoring_range")
            # The fitting contract measures the captured candidate duration,
            # not a VAD-shortened/reordered concatenation of active samples.
            audio_ms = scoring_samples * 1000.0 / self._sample_rate
            features = CalibrationFeatures(
                raw_similarity=observation.raw_similarity,
                audio_ms=audio_ms,
                rms=quality.rms,
                peak=quality.peak,
                near_silence=quality.near_silence,
                clipping=quality.clipping,
            )
            result = self._registered.package.classify(
                features,
                self._runtime_protocol,
            )
        except (AttributeError, TypeError, ValueError, CalibrationError):
            return failure("invalid_calibration_observation")
        try:
            outcome = CalibratedIdentityOutcome(result.outcome.value)
        except (AttributeError, ValueError):
            return failure("unsupported_calibration_outcome")
        return CalibratedIdentityEvidence(outcome, result.reason)

