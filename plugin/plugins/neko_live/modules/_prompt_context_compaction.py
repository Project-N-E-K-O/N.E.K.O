"""Compaction helpers for prompt context lines."""

from __future__ import annotations

from typing import Any


NEKO_ALREADY_SAID_MARKER = " / NEKO already said: "
REPLY_PATH_MARKER = " / reply: "
SPENT_OUTPUT_FAMILY_MARKER = " / spent_output_family="


def compact_context_line(value: Any, *, limit: int) -> str:
    text = str(value or "").strip().replace("\r", " ").replace("\n", " ")
    text = " ".join(text.split())
    if NEKO_ALREADY_SAID_MARKER in text:
        context, output = text.split(NEKO_ALREADY_SAID_MARKER, 1)
        output = compact_plain(output, limit=max(16, min(44, limit)))
        context = compact_preserving_reply_path(context, limit=max(16, limit - len(output) - 22))
        text = f"NEKO already said: {output}"
        if context:
            text += f" / context: {context}"
    if len(text) <= limit:
        return text
    return compact_preserving_reply_path(text, limit=limit)


def compact_preserving_reply_path(text: str, *, limit: int) -> str:
    if REPLY_PATH_MARKER not in text:
        return compact_preserving_spent_output_family(text, limit=limit)
    context, reply = text.split(REPLY_PATH_MARKER, 1)
    reply_limit = max(16, min(36, limit - 14))
    reply = compact_plain(reply, limit=reply_limit)
    context_limit = max(8, limit - len(reply) - len(REPLY_PATH_MARKER))
    context = compact_preserving_spent_output_family(context, limit=context_limit)
    if context:
        return f"{context}{REPLY_PATH_MARKER}{reply}"
    return f"reply: {reply}"


def compact_preserving_spent_output_family(text: str, *, limit: int) -> str:
    if SPENT_OUTPUT_FAMILY_MARKER not in text:
        return compact_plain(text, limit=limit)
    context, family = text.split(SPENT_OUTPUT_FAMILY_MARKER, 1)
    family = compact_plain(family, limit=max(12, min(32, limit - 14)))
    context_limit = max(8, limit - len(family) - len(SPENT_OUTPUT_FAMILY_MARKER))
    context = compact_plain(context, limit=context_limit)
    if context:
        return f"{context}{SPENT_OUTPUT_FAMILY_MARKER}{family}"
    return f"spent_output_family={family}"


def compact_plain(text: str, *, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."
