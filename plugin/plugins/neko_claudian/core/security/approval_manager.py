# Ported from claudian/src/core/security/ApprovalManager.ts
# Original author: Claudian contributors
# License: MIT

"""
ApprovalManager — Permission utilities for tool action approval.

Handles action pattern matching, rule evaluation, and permission management.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Tool name constants
TOOL_BASH = "Bash"
TOOL_READ = "Read"
TOOL_WRITE = "Write"
TOOL_EDIT = "Edit"
TOOL_GLOB = "Glob"
TOOL_GREP = "Grep"
TOOL_NOTEBOOK_EDIT = "NotebookEdit"
TOOL_WEB_FETCH = "WebFetch"
TOOL_WEB_SEARCH = "WebSearch"
TOOL_AGENT = "Agent"
TOOL_TASK = "Task"


def get_action_pattern(tool_name: str, input_data: Dict[str, Any]) -> Optional[str]:
    """Get the action pattern for a tool invocation.

    Ported from ApprovalManager.ts getActionPattern.
    """
    if tool_name == TOOL_BASH:
        cmd = input_data.get("command")
        return cmd.strip() if isinstance(cmd, str) else ""

    if tool_name in (TOOL_READ, TOOL_WRITE, TOOL_EDIT):
        path = input_data.get("file_path")
        return path if isinstance(path, str) and path else None

    if tool_name == TOOL_NOTEBOOK_EDIT:
        path = input_data.get("notebook_path") or input_data.get("file_path")
        return path if isinstance(path, str) and path else None

    if tool_name in (TOOL_GLOB, TOOL_GREP):
        pattern = input_data.get("pattern")
        return pattern if isinstance(pattern, str) and pattern else None

    return json.dumps(input_data)


def get_action_description(tool_name: str, input_data: Dict[str, Any]) -> str:
    """Get a human-readable description of a tool action.

    Ported from ApprovalManager.ts getActionDescription.
    """
    pattern = get_action_pattern(tool_name, input_data) or "(unknown)"

    descriptions = {
        TOOL_BASH: f"Run command: {pattern}",
        TOOL_READ: f"Read file: {pattern}",
        TOOL_WRITE: f"Write to file: {pattern}",
        TOOL_EDIT: f"Edit file: {pattern}",
        TOOL_GLOB: f"Search files matching: {pattern}",
        TOOL_GREP: f"Search content matching: {pattern}",
        TOOL_NOTEBOOK_EDIT: f"Edit notebook: {pattern}",
        TOOL_WEB_FETCH: f"Fetch URL: {pattern}",
        TOOL_WEB_SEARCH: f"Search web: {pattern}",
        TOOL_AGENT: f"Launch agent: {pattern}",
    }

    return descriptions.get(tool_name, f"{tool_name}: {pattern}")


def matches_rule_pattern(
    tool_name: str,
    action_pattern: Optional[str],
    rule_pattern: Optional[str],
) -> bool:
    """Check if an action matches a rule pattern.

    Bash: exact or explicit wildcard ("git *", "npm:*").
    File tools: path-prefix matching with segment boundaries.
    Other tools: simple prefix matching.

    Ported from ApprovalManager.ts matchesRulePattern.
    """
    # No rule pattern means match all
    if not rule_pattern:
        return True

    # Null action pattern means we can't determine the action
    if action_pattern is None:
        return False

    # Normalize paths
    normalized_action = action_pattern.replace("\\", "/")
    normalized_rule = rule_pattern.replace("\\", "/")

    # Wildcard matches everything
    if normalized_rule == "*":
        return True

    # Exact match
    if normalized_action == normalized_rule:
        return True

    # Bash: Only exact match or explicit wildcard patterns
    if tool_name == TOOL_BASH:
        # CC format "npm:*" — colon is a separator
        if normalized_rule.endswith(":*"):
            prefix = normalized_rule[:-2]
            return _matches_bash_prefix(normalized_action, prefix)
        # Space wildcard "git *"
        if normalized_rule.endswith("*"):
            prefix = normalized_rule[:-1]
            return _matches_bash_prefix(normalized_action, prefix)
        # No wildcard present and exact match failed
        return False

    # File tools: prefix match with path-segment boundary
    if tool_name in (TOOL_READ, TOOL_WRITE, TOOL_EDIT, TOOL_NOTEBOOK_EDIT):
        return _is_path_prefix_match(normalized_action, normalized_rule)

    # Other tools: simple prefix matching
    return normalized_action.startswith(normalized_rule)


def _is_path_prefix_match(action_path: str, approved_path: str) -> bool:
    """Check if action path is under approved path."""
    if not action_path.startswith(approved_path):
        return False

    if approved_path.endswith("/"):
        return True

    if len(action_path) == len(approved_path):
        return True

    return action_path[len(approved_path)] == "/"


def _matches_bash_prefix(action: str, prefix: str) -> bool:
    """Check if a bash command matches a prefix pattern."""
    if action == prefix:
        return True

    if prefix.endswith(" "):
        return action.startswith(prefix)

    return action.startswith(f"{prefix} ")


@dataclass
class PermissionRule:
    """A permission rule."""
    tool: str = ""
    pattern: Optional[str] = None
    behavior: str = "allow"  # "allow" | "deny"

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"tool": self.tool, "behavior": self.behavior}
        if self.pattern:
            out["pattern"] = self.pattern
        return out

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PermissionRule:
        return cls(
            tool=data.get("tool", ""),
            pattern=data.get("pattern"),
            behavior=data.get("behavior", "allow"),
        )


@dataclass
class PermissionProfile:
    """A collection of permission rules."""
    name: str = "default"
    rules: List[PermissionRule] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "rules": [r.to_dict() for r in self.rules],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PermissionProfile:
        return cls(
            name=data.get("name", "default"),
            rules=[PermissionRule.from_dict(r) for r in data.get("rules", [])],
        )


class ApprovalManager:
    """Manage tool approval permissions.

    Ported from ApprovalManager.ts
    """

    def __init__(self):
        self._session_rules: List[PermissionRule] = []
        self._persistent_rules: List[PermissionRule] = []

    def check_permission(
        self,
        tool_name: str,
        input_data: Dict[str, Any],
    ) -> Optional[str]:
        """Check if a tool invocation is permitted.

        Returns:
            "allow" if permitted
            "deny" if denied
            None if no rule matches (should prompt user)
        """
        action_pattern = get_action_pattern(tool_name, input_data)

        # Check session rules first (higher priority)
        for rule in self._session_rules:
            if rule.tool == tool_name:
                if matches_rule_pattern(tool_name, action_pattern, rule.pattern):
                    return rule.behavior

        # Check persistent rules
        for rule in self._persistent_rules:
            if rule.tool == tool_name:
                if matches_rule_pattern(tool_name, action_pattern, rule.pattern):
                    return rule.behavior

        return None

    def add_session_rule(self, rule: PermissionRule) -> None:
        """Add a session-scoped rule."""
        self._session_rules.append(rule)

    def add_persistent_rule(self, rule: PermissionRule) -> None:
        """Add a persistent rule."""
        self._persistent_rules.append(rule)

    def clear_session_rules(self) -> None:
        """Clear all session rules."""
        self._session_rules.clear()

    def get_session_rules(self) -> List[PermissionRule]:
        """Get all session rules."""
        return self._session_rules

    def get_persistent_rules(self) -> List[PermissionRule]:
        """Get all persistent rules."""
        return self._persistent_rules

    def load_rules(self, rules_data: List[Dict[str, Any]]) -> None:
        """Load rules from data."""
        self._persistent_rules = [PermissionRule.from_dict(r) for r in rules_data]

    def save_rules(self) -> List[Dict[str, Any]]:
        """Save rules to data."""
        return [r.to_dict() for r in self._persistent_rules]
