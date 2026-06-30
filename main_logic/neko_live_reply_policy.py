"""NEKO Live output policy shared with the host callback boundary.

This module owns the plugin-specific reply contract: route ceilings, host
two-sentence allowance, quality fallback, and callback metadata merging.
``main_logic.core`` should call into this boundary instead of carrying the
NEKO Live policy inline.
"""

from __future__ import annotations

import re
import zlib
from collections.abc import Mapping
from typing import Any


REPLY_TARGET_CHARS = 14
DEFAULT_DISPATCH_REPLY_CHARS = 28
DISPATCH_REPLY_CHAR_LIMITS = {
    "warmup_hosting": 56,
    "idle_hosting": 64,
    "active_engagement": 72,
}
ROUTE_CEILINGS = {
    "avatar_roast": 32,
    "danmaku_response": 28,
    "warmup_hosting": 56,
    "idle_hosting": 64,
    "active_engagement": 72,
}
ROUTE_NOTES = {
    "avatar_roast": (
        "For avatar_roast: connect the viewer's first message with the avatar/name, "
        "but keep it as one sharp first-appearance line."
    ),
    "danmaku_response": (
        "For danmaku_response: answer only the current danmaku; do not mention avatar, "
        "ID, first appearance, or previous replies."
    ),
    "warmup_hosting": (
        "For warmup_hosting: usually say one small opening line; if the beat is charming, "
        "two short sentences are allowed."
    ),
    "idle_hosting": (
        "For idle_hosting: make one small hosting beat. It can occasionally be a tiny two-sentence "
        "aside, but not a full monologue or survey."
    ),
    "active_engagement": (
        "For active_engagement: offer one concrete reply hook; do not say generic phrases "
        "like everyone interact or tell me what you want. If the topic is technical, "
        "game-specific, a guide/tutorial, or unfamiliar, make only a small surface reaction "
        "instead of inventing an expert A/B question. If the material is genuinely fun, "
        "a tiny two-sentence riff is allowed."
    ),
}
HOST_MODULES = {"warmup_hosting", "idle_hosting", "active_engagement"}
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


def is_live_reply_metadata(metadata: Mapping[str, Any] | None) -> bool:
    return isinstance(metadata, Mapping) and metadata.get("live_reply_contract") == "short_tts_line"


def max_reply_chars_for_module(module: str) -> int:
    return DISPATCH_REPLY_CHAR_LIMITS.get(str(module or "").strip(), DEFAULT_DISPATCH_REPLY_CHARS)


def build_reply_metadata(
    *,
    uid: str,
    live_mode: str,
    response_module_hint: str,
    demo: bool = False,
) -> dict[str, Any]:
    return {
        "plugin": "neko_roast",
        "uid": uid,
        "live_mode": live_mode,
        "demo": bool(demo),
        "live_reply_contract": "short_tts_line",
        "max_reply_chars": max_reply_chars_for_module(response_module_hint),
        "response_module_hint": response_module_hint,
    }


def coerce_live_reply_limit(value: Any) -> int | None:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return None
    if limit <= 0:
        return None
    return min(limit, 80)


def reply_limit_from_metadata(metadata: Mapping[str, Any] | None) -> int | None:
    if not is_live_reply_metadata(metadata):
        return None
    module = response_module(metadata)
    metadata_limit = coerce_live_reply_limit((metadata or {}).get("max_reply_chars"))
    module_limit = ROUTE_CEILINGS.get(module)
    candidates = [value for value in (metadata_limit, module_limit) if value]
    return min(candidates) if candidates else None


def response_module(metadata: Mapping[str, Any] | None) -> str:
    if not isinstance(metadata, Mapping):
        return ""
    return str(metadata.get("response_module_hint") or "").strip()


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


def first_sentences(text: str, budget: int = 1) -> tuple[str, bool]:
    cleaned = " ".join(str(text or "").replace("\r", "\n").split())
    if not cleaned:
        return "", False
    budget = max(1, int(budget or 1))
    seen = 0
    for index, char in enumerate(cleaned):
        if char in "。！？!?":
            seen += 1
            if seen >= budget:
                first = cleaned[: index + 1].strip()
                return first, first != cleaned
    return cleaned, False


def shape_reply_text(text: str, metadata: dict | None) -> tuple[str, dict | None]:
    outgoing_metadata = dict(metadata) if isinstance(metadata, dict) else metadata
    if not is_live_reply_metadata(outgoing_metadata):
        return text, outgoing_metadata
    if outgoing_metadata.get("neko_live_reply_shaped") is True:
        return str(text or "").strip(), outgoing_metadata
    limit = reply_limit_from_metadata(outgoing_metadata)
    if not limit:
        return text, outgoing_metadata

    raw = str(text or "")
    original = raw.strip()
    budget = sentence_budget(outgoing_metadata)
    selected_sentences, clipped_sentence = first_sentences(original, budget)
    shaped = selected_sentences or original
    clipped_length = False
    if len(shaped) > limit:
        shaped = shaped[:limit].rstrip(" ，,、；;：:")
        clipped_length = True
    shaped = shaped.strip()
    shaped, clipped_dangling_choice = trim_dangling_choice(shaped)
    used_quality_fallback = False
    if needs_quality_fallback(shaped, outgoing_metadata):
        fallback = safe_fallback_reply(shaped, outgoing_metadata)
        shaped = fallback[:limit].rstrip(" ，,、；;：:").strip()
        used_quality_fallback = True

    if shaped and shaped != original:
        outgoing_metadata["neko_live_reply_shaped"] = True
        outgoing_metadata["neko_live_reply_original_chars"] = len(original)
        outgoing_metadata["neko_live_reply_output_chars"] = len(shaped)
        reasons = []
        if clipped_sentence:
            reasons.append("first_sentences" if budget > 1 else "first_sentence")
        if clipped_length:
            reasons.append("max_reply_chars")
        if clipped_dangling_choice:
            reasons.append("dangling_choice")
        if used_quality_fallback:
            reasons.append("quality_fallback")
        outgoing_metadata["neko_live_reply_shape_reason"] = "+".join(reasons) or "short_tts_line"
        return shaped, outgoing_metadata
    if outgoing_metadata is not None:
        outgoing_metadata["neko_live_reply_shaped"] = False
        outgoing_metadata["neko_live_reply_output_chars"] = len(original)
    return raw, outgoing_metadata


def coerce_recent_reply_values(recent_live_replies: Any) -> list[str]:
    if not recent_live_replies:
        return []
    if isinstance(recent_live_replies, Mapping):
        source = recent_live_replies.values()
    else:
        try:
            source = list(recent_live_replies)
        except TypeError:
            source = [recent_live_replies]
    values: list[str] = []
    for reply in source:
        text = str(reply or "").strip()
        if text:
            values.append(text)
    return values


def render_recent_reply_avoidance(recent_live_replies: list[str] | None) -> list[str]:
    recent_reply_values = coerce_recent_reply_values(recent_live_replies)
    if not recent_reply_values:
        return []
    lines = [
        "- Recent NEKO Live outputs below are negative examples; do not continue or paraphrase them.",
    ]
    for reply in recent_reply_values[-RECENT_REPLY_AVOIDANCE_SIZE:]:
        text = str(reply or "").strip().replace("\n", " ")
        if not text:
            continue
        if len(text) > 48:
            text = text[:48].rstrip() + "..."
        lines.append(f"  - Avoid repeating: {text}")
    if len(lines) == 1:
        return []
    lines.append("- Answer the current live event from a fresh angle even if the topic is similar.")
    return lines


def render_contract_instruction(
    callbacks: list[dict],
    *,
    recent_live_replies: list[str] | None = None,
) -> str:
    modules: list[str] = []
    absolute_limit: int | None = None

    for cb in callbacks:
        metadata = cb.get("metadata")
        if not isinstance(metadata, Mapping):
            continue
        if metadata.get("live_reply_contract") != "short_tts_line":
            continue

        module = response_module(metadata)
        if module and module not in modules:
            modules.append(module)

        metadata_limit = coerce_live_reply_limit(metadata.get("max_reply_chars"))
        module_limit = ROUTE_CEILINGS.get(module)
        limit_candidates = [value for value in (metadata_limit, module_limit) if value]
        if limit_candidates:
            callback_limit = min(limit_candidates)
            absolute_limit = callback_limit if absolute_limit is None else min(absolute_limit, callback_limit)

    if not modules and absolute_limit is None:
        return ""

    host_only = bool(modules) and all(module in HOST_MODULES for module in modules)
    if absolute_limit is None:
        absolute_limit = 64 if host_only else REPLY_TARGET_CHARS
    target_limit = min(36 if host_only else REPLY_TARGET_CHARS, absolute_limit)
    module_notes = [ROUTE_NOTES[module] for module in modules if module in ROUTE_NOTES]

    lines = [
        "",
        "NEKO Live short output contract:",
        f"- Target at most {target_limit} Chinese characters; absolute ceiling {absolute_limit}.",
        (
            "- Host modules may use one or two short sentences when the beat is genuinely fun; no paragraph."
            if host_only
            else "- Output exactly one sentence, one breath, no paragraph."
        ),
        "- Do not continue, summarize, or imitate the previous NEKO reply.",
        "- Treat previous NEKO Live outputs as forbidden material, not conversation context to resume.",
        "- If the draft sounds like the previous NEKO reply, change the angle before output.",
        "- Do not reuse the previous reply's opening words, sentence rhythm, punchline, or host beat.",
        (
            "- If a host draft has two tiny connected ideas, keep both only when the second adds charm."
            if host_only
            else "- If a draft has two ideas, keep only the sharper one."
        ),
        "- Do not use host-script openings such as special plan, next let's, everyone look, or tell me what you want.",
        "- Do not use empty praise such as has a vibe, interesting, has a joke, 有点意思, 有点东西, or 很有梗.",
        "- Do not use 喵 as the whole punchline or a default suffix; the line still needs one concrete live-room point.",
        "- Do not invent a punishment, public-shaming, trial, labor-camp, report, or moral judgment bit.",
        "- Forbidden words: 公开示众, 劳改, 审判, 处刑, 惩罚.",
        "- Do not force technical, game-specific, guide, tutorial, or news material into an unclear expert question.",
        "- Never end with an unfinished choice such as 还是, 或者, or or.",
    ]
    lines.extend(render_recent_reply_avoidance(recent_live_replies))
    lines.extend(f"- {note}" for note in module_notes)
    return "\n".join(lines)


def merge_metadata_from_callbacks(callbacks: list[dict]) -> dict[str, Any] | None:
    """Carry NEKO Live reply metadata into generated callback output."""
    merged: dict[str, Any] | None = None
    modules: list[str] = []
    absolute_limit: int | None = None

    for cb in callbacks:
        metadata = cb.get("metadata")
        if not isinstance(metadata, Mapping):
            continue
        if metadata.get("live_reply_contract") != "short_tts_line":
            continue
        if merged is None:
            merged = dict(metadata)

        module = response_module(metadata)
        if module and module not in modules:
            modules.append(module)

        metadata_limit = coerce_live_reply_limit(metadata.get("max_reply_chars"))
        module_limit = ROUTE_CEILINGS.get(module)
        limit_candidates = [value for value in (metadata_limit, module_limit) if value]
        if limit_candidates:
            callback_limit = min(limit_candidates)
            absolute_limit = callback_limit if absolute_limit is None else min(absolute_limit, callback_limit)

    if merged is None:
        return None

    if modules:
        merged["response_module_hint"] = modules[0] if len(modules) == 1 else "mixed"
    if absolute_limit is not None:
        merged["max_reply_chars"] = absolute_limit
    return merged
