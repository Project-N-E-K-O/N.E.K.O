from __future__ import annotations

import re

ASCII_ID_RE = re.compile(r"[a-z0-9]+")
TEMPERATURE_STATUS_RE = re.compile(
    r"^\s*[+-]?\d{1,2}\s*(?:\N{DEGREE SIGN}\s*[cC\N{DEGREE CELSIUS}]?|\N{DEGREE CELSIUS})\s*$"
)

WINDOW_TITLE_TOP_MAX_RATIO = 0.06
TEMPERATURE_STATUS_BOTTOM_MIN_RATIO = 0.95
TEMPERATURE_STATUS_LEFT_MAX_RATIO = 0.20


def compact_ascii_id(value: str) -> str:
    return "".join(ASCII_ID_RE.findall(str(value or "").casefold()))


def looks_like_window_title_line(line: str, window_title: str) -> bool:
    title_key = compact_ascii_id(window_title)
    if len(title_key) < 4:
        return False
    line_key = compact_ascii_id(line)
    if not line_key:
        return False
    if line_key == title_key:
        return True
    return title_key.startswith(line_key) and len(title_key) <= len(line_key) + 3


def looks_like_temperature_status_line(line: str) -> bool:
    return bool(TEMPERATURE_STATUS_RE.match(str(line or "")))
