"""
1:1 ported from claudian/src/providers/claude/sdk/typeGuards.ts

类型守卫 — 鉴别 SDK 消息种类。
"""

from __future__ import annotations

from typing import Any, Dict


def is_system_message(msg: Any) -> bool:
    return isinstance(msg, dict) and msg.get("type") == "system"


def is_assistant_message(msg: Any) -> bool:
    return isinstance(msg, dict) and msg.get("type") == "assistant"


def is_user_message(msg: Any) -> bool:
    return isinstance(msg, dict) and msg.get("type") == "user"


def is_result_message(msg: Any) -> bool:
    return isinstance(msg, dict) and msg.get("type") == "result"


def is_stream_event(msg: Any) -> bool:
    return isinstance(msg, dict) and msg.get("type") == "stream_event"


def is_session_init_event(msg: Any) -> bool:
    """system/init 事件。"""
    return (
        isinstance(msg, dict)
        and msg.get("type") == "system"
        and msg.get("subtype") == "init"
    )


def is_context_window_event(msg: Any) -> bool:
    """系统状态变化事件（compact 等）。"""
    return (
        isinstance(msg, dict)
        and msg.get("type") == "system"
        and msg.get("subtype") in {"status", "compact_boundary"}
    )


def is_compact_boundary(msg: Any) -> bool:
    return (
        isinstance(msg, dict)
        and msg.get("type") == "system"
        and msg.get("subtype") == "compact_boundary"
    )


def is_error_result(msg: Any) -> bool:
    return is_result_message(msg) and msg.get("is_error") is True


def is_tool_use_block(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") == "tool_use"


def is_tool_result_block(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") == "tool_result"


def is_text_block(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") == "text"


def is_thinking_block(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") == "thinking"


def is_image_block(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") == "image"


def is_message_start_event(ev: Any) -> bool:
    return isinstance(ev, dict) and ev.get("type") == "message_start"


def is_content_block_start_event(ev: Any) -> bool:
    return isinstance(ev, dict) and ev.get("type") == "content_block_start"


def is_content_block_delta_event(ev: Any) -> bool:
    return isinstance(ev, dict) and ev.get("type") == "content_block_delta"


def is_content_block_stop_event(ev: Any) -> bool:
    return isinstance(ev, dict) and ev.get("type") == "content_block_stop"


def is_message_delta_event(ev: Any) -> bool:
    return isinstance(ev, dict) and ev.get("type") == "message_delta"


def is_message_stop_event(ev: Any) -> bool:
    return isinstance(ev, dict) and ev.get("type") == "message_stop"


def is_stream_chunk(msg: Any) -> bool:
    """StreamChunk 是我们自己的内部类型（在 stream/transform.py 中定义）。
    此处保留旧名以保持接口一致。"""
    from .messages import StreamEvent
    return isinstance(msg, StreamEvent) or is_stream_event(msg)


def get_subtype(msg: Any) -> str:
    if isinstance(msg, dict):
        return msg.get("subtype", "")
    return ""


def get_session_id(msg: Any) -> str:
    if isinstance(msg, dict):
        return msg.get("session_id", "")
    return ""
