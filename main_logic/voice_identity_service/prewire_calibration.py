"""Registered calibration adapted to provider-neutral pre-ASR evidence."""

from __future__ import annotations

from .calibration import (
    CalibrationError,
    CalibrationFeatures,
    CalibrationProtocol,
    CONTINUOUS_INTERCEPTION_SCOPE,
    RegisteredCalibration,
    _nonempty,
    _validated_artifact_sha256,
)
from .prewire_gate.decision import (
    CalibratedIdentityEvidence,
    CalibratedIdentityOutcome,
    PrewireScoreObservation,
)
from .prewire_gate.scheduler import ScoringWindowPlan


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
        scoring_sample_counts: tuple[int, ...] | None = None,
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
        self._scoring_sample_counts = (
            None if scoring_sample_counts is None
            else ScoringWindowPlan(scoring_sample_counts).sample_counts
        )

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

    @property
    def application_scope(self) -> str:
        """Actual registered scope; a duration allowlist never changes it."""

        return self._registered.package.protocol.application_scope

    @property
    def scoring_sample_counts(self) -> tuple[int, ...] | None:
        return self._scoring_sample_counts

    def require_continuous_support(
        self, sample_counts: tuple[int, ...], *, parameters_digest: str,
        profile_generation: str | None = None,
        model_generation: str | None = None,
        config_generation: str | None = None,
        decision_sample_ranges: tuple[tuple[int, int, int], ...] | None = None,
    ) -> None:
        """Reject production continuous wiring without matching released evidence.

        Existing terminal-short packages remain valid for their original use,
        but cannot certify continuous intervals even at an identical duration.
        Scope, data protocol, lengths, decision geometry and parameters are part of the immutable
        registered artifact. Adapter configuration cannot relabel a package.
        """

        plan = ScoringWindowPlan(sample_counts)
        _validated_artifact_sha256("parameters_digest", parameters_digest)
        if self._scoring_sample_counts is None:
            raise CalibrationError("continuous_scoring_plan_unavailable")
        if plan.sample_counts != self._scoring_sample_counts:
            raise CalibrationError("continuous_scoring_plan_mismatch")
        if parameters_digest != self._parameters_digest:
            raise CalibrationError("parameters_digest_mismatch")
        if self.application_scope != CONTINUOUS_INTERCEPTION_SCOPE:
            raise CalibrationError("continuous_calibration_package_unavailable")
        protocol = self._registered.package.protocol
        if protocol.scoring_sample_counts != plan.sample_counts:
            raise CalibrationError("continuous_package_scoring_plan_mismatch")
        if decision_sample_ranges is not None and (
            type(decision_sample_ranges) is not tuple
            or any(
                type(layout) is not tuple or len(layout) != 3
                or any(type(value) is not int for value in layout)
                for layout in decision_sample_ranges
            )
            or decision_sample_ranges != protocol.decision_sample_ranges
        ):
            raise CalibrationError("continuous_package_decision_plan_mismatch")
        if protocol.scoring_parameters_digest != parameters_digest:
            raise CalibrationError("continuous_package_parameters_digest_mismatch")
        if self._runtime_protocol != protocol:
            raise CalibrationError("continuous_runtime_protocol_mismatch")
        expected_versions = (profile_generation, model_generation, config_generation)
        if any(value is not None for value in expected_versions):
            if any(value is None for value in expected_versions):
                raise CalibrationError("continuous_runtime_identity_incomplete")
            for name, expected in zip(
                ("profile_generation", "model_generation", "config_generation"),
                expected_versions, strict=True,
            ):
                _nonempty(name, expected)
                if getattr(self, f"_{name}") != expected:
                    raise CalibrationError(f"{name}_mismatch")

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
            if self.application_scope == CONTINUOUS_INTERCEPTION_SCOPE:
                if self._scoring_sample_counts is None:
                    return failure("continuous_scoring_plan_unavailable")
                try:
                    self.require_continuous_support(
                        self._scoring_sample_counts,
                        parameters_digest=self._parameters_digest,
                    )
                except CalibrationError as exc:
                    return failure(str(exc))
            scoring_samples = observation.scoring_range.sample_count
            if self.application_scope == CONTINUOUS_INTERCEPTION_SCOPE:
                layout = (
                    scoring_samples,
                    observation.decision_range.start - observation.scoring_range.start,
                    observation.decision_range.end - observation.scoring_range.start,
                )
                if layout not in self._registered.package.protocol.decision_sample_ranges:
                    return CalibratedIdentityEvidence(
                        CalibratedIdentityOutcome.UNSUPPORTED,
                        "calibration_decision_range_unsupported",
                    )
            if (
                self._scoring_sample_counts is not None
                and scoring_samples not in self._scoring_sample_counts
            ):
                return CalibratedIdentityEvidence(
                    CalibratedIdentityOutcome.UNSUPPORTED,
                    "calibration_scoring_window_unsupported",
                )
            quality = observation.quality
            if not quality.continuous:
                return failure("discontinuous_scoring_audio")
            samples = quality.speech_samples
            if samples is None or type(samples) is not int or samples <= 0:
                return failure("missing_speech_samples")
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
