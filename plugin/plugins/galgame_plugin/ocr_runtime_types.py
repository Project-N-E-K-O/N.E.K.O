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

from .ocr_text_normalize import *
from .ocr_capture_profile import *


__all__ = [
    "DetectedGameWindow",
    "OCR_READER_BRIDGE_VERSION",
    "OCR_READER_DEFAULT_ENGINE",
    "OCR_READER_GAME_ID_PREFIX",
    "OCR_READER_ROUTE_ID",
    "OCR_READER_UNKNOWN_SCENE",
    "OCR_READER_VERSION",
    "OcrBackendDescriptor",
    "OcrCaptureProfile",
    "OcrExtractionResult",
    "OcrReaderBackendRuntime",
    "OcrReaderCaptureRuntime",
    "OcrReaderObservationRuntime",
    "OcrReaderPollRuntime",
    "OcrReaderProfileRuntime",
    "OcrReaderRuntime",
    "OcrReaderSessionRuntime",
    "OcrReaderStatusRuntime",
    "OcrReaderTargetRuntime",
    "OcrReaderTickResult",
    "OcrReaderWindowRuntime",
    "OcrTextBox",
    "OcrWindowTarget",
    "ParsedOcrCaptureBucket",
    "ParsedOcrCaptureProcessConfig",
    "ResolvedOcrCaptureSelection",
    "SelectedOcrBackendPlan",
    "WindowSelectionResult",
    "_ASCII_TOKEN_RE",
    "_AUTO_TARGET_DENY_PROCESS_NAMES",
    "_BACKEND_PLAN_CACHE_TTL_SECONDS",
    "_BACKGROUND_CANDIDATE_EARLY_CANDIDATE_MAX_SECONDS",
    "_BACKGROUND_CANDIDATE_EARLY_COMMIT_DISTANCE_MARGIN",
    "_BACKGROUND_CANDIDATE_EARLY_COMMIT_TEXT_GAP_SECONDS",
    "_BACKGROUND_CAPTURE_BACKEND_PAUSE_SECONDS",
    "_BACKGROUND_HASH_BOTTOM_INSET_RATIO",
    "_BACKGROUND_HASH_DIALOGUE_SAMPLE_INTERVAL_SECONDS",
    "_BACKGROUND_HASH_MIN_INTERVAL_SECONDS",
    "_BACKGROUND_SCENE_CHANGE_CONFIRM_POLLS",
    "_BACKGROUND_SCENE_CHANGE_DISTANCE",
    "_BACKGROUND_SCENE_CHANGE_FORCE_DISTANCE",
    "_BACKGROUND_SCENE_HASH_SIZE",
    "_CAPTURE_BACKEND_AUTO",
    "_CAPTURE_BACKEND_DXCAM",
    "_CAPTURE_BACKEND_IMAGEGRAB",
    "_CAPTURE_BACKEND_MSS",
    "_CAPTURE_BACKEND_PRINTWINDOW",
    "_CAPTURE_BACKEND_PYAUTOGUI",
    "_CAPTURE_BACKEND_SMART",
    "_CaptureStillRunning",
    "_CaptureTimedOut",
    "_CJK_CHAR_RE",
    "_DIALOGUE_BLOCK_CONTINUATION_MAX_SECONDS",
    "_DIALOGUE_BLOCK_NO_TEXT_GAP_POLLS",
    "_DIALOGUE_BLOCK_SCREEN_TYPES",
    "_DIALOGUE_BOUNDARY_SCREEN_TYPES",
    "_DIALOGUE_BOUNDARY_TITLE_RE",
    "_DIALOGUE_LIKE_CLASSIFICATION_TYPES",
    "_DIALOGUE_LINE_MARKERS",
    "_DXCAM_GRAB_RETRY_ATTEMPTS",
    "_DXCAM_GRAB_RETRY_DELAY_SECONDS",
    "_ENGLISH_GAME_OVERLAY_WORDS",
    "_FOREGROUND_ADVANCE_STABLE_GRACE_SECONDS",
    "_GAME_OVERLAY_TEXT_GUARD_SUBSTRINGS",
    "_HANGUL_RE",
    "_HELPER_CLASS_NAMES",
    "_HIRAGANA_RE",
    "_JA_MARKER_WORDS",
    "_KANA_BUD_RE",
    "_KANA_CHAR_RE",
    "_KATAKANA_RE",
    "_KEYBOARD_ADVANCE_VK_CODES",
    "_KNOWN_SCREEN_SKIP_BYPASS_SECONDS",
    "_LOGGER",
    "_MenuConsumeResult",
    "_NARRATION_PAREN_RE",
    "_NARRATION_QUOTE_RE",
    "_NON_ENGLISH_GAME_OVERLAY_SUBSTRINGS",
    "_OCR_CAPTURE_TIMEOUT_SECONDS",
    "_OCR_DIALOGUE_STRONG_PUNCTUATION_RE",
    "_OCR_DIALOGUE_WEAK_PUNCTUATION_RE",
    "_OCR_FOLLOWUP_CONFIRM_DELAY_SECONDS",
    "_OCR_LINE_ID_MAX_COLLISION_SUFFIX",
    "_OCR_MAX_ABANDONED_CAPTURE_WORKERS",
    "_OCR_PREPARE_MAX_LONG_EDGE",
    "_OCR_PREPARE_TARGET_LONG_EDGE",
    "_OCR_PREPARE_UPSCALE_SOURCE_LONG_EDGE",
    "_OCR_STABILITY_IGNORED_CHARS_RE",
    "_OCR_TRAILING_GARBAGE_AFTER_BRACKET_RE",
    "_OCR_TRAILING_GARBAGE_AFTER_DASH_RE",
    "_OCR_TRAILING_GARBAGE_AFTER_SENTENCE_RE",
    "_OCR_TRAILING_ORPHAN_AFTER_SENTENCE_RE",
    "_OcrLangDetector",
    "_OVERLAY_PROCESS_NAME_SUBSTRINGS",
    "_OVERLAY_WINDOW_TITLE_SUBSTRINGS",
    "_PENDING_VISUAL_SCENE_MAX_SECONDS",
    "_PUNCTUATION_CONFUSION_FIXES",
    "_RAPIDOCR_INFERENCE_LOCK",
    "_RAPIDOCR_RUNTIME_CACHE",
    "_RAPIDOCR_RUNTIME_CACHE_LOCK",
    "_RAPIDOCR_RUNTIME_IDLE_TTL_SECONDS",
    "_RapidOcrToken",
    "_RuntimeFieldProxy",
    "_acquire_rapidocr_runtime_cache",
    "_SCENE_CHANGE_COOLDOWN_SECONDS",
    "_SCREEN_AWARENESS_LATENCY_MODES",
    "_SCREEN_AWARENESS_LATENCY_MODE_AGGRESSIVE",
    "_SCREEN_AWARENESS_LATENCY_MODE_BALANCED",
    "_SCREEN_AWARENESS_LATENCY_MODE_FULL",
    "_SCREEN_AWARENESS_LATENCY_MODE_OFF",
    "_SELF_UI_GUARD_SUBSTRINGS",
    "_SELF_WINDOW_PATH_SUBSTRINGS",
    "_SELF_WINDOW_TITLE_SUBSTRINGS",
    "_SPEAKER_BRACKET_RE",
    "_SPEAKER_COLON_RE",
    "_SPEAKER_PAREN_PREFIX_RE",
    "_SPEAKER_PAREN_SUFFIX_RE",
    "_SPEAKER_QUOTE_RE",
    "_STALE_CAPTURE_FRAME_THRESHOLD",
    "_StableOcrTextState",
    "_TickPreflightResult",
    "_TickTargetContext",
    "_VISION_SNAPSHOT_JPEG_QUALITY",
    "_VISION_SNAPSHOT_TTL_SECONDS",
    "_WH_KEYBOARD_LL",
    "_WH_MOUSE_LL",
    "_WINDOW_SCAN_CACHE_TTL_SECONDS",
    "_WINDOW_SPACE_RE",
    "_WM_KEYDOWN",
    "_WM_LBUTTONDOWN",
    "_WM_LBUTTONUP",
    "_WM_MOUSEWHEEL",
    "_WM_SYSKEYDOWN",
    "_ZH_MARKER_WORDS",
    "_aihong_choice_boxes",
    "_aihong_choices_region_source_height",
    "_average_ocr_box_confidence",
    "_bounded_confidence_or_zero",
    "_build_window_key",
    "_builtin_capture_profile_for_target",
    "_builtin_capture_profile_for_target_stage",
    "_canonical_choice_candidate_text",
    "_classify_cjk_text",
    "_clean_ocr_dialogue_text",
    "_coerce_choice_lines",
    "_coerce_plain_choice_lines",
    "_coerce_prefixed_choice_lines",
    "_drop_ocr_chrome_noise_lines",
    "_extraction_choice_bounds_metadata",
    "_filter_boxes_to_region",
    "_fix_ocr_punctuation_confusion",
    "_float_or_zero",
    "_frame_choice_bounds_metadata",
    "_get_rapidocr_runtime_cache",
    "_release_rapidocr_runtime_cache",
    "_join_ocr_segments",
    "_looks_like_dialogue_line",
    "_looks_like_english_overlay_label",
    "_looks_like_game_overlay_normalized_text",
    "_looks_like_game_overlay_text",
    "_looks_like_noise_normalized_text",
    "_looks_like_noise_ocr_text",
    "_looks_like_non_english_overlay_label",
    "_looks_like_ocr_dialogue_normalized_text",
    "_looks_like_ocr_dialogue_text",
    "_looks_like_self_ui_text",
    "_looks_like_self_window_path",
    "_looks_like_self_window_title",
    "_lookup_capture_profile",
    "_matches_aihong_target",
    "_normalize_window_title",
    "_ocr_game_id_from_process",
    "_ocr_score_weight",
    "_ocr_stability_key",
    "_ocr_stability_keys_match",
    "_parse_configured_capture_profiles",
    "_perceptual_hash_image",
    "_prefer_ocr_stability_text",
    "_prepare_ocr_image",
    "_prune_rapidocr_runtime_cache",
    "_rapidocr_lines_from_output",
    "_rapidocr_points",
    "_rapidocr_runtime_cache_key",
    "_rapidocr_text_from_output",
    "_rapidocr_tokens_from_output",
    "_resolve_stage_capture_profile",
    "_score_ocr_text",
    "_should_insert_ascii_space",
    "_significant_char_count",
    "_store_rapidocr_runtime_cache",
    "_stripped_ocr_lines",
    "_uses_manual_capture_profile",
    "_weighted_ocr_score",
    "utc_now_iso",
]

OCR_READER_VERSION = "0.1.0"
OCR_READER_BRIDGE_VERSION = f"ocr-reader-{OCR_READER_VERSION}"
OCR_READER_GAME_ID_PREFIX = "ocr-"
OCR_READER_UNKNOWN_SCENE = "ocr:unknown_scene"
OCR_READER_ROUTE_ID = ""
OCR_READER_DEFAULT_ENGINE = "unknown"
_OCR_LINE_ID_MAX_COLLISION_SUFFIX = 10000
_LOGGER = logging.getLogger(__name__)
_VISION_SNAPSHOT_TTL_SECONDS = 8.0
_VISION_SNAPSHOT_JPEG_QUALITY = 72
_WM_MOUSEWHEEL = 0x020A
_WM_LBUTTONDOWN = 0x0201
_WM_LBUTTONUP = 0x0202
_WM_KEYDOWN = 0x0100
_WM_SYSKEYDOWN = 0x0104
_WH_KEYBOARD_LL = 13
_WH_MOUSE_LL = 14
_KEYBOARD_ADVANCE_VK_CODES = frozenset({
    0x0D,  # Enter
    0x20,  # Space
    0x22,  # PageDown
    0x28,  # Down
})
_OCR_FOLLOWUP_CONFIRM_DELAY_SECONDS = 0.18
_OCR_CAPTURE_TIMEOUT_SECONDS = 12.0
_OCR_MAX_ABANDONED_CAPTURE_WORKERS = 1
class _CaptureStillRunning(TimeoutError):
    """Backpressure: previous capture worker has not finished yet."""


class _CaptureTimedOut(TimeoutError):
    """A single capture/OCR call exceeded the deadline."""

_FOREGROUND_ADVANCE_STABLE_GRACE_SECONDS = 2.0
_CAPTURE_BACKEND_AUTO = "auto"
_CAPTURE_BACKEND_SMART = "smart"
_CAPTURE_BACKEND_DXCAM = "dxcam"
_CAPTURE_BACKEND_MSS = "mss"
_CAPTURE_BACKEND_PYAUTOGUI = "pyautogui"
# Legacy alias kept so existing user configs with "imagegrab" still load; mapped to MSS.
_CAPTURE_BACKEND_IMAGEGRAB = "imagegrab"
_CAPTURE_BACKEND_PRINTWINDOW = "printwindow"
_SCREEN_AWARENESS_LATENCY_MODE_OFF = "off"
_SCREEN_AWARENESS_LATENCY_MODE_BALANCED = "balanced"
_SCREEN_AWARENESS_LATENCY_MODE_FULL = "full"
_SCREEN_AWARENESS_LATENCY_MODE_AGGRESSIVE = "aggressive"
_SCREEN_AWARENESS_LATENCY_MODES = {
    _SCREEN_AWARENESS_LATENCY_MODE_OFF,
    _SCREEN_AWARENESS_LATENCY_MODE_BALANCED,
    _SCREEN_AWARENESS_LATENCY_MODE_FULL,
    _SCREEN_AWARENESS_LATENCY_MODE_AGGRESSIVE,
}
_DXCAM_GRAB_RETRY_ATTEMPTS = 2
_DXCAM_GRAB_RETRY_DELAY_SECONDS = 0.05
_STALE_CAPTURE_FRAME_THRESHOLD = 3
_WINDOW_SCAN_CACHE_TTL_SECONDS = 5.0
_RAPIDOCR_RUNTIME_IDLE_TTL_SECONDS = 300.0
_RAPIDOCR_RUNTIME_CACHE_LOCK = threading.RLock()
_RAPIDOCR_RUNTIME_CACHE: dict[tuple[str, str, str, str, str], tuple[Any, float]] = {}
_RAPIDOCR_RUNTIME_CACHE_OWNERS: dict[tuple[str, str, str, str, str], int] = {}
_RAPIDOCR_INFERENCE_LOCK = threading.Lock()
_OCR_PREPARE_UPSCALE_SOURCE_LONG_EDGE = 900
_OCR_PREPARE_TARGET_LONG_EDGE = 1400
_OCR_PREPARE_MAX_LONG_EDGE = 1600
_BACKGROUND_HASH_MIN_INTERVAL_SECONDS = 1.0
_BACKGROUND_HASH_DIALOGUE_SAMPLE_INTERVAL_SECONDS = 2.0
_BACKGROUND_HASH_BOTTOM_INSET_RATIO = 0.60
_BACKEND_PLAN_CACHE_TTL_SECONDS = 5.0
_BACKGROUND_SCENE_HASH_SIZE = 8
_BACKGROUND_SCENE_CHANGE_DISTANCE = 28
_BACKGROUND_SCENE_CHANGE_FORCE_DISTANCE = 40
_BACKGROUND_SCENE_CHANGE_CONFIRM_POLLS = 2
_PENDING_VISUAL_SCENE_MAX_SECONDS = 5.0
_SCENE_CHANGE_COOLDOWN_SECONDS = 15.0
_DIALOGUE_BLOCK_CONTINUATION_MAX_SECONDS = 5.0
_DIALOGUE_BLOCK_NO_TEXT_GAP_POLLS = 2
_BACKGROUND_CANDIDATE_EARLY_COMMIT_TEXT_GAP_SECONDS = 6.0
_BACKGROUND_CANDIDATE_EARLY_COMMIT_DISTANCE_MARGIN = 4
_BACKGROUND_CANDIDATE_EARLY_CANDIDATE_MAX_SECONDS = 25.0
_DIALOGUE_BLOCK_SCREEN_TYPES = frozenset(
    {
        "",
        OCR_CAPTURE_PROFILE_STAGE_DEFAULT,
        OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
        "dialogue",
        "primary_dialogue",
    }
)
_DIALOGUE_BOUNDARY_SCREEN_TYPES = frozenset(
    {
        OCR_CAPTURE_PROFILE_STAGE_TITLE,
        OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD,
        OCR_CAPTURE_PROFILE_STAGE_CONFIG,
        OCR_CAPTURE_PROFILE_STAGE_MENU,
        OCR_CAPTURE_PROFILE_STAGE_TRANSITION,
        OCR_CAPTURE_PROFILE_STAGE_GALLERY,
        OCR_CAPTURE_PROFILE_STAGE_MINIGAME,
        OCR_CAPTURE_PROFILE_STAGE_GAME_OVER,
    }
)
_DIALOGUE_LIKE_CLASSIFICATION_TYPES = frozenset(
    {
        "",
        OCR_CAPTURE_PROFILE_STAGE_DEFAULT,
        OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
        "dialogue",
        "primary_dialogue",
    }
)
_KNOWN_SCREEN_SKIP_BYPASS_SECONDS = 1.0
_DIALOGUE_BOUNDARY_TITLE_RE = re.compile(
    r"^\s*(?:第[一二三四五六七八九十百千万0-9]+[章章节幕話话]|"
    r"[0-9]{1,4}[./-][0-9]{1,2}(?:[./-][0-9]{1,2})?|"
    r"(?:上午|下午|清晨|黄昏|夜晚|深夜|翌日|次日|三年前|数日后))\s*$"
)
_BACKGROUND_CAPTURE_BACKEND_PAUSE_SECONDS = 5.0


def utc_now_iso(now: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if now is None else now))


def _ocr_game_id_from_process(name: str) -> str:
    normalized_name = str(name or "").strip().lower()
    digest = hashlib.sha1(normalized_name.encode("utf-8")).hexdigest()[:12]
    return f"{OCR_READER_GAME_ID_PREFIX}{digest}"


def _build_window_key(*, process_name: str, pid: int, hwnd: int, title: str) -> str:
    payload = f"{process_name.strip().lower()}|{max(0, int(pid))}|{max(0, int(hwnd))}|{_normalize_window_title(title)}"
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f"ocrwin:{digest}"


def _rapidocr_runtime_cache_key(
    *,
    install_target_dir_raw: str,
    engine_type: str,
    lang_type: str,
    model_type: str,
    ocr_version: str,
) -> tuple[str, str, str, str, str]:
    return (
        str(install_target_dir_raw or "").strip(),
        str(engine_type or "").strip().lower(),
        str(lang_type or "").strip().lower(),
        str(model_type or "").strip().lower(),
        str(ocr_version or "").strip(),
    )


def _prune_rapidocr_runtime_cache(now: float) -> None:
    with _RAPIDOCR_RUNTIME_CACHE_LOCK:
        stale_keys = [
            key
            for key, (_runtime, last_used_at) in _RAPIDOCR_RUNTIME_CACHE.items()
            if (
                now - float(last_used_at or 0.0) >= _RAPIDOCR_RUNTIME_IDLE_TTL_SECONDS
                and _RAPIDOCR_RUNTIME_CACHE_OWNERS.get(key, 0) <= 0
            )
        ]
        for key in stale_keys:
            _RAPIDOCR_RUNTIME_CACHE.pop(key, None)
            _RAPIDOCR_RUNTIME_CACHE_OWNERS.pop(key, None)


def _get_rapidocr_runtime_cache(
    key: tuple[str, str, str, str, str],
    *,
    now: float,
) -> Any | None:
    with _RAPIDOCR_RUNTIME_CACHE_LOCK:
        cached = _RAPIDOCR_RUNTIME_CACHE.get(key)
        if cached is None:
            _RAPIDOCR_RUNTIME_CACHE_OWNERS.pop(key, None)
            return None
        runtime, last_used_at = cached
        if (
            now - float(last_used_at or 0.0) >= _RAPIDOCR_RUNTIME_IDLE_TTL_SECONDS
            and _RAPIDOCR_RUNTIME_CACHE_OWNERS.get(key, 0) <= 0
        ):
            _RAPIDOCR_RUNTIME_CACHE.pop(key, None)
            _RAPIDOCR_RUNTIME_CACHE_OWNERS.pop(key, None)
            return None
        _RAPIDOCR_RUNTIME_CACHE[key] = (runtime, now)
        return runtime


def _store_rapidocr_runtime_cache(
    key: tuple[str, str, str, str, str],
    runtime: Any,
    *,
    now: float,
) -> None:
    with _RAPIDOCR_RUNTIME_CACHE_LOCK:
        _prune_rapidocr_runtime_cache(now)
        if key not in _RAPIDOCR_RUNTIME_CACHE:
            _RAPIDOCR_RUNTIME_CACHE_OWNERS[key] = 0
        _RAPIDOCR_RUNTIME_CACHE[key] = (runtime, now)


def _acquire_rapidocr_runtime_cache(
    key: tuple[str, str, str, str, str],
) -> None:
    with _RAPIDOCR_RUNTIME_CACHE_LOCK:
        _RAPIDOCR_RUNTIME_CACHE_OWNERS[key] = (
            _RAPIDOCR_RUNTIME_CACHE_OWNERS.get(key, 0) + 1
        )


def _release_rapidocr_runtime_cache(
    key: tuple[str, str, str, str, str],
    *,
    runtime: Any | None = None,
    owner_acquired: bool = False,
) -> bool:
    """Drop a cached native runtime when its owning backend is closed."""
    with _RAPIDOCR_RUNTIME_CACHE_LOCK:
        cached = _RAPIDOCR_RUNTIME_CACHE.get(key)
        if cached is None:
            return False
        if runtime is not None and cached[0] is not runtime:
            return False
        if owner_acquired:
            remaining = max(0, _RAPIDOCR_RUNTIME_CACHE_OWNERS.get(key, 0) - 1)
            if remaining:
                _RAPIDOCR_RUNTIME_CACHE_OWNERS[key] = remaining
                return False
            _RAPIDOCR_RUNTIME_CACHE_OWNERS.pop(key, None)
        else:
            _RAPIDOCR_RUNTIME_CACHE_OWNERS.pop(key, None)
        _RAPIDOCR_RUNTIME_CACHE.pop(key, None)
        return True


def _aihong_choice_boxes(
    choices: list[str],
    boxes: list[OcrTextBox],
) -> list[dict[str, float] | None]:
    remaining = list(boxes)
    matched: list[dict[str, float] | None] = []
    for choice in choices:
        choice_text = normalize_text(str(choice or "")).strip()
        found_index = -1
        best_dist = float("inf")
        for index, box in enumerate(remaining):
            box_text = _normalize_aihong_choice_box_text(box.text)
            if not box_text:
                continue
            dist = _levenshtein_distance(box_text, choice_text)
            max_allowed = max(2, int(len(choice_text) * 0.3))
            if dist <= max_allowed and dist < best_dist:
                best_dist = dist
                found_index = index
                if dist == 0:
                    break
        if found_index < 0:
            matched.append(None)
            continue
        box = remaining.pop(found_index)
        matched.append(
            {
                "left": float(box.left),
                "top": float(box.top),
                "right": float(box.right),
                "bottom": float(box.bottom),
            }
        )
    return matched


def _filter_boxes_to_region(
    boxes: list[OcrTextBox],
    *,
    source_height: float,
    top_ratio: float,
    bottom_inset_ratio: float,
) -> list[OcrTextBox]:
    """Keep OCR boxes whose y bounds are within the capture image region.

    source_height must use the same coordinate space as the OCR boxes.
    """
    if not boxes or source_height <= 0:
        return boxes
    top_y = source_height * top_ratio
    bottom_y = source_height * (1.0 - bottom_inset_ratio)
    if bottom_y <= top_y:
        return []
    result: list[OcrTextBox] = []
    for box in boxes:
        try:
            box_top = float(getattr(box, "top", 0) or 0)
            box_bottom = float(getattr(box, "bottom", 0) or 0)
        except (TypeError, ValueError):
            continue
        if box_top >= top_y and box_bottom <= bottom_y:
            result.append(box)
    return result


def _float_or_zero(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _aihong_choices_region_source_height(
    boxes: list[OcrTextBox],
    metadata: dict[str, Any] | None,
) -> float:
    data = metadata if isinstance(metadata, dict) else {}
    source_size = data.get("source_size")
    if isinstance(source_size, dict):
        source_height = _float_or_zero(source_size.get("height"))
        if source_height > 0:
            return source_height

    window_rect = data.get("window_rect")
    if isinstance(window_rect, dict):
        source_height = _float_or_zero(window_rect.get("height"))
        if source_height > 0:
            return source_height
        top = _float_or_zero(window_rect.get("top"))
        bottom = _float_or_zero(window_rect.get("bottom"))
        if bottom > top:
            return bottom - top

    max_bottom = 0.0
    for box in boxes:
        max_bottom = max(max_bottom, _float_or_zero(getattr(box, "bottom", 0)))
    return max_bottom if max_bottom > 0 else 1080.0


def _extraction_choice_bounds_metadata(extraction: "OcrExtractionResult") -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if extraction.bounds_coordinate_space:
        metadata["bounds_coordinate_space"] = extraction.bounds_coordinate_space
    if extraction.source_size:
        metadata["source_size"] = dict(extraction.source_size)
    if extraction.capture_rect:
        metadata["capture_rect"] = dict(extraction.capture_rect)
    if extraction.window_rect:
        metadata["window_rect"] = dict(extraction.window_rect)
    return metadata


def _frame_choice_bounds_metadata(frame: Any, *, text_source: str = "") -> dict[str, Any]:
    info = getattr(frame, "info", {}) if frame is not None else {}
    metadata: dict[str, Any] = {}
    if isinstance(info, dict):
        bounds_coordinate_space = str(info.get("galgame_bounds_coordinate_space") or "")
        if bounds_coordinate_space:
            metadata["bounds_coordinate_space"] = bounds_coordinate_space
        source_size = info.get("galgame_source_size")
        if isinstance(source_size, dict):
            metadata["source_size"] = dict(source_size)
        capture_rect = info.get("galgame_capture_rect")
        if isinstance(capture_rect, dict):
            metadata["capture_rect"] = dict(capture_rect)
        window_rect = info.get("galgame_window_rect")
        if isinstance(window_rect, dict):
            metadata["window_rect"] = dict(window_rect)
    if text_source:
        metadata["text_source"] = text_source
    return metadata


def _prepare_ocr_image(image: Any, *, apply_filters: bool = True) -> Any:
    from PIL import Image, ImageFilter, ImageOps

    resampling = getattr(Image, "Resampling", Image)
    prepared = image.convert("L")
    prepared = ImageOps.autocontrast(prepared)
    long_edge = max(prepared.width, prepared.height, 1)
    scale = 1.0
    if long_edge < _OCR_PREPARE_UPSCALE_SOURCE_LONG_EDGE:
        scale = min(2.0, _OCR_PREPARE_TARGET_LONG_EDGE / float(long_edge))
    elif long_edge > _OCR_PREPARE_MAX_LONG_EDGE:
        scale = _OCR_PREPARE_MAX_LONG_EDGE / float(long_edge)
    if abs(scale - 1.0) > 0.01:
        prepared = prepared.resize(
            (
                max(int(round(prepared.width * scale)), 1),
                max(int(round(prepared.height * scale)), 1),
            ),
            resampling.LANCZOS,
        )
        if apply_filters:
            prepared = prepared.filter(ImageFilter.SHARPEN)
    return prepared


def _perceptual_hash_image(frame: Any, *, size: int = _BACKGROUND_SCENE_HASH_SIZE) -> str:
    if frame is None:
        return ""
    try:
        from PIL import Image

        resampling = getattr(Image, "Resampling", Image)
        image = frame.convert("L").resize((size, size), resampling.BILINEAR)
        pixels = list(image.getdata())
        if not pixels:
            return ""
        average = sum(int(pixel) for pixel in pixels) / len(pixels)
        bits = "".join("1" if int(pixel) >= average else "0" for pixel in pixels)
        width = max(1, (size * size + 3) // 4)
        return f"{int(bits, 2):0{width}x}"
    except Exception:
        _LOGGER.debug("ocr_reader perceptual hash failed", exc_info=True)
        return ""


def _rapidocr_points(box: Any) -> list[tuple[float, float]]:
    if hasattr(box, "tolist"):
        box = box.tolist()
    if not isinstance(box, (list, tuple)):
        return []
    points: list[tuple[float, float]] = []
    for point in box:
        if hasattr(point, "tolist"):
            point = point.tolist()
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        try:
            points.append((float(point[0]), float(point[1])))
        except (TypeError, ValueError):
            continue
    return points


@dataclass(slots=True)
class _RapidOcrToken:
    text: str
    score: float
    left: float
    right: float
    top: float
    bottom: float
    height: float


@dataclass(slots=True)
class OcrTextBox:
    text: str
    left: float
    top: float
    right: float
    bottom: float
    score: float = 0.0

    def to_dict(self) -> dict[str, float | str]:
        return {
            "text": self.text,
            "left": self.left,
            "top": self.top,
            "right": self.right,
            "bottom": self.bottom,
            "score": self.score,
        }


def _rapidocr_tokens_from_output(raw_output: Any) -> list[_RapidOcrToken]:
    payload = raw_output[0] if isinstance(raw_output, tuple) and raw_output else raw_output
    if not isinstance(payload, list):
        return []
    tokens: list[_RapidOcrToken] = []
    low_confidence_count = 0
    for item in payload:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        box, text, score = item[0], item[1], item[2]
        normalized = normalize_text(str(text or "")).strip()
        if not normalized:
            continue
        try:
            score_value = float(score)
        except (TypeError, ValueError):
            score_value = 0.0
        if score_value < 0.30:
            low_confidence_count += 1
            continue
        points = _rapidocr_points(box)
        if not points:
            continue
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        top = min(ys)
        bottom = max(ys)
        tokens.append(
            _RapidOcrToken(
                text=normalized,
                score=score_value,
                left=min(xs),
                right=max(xs),
                top=top,
                bottom=bottom,
                height=max(bottom - top, 1.0),
            )
        )
    if low_confidence_count:
        _LOGGER.debug(
            "rapidocr discarded %d low-confidence token(s)",
            low_confidence_count,
        )
    return tokens


def _rapidocr_lines_from_output(raw_output: Any) -> list[tuple[str, float, OcrTextBox]]:
    tokens = _rapidocr_tokens_from_output(raw_output)
    if not tokens:
        return []
    tokens.sort(key=lambda token: (token.top, token.left))
    token_heights = sorted(max(1.0, float(token.height or 1.0)) for token in tokens)
    median_height = token_heights[len(token_heights) // 2]
    bucket_size = max(1.0, median_height * 0.75)
    line_entries: list[dict[str, Any]] = []
    line_buckets: dict[int, list[dict[str, Any]]] = {}

    def _bucket_key(center: float) -> int:
        return int(center // bucket_size)

    def _add_line_bucket(entry: dict[str, Any]) -> None:
        line_buckets.setdefault(int(entry["bucket"]), []).append(entry)

    def _remove_line_bucket(entry: dict[str, Any]) -> None:
        bucket = line_buckets.get(int(entry["bucket"]))
        if bucket is None:
            return
        for index, item in enumerate(bucket):
            if item is entry:
                del bucket[index]
                break
        else:
            return
        if not bucket:
            line_buckets.pop(int(entry["bucket"]), None)

    def _refresh_line_entry(entry: dict[str, Any], *, top: float, bottom: float) -> float:
        center = (top + bottom) / 2.0
        new_bucket = _bucket_key(center)
        if new_bucket != int(entry["bucket"]):
            _remove_line_bucket(entry)
            entry["bucket"] = new_bucket
            _add_line_bucket(entry)
        entry["top"] = top
        entry["bottom"] = bottom
        entry["center"] = center
        return max(1.0, bottom - top)

    max_line_height = max(1.0, tokens[0].height)
    for token in tokens:
        token_center = (token.top + token.bottom) / 2.0
        best_entry: dict[str, Any] | None = None
        best_distance = float("inf")
        search_radius = max(2, int(max(max_line_height, token.height) / bucket_size) + 2)
        candidate_entries: list[dict[str, Any]] = []
        token_bucket = _bucket_key(token_center)
        for bucket_key in range(token_bucket - search_radius, token_bucket + search_radius + 1):
            candidate_entries.extend(line_buckets.get(bucket_key, ()))
        for entry in candidate_entries:
            line_top = float(entry["top"])
            line_bottom = float(entry["bottom"])
            line_center = float(entry["center"])
            threshold = max((line_bottom - line_top) * 0.6, token.height * 0.6, token.height * 0.3)
            distance = abs(token_center - line_center)
            if distance <= threshold and distance < best_distance:
                best_entry = entry
                best_distance = distance
        if best_entry is not None:
            best_entry["tokens"].append(token)
            line_height = _refresh_line_entry(
                best_entry,
                top=min(float(best_entry["top"]), token.top),
                bottom=max(float(best_entry["bottom"]), token.bottom),
            )
            max_line_height = max(max_line_height, line_height)
        else:
            entry = {
                "tokens": [token],
                "top": token.top,
                "bottom": token.bottom,
                "center": token_center,
                "bucket": _bucket_key(token_center),
            }
            line_entries.append(entry)
            _add_line_bucket(entry)
            max_line_height = max(max_line_height, token.height)
    rendered_lines: list[str] = []
    line_results: list[tuple[str, float, OcrTextBox]] = []
    lines = [list(entry["tokens"]) for entry in line_entries]
    lines.sort(key=lambda line: (min(item.top for item in line), min(item.left for item in line)))
    for line in lines:
        line.sort(key=lambda item: item.left)
        text = _join_ocr_segments([item.text for item in line])
        if not text:
            continue
        line_score = _weighted_ocr_score(
            (item.score, _ocr_score_weight(item.text)) for item in line
        )
        rendered_lines.append(text)
        line_results.append(
            (
                text,
                line_score,
                OcrTextBox(
                    text=text,
                    left=min(item.left for item in line),
                    top=min(item.top for item in line),
                    right=max(item.right for item in line),
                    bottom=max(item.bottom for item in line),
                    score=line_score,
                ),
            )
        )
    if len(line_results) > 1:
        has_anchor_line = any(
            _significant_char_count(text) > 2 or float(score or 0.0) >= 0.80
            for text, score, _box in line_results
        )
        if has_anchor_line:
            filtered_results = [
                item
                for item in line_results
                if not (
                    (
                        _significant_char_count(item[0]) <= 2
                        and float(item[1] or 0.0) < 0.60
                    )
                    or (
                        bool(re.fullmatch(r"[A-Za-z0-9]", normalize_text(item[0])))
                        and float(item[1] or 0.0) < 0.80
                    )
                )
            ]
            if filtered_results:
                line_results = filtered_results
    text = "\n".join(line for line in rendered_lines if line)
    if len(line_results) != len(rendered_lines):
        text = "\n".join(text for text, _score, _box in line_results if text)
    normalized = normalize_text(text)
    if not normalized:
        return []
    average_score = _weighted_ocr_score(
        (score, _ocr_score_weight(text)) for text, score, _box in line_results
    )
    if _significant_char_count(normalized) < 4 and average_score < 0.55:
        return []
    return line_results


def _rapidocr_text_from_output(raw_output: Any) -> str:
    lines = _rapidocr_lines_from_output(raw_output)
    if not lines:
        return ""
    return "\n".join(text for text, _score, _box in lines)


@dataclass(slots=True)
class DetectedGameWindow:
    hwnd: int = 0
    title: str = ""
    process_name: str = ""
    pid: int = 0
    class_name: str = ""
    exe_path: str = ""
    width: int = 0
    height: int = 0
    area: int = 0
    is_foreground: bool = False
    is_minimized: bool = False
    eligible: bool = True
    exclude_reason: str = ""
    category: str = "eligible_game_window"
    score: float = 0.0

    @property
    def normalized_title(self) -> str:
        return _normalize_window_title(self.title)

    @property
    def window_key(self) -> str:
        return _build_window_key(
            process_name=self.process_name,
            pid=self.pid,
            hwnd=self.hwnd,
            title=self.title,
        )

    @property
    def aspect_ratio(self) -> float:
        return compute_ocr_window_aspect_ratio(self.width, self.height)

    def to_dict(self, *, is_attached: bool = False, is_manual_target: bool = False) -> dict[str, Any]:
        return {
            "window_key": self.window_key,
            "title": self.title,
            "process_name": self.process_name,
            "pid": self.pid,
            "hwnd": self.hwnd,
            "width": self.width,
            "height": self.height,
            "aspect_ratio": self.aspect_ratio,
            "eligible": self.eligible,
            "exclude_reason": self.exclude_reason,
            "is_foreground": self.is_foreground,
            "is_minimized": self.is_minimized,
            "is_attached": is_attached,
            "is_manual_target": is_manual_target,
            "class_name": self.class_name,
            "exe_path": self.exe_path,
            "category": self.category,
        }


@dataclass(slots=True)
class _StableOcrTextState:
    last_raw_text: str = ""
    last_text_key: str = ""
    repeat_count: int = 0
    stable_text: str = ""
    stable_text_key: str = ""
    last_block_reason: str = ""

    def reset(self) -> None:
        self.last_raw_text = ""
        self.last_text_key = ""
        self.repeat_count = 0
        self.stable_text = ""
        self.stable_text_key = ""
        self.last_block_reason = ""


@dataclass(slots=True)
class _MenuConsumeResult:
    emitted_kind: str = ""
    has_menu_candidate: bool = False




@dataclass(slots=True)
class OcrWindowTarget:
    mode: str = "auto"
    window_key: str = ""
    process_name: str = ""
    normalized_title: str = ""
    pid: int = 0
    last_known_hwnd: int = 0
    selected_at: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> OcrWindowTarget:
        raw = value if isinstance(value, dict) else {}
        mode = str(raw.get("mode") or "auto").strip().lower()
        if mode not in {"auto", "manual"}:
            mode = "auto"
        try:
            pid = int(raw.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        try:
            last_known_hwnd = int(raw.get("last_known_hwnd") or 0)
        except (TypeError, ValueError):
            last_known_hwnd = 0
        return cls(
            mode=mode,
            window_key=str(raw.get("window_key") or "").strip(),
            process_name=str(raw.get("process_name") or "").strip(),
            normalized_title=str(raw.get("normalized_title") or "").strip().lower(),
            pid=max(0, pid),
            last_known_hwnd=max(0, last_known_hwnd),
            selected_at=str(raw.get("selected_at") or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "window_key": self.window_key,
            "process_name": self.process_name,
            "normalized_title": self.normalized_title,
            "pid": self.pid,
            "last_known_hwnd": self.last_known_hwnd,
            "selected_at": self.selected_at,
        }

    def is_manual(self) -> bool:
        return self.mode == "manual"

    def matches_exact(self, candidate: DetectedGameWindow) -> bool:
        return bool(self.window_key) and self.window_key == candidate.window_key

    def matches_hwnd(self, candidate: DetectedGameWindow) -> bool:
        return bool(self.last_known_hwnd) and self.last_known_hwnd == candidate.hwnd

    def matches_signature(self, candidate: DetectedGameWindow) -> bool:
        has_process_name = bool(self.process_name)
        has_title = bool(self.normalized_title)
        if has_process_name and self.process_name.strip().lower() != candidate.process_name.strip().lower():
            return False
        if has_title and self.normalized_title != candidate.normalized_title:
            return False
        if has_process_name or has_title:
            return True
        return bool(self.pid > 0 and candidate.pid == self.pid)

    def resolved_for(self, candidate: DetectedGameWindow) -> OcrWindowTarget:
        return OcrWindowTarget(
            mode="manual",
            window_key=candidate.window_key,
            process_name=candidate.process_name,
            normalized_title=candidate.normalized_title,
            pid=candidate.pid,
            last_known_hwnd=candidate.hwnd,
            selected_at=self.selected_at,
        )


class _RuntimeFieldProxy:
    def __init__(self, group_name: str, field_name: str) -> None:
        self._group_name = group_name
        self._field_name = field_name

    def __get__(self, instance: Any, owner: type[Any] | None = None) -> Any:
        if instance is None:
            return self
        return getattr(getattr(instance, self._group_name), self._field_name)

    def __set__(self, instance: Any, value: Any) -> None:
        setattr(getattr(instance, self._group_name), self._field_name, value)


@dataclass(slots=True)
class OcrReaderStatusRuntime:
    enabled: bool = False
    status: str = "disabled"
    detail: str = ""


@dataclass(slots=True)
class OcrReaderWindowRuntime:
    process_name: str = ""
    pid: int = 0
    window_title: str = ""
    width: int = 0
    height: int = 0
    aspect_ratio: float = 0.0


@dataclass(slots=True)
class OcrReaderSessionRuntime:
    game_id: str = ""
    session_id: str = ""
    last_seq: int = 0
    last_event_ts: str = ""


@dataclass(slots=True)
class OcrReaderProfileRuntime:
    capture_stage: str = ""
    capture_profile: dict[str, float] = field(default_factory=dict)
    capture_profile_match_source: str = ""
    capture_profile_bucket_key: str = ""
    recommended_capture_profile: dict[str, Any] = field(default_factory=dict)
    recommended_capture_profile_process_name: str = ""
    recommended_capture_profile_stage: str = ""
    recommended_capture_profile_save_scope: str = ""
    recommended_capture_profile_reason: str = ""
    recommended_capture_profile_confidence: float = 0.0
    recommended_capture_profile_sample_text: str = ""
    recommended_capture_profile_bucket_key: str = ""
    recommended_capture_profile_manual_present: bool = False


@dataclass(slots=True)
class OcrReaderBackendRuntime:
    languages: str = ""
    takeover_reason: str = ""
    backend_kind: str = ""
    backend_detail: str = ""
    backend_path: str = ""
    backend_model: str = ""
    capture_backend_kind: str = ""
    capture_backend_detail: str = ""


@dataclass(slots=True)
class OcrReaderTargetRuntime:
    target_selection_mode: str = "auto"
    target_selection_detail: str = ""
    effective_window_key: str = ""
    effective_window_title: str = ""
    effective_process_name: str = ""
    target_is_foreground: bool = False
    target_window_visible: bool = False
    target_window_minimized: bool = False
    ocr_window_capture_eligible: bool = False
    ocr_window_capture_available: bool = False
    ocr_window_capture_block_reason: str = ""
    input_target_foreground: bool = False
    input_target_block_reason: str = ""
    manual_target: dict[str, Any] = field(default_factory=dict)
    locked_target: dict[str, Any] = field(default_factory=dict)
    candidate_count: int = 0
    excluded_candidate_count: int = 0
    last_exclude_reason: str = ""
    foreground_refresh_at: str = ""
    foreground_refresh_detail: str = ""
    foreground_hwnd: int = 0
    target_hwnd: int = 0
    foreground_advance_monitor_running: bool = False
    foreground_advance_last_seq: int = 0
    foreground_advance_consumed_seq: int = 0
    foreground_advance_last_kind: str = ""
    foreground_advance_last_delta: int = 0
    foreground_advance_last_matched: bool = False
    foreground_advance_last_match_reason: str = ""
    foreground_advance_consumed_count: int = 0
    foreground_advance_matched_count: int = 0
    foreground_advance_coalesced_count: int = 0
    foreground_advance_first_event_ts: float = 0.0
    foreground_advance_last_event_ts: float = 0.0
    foreground_advance_detected_at: float = 0.0
    foreground_advance_last_event_age_seconds: float = 0.0


@dataclass(slots=True)
class OcrReaderObservationRuntime:
    consecutive_no_text_polls: int = 0
    last_observed_at: str = ""
    ocr_capture_diagnostic_required: bool = False
    ocr_context_state: str = ""
    last_raw_ocr_text: str = ""
    last_rejected_ocr_text: str = ""
    last_rejected_ocr_reason: str = ""
    last_rejected_ocr_at: str = ""
    last_rejected_capture_backend: str = ""
    ocr_capture_content_trusted: bool = True
    ocr_capture_rejected_reason: str = ""
    last_observed_line: dict[str, Any] = field(default_factory=dict)
    last_stable_line: dict[str, Any] = field(default_factory=dict)
    stable_ocr_last_raw_text: str = ""
    stable_ocr_repeat_count: int = 0
    stable_ocr_stable_text: str = ""
    stable_ocr_block_reason: str = ""


@dataclass(slots=True)
class OcrReaderCaptureRuntime:
    last_capture_profile: dict[str, float] = field(default_factory=dict)
    last_capture_stage: str = ""
    last_capture_attempt_at: str = ""
    last_capture_completed_at: str = ""
    last_capture_error: str = ""
    last_capture_image_hash: str = ""
    last_capture_source_size: dict[str, float] = field(default_factory=dict)
    last_capture_rect: dict[str, float] = field(default_factory=dict)
    last_capture_window_rect: dict[str, float] = field(default_factory=dict)
    consecutive_same_capture_frames: int = 0
    stale_capture_backend: bool = False
    last_capture_total_duration_seconds: float = 0.0
    last_capture_frame_duration_seconds: float = 0.0
    last_capture_background_duration_seconds: float = 0.0
    last_capture_image_hash_duration_seconds: float = 0.0
    last_ocr_extract_duration_seconds: float = 0.0
    last_backend_plan_duration_seconds: float = 0.0
    last_window_scan_duration_seconds: float = 0.0
    last_capture_background_hash_skipped: bool = False
    vision_snapshot_available: bool = False
    vision_snapshot_captured_at: str = ""
    vision_snapshot_expires_at: str = ""
    vision_snapshot_source: str = ""
    vision_snapshot_width: int = 0
    vision_snapshot_height: int = 0
    vision_snapshot_byte_size: int = 0
    screen_awareness_sample_collection_enabled: bool = False
    screen_awareness_sample_count: int = 0
    screen_awareness_sample_last_path: str = ""
    screen_awareness_sample_last_error: str = ""
    screen_awareness_model_enabled: bool = False
    screen_awareness_model_available: bool = False
    screen_awareness_model_path: str = ""
    screen_awareness_model_detail: str = ""
    screen_awareness_model_last_stage: str = ""
    screen_awareness_model_last_confidence: float = 0.0
    screen_awareness_model_last_latency_seconds: float = 0.0
    vision_classifier_enabled: bool = False
    vision_classifier_available: bool = False
    vision_classifier_detail: str = ""
    vision_classifier_last_label: str = ""
    vision_classifier_last_confidence: float = 0.0
    vision_classifier_last_latency_ms: float = 0.0
    screen_awareness_last_skip_reason: str = ""
    screen_awareness_last_region_count: int = 0
    screen_awareness_last_capture_duration_seconds: float = 0.0
    screen_awareness_last_ocr_duration_seconds: float = 0.0
    scene_ordering_diagnostic: str = "none"


@dataclass(slots=True)
class OcrReaderPollRuntime:
    last_poll_started_at: str = ""
    last_poll_completed_at: str = ""
    last_poll_duration_seconds: float = 0.0
    last_poll_emitted_event: bool = False


@dataclass(slots=True, init=False)
class OcrReaderRuntime:
    status_state: OcrReaderStatusRuntime
    window: OcrReaderWindowRuntime
    session: OcrReaderSessionRuntime
    profile: OcrReaderProfileRuntime
    backend: OcrReaderBackendRuntime
    target: OcrReaderTargetRuntime
    observation: OcrReaderObservationRuntime
    capture: OcrReaderCaptureRuntime
    poll: OcrReaderPollRuntime

    _FIELD_MAP: ClassVar[dict[str, tuple[str, str]]] = {
        "enabled": ("status_state", "enabled"),
        "status": ("status_state", "status"),
        "detail": ("status_state", "detail"),
        "process_name": ("window", "process_name"),
        "pid": ("window", "pid"),
        "window_title": ("window", "window_title"),
        "width": ("window", "width"),
        "height": ("window", "height"),
        "aspect_ratio": ("window", "aspect_ratio"),
        "game_id": ("session", "game_id"),
        "session_id": ("session", "session_id"),
        "last_seq": ("session", "last_seq"),
        "last_event_ts": ("session", "last_event_ts"),
        "capture_stage": ("profile", "capture_stage"),
        "capture_profile": ("profile", "capture_profile"),
        "capture_profile_match_source": ("profile", "capture_profile_match_source"),
        "capture_profile_bucket_key": ("profile", "capture_profile_bucket_key"),
        "recommended_capture_profile": ("profile", "recommended_capture_profile"),
        "recommended_capture_profile_process_name": (
            "profile",
            "recommended_capture_profile_process_name",
        ),
        "recommended_capture_profile_stage": ("profile", "recommended_capture_profile_stage"),
        "recommended_capture_profile_save_scope": (
            "profile",
            "recommended_capture_profile_save_scope",
        ),
        "recommended_capture_profile_reason": ("profile", "recommended_capture_profile_reason"),
        "recommended_capture_profile_confidence": (
            "profile",
            "recommended_capture_profile_confidence",
        ),
        "recommended_capture_profile_sample_text": (
            "profile",
            "recommended_capture_profile_sample_text",
        ),
        "recommended_capture_profile_bucket_key": (
            "profile",
            "recommended_capture_profile_bucket_key",
        ),
        "recommended_capture_profile_manual_present": (
            "profile",
            "recommended_capture_profile_manual_present",
        ),
        "languages": ("backend", "languages"),
        "takeover_reason": ("backend", "takeover_reason"),
        "backend_kind": ("backend", "backend_kind"),
        "backend_detail": ("backend", "backend_detail"),
        "backend_path": ("backend", "backend_path"),
        "backend_model": ("backend", "backend_model"),
        "target_selection_mode": ("target", "target_selection_mode"),
        "target_selection_detail": ("target", "target_selection_detail"),
        "effective_window_key": ("target", "effective_window_key"),
        "effective_window_title": ("target", "effective_window_title"),
        "effective_process_name": ("target", "effective_process_name"),
        "target_is_foreground": ("target", "target_is_foreground"),
        "target_window_visible": ("target", "target_window_visible"),
        "target_window_minimized": ("target", "target_window_minimized"),
        "ocr_window_capture_eligible": ("target", "ocr_window_capture_eligible"),
        "ocr_window_capture_available": ("target", "ocr_window_capture_available"),
        "ocr_window_capture_block_reason": (
            "target",
            "ocr_window_capture_block_reason",
        ),
        "input_target_foreground": ("target", "input_target_foreground"),
        "input_target_block_reason": ("target", "input_target_block_reason"),
        "manual_target": ("target", "manual_target"),
        "locked_target": ("target", "locked_target"),
        "candidate_count": ("target", "candidate_count"),
        "excluded_candidate_count": ("target", "excluded_candidate_count"),
        "last_exclude_reason": ("target", "last_exclude_reason"),
        "consecutive_no_text_polls": ("observation", "consecutive_no_text_polls"),
        "last_observed_at": ("observation", "last_observed_at"),
        "last_capture_profile": ("capture", "last_capture_profile"),
        "last_capture_stage": ("capture", "last_capture_stage"),
        "ocr_capture_diagnostic_required": ("observation", "ocr_capture_diagnostic_required"),
        "ocr_context_state": ("observation", "ocr_context_state"),
        "last_capture_attempt_at": ("capture", "last_capture_attempt_at"),
        "last_capture_completed_at": ("capture", "last_capture_completed_at"),
        "last_capture_error": ("capture", "last_capture_error"),
        "last_raw_ocr_text": ("observation", "last_raw_ocr_text"),
        "last_rejected_ocr_text": ("observation", "last_rejected_ocr_text"),
        "last_rejected_ocr_reason": ("observation", "last_rejected_ocr_reason"),
        "last_rejected_ocr_at": ("observation", "last_rejected_ocr_at"),
        "last_rejected_capture_backend": (
            "observation",
            "last_rejected_capture_backend",
        ),
        "ocr_capture_content_trusted": ("observation", "ocr_capture_content_trusted"),
        "ocr_capture_rejected_reason": ("observation", "ocr_capture_rejected_reason"),
        "last_observed_line": ("observation", "last_observed_line"),
        "last_stable_line": ("observation", "last_stable_line"),
        "stable_ocr_last_raw_text": ("observation", "stable_ocr_last_raw_text"),
        "stable_ocr_repeat_count": ("observation", "stable_ocr_repeat_count"),
        "stable_ocr_stable_text": ("observation", "stable_ocr_stable_text"),
        "stable_ocr_block_reason": ("observation", "stable_ocr_block_reason"),
        "capture_backend_kind": ("backend", "capture_backend_kind"),
        "capture_backend_detail": ("backend", "capture_backend_detail"),
        "last_capture_image_hash": ("capture", "last_capture_image_hash"),
        "last_capture_source_size": ("capture", "last_capture_source_size"),
        "last_capture_rect": ("capture", "last_capture_rect"),
        "last_capture_window_rect": ("capture", "last_capture_window_rect"),
        "consecutive_same_capture_frames": ("capture", "consecutive_same_capture_frames"),
        "stale_capture_backend": ("capture", "stale_capture_backend"),
        "foreground_refresh_at": ("target", "foreground_refresh_at"),
        "foreground_refresh_detail": ("target", "foreground_refresh_detail"),
        "foreground_hwnd": ("target", "foreground_hwnd"),
        "target_hwnd": ("target", "target_hwnd"),
        "foreground_advance_monitor_running": ("target", "foreground_advance_monitor_running"),
        "foreground_advance_last_seq": ("target", "foreground_advance_last_seq"),
        "foreground_advance_consumed_seq": ("target", "foreground_advance_consumed_seq"),
        "foreground_advance_last_kind": ("target", "foreground_advance_last_kind"),
        "foreground_advance_last_delta": ("target", "foreground_advance_last_delta"),
        "foreground_advance_last_matched": ("target", "foreground_advance_last_matched"),
        "foreground_advance_last_match_reason": (
            "target",
            "foreground_advance_last_match_reason",
        ),
        "foreground_advance_consumed_count": ("target", "foreground_advance_consumed_count"),
        "foreground_advance_matched_count": ("target", "foreground_advance_matched_count"),
        "foreground_advance_coalesced_count": ("target", "foreground_advance_coalesced_count"),
        "foreground_advance_first_event_ts": ("target", "foreground_advance_first_event_ts"),
        "foreground_advance_last_event_ts": ("target", "foreground_advance_last_event_ts"),
        "foreground_advance_detected_at": ("target", "foreground_advance_detected_at"),
        "foreground_advance_last_event_age_seconds": (
            "target",
            "foreground_advance_last_event_age_seconds",
        ),
        "last_capture_total_duration_seconds": (
            "capture",
            "last_capture_total_duration_seconds",
        ),
        "last_capture_frame_duration_seconds": (
            "capture",
            "last_capture_frame_duration_seconds",
        ),
        "last_capture_background_duration_seconds": (
            "capture",
            "last_capture_background_duration_seconds",
        ),
        "last_capture_image_hash_duration_seconds": (
            "capture",
            "last_capture_image_hash_duration_seconds",
        ),
        "last_ocr_extract_duration_seconds": ("capture", "last_ocr_extract_duration_seconds"),
        "last_backend_plan_duration_seconds": (
            "capture",
            "last_backend_plan_duration_seconds",
        ),
        "last_window_scan_duration_seconds": ("capture", "last_window_scan_duration_seconds"),
        "last_capture_background_hash_skipped": (
            "capture",
            "last_capture_background_hash_skipped",
        ),
        "screen_awareness_last_skip_reason": (
            "capture",
            "screen_awareness_last_skip_reason",
        ),
        "screen_awareness_last_region_count": (
            "capture",
            "screen_awareness_last_region_count",
        ),
        "screen_awareness_last_capture_duration_seconds": (
            "capture",
            "screen_awareness_last_capture_duration_seconds",
        ),
        "screen_awareness_last_ocr_duration_seconds": (
            "capture",
            "screen_awareness_last_ocr_duration_seconds",
        ),
        "scene_ordering_diagnostic": ("capture", "scene_ordering_diagnostic"),
        "vision_snapshot_available": ("capture", "vision_snapshot_available"),
        "vision_snapshot_captured_at": ("capture", "vision_snapshot_captured_at"),
        "vision_snapshot_expires_at": ("capture", "vision_snapshot_expires_at"),
        "vision_snapshot_source": ("capture", "vision_snapshot_source"),
        "vision_snapshot_width": ("capture", "vision_snapshot_width"),
        "vision_snapshot_height": ("capture", "vision_snapshot_height"),
        "vision_snapshot_byte_size": ("capture", "vision_snapshot_byte_size"),
        "screen_awareness_sample_collection_enabled": (
            "capture",
            "screen_awareness_sample_collection_enabled",
        ),
        "screen_awareness_sample_count": ("capture", "screen_awareness_sample_count"),
        "screen_awareness_sample_last_path": ("capture", "screen_awareness_sample_last_path"),
        "screen_awareness_sample_last_error": ("capture", "screen_awareness_sample_last_error"),
        "screen_awareness_model_enabled": ("capture", "screen_awareness_model_enabled"),
        "screen_awareness_model_available": ("capture", "screen_awareness_model_available"),
        "screen_awareness_model_path": ("capture", "screen_awareness_model_path"),
        "screen_awareness_model_detail": ("capture", "screen_awareness_model_detail"),
        "screen_awareness_model_last_stage": ("capture", "screen_awareness_model_last_stage"),
        "screen_awareness_model_last_confidence": (
            "capture",
            "screen_awareness_model_last_confidence",
        ),
        "screen_awareness_model_last_latency_seconds": (
            "capture",
            "screen_awareness_model_last_latency_seconds",
        ),
        "vision_classifier_enabled": ("capture", "vision_classifier_enabled"),
        "vision_classifier_available": ("capture", "vision_classifier_available"),
        "vision_classifier_detail": ("capture", "vision_classifier_detail"),
        "vision_classifier_last_label": ("capture", "vision_classifier_last_label"),
        "vision_classifier_last_confidence": (
            "capture",
            "vision_classifier_last_confidence",
        ),
        "vision_classifier_last_latency_ms": (
            "capture",
            "vision_classifier_last_latency_ms",
        ),
        "last_poll_started_at": ("poll", "last_poll_started_at"),
        "last_poll_completed_at": ("poll", "last_poll_completed_at"),
        "last_poll_duration_seconds": ("poll", "last_poll_duration_seconds"),
        "last_poll_emitted_event": ("poll", "last_poll_emitted_event"),
    }

    def __init__(
        self,
        *,
        status_state: OcrReaderStatusRuntime | None = None,
        window: OcrReaderWindowRuntime | None = None,
        session: OcrReaderSessionRuntime | None = None,
        profile: OcrReaderProfileRuntime | None = None,
        backend: OcrReaderBackendRuntime | None = None,
        target: OcrReaderTargetRuntime | None = None,
        observation: OcrReaderObservationRuntime | None = None,
        capture: OcrReaderCaptureRuntime | None = None,
        poll: OcrReaderPollRuntime | None = None,
        **legacy_fields: Any,
    ) -> None:
        self.status_state = status_state if status_state is not None else OcrReaderStatusRuntime()
        self.window = window if window is not None else OcrReaderWindowRuntime()
        self.session = session if session is not None else OcrReaderSessionRuntime()
        self.profile = profile if profile is not None else OcrReaderProfileRuntime()
        self.backend = backend if backend is not None else OcrReaderBackendRuntime()
        self.target = target if target is not None else OcrReaderTargetRuntime()
        self.observation = (
            observation if observation is not None else OcrReaderObservationRuntime()
        )
        self.capture = capture if capture is not None else OcrReaderCaptureRuntime()
        self.poll = poll if poll is not None else OcrReaderPollRuntime()

        for field_name in self._FIELD_MAP:
            if field_name in legacy_fields:
                setattr(self, field_name, legacy_fields.pop(field_name))
        if legacy_fields:
            unexpected = ", ".join(sorted(legacy_fields))
            raise TypeError(f"unexpected OcrReaderRuntime field(s): {unexpected}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "status": self.status,
            "detail": self.detail,
            "process_name": self.process_name,
            "pid": self.pid,
            "window_title": self.window_title,
            "width": self.width,
            "height": self.height,
            "aspect_ratio": self.aspect_ratio,
            "game_id": self.game_id,
            "session_id": self.session_id,
            "last_seq": self.last_seq,
            "last_event_ts": self.last_event_ts,
            "capture_stage": self.capture_stage,
            "capture_profile": dict(self.capture_profile),
            "capture_profile_match_source": self.capture_profile_match_source,
            "capture_profile_bucket_key": self.capture_profile_bucket_key,
            "recommended_capture_profile": dict(self.recommended_capture_profile),
            "recommended_capture_profile_process_name": self.recommended_capture_profile_process_name,
            "recommended_capture_profile_stage": self.recommended_capture_profile_stage,
            "recommended_capture_profile_save_scope": self.recommended_capture_profile_save_scope,
            "recommended_capture_profile_reason": self.recommended_capture_profile_reason,
            "recommended_capture_profile_confidence": self.recommended_capture_profile_confidence,
            "recommended_capture_profile_sample_text": self.recommended_capture_profile_sample_text,
            "recommended_capture_profile_bucket_key": self.recommended_capture_profile_bucket_key,
            "recommended_capture_profile_manual_present": self.recommended_capture_profile_manual_present,
            "languages": self.languages,
            "takeover_reason": self.takeover_reason,
            "backend_kind": self.backend_kind,
            "backend_detail": self.backend_detail,
            "backend_path": self.backend_path,
            "backend_model": self.backend_model,
            "target_selection_mode": self.target_selection_mode,
            "target_selection_detail": self.target_selection_detail,
            "effective_window_key": self.effective_window_key,
            "effective_window_title": self.effective_window_title,
            "effective_process_name": self.effective_process_name,
            "target_is_foreground": self.target_is_foreground,
            "target_window_visible": self.target_window_visible,
            "target_window_minimized": self.target_window_minimized,
            "ocr_window_capture_eligible": self.ocr_window_capture_eligible,
            "ocr_window_capture_available": self.ocr_window_capture_available,
            "ocr_window_capture_block_reason": self.ocr_window_capture_block_reason,
            "input_target_foreground": self.input_target_foreground,
            "input_target_block_reason": self.input_target_block_reason,
            "manual_target": dict(self.manual_target),
            "locked_target": dict(self.locked_target),
            "candidate_count": self.candidate_count,
            "excluded_candidate_count": self.excluded_candidate_count,
            "last_exclude_reason": self.last_exclude_reason,
            "consecutive_no_text_polls": self.consecutive_no_text_polls,
            "last_observed_at": self.last_observed_at,
            "last_capture_profile": dict(self.last_capture_profile),
            "last_capture_stage": self.last_capture_stage,
            "ocr_capture_diagnostic_required": self.ocr_capture_diagnostic_required,
            "ocr_context_state": self.ocr_context_state,
            "last_capture_attempt_at": self.last_capture_attempt_at,
            "last_capture_completed_at": self.last_capture_completed_at,
            "last_capture_error": self.last_capture_error,
            "last_raw_ocr_text": self.last_raw_ocr_text,
            "last_rejected_ocr_text": self.last_rejected_ocr_text,
            "last_rejected_ocr_reason": self.last_rejected_ocr_reason,
            "last_rejected_ocr_at": self.last_rejected_ocr_at,
            "last_rejected_capture_backend": self.last_rejected_capture_backend,
            "ocr_capture_content_trusted": self.ocr_capture_content_trusted,
            "ocr_capture_rejected_reason": self.ocr_capture_rejected_reason,
            "last_observed_line": dict(self.last_observed_line),
            "last_stable_line": dict(self.last_stable_line),
            "stable_ocr_last_raw_text": self.stable_ocr_last_raw_text,
            "stable_ocr_repeat_count": self.stable_ocr_repeat_count,
            "stable_ocr_stable_text": self.stable_ocr_stable_text,
            "stable_ocr_block_reason": self.stable_ocr_block_reason,
            "capture_backend_kind": self.capture_backend_kind,
            "capture_backend_detail": self.capture_backend_detail,
            "last_capture_image_hash": self.last_capture_image_hash,
            "last_capture_source_size": dict(self.last_capture_source_size),
            "last_capture_rect": dict(self.last_capture_rect),
            "last_capture_window_rect": dict(self.last_capture_window_rect),
            "consecutive_same_capture_frames": self.consecutive_same_capture_frames,
            "stale_capture_backend": self.stale_capture_backend,
            "foreground_refresh_at": self.foreground_refresh_at,
            "foreground_refresh_detail": self.foreground_refresh_detail,
            "foreground_hwnd": self.foreground_hwnd,
            "target_hwnd": self.target_hwnd,
            "foreground_advance_monitor_running": self.foreground_advance_monitor_running,
            "foreground_advance_last_seq": self.foreground_advance_last_seq,
            "foreground_advance_consumed_seq": self.foreground_advance_consumed_seq,
            "foreground_advance_last_kind": self.foreground_advance_last_kind,
            "foreground_advance_last_delta": self.foreground_advance_last_delta,
            "foreground_advance_last_matched": self.foreground_advance_last_matched,
            "foreground_advance_last_match_reason": self.foreground_advance_last_match_reason,
            "foreground_advance_consumed_count": self.foreground_advance_consumed_count,
            "foreground_advance_matched_count": self.foreground_advance_matched_count,
            "foreground_advance_coalesced_count": self.foreground_advance_coalesced_count,
            "foreground_advance_first_event_ts": self.foreground_advance_first_event_ts,
            "foreground_advance_last_event_ts": self.foreground_advance_last_event_ts,
            "foreground_advance_detected_at": self.foreground_advance_detected_at,
            "foreground_advance_last_event_age_seconds": (
                self.foreground_advance_last_event_age_seconds
            ),
            "last_capture_total_duration_seconds": self.last_capture_total_duration_seconds,
            "last_capture_frame_duration_seconds": self.last_capture_frame_duration_seconds,
            "last_capture_background_duration_seconds": self.last_capture_background_duration_seconds,
            "last_capture_image_hash_duration_seconds": self.last_capture_image_hash_duration_seconds,
            "last_ocr_extract_duration_seconds": self.last_ocr_extract_duration_seconds,
            "last_backend_plan_duration_seconds": self.last_backend_plan_duration_seconds,
            "last_window_scan_duration_seconds": self.last_window_scan_duration_seconds,
            "last_capture_background_hash_skipped": self.last_capture_background_hash_skipped,
            "screen_awareness_last_skip_reason": self.screen_awareness_last_skip_reason,
            "screen_awareness_last_region_count": self.screen_awareness_last_region_count,
            "screen_awareness_last_capture_duration_seconds": (
                self.screen_awareness_last_capture_duration_seconds
            ),
            "screen_awareness_last_ocr_duration_seconds": (
                self.screen_awareness_last_ocr_duration_seconds
            ),
            "scene_ordering_diagnostic": self.scene_ordering_diagnostic,
            "vision_snapshot_available": self.vision_snapshot_available,
            "vision_snapshot_captured_at": self.vision_snapshot_captured_at,
            "vision_snapshot_expires_at": self.vision_snapshot_expires_at,
            "vision_snapshot_source": self.vision_snapshot_source,
            "vision_snapshot_width": self.vision_snapshot_width,
            "vision_snapshot_height": self.vision_snapshot_height,
            "vision_snapshot_byte_size": self.vision_snapshot_byte_size,
            "screen_awareness_sample_collection_enabled": (
                self.screen_awareness_sample_collection_enabled
            ),
            "screen_awareness_sample_count": self.screen_awareness_sample_count,
            "screen_awareness_sample_last_path": self.screen_awareness_sample_last_path,
            "screen_awareness_sample_last_error": self.screen_awareness_sample_last_error,
            "screen_awareness_model_enabled": self.screen_awareness_model_enabled,
            "screen_awareness_model_available": self.screen_awareness_model_available,
            "screen_awareness_model_path": self.screen_awareness_model_path,
            "screen_awareness_model_detail": self.screen_awareness_model_detail,
            "screen_awareness_model_last_stage": self.screen_awareness_model_last_stage,
            "screen_awareness_model_last_confidence": self.screen_awareness_model_last_confidence,
            "screen_awareness_model_last_latency_seconds": (
                self.screen_awareness_model_last_latency_seconds
            ),
            "vision_classifier_enabled": self.vision_classifier_enabled,
            "vision_classifier_available": self.vision_classifier_available,
            "vision_classifier_detail": self.vision_classifier_detail,
            "vision_classifier_last_label": self.vision_classifier_last_label,
            "vision_classifier_last_confidence": self.vision_classifier_last_confidence,
            "vision_classifier_last_latency_ms": self.vision_classifier_last_latency_ms,
            "last_poll_started_at": self.last_poll_started_at,
            "last_poll_completed_at": self.last_poll_completed_at,
            "last_poll_duration_seconds": self.last_poll_duration_seconds,
            "last_poll_emitted_event": self.last_poll_emitted_event,
        }


for _runtime_field_name, (
    _runtime_group_name,
    _runtime_group_attr,
) in OcrReaderRuntime._FIELD_MAP.items():
    setattr(
        OcrReaderRuntime,
        _runtime_field_name,
        _RuntimeFieldProxy(_runtime_group_name, _runtime_group_attr),
    )
del _runtime_field_name, _runtime_group_name, _runtime_group_attr


@dataclass(slots=True)
class WindowSelectionResult:
    target: DetectedGameWindow | None = None
    selection_mode: str = "auto"
    selection_detail: str = ""
    manual_target: OcrWindowTarget = field(default_factory=OcrWindowTarget)
    selected_by_manual: bool = False
    candidate_count: int = 0
    excluded_candidate_count: int = 0
    last_exclude_reason: str = ""


@dataclass(slots=True)
class OcrReaderTickResult:
    warnings: list[str] = field(default_factory=list)
    should_rescan: bool = False
    runtime: dict[str, Any] = field(default_factory=dict)
    stable_event_emitted: bool = False


@dataclass(slots=True)
class OcrBackendDescriptor:
    kind: str = ""
    backend: OcrBackend | None = None
    path: str = ""
    model: str = ""
    detail: str = ""
    available: bool = False


@dataclass(slots=True)
class SelectedOcrBackendPlan:
    selection: str = "auto"
    primary: OcrBackendDescriptor = field(default_factory=OcrBackendDescriptor)
    fallback: OcrBackendDescriptor = field(default_factory=OcrBackendDescriptor)
    rapidocr_inspection: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OcrExtractionResult:
    text: str = ""
    backend: OcrBackendDescriptor = field(default_factory=OcrBackendDescriptor)
    backend_detail: str = ""
    warnings: list[str] = field(default_factory=list)
    backend_errors: list[str] = field(default_factory=list)
    boxes: list[OcrTextBox] = field(default_factory=list)
    bounds_coordinate_space: str = ""
    source_size: dict[str, float] = field(default_factory=dict)
    capture_rect: dict[str, float] = field(default_factory=dict)
    window_rect: dict[str, float] = field(default_factory=dict)
    capture_backend_kind: str = ""
    capture_backend_detail: str = ""
    capture_image: Any | None = None
    capture_image_hash: str = ""
    background_hash: str = ""
    timing: dict[str, Any] = field(default_factory=dict)
    screen_ocr_regions: list[dict[str, Any]] = field(default_factory=list)
    screen_visual_features: dict[str, Any] = field(default_factory=dict)
    ocr_confidence: float = 0.0
    text_source: str = "bottom_region"

    @property
    def captured_image(self) -> Any | None:
        return self.capture_image

    @captured_image.setter
    def captured_image(self, value: Any | None) -> None:
        self.capture_image = value


@dataclass(slots=True)
class _TickPreflightResult:
    result: OcrReaderTickResult
    backend_plan: SelectedOcrBackendPlan = field(default_factory=SelectedOcrBackendPlan)
    backend_plan_duration: float = 0.0
    should_return: bool = False


@dataclass(slots=True)
class _TickTargetContext:
    result: OcrReaderTickResult
    target: DetectedGameWindow | None = None
    selection: WindowSelectionResult = field(default_factory=WindowSelectionResult)
    profile: OcrCaptureProfile = field(default_factory=OcrCaptureProfile)
    capture_profile_selection: ResolvedOcrCaptureSelection = field(
        default_factory=ResolvedOcrCaptureSelection
    )
    legacy_geometryless_auto_target: bool = False
    aihong_two_stage_enabled: bool = False
    started_session: bool = False
    window_scan_duration: float = 0.0
    now: float = 0.0
    should_return: bool = False
