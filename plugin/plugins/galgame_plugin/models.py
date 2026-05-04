from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, TypeAlias

MODE_SILENT = "silent"
MODE_COMPANION = "companion"
MODE_CHOICE_ADVISOR = "choice_advisor"
MODES = frozenset({MODE_SILENT, MODE_COMPANION, MODE_CHOICE_ADVISOR})

ADVANCE_SPEED_SLOW = "slow"
ADVANCE_SPEED_MEDIUM = "medium"
ADVANCE_SPEED_FAST = "fast"
ADVANCE_SPEEDS = frozenset({ADVANCE_SPEED_SLOW, ADVANCE_SPEED_MEDIUM, ADVANCE_SPEED_FAST})

DATA_SOURCE_NONE = "none"
DATA_SOURCE_BRIDGE_SDK = "bridge_sdk"
DATA_SOURCE_MEMORY_READER = "memory_reader"
DATA_SOURCE_OCR_READER = "ocr_reader"
DATA_SOURCES = frozenset(
    {
        DATA_SOURCE_NONE,
        DATA_SOURCE_BRIDGE_SDK,
        DATA_SOURCE_MEMORY_READER,
        DATA_SOURCE_OCR_READER,
    }
)
SharedStatePayload: TypeAlias = dict[str, Any]
MENU_PREFIX_RE = re.compile(r"^\s*(?:[-*•]\s+|\d+[\.\)\]:：]\s+)(.+\S)\s*$")

READER_MODE_AUTO = "auto"
READER_MODE_MEMORY = DATA_SOURCE_MEMORY_READER
READER_MODE_OCR = DATA_SOURCE_OCR_READER
READER_MODES = frozenset({READER_MODE_AUTO, READER_MODE_MEMORY, READER_MODE_OCR})

OCR_TRIGGER_MODE_INTERVAL = "interval"
OCR_TRIGGER_MODE_AFTER_ADVANCE = "after_advance"
OCR_TRIGGER_MODES = frozenset({OCR_TRIGGER_MODE_INTERVAL, OCR_TRIGGER_MODE_AFTER_ADVANCE})

DEFAULT_OCR_CAPTURE_LEFT_INSET_RATIO = 0.05
DEFAULT_OCR_CAPTURE_RIGHT_INSET_RATIO = 0.05
DEFAULT_OCR_CAPTURE_TOP_RATIO = 0.62
DEFAULT_OCR_CAPTURE_BOTTOM_INSET_RATIO = 0.08
OCR_CAPTURE_PROFILE_RATIO_KEYS = (
    "left_inset_ratio",
    "right_inset_ratio",
    "top_ratio",
    "bottom_inset_ratio",
)
OCR_CAPTURE_PROFILE_STAGE_DEFAULT = "default"
OCR_CAPTURE_PROFILE_STAGE_DIALOGUE = "dialogue_stage"
OCR_CAPTURE_PROFILE_STAGE_MENU = "menu_stage"
OCR_CAPTURE_PROFILE_STAGE_TITLE = "title_stage"
OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD = "save_load_stage"
OCR_CAPTURE_PROFILE_STAGE_CONFIG = "config_stage"
OCR_CAPTURE_PROFILE_STAGE_TRANSITION = "transition_stage"
OCR_CAPTURE_PROFILE_STAGE_GALLERY = "gallery_stage"
OCR_CAPTURE_PROFILE_STAGE_MINIGAME = "minigame_stage"
OCR_CAPTURE_PROFILE_STAGE_GAME_OVER = "game_over_stage"
OCR_CAPTURE_PROFILE_STAGES = frozenset(
    {
        OCR_CAPTURE_PROFILE_STAGE_DEFAULT,
        OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
        OCR_CAPTURE_PROFILE_STAGE_MENU,
        OCR_CAPTURE_PROFILE_STAGE_TITLE,
        OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD,
        OCR_CAPTURE_PROFILE_STAGE_CONFIG,
        OCR_CAPTURE_PROFILE_STAGE_TRANSITION,
        OCR_CAPTURE_PROFILE_STAGE_GALLERY,
        OCR_CAPTURE_PROFILE_STAGE_MINIGAME,
        OCR_CAPTURE_PROFILE_STAGE_GAME_OVER,
    }
)
OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY = "__window_buckets__"
OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET = "window_bucket"
OCR_CAPTURE_PROFILE_SAVE_SCOPE_PROCESS_FALLBACK = "process_fallback"
OCR_CAPTURE_PROFILE_SAVE_SCOPES = frozenset(
    {
        OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET,
        OCR_CAPTURE_PROFILE_SAVE_SCOPE_PROCESS_FALLBACK,
    }
)
OCR_CAPTURE_PROFILE_MATCH_SOURCE_BUCKET_EXACT = "bucket_exact"
OCR_CAPTURE_PROFILE_MATCH_SOURCE_BUCKET_ASPECT_NEAREST = "bucket_aspect_nearest"
OCR_CAPTURE_PROFILE_MATCH_SOURCE_PROCESS_FALLBACK = "process_fallback"
OCR_CAPTURE_PROFILE_MATCH_SOURCE_BUILTIN_PRESET = "builtin_preset"
OCR_CAPTURE_PROFILE_MATCH_SOURCE_CONFIG_DEFAULT = "config_default"

AGENT_STATUS_ACTIVE = "active"
AGENT_STATUS_STANDBY = "standby"
AGENT_STATUS_ERROR = "error"
AGENT_STATUSES = frozenset(
    {AGENT_STATUS_ACTIVE, AGENT_STATUS_STANDBY, AGENT_STATUS_ERROR}
)

STATE_DISCONNECTED = "disconnected"
STATE_IDLE = "idle"
STATE_ACTIVE = "active"
STATE_STALE = "stale"
STATE_ERROR = "error"
CONNECTION_STATES = frozenset(
    {STATE_DISCONNECTED, STATE_IDLE, STATE_ACTIVE, STATE_STALE, STATE_ERROR}
)

STORE_BOUND_GAME_ID = "bound_game_id"
STORE_MODE = "mode"
STORE_PUSH_NOTIFICATIONS = "push_notifications"
STORE_ADVANCE_SPEED = "advance_speed"
STORE_SESSION_ID = "session_id"
STORE_EVENTS_BYTE_OFFSET = "events_byte_offset"
STORE_EVENTS_FILE_SIZE = "events_file_size"
STORE_LAST_SEQ = "last_seq"
STORE_DEDUPE_WINDOW = "dedupe_window"
STORE_LAST_ERROR = "last_error"
STORE_OCR_CAPTURE_PROFILES = "ocr_capture_profiles"
STORE_OCR_WINDOW_TARGET = "ocr_window_target"
STORE_MEMORY_READER_TARGET = "memory_reader_target"
STORE_OCR_BACKEND_SELECTION = "ocr_backend_selection"
STORE_OCR_CAPTURE_BACKEND = "ocr_capture_backend"
STORE_READER_MODE = "reader_mode"
STORE_OCR_POLL_INTERVAL_SECONDS = "ocr_poll_interval_seconds"
STORE_OCR_TRIGGER_MODE = "ocr_trigger_mode"
STORE_LLM_VISION_ENABLED = "llm_vision_enabled"
STORE_LLM_VISION_MAX_IMAGE_PX = "llm_vision_max_image_px"
STORE_OCR_SCREEN_TEMPLATES = "ocr_screen_templates"
STORE_KEYS = (
    STORE_BOUND_GAME_ID,
    STORE_MODE,
    STORE_PUSH_NOTIFICATIONS,
    STORE_ADVANCE_SPEED,
    STORE_SESSION_ID,
    STORE_EVENTS_BYTE_OFFSET,
    STORE_EVENTS_FILE_SIZE,
    STORE_LAST_SEQ,
    STORE_DEDUPE_WINDOW,
    STORE_LAST_ERROR,
    STORE_OCR_CAPTURE_PROFILES,
    STORE_OCR_WINDOW_TARGET,
    STORE_MEMORY_READER_TARGET,
    STORE_OCR_BACKEND_SELECTION,
    STORE_OCR_CAPTURE_BACKEND,
    STORE_READER_MODE,
    STORE_OCR_POLL_INTERVAL_SECONDS,
    STORE_OCR_TRIGGER_MODE,
    STORE_LLM_VISION_ENABLED,
    STORE_LLM_VISION_MAX_IMAGE_PX,
    STORE_OCR_SCREEN_TEMPLATES,
)

DEFAULT_SAVE_CONTEXT = {
    "kind": "unknown",
    "slot_id": "",
    "display_name": "",
}


def json_copy(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [json_copy(item) for item in value]
    elif isinstance(value, dict):
        return {key: json_copy(item) for key, item in value.items()}
    elif isinstance(value, tuple):
        return tuple(json_copy(item) for item in value)
    return copy.deepcopy(value)


def build_ocr_capture_profile_bucket_key(width: int, height: int) -> str:
    return f"{max(0, int(width))}x{max(0, int(height))}"


def parse_ocr_capture_profile_bucket_key(value: str) -> tuple[int, int] | None:
    normalized = str(value or "").strip().lower()
    if "x" not in normalized:
        return None
    left, right = normalized.split("x", 1)
    try:
        width = int(left)
        height = int(right)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return (width, height)


def compute_ocr_window_aspect_ratio(width: int, height: int, *, precision: int = 4) -> float:
    width_value = max(0, int(width))
    height_value = max(0, int(height))
    if width_value <= 0 or height_value <= 0:
        return 0.0
    return round(width_value / height_value, precision)


def _string(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _bool(value: object, default: bool = False) -> bool:
    return value if isinstance(value, bool) else default


def _int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _float(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def sanitize_save_context(value: object) -> dict[str, str]:
    raw = value if isinstance(value, dict) else {}
    return {
        "kind": _string(raw.get("kind"), DEFAULT_SAVE_CONTEXT["kind"]),
        "slot_id": _string(raw.get("slot_id"), DEFAULT_SAVE_CONTEXT["slot_id"]),
        "display_name": _string(
            raw.get("display_name"), DEFAULT_SAVE_CONTEXT["display_name"]
        ),
    }


def _sanitize_choice_bounds(bounds: object) -> dict[str, float]:
    if not isinstance(bounds, dict):
        return {}
    try:
        sanitized = {
            key: float(bounds.get(key))  # type: ignore[arg-type]
            for key in ("left", "top", "right", "bottom")
        }
    except (TypeError, ValueError):
        return {}
    if sanitized["right"] <= sanitized["left"] or sanitized["bottom"] <= sanitized["top"]:
        return {}
    return sanitized


def sanitize_choice(value: object) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    choice = {
        "choice_id": _string(raw.get("choice_id")),
        "text": _string(raw.get("text")),
        "index": _int(raw.get("index"), 0),
        "enabled": _bool(raw.get("enabled"), True),
    }
    sanitized_bounds = _sanitize_choice_bounds(raw.get("bounds"))
    if sanitized_bounds:
        choice["bounds"] = sanitized_bounds
    bounds_coordinate_space = _string(raw.get("bounds_coordinate_space")).strip()
    if bounds_coordinate_space:
        choice["bounds_coordinate_space"] = bounds_coordinate_space
    source_size = raw.get("source_size")
    if isinstance(source_size, dict):
        try:
            width = float(source_size.get("width"))  # type: ignore[arg-type]
            height = float(source_size.get("height"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            width = 0.0
            height = 0.0
        if width > 0.0 and height > 0.0:
            choice["source_size"] = {"width": width, "height": height}
    for rect_key in ("capture_rect", "window_rect"):
        rect = raw.get(rect_key)
        if not isinstance(rect, dict):
            continue
        sanitized_rect: dict[str, float] = {}
        for key in ("left", "top", "right", "bottom"):
            try:
                sanitized_rect[key] = float(rect.get(key))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                sanitized_rect = {}
                break
        if (
            sanitized_rect
            and sanitized_rect["right"] > sanitized_rect["left"]
            and sanitized_rect["bottom"] > sanitized_rect["top"]
        ):
            choice[rect_key] = sanitized_rect
    return choice


def sanitize_screen_ui_element(value: object) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    element: dict[str, Any] = {
        "text": _string(raw.get("text")),
    }
    element_id = _string(raw.get("element_id")).strip()
    if element_id:
        element["element_id"] = element_id
    role = _string(raw.get("role")).strip()
    if role:
        element["role"] = role
    sanitized_bounds = _sanitize_choice_bounds(raw.get("bounds"))
    if sanitized_bounds:
        element["bounds"] = sanitized_bounds
    bounds_coordinate_space = _string(raw.get("bounds_coordinate_space")).strip()
    if bounds_coordinate_space:
        element["bounds_coordinate_space"] = bounds_coordinate_space
    text_source = _string(raw.get("text_source") or raw.get("source")).strip()
    if text_source:
        element["text_source"] = text_source
    source_size = raw.get("source_size")
    if isinstance(source_size, dict):
        try:
            width = float(source_size.get("width"))  # type: ignore[arg-type]
            height = float(source_size.get("height"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            width = 0.0
            height = 0.0
        if width > 0.0 and height > 0.0:
            element["source_size"] = {"width": width, "height": height}
    for rect_key in ("capture_rect", "window_rect"):
        rect = raw.get(rect_key)
        if not isinstance(rect, dict):
            continue
        sanitized_rect: dict[str, float] = {}
        for key in ("left", "top", "right", "bottom"):
            try:
                sanitized_rect[key] = float(rect.get(key))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                sanitized_rect = {}
                break
        if (
            sanitized_rect
            and sanitized_rect["right"] > sanitized_rect["left"]
            and sanitized_rect["bottom"] > sanitized_rect["top"]
        ):
            element[rect_key] = sanitized_rect
    normalized_bounds = raw.get("normalized_bounds")
    if isinstance(normalized_bounds, dict):
        sanitized_normalized: dict[str, float] = {}
        for key in ("left", "top", "right", "bottom"):
            try:
                value_float = float(normalized_bounds.get(key))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                sanitized_normalized = {}
                break
            sanitized_normalized[key] = max(0.0, min(value_float, 1.0))
        if (
            sanitized_normalized
            and sanitized_normalized["right"] > sanitized_normalized["left"]
            and sanitized_normalized["bottom"] > sanitized_normalized["top"]
        ):
            element["normalized_bounds"] = sanitized_normalized
    if not element["text"] and "bounds" not in element:
        return {}
    return element


def sanitize_screen_ui_elements(value: object, *, limit: int = 10) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    elements: list[dict[str, Any]] = []
    for item in value:
        sanitized = sanitize_screen_ui_element(item)
        if sanitized:
            elements.append(sanitized)
        if len(elements) >= max(0, int(limit)):
            break
    return elements


def sanitize_metadata(value: object) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    return {str(key): json_copy(item) for key, item in raw.items()}


def sanitize_snapshot_state(value: object) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    choices_obj = raw.get("choices")
    choices = (
        [sanitize_choice(item) for item in choices_obj]
        if isinstance(choices_obj, list)
        else []
    )
    return {
        "speaker": _string(raw.get("speaker")),
        "text": _string(raw.get("text")),
        "choices": choices,
        "scene_id": _string(raw.get("scene_id")),
        "line_id": _string(raw.get("line_id")),
        "route_id": _string(raw.get("route_id")),
        "is_menu_open": _bool(raw.get("is_menu_open"), bool(choices)),
        "save_context": sanitize_save_context(raw.get("save_context")),
        "stability": _string(raw.get("stability")),
        "screen_type": _string(raw.get("screen_type")),
        "screen_ui_elements": sanitize_screen_ui_elements(raw.get("screen_ui_elements")),
        "screen_confidence": _float(raw.get("screen_confidence"), 0.0),
        "screen_debug": sanitize_metadata(raw.get("screen_debug")),
        "ts": _string(raw.get("ts")),
    }


def sanitize_session_snapshot(value: object) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    return {
        "protocol_version": _int(raw.get("protocol_version"), 1),
        "game_id": _string(raw.get("game_id")),
        "game_title": _string(raw.get("game_title")),
        "engine": _string(raw.get("engine")),
        "session_id": _string(raw.get("session_id")),
        "started_at": _string(raw.get("started_at")),
        "last_seq": max(0, _int(raw.get("last_seq"), 0)),
        "locale": _string(raw.get("locale")),
        "bridge_sdk_version": _string(raw.get("bridge_sdk_version")),
        "metadata": sanitize_metadata(raw.get("metadata")),
        "state": sanitize_snapshot_state(raw.get("state")),
    }


def sanitize_event(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    payload = value.get("payload")
    normalized_payload = dict(payload) if isinstance(payload, dict) else {}
    return {
        "protocol_version": _int(value.get("protocol_version"), 1),
        "seq": max(0, _int(value.get("seq"), 0)),
        "ts": _string(value.get("ts")),
        "type": _string(value.get("type")),
        "session_id": _string(value.get("session_id")),
        "game_id": _string(value.get("game_id")),
        "payload": normalized_payload,
    }


def make_error(
    message: str,
    *,
    source: str,
    kind: str = "warning",
    ts: str = "",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": kind,
        "source": source,
        "message": message,
        "ts": ts,
    }
    if details:
        payload["details"] = dict(details)
    return payload


class _ConfigFieldProxy:
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
class GalgameBridgeConfig:
    bridge_root: Path = Path()
    active_poll_interval_seconds: float = 1.0
    idle_poll_interval_seconds: float = 3.0
    stale_after_seconds: float = 15.0
    default_mode: str = MODE_COMPANION
    push_notifications: bool = True
    scene_change_cooldown_seconds: float = 15.0
    scene_push_half_threshold: int = 4
    scene_push_time_fallback_seconds: float = 120.0
    scene_merge_total_threshold: int = 12


@dataclass(slots=True)
class GalgameHistoryConfig:
    history_events_limit: int = 500
    history_lines_limit: int = 200
    history_choices_limit: int = 50
    dedupe_window_limit: int = 64
    warmup_replay_bytes_limit: int = 65536
    warmup_replay_events_limit: int = 50


@dataclass(slots=True)
class GalgameLLMConfig:
    llm_call_timeout_seconds: float = 15.0
    llm_max_in_flight: int = 2
    llm_request_cache_ttl_seconds: float = 2.0
    llm_scene_summary_cache_ttl_seconds: float = 10.0
    llm_target_entry_ref: str = ""
    llm_vision_enabled: bool = False
    llm_vision_max_image_px: int = 768
    llm_temperature_agent_reply: float = 0.2
    llm_temperature_default: float = 0.0
    llm_max_tokens_agent_reply: int = 900
    llm_max_tokens_default: int = 1200


@dataclass(slots=True)
class GalgameReaderConfig:
    reader_mode: str = READER_MODE_AUTO


@dataclass(slots=True)
class GalgameMemoryReaderConfig:
    memory_reader_enabled: bool = False
    memory_reader_textractor_path: str = ""
    memory_reader_install_release_api_url: str = ""
    memory_reader_install_target_dir: str = ""
    memory_reader_install_timeout_seconds: float = 60.0
    memory_reader_auto_detect: bool = True
    memory_reader_hook_codes: list[str] = field(default_factory=list)
    memory_reader_engine_hook_codes: dict[str, list[str]] = field(default_factory=dict)
    memory_reader_poll_interval_seconds: float = 1.0


@dataclass(slots=True)
class GalgameOcrReaderConfig:
    ocr_reader_enabled: bool = False
    ocr_reader_enabled_explicit: bool = False
    ocr_reader_backend_selection: str = "auto"
    ocr_reader_backend_selection_explicit: bool = False
    ocr_reader_capture_backend: str = "smart"
    ocr_reader_capture_backend_explicit: bool = False
    ocr_reader_tesseract_path: str = ""
    ocr_reader_install_manifest_url: str = ""
    ocr_reader_install_target_dir: str = ""
    ocr_reader_install_timeout_seconds: float = 300.0
    ocr_reader_poll_interval_seconds: float = 2.0
    ocr_reader_trigger_mode: str = OCR_TRIGGER_MODE_INTERVAL
    ocr_reader_fast_loop_enabled: bool = False
    ocr_reader_no_text_takeover_after_seconds: float = 30.0
    ocr_reader_background_scene_change_distance: int = 28
    ocr_reader_languages: str = "chi_sim+jpn+eng"
    ocr_reader_left_inset_ratio: float = DEFAULT_OCR_CAPTURE_LEFT_INSET_RATIO
    ocr_reader_right_inset_ratio: float = DEFAULT_OCR_CAPTURE_RIGHT_INSET_RATIO
    ocr_reader_top_ratio: float = DEFAULT_OCR_CAPTURE_TOP_RATIO
    ocr_reader_bottom_inset_ratio: float = DEFAULT_OCR_CAPTURE_BOTTOM_INSET_RATIO
    ocr_reader_screen_awareness_full_frame_ocr: bool = False
    ocr_reader_screen_awareness_multi_region_ocr: bool = False
    ocr_reader_screen_awareness_visual_rules: bool = False
    ocr_reader_screen_awareness_latency_mode: str = "balanced"
    ocr_reader_screen_awareness_min_interval_seconds: float = 2.0
    ocr_reader_screen_awareness_sample_collection_enabled: bool = False
    ocr_reader_screen_awareness_sample_dir: str = ""
    ocr_reader_screen_awareness_model_enabled: bool = False
    ocr_reader_screen_awareness_model_path: str = ""
    ocr_reader_screen_awareness_model_min_confidence: float = 0.55
    ocr_reader_screen_templates: list[dict[str, Any]] = field(default_factory=list)
    ocr_reader_screen_type_transition_emit: bool = True
    ocr_reader_known_screen_timeout_seconds: float = 5.0
    ocr_reader_max_unobserved_advances_before_hold: int = 3
    ocr_reader_unobserved_advance_hold_duration_seconds: float = 0.0


@dataclass(slots=True)
class GalgameRapidOcrConfig:
    rapidocr_enabled: bool = False
    rapidocr_enabled_explicit: bool = False
    rapidocr_install_manifest_url: str = ""
    rapidocr_install_target_dir: str = ""
    rapidocr_install_timeout_seconds: float = 180.0
    rapidocr_engine_type: str = "onnxruntime"
    rapidocr_lang_type: str = "ch"
    rapidocr_model_type: str = "mobile"
    rapidocr_ocr_version: str = "PP-OCRv5"


@dataclass(slots=True, init=False)
class GalgameConfig:
    bridge: GalgameBridgeConfig
    history: GalgameHistoryConfig
    llm: GalgameLLMConfig
    reader: GalgameReaderConfig
    memory_reader: GalgameMemoryReaderConfig
    ocr_reader: GalgameOcrReaderConfig
    rapidocr: GalgameRapidOcrConfig

    _FIELD_MAP: ClassVar[dict[str, tuple[str, str]]] = {
        "bridge_root": ("bridge", "bridge_root"),
        "active_poll_interval_seconds": ("bridge", "active_poll_interval_seconds"),
        "idle_poll_interval_seconds": ("bridge", "idle_poll_interval_seconds"),
        "stale_after_seconds": ("bridge", "stale_after_seconds"),
        "default_mode": ("bridge", "default_mode"),
        "push_notifications": ("bridge", "push_notifications"),
        "scene_change_cooldown_seconds": ("bridge", "scene_change_cooldown_seconds"),
        "scene_push_half_threshold": ("bridge", "scene_push_half_threshold"),
        "scene_push_time_fallback_seconds": ("bridge", "scene_push_time_fallback_seconds"),
        "scene_merge_total_threshold": ("bridge", "scene_merge_total_threshold"),
        "history_events_limit": ("history", "history_events_limit"),
        "history_lines_limit": ("history", "history_lines_limit"),
        "history_choices_limit": ("history", "history_choices_limit"),
        "dedupe_window_limit": ("history", "dedupe_window_limit"),
        "warmup_replay_bytes_limit": ("history", "warmup_replay_bytes_limit"),
        "warmup_replay_events_limit": ("history", "warmup_replay_events_limit"),
        "llm_call_timeout_seconds": ("llm", "llm_call_timeout_seconds"),
        "llm_max_in_flight": ("llm", "llm_max_in_flight"),
        "llm_request_cache_ttl_seconds": ("llm", "llm_request_cache_ttl_seconds"),
        "llm_scene_summary_cache_ttl_seconds": ("llm", "llm_scene_summary_cache_ttl_seconds"),
        "llm_target_entry_ref": ("llm", "llm_target_entry_ref"),
        "llm_vision_enabled": ("llm", "llm_vision_enabled"),
        "llm_vision_max_image_px": ("llm", "llm_vision_max_image_px"),
        "llm_temperature_agent_reply": ("llm", "llm_temperature_agent_reply"),
        "llm_temperature_default": ("llm", "llm_temperature_default"),
        "llm_max_tokens_agent_reply": ("llm", "llm_max_tokens_agent_reply"),
        "llm_max_tokens_default": ("llm", "llm_max_tokens_default"),
        "reader_mode": ("reader", "reader_mode"),
        "memory_reader_enabled": ("memory_reader", "memory_reader_enabled"),
        "memory_reader_textractor_path": ("memory_reader", "memory_reader_textractor_path"),
        "memory_reader_install_release_api_url": (
            "memory_reader",
            "memory_reader_install_release_api_url",
        ),
        "memory_reader_install_target_dir": ("memory_reader", "memory_reader_install_target_dir"),
        "memory_reader_install_timeout_seconds": (
            "memory_reader",
            "memory_reader_install_timeout_seconds",
        ),
        "memory_reader_auto_detect": ("memory_reader", "memory_reader_auto_detect"),
        "memory_reader_hook_codes": ("memory_reader", "memory_reader_hook_codes"),
        "memory_reader_engine_hook_codes": (
            "memory_reader",
            "memory_reader_engine_hook_codes",
        ),
        "memory_reader_poll_interval_seconds": (
            "memory_reader",
            "memory_reader_poll_interval_seconds",
        ),
        "ocr_reader_enabled": ("ocr_reader", "ocr_reader_enabled"),
        "ocr_reader_enabled_explicit": ("ocr_reader", "ocr_reader_enabled_explicit"),
        "ocr_reader_backend_selection": ("ocr_reader", "ocr_reader_backend_selection"),
        "ocr_reader_backend_selection_explicit": (
            "ocr_reader",
            "ocr_reader_backend_selection_explicit",
        ),
        "ocr_reader_capture_backend": ("ocr_reader", "ocr_reader_capture_backend"),
        "ocr_reader_capture_backend_explicit": (
            "ocr_reader",
            "ocr_reader_capture_backend_explicit",
        ),
        "ocr_reader_tesseract_path": ("ocr_reader", "ocr_reader_tesseract_path"),
        "ocr_reader_install_manifest_url": ("ocr_reader", "ocr_reader_install_manifest_url"),
        "ocr_reader_install_target_dir": ("ocr_reader", "ocr_reader_install_target_dir"),
        "ocr_reader_install_timeout_seconds": (
            "ocr_reader",
            "ocr_reader_install_timeout_seconds",
        ),
        "ocr_reader_poll_interval_seconds": ("ocr_reader", "ocr_reader_poll_interval_seconds"),
        "ocr_reader_trigger_mode": ("ocr_reader", "ocr_reader_trigger_mode"),
        "ocr_reader_fast_loop_enabled": ("ocr_reader", "ocr_reader_fast_loop_enabled"),
        "ocr_reader_no_text_takeover_after_seconds": (
            "ocr_reader",
            "ocr_reader_no_text_takeover_after_seconds",
        ),
        "ocr_reader_background_scene_change_distance": (
            "ocr_reader",
            "ocr_reader_background_scene_change_distance",
        ),
        "ocr_reader_languages": ("ocr_reader", "ocr_reader_languages"),
        "ocr_reader_left_inset_ratio": ("ocr_reader", "ocr_reader_left_inset_ratio"),
        "ocr_reader_right_inset_ratio": ("ocr_reader", "ocr_reader_right_inset_ratio"),
        "ocr_reader_top_ratio": ("ocr_reader", "ocr_reader_top_ratio"),
        "ocr_reader_bottom_inset_ratio": ("ocr_reader", "ocr_reader_bottom_inset_ratio"),
        "ocr_reader_screen_awareness_full_frame_ocr": (
            "ocr_reader",
            "ocr_reader_screen_awareness_full_frame_ocr",
        ),
        "ocr_reader_screen_awareness_multi_region_ocr": (
            "ocr_reader",
            "ocr_reader_screen_awareness_multi_region_ocr",
        ),
        "ocr_reader_screen_awareness_visual_rules": (
            "ocr_reader",
            "ocr_reader_screen_awareness_visual_rules",
        ),
        "ocr_reader_screen_awareness_latency_mode": (
            "ocr_reader",
            "ocr_reader_screen_awareness_latency_mode",
        ),
        "ocr_reader_screen_awareness_min_interval_seconds": (
            "ocr_reader",
            "ocr_reader_screen_awareness_min_interval_seconds",
        ),
        "ocr_reader_screen_awareness_sample_collection_enabled": (
            "ocr_reader",
            "ocr_reader_screen_awareness_sample_collection_enabled",
        ),
        "ocr_reader_screen_awareness_sample_dir": (
            "ocr_reader",
            "ocr_reader_screen_awareness_sample_dir",
        ),
        "ocr_reader_screen_awareness_model_enabled": (
            "ocr_reader",
            "ocr_reader_screen_awareness_model_enabled",
        ),
        "ocr_reader_screen_awareness_model_path": (
            "ocr_reader",
            "ocr_reader_screen_awareness_model_path",
        ),
        "ocr_reader_screen_awareness_model_min_confidence": (
            "ocr_reader",
            "ocr_reader_screen_awareness_model_min_confidence",
        ),
        "ocr_reader_screen_templates": ("ocr_reader", "ocr_reader_screen_templates"),
        "ocr_reader_screen_type_transition_emit": (
            "ocr_reader",
            "ocr_reader_screen_type_transition_emit",
        ),
        "ocr_reader_known_screen_timeout_seconds": (
            "ocr_reader",
            "ocr_reader_known_screen_timeout_seconds",
        ),
        "ocr_reader_max_unobserved_advances_before_hold": (
            "ocr_reader",
            "ocr_reader_max_unobserved_advances_before_hold",
        ),
        "ocr_reader_unobserved_advance_hold_duration_seconds": (
            "ocr_reader",
            "ocr_reader_unobserved_advance_hold_duration_seconds",
        ),
        "rapidocr_enabled": ("rapidocr", "rapidocr_enabled"),
        "rapidocr_enabled_explicit": ("rapidocr", "rapidocr_enabled_explicit"),
        "rapidocr_install_manifest_url": ("rapidocr", "rapidocr_install_manifest_url"),
        "rapidocr_install_target_dir": ("rapidocr", "rapidocr_install_target_dir"),
        "rapidocr_install_timeout_seconds": ("rapidocr", "rapidocr_install_timeout_seconds"),
        "rapidocr_engine_type": ("rapidocr", "rapidocr_engine_type"),
        "rapidocr_lang_type": ("rapidocr", "rapidocr_lang_type"),
        "rapidocr_model_type": ("rapidocr", "rapidocr_model_type"),
        "rapidocr_ocr_version": ("rapidocr", "rapidocr_ocr_version"),
    }

    def __init__(
        self,
        *,
        bridge: GalgameBridgeConfig | None = None,
        history: GalgameHistoryConfig | None = None,
        llm: GalgameLLMConfig | None = None,
        reader: GalgameReaderConfig | None = None,
        memory_reader: GalgameMemoryReaderConfig | None = None,
        ocr_reader: GalgameOcrReaderConfig | None = None,
        rapidocr: GalgameRapidOcrConfig | None = None,
        **legacy_fields: Any,
    ) -> None:
        self.bridge = bridge if bridge is not None else GalgameBridgeConfig()
        self.history = history if history is not None else GalgameHistoryConfig()
        self.llm = llm if llm is not None else GalgameLLMConfig()
        self.reader = reader if reader is not None else GalgameReaderConfig()
        self.memory_reader = (
            memory_reader if memory_reader is not None else GalgameMemoryReaderConfig()
        )
        self.ocr_reader = ocr_reader if ocr_reader is not None else GalgameOcrReaderConfig()
        self.rapidocr = rapidocr if rapidocr is not None else GalgameRapidOcrConfig()

        for field_name in self._FIELD_MAP:
            if field_name in legacy_fields:
                setattr(self, field_name, legacy_fields.pop(field_name))
        if legacy_fields:
            unexpected = ", ".join(sorted(legacy_fields))
            raise TypeError(f"unexpected GalgameConfig field(s): {unexpected}")


for _field_name, (_group_name, _field_group_attr) in GalgameConfig._FIELD_MAP.items():
    setattr(GalgameConfig, _field_name, _ConfigFieldProxy(_group_name, _field_group_attr))
del _field_name, _group_name, _field_group_attr


@dataclass(slots=True)
class SessionCandidate:
    game_id: str
    session_path: Path
    events_path: Path
    session: dict[str, Any]
    data_source: str = DATA_SOURCE_BRIDGE_SDK

    @property
    def session_id(self) -> str:
        return _string(self.session.get("session_id"))

    @property
    def last_seq(self) -> int:
        return max(0, _int(self.session.get("last_seq"), 0))

    @property
    def sort_key(self) -> tuple[str, str, int]:
        state = self.session.get("state")
        state_ts = _string(state.get("ts")) if isinstance(state, dict) else ""
        return (state_ts, _string(self.session.get("started_at")), self.last_seq)
