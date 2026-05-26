from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .ocr_chrome_noise import (
    TEMPERATURE_STATUS_BOTTOM_MIN_RATIO,
    TEMPERATURE_STATUS_LEFT_MAX_RATIO,
    WINDOW_TITLE_TOP_MAX_RATIO,
    looks_like_temperature_status_line,
    looks_like_window_title_line,
)
from .models import (
    MENU_PREFIX_RE as _MENU_PREFIX_RE,
    OCR_CAPTURE_PROFILE_STAGE_CONFIG,
    OCR_CAPTURE_PROFILE_STAGE_DEFAULT,
    OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
    OCR_CAPTURE_PROFILE_STAGE_GALLERY,
    OCR_CAPTURE_PROFILE_STAGE_GAME_OVER,
    OCR_CAPTURE_PROFILE_STAGE_MINIGAME,
    OCR_CAPTURE_PROFILE_STAGE_MENU,
    OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD,
    OCR_CAPTURE_PROFILE_STAGE_TITLE,
    OCR_CAPTURE_PROFILE_STAGE_TRANSITION,
    OCR_CAPTURE_PROFILE_STAGES,
    json_copy,
    sanitize_screen_ui_elements,
)


try:
    from PIL import Image as _PIL_IMAGE_MODULE

    _PIL_RESAMPLING = getattr(_PIL_IMAGE_MODULE, "Resampling", None)
except ImportError:  # pragma: no cover - optional in non-visual test environments.
    _PIL_RESAMPLING = None


_RAW_OCR_TEXT_LIMIT = 20
_RAW_OCR_LINE_MAX_CHARS = 120
_DIALOGUE_COLON_RE = re.compile(r"^[^:：]{1,40}[:：]\s*.+\S$")
_SPEAKER_QUOTE_RE = re.compile(r"^[^「」『』:：]{1,40}[「『].+[」』]$")
_BRACKET_SPEAKER_RE = re.compile(r"^[【\[][^\]】]{1,40}[\]】]\s*.+\S$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LOGGER = logging.getLogger(__name__)
_DEFAULT_MODEL_FEATURE_SCALES = {
    "mean_luminance": 255.0,
    "luminance_std": 128.0,
    "texture_score": 128.0,
    "button_layout_score": 1.0,
    "dialogue_layout_score": 1.0,
    "backlog_list_score": 1.0,
    "save_load_grid_score": 1.0,
    "element_count": 10.0,
    "line_count": 20.0,
    "ui_element_count": 10.0,
    "horizontal_cluster_count": 10.0,
    "vertical_cluster_count": 10.0,
}

from ._stage_keywords import (
    _BACK_KEYWORDS,
    _BACKLOG_KEYWORDS,
    _CONFIG_KEYWORDS,
    _GALLERY_KEYWORDS,
    _GAME_OVER_KEYWORDS,
    _MINIGAME_KEYWORDS,
    _SAVE_LOAD_KEYWORDS,
    _TITLE_EXIT_KEYWORDS,
    _TITLE_KEYWORDS,
)
from ._ocr_pipeline import (
    SCREEN_UI_ELEMENT_LIMIT,
    _clean_line,
    _coerce_ocr_regions,
    _filter_chrome_noise_ui_elements,
    _has_long_dialogue_line,
    _keyword_hits,
    _looks_like_backlog,
    _looks_like_backlog_dialogue_list,
    _looks_like_config,
    _looks_like_dialogue,
    _looks_like_game_over,
    _looks_like_gallery,
    _looks_like_minigame,
    _looks_like_save_load,
    _looks_like_title,
    _merged_ocr_lines,
    _merged_screen_ui_elements,
    _normalize_for_match,
    _ocr_lines,
    _screen_ui_elements,
)
from ._layout import _layout_features, _normalized_bounds
from ._ocr_utils import (
    _bounded_debug_value,
    _bounded_raw_text,
    _confidence,
    _dedupe_preserve_order,
    _float,
    _visible_len,
)
from ._templates import (
    _template_context_score,
    _template_matches_context,
    _template_region_hits,
    _template_regions,
    _template_string_list,
)




@dataclass(slots=True)
class ScreenClassification:
    screen_type: str = OCR_CAPTURE_PROFILE_STAGE_DEFAULT
    confidence: float = 0.0
    ui_elements: list[dict[str, Any]] = field(default_factory=list)
    raw_ocr_text: list[str] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "screen_type": self.screen_type,
            "screen_confidence": self.confidence,
            "screen_ui_elements": json_copy(self.ui_elements),
            "raw_ocr_text": list(self.raw_ocr_text),
            "screen_debug": json_copy(self.debug),
        }


@dataclass(slots=True)
class _OcrRegion:
    source: str
    text: str = ""
    boxes: list[Any] = field(default_factory=list)
    bounds_metadata: dict[str, Any] = field(default_factory=dict)


def classify_screen_from_ocr(
    ocr_text: str,
    *,
    boxes: Iterable[Any] | None = None,
    bounds_metadata: dict[str, Any] | None = None,
    ocr_regions: Iterable[dict[str, Any]] | None = None,
    visual_features: dict[str, Any] | None = None,
    screen_templates: Iterable[dict[str, Any]] | None = None,
    template_context: dict[str, Any] | None = None,
) -> ScreenClassification:
    regions = _coerce_ocr_regions(
        ocr_text,
        boxes=boxes,
        bounds_metadata=bounds_metadata,
        ocr_regions=ocr_regions,
    )
    lines = _merged_ocr_lines(regions)
    ui_elements = _merged_screen_ui_elements(regions, lines=lines)
    ui_elements, filtered_count = _filter_chrome_noise_ui_elements(
        ui_elements,
        window_title=str((template_context or {}).get("window_title") or ""),
    )
    if filtered_count > 0:
        lines = _dedupe_preserve_order(
            _clean_line(str(element.get("text") or ""))
            for element in ui_elements
            if _clean_line(str(element.get("text") or ""))
        )
    visual = dict(visual_features or {})
    layout = _layout_features(ui_elements)
    debug: dict[str, Any] = {
        "sources": [region.source for region in regions if region.source],
        "line_count": len(lines),
        "ui_element_count": len(ui_elements),
        "chrome_filtered_count": filtered_count,
        "visual": _bounded_debug_value(visual),
        "layout": layout,
        "reason": "",
    }

    normalized_lines = [_normalize_for_match(line) for line in lines]
    joined = " ".join(normalized_lines)
    menu_prefix_count = sum(1 for line in lines if _MENU_PREFIX_RE.match(line))
    short_line_count = sum(1 for line in lines if _visible_len(line) <= 18)
    title_hits = _keyword_hits(normalized_lines, _TITLE_KEYWORDS) + _keyword_hits(
        normalized_lines, _TITLE_EXIT_KEYWORDS
    )
    save_hits = _keyword_hits(normalized_lines, _SAVE_LOAD_KEYWORDS)
    config_hits = _keyword_hits(normalized_lines, _CONFIG_KEYWORDS)
    back_hits = _keyword_hits(normalized_lines, _BACK_KEYWORDS)
    backlog_hits = _keyword_hits(normalized_lines, _BACKLOG_KEYWORDS)
    gallery_hits = _keyword_hits(normalized_lines, _GALLERY_KEYWORDS)
    minigame_hits = _keyword_hits(normalized_lines, _MINIGAME_KEYWORDS)
    game_over_hits = _keyword_hits(normalized_lines, _GAME_OVER_KEYWORDS)
    debug.update(
        {
            "keyword_hits": {
                "title": title_hits,
                "save_load": save_hits,
                "config": config_hits,
                "back": back_hits,
                "backlog": backlog_hits,
                "gallery": gallery_hits,
                "minigame": minigame_hits,
                "game_over": game_over_hits,
            },
            "menu_prefix_count": menu_prefix_count,
            "short_line_count": short_line_count,
        }
    )

    template_classification = _classification_from_templates(
        screen_templates,
        template_context=template_context or {},
        normalized_lines=normalized_lines,
        lines=lines,
        ui_elements=ui_elements,
        debug=debug,
    )
    if template_classification is not None:
        return template_classification

    if not lines:
        visual_classification = _classification_from_visual(
            visual=visual,
            layout=layout,
            ui_elements=ui_elements,
            lines=[],
            debug=debug,
        )
        if visual_classification is not None:
            return visual_classification
        debug["reason"] = "no_ocr_text"
        return ScreenClassification(raw_ocr_text=[], debug=debug)

    if menu_prefix_count >= 2 and max(save_hits, config_hits) < 2:
        return _classified(
            OCR_CAPTURE_PROFILE_STAGE_MENU,
            0.72 + min(menu_prefix_count, 4) * 0.04,
            lines=lines,
            ui_elements=ui_elements,
            debug=debug,
            reason="prefixed_menu_lines",
        )

    if _looks_like_backlog(backlog_hits, lines, normalized_lines):
        return _classified(
            OCR_CAPTURE_PROFILE_STAGE_GALLERY,
            0.62 + min(backlog_hits, 4) * 0.05 + min(back_hits, 1) * 0.03,
            lines=lines,
            ui_elements=ui_elements,
            debug=debug,
            reason="backlog_keywords",
        )

    if _looks_like_backlog_dialogue_list(lines, layout=layout):
        return _classified(
            OCR_CAPTURE_PROFILE_STAGE_GALLERY,
            0.58 + min(layout.get("backlog_list_score", 0.0), 0.18),
            lines=lines,
            ui_elements=ui_elements,
            debug=debug,
            reason="backlog_dialogue_list",
        )

    if _looks_like_save_load(save_hits, config_hits, title_hits, normalized_lines, joined):
        return _classified(
            OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD,
            0.62 + min(save_hits, 5) * 0.05 + min(back_hits, 1) * 0.03,
            lines=lines,
            ui_elements=ui_elements,
            debug=debug,
            reason="save_load_keywords",
        )

    if _looks_like_config(config_hits, save_hits, title_hits, normalized_lines):
        return _classified(
            OCR_CAPTURE_PROFILE_STAGE_CONFIG,
            0.62 + min(config_hits, 5) * 0.05 + min(back_hits, 1) * 0.03,
            lines=lines,
            ui_elements=ui_elements,
            debug=debug,
            reason="config_keywords",
        )

    if _looks_like_game_over(game_over_hits, title_hits, normalized_lines, joined):
        return _classified(
            OCR_CAPTURE_PROFILE_STAGE_GAME_OVER,
            0.64 + min(game_over_hits, 4) * 0.06,
            lines=lines,
            ui_elements=ui_elements,
            debug=debug,
            reason="game_over_keywords",
        )

    if _looks_like_gallery(gallery_hits, title_hits, normalized_lines, joined):
        return _classified(
            OCR_CAPTURE_PROFILE_STAGE_GALLERY,
            0.58 + min(gallery_hits, 5) * 0.06 + min(back_hits, 1) * 0.03,
            lines=lines,
            ui_elements=ui_elements,
            debug=debug,
            reason="gallery_keywords",
        )

    if _looks_like_minigame(minigame_hits, normalized_lines, joined):
        return _classified(
            OCR_CAPTURE_PROFILE_STAGE_MINIGAME,
            0.56 + min(minigame_hits, 5) * 0.06,
            lines=lines,
            ui_elements=ui_elements,
            debug=debug,
            reason="minigame_keywords",
        )

    if _looks_like_title(title_hits, save_hits, config_hits, short_line_count, normalized_lines):
        return _classified(
            OCR_CAPTURE_PROFILE_STAGE_TITLE,
            0.64 + min(title_hits, 5) * 0.05 + min(layout.get("button_layout_score", 0.0), 0.2),
            lines=lines,
            ui_elements=ui_elements,
            debug=debug,
            reason="title_keywords",
        )

    visual_classification = _classification_from_visual(
        visual=visual,
        layout=layout,
        ui_elements=ui_elements,
        lines=lines,
        debug=debug,
    )
    if visual_classification is not None and visual_classification.confidence >= 0.45:
        return visual_classification

    if _looks_like_dialogue(lines, joined, layout=layout):
        return _classified(
            OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
            0.55 + min(len(lines), 3) * 0.05 + min(layout.get("dialogue_layout_score", 0.0), 0.1),
            lines=lines,
            ui_elements=ui_elements,
            debug=debug,
            reason="dialogue_text_or_layout",
        )

    debug["reason"] = "default_no_match"
    return ScreenClassification(
        screen_type=OCR_CAPTURE_PROFILE_STAGE_DEFAULT,
        confidence=0.0,
        ui_elements=ui_elements,
        raw_ocr_text=_bounded_raw_text(lines),
        debug=debug,
    )


def analyze_screen_visual_features(
    image: Any,
    *,
    boxes: Iterable[Any] | None = None,
    bounds_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    features: dict[str, Any] = {}
    try:
        gray = image.convert("L") if hasattr(image, "convert") else image
        if _PIL_RESAMPLING is not None:
            resized = gray.resize((64, 64), _PIL_RESAMPLING.BILINEAR)
        else:
            resized = gray.resize((64, 64))
        pixels = [int(value) for value in resized.getdata()]
        if pixels:
            mean_value = sum(pixels) / len(pixels)
            variance = sum((value - mean_value) ** 2 for value in pixels) / len(pixels)
            features["mean_luminance"] = round(mean_value, 2)
            features["luminance_std"] = round(math.sqrt(variance), 2)
            diffs: list[int] = []
            for y in range(64):
                row_offset = y * 64
                for x in range(63):
                    diffs.append(abs(pixels[row_offset + x + 1] - pixels[row_offset + x]))
            for y in range(63):
                row_offset = y * 64
                next_offset = (y + 1) * 64
                for x in range(64):
                    diffs.append(abs(pixels[next_offset + x] - pixels[row_offset + x]))
            features["texture_score"] = round(sum(diffs) / max(len(diffs), 1), 2)
    except Exception:
        _LOGGER.debug("visual feature analysis failed", exc_info=True)

    elements = _screen_ui_elements(
        _ocr_lines("", boxes=boxes),
        boxes=boxes,
        bounds_metadata=bounds_metadata,
        source="visual_boxes",
    )
    features.update(_layout_features(elements))
    return features


def classify_screen_awareness_model(
    features: dict[str, Any],
    model_payload: dict[str, Any],
    *,
    min_confidence: float = 0.55,
) -> dict[str, Any] | None:
    if not isinstance(features, dict) or not isinstance(model_payload, dict):
        return None
    prototypes = model_payload.get("prototypes") or model_payload.get("labels") or []
    if not isinstance(prototypes, Iterable) or isinstance(prototypes, (str, bytes, bytearray, dict)):
        return None
    feature_scales = model_payload.get("feature_scales")
    if not isinstance(feature_scales, dict):
        feature_scales = {}
    feature_weights = model_payload.get("feature_weights")
    if not isinstance(feature_weights, dict):
        feature_weights = {}

    best: dict[str, Any] | None = None
    for index, raw_prototype in enumerate(list(prototypes)[:64]):
        if not isinstance(raw_prototype, dict):
            continue
        stage = normalize_screen_type(
            raw_prototype.get("stage")
            or raw_prototype.get("screen_type")
            or raw_prototype.get("label")
        )
        if not stage or stage == OCR_CAPTURE_PROFILE_STAGE_DEFAULT:
            continue
        prototype_features = (
            raw_prototype.get("features")
            or raw_prototype.get("visual_features")
            or raw_prototype.get("feature_vector")
        )
        if not isinstance(prototype_features, dict):
            continue
        distance = 0.0
        total_weight = 0.0
        used_features: list[str] = []
        for key, expected_value in prototype_features.items():
            if key not in features:
                continue
            expected = _float(expected_value, math.nan)
            actual = _float(features.get(key), math.nan)
            if not math.isfinite(expected) or not math.isfinite(actual):
                continue
            scale = abs(
                _float(
                    feature_scales.get(key),
                    _DEFAULT_MODEL_FEATURE_SCALES.get(str(key), 1.0),
                )
            )
            if scale <= 0.0 or not math.isfinite(scale):
                scale = 1.0
            weight = abs(_float(feature_weights.get(key), 1.0))
            if weight <= 0.0 or not math.isfinite(weight):
                continue
            delta = (actual - expected) / scale
            distance += weight * delta * delta
            total_weight += weight
            used_features.append(str(key))
        if len(used_features) < 2 or total_weight <= 0.0:
            continue
        normalized_distance = math.sqrt(distance / total_weight)
        similarity = 1.0 / (1.0 + normalized_distance)
        base_confidence = _float(
            raw_prototype.get("confidence", model_payload.get("base_confidence", 0.85)),
            0.85,
        )
        confidence = _confidence(base_confidence * similarity)
        candidate = {
            "stage": stage,
            "confidence": confidence,
            "prototype_id": str(
                raw_prototype.get("id")
                or raw_prototype.get("name")
                or f"prototype-{index + 1}"
            ),
            "distance": round(normalized_distance, 4),
            "feature_count": len(used_features),
            "features": used_features[:12],
        }
        if best is None or float(candidate["confidence"]) > float(best["confidence"]):
            best = candidate
    if best is None or float(best.get("confidence") or 0.0) < float(min_confidence):
        return None
    return best


def normalize_screen_type(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in OCR_CAPTURE_PROFILE_STAGES:
        return normalized
    return OCR_CAPTURE_PROFILE_STAGE_DEFAULT if normalized else ""


def _classification_from_templates(
    templates: Iterable[dict[str, Any]] | None,
    *,
    template_context: dict[str, Any],
    normalized_lines: list[str],
    lines: list[str],
    ui_elements: list[dict[str, Any]],
    debug: dict[str, Any],
) -> ScreenClassification | None:
    candidates: list[dict[str, Any]] = []
    for index, raw_template in enumerate(list(templates or [])[:32]):
        if not isinstance(raw_template, dict):
            continue
        stage = normalize_screen_type(raw_template.get("stage") or raw_template.get("screen_type"))
        if not stage or stage == OCR_CAPTURE_PROFILE_STAGE_DEFAULT:
            continue
        if not _template_matches_context(raw_template, template_context):
            continue
        exclude_keywords = _template_string_list(raw_template.get("exclude_keywords"))
        if exclude_keywords and _keyword_hits(normalized_lines, tuple(exclude_keywords)) > 0:
            continue
        keywords = _template_string_list(raw_template.get("keywords"))
        keyword_hits = _keyword_hits(normalized_lines, tuple(keywords)) if keywords else 0
        regions = _template_regions(raw_template.get("regions"))
        region_hits = _template_region_hits(regions, ui_elements)
        try:
            min_keyword_hits = int(
                raw_template.get("min_keyword_hits") if raw_template.get("min_keyword_hits") is not None else (1 if keywords else 0)
            )
        except (TypeError, ValueError):
            min_keyword_hits = 1 if keywords else 0
        try:
            min_region_hits = int(
                raw_template.get("min_region_hits") if raw_template.get("min_region_hits") is not None else (1 if regions else 0)
            )
        except (TypeError, ValueError):
            min_region_hits = 1 if regions else 0
        match_without_keywords = bool(raw_template.get("match_without_keywords"))
        if keywords and keyword_hits < max(1, min_keyword_hits):
            continue
        if (
            regions
            and region_hits < max(1, min_region_hits)
            and (not keywords or keyword_hits < max(1, min_keyword_hits))
        ):
            continue
        if not keywords and not regions and not match_without_keywords:
            continue
        try:
            priority = int(raw_template.get("priority") or 0)
        except (TypeError, ValueError):
            priority = 0
        context_score = _template_context_score(raw_template, template_context)
        candidates.append(
            {
                "index": index,
                "stage": stage,
                "keyword_hits": keyword_hits,
                "region_hits": region_hits,
                "priority": priority,
                "context_score": context_score,
                "id": str(raw_template.get("id") or raw_template.get("name") or f"template-{index + 1}"),
            }
        )
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            int(item["priority"]),
            int(item["keyword_hits"]),
            int(item["region_hits"]),
            int(item["context_score"]),
            -int(item["index"]),
        ),
        reverse=True,
    )
    winner = candidates[0]
    result_debug = dict(debug)
    result_debug["template"] = {
        "id": winner["id"],
        "stage": winner["stage"],
        "keyword_hits": winner["keyword_hits"],
        "region_hits": winner["region_hits"],
        "priority": winner["priority"],
        "context_score": winner["context_score"],
    }
    return _classified(
        str(winner["stage"]),
        0.58
        + min(float(winner["keyword_hits"]) * 0.06, 0.24)
        + min(float(winner["region_hits"]) * 0.05, 0.15)
        + min(float(winner["context_score"]) * 0.03, 0.09),
        lines=lines,
        ui_elements=ui_elements,
        debug=result_debug,
        reason="screen_template",
    )


def _classification_from_visual(
    *,
    visual: dict[str, Any],
    layout: dict[str, float],
    ui_elements: list[dict[str, Any]],
    lines: list[str],
    debug: dict[str, Any],
) -> ScreenClassification | None:
    mean_luminance = _float(visual.get("mean_luminance"), 0.0)
    luminance_std = _float(visual.get("luminance_std"), 0.0)
    texture_score = _float(visual.get("texture_score"), 0.0)
    if (
        visual
        and not lines
        and (mean_luminance <= 12.0 or mean_luminance >= 243.0)
        and luminance_std <= 10.0
        and texture_score <= 5.0
    ):
        return _classified(
            OCR_CAPTURE_PROFILE_STAGE_TRANSITION,
            0.62,
            lines=lines,
            ui_elements=ui_elements,
            debug=debug,
            reason="visual_blank_transition",
        )
    if layout.get("save_load_grid_score", 0.0) >= 0.65 and not _has_long_dialogue_line(lines):
        return _classified(
            OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD,
            0.56 + min(layout.get("save_load_grid_score", 0.0), 0.2),
            lines=lines,
            ui_elements=ui_elements,
            debug=debug,
            reason="visual_grid_layout",
        )
    if (
        layout.get("button_layout_score", 0.0) >= 0.58
        and 2 <= len(ui_elements) <= 8
        and not _has_long_dialogue_line(lines)
    ):
        return _classified(
            OCR_CAPTURE_PROFILE_STAGE_MENU,
            0.46 + min(layout.get("button_layout_score", 0.0), 0.15),
            lines=lines,
            ui_elements=ui_elements,
            debug=debug,
            reason="visual_button_layout",
        )
    if layout.get("dialogue_layout_score", 0.0) >= 0.58:
        return _classified(
            OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
            0.45 + min(layout.get("dialogue_layout_score", 0.0), 0.12),
            lines=lines,
            ui_elements=ui_elements,
            debug=debug,
            reason="visual_dialogue_layout",
        )
    return None


def _classified(
    screen_type: str,
    confidence: float,
    *,
    lines: list[str],
    ui_elements: list[dict[str, Any]],
    debug: dict[str, Any],
    reason: str,
) -> ScreenClassification:
    result_debug = dict(debug)
    result_debug["reason"] = reason
    return ScreenClassification(
        screen_type=screen_type,
        confidence=_confidence(confidence),
        ui_elements=ui_elements,
        raw_ocr_text=_bounded_raw_text(lines),
        debug=result_debug,
    )

