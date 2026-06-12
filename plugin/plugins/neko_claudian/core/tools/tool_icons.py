# Ported from claudian/src/core/tools/toolIcons.ts
# Original author: Claudian contributors
# License: MIT

"""
Tool icon mappings for UI rendering.
"""

from __future__ import annotations

from typing import Dict, Optional

from .tool_names import (
    TOOL_AGENT_OUTPUT,
    TOOL_ASK_USER_QUESTION,
    TOOL_BASH,
    TOOL_BASH_OUTPUT,
    TOOL_EDIT,
    TOOL_ENTER_PLAN_MODE,
    TOOL_EXIT_PLAN_MODE,
    TOOL_GLOB,
    TOOL_GREP,
    TOOL_KILL_SHELL,
    TOOL_LS,
    TOOL_NOTEBOOK_EDIT,
    TOOL_READ,
    TOOL_SKILL,
    TOOL_SUBAGENT,
    TOOL_SUBAGENT_LEGACY,
    TOOL_TODO_WRITE,
    TOOL_WEB_FETCH,
    TOOL_WEB_SEARCH,
    TOOL_WRITE,
)

# SVG icon names (matching Lucide icons)
TOOL_ICONS: Dict[str, str] = {
    TOOL_BASH: "terminal",
    TOOL_BASH_OUTPUT: "terminal",
    TOOL_KILL_SHELL: "x-circle",
    TOOL_READ: "file-text",
    TOOL_WRITE: "file-plus",
    TOOL_EDIT: "file-edit",
    TOOL_NOTEBOOK_EDIT: "file-edit",
    TOOL_GLOB: "search",
    TOOL_GREP: "search",
    TOOL_LS: "folder",
    TOOL_WEB_FETCH: "globe",
    TOOL_WEB_SEARCH: "search",
    TOOL_SUBAGENT: "bot",
    TOOL_SUBAGENT_LEGACY: "bot",
    TOOL_AGENT_OUTPUT: "bot",
    TOOL_SKILL: "sparkles",
    TOOL_ASK_USER_QUESTION: "help-circle",
    TOOL_TODO_WRITE: "check-square",
    TOOL_ENTER_PLAN_MODE: "map",
    TOOL_EXIT_PLAN_MODE: "map",
}


def get_tool_icon(tool_name: str) -> str:
    """Get the icon name for a tool.

    Ported from toolIcons.ts getToolIcon.
    """
    return TOOL_ICONS.get(tool_name, "tool")
