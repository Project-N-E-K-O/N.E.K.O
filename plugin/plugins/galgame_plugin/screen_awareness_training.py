from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .models import OCR_CAPTURE_PROFILE_STAGE_DEFAULT, OCR_CAPTURE_PROFILE_STAGES, json_copy
from .screen_classifier import classify_screen_awareness_model, normalize_screen_type


DEFAULT_LABEL_FIELDS = (
    "manual_screen_type",
    "manual_label",
    "label",
    "stage",
)


@dataclass(slots=True)
class ScreenAwarenessTrainingSample:
    label: str
    features: dict[str, float]
    source: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def load_screen_awareness_samples(
    path: str | Path,
    *,
    label_fields: Iterable[str] = DEFAULT_LABEL_FIELDS,
    allow_rule_labels: bool = False,
) -> list[ScreenAwarenessTrainingSample]:
    sample_path = Path(path)
    samples: list[ScreenAwarenessTrainingSample] = []
    fields = [str(item) for item in label_fields if str(item or "").strip()]
    if allow_rule_labels and "screen_type" not in fields:
        fields.append("screen_type")
    with sample_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            text = line.strip()
            if not text:
                continue
            try:
                record = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            label = _sample_label(record, fields)
            if not label:
                continue
            features = _sample_features(record)
            if len(features) < 2:
                continue
            samples.append(
                ScreenAwarenessTrainingSample(
                    label=label,
                    features=features,
                    source=f"{sample_path}:{line_number}",
                    raw=record,
                )
            )
    return samples


def train_screen_awareness_model(
    samples_path: str | Path,
    output_path: str | Path,
    *,
    label_fields: Iterable[str] = DEFAULT_LABEL_FIELDS,
    allow_rule_labels: bool = False,
    validation_ratio: float = 0.2,
    min_samples_per_stage: int = 2,
    min_confidence: float = 0.55,
) -> dict[str, Any]:
    samples = load_screen_awareness_samples(
        samples_path,
        label_fields=label_fields,
        allow_rule_labels=allow_rule_labels,
    )
    if not samples:
        raise ValueError("no labeled screen awareness samples found")
    training_samples, validation_samples = _split_samples(samples, validation_ratio=validation_ratio)
    model = build_prototype_model(
        training_samples,
        min_samples_per_stage=min_samples_per_stage,
        min_confidence=min_confidence,
    )
    evaluation = evaluate_screen_awareness_model_payload(
        model,
        validation_samples or training_samples,
        min_confidence=min_confidence,
    )
    model["training"] = {
        "sample_path": str(samples_path),
        "total_samples": len(samples),
        "training_samples": len(training_samples),
        "validation_samples": len(validation_samples),
        "label_fields": list(label_fields),
        "allow_rule_labels": bool(allow_rule_labels),
        "created_at": _utc_now_iso(),
    }
    model["evaluation"] = evaluation

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(model, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return {
        "model": model,
        "evaluation": evaluation,
        "output_path": str(destination),
        "summary": (
            f"screen awareness model trained stages={len(model.get('prototypes') or [])} "
            f"samples={len(samples)} accuracy={evaluation.get('accuracy', 0.0):.3f}"
        ),
    }


def evaluate_screen_awareness_model(
    samples_path: str | Path,
    model_path: str | Path,
    *,
    label_fields: Iterable[str] = DEFAULT_LABEL_FIELDS,
    allow_rule_labels: bool = False,
    min_confidence: float = 0.55,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    samples = load_screen_awareness_samples(
        samples_path,
        label_fields=label_fields,
        allow_rule_labels=allow_rule_labels,
    )
    if not samples:
        raise ValueError("no labeled screen awareness samples found")
    model_payload = json.loads(Path(model_path).read_text(encoding="utf-8"))
    if not isinstance(model_payload, dict):
        raise ValueError("model payload must be an object")
    evaluation = evaluate_screen_awareness_model_payload(
        model_payload,
        samples,
        min_confidence=min_confidence,
    )
    report = {
        "model_path": str(model_path),
        "sample_path": str(samples_path),
        "evaluated_at": _utc_now_iso(),
        "evaluation": evaluation,
    }
    if report_path is not None:
        destination = Path(report_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        report["report_path"] = str(destination)
    report["summary"] = (
        f"screen awareness model evaluated samples={evaluation.get('sample_count', 0)} "
        f"accuracy={evaluation.get('accuracy', 0.0):.3f}"
    )
    return report


def build_prototype_model(
    samples: list[ScreenAwarenessTrainingSample],
    *,
    min_samples_per_stage: int = 2,
    min_confidence: float = 0.55,
) -> dict[str, Any]:
    if not samples:
        raise ValueError("training sample set is empty")
    by_label: dict[str, list[ScreenAwarenessTrainingSample]] = {}
    for sample in samples:
        by_label.setdefault(sample.label, []).append(sample)
    feature_names = sorted({key for sample in samples for key in sample.features})
    if not feature_names:
        raise ValueError("training samples have no numeric features")
    scales = _feature_scales(samples, feature_names)
    prototypes: list[dict[str, Any]] = []
    for label, label_samples in sorted(by_label.items()):
        if len(label_samples) < max(1, int(min_samples_per_stage or 1)):
            continue
        features: dict[str, float] = {}
        for feature_name in feature_names:
            values = [sample.features[feature_name] for sample in label_samples if feature_name in sample.features]
            if values:
                features[feature_name] = round(sum(values) / len(values), 4)
        if len(features) < 2:
            continue
        sample_count = len(label_samples)
        prototypes.append(
            {
                "id": f"{label}-centroid",
                "stage": label,
                "features": features,
                "sample_count": sample_count,
                "confidence": round(min(0.95, max(float(min_confidence), 0.65 + min(sample_count, 30) / 100.0)), 3),
            }
        )
    if not prototypes:
        raise ValueError("not enough labeled samples per stage to build prototypes")
    return {
        "version": 1,
        "model_type": "prototype_centroid",
        "feature_scales": scales,
        "prototypes": prototypes,
        "base_confidence": 0.85,
    }


def evaluate_screen_awareness_model_payload(
    model_payload: dict[str, Any],
    samples: list[ScreenAwarenessTrainingSample],
    *,
    min_confidence: float = 0.55,
) -> dict[str, Any]:
    confusion: dict[str, dict[str, int]] = {}
    per_stage: dict[str, dict[str, int]] = {}
    correct = 0
    unknown = 0
    started_at = time.perf_counter()
    for sample in samples:
        prediction = classify_screen_awareness_model(
            sample.features,
            model_payload,
            min_confidence=min_confidence,
        )
        predicted = (
            str(prediction.get("stage") or OCR_CAPTURE_PROFILE_STAGE_DEFAULT)
            if isinstance(prediction, dict)
            else OCR_CAPTURE_PROFILE_STAGE_DEFAULT
        )
        if predicted == OCR_CAPTURE_PROFILE_STAGE_DEFAULT:
            unknown += 1
        if predicted == sample.label:
            correct += 1
        confusion.setdefault(sample.label, {})
        confusion[sample.label][predicted] = confusion[sample.label].get(predicted, 0) + 1
        stage_stats = per_stage.setdefault(sample.label, {"total": 0, "correct": 0})
        stage_stats["total"] += 1
        if predicted == sample.label:
            stage_stats["correct"] += 1
    duration = max(0.0, time.perf_counter() - started_at)
    sample_count = len(samples)
    per_stage_accuracy = {
        stage: {
            "total": stats["total"],
            "correct": stats["correct"],
            "accuracy": round(stats["correct"] / max(stats["total"], 1), 4),
        }
        for stage, stats in sorted(per_stage.items())
    }
    return {
        "sample_count": sample_count,
        "correct": correct,
        "unknown": unknown,
        "accuracy": round(correct / max(sample_count, 1), 4),
        "unknown_rate": round(unknown / max(sample_count, 1), 4),
        "duration_seconds": round(duration, 6),
        "avg_latency_seconds": round(duration / max(sample_count, 1), 6),
        "per_stage": per_stage_accuracy,
        "confusion": confusion,
    }


def _sample_label(record: dict[str, Any], label_fields: list[str]) -> str:
    for field_name in label_fields:
        label = normalize_screen_type(record.get(field_name))
        if label and label in OCR_CAPTURE_PROFILE_STAGES and label != OCR_CAPTURE_PROFILE_STAGE_DEFAULT:
            return label
    return ""


def _sample_features(record: dict[str, Any]) -> dict[str, float]:
    features: dict[str, float] = {}
    visual = record.get("visual_features")
    if isinstance(visual, dict):
        for key, value in visual.items():
            _assign_numeric_feature(features, str(key), value)
    screen_debug = record.get("screen_debug")
    if isinstance(screen_debug, dict):
        layout = screen_debug.get("layout")
        if isinstance(layout, dict):
            for key, value in layout.items():
                _assign_numeric_feature(features, str(key), value)
    ocr_lines = record.get("ocr_lines")
    if isinstance(ocr_lines, list):
        features["line_count"] = float(len(ocr_lines))
    ui_elements = record.get("screen_ui_elements")
    if isinstance(ui_elements, list):
        features["ui_element_count"] = float(len(ui_elements))
    return features


def _assign_numeric_feature(target: dict[str, float], key: str, value: object) -> None:
    if isinstance(value, bool):
        return
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return
    if math.isfinite(parsed):
        target[key] = parsed


def _feature_scales(
    samples: list[ScreenAwarenessTrainingSample],
    feature_names: list[str],
) -> dict[str, float]:
    scales: dict[str, float] = {}
    for feature_name in feature_names:
        values = [sample.features[feature_name] for sample in samples if feature_name in sample.features]
        if len(values) < 2:
            scales[feature_name] = 1.0
            continue
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        scale = math.sqrt(max(variance, 0.0))
        scales[feature_name] = round(scale if scale > 0.0001 else 1.0, 4)
    return scales


def _split_samples(
    samples: list[ScreenAwarenessTrainingSample],
    *,
    validation_ratio: float,
) -> tuple[list[ScreenAwarenessTrainingSample], list[ScreenAwarenessTrainingSample]]:
    ratio = max(0.0, min(float(validation_ratio or 0.0), 0.8))
    if ratio <= 0.0 or len(samples) < 5:
        return list(samples), []
    validation_size = max(1, int(round(len(samples) * ratio)))
    validation: list[ScreenAwarenessTrainingSample] = []
    training: list[ScreenAwarenessTrainingSample] = []
    interval = max(2, int(round(len(samples) / validation_size)))
    for index, sample in enumerate(samples):
        if len(validation) < validation_size and index % interval == 0:
            validation.append(sample)
        else:
            training.append(sample)
    if not training:
        return list(samples), []
    return training, validation


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _main() -> int:
    parser = argparse.ArgumentParser(description="Train or evaluate a galgame OCR screen awareness model.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    train_parser = subparsers.add_parser("train")
    train_parser.add_argument("samples")
    train_parser.add_argument("output")
    train_parser.add_argument("--allow-rule-labels", action="store_true")
    train_parser.add_argument("--validation-ratio", type=float, default=0.2)
    train_parser.add_argument("--min-samples-per-stage", type=int, default=2)
    train_parser.add_argument("--min-confidence", type=float, default=0.55)

    eval_parser = subparsers.add_parser("evaluate")
    eval_parser.add_argument("samples")
    eval_parser.add_argument("model")
    eval_parser.add_argument("--report")
    eval_parser.add_argument("--allow-rule-labels", action="store_true")
    eval_parser.add_argument("--min-confidence", type=float, default=0.55)

    args = parser.parse_args()
    if args.command == "train":
        result = train_screen_awareness_model(
            args.samples,
            args.output,
            allow_rule_labels=bool(args.allow_rule_labels),
            validation_ratio=float(args.validation_ratio),
            min_samples_per_stage=int(args.min_samples_per_stage),
            min_confidence=float(args.min_confidence),
        )
    else:
        result = evaluate_screen_awareness_model(
            args.samples,
            args.model,
            report_path=args.report,
            allow_rule_labels=bool(args.allow_rule_labels),
            min_confidence=float(args.min_confidence),
        )
    print(json.dumps(json_copy(result), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
