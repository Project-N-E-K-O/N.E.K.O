"""Measure local WAVs through the production activation scheduler/controller.

This deliberately supplies no speaker activity and uses a ready stub scorer to
isolate wake detection. Output is accepted by a local sink, never sent to ASR.
Each case has a fresh session; active periods are not KWS-observed. The real
idle policy can return long cases to standby and resume observation.
Use --paced for real-time arrivals. Unpaced runs measure backlog throughput,
not real-time latency. This is not microphone, UI, transport or game-load QA.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import multiprocessing
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from main_logic.voice_identity_service.activation_runtime import (
    VoiceSessionActivationRuntime, VoiceSessionActivationRuntimeConfig,
)
from main_logic.voice_identity_service.activation_scoring import ActivationScoreStatus
from main_logic.voice_input.activation import ActivationGeneration, AudioFrame, OutputCommit
from main_logic.voice_input.wake_word.sherpa_backend import SherpaWakeWordConfig, SherpaWakeWordDetector
from config.voice_wake_word import DEFAULT_WAKE_WORD_KEYWORDS
from scripts.wake_word.evaluate_wake_word import read_fixture


class _ReadyScorer:
    async def prepare(self):
        return ActivationScoreStatus.READY

    async def score(self, *args, **kwargs):
        raise AssertionError("Wake-only evaluation must not submit speaker work")

    async def close(self):
        pass


def _cpu(process):
    value = process.cpu_times()
    return value.user + value.system


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * fraction)]


class _MeasuredDetector:
    """Observe calls without replacing the production batching scheduler."""

    def __init__(self, delegate):
        self.delegate = delegate
        self.calls = []
        self.attempted_calls = 0
        self.hits = []
        self.started = 0.0
        self.child = None
        self.worker_cpu_start = None
        self.peak_worker_rss = 0
        self.next_rss_sample = 0.0
        self.metric_errors = []

    @property
    def inference_timeout_seconds(self):
        return self.delegate.inference_timeout_seconds

    async def prepare(self):
        import psutil

        await self.delegate.prepare()
        process = getattr(self.delegate, "_process", None)
        if process is not None:
            try:
                self.child = psutil.Process(process.pid)
                self.worker_cpu_start = _cpu(self.child)
                self.peak_worker_rss = self.child.memory_info().rss
            except psutil.Error:
                self.metric_errors.append("worker_prepare_metrics_unavailable")
                self.child = None

    async def _call(self, frames, epoch, *, batch):
        import psutil

        started = time.monotonic()
        self.attempted_calls += 1
        if batch:
            result = await self.delegate.feed_batch(frames, epoch)
            consumed, detection = result.consumed_frames, result.detection
        else:
            result = await self.delegate.feed(frames[0], epoch)
            consumed, detection = 1, result
        ended = time.monotonic()
        self.calls.append(dict(epoch=epoch, frames=len(frames), samples=sum(len(f.pcm) // 2 for f in frames),
                               consumed_frames=consumed, ipc_seconds=ended - started,
                               oldest_capture_age_seconds=started - frames[0].captured_at))
        if self.child is not None and ended >= self.next_rss_sample:
            try:
                self.peak_worker_rss = max(self.peak_worker_rss, self.child.memory_info().rss)
            except psutil.Error:
                if "worker_rss_sample_unavailable" not in self.metric_errors:
                    self.metric_errors.append("worker_rss_sample_unavailable")
            self.next_rss_sample = ended + 1.0
        if detection is not None:
            self.hits.append(dict(epoch=epoch, sample_start=detection.sample_start, sample_end=detection.sample_end,
                                  processed_end_sample=frames[consumed - 1].sample_end,
                                  returned_seconds=ended - self.started))
        return result

    async def feed(self, frame, epoch):
        return await self._call((frame,), epoch, batch=False)

    async def feed_batch(self, frames, epoch):
        return await self._call(frames, epoch, batch=True)

    async def close(self):
        await self.delegate.close()


async def measure_case(pcm, detector, *, batching=True, paced=True, frame_samples=(160,)):
    """Use real runtime/controller; detector injection permits bounded tests."""
    import psutil

    if not pcm or len(pcm) % 2:
        raise ValueError("Nonempty PCM16 is required")
    if not frame_samples or any(type(n) is not int or not 1 <= n <= 16000 for n in frame_samples):
        raise ValueError("Frame samples must be integers within 1..16000")
    if len(pcm) // 2 / min(frame_samples) > 200_000:
        raise ValueError("Evaluation exceeds the 200000-frame observation budget")
    measured = _MeasuredDetector(detector)
    generation = ActivationGeneration("runtime-evaluation", 1, 1, 1, 1, "fixture")
    statuses, delivered = [], []
    sink_hash = hashlib.sha256()

    async def output(frame):
        delivered.append((frame.sample_start, frame.sample_end))
        sink_hash.update(frame.pcm)
        return OutputCommit.LOCAL_ACCEPTED

    def status(decision):
        statuses.append(dict(reason=decision.reason, state=decision.state.value,
                             elapsed_seconds=time.monotonic() - measured.started))

    runtime = VoiceSessionActivationRuntime(generation, _ReadyScorer(), output,
        wake_detector=measured, status_callback=status,
        config=VoiceSessionActivationRuntimeConfig(wake_batching_enabled=batching))
    main = psutil.Process()
    prepare_start = time.monotonic()
    try:
        await runtime.prepare()
        if runtime.state.value != "waiting":
            raise RuntimeError(f"Production runtime did not prepare: {runtime.state.value}")
        prepare_seconds = time.monotonic() - prepare_start
        measured.started = time.monotonic()
        main_before = _cpu(main)
        peak_main_rss = main.memory_info().rss
        late, offset, sequence = [], 0, 0
        while offset < len(pcm):
            count = frame_samples[sequence % len(frame_samples)]
            chunk = pcm[offset:offset + count * 2]
            start, end = offset // 2, (offset + len(chunk)) // 2
            due = measured.started + end / 16000
            if paced:
                await asyncio.sleep(max(0.0, due - time.monotonic()))
                late.append(max(0.0, time.monotonic() - due))
            await runtime.feed(AudioFrame(sequence, start, end,
                measured.started + start / 16000, 16000, chunk, generation), voice_activity=False)
            await asyncio.sleep(0)
            if sequence % 100 == 0:
                peak_main_rss = max(peak_main_rss, main.memory_info().rss)
            offset += len(chunk)
            sequence += 1
        # Await the actual scheduler, including the finite-tail deadline. Do not
        # call the detector directly or manually force-flush its production queue.
        deadline = time.monotonic() + 5.0
        while True:
            tasks = [task for task in (runtime._wake_task, runtime._output_task)
                     if task is not None and not task.done()]
            if not tasks:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Production runtime failed to drain within evaluation budget")
            done, pending = await asyncio.wait(tasks, timeout=remaining)
            if pending:
                raise TimeoutError("Production runtime failed to drain within evaluation budget")
            for task in done:
                task.result()
            await asyncio.sleep(0)
        try:
            worker_cpu = (_cpu(measured.child) - measured.worker_cpu_start
                          if measured.child is not None else None)
        except psutil.Error:
            worker_cpu = None  # Failure retirement may already have removed the process.
            measured.metric_errors.append("worker_final_cpu_unavailable")
        main_cpu = _cpu(main) - main_before
        activations = [item for item in statuses if item["reason"] == "wake_word_detected"]
        return dict(duration_seconds=len(pcm) / 32000, pcm_sha256=hashlib.sha256(pcm).hexdigest(),
            batching=batching, paced=paced, frame_samples=list(frame_samples), input_frames=sequence,
            prepare_seconds=prepare_seconds, wall_seconds=time.monotonic() - measured.started,
            main_cpu_seconds=main_cpu, worker_cpu_seconds=worker_cpu,
            total_cpu_seconds=main_cpu + worker_cpu if worker_cpu is not None else None,
            sampled_peak_main_rss_bytes=peak_main_rss, sampled_peak_worker_rss_bytes=measured.peak_worker_rss,
            ipc_calls=measured.attempted_calls, completed_ipc_calls=len(measured.calls),
            batch_frame_counts=[c["frames"] for c in measured.calls],
            observed_standby_epochs=sorted({c["epoch"] for c in measured.calls}),
            lifecycle_statuses=statuses, metric_errors=measured.metric_errors,
            processed_frames=sum(c["consumed_frames"] for c in measured.calls),
            ipc_p50_seconds=_percentile([c["ipc_seconds"] for c in measured.calls], .5),
            ipc_p95_seconds=_percentile([c["ipc_seconds"] for c in measured.calls], .95),
            input_scheduling_late_p95_seconds=_percentile(late, .95),
            model_hits=measured.hits, accepted_activations=activations,
            local_output_ranges=delivered, local_output_pcm_sha256=sink_hash.hexdigest(),
            state_before_close=runtime.state.value,
            failure_reasons=[s["reason"] for s in statuses if s["state"] == "unavailable"],
            runtime_info=dict(getattr(detector, "runtime_info", None) or {}))
    finally:
        await runtime.close()


async def evaluate_runtime(args):
    pcm = await asyncio.to_thread(read_fixture, args.wav)
    results = []
    for repeat in range(args.repeats):
        # Alternate ordering to reduce consistent cold-cache/order bias.
        for batching in ((False, True) if repeat % 2 == 0 else (True, False)):
            detector = SherpaWakeWordDetector(SherpaWakeWordConfig(str(args.model_dir), DEFAULT_WAKE_WORD_KEYWORDS))
            result = await measure_case(pcm, detector, batching=batching,
                paced=args.paced, frame_samples=tuple(args.frame_samples))
            result["repeat"] = repeat
            results.append(result)
    return dict(schema_version=1, source=args.source, cases=results,
        limits="Production runtime and controller, wake-only scorer stub, local sink only. "
               "No ASR upload or real microphone/UI/game-load validation. Model events and accepted "
               "activation are separate. Active periods are not KWS-observed; production idle policy "
               "may return a long fixture to standby. All observed epochs are reported. No false-hit/hour "
               "or occurrence recall claim. IPC latency is not end-to-end wake latency. "
               "CPU excludes prepare/close; RSS is sampled, not an OS peak. Unpaced timestamps are synthetic.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--wav", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--paced", action="store_true")
    parser.add_argument("--frame-samples", type=int, nargs="+", default=[160])
    parser.add_argument("--repeats", type=int, choices=range(1, 6), default=2)
    parser.add_argument("--source", choices=("recording", "synthetic"), required=True)
    args = parser.parse_args()
    report = asyncio.run(evaluate_runtime(args))
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Report: {args.output}")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
