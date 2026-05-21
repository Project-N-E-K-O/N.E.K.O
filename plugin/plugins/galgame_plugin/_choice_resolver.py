from __future__ import annotations

from typing import Any

from .models import SharedStatePayload
from ._input_primitives import (
    _coerce_rect,
    _coerce_source_size,
    _rect_payload,
    _relative_point_forbidden_zone,
)


SYSTEM_MENU_MARKERS = (
    "SYSTEM",
    "重置选项",
    "语言设置",
    "画面设置",
    "选项设置",
    "回到标题",
    "返回",
)
VIRTUAL_MOUSE_DIALOGUE_CANDIDATES = (
    {"target_id": "dialogue_continue_primary", "relative_x": 0.23, "relative_y": 0.75},
    {"target_id": "dialogue_text_left", "relative_x": 0.18, "relative_y": 0.74},
    {"target_id": "dialogue_text_mid", "relative_x": 0.30, "relative_y": 0.76},
)


def _snapshot_has_visible_choices(shared: SharedStatePayload) -> bool:
    snapshot = shared.get("latest_snapshot")
    if not isinstance(snapshot, dict):
        return False
    return bool(snapshot.get("is_menu_open")) or bool(list(snapshot.get("choices") or []))


def _resolve_virtual_mouse_dialogue_target(
    actuation: dict[str, Any],
    client_rect: tuple[int, int, int, int],
    *,
    candidates: tuple[dict[str, float | str], ...] = VIRTUAL_MOUSE_DIALOGUE_CANDIDATES,
) -> dict[str, Any]:
    left, top, right, bottom = client_rect
    width = max(int(right - left), 1)
    height = max(int(bottom - top), 1)
    start_index = max(0, int(actuation.get("instruction_variant") or 0))
    requested_target_id = str(actuation.get("virtual_mouse_target_id") or "").strip()
    requested_indices = [
        index
        for index, candidate in enumerate(candidates)
        if str(candidate.get("target_id") or "") == requested_target_id
    ]
    fallback_indices = [
        (start_index + offset) % len(candidates)
        for offset in range(len(candidates))
    ]
    ordered_indices: list[int] = []
    for candidate_index in [*requested_indices, *fallback_indices]:
        if candidate_index not in ordered_indices:
            ordered_indices.append(candidate_index)
    skipped: list[dict[str, Any]] = []
    for candidate_index in ordered_indices:
        candidate = candidates[candidate_index]
        relative_x = float(candidate.get("relative_x") or 0.0)
        relative_y = float(candidate.get("relative_y") or 0.0)
        zone_id = _relative_point_forbidden_zone(relative_x, relative_y)
        if zone_id:
            skipped.append(
                {
                    "target_id": str(candidate.get("target_id") or ""),
                    "candidate_index": candidate_index,
                    "relative_x": relative_x,
                    "relative_y": relative_y,
                    "forbidden_zone": zone_id,
                }
            )
            continue
        clamped_x = max(0.0, min(relative_x, 1.0))
        clamped_y = max(0.0, min(relative_y, 1.0))
        screen_x = left + min(int(clamped_x * width), width - 1)
        screen_y = top + min(int(clamped_y * height), height - 1)
        return {
            "success": True,
            "target_id": str(candidate.get("target_id") or ""),
            "candidate_index": candidate_index,
            "relative_x": relative_x,
            "relative_y": relative_y,
            "screen_x": int(screen_x),
            "screen_y": int(screen_y),
            "client_rect": _rect_payload(client_rect),
            "forbidden_zone_hit": False,
            "requested_target_id": requested_target_id,
            "skipped_candidates": skipped,
        }
    return {
        "success": False,
        "reason": "virtual_mouse_candidates_blocked_by_forbidden_zones",
        "client_rect": _rect_payload(client_rect),
        "forbidden_zone_hit": True,
        "requested_target_id": requested_target_id,
        "skipped_candidates": skipped,
    }


def _choose_index(actuation: dict[str, Any]) -> int:
    choices = list(actuation.get("candidate_choices") or [])
    candidate_index = max(0, int(actuation.get("candidate_index") or 0))
    if candidate_index < len(choices):
        return max(0, int(dict(choices[candidate_index]).get("index") or 0))
    return candidate_index


def _choose_choice(actuation: dict[str, Any]) -> dict[str, Any]:
    choices = list(actuation.get("candidate_choices") or [])
    candidate_index = max(0, int(actuation.get("candidate_index") or 0))
    if candidate_index >= len(choices):
        return {}
    return dict(choices[candidate_index] or {})


def _choose_bounds(actuation: dict[str, Any]) -> dict[str, float]:
    bounds = dict(_choose_choice(actuation).get("bounds") or {})
    try:
        left = float(bounds.get("left"))
        top = float(bounds.get("top"))
        right = float(bounds.get("right"))
        bottom = float(bounds.get("bottom"))
    except (TypeError, ValueError):
        return {}
    if right <= left or bottom <= top:
        return {}
    return {"left": left, "top": top, "right": right, "bottom": bottom}


def _snapshot_screen_type(shared: SharedStatePayload) -> str:
    snapshot = shared.get("latest_snapshot")
    if isinstance(snapshot, dict):
        screen_type = str(snapshot.get("screen_type") or "").strip()
        if screen_type:
            return screen_type
    return str(shared.get("screen_type") or "").strip()


def _recover_should_press_escape(shared: SharedStatePayload, actuation: dict[str, Any]) -> bool:
    strategy_id = str(actuation.get("strategy_id") or "")
    if strategy_id in {"save_load_escape", "config_escape", "gallery_escape", "game_over_escape"}:
        return True
    return _snapshot_screen_type(shared) in {
        "save_load_stage",
        "config_stage",
        "gallery_stage",
        "game_over_stage",
    }


def _resolve_choice_bounds_click_target(
    actuation: dict[str, Any],
    bounds: dict[str, float],
    *,
    window_rect: tuple[int, int, int, int],
    client_rect: tuple[int, int, int, int],
) -> dict[str, Any]:
    choice = _choose_choice(actuation)
    bounds_payload = dict(choice.get("bounds") or {})
    coordinate_space = str(
        choice.get("bounds_coordinate_space")
        or bounds_payload.get("bounds_coordinate_space")
        or ""
    ).strip().lower()
    capture_rect = _coerce_rect(choice.get("capture_rect") or bounds_payload.get("capture_rect"))
    metadata_window_rect = _coerce_rect(
        choice.get("window_rect") or bounds_payload.get("window_rect")
    )
    source_width, source_height = _coerce_source_size(
        choice.get("source_size") or bounds_payload.get("source_size")
    )

    if coordinate_space == "capture" and capture_rect != (0, 0, 0, 0):
        target_rect = capture_rect
        resolved_space = "capture"
    elif not coordinate_space and capture_rect != (0, 0, 0, 0):
        target_rect = capture_rect
        resolved_space = "capture"
    elif coordinate_space == "client" and client_rect != (0, 0, 0, 0):
        target_rect = client_rect
        resolved_space = "client"
    else:
        target_rect = metadata_window_rect if metadata_window_rect != (0, 0, 0, 0) else window_rect
        resolved_space = "window"

    left, top, right, bottom = target_rect
    width = max(right - left, 1)
    height = max(bottom - top, 1)
    if source_width <= 0.0:
        source_width = float(width)
    if source_height <= 0.0:
        source_height = float(height)

    center_x = float(bounds["left"] + bounds["right"]) / 2.0
    center_y = float(bounds["top"] + bounds["bottom"]) / 2.0
    text_left_x = float(bounds["left"]) + min(
        16.0,
        max(4.0, (float(bounds["right"]) - float(bounds["left"])) * 0.12),
    )
    screen_points: list[dict[str, int]] = []
    for raw_x, raw_y in ((center_x, center_y), (text_left_x, center_y)):
        clamped_x = max(0.0, min(raw_x / source_width, 1.0))
        clamped_y = max(0.0, min(raw_y / source_height, 1.0))
        x = left + min(int(clamped_x * width), width - 1)
        y = top + min(int(clamped_y * height), height - 1)
        screen_points.append({"x": int(x), "y": int(y)})

    return {
        "coordinate_space": resolved_space,
        "source_size": {"width": float(source_width), "height": float(source_height)},
        "target_rect": _rect_payload(target_rect),
        "window_rect": _rect_payload(window_rect),
        "client_rect": _rect_payload(client_rect),
        "capture_rect": _rect_payload(capture_rect) if capture_rect != (0, 0, 0, 0) else {},
        "bounds": dict(bounds),
        "screen_points": screen_points,
    }


def _snapshot_text(shared: SharedStatePayload) -> str:
    snapshot = shared.get("latest_snapshot")
    if not isinstance(snapshot, dict):
        return ""
    return "\n".join(
        str(snapshot.get(key) or "").strip()
        for key in ("speaker", "text")
        if str(snapshot.get(key) or "").strip()
    )


def _looks_like_system_menu(shared: SharedStatePayload) -> bool:
    text = _snapshot_text(shared)
    if not text:
        return False
    return sum(1 for marker in SYSTEM_MENU_MARKERS if marker in text) >= 2
