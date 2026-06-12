# Ported from claudian/src/core/commands/builtInCommands.ts
# Original author: Claudian contributors
# License: MIT

"""
Built-in slash commands — System commands that perform actions.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class BuiltInCommand:
    """A built-in slash command."""
    name: str = ""
    aliases: List[str] = field(default_factory=list)
    description: str = ""
    action: str = ""  # "clear" | "add-dir" | "resume" | "fork"
    has_args: bool = False
    argument_hint: Optional[str] = None
    required_capability: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "action": self.action,
        }
        if self.aliases:
            out["aliases"] = self.aliases
        if self.has_args:
            out["hasArgs"] = True
        if self.argument_hint:
            out["argumentHint"] = self.argument_hint
        if self.required_capability:
            out["requiredCapability"] = self.required_capability
        return out


@dataclass
class BuiltInCommandResult:
    """Result of detecting a built-in command."""
    command: BuiltInCommand = field(default_factory=BuiltInCommand)
    args: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command": self.command.to_dict(),
            "args": self.args,
        }


# Built-in commands
BUILT_IN_COMMANDS = [
    BuiltInCommand(
        name="clear",
        aliases=["new"],
        description="Start a new conversation",
        action="clear",
    ),
    BuiltInCommand(
        name="add-dir",
        description="Add external context directory",
        action="add-dir",
        has_args=True,
        argument_hint="[path/to/directory]",
    ),
    BuiltInCommand(
        name="resume",
        description="Resume a previous conversation",
        action="resume",
        required_capability="supportsNativeHistory",
    ),
    BuiltInCommand(
        name="fork",
        description="Fork entire conversation to new session",
        action="fork",
        required_capability="supportsFork",
    ),
]

# Command map for quick lookup
_command_map: Dict[str, BuiltInCommand] = {}

for cmd in BUILT_IN_COMMANDS:
    _command_map[cmd.name.lower()] = cmd
    for alias in cmd.aliases:
        _command_map[alias.lower()] = cmd


def is_built_in_command_supported(
    command: BuiltInCommand,
    capabilities: Optional[Dict[str, Any]] = None,
) -> bool:
    """Check if a built-in command is supported by the provider.

    Ported from builtInCommands.ts isBuiltInCommandSupported.
    """
    if not command.required_capability or not capabilities:
        return True

    return capabilities.get(command.required_capability, False)


def detect_built_in_command(input_text: str) -> Optional[BuiltInCommandResult]:
    """Check if input is a built-in command.

    Returns the command and arguments if found, None otherwise.

    Ported from builtInCommands.ts detectBuiltInCommand.
    """
    trimmed = input_text.strip()
    if not trimmed.startswith("/"):
        return None

    # Extract command name (first word after /)
    match = re.match(r"^/([a-zA-Z0-9_-]+)(?:\s(.*))?$", trimmed)
    if not match:
        return None

    cmd_name = match.group(1).lower()
    command = _command_map.get(cmd_name)
    if not command:
        return None

    args = (match.group(2) or "").strip()

    return BuiltInCommandResult(command=command, args=args)


def get_built_in_commands_for_dropdown(
    capabilities: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Get built-in commands for dropdown display.

    Ported from builtInCommands.ts getBuiltInCommandsForDropdown.
    """
    result = []
    for cmd in BUILT_IN_COMMANDS:
        if is_built_in_command_supported(cmd, capabilities):
            result.append({
                "id": f"builtin:{cmd.name}",
                "name": cmd.name,
                "description": cmd.description,
                "content": "",  # Built-in commands don't have prompt content
                "argumentHint": cmd.argument_hint,
            })
    return result
