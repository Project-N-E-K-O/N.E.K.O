# Ported from claudian/src/core/agents/ (concept)
# Original author: Claudian contributors
# License: MIT

"""
AgentManager — Manage agent definitions and execution.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class AgentDefinition:
    """Agent definition."""
    id: str = ""
    name: str = ""
    description: str = ""
    prompt: str = ""
    tools: List[str] = field(default_factory=list)
    model: Optional[str] = None
    source: str = "builtin"  # "builtin" | "plugin" | "user"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "prompt": self.prompt,
            "tools": self.tools,
            "model": self.model,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AgentDefinition:
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            prompt=data.get("prompt", ""),
            tools=data.get("tools", []),
            model=data.get("model"),
            source=data.get("source", "builtin"),
        )


class AgentManager:
    """Manage agent definitions.

    Ported from AgentManager concept
    """

    def __init__(self):
        self._agents: Dict[str, AgentDefinition] = {}

    def load_agents(self, agents_data: List[Dict[str, Any]]) -> None:
        """Load agents from data."""
        for data in agents_data:
            agent = AgentDefinition.from_dict(data)
            self._agents[agent.id] = agent

    def get_agent(self, agent_id: str) -> Optional[AgentDefinition]:
        """Get an agent by ID."""
        return self._agents.get(agent_id)

    def get_all_agents(self) -> List[AgentDefinition]:
        """Get all agents."""
        return list(self._agents.values())

    def add_agent(self, agent: AgentDefinition) -> None:
        """Add an agent."""
        self._agents[agent.id] = agent

    def remove_agent(self, agent_id: str) -> bool:
        """Remove an agent."""
        if agent_id in self._agents:
            del self._agents[agent_id]
            return True
        return False

    def has_agent(self, agent_id: str) -> bool:
        """Check if an agent exists."""
        return agent_id in self._agents
