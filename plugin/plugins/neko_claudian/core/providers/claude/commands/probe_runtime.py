# Ported from claudian/src/providers/claude/commands/probeRuntimeCommands.ts
# Original author: Claudian contributors
# License: MIT

"""
Probe runtime for available commands.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


async def probe_runtime_commands(
    get_agent_service: callable,
    timeout: float = 5.0,
) -> List[Dict[str, Any]]:
    """Probe the runtime for available commands.

    Args:
        get_agent_service: Function to get the agent service
        timeout: Timeout in seconds

    Returns:
        List of available commands
    """
    agent_service = get_agent_service()
    if not agent_service:
        return []

    try:
        commands = await agent_service.get_supported_commands()
        return [cmd if isinstance(cmd, dict) else {"name": str(cmd)} for cmd in commands]
    except Exception as e:
        logger.warning(f"Failed to probe runtime commands: {e}")
        return []
