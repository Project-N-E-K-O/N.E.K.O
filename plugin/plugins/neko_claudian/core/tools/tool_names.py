# Ported from claudian/src/core/tools/toolNames.ts
# Original author: Claudian contributors
# License: MIT

"""
Tool name constants and helper functions.
"""

from __future__ import annotations

from typing import List, Set

# Core tool names
TOOL_AGENT_OUTPUT = "TaskOutput"
TOOL_ASK_USER_QUESTION = "AskUserQuestion"
TOOL_BASH = "Bash"
TOOL_BASH_OUTPUT = "BashOutput"
TOOL_EDIT = "Edit"
TOOL_GLOB = "Glob"
TOOL_GREP = "Grep"
TOOL_KILL_SHELL = "KillShell"
TOOL_LS = "LS"
TOOL_LIST_MCP_RESOURCES = "ListMcpResources"
TOOL_MCP = "Mcp"
TOOL_NOTEBOOK_EDIT = "NotebookEdit"
TOOL_READ = "Read"
TOOL_READ_MCP_RESOURCE = "ReadMcpResource"
TOOL_SKILL = "Skill"
TOOL_SUBAGENT = "Agent"
TOOL_SUBAGENT_LEGACY = "Task"
TOOL_TASK = TOOL_SUBAGENT
TOOL_TODO_WRITE = "TodoWrite"
TOOL_TOOL_SEARCH = "ToolSearch"
TOOL_WEB_FETCH = "WebFetch"
TOOL_WEB_SEARCH = "WebSearch"
TOOL_WRITE = "Write"

# Plan mode tools
TOOL_ENTER_PLAN_MODE = "EnterPlanMode"
TOOL_EXIT_PLAN_MODE = "ExitPlanMode"

# Runtime-managed tools
TOOL_APPLY_PATCH = "apply_patch"
TOOL_WRITE_STDIN = "write_stdin"
TOOL_SPAWN_AGENT = "spawn_agent"
TOOL_SEND_INPUT = "send_input"
TOOL_WAIT = "wait"
TOOL_WAIT_AGENT = "wait_agent"
TOOL_RESUME_AGENT = "resume_agent"
TOOL_CLOSE_AGENT = "close_agent"

# Tool groups
AGENT_LIFECYCLE_TOOLS = [
    TOOL_SPAWN_AGENT,
    TOOL_SEND_INPUT,
    TOOL_WAIT,
    TOOL_WAIT_AGENT,
    TOOL_RESUME_AGENT,
    TOOL_CLOSE_AGENT,
]

SUBAGENT_HIDDEN_TOOLS = [
    TOOL_WAIT,
    TOOL_WAIT_AGENT,
    TOOL_CLOSE_AGENT,
]

TOOLS_SKIP_BLOCKED_DETECTION = [
    TOOL_ENTER_PLAN_MODE,
    TOOL_EXIT_PLAN_MODE,
    TOOL_ASK_USER_QUESTION,
]

SUBAGENT_TOOL_NAMES = [
    TOOL_SUBAGENT,
    TOOL_SUBAGENT_LEGACY,
]

EDIT_TOOLS = [TOOL_WRITE, TOOL_EDIT, TOOL_NOTEBOOK_EDIT]
WRITE_EDIT_TOOLS = [TOOL_WRITE, TOOL_EDIT]
BASH_TOOLS = [TOOL_BASH, TOOL_BASH_OUTPUT, TOOL_KILL_SHELL]
FILE_TOOLS = [
    TOOL_READ, TOOL_WRITE, TOOL_EDIT, TOOL_GLOB,
    TOOL_GREP, TOOL_LS, TOOL_NOTEBOOK_EDIT, TOOL_BASH,
]
MCP_TOOLS = [TOOL_LIST_MCP_RESOURCES, TOOL_READ_MCP_RESOURCE, TOOL_MCP]
READ_ONLY_TOOLS = [
    TOOL_READ, TOOL_GREP, TOOL_GLOB, TOOL_LS,
    TOOL_WEB_SEARCH, TOOL_WEB_FETCH,
]


def is_agent_lifecycle_tool(name: str) -> bool:
    """Check if tool is an agent lifecycle tool."""
    return name in AGENT_LIFECYCLE_TOOLS


def is_subagent_spawn_tool(name: str) -> bool:
    """Check if tool is a subagent spawn tool."""
    return name == TOOL_SPAWN_AGENT


def is_subagent_hidden_tool(name: str) -> bool:
    """Check if tool should be hidden in subagent rendering."""
    return name in SUBAGENT_HIDDEN_TOOLS


def skips_blocked_detection(name: str) -> bool:
    """Check if tool skips blocked detection."""
    return name in TOOLS_SKIP_BLOCKED_DETECTION


def is_subagent_tool_name(name: str) -> bool:
    """Check if tool is a subagent tool."""
    return name in SUBAGENT_TOOL_NAMES


def is_edit_tool(tool_name: str) -> bool:
    """Check if tool is an edit tool."""
    return tool_name in EDIT_TOOLS


def is_write_edit_tool(tool_name: str) -> bool:
    """Check if tool is a write/edit tool."""
    return tool_name in WRITE_EDIT_TOOLS


def is_file_tool(tool_name: str) -> bool:
    """Check if tool is a file tool."""
    return tool_name in FILE_TOOLS


def is_bash_tool(tool_name: str) -> bool:
    """Check if tool is a bash tool."""
    return tool_name in BASH_TOOLS


def is_mcp_tool(tool_name: str) -> bool:
    """Check if tool is an MCP tool."""
    return tool_name in MCP_TOOLS


def is_read_only_tool(tool_name: str) -> bool:
    """Check if tool is read-only."""
    return tool_name in READ_ONLY_TOOLS
