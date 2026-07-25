"""Codex Adapter — JSONL 输出解析器。

Codex CLI 在 `exec --json -` 模式下，每行输出一个 JSONL 事件。
事件类型包括（来自 paperclip codex-local/src/server/parse.ts）：

- ``thread.started``：流的第一行，含 thread_id（会话 ID）
- ``item.completed``：item 完成（含 agent_message 类型的助手消息）
- ``turn.completed``：单轮完成，含 token 使用量
- ``turn.failed``：单轮失败，含 error.message
- ``error``：CLI 错误，含 message

本模块提供逐行解析能力，将原始 JSON 行转换为结构化事件，
并最终汇总为 ``ParsedCodexStream``。

与 Claude Code 适配器的差异：
- 会话 ID 字段为 ``thread_id``（而非 ``session_id``）
- 助手消息在 ``item.completed`` 事件的 ``item.text`` 中
- token 使用量可能多次出现（多个 turn.completed 累加）
- 无 cost_usd 字段（Codex CLI 不提供成本数据）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .models import UsageSummary


# ---------------------------------------------------------------------------
# 事件类型
# ---------------------------------------------------------------------------


@dataclass
class ThreadStartedEvent:
    """``thread.started`` 事件 — 流的第一行，含会话 thread_id。"""

    thread_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ItemCompletedEvent:
    """``item.completed`` 事件 — item 完成。

    仅 ``agent_message`` 类型的 item 携带助手文本，其他类型
    （如 ``file_change`` / ``command_execution``）保留原始 payload 供调试。
    """

    item_type: str = ""
    """item.type 字段（agent_message / file_change / ...）。"""

    text: str = ""
    """agent_message 的文本（仅 item_type == "agent_message" 时有效）。"""

    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class TurnCompletedEvent:
    """``turn.completed`` 事件 — 单轮完成，含 token 使用量。

    一次执行可能产生多个 turn.completed 事件（多轮对话），
    解析器会累加所有事件的 token 使用量。
    """

    usage: UsageSummary = field(default_factory=UsageSummary)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class TurnFailedEvent:
    """``turn.failed`` 事件 — 单轮失败。"""

    error_message: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ErrorEvent:
    """``error`` 事件 — CLI 错误。"""

    message: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedCodexStream:
    """一次完整 Codex 流式输出的解析结果。"""

    thread_started: Optional[ThreadStartedEvent] = None
    """流的第一个 thread.started 事件（含 thread_id）。"""

    agent_messages: list[ItemCompletedEvent] = field(default_factory=list)
    """所有 agent_message 类型的 item.completed 事件（按出现顺序）。"""

    other_items: list[ItemCompletedEvent] = field(default_factory=list)
    """非 agent_message 类型的 item.completed 事件（用于调试）。"""

    turn_completed: list[TurnCompletedEvent] = field(default_factory=list)
    """所有 turn.completed 事件。"""

    turn_failed: Optional[TurnFailedEvent] = None
    """最后一个 turn.failed 事件（如果有）。"""

    error: Optional[ErrorEvent] = None
    """最后一个 error 事件（如果有）。"""

    parse_errors: list[str] = field(default_factory=list)
    """无法解析的行（用于调试）。"""

    # ------------------------------------------------------------------
    # 便捷属性
    # ------------------------------------------------------------------

    @property
    def thread_id(self) -> str:
        """会话 thread_id（来自 thread.started 事件）。"""
        if self.thread_started and self.thread_started.thread_id:
            return self.thread_started.thread_id
        return ""

    @property
    def final_text(self) -> str:
        """最后一条 agent_message 的文本。

        与 paperclip parseCodexJsonl 的 finalMessage 行为一致：
        每遇到非空 agent_message 就覆盖，最终返回最后一条。
        """
        for msg in reversed(self.agent_messages):
            if msg.text:
                return msg.text
        return ""

    @property
    def error_message(self) -> str:
        """错误消息（优先 turn.failed，其次 error 事件）。"""
        if self.turn_failed and self.turn_failed.error_message:
            return self.turn_failed.error_message
        if self.error and self.error.message:
            return self.error.message
        return ""

    @property
    def total_usage(self) -> UsageSummary:
        """累加所有 turn.completed 事件的 token 使用量。"""
        total = UsageSummary()
        for ev in self.turn_completed:
            total.input_tokens += ev.usage.input_tokens
            total.cached_input_tokens += ev.usage.cached_input_tokens
            total.output_tokens += ev.usage.output_tokens
        return total

    @property
    def is_error(self) -> bool:
        """是否包含错误事件。"""
        return bool(self.turn_failed or self.error)


# ---------------------------------------------------------------------------
# 解析器
# ---------------------------------------------------------------------------


class CodexOutputParser:
    """Codex CLI JSONL 输出解析器。

    用法::

        parser = CodexOutputParser()
        async for line in process.stdout:
            event = parser.parse_line(line)
            if event:
                handle(event)
        stream = parser.finalize()

    解析器是有状态的：会累积所有事件，``finalize()`` 返回汇总结果。
    """

    def __init__(self) -> None:
        self._thread_started: Optional[ThreadStartedEvent] = None
        self._agent_messages: list[ItemCompletedEvent] = []
        self._other_items: list[ItemCompletedEvent] = []
        self._turn_completed: list[TurnCompletedEvent] = []
        self._turn_failed: Optional[TurnFailedEvent] = None
        self._error: Optional[ErrorEvent] = None
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
        if event_type == "thread.started":
            return self._handle_thread_started(payload)
        if event_type == "item.completed":
            return self._handle_item_completed(payload)
        if event_type == "turn.completed":
            return self._handle_turn_completed(payload)
        if event_type == "turn.failed":
            return self._handle_turn_failed(payload)
        if event_type == "error":
            return self._handle_error(payload)

        # 未知事件类型 — 记录但不报错（前向兼容）
        self._parse_errors.append(f"unknown type {event_type!r}: {line[:200]}")
        return None

    def _handle_thread_started(self, payload: dict[str, Any]) -> ThreadStartedEvent:
        # thread.started 结构：{"type":"thread.started","thread_id":"..."}
        event = ThreadStartedEvent(
            thread_id=str(payload.get("thread_id", "")),
            raw=payload,
        )
        # 只保留第一个 thread.started 事件
        if self._thread_started is None:
            self._thread_started = event
        return event

    def _handle_item_completed(self, payload: dict[str, Any]) -> ItemCompletedEvent:
        # item.completed 结构：
        # {"type":"item.completed","item":{"type":"agent_message","text":"..."}}
        # item.type 可能是 agent_message / file_change / command_execution / ...
        item = payload.get("item")
        if not isinstance(item, dict):
            item = {}

        item_type = str(item.get("type", ""))
        text = ""
        if item_type == "agent_message":
            text = str(item.get("text", ""))

        event = ItemCompletedEvent(
            item_type=item_type,
            text=text,
            raw=payload,
        )

        if item_type == "agent_message":
            self._agent_messages.append(event)
        else:
            self._other_items.append(event)
        return event

    def _handle_turn_completed(self, payload: dict[str, Any]) -> TurnCompletedEvent:
        # turn.completed 结构：
        # {"type":"turn.completed","usage":{"input_tokens":N,"cached_input_tokens":N,"output_tokens":N}}
        usage_obj = payload.get("usage")
        if not isinstance(usage_obj, dict):
            usage_obj = {}

        usage = UsageSummary(
            input_tokens=_as_int(usage_obj.get("input_tokens")),
            cached_input_tokens=_as_int(usage_obj.get("cached_input_tokens")),
            output_tokens=_as_int(usage_obj.get("output_tokens")),
        )
        event = TurnCompletedEvent(usage=usage, raw=payload)
        self._turn_completed.append(event)
        return event

    def _handle_turn_failed(self, payload: dict[str, Any]) -> TurnFailedEvent:
        # turn.failed 结构：{"type":"turn.failed","error":{"message":"..."}}
        err = payload.get("error")
        if not isinstance(err, dict):
            err = {}
        event = TurnFailedEvent(
            error_message=str(err.get("message", "")).strip(),
            raw=payload,
        )
        # 保留最后一个 turn.failed（通常只有一个）
        self._turn_failed = event
        return event

    def _handle_error(self, payload: dict[str, Any]) -> ErrorEvent:
        # error 结构：{"type":"error","message":"..."}
        event = ErrorEvent(
            message=str(payload.get("message", "")).strip(),
            raw=payload,
        )
        # 保留最后一个 error 事件
        self._error = event
        return event

    # ------------------------------------------------------------------
    # 完成解析
    # ------------------------------------------------------------------

    def finalize(self) -> ParsedCodexStream:
        """返回完整的解析结果。调用后解析器状态不变，可继续解析。"""
        return ParsedCodexStream(
            thread_started=self._thread_started,
            agent_messages=list(self._agent_messages),
            other_items=list(self._other_items),
            turn_completed=list(self._turn_completed),
            turn_failed=self._turn_failed,
            error=self._error,
            parse_errors=list(self._parse_errors),
        )


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _as_int(value: Any) -> int:
    """安全转换为非负整数。"""
    if value is None:
        return 0
    try:
        n = int(value)
        return n if n > 0 else 0
    except (TypeError, ValueError):
        return 0


def parse_codex_jsonl(stdout: str) -> ParsedCodexStream:
    """一次性解析整段 stdout 文本（来自 paperclip parseCodexJsonl）。

    对于流式场景，建议使用 ``CodexOutputParser`` 逐行解析；
    本函数适用于已有完整 stdout 文本的场景（如单元测试）。
    """
    parser = CodexOutputParser()
    for line in stdout.splitlines():
        parser.parse_line(line)
    return parser.finalize()


# ---------------------------------------------------------------------------
# 流式回调类型
# ---------------------------------------------------------------------------


StreamCallback = Callable[[Any], None]
"""流式事件回调。可以是同步或异步函数。"""


__all__ = [
    # 事件类型
    "ThreadStartedEvent",
    "ItemCompletedEvent",
    "TurnCompletedEvent",
    "TurnFailedEvent",
    "ErrorEvent",
    "ParsedCodexStream",
    # 解析器
    "CodexOutputParser",
    "parse_codex_jsonl",
    # 类型
    "StreamCallback",
]
