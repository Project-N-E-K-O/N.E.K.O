from __future__ import annotations

import copy
from typing import Any

from .store_keys import _RAPIDOCR_OCR_VERSIONS

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


def normalize_rapidocr_ocr_version(value: object) -> str:
    """Normalize user input to ``PP-OCRv4`` / ``PP-OCRv5`` or return ``""``."""
    raw = str(value).strip() if value is not None else ""
    if not raw:
        return ""
    lowered = raw.lower()
    canonical_by_lower = {version.lower(): version for version in _RAPIDOCR_OCR_VERSIONS}
    if lowered in canonical_by_lower:
        return canonical_by_lower[lowered]
    for version in sorted(_RAPIDOCR_OCR_VERSIONS):
        suffix = version.lower().removeprefix("pp-ocrv")
        if lowered in {f"v{suffix}", suffix}:
            return version
    return ""


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
    max_items = max(0, int(limit))
    if max_items <= 0:
        return []
    elements: list[dict[str, Any]] = []
    for item in value:
        sanitized = sanitize_screen_ui_element(item)
        if sanitized:
            elements.append(sanitized)
        if len(elements) >= max_items:
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
