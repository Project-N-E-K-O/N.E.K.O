from __future__ import annotations

import argparse
import ast
import json
import logging
import math
import os
import statistics
import subprocess
import sys
import time
import trace
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from PIL import Image

from plugin.plugins.galgame_plugin.core.vision.labels import (
    vision_label_to_screen_type,
)
from plugin.plugins.galgame_plugin.core.vision.preprocessing import (
    IMAGENET_MEAN,
    IMAGENET_STD,
    softmax,
)
from plugin.plugins.galgame_plugin.models import OCR_CAPTURE_PROFILE_STAGE_DEFAULT
from plugin.plugins.galgame_plugin.screen_awareness_training import (
    ScreenAwarenessTrainingSample,
    build_prototype_model,
)
from plugin.plugins.galgame_plugin.screen_classifier import (
    analyze_screen_visual_features,
    classify_screen_awareness_model,
)


_LOGGER = logging.getLogger(__name__)
DEFAULT_MODEL_DIR = "plugin/plugins/galgame_plugin/models/vision/screen_classifier"


@dataclass(slots=True)
class Phase1PredictionRecord:
    tick_index: int
    image_path: str
    expected_label: str
    expected_stage: str
    cnn_label: str
    cnn_stage: str
    cnn_confidence: float
    cnn_latency_ms: float
    prototype_stage: str
    prototype_confidence: float
    prototype_latency_ms: float


class OnnxScreenClassifier:
    def __init__(
        self,
        *,
        model_path: Path,
        config_path: Path,
        providers: list[str] | None = None,
    ) -> None:
        import onnxruntime as ort

        self.config = json.loads(config_path.read_text(encoding="utf-8"))
        self.labels = tuple(str(label) for label in self.config.get("labels") or [])
        if not self.labels:
            raise ValueError(f"{config_path} has no labels")
        input_size = self.config.get("input_size") or [224, 224]
        self.input_size = (int(input_size[0]), int(input_size[1]))
        self.threshold = float(self.config.get("threshold", 0.75))
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.intra_op_num_threads = 2
        opts.inter_op_num_threads = 1
        opts.enable_mem_pattern = True
        self.providers = providers or ["CPUExecutionProvider"]
        self.session = ort.InferenceSession(
            str(model_path),
            providers=self.providers,
            sess_options=opts,
        )
        self.input_name = str(self.session.get_inputs()[0].name)

    def classify(self, image_path: str | Path) -> dict[str, Any]:
        tensor = self._preprocess(image_path)
        started_at = time.perf_counter()
        output = self.session.run(None, {self.input_name: tensor})[0]
        latency_ms = (time.perf_counter() - started_at) * 1000.0
        logits = np.asarray(output, dtype=np.float32)
        if logits.ndim == 2:
            logits = logits[0]
        if logits.ndim != 1 or logits.size <= 0:
            raise ValueError(f"invalid_logits_shape: {tuple(logits.shape)}")
        if logits.size != len(self.labels):
            raise ValueError(
                "logits_label_mismatch: "
                f"shape={tuple(logits.shape)}, logits={logits.size}, labels={len(self.labels)}"
            )
        scores = softmax(logits)
        top_index = int(np.argmax(scores))
        label = self.labels[top_index]
        confidence = round(float(scores[top_index]), 4)
        return {
            "label": label,
            "stage": label_to_stage(label),
            "confidence": confidence,
            "latency_ms": round(max(0.0, latency_ms), 3),
            "all_scores": {
                self.labels[index]: round(float(score), 6)
                for index, score in enumerate(scores[: len(self.labels)])
            },
        }

    def _preprocess(self, image_path: str | Path) -> np.ndarray:
        with Image.open(image_path) as image:
            image = image.convert("RGB").resize(self.input_size, Image.Resampling.BILINEAR)
            array = np.asarray(image, dtype=np.float32) / 255.0
        array = (array - IMAGENET_MEAN) / IMAGENET_STD
        array = np.transpose(array, (2, 0, 1))
        return np.expand_dims(array.astype(np.float32, copy=False), axis=0)


def label_to_stage(label: str) -> str:
    return vision_label_to_screen_type(label)


def load_split_samples(data_dir: Path, splits: Iterable[str]) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    seen: set[str] = set()
    for split in splits:
        path = data_dir / f"{split}.jsonl"
        with path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    _LOGGER.warning("invalid JSONL row skipped: %s:%s: %s", path, line_no, exc)
                    continue
                rel_path = str(record.get("image_path") or "")
                label = str(record.get("label") or "").strip()
                if not rel_path or not label:
                    continue
                absolute = Path(rel_path)
                if not absolute.is_absolute():
                    absolute = data_dir / absolute
                key = str(absolute.resolve())
                if key in seen:
                    continue
                seen.add(key)
                samples.append(
                    {
                        "image_path": key,
                        "label": label,
                        "stage": label_to_stage(label),
                        "source": str(record.get("source") or split),
                        "split": split,
                        "line_no": line_no,
                    }
                )
    return samples


def build_replay_ticks(samples: list[dict[str, Any]], *, tick_count: int) -> list[dict[str, Any]]:
    if not samples:
        raise ValueError("cannot build replay ticks from an empty sample list")
    requested = max(1, int(tick_count))
    ticks: list[dict[str, Any]] = []
    for index in range(requested):
        sample = dict(samples[index % len(samples)])
        sample["tick_index"] = index
        ticks.append(sample)
    return ticks


def build_visual_prototype_model(samples: list[dict[str, Any]]) -> dict[str, Any]:
    training_samples: list[ScreenAwarenessTrainingSample] = []
    for sample in samples:
        stage = str(sample.get("stage") or "")
        if not stage or stage == OCR_CAPTURE_PROFILE_STAGE_DEFAULT:
            continue
        features = visual_features_for_image(Path(str(sample["image_path"])))
        if len(features) < 2:
            continue
        training_samples.append(
            ScreenAwarenessTrainingSample(
                label=stage,
                features={key: float(value) for key, value in features.items()},
                source=str(sample.get("image_path") or ""),
                raw=dict(sample),
            )
        )
    return build_prototype_model(
        training_samples,
        min_samples_per_stage=2,
        min_confidence=0.55,
    )


def visual_features_for_image(image_path: Path) -> dict[str, float]:
    with Image.open(image_path) as image:
        features = analyze_screen_visual_features(image.convert("RGB"))
    result: dict[str, float] = {}
    for key, value in features.items():
        if isinstance(value, bool):
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed):
            result[str(key)] = parsed
    return result


def evaluate_replay_ticks(
    *,
    classifier: OnnxScreenClassifier,
    prototype_model: dict[str, Any],
    ticks: list[dict[str, Any]],
    prototype_min_confidence: float = 0.0,
) -> list[Phase1PredictionRecord]:
    records: list[Phase1PredictionRecord] = []
    for tick in ticks:
        image_path = Path(str(tick["image_path"]))
        cnn = classifier.classify(image_path)
        features = visual_features_for_image(image_path)
        started_at = time.perf_counter()
        prototype = classify_screen_awareness_model(
            features,
            prototype_model,
            min_confidence=prototype_min_confidence,
        )
        prototype_latency_ms = (time.perf_counter() - started_at) * 1000.0
        prototype_stage = (
            str(prototype.get("stage") or OCR_CAPTURE_PROFILE_STAGE_DEFAULT)
            if isinstance(prototype, dict)
            else OCR_CAPTURE_PROFILE_STAGE_DEFAULT
        )
        prototype_confidence = (
            float(prototype.get("confidence") or 0.0) if isinstance(prototype, dict) else 0.0
        )
        records.append(
            Phase1PredictionRecord(
                tick_index=int(tick["tick_index"]),
                image_path=str(image_path),
                expected_label=str(tick["label"]),
                expected_stage=str(tick["stage"]),
                cnn_label=str(cnn["label"]),
                cnn_stage=str(cnn["stage"]),
                cnn_confidence=float(cnn["confidence"]),
                cnn_latency_ms=float(cnn["latency_ms"]),
                prototype_stage=prototype_stage,
                prototype_confidence=round(prototype_confidence, 4),
                prototype_latency_ms=round(max(0.0, prototype_latency_ms), 3),
            )
        )
    return records


def summarize_predictions(
    records: list[Phase1PredictionRecord],
    *,
    threshold: float,
) -> dict[str, Any]:
    tick_count = len(records)
    if tick_count <= 0:
        raise ValueError("cannot summarize empty prediction records")
    cnn_label_correct = sum(1 for item in records if item.cnn_label == item.expected_label)
    cnn_stage_correct = sum(1 for item in records if item.cnn_stage == item.expected_stage)
    prototype_stage_correct = sum(
        1 for item in records if item.prototype_stage == item.expected_stage
    )
    agreement = sum(1 for item in records if item.cnn_stage == item.prototype_stage)
    high_confidence = sum(1 for item in records if item.cnn_confidence >= threshold)
    fallback_correct = 0
    fallback_used = 0
    for item in records:
        if item.cnn_confidence >= threshold:
            final_stage = item.cnn_stage
        else:
            fallback_used += 1
            final_stage = item.prototype_stage
        if final_stage == item.expected_stage:
            fallback_correct += 1
    return {
        "tick_count": tick_count,
        "threshold": threshold,
        "cnn_label_accuracy": _rate(cnn_label_correct, tick_count),
        "cnn_stage_accuracy": _rate(cnn_stage_correct, tick_count),
        "prototype_stage_accuracy": _rate(prototype_stage_correct, tick_count),
        "cnn_vs_prototype_stage_agreement": _rate(agreement, tick_count),
        "cnn_high_confidence_rate": _rate(high_confidence, tick_count),
        "cnn_high_confidence_count": high_confidence,
        "cnn_primary_with_prototype_fallback_stage_accuracy": _rate(
            fallback_correct,
            tick_count,
        ),
        "prototype_fallback_count": fallback_used,
        "cnn_latency_ms": _latency_summary([item.cnn_latency_ms for item in records]),
        "prototype_latency_ms": _latency_summary(
            [item.prototype_latency_ms for item in records]
        ),
        "per_expected_stage": _per_stage_summary(records),
        "stage_confusion": _stage_confusion(records),
    }


def benchmark_onnx_provider(
    *,
    model_path: Path,
    config_path: Path,
    provider: str,
    image_path: Path,
    iterations: int,
) -> dict[str, Any]:
    import onnxruntime as ort

    available = list(ort.get_available_providers())
    if provider not in available:
        return {
            "provider": provider,
            "status": "not_available",
            "available_providers": available,
        }
    try:
        classifier = OnnxScreenClassifier(
            model_path=model_path,
            config_path=config_path,
            providers=[provider],
        )
        for _ in range(10):
            classifier.classify(image_path)
        latencies = [
            float(classifier.classify(image_path)["latency_ms"])
            for _ in range(max(1, int(iterations)))
        ]
    except Exception as exc:
        return {
            "provider": provider,
            "status": "provider_failed",
            "available_providers": available,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "provider": provider,
        "status": "ok",
        "available_providers": available,
        "latency_ms": _latency_summary(latencies),
    }


def run_changed_line_coverage(
    *,
    repo: Path,
    source_paths: list[str],
    pytest_args: list[str],
    base_ref: str | None = None,
) -> dict[str, Any]:
    try:
        import pytest
    except ImportError:
        return {"status": "pytest_unavailable"}
    raw_changed_lines = _changed_lines(repo, source_paths, base_ref=base_ref)
    changed_lines: dict[str, set[int]] = {}
    ignored_lines: dict[str, list[int]] = {}
    for rel_path, lines in raw_changed_lines.items():
        executable = _function_executable_lines(repo / rel_path)
        selected = set(lines) & executable
        ignored = set(lines) - selected
        if selected:
            changed_lines[rel_path] = selected
        if ignored:
            ignored_lines[rel_path] = sorted(ignored)
    if not changed_lines:
        return {"status": "no_changed_lines", "source_paths": source_paths}
    tracer = trace.Trace(count=True, trace=False)
    exit_code = tracer.runfunc(pytest.main, pytest_args)
    counts = tracer.results().counts
    per_file: dict[str, Any] = {}
    total_changed = 0
    total_covered = 0
    for rel_path, lines in sorted(changed_lines.items()):
        absolute = str((repo / rel_path).resolve())
        executed = {
            lineno
            for (filename, lineno), count in counts.items()
            if count > 0 and os.path.normcase(str(Path(filename).resolve())) == os.path.normcase(absolute)
        }
        covered = sorted(set(lines) & executed)
        total_changed += len(lines)
        total_covered += len(covered)
        per_file[rel_path] = {
            "changed_lines": sorted(lines),
            "covered_lines": covered,
            "coverage": _rate(len(covered), len(lines)),
        }
    return {
        "status": "ok" if int(exit_code) == 0 else "pytest_failed",
        "pytest_exit_code": int(exit_code),
        "changed_line_coverage": _rate(total_covered, total_changed),
        "changed_lines": total_changed,
        "covered_changed_lines": total_covered,
        "ignored_non_function_or_non_executable_changed_lines": ignored_lines,
        "per_file": per_file,
    }


def run_acceptance(args: argparse.Namespace) -> dict[str, Any]:
    repo = Path(args.repo).resolve()
    data_dir = (repo / args.data_dir).resolve()
    model_path = (repo / args.model_path).resolve()
    config_path = (repo / args.config_path).resolve()
    train_samples = load_split_samples(data_dir, ["train"])
    eval_samples = load_split_samples(data_dir, list(args.splits))
    if not eval_samples:
        raise ValueError("no evaluation samples loaded")
    classifier = OnnxScreenClassifier(
        model_path=model_path,
        config_path=config_path,
        providers=["CPUExecutionProvider"],
    )
    prototype_model = build_visual_prototype_model(train_samples)
    ticks = build_replay_ticks(eval_samples, tick_count=int(args.ticks))
    records = evaluate_replay_ticks(
        classifier=classifier,
        prototype_model=prototype_model,
        ticks=ticks,
        prototype_min_confidence=0.0,
    )
    summary = summarize_predictions(records, threshold=classifier.threshold)
    representative = Path(str(eval_samples[0]["image_path"]))
    provider_benchmarks = {
        "cpu": benchmark_onnx_provider(
            model_path=model_path,
            config_path=config_path,
            provider="CPUExecutionProvider",
            image_path=representative,
            iterations=int(args.latency_iterations),
        ),
        "directml": benchmark_onnx_provider(
            model_path=model_path,
            config_path=config_path,
            provider="DmlExecutionProvider",
            image_path=representative,
            iterations=int(args.latency_iterations),
        ),
        "cuda": benchmark_onnx_provider(
            model_path=model_path,
            config_path=config_path,
            provider="CUDAExecutionProvider",
            image_path=representative,
            iterations=int(args.latency_iterations),
        ),
    }
    report = {
        "report_name": "phase1_acceptance_v1",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "data": {
            "train_samples": len(train_samples),
            "eval_samples": len(eval_samples),
            "tick_mode": "deterministic_replay",
            "tick_count": len(records),
            "splits": list(args.splits),
        },
        "ab_1000_tick_replay": summary,
        "cnn_vs_prototype": {
            "cnn_stage_accuracy": summary["cnn_stage_accuracy"],
            "prototype_stage_accuracy": summary["prototype_stage_accuracy"],
            "absolute_accuracy_delta": round(
                float(summary["cnn_stage_accuracy"])
                - float(summary["prototype_stage_accuracy"]),
                6,
            ),
            "agreement": summary["cnn_vs_prototype_stage_agreement"],
            "note": (
                "Replay uses the same labeled screenshots for CNN and the legacy "
                "visual prototype centroid classifier. OCR text is not replayed."
            ),
        },
        "provider_latency": provider_benchmarks,
        "records_preview": [asdict(item) for item in records[: min(20, len(records))]],
    }
    output_path = (repo / args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def run_coverage(args: argparse.Namespace) -> dict[str, Any]:
    repo = Path(args.repo).resolve()
    report = run_changed_line_coverage(
        repo=repo,
        source_paths=list(args.coverage_source),
        pytest_args=list(args.coverage_pytest_args),
        base_ref=str(args.coverage_base_ref),
    )
    payload = {
        "report_name": "phase1_changed_line_coverage_v1",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "coverage": report,
    }
    output_path = (repo / args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def _coverage_exit_code(payload: dict[str, Any]) -> int:
    coverage = payload.get("coverage", {})
    if not isinstance(coverage, dict):
        return 1
    pytest_exit_code = int(coverage.get("pytest_exit_code") or 0)
    if pytest_exit_code:
        return pytest_exit_code
    status = str(coverage.get("status", "") or "")
    return 0 if status in {"ok", "no_changed_lines"} else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Phase 1 screen classifier acceptance checks.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    acceptance = subparsers.add_parser("acceptance")
    acceptance.add_argument("--repo", default=".")
    acceptance.add_argument("--data-dir", default="plugin/plugins/galgame_plugin/training/data")
    acceptance.add_argument("--model-path", default=f"{DEFAULT_MODEL_DIR}/v1_galgame.onnx")
    acceptance.add_argument("--config-path", default=f"{DEFAULT_MODEL_DIR}/v1_config.json")
    acceptance.add_argument("--output", default=f"{DEFAULT_MODEL_DIR}/phase1_acceptance_v1.json")
    acceptance.add_argument("--ticks", type=int, default=1000)
    acceptance.add_argument("--splits", nargs="+", default=["val"])
    acceptance.add_argument("--latency-iterations", type=int, default=120)

    coverage = subparsers.add_parser("coverage")
    coverage.add_argument("--repo", default=".")
    coverage.add_argument("--output", default=f"{DEFAULT_MODEL_DIR}/phase1_coverage_v1.json")
    coverage.add_argument(
        "--coverage-base-ref",
        default=os.environ.get("GALGAME_PHASE1_COVERAGE_BASE_REF", "origin/main"),
        help=(
            "Git base ref or explicit diff range for changed-line coverage "
            "(default: origin/main via merge-base)."
        ),
    )
    coverage.add_argument(
        "--coverage-source",
        nargs="+",
        default=[
            "plugin/plugins/_shared/rapidocr/_runtime.py",
            "plugin/plugins/galgame_plugin/ocr_capture_backends/_helpers.py",
            "plugin/plugins/galgame_plugin/ocr_manager_capture.py",
            "plugin/plugins/galgame_plugin/ocr_manager_runtime.py",
            "plugin/plugins/galgame_plugin/ocr_reader.py",
            "plugin/plugins/galgame_plugin/ocr_runtime_types.py",
            "plugin/plugins/galgame_plugin/service/__init__.py",
        ],
    )
    coverage.add_argument(
        "--coverage-pytest-args",
        nargs="+",
        default=[
            "-q",
            "plugin/tests/unit/plugins/test_galgame_rapidocr_support.py",
            "plugin/tests/unit/plugins/test_galgame_ocr_reader.py::test_background_hash_excludes_bottom_dialogue_region",
            "plugin/tests/unit/plugins/test_galgame_ocr_reader.py::test_ocr_capture_keeps_full_frame_for_vision_classifier",
            "plugin/tests/unit/plugins/test_galgame_ocr_reader.py::test_ocr_reader_runtime_groups_fields_and_keeps_flat_compatibility",
            "plugin/tests/unit/plugins/test_galgame_ocr_reader.py::test_ocr_reader_build_runtime_exposes_vision_classifier_status",
            "plugin/tests/unit/plugins/test_galgame_ocr_reader.py::test_ocr_reader_logs_when_vision_classifier_loads",
            "plugin/tests/unit/plugins/test_galgame_service.py::test_status_payload_exposes_vision_classifier_runtime",
            "--basetemp",
            ".codex-tmp/pytest-phase1-coverage",
        ],
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "coverage":
        payload = run_coverage(args)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return _coverage_exit_code(payload)
    else:
        payload = run_acceptance(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _rate(numerator: int, denominator: int) -> float:
    return round(float(numerator) / max(1, int(denominator)), 6)


def _latency_summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {"p50": 0.0, "p95": 0.0, "max": 0.0}
    ordered = sorted(float(value) for value in values)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return {
        "p50": round(float(statistics.median(ordered)), 3),
        "p95": round(float(ordered[p95_index]), 3),
        "max": round(float(max(ordered)), 3),
    }


def _per_stage_summary(records: list[Phase1PredictionRecord]) -> dict[str, Any]:
    totals: dict[str, Counter[str]] = defaultdict(Counter)
    for item in records:
        stage = item.expected_stage
        totals[stage]["total"] += 1
        if item.cnn_stage == stage:
            totals[stage]["cnn_correct"] += 1
        if item.prototype_stage == stage:
            totals[stage]["prototype_correct"] += 1
        if item.cnn_stage == item.prototype_stage:
            totals[stage]["agreement"] += 1
    return {
        stage: {
            "total": int(values["total"]),
            "cnn_accuracy": _rate(int(values["cnn_correct"]), int(values["total"])),
            "prototype_accuracy": _rate(
                int(values["prototype_correct"]),
                int(values["total"]),
            ),
            "agreement": _rate(int(values["agreement"]), int(values["total"])),
        }
        for stage, values in sorted(totals.items())
    }


def _stage_confusion(records: list[Phase1PredictionRecord]) -> dict[str, Any]:
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for item in records:
        confusion[item.expected_stage][item.cnn_stage] += 1
    return {
        stage: dict(sorted(predictions.items()))
        for stage, predictions in sorted(confusion.items())
    }


def _changed_lines(
    repo: Path,
    source_paths: list[str],
    *,
    base_ref: str | None = None,
) -> dict[str, set[int]]:
    diff_ref = _resolve_changed_line_diff_ref(repo, base_ref)
    completed = subprocess.run(
        ["git", "diff", "-U0", diff_ref, "--", *source_paths],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    changed: dict[str, set[int]] = {}
    current_path = ""
    for line in completed.stdout.splitlines():
        if line.startswith("+++ b/"):
            current_path = line[6:] if line.startswith("+++ b/") else line
            continue
        if not current_path or not line.startswith("@@"):
            continue
        marker = line.split("@@", 2)[1].strip()
        plus_part = next((part for part in marker.split() if part.startswith("+")), "")
        if not plus_part:
            continue
        start_text, _, count_text = plus_part[1:].partition(",")
        try:
            start = int(start_text)
            count = int(count_text) if count_text else 1
        except ValueError:
            continue
        if count <= 0:
            continue
        changed.setdefault(current_path, set()).update(range(start, start + count))
    return changed


def _resolve_changed_line_diff_ref(repo: Path, base_ref: str | None) -> str:
    configured = str(
        base_ref or os.environ.get("GALGAME_PHASE1_COVERAGE_BASE_REF") or "origin/main"
    ).strip()
    if not configured:
        configured = "origin/main"
    if ".." in configured:
        return configured
    completed = subprocess.run(
        ["git", "merge-base", configured, "HEAD"],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    merge_base = completed.stdout.strip()
    if completed.returncode == 0 and merge_base:
        return f"{merge_base}..HEAD"
    return f"{configured}..HEAD"


def _function_executable_lines(path: Path) -> set[int]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return set()
    lines: set[int] = set()
    for function in [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]:
        for child in ast.walk(function):
            if child is function:
                continue
            line_no = getattr(child, "lineno", None)
            if isinstance(line_no, int):
                lines.add(line_no)
    return lines


if __name__ == "__main__":
    raise SystemExit(main())
