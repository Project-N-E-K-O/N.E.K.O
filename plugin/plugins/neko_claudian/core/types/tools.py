# Ported from claudian/src/core/types/tools.ts
# Original author: Claudian contributors
# License: MIT

"""
Tool type definitions — tool calls, subagents, AskUserQuestion, ExitPlanMode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List, Optional, Union

from .diff import DiffLine, DiffStats


@dataclass
class ToolDiffData:
    """Diff data for Write/Edit tool operations."""
    file_path: str = ""
    diff_lines: List[DiffLine] = field(default_factory=list)
    stats: Optional[DiffStats] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "filePath": self.file_path,
            "diffLines": [dl.to_dict() for dl in self.diff_lines],
            "stats": self.stats.to_dict() if self.stats else None,
        }


@dataclass
class AskUserQuestionOption:
    """Parsed option for AskUserQuestion tool."""
    label: str = ""
    description: str = ""
    value: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "label": self.label,
            "description": self.description,
        }
        if self.value:
            out["value"] = self.value
        return out


@dataclass
class AskUserQuestionItem:
    """Parsed question for AskUserQuestion tool."""
    question: str = ""
    id: Optional[str] = None
    header: str = ""
    options: List[AskUserQuestionOption] = field(default_factory=list)
    multi_select: bool = False
    is_other: bool = False
    is_secret: bool = False

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "question": self.question,
            "header": self.header,
            "options": [o.to_dict() for o in self.options],
            "multiSelect": self.multi_select,
        }
        if self.id:
            out["id"] = self.id
        if self.is_other:
            out["isOther"] = True
        if self.is_secret:
            out["isSecret"] = True
        return out


# User-provided answers keyed by question text or stable question id
AskUserAnswers = Dict[str, Union[str, List[str]]]


class SubagentMode(str, Enum):
    """Subagent execution mode."""
    SYNC = "sync"
    ASYNC = "async"


class AsyncSubagentStatus(str, Enum):
    """Async subagent lifecycle states."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"
    ORPHANED = "orphaned"


@dataclass
class SubagentInfo:
    """Subagent (Agent tool, legacy Task) tracking."""
    id: str = ""
    description: str = ""
    prompt: Optional[str] = None
    mode: Optional[SubagentMode] = None
    is_expanded: bool = False
    result: Optional[str] = None
    status: str = "running"  # "running" | "completed" | "error"
    tool_calls: List[ToolCallInfo] = field(default_factory=list)
    async_status: Optional[AsyncSubagentStatus] = None
    agent_id: Optional[str] = None
    output_tool_id: Optional[str] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": self.id,
            "description": self.description,
            "isExpanded": self.is_expanded,
            "status": self.status,
            "toolCalls": [tc.to_dict() for tc in self.tool_calls],
        }
        if self.prompt:
            out["prompt"] = self.prompt
        if self.mode:
            out["mode"] = self.mode.value
        if self.result:
            out["result"] = self.result
        if self.async_status:
            out["asyncStatus"] = self.async_status.value
        if self.agent_id:
            out["agentId"] = self.agent_id
        if self.output_tool_id:
            out["outputToolId"] = self.output_tool_id
        if self.started_at:
            out["startedAt"] = self.started_at
        if self.completed_at:
            out["completedAt"] = self.completed_at
        return out


@dataclass
class ToolCallInfo:
    """Tool call tracking with status and result."""
    id: str = ""
    name: str = ""
    input: Dict[str, Any] = field(default_factory=dict)
    status: str = "running"  # "running" | "completed" | "error" | "blocked"
    result: Optional[str] = None
    is_expanded: bool = False
    diff_data: Optional[ToolDiffData] = None
    resolved_answers: Optional[AskUserAnswers] = None
    subagent: Optional[SubagentInfo] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "input": self.input,
            "status": self.status,
        }
        if self.result:
            out["result"] = self.result
        if self.is_expanded:
            out["isExpanded"] = True
        if self.diff_data:
            out["diffData"] = self.diff_data.to_dict()
        if self.resolved_answers:
            out["resolvedAnswers"] = self.resolved_answers
        if self.subagent:
            out["subagent"] = self.subagent.to_dict()
        return out


# ExitPlanModeDecision is a union type in TypeScript
# In Python we use a dict with type discriminator
@dataclass
class ExitPlanModeDecision:
    """Decision from exit plan mode."""
    type: str = "approve"  # "approve" | "approve-new-session" | "feedback"
    text: Optional[str] = None
    plan_content: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"type": self.type}
        if self.text:
            out["text"] = self.text
        if self.plan_content:
            out["planContent"] = self.plan_content
        return out


# ExitPlanModeCallback type
ExitPlanModeCallback = Callable[
    [Dict[str, Any], Optional[Any]],  # (input, signal)
    Coroutine[Any, Any, Optional[ExitPlanModeDecision]]
]
