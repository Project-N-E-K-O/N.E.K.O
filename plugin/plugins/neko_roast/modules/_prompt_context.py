"""Prompt helpers shared by live interaction modules."""

from __future__ import annotations

from typing import Any


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
        + "Do not reuse the same opening, punchline shape, or host beat from the recent context.\n"
        + "The current danmaku is always the primary target. Short danmaku should receive a short reply.\n"
    )
