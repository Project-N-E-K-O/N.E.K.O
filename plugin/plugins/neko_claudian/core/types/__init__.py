# Ported from claudian/src/core/types/index.ts
# Original author: Claudian contributors
# License: MIT

"""
Core type definitions for Neko Claudian plugin.

Re-exports all type modules for convenient importing.
"""

from __future__ import annotations

# Chat types
from .chat import (
    ChatMessage,
    ContentBlock,
    Conversation,
    ConversationMeta,
    ForkSource,
    ImageAttachment,
    ImageMediaType,
    SessionMetadata,
    StreamChunk,
    UsageInfo,
)

# Provider types
from .provider import ProviderId

# Settings types
from .settings import (
    ApprovalDecision,
    ClaudianSettings,
    EnvironmentScope,
    EnvSnippet,
    InstructionRefineResult,
    KeyboardNavigationSettings,
    PermissionMode,
    SlashCommand,
    TabBarPosition,
)

# Diff types
from .diff import (
    DiffLine,
    DiffStats,
    SDKToolUseResult,
    StructuredPatchHunk,
)

# Tool types
from .tools import (
    AskUserAnswers,
    AskUserQuestionItem,
    AskUserQuestionOption,
    AsyncSubagentStatus,
    ExitPlanModeCallback,
    ExitPlanModeDecision,
    SubagentInfo,
    SubagentMode,
    ToolCallInfo,
    ToolDiffData,
)

# Agent types
from .agent import AgentDefinition, AgentFrontmatter

# Plugin types
from .plugins import PluginInfo, PluginScope

# MCP types
from .mcp import (
    DEFAULT_MCP_SERVER,
    get_mcp_server_type,
    is_valid_mcp_server_config,
    ManagedMcpConfigFile,
    ManagedMcpServer,
    McpConfigFile,
    McpHttpServerConfig,
    McpServerConfig,
    McpServerType,
    McpSSEServerConfig,
    McpStdioServerConfig,
    ParsedMcpConfig,
)

__all__ = [
    # Chat
    "ChatMessage", "ContentBlock", "Conversation", "ConversationMeta",
    "ForkSource", "ImageAttachment", "ImageMediaType", "SessionMetadata",
    "StreamChunk", "UsageInfo",
    # Provider
    "ProviderId",
    # Settings
    "ApprovalDecision", "ClaudianSettings", "EnvironmentScope", "EnvSnippet",
    "InstructionRefineResult", "KeyboardNavigationSettings", "PermissionMode",
    "SlashCommand", "TabBarPosition",
    # Diff
    "DiffLine", "DiffStats", "SDKToolUseResult", "StructuredPatchHunk",
    # Tools
    "AskUserAnswers", "AskUserQuestionItem", "AskUserQuestionOption",
    "AsyncSubagentStatus", "ExitPlanModeCallback", "ExitPlanModeDecision",
    "SubagentInfo", "SubagentMode", "ToolCallInfo", "ToolDiffData",
    # Agent
    "AgentDefinition", "AgentFrontmatter",
    # Plugin
    "PluginInfo", "PluginScope",
    # MCP
    "DEFAULT_MCP_SERVER", "get_mcp_server_type", "is_valid_mcp_server_config",
    "ManagedMcpConfigFile", "ManagedMcpServer", "McpConfigFile",
    "McpHttpServerConfig", "McpServerConfig", "McpServerType",
    "McpSSEServerConfig", "McpStdioServerConfig", "ParsedMcpConfig",
]
