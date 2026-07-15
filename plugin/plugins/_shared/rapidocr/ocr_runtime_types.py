from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import Any


_LOGGER = logging.getLogger(__name__)

_RAPIDOCR_RUNTIME_IDLE_TTL_SECONDS = 300.0
_RAPIDOCR_RUNTIME_CACHE_LOCK = threading.RLock()
_RAPIDOCR_RUNTIME_CACHE: dict[tuple[str, str, str, str, str, str], tuple[Any, float]] = {}
_RAPIDOCR_RUNTIME_CACHE_OWNERS: dict[tuple[str, str, str, str, str, str], int] = {}
_RAPIDOCR_INFERENCE_LOCK = threading.Lock()

_OCR_PREPARE_UPSCALE_SOURCE_LONG_EDGE = 900
_OCR_PREPARE_TARGET_LONG_EDGE = 1400
_OCR_PREPARE_MAX_LONG_EDGE = 1600

_CJK_CHAR_RE = re.compile(r"[\u3400-\u9fff]")
_KANA_CHAR_RE = re.compile(r"[\u3040-\u30ff]")
_HANGUL_RE = re.compile(r"[\uac00-\ud7af]")
_SPACE_RE = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\u3000", " ")).strip()


def _significant_char_count(text: str) -> int:
    return sum(1 for ch in str(text or "") if ch.isalnum() or _CJK_CHAR_RE.match(ch) or _KANA_CHAR_RE.match(ch) or _HANGUL_RE.match(ch))


def _score_ocr_text(text: str) -> int:
    return _significant_char_count(normalize_text(text))


def _should_insert_ascii_space(previous_text: str, next_text: str) -> bool:
    if not previous_text or not next_text:
        return False
    left = previous_text[-1]
    right = next_text[0]
    return left.isascii() and right.isascii() and left.isalnum() and right.isalnum()


def _join_ocr_segments(parts: list[str]) -> str:
    rendered = ""
    for part in parts:
        normalized = normalize_text(str(part or "")).replace("\n", " ").strip()
        if not normalized:
            continue
        if not rendered:
            rendered = normalized
            continue
        if _should_insert_ascii_space(rendered, normalized):
            rendered += " "
        rendered += normalized
    return rendered


def _ocr_score_weight(text: str) -> int:
    return max(_significant_char_count(text), 1)


def _weighted_ocr_score(scores: Any) -> float:
    total_weight = 0
    weighted_sum = 0.0
    for score, weight in scores:
        normalized_weight = max(int(weight or 0), 1)
        weighted_sum += float(score) * normalized_weight
        total_weight += normalized_weight
    if total_weight <= 0:
        return 0.0
    return max(0.0, min(1.0, weighted_sum / total_weight))


def _rapidocr_runtime_cache_key(
    *,
    install_target_dir_raw: str,
    engine_type: str,
    lang_type: str,
    model_type: str,
    ocr_version: str,
    plugin_id: str,
) -> tuple[str, str, str, str, str, str]:
    return (
        str(plugin_id or "").strip().lower(),
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
    key: tuple[str, str, str, str, str, str],
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
    key: tuple[str, str, str, str, str, str],
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
    key: tuple[str, str, str, str, str, str],
) -> None:
    with _RAPIDOCR_RUNTIME_CACHE_LOCK:
        _RAPIDOCR_RUNTIME_CACHE_OWNERS[key] = (
            _RAPIDOCR_RUNTIME_CACHE_OWNERS.get(key, 0) + 1
        )


def _release_rapidocr_runtime_cache(
    key: tuple[str, str, str, str, str, str],
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
        _LOGGER.debug("rapidocr discarded %d low-confidence token(s)", low_confidence_count)
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
    text = "\n".join(line for line in rendered_lines if line)
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


__all__ = [
    "OcrTextBox",
    "_CJK_CHAR_RE",
    "_KANA_CHAR_RE",
    "_LOCAL_RAPIDOCR_INFERENCE_LOCK",
    "_OCR_PREPARE_MAX_LONG_EDGE",
    "_OCR_PREPARE_TARGET_LONG_EDGE",
    "_OCR_PREPARE_UPSCALE_SOURCE_LONG_EDGE",
    "_RAPIDOCR_INFERENCE_LOCK",
    "_RAPIDOCR_RUNTIME_CACHE_LOCK",
    "_RAPIDOCR_RUNTIME_IDLE_TTL_SECONDS",
    "_RapidOcrToken",
    "_acquire_rapidocr_runtime_cache",
    "_get_rapidocr_runtime_cache",
    "_join_ocr_segments",
    "_prepare_ocr_image",
    "_release_rapidocr_runtime_cache",
    "_rapidocr_lines_from_output",
    "_rapidocr_points",
    "_rapidocr_runtime_cache_key",
    "_rapidocr_text_from_output",
    "_rapidocr_tokens_from_output",
    "_score_ocr_text",
    "_should_insert_ascii_space",
    "_significant_char_count",
    "_store_rapidocr_runtime_cache",
]

_LOCAL_RAPIDOCR_INFERENCE_LOCK = _RAPIDOCR_INFERENCE_LOCK
