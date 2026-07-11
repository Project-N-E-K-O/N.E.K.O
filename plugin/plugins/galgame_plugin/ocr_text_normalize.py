from __future__ import annotations

import re
import time
from typing import Any, Iterable

from .aihong_state import (
    AIHONG_MENU_MAX_LINES as _AIHONG_MENU_MAX_LINES,
    AIHONG_MENU_MAX_SIGNIFICANT_CHARS as _AIHONG_MENU_MAX_SIGNIFICANT_CHARS,
)
from .models import MENU_PREFIX_RE as _MENU_PREFIX_RE
from .ocr_chrome_noise import (
    looks_like_temperature_status_line as _looks_like_temperature_status_line,
    looks_like_window_title_line as _looks_like_window_title_line,
)
from .reader import normalize_text

__all__ = [
    "_ASCII_TOKEN_RE",
    "_AUTO_TARGET_DENY_PROCESS_NAMES",
    "_CJK_CHAR_RE",
    "_DIALOGUE_LINE_MARKERS",
    "_ENGLISH_GAME_OVERLAY_WORDS",
    "_GAME_OVERLAY_TEXT_GUARD_SUBSTRINGS",
    "_HANGUL_RE",
    "_HELPER_CLASS_NAMES",
    "_HIRAGANA_RE",
    "_JA_MARKER_WORDS",
    "_KANA_BUD_RE",
    "_KANA_CHAR_RE",
    "_KATAKANA_RE",
    "_NARRATION_PAREN_RE",
    "_NARRATION_QUOTE_RE",
    "_NON_ENGLISH_GAME_OVERLAY_SUBSTRINGS",
    "_OCR_DIALOGUE_STRONG_PUNCTUATION_RE",
    "_OCR_DIALOGUE_WEAK_PUNCTUATION_RE",
    "_OCR_STABILITY_IGNORED_CHARS_RE",
    "_OCR_TRAILING_GARBAGE_AFTER_BRACKET_RE",
    "_OCR_TRAILING_GARBAGE_AFTER_DASH_RE",
    "_OCR_TRAILING_GARBAGE_AFTER_SENTENCE_RE",
    "_OCR_TRAILING_ORPHAN_AFTER_SENTENCE_RE",
    "_OVERLAY_PROCESS_NAME_SUBSTRINGS",
    "_OVERLAY_WINDOW_TITLE_SUBSTRINGS",
    "_OcrLangDetector",
    "_PUNCTUATION_CONFUSION_FIXES",
    "_SELF_UI_GUARD_SUBSTRINGS",
    "_SELF_WINDOW_PATH_SUBSTRINGS",
    "_SELF_WINDOW_TITLE_SUBSTRINGS",
    "_SPEAKER_BRACKET_RE",
    "_SPEAKER_COLON_RE",
    "_SPEAKER_PAREN_PREFIX_RE",
    "_SPEAKER_PAREN_SUFFIX_RE",
    "_SPEAKER_QUOTE_RE",
    "_WINDOW_SPACE_RE",
    "_ZH_MARKER_WORDS",
    "_average_ocr_box_confidence",
    "_bounded_confidence_or_zero",
    "_canonical_choice_candidate_text",
    "_classify_cjk_text",
    "_clean_ocr_dialogue_text",
    "_coerce_choice_lines",
    "_coerce_plain_choice_lines",
    "_coerce_prefixed_choice_lines",
    "_drop_ocr_chrome_noise_lines",
    "_fix_ocr_punctuation_confusion",
    "_join_ocr_segments",
    "_looks_like_dialogue_line",
    "_looks_like_english_overlay_label",
    "_looks_like_game_overlay_normalized_text",
    "_looks_like_game_overlay_text",
    "_looks_like_noise_normalized_text",
    "_looks_like_noise_ocr_text",
    "_looks_like_non_english_overlay_label",
    "_looks_like_ocr_dialogue_normalized_text",
    "_looks_like_ocr_dialogue_text",
    "_looks_like_self_ui_text",
    "_looks_like_self_window_path",
    "_looks_like_self_window_title",
    "_normalize_window_title",
    "_ocr_score_weight",
    "_ocr_stability_key",
    "_ocr_stability_keys_match",
    "_prefer_ocr_stability_text",
    "_score_ocr_text",
    "_should_insert_ascii_space",
    "_significant_char_count",
    "_stripped_ocr_lines",
    "_weighted_ocr_score",
]

_SPEAKER_QUOTE_RE = re.compile(
    r"^\s*([^\u300c\u300d:\uff1a]{1,40})[\u300c\u300e](.{1,200})[\u300d\u300f]\s*$"
)
_SPEAKER_COLON_RE = re.compile(r"^\s*([^:\uff1a]{1,40})[:\uff1a]\s*(.{0,199}\S)\s*$")
_SPEAKER_BRACKET_RE = re.compile(
    r"^\s*[\u3010\[]([^\u3011\]]{1,40})[\u3011\]]\s*(.{0,199}\S)\s*$"
)
_SPEAKER_PAREN_SUFFIX_RE = re.compile(
    r"^\s*([^\uff08\uff09()]{1,40})[\uff08(](.{0,199}\S)[\uff09)]\s*$"
)
_SPEAKER_PAREN_PREFIX_RE = re.compile(
    r"^\s*[\uff08(]([^\uff09)]{1,40})[\uff09)]\s*(.{0,199}\S)\s*$"
)
_NARRATION_QUOTE_RE = re.compile(r"^\s*[\u300c\u300e\u201c\"](.{0,199}\S)[\u300d\u300f\u201d\"]\s*$")
_NARRATION_PAREN_RE = re.compile(r"^\s*[\uff08(]([^\uff09)]{1,40})[\uff09)]\s*$")
_CJK_CHAR_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_KANA_CHAR_RE = re.compile(r"[\u3040-\u30ff]")
_HANGUL_RE = re.compile(r"[\uac00-\ud7af\u1100-\u11ff\u3130-\u318f]")
_HIRAGANA_RE = re.compile(r"[\u3040-\u309f]")
_KATAKANA_RE = re.compile(r"[\u30a0-\u30ff\u31f0-\u31ff]")
_KANA_BUD_RE = re.compile(
    r"[\u3041\u3043\u3045\u3047\u3049\u3063\u3083\u3085\u3087"
    r"\u30a1\u30a3\u30a5\u30a7\u30a9\u30c3\u30e3\u30e5\u30e7]"
)
# Keep Japanese markers kana-only. Adding common kanji words would bias
# OCR-fragmented pure-kanji Japanese text and Chinese text in opposite ways;
# without kana/hangul, pure CJK remains a best-effort fallback to Chinese.
_JA_MARKER_WORDS = frozenset({
    "です",
    "ます",
    "した",
    "して",
    "いる",
    "ある",
    "ない",
    "こと",
    "もの",
    "よう",
    "そう",
    "これ",
    "それ",
    "どれ",
})
_ZH_MARKER_WORDS = frozenset({
    "的",
    "了",
    "是",
    "在",
    "我",
    "你",
    "他",
    "她",
    "它",
    "们",
    "这",
    "那",
    "有",
    "没",
    "很",
    "都",
    "要",
    "可以",
    "因为",
    "所以",
    "但是",
    "虽然",
    "而且",
    "什么",
    "怎么",
    "为什么",
    "这个",
    "那个",
    "哪个",
})
_ASCII_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_WINDOW_SPACE_RE = re.compile(r"\s+")
_SELF_WINDOW_TITLE_SUBSTRINGS = (
    "n.e.k.o",
    "plugin manager",
    "插件管理",
    "galgame plugin",
    "galgame play assistant",
    "galgame 游玩助手",
    "galgame 遊玩助手",
    "galgame プレイアシスタント",
    "galgame 플레이 도우미",
    "asistente para galgame",
    "assistente de galgame",
    "помощник для galgame",
    "phase 2",
)
_SELF_WINDOW_PATH_SUBSTRINGS = (
    "n.e.k.o",
    "galgame_plugin",
)
_OVERLAY_WINDOW_TITLE_SUBSTRINGS = (
    "nvidia overlay",
    "overlay",
    "launcher",
    "task manager",
    "visual studio code",
    "obs",
    "program manager",
    "settings",
    "microsoft text input application",
)
_OVERLAY_PROCESS_NAME_SUBSTRINGS = (
    "nvidia",
    "overlay",
    "launcher",
    "gamebar",
    "obs",
    "code",
    "steamwebhelper",
)
_AUTO_TARGET_DENY_PROCESS_NAMES = {
    "applicationframehost.exe",
    "chrome.exe",
    "cmd.exe",
    "code.exe",
    "explorer.exe",
    "firefox.exe",
    "msedge.exe",
    "notepad.exe",
    "powershell.exe",
    "razerappengine.exe",
    "windowsterminal.exe",
    "winword.exe",
    "wps.exe",
}
_HELPER_CLASS_NAMES = {
    "Shell_TrayWnd",
    "Windows.UI.Core.CoreWindow",
    "ApplicationFrameWindow",
    "RzMonitorForegroundWindowClass",
    "Windows.UI.Composition.DesktopWindowContentBridge",
}
_SELF_UI_GUARD_SUBSTRINGS = (
    ".agent",
    ".codex",
    ".codex_tmp",
    ".codex_pytest_tmp",
    "__pycache__",
    "-pycache_",
    "codex_tmp",
    "documents\\code\\n.e.k.o",
    "d:\\work\\code\\n.e.k.o",
    "rapidocr",
    "install queued task",
    "plugin manager",
    "galgame plugin",
    "galgame play assistant",
    "galgame 游玩助手",
    "galgame 遊玩助手",
    "galgame プレイアシスタント",
    "galgame 플레이 도우미",
    "asistente para galgame",
    "assistente de galgame",
    "помощник для galgame",
    "n.e.k.o",
    "插件设置",
    "运行控制",
    "模式静默",
    "静默进入待机",
    "进入待机",
    "恢复活跃",
    "推送通知",
    "推进速度",
    "保存设置",
    "ocr 目标窗口",
    "ocr目标窗口",
    "等待 ocr 窗口候选列表",
    "等待ocr窗口候选列表",
    "查看排除窗口",
    "选择识别窗口",
    "截图校准",
    "最近稳定台词",
    "stable 与 observed",
    "当前台词解释",
    "场景总结",
    "游戏 agent",
    "plugin.plugins.galgame_plugin",
    "uv run python",
    "launcher.py",
    "visual studio code",
    "code.exe",
    "windows terminal",
    "powershell",
    "ps c:",
)
_GAME_OVERLAY_TEXT_GUARD_SUBSTRINGS = (
    "backlog",
    "history",
    "skip",
    "auto",
    "quick",
    "fast",
    "forward",
    "config",
    "system",
    "load",
    "save",
    "menu",
    "回想",
    "历史",
    "履历",
    "快进",
    "跳过",
    "自动",
    "菜单",
    "设置",
    "系统",
    "存档",
    "读档",
)
_ENGLISH_GAME_OVERLAY_WORDS = frozenset(
    token for token in _GAME_OVERLAY_TEXT_GUARD_SUBSTRINGS if token.isascii()
)
_NON_ENGLISH_GAME_OVERLAY_SUBSTRINGS = tuple(
    token for token in _GAME_OVERLAY_TEXT_GUARD_SUBSTRINGS if not token.isascii()
)
_DIALOGUE_LINE_MARKERS = (":", "：", "「", "」")
_OCR_DIALOGUE_STRONG_PUNCTUATION_RE = re.compile(r"[。！？!?…]|——|「|」|『|』|“|”")
_OCR_DIALOGUE_WEAK_PUNCTUATION_RE = re.compile(r"[，,、：:]")
_OCR_TRAILING_GARBAGE_AFTER_SENTENCE_RE = re.compile(r"([。！？!?…」』”\]］])\s*[号口日曰益了交0-9]{1,2}\s*$")
_OCR_TRAILING_ORPHAN_AFTER_SENTENCE_RE = re.compile(
    r"([。！？!?…」』”\]］])\s*[义人入丁七十廿卜丿丨丶]\s*$"
)
_OCR_TRAILING_MIDDLE_DOT_RE = re.compile(r"(.{9,}[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff])\s*[·•]\s*$")
_OCR_TRAILING_GARBAGE_AFTER_BRACKET_RE = re.compile(
    r"([\]］）】」』”])\s*[^。！？!?…，,、：:；;「」『』“”\[\]［］【】（）()]{1,4}\s*$"
)
_OCR_TRAILING_GARBAGE_AFTER_DASH_RE = re.compile(
    r"((?:——|--|—|－|-|一一))\s*[^。！？!?…，,、：:「」『』“”\[\]［］【】（）()]{1,4}\s*$"
)
_OCR_STABILITY_IGNORED_CHARS_RE = re.compile(
    r"[\s　\-_.,，。:：;；!！?？…~～'\"“”‘’「」『』()\[\]［］【】]+"
)


def _normalize_window_title(value: str) -> str:
    normalized = _WINDOW_SPACE_RE.sub(" ", str(value or "").strip().lower())
    return normalized

def _looks_like_self_window_title(title: str) -> bool:
    normalized = _normalize_window_title(title)
    return any(token in normalized for token in _SELF_WINDOW_TITLE_SUBSTRINGS)


def _looks_like_self_window_path(exe_path: str) -> bool:
    lowered = str(exe_path or "").strip().lower()
    if not lowered:
        return False
    return any(token in lowered for token in _SELF_WINDOW_PATH_SUBSTRINGS)


def _looks_like_self_ui_text(text: str) -> bool:
    normalized = normalize_text(text).strip().lower()
    if not normalized:
        return False
    return any(token in normalized for token in _SELF_UI_GUARD_SUBSTRINGS)


def _looks_like_english_overlay_label(line: str) -> bool:
    words = re.findall(r"[a-z]+", normalize_text(line).strip().lower())
    if not words:
        return False
    if any(word not in _ENGLISH_GAME_OVERLAY_WORDS for word in words):
        return False
    return True


def _looks_like_non_english_overlay_label(line: str) -> bool:
    compact = re.sub(
        r"[\s\-_.,，。:：;；!！?？/\\|()\[\]【】「」『』]+",
        "",
        normalize_text(line).strip().lower(),
    )
    if not compact:
        return False
    remainder = compact
    matched = False
    for token in sorted(_NON_ENGLISH_GAME_OVERLAY_SUBSTRINGS, key=len, reverse=True):
        if token in remainder:
            matched = True
            remainder = remainder.replace(token, "")
    return matched and not remainder


def _looks_like_game_overlay_normalized_text(normalized: str) -> bool:
    normalized = str(normalized or "").strip()
    if not normalized:
        return False
    lowered = normalized.lower()
    lines = _stripped_ocr_lines(lowered)
    non_english_overlay_lines = [
        line for line in lines if _looks_like_non_english_overlay_label(line)
    ]
    if bool(lines) and len(non_english_overlay_lines) == len(lines):
        return True
    english_overlay_lines = [
        line for line in lines if _looks_like_english_overlay_label(line)
    ]
    return bool(lines) and len(english_overlay_lines) == len(lines)


def _looks_like_game_overlay_text(text: str) -> bool:
    normalized = normalize_text(text).strip().lower()
    return _looks_like_game_overlay_normalized_text(normalized)


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


def _looks_like_dialogue_line(text: str) -> bool:
    normalized = normalize_text(text).strip()
    if not normalized:
        return False
    return any(marker in normalized for marker in _DIALOGUE_LINE_MARKERS)


def _looks_like_ocr_dialogue_text(text: str) -> bool:
    normalized = normalize_text(text).replace("\n", " ").strip()
    return _looks_like_ocr_dialogue_normalized_text(normalized)


def _looks_like_ocr_dialogue_normalized_text(normalized: str) -> bool:
    normalized = str(normalized or "").replace("\n", " ").strip()
    if not normalized:
        return False
    significant_chars = _significant_char_count(normalized)
    if significant_chars < 2 or significant_chars > 220:
        return False
    if _OCR_DIALOGUE_STRONG_PUNCTUATION_RE.search(normalized):
        return True
    if _OCR_DIALOGUE_WEAK_PUNCTUATION_RE.search(normalized) and significant_chars >= 8:
        return True
    return False


def _clean_ocr_dialogue_text(text: str) -> str:
    normalized = normalize_text(text).replace("\n", " ").replace("　", "").strip()
    if not normalized:
        return ""
    cleaned = normalized
    cleaned = _OCR_TRAILING_GARBAGE_AFTER_SENTENCE_RE.sub(r"\1", cleaned).strip()
    if _significant_char_count(cleaned) >= 10:
        cleaned = _OCR_TRAILING_ORPHAN_AFTER_SENTENCE_RE.sub(r"\1", cleaned).strip()
        cleaned = _OCR_TRAILING_MIDDLE_DOT_RE.sub(r"\1", cleaned).strip()
    cleaned = _OCR_TRAILING_GARBAGE_AFTER_BRACKET_RE.sub(r"\1", cleaned).strip()
    cleaned = _OCR_TRAILING_GARBAGE_AFTER_DASH_RE.sub(r"\1", cleaned).strip()
    return cleaned


def _drop_ocr_chrome_noise_lines(text: str, *, window_title: str = "") -> str:
    lines = [line.strip() for line in str(text or "").splitlines()]
    meaningful = [line for line in lines if line]
    if len(meaningful) < 2:
        return str(text or "")
    filtered = [
        line
        for line in meaningful
        if not _looks_like_temperature_status_line(line)
        and not _looks_like_window_title_line(line, window_title)
    ]
    if len(filtered) < len(meaningful):
        return "\n".join(filtered)
    return str(text or "")


def _ocr_stability_key(text: str) -> str:
    normalized = normalize_text(str(text or "")).replace("\n", " ").strip().lower()
    if not normalized:
        return ""
    return _OCR_STABILITY_IGNORED_CHARS_RE.sub("", normalized)


def _ocr_stability_keys_match(left: str, right: str) -> bool:
    left_key = str(left or "")
    right_key = str(right or "")
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    if len(left_key) == len(right_key) and len(left_key) >= 8:
        distance = sum(1 for left_char, right_char in zip(left_key, right_key) if left_char != right_char)
        allowed_distance = max(1, int(len(left_key) * 0.08))
        return distance <= allowed_distance
    return False


def _prefer_ocr_stability_text(existing: str, current: str) -> str:
    existing_text = normalize_text(str(existing or "")).strip()
    current_text = normalize_text(str(current or "")).strip()
    if not existing_text:
        return current_text
    if not current_text:
        return existing_text
    existing_has_strong_end = bool(_OCR_DIALOGUE_STRONG_PUNCTUATION_RE.search(existing_text[-2:]))
    current_has_strong_end = bool(_OCR_DIALOGUE_STRONG_PUNCTUATION_RE.search(current_text[-2:]))
    if existing_has_strong_end != current_has_strong_end:
        return existing_text if existing_has_strong_end else current_text
    if _significant_char_count(current_text) > _significant_char_count(existing_text):
        return current_text
    return existing_text


def _coerce_plain_choice_lines(lines: list[str]) -> list[str]:
    if not 2 <= len(lines) <= _AIHONG_MENU_MAX_LINES:
        return []
    choices: list[str] = []
    seen: set[str] = set()
    for line in lines:
        text = normalize_text(str(line or "")).replace("\n", " ").strip()
        if not text or _looks_like_dialogue_line(text):
            return []
        if _significant_char_count(text) > _AIHONG_MENU_MAX_SIGNIFICANT_CHARS:
            return []
        if text in seen:
            continue
        seen.add(text)
        choices.append(text)
    if not 2 <= len(choices) <= _AIHONG_MENU_MAX_LINES:
        return []
    return choices


def _coerce_choice_lines(lines: list[str], *, allow_plain_text: bool = False) -> list[str]:
    choices = _coerce_prefixed_choice_lines(lines)
    if choices:
        return choices
    if allow_plain_text:
        return _coerce_plain_choice_lines(lines)
    return []


def _score_ocr_text(text: str) -> tuple[float, int, int]:
    normalized = normalize_text(text)
    if not normalized:
        return (-1.0, 0, 0)
    cjk_count = len(_CJK_CHAR_RE.findall(normalized))
    kana_count = len(_KANA_CHAR_RE.findall(normalized))
    ascii_tokens = _ASCII_TOKEN_RE.findall(normalized)
    isolated_ascii_tokens = sum(
        1 for token in ascii_tokens if len(token) == 1 and token.lower() not in {"i", "a"}
    )
    multi_char_ascii_tokens = sum(1 for token in ascii_tokens if len(token) > 1)
    significant_chars = sum(1 for ch in normalized if not ch.isspace())
    score = (
        (cjk_count * 5.0)
        + (kana_count * 4.0)
        + (multi_char_ascii_tokens * 1.5)
        + (significant_chars * 0.2)
        - (isolated_ascii_tokens * 2.0)
    )
    return (score, cjk_count + kana_count, significant_chars)


_PUNCTUATION_CONFUSION_FIXES = [
    (re.compile(r"(?<=[^\x00-\x7F])\.(?![\x00-\x7F])"), "。"),
    (re.compile(r"(?<=[^\x00-\x7F])\s*,\s*(?=[^\x00-\x7F])"), "、"),
    (re.compile(r"(?<=[^\x00-\x7F])!(?![\x00-\x7F])"), "！"),
    (re.compile(r"(?<=[^\x00-\x7F])\?(?![\x00-\x7F])"), "？"),
]


def _fix_ocr_punctuation_confusion(text: str) -> str:
    value = str(text or "")
    for pattern, replacement in _PUNCTUATION_CONFUSION_FIXES:
        value = pattern.sub(replacement, value)
    return value


def _significant_char_count(text: str) -> int:
    return sum(1 for ch in str(text or "") if not ch.isspace())


def _looks_like_noise_ocr_text(text: str) -> bool:
    normalized = normalize_text(str(text or "")).strip()
    return _looks_like_noise_normalized_text(normalized)


def _looks_like_noise_normalized_text(normalized: str) -> bool:
    normalized = str(normalized or "").strip()
    if not normalized:
        return True
    significant_chars = _significant_char_count(normalized)
    cjk_or_kana_count = len(_CJK_CHAR_RE.findall(normalized)) + len(_KANA_CHAR_RE.findall(normalized))
    if cjk_or_kana_count <= 0 and significant_chars <= 2:
        return True
    return False


def _classify_cjk_text(text: str) -> str:
    """Return RapidOCR lang_type: japan, korean, ch, or unknown."""
    if not text or not text.strip():
        return "unknown"
    if _HANGUL_RE.search(text):
        return "korean"
    if _HIRAGANA_RE.search(text) or _KATAKANA_RE.search(text):
        return "japan"
    if not _CJK_CHAR_RE.search(text):
        return "unknown"

    ja_votes = sum(1 for word in _JA_MARKER_WORDS if word in text)
    zh_votes = sum(1 for word in _ZH_MARKER_WORDS if word in text)
    if ja_votes > zh_votes:
        return "japan"
    if zh_votes > ja_votes:
        return "ch"
    if _KANA_BUD_RE.search(text):
        return "japan"
    return "ch"

class _OcrLangDetector:
    def __init__(self, window_size: int = 8, confirm_streak: int = 2) -> None:
        self._window_size = max(1, int(window_size or 1))
        self._confirm_streak = max(1, int(confirm_streak or 1))
        self._buffer: list[str] = []
        self._last_detected: str | None = None
        self._confirmed_lang: str | None = None
        self._streak = 0
        self._switched_at: float | None = None

    def feed(self, text: str) -> str | None:
        cleaned = str(text or "").strip()
        if not cleaned:
            return None
        if not (
            _CJK_CHAR_RE.search(cleaned)
            or _HIRAGANA_RE.search(cleaned)
            or _KATAKANA_RE.search(cleaned)
            or _HANGUL_RE.search(cleaned)
        ):
            return None

        self._buffer.append(cleaned)
        if len(self._buffer) < self._window_size:
            return None

        merged = " ".join(self._buffer)
        self._buffer.clear()
        detected = _classify_cjk_text(merged)
        if detected == "unknown":
            return None

        if detected == self._last_detected:
            self._streak += 1
        else:
            self._last_detected = detected
            self._streak = 1

        if self._streak >= self._confirm_streak:
            if self._confirmed_lang is not None and detected != self._confirmed_lang:
                self._switched_at = time.monotonic()
            self._confirmed_lang = detected
            return detected
        return None

    def reset(self, *, clear_switch_time: bool = False) -> None:
        self._buffer.clear()
        self._last_detected = None
        self._confirmed_lang = None
        self._streak = 0
        if clear_switch_time:
            self._switched_at = None

    @property
    def last_switched_at(self) -> float | None:
        return self._switched_at


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


def _weighted_ocr_score(scores: Iterable[tuple[float, int]]) -> float:
    total_weight = 0
    weighted_sum = 0.0
    for score, weight in scores:
        normalized_weight = max(int(weight or 0), 1)
        weighted_sum += float(score) * normalized_weight
        total_weight += normalized_weight
    if total_weight <= 0:
        return 0.0
    return weighted_sum / total_weight


def _average_ocr_box_confidence(boxes: Iterable[Any]) -> float:
    scores: list[tuple[float, int]] = []
    for box in list(boxes or []):
        try:
            score = float(getattr(box, "score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        if score <= 0.0:
            continue
        text = str(getattr(box, "text", "") or "")
        scores.append((score, _ocr_score_weight(text)))
    if not scores:
        return 0.0
    return round(max(0.0, min(_weighted_ocr_score(scores), 1.0)), 3)


def _bounded_confidence_or_zero(value: object) -> float:
    try:
        return round(max(0.0, min(float(value), 1.0)), 3)
    except (TypeError, ValueError):
        return 0.0


def _canonical_choice_candidate_text(choices: list[str]) -> str:
    normalized = [normalize_text(str(choice or "")).strip() for choice in choices]
    return "\n".join(item for item in normalized if item)


def _stripped_ocr_lines(raw_text: str) -> list[str]:
    return [line.strip() for line in str(raw_text or "").splitlines() if line.strip()]
