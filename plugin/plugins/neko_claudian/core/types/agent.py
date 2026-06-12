# Ported from claudian/src/core/types/agent.ts
# Original author: Claudian contributors
# License: MIT

"""
Agent type definitions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AgentDefinition:
    """Agent definition with configuration."""
    id: str = ""
    name: str = ""
    description: str = ""
    prompt: str = ""
    tools: Optional[List[str]] = None
    disallowed_tools: Optional[List[str]] = None
    model: Optional[str] = None
    source: str = "builtin"  # "plugin" | "vault" | "global" | "builtin"
    plugin_name: Optional[str] = None
    file_path: Optional[str] = None
    skills: Optional[List[str]] = None
    permission_mode: Optional[str] = None
    hooks: Optional[Dict[str, Any]] = None
    extra_frontmatter: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "prompt": self.prompt,
            "source": self.source,
        }
        if self.tools:
            out["tools"] = self.tools
        if self.disallowed_tools:
            out["disallowedTools"] = self.disallowed_tools
        if self.model:
            out["model"] = self.model
        if self.plugin_name:
            out["pluginName"] = self.plugin_name
        if self.file_path:
            out["filePath"] = self.file_path
        if self.skills:
            out["skills"] = self.skills
        if self.permission_mode:
            out["permissionMode"] = self.permission_mode
        if self.hooks:
            out["hooks"] = self.hooks
        if self.extra_frontmatter:
            out["extraFrontmatter"] = self.extra_frontmatter
        return out


@dataclass
class AgentFrontmatter:
    """Agent frontmatter from agent.md files."""
    name: str = ""
    description: str = ""
    tools: Optional[List[str]] = None
    disallowed_tools: Optional[List[str]] = None
    model: Optional[str] = None
    skills: Optional[List[str]] = None
    permission_mode: Optional[str] = None
    hooks: Optional[Dict[str, Any]] = None
    extra_frontmatter: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "name": self.name,
            "description": self.description,
        }
        if self.tools:
            out["tools"] = self.tools
        if self.disallowed_tools:
            out["disallowedTools"] = self.disallowed_tools
        if self.model:
            out["model"] = self.model
        if self.skills:
            out["skills"] = self.skills
        if self.permission_mode:
            out["permissionMode"] = self.permission_mode
        if self.hooks:
            out["hooks"] = self.hooks
        if self.extra_frontmatter:
            out["extraFrontmatter"] = self.extra_frontmatter
        return out
