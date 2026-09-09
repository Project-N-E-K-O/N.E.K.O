from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import math

import pytest

from main_logic.voice_identity_service.calibration import (
    CALIBRATION_FEATURE_ORDER,
    CALIBRATION_MAX_AUDIO_MS,
    CALIBRATION_MODEL_FEATURE_ORDER,
    CALIBRATION_SCHEMA_VERSION,
    CalibrationError,
    CalibrationExample,
    CalibrationFeatures,
    CalibrationOutcome,
    CalibrationPackage,
    CalibrationProtocol,
    DatasetSplit,
    REFERENCE_FORMATION_PROTOCOL,
    RegisteredCalibration,
    calibration_package_artifact_sha256,
    fit_linear_calibration,
    evaluate_calibration,
    register_calibration_package,
    validate_fit_dataset,
)


def _protocol(**changes: object) -> CalibrationProtocol:
    values = {
        "model_id": "campplus",
        "model_revision": "rev-1",
        "embedding_dimension": 192,
        "preprocessing_contract_id": "owner-campplus-desktop-v1",
        "preprocessing_revision": 1,
        "noise_reduction_enabled": False,
        "reference_formation_protocol": REFERENCE_FORMATION_PROTOCOL,
        "reference_recording_count": 3,
    }
    values.update(changes)
    return CalibrationProtocol(**values)  # type: ignore[arg-type]


def _example(split: DatasetSplit, label: CalibrationOutcome, index: int) -> CalibrationExample:
    owner = label == CalibrationOutcome.OWNER
    base = f"{split.value}-{label.value}-{index}"
    return CalibrationExample(
        example_id=base,
        group_id=f"group-{base}",
        reference_speaker_id=f"reference-speaker-{base}",
        candidate_speaker_id=(
            f"reference-speaker-{base}" if owner else f"candidate-speaker-{base}"
        ),
        candidate_session_id=f"candidate-session-{base}",
        reference_session_ids=(
            f"reference-session-a-{base}",
            f"reference-session-b-{base}",
            f"reference-session-c-{base}",
        ),
        candidate_recording_family_id=f"candidate-family-{base}",
        reference_recording_family_ids=(
            f"reference-family-a-{base}",
            f"reference-family-b-{base}",
            f"reference-family-c-{base}",
        ),
        source_recording_id=f"source-{base}",
        reference_recording_ids=(f"ref-a-{base}", f"ref-b-{base}", f"ref-c-{base}"),
        split=split,
        label=label,
        protocol=_protocol(),
        features=CalibrationFeatures(
            raw_similarity=(0.82 + index * 0.01) if owner else (0.12 + index * 0.01),
            audio_ms=1000 + index * 10,
            rms=0.25 if owner else 0.1,
            peak=0.7 if owner else 0.3,
            near_silence=0.0 if owner else None,
            clipping=None,
        ),
    )


def _dataset() -> tuple[CalibrationExample, ...]:
    return tuple(
        _example(split, label, index)
        for split in DatasetSplit
        for label in (CalibrationOutcome.OWNER, CalibrationOutcome.NONOWNER)
        for index in range(2)
    )


def _package() -> CalibrationPackage:
    size = len(CALIBRATION_MODEL_FEATURE_ORDER)
    weights = (1.0,) + (0.0,) * (size - 1)
    return CalibrationPackage(
        schema_version=CALIBRATION_SCHEMA_VERSION,
        package_revision="candidate-1",
        protocol=_protocol(),
        feature_order=CALIBRATION_MODEL_FEATURE_ORDER,
        center=(0.0,) * size,
        scale=(1.0,) * size,
        weights=weights,
        intercept=0.0,
        nonowner_boundary=0.2,
        owner_boundary=0.7,
        dataset_digest="a" * 64,
        split_group_counts=(2, 2, 2),
    )


def test_feature_contract_has_stable_order_and_explicit_missing_indicators() -> None:
    assert CALIBRATION_FEATURE_ORDER == (
        "raw_similarity", "audio_ms", "rms", "peak", "near_silence", "clipping"
    )
    features = CalibrationFeatures(0.8, 3000, rms=None, peak=0.5)
    vector = features.model_vector()
    assert len(vector) == 12
    assert vector[:6] == (0.8, 3000.0, 0.0, 0.5, 0.0, 0.0)
    assert vector[6:] == (0.0, 0.0, 1.0, 0.0, 1.0, 1.0)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, True])
def test_features_reject_nonfinite_or_boolean_values(value: object) -> None:
    with pytest.raises(CalibrationError):
        CalibrationFeatures(value, 3000)  # type: ignore[arg-type]


def test_audio_duration_has_finite_four_second_protocol_ceiling() -> None:
    assert CalibrationFeatures(0.5, CALIBRATION_MAX_AUDIO_MS).audio_ms == 4000.0
    with pytest.raises(CalibrationError, match="supported range"):
        CalibrationFeatures(0.5, 1e308)


def test_package_is_immutable_and_requires_strictly_ordered_boundaries() -> None:
    package = _package()
    with pytest.raises(FrozenInstanceError):
        package.owner_boundary = 1.0  # type: ignore[misc]
    with pytest.raises(CalibrationError, match="strictly below"):
        replace(package, nonowner_boundary=0.7)


def test_classify_returns_evidence_categories_without_probability_contract() -> None:
    package = _package()
    assert package.classify(CalibrationFeatures(0.1, 1000), _protocol()).outcome == CalibrationOutcome.NONOWNER
    middle = package.classify(CalibrationFeatures(0.5, 1000), _protocol())
    assert middle.outcome == CalibrationOutcome.UNCERTAIN
    assert middle.evidence_score == pytest.approx(0.5)
    assert package.classify(CalibrationFeatures(0.9, 1000), _protocol()).outcome == CalibrationOutcome.OWNER
    assert not hasattr(middle, "probability")


def test_incompatible_protocol_and_missing_primary_feature_are_unsupported() -> None:
    package = _package()
    incompatible = _protocol(model_revision="rev-2")
    assert package.classify(CalibrationFeatures(0.9, 1000), incompatible).outcome == CalibrationOutcome.UNSUPPORTED
    assert package.classify(CalibrationFeatures(None, 1000), _protocol()).outcome == CalibrationOutcome.UNSUPPORTED


@pytest.mark.parametrize("audio_ms", [44.999, 1500.0, 3000.0])
def test_package_rejects_audio_outside_terminal_short_domain(audio_ms: float) -> None:
    result = _package().classify(CalibrationFeatures(0.9, audio_ms), _protocol())
    assert result.outcome == CalibrationOutcome.UNSUPPORTED
    assert result.reason == "outside_application_domain"


def test_calibration_example_enforces_terminal_short_domain() -> None:
    item = _example(DatasetSplit.TRAIN, CalibrationOutcome.OWNER, 0)
    with pytest.raises(CalibrationError, match="terminal-short"):
        replace(item, features=replace(item.features, audio_ms=1500.0))


def test_invalid_numeric_mapping_returns_failure() -> None:
    result = _package().classify(
        {"raw_similarity": math.nan, "audio_ms": 2000},
        _protocol(),
    )
    assert result.outcome == CalibrationOutcome.FAILURE
    assert result.evidence_score is None


def test_package_round_trip_preserves_versioned_contract() -> None:
    package = _package()
    assert CalibrationPackage.from_dict(package.to_dict()) == package
    broken = package.to_dict()
    broken["schema_version"] = 999
    with pytest.raises(CalibrationError, match="schema version"):
        CalibrationPackage.from_dict(broken)
    boolean_version = package.to_dict()
    boolean_version["schema_version"] = True
    with pytest.raises(CalibrationError, match="schema version"):
        CalibrationPackage.from_dict(boolean_version)
    boolean_count = package.to_dict()
    boolean_count["split_group_counts"] = [True, 2, 2]
    with pytest.raises(CalibrationError, match="group counts"):
        CalibrationPackage.from_dict(boolean_count)
    tuple_vector = package.to_dict()
    tuple_vector["weights"] = tuple(tuple_vector["weights"])
    with pytest.raises(CalibrationError, match="JSON array"):
        CalibrationPackage.from_dict(tuple_vector)


def test_canonical_artifact_digest_covers_the_complete_package() -> None:
    package = _package()
    digest = calibration_package_artifact_sha256(package)

    assert len(digest) == 64
    assert digest == calibration_package_artifact_sha256(
        CalibrationPackage.from_dict(package.to_dict())
    )
    assert digest != calibration_package_artifact_sha256(
        replace(package, release_status="registered")
    )
    assert digest != calibration_package_artifact_sha256(
        replace(package, package_revision="candidate-2")
    )


@pytest.mark.parametrize(
    "expected_digest",
    ("A" * 64, "0" * 63, "g" * 64, 123),
)
def test_registration_rejects_malformed_expected_digest(expected_digest: object) -> None:
    with pytest.raises(CalibrationError, match="lowercase SHA-256"):
        register_calibration_package(
            _package(),
            expected_digest=expected_digest,  # type: ignore[arg-type]
        )


def test_registration_requires_the_app_allowlisted_artifact_digest() -> None:
    package = _package()
    with pytest.raises(CalibrationError, match="artifact digest mismatch"):
        register_calibration_package(package, expected_digest="0" * 64)


def test_controlled_registration_returns_an_immutable_bound_wrapper() -> None:
    package = _package()
    digest = calibration_package_artifact_sha256(package)
    registered = register_calibration_package(package, expected_digest=digest)

    assert registered.package is package
    assert registered.artifact_sha256 == digest
    assert registered.registration_scheme == "app_allowlisted_sha256_v1"
    with pytest.raises(FrozenInstanceError):
        registered.artifact_sha256 = "0" * 64  # type: ignore[misc]
    with pytest.raises(CalibrationError, match="controlled loader"):
        RegisteredCalibration(package, digest)


def test_dataset_requires_three_distinct_reference_recordings() -> None:
    item = _example(DatasetSplit.TRAIN, CalibrationOutcome.OWNER, 0)
    with pytest.raises(CalibrationError, match="three distinct"):
        replace(item, reference_recording_ids=("a", "a", "b"))


def test_example_requires_label_to_match_opaque_speaker_relationship() -> None:
    owner = _example(DatasetSplit.TRAIN, CalibrationOutcome.OWNER, 0)
    with pytest.raises(CalibrationError, match="label is inconsistent"):
        replace(owner, candidate_speaker_id="different-opaque-speaker")
    nonowner = _example(DatasetSplit.TRAIN, CalibrationOutcome.NONOWNER, 0)
    with pytest.raises(CalibrationError, match="label is inconsistent"):
        replace(nonowner, candidate_speaker_id=nonowner.reference_speaker_id)


def test_example_requires_independent_reference_families_and_owner_session() -> None:
    owner = _example(DatasetSplit.TRAIN, CalibrationOutcome.OWNER, 0)
    with pytest.raises(CalibrationError, match="three distinct reference recording families"):
        replace(owner, reference_recording_family_ids=("family-a", "family-a", "family-c"))
    with pytest.raises(CalibrationError, match="families must be independent"):
        replace(
            owner,
            candidate_recording_family_id=owner.reference_recording_family_ids[0],
        )
    with pytest.raises(CalibrationError, match="candidate session must be independent"):
        replace(owner, candidate_session_id=owner.reference_session_ids[0])
    nonowner = _example(DatasetSplit.TRAIN, CalibrationOutcome.NONOWNER, 0)
    with pytest.raises(CalibrationError, match="candidate session must be independent"):
        replace(nonowner, candidate_session_id=nonowner.reference_session_ids[0])


def test_dataset_rejects_group_and_raw_recording_leakage() -> None:
    records = list(_dataset())
    records[4] = replace(records[4], group_id=records[0].group_id)
    with pytest.raises(CalibrationError, match="group appears"):
        validate_fit_dataset(records)


def test_dataset_rejects_duplicate_example_ids() -> None:
    records = list(_dataset())
    records[1] = replace(records[1], example_id=records[0].example_id)
    with pytest.raises(CalibrationError, match="example_id"):
        validate_fit_dataset(records)
    records = list(_dataset())
    records[4] = replace(records[4], source_recording_id=records[0].reference_recording_ids[0])
    with pytest.raises(CalibrationError, match="raw recording"):
        validate_fit_dataset(records)


@pytest.mark.parametrize(
    ("field", "copied_value", "message"),
    [
        ("candidate_speaker_id", "candidate_speaker_id", "speaker appears"),
        ("candidate_session_id", "candidate_session_id", "session appears"),
        (
            "candidate_recording_family_id",
            "candidate_recording_family_id",
            "recording family appears",
        ),
    ],
)
def test_dataset_rejects_identity_session_and_family_cross_split_leakage(
    field: str, copied_value: str, message: str
) -> None:
    records = list(_dataset())
    records[6] = replace(records[6], **{field: getattr(records[2], copied_value)})
    with pytest.raises(CalibrationError, match=message):
        validate_fit_dataset(records)


def test_fit_is_deterministic_candidate_and_uses_dev_for_ordered_boundaries() -> None:
    records = _dataset()
    first = fit_linear_calibration(
        records,
        package_revision="candidate-2026-09-07",
        max_nonowner_as_owner_rate=0.0,
        max_owner_as_nonowner_rate=0.0,
    )
    second = fit_linear_calibration(
        reversed(records),
        package_revision="candidate-2026-09-07",
        max_nonowner_as_owner_rate=0.0,
        max_owner_as_nonowner_rate=0.0,
    )
    assert first == second
    assert first.release_status == "candidate"
    assert first.nonowner_boundary < first.owner_boundary
    assert first.dataset_digest == second.dataset_digest
    assert first.nonowner_boundary != 0.40
    assert first.owner_boundary != 0.40


def _fit(records: tuple[CalibrationExample, ...] | list[CalibrationExample]) -> CalibrationPackage:
    return fit_linear_calibration(
        records,
        package_revision="role-test",
        max_nonowner_as_owner_rate=0.0,
        max_owner_as_nonowner_rate=0.0,
    )


def test_train_dev_test_have_separate_fit_roles() -> None:
    baseline_records = list(_dataset())
    baseline = _fit(baseline_records)

    test_changed_records = list(baseline_records)
    test_index = next(
        index
        for index, item in enumerate(test_changed_records)
        if item.split == DatasetSplit.TEST and item.label == CalibrationOutcome.OWNER
    )
    test_changed_records[test_index] = replace(
        test_changed_records[test_index],
        features=replace(test_changed_records[test_index].features, raw_similarity=0.99),
    )
    test_changed = _fit(test_changed_records)
    assert (
        test_changed.center,
        test_changed.scale,
        test_changed.weights,
        test_changed.intercept,
        test_changed.nonowner_boundary,
        test_changed.owner_boundary,
    ) == (
        baseline.center,
        baseline.scale,
        baseline.weights,
        baseline.intercept,
        baseline.nonowner_boundary,
        baseline.owner_boundary,
    )
    assert test_changed.dataset_digest != baseline.dataset_digest

    dev_changed_records = list(baseline_records)
    dev_index = next(
        index
        for index, item in enumerate(dev_changed_records)
        if item.split == DatasetSplit.DEV and item.label == CalibrationOutcome.OWNER
    )
    dev_changed_records[dev_index] = replace(
        dev_changed_records[dev_index],
        features=replace(dev_changed_records[dev_index].features, raw_similarity=0.7),
    )
    dev_changed = _fit(dev_changed_records)
    assert dev_changed.weights == baseline.weights
    assert dev_changed.owner_boundary != baseline.owner_boundary


def test_fit_refuses_empty_or_incomplete_data_instead_of_making_parameters() -> None:
    with pytest.raises(CalibrationError, match="empty"):
        fit_linear_calibration(
            (), package_revision="candidate",
            max_nonowner_as_owner_rate=0.0, max_owner_as_nonowner_rate=0.0,
        )
    incomplete = [item for item in _dataset() if item.split != DatasetSplit.TEST]
    with pytest.raises(CalibrationError, match="test split"):
        fit_linear_calibration(
            incomplete, package_revision="candidate",
            max_nonowner_as_owner_rate=0.0, max_owner_as_nonowner_rate=0.0,
        )


def test_fit_rejects_unsupported_reference_protocol() -> None:
    records = [replace(item, protocol=_protocol(reference_recording_count=2)) for item in _dataset()]
    with pytest.raises(CalibrationError, match="reference formation"):
        fit_linear_calibration(
            records, package_revision="candidate",
            max_nonowner_as_owner_rate=0.0, max_owner_as_nonowner_rate=0.0,
        )


def test_evaluation_reports_confusion_uncertain_split_and_coverage() -> None:
    package = _package()
    examples = (
        replace(_example(DatasetSplit.TEST, CalibrationOutcome.OWNER, 10), features=CalibrationFeatures(0.9, 1000)),
        replace(_example(DatasetSplit.TEST, CalibrationOutcome.OWNER, 11), features=CalibrationFeatures(0.1, 1000)),
        replace(_example(DatasetSplit.TEST, CalibrationOutcome.NONOWNER, 12), features=CalibrationFeatures(0.8, 1000)),
        replace(_example(DatasetSplit.TEST, CalibrationOutcome.NONOWNER, 13), features=CalibrationFeatures(0.5, 1000)),
    )
    test = evaluate_calibration(package, examples)["test"]
    assert (test["tp"], test["fn"], test["fp"], test["tn"]) == (1, 1, 1, 0)
    assert test["nonowner_uncertain"] == 1
    assert test["coverage"] == pytest.approx(0.75)
