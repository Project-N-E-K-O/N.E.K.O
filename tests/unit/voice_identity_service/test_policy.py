import math
from dataclasses import replace

import pytest

from main_logic.voice_identity_service.policy import (
    OwnerVoiceClassification,
    OwnerVoicePolicy,
)
from main_logic.voice_identity_service.calibration import (
    CALIBRATION_MODEL_FEATURE_ORDER,
    CalibrationOutcome,
    CalibrationPackage,
    CalibrationProtocol,
    RegisteredCalibration,
    calibration_package_artifact_sha256,
    register_calibration_package,
)


def _registered_package() -> CalibrationPackage:
    size = len(CALIBRATION_MODEL_FEATURE_ORDER)
    return CalibrationPackage(
        schema_version=1,
        package_revision="terminal-short-test",
        protocol=CalibrationProtocol(
            model_id="campplus",
            model_revision="test",
            embedding_dimension=192,
            preprocessing_contract_id="pcm16-test",
            preprocessing_revision=1,
            noise_reduction_enabled=True,
        ),
        feature_order=CALIBRATION_MODEL_FEATURE_ORDER,
        center=(0.0,) * size,
        scale=(1.0,) * size,
        weights=(1.0,) + (0.0,) * (size - 1),
        intercept=0.0,
        nonowner_boundary=-0.5,
        owner_boundary=0.5,
        dataset_digest="0" * 64,
        split_group_counts=(1, 1, 1),
        release_status="registered",
    )


def _allowlisted(package: CalibrationPackage) -> RegisteredCalibration:
    return register_calibration_package(
        package,
        expected_digest=calibration_package_artifact_sha256(package),
    )


@pytest.mark.parametrize("checkpoint_ms", [1_500, 3_000])
def test_policy_classifies_each_low_checkpoint_without_candidate_state(
    checkpoint_ms: int,
) -> None:
    result = OwnerVoicePolicy.classify(
        checkpoint_ms=checkpoint_ms,
        similarity=0.39,
    )

    assert result.classification is OwnerVoiceClassification.LOW
    assert result.reason == "clear_mismatch"


def test_policy_classifies_threshold_as_high() -> None:
    result = OwnerVoicePolicy.classify(
        checkpoint_ms=1_500,
        similarity=0.40,
    )

    assert result.classification is OwnerVoiceClassification.HIGH
    assert result.reason == "owner_or_uncertain"


@pytest.mark.parametrize("audio_ms", [1_501, 2_999])
def test_policy_accepts_completion_confirmation_as_one_low_fact(
    audio_ms: int,
) -> None:
    result = OwnerVoicePolicy.classify(
        checkpoint_ms=1_500,
        similarity=0.20,
        observation_kind="completion_confirmation",
        audio_ms=audio_ms,
    )

    assert result.classification is OwnerVoiceClassification.LOW


@pytest.mark.parametrize(
    ("checkpoint_ms", "similarity", "observation_kind", "audio_ms"),
    [
        (None, 0.1, "checkpoint", None),
        (2_000, 0.1, "checkpoint", None),
        (1_500, math.nan, "checkpoint", None),
        (1_500, math.inf, "checkpoint", None),
        (1_500, 0.1, "completion_confirmation", 1_500),
        (1_500, 0.1, "completion_confirmation", 3_000),
    ],
)
def test_policy_invalid_observation_is_unavailable(
    checkpoint_ms: int | None,
    similarity: float,
    observation_kind: str,
    audio_ms: int | None,
) -> None:
    result = OwnerVoicePolicy.classify(
        checkpoint_ms=checkpoint_ms,
        similarity=similarity,
        observation_kind=observation_kind,  # type: ignore[arg-type]
        audio_ms=audio_ms,
    )

    assert result.classification is OwnerVoiceClassification.UNAVAILABLE
    assert result.reason == "invalid_observation"


def test_terminal_short_without_registered_package_is_insufficient() -> None:
    result = OwnerVoicePolicy.classify(
        checkpoint_ms=None,
        similarity=-0.9,
        observation_kind="terminal_short",
        audio_ms=900,
    )

    assert result.classification is OwnerVoiceClassification.INSUFFICIENT
    assert result.reason == "terminal_short_observation_only"


@pytest.mark.parametrize(
    ("similarity", "expected_outcome"),
    (
        (0.8, CalibrationOutcome.OWNER),
        (-0.8, CalibrationOutcome.NONOWNER),
        (0.0, CalibrationOutcome.UNCERTAIN),
    ),
)
def test_candidate_package_returns_only_a_fail_open_recommendation(
    similarity: float,
    expected_outcome: CalibrationOutcome,
) -> None:
    package = replace(_registered_package(), release_status="candidate")

    result = OwnerVoicePolicy.classify(
        checkpoint_ms=None,
        similarity=similarity,
        observation_kind="terminal_short",
        audio_ms=900,
        calibration_package=package,
        runtime_protocol=package.protocol,
    )

    assert result.classification is OwnerVoiceClassification.INSUFFICIENT
    assert result.calibration_outcome is expected_outcome


def test_bare_package_cannot_gain_authority_from_registered_json_field() -> None:
    package = CalibrationPackage.from_dict(_registered_package().to_dict())

    result = OwnerVoicePolicy.classify(
        checkpoint_ms=None,
        similarity=-0.8,
        observation_kind="terminal_short",
        audio_ms=900,
        calibration_package=package,
        runtime_protocol=package.protocol,
    )

    assert package.release_status == "registered"
    assert result.classification is OwnerVoiceClassification.INSUFFICIENT
    assert result.calibration_outcome is CalibrationOutcome.NONOWNER


def test_candidate_unsupported_and_failure_remain_observation_only() -> None:
    package = replace(_registered_package(), release_status="candidate")
    unsupported = OwnerVoicePolicy.classify(
        checkpoint_ms=None,
        similarity=0.8,
        observation_kind="terminal_short",
        audio_ms=900,
        calibration_package=package,
        runtime_protocol=replace(package.protocol, preprocessing_revision=2),
    )
    numerically_unsafe = replace(
        package,
        scale=(1e-320,) + package.scale[1:],
    )
    failure = OwnerVoicePolicy.classify(
        checkpoint_ms=None,
        similarity=0.8,
        observation_kind="terminal_short",
        audio_ms=900,
        calibration_package=numerically_unsafe,
        runtime_protocol=numerically_unsafe.protocol,
    )

    assert unsupported.classification is OwnerVoiceClassification.INSUFFICIENT
    assert unsupported.calibration_outcome is CalibrationOutcome.UNSUPPORTED
    assert failure.classification is OwnerVoiceClassification.INSUFFICIENT
    assert failure.calibration_outcome is CalibrationOutcome.FAILURE


@pytest.mark.parametrize(
    ("similarity", "expected"),
    [
        (0.8, OwnerVoiceClassification.HIGH),
        (-0.8, OwnerVoiceClassification.LOW),
        (0.0, OwnerVoiceClassification.INSUFFICIENT),
    ],
)
def test_registered_calibration_maps_terminal_short_three_state(
    similarity: float,
    expected: OwnerVoiceClassification,
) -> None:
    package = _registered_package()
    registered = _allowlisted(package)

    result = OwnerVoicePolicy.classify(
        checkpoint_ms=None,
        similarity=similarity,
        observation_kind="terminal_short",
        audio_ms=900,
        calibration_package=package,
        registered_calibration=registered,
        runtime_protocol=package.protocol,
    )

    assert result.classification is expected
    assert result.reason == "calibrated_evidence"


def test_terminal_short_protocol_mismatch_remains_unavailable() -> None:
    package = _registered_package()
    registered = _allowlisted(package)
    runtime_protocol = replace(package.protocol, preprocessing_revision=2)

    result = OwnerVoicePolicy.classify(
        checkpoint_ms=None,
        similarity=0.8,
        observation_kind="terminal_short",
        audio_ms=900,
        calibration_package=package,
        registered_calibration=registered,
        runtime_protocol=runtime_protocol,
    )

    assert result.classification is OwnerVoiceClassification.UNAVAILABLE
    assert result.reason == "protocol_mismatch"


def test_registered_wrapper_cannot_authorize_a_different_package() -> None:
    package = _registered_package()
    other = replace(package, package_revision="different-package")

    result = OwnerVoicePolicy.classify(
        checkpoint_ms=None,
        similarity=0.8,
        observation_kind="terminal_short",
        audio_ms=900,
        calibration_package=other,
        registered_calibration=_allowlisted(package),
        runtime_protocol=other.protocol,
    )

    assert result.classification is OwnerVoiceClassification.UNAVAILABLE
    assert result.reason == "registered_calibration_mismatch"
