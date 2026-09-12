"""Bounded, disposable process boundary for local streaming keyword detection."""

from __future__ import annotations

import asyncio
import math
import multiprocessing
import threading
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from multiprocessing.connection import Connection
from pathlib import Path
from types import MappingProxyType

from main_logic.voice_input.activation.contracts import AudioFrame, WakeWordDetection
from .diagnostics import WakeWordDiagnostics


SUPPORTED_RUNTIME_VERSION = "1.13.8+neko.kws2"


class WakeWordBackendError(RuntimeError):
    """The detector is unavailable; this is never a positive detection."""


@dataclass(frozen=True, slots=True)
class SherpaWakeWordConfig:
    """Explicit model assets and phonetic keyword lines, supplied by configuration."""

    model_dir: str
    keywords: tuple[str, ...]
    keyword_threshold: float = 0.25
    keyword_score: float = 1.0
    num_threads: int = 1
    prepare_timeout: float = 30.0
    inference_timeout: float = 2.0
    max_frame_samples: int = 16000
    max_active_paths: int = 8

    def __post_init__(self) -> None:
        if not self.keywords or len(self.keywords) > 32:
            raise ValueError("WAKE_WORD_KEYWORDS_INVALID")
        for line in self.keywords:
            parts = line.split()
            if (len(line) > 512 or len(parts) < 2 or not parts[-1].startswith("@")
                    or len(parts[-1]) < 2 or any(c in line for c in "\r\n/")):
                raise ValueError("WAKE_WORD_KEYWORDS_INVALID")
        if not math.isfinite(self.keyword_threshold) or not 0 < self.keyword_threshold <= 1:
            raise ValueError("WAKE_WORD_THRESHOLD_INVALID")
        if not math.isfinite(self.keyword_score) or not 0 < self.keyword_score <= 10:
            raise ValueError("WAKE_WORD_SCORE_INVALID")
        if type(self.max_active_paths) is not int or not 1 <= self.max_active_paths <= 16:
            raise ValueError("WAKE_WORD_MAX_ACTIVE_PATHS_INVALID")
        if not 1 <= self.num_threads <= 4 or not 1 <= self.max_frame_samples <= 32000:
            raise ValueError("WAKE_WORD_BUDGET_INVALID")
        if not all(math.isfinite(v) and 0 < v <= 120
                   for v in (self.prepare_timeout, self.inference_timeout)):
            raise ValueError("WAKE_WORD_TIMEOUT_INVALID")


def model_files(model_dir: str) -> dict[str, str]:
    """Paths for the pinned bilingual 2025-12-20 chunk-8 export."""
    root = Path(model_dir)
    suffix = "epoch-13-avg-2-chunk-8-left-64"
    return {
        "encoder": str(root / f"encoder-{suffix}.int8.onnx"),
        "decoder": str(root / f"decoder-{suffix}.onnx"),
        "joiner": str(root / f"joiner-{suffix}.int8.onnx"),
        "tokens": str(root / "tokens.txt"),
    }


class _StreamingSpotter:
    """Worker-owned decoder and affine mapping to original PCM sample positions."""

    def __init__(self, config: SherpaWakeWordConfig) -> None:
        import sherpa_onnx

        self.runtime_version = getattr(sherpa_onnx, "__version__", None)
        self.native_version = getattr(sherpa_onnx, "version", None)
        if (self.runtime_version != SUPPORTED_RUNTIME_VERSION
                or self.native_version != SUPPORTED_RUNTIME_VERSION):
            raise WakeWordBackendError("WAKE_WORD_RUNTIME_FIX_REQUIRED")
        paths = model_files(config.model_dir)
        if not all(Path(path).is_file() for path in paths.values()):
            raise WakeWordBackendError("WAKE_WORD_MODEL_MISSING")
        vocabulary = {line.split()[0] for line in Path(paths["tokens"]).read_text(
            encoding="utf-8").splitlines() if line.strip()}
        if any(token not in vocabulary for line in config.keywords
               for token in line.split()[:-1]):
            raise WakeWordBackendError("WAKE_WORD_TOKEN_UNKNOWN")
        # This version requires an actual keyword file even for inline streams.
        # Construction reads it eagerly; nothing private is persisted afterward.
        with tempfile.TemporaryDirectory(prefix="neko-kws-") as temporary:
            keyword_file = Path(temporary) / "keywords.txt"
            keyword_file.write_text("\n".join(config.keywords) + "\n", encoding="utf-8")
            self.spotter = sherpa_onnx.KeywordSpotter(
                **paths, keywords_file=str(keyword_file), sample_rate=16000,
                num_threads=config.num_threads, keywords_threshold=config.keyword_threshold,
                keywords_score=config.keyword_score, num_trailing_blanks=1, provider="cpu",
                max_active_paths=config.max_active_paths,
            )
        self.keywords = "/".join(config.keywords)
        self.labels = {line.split()[-1][1:] for line in config.keywords}
        self.stream = None
        self.identity = None
        self.base = 0
        self.end = 0
        self.detected = False

    def feed(self, frame: AudioFrame, epoch: int) -> WakeWordDetection | None:
        import numpy as np

        identity = (frame.generation, epoch)
        if identity != self.identity or frame.sample_start != self.end or self.detected:
            self.stream = self.spotter.create_stream()
            self.identity = identity
            self.base = frame.sample_start
            self.detected = False
        self.end = frame.sample_end
        # Recreate after a hit if it was rejected and the runtime keeps feeding.
        # Accepted hits stop feeding until the runtime's next standby epoch.
        self.stream.accept_waveform(16000, np.frombuffer(frame.pcm, dtype="<i2").astype(
            np.float32) / 32768.0)
        while self.spotter.is_ready(self.stream):
            self.spotter.decode_stream(self.stream)
            # Fetch exactly once: 1.13.8 consumes duplicate results on retrieval,
            # so separate get_result()/timestamps() calls lose the timestamps.
            raw_result = self.spotter.keyword_spotter.get_result(self.stream)
            keyword = raw_result.keyword.strip()
            if not keyword:
                continue
            timestamps = raw_result.timestamps
            # Requires the KWS reset fix: decoder resets must preserve cumulative
            # frame_offset so token times remain relative to this stream's base.
            # Token times are approximate onset positions, not cut boundaries.
            if (keyword not in self.labels or not timestamps
                    or not all(math.isfinite(t) and t >= 0 for t in timestamps)
                    or any(a > b for a, b in zip(timestamps, timestamps[1:]))):
                raise WakeWordBackendError("WAKE_WORD_RESULT_INVALID")
            start = self.base + math.floor(timestamps[0] * 16000)
            end = self.base + math.ceil((timestamps[-1] + 0.04) * 16000)
            if start < self.base or start >= self.end or end > self.end:
                raise WakeWordBackendError("WAKE_WORD_RESULT_RANGE_INVALID")
            self.detected = True
            return WakeWordDetection(keyword=keyword, generation=frame.generation,
                                     epoch=epoch, sample_start=start, sample_end=end)
        return None


def _runtime_details(config: SherpaWakeWordConfig, version: str, native_version: str) -> dict:
    """Configuration supplied to the native constructor, plus its loaded version."""
    return dict(runtime_version=version, native_version=native_version,
                max_active_paths=config.max_active_paths,
                keyword_threshold=config.keyword_threshold, keyword_score=config.keyword_score,
                num_threads=config.num_threads, num_trailing_blanks=1,
                sample_rate=16000, provider="cpu")


def _worker(connection: Connection, config: SherpaWakeWordConfig) -> None:
    """No raw audio/text logging; native crashes stay inside this process."""
    diagnostics = WakeWordDiagnostics()
    try:
        spotter = _StreamingSpotter(config)
        runtime_info = _runtime_details(config, spotter.runtime_version, spotter.native_version)
        diagnostics.emit("ready", **runtime_info)
        connection.send((True, runtime_info))
        while True:
            frame, epoch = connection.recv()
            previous_stream = spotter.stream
            started = time.perf_counter()
            detection = spotter.feed(frame, epoch)
            diagnostics.record(frame, epoch, detection,
                               restarted=spotter.stream is not previous_stream,
                               inference_ms=(time.perf_counter() - started) * 1000)
            connection.send((True, detection))
    except (EOFError, BrokenPipeError):
        pass
    except Exception:
        diagnostics.emit("failed")
        try:
            connection.send((False, "WAKE_WORD_WORKER_FAILED"))
        except (OSError, EOFError):
            pass
    finally:
        connection.close()


class SherpaWakeWordDetector:
    """Single-flight async facade; close/cancellation kill native inference."""

    def __init__(self, config: SherpaWakeWordConfig) -> None:
        self.config = config
        self._closed = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._process = None
        self._connection = None
        self._busy = False
        self._ready = False
        self._runtime_info = None

    @property
    def runtime_info(self) -> Mapping | None:
        """Verified worker metadata while ready; unavailable before prepare/after close."""
        return self._runtime_info

    def _launch(self) -> None:
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(target=_worker, args=(child, self.config), daemon=True)
        try:
            process.start()
        except BaseException:
            parent.close()
            raise
        finally:
            child.close()
        with self._lifecycle_lock:
            self._connection, self._process = parent, process
        # close may run while spawn is in flight; the spawning thread owns cleanup.
        if self._closed.is_set():
            self._stop()
            raise WakeWordBackendError("WAKE_WORD_CLOSED")

    def _exchange(self, request: tuple | None, timeout: float):
        connection = self._connection
        if self._closed.is_set() or connection is None:
            raise WakeWordBackendError("WAKE_WORD_CLOSED")
        if request is not None:
            connection.send(request)
        if not connection.poll(timeout):
            raise WakeWordBackendError("WAKE_WORD_TIMEOUT")
        ok, result = connection.recv()
        if not ok:
            raise WakeWordBackendError("WAKE_WORD_WORKER_FAILED")
        if self._closed.is_set():
            raise WakeWordBackendError("WAKE_WORD_CLOSED")
        return result

    def _stop(self) -> None:
        with self._lifecycle_lock:
            process, self._process = self._process, None
            connection, self._connection = self._connection, None
        if process is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=0.5)
            if process.is_alive():
                process.kill()
                process.join(timeout=0.5)
            if not process.is_alive():
                process.close()
        if connection is not None:
            connection.close()

    async def prepare(self) -> None:
        if self._closed.is_set():
            raise WakeWordBackendError("WAKE_WORD_CLOSED")
        if self._busy:
            raise WakeWordBackendError("WAKE_WORD_CONCURRENT_CALL")
        if self._ready:
            return
        self._busy = True
        try:
            deadline = asyncio.get_running_loop().time() + self.config.prepare_timeout
            await asyncio.wait_for(asyncio.to_thread(self._launch), self.config.prepare_timeout)
            remaining = max(0.001, deadline - asyncio.get_running_loop().time())
            runtime_info = await asyncio.wait_for(asyncio.to_thread(self._exchange, None,
                                                  remaining), remaining)
            if self._closed.is_set():
                raise WakeWordBackendError("WAKE_WORD_CLOSED")
            if (not isinstance(runtime_info, dict)
                    or runtime_info != _runtime_details(
                        self.config, SUPPORTED_RUNTIME_VERSION, SUPPORTED_RUNTIME_VERSION)):
                raise WakeWordBackendError("WAKE_WORD_RUNTIME_INFO_INVALID")
            self._runtime_info = MappingProxyType(dict(runtime_info))
            self._ready = True
        except BaseException:
            await self.close()
            raise
        finally:
            self._busy = False

    async def feed(self, frame: AudioFrame, epoch: int) -> WakeWordDetection | None:
        if self._closed.is_set() or not self._ready:
            raise WakeWordBackendError("WAKE_WORD_NOT_READY")
        if self._busy:
            raise WakeWordBackendError("WAKE_WORD_CONCURRENT_CALL")
        if frame.sample_rate != 16000 or frame.sample_end - frame.sample_start > self.config.max_frame_samples:
            raise WakeWordBackendError("WAKE_WORD_FRAME_INVALID")
        self._busy = True
        try:
            result = await asyncio.wait_for(asyncio.to_thread(self._exchange,
                (replace(frame, context=None), epoch), self.config.inference_timeout), self.config.inference_timeout)
            if self._closed.is_set():
                raise WakeWordBackendError("WAKE_WORD_CLOSED")
            return result
        except BaseException:
            await self.close()
            raise
        finally:
            self._busy = False

    async def close(self) -> None:
        self._closed.set()
        self._ready = False
        self._runtime_info = None
        await asyncio.to_thread(self._stop)
