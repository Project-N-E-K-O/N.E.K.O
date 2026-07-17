"""Shape final NEKO Live output text to the plugin-owned reply contract."""

from __future__ import annotations

import re

from .live_output_quality import (
    looks_like_unfulfilled_content_request,
    needs_pretrim_quality_fallback,
    needs_quality_fallback,
    safe_fallback_reply,
)
from .live_reply_contract import (
    HOST_MODULES,
    is_longer_danmaku_reply,
    is_live_reply_metadata,
    reply_limit_from_metadata,
    response_module,
)


DANGLING_CHOICE_RE = re.compile(
    r"(?i)([\uff0c,\u3001\uff1b;]\s*(?:\u8fd8\u662f|\u6216\u8005|\u6216\u662f|\u8981\u4e48|or)\s*[^\uff0c,\u3001\uff1b;\u3002.!?\uff01\uff1f]{0,8})$"
)
STAGE_DIRECTION_RE = re.compile(r"[\(\uff08\u3010\[]\s*([^\(\)\uff08\uff09\u3010\u3011\[\]]{1,48})\s*[\)\uff09\u3011\]]")
STAGE_DIRECTION_MARKERS = (
    "\u955c\u5934",
    "\u5bf9\u7740",
    "\u62ac\u722a",
    "\u6325\u722a",
    "\u6446\u722a",
    "\u722a\u5b50",
    "\u8bed\u6c14",
    "\u4fcf\u76ae",
    "\u7728\u773c",
    "\u7b11",
    "\u6b6a\u5934",
    "\u70b9\u5934",
    "\u52a8\u4f5c",
    "camera",
    "pose",
    "gesture",
    "tone",
)


def trim_dangling_choice(text: str) -> tuple[str, bool]:
    cleaned = str(text or "").strip()
    if not cleaned:
        return "", False
    match = DANGLING_CHOICE_RE.search(cleaned)
    if match:
        trimmed = cleaned[: match.start()].rstrip(" \uff0c,\u3001\uff1b;\u3002.!?\uff01\uff1f")
        if trimmed:
            return trimmed, True
    for suffix in ("\u8fd8\u662f", "\u6216\u8005", "\u6216\u662f", "\u8981\u4e48", "or"):
        if cleaned.casefold().endswith(suffix.casefold()):
            trimmed = cleaned[: -len(suffix)].rstrip(" \uff0c,\u3001\uff1b;\u3002.!?\uff01\uff1f")
            if trimmed:
                return trimmed, True
    return cleaned, False


def strip_stage_directions(text: str) -> tuple[str, bool]:
    cleaned = str(text or "").strip()
    if not cleaned:
        return "", False
    removed = False

    def _replace(match: re.Match[str]) -> str:
        nonlocal removed
        inner = match.group(1).strip()
        dense = _normalize_visible_target(inner)
        if any(marker.casefold() in inner.casefold() or _normalize_visible_target(marker) in dense for marker in STAGE_DIRECTION_MARKERS):
            removed = True
            return ""
        return match.group(0)

    stripped = STAGE_DIRECTION_RE.sub(_replace, cleaned)
    stripped = " ".join(stripped.split()).strip()
    stripped = stripped.lstrip(" \uff0c,\u3001\uff1b;\u3002.!?\uff01\uff1f")
    return stripped, removed


def sentence_budget(metadata: dict | None) -> int:
    module = response_module(metadata)
    return 2 if module in HOST_MODULES or is_longer_danmaku_reply(metadata) else 1


def first_sentences(text: str, budget: int = 1) -> tuple[str, bool]:
    cleaned = " ".join(str(text or "").replace("\r", "\n").split())
    if not cleaned:
        return "", False
    budget = max(1, int(budget or 1))
    seen = 0
    for index, char in enumerate(cleaned):
        if char in "\u3002\uff01\uff1f!?":
            seen += 1
            if seen >= budget:
                first = cleaned[: index + 1].strip()
                return first, first != cleaned
    return cleaned, False


def _preserve_fulfilled_content(text: str, shaped: str, metadata: dict | None) -> tuple[str, bool]:
    if not looks_like_unfulfilled_content_request(shaped, metadata):
        return shaped, shaped != text
    if looks_like_unfulfilled_content_request(text, metadata):
        return shaped, shaped != text
    return text, False


def _clip_fulfilled_content(text: str, limit: int, metadata: dict | None) -> str:
    cleaned = str(text or "").strip()
    starts = [0]
    starts.extend(
        index + 1
        for index, char in enumerate(cleaned)
        if char in "，,；;。.!！？?"
    )
    for start in starts:
        candidate = cleaned[start:].lstrip(" ，,；;。.!！？?")
        clipped = candidate[:limit].rstrip(" ，,、；;。.!！？?")
        if clipped and not looks_like_unfulfilled_content_request(clipped, metadata):
            return clipped
    return cleaned[:limit].rstrip(" ，,、；;。.!！？?")


def _normalize_visible_target(value: str) -> str:
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def _danmaku_viewer_nickname(metadata: dict | None) -> str:
    if not isinstance(metadata, dict) or response_module(metadata) != "danmaku_response":
        return ""
    profile = str(metadata.get("danmaku_profile") or "").strip()
    if profile in {"empty", "emoji_or_reaction", "viewer_to_viewer_mention", "target_roast_request"}:
        return ""
    value = metadata.get("danmaku_viewer_nickname")
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split())[:12]


def ensure_danmaku_viewer_prefix(text: str, metadata: dict | None, *, limit: int) -> tuple[str, bool]:
    nickname = _danmaku_viewer_nickname(metadata)
    cleaned = str(text or "").strip()
    max_chars = max(0, int(limit or 0))
    if max_chars:
        cleaned = cleaned[:max_chars].rstrip(" \uff0c,\u3001\uff1b;\u3002.!?\uff01\uff1f")
    if not nickname or not cleaned:
        return cleaned, False
    dense_name = _normalize_visible_target(nickname)
    dense_text = _normalize_visible_target(cleaned[: max(len(nickname) + 8, 18)])
    if dense_name and dense_name in dense_text:
        return cleaned, False
    prefix = f"{nickname}\uff0c"
    available = max(0, max_chars - len(prefix))
    if available <= 0:
        return cleaned, False
    body = cleaned[:available].rstrip(" \uff0c,\u3001\uff1b;\u3002.!?\uff01\uff1f")
    return (prefix + body).strip(), True


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
    used_quality_fallback = False
    clipped_sentence = False
    if needs_pretrim_quality_fallback(original, outgoing_metadata):
        shaped = safe_fallback_reply(original, outgoing_metadata)
        used_quality_fallback = True
    else:
        shaped, clipped_sentence = first_sentences(original, budget)
        shaped = shaped or original
        shaped, clipped_sentence = _preserve_fulfilled_content(original, shaped, outgoing_metadata)
    shaped, clipped_stage_direction = strip_stage_directions(shaped)
    pre_length_shape = shaped
    fulfilled_before_length_clip = not looks_like_unfulfilled_content_request(
        shaped,
        outgoing_metadata,
    )
    clipped_length = False
    if len(shaped) > limit:
        shaped = shaped[:limit].rstrip(" \uff0c,\u3001\uff1b;\u3002.!?\uff01\uff1f")
        clipped_length = True
        if (
            fulfilled_before_length_clip
            and looks_like_unfulfilled_content_request(shaped, outgoing_metadata)
        ):
            shaped = _clip_fulfilled_content(pre_length_shape, limit, outgoing_metadata)
    shaped = shaped.strip()
    shaped, clipped_dangling_choice = trim_dangling_choice(shaped)
    if not used_quality_fallback and needs_quality_fallback(shaped, outgoing_metadata):
        fallback = safe_fallback_reply(shaped, outgoing_metadata)
        shaped = fallback[:limit].rstrip(" \uff0c,\u3001\uff1b;\u3002.!?\uff01\uff1f").strip()
        used_quality_fallback = True
    shaped, added_viewer_prefix = ensure_danmaku_viewer_prefix(shaped, outgoing_metadata, limit=limit)

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
        if clipped_stage_direction:
            reasons.append("stage_direction")
        if used_quality_fallback:
            reasons.append("quality_fallback")
        if added_viewer_prefix:
            reasons.append("viewer_prefix")
        outgoing_metadata["neko_live_reply_shape_reason"] = "+".join(reasons) or "short_tts_line"
        return shaped, outgoing_metadata
    if outgoing_metadata is not None:
        outgoing_metadata["neko_live_reply_shaped"] = False
        outgoing_metadata["neko_live_reply_output_chars"] = len(original)
    return raw, outgoing_metadata
