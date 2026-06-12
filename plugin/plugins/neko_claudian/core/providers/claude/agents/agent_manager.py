# Ported from claudian/src/providers/claude/agents/AgentManager.ts
# Original author: Claudian contributors
# License: MIT

"""
Claude-specific agent manager.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ClaudeAgentManager:
    """Claude-specific agent manager.

    Ported from providers/claude/agents/AgentManager.ts
    """

    def __init__(self):
        self._agents: Dict[str, Dict[str, Any]] = {}

    def load_agents(self, agents_data: List[Dict[str, Any]]) -> None:
        """Load agents from data."""
        for agent in agents_data:
            agent_id = agent.get("id", "")
            if agent_id:
                self._agents[agent_id] = agent

    def get_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get an agent by ID."""
        return self._agents.get(agent_id)

    def get_all_agents(self) -> List[Dict[str, Any]]:
        """Get all agents."""
        return list(self._agents.values())

    def add_agent(self, agent: Dict[str, Any]) -> None:
        """Add an agent."""
        agent_id = agent.get("id", "")
        if agent_id:
            self._agents[agent_id] = agent

    def remove_agent(self, agent_id: str) -> bool:
        """Remove an agent."""
        if agent_id in self._agents:
            del self._agents[agent_id]
            return True
        return False
