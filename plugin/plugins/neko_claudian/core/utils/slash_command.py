# Ported from claudian/src/utils/slashCommand.ts
# Original author: Claudian contributors
# License: MIT

"""
Slash command utilities.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple


def parse_slash_command(text: str) -> Tuple[Optional[str], str]:
    """Parse a slash command from text.

    Returns:
        Tuple of (command_name, args) or (None, text) if not a command
    """
    if not text.startswith("/"):
        return None, text

    match = re.match(r"^/([a-zA-Z0-9_-]+)(?:\s(.*))?$", text.strip())
    if not match:
        return None, text

    command = match.group(1).lower()
    args = (match.group(2) or "").strip()

    return command, args


def is_slash_command(text: str) -> bool:
    """Check if text is a slash command."""
    return text.strip().startswith("/")
