"""Opt-in, bounded keyword worker counters; never retain or print audio/text."""

import os
from datetime import datetime, timezone


class WakeWordDiagnostics:
    def __init__(self):
        self.enabled = os.getenv("NEKO_WAKE_WORD_DIAGNOSTICS") == "1"
        self.frames = self.samples = self.hits = self.resets = 0
        self.window_samples = self.nonzero = 0
        self.energy = self.peak = self.max_inference_ms = 0.0
        self.next_report = 0

    def emit(self, event, **fields):
        if not self.enabled:
            return
        try:
            values = " ".join(f"{key}={value}" for key, value in fields.items())
            print(f"[WakeWord] {datetime.now(timezone.utc).isoformat()} event={event} {values}",
                  flush=True)
        except Exception:
            # Diagnostics must not change activation or retire a healthy worker.
            self.enabled = False

    def record(self, frame, epoch, detection, *, restarted, inference_ms):
        if not self.enabled:
            return
        try:
            import numpy as np

            values = np.frombuffer(frame.pcm, dtype="<i2").astype(np.float32) / 32768.0
            self.frames += 1
            self.samples += len(values)
            self.window_samples += len(values)
            self.nonzero += int(np.count_nonzero(values))
            self.energy += float(np.dot(values, values))
            self.peak = max(self.peak, float(np.max(np.abs(values))))
            self.max_inference_ms = max(self.max_inference_ms, inference_ms)
            self.resets += int(restarted)
            if detection is not None:
                self.hits += 1
                self.emit("hit", epoch=epoch, sequence=frame.sequence,
                          start=detection.sample_start, end=detection.sample_end)
            if self.samples >= self.next_report:
                self.emit("progress", epoch=epoch, frames=self.frames,
                          audio_seconds=round(self.samples / 16000, 2),
                          sequence=frame.sequence, resets=self.resets, hits=self.hits,
                          nonzero_ratio=round(self.nonzero / self.window_samples, 4),
                          rms=round((self.energy / self.window_samples) ** 0.5, 6),
                          peak=round(self.peak, 6), max_inference_ms=round(self.max_inference_ms, 2))
                self.next_report = self.samples + 5 * 16000
                self.window_samples = self.nonzero = 0
                self.energy = self.peak = self.max_inference_ms = 0.0
        except Exception:
            self.enabled = False
