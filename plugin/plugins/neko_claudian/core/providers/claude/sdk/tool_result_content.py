"""
1:1 ported from claudian/src/providers/claude/sdk/toolResultContent.ts

工具结果内容解析。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Union


def parse_tool_result_content(content: Any) -> List[Dict[str, Any]]:
    """
    把 tool_result.content 解析为统一的内容块列表。
    可能是字符串 / 列表（text / image blocks） / None。
    """
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [blk for blk in content if isinstance(blk, dict)]
    if isinstance(content, dict):
        return [content]
    return [{"type": "text", "text": str(content)}]


def extract_text_from_tool_result(content: Any) -> str:
    """从 tool_result.content 提取纯文本。"""
    blocks = parse_tool_result_content(content)
    parts: List[str] = []
    for blk in blocks:
        if blk.get("type") == "text":
            parts.append(blk.get("text", ""))
    return "\n".join(parts)


def is_error_tool_result(block: Dict[str, Any]) -> bool:
    return bool(block.get("is_error"))


def normalize_tool_result(block: Dict[str, Any]) -> Dict[str, Any]:
    """
    规范化 tool_result block，缺失字段填默认值。
    """
    return {
        "type": "tool_result",
        "tool_use_id": block.get("tool_use_id", ""),
        "content": block.get("content", ""),
        "is_error": block.get("is_error", False),
    }
