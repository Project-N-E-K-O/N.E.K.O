# Ported from claudian/src/providers/claude/prompt/ClaudeTurnEncoder.ts
# Original author: Claudian contributors
# License: MIT

"""
Turn encoder for Claude provider.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ClaudeTurnEncoder:
    """Encode turns for Claude SDK.

    Ported from providers/claude/prompt/ClaudeTurnEncoder.ts
    """

    def __init__(self):
        pass

    def encode_user_message(
        self,
        text: str,
        images: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Encode a user message for the SDK."""
        content = []

        # Add images first
        if images:
            for img in images:
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": img.get("mediaType", "image/png"),
                        "data": img.get("data", ""),
                    },
                })

        # Add text
        content.append({
            "type": "text",
            "text": text,
        })

        return {
            "role": "user",
            "content": content,
        }

    def encode_assistant_message(self, text: str) -> Dict[str, Any]:
        """Encode an assistant message for the SDK."""
        return {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
        }

    def encode_conversation(
        self,
        messages: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Encode a conversation for the SDK."""
        encoded = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if isinstance(content, str):
                if role == "user":
                    encoded.append(self.encode_user_message(content, msg.get("images")))
                else:
                    encoded.append(self.encode_assistant_message(content))
            elif isinstance(content, list):
                # Already in SDK format
                encoded.append({"role": role, "content": content})

        return encoded
