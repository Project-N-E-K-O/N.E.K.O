"""
1:1 ported from claudian/src/providers/claude/stream/toolInputStreamState.ts

跟踪 tool_use 的 input_json_delta 累积状态。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional


class ToolInputStreamState:
    """
    当 Claude 流式返回 input_json_delta 时，需要累积成完整 JSON。
    对应 Claudian 的 ToolInputStreamState。
    """

    def __init__(self):
        self._buffers: Dict[str, str] = {}  # tool_use_id -> partial json str

    def append_delta(self, tool_use_id: str, partial_json: str):
        if tool_use_id not in self._buffers:
            self._buffers[tool_use_id] = ""
        self._buffers[tool_use_id] += partial_json

    def get(self, tool_use_id: str) -> Optional[Dict[str, Any]]:
        raw = self._buffers.get(tool_use_id)
        if raw is None:
            return None
        raw = raw.strip()
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # 还不完整，返回 None
            return None

    def peek(self, tool_use_id: str) -> str:
        return self._buffers.get(tool_use_id, "")

    def has(self, tool_use_id: str) -> bool:
        return tool_use_id in self._buffers

    def clear(self, tool_use_id: str):
        self._buffers.pop(tool_use_id, None)

    def clear_all(self):
        self._buffers.clear()

    def size(self) -> int:
        return len(self._buffers)
