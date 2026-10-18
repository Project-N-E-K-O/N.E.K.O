from dataclasses import replace
import json

import pytest

from main_logic.voice_identity_service.candidate_calibration import CandidateCalibrationContract
from main_logic.voice_identity_service.candidate_identity import CandidateBinding
from main_logic.voice_identity_service.prewire_gate.contracts import PrewireStreamKey

pytestmark = pytest.mark.runtime


def binding():
    return CandidateBinding(PrewireStreamKey("session", 1), "profile", "scorer", "config", "tfmap", "reference", "a" * 64, "b" * 64)


def contract():
    return CandidateCalibrationContract("scorer", "tfmap", "reference", "pcm16-16khz-v1", "config", "a" * 64, "b" * 64, (8,), ((8, 0, 6),))


def test_persisted_roundtrip_preserves_candidate_source_and_geometry(tmp_path):
    path = tmp_path / "candidate-contract.json"
    path.write_text(json.dumps(contract().to_mapping()), encoding="utf-8")
    restored = CandidateCalibrationContract.from_mapping(json.loads(path.read_text(encoding="utf-8")))
    assert restored == contract()
    restored.require_candidate_support(binding(), sample_counts=(8,), decision_sample_ranges=((8, 0, 6),))
    assert restored.application_scope == "continuous_candidate_interception"
    assert restored.evidence_audio == "candidate_pcm16"


@pytest.mark.parametrize("field,value", [
    ("model_generation", "different-scorer"), ("separator_generation", "different-separator"),
    ("reference_generation", "different-reference"), ("preprocessing_generation", "different-frontend"),
    ("config_generation", "different-config"), ("scoring_parameters_digest", "c" * 64),
    ("calibration_digest", "d" * 64),
])
def test_persisted_contract_rejects_changed_processing_identity(field, value):
    restored = CandidateCalibrationContract.from_mapping(json.loads(json.dumps(contract().to_mapping())))
    with pytest.raises(ValueError):
        restored.require_candidate_support(replace(binding(), **{field: value}), sample_counts=(8,), decision_sample_ranges=((8, 0, 6),))


def test_raw_terminal_packages_and_scope_relabeling_do_not_admit_candidate_protocol():
    raw = {"protocol": {"application_scope": "terminal_short_event"}, "release_status": "registered"}
    with pytest.raises(ValueError):
        CandidateCalibrationContract.from_mapping(raw)
    for key, value in (("application_scope", "terminal_short_event"), ("application_scope", "continuous_interception"), ("evidence_audio", "raw_pcm16")):
        mapping = contract().to_mapping()
        mapping[key] = value
        with pytest.raises(ValueError):
            CandidateCalibrationContract.from_mapping(mapping)
    mapping = contract().to_mapping()
    mapping["release_status"] = "registered"
    with pytest.raises(ValueError):
        CandidateCalibrationContract.from_mapping(mapping)


def test_scoring_lengths_and_decision_geometry_need_actual_candidate_support():
    target = contract()
    for counts, layouts in (((4,), ((4, 0, 4),)), ((8,), ((8, 0, 8),)), ((8,), ((8, 1, 6),))):
        with pytest.raises(ValueError):
            target.require_candidate_support(binding(), sample_counts=counts, decision_sample_ranges=layouts)
    with pytest.raises(ValueError):
        replace(target, decision_sample_ranges=())
    with pytest.raises(ValueError):
        replace(target, decision_sample_ranges=((8, 0, 9),))
