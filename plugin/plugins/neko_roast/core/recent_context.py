"""Helpers for compact live-context memory used by the roast runtime."""

from __future__ import annotations

import re
from typing import Any


_SPENT_OUTPUT_ASCII_WORD_RE = re.compile(r"[a-z0-9]+")
_SPENT_OUTPUT_FAMILY_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("surprise", ("surprise", "惊喜", "小惊喜")),
    ("reward", ("reward", "present", "gift", "snack", "小鱼干", "鱼干", "奖励", "礼物")),
    ("program_plan", ("plan", "program", "segment", "企划", "节目", "环节", "计划")),
    (
        "audience_prompt",
        (
            "chat",
            "danmaku",
            "viewer",
            "audience",
            "drop a 1",
            "type 1",
            "say hi",
            "anyone here",
            "still here",
            "大家",
            "你们",
            "观众",
            "弹幕",
            "互动",
            "接话",
            "发言",
            "发弹幕",
            "发个1",
            "扣1",
            "想听",
            "想看",
            "聊点",
            "聊什么",
            "说点",
            "来一句",
            "扣个",
            "扣个1",
            "打个1",
            "打个分",
            "打个标签",
            "吱一声",
            "冒个泡",
            "举个爪",
            "给点反应",
            "给猫猫一点反应",
            "还在吗",
            "有人吗",
            "有人在吗",
            "在不在",
        ),
    ),
    ("host_self_test", ("host score", "主播力", "正经主播", "主持", "像主播")),
    ("short_callback", ("one word", "password", "一个字", "一个词", "三字", "暗号", "打分")),
    ("choice_vote", ("either_or", "a/b", "choice", "二选一", "选一个", "还是")),
    ("room_mood", ("room mood", "气氛", "温度", "猫窝", "小电台", "晴天", "小雨")),
    ("object_scene", ("desk", "screen", "keyboard", "桌面", "水杯", "零食", "屏幕", "键盘")),
    ("tease", ("tease", "吐槽", "别笑", "被自己")),
    ("micro_challenge", ("challenge", "task", "三秒", "挑战", "任务", "姿势")),
    ("quiet_room", ("quiet", "idle", "冷场", "安静", "没人说话", "没弹幕")),
)

_SYNTHETIC_OUTPUT_PREFIXES = (
    "queued_to_neko(",
    "dry_run(",
    "skipped_to_neko(",
    "instructions_queued(",
    "instructions_restored(",
    "developer_instructions_queued(",
    "developer_instructions_restored(",
    "developer_mode_announced(",
)

_KNOWN_ROUTE_STEPS = {
    "danmaku_response",
    "avatar_roast",
    "idle_hosting",
    "active_engagement",
    "warmup_hosting",
    "gift_signal",
    "super_chat_signal",
}


def _normalize_spent_output_family_text(value: str) -> str:
    return "".join(str(value or "").casefold().split())


def _spent_output_ascii_words(value: str) -> set[str]:
    return set(_SPENT_OUTPUT_ASCII_WORD_RE.findall(str(value or "").casefold()))


def _spent_output_family_token_matches(
    *,
    normalized_output: str,
    ascii_words: set[str],
    token: str,
) -> bool:
    token_text = str(token or "").strip()
    normalized_token = _normalize_spent_output_family_text(token_text)
    if not normalized_token:
        return False
    if token_text.isascii() and token_text.replace(" ", "").isalnum() and " " not in token_text:
        return normalized_token in ascii_words
    return normalized_token in normalized_output


def spent_output_text(result: dict[str, Any]) -> str:
    """Return real text NEKO said, excluding synthetic dispatcher markers."""
    if str(result.get("status") or "") != "pushed":
        return ""
    output = str(result.get("output") or "").strip()
    if not output:
        return ""
    if output.startswith(_SYNTHETIC_OUTPUT_PREFIXES):
        return ""
    return output


def spent_output_families(output: str) -> list[str]:
    normalized = _normalize_spent_output_family_text(output)
    if not normalized:
        return []
    ascii_words = _spent_output_ascii_words(output)
    families: list[str] = []
    for family, tokens in _SPENT_OUTPUT_FAMILY_TOKENS:
        if any(
            _spent_output_family_token_matches(
                normalized_output=normalized,
                ascii_words=ascii_words,
                token=token,
            )
            for token in tokens
        ):
            families.append(family)
    return families


def compact_context_text(value: str, *, limit: int = 80) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def route_from_result(result: dict[str, Any]) -> str:
    response_module = str(result.get("response_module") or "")
    if response_module:
        return response_module
    event = result.get("event") if isinstance(result.get("event"), dict) else {}
    source = str(event.get("source") or "")
    event_type = str(event.get("event_type") or "").strip().lower()
    signal_route = signal_route_for_event_type(event_type)
    if signal_route:
        return signal_route
    if source in {"idle_hosting", "active_engagement", "warmup_hosting"}:
        return source
    steps = result.get("steps") if isinstance(result.get("steps"), list) else []
    for step in reversed(steps):
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("id") or "")
        if step_id in _KNOWN_ROUTE_STEPS:
            return step_id
    return source or "unknown"


def signal_route_for_event_type(event_type: str) -> str:
    normalized = str(event_type or "").strip().lower()
    if normalized in {"gift", "guard"}:
        return "gift_signal"
    if normalized in {"super_chat", "sc"}:
        return "super_chat_signal"
    return ""


def event_signal_from_result(result: dict[str, Any]) -> str:
    event = result.get("event") if isinstance(result.get("event"), dict) else {}
    source = str(event.get("source") or "")
    if source != "live_danmaku":
        return source or "unknown"
    event_type = str(event.get("event_type") or "").strip().lower()
    if event_type in {"gift", "guard"}:
        return "gift_signal"
    if event_type in {"super_chat", "sc"}:
        return "super_chat_signal"
    text = str(event.get("danmaku_text") or "").strip().lower()
    if any(token in text for token in ("粉丝团灯牌", "赠送", "送出", "礼物", "gift")):
        return "gift_signal"
    if any(token in text for token in ("super chat", "superchat", "醒目留言", "sc")):
        return "super_chat_signal"
    return "danmaku_signal"
