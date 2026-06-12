"""
1:1 ported from claudian/src/providers/claude/prompt/ClaudeTurnEncoder.ts

Turn 编码（把 ChatTurnRequest 转为 PreparedChatTurn）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .types import ChatTurnRequest, PreparedChatTurn
from .user_message_factory import build_user_message_from_request


def encode_claude_turn(
    request: ChatTurnRequest,
    *,
    session_id: Optional[str] = None,
) -> PreparedChatTurn:
    """编码单个 turn。"""
    msg = build_user_message_from_request(request, session_id=session_id)
    return PreparedChatTurn(request=request, encoded_message=msg)


def merge_texts(turns: List[ChatTurnRequest]) -> str:
    """把多个 turn 的文本合并为一段。"""
    return "\n\n".join((t.text or "").strip() for t in turns if t.text)


def build_combined_user_turn(turns: List[ChatTurnRequest]) -> PreparedChatTurn:
    """把多个 queue 的 turn 合并成一条 user turn。"""
    if not turns:
        return PreparedChatTurn()
    if len(turns) == 1:
        return encode_claude_turn(turns[0])
    text = merge_texts(turns)
    images = [img for t in turns for img in (t.images or [])]
    merged = ChatTurnRequest(text=text, images=images)
    return encode_claude_turn(merged)
