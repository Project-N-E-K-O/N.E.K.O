"""Provider-neutral contracts for local audio decisions before ASR wiring."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math


SAMPLE_RATE_HZ = 16_000
ENDED_MICRO_EVENT_MAX_SAMPLES = SAMPLE_RATE_HZ // 5


class PrewireContractError(ValueError):
    """Raised when a pre-wire identity or range violates its contract."""


class PrewireDecisionState(str, Enum):
    PENDING = "pending"
    KEEP = "keep"
    DROP = "drop"
    UNCERTAIN = "uncertain"
    UNAVAILABLE = "unavailable"
    STALE = "stale"


class PrewireCommitStage(str, Enum):
    PENDING = "pending"
    SELECTED = "selected"
    ENQUEUED = "enqueued"
    WRITTEN = "written"
    REMOTE_CONFIRMED = "remote-confirmed"
    UNKNOWN = "unknown"


def _nonempty(name: str, value: object) -> str:
    if type(value) is not str or not value.strip():
        raise PrewireContractError(f"{name} must be a non-empty string")
    return value


def _sha256(name: str, value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise PrewireContractError(f"{name} must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True, slots=True, order=True)
class SampleRange:
    """Half-open sample range in a single 16 kHz timeline."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if type(self.start) is not int or type(self.end) is not int:
            raise PrewireContractError("sample range bounds must be integers")
        if self.start < 0 or self.end <= self.start:
            raise PrewireContractError(
                "sample range must be non-empty and non-negative"
            )

    @property
    def sample_count(self) -> int:
        return self.end - self.start

    def contains(self, other: SampleRange) -> bool:
        return self.start <= other.start and other.end <= self.end

    def overlaps(self, other: SampleRange) -> bool:
        return self.start < other.end and other.start < self.end


@dataclass(frozen=True, slots=True)
class PrewireStreamKey:
    """A local stream identity that exists before a Provider utterance."""

    session_id: str
    ingress_generation: int

    def __post_init__(self) -> None:
        _nonempty("session_id", self.session_id)
        if type(self.ingress_generation) is not int or self.ingress_generation < 1:
            raise PrewireContractError("ingress_generation must be a positive integer")


@dataclass(frozen=True, slots=True)
class PrewireIntervalIdentity:
    """Stable local ownership for one captured interval."""

    stream: PrewireStreamKey
    segment_id: int
    original_range: SampleRange
    profile_generation: str
    model_generation: str
    config_generation: str

    def __post_init__(self) -> None:
        if type(self.stream) is not PrewireStreamKey:
            raise PrewireContractError("stream must be PrewireStreamKey")
        if type(self.segment_id) is not int or self.segment_id < 1:
            raise PrewireContractError("segment_id must be a positive integer")
        if type(self.original_range) is not SampleRange:
            raise PrewireContractError("original_range must be SampleRange")
        for name in (
            "profile_generation",
            "model_generation",
            "config_generation",
        ):
            _nonempty(name, getattr(self, name))


@dataclass(frozen=True, slots=True)
class OriginalAsrMapping:
    """One-to-one mapping from captured samples to the local ASR timeline."""

    original_range: SampleRange
    asr_range: SampleRange

    def __post_init__(self) -> None:
        if type(self.original_range) is not SampleRange:
            raise PrewireContractError("mapping original_range must be SampleRange")
        if type(self.asr_range) is not SampleRange:
            raise PrewireContractError("mapping asr_range must be SampleRange")
        if self.original_range.sample_count != self.asr_range.sample_count:
            raise PrewireContractError(
                "original and ASR mappings must have equal length"
            )

    def map_original(self, sample_range: SampleRange) -> SampleRange:
        if not self.original_range.contains(sample_range):
            raise PrewireContractError("mapped range is outside original ownership")
        offset = self.asr_range.start - self.original_range.start
        return SampleRange(sample_range.start + offset, sample_range.end + offset)


@dataclass(frozen=True, slots=True)
class PrewireIntervalSpec:
    """Separate scoring, decision, and eventual commit ownership."""

    identity: PrewireIntervalIdentity
    scoring_range: SampleRange
    decision_range: SampleRange
    commit_range: SampleRange
    event_ended: bool
    boundary_trusted: bool
    independent_event: bool

    def __post_init__(self) -> None:
        if type(self.identity) is not PrewireIntervalIdentity:
            raise PrewireContractError("identity must be PrewireIntervalIdentity")
        for name in ("scoring_range", "decision_range", "commit_range"):
            value = getattr(self, name)
            if type(value) is not SampleRange:
                raise PrewireContractError(f"{name} must be SampleRange")
            if not self.identity.original_range.contains(value):
                raise PrewireContractError(f"{name} is outside original ownership")
        if not self.decision_range.contains(self.commit_range):
            raise PrewireContractError(
                "commit_range must be contained by decision_range"
            )
        for name in ("event_ended", "boundary_trusted", "independent_event"):
            if type(getattr(self, name)) is not bool:
                raise PrewireContractError(f"{name} must be bool")

    @property
    def permits_ended_micro_event_rule(self) -> bool:
        return bool(
            self.event_ended
            and self.boundary_trusted
            and self.independent_event
            and self.decision_range.sample_count < ENDED_MICRO_EVENT_MAX_SAMPLES
        )


@dataclass(frozen=True, slots=True)
class PrewireIntervalRecord:
    spec: PrewireIntervalSpec
    decision: PrewireDecisionState = PrewireDecisionState.PENDING
    score: float | None = None
    scoring_parameters_digest: str | None = None
    decision_reason: str | None = None
    used_ended_micro_event_rule: bool = False
    commit_stage: PrewireCommitStage = PrewireCommitStage.PENDING
    original_to_asr: OriginalAsrMapping | None = None

    def __post_init__(self) -> None:
        if type(self.spec) is not PrewireIntervalSpec:
            raise PrewireContractError("spec must be PrewireIntervalSpec")
        if type(self.decision) is not PrewireDecisionState:
            raise PrewireContractError("decision must be PrewireDecisionState")
        if self.score is not None:
            if type(self.score) not in {int, float} or not math.isfinite(self.score):
                raise PrewireContractError("score must be finite")
            if not -1.0 <= float(self.score) <= 1.0:
                raise PrewireContractError("score must be within [-1, 1]")
        if (self.score is None) != (self.scoring_parameters_digest is None):
            raise PrewireContractError(
                "score and scoring parameters must be recorded together"
            )
        if self.scoring_parameters_digest is not None:
            _sha256("scoring_parameters_digest", self.scoring_parameters_digest)
        if self.decision_reason is not None:
            _nonempty("decision_reason", self.decision_reason)
        if type(self.used_ended_micro_event_rule) is not bool:
            raise PrewireContractError("used_ended_micro_event_rule must be bool")
        if (
            self.used_ended_micro_event_rule
            and not self.spec.permits_ended_micro_event_rule
        ):
            raise PrewireContractError(
                "200ms rule requires an ended, trusted, independent micro event"
            )
        if type(self.commit_stage) is not PrewireCommitStage:
            raise PrewireContractError("commit_stage must be PrewireCommitStage")
        if self.original_to_asr is not None:
            if type(self.original_to_asr) is not OriginalAsrMapping:
                raise PrewireContractError(
                    "original_to_asr must be OriginalAsrMapping or None"
                )
            if self.original_to_asr.original_range != self.spec.commit_range:
                raise PrewireContractError(
                    "mapping must cover exactly the commit_range"
                )
        mapping_required = bool(
            self.decision is PrewireDecisionState.KEEP
            and self.commit_stage is not PrewireCommitStage.PENDING
        )
        if mapping_required != (self.original_to_asr is not None):
            raise PrewireContractError(
                "ASR mapping exists only for a selected keep range"
            )
        scoreless_micro_drop = bool(
            self.decision is PrewireDecisionState.DROP
            and self.used_ended_micro_event_rule
            and self.spec.permits_ended_micro_event_rule
        )
        if (
            self.decision
            in {
                PrewireDecisionState.KEEP,
                PrewireDecisionState.DROP,
                PrewireDecisionState.UNCERTAIN,
            }
            and not scoreless_micro_drop
            and self.score is None
        ):
            raise PrewireContractError(
                "keep, ordinary drop, and uncertain require score and parameters"
            )


@dataclass(frozen=True, slots=True)
class PrewireRelease:
    identity: PrewireIntervalIdentity
    original_range: SampleRange
    asr_range: SampleRange


@dataclass(frozen=True, slots=True)
class PrewireGap:
    """A decided original-timeline range that produces no ASR audio."""

    identity: PrewireIntervalIdentity
    original_range: SampleRange
    decision: PrewireDecisionState

    def __post_init__(self) -> None:
        if type(self.identity) is not PrewireIntervalIdentity:
            raise PrewireContractError("gap identity must be PrewireIntervalIdentity")
        if type(self.original_range) is not SampleRange:
            raise PrewireContractError("gap original_range must be SampleRange")
        if self.decision not in {
            PrewireDecisionState.DROP,
            PrewireDecisionState.UNCERTAIN,
            PrewireDecisionState.UNAVAILABLE,
            PrewireDecisionState.STALE,
        }:
            raise PrewireContractError("gap must carry a non-audio final decision")


@dataclass(frozen=True, slots=True)
class PrewireContiguousPlan:
    """Immutable proposal that is safe to claim only after enqueue succeeds."""

    ledger_id: str
    revision: int
    stream: PrewireStreamKey
    original_cursor_start: int
    original_cursor_end: int
    asr_cursor_start: int
    asr_cursor_end: int
    record_identities: tuple[PrewireIntervalIdentity, ...]
    releases: tuple[PrewireRelease, ...]
    gaps: tuple[PrewireGap, ...]

    def __post_init__(self) -> None:
        _nonempty("ledger_id", self.ledger_id)
        if type(self.revision) is not int or self.revision < 0:
            raise PrewireContractError("plan revision must be a non-negative integer")
        if type(self.stream) is not PrewireStreamKey:
            raise PrewireContractError("plan stream must be PrewireStreamKey")
        for name in (
            "original_cursor_start",
            "original_cursor_end",
            "asr_cursor_start",
            "asr_cursor_end",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise PrewireContractError(f"{name} must be a non-negative integer")
        if self.original_cursor_end < self.original_cursor_start:
            raise PrewireContractError("plan original cursor cannot move backwards")
        if self.asr_cursor_end < self.asr_cursor_start:
            raise PrewireContractError("plan ASR cursor cannot move backwards")
        for identity in self.record_identities:
            if (
                type(identity) is not PrewireIntervalIdentity
                or identity.stream != self.stream
            ):
                raise PrewireContractError("plan identities must belong to its stream")
        if len(set(self.record_identities)) != len(self.record_identities):
            raise PrewireContractError("plan identities must be unique")
        described: list[tuple[int, PrewireIntervalIdentity]] = []
        for release in self.releases:
            if type(release) is not PrewireRelease:
                raise PrewireContractError("plan releases must be PrewireRelease")
            described.append((release.original_range.start, release.identity))
        for gap in self.gaps:
            if type(gap) is not PrewireGap:
                raise PrewireContractError("plan gaps must be PrewireGap")
            described.append((gap.original_range.start, gap.identity))
        described_identities = tuple(
            identity for _, identity in sorted(described, key=lambda item: item[0])
        )
        if described_identities != self.record_identities:
            raise PrewireContractError(
                "plan releases and gaps must exactly describe its record identities"
            )
        if sum(release.asr_range.sample_count for release in self.releases) != (
            self.asr_cursor_end - self.asr_cursor_start
        ):
            raise PrewireContractError("plan ASR cursor delta must match its releases")
