"""Replay explicitly supplied 16 kHz mono PCM16 WAV files through local TSE.

Example (no model download and no ASR/network request):
    uv run --no-sync python scripts/tse_replay.py --model-dir /models/tse \
        --reference ref1.wav ref2.wav ref3.wav --input mixed.wav --output extracted.wav

The fourth enrollment validation clip must not be supplied as a reference.
Metrics describe this offline process, not integrated NEKO latency or memory.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import wave

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main_logic.voice_identity.tse import TseEncoder, TseModel  # noqa: E402
from main_logic.voice_identity.tse.contracts import (  # noqa: E402
    BLOCK_SAMPLES, PREPROCESSING_REVISION, REFERENCE_METHOD, RESOURCE_REVISION, SAMPLE_RATE,
)


def open_pcm16(path: Path) -> wave.Wave_read:
    source = wave.open(str(path), "rb")
    if (source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getcomptype()) != (1, 2, SAMPLE_RATE, "NONE"):
        source.close()
        raise ValueError(f"{path.name}: expected mono 16 kHz uncompressed PCM16 WAV")
    return source


def read_pcm16(path: Path, *, maximum_samples: int | None = None) -> np.ndarray:
    with open_pcm16(path) as source:
        if maximum_samples is not None and source.getnframes() > maximum_samples:
            raise ValueError(f"{path.name}: reference recording exceeds 30 seconds")
        return np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(np.float32) / 32768


def si_sdr(estimate: np.ndarray, target: np.ndarray) -> float | None:
    if estimate.shape != target.shape:
        raise ValueError("clean target and replay input must have exactly the same sample range")
    estimate = estimate.astype(np.float64) - np.mean(estimate, dtype=np.float64)
    target = target.astype(np.float64) - np.mean(target, dtype=np.float64)
    energy = float(target @ target)
    if energy <= 1e-12:
        return None
    projection = target * ((estimate @ target) / energy)
    residual = estimate - projection
    return float(10 * np.log10(max(float(projection @ projection), 1e-20) / max(float(residual @ residual), 1e-20)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--reference", type=Path, nargs=3, required=True, metavar=("REF1", "REF2", "REF3"))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--clean-target", type=Path, help="optional aligned clean target for SI-SDR; never enrollment validation audio")
    args = parser.parse_args()
    report_path = args.report or args.output.with_suffix(".report.json")
    protected_paths = {path.resolve() for path in [args.input, *args.reference]}
    if args.clean_target:
        protected_paths.add(args.clean_target.resolve())
    if args.output.resolve() in protected_paths or report_path.resolve() in protected_paths or args.output.resolve() == report_path.resolve():
        parser.error("output/report paths must not overwrite source recordings or each other")
    if args.output.exists() or report_path.exists():
        parser.error("output/report already exists; choose fresh paths")
    references = [read_pcm16(path, maximum_samples=30 * SAMPLE_RATE) for path in args.reference]
    import psutil

    process = psutil.Process()
    baseline_rss = process.memory_info().rss
    start = time.perf_counter()
    with TseEncoder(args.model_dir) as encoder:
        embedding = encoder.encode_references(references)
    enrollment_ms = (time.perf_counter() - start) * 1000
    for reference in references:
        reference.fill(0)
    del references
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    durations, output_count, clipped_samples = [], 0, 0
    total_start = time.perf_counter()
    with TseModel(args.model_dir) as model:
        load_ms = (time.perf_counter() - total_start) * 1000
        stream = model.create_stream(embedding)
        embedding.fill(0)
        try:
            with open_pcm16(args.input) as source, wave.open(str(args.output), "wb") as destination:
                destination.setnchannels(1)
                destination.setsampwidth(2)
                destination.setframerate(SAMPLE_RATE)
                input_count = source.getnframes()
                while True:
                    raw = source.readframes(BLOCK_SAMPLES)
                    if not raw:
                        break
                    pcm = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
                    start = time.perf_counter()
                    chunks = stream.push(pcm)
                    durations.append((time.perf_counter() - start) * 1000)
                    for chunk in chunks:
                        if chunk.start_sample != output_count:
                            raise RuntimeError("non-contiguous replay output")
                        clipped_samples += int(np.count_nonzero(np.abs(chunk.pcm) > 1))
                        destination.writeframesraw(np.clip(np.rint(chunk.pcm * 32768), -32768, 32767).astype("<i2").tobytes())
                        output_count = chunk.end_sample
                start = time.perf_counter()
                tail = stream.flush()
                flush_ms = (time.perf_counter() - start) * 1000
                for chunk in tail:
                    if chunk.start_sample != output_count:
                        raise RuntimeError("non-contiguous replay tail")
                    clipped_samples += int(np.count_nonzero(np.abs(chunk.pcm) > 1))
                    destination.writeframesraw(np.clip(np.rint(chunk.pcm * 32768), -32768, 32767).astype("<i2").tobytes())
                    output_count = chunk.end_sample
                if output_count != input_count:
                    raise RuntimeError("replay output does not cover the whole input")
        finally:
            stream.close()
    info = process.memory_info()
    report = {
        "resource_revision": RESOURCE_REVISION,
        "preprocessing_revision": PREPROCESSING_REVISION,
        "reference_method": REFERENCE_METHOD,
        "quality_status": "reference aggregation and real-room performance await independent acceptance",
        "scope": "offline supplied recordings; not end-to-end NEKO latency or incremental memory",
        "input": str(args.input.resolve()), "output": str(args.output.resolve()),
        "sample_rate": SAMPLE_RATE, "input_samples": input_count, "output_samples": output_count,
        "enrollment_and_encoder_load_ms": enrollment_ms, "separator_load_ms": load_ms,
        "block_samples": BLOCK_SAMPLES, "blocks": len(durations), "flush_ms": flush_ms,
        "block_median_ms": float(np.median(durations)) if durations else 0,
        "block_p95_ms": float(np.percentile(durations, 95)) if durations else 0,
        "block_p99_ms": float(np.percentile(durations, 99)) if durations else 0,
        "blocks_over_40ms": sum(value > 40 for value in durations),
        "clipped_output_samples": clipped_samples,
        "baseline_process_rss_bytes": baseline_rss, "final_process_rss_bytes": info.rss,
        "process_lifetime_peak_working_set_bytes": getattr(info, "peak_wset", None),
        "processing_wall_ms": (time.perf_counter() - total_start) * 1000,
    }
    if args.clean_target:
        target = read_pcm16(args.clean_target)
        report["mixture_si_sdr_db"] = si_sdr(read_pcm16(args.input), target)
        report["extracted_si_sdr_db"] = si_sdr(read_pcm16(args.output), target)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
