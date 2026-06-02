from __future__ import annotations

import asyncio
import atexit
import ctypes
import hashlib
import json
import logging
import os
import queue
import shutil
import subprocess
import threading
import types as _types
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
import re
import sys
import time
from typing import Any, Awaitable, Callable, Protocol
from uuid import uuid4

try:
    import psutil
except ImportError:  # pragma: no cover - psutil is available in the project runtime.
    psutil = None

from .models import (
    DATA_SOURCE_MEMORY_READER,
    GalgameConfig,
    MENU_PREFIX_RE as _MENU_PREFIX_RE,
    sanitize_choice,
    sanitize_save_context,
)
from .reader import normalize_text

from ._types import (
    DetectedGameProcess,
    MEMORY_READER_DEFAULT_ENGINE,
    MemoryReaderProcessTarget,
    MemoryReaderRuntime,
    MemoryReaderTickResult,
    ParsedTextractorLine,
    TextractorProcessHandle,
)
from ._textractor_paths import (
    TEXTRACTOR_EXECUTABLE,
    resolve_textractor_path,
)
from ._process_detection import (
    _default_process_inventory,
    _default_process_scanner,
    _engine_from_text,
    _scan_processes,
    _KIRIKIRI_DIR_CACHE,
    _KIRIKIRI_DIR_CACHE_LOCK,
)
from ._textractor_handle import (
    _AsyncioTextractorHandle,
    _decode_textractor_stdout_line,
    _default_process_factory,
    _is_event_loop_binding_error,
    _select_hook_codes_for_engine,
    _textractor_hook_command,
)
from . import _win32_job_objects  # noqa: F401  # registers atexit handler
from ._win32_job_objects import _create_kill_on_close_job_for_process

MEMORY_READER_VERSION = "0.1.0"
MEMORY_READER_BRIDGE_VERSION = f"memory-reader-{MEMORY_READER_VERSION}"
MEMORY_READER_GAME_ID_PREFIX = "mem-"
MEMORY_READER_UNKNOWN_SCENE = "mem:unknown_scene"
MEMORY_READER_ROUTE_ID = ""
MEMORY_READER_MAX_HOOK_CACHE = 256
_MEMORY_LINE_ID_MAX_COLLISION_SUFFIX = 10000
_SPEAKER_QUOTE_RE = re.compile(r"^\s*([^「」:：]{1,40})[「『](.+)[」』]\s*$")
_SPEAKER_COLON_RE = re.compile(r"^\s*([^:：]{1,40})[:：]\s*(.+\S)\s*$")
_ZERO_WIDTH_CHARS = ("\u200b", "\u200c", "\u200d", "\ufeff")
_LOGGER = logging.getLogger(__name__)


def is_windows_platform() -> bool:
    from plugin.plugins.galgame_plugin.capture_platform import is_windows  # noqa: PLC0415

    return is_windows()


def utc_now_iso(now: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if now is None else now))


def compute_memory_reader_game_id(process_name: str) -> str:
    digest = hashlib.sha256(process_name.encode("utf-8")).hexdigest()[:16]
    return f"{MEMORY_READER_GAME_ID_PREFIX}{digest}"


def _coerce_choice_lines(lines: list[str]) -> list[str]:
    if len(lines) < 2:
        return []
    choices: list[str] = []
    for line in lines:
        match = _MENU_PREFIX_RE.match(line)
        if match is None:
            return []
        text = match.group(1).strip()
        if not text:
            return []
        choices.append(text)
    return choices


def _split_speaker_text(raw_text: str) -> tuple[str, str]:
    match = _SPEAKER_QUOTE_RE.match(raw_text)
    if match is not None:
        return match.group(1).strip(), match.group(2).strip()
    match = _SPEAKER_COLON_RE.match(raw_text)
    if match is not None:
        return match.group(1).strip(), match.group(2).strip()
    return "", raw_text.strip()


class MemoryReaderBridgeWriter:
    def __init__(
        self,
        *,
        bridge_root: Path,
        version: str = MEMORY_READER_BRIDGE_VERSION,
        time_fn: Callable[[], float] | None = None,
    ) -> None:
        self._bridge_root = bridge_root
        self._version = version
        self._time_fn = time_fn or time.time
        self._game_id = ""
        self._session_id = ""
        self._process_name = ""
        self._pid = 0
        self._exe_path = ""
        self._detection_reason = ""
        self._engine = MEMORY_READER_DEFAULT_ENGINE
        self._started_at = ""
        self._last_seq = 0
        self._last_event_ts = ""
        self._last_text_seq = 0
        self._last_text_ts = ""
        self._state = self._initial_state("")
        self._text_to_line_id: dict[str, str] = {}
        self._line_id_owner: dict[str, str] = {}
        self._io_lock = threading.RLock()

    @property
    def bridge_root(self) -> Path:
        return self._bridge_root

    @property
    def game_id(self) -> str:
        return self._game_id

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def engine(self) -> str:
        return self._engine

    @property
    def last_seq(self) -> int:
        return self._last_seq

    @property
    def last_event_ts(self) -> str:
        return self._last_event_ts

    @property
    def last_text_seq(self) -> int:
        return self._last_text_seq

    @property
    def last_text_ts(self) -> str:
        return self._last_text_ts

    @property
    def pid(self) -> int:
        return self._pid

    def update_engine(self, engine: str) -> bool:
        normalized = engine or MEMORY_READER_DEFAULT_ENGINE
        if normalized == self._engine or not self._session_id:
            return False
        self._engine = normalized
        self._write_session_snapshot()
        return True

    def start_session(self, process: DetectedGameProcess) -> None:
        started_at = utc_now_iso(self._time_fn())
        self._game_id = compute_memory_reader_game_id(process.name)
        self._session_id = f"mem-{uuid4()}"
        self._process_name = process.name
        self._pid = process.pid
        self._exe_path = process.exe_path
        self._detection_reason = process.detection_reason
        self._engine = process.engine or MEMORY_READER_DEFAULT_ENGINE
        self._started_at = started_at
        self._last_seq = 0
        self._last_event_ts = started_at
        self._last_text_seq = 0
        self._last_text_ts = ""
        self._state = self._initial_state(started_at)
        self._text_to_line_id.clear()
        self._line_id_owner.clear()
        self._bridge_dir().mkdir(parents=True, exist_ok=True)
        self._events_path().write_bytes(b"")
        self._write_session_snapshot()
        self._append_event(
            "session_started",
            {
                "game_title": process.name,
                "engine": self._engine,
                "locale": "",
                "started_at": started_at,
                "scene_id": self._state["scene_id"],
                "line_id": self._state["line_id"],
                "route_id": self._state["route_id"],
                "is_menu_open": self._state["is_menu_open"],
                "speaker": self._state["speaker"],
                "text": self._state["text"],
                "choices": self._state["choices"],
                "save_context": self._state["save_context"],
            },
            ts=started_at,
        )

    def emit_line(self, raw_text: str, *, ts: str) -> bool:
        cleaned = raw_text.strip()
        if not cleaned or not self._session_id:
            return False
        speaker, text = _split_speaker_text(cleaned)
        if not text:
            return False
        line_id = self._line_id_for_text(text)
        self._state = {
            **self._state,
            "speaker": speaker,
            "text": text,
            "choices": [],
            "scene_id": MEMORY_READER_UNKNOWN_SCENE,
            "line_id": line_id,
            "route_id": MEMORY_READER_ROUTE_ID,
            "is_menu_open": False,
            "save_context": sanitize_save_context(self._state.get("save_context")),
            "ts": ts,
        }
        self._append_event(
            "line_changed",
            {
                "source": DATA_SOURCE_MEMORY_READER,
                "speaker": speaker,
                "text": text,
                "line_id": line_id,
                "line_id_source": "text_hash",
                "scene_id": self._state["scene_id"],
                "route_id": self._state["route_id"],
            },
            ts=ts,
            text_event=True,
        )
        return True

    def emit_choices(self, choices: list[str], *, ts: str) -> bool:
        if not choices or not self._session_id:
            return False
        line_id = str(self._state.get("line_id") or "")
        if not line_id:
            return False
        payload_choices = [
            sanitize_choice(
                {
                    "choice_id": f"{line_id}#choice{index}",
                    "text": text,
                    "index": index,
                    "enabled": True,
                }
            )
            for index, text in enumerate(choices)
        ]
        self._state = {
            **self._state,
            "choices": payload_choices,
            "is_menu_open": True,
            "ts": ts,
        }
        self._append_event(
            "choices_shown",
            {
                "line_id": line_id,
                "scene_id": self._state["scene_id"],
                "route_id": self._state["route_id"],
                "choices": payload_choices,
            },
            ts=ts,
            text_event=True,
        )
        return True

    def emit_heartbeat(self, *, ts: str) -> bool:
        if not self._session_id:
            return False
        self._append_event(
            "heartbeat",
            {
                "state_ts": str(self._state.get("ts") or ""),
                "idle_seconds": 0,
                "scene_id": self._state["scene_id"],
                "line_id": self._state["line_id"],
                "route_id": self._state["route_id"],
            },
            ts=ts,
            update_snapshot=False,
        )
        return True

    def emit_error(self, message: str, *, ts: str, details: dict[str, Any] | None = None) -> bool:
        if not self._session_id:
            return False
        payload: dict[str, Any] = {
            "message": message,
            "source": DATA_SOURCE_MEMORY_READER,
            "scene_id": self._state["scene_id"],
            "line_id": self._state["line_id"],
            "route_id": self._state["route_id"],
        }
        if details:
            payload["details"] = dict(details)
        self._append_event("error", payload, ts=ts, update_snapshot=False)
        return True

    def end_session(self, *, ts: str) -> bool:
        if not self._session_id:
            return False
        payload = {
            "scene_id": self._state["scene_id"],
            "line_id": self._state["line_id"],
            "route_id": self._state["route_id"],
        }
        self._append_event("session_ended", payload, ts=ts, update_snapshot=False)
        self._write_session_snapshot()
        self._text_to_line_id.clear()
        self._line_id_owner.clear()
        self._session_id = ""
        self._process_name = ""
        self._pid = 0
        self._exe_path = ""
        self._engine = ""
        self._detection_reason = ""
        self._started_at = ""
        self._last_seq = 0
        self._last_event_ts = ""
        return True

    def runtime(self) -> MemoryReaderRuntime:
        return MemoryReaderRuntime(
            enabled=True,
            status="active" if self._session_id else "idle",
            detail="",
            process_name=self._process_name,
            pid=self._pid,
            exe_path=self._exe_path,
            engine=self._engine,
            detection_reason=self._detection_reason,
            game_id=self._game_id,
            session_id=self._session_id,
            last_seq=self._last_seq,
            last_event_ts=self._last_event_ts,
            last_text_seq=self._last_text_seq,
            last_text_ts=self._last_text_ts,
        )

    def _initial_state(self, ts: str) -> dict[str, Any]:
        return {
            "speaker": "",
            "text": "",
            "choices": [],
            "scene_id": MEMORY_READER_UNKNOWN_SCENE,
            "line_id": "",
            "route_id": MEMORY_READER_ROUTE_ID,
            "is_menu_open": False,
            "save_context": {
                "kind": "unknown",
                "slot_id": "",
                "display_name": "",
            },
            "ts": ts,
        }

    def _bridge_dir(self) -> Path:
        return self._bridge_root / self._game_id

    def _session_path(self) -> Path:
        return self._bridge_dir() / "session.json"

    def _events_path(self) -> Path:
        return self._bridge_dir() / "events.jsonl"

    def _session_snapshot(self) -> dict[str, Any]:
        return {
            "protocol_version": 1,
            "game_id": self._game_id,
            "game_title": self._process_name,
            "engine": self._engine,
            "session_id": self._session_id,
            "started_at": self._started_at,
            "last_seq": self._last_seq,
            "locale": "",
            "bridge_sdk_version": self._version,
            "metadata": {
                "source": DATA_SOURCE_MEMORY_READER,
                "game_process_name": self._process_name,
                "game_pid": self._pid,
                "game_exe_path": str(getattr(self, "_exe_path", "") or ""),
                "detection_reason": str(getattr(self, "_detection_reason", "") or ""),
            },
            "state": {
                "speaker": str(self._state.get("speaker") or ""),
                "text": str(self._state.get("text") or ""),
                "choices": [sanitize_choice(item) for item in self._state.get("choices", [])],
                "scene_id": str(self._state.get("scene_id") or MEMORY_READER_UNKNOWN_SCENE),
                "line_id": str(self._state.get("line_id") or ""),
                "route_id": str(self._state.get("route_id") or MEMORY_READER_ROUTE_ID),
                "is_menu_open": bool(self._state.get("is_menu_open", False)),
                "save_context": sanitize_save_context(self._state.get("save_context")),
                "ts": str(self._state.get("ts") or self._started_at),
            },
        }

    def _write_session_snapshot(self) -> None:
        with self._io_lock:
            self._bridge_dir().mkdir(parents=True, exist_ok=True)
            tmp_path = self._session_path().with_suffix(".json.tmp")
            payload = json.dumps(
                self._session_snapshot(),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            with tmp_path.open("wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self._session_path())

    def _append_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        ts: str,
        update_snapshot: bool = True,
        text_event: bool = False,
    ) -> int:
        with self._io_lock:
            self._last_seq += 1
            self._last_event_ts = ts
            if text_event:
                self._last_text_seq = self._last_seq
                self._last_text_ts = ts
            event = {
                "protocol_version": 1,
                "seq": self._last_seq,
                "ts": ts,
                "type": event_type,
                "session_id": self._session_id,
                "game_id": self._game_id,
                "payload": payload,
            }
            with self._events_path().open("ab") as handle:
                handle.write(
                    json.dumps(
                        event,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    + b"\n"
                )
                handle.flush()
            if update_snapshot:
                self._write_session_snapshot()
            return self._last_seq

    def _line_id_for_text(self, text: str) -> str:
        normalized = normalize_text(text)
        cached = self._text_to_line_id.get(normalized)
        if cached is not None:
            return cached
        digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()
        widths = list(range(12, len(digest) + 1, 4))
        if widths[-1] != len(digest):
            widths.append(len(digest))
        for width in widths:
            candidate = f"mem:{digest[:width]}"
            owner = self._line_id_owner.get(candidate)
            if owner in {None, normalized}:
                self._line_id_owner[candidate] = normalized
                self._text_to_line_id[normalized] = candidate
                return candidate
        for suffix in range(1, _MEMORY_LINE_ID_MAX_COLLISION_SUFFIX + 1):
            candidate = f"mem:{digest}#{suffix}"
            owner = self._line_id_owner.get(candidate)
            if owner in {None, normalized}:
                self._line_id_owner[candidate] = normalized
                self._text_to_line_id[normalized] = candidate
                return candidate
        raise RuntimeError(
            "memory line_id collision limit exceeded "
            f"after {_MEMORY_LINE_ID_MAX_COLLISION_SUFFIX} suffix attempts"
        )


class MemoryReaderManager:
    def __init__(
        self,
        *,
        logger,
        config: GalgameConfig,
        process_factory: Callable[[str], Awaitable[TextractorProcessHandle]] | None = None,
        process_scanner: Callable[[], list[DetectedGameProcess]] | None = None,
        process_inventory_scanner: Callable[[], list[DetectedGameProcess]] | None = None,
        time_fn: Callable[[], float] | None = None,
        platform_fn: Callable[[], bool] | None = None,
        writer: MemoryReaderBridgeWriter | None = None,
    ) -> None:
        self._logger = logger
        self._config = config
        if process_factory is None:
            async def _process_factory_with_logger(path: str) -> TextractorProcessHandle:
                return await _default_process_factory(path, logger=self._logger)

            self._process_factory = _process_factory_with_logger
        else:
            self._process_factory = process_factory
        self._process_scanner = process_scanner or _default_process_scanner
        self._process_inventory_scanner = process_inventory_scanner or _default_process_inventory
        self._time_fn = time_fn or time.time
        self._platform_fn = platform_fn or is_windows_platform
        self._writer = writer or MemoryReaderBridgeWriter(
            bridge_root=config.bridge_root,
            time_fn=self._time_fn,
        )
        self._runtime = MemoryReaderRuntime(enabled=config.memory_reader_enabled)
        self._process: TextractorProcessHandle | None = None
        self._attached_process: DetectedGameProcess | None = None
        self._manual_target = MemoryReaderProcessTarget()
        self._target_selection_detail = "auto_candidate_scan"
        self._last_process_inventory: list[DetectedGameProcess] = []
        self._target_restart_requested = False
        self._attach_started_at = 0.0
        self._backoff_until = 0.0
        self._restart_attempts = 0
        self._last_hook_text: OrderedDict[str, str] = OrderedDict()
        self._last_hook_text_lock = threading.Lock()
        self._config_update_lock = threading.RLock()
        self._last_heartbeat_at = 0.0
        self._last_no_text_warning_at = 0.0
        self._last_hook_code_count = 0
        self._last_hook_code_detail = "hook_codes_none"
        self._skip_process_pids: set[int] = set()
        self._consecutive_attach_timeouts = 0
        self._max_attach_timeouts = 3

    def update_config(self, config: GalgameConfig) -> None:
        with self._config_update_lock:
            bridge_root_changed = self._writer.bridge_root != config.bridge_root
            self._config = config
            self._runtime.enabled = config.memory_reader_enabled
            self._skip_process_pids.clear()
            self._consecutive_attach_timeouts = 0
            if not bridge_root_changed:
                return
            self._writer = MemoryReaderBridgeWriter(
                bridge_root=config.bridge_root,
                time_fn=self._time_fn,
            )
            self._runtime = MemoryReaderRuntime(
                enabled=config.memory_reader_enabled,
                status="idle",
                detail="bridge_root_changed",
            )
            self._attached_process = None
            self._target_restart_requested = False
            self._attach_started_at = 0.0
            self._backoff_until = 0.0
            self._last_heartbeat_at = 0.0
            self._last_no_text_warning_at = 0.0
            self._last_hook_code_count = 0
            self._last_hook_code_detail = "hook_codes_none"
            with self._last_hook_text_lock:
                self._last_hook_text.clear()

    def update_process_target(self, target: dict[str, Any] | None) -> None:
        old_target = self._manual_target.to_dict()
        self._manual_target = MemoryReaderProcessTarget.from_dict(target)
        self._target_selection_detail = (
            "manual_target_active" if self._manual_target.is_manual() else "auto_candidate_scan"
        )
        target_changed = old_target != self._manual_target.to_dict()
        if target_changed:
            self._skip_process_pids.clear()
            self._consecutive_attach_timeouts = 0
        if target_changed and (
            self._attached_process is not None or self._process is not None
        ):
            self._target_restart_requested = True

    def current_process_target(self) -> dict[str, Any]:
        return self._manual_target.to_dict()

    def list_processes_snapshot(self, *, include_unknown: bool = True) -> dict[str, Any]:
        scanner = self._process_inventory_scanner if include_unknown else self._process_scanner
        processes = scanner()
        self._last_process_inventory = list(processes)
        manual = self._manual_target
        return {
            "target_selection_mode": "manual" if manual.is_manual() else "auto",
            "manual_target": manual.to_dict(),
            "candidate_count": len(processes),
            "processes": [
                item.to_dict(
                    is_attached=(
                        self._attached_process is not None
                        and item.pid == self._attached_process.pid
                    ),
                    is_manual_target=(
                        manual.is_manual()
                        and (manual.matches_exact(item) or manual.matches_signature(item))
                    ),
                )
                for item in processes
            ],
        }

    def resolve_manual_process_target(
        self,
        *,
        process_key: str = "",
        pid: int = 0,
        exe_path: str = "",
        process_name: str = "",
    ) -> dict[str, Any]:
        normalized_key = str(process_key or "").strip()
        normalized_exe = os.path.normcase(str(exe_path or "").strip())
        normalized_name = str(process_name or "").strip().lower()
        try:
            normalized_pid = int(pid or 0)
        except (TypeError, ValueError):
            normalized_pid = 0
        if not any([normalized_key, normalized_pid, normalized_exe, normalized_name]):
            raise ValueError("process_key, pid, exe_path, or process_name is required")
        processes = self._process_inventory_scanner()
        self._last_process_inventory = list(processes)
        for candidate in processes:
            if normalized_key and candidate.process_key == normalized_key:
                return MemoryReaderProcessTarget(
                    mode="manual",
                    process_key=candidate.process_key,
                    process_name=candidate.name,
                    exe_path=candidate.exe_path,
                    pid=candidate.pid,
                    engine=candidate.engine,
                    detection_reason=candidate.detection_reason,
                    create_time=candidate.create_time,
                    selected_at=utc_now_iso(self._time_fn()),
                ).to_dict()
        for candidate in processes:
            if normalized_pid > 0 and candidate.pid == normalized_pid:
                return MemoryReaderProcessTarget(
                    mode="manual",
                    process_key=candidate.process_key,
                    process_name=candidate.name,
                    exe_path=candidate.exe_path,
                    pid=candidate.pid,
                    engine=candidate.engine,
                    detection_reason=candidate.detection_reason,
                    create_time=candidate.create_time,
                    selected_at=utc_now_iso(self._time_fn()),
                ).to_dict()
        for candidate in processes:
            candidate_exe = os.path.normcase(candidate.exe_path.strip())
            candidate_name = candidate.name.strip().lower()
            if normalized_exe and candidate_exe != normalized_exe:
                continue
            if normalized_name and candidate_name != normalized_name:
                continue
            if normalized_exe or normalized_name:
                return MemoryReaderProcessTarget(
                    mode="manual",
                    process_key=candidate.process_key,
                    process_name=candidate.name,
                    exe_path=candidate.exe_path,
                    pid=candidate.pid,
                    engine=candidate.engine,
                    detection_reason=candidate.detection_reason,
                    create_time=candidate.create_time,
                    selected_at=utc_now_iso(self._time_fn()),
                ).to_dict()
        raise ValueError("process target not found")

    def _select_target_process(
        self,
        processes: list[DetectedGameProcess],
    ) -> tuple[DetectedGameProcess | None, str]:
        if self._manual_target.is_manual():
            for candidate in processes:
                if self._manual_target.matches_exact(candidate):
                    resolved = self._manual_target.resolved_for(candidate)
                    resolved.selected_at = self._manual_target.selected_at
                    self._manual_target = resolved
                    return candidate, "manual_target_exact"
            for candidate in processes:
                if self._manual_target.matches_signature(candidate):
                    resolved = self._manual_target.resolved_for(candidate)
                    resolved.selected_at = self._manual_target.selected_at
                    self._manual_target = resolved
                    return candidate, "manual_target_rebound"
            return None, "manual_target_unavailable"
        if not self._config.memory_reader_auto_detect:
            return None, "manual_pid_unimplemented"
        if not processes:
            return None, "no_detected_game_process"
        return processes[0], processes[0].detection_reason or "auto_candidate_scan"

    def _current_runtime(
        self,
        *,
        status: str,
        detail: str,
        process: DetectedGameProcess | None = None,
    ) -> MemoryReaderRuntime:
        attached = process or self._attached_process
        return MemoryReaderRuntime(
            enabled=True,
            status=status,
            detail=detail,
            target_selection_mode="manual" if self._manual_target.is_manual() else "auto",
            target_selection_detail=self._target_selection_detail,
            process_name=attached.name if attached else "",
            pid=attached.pid if attached else 0,
            exe_path=attached.exe_path if attached else "",
            engine=(
                self._writer.engine
                if self._writer.session_id
                else (attached.engine if attached else MEMORY_READER_DEFAULT_ENGINE)
            )
            or MEMORY_READER_DEFAULT_ENGINE,
            detection_reason=attached.detection_reason if attached else "",
            hook_code_count=self._last_hook_code_count,
            hook_code_detail=self._last_hook_code_detail,
            game_id=self._writer.game_id,
            session_id=self._writer.session_id,
            last_seq=self._writer.last_seq,
            last_event_ts=self._writer.last_event_ts,
            last_text_seq=self._writer.last_text_seq,
            last_text_ts=self._writer.last_text_ts,
        )

    async def shutdown(self) -> None:
        await self._stop_textractor()

    async def tick(self, *, bridge_sdk_available: bool) -> MemoryReaderTickResult:
        now = self._time_fn()
        result = MemoryReaderTickResult(runtime=self._runtime.to_dict())
        if not self._config.memory_reader_enabled:
            self._runtime = MemoryReaderRuntime(enabled=False, status="disabled", detail="disabled_by_config")
            await self._stop_textractor()
            result.runtime = self._runtime.to_dict()
            return result
        if not self._platform_fn():
            await self._stop_textractor()
            self._runtime = MemoryReaderRuntime(
                enabled=True,
                status="idle",
                detail="unsupported_platform",
            )
            result.warnings.append("memory_reader is Windows-only")
            result.runtime = self._runtime.to_dict()
            return result
        textractor_path = await asyncio.to_thread(
            resolve_textractor_path,
            self._config.memory_reader_textractor_path,
            install_target_dir_raw=self._config.memory_reader_install_target_dir,
        )
        if not textractor_path:
            await self._stop_textractor()
            self._runtime = MemoryReaderRuntime(
                enabled=True,
                status="idle",
                detail="invalid_textractor_path",
            )
            result.warnings.append("memory_reader TextractorCLI.exe is invalid or missing")
            result.runtime = self._runtime.to_dict()
            return result
        if not self._config.memory_reader_auto_detect and not self._manual_target.is_manual():
            await self._stop_textractor()
            self._target_selection_detail = "manual_pid_unimplemented"
            self._runtime = self._current_runtime(
                status="idle",
                detail="manual_pid_unimplemented",
            )
            result.warnings.append("memory_reader auto_detect=false is not implemented in this release")
            result.runtime = self._runtime.to_dict()
            return result
        if bridge_sdk_available:
            await self._stop_textractor()
            self._runtime = MemoryReaderRuntime(
                enabled=True,
                status="idle",
                detail="bridge_sdk_available",
                process_name=self._runtime.process_name,
                pid=self._runtime.pid,
                engine=self._runtime.engine,
                game_id=self._runtime.game_id,
                session_id=self._runtime.session_id,
                last_seq=self._runtime.last_seq,
                last_event_ts=self._runtime.last_event_ts,
                last_text_seq=self._runtime.last_text_seq,
                last_text_ts=self._runtime.last_text_ts,
            )
            result.runtime = self._runtime.to_dict()
            return result
        if self._target_restart_requested:
            self._target_restart_requested = False
            if self._writer.end_session(ts=utc_now_iso(now)):
                result.should_rescan = True
            await self._stop_textractor()
            self._runtime = self._current_runtime(
                status="scanning",
                detail="target_changed",
            )
        if self._backoff_until and now < self._backoff_until:
            self._runtime.status = "backoff"
            self._runtime.detail = "waiting_before_restart"
            result.runtime = self._runtime.to_dict()
            return result

        if self._attached_process is None:
            self._runtime.status = "scanning"
            self._runtime.detail = "scanning_processes"
        scanner = self._process_inventory_scanner if self._manual_target.is_manual() else self._process_scanner
        processes = await asyncio.to_thread(scanner)
        processes = [item for item in processes if item.pid not in self._skip_process_pids]
        self._last_process_inventory = list(processes)
        if self._attached_process is None and processes:
            preview = ", ".join(f"{item.name}({item.pid},{item.engine})" for item in processes[:5])
            self._logger.debug("memory_reader detected candidate processes: {}", preview)
        if self._attached_process is not None:
            process_lookup = {item.pid: item for item in processes}
            attached = process_lookup.get(self._attached_process.pid)
            if attached is None:
                previous = self._attached_process
                self._logger.info(
                    "memory_reader detached because process disappeared: {}({})",
                    previous.name,
                    previous.pid,
                )
                if self._writer.end_session(ts=utc_now_iso(now)):
                    result.should_rescan = True
                await self._stop_textractor()
                self._attached_process = None
            self._attached_process = attached

        if self._process is not None and self._process.poll() is not None:
            crash_warning = await self._handle_textractor_crash(now)
            if crash_warning:
                result.warnings.append(crash_warning)
                if self._runtime.status == "error" and self._writer.emit_error(
                    crash_warning,
                    ts=utc_now_iso(now),
                ):
                    result.should_rescan = True
                result.runtime = self._runtime.to_dict()
                return result

        if self._attached_process is None:
            target, selection_detail = self._select_target_process(processes)
            self._target_selection_detail = selection_detail
            if target is None:
                self._runtime = self._current_runtime(
                    status="idle",
                    detail=selection_detail,
                )
                result.runtime = self._runtime.to_dict()
                return result
            if (
                not self._writer.session_id
                or self._writer.game_id != compute_memory_reader_game_id(target.name)
                or self._writer.pid != target.pid
            ):
                self._writer.start_session(target)
                result.should_rescan = True
            self._attached_process = target
            self._last_heartbeat_at = now
            self._last_no_text_warning_at = 0.0
            self._runtime.status = "starting"
            self._runtime.detail = "starting_textractor"
            hook_codes, hook_code_detail = _select_hook_codes_for_engine(
                self._config,
                target.engine,
            )
            self._last_hook_code_count = len(hook_codes)
            self._last_hook_code_detail = hook_code_detail
            self._logger.info(
                "memory_reader injection codes selected (count={}, engine={}, detail={})",
                len(hook_codes),
                target.engine,
                hook_code_detail,
            )
            if not hook_codes and hook_code_detail == "hook_codes_none":
                self._logger.info(
                    "memory_reader no hook codes for engine={}; staying idle, OCR will handle",
                    target.engine,
                )
                self._skip_process_pids.add(target.pid)
                self._attached_process = None
                self._runtime = self._current_runtime(
                    status="idle",
                    detail="no_hook_codes_available",
                    process=target,
                )
                result.runtime = self._runtime.to_dict()
                return result
            await self._ensure_textractor_started(textractor_path)
            try:
                if self._process is None:
                    raise RuntimeError("textractor process is unavailable")
                self._logger.info(
                    "memory_reader attaching Textractor to {}({}) engine={}",
                    target.name,
                    target.pid,
                    target.engine,
                )
                await self._process.write(f"attach -P{target.pid}\n")
                hook_codes, hook_code_detail = _select_hook_codes_for_engine(
                    self._config,
                    target.engine,
                )
                self._last_hook_code_count = len(hook_codes)
                self._last_hook_code_detail = hook_code_detail
                self._logger.info(
                    "memory_reader injection codes selected (count={}, engine={}, detail={})",
                    len(hook_codes),
                    target.engine,
                    hook_code_detail,
                )
                if hook_codes:
                    self._logger.info(
                        "memory_reader sending {} hook code(s) for {}({})",
                        len(hook_codes),
                        target.name,
                        target.pid,
                    )
                    for code in hook_codes:
                        hook_command = _textractor_hook_command(code, target.pid)
                        if hook_command:
                            await self._process.write(f"{hook_command}\n")
                elif hook_code_detail.startswith("hook_codes_skipped"):
                    self._logger.info(
                        "memory_reader skipped hook code injection for {}({}) engine={} reason={}",
                        target.name,
                        target.pid,
                        target.engine,
                        hook_code_detail,
                    )
            except Exception as exc:
                self._runtime = self._current_runtime(
                    status="backoff",
                    detail="attach_command_failed",
                    process=target,
                )
                self._backoff_until = now + 5.0
                result.warnings.append(f"memory_reader attach failed: {exc}")
                if self._writer.emit_error(f"attach failed: {exc}", ts=utc_now_iso(now)):
                    result.should_rescan = True
                result.runtime = self._runtime.to_dict()
                return result
            self._attach_started_at = now
            self._runtime = self._current_runtime(
                status="attaching",
                detail="waiting_for_attach_confirmation",
                process=target,
            )

        try:
            parsed_lines, log_lines, parse_warnings = await self._drain_stdout()
        except RuntimeError as exc:
            if not _is_event_loop_binding_error(exc):
                raise
            self._logger.warning(
                "memory_reader detected event-loop-bound Textractor handle; restarting: {}",
                exc,
            )
            result.warnings.append(
                "memory_reader Textractor handle was bound to a different event loop; restarting"
            )
            self._runtime.status = "backoff"
            self._runtime.detail = "event_loop_mismatch"
            self._backoff_until = now + 2.0
            await self._stop_textractor()
            self._attached_process = None
            result.runtime = MemoryReaderRuntime(
                enabled=True,
                status="backoff",
                detail="event_loop_mismatch",
                game_id=self._writer.game_id,
                session_id=self._writer.session_id,
                last_seq=self._writer.last_seq,
                last_event_ts=self._writer.last_event_ts,
                last_text_seq=self._writer.last_text_seq,
                last_text_ts=self._writer.last_text_ts,
            ).to_dict()
            return result
        result.warnings.extend(parse_warnings)
        if parse_warnings and not parsed_lines and not log_lines:
            self._runtime.detail = "textractor_stdout_parse_failed"
        engine_override = self._engine_from_logs(log_lines)
        if engine_override and self._writer.update_engine(engine_override):
            result.should_rescan = True
        if self._attached_process is not None and self._runtime.status == "attaching":
            if any(line.pid == self._attached_process.pid for line in parsed_lines) or log_lines:
                self._restart_attempts = 0
                self._consecutive_attach_timeouts = 0
                self._runtime.status = "active"
                self._runtime.detail = "attached" if parsed_lines else "attached_no_text_yet"
                self._logger.info(
                    "memory_reader attach confirmed for {}({}); parsed_lines={} log_lines={}",
                    self._attached_process.name,
                    self._attached_process.pid,
                    len(parsed_lines),
                    len(log_lines),
                )
        if self._runtime.status == "attaching" and now - self._attach_started_at > 5.0:
            self._consecutive_attach_timeouts += 1
            self._logger.warning(
                "memory_reader attach timeout ({}/{}) for {}({})",
                self._consecutive_attach_timeouts,
                self._max_attach_timeouts,
                self._attached_process.name if self._attached_process else "",
                self._attached_process.pid if self._attached_process else 0,
            )
            if self._consecutive_attach_timeouts >= self._max_attach_timeouts:
                if self._attached_process is not None:
                    self._skip_process_pids.add(self._attached_process.pid)
                message = "memory_reader attach confirmation timed out too many times; giving up"
                self._logger.warning(message)
                result.warnings.append(message)
                if self._writer.emit_error(message, ts=utc_now_iso(now)):
                    result.should_rescan = True
                self._runtime.status = "idle"
                self._runtime.detail = "attach_timeout_limit_reached"
                await self._stop_textractor()
                result.runtime = self._runtime.to_dict()
                return result
            message = "memory_reader attach confirmation timed out"
            self._logger.warning(
                "memory_reader attach confirmation timed out for {}({})",
                self._attached_process.name if self._attached_process else "",
                self._attached_process.pid if self._attached_process else 0,
            )
            result.warnings.append(message)
            if self._writer.emit_error(message, ts=utc_now_iso(now)):
                result.should_rescan = True
            self._runtime.status = "backoff"
            self._runtime.detail = "attach_timeout"
            self._backoff_until = now + 5.0
            await self._stop_textractor()
            self._attached_process = None
            result.runtime = self._runtime.to_dict()
            return result

        emitted = False
        if self._attached_process is not None and parsed_lines:
            emitted = self._consume_parsed_lines(
                [line for line in parsed_lines if line.pid == self._attached_process.pid],
                ts=utc_now_iso(now),
            )
        if emitted:
            result.should_rescan = True
            self._last_heartbeat_at = now
            self._consecutive_attach_timeouts = 0
            self._last_no_text_warning_at = 0.0
            self._runtime.detail = "receiving_text"
        elif self._runtime.status == "active":
            if self._writer.last_text_seq > 0:
                self._runtime.detail = "attached_idle_after_text"
            else:
                self._runtime.detail = "attached_no_text_yet"
            if now - self._last_heartbeat_at >= float(
                self._config.memory_reader_poll_interval_seconds
            ):
                if self._writer.emit_heartbeat(ts=utc_now_iso(now)):
                    result.should_rescan = True
                    self._last_heartbeat_at = now
            if self._writer.last_text_seq <= 0:
                if now - self._attach_started_at >= 3.0 and now - self._last_no_text_warning_at >= 10.0:
                    self._last_no_text_warning_at = now
                    self._logger.warning(
                        "memory_reader is attached to {}({}) but no dialogue text has been captured yet",
                        self._attached_process.name if self._attached_process else "",
                        self._attached_process.pid if self._attached_process else 0,
                    )

        self._runtime = self._current_runtime(
            status=self._runtime.status,
            detail=self._runtime.detail,
        )
        result.runtime = self._runtime.to_dict()
        return result

    async def _ensure_textractor_started(self, textractor_path: str) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        self._logger.info(
            "memory_reader starting Textractor: {}",
            textractor_path,
        )
        self._process = await self._process_factory(textractor_path)

    async def _handle_textractor_crash(self, now: float) -> str:
        self._restart_attempts += 1
        self._logger.warning("memory_reader detected Textractor crash; restart_attempt={}", self._restart_attempts)
        await self._stop_textractor()
        if self._restart_attempts > 3:
            self._runtime.status = "error"
            self._runtime.detail = "textractor_crash_limit_exceeded"
            return "memory_reader Textractor crashed too many times"
        self._runtime.status = "backoff"
        self._runtime.detail = "textractor_crashed"
        self._backoff_until = now + 5.0
        return "memory_reader Textractor crashed; scheduling restart"

    async def _stop_textractor(self) -> None:
        attached_process = self._attached_process
        if self._process is None:
            self._attached_process = None
            return
        try:
            if attached_process is not None:
                self._logger.info(
                    "memory_reader stopping Textractor for {}({})",
                    attached_process.name,
                    attached_process.pid,
                )
                try:
                    await self._process.write(f"detach -P{attached_process.pid}\n")
                except Exception as exc:
                    self._logger.warning(
                        "memory_reader Textractor detach command failed: {}",
                        exc,
                    )
            await self._process.terminate()
            await self._process.wait(timeout=1.0)
        finally:
            self._process = None
            self._attached_process = None
            self._attach_started_at = 0.0
            self._last_heartbeat_at = 0.0
            self._last_no_text_warning_at = 0.0
            self._last_hook_code_count = 0
            self._last_hook_code_detail = "hook_codes_none"
            with self._last_hook_text_lock:
                self._last_hook_text.clear()

    async def _drain_stdout(self) -> tuple[list[ParsedTextractorLine], list[str], list[str]]:
        parsed: list[ParsedTextractorLine] = []
        logs: list[str] = []
        warnings: list[str] = []
        if self._process is None:
            return parsed, logs, warnings
        for _ in range(64):
            line = await self._process.readline(timeout=0.01)
            if line is None:
                break
            if line == "":
                break
            if not line.startswith("["):
                logs.append(line)
                continue
            parsed_line, error = self._parse_textractor_line(line)
            if error:
                warnings.append(error)
                continue
            if parsed_line is None:
                continue
            with self._last_hook_text_lock:
                previous = self._last_hook_text.get(parsed_line.hook_id)
                if previous == parsed_line.text:
                    self._last_hook_text.move_to_end(parsed_line.hook_id)
                    continue
                self._last_hook_text[parsed_line.hook_id] = parsed_line.text
                self._last_hook_text.move_to_end(parsed_line.hook_id)
                if len(self._last_hook_text) > MEMORY_READER_MAX_HOOK_CACHE:
                    self._last_hook_text.popitem(last=False)
            parsed.append(parsed_line)
        for line in logs[:8]:
            print(f"memory_reader Textractor log: {line}")
        for warning in warnings[:8]:
            print(f"memory_reader Textractor warning: {warning}")
        if parsed:
            preview = " | ".join(
                f"{item.pid}:{item.hook_addr}:{normalize_text(item.text)[:80]}" for item in parsed[:4]
            )
            print(f"memory_reader parsed Textractor lines: {preview}")
        return parsed, logs, warnings

    @staticmethod
    def _parse_textractor_line(raw_line: str) -> tuple[ParsedTextractorLine | None, str]:
        close = raw_line.find("]")
        if close <= 1:
            return None, f"memory_reader failed to parse Textractor line: {raw_line}"
        metadata = raw_line[1:close]
        text = raw_line[close + 1 :].lstrip()
        parts = metadata.split(":")
        if len(parts) < 4:
            return None, f"memory_reader failed to parse Textractor metadata: {raw_line}"
        try:
            pid = int(parts[0])
        except ValueError:
            return None, f"memory_reader invalid Textractor pid: {raw_line}"
        return (
            ParsedTextractorLine(
                pid=pid,
                hook_addr=parts[1],
                ctx=parts[2],
                sub_ctx=parts[3],
                text=text,
            ),
            "",
        )

    @staticmethod
    def _engine_from_logs(lines: list[str]) -> str:
        for line in lines:
            engine = _engine_from_text(line)
            if engine:
                return engine
        return ""

    def _consume_parsed_lines(self, lines: list[ParsedTextractorLine], *, ts: str) -> bool:
        texts: list[str] = []
        seen: set[str] = set()
        for item in lines:
            cleaned = normalize_text(item.text)
            if not cleaned:
                continue
            if cleaned in seen:
                continue
            seen.add(cleaned)
            texts.append(cleaned)
        if not texts:
            return False
        choices = _coerce_choice_lines(texts)
        if choices and self._writer.emit_choices(choices, ts=ts):
            return True
        emitted = False
        for text in texts:
            emitted = self._writer.emit_line(text, ts=ts) or emitted
        return emitted


# Pre-split, all memory_reader symbols lived in this one module. After the
# split, tests that monkeypatch ``memory_reader.<name>`` must keep affecting the
# call sites in the submodules where the name now actually lives (process
# scanning resolves ``psutil`` in ``_process_detection``; the Textractor
# handle resolves ``subprocess`` in ``_textractor_handle``; Win32 job setup
# resolves ``ctypes`` in ``_win32_job_objects``). This proxy reroutes the writes
# so the old test-time semantics survive the split unchanged.
_PROXY_TO_PROCESS_DETECTION = frozenset({"psutil"})
_PROXY_TO_TEXTRACTOR_HANDLE = frozenset({"subprocess"})
_PROXY_TO_WIN32_JOB_OBJECTS = frozenset({"ctypes"})


class _ShimModule(_types.ModuleType):
    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)
        if name in _PROXY_TO_PROCESS_DETECTION:
            from . import _process_detection

            setattr(_process_detection, name, value)
        if name in _PROXY_TO_TEXTRACTOR_HANDLE:
            from . import _textractor_handle

            setattr(_textractor_handle, name, value)
        if name in _PROXY_TO_WIN32_JOB_OBJECTS:
            from . import _win32_job_objects

            setattr(_win32_job_objects, name, value)


sys.modules[__name__].__class__ = _ShimModule
