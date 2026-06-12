# Ported from claudian/src/providers/claude/history/sdkHistoryTypes.ts
# Original author: Claudian contributors
# License: MIT

"""
History types for Claude provider.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class HistoryEntry:
    """A history entry."""
    id: str = ""
    conversation_id: str = ""
    session_id: Optional[str] = None
    role: str = "user"
    content: str = ""
    timestamp: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "conversationId": self.conversation_id,
            "sessionId": self.session_id,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> HistoryEntry:
        return cls(
            id=data.get("id", ""),
            conversation_id=data.get("conversationId", ""),
            session_id=data.get("sessionId"),
            role=data.get("role", "user"),
            content=data.get("content", ""),
            timestamp=data.get("timestamp", 0.0),
        )


@dataclass
class SessionPath:
    """Path to a session's data."""
    session_id: str = ""
    path: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"sessionId": self.session_id, "path": self.path}
