"""Heuristic token estimates for galgame prompt context budgeting."""

from __future__ import annotations

import json
import math
from typing import Any


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x3040 <= code <= 0x30FF
        or 0xAC00 <= code <= 0xD7AF
    )


def count_tokens_heuristic(text: str) -> int:
    """Estimate token count from character classes without external tokenizers.

    The weights intentionally bias CJK and non-ASCII text upward for prompt
    safety: CJK characters count as about 1.5 tokens, ASCII as about 0.25
    tokens, and other Unicode characters as 1 token.
    """
    if not text:
        return 0
    total = 0.0
    for ch in text:
        if _is_cjk(ch):
            total += 1.5
        elif ord(ch) < 128:
            total += 0.25
        else:
            total += 1.0
    return int(math.ceil(total))


def truncate_tokens_heuristic(
    text: str,
    max_tokens: int,
    *,
    notice_template: str | None = None,
) -> str:
    """Return the longest prefix that fits the heuristic token budget."""
    if max_tokens <= 0 or not text:
        return ""
    if count_tokens_heuristic(text) <= max_tokens:
        return text

    def candidate(prefix_len: int) -> str:
        prefix = text[:prefix_len]
        if notice_template is None:
            return prefix
        omitted = len(text) - prefix_len
        return f"{prefix}{notice_template.format(omitted=omitted)}"

    best = ""
    low = 0
    high = len(text) - 1
    while low <= high:
        mid = (low + high) // 2
        clipped = candidate(mid)
        if count_tokens_heuristic(clipped) <= max_tokens:
            best = clipped
            low = mid + 1
        else:
            high = mid - 1

    if best or notice_template is None:
        return best
    return truncate_tokens_heuristic(text, max_tokens)


def estimate_context_tokens(context: dict[str, Any]) -> int:
    """Estimate tokens for a context dict using the prompt JSON representation."""
    rendered = json.dumps(context, ensure_ascii=False, indent=2, default=str)
    return count_tokens_heuristic(rendered)
