# Ported from claudian/src/utils/session.ts
# Original author: Claudian contributors
# License: MIT

"""
Session utilities.
"""

from __future__ import annotations

import uuid
from typing import Optional


def generate_session_id() -> str:
    """Generate a new session ID."""
    return str(uuid.uuid4())


def generate_message_id() -> str:
    """Generate a new message ID."""
    return f"msg-{uuid.uuid4().hex[:12]}"


def generate_conversation_id() -> str:
    """Generate a new conversation ID."""
    return f"conv-{uuid.uuid4().hex[:12]}"
