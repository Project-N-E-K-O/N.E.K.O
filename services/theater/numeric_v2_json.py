"""Shared JSON-output helpers for Numeric v2 model calls."""

from __future__ import annotations

from typing import Any


def strip_single_json_fence(content: Any) -> Any:
    """Unwrap exactly one complete ```json (or bare ```) fence around a model reply.

    Only a reply whose first line is the opening fence and whose last line is the
    closing fence is unwrapped. Prose around the fence, several fences, other
    languages and unterminated fences are returned unchanged so the caller's strict
    JSON parsing still rejects them; nothing is repaired or extracted. Non-string
    input is returned as-is.
    """

    if not isinstance(content, str):
        return content
    lines = content.strip().splitlines()
    if len(lines) >= 3 and lines[0].lower() in {"```json", "```"} and lines[-1] == "```":
        return "\n".join(lines[1:-1])
    return content


__all__ = ["strip_single_json_fence"]
