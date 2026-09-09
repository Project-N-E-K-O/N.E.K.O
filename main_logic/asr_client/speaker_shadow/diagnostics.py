"""Immutable, content-free observations; never speaker admission authority."""

from dataclasses import dataclass
import re

from .contracts import SpeakerShadowCandidateKey

_OPAQUE_REF = re.compile(r"[a-f0-9]{16}\Z")
_VERSION_LABEL = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


@dataclass(frozen=True, slots=True)
class SpeakerScoreDiagnosticConfiguration:
    """Content-free identity of the configuration bound to one scorer.

    Profile, activation and installation values are process-local opaque refs.
    They remain distinct because none of those generations is interchangeable.
    """

    profile_generation_ref: str | None = None
    activation_generation_ref: str | None = None
    installation_ref: str | None = None
    model_version: str | None = None
    scoring_rule_version: str | None = None

    def __post_init__(self) -> None:
        refs = (
            self.profile_generation_ref,
            self.activation_generation_ref,
            self.installation_ref,
        )
        versions = (self.model_version, self.scoring_rule_version)
        if any(value is not None and not _OPAQUE_REF.fullmatch(value) for value in refs):
            raise ValueError("diagnostic identity must be an opaque reference")
        if any(value is not None and not _VERSION_LABEL.fullmatch(value) for value in versions):
            raise ValueError("diagnostic version must be a safe label")


@dataclass(frozen=True, slots=True)
class SpeakerShadowDiagnostic:
    candidate: SpeakerShadowCandidateKey
    stage: str
    worker_generation: int
    sample_rate_hz: int
    accepted_sample_count: int
    buffered_sample_count: int | None
    finish_sample_count: int | None
    minimum_sample_count: int | None
    score_attempt_count: int
    score_input_sample_count: int
    score_outcome: str
    scored_sample_count: int
    last_checkpoint_ms: int | None
    terminal_reason: str | None
    evidence_sequence_no: int
    anchor_applied: bool
    anchor_discard_prefix_sample_count: int | None
    scoring_deferred: bool
    score_id: str | None = None
    score_checkpoint_kind: str | None = None
    score_checkpoint_ms: int | None = None
    score_window_start_sample: int | None = None
    score_window_end_sample: int | None = None
    score_duration_ms: int | None = None
    score_continuity: str | None = None
    known_missing_sample_count: int | None = None
    known_duplicate_sample_count: int | None = None
    trimmed_prefix_sample_count: int | None = None
    profile_generation_ref: str | None = None
    activation_generation_ref: str | None = None
    installation_ref: str | None = None
    model_version: str | None = None
    scoring_rule_version: str | None = None
    quality_summary_outcome: str | None = None
    quality_summary_version: str | None = None
    rms_milli: int | None = None
    peak_milli: int | None = None
    near_silence_ratio_milli: int | None = None
    clipping_ratio_milli: int | None = None
    near_silence_threshold_milli: int | None = None
    clipping_threshold_milli: int | None = None
    voice_activity_measurement: str | None = None
    voice_activity_ratio_milli: int | None = None
