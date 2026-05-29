from __future__ import annotations

import asyncio
import base64
from concurrent.futures import Future, ThreadPoolExecutor
import ctypes
from datetime import datetime, timezone
import hashlib
import io
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from collections import deque
from ctypes import wintypes
from dataclasses import dataclass, field, replace
from functools import wraps
from pathlib import Path
from typing import Any, Callable, ClassVar, Iterable, Protocol
from uuid import uuid4

from .models import (
    ADVANCE_SPEED_FAST,
    ADVANCE_SPEED_MEDIUM,
    ADVANCE_SPEED_SLOW,
    ADVANCE_SPEEDS,
    DATA_SOURCE_OCR_READER,
    DEFAULT_OCR_CAPTURE_BOTTOM_INSET_RATIO,
    DEFAULT_OCR_CAPTURE_LEFT_INSET_RATIO,
    DEFAULT_OCR_CAPTURE_RIGHT_INSET_RATIO,
    DEFAULT_OCR_CAPTURE_TOP_RATIO,
    GalgameConfig,
    MENU_PREFIX_RE as _MENU_PREFIX_RE,
    OCR_CAPTURE_PROFILE_STAGE_CONFIG,
    OCR_CAPTURE_PROFILE_STAGE_GALLERY,
    OCR_CAPTURE_PROFILE_STAGE_GAME_OVER,
    OCR_CAPTURE_PROFILE_MATCH_SOURCE_BUCKET_ASPECT_NEAREST,
    OCR_CAPTURE_PROFILE_MATCH_SOURCE_BUCKET_EXACT,
    OCR_CAPTURE_PROFILE_MATCH_SOURCE_BUILTIN_PRESET,
    OCR_CAPTURE_PROFILE_MATCH_SOURCE_CONFIG_DEFAULT,
    OCR_CAPTURE_PROFILE_MATCH_SOURCE_PROCESS_FALLBACK,
    OCR_CAPTURE_PROFILE_RATIO_KEYS,
    OCR_CAPTURE_PROFILE_STAGE_DEFAULT,
    OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
    OCR_CAPTURE_PROFILE_STAGE_MINIGAME,
    OCR_CAPTURE_PROFILE_STAGE_MENU,
    OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD,
    OCR_CAPTURE_PROFILE_STAGE_TITLE,
    OCR_CAPTURE_PROFILE_STAGE_TRANSITION,
    OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY,
    OCR_TRIGGER_MODE_AFTER_ADVANCE,
    READER_MODE_AUTO,
    READER_MODE_MEMORY,
    build_ocr_capture_profile_bucket_key,
    compute_ocr_window_aspect_ratio,
    json_copy,
    sanitize_screen_ui_elements,
    parse_ocr_capture_profile_bucket_key,
)
from .ocr_chrome_noise import (
    looks_like_temperature_status_line as _looks_like_temperature_status_line,
    looks_like_window_title_line as _looks_like_window_title_line,
)
from .aihong_state import (
    AIHONG_CHOICES_REGION_PRESET as _AIHONG_CHOICES_REGION_PRESET,
    AIHONG_DIALOGUE_CAPTURE_PROFILE_PRESET as _AIHONG_DIALOGUE_CAPTURE_PROFILE_PRESET,
    AIHONG_DIALOGUE_STAGE as _AIHONG_DIALOGUE_STAGE,
    AIHONG_MENU_CAPTURE_PROFILE_PRESET as _AIHONG_MENU_CAPTURE_PROFILE_PRESET,
    AIHONG_MENU_MAX_LINES as _AIHONG_MENU_MAX_LINES,
    AIHONG_MENU_MAX_SIGNIFICANT_CHARS as _AIHONG_MENU_MAX_SIGNIFICANT_CHARS,
    AIHONG_MENU_STAGE as _AIHONG_MENU_STAGE,
    coerce_aihong_menu_choices as _coerce_aihong_menu_choices,
    levenshtein_distance as _levenshtein_distance,
    looks_like_aihong_menu_status_only_text as _looks_like_aihong_menu_status_only_text,
    matches_aihong_target as _matches_aihong_target_info,
    normalize_aihong_choice_box_text as _normalize_aihong_choice_box_text,
)
from plugin.plugins._shared.rapidocr.rapidocr_support import (
    inspect_rapidocr_installation,
    load_rapidocr_runtime,
)
from .reader import normalize_text
from .screen_classifier import (
    ScreenClassification,
    classify_screen_awareness_model,
    classify_screen_from_ocr,
    normalize_screen_type,
)
from .screen_classifier import analyze_screen_visual_features

try:
    from PIL import Image as _PIL_IMAGE_MODULE

    _PIL_RESAMPLING = getattr(_PIL_IMAGE_MODULE, "Resampling", None)
except ImportError:  # pragma: no cover - optional in non-visual test environments.
    _PIL_RESAMPLING = None

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

from .ocr_runtime_types import *
from .ocr_backend_interface import *

def _locked_ocr_writer_method(method):
    @wraps(method)
    def _wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return _wrapper


class OcrReaderBridgeWriter:
    def __init__(
        self,
        *,
        bridge_root: Path,
        version: str = OCR_READER_BRIDGE_VERSION,
        time_fn: Callable[[], float] | None = None,
        logger: Any | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._bridge_root = bridge_root
        self._version = version
        self._time_fn = time_fn or time.time
        self._logger = logger
        self._game_id = ""
        self._session_id = ""
        self._process_name = ""
        self._pid = 0
        self._window_title = ""
        self._engine = OCR_READER_DEFAULT_ENGINE
        self._started_at = ""
        self._last_seq = 0
        self._last_event_ts = ""
        self._scene_index = 0
        self._keep_unknown_scene_until_visual_scene = False
        self._state = self._initial_state("")
        self._text_to_line_id: dict[str, str] = {}
        self._line_id_owner: dict[str, str] = {}

    @property
    def bridge_root(self) -> Path:
        return self._bridge_root

    @property
    def game_id(self) -> str:
        with self._lock:
            return self._game_id

    @property
    def session_id(self) -> str:
        with self._lock:
            return self._session_id

    @property
    def engine(self) -> str:
        with self._lock:
            return self._engine

    @property
    def last_seq(self) -> int:
        with self._lock:
            return self._last_seq

    @property
    def last_event_ts(self) -> str:
        with self._lock:
            return self._last_event_ts

    @property
    def current_state(self) -> dict[str, Any]:
        with self._lock:
            return json_copy(self._state if isinstance(self._state, dict) else {})

    @_locked_ocr_writer_method
    def start_session(self, window: DetectedGameWindow) -> None:
        started_at = utc_now_iso(self._time_fn())
        self._game_id = _ocr_game_id_from_process(window.process_name or window.title)
        self._session_id = f"ocr-{uuid4()}"
        self._process_name = window.process_name
        self._pid = window.pid
        self._window_title = window.title
        self._engine = OCR_READER_DEFAULT_ENGINE
        self._started_at = started_at
        self._last_seq = 0
        self._last_event_ts = started_at
        self._scene_index = 1
        self._keep_unknown_scene_until_visual_scene = False
        self._state = {}
        self._state = self._initial_state(started_at)
        self._text_to_line_id.clear()
        self._line_id_owner.clear()
        self._bridge_dir().mkdir(parents=True, exist_ok=True)
        self._last_seq = self._existing_last_seq_unlocked()
        self._write_session_snapshot()
        self._append_event(
            "session_started",
            {
                "game_title": window.title or window.process_name,
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
                "stability": self._state.get("stability", ""),
                "screen_type": self._state.get("screen_type", ""),
                "screen_ui_elements": self._state.get("screen_ui_elements", []),
                "screen_confidence": self._state.get("screen_confidence", 0.0),
                "screen_debug": json_copy(self._state.get("screen_debug") or {}),
            },
            ts=started_at,
        )

    @_locked_ocr_writer_method
    def keep_unknown_scene_until_visual_scene(self) -> None:
        self._keep_unknown_scene_until_visual_scene = True
        if self._state:
            self._state["scene_id"] = OCR_READER_UNKNOWN_SCENE
            self._write_session_snapshot()

    @_locked_ocr_writer_method
    def discard_session(self) -> None:
        if self._session_id:
            self._append_event(
                "session_ended",
                {
                    "scene_id": str(self._state.get("scene_id") or OCR_READER_UNKNOWN_SCENE),
                    "line_id": str(self._state.get("line_id") or ""),
                    "route_id": str(self._state.get("route_id") or OCR_READER_ROUTE_ID),
                    "discarded": True,
                },
                ts=utc_now_iso(self._time_fn()),
                update_snapshot=False,
            )
            self._clear_session_snapshot_unlocked()
        self._reset_session_unlocked()

    def _existing_last_seq_unlocked(self) -> int:
        events_path = self._events_path()
        if not events_path.is_file():
            return 0
        last_seq = 0
        try:
            with events_path.open("rb") as handle:
                for raw_line in handle:
                    try:
                        event = json.loads(raw_line.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
                        continue
                    if not isinstance(event, dict):
                        continue
                    try:
                        last_seq = max(last_seq, int(event.get("seq") or 0))
                    except (TypeError, ValueError):
                        continue
        except OSError:
            return 0
        return last_seq

    @_locked_ocr_writer_method
    def emit_line(
        self,
        raw_text: str,
        *,
        ts: str,
        ocr_confidence: float | None = None,
        text_source: str = "",
    ) -> bool:
        cleaned = raw_text.strip()
        if not cleaned or not self._session_id:
            return False
        speaker, text = self._split_speaker_text(cleaned)
        if not text:
            return False
        speaker_confidence = self._speaker_confidence(cleaned, speaker)
        line_id = self._line_id_for_text(text)
        self._state = {
            **self._state,
            "speaker": speaker,
            "text": text,
            "choices": [],
            "scene_id": self._current_scene_id(),
            "line_id": line_id,
            "route_id": OCR_READER_ROUTE_ID,
            "is_menu_open": False,
            "save_context": self._state.get("save_context", {"kind": "unknown", "slot_id": "", "display_name": ""}),
            "stability": "stable",
            "ts": ts,
        }
        self._append_event(
            "line_changed",
            {
                "speaker": speaker,
                "text": text,
                "line_id": line_id,
                "line_id_source": "text_hash",
                "scene_id": self._state["scene_id"],
                "route_id": self._state["route_id"],
                "stability": "stable",
                "ocr_confidence": _bounded_confidence_or_zero(ocr_confidence),
                "speaker_confidence": speaker_confidence,
                "text_source": text_source or "bottom_region",
            },
            ts=ts,
        )
        return True

    @_locked_ocr_writer_method
    def emit_line_observed(
        self,
        raw_text: str,
        *,
        ts: str,
        ocr_confidence: float | None = None,
        text_source: str = "",
    ) -> bool:
        cleaned = raw_text.strip()
        if not cleaned or not self._session_id:
            return False
        speaker, text = self._split_speaker_text(cleaned)
        if not text:
            return False
        speaker_confidence = self._speaker_confidence(cleaned, speaker)
        normalized_text = normalize_text(text)
        current_text = str(self._state.get("text") or "")
        current_speaker = str(self._state.get("speaker") or "")
        current_stability = str(self._state.get("stability") or "")
        if current_text == text and current_speaker == speaker and current_stability in {"tentative", "stable"}:
            return False
        current_line_id = str(self._state.get("line_id") or "")
        existing_line_id = self._text_to_line_id.get(normalized_text)
        if existing_line_id and existing_line_id != current_line_id:
            return False
        if existing_line_id and existing_line_id == current_line_id and current_stability == "choices":
            return False
        line_id = self._line_id_for_text(text)
        self._state = {
            **self._state,
            "speaker": speaker,
            "text": text,
            "choices": [],
            "scene_id": self._current_scene_id(),
            "line_id": line_id,
            "route_id": OCR_READER_ROUTE_ID,
            "is_menu_open": False,
            "save_context": self._state.get("save_context", {"kind": "unknown", "slot_id": "", "display_name": ""}),
            "stability": "tentative",
            "ts": ts,
        }
        self._append_event(
            "line_observed",
            {
                "speaker": speaker,
                "text": text,
                "line_id": line_id,
                "line_id_source": "text_hash",
                "scene_id": self._state["scene_id"],
                "route_id": self._state["route_id"],
                "stability": "tentative",
                "ocr_confidence": _bounded_confidence_or_zero(ocr_confidence),
                "speaker_confidence": speaker_confidence,
                "text_source": text_source or "bottom_region",
            },
            ts=ts,
        )
        return True

    @_locked_ocr_writer_method
    def emit_choices(
        self,
        choices: list[str],
        *,
        ts: str,
        choice_bounds: list[dict[str, float] | None] | None = None,
        choice_bounds_metadata: dict[str, Any] | None = None,
    ) -> bool:
        if not choices or not self._session_id:
            return False
        line_id = str(self._state.get("line_id") or "")
        if not line_id:
            line_id = self._line_id_for_text(_canonical_choice_candidate_text(choices))
        bounds = list(choice_bounds or [])
        bounds_metadata = dict(choice_bounds_metadata or {})
        payload_choices = []
        for index, text in enumerate(choices):
            item = {
                "choice_id": f"{line_id}#choice{index}",
                "text": text,
                "index": index,
                "enabled": True,
            }
            if index < len(bounds) and bounds[index]:
                item["bounds"] = dict(bounds[index] or {})
                for key in (
                    "bounds_coordinate_space",
                    "source_size",
                    "capture_rect",
                    "window_rect",
                ):
                    value = bounds_metadata.get(key)
                    if value:
                        item[key] = dict(value) if isinstance(value, dict) else value
            payload_choices.append(item)
        self._state = {
            **self._state,
            "line_id": line_id,
            "scene_id": self._current_scene_id(),
            "choices": payload_choices,
            "is_menu_open": True,
            "stability": "choices",
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
        )
        return True

    @_locked_ocr_writer_method
    def emit_screen_classified(
        self,
        *,
        screen_type: str,
        confidence: float,
        ui_elements: list[dict[str, Any]] | None = None,
        raw_ocr_text: list[str] | None = None,
        screen_debug: dict[str, Any] | None = None,
        ts: str,
    ) -> bool:
        if not self._session_id:
            return False
        normalized_type = normalize_screen_type(screen_type)
        if not normalized_type:
            return False
        elements = sanitize_screen_ui_elements(ui_elements or [], limit=10)
        try:
            normalized_confidence = round(max(0.0, min(float(confidence), 1.0)), 2)
        except (TypeError, ValueError):
            normalized_confidence = 0.0
        raw_lines = [
            str(line or "")[:120]
            for line in list(raw_ocr_text or [])[:20]
            if str(line or "").strip()
        ]
        current_type = str(self._state.get("screen_type") or "")
        current_elements = sanitize_screen_ui_elements(
            self._state.get("screen_ui_elements") or [], limit=10
        )
        try:
            current_confidence = round(float(self._state.get("screen_confidence") or 0.0), 2)
        except (TypeError, ValueError):
            current_confidence = 0.0
        if (
            current_type == normalized_type
            and current_elements == elements
            and abs(current_confidence - normalized_confidence) < 0.01
        ):
            return False
        if not current_type and normalized_type == OCR_CAPTURE_PROFILE_STAGE_DEFAULT:
            return False
        self._state = {
            **self._state,
            "screen_type": normalized_type,
            "screen_ui_elements": elements,
            "screen_confidence": normalized_confidence,
            "screen_debug": json_copy(screen_debug or {}),
            "ts": ts,
        }
        self._append_event(
            "screen_classified",
            {
                "screen_type": normalized_type,
                "screen_ui_elements": elements,
                "screen_confidence": normalized_confidence,
                "screen_debug": json_copy(screen_debug or {}),
                "raw_ocr_text": raw_lines,
                "scene_id": self._state["scene_id"],
                "line_id": self._state["line_id"],
                "route_id": self._state["route_id"],
            },
            ts=ts,
        )
        return True

    @_locked_ocr_writer_method
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

    @_locked_ocr_writer_method
    def emit_error(self, message: str, *, ts: str, details: dict[str, Any] | None = None) -> bool:
        if not self._session_id:
            return False
        payload: dict[str, Any] = {
            "message": message,
            "source": DATA_SOURCE_OCR_READER,
            "scene_id": self._state["scene_id"],
            "line_id": self._state["line_id"],
            "route_id": self._state["route_id"],
        }
        if details:
            payload["details"] = dict(details)
        self._append_event("error", payload, ts=ts, update_snapshot=False)
        return True

    @_locked_ocr_writer_method
    def emit_scene_changed(
        self,
        *,
        scene_id: str,
        ts: str,
        reason: str,
        background_hash: str = "",
    ) -> bool:
        return self._emit_scene_changed_unlocked(
            scene_id=scene_id,
            ts=ts,
            reason=reason,
            background_hash=background_hash,
        )

    def _emit_scene_changed_unlocked(
        self,
        *,
        scene_id: str,
        ts: str,
        reason: str,
        background_hash: str = "",
    ) -> bool:
        if not self._session_id or not scene_id:
            return False
        if str(self._state.get("scene_id") or "") == scene_id:
            return False
        self._keep_unknown_scene_until_visual_scene = False
        self._state = {
            **self._state,
            "scene_id": scene_id,
            "speaker": "",
            "text": "",
            "line_id": "",
            "choices": [],
            "is_menu_open": False,
            "stability": "",
            "ts": ts,
        }
        self._append_event(
            "scene_changed",
            {
                "scene_id": scene_id,
                "route_id": self._state["route_id"],
                "reason": reason,
                "background_hash": background_hash,
            },
            ts=ts,
        )
        return True

    @_locked_ocr_writer_method
    def advance_visual_scene(self, *, ts: str, background_hash: str = "") -> str:
        self._scene_index += 1
        scene_id = f"ocr:{self._game_id or 'unknown'}:scene-{self._scene_index:04d}"
        self._emit_scene_changed_unlocked(
            scene_id=scene_id,
            ts=ts,
            reason="background_changed",
            background_hash=background_hash,
        )
        return scene_id

    @_locked_ocr_writer_method
    def end_session(self, *, ts: str) -> bool:
        if not self._session_id:
            return False
        payload = {
            "scene_id": self._state["scene_id"],
            "line_id": self._state["line_id"],
            "route_id": self._state["route_id"],
        }
        self._append_event("session_ended", payload, ts=ts, update_snapshot=False)
        self._clear_session_snapshot_unlocked()
        self._reset_session_unlocked()
        return True

    @_locked_ocr_writer_method
    def runtime(self) -> OcrReaderRuntime:
        return OcrReaderRuntime(
            enabled=True,
            status="active" if self._session_id else "idle",
            detail="",
            process_name=self._process_name,
            pid=self._pid,
            window_title=self._window_title,
            game_id=self._game_id,
            session_id=self._session_id,
            last_seq=self._last_seq,
            last_event_ts=self._last_event_ts,
        )

    def _reset_session_unlocked(self) -> None:
        self._game_id = ""
        self._session_id = ""
        self._process_name = ""
        self._pid = 0
        self._window_title = ""
        self._engine = OCR_READER_DEFAULT_ENGINE
        self._started_at = ""
        self._last_seq = 0
        self._last_event_ts = ""
        self._scene_index = 1
        self._keep_unknown_scene_until_visual_scene = False
        self._state = self._initial_state("")
        self._text_to_line_id.clear()
        self._line_id_owner.clear()

    def _initial_state(self, ts: str) -> dict[str, Any]:
        return {
            "speaker": "",
            "text": "",
            "choices": [],
            "scene_id": self._current_scene_id(),
            "line_id": "",
            "route_id": OCR_READER_ROUTE_ID,
            "is_menu_open": False,
            "save_context": {"kind": "unknown", "slot_id": "", "display_name": ""},
            "stability": "",
            "screen_type": "",
            "screen_ui_elements": [],
            "screen_confidence": 0.0,
            "screen_debug": {},
            "ts": ts,
        }

    def _current_scene_id(self) -> str:
        state = getattr(self, "_state", {}) or {}
        current = str(state.get("scene_id") or "").strip()
        if current and current != OCR_READER_UNKNOWN_SCENE:
            return current
        if self._keep_unknown_scene_until_visual_scene:
            return OCR_READER_UNKNOWN_SCENE
        return f"ocr:{self._game_id or 'unknown'}:scene-{int(getattr(self, '_scene_index', 1) or 1):04d}"

    def _bridge_dir(self) -> Path:
        return self._bridge_root / self._game_id

    def _session_path(self) -> Path:
        return self._bridge_dir() / "session.json"

    def _events_path(self) -> Path:
        return self._bridge_dir() / "events.jsonl"

    def _clear_session_snapshot_unlocked(self) -> None:
        try:
            self._session_path().unlink(missing_ok=True)
        except Exception as exc:
            if self._logger is not None:
                try:
                    self._logger.warning("ocr_reader session snapshot clear failed: {}", exc)
                except Exception:
                    pass

    def _session_snapshot(self) -> dict[str, Any]:
        return {
            "protocol_version": 1,
            "game_id": self._game_id,
            "game_title": self._window_title or self._process_name,
            "engine": self._engine,
            "session_id": self._session_id,
            "started_at": self._started_at,
            "last_seq": self._last_seq,
            "locale": "",
            "bridge_sdk_version": self._version,
            "metadata": {
                "source": DATA_SOURCE_OCR_READER,
                "game_process_name": self._process_name,
                "game_pid": self._pid,
                "window_title": self._window_title,
            },
            "state": {
                "speaker": str(self._state.get("speaker") or ""),
                "text": str(self._state.get("text") or ""),
                "choices": list(self._state.get("choices", [])),
                "scene_id": str(self._state.get("scene_id") or OCR_READER_UNKNOWN_SCENE),
                "line_id": str(self._state.get("line_id") or ""),
                "route_id": str(self._state.get("route_id") or OCR_READER_ROUTE_ID),
                "is_menu_open": bool(self._state.get("is_menu_open", False)),
                "save_context": dict(self._state.get("save_context", {"kind": "unknown", "slot_id": "", "display_name": ""})),
                "stability": str(self._state.get("stability") or ""),
                "screen_type": str(self._state.get("screen_type") or ""),
                "screen_ui_elements": sanitize_screen_ui_elements(
                    self._state.get("screen_ui_elements") or [], limit=10
                ),
                "screen_confidence": float(self._state.get("screen_confidence") or 0.0),
                "screen_debug": json_copy(self._state.get("screen_debug") or {}),
                "ts": str(self._state.get("ts") or self._started_at),
            },
        }

    def _write_session_snapshot(self) -> None:
        session_path = self._session_path()
        tmp_fd: int | None = None
        tmp_path: Path | None = None
        try:
            bridge_dir = self._bridge_dir()
            bridge_dir.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(
                self._session_snapshot(),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            tmp_fd, tmp_name = tempfile.mkstemp(
                prefix=f".{session_path.name}.",
                suffix=".tmp",
                dir=str(bridge_dir),
            )
            tmp_path = Path(tmp_name)
            with os.fdopen(tmp_fd, "wb") as handle:
                tmp_fd = None
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            for attempt in range(3):
                try:
                    os.replace(tmp_path, session_path)
                    break
                except PermissionError:
                    if attempt >= 2:
                        raise
                    time.sleep(0.05)
            tmp_path = None
        except Exception as exc:
            cleanup_errors: list[str] = []
            if tmp_fd is not None:
                try:
                    os.close(tmp_fd)
                except Exception as close_exc:
                    cleanup_errors.append(f"close_tmp_fd_failed: {close_exc}")
            try:
                if tmp_path is not None:
                    tmp_path.unlink(missing_ok=True)
            except Exception as unlink_exc:
                cleanup_errors.append(f"unlink_tmp_file_failed: {unlink_exc}")
            if self._logger is not None:
                try:
                    if cleanup_errors:
                        self._logger.warning(
                            "ocr_reader session snapshot write failed: {}; cleanup_errors={}",
                            exc,
                            cleanup_errors,
                        )
                    else:
                        self._logger.warning("ocr_reader session snapshot write failed: {}", exc)
                except Exception:
                    pass
            if cleanup_errors:
                raise RuntimeError(
                    f"ocr_reader session snapshot write failed: {exc}; "
                    f"cleanup_errors={cleanup_errors}"
                ) from exc
            raise RuntimeError(f"ocr_reader session snapshot write failed: {exc}") from exc

    def _append_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        ts: str,
        update_snapshot: bool = True,
    ) -> None:
        assert self._lock.locked(), "_append_event must be called under _lock"
        self._last_seq += 1
        self._last_event_ts = ts
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
            return

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
            candidate = f"ocr:{digest[:width]}"
            owner = self._line_id_owner.get(candidate)
            if owner in {None, normalized}:
                self._line_id_owner[candidate] = normalized
                self._text_to_line_id[normalized] = candidate
                return candidate
        for suffix in range(1, _OCR_LINE_ID_MAX_COLLISION_SUFFIX + 1):
            candidate = f"ocr:{digest}#{suffix}"
            owner = self._line_id_owner.get(candidate)
            if owner in {None, normalized}:
                self._line_id_owner[candidate] = normalized
                self._text_to_line_id[normalized] = candidate
                return candidate
        raise RuntimeError(
            "ocr line_id collision limit exceeded "
            f"after {_OCR_LINE_ID_MAX_COLLISION_SUFFIX} suffix attempts"
        )

    @staticmethod
    def _split_speaker_text(raw_text: str) -> tuple[str, str]:
        match = _SPEAKER_BRACKET_RE.match(raw_text)
        if match is not None:
            return match.group(1).strip(), match.group(2).strip()
        match = _SPEAKER_PAREN_PREFIX_RE.match(raw_text)
        if match is not None:
            return match.group(1).strip(), match.group(2).strip()
        match = _SPEAKER_QUOTE_RE.match(raw_text)
        if match is not None:
            return match.group(1).strip(), match.group(2).strip()
        match = _SPEAKER_COLON_RE.match(raw_text)
        if match is not None:
            return match.group(1).strip(), match.group(2).strip()
        match = _SPEAKER_PAREN_SUFFIX_RE.match(raw_text)
        if match is not None:
            return match.group(1).strip(), match.group(2).strip()
        match = _NARRATION_QUOTE_RE.match(raw_text)
        if match is not None:
            return "", match.group(1).strip()
        match = _NARRATION_PAREN_RE.match(raw_text)
        if match is not None:
            return "", match.group(1).strip()
        return "", raw_text.strip()

    @staticmethod
    def _speaker_confidence(raw_text: str, speaker: str) -> float:
        if not speaker:
            return 0.0
        if _SPEAKER_BRACKET_RE.match(raw_text) is not None:
            return 0.96
        if _SPEAKER_QUOTE_RE.match(raw_text) is not None:
            return 0.94
        if _SPEAKER_COLON_RE.match(raw_text) is not None:
            return 0.94
        if _SPEAKER_PAREN_PREFIX_RE.match(raw_text) is not None:
            return 0.84
        if _SPEAKER_PAREN_SUFFIX_RE.match(raw_text) is not None:
            return 0.80
        return 0.65
