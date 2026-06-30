"""Final text shaping for NEKO Live replies."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .live_reply_contracts import HOST_MODULES, is_live_reply_metadata, reply_limit_from_metadata, response_module
from .live_reply_fallbacks import (
    needs_quality_fallback,
    safe_fallback_reply,
    sentence_budget,
    trim_dangling_choice,
)


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

