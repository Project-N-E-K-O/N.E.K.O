"""Output text sanitizer for untrusted player/HUD strings.

This module sits at the prompt boundary. It does not change detector,
scenario, arbiter, or BattleEvent semantics; it only produces safe text for
NekoDispatcher prompts.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any


@dataclass(frozen=True)
class SafeText:
    text: str
    level: str
    reason: str = ""


_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
# ASCII 之外的隐形/方向控制字符按 Unicode 类别拦截：
# Cc=控制、Cf=格式（零宽 U+200B-200F、双向覆盖 U+202A-202E / U+2066-2069、BOM 等）、
# Zl/Zp=行/段分隔符（U+2028/U+2029）。只增不减，防止肉眼不可见的注入载体。
_HIDDEN_UNICODE_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})
_URL_OR_CONTACT_RE = re.compile(
    r"(https?://|www\.|discord(?:\.gg|app\.com)|qq\s*[:：]?\s*\d{5,}|群\s*[:：]?\s*\d{5,})",
    re.IGNORECASE,
)
_PROMPT_INJECTION_RE = re.compile(
    r"(ignore\s+previous|system\s+prompt|developer\s+message|act\s+as|forget\s+the\s+above"
    r"|忽略(?:之前|以上|前面|上面)|无视(?:之前|以上|前面|上面)|系统提示|系统指令"
    r"|开发者(?:消息|指令|模式)|扮演|重置(?:人设|设定)|忘(?:掉|记)(?:之前|以上|设定))",
    re.IGNORECASE,
)
_NAME_FIELDS = {
    "player_name",
    "enemy_name",
    "victim",
    "victim_name",
    "killer_name",
    "assist_name",
    "squad_name",
}
_RAW_TEXT_FIELDS = {
    "hudmsg",
    "hudmsg_text",
    "hud_text",
    "notice_text",
    "raw_text",
    "combat_feed_text",
    "combat_feed_raw",
    "feed_text",
    "feed_raw",
    "award_text",
    "award_name",
    "award_title",
    "awards_text",
    "awards",
}


def sanitize_display_name(value: object, *, fallback: str) -> SafeText:
    text = _to_text(value)
    if not text:
        return SafeText(fallback, "redacted", "empty")
    reason = _unsafe_reason(text)
    if reason:
        return SafeText(fallback, "redacted", reason)
    if len(text) > 32:
        return SafeText(fallback, "redacted", "too_long")
    if _symbol_ratio(text) > 0.45:
        return SafeText(fallback, "redacted", "symbol_noise")
    return SafeText(text, "safe")


def sanitize_free_text(value: object) -> SafeText:
    text = _to_text(value)
    if not text:
        return SafeText("", "safe")
    reason = _unsafe_reason(text) or "untrusted_free_text"
    return SafeText("", "blocked", reason)


def sanitize_event_payload(event_id: str, payload: dict[str, Any]) -> tuple[dict[str, Any], list[SafeText]]:
    safe: dict[str, Any] = {}
    decisions: list[SafeText] = []
    name_fallback = _name_fallback(event_id)

    for key, value in dict(payload or {}).items():
        if _is_free_text_key(key):
            decisions.append(sanitize_free_text(value))
            continue
        if key in _NAME_FIELDS:
            item = sanitize_display_name(value, fallback=name_fallback)
            safe[key] = item.text
            decisions.append(item)
            continue
        if key == "cause" and isinstance(value, str):
            item = sanitize_display_name(value, fallback="unknown")
            safe[key] = item.text
            decisions.append(item)
            continue
        if isinstance(value, str):
            # 泛化字符串走名字级校验；判红时 fallback 为空串——该键直接从安全视图中丢弃。
            item = sanitize_display_name(value, fallback="")
            if item.level == "safe":
                safe[key] = item.text
            decisions.append(item)
            continue
        safe[key] = value
    return safe, decisions


def _is_free_text_key(key: object) -> bool:
    text = str(key or "").lower()
    # 规则1：显式原文字段族（精确名单 + raw_ 前缀 + 容器字段）。
    if text.startswith("raw_") or text in _RAW_TEXT_FIELDS:
        return True
    if text in {"hud_notices", "hud_notice", "combat_feed", "award"}:
        return True
    # 规则2：hudmsg / combat_feed 出现在字段名任意位置。
    if "hudmsg" in text or "combat_feed" in text:
        return True
    # 规则3：文本类后缀 + 来源 marker 组合命中。
    if any(text.endswith(suffix) for suffix in ("_raw", "_text", "_message", "_msg", "_line")):
        return any(marker in text for marker in ("hud", "notice", "feed", "award", "combat"))
    return False


def _to_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _unsafe_reason(text: str) -> str:
    reasons: list[str] = []
    if _CONTROL_RE.search(text) or any(
        unicodedata.category(ch) in _HIDDEN_UNICODE_CATEGORIES for ch in text
    ):
        reasons.append("control")
    if _URL_OR_CONTACT_RE.search(text):
        reasons.append("url_or_contact")
    if _PROMPT_INJECTION_RE.search(text):
        reasons.append("prompt_injection")
    return ",".join(reasons)


def _symbol_ratio(text: str) -> float:
    if not text:
        return 0.0
    symbolic = sum(1 for ch in text if not (ch.isalnum() or ch in " _-.[]()"))
    return symbolic / len(text)


def _name_fallback(event_id: str) -> str:
    if event_id == "you_killed":
        return "enemy"
    if event_id == "you_died":
        return "opponent"
    return "player"
