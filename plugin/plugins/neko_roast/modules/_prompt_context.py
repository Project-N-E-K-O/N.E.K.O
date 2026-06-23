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
        + "Continuity rule: Do not reuse the same opening, punchline shape, or host beat from the recent context.\n"
    )
