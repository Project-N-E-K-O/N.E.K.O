"""Claude Code Adapter — 流式 JSON 输出解析器。

Claude CLI 在 `--output-format stream-json --verbose` 模式下，
每行输出一个 JSON 事件。事件类型包括：

- system: 系统事件（含 session_id、cwd、version 等）
- assistant: assistant 消息（含 text / tool_use 块）
- result: 最终结果（含 cost、duration、num_turns 等）

本模块提供逐行解析能力，将原始 JSON 行转换为结构化事件。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .models import AssistantMessage


# ---------------------------------------------------------------------------
# 事件类型
# ---------------------------------------------------------------------------


@dataclass
class SystemEvent:
    """system 事件 — 通常是流的第一行。"""

    session_id: str = ""
    cwd: str = ""
    version: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResultEvent:
    """result 事件 — 流的最后一行，包含执行统计。"""

    is_error: bool = False
    result_text: str = ""
    session_id: str = ""
    total_cost_usd: float = 0.0
    duration_ms: int = 0
    num_turns: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedStream:
    """一次完整流式输出的解析结果。"""

    system: Optional[SystemEvent] = None
    messages: list[AssistantMessage] = field(default_factory=list)
    result: Optional[ResultEvent] = None
    parse_errors: list[str] = field(default_factory=list)
    """无法解析的行（用于调试）。"""

    @property
    def final_text(self) -> str:
        """最后一条 assistant 消息的文本。"""
        for msg in reversed(self.messages):
            if msg.text:
                return msg.text
        if self.result and self.result.result_text:
            return self.result.result_text
        return ""

    @property
    def session_id(self) -> str:
        """会话 ID（优先从 system 事件取，回退到 result 事件）。"""
        if self.system and self.system.session_id:
            return self.system.session_id
        if self.result and self.result.session_id:
            return self.result.session_id
        return ""


# ---------------------------------------------------------------------------
# 解析器
# ---------------------------------------------------------------------------


class ClaudeOutputParser:
    """流式 JSON 输出解析器。

    用法::

        parser = ClaudeOutputParser()
        async for line in process.stdout:
            event = parser.parse_line(line)
            if event:
                handle(event)
        stream = parser.finalize()
    """

    def __init__(self) -> None:
        self._system: Optional[SystemEvent] = None
        self._messages: list[AssistantMessage] = []
        self._result: Optional[ResultEvent] = None
        self._parse_errors: list[str] = []

    # ------------------------------------------------------------------
    # 逐行解析
    # ------------------------------------------------------------------

    def parse_line(self, line: str | bytes) -> Optional[Any]:
        """解析一行输出，返回对应的事件对象。

        无法解析的行会被记录到 parse_errors，返回 None。
        空行返回 None。
        """
        if isinstance(line, bytes):
            try:
                line = line.decode("utf-8", errors="replace")
            except Exception:
                return None

        line = line.strip()
        if not line:
            return None

        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            # 非 JSON 行（可能是 stderr 串到 stdout，或 CLI 的调试输出）
            self._parse_errors.append(line[:200])
            return None

        if not isinstance(payload, dict):
            self._parse_errors.append(f"non-object: {line[:200]}")
            return None

        event_type = payload.get("type")
        if event_type == "system":
            return self._handle_system(payload)
        if event_type == "assistant":
            return self._handle_assistant(payload)
        if event_type == "result":
            return self._handle_result(payload)

        # 未知事件类型 — 记录但不报错
        self._parse_errors.append(f"unknown type {event_type!r}: {line[:200]}")
        return None

    def _handle_system(self, payload: dict[str, Any]) -> SystemEvent:
        # system 事件结构：{"type":"system","subtype":"init","session_id":"...","cwd":"...",...}
        event = SystemEvent(
            session_id=str(payload.get("session_id", "")),
            cwd=str(payload.get("cwd", "")),
            version=str(payload.get("version", "")),
            raw=payload,
        )
        # 只保留第一个 system 事件（init）
        if self._system is None:
            self._system = event
        return event

    def _handle_assistant(self, payload: dict[str, Any]) -> AssistantMessage:
        # assistant 事件结构：
        # {"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"..."},{"type":"tool_use",...}]}}
        message = payload.get("message", {})
        content = message.get("content", []) if isinstance(message, dict) else []

        text_parts: list[str] = []
        tool_uses: list[dict[str, Any]] = []

        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type == "text":
                    t = block.get("text", "")
                    if isinstance(t, str) and t:
                        text_parts.append(t)
                elif block_type == "tool_use":
                    tool_uses.append(
                        {
                            "id": block.get("id", ""),
                            "name": block.get("name", ""),
                            "input": block.get("input", {}),
                        }
                    )

        msg = AssistantMessage(
            text="".join(text_parts),
            tool_uses=tool_uses,
            raw=payload,
        )
        self._messages.append(msg)
        return msg

    def _handle_result(self, payload: dict[str, Any]) -> ResultEvent:
        # result 事件结构：
        # {"type":"result","subtype":"success","is_error":false,"result":"...","session_id":"...",
        #  "total_cost_usd":0.01,"duration_ms":1234,"num_turns":3,...}
        event = ResultEvent(
            is_error=bool(payload.get("is_error", False)),
            result_text=str(payload.get("result", "")),
            session_id=str(payload.get("session_id", "")),
            total_cost_usd=float(payload.get("total_cost_usd", 0.0) or 0.0),
            duration_ms=int(payload.get("duration_ms", 0) or 0),
            num_turns=int(payload.get("num_turns", 0) or 0),
            raw=payload,
        )
        self._result = event
        return event

    # ------------------------------------------------------------------
    # 完成解析
    # ------------------------------------------------------------------

    def finalize(self) -> ParsedStream:
        """返回完整的解析结果。调用后解析器状态不变，可继续解析。"""
        return ParsedStream(
            system=self._system,
            messages=list(self._messages),
            result=self._result,
            parse_errors=list(self._parse_errors),
        )


# ---------------------------------------------------------------------------
# 流式回调类型
# ---------------------------------------------------------------------------


StreamCallback = Callable[[Any], None]
"""流式事件回调。可以是同步或异步函数。"""


__all__ = [
    "SystemEvent",
    "ResultEvent",
    "ParsedStream",
    "ClaudeOutputParser",
    "StreamCallback",
]
