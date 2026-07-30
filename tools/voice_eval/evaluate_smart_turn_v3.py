"""Evaluate Smart Turn v3 on an authorized JSONL-labelled WAV fixture set."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from main_logic.voice_turn.smart_turn_v3 import SmartTurnV3  # noqa: E402


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    path: Path
    expected_complete: bool
    language: str | None = None
    category: str | None = None


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


def load_cases(fixture_dir: Path, labels_path: Path) -> list[EvaluationCase]:
    root = fixture_dir.resolve()
    cases: list[EvaluationCase] = []
    for line_number, line in enumerate(labels_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        relative = Path(value["path"])
        path = (root / relative).resolve()
        if root not in path.parents:
            raise ValueError(f"line {line_number}: fixture path escapes fixture directory")
        expected = value.get("expected")
        if expected not in ("complete", "incomplete"):
            raise ValueError(f"line {line_number}: expected must be complete or incomplete")
        cases.append(
            EvaluationCase(
                path=path,
                expected_complete=expected == "complete",
                language=value.get("language"),
                category=value.get("category"),
            )
        )
    if not cases:
        raise ValueError("labels file contains no evaluation cases")
    return cases


def read_pcm16_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wav_file:
        if wav_file.getnchannels() != 1:
            raise ValueError(f"{path}: expected mono WAV")
        if wav_file.getframerate() != 16_000:
            raise ValueError(f"{path}: expected 16000 Hz WAV")
        if wav_file.getsampwidth() != 2:
            raise ValueError(f"{path}: expected signed PCM16 WAV")
        pcm = wav_file.readframes(wav_file.getnframes())
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


def evaluate_cases(
    cases: list[EvaluationCase], predictor: Predictor, *, threshold: float = 0.5
) -> dict[str, object]:
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be within [0, 1]")
    matrix = ConfusionMatrix()
    latencies_ms: list[float] = []
    results: list[dict[str, object]] = []
    for case in cases:
        audio = read_pcm16_wav(case.path)
        started = time.perf_counter()
        probability = predictor.predict_probability(audio)
        latency_ms = (time.perf_counter() - started) * 1000
        predicted = probability >= threshold
        matrix.add(expected=case.expected_complete, predicted=predicted)
        latencies_ms.append(latency_ms)
        results.append(
            {
                "path": case.path.name,
                "language": case.language,
                "category": case.category,
                "expected": "complete" if case.expected_complete else "incomplete",
                "predicted": "complete" if predicted else "incomplete",
                "probability": probability,
                "latency_ms": latency_ms,
            }
        )
    ordered = sorted(latencies_ms)

    def percentile(fraction: float) -> float:
        index = round((len(ordered) - 1) * fraction)
        return ordered[index]

    return {
        "threshold": threshold,
        "case_count": len(cases),
        "confusion_matrix": asdict(matrix),
        "latency_ms": {
            "median": statistics.median(latencies_ms),
            "p95": percentile(0.95),
            "p99": percentile(0.99),
        },
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-dir", required=True, type=Path)
    parser.add_argument("--labels", required=True, type=Path)
    parser.add_argument("--asset-dir", type=Path, default=PROJECT_ROOT / "data" / "vad_models")
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    runtime = SmartTurnV3(enabled=True, asset_dir=args.asset_dir.resolve())
    if not runtime.load():
        parser.error(f"Smart Turn runtime unavailable: {runtime.unavailable_reason}")
    try:
        report = evaluate_cases(
            load_cases(args.fixture_dir, args.labels), runtime, threshold=args.threshold
        )
    finally:
        runtime.close()
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
