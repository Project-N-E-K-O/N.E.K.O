"""Synthetic control fixtures prove contracts, never acoustic calibration quality."""

from dataclasses import replace
import json

import pytest

from main_logic.voice_identity_service.calibration import (
    CONTINUOUS_DATA_PROTOCOL, CONTINUOUS_INTERCEPTION_SCOPE,
    CalibrationError, CalibrationExample, CalibrationFeatures, CalibrationOutcome,
    CalibrationPackage, CalibrationProtocol, DatasetSplit,
    calibration_package_artifact_sha256, fit_linear_calibration,
    register_calibration_package, validate_fit_dataset,
)
from main_logic.voice_identity_service.prewire_calibration import PrewireCalibrationClassifier
from main_logic.voice_identity_service.prewire_gate.contracts import SampleRange
from main_logic.voice_identity_service.prewire_gate.decision import (
    CalibratedIdentityOutcome, PrewireQualitySummary, PrewireScoreObservation,
)
from scripts.evaluate_voice_identity_calibration import main
from tests.unit.voice_identity_service.test_calibration import _dataset, _package, _protocol

pytestmark = pytest.mark.unit_fast


def protocol(**changes):
    values = dict(
        application_scope=CONTINUOUS_INTERCEPTION_SCOPE,
        scoring_sample_counts=(4_000, 7_200),
        scoring_parameters_digest="b" * 64,
        data_protocol=CONTINUOUS_DATA_PROTOCOL,
        decision_sample_ranges=((4_000, 0, 2_400), (7_200, 0, 2_400)),
    )
    values.update(changes)
    return _protocol(**values)


def dataset():
    result = []
    for item in _dataset():
        count = 4_000 if item.example_id.endswith("-0") else 7_200
        result.append(replace(
            item, protocol=protocol(),
            features=replace(item.features, audio_ms=count / 16),
            scoring_range_samples=(100, 100 + count),
            decision_range_samples=(100, 2_500),
        ))
    return tuple(result)


def package():
    return fit_linear_calibration(
        dataset(), package_revision="synthetic-continuous-control",
        max_nonowner_as_owner_rate=0, max_owner_as_nonowner_rate=0,
    )


def classifier(*, runtime_protocol=None, adapter_counts=(4_000, 7_200), digest="b" * 64):
    # Explicit registration is fixture-only; the fitter never registers output.
    released = replace(package(), release_status="registered")
    registered = register_calibration_package(released, expected_digest=calibration_package_artifact_sha256(released))
    return PrewireCalibrationClassifier(
        registered, runtime_protocol=runtime_protocol or released.protocol,
        profile_generation="p", model_generation="m", config_generation="c",
        parameters_digest=digest, scoring_sample_counts=adapter_counts,
    )


def test_terminal_serialization_and_artifact_digest_are_byte_contract_compatible():
    old = _package()
    assert calibration_package_artifact_sha256(old) == "271a7c1fe725a13d9be228353f6dd53c76cf4824539d324051f0f06483925517"
    assert not {"scoring_sample_counts", "scoring_parameters_digest", "data_protocol"} & old.protocol.to_dict().keys()
    assert CalibrationPackage.from_dict(old.to_dict()) == old
    item = _dataset()[0]
    assert "scoring_range_samples" not in item.to_dict()
    assert CalibrationExample.from_dict(item.to_dict()) == item


def test_continuous_fit_roundtrip_is_candidate_with_bound_scope_ranges_and_parameters():
    fitted = package()
    assert fitted.release_status == "candidate"
    assert fitted.protocol.application_scope == CONTINUOUS_INTERCEPTION_SCOPE
    assert fitted.protocol.scoring_sample_counts == (4_000, 7_200)
    assert CalibrationPackage.from_dict(fitted.to_dict()) == fitted
    for item in dataset():
        assert CalibrationExample.from_dict(item.to_dict()) == item
    with pytest.raises(CalibrationError, match="not released"):
        PrewireCalibrationClassifier(
            register_calibration_package(fitted, expected_digest=calibration_package_artifact_sha256(fitted)),
            runtime_protocol=fitted.protocol, profile_generation="p", model_generation="m",
            config_generation="c", parameters_digest="b" * 64, scoring_sample_counts=(4_000, 7_200),
        )


def test_legitimate_registered_continuous_fixture_passes_exact_readiness():
    configured = classifier()
    configured.require_continuous_support(
        (4_000, 7_200), parameters_digest="b" * 64,
        profile_generation="p", model_generation="m", config_generation="c",
    )
    result = configured.registered.package.classify(dataset()[0].features, configured.registered.package.protocol)
    assert result.outcome is CalibrationOutcome.OWNER
    unsupported = configured.registered.package.classify(CalibrationFeatures(0.9, 350), configured.registered.package.protocol)
    assert unsupported.outcome is CalibrationOutcome.UNSUPPORTED
    assert unsupported.reason == "outside_scoring_length_plan"


def observation():
    item = dataset()[0]
    return PrewireScoreObservation(
        SampleRange(*item.scoring_range_samples), SampleRange(*item.decision_range_samples),
        item.features.raw_similarity,
        PrewireQualitySummary(
            speech_samples=4_000, rms=item.features.rms, peak=item.features.peak,
            near_silence=item.features.near_silence, clipping=item.features.clipping,
        ), "p", "m", "c", "b" * 64,
    )


def test_continuous_wrapper_classifies_valid_control_and_rechecks_binding_at_use():
    configured = classifier()
    assert configured.classify(observation()).outcome is CalibratedIdentityOutcome.OWNER
    bad = classifier(adapter_counts=(4_000,))
    assert bad.classify(observation()).reason == "continuous_package_scoring_plan_mismatch"
    unbound = classifier(adapter_counts=None)
    assert unbound.classify(observation()).reason == "continuous_scoring_plan_unavailable"


@pytest.mark.parametrize("changes", [
    {"scoring_sample_counts": ()}, {"scoring_sample_counts": (7_200, 4_000)},
    {"scoring_sample_counts": [4_000, 7_200]}, {"scoring_sample_counts": (True, 7_200)},
    {"scoring_sample_counts": (4_000, 4_000)}, {"scoring_sample_counts": (10,)},
    {"scoring_parameters_digest": "invalid"}, {"data_protocol": "terminal-relabeled"},
])
def test_continuous_protocol_requires_explicit_valid_data_and_length_contract(changes):
    with pytest.raises(CalibrationError):
        protocol(**changes)


@pytest.mark.parametrize("changes", [
    {"scoring_range_samples": None}, {"decision_range_samples": None},
    {"decision_range_samples": (0, 2_400)}, {"decision_range_samples": (100, 5_000)},
    {"scoring_range_samples": (100, 4_101)},
    {"features": CalibrationFeatures(0.9, 350)},
])
def test_continuous_examples_cannot_relabel_terminal_duration_or_borrow_prefix(changes):
    with pytest.raises(CalibrationError):
        replace(dataset()[0], **changes)
    with pytest.raises(CalibrationError):
        replace(_dataset()[0], protocol=protocol())


def test_each_declared_length_requires_both_labels_in_every_split():
    records = dataset()
    missing = tuple(item for item in records if not (
        item.split is DatasetSplit.TEST and item.label is CalibrationOutcome.NONOWNER
        and item.features.audio_ms == 450
    ))
    with pytest.raises(CalibrationError, match="scoring length 7200"):
        validate_fit_dataset(missing)


def test_continuous_preserves_speaker_session_family_split_isolation():
    records = list(dataset())
    records[4] = replace(records[4], candidate_session_id=records[0].candidate_session_id)
    with pytest.raises(CalibrationError, match="session appears"):
        validate_fit_dataset(records)


def test_relabeling_or_parameter_changes_invalidate_original_allowlisted_digest():
    released = replace(package(), release_status="registered")
    digest = calibration_package_artifact_sha256(released)
    changed = replace(released, protocol=protocol(scoring_parameters_digest="c" * 64))
    assert calibration_package_artifact_sha256(changed) != digest
    with pytest.raises(CalibrationError, match="digest mismatch"):
        register_calibration_package(changed, expected_digest=digest)
    with pytest.raises(CalibrationError):
        CalibrationProtocol.from_dict({**_protocol().to_dict(), "application_scope": CONTINUOUS_INTERCEPTION_SCOPE})
    with pytest.raises(CalibrationError):
        _protocol(scoring_sample_counts=[])


@pytest.mark.parametrize("kwargs,expected", [
    ({"adapter_counts": (4_000,)}, "continuous_package_scoring_plan_mismatch"),
    ({"digest": "c" * 64}, "continuous_package_parameters_digest_mismatch"),
    ({"runtime_protocol": protocol(model_revision="different")}, "continuous_runtime_protocol_mismatch"),
])
def test_readiness_checks_real_artifact_not_only_adapter_claims(kwargs, expected):
    configured = classifier(**kwargs)
    with pytest.raises(CalibrationError, match=expected):
        configured.require_continuous_support(configured.scoring_sample_counts, parameters_digest=configured.parameters_digest)


def test_readiness_checks_complete_runtime_identity_when_requested():
    configured = classifier()
    for field in ("profile_generation", "model_generation", "config_generation"):
        expected = dict(profile_generation="p", model_generation="m", config_generation="c")
        expected[field] = "stale"
        with pytest.raises(CalibrationError, match=f"{field}_mismatch"):
            configured.require_continuous_support((4_000, 7_200), parameters_digest="b" * 64, **expected)
    with pytest.raises(CalibrationError, match="identity_incomplete"):
        configured.require_continuous_support((4_000, 7_200), parameters_digest="b" * 64, profile_generation="p")


def test_cli_fits_and_evaluates_continuous_candidate_without_registration(tmp_path):
    source = tmp_path / "continuous-fixture.json"
    output = tmp_path / "candidate.json"
    evaluation = tmp_path / "evaluation.json"
    source.write_text(json.dumps({"schema_version": 1, "examples": [item.to_dict() for item in dataset()]}), encoding="utf-8")
    assert main([
        "fit", "--dataset", str(source), "--package-revision", "synthetic-control",
        "--max-nonowner-as-owner-rate", "0", "--max-owner-as-nonowner-rate", "0",
        "--output", str(output),
    ]) == 0
    assert json.loads(output.read_text())["release_status"] == "candidate"
    assert main(["evaluate", "--dataset", str(source), "--package", str(output), "--output", str(evaluation)]) == 0
    assert json.loads(evaluation.read_text())["test"]["total"] == 4


@pytest.mark.parametrize("decision", [(2500, 4100), (1700, 4100), (100, 2600)])
def test_registered_score_cannot_authorize_shifted_or_resized_decision(decision):
    configured = classifier()
    original = observation()
    assert configured.classify(original).outcome is CalibratedIdentityOutcome.OWNER
    moved = replace(original, decision_range=SampleRange(*decision))
    result = configured.classify(moved)
    assert result.outcome is CalibratedIdentityOutcome.UNSUPPORTED
    assert result.reason == "calibration_decision_range_unsupported"


def test_calibrated_geometry_allows_the_stream_axis_to_advance():
    advanced = replace(observation(), scoring_range=SampleRange(8100, 12100),
                       decision_range=SampleRange(8100, 10500))
    assert classifier().classify(advanced).outcome is CalibratedIdentityOutcome.OWNER
    assert replace(dataset()[0], scoring_range_samples=(8100, 12100),
                   decision_range_samples=(8100, 10500)).protocol == protocol()


@pytest.mark.parametrize("decision", [(1700, 4100), (100, 2600)])
def test_fitting_records_cannot_borrow_uncalibrated_decision_geometry(decision):
    with pytest.raises(CalibrationError, match="decision range is not calibrated"):
        replace(dataset()[0], decision_range_samples=decision)


def test_geometry_changes_invalidate_the_released_artifact_digest():
    released = replace(package(), release_status="registered")
    digest = calibration_package_artifact_sha256(released)
    moved = replace(released, protocol=protocol(
        decision_sample_ranges=((4000, 1600, 4000), (7200, 4800, 7200)),
    ))
    assert calibration_package_artifact_sha256(moved) != digest
    with pytest.raises(CalibrationError, match="digest mismatch"):
        register_calibration_package(moved, expected_digest=digest)


@pytest.mark.parametrize("layouts", [
    (), ((4000, 0, 2400),), ((7200, 0, 2400), (4000, 0, 2400)),
    ((4000, -1, 2400), (7200, 0, 2400)),
    ((4000, 0, 4001), (7200, 0, 2400)),
    ((4000, 0, 0), (7200, 0, 2400)),
    ((4000, False, 2400), (7200, 0, 2400)),
    ((4000, 0, 2400, 2400), (7200, 0, 2400)),
    ([4000, 0, 2400], (7200, 0, 2400)),
])
def test_decision_protocol_requires_one_valid_geometry_per_length(layouts):
    with pytest.raises(CalibrationError, match="decision ranges must bind"):
        protocol(decision_sample_ranges=layouts)


def test_continuous_json_requires_explicit_geometry_without_default_inference():
    old = protocol().to_dict()
    del old["decision_sample_ranges"]
    with pytest.raises(CalibrationError, match="fields do not match schema"):
        CalibrationProtocol.from_dict(old)
    for malformed in (None, [[4000, 0, 2400], 7200]):
        with pytest.raises(CalibrationError, match="JSON array of arrays"):
            CalibrationProtocol.from_dict({**protocol().to_dict(), "decision_sample_ranges": malformed})
    assert "decision_sample_ranges" not in _protocol().to_dict()
    with pytest.raises(CalibrationError, match="continuous fields require"):
        _protocol(decision_sample_ranges=((4000, 0, 2400),))


@pytest.mark.parametrize("layouts", [
    ((4000, 0, 4000), (7200, 0, 4000)),
    ((4000, 1600, 4000), (7200, 4800, 7200)),
    ((4000, False, 2400), (7200, 0, 2400)),
    [[4000, 0, 2400], [7200, 0, 2400]],
])
def test_readiness_rejects_a_different_decision_plan(layouts):
    with pytest.raises(CalibrationError, match="continuous_package_decision_plan_mismatch"):
        classifier().require_continuous_support(
            (4000, 7200), parameters_digest="b" * 64,
            decision_sample_ranges=layouts,
        )
