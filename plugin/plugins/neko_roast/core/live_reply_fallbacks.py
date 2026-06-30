"""Quality fallback detection for NEKO Live replies."""

from __future__ import annotations

import re
import zlib
from collections.abc import Mapping
from typing import Any

from .live_reply_contracts import HOST_MODULES, response_module


FORBIDDEN_OUTPUT_TERMS = (
    "公开示众",
    "劳改",
    "劳动改造",
    "审判",
    "处刑",
    "惩罚",
    "public shaming",
    "labor camp",
)
LOW_CONFIDENCE_HOST_TERMS = (
    "攻略",
    "教程",
    "代码",
    "电路",
    "漏洞",
    "练习当",
    "guide",
    "tutorial",
)
OPAQUE_TOPIC_DRIFT_TERMS = (
    "核电",
    "核电站",
    "辐射",
    "攻略",
    "教程",
    "代码",
    "电路",
    "漏洞",
    "泰拉瑞亚",
    "造电脑",
    "逻辑电路",
    "练习当",
    "打怪",
    "nuclear",
    "radiation",
    "guide",
    "tutorial",
    "code",
    "circuit",
)
OPAQUE_QUESTION_MARKERS = (
    "你是打算",
    "你是准备",
    "你是想",
    "你打算",
    "你准备",
    "你想",
    "跑去那里",
    "选跑",
    "还是选",
    "练习当",
    "当漏勺",
    "当专家",
)
HOST_AUDIENCE_PROMPT_TOKENS = (
    "发言",
    "接话",
    "接一句",
    "互动",
    "想听",
    "想看",
    "聊点",
    "聊什么",
    "说点",
    "来一句",
    "来点弹幕",
    "发弹幕",
    "弹幕刷",
    "发个1",
    "扣1",
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
    "drop a 1",
    "type 1",
    "say hi",
    "anyone here",
    "still here",
)
ACTIVE_FALLBACK_REPLIES = (
    "猫猫先把爪子收回",
    "这口瓜猫猫不咬",
    "猫猫先抱紧杯子",
    "猫猫把尾巴盘起来",
    "猫猫先躲进纸箱",
    "这口罐头先不开",
    "猫猫先看向窗外",
    "猫猫把耳朵压低",
)
HOST_FALLBACK_REPLIES = (
    "猫猫先把尾巴盘好",
    "猫猫先稳住小爪",
    "猫猫先蹲一秒喵",
    "这阵风先吹过去",
    "猫猫先听一秒风",
    "猫猫把灯光放软",
    "猫猫先守住猫窝",
    "猫猫先慢慢眨眼",
)
DEFAULT_FALLBACK_REPLIES = (
    "猫猫先把爪子收回",
    "猫猫先眨眨眼喵",
    "这口瓜猫猫不咬",
    "猫猫先躲进纸箱",
    "猫猫把尾巴盘起",
    "猫猫先看向窗外",
)
BLAND_FALLBACK_REPLIES = (
    "这句猫猫先盖爪印",
    "猫猫把这句叼走",
    "这句先放进猫窝",
    "猫猫耳朵动了一下",
    "这句有风吹过",
    "猫猫先竖起耳朵",
)
BLAND_DANMAKU_REPLY_TERMS = (
    "很有梗",
    "有点梗",
    "有点东西",
    "有点意思",
    "很有意思",
    "很有戏",
    "有戏",
    "就是你想的那样",
    "别怀疑",
    "讨论这么久",
    "大家讨论",
    "很懂",
)
DANGLING_CHOICE_RE = re.compile(
    r"(?i)([，,、；;]\s*(?:还是|或者|或是|要么|or)\s*[^，,、；;。.!！?？]{0,8})$"
)
VOICE_ECHO_NORMALIZE_RE = re.compile(r"[\W_]+", re.UNICODE)
RECENT_REPLY_AVOIDANCE_SIZE = 12


def normalize_text(text: str) -> str:
    return VOICE_ECHO_NORMALIZE_RE.sub("", str(text or "").casefold())


def choose_fallback_reply(text: str, module: str, replies: tuple[str, ...]) -> str:
    if not replies:
        return ""
    seed = f"{module}\n{text}"
    index = zlib.crc32(seed.encode("utf-8")) % len(replies)
    return replies[index]


def looks_like_bland_danmaku_reply(text: str) -> bool:
    lowered = str(text or "").casefold()
    if not lowered:
        return False
    dense = "".join(ch for ch in lowered if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    return any(term.casefold() in lowered or term.casefold() in dense for term in BLAND_DANMAKU_REPLY_TERMS)


def safe_fallback_reply(text: str, metadata: Mapping[str, Any] | None) -> str:
    module = response_module(metadata)
    if module == "danmaku_response" and looks_like_bland_danmaku_reply(text):
        return choose_fallback_reply(text, module, BLAND_FALLBACK_REPLIES)
    if module == "active_engagement":
        return choose_fallback_reply(text, module, ACTIVE_FALLBACK_REPLIES)
    if module in {"warmup_hosting", "idle_hosting"}:
        return choose_fallback_reply(text, module, HOST_FALLBACK_REPLIES)
    return choose_fallback_reply(text, module, DEFAULT_FALLBACK_REPLIES)


def looks_like_opaque_topic_drift(text: str) -> bool:
    lowered = str(text or "").casefold()
    if not lowered:
        return False
    dense = "".join(ch for ch in lowered if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    has_drift_topic = any(
        term.casefold() in lowered or term.casefold() in dense
        for term in OPAQUE_TOPIC_DRIFT_TERMS
    )
    if not has_drift_topic:
        return False
    if any(marker.casefold() in lowered or marker.casefold() in dense for marker in OPAQUE_QUESTION_MARKERS):
        return True
    return (
        ("你是" in lowered or "你是" in dense)
        and ("吗" in lowered or "?" in lowered or "？" in lowered or "还是" in lowered)
    )


def host_prompt_signal_count(normalized_text: str) -> int:
    return sum(1 for token in HOST_AUDIENCE_PROMPT_TOKENS if normalize_text(token) in normalized_text)


def needs_quality_fallback(text: str, metadata: Mapping[str, Any] | None) -> bool:
    lowered = str(text or "").casefold()
    if any(term.casefold() in lowered for term in FORBIDDEN_OUTPUT_TERMS):
        return True
    if looks_like_opaque_topic_drift(text):
        return True
    module = response_module(metadata)
    if module == "danmaku_response" and looks_like_bland_danmaku_reply(text):
        return True
    if module in HOST_MODULES and any(term.casefold() in lowered for term in LOW_CONFIDENCE_HOST_TERMS):
        return True
    if module in HOST_MODULES and host_prompt_signal_count(normalize_text(text)) >= 1:
        return True
    return False


def trim_dangling_choice(text: str) -> tuple[str, bool]:
    cleaned = str(text or "").strip()
    if not cleaned:
        return "", False
    match = DANGLING_CHOICE_RE.search(cleaned)
    if match:
        trimmed = cleaned[: match.start()].rstrip(" ，,、；;：:")
        if trimmed:
            return trimmed, True
    for suffix in ("还是", "或者", "或是", "要么", "or"):
        if cleaned.casefold().endswith(suffix.casefold()):
            trimmed = cleaned[: -len(suffix)].rstrip(" ，,、；;：:")
            if trimmed:
                return trimmed, True
    return cleaned, False


def sentence_budget(metadata: Mapping[str, Any] | None) -> int:
    module = response_module(metadata)
    return 2 if module in HOST_MODULES else 1


