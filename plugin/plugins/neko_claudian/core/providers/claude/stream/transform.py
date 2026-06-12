"""
1:1 ported from claudian/src/providers/claude/stream/transformClaudeMessage.ts

把 SDK 消息 (system/assistant/user/result/stream_event) 转为内部 StreamChunk 列表。

设计要点：
- 一条 SDK 消息可能产生 0..N 个 StreamChunk
- 流事件 (stream_event) 持续累积到 assistant message
- 工具调用 / 工具结果 / 思考 / 文本各自有 type
- session info / usage / error / control_request 都有专门 type
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from .types import StreamChunk, ChunkType
from .tool_input_state import ToolInputStreamState
from ..sdk.type_guards import (
    is_assistant_message,
    is_compact_boundary,
    is_content_block_delta_event,
    is_content_block_start_event,
    is_content_block_stop_event,
    is_error_result,
    is_image_block,
    is_message_delta_event,
    is_message_start_event,
    is_message_stop_event,
    is_result_message,
    is_session_init_event,
    is_stream_event,
    is_text_block,
    is_thinking_block,
    is_tool_result_block,
    is_tool_use_block,
    is_user_message,
)


# ---------------------------------------------------------------------------
# 状态（per-conversation 累积）
# ---------------------------------------------------------------------------

class TransformStreamState:
    """
    维护一个流会话的累积状态。
    - 工具输入部分 JSON 累积
    - 当前 assistant message 累积
    - content block 索引
    """

    def __init__(self):
        self.tool_input = ToolInputStreamState()
        self.current_message_id: Optional[str] = None
        self.current_model: str = ""
        self.active_block_index: Optional[int] = None
        self.active_block_type: Optional[str] = None  # text / thinking / tool_use
        self.active_tool_use_id: Optional[str] = None
        # 已累积的 content blocks
        self.accumulated_text: str = ""
        self.accumulated_thinking: str = ""
        self.tool_calls: Dict[str, Dict[str, Any]] = {}  # tool_use_id -> {name, input, ...}

    def reset_for_new_turn(self):
        self.tool_input.clear_all()
        self.current_message_id = None
        self.active_block_index = None
        self.active_block_type = None
        self.active_tool_use_id = None
        self.accumulated_text = ""
        self.accumulated_thinking = ""
        self.tool_calls = {}


class TransformUsageState:
    """Token 累积状态。"""

    def __init__(self):
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.cache_creation_input_tokens: int = 0
        self.cache_read_input_tokens: int = 0
        self.context_window: int = 200000
        self.last_usage_message: Optional[Dict[str, Any]] = None

    def update(self, usage: Dict[str, Any]):
        if not usage:
            return
        self.input_tokens = usage.get("input_tokens", self.input_tokens)
        self.output_tokens = usage.get("output_tokens", self.output_tokens)
        self.cache_creation_input_tokens = usage.get(
            "cache_creation_input_tokens", self.cache_creation_input_tokens
        )
        self.cache_read_input_tokens = usage.get(
            "cache_read_input_tokens", self.cache_read_input_tokens
        )
        self.last_usage_message = usage

    def snapshot(self) -> Dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "context_window": self.context_window,
        }


# ---------------------------------------------------------------------------
# 转换函数
# ---------------------------------------------------------------------------

def _ts() -> float:
    return time.time()


def transform_sdk_message(
    msg: Dict[str, Any],
    *,
    state: Optional[TransformStreamState] = None,
    usage_state: Optional[TransformUsageState] = None,
) -> List[StreamChunk]:
    """
    把一条 SDK 消息转为 0..N 个 StreamChunk。
    """
    if state is None:
        state = TransformStreamState()
    if usage_state is None:
        usage_state = TransformUsageState()

    out: List[StreamChunk] = []

    if is_session_init_event(msg):
        # system/init
        out.append(StreamChunk(
            type=ChunkType.SESSION_INFO.value,
            data={
                "session_id": msg.get("session_id", ""),
                "model": msg.get("model", ""),
                "tools": msg.get("tools", []),
                "mcp_servers": msg.get("mcp_servers", []),
                "cwd": msg.get("cwd", ""),
                "permission_mode": msg.get("permission_mode", "default"),
                "claude_code_version": msg.get("claude_code_version"),
                "uuid": msg.get("uuid", ""),
            },
            timestamp=_ts(),
        ))
        # 重置 turn 状态
        state.reset_for_new_turn()
        return out

    if is_compact_boundary(msg):
        out.append(StreamChunk(
            type=ChunkType.COMPACT.value,
            data={
                "trigger": msg.get("trigger", ""),
                "pre_tokens": msg.get("pre_tokens", 0),
            },
            timestamp=_ts(),
        ))
        return out

    if is_user_message(msg):
        for chunk in _transform_user_message(msg, state):
            out.append(chunk)
        return out

    if is_assistant_message(msg):
        for chunk in _transform_assistant_message(msg, state, usage_state):
            out.append(chunk)
        return out

    if is_result_message(msg):
        for chunk in _transform_result_message(msg, usage_state):
            out.append(chunk)
        return out

    if is_stream_event(msg):
        for chunk in _transform_stream_event(msg, state, usage_state):
            out.append(chunk)
        return out

    # 未知类型
    out.append(StreamChunk(
        type=ChunkType.STATUS.value,
        data={"raw": msg},
        timestamp=_ts(),
    ))
    return out


# ---------------------------------------------------------------------------
# User message
# ---------------------------------------------------------------------------

def _transform_user_message(
    msg: Dict[str, Any],
    state: TransformStreamState,
) -> List[StreamChunk]:
    out: List[StreamChunk] = []
    content = msg.get("message", {}).get("content", [])
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    for blk in content or []:
        if is_tool_result_block(blk):
            tool_use_id = blk.get("tool_use_id", "")
            content_v = blk.get("content", "")
            is_err = bool(blk.get("is_error", False))
            out.append(StreamChunk(
                type=ChunkType.TOOL_RESULT.value,
                data={
                    "tool_use_id": tool_use_id,
                    "content": content_v,
                    "is_error": is_err,
                    "parent_tool_use_id": msg.get("parent_tool_use_id"),
                    "uuid": msg.get("uuid", ""),
                },
                timestamp=_ts(),
            ))
    return out


# ---------------------------------------------------------------------------
# Assistant message
# ---------------------------------------------------------------------------

def _transform_assistant_message(
    msg: Dict[str, Any],
    state: TransformStreamState,
    usage_state: TransformUsageState,
) -> List[StreamChunk]:
    out: List[StreamChunk] = []
    inner = msg.get("message", {})
    state.current_model = inner.get("model", state.current_model)
    usage = inner.get("usage")
    if usage:
        usage_state.update(usage)

    content = inner.get("content", [])
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]

    # 用于一次性发整条 assistant message（前端一次性渲染）
    # 但 stream_event 模式下我们已经增量发过 TEXT / THINKING / TOOL_USE 了
    # 这里以"完整版"为权威；如果 state 表明这是 stream 累积后的整条消息，则不重复发
    for blk in content or []:
        if is_text_block(blk):
            text = blk.get("text", "")
            if text:
                out.append(StreamChunk(
                    type=ChunkType.TEXT.value,
                    data={"text": text, "complete": True},
                    timestamp=_ts(),
                ))
        elif is_thinking_block(blk):
            thinking = blk.get("thinking", "")
            if thinking:
                out.append(StreamChunk(
                    type=ChunkType.THINKING.value,
                    data={
                        "thinking": thinking,
                        "signature": blk.get("signature"),
                        "complete": True,
                    },
                    timestamp=_ts(),
                ))
        elif is_tool_use_block(blk):
            tool_use_id = blk.get("id", "")
            out.append(StreamChunk(
                type=ChunkType.TOOL_USE.value,
                data={
                    "tool_use_id": tool_use_id,
                    "name": blk.get("name", ""),
                    "input": blk.get("input", {}),
                    "complete": True,
                },
                timestamp=_ts(),
            ))
        elif is_image_block(blk):
            out.append(StreamChunk(
                type=ChunkType.STATUS.value,
                data={"image": blk.get("source")},
                timestamp=_ts(),
            ))

    # 发 assistant 整体（前端会重组）
    out.append(StreamChunk(
        type=ChunkType.ASSISTANT.value,
        data={
            "id": inner.get("id", ""),
            "model": state.current_model,
            "content": content,
            "usage": usage,
        },
        timestamp=_ts(),
    ))
    return out


# ---------------------------------------------------------------------------
# Result message
# ---------------------------------------------------------------------------

def _transform_result_message(
    msg: Dict[str, Any],
    usage_state: TransformUsageState,
) -> List[StreamChunk]:
    out: List[StreamChunk] = []
    if is_error_result(msg):
        err = msg.get("error") or {}
        if isinstance(err, dict):
            err_str = err.get("message", "")
        else:
            err_str = str(err)
        out.append(StreamChunk(
            type=ChunkType.ERROR.value,
            data={
                "message": err_str,
                "subtype": msg.get("subtype", "error"),
                "duration_ms": msg.get("duration_ms", 0),
                "session_id": msg.get("session_id", ""),
            },
            timestamp=_ts(),
        ))
    else:
        out.append(StreamChunk(
            type=ChunkType.RESULT.value if False else ChunkType.DONE.value,
            data={
                "subtype": msg.get("subtype", "success"),
                "result": msg.get("result", ""),
                "duration_ms": msg.get("duration_ms", 0),
                "duration_api_ms": msg.get("duration_api_ms", 0),
                "num_turns": msg.get("num_turns", 0),
                "total_cost_usd": msg.get("total_cost_usd", 0.0),
                "usage": msg.get("usage"),
                "session_id": msg.get("session_id", ""),
            },
            timestamp=_ts(),
        ))
    # 总是发一个 usage
    out.append(StreamChunk(
        type=ChunkType.USAGE.value,
        data=usage_state.snapshot(),
        timestamp=_ts(),
    ))
    return out


# ---------------------------------------------------------------------------
# Stream event
# ---------------------------------------------------------------------------

def _transform_stream_event(
    msg: Dict[str, Any],
    state: TransformStreamState,
    usage_state: TransformUsageState,
) -> List[StreamChunk]:
    out: List[StreamChunk] = []
    ev = msg.get("event", {})

    if is_message_start_event(ev):
        state.current_message_id = ev.get("message", {}).get("id", "")
        state.reset_for_new_turn()
        msg_inner = ev.get("message", {})
        state.current_model = msg_inner.get("model", state.current_model)
        usage = msg_inner.get("usage")
        if usage:
            usage_state.update(usage)
            out.append(StreamChunk(
                type=ChunkType.USAGE.value,
                data=usage_state.snapshot(),
                timestamp=_ts(),
            ))

    elif is_content_block_start_event(ev):
        idx = ev.get("index", 0)
        blk = ev.get("content_block", {}) or {}
        state.active_block_index = idx
        state.active_block_type = blk.get("type", "")
        if is_tool_use_block(blk):
            tool_use_id = blk.get("id", "")
            state.active_tool_use_id = tool_use_id
            state.tool_calls[tool_use_id] = {
                "name": blk.get("name", ""),
                "input": {},
            }
            out.append(StreamChunk(
                type=ChunkType.TOOL_USE.value,
                data={
                    "tool_use_id": tool_use_id,
                    "name": blk.get("name", ""),
                    "input": {},
                    "complete": False,
                },
                timestamp=_ts(),
            ))

    elif is_content_block_delta_event(ev):
        idx = ev.get("index", 0)
        delta = ev.get("delta", {}) or {}
        delta_type = delta.get("type", "")
        if delta_type == "text_delta":
            text = delta.get("text", "")
            if text:
                state.accumulated_text += text
                out.append(StreamChunk(
                    type=ChunkType.TEXT.value,
                    data={"text": text, "complete": False, "index": idx},
                    timestamp=_ts(),
                ))
        elif delta_type == "thinking_delta":
            thinking = delta.get("thinking", "")
            if thinking:
                state.accumulated_thinking += thinking
                out.append(StreamChunk(
                    type=ChunkType.THINKING.value,
                    data={
                        "thinking": thinking,
                        "complete": False,
                        "index": idx,
                    },
                    timestamp=_ts(),
                ))
        elif delta_type == "input_json_delta":
            partial = delta.get("partial_json", "")
            if state.active_tool_use_id:
                state.tool_input.append_delta(state.active_tool_use_id, partial)
                parsed = state.tool_input.get(state.active_tool_use_id)
                if parsed is not None and state.active_tool_use_id in state.tool_calls:
                    state.tool_calls[state.active_tool_use_id]["input"] = parsed
                out.append(StreamChunk(
                    type=ChunkType.TOOL_INPUT_PROGRESS.value,
                    data={
                        "tool_use_id": state.active_tool_use_id,
                        "partial": partial,
                        "parsed": parsed,
                    },
                    timestamp=_ts(),
                ))

    elif is_content_block_stop_event(ev):
        if state.active_tool_use_id:
            parsed = state.tool_input.get(state.active_tool_use_id)
            out.append(StreamChunk(
                type=ChunkType.TOOL_USE.value,
                data={
                    "tool_use_id": state.active_tool_use_id,
                    "name": state.tool_calls.get(state.active_tool_use_id, {}).get("name", ""),
                    "input": parsed or {},
                    "complete": True,
                },
                timestamp=_ts(),
            ))
            state.tool_input.clear(state.active_tool_use_id)
            state.active_tool_use_id = None
        state.active_block_type = None
        state.active_block_index = None

    elif is_message_delta_event(ev):
        usage = (ev.get("usage") or {})
        if usage:
            usage_state.update(usage)
            out.append(StreamChunk(
                type=ChunkType.USAGE.value,
                data=usage_state.snapshot(),
                timestamp=_ts(),
            ))
        # stop_reason / stop_sequence
        delta = ev.get("delta", {}) or {}
        if delta.get("stop_reason"):
            out.append(StreamChunk(
                type=ChunkType.STATUS.value,
                data={
                    "stop_reason": delta.get("stop_reason"),
                    "stop_sequence": delta.get("stop_sequence"),
                },
                timestamp=_ts(),
            ))

    elif is_message_stop_event(ev):
        # turn 结束
        out.append(StreamChunk(
            type=ChunkType.STATUS.value,
            data={"event": "message_stop", "message_id": state.current_message_id},
            timestamp=_ts(),
        ))

    return out
