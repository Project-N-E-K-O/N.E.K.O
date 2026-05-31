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

from .ocr_capture_backends import *

from .ocr_rapidocr_backend import *
from .ocr_input_hooks import *
from .ocr_bridge_writer import *
from . import ocr_reader as _ocr_reader_module
from .ocr_reader import (
    _classify_window_candidate,
    _foreground_matches_target,
    _is_confident_auto_window,
    _is_legacy_geometryless_auto_window,
    _window_sort_key,
)

def _foreground_window_handle() -> int:
    return _ocr_reader_module._foreground_window_handle()


class ObserveMixin:
    """窗口扫描、目标匹配、前景检测、vision snapshot"""

    def _observe_memory_reader_text_progress(
        self,
        memory_reader_runtime: dict[str, Any],
        *,
        now: float,
    ) -> bool:
        status = str(memory_reader_runtime.get("status") or "")
        game_id = str(memory_reader_runtime.get("game_id") or "")
        session_id = str(memory_reader_runtime.get("session_id") or "")
        try:
            last_text_seq = int(memory_reader_runtime.get("last_text_seq") or 0)
        except (TypeError, ValueError):
            last_text_seq = 0
        received_text_this_tick = (
            str(memory_reader_runtime.get("detail") or "") == "receiving_text"
            and last_text_seq > 0
        )

        if status not in {"attaching", "active"} or not game_id or not session_id:
            self._reset_memory_reader_text_progress_tracking()
            return False

        if "last_text_recent" in memory_reader_runtime:
            self._last_seen_memory_reader_game_id = game_id
            self._last_seen_memory_reader_session_id = session_id
            self._last_seen_memory_reader_text_seq = max(0, last_text_seq)
            if bool(memory_reader_runtime.get("last_text_recent")) and last_text_seq > 0:
                self._last_memory_reader_text_at = now
                return True
            if last_text_seq <= 0:
                self._last_memory_reader_text_at = 0.0
            return False

        session_changed = (
            game_id != self._last_seen_memory_reader_game_id
            or session_id != self._last_seen_memory_reader_session_id
        )
        seq_reset = last_text_seq < self._last_seen_memory_reader_text_seq
        if session_changed or seq_reset:
            self._last_seen_memory_reader_game_id = game_id
            self._last_seen_memory_reader_session_id = session_id
            self._last_seen_memory_reader_text_seq = last_text_seq
            self._last_memory_reader_text_at = now if received_text_this_tick else 0.0
            return received_text_this_tick

        if last_text_seq > self._last_seen_memory_reader_text_seq:
            self._last_seen_memory_reader_text_seq = last_text_seq
            self._last_memory_reader_text_at = now
            return True
        return False


    @staticmethod
    def _background_perceptual_hash(frame: Any) -> str:
        return _perceptual_hash_image(frame)


    @staticmethod
    def _hash_distance(left: str, right: str) -> int:
        if not left or not right:
            return 0
        try:
            return (int(left, 16) ^ int(right, 16)).bit_count()
        except Exception:
            return 64


    def _background_scene_change_distance(self) -> int:
        try:
            threshold = int(
                getattr(
                    self._config,
                    "ocr_reader_background_scene_change_distance",
                    _BACKGROUND_SCENE_CHANGE_DISTANCE,
                )
            )
        except (TypeError, ValueError):
            return _BACKGROUND_SCENE_CHANGE_DISTANCE
        if threshold < 18 or threshold > _BACKGROUND_SCENE_CHANGE_FORCE_DISTANCE:
            return _BACKGROUND_SCENE_CHANGE_DISTANCE
        return threshold


    def _observe_background_hash(
        self,
        background_hash: str,
        *,
        now: float,
        confirm_polls: int = _BACKGROUND_SCENE_CHANGE_CONFIRM_POLLS,
        defer_scene_emit: bool = False,
    ) -> bool:
        if not background_hash:
            return False
        if not self._last_background_hash:
            self._last_background_hash = background_hash
            self._pending_background_hash = ""
            self._pending_background_change_count = 0
            return False
        distance = self._hash_distance(self._last_background_hash, background_hash)
        if distance < self._background_scene_change_distance():
            if self._pending_background_candidate_hash:
                self._clear_pending_background_candidate(
                    diagnostic="background_candidate_cleared_below_threshold"
                )
            self._pending_background_hash = ""
            self._pending_background_change_count = 0
            return False
        if background_hash != self._pending_background_hash:
            self._pending_background_hash = background_hash
            self._pending_background_change_count = 1
            self._record_pending_background_candidate(
                background_hash=background_hash,
                base_hash=self._last_background_hash,
                distance=distance,
                now=now,
            )
        else:
            self._pending_background_change_count += 1
        required_confirm_polls = max(1, int(confirm_polls or 1))
        if self._pending_background_change_count < required_confirm_polls:
            return False
        self._clear_pending_background_candidate(
            diagnostic="background_candidate_promoted_to_pending_visual_scene"
        )
        self._promote_background_hash_to_pending_visual_scene(
            background_hash=background_hash,
            distance=distance,
            now=now,
            diagnostic=(
                "background_hash_scene_pending"
                if not defer_scene_emit
                else "followup_background_hash_scene_pending"
            ),
        )
        return False


    def _set_scene_ordering_diagnostic(self, value: str) -> None:
        diagnostic = str(value or "").strip() or "none"
        self._scene_ordering_diagnostic = diagnostic
        try:
            self._runtime.scene_ordering_diagnostic = diagnostic
        except Exception:
            pass


    def _clear_pending_visual_scene(self, *, diagnostic: str = "") -> bool:
        if not self._pending_visual_scene_hash:
            return False
        self._pending_visual_scene_hash = ""
        self._pending_visual_scene_at = 0.0
        self._pending_visual_scene_distance = 0
        self._pending_visual_scene_commit_diagnostic = ""
        if diagnostic:
            self._set_scene_ordering_diagnostic(diagnostic)
        return True

    # -- background candidate helpers --


    def _record_pending_background_candidate(
        self,
        *,
        background_hash: str,
        base_hash: str,
        distance: int,
        now: float,
    ) -> None:
        self._pending_background_candidate_hash = background_hash
        self._pending_background_candidate_at = now
        self._pending_background_candidate_distance = distance
        self._pending_background_candidate_base_hash = base_hash
        self._pending_background_candidate_used = False


    def _clear_pending_background_candidate(self, *, diagnostic: str = "") -> None:
        self._pending_background_candidate_hash = ""
        self._pending_background_candidate_at = 0.0
        self._pending_background_candidate_distance = 0
        self._pending_background_candidate_base_hash = ""
        self._pending_background_candidate_used = False
        if diagnostic:
            self._set_scene_ordering_diagnostic(diagnostic)


    def _promote_background_hash_to_pending_visual_scene(
        self,
        *,
        background_hash: str,
        distance: int,
        now: float,
        diagnostic: str,
        set_commit_diagnostic: bool = False,
    ) -> None:
        last_observed_line = dict(self._last_observed_line or {})
        last_stable_line = dict(self._last_stable_line or {})
        consecutive_no_text_polls = int(self._consecutive_no_text_polls or 0)
        self._reset_default_ocr_state()
        self._last_observed_line = last_observed_line
        self._last_stable_line = last_stable_line
        self._consecutive_no_text_polls = consecutive_no_text_polls
        self._last_background_hash = background_hash
        self._reset_aihong_menu_state()
        self._pending_visual_scene_hash = background_hash
        self._pending_visual_scene_at = now
        self._pending_visual_scene_distance = distance
        if set_commit_diagnostic:
            self._pending_visual_scene_commit_diagnostic = diagnostic
        self._set_scene_ordering_diagnostic(diagnostic)


    def _has_early_scene_commit_signal(
        self,
        *,
        previous_line: dict[str, Any] | None,
        screen_type: str,
        has_choices: bool,
        now: float,
    ) -> bool:
        if has_choices:
            return True
        normalized = normalize_screen_type(screen_type)
        if normalized in _DIALOGUE_BOUNDARY_SCREEN_TYPES:
            return True
        if int(self._consecutive_no_text_polls or 0) >= _DIALOGUE_BLOCK_NO_TEXT_GAP_POLLS:
            return True
        if previous_line:
            age = self._line_timestamp_age_seconds(previous_line, now=now)
            if age is not None and age >= _BACKGROUND_CANDIDATE_EARLY_COMMIT_TEXT_GAP_SECONDS:
                return True
        distance = int(self._pending_background_candidate_distance or 0)
        threshold = self._background_scene_change_distance()
        if distance >= threshold + _BACKGROUND_CANDIDATE_EARLY_COMMIT_DISTANCE_MARGIN:
            return True
        return False


    def _commit_pending_background_candidate(
        self,
        *,
        now: float,
        diagnostic: str,
    ) -> None:
        background_hash = self._pending_background_candidate_hash
        distance = int(self._pending_background_candidate_distance or 0)
        if not background_hash:
            return
        self._pending_background_candidate_used = True
        self._pending_background_hash = ""
        self._pending_background_change_count = 0
        self._promote_background_hash_to_pending_visual_scene(
            background_hash=background_hash,
            distance=distance,
            now=now,
            diagnostic=diagnostic,
            set_commit_diagnostic=True,
        )
        self._commit_pending_visual_scene(
            now=now,
            diagnostic=diagnostic,
        )
        self._clear_pending_background_candidate(diagnostic=diagnostic)


    def _resolve_pending_background_candidate_before_dialogue(
        self,
        *,
        cleaned_text: str,
        speaker: str,
        text: str,
        now: float,
    ) -> None:
        if not self._pending_background_candidate_hash:
            return
        if self._pending_background_candidate_used:
            self._clear_pending_background_candidate(
                diagnostic="background_candidate_cleared_after_used"
            )
            return
        if now - self._pending_background_candidate_at > _BACKGROUND_CANDIDATE_EARLY_CANDIDATE_MAX_SECONDS:
            self._clear_pending_background_candidate(
                diagnostic="background_candidate_expired"
            )
            return
        candidate_distance = int(self._pending_background_candidate_distance or 0)
        if candidate_distance >= _BACKGROUND_SCENE_CHANGE_FORCE_DISTANCE:
            self._commit_pending_background_candidate(
                now=now,
                diagnostic="background_candidate_committed_by_force_distance",
            )
            return
        state = getattr(self._writer, "_state", {})
        screen_type = normalize_screen_type(
            str((state or {}).get("screen_type") or "")
        )
        has_choices = (
            bool((state or {}).get("choices")) if isinstance(state, dict) else False
        )
        previous_line = self._last_stable_line or self._last_observed_line
        if previous_line and self._is_dialogue_block_continuation(
            previous_line,
            text or cleaned_text,
            current_speaker=speaker,
            screen_type=screen_type,
            has_choices=has_choices,
            now=now,
        ):
            self._clear_pending_background_candidate(
                diagnostic="background_candidate_suppressed_by_dialogue_continuation"
            )
            return
        if not self._has_early_scene_commit_signal(
            previous_line=previous_line,
            screen_type=screen_type,
            has_choices=has_choices,
            now=now,
        ):
            return
        self._commit_pending_background_candidate(
            now=now,
            diagnostic="background_candidate_committed_before_observed",
        )


    def _commit_pending_visual_scene(
        self,
        *,
        now: float,
        diagnostic: str = "",
    ) -> bool:
        background_hash = str(self._pending_visual_scene_hash or "")
        if not background_hash:
            return False
        scene_at = float(self._pending_visual_scene_at or now)
        commit_diagnostic = str(
            diagnostic or self._pending_visual_scene_commit_diagnostic or ""
        )
        distance = int(self._pending_visual_scene_distance or 0)
        self._pending_visual_scene_hash = ""
        self._pending_visual_scene_at = 0.0
        self._pending_visual_scene_distance = 0
        self._pending_visual_scene_commit_diagnostic = ""
        if self._last_scene_change_committed_ts > 0:
            if now - self._last_scene_change_committed_ts < _SCENE_CHANGE_COOLDOWN_SECONDS:
                if distance < _BACKGROUND_SCENE_CHANGE_FORCE_DISTANCE:
                    self._clear_pending_visual_scene(
                        diagnostic="scene_change_suppressed_by_cooldown"
                    )
                    return False
        committed = bool(
            self._writer.advance_visual_scene(
                ts=utc_now_iso(scene_at if scene_at > 0 else now),
                background_hash=background_hash,
            )
        )
        if committed:
            self._visual_scene_committed = True
            self._last_scene_change_committed_ts = now
            if commit_diagnostic:
                self._set_scene_ordering_diagnostic(commit_diagnostic)
        return committed


    @staticmethod
    def _line_timestamp_age_seconds(line: dict[str, Any], *, now: float) -> float | None:
        raw_ts = str(line.get("ts") or "").strip()
        if not raw_ts:
            return None
        try:
            parsed = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, float(now) - parsed.timestamp())


    @staticmethod
    def _looks_like_dialogue_boundary_title(text: str) -> bool:
        normalized = normalize_text(text).strip()
        if not normalized:
            return False
        if _DIALOGUE_BOUNDARY_TITLE_RE.match(normalized):
            return True
        significant = re.sub(r"[\s\u3000,，.。!！?？:：;；、\"'“”‘’「」『』（）()【】\[\]-]", "", normalized)
        return 1 <= len(significant) <= 8 and not _looks_like_ocr_dialogue_normalized_text(normalized)


    def _is_dialogue_block_continuation(
        self,
        previous_line: dict[str, Any],
        current_text: str,
        *,
        current_speaker: str = "",
        screen_type: str = "",
        has_choices: bool = False,
        now: float = 0.0,
    ) -> bool:
        if has_choices:
            return False
        normalized_screen_type = normalize_screen_type(screen_type)
        if normalized_screen_type in _DIALOGUE_BOUNDARY_SCREEN_TYPES:
            return False
        if normalized_screen_type not in _DIALOGUE_BLOCK_SCREEN_TYPES:
            return False
        previous_text = str(previous_line.get("text") or "").strip()
        current = str(current_text or "").strip()
        if not previous_text or not current:
            return False
        if self._looks_like_dialogue_boundary_title(current):
            return False
        if not _looks_like_ocr_dialogue_text(previous_text):
            return False
        if not _looks_like_ocr_dialogue_text(current):
            return False
        age = self._line_timestamp_age_seconds(previous_line, now=now)
        if age is not None and age > _DIALOGUE_BLOCK_CONTINUATION_MAX_SECONDS:
            return False
        del current_speaker
        return True


    def _resolve_pending_visual_scene_for_dialogue(
        self,
        *,
        cleaned_text: str,
        speaker: str,
        text: str,
        now: float,
        commit_diagnostic: str = "pending_scene_committed_by_dialogue_boundary",
    ) -> None:
        if not self._pending_visual_scene_hash:
            return
        if int(self._pending_visual_scene_distance or 0) >= _BACKGROUND_SCENE_CHANGE_FORCE_DISTANCE:
            self._commit_pending_visual_scene(
                now=now,
                diagnostic=self._pending_visual_scene_commit_diagnostic
                or "pending_scene_committed_by_force_background_distance",
            )
            return
        state = getattr(self._writer, "_state", {})
        has_choices = bool((state or {}).get("choices")) if isinstance(state, dict) else False
        screen_type = str((state or {}).get("screen_type") or "") if isinstance(state, dict) else ""
        if int(self._consecutive_no_text_polls or 0) >= _DIALOGUE_BLOCK_NO_TEXT_GAP_POLLS:
            self._commit_pending_visual_scene(
                now=now,
                diagnostic="pending_scene_committed_after_no_text_gap",
            )
            return
        previous_line = self._last_stable_line or self._last_observed_line
        if previous_line and self._is_dialogue_block_continuation(
            previous_line,
            text or cleaned_text,
            current_speaker=speaker,
            screen_type=screen_type,
            has_choices=has_choices,
            now=now,
        ):
            self._clear_pending_visual_scene(
                diagnostic="pending_scene_suppressed_by_dialogue_continuation"
            )
            return
        self._commit_pending_visual_scene(
            now=now,
            diagnostic=self._pending_visual_scene_commit_diagnostic
            or commit_diagnostic,
        )


    def _observe_followup_background_hash(
        self,
        extraction: OcrExtractionResult,
        *,
        now: float,
        confirm_polls: int,
        defer_scene_emit: bool,
    ) -> bool:
        background_hash = str(extraction.background_hash or "")
        if not background_hash:
            return False
        pending_before = str(self._pending_visual_scene_hash or "")
        emitted = self._observe_background_hash(
            background_hash,
            now=now,
            confirm_polls=confirm_polls,
            defer_scene_emit=defer_scene_emit,
        )
        if emitted:
            self._set_scene_ordering_diagnostic(
                "followup_background_hash_scene_committed"
            )
            return True
        pending_after = str(self._pending_visual_scene_hash or "")
        if pending_after and pending_after != pending_before:
            self._pending_visual_scene_commit_diagnostic = (
                "followup_background_hash_scene_committed"
            )
            self._set_scene_ordering_diagnostic("followup_background_hash_scene_pending")
        return False


    def update_window_target(self, target: dict[str, Any] | None) -> None:
        self._manual_target = OcrWindowTarget.from_dict(target)
        self._locked_target = OcrWindowTarget()
        self._consecutive_no_text_polls = 0
        self._last_selection = WindowSelectionResult(
            selection_mode="manual" if self._manual_target.is_manual() else "auto",
            selection_detail="manual_target_active"
            if self._manual_target.is_manual()
            else "auto_candidate_scan",
            manual_target=self._manual_target,
            candidate_count=len(self._last_eligible_windows),
            excluded_candidate_count=len(self._last_excluded_windows),
            last_exclude_reason=(
                str(self._last_excluded_windows[0].exclude_reason or "")
                if self._last_excluded_windows
                else ""
            ),
        )


    def current_window_target(self) -> dict[str, Any]:
        return self._manual_target.to_dict()


    def refresh_foreground_state(self) -> dict[str, Any]:
        if not self._config.ocr_reader_enabled or not self._platform_fn():
            return self._runtime.to_dict()
        foreground_hwnd = _foreground_window_handle()
        target, detail = self._foreground_refresh_target()
        target_hwnd = int(target.hwnd or 0) if target is not None else 0
        if target is not None:
            is_foreground, foreground_match_reason = _foreground_matches_target(
                foreground_hwnd,
                target,
            )
            (
                target_window_visible,
                target_window_minimized,
                ocr_window_capture_eligible,
                ocr_window_capture_block_reason,
            ) = _ocr_reader_module._target_window_capture_state(target)
            last_capture_error = str(self._last_capture_error or self._runtime.last_capture_error)
            stale_capture_backend = bool(
                self._stale_capture_backend or self._runtime.stale_capture_backend
            )
            has_recent_capture_result = bool(
                self._last_capture_completed_at
                or self._runtime.last_capture_completed_at
                or self._last_raw_ocr_text
                or self._runtime.last_raw_ocr_text
                or str((self._last_stable_line or self._runtime.last_stable_line).get("text") or "")
            )
            if ocr_window_capture_eligible and stale_capture_backend:
                ocr_window_capture_block_reason = "stale_capture_backend"
            elif ocr_window_capture_eligible and last_capture_error:
                ocr_window_capture_block_reason = "capture_failed"
            self._runtime.target_is_foreground = is_foreground
            self._runtime.target_window_visible = target_window_visible
            self._runtime.target_window_minimized = target_window_minimized
            self._runtime.ocr_window_capture_eligible = ocr_window_capture_eligible
            self._runtime.ocr_window_capture_available = bool(
                ocr_window_capture_eligible
                and has_recent_capture_result
                and not last_capture_error
                and not stale_capture_backend
            )
            self._runtime.ocr_window_capture_block_reason = ocr_window_capture_block_reason
            self._runtime.input_target_foreground = is_foreground
            self._runtime.input_target_block_reason = (
                "" if is_foreground else "target_not_foreground"
            )
            self._runtime.effective_window_key = str(target.window_key or self._runtime.effective_window_key)
            self._runtime.effective_window_title = str(target.title or self._runtime.effective_window_title)
            self._runtime.effective_process_name = str(target.process_name or self._runtime.effective_process_name)
            if not self._runtime.process_name:
                self._runtime.process_name = str(target.process_name or "")
            if not self._runtime.window_title:
                self._runtime.window_title = str(target.title or "")
            if not self._runtime.pid:
                self._runtime.pid = int(target.pid or 0)
            detail = (
                f"{detail}:foreground_{foreground_match_reason}"
                if is_foreground
                else f"{detail}:background"
            )
        elif self._runtime.effective_window_key or self._runtime.process_name:
            self._runtime.target_is_foreground = False
            self._runtime.input_target_foreground = False
            self._runtime.input_target_block_reason = "target_missing"
            self._runtime.target_window_visible = False
            self._runtime.target_window_minimized = False
            self._runtime.ocr_window_capture_eligible = False
            self._runtime.ocr_window_capture_available = False
            self._runtime.ocr_window_capture_block_reason = "target_missing"
            detail = detail or "target_unresolved"
        else:
            self._runtime.target_is_foreground = False
            self._runtime.input_target_foreground = False
            self._runtime.input_target_block_reason = "target_missing"
            self._runtime.target_window_visible = False
            self._runtime.target_window_minimized = False
            self._runtime.ocr_window_capture_eligible = False
            self._runtime.ocr_window_capture_available = False
            self._runtime.ocr_window_capture_block_reason = "target_missing"
            detail = "no_target"
        self._runtime.foreground_refresh_at = utc_now_iso(self._time_fn())
        self._runtime.foreground_refresh_detail = detail
        self._runtime.foreground_hwnd = max(0, int(foreground_hwnd or 0))
        self._runtime.target_hwnd = max(0, int(target_hwnd or 0))
        return self._runtime.to_dict()


    def _foreground_refresh_target(self) -> tuple[DetectedGameWindow | None, str]:
        windows = list(self._last_detected_windows or [])
        for target, detail in (
            (self._manual_target, "manual_target"),
            (self._locked_target, "locked_target"),
        ):
            if not isinstance(target, OcrWindowTarget):
                continue
            if not (
                target.window_key
                or target.last_known_hwnd
                or target.pid
                or target.process_name
                or target.normalized_title
            ):
                continue
            for candidate in windows:
                if target.matches_exact(candidate) or target.matches_hwnd(candidate):
                    return candidate, f"{detail}_exact"
            for candidate in windows:
                if target.matches_signature(candidate):
                    return candidate, f"{detail}_rebound"
        runtime_key = str(self._runtime.effective_window_key or "").strip()
        runtime_process = str(self._runtime.effective_process_name or self._runtime.process_name or "").strip().lower()
        runtime_pid = int(self._runtime.pid or 0)
        if runtime_key:
            for candidate in windows:
                if candidate.window_key == runtime_key:
                    return candidate, "runtime_effective_key"
        if runtime_pid > 0:
            for candidate in windows:
                if candidate.pid == runtime_pid:
                    return candidate, "runtime_pid"
        if runtime_process:
            for candidate in windows:
                if candidate.process_name.strip().lower() == runtime_process:
                    return candidate, "runtime_process"
        return None, "target_unresolved"


    def _has_locked_target(self) -> bool:
        return bool(
            self._locked_target.window_key
            or self._locked_target.last_known_hwnd
            or self._locked_target.pid
            or self._locked_target.process_name
            or self._locked_target.normalized_title
        )


    def _remember_locked_target(self, target: DetectedGameWindow) -> None:
        if self._manual_target.is_manual():
            return
        self._locked_target = OcrWindowTarget(
            mode="auto",
            window_key=target.window_key,
            process_name=target.process_name,
            normalized_title=target.normalized_title,
            pid=target.pid,
            last_known_hwnd=target.hwnd,
            selected_at=utc_now_iso(self._time_fn()),
        )


    def list_windows_snapshot(
        self,
        *,
        include_excluded: bool = False,
        force: bool = False,
    ) -> dict[str, Any]:
        eligible_windows, excluded_windows = self._scan_window_inventory(force=force)
        payload = {
            "target_selection_mode": self._manual_target.mode,
            "manual_target": self._manual_target.to_dict(),
            "candidate_count": len(eligible_windows),
            "excluded_candidate_count": len(excluded_windows),
            "windows": [
                candidate.to_dict(
                    is_attached=self._matches_attached_window(candidate),
                    is_manual_target=self._manual_target.is_manual()
                    and (
                        self._manual_target.matches_exact(candidate)
                        or self._manual_target.matches_signature(candidate)
                    ),
                )
                for candidate in eligible_windows
            ],
        }
        if include_excluded:
            payload["excluded_windows"] = [
                candidate.to_dict(
                    is_attached=self._matches_attached_window(candidate),
                    is_manual_target=False,
                )
                for candidate in excluded_windows
            ]
        return payload


    def latest_vision_snapshot(self) -> dict[str, Any]:
        if not bool(self._config.llm_vision_enabled):
            return {}
        snapshot = dict(self._latest_vision_snapshot or {})
        image_base64 = str(self._latest_vision_snapshot_base64 or "")
        if not snapshot or not image_base64:
            return {}
        now = self._time_fn()
        if now >= float(snapshot.get("expires_at_monotonic") or 0.0):
            self._clear_vision_snapshot()
            return {}
        payload = {
            key: json_copy(value)
            for key, value in snapshot.items()
            if key != "expires_at_monotonic"
        }
        payload["vision_image_base64"] = image_base64
        return payload


    def resolve_manual_window_target(self, window_key: str) -> dict[str, Any]:
        normalized_key = str(window_key or "").strip()
        if not normalized_key:
            raise ValueError("window_key is required")
        eligible_windows, excluded_windows = self._scan_window_inventory(force=True)
        for candidate in eligible_windows:
            if candidate.window_key == normalized_key:
                return OcrWindowTarget(
                    mode="manual",
                    window_key=candidate.window_key,
                    process_name=candidate.process_name,
                    normalized_title=candidate.normalized_title,
                    pid=candidate.pid,
                    last_known_hwnd=candidate.hwnd,
                    selected_at=utc_now_iso(self._time_fn()),
                ).to_dict()
        for candidate in excluded_windows:
            if candidate.window_key == normalized_key:
                raise ValueError("window_key points to an excluded OCR window")
        raise ValueError("window_key not found among eligible OCR windows")


    def _matches_attached_window(self, candidate: DetectedGameWindow) -> bool:
        if self._attached_window is None:
            return False
        if candidate.hwnd and self._attached_window.hwnd and candidate.hwnd == self._attached_window.hwnd:
            return True
        return bool(candidate.pid and self._attached_window.pid and candidate.pid == self._attached_window.pid)


    def _prepare_window_inventory(
        self,
        windows: list[DetectedGameWindow],
    ) -> tuple[list[DetectedGameWindow], list[DetectedGameWindow]]:
        use_windows_foreground_api = bool(self._platform_fn())
        foreground_hwnd = (
            _foreground_window_handle() if use_windows_foreground_api else 0
        )
        prepared: list[DetectedGameWindow] = []
        for window in windows:
            candidate = replace(window)
            candidate.process_name = str(candidate.process_name or "").strip()
            candidate.title = str(candidate.title or "")
            candidate.class_name = str(candidate.class_name or "")
            candidate.exe_path = str(candidate.exe_path or "")
            candidate.pid = max(0, int(candidate.pid or 0))
            candidate.hwnd = max(0, int(candidate.hwnd or 0))
            candidate.area = max(0, int(candidate.area or 0))
            candidate.is_minimized = bool(candidate.is_minimized)
            if use_windows_foreground_api:
                foreground_match, _ = _foreground_matches_target(foreground_hwnd, candidate)
                candidate.is_foreground = foreground_match
            else:
                candidate.is_foreground = bool(candidate.is_foreground)
            candidate.score = float(max(candidate.area, 1))
            candidate = _classify_window_candidate(candidate)
            prepared.append(candidate)
        prepared.sort(key=_window_sort_key, reverse=True)
        eligible_windows = [candidate for candidate in prepared if candidate.eligible]
        excluded_windows = [candidate for candidate in prepared if not candidate.eligible]
        self._last_detected_windows = list(prepared)
        self._last_eligible_windows = list(eligible_windows)
        self._last_excluded_windows = list(excluded_windows)
        return eligible_windows, excluded_windows


    def _scan_raw_windows_cached(self, *, force: bool = False) -> list[DetectedGameWindow]:
        now = self._time_fn()
        if (
            not force
            and self._window_inventory_cache_at > 0.0
            and now - float(self._window_inventory_cache_at or 0.0) < _WINDOW_SCAN_CACHE_TTL_SECONDS
        ):
            return list(self._window_inventory_cache)
        scanned = list(self._window_scanner() or [])
        self._window_inventory_cache = list(scanned)
        self._window_inventory_cache_at = now
        return scanned


    def _scan_window_inventory(
        self,
        *,
        force: bool = False,
    ) -> tuple[list[DetectedGameWindow], list[DetectedGameWindow]]:
        # Window enumeration is now cross-platform via capture_platform.
        # The injected _window_scanner dispatches per platform; non-Windows
        # paths return [] gracefully (Wayland / missing pyobjc / etc.) so
        # we always run the scan and let the inventory cache absorb empties.
        scanned = self._scan_raw_windows_cached(force=force)
        return self._prepare_window_inventory(scanned)


    def _clear_vision_snapshot(self) -> None:
        self._latest_vision_snapshot = {}
        self._latest_vision_snapshot_base64 = ""


    def _vision_snapshot_runtime_status(self) -> dict[str, Any]:
        snapshot = dict(self._latest_vision_snapshot or {})
        if not snapshot or not self._latest_vision_snapshot_base64:
            return {
                "available": False,
                "captured_at": "",
                "expires_at": "",
                "source": "",
                "width": 0,
                "height": 0,
                "byte_size": 0,
            }
        if self._time_fn() >= float(snapshot.get("expires_at_monotonic") or 0.0):
            self._clear_vision_snapshot()
            return {
                "available": False,
                "captured_at": "",
                "expires_at": "",
                "source": "",
                "width": 0,
                "height": 0,
                "byte_size": 0,
            }
        return {
            "available": True,
            "captured_at": str(snapshot.get("captured_at") or ""),
            "expires_at": str(snapshot.get("expires_at") or ""),
            "source": str(snapshot.get("source") or ""),
            "width": int(snapshot.get("width") or 0),
            "height": int(snapshot.get("height") or 0),
            "byte_size": int(snapshot.get("byte_size") or 0),
        }


    def _remember_vision_snapshot(
        self,
        frame: Any,
        *,
        source: str,
        now: float,
    ) -> None:
        if not bool(self._config.llm_vision_enabled):
            self._clear_vision_snapshot()
            return
        if frame is None or not hasattr(frame, "save"):
            return
        try:
            image = frame.convert("RGB") if hasattr(frame, "convert") else frame
            max_px = max(64, int(self._config.llm_vision_max_image_px or 768))
            width, height = image.size
            if width <= 0 or height <= 0:
                return
            scale = min(1.0, float(max_px) / float(max(width, height)))
            if scale < 1.0:
                next_size = (max(1, int(width * scale)), max(1, int(height * scale)))
                image = image.resize(
                    next_size,
                    _PIL_RESAMPLING.LANCZOS if _PIL_RESAMPLING is not None else 1,
                )
                width, height = image.size
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=_VISION_SNAPSHOT_JPEG_QUALITY, optimize=True)
            raw = buffer.getvalue()
            if not raw:
                return
            expires_at = now + _VISION_SNAPSHOT_TTL_SECONDS
            self._latest_vision_snapshot_base64 = (
                "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")
            )
            self._latest_vision_snapshot = {
                "captured_at": utc_now_iso(now),
                "expires_at": utc_now_iso(expires_at),
                "expires_at_monotonic": expires_at,
                "source": source,
                "width": int(width),
                "height": int(height),
                "byte_size": len(raw),
                "ttl_seconds": _VISION_SNAPSHOT_TTL_SECONDS,
            }
        except Exception as exc:
            self._logger.debug("ocr_reader vision snapshot encoding skipped: {}", exc)


    def _select_target_window(
        self,
        windows: list[DetectedGameWindow],
        *,
        excluded_windows: list[DetectedGameWindow] | None = None,
        memory_reader_runtime: dict[str, Any] | None = None,
    ) -> WindowSelectionResult:
        excluded = list(excluded_windows or [])
        selection = WindowSelectionResult(
            selection_mode="manual" if self._manual_target.is_manual() else "auto",
            selection_detail="manual_target_active"
            if self._manual_target.is_manual()
            else "auto_candidate_scan",
            manual_target=self._manual_target,
            candidate_count=len(windows),
            excluded_candidate_count=len(excluded),
            last_exclude_reason=str(excluded[0].exclude_reason or "") if excluded else "",
        )
        preferred_pid = int((memory_reader_runtime or {}).get("pid") or 0)
        preferred_process_name = str(
            (memory_reader_runtime or {}).get("process_name") or ""
        ).strip().lower()
        memory_reader_status = str((memory_reader_runtime or {}).get("status") or "")
        prefer_memory_reader_window = (
            getattr(self._config, "reader_mode", READER_MODE_AUTO) == READER_MODE_AUTO
            and memory_reader_status in {"attaching", "active"}
            and (preferred_pid > 0 or bool(preferred_process_name))
        )

        def _matches_memory_reader_target(candidate: DetectedGameWindow) -> bool:
            if preferred_pid > 0 and candidate.pid == preferred_pid:
                return True
            return (
                bool(preferred_process_name)
                and str(candidate.process_name or "").strip().lower() == preferred_process_name
            )

        manual_target_overridden_by_memory_reader = False

        def _manual_target_allowed(candidate: DetectedGameWindow) -> bool:
            nonlocal manual_target_overridden_by_memory_reader
            if not prefer_memory_reader_window:
                return True
            if _matches_memory_reader_target(candidate):
                return True
            manual_target_overridden_by_memory_reader = True
            selection.selection_detail = "manual_target_overridden_by_memory_reader"
            return False

        def _clear_overridden_manual_target() -> None:
            if not manual_target_overridden_by_memory_reader:
                return
            self._manual_target = OcrWindowTarget()
            selection.selection_mode = "auto"
            selection.manual_target = self._manual_target

        def _memory_reader_minimized_window() -> DetectedGameWindow | None:
            if preferred_pid <= 0 and not preferred_process_name:
                return None
            for candidate in excluded:
                if str(candidate.exclude_reason or "") != "excluded_minimized_window":
                    continue
                if preferred_pid > 0 and candidate.pid == preferred_pid:
                    return candidate
                if (
                    preferred_process_name
                    and str(candidate.process_name or "").strip().lower()
                    == preferred_process_name
                ):
                    return candidate
            return None

        def _use_memory_reader_minimized_diagnostic(
            candidate: DetectedGameWindow,
        ) -> WindowSelectionResult:
            selection.selection_detail = "memory_reader_window_minimized"
            selection.last_exclude_reason = "excluded_minimized_window"
            selection.excluded_candidate_count = len(excluded)
            return selection

        if not windows:
            minimized_window = _memory_reader_minimized_window()
            if minimized_window is not None:
                foreground_hwnd = _foreground_window_handle()
                if (
                    foreground_hwnd
                    and foreground_hwnd != 0
                    and foreground_hwnd == minimized_window.hwnd
                ):
                    selection.target = minimized_window
                    selection.selection_detail = (
                        "memory_reader_minimized_overridden_by_foreground"
                    )
                    return selection
                return _use_memory_reader_minimized_diagnostic(minimized_window)
            selection.selection_detail = (
                "manual_target_unavailable_fallback_to_auto"
                if self._manual_target.is_manual()
                else "no_eligible_window"
            )
            if selection.selection_mode == "auto":
                use_windows_foreground_api = bool(self._platform_fn())
                foreground_hwnd = (
                    _foreground_window_handle() if use_windows_foreground_api else 0
                )
                for candidate in excluded:
                    candidate_foreground = (
                        bool(foreground_hwnd and candidate.hwnd == foreground_hwnd)
                        if use_windows_foreground_api
                        else bool(candidate.is_foreground)
                    )
                    if candidate_foreground:
                        selection.selection_detail = "foreground_window_needs_manual_confirmation"
                        break
            return selection

        if self._manual_target.is_manual():
            for candidate in windows:
                if (
                    (
                        self._manual_target.matches_exact(candidate)
                        or self._manual_target.matches_hwnd(candidate)
                    )
                    and _manual_target_allowed(candidate)
                ):
                    resolved_target = self._manual_target.resolved_for(candidate)
                    self._manual_target = resolved_target
                    selection.target = candidate
                    selection.selection_detail = "manual_target_exact"
                    selection.manual_target = resolved_target
                    selection.selected_by_manual = True
                    return selection
            for candidate in windows:
                if self._manual_target.matches_signature(candidate) and _manual_target_allowed(candidate):
                    resolved_target = self._manual_target.resolved_for(candidate)
                    self._manual_target = resolved_target
                    selection.target = candidate
                    selection.selection_detail = "manual_target_rebound"
                    selection.manual_target = resolved_target
                    selection.selected_by_manual = True
                    return selection
            if not manual_target_overridden_by_memory_reader:
                selection.selection_detail = "manual_target_unavailable_fallback_to_auto"

        if preferred_pid > 0:
            for candidate in windows:
                if candidate.pid == preferred_pid:
                    selection.target = candidate
                    if manual_target_overridden_by_memory_reader:
                        _clear_overridden_manual_target()
                        selection.selection_detail = "manual_target_overridden_by_memory_reader_pid"
                    elif selection.selection_mode == "auto":
                        selection.selection_detail = "memory_reader_pid"
                    return selection
        if preferred_process_name:
            for candidate in windows:
                if str(candidate.process_name or "").strip().lower() == preferred_process_name:
                    selection.target = candidate
                    if manual_target_overridden_by_memory_reader:
                        _clear_overridden_manual_target()
                        selection.selection_detail = "manual_target_overridden_by_memory_reader_process"
                    elif selection.selection_mode == "auto":
                        selection.selection_detail = "memory_reader_process"
                    return selection
        minimized_window = _memory_reader_minimized_window()
        if minimized_window is not None:
            return _use_memory_reader_minimized_diagnostic(minimized_window)
        if prefer_memory_reader_window:
            _clear_overridden_manual_target()
            selection.selection_detail = (
                "manual_target_overridden_by_memory_reader_unavailable"
                if manual_target_overridden_by_memory_reader
                else "memory_reader_target_unavailable"
            )
            return selection
        if self._attached_window is not None:
            for candidate in windows:
                if candidate.hwnd == self._attached_window.hwnd:
                    selection.target = candidate
                    if selection.selection_mode == "auto":
                        selection.selection_detail = "attached_hwnd"
                    return selection
            if self._attached_window.pid:
                for candidate in windows:
                    if candidate.pid == self._attached_window.pid:
                        selection.target = candidate
                        if selection.selection_mode == "auto":
                            selection.selection_detail = "attached_pid"
                        return selection
        if self._has_locked_target():
            for candidate in windows:
                if self._locked_target.matches_exact(candidate) or self._locked_target.matches_hwnd(candidate):
                    selection.target = candidate
                    if selection.selection_mode == "auto":
                        selection.selection_detail = "locked_target_exact"
                    return selection
            for candidate in windows:
                if self._locked_target.matches_signature(candidate):
                    selection.target = candidate
                    if selection.selection_mode == "auto":
                        selection.selection_detail = "locked_target_rebound"
                    return selection
            if selection.selection_mode == "auto":
                selection.selection_detail = "locked_target_unavailable"
            return selection
        use_windows_foreground_api = bool(self._platform_fn())
        foreground_hwnd = (
            _foreground_window_handle() if use_windows_foreground_api else 0
        )
        for candidate in windows:
            candidate_foreground = (
                bool(foreground_hwnd and candidate.hwnd == foreground_hwnd)
                if use_windows_foreground_api
                else bool(candidate.is_foreground)
            )
            if not candidate_foreground:
                continue
            if not _is_confident_auto_window(candidate):
                if selection.selection_mode == "auto":
                    selection.selection_detail = "foreground_window_needs_manual_confirmation"
                return selection
            selection.target = candidate
            if selection.selection_mode == "auto":
                selection.selection_detail = "foreground_window"
            return selection
        if len(windows) == 1:
            configured_profile = _lookup_capture_profile(
                self._capture_profiles,
                windows[0],
                stage=_AIHONG_DIALOGUE_STAGE,
            )
            if configured_profile is not None:
                selection.target = windows[0]
                if selection.selection_mode == "auto":
                    selection.selection_detail = "single_configured_profile_candidate"
                return selection
            if (
                _is_confident_auto_window(windows[0])
                and not _is_legacy_geometryless_auto_window(windows[0])
            ):
                selection.target = windows[0]
                if selection.selection_mode == "auto":
                    selection.selection_detail = "single_confident_candidate"
                return selection
        if len(windows) == 1 and _is_legacy_geometryless_auto_window(windows[0]):
            selection.target = windows[0]
            if selection.selection_mode == "auto":
                selection.selection_detail = "single_geometryless_candidate"
            return selection
        if selection.selection_mode == "auto":
            selection.selection_detail = "auto_detect_needs_manual_fallback"
        return selection
