from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

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


SCREEN_UI_ELEMENT_LIMIT = 10
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

_TITLE_KEYWORDS = (
    "start",
    "new game",
    "newgame",
    "continue",
    "load game",
    "extra",
    "gallery",
    "start game",
    "开始",
    "開始",
    "新游戏",
    "新遊戲",
    "继续",
    "繼續",
    "载入",
    "載入",
    "はじめから",
    "つづきから",
    "スタート",
    "ロード",
    "タイトル",
    "コンティニュー",
)
_TITLE_EXIT_KEYWORDS = ("quit", "exit", "结束", "終了", "退出")
_SAVE_LOAD_KEYWORDS = (
    "save",
    "load",
    "quick save",
    "quick load",
    "autosave",
    "auto save",
    "slot",
    "page",
    "保存",
    "存档",
    "存檔",
    "读档",
    "讀檔",
    "载入",
    "載入",
    "セーブ",
    "ロード",
    "スロット",
    "ページ",
)
_CONFIG_KEYWORDS = (
    "config",
    "configuration",
    "settings",
    "setting",
    "volume",
    "sound",
    "voice",
    "bgm",
    "se",
    "text speed",
    "message speed",
    "window mode",
    "fullscreen",
    "设置",
    "設定",
    "音量",
    "声音",
    "語音",
    "语音",
    "文字速度",
    "对话速度",
    "對話速度",
    "全屏",
    "画面",
    "畫面",
    "コンフィグ",
    "設定",
    "オプション",
    "音量",
    "ボイス",
    "メッセージ",
)
_BACK_KEYWORDS = ("back", "return", "close", "戻る", "返回", "取消")
_BACKLOG_KEYWORDS = (
    "backlog",
    "message log",
    "dialogue log",
    "dialog log",
    "text log",
    "history",
    "履歴",
    "会話履歴",
    "バックログ",
    "ログ",
    "历史",
    "歷史",
    "历史记录",
    "歷史記錄",
    "对话历史",
    "對話歷史",
    "对白历史",
    "對白歷史",
    "文本历史",
    "文本歷史",
    "讯息记录",
    "訊息記錄",
)
_GALLERY_KEYWORDS = (
    "gallery",
    "cg mode",
    "cg",
    "scene replay",
    "replay",
    "album",
    "memories",
    "extra",
    "回想",
    "鉴赏",
    "鑑賞",
    "画廊",
    "圖鑑",
    "图鉴",
    "相册",
    "ギャラリー",
    "シーン回想",
    "鑑賞モード",
)
_MINIGAME_KEYWORDS = (
    "minigame",
    "mini game",
    "score",
    "combo",
    "time limit",
    "remaining time",
    "hp",
    "操作説明",
    "スコア",
    "コンボ",
    "残り時間",
    "小游戏",
    "小遊戲",
    "得分",
    "连击",
    "連擊",
    "剩余时间",
    "剩餘時間",
)
_GAME_OVER_KEYWORDS = (
    "game over",
    "bad end",
    "dead end",
    "retry",
    "try again",
    "continue?",
    "return to title",
    "back to title",
    "游戏结束",
    "遊戲結束",
    "重试",
    "重試",
    "再试一次",
    "再試一次",
    "返回标题",
    "返回標題",
    "タイトルへ",
    "リトライ",
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
    visual = dict(visual_features or {})
    layout = _layout_features(ui_elements)
    debug: dict[str, Any] = {
        "sources": [region.source for region in regions if region.source],
        "line_count": len(lines),
        "ui_element_count": len(ui_elements),
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


def _coerce_ocr_regions(
    ocr_text: str,
    *,
    boxes: Iterable[Any] | None,
    bounds_metadata: dict[str, Any] | None,
    ocr_regions: Iterable[dict[str, Any]] | None,
) -> list[_OcrRegion]:
    regions: list[_OcrRegion] = []
    default_metadata = dict(bounds_metadata or {})
    source = str(default_metadata.get("text_source") or "bottom_region")
    regions.append(
        _OcrRegion(
            source=source,
            text=str(ocr_text or ""),
            boxes=list(boxes or []),
            bounds_metadata=default_metadata,
        )
    )
    for item in list(ocr_regions or []):
        if not isinstance(item, dict):
            continue
        metadata = dict(item.get("bounds_metadata") or {})
        source = str(item.get("source") or metadata.get("text_source") or "").strip()
        if not source:
            source = f"region_{len(regions)}"
        metadata.setdefault("text_source", source)
        regions.append(
            _OcrRegion(
                source=source,
                text=str(item.get("text") or ""),
                boxes=list(item.get("boxes") or []),
                bounds_metadata=metadata,
            )
        )
    return regions


def _merged_ocr_lines(regions: list[_OcrRegion]) -> list[str]:
    lines: list[str] = []
    for region in regions:
        lines.extend(_ocr_lines(region.text, boxes=region.boxes))
    return _dedupe_preserve_order(lines)


def _merged_screen_ui_elements(
    regions: list[_OcrRegion],
    *,
    lines: list[str],
) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    seen: set[tuple[str, float, float, float, float, str]] = set()
    for region in regions:
        region_lines = _ocr_lines(region.text, boxes=region.boxes)
        for element in _screen_ui_elements(
            region_lines,
            boxes=region.boxes,
            bounds_metadata=region.bounds_metadata,
            source=region.source,
        ):
            bounds = dict(element.get("bounds") or {})
            key = (
                _normalize_for_match(str(element.get("text") or "")),
                float(bounds.get("left", 0.0)),
                float(bounds.get("top", 0.0)),
                float(bounds.get("right", 0.0)),
                float(bounds.get("bottom", 0.0)),
                str(element.get("text_source") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            elements.append(element)
            if len(elements) >= SCREEN_UI_ELEMENT_LIMIT:
                return sanitize_screen_ui_elements(elements, limit=SCREEN_UI_ELEMENT_LIMIT)
    if elements:
        return sanitize_screen_ui_elements(elements, limit=SCREEN_UI_ELEMENT_LIMIT)
    return sanitize_screen_ui_elements(
        [{"element_id": f"ocr-ui-line-{index}", "text": line, "role": "text"} for index, line in enumerate(lines)],
        limit=SCREEN_UI_ELEMENT_LIMIT,
    )


def _ocr_lines(ocr_text: str, *, boxes: Iterable[Any] | None) -> list[str]:
    lines = [_clean_line(line) for line in str(ocr_text or "").splitlines()]
    lines = [line for line in lines if line]
    if lines:
        return _dedupe_preserve_order(lines)
    box_lines = [_clean_line(_box_text(box)) for box in list(boxes or [])]
    return _dedupe_preserve_order(line for line in box_lines if line)


def _screen_ui_elements(
    lines: list[str],
    *,
    boxes: Iterable[Any] | None,
    bounds_metadata: dict[str, Any] | None,
    source: str = "bottom_region",
) -> list[dict[str, Any]]:
    metadata = dict(bounds_metadata or {})
    metadata.setdefault("text_source", source)
    elements: list[dict[str, Any]] = []
    seen: set[tuple[str, float, float, float, float]] = set()
    for index, box in enumerate(list(boxes or [])):
        text = _clean_line(_box_text(box))
        if not text:
            continue
        bounds = _box_bounds(box)
        key = (
            _normalize_for_match(text),
            float(bounds.get("left", 0.0)),
            float(bounds.get("top", 0.0)),
            float(bounds.get("right", 0.0)),
            float(bounds.get("bottom", 0.0)),
        )
        if key in seen:
            continue
        seen.add(key)
        element: dict[str, Any] = {
            "element_id": f"ocr-ui-{source}-{index}",
            "text": text,
            "role": "text",
            "text_source": source,
        }
        if bounds:
            element["bounds"] = bounds
            normalized_bounds = _normalized_bounds(bounds, metadata)
            if normalized_bounds:
                element["normalized_bounds"] = normalized_bounds
            for meta_key in (
                "bounds_coordinate_space",
                "source_size",
                "capture_rect",
                "window_rect",
            ):
                value = metadata.get(meta_key)
                if value:
                    element[meta_key] = dict(value) if isinstance(value, dict) else value
        elements.append(element)
        if len(elements) >= SCREEN_UI_ELEMENT_LIMIT:
            break
    if not elements:
        for index, line in enumerate(lines[:SCREEN_UI_ELEMENT_LIMIT]):
            elements.append(
                {
                    "element_id": f"ocr-ui-line-{source}-{index}",
                    "text": line,
                    "role": "text",
                    "text_source": source,
                }
            )
    return sanitize_screen_ui_elements(elements, limit=SCREEN_UI_ELEMENT_LIMIT)


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
            min_overlap = float(item.get("min_overlap") or 0.35)
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
            if overlap_area / element_area >= float(region.get("min_overlap") or 0.35):
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
            float(bounds["left"]) / width,
            float(bounds["right"]) / width,
        )
        top, bottom = _nondegenerate_unit_interval(
            float(bounds["top"]) / height,
            float(bounds["bottom"]) / height,
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
        (capture_rect["left"] + float(bounds["left"]) - window_rect["left"]) / window_width,
        (capture_rect["left"] + float(bounds["right"]) - window_rect["left"]) / window_width,
    )
    top, bottom = _nondegenerate_unit_interval(
        (capture_rect["top"] + float(bounds["top"]) - window_rect["top"]) / window_height,
        (capture_rect["top"] + float(bounds["bottom"]) - window_rect["top"]) / window_height,
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


def _box_text(box: Any) -> str:
    if isinstance(box, dict):
        return str(box.get("text") or "")
    return str(getattr(box, "text", "") or "")


def _box_bounds(box: Any) -> dict[str, float]:
    raw = box if isinstance(box, dict) else {
        "left": getattr(box, "left", None),
        "top": getattr(box, "top", None),
        "right": getattr(box, "right", None),
        "bottom": getattr(box, "bottom", None),
    }
    try:
        bounds = {
            "left": float(raw.get("left")),  # type: ignore[union-attr,arg-type]
            "top": float(raw.get("top")),  # type: ignore[union-attr,arg-type]
            "right": float(raw.get("right")),  # type: ignore[union-attr,arg-type]
            "bottom": float(raw.get("bottom")),  # type: ignore[union-attr,arg-type]
        }
    except (AttributeError, TypeError, ValueError):
        return {}
    if bounds["right"] <= bounds["left"] or bounds["bottom"] <= bounds["top"]:
        return {}
    return bounds


def _clean_line(value: object) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_RE.sub(" ", text)
    return " ".join(text.strip().split())


def _normalize_for_match(value: str) -> str:
    text = _clean_line(value).casefold()
    return re.sub(r"\s+", " ", text)


def _keyword_hits(lines: list[str], keywords: Iterable[str]) -> int:
    hits = 0
    for line in lines:
        for keyword in keywords:
            if keyword.casefold() in line:
                hits += 1
                break
    return hits


def _looks_like_save_load(
    save_hits: int,
    config_hits: int,
    title_hits: int,
    normalized_lines: list[str],
    joined: str,
) -> bool:
    if save_hits >= 3:
        return True
    if save_hits >= 2 and title_hits < 2 and config_hits < 2:
        return True
    if save_hits >= 1 and title_hits == 0 and any(token in joined for token in ("slot", "page", "スロット", "ページ")):
        return True
    if save_hits >= 1 and title_hits == 0 and any("slot" in line or "存档" in line or "存檔" in line for line in normalized_lines):
        return True
    return False


def _looks_like_config(
    config_hits: int,
    save_hits: int,
    title_hits: int,
    normalized_lines: list[str],
) -> bool:
    if config_hits >= 4:
        return True
    if config_hits >= 3 and save_hits == 0 and title_hits == 0:
        return True
    if config_hits >= 1 and any(
        token in line
        for line in normalized_lines
        for token in ("volume", "音量", "text speed", "文字速度", "fullscreen", "全屏")
    ):
        return True
    return False


def _looks_like_game_over(
    game_over_hits: int,
    title_hits: int,
    normalized_lines: list[str],
    joined: str,
) -> bool:
    del normalized_lines
    if "game over" in joined or "bad end" in joined or "dead end" in joined:
        return True
    if game_over_hits >= 2:
        return True
    if game_over_hits >= 1 and title_hits <= 1 and any(
        token in joined
        for token in ("retry", "try again", "return to title", "游戏结束", "遊戲結束", "リトライ")
    ):
        return True
    return False


def _is_backlog_label(normalized_line: str) -> bool:
    compact = re.sub(
        r"[\s\-_.,，。:：;；!！?？/\\|()\[\]【】「」『』]+",
        "",
        str(normalized_line or "").casefold(),
    )
    if not compact:
        return False
    return compact in {
        "backlog",
        "history",
        "messagelog",
        "dialoguelog",
        "dialoglog",
        "textlog",
        "履歴",
        "会話履歴",
        "バックログ",
        "ログ",
        "历史",
        "歷史",
        "历史记录",
        "歷史記錄",
        "对话历史",
        "對話歷史",
        "对白历史",
        "對白歷史",
        "文本历史",
        "文本歷史",
        "讯息记录",
        "訊息記錄",
    }


def _looks_like_backlog(
    backlog_hits: int,
    lines: list[str],
    normalized_lines: list[str],
) -> bool:
    if backlog_hits <= 0:
        return False
    if any(_is_backlog_label(line) for line in normalized_lines):
        return True
    dialogue_like_count = sum(
        1
        for line in lines
        if _DIALOGUE_COLON_RE.match(line)
        or _SPEAKER_QUOTE_RE.match(line)
        or _BRACKET_SPEAKER_RE.match(line)
    )
    if backlog_hits >= 2 and len(lines) >= 2:
        return True
    return backlog_hits >= 1 and len(lines) >= 4 and dialogue_like_count >= 2


def _dialogue_list_signal(lines: list[str]) -> tuple[int, int]:
    dialogue_like_count = 0
    speakers: set[str] = set()
    for line in lines:
        text = str(line or "")
        speaker = ""
        colon_match = _DIALOGUE_COLON_RE.match(text)
        if colon_match:
            speaker = re.split(r"[:：]", text, maxsplit=1)[0].strip()
        elif _SPEAKER_QUOTE_RE.match(text):
            speaker = re.split(r"[「『]", text, maxsplit=1)[0].strip()
        else:
            bracket_match = re.match(r"^[【\[]([^\]】]{1,40})[\]】]", text)
            if bracket_match:
                speaker = bracket_match.group(1).strip()
        if speaker:
            dialogue_like_count += 1
            speakers.add(_normalize_for_match(speaker))
    return dialogue_like_count, len({speaker for speaker in speakers if speaker})


def _looks_like_backlog_dialogue_list(
    lines: list[str],
    *,
    layout: dict[str, float],
) -> bool:
    if len(lines) < 4:
        return False
    dialogue_like_count, distinct_speaker_count = _dialogue_list_signal(lines)
    if dialogue_like_count < 3:
        return False
    if layout.get("dialogue_layout_score", 0.0) >= 0.58:
        return False
    if layout.get("backlog_list_score", 0.0) >= 0.58:
        return True
    return len(lines) >= 5 and dialogue_like_count >= 4 and distinct_speaker_count >= 2


def _looks_like_gallery(
    gallery_hits: int,
    title_hits: int,
    normalized_lines: list[str],
    joined: str,
) -> bool:
    del normalized_lines
    if gallery_hits >= 3:
        return True
    if gallery_hits >= 2 and title_hits <= 2:
        return True
    if gallery_hits >= 1 and any(
        token in joined
        for token in ("scene replay", "cg mode", "シーン回想", "鑑賞モード", "回想", "鉴赏", "鑑賞")
    ):
        return True
    return False


def _looks_like_minigame(
    minigame_hits: int,
    normalized_lines: list[str],
    joined: str,
) -> bool:
    if "minigame" in joined or "mini game" in joined or "小游戏" in joined or "小遊戲" in joined:
        return True
    if minigame_hits >= 3:
        return True
    if minigame_hits >= 2 and any(
        token in line
        for line in normalized_lines
        for token in ("score", "combo", "time", "スコア", "コンボ", "得分", "连击", "連擊")
    ):
        return True
    return False


def _looks_like_title(
    title_hits: int,
    save_hits: int,
    config_hits: int,
    short_line_count: int,
    normalized_lines: list[str],
) -> bool:
    if title_hits >= 3 and short_line_count >= 2:
        return True
    if title_hits >= 2 and short_line_count >= 2 and max(save_hits, config_hits) <= 2:
        return True
    if title_hits >= 1 and len(normalized_lines) <= 6 and any(
        token in " ".join(normalized_lines)
        for token in ("new game", "newgame", "はじめから", "开始", "開始", "新游戏")
    ):
        return True
    return False


def _looks_like_dialogue(lines: list[str], joined: str, *, layout: dict[str, float]) -> bool:
    if any(_DIALOGUE_COLON_RE.match(line) for line in lines):
        return True
    if any(_SPEAKER_QUOTE_RE.match(line) for line in lines):
        return True
    if any(_BRACKET_SPEAKER_RE.match(line) for line in lines):
        return True
    if len(lines) <= 3 and _visible_len(joined) >= 12:
        return True
    return layout.get("dialogue_layout_score", 0.0) >= 0.58 and _has_long_dialogue_line(lines)


def _has_long_dialogue_line(lines: list[str]) -> bool:
    return any(_visible_len(line) > 18 for line in lines)


def _cluster_count(values: list[float], *, tolerance: float) -> int:
    if not values:
        return 0
    clusters: list[float] = []
    for value in sorted(values):
        if not clusters or abs(value - clusters[-1]) > tolerance:
            clusters.append(value)
        else:
            clusters[-1] = (clusters[-1] + value) / 2.0
    return len(clusters)


def _visible_len(value: str) -> int:
    return sum(1 for ch in str(value or "") if not ch.isspace())


def _bounded_raw_text(lines: list[str]) -> list[str]:
    bounded: list[str] = []
    for line in lines[:_RAW_OCR_TEXT_LIMIT]:
        if len(line) > _RAW_OCR_LINE_MAX_CHARS:
            bounded.append(line[:_RAW_OCR_LINE_MAX_CHARS])
        else:
            bounded.append(line)
    return bounded


def _dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = _normalize_for_match(value)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(str(value))
    return result


def _bounded_debug_value(value: dict[str, Any]) -> dict[str, Any]:
    bounded: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, (str, int, float, bool)) or item is None:
            bounded[str(key)] = item
        elif isinstance(item, list):
            bounded[str(key)] = item[:12]
        elif isinstance(item, dict):
            bounded[str(key)] = {
                str(inner_key): inner_value
                for inner_key, inner_value in list(item.items())[:12]
                if isinstance(inner_value, (str, int, float, bool)) or inner_value is None
            }
    return bounded


def _float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _confidence(value: float) -> float:
    return round(max(0.0, min(float(value or 0.0), 0.99)), 2)
