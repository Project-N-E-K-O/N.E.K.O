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

"""Permission gate for the built-in tools.

Every built-in tool call passes the loop's before-tool hook built here. The
hook classifies the call and asks the host's ``decide_permission`` callback.
This module supplies the plumbing and the default decision; asking the user
and remembering the answer belong to the host.

Categories and their default (design section 10.1):

=================  ======================================================  =======
category           what                                                    default
=================  ======================================================  =======
``workdir_files``  reading or writing a path inside the working directory  allow
``outside_read``   reading a path outside the working directory            allow
``outside_write``  writing a path outside the working directory            ask
``run_command``    running a shell command                                 ask
``other``          tools that touch neither files nor the shell            allow
=================  ======================================================  =======

The host is consulted for every category except ``other``. Without a host
callback the default applies and ``ask`` is refused, because nobody can be
asked.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from nekoclaw.runtime.host import HostBridge
from nekoclaw.runtime.loop.messages import ToolCall
from nekoclaw.runtime.tools.base import resolve_path

Category = Literal["workdir_files", "outside_read", "outside_write", "run_command", "other"]
Decision = Literal["allow", "ask"]

CATEGORIES: tuple[str, ...] = (
    "workdir_files",
    "outside_read",
    "outside_write",
    "run_command",
    "other",
)

_READ_TOOLS = {"read": "file_path", "read_image": "file_path", "glob": "path", "grep": "path"}
_WRITE_TOOLS = {"write": "file_path", "edit": "file_path"}
_COMMAND_TOOLS = frozenset({"pwsh", "bash"})
_CANCEL_POLL_SECONDS = 0.05

NO_APPROVER_REASON = (
    "permission denied: this action needs the user's approval and no approver is connected"
)


@dataclass(frozen=True, slots=True)
class PermissionRequest:
    """One tool call, classified."""

    tool: str
    category: Category
    workdir: str
    path: str | None = None
    command: str | None = None
    # Directory a command runs in: the session's working directory unless
    # the call names another one. None for tools that run no command.
    cwd: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "category": self.category,
            "path": self.path,
            "command": self.command,
            "cwd": self.cwd,
            "workdir": self.workdir,
        }


def _real(path: Path) -> str:
    """Resolve symbolic links, also for a path that does not exist yet."""
    return os.path.normcase(os.path.realpath(path))


def is_inside(path: Path, workdir: Path) -> bool:
    """Return whether ``path`` lies in ``workdir`` once links are resolved."""
    root = _real(workdir)
    target = _real(path)
    if target == root:
        return True
    try:
        return os.path.commonpath([root, target]) == root
    except ValueError:
        # Different drives on Windows.
        return False


def classify(tool: str, arguments: Mapping[str, Any], workdir: Path) -> PermissionRequest:
    """Classify one tool call into a permission category."""
    base = str(workdir)
    if tool in _COMMAND_TOOLS:
        # The approver must see where the command really runs.
        override = arguments.get("workdir")
        cwd = (
            resolve_path(workdir, override)
            if isinstance(override, str) and override.strip()
            else workdir
        )
        return PermissionRequest(
            tool,
            "run_command",
            base,
            command=str(arguments.get("command", "")),
            cwd=str(cwd),
        )
    writes = tool in _WRITE_TOOLS
    if writes or tool in _READ_TOOLS:
        key = _WRITE_TOOLS[tool] if writes else _READ_TOOLS[tool]
        raw = arguments.get(key)
        path = resolve_path(workdir, raw) if isinstance(raw, str) and raw.strip() else workdir
        if is_inside(path, workdir):
            category: Category = "workdir_files"
        else:
            category = "outside_write" if writes else "outside_read"
        return PermissionRequest(tool, category, base, path=str(path))
    return PermissionRequest(tool, "other", base)


def default_permission_decision(request: PermissionRequest) -> Decision:
    """Return the built-in decision for a request; see the module docstring."""
    if request.category in ("outside_write", "run_command"):
        return "ask"
    return "allow"


BeforeToolCall = Callable[[ToolCall], Awaitable[tuple[bool, "str | None"]]]


def make_permission_hook(
    *,
    workdir: Path,
    host: HostBridge,
    session_id: str,
    is_cancelled: Callable[[], bool],
) -> BeforeToolCall:
    """Build the loop's before-tool hook. It returns ``(blocked, reason)``."""

    async def consult(payload: dict[str, Any]) -> dict[str, Any] | None:
        task = asyncio.ensure_future(host.call(session_id, "decide_permission", payload))
        try:
            while True:
                done, _pending = await asyncio.wait({task}, timeout=_CANCEL_POLL_SECONDS)
                if done:
                    return task.result()
                if is_cancelled():
                    return None
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def before_tool_call(call: ToolCall) -> tuple[bool, str | None]:
        request = classify(call.name, call.arguments, workdir)
        if request.category == "other":
            return False, None
        default = default_permission_decision(request)
        if "decide_permission" not in host.capabilities:
            return (False, None) if default == "allow" else (True, NO_APPROVER_REASON)
        try:
            reply = await consult({**request.to_json(), "default": default})
        except Exception as exc:  # noqa: BLE001 - a failing approver must fail closed
            return True, f"permission denied: the permission check failed ({exc})"
        if reply is None:
            return True, "Operation aborted"
        if reply.get("allow") is True:
            return False, None
        reason = reply.get("reason")
        detail = reason if isinstance(reason, str) and reason.strip() else "not allowed by the user"
        return True, f"permission denied: {detail}"

    return before_tool_call
