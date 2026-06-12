# Ported from claudian/src/providers/claude/runtime/ClaudeApprovalHandler.ts
# Original author: Claudian contributors
# License: MIT

"""
ClaudeApprovalHandler — canUseTool callback for permission management.

Wraps ApprovalManager + catgirl LLM decision logic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, Protocol

logger = logging.getLogger(__name__)

# Tool name constants (ported from core/tools/toolNames.ts)
TOOL_ASK_USER_QUESTION = "AskUserQuestion"
TOOL_EXIT_PLAN_MODE = "ExitPlanMode"
TOOL_SKILL = "Skill"


class PermissionMode:
    """Permission modes matching Claude Code SDK."""
    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    BYPASS_PERMISSIONS = "bypassPermissions"
    PLAN = "plan"


class PermissionResult:
    """Result of a permission check."""
    def __init__(self, behavior: str, message: str = "", interrupt: bool = False,
                 updated_input: Any = None, updated_permissions: list | None = None):
        self.behavior = behavior  # "allow" or "deny"
        self.message = message
        self.interrupt = interrupt
        self.updated_input = updated_input
        self.updated_permissions = updated_permissions or []


class ApprovalDecision:
    """Possible approval decisions."""
    ALLOW = "allow"
    ALLOW_ALWAYS = "allow-always"
    DENY = "deny"
    CANCEL = "cancel"


@dataclass
class ExitPlanModeDecision:
    """Decision from exit plan mode callback."""
    type: str  # "approve" or "feedback"
    text: str = ""


# Callback type aliases
ApprovalCallback = Callable[[str, Any, str, dict], Awaitable[str]]
AskUserQuestionCallback = Callable[[Any, Any], Awaitable[Optional[dict]]]
ExitPlanModeCallback = Callable[[Any, Any], Awaitable[Optional[ExitPlanModeDecision]]]


@dataclass
class ClaudeApprovalHandlerDeps:
    """Dependencies for ClaudeApprovalHandler."""
    get_allowed_tools: Callable[[], Optional[list[str]]]
    get_approval_callback: Callable[[], Optional[ApprovalCallback]]
    get_ask_user_question_callback: Callable[[], Optional[AskUserQuestionCallback]]
    get_exit_plan_mode_callback: Callable[[], Optional[ExitPlanModeCallback]]
    get_permission_mode: Callable[[], str]
    resolve_sdk_permission_mode: Callable[[str], str]
    sync_permission_mode: Callable[[str, str], None]


def get_action_description(tool_name: str, input_data: Any) -> str:
    """Get human-readable description of a tool action.

    Ported from core/security/ApprovalManager.ts getActionDescription.
    """
    if not isinstance(input_data, dict):
        return f"Execute {tool_name}"

    descriptions = {
        "Write": lambda d: f"Write to {d.get('file_path', 'unknown file')}",
        "Edit": lambda d: f"Edit {d.get('file_path', 'unknown file')}",
        "Bash": lambda d: f"Run command: {d.get('command', 'unknown')}",
        "Read": lambda d: f"Read {d.get('file_path', 'unknown file')}",
        "Glob": lambda d: f"Search files: {d.get('pattern', 'unknown pattern')}",
        "Grep": lambda d: f"Search content: {d.get('pattern', 'unknown pattern')}",
        "WebFetch": lambda d: f"Fetch URL: {d.get('url', 'unknown URL')}",
        "WebSearch": lambda d: f"Search web: {d.get('query', 'unknown query')}",
        "Agent": lambda d: f"Launch agent: {d.get('description', 'unknown')}",
    }

    if tool_name in descriptions:
        try:
            return descriptions[tool_name](input_data)
        except Exception:
            pass

    return f"Execute {tool_name}"


def create_claude_approval_callback(deps: ClaudeApprovalHandlerDeps) -> Callable:
    """Create a canUseTool callback for Claude SDK.

    This is the main entry point - creates an async callback that handles
    permission checks for tool usage.

    Ported from ClaudeApprovalHandler.ts createClaudeApprovalCallback.
    """

    async def can_use_tool(tool_name: str, input_data: Any, options: Any) -> PermissionResult:
        # Check allowed tools list
        current_allowed_tools = deps.get_allowed_tools()
        if current_allowed_tools is not None:
            if tool_name not in current_allowed_tools and tool_name != TOOL_SKILL:
                allowed_list = (
                    f" Allowed tools: {', '.join(current_allowed_tools)}."
                    if current_allowed_tools
                    else " No tools are allowed for this query type."
                )
                return PermissionResult(
                    behavior="deny",
                    message=f'Tool "{tool_name}" is not allowed for this query.{allowed_list}'
                )

        # Handle ExitPlanMode tool
        exit_plan_mode_callback = deps.get_exit_plan_mode_callback()
        if tool_name == TOOL_EXIT_PLAN_MODE and exit_plan_mode_callback:
            try:
                signal = getattr(options, 'signal', None)
                decision = await exit_plan_mode_callback(input_data, signal)
                if decision is None:
                    return PermissionResult(behavior="deny", message="User cancelled.", interrupt=True)
                if decision.type == "feedback":
                    return PermissionResult(behavior="deny", message=decision.text, interrupt=False)

                permission_mode = deps.get_permission_mode()
                sdk_mode = deps.resolve_sdk_permission_mode(permission_mode)
                deps.sync_permission_mode(permission_mode, sdk_mode)
                return PermissionResult(
                    behavior="allow",
                    updated_input=input_data,
                    updated_permissions=[
                        {"type": "setMode", "mode": sdk_mode, "destination": "session"}
                    ]
                )
            except Exception as error:
                return PermissionResult(
                    behavior="deny",
                    message=f"Failed to handle plan mode exit: {str(error)}",
                    interrupt=True
                )

        # Handle AskUserQuestion tool
        ask_user_question_callback = deps.get_ask_user_question_callback()
        if tool_name == TOOL_ASK_USER_QUESTION and ask_user_question_callback:
            try:
                # Inject isOther into questions (matching Claude Code CLI behavior)
                if isinstance(input_data, dict):
                    questions = input_data.get("questions", [])
                    if isinstance(questions, list):
                        for q in questions:
                            if isinstance(q, dict) and "isOther" not in q:
                                q["isOther"] = True

                signal = getattr(options, 'signal', None)
                answers = await ask_user_question_callback(input_data, signal)
                if answers is None:
                    return PermissionResult(behavior="deny", message="User declined to answer.", interrupt=True)
                return PermissionResult(
                    behavior="allow",
                    updated_input={**input_data, "answers": answers}
                )
            except Exception as error:
                return PermissionResult(
                    behavior="deny",
                    message=f"Failed to get user answers: {str(error)}",
                    interrupt=True
                )

        # General approval callback
        approval_callback = deps.get_approval_callback()
        if not approval_callback:
            return PermissionResult(behavior="deny", message="No approval handler available.")

        try:
            decision_reason = getattr(options, 'decisionReason', None)
            blocked_path = getattr(options, 'blockedPath', None)
            agent_id = getattr(options, 'agentID', None)

            description = get_action_description(tool_name, input_data)
            decision = await approval_callback(
                tool_name,
                input_data,
                description,
                {"decisionReason": decision_reason, "blockedPath": blocked_path, "agentID": agent_id}
            )

            if decision == ApprovalDecision.CANCEL:
                return PermissionResult(behavior="deny", message="User interrupted.", interrupt=True)

            if decision in (ApprovalDecision.ALLOW, ApprovalDecision.ALLOW_ALWAYS):
                # Build permission updates (simplified version)
                updated_permissions = []
                if decision == ApprovalDecision.ALWAYS:
                    updated_permissions.append({
                        "type": "addRules",
                        "rules": [{"tool": tool_name, "behavior": "allow"}],
                        "destination": "session"
                    })
                return PermissionResult(
                    behavior="allow",
                    updated_input=input_data,
                    updated_permissions=updated_permissions
                )

            return PermissionResult(behavior="deny", message="User denied this action.", interrupt=False)
        except Exception as error:
            return PermissionResult(
                behavior="deny",
                message=f"Approval request failed: {str(error)}",
                interrupt=False
            )

    return can_use_tool
