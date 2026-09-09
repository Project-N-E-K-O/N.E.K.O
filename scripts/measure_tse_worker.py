"""Measure a real-time paced, synthetic TSE worker without ASR/TTS or a microphone.

uv run --no-sync python scripts/measure_tse_worker.py --model-dir /models/tse \
    --duration-seconds 1800 --report /scratch/tse-stability.json

The report is a single-worker development result. It cannot certify integrated
NEKO memory, reply latency, acoustic quality, or a target laptop's performance.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import deque
import json
import math
from pathlib import Path
import sys
import threading
import time

import numpy as np
import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main_logic.voice_identity.tse.contracts import BLOCK_SAMPLES, SAMPLE_RATE  # noqa: E402
from main_logic.voice_identity.tse.worker import TseWorker  # noqa: E402


def process_snapshot() -> dict:
    process = psutil.Process()
    children = process.children(recursive=True)
    rss, cpu_seconds, count = 0, 0.0, 0
    for item in [process, *children]:
        try:
            rss += item.memory_info().rss
            cpu = item.cpu_times()
            cpu_seconds += cpu.user + cpu.system
            count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    own = process.memory_info()
    return {"monotonic_seconds": time.monotonic(), "process_tree_rss_bytes": rss,
            "process_tree_cpu_seconds": cpu_seconds, "process_count": count,
            "own_lifetime_peak_working_set_bytes": getattr(own, "peak_wset", None),
            "thread_count": process.num_threads()}


def write_json(path: Path, report: dict) -> None:
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


async def run_measurement(asset_dir: Path, duration_seconds: float, report_path: Path, *,
                          progress_seconds: float = 60) -> dict:
    if any(not math.isfinite(value) or value <= 0 for value in (duration_seconds, progress_seconds)):
        raise ValueError("measurement duration and progress period must be positive")
    if report_path.exists():
        raise ValueError("report already exists; choose a fresh output path")
    await asyncio.to_thread(report_path.parent.mkdir, parents=True, exist_ok=True)
    baseline = await asyncio.to_thread(process_snapshot)
    reference = np.random.default_rng(43).normal(0, 0.1, 192).astype(np.float32)
    instance = TseWorker(asset_dir, reference)
    reference.fill(0)
    timings, samples, progress = [], [], []
    input_samples = output_samples = high_pending = 0
    high_age = 0.0
    pending = deque()
    failure = None
    starting = time.monotonic()
    loaded_ms = None
    stream_start = None

    async def submit(pcm, position):
        submitted = time.monotonic()
        chunks = await instance.push(pcm, start_sample=position)
        return (time.monotonic() - submitted) * 1000, chunks

    def accept(chunks):
        nonlocal output_samples
        for chunk in chunks:
            if chunk.start_sample != output_samples or not np.isfinite(chunk.pcm).all():
                raise RuntimeError("worker emitted a gap, overlap, or nonfinite PCM")
            output_samples = chunk.end_sample

    try:
        await instance.start(timeout=5)
        loaded_ms = (time.monotonic() - starting) * 1000
        stream_start = time.monotonic()
        next_snapshot = stream_start
        next_progress = stream_start + progress_seconds
        count = math.ceil(duration_seconds * SAMPLE_RATE / BLOCK_SAMPLES)
        block_axis = np.arange(BLOCK_SAMPLES, dtype=np.float64)
        rng = np.random.default_rng(45)
        for index in range(count):
            target = stream_start + index * BLOCK_SAMPLES / SAMPLE_RATE
            if target > time.monotonic():
                await asyncio.sleep(target - time.monotonic())
            while pending and pending[0].done():
                elapsed_ms, chunks = await pending.popleft()
                timings.append(elapsed_ms)
                accept(chunks)
            axis = (block_axis + input_samples) / SAMPLE_RATE
            envelope = 0.6 + 0.4 * np.sin(2 * np.pi * 0.73 * axis)
            pcm = (0.06 * np.sin(2 * np.pi * 183 * axis) * envelope
                   + 0.035 * np.sin(2 * np.pi * 271 * axis)
                   + rng.normal(0, 0.005, BLOCK_SAMPLES)).astype(np.float32)
            pending.append(asyncio.create_task(submit(pcm, input_samples)))
            input_samples += BLOCK_SAMPLES
            await asyncio.sleep(0)
            high_pending = max(high_pending, instance.pending_samples)
            high_age = max(high_age, instance.oldest_age_ms)
            if len(pending) > 6:
                raise RuntimeError("more than 240 ms of unresolved submissions")
            now = time.monotonic()
            if now >= next_snapshot:
                samples.append(await asyncio.to_thread(process_snapshot))
                next_snapshot = now + 1
            if now >= next_progress:
                item = {"elapsed_seconds": now - stream_start, "blocks_completed": len(timings),
                        "output_samples": output_samples, "pending_samples": instance.pending_samples,
                        "oldest_age_ms": instance.oldest_age_ms,
                        "process_tree_rss_bytes": samples[-1]["process_tree_rss_bytes"],
                        "worker_round_trip_p95_ms": float(np.percentile(timings, 95)) if timings else None}
                progress.append(item)
                print(json.dumps({"tse_stability_progress": item}), flush=True)
                next_progress = now + progress_seconds
        while pending:
            elapsed_ms, chunks = await pending.popleft()
            timings.append(elapsed_ms)
            accept(chunks)
        accept(await instance.flush())
        if input_samples != output_samples:
            raise RuntimeError("worker did not flush the exact real input length")
    except Exception as exc:
        failure = {"type": type(exc).__name__, "reason": str(exc), "worker_reason": instance.failure_reason}
    finally:
        stopped = await instance.close(timeout=2)
        for task in pending:
            if not task.done():
                task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    finished = await asyncio.to_thread(process_snapshot)
    snapshots = [baseline, *samples, finished]
    runtime = max(1e-9, finished["monotonic_seconds"] - baseline["monotonic_seconds"])
    report = {
        "scope": "standalone CPU TSE worker; synthetic audio; no ASR/TTS/UI; not P5 acceptance",
        "requested_duration_seconds": duration_seconds,
        "measured_stream_seconds": 0 if stream_start is None else time.monotonic() - stream_start,
        "input_audio_seconds": input_samples / SAMPLE_RATE,
        "input_samples": input_samples, "output_samples": output_samples,
        "sample_rate": SAMPLE_RATE, "block_samples": BLOCK_SAMPLES,
        "startup_ms": loaded_ms, "completed_blocks": len(timings),
        "worker_round_trip_median_ms": float(np.median(timings)) if timings else None,
        "worker_round_trip_p95_ms": float(np.percentile(timings, 95)) if timings else None,
        "worker_round_trip_p99_ms": float(np.percentile(timings, 99)) if timings else None,
        "worker_round_trip_max_ms": max(timings) if timings else None,
        "blocks_over_40ms": sum(value > 40 for value in timings),
        "pending_samples_high_water": high_pending, "observed_oldest_age_max_ms": high_age,
        "baseline_process_tree_rss_bytes": baseline["process_tree_rss_bytes"],
        "sampled_process_tree_rss_peak_bytes": max(item["process_tree_rss_bytes"] for item in snapshots),
        "own_lifetime_peak_working_set_bytes": finished["own_lifetime_peak_working_set_bytes"],
        "final_process_tree_rss_bytes": finished["process_tree_rss_bytes"],
        "process_tree_cpu_seconds": finished["process_tree_cpu_seconds"] - baseline["process_tree_cpu_seconds"],
        "average_cpu_core_equivalents": (finished["process_tree_cpu_seconds"] - baseline["process_tree_cpu_seconds"]) / runtime,
        "worker_stopped": stopped, "native_thread_still_alive": not instance.stopped,
        "tse_threads_remaining": [item.name for item in threading.enumerate() if item.name == "neko-tse-stream"],
        "failure": failure, "memory_time_series": samples, "progress": progress,
    }
    await asyncio.to_thread(write_json, report_path, report)
    print(json.dumps({"tse_stability_complete": {key: value for key, value in report.items()
                                               if key not in ("memory_time_series", "progress")}}), flush=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--duration-seconds", type=float, default=1800)
    parser.add_argument("--progress-seconds", type=float, default=60)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(run_measurement(args.model_dir, args.duration_seconds, args.report,
                                        progress_seconds=args.progress_seconds))
    if result["failure"] is not None or not result["worker_stopped"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
