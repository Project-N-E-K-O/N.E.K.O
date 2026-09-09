"""Offline voice-identity calibration contracts and deterministic fitting.

Calibration outputs a logistic evidence score.  It must not be interpreted as
the probability that a person has a particular identity.
"""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from enum import Enum
import hashlib
import json
import math
from typing import Any, Iterable, Mapping


CALIBRATION_SCHEMA_VERSION = 1
DATASET_SCHEMA_VERSION = 1
CALIBRATION_MAX_AUDIO_MS = 4_000.0
TERMINAL_SHORT_SCOPE = "terminal_short_v1"
TERMINAL_SHORT_MINIMUM_AUDIO_MS = 45.0
TERMINAL_SHORT_MAXIMUM_AUDIO_MS_EXCLUSIVE = 1_500.0
REFERENCE_FORMATION_PROTOCOL = "three_normalized_embeddings_centroid_v1"
CALIBRATION_FEATURE_ORDER = (
    "raw_similarity",
    "audio_ms",
    "rms",
    "peak",
    "near_silence",
    "clipping",
)
CALIBRATION_MODEL_FEATURE_ORDER = CALIBRATION_FEATURE_ORDER + tuple(
    f"{name}_missing" for name in CALIBRATION_FEATURE_ORDER
)
CALIBRATION_REGISTRATION_SCHEME = "app_allowlisted_sha256_v1"
_CALIBRATION_REGISTRATION_AUTHORITY = object()


class CalibrationError(ValueError):
    """Raised when a calibration artifact or dataset violates its contract."""


class CalibrationOutcome(str, Enum):
    OWNER = "owner"
    NONOWNER = "nonowner"
    UNCERTAIN = "uncertain"
    UNSUPPORTED = "unsupported"
    FAILURE = "failure"


class DatasetSplit(str, Enum):
    TRAIN = "train"
    DEV = "dev"
    TEST = "test"


def _finite(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CalibrationError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise CalibrationError(f"{name} must be finite")
    return result


def _nonempty(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CalibrationError(f"{name} must be a non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class CalibrationFeatures:
    raw_similarity: float | None
    audio_ms: float | None
    rms: float | None = None
    peak: float | None = None
    near_silence: float | None = None
    clipping: float | None = None

    def __post_init__(self) -> None:
        ranges = {
            "raw_similarity": (-1.0, 1.0),
            "audio_ms": (0.0, CALIBRATION_MAX_AUDIO_MS),
            "rms": (0.0, 1.0),
            "peak": (0.0, 1.0),
            "near_silence": (0.0, 1.0),
            "clipping": (0.0, 1.0),
        }
        for name, (lower, upper) in ranges.items():
            value = getattr(self, name)
            if value is None:
                continue
            number = _finite(name, value)
            if number < lower or number > upper or (name == "audio_ms" and number == 0):
                raise CalibrationError(f"{name} is outside its supported range")
            object.__setattr__(self, name, number)

    def model_vector(self) -> tuple[float, ...]:
        values = tuple(getattr(self, name) for name in CALIBRATION_FEATURE_ORDER)
        return tuple(0.0 if value is None else value for value in values) + tuple(
            1.0 if value is None else 0.0 for value in values
        )

    def to_dict(self) -> dict[str, float | None]:
        return {name: getattr(self, name) for name in CALIBRATION_FEATURE_ORDER}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CalibrationFeatures:
        if type(value) is not dict:
            raise CalibrationError("calibration features must be a JSON object")
        unknown = set(value) - set(CALIBRATION_FEATURE_ORDER)
        if unknown:
            raise CalibrationError(f"unknown calibration features: {sorted(unknown)}")
        return cls(**{name: value.get(name) for name in CALIBRATION_FEATURE_ORDER})


@dataclass(frozen=True, slots=True)
class CalibrationProtocol:
    model_id: str
    model_revision: str
    embedding_dimension: int
    preprocessing_contract_id: str
    preprocessing_revision: int
    noise_reduction_enabled: bool
    reference_formation_protocol: str = REFERENCE_FORMATION_PROTOCOL
    reference_recording_count: int = 3
    application_scope: str = TERMINAL_SHORT_SCOPE
    minimum_audio_ms: float = TERMINAL_SHORT_MINIMUM_AUDIO_MS
    maximum_audio_ms_exclusive: float = TERMINAL_SHORT_MAXIMUM_AUDIO_MS_EXCLUSIVE

    def __post_init__(self) -> None:
        _nonempty("model_id", self.model_id)
        _nonempty("model_revision", self.model_revision)
        _nonempty("preprocessing_contract_id", self.preprocessing_contract_id)
        _nonempty("reference_formation_protocol", self.reference_formation_protocol)
        if type(self.embedding_dimension) is not int or self.embedding_dimension <= 0:
            raise CalibrationError("embedding_dimension must be positive")
        if type(self.preprocessing_revision) is not int or self.preprocessing_revision <= 0:
            raise CalibrationError("preprocessing_revision must be positive")
        if not isinstance(self.noise_reduction_enabled, bool):
            raise CalibrationError("noise_reduction_enabled must be boolean")
        if type(self.reference_recording_count) is not int or self.reference_recording_count <= 0:
            raise CalibrationError("reference_recording_count must be positive")
        _nonempty("application_scope", self.application_scope)
        minimum = _finite("minimum_audio_ms", self.minimum_audio_ms)
        maximum = _finite("maximum_audio_ms_exclusive", self.maximum_audio_ms_exclusive)
        if minimum <= 0 or minimum >= maximum:
            raise CalibrationError("calibration application audio range is invalid")
        object.__setattr__(self, "minimum_audio_ms", minimum)
        object.__setattr__(self, "maximum_audio_ms_exclusive", maximum)

    @property
    def has_supported_reference_formation(self) -> bool:
        return (
            self.reference_formation_protocol == REFERENCE_FORMATION_PROTOCOL
            and self.reference_recording_count == 3
        )

    @property
    def has_supported_terminal_short_domain(self) -> bool:
        return (
            self.application_scope == TERMINAL_SHORT_SCOPE
            and self.minimum_audio_ms == TERMINAL_SHORT_MINIMUM_AUDIO_MS
            and self.maximum_audio_ms_exclusive
            == TERMINAL_SHORT_MAXIMUM_AUDIO_MS_EXCLUSIVE
        )

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CalibrationProtocol:
        if type(value) is not dict:
            raise CalibrationError("calibration protocol must be a JSON object")
        expected = set(cls.__dataclass_fields__)
        if set(value) != expected:
            raise CalibrationError("calibration protocol fields do not match schema")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    outcome: CalibrationOutcome
    evidence_score: float | None
    reason: str


@dataclass(frozen=True, slots=True)
class CalibrationPackage:
    schema_version: int
    package_revision: str
    protocol: CalibrationProtocol
    feature_order: tuple[str, ...]
    center: tuple[float, ...]
    scale: tuple[float, ...]
    weights: tuple[float, ...]
    intercept: float
    nonowner_boundary: float
    owner_boundary: float
    dataset_digest: str
    split_group_counts: tuple[int, int, int]
    fit_method: str = "balanced_logistic_v1"
    release_status: str = "candidate"

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != CALIBRATION_SCHEMA_VERSION:
            raise CalibrationError("unsupported calibration schema version")
        _nonempty("package_revision", self.package_revision)
        if not self.protocol.has_supported_reference_formation:
            raise CalibrationError("package uses an unsupported reference formation protocol")
        if not self.protocol.has_supported_terminal_short_domain:
            raise CalibrationError("package uses an unsupported calibration application domain")
        if tuple(self.feature_order) != CALIBRATION_MODEL_FEATURE_ORDER:
            raise CalibrationError("package feature order does not match the fixed contract")
        size = len(CALIBRATION_MODEL_FEATURE_ORDER)
        if any(len(values) != size for values in (self.center, self.scale, self.weights)):
            raise CalibrationError("package vector dimensions do not match feature order")
        for field_name in ("center", "scale", "weights"):
            values = tuple(_finite(field_name, item) for item in getattr(self, field_name))
            object.__setattr__(self, field_name, values)
        if any(value <= 0 for value in self.scale):
            raise CalibrationError("all feature scales must be positive")
        for name in ("intercept", "nonowner_boundary", "owner_boundary"):
            object.__setattr__(self, name, _finite(name, getattr(self, name)))
        if self.nonowner_boundary >= self.owner_boundary:
            raise CalibrationError("nonowner_boundary must be strictly below owner_boundary")
        if len(self.dataset_digest) != 64 or any(c not in "0123456789abcdef" for c in self.dataset_digest):
            raise CalibrationError("dataset_digest must be a lowercase SHA-256 digest")
        if len(self.split_group_counts) != 3 or any(
            type(count) is not int or count <= 0 for count in self.split_group_counts
        ):
            raise CalibrationError("all train/dev/test group counts must be positive")
        if self.release_status not in {"candidate", "registered"}:
            raise CalibrationError("release_status must be candidate or registered")
        if self.fit_method != "balanced_logistic_v1":
            raise CalibrationError("unsupported calibration fit method")

    def classify(
        self,
        features: CalibrationFeatures | Mapping[str, Any],
        runtime_protocol: CalibrationProtocol,
    ) -> CalibrationResult:
        if not isinstance(runtime_protocol, CalibrationProtocol):
            return CalibrationResult(CalibrationOutcome.FAILURE, None, "invalid_protocol_input")
        if not isinstance(features, CalibrationFeatures):
            try:
                features = CalibrationFeatures.from_dict(features)
            except (CalibrationError, TypeError, ValueError):
                return CalibrationResult(CalibrationOutcome.FAILURE, None, "invalid_feature_input")
        if runtime_protocol != self.protocol or not runtime_protocol.has_supported_reference_formation:
            return CalibrationResult(CalibrationOutcome.UNSUPPORTED, None, "protocol_mismatch")
        if features.raw_similarity is None or features.audio_ms is None:
            return CalibrationResult(CalibrationOutcome.UNSUPPORTED, None, "missing_primary_feature")
        if not (
            self.protocol.minimum_audio_ms
            <= features.audio_ms
            < self.protocol.maximum_audio_ms_exclusive
        ):
            return CalibrationResult(CalibrationOutcome.UNSUPPORTED, None, "outside_application_domain")
        try:
            normalized = tuple(
                (value - center) / scale
                for value, center, scale in zip(features.model_vector(), self.center, self.scale)
            )
            score = self.intercept + math.fsum(
                weight * value for weight, value in zip(self.weights, normalized)
            )
        except (ArithmeticError, OverflowError):
            return CalibrationResult(CalibrationOutcome.FAILURE, None, "numeric_evaluation_failure")
        if not math.isfinite(score):
            return CalibrationResult(CalibrationOutcome.FAILURE, None, "nonfinite_evidence_score")
        if score <= self.nonowner_boundary:
            outcome = CalibrationOutcome.NONOWNER
        elif score >= self.owner_boundary:
            outcome = CalibrationOutcome.OWNER
        else:
            outcome = CalibrationOutcome.UNCERTAIN
        return CalibrationResult(outcome, score, "calibrated_evidence")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "package_revision": self.package_revision,
            "protocol": self.protocol.to_dict(),
            "feature_order": list(self.feature_order),
            "center": list(self.center),
            "scale": list(self.scale),
            "weights": list(self.weights),
            "intercept": self.intercept,
            "nonowner_boundary": self.nonowner_boundary,
            "owner_boundary": self.owner_boundary,
            "dataset_digest": self.dataset_digest,
            "split_group_counts": list(self.split_group_counts),
            "fit_method": self.fit_method,
            "release_status": self.release_status,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CalibrationPackage:
        if type(value) is not dict:
            raise CalibrationError("calibration package must be a JSON object")
        expected = set(cls.__dataclass_fields__)
        if set(value) != expected:
            raise CalibrationError("calibration package fields do not match schema")
        converted = dict(value)
        converted["protocol"] = CalibrationProtocol.from_dict(converted["protocol"])
        for name in ("feature_order", "center", "scale", "weights", "split_group_counts"):
            if type(converted[name]) is not list:
                raise CalibrationError(f"{name} must be a JSON array")
            converted[name] = tuple(converted[name])
        return cls(**converted)


def calibration_package_artifact_sha256(package: CalibrationPackage) -> str:
    """Return the canonical SHA-256 for every serialized package field."""

    if type(package) is not CalibrationPackage:
        raise TypeError("package must be CalibrationPackage")
    payload = json.dumps(
        package.to_dict(),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validated_artifact_sha256(name: str, value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise CalibrationError(f"{name} must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True, slots=True)
class RegisteredCalibration:
    """A package admitted by an application-owned artifact digest allowlist."""

    package: CalibrationPackage
    artifact_sha256: str
    registration_scheme: str = field(
        init=False,
        default=CALIBRATION_REGISTRATION_SCHEME,
    )
    _authority: InitVar[object | None] = None

    def __post_init__(self, _authority: object | None) -> None:
        if _authority is not _CALIBRATION_REGISTRATION_AUTHORITY:
            raise CalibrationError(
                "registered calibration must be created by the controlled loader"
            )
        if type(self.package) is not CalibrationPackage:
            raise TypeError("package must be CalibrationPackage")
        digest = _validated_artifact_sha256(
            "artifact_sha256",
            self.artifact_sha256,
        )
        if digest != calibration_package_artifact_sha256(self.package):
            raise CalibrationError("registered calibration artifact digest mismatch")


def register_calibration_package(
    package: CalibrationPackage,
    *,
    expected_digest: str,
) -> RegisteredCalibration:
    """Admit one immutable package only when the app-owned digest matches."""

    if type(package) is not CalibrationPackage:
        raise TypeError("package must be CalibrationPackage")
    expected = _validated_artifact_sha256("expected_digest", expected_digest)
    if (
        not package.protocol.has_supported_reference_formation
        or not package.protocol.has_supported_terminal_short_domain
    ):
        raise CalibrationError("calibration package protocol is not registered for this application")
    actual = calibration_package_artifact_sha256(package)
    if actual != expected:
        raise CalibrationError("calibration package artifact digest mismatch")
    return RegisteredCalibration(
        package,
        actual,
        _authority=_CALIBRATION_REGISTRATION_AUTHORITY,
    )


@dataclass(frozen=True, slots=True)
class CalibrationExample:
    example_id: str
    group_id: str
    reference_speaker_id: str
    candidate_speaker_id: str
    candidate_session_id: str
    reference_session_ids: tuple[str, str, str]
    candidate_recording_family_id: str
    reference_recording_family_ids: tuple[str, str, str]
    source_recording_id: str
    reference_recording_ids: tuple[str, str, str]
    split: DatasetSplit
    label: CalibrationOutcome
    protocol: CalibrationProtocol
    features: CalibrationFeatures

    def __post_init__(self) -> None:
        for name in (
            "example_id",
            "group_id",
            "reference_speaker_id",
            "candidate_speaker_id",
            "candidate_session_id",
            "candidate_recording_family_id",
            "source_recording_id",
        ):
            _nonempty(name, getattr(self, name))
        refs = tuple(self.reference_recording_ids)
        if len(refs) != 3 or len(set(refs)) != 3 or any(not item for item in refs):
            raise CalibrationError("exactly three distinct reference recordings are required")
        if self.source_recording_id in refs:
            raise CalibrationError("source recording cannot also be an enrollment reference")
        object.__setattr__(self, "reference_recording_ids", refs)
        reference_sessions = tuple(self.reference_session_ids)
        if len(reference_sessions) != 3 or len(set(reference_sessions)) != 3 or any(
            not item for item in reference_sessions
        ):
            raise CalibrationError("three distinct reference sessions are required")
        object.__setattr__(self, "reference_session_ids", reference_sessions)
        reference_families = tuple(self.reference_recording_family_ids)
        if len(reference_families) != 3 or len(set(reference_families)) != 3 or any(
            not item for item in reference_families
        ):
            raise CalibrationError("three distinct reference recording families are required")
        if self.candidate_recording_family_id in reference_families:
            raise CalibrationError("candidate and reference recording families must be independent")
        object.__setattr__(self, "reference_recording_family_ids", reference_families)
        if not isinstance(self.split, DatasetSplit):
            raise CalibrationError("split must be a DatasetSplit")
        if not isinstance(self.protocol, CalibrationProtocol):
            raise CalibrationError("protocol must be a CalibrationProtocol")
        if not isinstance(self.features, CalibrationFeatures):
            raise CalibrationError("features must be CalibrationFeatures")
        if self.label not in {CalibrationOutcome.OWNER, CalibrationOutcome.NONOWNER}:
            raise CalibrationError("dataset labels must be owner or nonowner")
        if self.features.audio_ms is None or not (
            self.protocol.minimum_audio_ms
            <= self.features.audio_ms
            < self.protocol.maximum_audio_ms_exclusive
        ):
            raise CalibrationError("calibration example is outside terminal-short audio domain")
        speakers_match = self.candidate_speaker_id == self.reference_speaker_id
        if (self.label == CalibrationOutcome.OWNER) != speakers_match:
            raise CalibrationError("label is inconsistent with opaque speaker identities")
        if self.candidate_session_id in reference_sessions:
            raise CalibrationError("candidate session must be independent from reference sessions")

    def to_dict(self) -> dict[str, Any]:
        return {
            "example_id": self.example_id,
            "group_id": self.group_id,
            "reference_speaker_id": self.reference_speaker_id,
            "candidate_speaker_id": self.candidate_speaker_id,
            "candidate_session_id": self.candidate_session_id,
            "reference_session_ids": list(self.reference_session_ids),
            "candidate_recording_family_id": self.candidate_recording_family_id,
            "reference_recording_family_ids": list(self.reference_recording_family_ids),
            "source_recording_id": self.source_recording_id,
            "reference_recording_ids": list(self.reference_recording_ids),
            "split": self.split.value,
            "label": self.label.value,
            "protocol": self.protocol.to_dict(),
            "features": self.features.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CalibrationExample:
        if type(value) is not dict:
            raise CalibrationError("calibration example must be a JSON object")
        expected = set(cls.__dataclass_fields__)
        if set(value) != expected:
            raise CalibrationError("calibration example fields do not match schema")
        converted = dict(value)
        for name in (
            "reference_recording_ids",
            "reference_session_ids",
            "reference_recording_family_ids",
        ):
            if type(converted[name]) is not list:
                raise CalibrationError(f"{name} must be a JSON array")
            converted[name] = tuple(converted[name])
        converted["split"] = DatasetSplit(converted["split"])
        converted["label"] = CalibrationOutcome(converted["label"])
        converted["protocol"] = CalibrationProtocol.from_dict(converted["protocol"])
        converted["features"] = CalibrationFeatures.from_dict(converted["features"])
        return cls(**converted)


def validate_fit_dataset(examples: Iterable[CalibrationExample]) -> tuple[CalibrationExample, ...]:
    records = tuple(examples)
    if not records:
        raise CalibrationError("calibration data is empty")
    protocol = records[0].protocol
    if not protocol.has_supported_reference_formation:
        raise CalibrationError("dataset reference formation protocol is unsupported")
    if not protocol.has_supported_terminal_short_domain:
        raise CalibrationError("dataset calibration application domain is unsupported")
    groups: dict[str, DatasetSplit] = {}
    recordings: dict[str, DatasetSplit] = {}
    speakers: dict[str, DatasetSplit] = {}
    sessions: dict[str, DatasetSplit] = {}
    recording_families: dict[str, DatasetSplit] = {}
    example_ids: set[str] = set()
    for item in records:
        if item.example_id in example_ids:
            raise CalibrationError("example_id values must be unique")
        example_ids.add(item.example_id)
        if item.protocol != protocol:
            raise CalibrationError("dataset mixes incompatible protocols")
        if item.features.raw_similarity is None or item.features.audio_ms is None:
            raise CalibrationError("fit examples require similarity and audio duration")
        previous = groups.setdefault(item.group_id, item.split)
        if previous != item.split:
            raise CalibrationError("a group appears in multiple dataset splits")
        for speaker_id in (item.reference_speaker_id, item.candidate_speaker_id):
            previous = speakers.setdefault(speaker_id, item.split)
            if previous != item.split:
                raise CalibrationError("an opaque speaker appears in multiple dataset splits")
        for session_id in (item.candidate_session_id, *item.reference_session_ids):
            previous = sessions.setdefault(session_id, item.split)
            if previous != item.split:
                raise CalibrationError("an opaque session appears in multiple dataset splits")
        for family_id in (
            item.candidate_recording_family_id,
            *item.reference_recording_family_ids,
        ):
            previous = recording_families.setdefault(family_id, item.split)
            if previous != item.split:
                raise CalibrationError("a recording family appears in multiple dataset splits")
        for recording_id in (item.source_recording_id, *item.reference_recording_ids):
            previous = recordings.setdefault(recording_id, item.split)
            if previous != item.split:
                raise CalibrationError("a raw recording appears in multiple dataset splits")
    for split in DatasetSplit:
        labels = {item.label for item in records if item.split == split}
        if labels != {CalibrationOutcome.OWNER, CalibrationOutcome.NONOWNER}:
            raise CalibrationError(f"{split.value} split requires both owner and nonowner examples")
    return tuple(sorted(records, key=lambda item: item.example_id))


def _mean_rows(rows: list[tuple[float, ...]]) -> tuple[float, ...]:
    return tuple(math.fsum(row[index] for row in rows) / len(rows) for index in range(len(rows[0])))


def _dataset_digest(records: tuple[CalibrationExample, ...]) -> str:
    payload = [item.to_dict() for item in sorted(records, key=lambda item: item.example_id)]
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _fit_linear_calibration(
    examples: Iterable[CalibrationExample],
    *,
    package_revision: str,
    max_nonowner_as_owner_rate: float,
    max_owner_as_nonowner_rate: float,
) -> CalibrationPackage:
    """Fit balanced logistic evidence and constrained development boundaries."""
    records = validate_fit_dataset(examples)
    _nonempty("package_revision", package_revision)
    max_false_owner = _finite("max_nonowner_as_owner_rate", max_nonowner_as_owner_rate)
    max_false_nonowner = _finite("max_owner_as_nonowner_rate", max_owner_as_nonowner_rate)
    if not (0 <= max_false_owner < 1 and 0 <= max_false_nonowner < 1):
        raise CalibrationError("development error-rate limits must be in [0, 1)")
    train = [item for item in records if item.split == DatasetSplit.TRAIN]
    vectors = [item.features.model_vector() for item in train]
    center = _mean_rows(vectors)
    scale = tuple(
        max(
            math.sqrt(math.fsum((row[i] - center[i]) ** 2 for row in vectors) / len(vectors)),
            1e-9,
        )
        for i in range(len(center))
    )
    normalized = [tuple((row[i] - center[i]) / scale[i] for i in range(len(row))) for row in vectors]
    owner = [row for row, item in zip(normalized, train) if item.label == CalibrationOutcome.OWNER]
    nonowner = [row for row, item in zip(normalized, train) if item.label == CalibrationOutcome.NONOWNER]
    # Deterministic full-batch gradient descent on a class-balanced logistic
    # objective.  The returned logit remains an evidence score, not a probability.
    weights = [0.0] * len(center)
    intercept = 0.0
    labels = [1.0 if item.label == CalibrationOutcome.OWNER else 0.0 for item in train]
    class_counts = {0.0: len(nonowner), 1.0: len(owner)}
    for iteration in range(1600):
        gradient = [0.0] * len(weights)
        intercept_gradient = 0.0
        for row, label in zip(normalized, labels):
            logit = max(-40.0, min(40.0, intercept + math.fsum(w * x for w, x in zip(weights, row))))
            fitted = 1.0 / (1.0 + math.exp(-logit))
            sample_weight = 0.5 / class_counts[label]
            residual = sample_weight * (fitted - label)
            intercept_gradient += residual
            for index, value in enumerate(row):
                gradient[index] += residual * value
        step = 0.25 / math.sqrt(iteration + 1.0)
        intercept -= step * intercept_gradient
        for index in range(len(weights)):
            weights[index] -= step * (gradient[index] + 1e-4 * weights[index])
    weights = tuple(weights)
    if not all(math.isfinite(value) for value in (*weights, intercept)):
        raise CalibrationError("balanced logistic fit produced nonfinite parameters")

    def score(item: CalibrationExample) -> float:
        row = item.features.model_vector()
        return intercept + math.fsum(
            weights[i] * ((row[i] - center[i]) / scale[i]) for i in range(len(row))
        )

    dev_owner = [score(item) for item in records if item.split == DatasetSplit.DEV and item.label == CalibrationOutcome.OWNER]
    dev_nonowner = [score(item) for item in records if item.split == DatasetSplit.DEV and item.label == CalibrationOutcome.NONOWNER]
    candidates = sorted(set(dev_owner + dev_nonowner))
    lower_candidates = [
        value
        for value in candidates
        if sum(score_value <= value for score_value in dev_owner) / len(dev_owner) <= max_false_nonowner
    ]
    upper_candidates = [
        value
        for value in candidates
        if sum(score_value >= value for score_value in dev_nonowner) / len(dev_nonowner) <= max_false_owner
    ]
    if not lower_candidates or not upper_candidates:
        raise CalibrationError("development set cannot satisfy the requested error-rate limits")
    nonowner_boundary, owner_boundary = max(lower_candidates), min(upper_candidates)
    if not nonowner_boundary < owner_boundary:
        raise CalibrationError("development constraints do not define ordered decision boundaries")
    counts = tuple(len({item.group_id for item in records if item.split == split}) for split in DatasetSplit)
    return CalibrationPackage(
        schema_version=CALIBRATION_SCHEMA_VERSION,
        package_revision=package_revision,
        protocol=records[0].protocol,
        feature_order=CALIBRATION_MODEL_FEATURE_ORDER,
        center=center,
        scale=scale,
        weights=weights,
        intercept=intercept,
        nonowner_boundary=nonowner_boundary,
        owner_boundary=owner_boundary,
        dataset_digest=_dataset_digest(records),
        split_group_counts=counts,
        fit_method="balanced_logistic_v1",
        release_status="candidate",
    )


def fit_linear_calibration(
    examples: Iterable[CalibrationExample],
    *,
    package_revision: str,
    max_nonowner_as_owner_rate: float,
    max_owner_as_nonowner_rate: float,
) -> CalibrationPackage:
    try:
        return _fit_linear_calibration(
            examples,
            package_revision=package_revision,
            max_nonowner_as_owner_rate=max_nonowner_as_owner_rate,
            max_owner_as_nonowner_rate=max_owner_as_nonowner_rate,
        )
    except CalibrationError:
        raise
    except ArithmeticError:
        raise CalibrationError("calibration numeric operation failed") from None


def evaluate_calibration(
    package: CalibrationPackage, examples: Iterable[CalibrationExample]
) -> dict[str, dict[str, int | float]]:
    result: dict[str, dict[str, int | float]] = {
        split.value: {
            "tp": 0, "fn": 0, "fp": 0, "tn": 0,
            "owner_uncertain": 0, "nonowner_uncertain": 0,
            "unsupported": 0, "failure": 0, "total": 0, "decided": 0, "coverage": 0.0,
        }
        for split in DatasetSplit
    }
    for item in examples:
        outcome = package.classify(item.features, item.protocol).outcome
        bucket = result[item.split.value]
        bucket["total"] += 1
        if outcome == CalibrationOutcome.OWNER:
            bucket["tp" if item.label == CalibrationOutcome.OWNER else "fp"] += 1
            bucket["decided"] += 1
        elif outcome == CalibrationOutcome.NONOWNER:
            bucket["fn" if item.label == CalibrationOutcome.OWNER else "tn"] += 1
            bucket["decided"] += 1
        elif outcome == CalibrationOutcome.UNCERTAIN:
            bucket["owner_uncertain" if item.label == CalibrationOutcome.OWNER else "nonowner_uncertain"] += 1
        else:
            bucket[outcome.value] += 1
    for bucket in result.values():
        bucket["coverage"] = bucket["decided"] / bucket["total"] if bucket["total"] else 0.0
    return result
