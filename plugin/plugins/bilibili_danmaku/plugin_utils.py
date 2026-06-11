"""
弹幕插件通用工具集

提取共享的工具函数，减少代码重复。
"""

from __future__ import annotations

import json
from typing import Optional


def extract_json(text: str) -> Optional[dict]:
    """从 LLM 输出中提取 JSON

    支持：
    - 纯 JSON 文本
    - ```json ... ``` 代码块
    - ``` ... ``` 代码块
    - 大括号包围的内容
    """
    text = text.strip()

    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试从 ```json ... ``` 中提取
    markers = ["```json", "```"]
    for marker in markers:
        if marker in text:
            start = text.index(marker) + len(marker)
            end = text.index("```", start) if "```" in text[start:] else len(text)
            try:
                return json.loads(text[start:end].strip())
            except (json.JSONDecodeError, ValueError):
                continue

    # 尝试从大括号中提取
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start >= 0 and brace_end > brace_start:
        try:
            return json.loads(text[brace_start: brace_end + 1])
        except (json.JSONDecodeError, ValueError):
            pass

    return None
