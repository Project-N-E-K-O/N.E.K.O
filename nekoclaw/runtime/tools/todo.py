# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The ``todo_write`` tool; texts follow dsh ``packages/todo/tool-todo``."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from nekoclaw.runtime.loop.tools import AgentTool
from nekoclaw.runtime.tools.base import ToolCallInfo, ToolContext, ToolError, define_tool

STATUSES = ("pending", "in_progress", "completed")


class TodoState:
    """The session's current task list. ``None`` means no list was written."""

    def __init__(self, on_change: Callable[[], None] | None = None) -> None:
        self.items: list[dict[str, str]] | None = None
        self._on_change = on_change

    def write(self, raw: list[dict[str, Any]]) -> list[dict[str, str]]:
        todos: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in raw:
            content = str(item.get("content", "")).strip()
            if not content:
                raise ToolError("invalid todo: `content` must be a non-empty string")
            if content in seen:
                raise ToolError(
                    f"invalid todos: duplicate content {json.dumps(content, ensure_ascii=False)}"
                )
            seen.add(content)
            todos.append({"content": content, "status": str(item["status"])})
        self.items = todos
        if self._on_change is not None:
            self._on_change()
        return todos

    def restore(self, items: object) -> None:
        if isinstance(items, list):
            self.items = [
                {"content": str(item["content"]), "status": str(item["status"])}
                for item in items
                if isinstance(item, dict) and "content" in item and item.get("status") in STATUSES
            ]

    def open_items(self) -> list[dict[str, str]]:
        """Return the list while anything on it is unfinished, else nothing."""
        items = self.items or []
        if any(item["status"] != "completed" for item in items):
            return list(items)
        return []


def build_todo_tool(ctx: ToolContext, schemas: dict[str, dict[str, Any]]) -> list[AgentTool]:
    async def todo_write(args: dict[str, Any], call: ToolCallInfo) -> str:
        del call
        todos = ctx.todos.write(list(args["todos"]))
        counts = {status: sum(1 for todo in todos if todo["status"] == status) for status in STATUSES}
        return (
            f"Updated todo list: {counts['pending']} pending, {counts['in_progress']} in progress, "
            f"{counts['completed']} completed."
        )

    return [define_tool(schemas["todo_write"], todo_write)]
