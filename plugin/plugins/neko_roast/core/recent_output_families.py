"""Spent-output memory helpers for recent live context."""

from __future__ import annotations

import re
from typing import Any


_SPENT_OUTPUT_ASCII_WORD_RE = re.compile(r"[a-z0-9]+")

_SPENT_OUTPUT_FAMILY_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "choice_vote",
        (
            "either_or",
            "a/b",
            "choice",
            "\u4e8c\u9009\u4e00",
            "\u4e8c\u62e9\u4e00",
            "\u9009\u4e00\u4e2a",
            "\u9009\u4e00",
            "\u8fd8\u662f",
        ),
    ),
    (
        "food_drink",
        (
            "snack",
            "drink",
            "dessert",
            "\u5c0f\u751c\u98df",
            "\u751c\u98df",
            "\u70ed\u996e",
            "\u996e\u6599",
            "\u96f6\u98df",
            "\u5c0f\u9c7c\u5e72",
        ),
    ),
    ("surprise", ("surprise", "惊喜", "小惊喜")),
    (
        "reward",
        ("reward", "present", "gift", "snack", "小鱼干", "鱼干", "奖励", "礼物"),
    ),
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
    (
        "short_callback",
        ("one word", "password", "一个字", "一个词", "三字", "暗号", "打分"),
    ),
    ("room_mood", ("room mood", "气氛", "温度", "猫窝", "小电台", "晴天", "小雨")),
    (
        "object_scene",
        ("desk", "screen", "keyboard", "桌面", "水杯", "零食", "屏幕", "键盘"),
    ),
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
    if (
        token_text.isascii()
        and token_text.replace(" ", "").isalnum()
        and " " not in token_text
    ):
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


def recent_spent_output_families(recent_results: Any, *, limit: int = 12) -> set[str]:
    families: set[str] = set()
    seen_results = 0
    for result in reversed(list(recent_results or [])):
        if not isinstance(result, dict):
            continue
        if str(result.get("status") or "") != "pushed":
            continue
        raw_family = str(result.get("spent_output_family") or "").strip()
        if not raw_family:
            raw_family = ",".join(spent_output_families(spent_output_text(result)))
        if not raw_family:
            continue
        seen_results += 1
        families.update(part.strip() for part in raw_family.split(",") if part.strip())
        if seen_results >= max(1, int(limit)):
            break
    return families
