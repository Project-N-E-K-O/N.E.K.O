"""Text compaction helpers for recent live-context memory."""

from __future__ import annotations


def compact_context_text(value: str, *, limit: int = 80) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."
