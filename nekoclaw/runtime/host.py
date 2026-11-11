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

"""Callbacks the host injects into the runtime.

Some tools cannot be carried out inside the runtime: asking the user, filing a
report, storing a lesson, searching the web and starting further familiars all
belong to the host. The runtime defines those tools and executes them through
the callbacks below. A tool whose callback is missing is not offered to the
model at all.

Every callback has the same shape::

    async def callback(session_id: str, payload: dict) -> dict | None

``payload`` and the returned dict are plain JSON, because the subprocess shell
forwards them over a pipe. Raising an exception makes the tool call fail with
the exception text as its error. A callback may take as long as it needs (a
user may answer hours later); it is cancelled when the run is aborted.

Callbacks, their payload and their result:

``decide_permission``
    Asked before a built-in tool acts. Payload::

        {"tool": str, "category": str, "path": str | None, "command": str | None,
         "workdir": str, "default": "allow" | "ask"}

    ``category`` is one of ``nekoclaw.runtime.permissions.CATEGORIES`` and
    ``default`` is what ``default_permission_decision`` returns for the request.
    Result: ``{"allow": bool, "reason": str | None}``. Without this callback
    the default decision applies and ``ask`` is refused, since nobody can be
    asked.

``ask_user``  (tool ``ask_user_question``)
    Payload: ``{"questions": [{"id", "question", "header"?, "options"?,
    "multi_select"?}, ...]}`` exactly as the model wrote them. Result:
    ``{"answers": [{"id": str, "selected": [str, ...], "custom": str?}, ...]}``.
    An item with empty ``selected`` and no ``custom`` means the user skipped it.

``report``  (tool ``report``)
    Payload: ``{"status": "progress" | "waiting" | "ready" | "blocked" |
    "failed", "summary": str, "details": str | None, "delivery": "proactive" |
    "passive" | "silent" | None}``. Result: ``None`` or ``{"message": str}``,
    a note returned to the model. The runtime also emits a ``report`` event.

``note_lesson``  (tool ``note_lesson``)
    Payload: ``{"title": str, "content": str, "category": "feedback" |
    "lesson" | "reflection" | "reference" | "progress", "scope": "project" |
    "global"}``. Result: ``None`` or ``{"message": str}``.

``web_search``  (tool ``web_search``)
    Payload: ``{"queries": [str, ...]}`` (1 to 10, already de-duplicated).
    Result: ``{"results": [{"query": str, "answer": str?, "sources": [{"url":
    str, "title": str?, "snippet": str?, "published_at": str?}, ...]}, ...]}``
    with one entry per query, in order. The runtime merges and formats them.

``subagent_start``  (tool ``subagent``)
    Payload: ``{"description": str, "prompt": str, "name": str | None, "cwd":
    str | None}``. Result: ``{"name": str}``, the name the child is addressed
    by. The call returns once the child is registered, not when it finishes;
    the child's result reaches this session later as a ``follow_up`` or
    ``steer`` command.

``subagent_list``  (tool ``list_agents``)
    Payload: ``{}``. Result: ``{"agents": [{"name": str, "status": str,
    "description": str?}, ...]}``, the root first.

``subagent_send``  (tool ``send_message``)
    Payload: ``{"agent": str, "message": str}``. Result: ``None``.

``subagent_interrupt``  (tool ``interrupt_agent``)
    Payload: ``{"agent": str}``. Result: ``None``.

The four ``subagent_*`` callbacks are offered together or not at all;
``wait_agent`` needs no callback and appears with them.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, fields
from typing import Any, Protocol

HostCallback = Callable[[str, dict[str, Any]], Awaitable["dict[str, Any] | None"]]

SUBAGENT_CALLS: tuple[str, ...] = (
    "subagent_start",
    "subagent_list",
    "subagent_send",
    "subagent_interrupt",
)

HOST_CALLS: tuple[str, ...] = (
    "decide_permission",
    "ask_user",
    "report",
    "note_lesson",
    "web_search",
    *SUBAGENT_CALLS,
)


class HostCallError(RuntimeError):
    """A host callback failed or is not available."""


class HostBridge(Protocol):
    """What the runtime needs from the host; see the module docstring."""

    @property
    def capabilities(self) -> frozenset[str]:
        """Return the names from ``HOST_CALLS`` this host can serve."""
        ...

    async def call(self, session_id: str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Run one callback and return its result (an empty dict for ``None``)."""
        ...


@dataclass(slots=True)
class HostCallbacks:
    """In-process ``HostBridge`` built from plain async callables."""

    decide_permission: HostCallback | None = None
    ask_user: HostCallback | None = None
    report: HostCallback | None = None
    note_lesson: HostCallback | None = None
    web_search: HostCallback | None = None
    subagent_start: HostCallback | None = None
    subagent_list: HostCallback | None = None
    subagent_send: HostCallback | None = None
    subagent_interrupt: HostCallback | None = None

    @property
    def capabilities(self) -> frozenset[str]:
        return frozenset(
            item.name for item in fields(self) if getattr(self, item.name) is not None
        )

    async def call(self, session_id: str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        callback = getattr(self, name, None) if name in HOST_CALLS else None
        if callback is None:
            raise HostCallError(f"host callback {name!r} is not available")
        result = await callback(session_id, payload)
        return dict(result) if result else {}


class NoHost:
    """``HostBridge`` with no callbacks: every host-backed tool is withheld."""

    @property
    def capabilities(self) -> frozenset[str]:
        return frozenset()

    async def call(self, session_id: str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        del session_id, payload
        raise HostCallError(f"host callback {name!r} is not available")


def offers_subagents(capabilities: frozenset[str]) -> bool:
    """Return whether the subagent tool group can be offered."""
    return all(name in capabilities for name in SUBAGENT_CALLS)
