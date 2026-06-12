"""
1:1 ported from claudian/src/providers/claude/runtime/ClaudeUserMessageFactory.ts

构造 SDKUserMessage（含 image attachment）。
"""

from __future__ import annotations

import base64
import json
import uuid
from typing import Any, Dict, List, Optional

from ...claude.sdk.types import ImageAttachment
from .types import ChatTurnRequest


def _gen_uuid(prefix: str = "msg") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def build_claude_sdk_user_message(
    text: str,
    *,
    images: Optional[List[ImageAttachment]] = None,
    session_id: Optional[str] = None,
    parent_tool_use_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    构造 SDKUserMessage 字典（与 Agent SDK 的 stream-json 协议对齐）。
    """
    content: List[Dict[str, Any]] = []
    if text:
        content.append({"type": "text", "text": text})
    for img in (images or []):
        if img.url and not img.data:
            content.append({
                "type": "image",
                "source": {"type": "url", "url": img.url},
            })
        else:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": img.media_type or "image/png",
                    "data": img.data,
                },
            })

    msg: Dict[str, Any] = {
        "type": "user",
        "message": {
            "role": "user",
            "content": content,
        },
        "uuid": _gen_uuid("user"),
        "session_id": session_id or "",
    }
    if parent_tool_use_id:
        msg["parent_tool_use_id"] = parent_tool_use_id
    if extra:
        msg["extra"] = extra
    return msg


def build_user_message_jsonl(
    text: str,
    *,
    images: Optional[List[ImageAttachment]] = None,
    session_id: Optional[str] = None,
    parent_tool_use_id: Optional[str] = None,
) -> str:
    """把 SDKUserMessage 序列化为 JSONL 行。"""
    return json.dumps(
        build_claude_sdk_user_message(
            text,
            images=images,
            session_id=session_id,
            parent_tool_use_id=parent_tool_use_id,
        ),
        ensure_ascii=False,
    )


def build_user_message_from_request(
    request: ChatTurnRequest,
    *,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """从 ChatTurnRequest 构造 SDKUserMessage。"""
    return build_claude_sdk_user_message(
        request.text,
        images=request.images,
        session_id=session_id or request.session_id,
    )
