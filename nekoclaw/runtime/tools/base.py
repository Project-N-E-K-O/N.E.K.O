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

"""Shared pieces of the built-in tools: context, errors, argument checks."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from nekoclaw.runtime.loop.tools import (
    AgentTool,
    AgentToolResult,
    ToolCancellationToken,
    ToolUpdateCallback,
)

if TYPE_CHECKING:
    from nekoclaw.runtime.host import HostBridge
    from nekoclaw.runtime.protocol import RuntimeLimits
    from nekoclaw.runtime.tools.goal import GoalState
    from nekoclaw.runtime.tools.jobs import JobRegistry
    from nekoclaw.runtime.tools.todo import TodoState

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Set by the model adapter when a tool call's arguments were not valid JSON.
INVALID_ARGUMENTS_KEY = "__invalid_json_arguments__"

_CANCEL_POLL_SECONDS = 0.05


class ToolError(Exception):
    """A failure the model should see. The text becomes ``Error: <text>``."""


class ToolAborted(ToolError):
    """The run was aborted while the tool was working."""

    def __init__(self) -> None:
        super().__init__("tool call aborted")


@dataclass(frozen=True, slots=True)
class ToolCallInfo:
    """Per-call facilities handed to a tool implementation."""

    id: str
    name: str
    signal: ToolCancellationToken | None
    on_update: ToolUpdateCallback | None

    def cancelled(self) -> bool:
        return self.signal is not None and self.signal.is_cancelled()

    def progress(self, text: str) -> None:
        """Report output produced so far; shown while the tool is still running."""
        if self.on_update is not None and text:
            self.on_update(AgentToolResult(content=text))


class SpillStore:
    """Writes full tool output to files the model can read back.

    Files live in a directory the session owns. Saving is best effort: a
    failure returns ``None`` and the caller keeps what it has.

    File names start with a number that keeps growing across restarts of the
    session: the transcript of a resumed session still points at the files
    written before, so none of them may be reused.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._lock = threading.Lock()
        # None until the directory has been looked at.
        self._counter: int | None = None

    def _highest_number(self) -> int:
        """Return the largest number already used by a file in the directory."""
        highest = 0
        try:
            names = os.listdir(self.directory)
        except OSError:
            return highest
        for name in names:
            digits = name.partition("-")[0]
            if digits.isdecimal():
                highest = max(highest, int(digits))
        return highest

    def reserve(self, hint: str, suffix: str = ".txt") -> Path | None:
        """Return a fresh path below the spill directory without writing it."""
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in hint)[:40] or "output"
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        with self._lock:
            if self._counter is None:
                self._counter = self._highest_number()
            self._counter += 1
            number = self._counter
        return self.directory / f"{number:04d}-{safe}{suffix}"

    def save(self, hint: str, text: str) -> Path | None:
        """Write ``text`` as UTF-8 and return its path, or ``None`` on failure."""
        path = self.reserve(hint)
        if path is None:
            return None
        try:
            path.write_bytes(text.encode("utf-8", errors="replace"))
        except OSError:
            return None
        return path


class SessionInbox:
    """What tools may know about the session's pending input.

    The session implements this; tools only read it.
    """

    def has_pending_input(self) -> bool:
        """Return whether a message is queued for the model."""
        raise NotImplementedError

    def has_pending_user_steer(self) -> bool:
        """Return whether text from the user is queued for the current run."""
        raise NotImplementedError

    async def wait_for_input(self, timeout_seconds: float) -> bool:
        """Wait for queued input; return False when the wait timed out."""
        raise NotImplementedError

    def in_direct_human_turn(self) -> bool:
        """Return whether the current run was given text by the user."""
        raise NotImplementedError

    def in_goal_round(self, goal_id: str, revision: int, round_number: int) -> bool:
        """Return whether the current run is the given automatic goal round."""
        raise NotImplementedError

    def deliver_notice(self, kind: str, text: str) -> object:
        """Queue a runtime notice for the model; returns a handle for ``withdraw_notice``.

        A running session receives it before its next model request. An idle
        session is woken with it.
        """
        raise NotImplementedError

    def withdraw_notice(self, handle: object) -> bool:
        """Remove a queued notice; returns False when the model already has it."""
        raise NotImplementedError


@dataclass(slots=True)
class ToolContext:
    """Session-level services shared by the built-in tools."""

    session_id: str
    workdir: Path
    spill: SpillStore
    host: "HostBridge"
    limits: "RuntimeLimits"
    supports_images: bool
    platform: str
    inbox: SessionInbox
    jobs: "JobRegistry"
    todos: "TodoState"
    goals: "GoalState"
    # Called with the report payload once the host accepted it.
    emit_report: Callable[[dict[str, Any]], None]
    top_level: bool = True

    def resolve(self, raw: str) -> Path:
        """Resolve a model-supplied path against the session working directory."""
        return resolve_path(self.workdir, raw)


def resolve_path(workdir: Path, raw: str) -> Path:
    """Return the absolute, normalized form of a model-supplied path."""
    expanded = os.path.expanduser(raw.strip())
    candidate = Path(expanded)
    if not candidate.is_absolute():
        candidate = workdir / candidate
    return Path(os.path.abspath(candidate))


def text_result(
    text: str,
    *,
    terminate: bool | None = None,
    details: Any = None,
) -> AgentToolResult:
    return AgentToolResult(content=text, details=details, terminate=terminate)


async def run_cancellable(awaitable: Awaitable[T], call: ToolCallInfo) -> T:
    """Await ``awaitable`` but give up with ``ToolAborted`` once the run is aborted.

    The loop's cancellation token can only be polled, so the wait is raced
    against a short polling loop.
    """
    task = asyncio.ensure_future(awaitable)
    try:
        while True:
            done, _pending = await asyncio.wait({task}, timeout=_CANCEL_POLL_SECONDS)
            if done:
                return task.result()
            if call.cancelled():
                raise ToolAborted
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


# ---------------------------------------------------------------------------
# Argument validation: the subset of JSON Schema the tool definitions use.
# ---------------------------------------------------------------------------


def _type_ok(expected: str, value: object) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "integer":
        if isinstance(value, bool):
            return False
        return isinstance(value, int) or (isinstance(value, float) and value.is_integer())
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True


def _check(schema: Mapping[str, Any], value: object, where: str) -> None:
    expected = schema.get("type")
    if isinstance(expected, str) and not _type_ok(expected, value):
        article = "an" if expected[0] in "aeio" else "a"
        raise ToolError(f"invalid arguments: {where} must be {article} {expected}")
    options = schema.get("enum")
    if isinstance(options, list) and value not in options:
        allowed = ", ".join(str(item) for item in options)
        raise ToolError(f"invalid arguments: {where} must be one of: {allowed}")
    if isinstance(value, dict):
        properties = schema.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        for name in schema.get("required") or ():
            if name not in value or value[name] is None:
                prefix = "" if where == "arguments" else f"{where}."
                raise ToolError(f"invalid arguments: missing required {prefix}{name}")
        for name, item in value.items():
            sub = properties.get(name)
            if isinstance(sub, dict) and item is not None:
                child = name if where == "arguments" else f"{where}.{name}"
                _check(sub, item, child)
    elif isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                _check(items, item, f"{where}[{index}]")


def validate_arguments(parameters: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Check ``arguments`` against a tool's parameter schema.

    Unknown properties are ignored rather than rejected, and an explicit null
    counts as "not given". Returns the arguments without null entries.
    """
    if INVALID_ARGUMENTS_KEY in arguments:
        raise ToolError(
            "invalid arguments: the arguments were not valid JSON "
            f"({str(arguments[INVALID_ARGUMENTS_KEY])[:200]})"
        )
    cleaned = {key: value for key, value in arguments.items() if value is not None}
    _check(parameters, cleaned, "arguments")
    return cleaned


ToolImplementation = Callable[[dict[str, Any], ToolCallInfo], Awaitable["str | AgentToolResult"]]


def define_tool(
    schema: Mapping[str, Any],
    implementation: ToolImplementation,
    *,
    parallel: bool = False,
) -> AgentTool:
    """Wrap an implementation as a loop tool using a definition from config.

    ``parallel`` marks a read-only tool: the loop may run adjacent calls to
    such tools together.
    """
    name = str(schema["name"])
    parameters: Mapping[str, Any] = schema["parameters"]

    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, Any],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        call = ToolCallInfo(id=tool_call_id, name=name, signal=signal, on_update=on_update)
        cleaned = validate_arguments(parameters, arguments)
        try:
            outcome = await implementation(cleaned, call)
        except (ToolError, asyncio.CancelledError):
            raise
        except Exception as exc:  # noqa: BLE001 - reported to the model, logged here
            logger.exception("tool %s failed unexpectedly", name)
            raise ToolError(f"{name} failed: {type(exc).__name__}: {exc}") from exc
        if isinstance(outcome, AgentToolResult):
            return outcome
        return AgentToolResult(content=outcome)

    return AgentTool(
        name=name,
        label=name,
        description=str(schema["description"]),
        parameters=parameters,
        execute_fn=execute,
        execution_mode="parallel" if parallel else "sequential",
    )


def as_int(value: Any) -> int:
    """Return an integer for a validated ``number`` / ``integer`` argument."""
    return int(value)
