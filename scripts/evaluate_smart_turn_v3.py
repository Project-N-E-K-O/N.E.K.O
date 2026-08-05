"""Audit Smart Turn v3 against authorized, local human-voice WAV evidence.

The evaluator is intentionally scoped to direct model-checkpoint replay.  It
does not exercise Electron capture, VAD candidate timing, coordinator retries,
provider commits, or ASR network behavior, so it can never grant product-level
quality approval by itself.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import io
import json
import math
import platform
import re
import statistics
import subprocess
import sys
import time
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import NormalDist
from typing import Any, Mapping, Protocol, Sequence

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from main_logic.asr_client.endpointing.smart_turn_v3 import SmartTurnV3  # noqa: E402
from main_logic.asr_client.endpointing.config import SmartTurnConfig  # noqa: E402


SUPPORTED_LANGUAGES = frozenset({"zh-CN", "en-US", "ja-JP"})
SUPPORTED_SCENARIOS = frozenset(
    {
        "terminal_end",
        "sentence_internal_pause",
        "hesitation_continue",
        "long_pause_continue",
        "keyboard_noise",
        "barge_in",
    }
)
SUPPORTED_SPLITS = frozenset({"calibration", "holdout"})
SUPPORTED_CAPTURE_ROUTES = frozenset({"dummy", "glm", "gemini"})
MAX_AUDIO_FRAMES = SmartTurnConfig().max_audio_seconds * 16_000
MANIFEST_KEYS = frozenset({"schema_version", "dataset_id", "cases"})
CASE_KEYS = frozenset(
    {
        "id",
        "path",
        "audio_sha256",
        "expected",
        "language",
        "scenario",
        "source_group",
        "speaker_id",
        "session_id",
        "device_id",
        "capture_surface",
        "capture_route_context",
        "split",
        "pause_ms",
    }
)
CRITERIA_KEYS = frozenset(
    {
        "schema_version",
        "criteria_id",
        "manifest_sha256",
        "decision_threshold",
        "confidence_level",
        "confidence_method",
        "holdout",
    }
)
LANGUAGE_CRITERIA_KEYS = frozenset(
    {
        "speaker_count",
        "speaker_roster_sha256",
        "min_devices",
        "speaker_scenario_matrix",
        "max_speakers_with_any_premature_split",
        "max_speakers_with_any_missed_endpoint",
        "max_speaker_premature_split_rate_upper_bound",
        "max_speaker_missed_endpoint_rate_upper_bound",
    }
)
SCENARIO_LABEL_KEYS = frozenset({"complete", "incomplete"})


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    path: Path
    expected_complete: bool
    id: str
    language: str
    scenario: str
    source_group: str | None
    speaker_id: str | None
    session_id: str | None
    device_id: str | None
    capture_surface: str
    capture_route_context: str
    split: str
    pause_ms: int
    audio_sha256: str | None
    pcm_sha256: str | None
    duration_ms: int


@dataclass(frozen=True, slots=True)
class EvaluationManifest:
    path: Path | None
    dataset_id: str
    cases: tuple[EvaluationCase, ...]
    sha256: str


@dataclass(frozen=True, slots=True)
class LanguageAcceptanceCriteria:
    speaker_count: int
    speaker_roster_sha256: str
    min_devices: int
    speaker_scenario_matrix: Mapping[str, Mapping[str, int]]
    max_speakers_with_any_premature_split: int
    max_speakers_with_any_missed_endpoint: int
    max_speaker_premature_split_rate_upper_bound: float
    max_speaker_missed_endpoint_rate_upper_bound: float


@dataclass(frozen=True, slots=True)
class AcceptanceCriteria:
    path: Path
    criteria_id: str
    manifest_sha256: str
    decision_threshold: float
    confidence_level: float
    confidence_method: str
    languages: Mapping[str, LanguageAcceptanceCriteria]
    sha256: str


@dataclass(slots=True)
class ConfusionMatrix:
    true_complete: int = 0
    false_incomplete: int = 0
    false_complete: int = 0
    true_incomplete: int = 0

    def add(self, *, expected: bool, predicted: bool) -> None:
        if expected and predicted:
            self.true_complete += 1
        elif expected:
            self.false_incomplete += 1
        elif predicted:
            self.false_complete += 1
        else:
            self.true_incomplete += 1


class Predictor(Protocol):
    def predict_probability(self, audio: np.ndarray) -> float: ...


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def _load_json_object_snapshot(
    path: Path, *, description: str
) -> tuple[dict[str, Any], str]:
    try:
        encoded = path.read_bytes()
        value = json.loads(
            encoded.decode("utf-8"), parse_constant=_reject_json_constant
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read {description}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{description} must be a JSON object")
    return value, hashlib.sha256(encoded).hexdigest()


def _load_json_object(path: Path, *, description: str) -> dict[str, Any]:
    value, _sha256_digest = _load_json_object_snapshot(path, description=description)
    return value


def _require_exact_keys(
    value: Mapping[str, Any], expected: frozenset[str], *, context: str
) -> None:
    keys = set(value)
    missing = sorted(expected - keys)
    unknown = sorted(keys - expected)
    if missing:
        raise ValueError(f"{context}: missing required fields: {', '.join(missing)}")
    if unknown:
        raise ValueError(
            f"{context}: unknown fields are not allowed: {', '.join(unknown)}"
        )


def _anonymous_id(value: Any, *, prefix: str, context: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        rf"{re.escape(prefix)}-[0-9a-f]{{32}}", value
    ):
        raise ValueError(
            f"{context} must be an opaque {prefix}-prefixed 128-bit hex identifier"
        )
    return value


def _declared_sha256(value: Any, *, context: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{context} must be a lowercase SHA-256 digest")
    return value


def _finite_rate(value: Any, *, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context} must be a finite number within [0, 1]")
    rendered = float(value)
    if not math.isfinite(rendered) or not 0.0 <= rendered <= 1.0:
        raise ValueError(f"{context} must be a finite number within [0, 1]")
    return rendered


def _non_negative_int(value: Any, *, context: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{context} must be an integer >= {minimum}")
    return value


def _read_wav_contract(
    path: Path, *, enforce_max_duration: bool = True
) -> tuple[np.ndarray, int, str, str]:
    try:
        encoded = path.read_bytes()
        with wave.open(io.BytesIO(encoded), "rb") as wav_file:
            if wav_file.getnchannels() != 1:
                raise ValueError(f"{path}: expected mono WAV")
            if wav_file.getframerate() != 16_000:
                raise ValueError(f"{path}: expected 16000 Hz WAV")
            if wav_file.getsampwidth() != 2:
                raise ValueError(f"{path}: expected signed PCM16 WAV")
            frames = wav_file.getnframes()
            pcm = wav_file.readframes(frames)
    except (OSError, wave.Error, EOFError) as exc:
        raise ValueError(f"{path}: unreadable WAV") from exc
    if frames <= 0:
        raise ValueError(f"{path}: WAV must contain audio")
    if enforce_max_duration and frames > MAX_AUDIO_FRAMES:
        raise ValueError(f"{path}: WAV exceeds the SmartTurn 8 second input window")
    if len(pcm) != frames * 2:
        raise ValueError(f"{path}: truncated PCM frames")
    duration_ms = frames * 1_000 // 16_000
    audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    return (
        audio,
        duration_ms,
        hashlib.sha256(encoded).hexdigest(),
        hashlib.sha256(pcm).hexdigest(),
    )


def _inspect_wav_contract(
    path: Path, *, enforce_max_duration: bool = True
) -> tuple[int, str]:
    _audio, duration_ms, _audio_sha256, pcm_sha256 = _read_wav_contract(
        path, enforce_max_duration=enforce_max_duration
    )
    return duration_ms, pcm_sha256


def _resolve_audio_path(root: Path, value: Any, *, context: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context}: path must be a non-empty relative string")
    relative = Path(value)
    if relative.is_absolute():
        raise ValueError(f"{context}: path must be relative to the manifest")
    try:
        resolved = (root / relative).resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{context}: audio path does not exist") from exc
    if root != resolved and root not in resolved.parents:
        raise ValueError(f"{context}: audio path escapes the manifest directory")
    if not resolved.is_file():
        raise ValueError(f"{context}: audio path must identify a file")
    if resolved.suffix.lower() != ".wav":
        raise ValueError(f"{context}: audio path must use the .wav extension")
    return resolved


def load_manifest(manifest_path: Path) -> EvaluationManifest:
    """Load and validate a privacy-minimizing SmartTurn human-data manifest."""

    manifest_path = manifest_path.resolve()
    value, manifest_sha256 = _load_json_object_snapshot(
        manifest_path, description="manifest"
    )
    _require_exact_keys(value, MANIFEST_KEYS, context="manifest")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("manifest schema_version must be 1")
    dataset_id = _anonymous_id(
        value["dataset_id"], prefix="dataset", context="manifest dataset_id"
    )
    raw_cases = value["cases"]
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("manifest cases must be a non-empty list")

    root = manifest_path.parent.resolve()
    cases: list[EvaluationCase] = []
    ids: set[str] = set()
    paths: set[Path] = set()
    pcm_digests: set[str] = set()
    for index, raw_case in enumerate(raw_cases, 1):
        context = f"manifest case {index}"
        if not isinstance(raw_case, dict):
            raise ValueError(f"{context} must be a JSON object")
        _require_exact_keys(raw_case, CASE_KEYS, context=context)
        case_id = _anonymous_id(raw_case["id"], prefix="case", context=f"{context} id")
        if case_id in ids:
            raise ValueError(f"{context}: case ids must be unique")
        ids.add(case_id)
        path = _resolve_audio_path(root, raw_case["path"], context=context)
        if path in paths:
            raise ValueError(f"{context}: audio paths must be unique")
        paths.add(path)
        declared_audio_sha256 = _declared_sha256(
            raw_case["audio_sha256"], context=f"{context} audio_sha256"
        )
        _audio, duration_ms, actual_audio_sha256, pcm_sha256 = _read_wav_contract(path)
        if declared_audio_sha256 != actual_audio_sha256:
            raise ValueError(f"{context}: audio_sha256 does not match the WAV file")
        if pcm_sha256 in pcm_digests:
            raise ValueError(f"{context}: duplicate PCM content is not allowed")
        pcm_digests.add(pcm_sha256)

        expected = raw_case["expected"]
        if not isinstance(expected, str) or expected not in {"complete", "incomplete"}:
            raise ValueError(f"{context}: expected must be complete or incomplete")
        language = raw_case["language"]
        if not isinstance(language, str) or language not in SUPPORTED_LANGUAGES:
            raise ValueError(
                f"{context}: language must be one of {', '.join(sorted(SUPPORTED_LANGUAGES))}"
            )
        scenario = raw_case["scenario"]
        if not isinstance(scenario, str) or scenario not in SUPPORTED_SCENARIOS:
            raise ValueError(f"{context}: scenario is unsupported")
        if scenario == "terminal_end" and expected != "complete":
            raise ValueError(f"{context}: terminal_end must be labelled complete")
        if (
            scenario
            in {
                "sentence_internal_pause",
                "hesitation_continue",
                "long_pause_continue",
            }
            and expected != "incomplete"
        ):
            raise ValueError(
                f"{context}: continuation scenario must be labelled incomplete"
            )
        if raw_case["capture_surface"] != "electron":
            raise ValueError(f"{context}: capture_surface must be electron")
        capture_route_context = raw_case["capture_route_context"]
        if (
            not isinstance(capture_route_context, str)
            or capture_route_context not in SUPPORTED_CAPTURE_ROUTES
        ):
            raise ValueError(f"{context}: capture_route_context is unsupported")
        split = raw_case["split"]
        if not isinstance(split, str) or split not in SUPPORTED_SPLITS:
            raise ValueError(f"{context}: split must be calibration or holdout")
        pause_ms = _non_negative_int(
            raw_case["pause_ms"], context=f"{context} pause_ms"
        )
        runtime_defaults = SmartTurnConfig()
        if pause_ms < runtime_defaults.candidate_silence_ms:
            raise ValueError(
                f"{context}: pause_ms must reach the production candidate silence"
            )
        if duration_ms < pause_ms + runtime_defaults.minimum_speech_ms:
            raise ValueError(
                f"{context}: WAV duration must cover pause_ms plus minimum speech"
            )

        cases.append(
            EvaluationCase(
                id=case_id,
                path=path,
                expected_complete=expected == "complete",
                language=language,
                scenario=scenario,
                source_group=_anonymous_id(
                    raw_case["source_group"],
                    prefix="source",
                    context=f"{context} source_group",
                ),
                speaker_id=_anonymous_id(
                    raw_case["speaker_id"],
                    prefix="speaker",
                    context=f"{context} speaker_id",
                ),
                session_id=_anonymous_id(
                    raw_case["session_id"],
                    prefix="session",
                    context=f"{context} session_id",
                ),
                device_id=_anonymous_id(
                    raw_case["device_id"],
                    prefix="device",
                    context=f"{context} device_id",
                ),
                capture_surface="electron",
                capture_route_context=capture_route_context,
                split=split,
                pause_ms=pause_ms,
                audio_sha256=declared_audio_sha256,
                pcm_sha256=pcm_sha256,
                duration_ms=duration_ms,
            )
        )

    for field in ("speaker_id", "source_group"):
        splits_by_identity: dict[str, set[str]] = {}
        for case in cases:
            splits_by_identity.setdefault(getattr(case, field), set()).add(case.split)
        if any(len(splits) > 1 for splits in splits_by_identity.values()):
            raise ValueError(
                f"manifest {field} values must not cross calibration/holdout split boundaries"
            )

    source_metadata: dict[str, tuple[str, str, str, str, str]] = {}
    for case in cases:
        metadata = (
            case.speaker_id,
            case.session_id,
            case.device_id,
            case.language,
            case.capture_route_context,
        )
        prior = source_metadata.setdefault(case.source_group, metadata)
        if prior != metadata:
            raise ValueError("manifest source_group metadata must remain consistent")

    return EvaluationManifest(
        path=manifest_path,
        dataset_id=dataset_id,
        cases=tuple(cases),
        sha256=manifest_sha256,
    )


def load_acceptance_criteria(criteria_path: Path) -> AcceptanceCriteria:
    """Load pre-registered checkpoint criteria without inventing thresholds."""

    criteria_path = criteria_path.resolve()
    value, criteria_sha256 = _load_json_object_snapshot(
        criteria_path, description="acceptance criteria"
    )
    _require_exact_keys(value, CRITERIA_KEYS, context="acceptance criteria")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("acceptance criteria schema_version must be 1")
    criteria_id = _anonymous_id(
        value["criteria_id"], prefix="criteria", context="criteria_id"
    )
    manifest_sha256 = _declared_sha256(
        value["manifest_sha256"], context="criteria manifest_sha256"
    )
    threshold = _finite_rate(value["decision_threshold"], context="decision_threshold")
    production_threshold = SmartTurnConfig().evaluation_threshold
    if not math.isclose(threshold, production_threshold, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError(
            "acceptance criteria decision_threshold must match the deployed production threshold"
        )
    confidence_level = _finite_rate(
        value["confidence_level"], context="confidence_level"
    )
    if not 0.5 < confidence_level < 1.0:
        raise ValueError("confidence_level must be greater than 0.5 and less than 1")
    if value["confidence_method"] != "wilson":
        raise ValueError("confidence_method must be wilson")
    holdout = value["holdout"]
    if not isinstance(holdout, dict):
        raise ValueError("criteria holdout must be a JSON object")
    _require_exact_keys(holdout, frozenset({"languages"}), context="criteria holdout")
    languages = holdout["languages"]
    if not isinstance(languages, dict) or set(languages) != SUPPORTED_LANGUAGES:
        raise ValueError("criteria must pre-register zh-CN, en-US, and ja-JP")

    parsed_languages: dict[str, LanguageAcceptanceCriteria] = {}
    for language in sorted(SUPPORTED_LANGUAGES):
        raw = languages[language]
        if not isinstance(raw, dict):
            raise ValueError(f"criteria {language} must be a JSON object")
        _require_exact_keys(raw, LANGUAGE_CRITERIA_KEYS, context=f"criteria {language}")
        raw_matrix = raw["speaker_scenario_matrix"]
        if not isinstance(raw_matrix, dict) or set(raw_matrix) != SUPPORTED_SCENARIOS:
            raise ValueError(
                f"criteria {language} speaker_scenario_matrix must cover every scenario"
            )
        scenario_matrix: dict[str, dict[str, int]] = {}
        for scenario in sorted(SUPPORTED_SCENARIOS):
            label_counts = raw_matrix[scenario]
            if not isinstance(label_counts, dict):
                raise ValueError(
                    f"criteria {language} {scenario} matrix must be a JSON object"
                )
            _require_exact_keys(
                label_counts,
                SCENARIO_LABEL_KEYS,
                context=f"criteria {language} {scenario}",
            )
            scenario_matrix[scenario] = {
                label: _non_negative_int(
                    label_counts[label],
                    context=f"criteria {language} {scenario} {label}",
                )
                for label in sorted(SCENARIO_LABEL_KEYS)
            }
        if (
            scenario_matrix["terminal_end"]["complete"] < 1
            or scenario_matrix["terminal_end"]["incomplete"] != 0
        ):
            raise ValueError(
                f"criteria {language} terminal_end needs complete-only evidence"
            )
        for scenario in (
            "sentence_internal_pause",
            "hesitation_continue",
            "long_pause_continue",
        ):
            if (
                scenario_matrix[scenario]["complete"] != 0
                or scenario_matrix[scenario]["incomplete"] < 1
            ):
                raise ValueError(
                    f"criteria {language} {scenario} needs incomplete-only evidence"
                )
        for scenario in ("keyboard_noise", "barge_in"):
            if any(
                scenario_matrix[scenario][label] < 1 for label in SCENARIO_LABEL_KEYS
            ):
                raise ValueError(
                    f"criteria {language} {scenario} needs complete and incomplete evidence"
                )
        parsed_languages[language] = LanguageAcceptanceCriteria(
            speaker_count=_non_negative_int(
                raw["speaker_count"],
                context=f"criteria {language} speaker_count",
                minimum=1,
            ),
            speaker_roster_sha256=_declared_sha256(
                raw["speaker_roster_sha256"],
                context=f"criteria {language} speaker_roster_sha256",
            ),
            min_devices=_non_negative_int(
                raw["min_devices"],
                context=f"criteria {language} min_devices",
                minimum=1,
            ),
            speaker_scenario_matrix=scenario_matrix,
            max_speakers_with_any_premature_split=_non_negative_int(
                raw["max_speakers_with_any_premature_split"],
                context=(f"criteria {language} max_speakers_with_any_premature_split"),
            ),
            max_speakers_with_any_missed_endpoint=_non_negative_int(
                raw["max_speakers_with_any_missed_endpoint"],
                context=f"criteria {language} max_speakers_with_any_missed_endpoint",
            ),
            max_speaker_premature_split_rate_upper_bound=_finite_rate(
                raw["max_speaker_premature_split_rate_upper_bound"],
                context=(
                    f"criteria {language} max_speaker_premature_split_rate_upper_bound"
                ),
            ),
            max_speaker_missed_endpoint_rate_upper_bound=_finite_rate(
                raw["max_speaker_missed_endpoint_rate_upper_bound"],
                context=(
                    f"criteria {language} max_speaker_missed_endpoint_rate_upper_bound"
                ),
            ),
        )

    return AcceptanceCriteria(
        path=criteria_path,
        criteria_id=criteria_id,
        manifest_sha256=manifest_sha256,
        decision_threshold=threshold,
        confidence_level=confidence_level,
        confidence_method="wilson",
        languages=parsed_languages,
        sha256=criteria_sha256,
    )


def load_cases(fixture_dir: Path, labels_path: Path) -> list[EvaluationCase]:
    """Load the legacy JSONL format for compatibility; reports stay exploratory."""

    root = fixture_dir.resolve()
    cases: list[EvaluationCase] = []
    for line_number, line in enumerate(
        labels_path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        value = json.loads(line, parse_constant=_reject_json_constant)
        relative = Path(value["path"])
        if relative.is_absolute():
            raise ValueError(f"line {line_number}: fixture path must be relative")
        try:
            path = (root / relative).resolve(strict=True)
        except OSError as exc:
            raise ValueError(
                f"line {line_number}: fixture path does not exist"
            ) from exc
        if root != path and root not in path.parents:
            raise ValueError(
                f"line {line_number}: fixture path escapes fixture directory"
            )
        expected = value.get("expected")
        if expected not in ("complete", "incomplete"):
            raise ValueError(
                f"line {line_number}: expected must be complete or incomplete"
            )
        duration_ms, pcm_sha256 = _inspect_wav_contract(
            path, enforce_max_duration=False
        )
        cases.append(
            EvaluationCase(
                id=f"case-legacy-{line_number:04d}",
                path=path,
                expected_complete=expected == "complete",
                language=str(value.get("language") or "legacy-unknown"),
                scenario=str(value.get("category") or "legacy-unclassified"),
                source_group=None,
                speaker_id=None,
                session_id=None,
                device_id=None,
                capture_surface="legacy-unknown",
                capture_route_context="legacy-unknown",
                split="holdout",
                pause_ms=0,
                audio_sha256=None,
                pcm_sha256=pcm_sha256,
                duration_ms=duration_ms,
            )
        )
    if not cases:
        raise ValueError("labels file contains no evaluation cases")
    return cases


def read_pcm16_wav(path: Path, *, enforce_max_duration: bool = True) -> np.ndarray:
    audio, _duration_ms, _audio_sha256, _pcm_sha256 = _read_wav_contract(
        path, enforce_max_duration=enforce_max_duration
    )
    return audio


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _wilson_interval(
    errors: int, total: int, confidence_level: float
) -> dict[str, float] | None:
    if total == 0:
        return None
    z = NormalDist().inv_cdf(0.5 + confidence_level / 2.0)
    observed = errors / total
    denominator = 1.0 + (z * z / total)
    center = (observed + z * z / (2.0 * total)) / denominator
    margin = (
        z
        * math.sqrt(observed * (1.0 - observed) / total + z * z / (4.0 * total * total))
        / denominator
    )
    return {
        "confidence_level": confidence_level,
        "lower": max(0.0, center - margin),
        "upper": min(1.0, center + margin),
    }


def _summarize_results(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    matrix = ConfusionMatrix()
    for record in records:
        matrix.add(
            expected=record["expected_complete"], predicted=record["predicted_complete"]
        )
    cells = asdict(matrix)
    complete_total = matrix.true_complete + matrix.false_incomplete
    incomplete_total = matrix.true_incomplete + matrix.false_complete
    complete_recall = _ratio(matrix.true_complete, complete_total)
    continuation_specificity = _ratio(matrix.true_incomplete, incomplete_total)
    premature_split_rate = _ratio(matrix.false_complete, incomplete_total)
    missed_endpoint_rate = _ratio(matrix.false_incomplete, complete_total)
    balanced_accuracy = (
        (complete_recall + continuation_specificity) / 2.0
        if complete_recall is not None and continuation_specificity is not None
        else None
    )
    return {
        **cells,
        "case_count": len(records),
        "confusion_matrix": cells,
        "descriptive_case_complete_recall": complete_recall,
        "descriptive_case_continuation_specificity": continuation_specificity,
        "descriptive_case_premature_split_rate": premature_split_rate,
        "descriptive_case_missed_endpoint_rate": missed_endpoint_rate,
        "descriptive_case_balanced_accuracy": balanced_accuracy,
    }


def _group_metrics(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    def by(field_names: tuple[str, ...]) -> dict[str, Any]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            key = "|".join(str(record[field]) for field in field_names)
            grouped.setdefault(key, []).append(record)
        return {
            key: _summarize_results(items) for key, items in sorted(grouped.items())
        }

    return {
        "overall": _summarize_results(records),
        "by_language": by(("language",)),
        "by_scenario": by(("scenario",)),
        "by_language_scenario": by(("language", "scenario")),
        # Informational only: the direct GLM/Gemini checkpoint is identical.
        "by_language_capture_route_context": by(("language", "capture_route_context")),
    }


def _dataset_summary(manifest: EvaluationManifest) -> dict[str, Any]:
    cases = manifest.cases

    def counts(field: str) -> dict[str, int]:
        values: dict[str, int] = {}
        for case in cases:
            key = str(getattr(case, field))
            values[key] = values.get(key, 0) + 1
        return dict(sorted(values.items()))

    def unique_count(selected: Sequence[EvaluationCase], field: str) -> int | None:
        values = {
            value for case in selected if (value := getattr(case, field)) is not None
        }
        return len(values) if values else None

    by_language: dict[str, dict[str, int | None]] = {}
    for language in sorted({case.language for case in cases}):
        selected = [case for case in cases if case.language == language]
        by_language[language] = {
            "case_count": len(selected),
            "complete": sum(case.expected_complete for case in selected),
            "incomplete": sum(not case.expected_complete for case in selected),
            "speaker_count": unique_count(selected, "speaker_id"),
            "session_count": unique_count(selected, "session_id"),
            "device_count": unique_count(selected, "device_id"),
            "source_group_count": unique_count(selected, "source_group"),
        }
    pcm_digests = sorted(
        case.pcm_sha256 for case in cases if case.pcm_sha256 is not None
    )
    corpus_digest = (
        hashlib.sha256(
            json.dumps(pcm_digests, separators=(",", ":")).encode("ascii")
        ).hexdigest()
        if pcm_digests
        else None
    )
    return {
        "dataset_id": manifest.dataset_id,
        "case_count": len(cases),
        "audio_corpus_sha256": corpus_digest,
        "split_counts": counts("split"),
        "language_counts": counts("language"),
        "scenario_counts": counts("scenario"),
        "capture_route_context_counts": counts("capture_route_context"),
        "speaker_count": unique_count(cases, "speaker_id"),
        "session_count": unique_count(cases, "session_id"),
        "device_count": unique_count(cases, "device_id"),
        "source_group_count": unique_count(cases, "source_group"),
        "by_language": by_language,
    }


def _gate_check(
    checks: list[dict[str, Any]],
    *,
    language: str,
    name: str,
    observed: Any,
    required: Any,
    passed: bool,
    insufficient_evidence: bool = False,
) -> None:
    checks.append(
        {
            "language": language,
            "check": name,
            "observed": observed,
            "required": required,
            "status": "pass"
            if passed
            else ("blocked" if insufficient_evidence else "fail"),
        }
    )


def _opaque_roster_sha256(values: Sequence[str]) -> str:
    encoded = json.dumps(sorted(values), separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _evidence_digest_summary(manifest: EvaluationManifest) -> dict[str, Any]:
    """Return canonical manifest and holdout-speaker digests for preregistration."""

    languages: dict[str, dict[str, Any]] = {}
    for language in sorted(SUPPORTED_LANGUAGES):
        speaker_ids = {
            case.speaker_id
            for case in manifest.cases
            if case.split == "holdout"
            and case.language == language
            and case.speaker_id is not None
        }
        languages[language] = {
            "speaker_count": len(speaker_ids),
            "speaker_roster_sha256": _opaque_roster_sha256(list(speaker_ids)),
        }
    return {
        "schema_version": 1,
        "manifest_sha256": manifest.sha256,
        "languages": languages,
    }


def _build_checkpoint_gate(
    records: Sequence[dict[str, Any]],
    criteria: AcceptanceCriteria | None,
    manifest_sha256: str,
) -> dict[str, Any]:
    if criteria is None:
        return {"scope": "model_checkpoint", "outcome": "exploratory", "checks": []}

    checks: list[dict[str, Any]] = []
    speaker_metrics: dict[str, dict[str, Any]] = {}
    manifest_matches = manifest_sha256 == criteria.manifest_sha256
    _gate_check(
        checks,
        language="all",
        name="manifest_binding",
        observed=manifest_matches,
        required="match the manifest digest frozen in the acceptance criteria",
        passed=manifest_matches,
        insufficient_evidence=True,
    )
    holdout = [record for record in records if record["case"].split == "holdout"]
    for language in sorted(SUPPORTED_LANGUAGES):
        requirement = criteria.languages[language]
        selected = [record for record in holdout if record["case"].language == language]
        records_by_speaker: dict[str, list[dict[str, Any]]] = {}
        for record in selected:
            speaker_id = record["case"].speaker_id
            if speaker_id is not None:
                records_by_speaker.setdefault(speaker_id, []).append(record)

        compliant_speakers: list[list[dict[str, Any]]] = []
        for speaker_records in records_by_speaker.values():
            observed_matrix = {
                scenario: {"complete": 0, "incomplete": 0}
                for scenario in SUPPORTED_SCENARIOS
            }
            for record in speaker_records:
                label = "complete" if record["expected_complete"] else "incomplete"
                observed_matrix[record["scenario"]][label] += 1
            if observed_matrix == requirement.speaker_scenario_matrix:
                compliant_speakers.append(speaker_records)

        speaker_count = len(records_by_speaker)
        compliant_count = len(compliant_speakers)
        roster_matches = (
            _opaque_roster_sha256(list(records_by_speaker))
            == requirement.speaker_roster_sha256
        )
        device_count = len(
            {
                record["case"].device_id
                for record in selected
                if record["case"].device_id is not None
            }
        )
        evidence_checks = (
            ("speaker_count", speaker_count, requirement.speaker_count),
            ("min_devices", device_count, requirement.min_devices),
        )
        for name, observed, required in evidence_checks:
            _gate_check(
                checks,
                language=language,
                name=name,
                observed=observed,
                required=required,
                passed=(
                    observed == required
                    if name == "speaker_count"
                    else observed >= required
                ),
                insufficient_evidence=True,
            )
        _gate_check(
            checks,
            language=language,
            name="speaker_roster_compliance",
            observed=roster_matches,
            required="match the pre-registered opaque speaker roster digest",
            passed=roster_matches,
            insufficient_evidence=True,
        )
        _gate_check(
            checks,
            language=language,
            name="speaker_matrix_compliance",
            observed={"compliant_speakers": compliant_count, "speakers": speaker_count},
            required="every speaker exactly matches the pre-registered scenario/label matrix",
            passed=speaker_count > 0 and compliant_count == speaker_count,
            insufficient_evidence=True,
        )

        speakers_with_premature_split = sum(
            any(
                not record["expected_complete"] and record["predicted_complete"]
                for record in speaker_records
            )
            for speaker_records in compliant_speakers
        )
        speakers_with_missed_endpoint = sum(
            any(
                record["expected_complete"] and not record["predicted_complete"]
                for record in speaker_records
            )
            for speaker_records in compliant_speakers
        )
        premature_rate = _ratio(speakers_with_premature_split, compliant_count)
        missed_rate = _ratio(speakers_with_missed_endpoint, compliant_count)
        premature_interval = _wilson_interval(
            speakers_with_premature_split, compliant_count, criteria.confidence_level
        )
        missed_interval = _wilson_interval(
            speakers_with_missed_endpoint, compliant_count, criteria.confidence_level
        )
        speaker_metrics[language] = {
            "speaker_count": speaker_count,
            "matrix_compliant_speaker_count": compliant_count,
            "matrix_noncompliant_speaker_count": speaker_count - compliant_count,
            "device_count": device_count,
            "speaker_roster_compliant": roster_matches,
            "speakers_with_any_premature_split": speakers_with_premature_split,
            "speakers_with_any_missed_endpoint": speakers_with_missed_endpoint,
            "speaker_premature_split_rate": premature_rate,
            "speaker_premature_split_rate_interval": premature_interval,
            "speaker_missed_endpoint_rate": missed_rate,
            "speaker_missed_endpoint_rate_interval": missed_interval,
        }

        _gate_check(
            checks,
            language=language,
            name="max_speakers_with_any_premature_split",
            observed=speakers_with_premature_split,
            required=requirement.max_speakers_with_any_premature_split,
            passed=(
                compliant_count > 0
                and speakers_with_premature_split
                <= requirement.max_speakers_with_any_premature_split
            ),
            insufficient_evidence=compliant_count == 0,
        )
        _gate_check(
            checks,
            language=language,
            name="max_speakers_with_any_missed_endpoint",
            observed=speakers_with_missed_endpoint,
            required=requirement.max_speakers_with_any_missed_endpoint,
            passed=(
                compliant_count > 0
                and speakers_with_missed_endpoint
                <= requirement.max_speakers_with_any_missed_endpoint
            ),
            insufficient_evidence=compliant_count == 0,
        )
        for metric_name, interval, maximum in (
            (
                "speaker_premature_split_rate_interval",
                premature_interval,
                requirement.max_speaker_premature_split_rate_upper_bound,
            ),
            (
                "speaker_missed_endpoint_rate_interval",
                missed_interval,
                requirement.max_speaker_missed_endpoint_rate_upper_bound,
            ),
        ):
            upper = interval["upper"] if interval is not None else None
            _gate_check(
                checks,
                language=language,
                name=f"max_{metric_name}_upper",
                observed=upper,
                required=maximum,
                passed=upper is not None and upper <= maximum,
                insufficient_evidence=upper is None,
            )

    statuses = {check["status"] for check in checks}
    outcome = (
        "blocked"
        if "blocked" in statuses
        else ("fail" if "fail" in statuses else "pass")
    )
    return {
        "scope": "model_checkpoint",
        "outcome": outcome,
        "criteria_id": criteria.criteria_id,
        "checks": checks,
        "speaker_metrics": speaker_metrics,
    }


def _runtime_provenance(asset_dir: Path) -> dict[str, Any]:
    model: dict[str, Any] = {"filename": "smart_turn_v3.onnx"}
    manifest_path = asset_dir / "manifest.json"
    if manifest_path.is_file():
        manifest = _load_json_object(manifest_path, description="asset manifest")
        for asset in manifest.get("assets", []):
            if asset.get("filename") == "smart_turn_v3.onnx":
                model.update(
                    version=asset.get("version"),
                    sha256=asset.get("sha256"),
                )
                break
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        revision = "unknown"
    try:
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=normal"],
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError):
        dirty = False
    if dirty and revision != "unknown":
        revision = f"{revision}-dirty"
    return {
        "git_revision": revision,
        "evaluator_sha256": _sha256(Path(__file__).resolve()),
        "model": model,
        "runtime": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "numpy": np.__version__,
            "onnxruntime": importlib.metadata.version("onnxruntime"),
        },
    }


def _latency_summary(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    def distribution(values: Sequence[float]) -> dict[str, Any] | None:
        if not values:
            return None
        ordered = sorted(values)

        def percentile(fraction: float) -> float:
            index = round((len(ordered) - 1) * fraction)
            return ordered[index]

        return {
            "case_count": len(ordered),
            "median": statistics.median(ordered),
            "p95": percentile(0.95),
            "p99": percentile(0.99),
        }

    all_values = [record["latency_ms"] for record in records]
    cold_values = [
        record["latency_ms"] for record in records if record["is_cold_start"]
    ]
    warm_values = [
        record["latency_ms"] for record in records if not record["is_cold_start"]
    ]
    overall = distribution(all_values)
    return {
        "case_count": len(records),
        "cold_case_count": len(cold_values),
        "median": overall["median"] if overall is not None else None,
        "p95": overall["p95"] if overall is not None else None,
        "p99": overall["p99"] if overall is not None else None,
        "cold": distribution(cold_values),
        "warm": distribution(warm_values),
    }


def evaluate_manifest(
    manifest: EvaluationManifest,
    predictor: Predictor,
    *,
    threshold: float | None = None,
    criteria: AcceptanceCriteria | None = None,
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate a validated manifest and return a privacy-redacted report."""

    selected_threshold = (
        criteria.decision_threshold
        if threshold is None and criteria is not None
        else (
            SmartTurnConfig().evaluation_threshold if threshold is None else threshold
        )
    )
    selected_threshold = _finite_rate(selected_threshold, context="threshold")
    if criteria is not None and not math.isclose(
        selected_threshold, criteria.decision_threshold, rel_tol=0.0, abs_tol=1e-12
    ):
        raise ValueError("evaluation threshold must match the pre-registered criteria")
    records: list[dict[str, Any]] = []
    public_results: list[dict[str, Any]] = []
    for inference_index, case in enumerate(manifest.cases):
        audio, duration_ms, audio_sha256, pcm_sha256 = _read_wav_contract(
            case.path, enforce_max_duration=manifest.path is not None
        )
        if (
            (case.audio_sha256 is not None and audio_sha256 != case.audio_sha256)
            or pcm_sha256 != case.pcm_sha256
            or duration_ms != case.duration_ms
        ):
            raise ValueError(
                f"case {case.id}: WAV changed after manifest or labels validation"
            )
        started = time.perf_counter()
        raw_probability = predictor.predict_probability(audio)
        latency_ms = (time.perf_counter() - started) * 1000.0
        try:
            probability = float(raw_probability)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"case {case.id}: predictor returned an invalid probability"
            ) from exc
        if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise ValueError(
                f"case {case.id}: predictor returned an invalid probability"
            )
        predicted_complete = probability >= selected_threshold
        record = {
            "case": case,
            "language": case.language,
            "scenario": case.scenario,
            "capture_route_context": case.capture_route_context,
            "expected_complete": case.expected_complete,
            "predicted_complete": predicted_complete,
            "latency_ms": latency_ms,
            "is_cold_start": inference_index == 0,
        }
        records.append(record)
        public_results.append(
            {
                "id": case.id,
                "language": case.language,
                "scenario": case.scenario,
                "capture_surface": case.capture_surface,
                "capture_route_context": case.capture_route_context,
                "split": case.split,
                "pause_ms": case.pause_ms,
                "duration_ms": case.duration_ms,
                "expected": "complete" if case.expected_complete else "incomplete",
                "predicted": "complete" if predicted_complete else "incomplete",
                "probability": probability,
                "latency_ms": latency_ms,
                "is_cold_start": inference_index == 0,
            }
        )

    metrics = {
        split: _group_metrics(
            [record for record in records if record["case"].split == split]
        )
        for split in ("calibration", "holdout")
    }
    latency = {
        split: _latency_summary(
            [record for record in records if record["case"].split == split]
        )
        for split in ("calibration", "holdout")
    }

    report_provenance = dict(provenance or {})
    report_provenance.setdefault("evaluator_sha256", _sha256(Path(__file__).resolve()))
    report_provenance["manifest_sha256"] = manifest.sha256
    report_provenance["criteria_sha256"] = criteria.sha256 if criteria else None
    gate = _build_checkpoint_gate(records, criteria, manifest.sha256)
    approval_reasons = ["electron_live_route_evidence_missing"]
    if gate["outcome"] != "pass":
        approval_reasons.insert(0, f"model_checkpoint_gate_{gate['outcome']}")
    holdout_overall = metrics["holdout"]["overall"]
    return {
        "schema_version": 1,
        "scope": "smart_turn_model_checkpoint_replay",
        "evidence_limitations": [
            "does_not_exercise_electron_live_capture",
            "does_not_exercise_vad_candidate_timing_or_coordinator",
            "does_not_exercise_provider_commit_or_asr_network",
            "capture_route_context_is_informational_not_an_independent_model_route",
        ],
        "threshold": selected_threshold,
        "dataset": _dataset_summary(manifest),
        "metrics": metrics,
        # Compatibility summary: holdout only, never calibration-contaminated.
        "case_count": holdout_overall["case_count"],
        "confusion_matrix": holdout_overall["confusion_matrix"],
        "latency_ms": latency,
        "gate": gate,
        "product_quality_approval": {
            "status": "blocked",
            "reasons": approval_reasons,
        },
        "provenance": report_provenance,
        "results": public_results,
    }


def evaluate_cases(
    cases: list[EvaluationCase], predictor: Predictor, *, threshold: float = 0.5
) -> dict[str, Any]:
    """Compatibility wrapper for the legacy, unversioned JSONL input."""

    report = evaluate_manifest(
        EvaluationManifest(
            path=None,
            dataset_id="dataset-legacy-unversioned",
            cases=tuple(cases),
            sha256="legacy-unversioned",
        ),
        predictor,
        threshold=threshold,
    )
    report["scope"] = "smart_turn_legacy_unversioned_checkpoint_replay"
    return report


def render_markdown_report(report: Mapping[str, Any]) -> str:
    gate = report["gate"]
    lines = [
        "# SmartTurn v3 human-voice checkpoint report",
        "",
        f"- Checkpoint gate: **{str(gate['outcome']).upper()}**",
        "- Product quality approval: **BLOCKED** (live Electron route evidence is required)",
        f"- Decision threshold: `{report['threshold']}`",
        f"- Holdout cases: `{report['case_count']}`",
        "",
        "This report is direct ONNX checkpoint replay. It does not cover live Electron capture, "
        "VAD/coordinator timing, provider commit, or ASR network behavior.",
        "",
        "| Language | Cases | Case premature split | Case missed endpoint | Case balanced accuracy |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    language_metrics = report["metrics"]["holdout"]["by_language"]

    def display(value: Any) -> str:
        return "n/a" if value is None else f"{value:.3f}"

    for language in sorted(SUPPORTED_LANGUAGES):
        metrics = language_metrics.get(language, {})
        lines.append(
            "| "
            + " | ".join(
                [
                    language,
                    str(metrics.get("case_count", 0)),
                    display(metrics.get("descriptive_case_premature_split_rate")),
                    display(metrics.get("descriptive_case_missed_endpoint_rate")),
                    display(metrics.get("descriptive_case_balanced_accuracy")),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--criteria", type=Path)
    parser.add_argument("--fixture-dir", type=Path, help="legacy JSONL mode")
    parser.add_argument("--labels", type=Path, help="legacy JSONL mode")
    parser.add_argument(
        "--asset-dir",
        type=Path,
        default=PROJECT_ROOT / "main_logic" / "asr_client" / "endpointing" / "models",
    )
    parser.add_argument("--threshold", type=float)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument(
        "--evidence-digest-output",
        type=Path,
        help=(
            "validate a versioned manifest and write canonical manifest/holdout "
            "speaker digests without loading the model"
        ),
    )
    args = parser.parse_args(argv)

    if args.evidence_digest_output is not None:
        if args.manifest is None:
            parser.error("--evidence-digest-output requires --manifest")
        if any(
            value is not None
            for value in (
                args.criteria,
                args.fixture_dir,
                args.labels,
                args.threshold,
                args.output,
                args.markdown_output,
            )
        ):
            parser.error(
                "--evidence-digest-output cannot be combined with evaluation or "
                "legacy input options"
            )
        manifest = load_manifest(args.manifest)
        rendered = json.dumps(
            _evidence_digest_summary(manifest),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        args.evidence_digest_output.parent.mkdir(parents=True, exist_ok=True)
        args.evidence_digest_output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return 0

    if args.manifest is not None:
        if args.fixture_dir is not None or args.labels is not None:
            parser.error(
                "--manifest cannot be combined with legacy --fixture-dir/--labels"
            )
        manifest = load_manifest(args.manifest)
        criteria = load_acceptance_criteria(args.criteria) if args.criteria else None
    else:
        if args.fixture_dir is None or args.labels is None:
            parser.error("provide --manifest or both --fixture-dir and --labels")
        if args.criteria is not None:
            parser.error("--criteria requires a versioned --manifest")
        legacy_cases = load_cases(args.fixture_dir, args.labels)
        manifest = EvaluationManifest(
            path=None,
            dataset_id="dataset-legacy-unversioned",
            cases=tuple(legacy_cases),
            sha256="legacy-unversioned",
        )
        criteria = None

    asset_dir = args.asset_dir.resolve()
    runtime = SmartTurnV3(enabled=True, asset_dir=asset_dir)
    if not runtime.load():
        parser.error(f"Smart Turn runtime unavailable: {runtime.unavailable_reason}")
    try:
        report = evaluate_manifest(
            manifest,
            runtime,
            threshold=args.threshold,
            criteria=criteria,
            provenance=_runtime_provenance(asset_dir),
        )
    finally:
        runtime.close()
    if args.manifest is None:
        report["scope"] = "smart_turn_legacy_unversioned_checkpoint_replay"

    rendered = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(
            render_markdown_report(report), encoding="utf-8"
        )
    print(rendered)
    return 2 if criteria is not None and report["gate"]["outcome"] != "pass" else 0


if __name__ == "__main__":
    raise SystemExit(main())
