from __future__ import annotations

import re

from .models import (
    MENU_PREFIX_RE as _MENU_PREFIX_RE,
    OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
    OCR_CAPTURE_PROFILE_STAGE_MENU,
)
from .reader import normalize_text

AIHONG_PROCESS_NAMES = frozenset({"thelamentinggeese.exe"})
AIHONG_TITLE_SUBSTRINGS = ("哀鸿", "aihong")
AIHONG_DIALOGUE_CAPTURE_PROFILE_PRESET = {
    "left_inset_ratio": 0.0,
    "right_inset_ratio": 0.0,
    "top_ratio": 0.60,
    "bottom_inset_ratio": 0.05,
}
AIHONG_MENU_CAPTURE_PROFILE_PRESET = {
    "left_inset_ratio": 0.0,
    "right_inset_ratio": 0.0,
    "top_ratio": 0.0,
    "bottom_inset_ratio": 0.0,
}
AIHONG_DIALOGUE_STAGE = OCR_CAPTURE_PROFILE_STAGE_DIALOGUE
AIHONG_MENU_STAGE = OCR_CAPTURE_PROFILE_STAGE_MENU
AIHONG_MENU_MAX_LINES = 4
AIHONG_MENU_MIN_SIGNIFICANT_CHARS = 2
AIHONG_MENU_MAX_SIGNIFICANT_CHARS = 10
AIHONG_MENU_STATUS_KEYWORDS = ("银两剩余", "余额", "剩余")
AIHONG_MENU_DIALOGUE_MARKERS = (
    ",",
    ".",
    ":",
    ";",
    "?",
    "!",
    "[",
    "]",
    "，",
    "。",
    "：",
    "；",
    "？",
    "！",
    "「",
    "」",
    "【",
    "】",
)

_AIHONG_MENU_AMOUNT_RE = re.compile(r"^\s*\d+\s*两\S{0,3}\s*$")
_DIALOGUE_LINE_MARKERS = (":", "：", "「", "」")
_CHINESE_DIALOGUE_INDICATOR_CHARS = frozenset(
    "我你他她它您俺咱们吗呢吧啊呀啦哦哈嗯哎唉喂嘿啥咋谁哪怎这那"
)
_AIHONG_PLAIN_CHOICE_PRONOUN_BYPASS_TOKENS = ("钱", "银", "两", "买", "卖", "付", "赏")


def significant_char_count(text: str) -> int:
    return sum(1 for ch in str(text or "") if not ch.isspace())


def stripped_ocr_lines(raw_text: str) -> list[str]:
    return [line.strip() for line in str(raw_text or "").splitlines() if line.strip()]


def matches_aihong_target(*, process_name: str, normalized_title: str) -> bool:
    if str(process_name or "").strip().lower() in AIHONG_PROCESS_NAMES:
        return True
    title = str(normalized_title or "").strip().lower()
    return any(token in title for token in AIHONG_TITLE_SUBSTRINGS)


def looks_like_dialogue_line(text: str) -> bool:
    normalized = normalize_text(text).strip()
    if not normalized:
        return False
    return any(marker in normalized for marker in _DIALOGUE_LINE_MARKERS)


def looks_like_chinese_dialogue(text: str) -> bool:
    normalized = normalize_text(text).strip()
    if not normalized:
        return False
    return any(ch in normalized for ch in _CHINESE_DIALOGUE_INDICATOR_CHARS)


def looks_like_aihong_dialogue_text(text: str) -> bool:
    normalized = normalize_text(text).strip()
    if not normalized:
        return False
    return any(marker in normalized for marker in AIHONG_MENU_DIALOGUE_MARKERS)


def looks_like_aihong_menu_status_line(text: str) -> bool:
    normalized = normalize_text(str(text or "")).replace("\n", " ").strip()
    if not normalized:
        return False
    if any(keyword in normalized for keyword in AIHONG_MENU_STATUS_KEYWORDS):
        return True
    return bool(_AIHONG_MENU_AMOUNT_RE.match(normalized))


def looks_like_aihong_menu_status_only_text(raw_text: str) -> bool:
    lines = stripped_ocr_lines(raw_text)
    if not lines:
        return False
    return all(looks_like_aihong_menu_status_line(line) for line in lines)


def _plain_choice_allows_dialogue_pronoun(text: str) -> bool:
    normalized = normalize_text(str(text or "")).strip()
    return any(token in normalized for token in _AIHONG_PLAIN_CHOICE_PRONOUN_BYPASS_TOKENS)


def normalize_aihong_choice_box_text(text: str) -> str:
    normalized = normalize_text(str(text or "")).replace("\n", " ").strip()
    if not normalized or looks_like_aihong_menu_status_line(normalized):
        return ""
    if normalized.endswith("手") and significant_char_count(normalized) > AIHONG_MENU_MIN_SIGNIFICANT_CHARS:
        normalized = normalized[:-1].strip()
    return normalized


def levenshtein_distance(a: str, b: str) -> int:
    if len(a) < len(b):
        return levenshtein_distance(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (0 if ca == cb else 1)))
        prev = curr
    return prev[-1]


def _coerce_prefixed_choice_lines(lines: list[str]) -> list[str]:
    if len(lines) < 2:
        return []
    choices: list[str] = []
    for line in lines:
        match = _MENU_PREFIX_RE.match(line)
        if match is None:
            return []
        text = match.group(1).strip()
        if not text:
            return []
        choices.append(text)
    return choices


def _coerce_plain_choice_lines(lines: list[str]) -> list[str]:
    if not 2 <= len(lines) <= AIHONG_MENU_MAX_LINES:
        return []
    choices: list[str] = []
    seen: set[str] = set()
    for line in lines:
        text = normalize_text(str(line or "")).replace("\n", " ").strip()
        if not text or looks_like_dialogue_line(text):
            return []
        if looks_like_chinese_dialogue(text) and not _plain_choice_allows_dialogue_pronoun(text):
            return []
        if significant_char_count(text) > AIHONG_MENU_MAX_SIGNIFICANT_CHARS:
            return []
        if text in seen:
            continue
        seen.add(text)
        choices.append(text)
    if not 2 <= len(choices) <= AIHONG_MENU_MAX_LINES:
        return []
    return choices


def _coerce_choice_lines(lines: list[str], *, allow_plain_text: bool = False) -> list[str]:
    choices = _coerce_prefixed_choice_lines(lines)
    if choices:
        return choices
    if allow_plain_text:
        return _coerce_plain_choice_lines(lines)
    return []


def coerce_aihong_menu_choices(lines: list[str], *, allow_plain_text: bool = True) -> list[str]:
    filtered_lines: list[str] = []
    for line in lines:
        text = normalize_text(str(line or "")).replace("\n", " ").strip()
        if not text:
            continue
        if looks_like_aihong_menu_status_line(text):
            continue
        if text.endswith("手") and significant_char_count(text) > AIHONG_MENU_MIN_SIGNIFICANT_CHARS:
            text = text[:-1].strip()
        filtered_lines.append(text)
    choices = _coerce_choice_lines(filtered_lines, allow_plain_text=allow_plain_text)
    if not 2 <= len(choices) <= AIHONG_MENU_MAX_LINES:
        return []
    normalized_choices: list[str] = []
    for choice in choices:
        text = normalize_text(str(choice or "")).replace("\n", " ").strip()
        if not text or looks_like_aihong_dialogue_text(text):
            return []
        significant_chars = significant_char_count(text)
        if not AIHONG_MENU_MIN_SIGNIFICANT_CHARS <= significant_chars <= AIHONG_MENU_MAX_SIGNIFICANT_CHARS:
            return []
        normalized_choices.append(text)
    return normalized_choices
