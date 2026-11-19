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

"""The system prompt and tool definitions a session was opened with, kept in its directory.

A session outlasts the commissions worked on in it and is opened anew for
each. Put together anew every time, its system prompt would change whenever
what goes into it changed meanwhile (what the task agent space holds, which
tools are on offer), and with it the head of every request, which a
provider can only cache while it stays the same. So what a session was
opened with is written to ``system.json`` in its directory and read from
there when the session is opened again.

A snapshot is replaced after the conversation was summed up
(``TaskAgentSession``), when the head of the request changes anyway; and
when the session's working directory is no longer the one the snapshot was
made for (the data was moved), because the system prompt names that
directory and every path the task agent writes would be the old one.
Until then the session is told of the tools it was opened with: one that is
gone since is answered for by a stand-in, and one that came since is not
told of. (A change every session has to take up when it is next opened is
made by raising ``_VERSION``: a snapshot of another version counts as none.)

Nothing here is secret: the file holds what every request of the session
sends to the model.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nekoclaw.runtime.loop.tools import AgentTool

logger = logging.getLogger(__name__)

SYSTEM_FILE = "system.json"
# 2: the system prompt no longer names the language of the commission the
# session was opened for, and the report tool no longer says to use the
# language of the user's request. A session saved before is put together
# anew once, when it is next opened, as after a summary: it is then told of
# the tools there are by then, and what it had said is kept as it was.
# 3: the tools that reach other sessions and set a wake (``list_sessions``,
# ``read_session``, ``message_session``, ``schedule_wake``, ``ask_session``).
# A session that is written to is told to answer with one of them, so every
# session has them from when it is next opened, not from its next summary.
_VERSION = 3

# Why a snapshot was made.
REASON_OPENED = "opened"
REASON_COMPACTED = "compacted"
REASON_MOVED = "moved"


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """One tool as the model is told of it."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SystemSnapshot:
    """What every request of a session begins with."""

    system: str
    tools: tuple[ToolDefinition, ...]
    reason: str = REASON_OPENED
    made_at: float = 0.0
    # The working directory the system prompt names; empty when not on record.
    workdir: str = ""
    # How often the conversation had been summed up when this was made; None
    # when not on record. One made before the latest summary is replaced.
    compactions: int | None = None

    @property
    def tool_names(self) -> frozenset[str]:
        return frozenset(tool.name for tool in self.tools)


def definitions_of(tools: Iterable[AgentTool]) -> tuple[ToolDefinition, ...]:
    """Return tools as the model is told of them."""
    return tuple(ToolDefinition(tool.name, tool.description, dict(tool.parameters)) for tool in tools)


def read_snapshot(session_dir: Path) -> SystemSnapshot | None:
    """Return the snapshot kept in a session directory, or None when there is no usable one. Blocking.

    A file that is missing, cannot be read or is not what this module
    writes counts as none: the session is then opened as a new one would be.
    """
    path = session_dir / SYSTEM_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.warning("the system snapshot of a session cannot be read (%s); making a new one", type(exc).__name__)
        return None
    if not isinstance(raw, dict) or raw.get("version") != _VERSION:
        return None
    system, listed = raw.get("system"), raw.get("tools")
    if not isinstance(system, str) or not system or not isinstance(listed, list):
        return None
    tools: list[ToolDefinition] = []
    for item in listed:
        if not isinstance(item, dict):
            return None
        name, description, parameters = item.get("name"), item.get("description"), item.get("parameters")
        if not isinstance(name, str) or not name or not isinstance(description, str) or not isinstance(parameters, dict):
            return None
        tools.append(ToolDefinition(name, description, parameters))
    if len({tool.name for tool in tools}) != len(tools):
        return None
    made_at, workdir, compactions = raw.get("made_at"), raw.get("workdir"), raw.get("compactions")
    return SystemSnapshot(
        system=system,
        tools=tuple(tools),
        reason=str(raw.get("reason") or REASON_OPENED),
        made_at=float(made_at) if isinstance(made_at, (int, float)) and not isinstance(made_at, bool) else 0.0,
        workdir=workdir if isinstance(workdir, str) else "",
        compactions=compactions if isinstance(compactions, int) and not isinstance(compactions, bool) else None,
    )


def write_snapshot(session_dir: Path, snapshot: SystemSnapshot) -> None:
    """Write a snapshot into a session directory, in place of the one there. Blocking."""
    from utils.file_utils import atomic_write_json

    atomic_write_json(
        session_dir / SYSTEM_FILE,
        {
            "version": _VERSION,
            "reason": snapshot.reason,
            "made_at": snapshot.made_at or time.time(),
            "workdir": snapshot.workdir,
            "compactions": snapshot.compactions,
            "system": snapshot.system,
            "tools": [
                {"name": tool.name, "description": tool.description, "parameters": tool.parameters}
                for tool in snapshot.tools
            ],
        },
    )
