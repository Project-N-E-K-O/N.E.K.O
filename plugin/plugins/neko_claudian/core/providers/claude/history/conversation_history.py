# Ported from claudian/src/providers/claude/history/ClaudeConversationHistoryService.ts
# Original author: Claudian contributors
# License: MIT

"""
Conversation history service for Claude provider.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ClaudeConversationHistoryService:
    """Conversation history service for Claude.

    Ported from ClaudeConversationHistoryService.ts
    """

    def __init__(self):
        pass

    def build_fork_provider_state(
        self,
        source_session_id: str,
        resume_at: str,
        source_provider_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build provider state for forking a conversation."""
        return {
            "sourceSessionId": source_session_id,
            "resumeAt": resume_at,
            "sourceProviderState": source_provider_state,
        }

    def extract_session_id(self, provider_state: Dict[str, Any]) -> Optional[str]:
        """Extract session ID from provider state."""
        return provider_state.get("sessionId")

    def extract_resume_at(self, provider_state: Dict[str, Any]) -> Optional[str]:
        """Extract resume checkpoint from provider state."""
        return provider_state.get("resumeAt")
