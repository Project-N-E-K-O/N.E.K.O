from __future__ import annotations

from typing import Any, Iterable


def _template_regions(value: object) -> list[dict[str, float]]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, bytearray, dict)):
        return []
    regions: list[dict[str, float]] = []
    for item in list(value)[:8]:
        if not isinstance(item, dict):
            continue
        try:
            left = float(item.get("left"))
            top = float(item.get("top"))
            right = float(item.get("right"))
            bottom = float(item.get("bottom"))
            raw_min_overlap = item.get("min_overlap")
            min_overlap = float(0.35 if raw_min_overlap is None else raw_min_overlap)
        except (TypeError, ValueError):
            continue
        if right <= left or bottom <= top:
            continue
        regions.append(
            {
                "left": max(0.0, min(left, 1.0)),
                "top": max(0.0, min(top, 1.0)),
                "right": max(0.0, min(right, 1.0)),
                "bottom": max(0.0, min(bottom, 1.0)),
                "min_overlap": max(0.0, min(min_overlap, 1.0)),
            }
        )
    return regions


def _template_region_hits(
    regions: list[dict[str, float]],
    ui_elements: list[dict[str, Any]],
) -> int:
    if not regions or not ui_elements:
        return 0
    hits = 0
    for region in regions:
        for element in ui_elements:
            bounds = element.get("normalized_bounds") if isinstance(element, dict) else None
            if not isinstance(bounds, dict):
                continue
            try:
                left = float(bounds.get("left"))
                top = float(bounds.get("top"))
                right = float(bounds.get("right"))
                bottom = float(bounds.get("bottom"))
            except (TypeError, ValueError):
                continue
            if right <= left or bottom <= top:
                continue
            overlap_left = max(left, region["left"])
            overlap_top = max(top, region["top"])
            overlap_right = min(right, region["right"])
            overlap_bottom = min(bottom, region["bottom"])
            if overlap_right <= overlap_left or overlap_bottom <= overlap_top:
                continue
            element_area = max((right - left) * (bottom - top), 0.0001)
            overlap_area = (overlap_right - overlap_left) * (overlap_bottom - overlap_top)
            raw_min_overlap = region.get("min_overlap")
            min_overlap = float(0.35 if raw_min_overlap is None else raw_min_overlap)
            if overlap_area / element_area >= min_overlap:
                hits += 1
                break
    return hits


def _template_string_list(value: object) -> list[str]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, Iterable) and not isinstance(value, (dict, bytes, bytearray)):
        items = list(value)
    else:
        return []
    result: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text:
            result.append(text)
    return result


def _template_matches_context(
    template: dict[str, Any],
    context: dict[str, Any],
) -> bool:
    process_name = str(context.get("process_name") or "").strip().casefold()
    window_title = str(context.get("window_title") or "").strip().casefold()
    game_id = str(context.get("game_id") or "").strip().casefold()
    process_names = [item.casefold() for item in _template_string_list(template.get("process_names") or template.get("process_name"))]
    if process_names and process_name not in process_names:
        return False
    process_contains = [item.casefold() for item in _template_string_list(template.get("process_name_contains"))]
    if process_contains and not any(item in process_name for item in process_contains):
        return False
    title_contains = [item.casefold() for item in _template_string_list(template.get("window_title_contains"))]
    if title_contains and not any(item in window_title for item in title_contains):
        return False
    game_ids = [item.casefold() for item in _template_string_list(template.get("game_ids") or template.get("game_id"))]
    if game_ids and game_id not in game_ids:
        return False
    try:
        width = int(context.get("width") or 0)
        height = int(context.get("height") or 0)
        template_width = int(template.get("width") or 0)
        template_height = int(template.get("height") or 0)
        tolerance = max(0, int(template.get("resolution_tolerance") or 8))
    except (TypeError, ValueError):
        return False
    if template_width > 0 and template_height > 0:
        if width <= 0 or height <= 0:
            return False
        if abs(width - template_width) > tolerance or abs(height - template_height) > tolerance:
            return False
    return True


def _template_context_score(template: dict[str, Any], context: dict[str, Any]) -> int:
    score = 0
    for key in ("process_names", "process_name", "process_name_contains", "window_title_contains", "game_ids", "game_id"):
        if _template_string_list(template.get(key)):
            score += 1
    try:
        if int(context.get("width") or 0) > 0 and int(template.get("width") or 0) > 0:
            score += 1
    except (TypeError, ValueError):
        pass
    return score
