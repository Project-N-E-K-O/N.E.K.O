# Ported from claudian/src/providers/claude/runtime/ClaudeTaskResultInterpreter.ts
# Original author: Claudian contributors
# License: MIT

"""
ClaudeTaskResultInterpreter — Interpret task results from Claude SDK.

Handles extraction of agent IDs, structured results, and terminal status
from tool use results.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional, Protocol

logger = logging.getLogger(__name__)


class ProviderTaskTerminalStatus:
    """Terminal status for provider tasks."""
    COMPLETED = "completed"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class ProviderTaskResultInterpreter(Protocol):
    """Protocol for interpreting task results."""

    def has_async_launch_marker(self, tool_use_result: Any) -> bool:
        """Check if the result indicates an async task launch."""
        ...

    def extract_agent_id(self, tool_use_result: Any) -> Optional[str]:
        """Extract agent ID from tool use result."""
        ...

    def extract_structured_result(self, tool_use_result: Any) -> Optional[str]:
        """Extract structured result text from tool use result."""
        ...

    def resolve_terminal_status(
        self,
        tool_use_result: Any,
        fallback_status: str
    ) -> str:
        """Resolve the terminal status of a task."""
        ...

    def extract_tag_value(self, payload: str, tag_name: str) -> Optional[str]:
        """Extract value of an XML tag from payload."""
        ...


def _extract_agent_id_from_string(value: str) -> Optional[str]:
    """Extract agent ID from a string using regex patterns."""
    patterns = [
        r'"agent_id"\s*:\s*"([^"]+)"',
        r'"agentId"\s*:\s*"([^"]+)"',
        r'agent_id[=:]\s*"?([a-zA-Z0-9_-]+)"?',
        r'agentId[=:]\s*"?([a-zA-Z0-9_-]+)"?',
    ]

    for pattern in patterns:
        match = re.search(pattern, value, re.IGNORECASE)
        if match and match.group(1):
            return match.group(1)

    return None


def _extract_agent_id_from_tool_use_result(tool_use_result: Any) -> Optional[str]:
    """Extract agent ID directly from tool use result object."""
    if not isinstance(tool_use_result, dict):
        return None

    # Check direct fields
    for key in ("agent_id", "agentId"):
        value = tool_use_result.get(key)
        if isinstance(value, str) and value:
            return value

    # Check in content
    content = tool_use_result.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, str):
                extracted = _extract_agent_id_from_string(block)
                if extracted:
                    return extracted
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    extracted = _extract_agent_id_from_string(text)
                    if extracted:
                        return extracted
    elif isinstance(content, str):
        return _extract_agent_id_from_string(content)

    return None


def _extract_result_from_task_object(task: Any) -> Optional[str]:
    """Extract result from a task object."""
    if not isinstance(task, dict):
        return None

    result = task.get("result")
    if isinstance(result, str) and result.strip():
        return result.strip()

    output = task.get("output")
    if isinstance(output, str) and output.strip():
        return output.strip()

    return None


def _extract_text_from_content_blocks(content: Any) -> Optional[str]:
    """Extract text from content blocks array."""
    if not isinstance(content, list):
        return None

    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()

    return None


def _resolve_tool_use_result_status(
    tool_use_result: Any,
    fallback_status: str
) -> str:
    """Resolve status from tool use result.

    Ported from ClaudeHistoryStore.ts resolveToolUseResultStatus.
    """
    if not isinstance(tool_use_result, dict):
        return fallback_status

    # Check for error status
    retrieval_status = tool_use_result.get("retrieval_status") or tool_use_result.get("status")
    if isinstance(retrieval_status, str):
        if retrieval_status.lower() == "error":
            return ProviderTaskTerminalStatus.ERROR
        if retrieval_status.lower() in ("completed", "success"):
            return ProviderTaskTerminalStatus.COMPLETED

    # Check for result status
    task = tool_use_result.get("task")
    if isinstance(task, dict):
        task_status = task.get("status")
        if isinstance(task_status, str):
            if task_status.lower() == "error":
                return ProviderTaskTerminalStatus.ERROR
            if task_status.lower() in ("completed", "success"):
                return ProviderTaskTerminalStatus.COMPLETED

    return fallback_status


def _extract_xml_tag(payload: str, tag_name: str) -> Optional[str]:
    """Extract content of an XML tag from payload.

    Ported from ClaudeHistoryStore.ts extractXmlTag.
    """
    pattern = rf"<{re.escape(tag_name)}>(.*?)</{re.escape(tag_name)}>"
    match = re.search(pattern, payload, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


class ClaudeTaskResultInterpreter:
    """Claude-specific implementation of ProviderTaskResultInterpreter.

    Ported from ClaudeTaskResultInterpreter.ts.
    """

    def has_async_launch_marker(self, tool_use_result: Any) -> bool:
        """Check if the result indicates an async task launch."""
        if not isinstance(tool_use_result, dict):
            return False

        # Direct async flag
        if tool_use_result.get("isAsync") is True:
            return True

        # Status-based check
        raw_status = tool_use_result.get("retrieval_status") or tool_use_result.get("status")
        if isinstance(raw_status, str) and raw_status.lower() == "async_launched":
            return True

        # Output file presence (only when no explicit async marker)
        output_file = tool_use_result.get("outputFile")
        if isinstance(output_file, str) and output_file:
            return True

        return False

    def extract_agent_id(self, tool_use_result: Any) -> Optional[str]:
        """Extract agent ID from tool use result."""
        # Try direct extraction first
        direct_id = _extract_agent_id_from_tool_use_result(tool_use_result)
        if direct_id:
            return direct_id

        if not isinstance(tool_use_result, dict):
            return None

        # Check content array
        content = tool_use_result.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, str):
                    extracted = _extract_agent_id_from_string(block)
                    if extracted:
                        return extracted
                elif isinstance(block, dict):
                    text = block.get("text")
                    if isinstance(text, str):
                        extracted = _extract_agent_id_from_string(text)
                        if extracted:
                            return extracted
        elif isinstance(content, str):
            return _extract_agent_id_from_string(content)

        return None

    def extract_structured_result(self, tool_use_result: Any) -> Optional[str]:
        """Extract structured result text from tool use result."""
        if not isinstance(tool_use_result, dict):
            return None

        # Check for error status
        if tool_use_result.get("retrieval_status") == "error":
            error = tool_use_result.get("error")
            error_msg = error if isinstance(error, str) else "Task retrieval failed"
            return f"Error: {error_msg}"

        # Try task result
        task = tool_use_result.get("task")
        task_result = _extract_result_from_task_object(task)
        if task_result:
            return task_result

        # Try direct result
        result = tool_use_result.get("result")
        if isinstance(result, str) and result.strip():
            return result.strip()

        # Try output
        output = tool_use_result.get("output")
        if isinstance(output, str) and output.strip():
            return output.strip()

        # Try content blocks
        content = tool_use_result.get("content")
        return _extract_text_from_content_blocks(content)

    def resolve_terminal_status(
        self,
        tool_use_result: Any,
        fallback_status: str
    ) -> str:
        """Resolve the terminal status of a task."""
        resolved = _resolve_tool_use_result_status(tool_use_result, fallback_status)
        if resolved == ProviderTaskTerminalStatus.ERROR:
            return ProviderTaskTerminalStatus.ERROR
        if resolved == ProviderTaskTerminalStatus.COMPLETED:
            return ProviderTaskTerminalStatus.COMPLETED
        return fallback_status

    def extract_tag_value(self, payload: str, tag_name: str) -> Optional[str]:
        """Extract value of an XML tag from payload."""
        return _extract_xml_tag(payload, tag_name)
