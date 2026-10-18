"""Persisted candidate calibration protocol; no registration or accuracy claim.

This contract describes the evidence an actual trained candidate classifier
must support. It cannot convert raw/terminal packages into candidate evidence,
fit thresholds, or admit a package to the application's release allowlist.
"""

from __future__ import annotations

from dataclasses import dataclass

from .prewire_gate.candidate_contracts import CandidateBinding, _digest, _text


@dataclass(frozen=True, slots=True)
class CandidateCalibrationContract:
    model_generation: str
    separator_generation: str
    reference_generation: str
    preprocessing_generation: str
    config_generation: str
    scoring_parameters_digest: str
    calibration_digest: str
    scoring_sample_counts: tuple[int, ...]
    decision_sample_ranges: tuple[tuple[int, int, int], ...]
    schema_version: int = 1
    application_scope: str = "continuous_candidate_interception"
    evidence_audio: str = "candidate_pcm16"
    sample_rate_hz: int = 16_000

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("candidate_calibration_schema_unsupported")
        if self.application_scope != "continuous_candidate_interception" or self.evidence_audio != "candidate_pcm16":
            raise ValueError("candidate_calibration_scope_mismatch")
        if type(self.sample_rate_hz) is not int or self.sample_rate_hz != 16_000:
            raise ValueError("candidate_calibration_sample_rate_mismatch")
        for name in ("model_generation", "separator_generation", "reference_generation", "preprocessing_generation", "config_generation"):
            _text(name, getattr(self, name))
        _digest("scoring_parameters_digest", self.scoring_parameters_digest)
        _digest("calibration_digest", self.calibration_digest)
        counts = self.scoring_sample_counts
        if type(counts) is not tuple or not counts or any(type(count) is not int or count <= 0 for count in counts) or tuple(sorted(set(counts))) != counts:
            raise ValueError("candidate_calibration_scoring_plan_invalid")
        layouts = self.decision_sample_ranges
        if type(layouts) is not tuple or not layouts:
            raise ValueError("candidate_calibration_decision_plan_invalid")
        for layout in layouts:
            if type(layout) is not tuple or len(layout) != 3 or any(type(value) is not int for value in layout):
                raise ValueError("candidate_calibration_decision_plan_invalid")
            count, start, end = layout
            if count not in counts or not 0 <= start < end <= count:
                raise ValueError("candidate_calibration_decision_plan_invalid")
        if len(set(layouts)) != len(layouts):
            raise ValueError("candidate_calibration_decision_plan_invalid")
        if {layout[0] for layout in layouts} != set(counts):
            raise ValueError("candidate_calibration_decision_plan_incomplete")

    def require_candidate_support(
        self,
        binding: CandidateBinding,
        *,
        sample_counts: tuple[int, ...],
        decision_sample_ranges: tuple[tuple[int, int, int], ...],
    ) -> None:
        if type(binding) is not CandidateBinding:
            raise ValueError("candidate_calibration_binding_invalid")
        for name in ("model_generation", "separator_generation", "reference_generation", "preprocessing_generation", "config_generation", "scoring_parameters_digest", "calibration_digest"):
            if getattr(binding, name) != getattr(self, name):
                raise ValueError(f"candidate_calibration_{name}_mismatch")
        if type(sample_counts) is not tuple or not sample_counts or any(type(count) is not int or count not in self.scoring_sample_counts for count in sample_counts):
            raise ValueError("candidate_calibration_scoring_plan_mismatch")
        if type(decision_sample_ranges) is not tuple or any(layout not in self.decision_sample_ranges for layout in decision_sample_ranges):
            raise ValueError("candidate_calibration_decision_plan_mismatch")

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "application_scope": self.application_scope,
            "evidence_audio": self.evidence_audio,
            "sample_rate_hz": self.sample_rate_hz,
            "model_generation": self.model_generation,
            "separator_generation": self.separator_generation,
            "reference_generation": self.reference_generation,
            "preprocessing_generation": self.preprocessing_generation,
            "config_generation": self.config_generation,
            "scoring_parameters_digest": self.scoring_parameters_digest,
            "calibration_digest": self.calibration_digest,
            "scoring_sample_counts": list(self.scoring_sample_counts),
            "decision_sample_ranges": [list(layout) for layout in self.decision_sample_ranges],
        }

    @classmethod
    def from_mapping(cls, mapping: dict[str, object]) -> CandidateCalibrationContract:
        expected = set(cls.__dataclass_fields__)
        if type(mapping) is not dict or set(mapping) != expected:
            raise ValueError("candidate_calibration_mapping_invalid")
        values = dict(mapping)
        counts = values["scoring_sample_counts"]
        layouts = values["decision_sample_ranges"]
        if type(counts) is not list or type(layouts) is not list or any(type(layout) is not list for layout in layouts):
            raise ValueError("candidate_calibration_mapping_invalid")
        values["scoring_sample_counts"] = tuple(counts)
        values["decision_sample_ranges"] = tuple(tuple(layout) for layout in layouts)
        return cls(**values)
