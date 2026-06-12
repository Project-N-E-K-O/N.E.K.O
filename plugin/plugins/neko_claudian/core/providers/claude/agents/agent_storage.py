# Ported from claudian/src/providers/claude/agents/AgentStorage.ts
# Original author: Claudian contributors
# License: MIT

"""
Agent storage for Claude provider.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ClaudeAgentStorage:
    """Storage for Claude agent definitions.

    Ported from providers/claude/agents/AgentStorage.ts
    """

    def __init__(self, storage_path: Path):
        self._storage_path = storage_path
        self._agents_file = storage_path / "agents.json"

    async def load(self) -> List[Dict[str, Any]]:
        """Load agents from storage."""
        if not self._agents_file.exists():
            return []

        try:
            with open(self._agents_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"Failed to load agents: {e}")
            return []

    async def save(self, agents: List[Dict[str, Any]]) -> None:
        """Save agents to storage."""
        try:
            self._storage_path.mkdir(parents=True, exist_ok=True)
            with open(self._agents_file, "w", encoding="utf-8") as f:
                json.dump(agents, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save agents: {e}")

    async def add_agent(self, agent: Dict[str, Any]) -> None:
        """Add an agent to storage."""
        agents = await self.load()
        # Update existing or add new
        agent_id = agent.get("id", "")
        for i, existing in enumerate(agents):
            if existing.get("id") == agent_id:
                agents[i] = agent
                await self.save(agents)
                return
        agents.append(agent)
        await self.save(agents)

    async def remove_agent(self, agent_id: str) -> bool:
        """Remove an agent from storage."""
        agents = await self.load()
        filtered = [a for a in agents if a.get("id") != agent_id]
        if len(filtered) < len(agents):
            await self.save(filtered)
            return True
        return False
