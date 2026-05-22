from __future__ import annotations

import re
from typing import Any, Iterable


_RAW_OCR_TEXT_LIMIT = 20
_RAW_OCR_LINE_MAX_CHARS = 120
_DIALOGUE_COLON_RE = re.compile(r"^[^:：]{1,40}[:：]\s*.+\S$")
_SPEAKER_QUOTE_RE = re.compile(r"^[^「」『』:：]{1,40}[「『].+[」』]$")
_BRACKET_SPEAKER_RE = re.compile(r"^[【\[][^\]】]{1,40}[\]】]\s*.+\S$")


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
    from ._ocr_pipeline import _normalize_for_match
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
