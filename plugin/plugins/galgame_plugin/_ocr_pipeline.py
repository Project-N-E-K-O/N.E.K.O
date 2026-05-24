from __future__ import annotations

import re
from typing import Any, Iterable

from .models import sanitize_screen_ui_elements
from .ocr_chrome_noise import (
    TEMPERATURE_STATUS_BOTTOM_MIN_RATIO,
    TEMPERATURE_STATUS_LEFT_MAX_RATIO,
    WINDOW_TITLE_TOP_MAX_RATIO,
    looks_like_temperature_status_line,
    looks_like_window_title_line,
)

from ._layout import _normalized_bounds
from ._ocr_utils import (
    _BRACKET_SPEAKER_RE,
    _DIALOGUE_COLON_RE,
    _SPEAKER_QUOTE_RE,
    _bounded_raw_text,
    _confidence,
    _dedupe_preserve_order,
    _float,
    _visible_len,
)


SCREEN_UI_ELEMENT_LIMIT = 10
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _coerce_ocr_regions(
    ocr_text: str,
    *,
    boxes: Iterable[Any] | None,
    bounds_metadata: dict[str, Any] | None,
    ocr_regions: Iterable[dict[str, Any]] | None,
) -> list[_OcrRegion]:
    from .screen_classifier import _OcrRegion
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


def _filter_chrome_noise_ui_elements(
    elements: list[dict[str, Any]],
    *,
    window_title: str,
) -> tuple[list[dict[str, Any]], int]:
    filtered: list[dict[str, Any]] = []
    removed = 0
    for element in elements:
        text = _clean_line(str(element.get("text") or ""))
        bounds = element.get("normalized_bounds")
        if not isinstance(bounds, dict):
            filtered.append(element)
            continue
        try:
            top = float(bounds.get("top"))
            bottom = float(bounds.get("bottom"))
            left = float(bounds.get("left"))
        except (TypeError, ValueError):
            filtered.append(element)
            continue
        if top <= WINDOW_TITLE_TOP_MAX_RATIO and looks_like_window_title_line(text, window_title):
            removed += 1
            continue
        if (
            bottom >= TEMPERATURE_STATUS_BOTTOM_MIN_RATIO
            and left <= TEMPERATURE_STATUS_LEFT_MAX_RATIO
            and looks_like_temperature_status_line(text)
        ):
            removed += 1
            continue
        filtered.append(element)
    return filtered, removed


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
