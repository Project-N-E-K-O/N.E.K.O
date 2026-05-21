from __future__ import annotations

import logging
from typing import Any

from ._ocr_utils import (
    _BRACKET_SPEAKER_RE,
    _DIALOGUE_COLON_RE,
    _SPEAKER_QUOTE_RE,
    _cluster_count,
    _visible_len,
)


_LOGGER = logging.getLogger(__name__)


def _box_outside_unit_space(box: dict[str, float]) -> bool:
    return (
        box["left"] < 0.0
        or box["top"] < 0.0
        or box["right"] > 1.0
        or box["bottom"] > 1.0
    )


def _layout_features(elements: list[dict[str, Any]]) -> dict[str, float]:
    records: list[tuple[dict[str, float], str]] = []
    has_normalized_bounds = any(
        isinstance(element, dict) and isinstance(element.get("normalized_bounds"), dict)
        for element in elements
    )
    for element in elements:
        bounds = (
            dict(element.get("normalized_bounds") or {})
            if has_normalized_bounds
            else dict(element.get("bounds") or element.get("normalized_bounds") or {})
        )
        if not bounds:
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
        records.append(
            (
                {"left": left, "top": top, "right": right, "bottom": bottom},
                str(element.get("text") or ""),
            )
        )
    if has_normalized_bounds:
        unit_records = [
            record for record in records if not _box_outside_unit_space(record[0])
        ]
        if len(unit_records) != len(records):
            _LOGGER.debug(
                "layout feature analysis skipped %d non-normalized bounds",
                len(records) - len(unit_records),
            )
        records = unit_records
    elif any(_box_outside_unit_space(box) for box, _text in records):
        pixel_records = [
            record for record in records if _box_outside_unit_space(record[0])
        ]
        if len(pixel_records) != len(records):
            _LOGGER.debug(
                "layout feature analysis skipped %d unit-space boxes from mixed raw bounds",
                len(records) - len(pixel_records),
            )
        records = pixel_records
        max_right = max(max(box["right"], box["left"]) for box, _text in records)
        max_bottom = max(max(box["bottom"], box["top"]) for box, _text in records)
        if max_right > 0.0 and max_bottom > 0.0:
            records = [
                (
                    {
                        "left": box["left"] / max_right,
                        "top": box["top"] / max_bottom,
                        "right": box["right"] / max_right,
                        "bottom": box["bottom"] / max_bottom,
                    },
                    text,
                )
                for box, text in records
            ]
    boxes = [box for box, _text in records]
    if not boxes:
        return {
            "button_layout_score": 0.0,
            "save_load_grid_score": 0.0,
            "dialogue_layout_score": 0.0,
            "backlog_list_score": 0.0,
        }
    short_texts = sum(1 for _box, text in records if _visible_len(text) <= 18)
    bottom_texts = sum(
        1 for box, _text in records if (box["top"] + box["bottom"]) / 2.0 >= 0.58
    )
    centers_x = [(box["left"] + box["right"]) / 2.0 for box in boxes]
    centers_y = [(box["top"] + box["bottom"]) / 2.0 for box in boxes]
    widths = [box["right"] - box["left"] for box in boxes]
    heights = [box["bottom"] - box["top"] for box in boxes]
    vertical_spread = max(centers_y) - min(centers_y)
    horizontal_spread = max(centers_x) - min(centers_x)
    avg_width = sum(widths) / max(len(widths), 1)
    avg_height = sum(heights) / max(len(heights), 1)
    width_variance = sum(abs(width - avg_width) for width in widths) / max(len(widths), 1)
    short_ratio = short_texts / max(len(boxes), 1)
    button_layout_score = 0.0
    if 2 <= len(boxes) <= 8:
        button_layout_score = (
            min(vertical_spread / 0.35, 1.0) * 0.35
            + max(0.0, 1.0 - min(horizontal_spread / 0.35, 1.0)) * 0.25
            + max(0.0, 1.0 - min(width_variance / max(avg_width, 0.01), 1.0)) * 0.2
            + short_ratio * 0.2
        )
    row_count = _cluster_count(centers_y, tolerance=max(avg_height * 1.8, 0.05))
    col_count = _cluster_count(centers_x, tolerance=max(avg_width * 1.4, 0.06))
    save_load_grid_score = 0.0
    if len(boxes) >= 6 and row_count >= 2 and col_count >= 2:
        save_load_grid_score = min(1.0, 0.25 + (row_count * col_count) / 24.0)
    dialogue_layout_score = 0.0
    if bottom_texts and len(boxes) <= 4:
        dialogue_layout_score = min(
            1.0,
            0.25
            + (bottom_texts / max(len(boxes), 1)) * 0.35
            + min(max(widths) / 0.7, 1.0) * 0.25
            + min(vertical_spread / 0.18, 1.0) * 0.15,
        )
    dialogue_like_texts = sum(
        1
        for _box, text in records
        if _DIALOGUE_COLON_RE.match(text)
        or _SPEAKER_QUOTE_RE.match(text)
        or _BRACKET_SPEAKER_RE.match(text)
    )
    backlog_list_score = 0.0
    if len(boxes) >= 4 and row_count >= 4 and dialogue_like_texts >= 3:
        top_or_middle_ratio = sum(
            1 for box in boxes if (box["top"] + box["bottom"]) / 2.0 <= 0.72
        ) / max(len(boxes), 1)
        dialogue_like_ratio = dialogue_like_texts / max(len(boxes), 1)
        backlog_list_score = min(
            1.0,
            0.25
            + min(vertical_spread / 0.55, 1.0) * 0.25
            + min(row_count / 6.0, 1.0) * 0.2
            + top_or_middle_ratio * 0.15
            + dialogue_like_ratio * 0.15,
        )
    return {
        "button_layout_score": round(button_layout_score, 2),
        "save_load_grid_score": round(save_load_grid_score, 2),
        "dialogue_layout_score": round(dialogue_layout_score, 2),
        "backlog_list_score": round(backlog_list_score, 2),
    }


def _clamp_unit(value: float) -> float:
    return max(0.0, min(float(value), 1.0))


def _nondegenerate_unit_interval(start: float, end: float) -> tuple[float, float]:
    left = _clamp_unit(start)
    right = _clamp_unit(end)
    if left < right:
        return left, right
    min_span = 0.01
    if left >= 1.0:
        return max(0.0, 1.0 - min_span), 1.0
    if right <= 0.0:
        return 0.0, min_span
    right = min(left + min_span, 1.0)
    if left >= right:
        left = max(0.0, right - min_span)
    return left, right


def _normalized_bounds(bounds: dict[str, float], metadata: dict[str, Any]) -> dict[str, float]:
    try:
        bounds_left = float(bounds["left"])
        bounds_top = float(bounds["top"])
        bounds_right = float(bounds["right"])
        bounds_bottom = float(bounds["bottom"])
    except (KeyError, TypeError, ValueError):
        return {}

    capture_rect = _coerce_rect(metadata.get("capture_rect"))
    window_rect = _coerce_rect(metadata.get("window_rect"))
    if not capture_rect or not window_rect:
        source_size = metadata.get("source_size")
        if not isinstance(source_size, dict):
            return {}
        try:
            width = float(source_size.get("width"))
            height = float(source_size.get("height"))
        except (TypeError, ValueError):
            return {}
        if width <= 0 or height <= 0:
            return {}
        left, right = _nondegenerate_unit_interval(
            bounds_left / width,
            bounds_right / width,
        )
        top, bottom = _nondegenerate_unit_interval(
            bounds_top / height,
            bounds_bottom / height,
        )
        return {
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
        }
    window_width = max(window_rect["right"] - window_rect["left"], 1.0)
    window_height = max(window_rect["bottom"] - window_rect["top"], 1.0)
    left, right = _nondegenerate_unit_interval(
        (capture_rect["left"] + bounds_left - window_rect["left"]) / window_width,
        (capture_rect["left"] + bounds_right - window_rect["left"]) / window_width,
    )
    top, bottom = _nondegenerate_unit_interval(
        (capture_rect["top"] + bounds_top - window_rect["top"]) / window_height,
        (capture_rect["top"] + bounds_bottom - window_rect["top"]) / window_height,
    )
    return {
        "left": left,
        "top": top,
        "right": right,
        "bottom": bottom,
    }


def _coerce_rect(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    try:
        rect = {
            "left": float(value.get("left")),
            "top": float(value.get("top")),
            "right": float(value.get("right")),
            "bottom": float(value.get("bottom")),
        }
    except (TypeError, ValueError):
        return {}
    if rect["right"] <= rect["left"] or rect["bottom"] <= rect["top"]:
        return {}
    return rect
