# Ported from claudian/src/core/tools/todo.ts
# Original author: Claudian contributors
# License: MIT

"""
Todo tool helpers — Parses TodoWrite tool input into typed todo items.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from .tool_names import TOOL_TODO_WRITE


@dataclass
class TodoItem:
    """A single todo item."""
    content: str = ""  # Imperative description (e.g., "Run tests")
    status: str = "pending"  # "pending" | "in_progress" | "completed"
    active_form: str = ""  # Present continuous form (e.g., "Running tests")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content": self.content,
            "status": self.status,
            "activeForm": self.active_form,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Optional[TodoItem]:
        if not isinstance(data, dict):
            return None

        content = data.get("content")
        status = data.get("status")
        active_form = data.get("activeForm")

        if not isinstance(content, str) or not content:
            return None
        if not isinstance(active_form, str) or not active_form:
            return None
        if status not in ("pending", "in_progress", "completed"):
            return None

        return cls(content=content, status=status, active_form=active_form)


def parse_todo_input(input_data: Dict[str, Any]) -> Optional[List[TodoItem]]:
    """Parse TodoWrite tool input into typed todo items.

    Ported from todo.ts parseTodoInput.
    """
    todos = input_data.get("todos")
    if not isinstance(todos, list):
        return None

    valid_todos: List[TodoItem] = []
    for item in todos:
        todo = TodoItem.from_dict(item)
        if todo:
            valid_todos.append(todo)

    return valid_todos if valid_todos else None


def extract_last_todos_from_messages(
    messages: List[Dict[str, Any]],
) -> Optional[List[TodoItem]]:
    """Extract the last TodoWrite todos from a list of messages.

    Used to restore the todo panel when loading a saved conversation.

    Ported from todo.ts extractLastTodosFromMessages.
    """
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue

        tool_calls = msg.get("toolCalls") or msg.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue

        for tc in reversed(tool_calls):
            if not isinstance(tc, dict):
                continue
            if tc.get("name") == TOOL_TODO_WRITE:
                todos = parse_todo_input(tc.get("input", {}))
                if todos:
                    return todos

    return None
