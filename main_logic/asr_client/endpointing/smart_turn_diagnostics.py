"""Explicitly opted-in, privacy-safe SmartTurn runtime diagnostics."""

from __future__ import annotations

import asyncio
import json
import math
import os
import queue
import secrets
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, TextIO, TypeAlias


SMART_TURN_DIAGNOSTICS_ENABLED_ENV = "NEKO_SMART_TURN_DIAGNOSTICS"
SMART_TURN_DIAGNOSTICS_PATH_ENV = "NEKO_SMART_TURN_DIAGNOSTICS_PATH"

_DEFAULT_RELATIVE_PATH = Path("data/smart_turn/runtime-diagnostics.jsonl")
_SCHEMA = "neko.smart_turn.runtime_diagnostics.v1"
_ACK_TIMEOUT_SECONDS = 0.05
_ENABLED_VALUES = frozenset({"1", "true", "yes", "on"})

EvaluationReason: TypeAlias = Literal[
    "candidate_pause",
    "periodic_no_vad",
    "strict_retry",
    "unknown",
]
EvaluationOutcome: TypeAlias = Literal[
    "complete",
    "incomplete",
    "stale",
    "unavailable",
    "error",
    "discarded",
    "superseded",
    "unknown",
]
FailureKind: TypeAlias = Literal["unavailable", "runtime_error", "unknown"]
FailureStage: TypeAlias = Literal[
    "vad_load",
    "vad_feed",
    "smart_turn",
    "consumer",
    "unknown",
]

_EVALUATION_REASONS = frozenset({"candidate_pause", "periodic_no_vad", "strict_retry"})
_EVALUATION_OUTCOMES = frozenset(
    {
        "complete",
        "incomplete",
        "stale",
        "unavailable",
        "error",
        "discarded",
        "superseded",
    }
)
_FAILURE_KINDS = frozenset({"unavailable", "runtime_error"})
_FAILURE_STAGES = frozenset({"vad_load", "vad_feed", "smart_turn", "consumer"})


class SmartTurnRuntimeDiagnostics(Protocol):
    """Narrow event contract that cannot accept audio or transcript payloads."""

    @property
    def enabled(self) -> bool:
        raise NotImplementedError

    def candidate(self, *, reason: str) -> None:
        raise NotImplementedError

    def evaluation(
        self,
        *,
        reason: str,
        outcome: str,
        evaluation_ms: int,
        probability: float | None,
        threshold: float | None,
    ) -> None:
        raise NotImplementedError

    def complete(self, *, reason: str) -> None:
        raise NotImplementedError

    def failure(self, *, kind: str, stage: str) -> None:
        raise NotImplementedError

    async def flush(self) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class _WriteCommand:
    line: str


@dataclass(frozen=True, slots=True)
class _FlushCommand:
    completed: threading.Event


@dataclass(frozen=True, slots=True)
class _CloseCommand:
    completed: threading.Event


_WriterCommand: TypeAlias = _WriteCommand | _FlushCommand | _CloseCommand


class _DisabledSmartTurnRuntimeDiagnostics:
    @property
    def enabled(self) -> bool:
        return False

    def candidate(self, *, reason: str) -> None:
        del reason

    def evaluation(
        self,
        *,
        reason: str,
        outcome: str,
        evaluation_ms: int,
        probability: float | None,
        threshold: float | None,
    ) -> None:
        del reason, outcome, evaluation_ms, probability, threshold

    def complete(self, *, reason: str) -> None:
        del reason

    def failure(self, *, kind: str, stage: str) -> None:
        del kind, stage

    async def flush(self) -> None:
        return None

    async def close(self) -> None:
        return None


_DISABLED_DIAGNOSTICS = _DisabledSmartTurnRuntimeDiagnostics()


class _JsonlSmartTurnRuntimeDiagnostics:
    def __init__(self, target: Path) -> None:
        self._target = target
        self._run_id = secrets.token_hex(16)
        self._started_ns = time.monotonic_ns()
        self._sequence = 0
        self._state_lock = threading.Lock()
        self._closed = False
        self._commands: queue.SimpleQueue[_WriterCommand] = queue.SimpleQueue()
        self._writer = threading.Thread(
            target=self._writer_loop,
            name="smart-turn-diagnostics",
            daemon=True,
        )
        self._writer.start()
        self._emit("session_start")

    @property
    def enabled(self) -> bool:
        return True

    def candidate(self, *, reason: str) -> None:
        self._emit(
            "candidate",
            reason=_allowed_value(reason, _EVALUATION_REASONS),
        )

    def evaluation(
        self,
        *,
        reason: str,
        outcome: str,
        evaluation_ms: int,
        probability: float | None,
        threshold: float | None,
    ) -> None:
        try:
            fields: dict[str, str | int | float] = {
                "reason": _allowed_value(reason, _EVALUATION_REASONS),
                "outcome": _allowed_value(outcome, _EVALUATION_OUTCOMES),
                "evaluation_ms": max(0, int(evaluation_ms)),
            }
            bounded_probability = _bounded_probability(probability)
            bounded_threshold = _bounded_probability(threshold)
            if bounded_probability is not None:
                fields["probability"] = bounded_probability
            if bounded_threshold is not None:
                fields["threshold"] = bounded_threshold
            self._emit("evaluation", **fields)
        except Exception:
            return

    def complete(self, *, reason: str) -> None:
        self._emit(
            "complete",
            reason=_allowed_value(reason, _EVALUATION_REASONS),
        )

    def failure(self, *, kind: str, stage: str) -> None:
        self._emit(
            "failure",
            kind=_allowed_value(kind, _FAILURE_KINDS),
            stage=_allowed_value(stage, _FAILURE_STAGES),
        )

    async def flush(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            completed = threading.Event()
            try:
                self._commands.put(_FlushCommand(completed))
            except Exception:
                return
        try:
            await asyncio.to_thread(completed.wait, _ACK_TIMEOUT_SECONDS)
        except Exception:
            return

    async def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            try:
                self._queue_event_locked("session_end", {})
            except Exception:
                # Diagnostics are best-effort and must never block session teardown.
                pass
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

    def _emit(self, event: str, **fields: str | int | float) -> None:
        try:
            with self._state_lock:
                if self._closed:
                    return
                self._queue_event_locked(event, fields)
        except Exception:
            return

    def _queue_event_locked(
        self,
        event: str,
        fields: Mapping[str, str | int | float],
    ) -> None:
        self._sequence += 1
        record: dict[str, str | int | float] = {
            "schema": _SCHEMA,
            "run_id": self._run_id,
            "sequence": self._sequence,
            "elapsed_ms": max(
                0,
                (time.monotonic_ns() - self._started_ns) // 1_000_000,
            ),
            "event": event,
            **fields,
        }
        line = json.dumps(
            record,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        self._commands.put(_WriteCommand(line))

    def _writer_loop(self) -> None:
        stream: TextIO | None = None
        broken = False
        while True:
            command = self._commands.get()
            if isinstance(command, _WriteCommand):
                if broken:
                    continue
                try:
                    if stream is None:
                        self._target.parent.mkdir(parents=True, exist_ok=True)
                        stream = self._target.open(
                            "a",
                            encoding="utf-8",
                            newline="\n",
                        )
                    stream.write(command.line)
                    stream.write("\n")
                    stream.flush()
                except Exception:
                    broken = True
                    _close_stream(stream)
                    stream = None
                continue
            if isinstance(command, _FlushCommand):
                if not broken and stream is not None:
                    try:
                        stream.flush()
                    except Exception:
                        broken = True
                        _close_stream(stream)
                        stream = None
                command.completed.set()
                continue
            if stream is not None:
                try:
                    stream.flush()
                except Exception:
                    pass
                _close_stream(stream)
            command.completed.set()
            return


def create_smart_turn_runtime_diagnostics(
    *,
    environ: Mapping[str, str] | None = None,
    repo_root: Path | None = None,
) -> SmartTurnRuntimeDiagnostics:
    """Create a sink only after explicit opt-in and a confined path check."""

    try:
        environment = os.environ if environ is None else environ
        enabled = environment.get(SMART_TURN_DIAGNOSTICS_ENABLED_ENV, "")
        if enabled.strip().casefold() not in _ENABLED_VALUES:
            return _DISABLED_DIAGNOSTICS

        root = (
            Path(__file__).resolve().parents[3]
            if repo_root is None
            else repo_root.resolve()
        )
        data_root = (root / "data").resolve()
        configured_path = environment.get(
            SMART_TURN_DIAGNOSTICS_PATH_ENV,
            str(_DEFAULT_RELATIVE_PATH),
        ).strip()
        if not configured_path:
            configured_path = str(_DEFAULT_RELATIVE_PATH)
        target = Path(configured_path)
        if not target.is_absolute():
            target = root / target
        target = target.resolve()
        if target == data_root or target.suffix.casefold() != ".jsonl":
            return _DISABLED_DIAGNOSTICS
        try:
            target.relative_to(data_root)
        except ValueError:
            return _DISABLED_DIAGNOSTICS
        return _JsonlSmartTurnRuntimeDiagnostics(target)
    except Exception:
        return _DISABLED_DIAGNOSTICS


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


def _close_stream(stream: TextIO | None) -> None:
    if stream is None:
        return
    try:
        stream.close()
    except Exception:
        return
