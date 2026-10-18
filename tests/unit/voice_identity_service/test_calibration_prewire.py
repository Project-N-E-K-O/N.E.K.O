from __future__ import annotations

from dataclasses import replace

import pytest

from main_logic.voice_identity_service.calibration import (
    CALIBRATION_MODEL_FEATURE_ORDER,
    CALIBRATION_SCHEMA_VERSION,
    CalibrationPackage,
    CalibrationProtocol,
    REFERENCE_FORMATION_PROTOCOL,
    calibration_package_artifact_sha256,
    register_calibration_package,
    CalibrationError,
)
from main_logic.voice_identity_service.prewire_calibration import PrewireCalibrationClassifier
from main_logic.voice_identity_service.prewire_gate.contracts import SampleRange
from main_logic.voice_identity_service.prewire_gate.decision import (
    CalibratedIdentityOutcome,
    PrewireQualitySummary,
    PrewireScoreObservation,
)

pytestmark = pytest.mark.unit_fast


def _classifier() -> tuple[PrewireCalibrationClassifier, str]:
    protocol = CalibrationProtocol(
        model_id="campplus",
        model_revision="rev-1",
        embedding_dimension=192,
        preprocessing_contract_id="campplus-v1",
        preprocessing_revision=1,
        noise_reduction_enabled=False,
        reference_formation_protocol=REFERENCE_FORMATION_PROTOCOL,
        reference_recording_count=3,
    )
    size = len(CALIBRATION_MODEL_FEATURE_ORDER)
    package = CalibrationPackage(
        schema_version=CALIBRATION_SCHEMA_VERSION,
        package_revision="registered-1",
        protocol=protocol,
        feature_order=CALIBRATION_MODEL_FEATURE_ORDER,
        center=(0.0,) * size,
        scale=(1.0,) * size,
        weights=(1.0,) + (0.0,) * (size - 1),
        intercept=0.0,
        nonowner_boundary=0.2,
        owner_boundary=0.7,
        dataset_digest="a" * 64,
        split_group_counts=(1, 1, 1),
        release_status="registered",
    )
    digest = calibration_package_artifact_sha256(package)
    registered = register_calibration_package(package, expected_digest=digest)
    classifier = PrewireCalibrationClassifier(
        registered,
        runtime_protocol=protocol,
        profile_generation="profile-1",
        model_generation="model-1",
        config_generation="config-1",
        parameters_digest="b" * 64,
    )
    return classifier, digest


def _observation(*, similarity: float = 0.9, **changes: object) -> PrewireScoreObservation:
    values: dict[str, object] = {
        "scoring_range": SampleRange(0, 16000),
        "decision_range": SampleRange(0, 16000),
        "raw_similarity": similarity,
        "quality": PrewireQualitySummary(
            speech_samples=16000,
            rms=0.2,
            peak=0.7,
            near_silence=0.0,
            clipping=0.0,
        ),
        "profile_generation": "profile-1",
        "model_generation": "model-1",
        "config_generation": "config-1",
        "parameters_digest": "b" * 64,
    }
    values.update(changes)
    return PrewireScoreObservation(**values)  # type: ignore[arg-type]


def test_registered_classifier_maps_calibrated_owner() -> None:
    classifier, _ = _classifier()
    result = classifier.classify(_observation())
    assert result.outcome is CalibratedIdentityOutcome.OWNER
    assert result.reason == "calibrated_evidence"


def test_classifier_requires_released_package() -> None:
    classifier, _ = _classifier()
    package = classifier.registered.package
    candidate = package.__class__.from_dict({**package.to_dict(), "release_status": "candidate"})
    registered = register_calibration_package(candidate, expected_digest=calibration_package_artifact_sha256(candidate))
    with pytest.raises(CalibrationError, match="not released"):
        PrewireCalibrationClassifier(
            registered,
            runtime_protocol=package.protocol,
            profile_generation="profile-1",
            model_generation="model-1",
            config_generation="config-1",
            parameters_digest="b" * 64,
        )


@pytest.mark.parametrize("field", ["profile_generation", "model_generation", "config_generation", "parameters_digest"])
def test_registered_classifier_fails_closed_on_identity_fence(field: str) -> None:
    classifier, _ = _classifier()
    result = classifier.classify(_observation(**{field: "c" * 64 if field == "parameters_digest" else "other"}))
    assert result.outcome is CalibratedIdentityOutcome.FAILURE
    assert result.reason == f"{field}_mismatch"


def test_registered_classifier_rejects_missing_duration_and_discontinuity() -> None:
    classifier, _ = _classifier()
    missing = classifier.classify(_observation(quality=PrewireQualitySummary()))
    assert missing.outcome is CalibratedIdentityOutcome.FAILURE
    assert missing.reason == "missing_speech_samples"
    discontinuous = classifier.classify(
        _observation(quality=PrewireQualitySummary(speech_samples=16000, continuous=False))
    )
    assert discontinuous.outcome is CalibratedIdentityOutcome.FAILURE
    assert discontinuous.reason == "discontinuous_scoring_audio"


def test_classifier_rejects_activity_coverage_outside_real_pcm_axis() -> None:
    classifier, _ = _classifier()
    result = classifier.classify(
        _observation(quality=PrewireQualitySummary(speech_samples=16001))
    )
    assert result.outcome is CalibratedIdentityOutcome.FAILURE
    assert result.reason == "speech_samples_exceed_scoring_range"


def test_classifier_rejects_actual_deployment_protocol_mismatch() -> None:
    configured, _ = _classifier()
    classifier = PrewireCalibrationClassifier(
        configured.registered,
        runtime_protocol=replace(configured.registered.package.protocol, model_revision="other"),
        profile_generation="profile-1",
        model_generation="model-1",
        config_generation="config-1",
        parameters_digest="b" * 64,
    )
    result = classifier.classify(_observation())
    assert result.outcome is CalibratedIdentityOutcome.UNSUPPORTED
    assert result.reason == "protocol_mismatch"
