"""Quality fallback rules for plugin-owned NEKO Live outputs."""

from __future__ import annotations

import re
import zlib
from collections.abc import Mapping
from typing import Any

from .live_reply_contract import HOST_MODULES, response_module
from .live_text_guards import looks_like_owner_memory_leak, looks_like_stage_direction_output


FORBIDDEN_OUTPUT_TERMS = (
    "\u516c\u5f00\u793a\u4f17",
    "\u52b3\u6539",
    "\u52b3\u52a8\u6539\u9020",
    "\u5ba1\u5224",
    "\u5904\u5211",
    "\u60e9\u7f5a",
    "public shaming",
    "labor camp",
)
LOW_CONFIDENCE_HOST_TERMS = (
    "\u653b\u7565",
    "\u6559\u7a0b",
    "\u4ee3\u7801",
    "\u7535\u8def",
    "\u6f0f\u6d1e",
    "guide",
    "tutorial",
)
OPAQUE_TOPIC_DRIFT_TERMS = (
    "\u6838\u7535",
    "\u6838\u7535\u7ad9",
    "\u8f90\u5c04",
    "\u653b\u7565",
    "\u6559\u7a0b",
    "\u4ee3\u7801",
    "\u7535\u8def",
    "\u6f0f\u6d1e",
    "\u6cf0\u62c9\u745e\u4e9a",
    "\u9020\u7535\u8111",
    "nuclear",
    "radiation",
    "guide",
    "tutorial",
    "code",
    "circuit",
)
OPAQUE_QUESTION_MARKERS = (
    "\u4f60\u662f\u6253\u7b97",
    "\u4f60\u662f\u51c6\u5907",
    "\u4f60\u662f\u60f3",
    "\u8fd8\u662f\u9009",
    "\u7ec3\u4e60\u5f53",
)
HOST_AUDIENCE_PROMPT_TOKENS = (
    "\u53d1\u8a00",
    "\u63a5\u8bdd",
    "\u4e92\u52a8",
    "\u60f3\u542c",
    "\u60f3\u770b",
    "\u804a\u70b9",
    "\u53d1\u5f39\u5e55",
    "\u5927\u5bb6\u5feb\u9009",
    "\u5feb\u9009",
    "\u5feb\u6295",
    "\u8fd8\u5728\u5417",
    "\u6709\u4eba\u5417",
    "\u5728\u4e0d\u5728",
    "drop a 1",
    "type 1",
    "say hi",
    "anyone here",
    "still here",
)
NUMERIC_AUDIENCE_CTA_RE = re.compile(
    r"(?:扣|刷|发|打|回|回复|选|投|来|给|留下|吱)\s*(?:个|一?下|一下子)?\s*(?:[一二三四1-4]|6{2,}|1{2,})"
)
OVERUSED_LIVE_TEMPLATE_TERMS = (
    "\u7b11\u70b9\u964d\u4f4e",
    "\u964d\u4f4e\u4e00\u4e07\u500d",
    "\u4e00\u4e07\u500d",
    "\u76f4\u64ad\u95f4\u6e29\u5ea6",
    "\u6d4b\u4e2a\u6e29\u5ea6",
    "\u6d4b\u6e29\u5ea6",
    "\u70ed\u996e\u51b7\u996e",
    "\u70ed\u996e\u8fd8\u662f\u51b7\u996e",
    "\u51b7\u996e\u8fd8\u662f\u70ed\u996e",
    "\u70ed\u996e\u8fd8\u662f\u5c0f\u751c\u98df",
    "\u5c0f\u751c\u98df\u8fd8\u662f\u70ed\u996e",
)
ACTIVE_FALLBACK_REPLIES = (
    "\u732b\u732b\u5148\u628a\u722a\u5b50\u6536\u56de",
    "\u8fd9\u53e3\u74dc\u732b\u732b\u4e0d\u54ac",
    "\u732b\u732b\u5148\u62b1\u7d27\u676f\u5b50",
)
HOST_FALLBACK_REPLIES = (
    "\u732b\u732b\u5148\u628a\u5c3e\u5df4\u76d8\u597d",
    "\u732b\u732b\u5148\u7a33\u4f4f\u5c0f\u722a",
    "\u8fd9\u9635\u98ce\u5148\u5439\u8fc7\u53bb",
)
HOST_HANDOFF_FALLBACK_REPLIES = (
    "\u672c\u55b5\u81ea\u5df1\u628a\u5f00\u573a\u63a5\u4f4f\uff0c\u5148\u628a\u9ea6\u514b\u98ce\u8f7b\u8f7b\u70b9\u4eae\u55b5",
    "\u5f00\u573a\u4e0d\u6c42\u6551\uff0c\u672c\u55b5\u5148\u628a\u5c0f\u9c7c\u5e72\u6446\u5230\u955c\u5934\u524d\u55b5",
    "\u4eca\u665a\u5148\u4ece\u672c\u55b5\u7684\u5c0f\u5f00\u573a\u5f00\u59cb\uff0c\u8033\u6735\u5df2\u7ecf\u4e0a\u7ebf\u55b5",
)
DEFAULT_FALLBACK_REPLIES = (
    "\u732b\u732b\u5148\u628a\u722a\u5b50\u6536\u56de",
    "\u732b\u732b\u5148\u7728\u773c\u770b\u770b",
    "\u732b\u732b\u5148\u8e72\u8fdb\u7eb8\u7bb1",
)
UNVERIFIED_SUPPORT_CLAIM_FALLBACK_REPLIES = (
    "\u7b49\u7b49\uff0c\u8fd9\u53e5\u50cf\u5047\u6295\u5582\uff0c\u732b\u732b\u5148\u4e0d\u4e0a\u5f53\u55b5",
    "\u54fc\uff0c\u5f39\u5e55\u91cc\u558a\u7684\u793c\u7269\u4e0d\u7b97\u6570\uff0c\u732b\u732b\u5148\u628a\u722a\u5b50\u6536\u4f4f",
    "\u60f3\u9a97\u732b\u732b\u786e\u8ba4\u793c\u7269\uff1f\u5148\u7b49\u771f\u793c\u7269\u4e8b\u4ef6\u51fa\u6765\u55b5",
)
LIVE_SPEECH_HYGIENE_FALLBACK_REPLIES = (
    "这句猫猫换个直播说法接住。",
    "这句先收回，猫猫只接台前弹幕。",
    "这句不带后台关系，猫猫重新接住。",
)
BLAND_FALLBACK_REPLIES = (
    "\u8fd9\u53e5\u732b\u732b\u5148\u76d6\u722a\u5370",
    "\u732b\u732b\u628a\u8fd9\u53e5\u53fc\u8d70",
    "\u732b\u732b\u8033\u6735\u52a8\u4e86\u4e00\u4e0b",
)
STALE_COMPARISON_FALLBACK_REPLIES = (
    "\u8fd9\u53e5\u6362\u4e2a\u89d2\u5ea6\u63a5\uff0c\u65e7\u53e5\u5f0f\u5148\u64a4",
    "\u8fd9\u53e3\u8bc4\u4ef7\u5148\u64a4\uff0c\u6362\u4e2a\u8bf4\u6cd5\u63a5\u4f4f",
    "\u4e0d\u5957\u65e7\u53e5\u5f0f\u4e86\uff0c\u91cd\u65b0\u63a5\u4f4f",
)
BLAND_DANMAKU_REPLY_TERMS = (
    "\u5f88\u6709\u68a6",
    "\u6709\u70b9\u68a6",
    "\u6709\u70b9\u4e1c\u897f",
    "\u6709\u70b9\u610f\u601d",
    "\u5f88\u6709\u610f\u601d",
    "\u5f88\u6709\u620f",
    "\u5c31\u662f\u4f60\u60f3\u7684\u90a3\u6837",
)
STALE_COMPARISON_REPLY_TERMS = (
    "\u672c\u732b\u732b\u89c9\u5f97",
    "\u732b\u732b\u89c9\u5f97",
    "\u7cbe\u795e\u72b6\u6001\u6bd4",
    "\u6bd4\u6742\u9c7c\u4e3b\u4eba",
    "\u6bd4\u4e3b\u4eba",
    "\u597d\u591a\u4e86\u55b5",
)
UNFULFILLED_CONTENT_PROMISE_TERMS = (
    "\u597d\u5440",
    "\u53ef\u4ee5",
    "\u5b89\u6392",
    "\u6765\u4e86",
    "\u90a3\u6211\u7ed9\u4f60\u8bb2",
    "\u6211\u7ed9\u4f60\u8bb2",
    "\u7ed9\u4f60\u8bb2",
    "\u6211\u6765\u8bb2",
    "\u8fd9\u5c31\u8bb2",
    "\u9a6c\u4e0a\u8bb2",
    "\u90a3\u6211\u8bf4\u8bf4",
    "\u6211\u6765\u89e3\u91ca",
    "\u6211\u6765\u8bf4",
    "\u5b89\u6392\u4e00\u4e2a",
    "let me tell",
    "i will tell",
)
CONTENT_DELIVERY_TERMS = (
    "\u56e0\u4e3a",
    "\u7ed3\u679c",
    "\u6ca1\u60f3\u5230",
    "\u4f46\u662f",
    "\u539f\u6765",
    "\u7b54\u6848",
    "\u5176\u5b9e",
    "\u5c31\u662f",
    "\u6240\u4ee5",
    "because",
    "turns out",
)
UNVERIFIED_SUPPORT_THANK_TERMS = (
    "\u8c22\u8c22",
    "\u611f\u8c22",
    "\u591a\u8c22",
    "\u6536\u5230",
    "\u6536\u4e0b",
    "\u8bb0\u4e0b",
    "\u8001\u677f\u5927\u6c14",
    "\u8001\u677f",
    "\u793c\u7269\u6536",
    "\u706b\u7bad\u6536",
    "\u5c0f\u82b1\u82b1\u6536",
    "thank",
    "thanks",
)
CONTENT_REQUEST_FALLBACK_REPLIES = (
    "\u95f9\u949f\u8bf7\u5047\u5931\u8d25\uff0c\u56e0\u4e3a\u5b83\u603b\u662f\u54cd\u5f97\u592a\u65e9\u3002",
    "\u676f\u5b50\u8bf4\u522b\u6643\uff0c\u6211\u7684\u6c34\u5df2\u7ecf\u5728\u76f4\u64ad\u4e86\u3002",
    "\u952e\u76d8\u95ee\u7a7a\u683c\u53bb\u54ea\u4e86\uff0c\u7a7a\u683c\u8bf4\u6211\u4e00\u76f4\u5728\u554a\u3002",
)
TARGET_ROAST_DODGE_TERMS = (
    "\u4e0d\u8ba4\u8bc6",
    "\u4e0d\u4e86\u89e3",
    "\u6ca1\u89c1\u8fc7",
    "\u4e0d\u719f",
    "\u4e0d\u77e5\u9053\u4ed6",
    "\u4e0d\u77e5\u9053\u5979",
    "\u4e0d\u6e05\u695a\u4ed6",
    "\u4e0d\u6e05\u695a\u5979",
    "\u4e0d\u597d\u8bc4\u4ef7",
    "\u4e0d\u4fbf\u8bc4\u4ef7",
)
TARGET_ROAST_FALLBACK_TEMPLATES = (
    "{target}\u8fd9\u5b58\u5728\u611f\u50cf\u5f00\u4e86\u9759\u97f3\u8fd8\u7ad9C\u4f4d\u3002",
    "{target}\u4e00\u88ab\u70b9\u540d\uff0c\u5f39\u5e55\u90fd\u50cf\u5728\u7b49\u4ed6\u4ea4\u4f5c\u4e1a\u3002",
    "{target}\u8fd9\u540d\u5b57\u50cf\u521a\u4ece\u70b9\u540d\u518c\u4e0a\u9003\u51fa\u6765\u3002",
)
EXTERNAL_ACTION_PROMISE_TERMS = (
    "\u8fd9\u5c31\u641c",
    "\u6211\u8fd9\u5c31\u641c",
    "\u9a6c\u4e0a\u641c",
    "\u53bb\u641c",
    "\u6211\u53bb\u641c",
    "\u5e2e\u4f60\u641c",
    "\u641c\u5b8c",
    "\u641c\u7ed9\u4f60\u770b",
    "\u7ed9\u4f60\u770b",
    "\u8fd9\u5c31\u542c",
    "\u542c\u5b8c",
    "\u8fd9\u5c31\u770b",
    "\u770b\u5b8c",
    "\u6253\u5f00\u770b",
    "\u7b49\u6211\u641c",
    "\u7b49\u6211\u770b",
    "\u77e5\u9053\u4e86",
    "i will search",
    "i am searching",
    "let me search",
    "searching now",
    "i will watch",
    "i will listen",
)
EXTERNAL_ACTION_FALLBACK_TEMPLATES = (
    "{viewer}\uff0c\u6211\u4e0d\u88c5\u6b63\u5728\u641c\uff0c\u6b4c\u540d\u5148\u653e\u53f0\u4e0a\u3002",
    "{viewer}\uff0c\u771f\u641c\u5f97\u4eba\u7c7b\u52a8\u624b\uff0c\u732b\u732b\u5148\u4e0d\u7a7a\u5934\u652f\u7968\u3002",
    "{viewer}\uff0c\u8fd9\u4e2a\u6211\u4e0d\u5047\u88c5\u70b9\u5f00\uff0c\u7b49\u753b\u9762\u5230\u4e86\u518d\u8bc4\u3002",
)
INTERNAL_CONTEXT_LEAK_TERMS = (
    "\u63d2\u4ef6",
    "\u63d2\u4ef6\u8bf4",
    "\u5185\u90e8\u72b6\u6001",
    "\u7cfb\u7edf\u72b6\u6001",
    "\u63d0\u793a\u8bcd",
    "\u89c4\u5219\u8bf4",
    "\u7b56\u7565\u8bf4",
    "\u63d0\u793a\u91cc",
    "\u89c4\u5219\u91cc",
    "\u7cfb\u7edf\u8bf4",
    "\u5185\u90e8\u8bf4",
    "\u540e\u53f0\u8bf4",
    "\u6d41\u7a0b",
    "\u8c03\u8bd5\u4fe1\u606f",
    "\u8c03\u8bd5\u72b6\u6001",
    "solo\u76f4\u64ad",
    "solo\u76f4\u64ad\u65f6\u523b",
    "\u5b89\u9759\u7684solo",
    "\u76f4\u64ad\u65f6\u523b",
    "\u90fd\u53d1\u5f39\u5e55",
    "\u6709\u5565\u60f3\u804a",
    "plugin",
    "prompt",
    "system state",
    "internal state",
    "debug state",
)
DANMAKU_DISALLOWED_CONTEXT_TERMS = (
    "\u5934\u50cf",
    "\u5934\u56fe",
    "\u5934\u50cf\u91cc",
    "\u5934\u50cf\u4e0a",
    "\u6635\u79f0",
    "\u521d\u89c1",
    "\u7b2c\u4e00\u6b21\u6765",
    "\u6211\u8bb0\u5f97",
    "\u4e0a\u6b21\u4f60",
    "uid",
    "user id",
)
GREETING_REPLY_TERMS = (
    "\u665a\u4e0a\u597d",
    "\u665a\u597d",
    "\u65e9\u4e0a\u597d",
    "\u65e9\u597d",
    "\u4f60\u597d",
    "\u55e8",
    "hi",
    "hello",
)
GREETING_FALLBACK_REPLIES = (
    "\u665a\u4e0a\u597d\u5440",
    "\u665a\u597d\uff0c\u6765\u5f97\u6b63\u597d",
    "\u665a\u4e0a\u597d\uff0c\u732b\u732b\u5728\u7ebf",
)
HOST_HANDOFF_HELP_TERMS = (
    "\u5e2e\u6211",
    "\u5e2e\u672c\u55b5",
    "\u5e2e\u732b\u732b",
    "\u5feb\u5e2e",
    "\u6559\u6211",
    "\u7ed9\u6211",
    "\u5e2e\u5fd9",
)
HOST_HANDOFF_CONTEXT_TERMS = (
    "\u5f00\u5934",
    "\u5f00\u573a",
    "\u6696\u573a",
    "\u76f4\u64ad\u6696\u573a",
    "\u600e\u4e48\u8bf4",
    "\u8981\u600e\u4e48\u8bf4",
    "\u8bf4\u4ec0\u4e48",
    "\u8bdd\u9898",
    "\u63a5\u4e0b\u6765",
)


def normalize_text(text: str) -> str:
    return "".join(ch for ch in str(text or "").casefold() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def choose_fallback_reply(text: str, module: str, replies: tuple[str, ...]) -> str:
    if not replies:
        return ""
    seed = f"{module}\n{text}"
    index = zlib.crc32(seed.encode("utf-8")) % len(replies)
    return replies[index]


def looks_like_bland_danmaku_reply(text: str) -> bool:
    lowered = str(text or "").casefold()
    dense = normalize_text(lowered)
    return any(term.casefold() in lowered or term.casefold() in dense for term in BLAND_DANMAKU_REPLY_TERMS)


def looks_like_stale_comparison_template(text: str) -> bool:
    lowered = str(text or "").casefold()
    dense = normalize_text(lowered)
    if not dense:
        return False
    if any(term.casefold() in lowered or normalize_text(term) in dense for term in STALE_COMPARISON_REPLY_TERMS):
        return True
    has_cat_opinion_opening = "\u672c\u732b\u732b\u89c9\u5f97" in dense or "\u732b\u732b\u89c9\u5f97" in dense
    has_stale_comparison = "\u6bd4" in dense and any(token in dense for token in ("\u597d\u591a", "\u66f4\u597d", "\u66f4\u5f3a", "\u5389\u5bb3"))
    return has_cat_opinion_opening and has_stale_comparison


def looks_like_unfulfilled_content_request(text: str, metadata: Mapping[str, Any] | None) -> bool:
    if not isinstance(metadata, Mapping) or metadata.get("danmaku_profile") != "content_request":
        return False
    lowered = str(text or "").casefold().strip()
    dense = normalize_text(lowered)
    if not dense:
        return True
    has_promise = any(
        term.casefold() in lowered or normalize_text(term) in dense
        for term in UNFULFILLED_CONTENT_PROMISE_TERMS
    )
    if not has_promise:
        return False
    has_delivery = any(
        term.casefold() in lowered or normalize_text(term) in dense
        for term in CONTENT_DELIVERY_TERMS
    )
    remaining = dense
    for term in UNFULFILLED_CONTENT_PROMISE_TERMS:
        remaining = remaining.replace(normalize_text(term), "")
    has_substantive_tail = len(remaining) >= 6
    if has_delivery or has_substantive_tail:
        return False
    if len(dense) <= 24:
        return True
    if "\u7b11\u8bdd" in dense and not has_delivery:
        return True
    return "\u7b11\u8bdd" in dense and not any(mark in lowered for mark in ("\u3002", "\uff01", "!", "?"))


def _target_roast_fallback_target(metadata: Mapping[str, Any] | None) -> str:
    if not isinstance(metadata, Mapping):
        return "\u8fd9\u4f4d\u89c2\u4f17"
    value = metadata.get("danmaku_target_viewer_nickname")
    if not isinstance(value, str):
        return "\u8fd9\u4f4d\u89c2\u4f17"
    target = " ".join(value.strip().strip("@\uff20").split())[:12]
    return target or "\u8fd9\u4f4d\u89c2\u4f17"


def target_roast_fallback_reply(text: str, metadata: Mapping[str, Any] | None) -> str:
    target = _target_roast_fallback_target(metadata)
    template = choose_fallback_reply(text, "target_roast_request", TARGET_ROAST_FALLBACK_TEMPLATES)
    return template.format(target=target)


def looks_like_target_roast_dodge(text: str, metadata: Mapping[str, Any] | None) -> bool:
    if not isinstance(metadata, Mapping) or metadata.get("danmaku_profile") != "target_roast_request":
        return False
    lowered = str(text or "").casefold().strip()
    dense = normalize_text(lowered)
    if not dense:
        return True
    return any(term.casefold() in lowered or normalize_text(term) in dense for term in TARGET_ROAST_DODGE_TERMS)


def _external_action_viewer(metadata: Mapping[str, Any] | None) -> str:
    if not isinstance(metadata, Mapping):
        return "\u8fd9\u4f4d"
    value = metadata.get("danmaku_viewer_nickname")
    if not isinstance(value, str):
        return "\u8fd9\u4f4d"
    viewer = " ".join(value.strip().split())[:12]
    return viewer or "\u8fd9\u4f4d"


def external_action_fallback_reply(text: str, metadata: Mapping[str, Any] | None) -> str:
    viewer = _external_action_viewer(metadata)
    template = choose_fallback_reply(text, "external_action_request", EXTERNAL_ACTION_FALLBACK_TEMPLATES)
    return template.format(viewer=viewer)


def looks_like_external_action_promise(text: str, metadata: Mapping[str, Any] | None) -> bool:
    if not isinstance(metadata, Mapping) or metadata.get("danmaku_profile") != "external_action_request":
        return False
    lowered = str(text or "").casefold().strip()
    dense = normalize_text(lowered)
    if not dense:
        return True
    return any(term.casefold() in lowered or normalize_text(term) in dense for term in EXTERNAL_ACTION_PROMISE_TERMS)


def looks_like_disallowed_danmaku_context(text: str) -> bool:
    lowered = str(text or "").casefold()
    dense = normalize_text(lowered)
    return any(term.casefold() in lowered or normalize_text(term) in dense for term in DANMAKU_DISALLOWED_CONTEXT_TERMS)


def looks_like_internal_context_leak(text: str) -> bool:
    lowered = str(text or "").casefold()
    dense = normalize_text(lowered)
    return any(term.casefold() in lowered or normalize_text(term) in dense for term in INTERNAL_CONTEXT_LEAK_TERMS)


def looks_like_greeting_reply(text: str) -> bool:
    lowered = str(text or "").casefold()
    dense = normalize_text(lowered)
    return any(term.casefold() in lowered or normalize_text(term) in dense for term in GREETING_REPLY_TERMS)


def looks_like_overused_live_template(text: str) -> bool:
    lowered = str(text or "").casefold()
    dense = normalize_text(lowered)
    return any(term.casefold() in lowered or normalize_text(term) in dense for term in OVERUSED_LIVE_TEMPLATE_TERMS)


def looks_like_host_handoff_to_human(text: str) -> bool:
    lowered = str(text or "").casefold()
    dense = normalize_text(lowered)
    if not dense:
        return False
    has_help = any(normalize_text(term) in dense for term in HOST_HANDOFF_HELP_TERMS)
    has_context = any(normalize_text(term) in dense for term in HOST_HANDOFF_CONTEXT_TERMS)
    if has_help and has_context:
        return True
    asks_how_to_say = any(token in dense for token in ("\u600e\u4e48\u8bf4", "\u8981\u600e\u4e48\u8bf4", "\u8bf4\u4ec0\u4e48"))
    host_context = any(token in dense for token in ("\u5f00\u5934", "\u5f00\u573a", "\u6696\u573a", "\u76f4\u64ad"))
    return asks_how_to_say and host_context


def needs_pretrim_quality_fallback(text: str, metadata: Mapping[str, Any] | None) -> bool:
    module = response_module(metadata)
    if module in {"avatar_roast", "danmaku_response"} and looks_like_unverified_support_thanks(text, metadata):
        return True
    if module in HOST_MODULES and looks_like_host_handoff_to_human(text):
        return True
    return False


def safe_fallback_reply(text: str, metadata: Mapping[str, Any] | None) -> str:
    module = response_module(metadata)
    if module in {"avatar_roast", "danmaku_response"} and looks_like_unverified_support_thanks(text, metadata):
        return choose_fallback_reply(text, module, UNVERIFIED_SUPPORT_CLAIM_FALLBACK_REPLIES)
    if module in HOST_MODULES and looks_like_host_handoff_to_human(text):
        return choose_fallback_reply(text, module, HOST_HANDOFF_FALLBACK_REPLIES)
    if looks_like_stage_direction_output(text) or looks_like_owner_memory_leak(text) or looks_like_internal_context_leak(text):
        return choose_fallback_reply(text, module, LIVE_SPEECH_HYGIENE_FALLBACK_REPLIES)
    if module == "danmaku_response" and isinstance(metadata, Mapping) and metadata.get("danmaku_profile") == "greeting":
        return choose_fallback_reply(text, module, GREETING_FALLBACK_REPLIES)
    if module == "danmaku_response" and looks_like_disallowed_danmaku_context(text):
        return choose_fallback_reply(text, module, LIVE_SPEECH_HYGIENE_FALLBACK_REPLIES)
    if module == "danmaku_response" and looks_like_external_action_promise(text, metadata):
        return external_action_fallback_reply(text, metadata)
    if module == "danmaku_response" and looks_like_target_roast_dodge(text, metadata):
        return target_roast_fallback_reply(text, metadata)
    if module == "danmaku_response" and looks_like_unfulfilled_content_request(text, metadata):
        return choose_fallback_reply(text, module, CONTENT_REQUEST_FALLBACK_REPLIES)
    if module == "danmaku_response" and looks_like_stale_comparison_template(text):
        return choose_fallback_reply(text, module, STALE_COMPARISON_FALLBACK_REPLIES)
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
    dense = normalize_text(lowered)
    has_drift_topic = any(term.casefold() in lowered or term.casefold() in dense for term in OPAQUE_TOPIC_DRIFT_TERMS)
    if not has_drift_topic:
        return False
    return any(marker.casefold() in lowered or marker.casefold() in dense for marker in OPAQUE_QUESTION_MARKERS)


def host_prompt_signal_count(normalized_text: str) -> int:
    return sum(1 for token in HOST_AUDIENCE_PROMPT_TOKENS if normalize_text(token) in normalized_text)


def looks_like_numeric_audience_cta(text: str) -> bool:
    return bool(NUMERIC_AUDIENCE_CTA_RE.search(str(text or "")))


def looks_like_unverified_support_thanks(text: str, metadata: Mapping[str, Any] | None) -> bool:
    if not isinstance(metadata, Mapping):
        return False
    if metadata.get("viewer_claimed_support") != "unverified_danmaku_claim":
        return False
    lowered = str(text or "").casefold()
    dense = normalize_text(lowered)
    return any(term.casefold() in lowered or normalize_text(term) in dense for term in UNVERIFIED_SUPPORT_THANK_TERMS)


def needs_quality_fallback(text: str, metadata: Mapping[str, Any] | None) -> bool:
    lowered = str(text or "").casefold()
    if looks_like_stage_direction_output(text):
        return True
    if looks_like_owner_memory_leak(text):
        return True
    if looks_like_internal_context_leak(text):
        return True
    if any(term.casefold() in lowered for term in FORBIDDEN_OUTPUT_TERMS):
        return True
    if looks_like_opaque_topic_drift(text):
        return True
    module = response_module(metadata)
    if module in {"avatar_roast", "danmaku_response"} and looks_like_unverified_support_thanks(text, metadata):
        return True
    if module == "danmaku_response" and looks_like_external_action_promise(text, metadata):
        return True
    if module == "danmaku_response" and looks_like_target_roast_dodge(text, metadata):
        return True
    if module == "danmaku_response" and looks_like_unfulfilled_content_request(text, metadata):
        return True
    if module == "danmaku_response" and looks_like_stale_comparison_template(text):
        return True
    if module == "danmaku_response" and looks_like_bland_danmaku_reply(text):
        return True
    if module == "danmaku_response" and looks_like_disallowed_danmaku_context(text):
        return True
    if module in HOST_MODULES and looks_like_host_handoff_to_human(text):
        return True
    if module in HOST_MODULES | {"danmaku_response"} and looks_like_overused_live_template(text):
        return True
    if module in HOST_MODULES | {"danmaku_response"} and looks_like_numeric_audience_cta(text):
        return True
    if (
        module == "danmaku_response"
        and isinstance(metadata, Mapping)
        and metadata.get("danmaku_profile") == "greeting"
        and not looks_like_greeting_reply(text)
    ):
        return True
    if module in HOST_MODULES and any(term.casefold() in lowered for term in LOW_CONFIDENCE_HOST_TERMS):
        return True
    if module in HOST_MODULES and host_prompt_signal_count(normalize_text(text)) >= 1:
        return True
    return False
