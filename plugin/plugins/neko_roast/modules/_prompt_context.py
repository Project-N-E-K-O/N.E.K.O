"""Prompt helpers shared by live interaction modules."""

from __future__ import annotations

from typing import Any


SHORT_REPLY_CONTRACT = "Hard length limit: one sentence, no paragraph, at most 14 Chinese characters or 8 English words."


def short_reply_rules() -> list[str]:
    return [
        SHORT_REPLY_CONTRACT,
        "If the viewer's danmaku is short, answer even shorter.",
        "For one-word or very short danmaku, answer with a tiny reaction.",
        "One breath only: no more than 20 Chinese chars or 10 English words when the idea still works.",
        "If recent context was longer than the current danmaku, shrink the reply instead of matching it.",
        "Prefer a compact live punchline over explanation, setup, or follow-up commentary.",
        "Do not turn a reply into a host script, segment intro, plan, or audience survey.",
        "Do not chain multiple clauses with commas; if the draft has a comma, cut one side.",
        "Avoid phrases like special plan, everyone look, next let's, what should we talk about, or tell me what you want.",
        "No explanation, no setup, no second sentence, no follow-up question unless the current danmaku asks one.",
    ]


def recent_context_block(ctx: Any, *, limit: int = 3) -> str:
    provider = getattr(ctx, "recent_interaction_context", None)
    if not callable(provider):
        return ""
    try:
        raw_lines = provider(limit=limit)
    except TypeError:
        raw_lines = provider()
    except Exception:
        return ""
    if not isinstance(raw_lines, list):
        return ""
    lines = [str(line).strip() for line in raw_lines if str(line).strip()]
    if not lines:
        return ""
    return (
        "Recent live context:\n"
        + "\n".join(f"- {line}" for line in lines[:limit])
        + "\n\n"
        + "Continuity rule: Use recent context only to avoid repetition.\n"
        + "Do not continue the previous reply, inherit the previous topic, or reuse the same opening, punchline shape, or host beat.\n"
        + "Do not inherit the previous answer's topic, rhythm, or sentence length.\n"
        + "Do not continue prizes, plans, games, or audience-suggestion beats from the previous reply.\n"
        + "Do not reuse the same opening, punchline shape, or host beat from the recent context.\n"
        + "Current danmaku wins over recent context.\n"
        + "The current danmaku is always the primary target. Short danmaku should receive a short reply.\n"
    )
