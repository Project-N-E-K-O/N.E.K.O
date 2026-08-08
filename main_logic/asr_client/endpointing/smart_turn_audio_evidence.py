"""Explicitly opted-in SmartTurn audio evidence capture."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import queue
import secrets
import threading
import time
import wave
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeAlias


SMART_TURN_AUDIO_EVIDENCE_ENABLED_ENV = "NEKO_SMART_TURN_AUDIO_EVIDENCE"
SMART_TURN_AUDIO_EVIDENCE_DIR_ENV = "NEKO_SMART_TURN_AUDIO_EVIDENCE_DIR"

_DEFAULT_RELATIVE_DIR = Path("data/smart_turn/audio-evidence")
_SCHEMA = "neko.smart_turn.audio_evidence.v1"
_ENABLED_VALUES = frozenset({"1", "true", "yes", "on"})
_SAMPLE_RATE_HZ = 16_000
_SAMPLE_WIDTH_BYTES = 2
_MAX_CAPTURE_SECONDS = 30
_MAX_CAPTURE_BYTES = _SAMPLE_RATE_HZ * _SAMPLE_WIDTH_BYTES * _MAX_CAPTURE_SECONDS
_ACK_TIMEOUT_SECONDS = 0.05
_COMPLETE_REASONS = frozenset({"candidate_pause", "periodic_no_vad", "strict_retry"})

_Identity: TypeAlias = tuple[int, int, int]


class SmartTurnAudioEvidenceRecorder(Protocol):
    """Narrow contract for opt-in local audio capture."""

    @property
    def enabled(self) -> bool: ...

    def accepted_audio(self, *, identity: _Identity, pcm16: bytes) -> None: ...

    def complete(
        self,
        *,
        identity: _Identity,
        reason: str,
        probability: float | None,
        threshold: float | None,
    ) -> None: ...

    def discard(self) -> None: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _WriteTurnCommand:
    wav_path: Path
    index_line: str
    pcm16: bytes


@dataclass(frozen=True, slots=True)
class _CloseCommand:
    completed: threading.Event


_WriterCommand: TypeAlias = _WriteTurnCommand | _CloseCommand


class _DisabledSmartTurnAudioEvidenceRecorder:
    @property
    def enabled(self) -> bool:
        return False

    def accepted_audio(self, *, identity: _Identity, pcm16: bytes) -> None:
        del identity, pcm16

    def complete(
        self,
        *,
        identity: _Identity,
        reason: str,
        probability: float | None,
        threshold: float | None,
    ) -> None:
        del identity, reason, probability, threshold

    def discard(self) -> None:
        return None

    async def close(self) -> None:
        return None


_DISABLED_RECORDER = _DisabledSmartTurnAudioEvidenceRecorder()


class _WavSmartTurnAudioEvidenceRecorder:
    def __init__(self, target_root: Path) -> None:
        self._run_id = secrets.token_hex(16)
        self._run_dir = target_root / self._run_id
        self._started_ns = time.monotonic_ns()
        self._sequence = 0
        self._state_lock = threading.Lock()
        self._closed = False
        self._current_identity: _Identity | None = None
        self._current_pcm = bytearray()
        self._current_truncated_prefix = False
        self._commands: queue.SimpleQueue[_WriterCommand] = queue.SimpleQueue()
        self._writer = threading.Thread(
            target=self._writer_loop,
            name="smart-turn-audio-evidence",
            daemon=True,
        )
        self._writer.start()

    @property
    def enabled(self) -> bool:
        return True

    def accepted_audio(self, *, identity: _Identity, pcm16: bytes) -> None:
        try:
            if not pcm16 or len(pcm16) % _SAMPLE_WIDTH_BYTES:
                return
            with self._state_lock:
                if self._closed:
                    return
                if self._current_identity != identity:
                    self._current_identity = identity
                    self._current_pcm = bytearray()
                    self._current_truncated_prefix = False
                self._current_pcm.extend(pcm16)
                overflow = len(self._current_pcm) - _MAX_CAPTURE_BYTES
                if overflow > 0:
                    trim = overflow + (overflow % _SAMPLE_WIDTH_BYTES)
                    del self._current_pcm[:trim]
                    self._current_truncated_prefix = True
        except Exception:
            return

    def complete(
        self,
        *,
        identity: _Identity,
        reason: str,
        probability: float | None,
        threshold: float | None,
    ) -> None:
        try:
            with self._state_lock:
                if (
                    self._closed
                    or self._current_identity != identity
                    or not self._current_pcm
                ):
                    return
                self._sequence += 1
                sequence = self._sequence
                pcm16 = bytes(self._current_pcm)
                truncated_prefix = self._current_truncated_prefix
                self._current_identity = None
                self._current_pcm = bytearray()
                self._current_truncated_prefix = False

            filename = f"turn-{sequence:04d}.wav"
            wav_path = self._run_dir / filename
            record = {
                "schema": _SCHEMA,
                "run_id": self._run_id,
                "sequence": sequence,
                "elapsed_ms": max(
                    0,
                    (time.monotonic_ns() - self._started_ns) // 1_000_000,
                ),
                "event": "turn_audio",
                "file": filename,
                "sample_rate_hz": _SAMPLE_RATE_HZ,
                "channels": 1,
                "sample_width_bytes": _SAMPLE_WIDTH_BYTES,
                "duration_ms": len(pcm16)
                // (_SAMPLE_RATE_HZ * _SAMPLE_WIDTH_BYTES // 1_000),
                "pcm_sha256": hashlib.sha256(pcm16).hexdigest(),
                "reason": _allowed_value(reason, _COMPLETE_REASONS),
                "truncated_prefix": truncated_prefix,
            }
            bounded_probability = _bounded_probability(probability)
            bounded_threshold = _bounded_probability(threshold)
            if bounded_probability is not None:
                record["probability"] = bounded_probability
            if bounded_threshold is not None:
                record["threshold"] = bounded_threshold
            index_line = json.dumps(
                record,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            self._commands.put(_WriteTurnCommand(wav_path, index_line, pcm16))
        except Exception:
            return

    def discard(self) -> None:
        try:
            with self._state_lock:
                self._current_identity = None
                self._current_pcm = bytearray()
                self._current_truncated_prefix = False
        except Exception:
            return

    async def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            completed = threading.Event()
            try:
                self._commands.put(_CloseCommand(completed))
            except Exception:
                return
        try:
            await asyncio.to_thread(completed.wait, _ACK_TIMEOUT_SECONDS)
        except Exception:
            return

    def _writer_loop(self) -> None:
        index_stream = None
        broken = False
        while True:
            command = self._commands.get()
            if isinstance(command, _WriteTurnCommand):
                if broken:
                    continue
                try:
                    self._run_dir.mkdir(parents=True, exist_ok=True)
                    _write_pcm16_wav(command.wav_path, command.pcm16)
                    if index_stream is None:
                        index_stream = (self._run_dir / "index.jsonl").open(
                            "a",
                            encoding="utf-8",
                            newline="\n",
                        )
                    index_stream.write(command.index_line)
                    index_stream.write("\n")
                    index_stream.flush()
                except Exception:
                    broken = True
                    _close_stream(index_stream)
                    index_stream = None
                continue
            _close_stream(index_stream)
            command.completed.set()
            return


def create_smart_turn_audio_evidence_recorder(
    *,
    environ: Mapping[str, str] | None = None,
    repo_root: Path | None = None,
) -> SmartTurnAudioEvidenceRecorder:
    """Create a local WAV recorder only after explicit opt-in."""

    try:
        environment = os.environ if environ is None else environ
        enabled = environment.get(SMART_TURN_AUDIO_EVIDENCE_ENABLED_ENV, "")
        if enabled.strip().casefold() not in _ENABLED_VALUES:
            return _DISABLED_RECORDER

        root = (
            Path(__file__).resolve().parents[3]
            if repo_root is None
            else repo_root.resolve()
        )
        data_root = (root / "data").resolve()
        configured_dir = environment.get(
            SMART_TURN_AUDIO_EVIDENCE_DIR_ENV,
            str(_DEFAULT_RELATIVE_DIR),
        ).strip()
        if not configured_dir:
            configured_dir = str(_DEFAULT_RELATIVE_DIR)
        target_root = Path(configured_dir)
        if not target_root.is_absolute():
            target_root = root / target_root
        target_root = target_root.resolve()
        if target_root == data_root:
            return _DISABLED_RECORDER
        try:
            target_root.relative_to(data_root)
        except ValueError:
            return _DISABLED_RECORDER
        return _WavSmartTurnAudioEvidenceRecorder(target_root)
    except Exception:
        return _DISABLED_RECORDER


def _write_pcm16_wav(path: Path, pcm16: bytes) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(_SAMPLE_WIDTH_BYTES)
        wav_file.setframerate(_SAMPLE_RATE_HZ)
        wav_file.writeframes(pcm16)


def _allowed_value(value: str, allowed: frozenset[str]) -> str:
    return value if value in allowed else "unknown"


def _bounded_probability(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(numeric) or not 0.0 <= numeric <= 1.0:
        return None
    return numeric


def _close_stream(stream) -> None:
    if stream is None:
        return
    try:
        stream.close()
    except Exception:
        return
