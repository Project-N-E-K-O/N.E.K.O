"""Immutable provider-neutral separated-candidate evidence and PCM contracts."""
from __future__ import annotations
from dataclasses import dataclass, field
import hashlib
import math
from .contracts import PrewireDecisionState, PrewireIntervalSpec, PrewireStreamKey, SampleRange
from .decision import PrewireQualitySummary


def _text(name: str, value: object) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _digest(name: str, value: object) -> None:
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


@dataclass(frozen=True, slots=True)
class CandidateBinding:
    stream: PrewireStreamKey
    profile_generation: str
    model_generation: str
    config_generation: str
    separator_generation: str
    reference_generation: str
    scoring_parameters_digest: str
    calibration_digest: str
    preprocessing_generation: str = "pcm16-16khz-v1"

    def __post_init__(self) -> None:
        if type(self.stream) is not PrewireStreamKey:
            raise ValueError("stream must be PrewireStreamKey")
        for name in ("profile_generation", "model_generation", "config_generation", "separator_generation", "reference_generation", "preprocessing_generation"):
            _text(name, getattr(self, name))
        _digest("scoring_parameters_digest", self.scoring_parameters_digest)
        _digest("calibration_digest", self.calibration_digest)

    def validate_spec(self, spec: PrewireIntervalSpec) -> None:
        if type(spec) is not PrewireIntervalSpec:
            raise ValueError("spec must be PrewireIntervalSpec")
        identity = spec.identity
        if (identity.stream, identity.profile_generation, identity.model_generation, identity.config_generation) != (
            self.stream, self.profile_generation, self.model_generation, self.config_generation,
        ):
            raise ValueError("candidate_interval_identity_mismatch")


@dataclass(frozen=True, slots=True)
class CandidateAudio:
    binding: CandidateBinding
    candidate_id: str
    original_range: SampleRange
    pcm16: bytes = field(repr=False)
    quality: PrewireQualitySummary = field(default_factory=PrewireQualitySummary)
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.binding) is not CandidateBinding or type(self.original_range) is not SampleRange:
            raise ValueError("candidate requires exact binding and sample range")
        _text("candidate_id", self.candidate_id)
        if type(self.pcm16) is not bytes or len(self.pcm16) != self.original_range.sample_count * 2:
            raise ValueError("candidate PCM must exactly cover its original range")
        if type(self.quality) is not PrewireQualitySummary:
            raise ValueError("quality must be PrewireQualitySummary")
        if self.quality.speech_samples is not None and self.quality.speech_samples > self.original_range.sample_count:
            raise ValueError("candidate speech samples exceed its scoring range")
        object.__setattr__(self, "content_digest", hashlib.sha256(self.pcm16).hexdigest())

    def copy_range(self, sample_range: SampleRange) -> bytes:
        if not self.original_range.contains(sample_range):
            raise ValueError("candidate_slice_outside_scoring_range")
        start = (sample_range.start - self.original_range.start) * 2
        return self.pcm16[start:start + sample_range.sample_count * 2]


@dataclass(frozen=True, slots=True)
class CandidateBatch:
    binding: CandidateBinding
    spec: PrewireIntervalSpec
    candidates: tuple[CandidateAudio, ...]

    def __post_init__(self) -> None:
        if type(self.binding) is not CandidateBinding:
            raise ValueError("binding must be CandidateBinding")
        self.binding.validate_spec(self.spec)
        if type(self.candidates) is not tuple or len(self.candidates) > 2:
            raise ValueError("at most two immutable candidates are supported")
        seen: set[str] = set()
        for candidate in self.candidates:
            if type(candidate) is not CandidateAudio or candidate.binding != self.binding:
                raise ValueError("candidate_binding_mismatch")
            if candidate.original_range != self.spec.scoring_range:
                raise ValueError("candidate_scoring_range_mismatch")
            if candidate.candidate_id in seen:
                raise ValueError("candidate IDs must be unique within an interval")
            seen.add(candidate.candidate_id)


@dataclass(frozen=True, slots=True)
class CandidateEvidence:
    candidate_id: str
    content_digest: str
    score: float | None
    decision: PrewireDecisionState
    reason: str

    def __post_init__(self) -> None:
        _text("candidate_id", self.candidate_id)
        _digest("content_digest", self.content_digest)
        _text("reason", self.reason)
        if type(self.decision) is not PrewireDecisionState or self.decision is PrewireDecisionState.PENDING:
            raise ValueError("candidate evidence must be completed")
        if self.score is not None and (type(self.score) not in {int, float} or not math.isfinite(self.score) or not -1 <= self.score <= 1):
            raise ValueError("candidate score must be finite in [-1, 1]")


@dataclass(frozen=True, slots=True)
class CandidateSelection:
    binding: CandidateBinding
    spec: PrewireIntervalSpec
    decision: PrewireDecisionState
    reason: str
    evidence: tuple[CandidateEvidence, ...]
    selected: CandidateAudio | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.binding.validate_spec(self.spec)
        _text("reason", self.reason)
        if type(self.decision) is not PrewireDecisionState or self.decision is PrewireDecisionState.PENDING:
            raise ValueError("candidate selection must be completed")
        if type(self.evidence) is not tuple or len(self.evidence) > 2 or any(type(item) is not CandidateEvidence for item in self.evidence):
            raise ValueError("invalid candidate evidence")
        if len({item.candidate_id for item in self.evidence}) != len(self.evidence):
            raise ValueError("duplicate candidate evidence")
        if self.decision is PrewireDecisionState.KEEP:
            if type(self.selected) is not CandidateAudio:
                raise ValueError("KEEP requires the actual scored candidate")
            if self.selected.binding != self.binding or self.selected.original_range != self.spec.scoring_range:
                raise ValueError("selected_candidate_identity_mismatch")
            owners = tuple(item for item in self.evidence if item.decision is PrewireDecisionState.KEEP)
            if len(owners) != 1 or any(item.decision not in {PrewireDecisionState.KEEP, PrewireDecisionState.DROP} for item in self.evidence):
                raise ValueError("KEEP requires exactly one owner and no unresolved candidate")
            owner = owners[0]
            if owner.candidate_id != self.selected.candidate_id or owner.content_digest != self.selected.content_digest or owner.score is None:
                raise ValueError("selected_candidate_content_mismatch")
        elif self.selected is not None:
            raise ValueError("only KEEP may carry selected PCM")

    @property
    def score(self) -> float | None:
        if self.selected is None:
            return None
        return next(item.score for item in self.evidence if item.candidate_id == self.selected.candidate_id)

    @property
    def commit_pcm16(self) -> bytes:
        return b"" if self.selected is None else self.selected.copy_range(self.spec.commit_range)

    def validate_for(self, spec: PrewireIntervalSpec, binding: CandidateBinding) -> None:
        if self.spec != spec or self.binding != binding:
            raise ValueError("candidate_selection_identity_or_range_changed")
        if self.selected is not None and hashlib.sha256(self.selected.pcm16).hexdigest() != self.selected.content_digest:
            raise ValueError("candidate_selection_content_changed")
