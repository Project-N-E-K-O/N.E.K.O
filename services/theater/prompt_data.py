"""Lossless prompt data framing shared by the workshop SDK and theater."""

from __future__ import annotations

import json
from typing import Any, Mapping


def prompt_data_block(label: str, data: Mapping[str, Any]) -> str:
    """Fence lossless JSON data without allowing values to forge block delimiters."""

    # Keep evidence and player input reversible; destructive sanitizers would
    # change the original strings against which quote validation operates.
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("=", r"\u003d").replace("<", r"\u003c").replace(">", r"\u003e")
    return f"======以下为{label}======\n{payload}\n======以上为{label}======"


def prompt_data_block_payload(text: str, label: str) -> dict[str, Any]:
    """Read one block back out of prompt_data_block output, reversing its delimiter escaping."""

    # Match the markers without their surrounding newlines so CRLF text reads
    # back too; json.loads ignores the leading/trailing whitespace that leaves
    # behind, and turns the renderer's escaped delimiter characters back into
    # the originals.
    opening = f"======以下为{label}======"
    closing = f"======以上为{label}======"
    start = text.find(opening)
    if start < 0:
        raise KeyError(label)
    start += len(opening)
    end = text.find(closing, start)
    if end < 0:
        raise KeyError(label)
    return json.loads(text[start:end])
