"""Prompt instructions and callback metadata merging for NEKO Live replies."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .neko_live_reply_contracts import (
    HOST_MODULES,
    REPLY_TARGET_CHARS,
    ROUTE_CEILINGS,
    ROUTE_NOTES,
    coerce_live_reply_limit,
    response_module,
)
from .neko_live_reply_fallbacks import RECENT_REPLY_AVOIDANCE_SIZE


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
