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

"""Wire format of the built-in runtime: session spec, commands and events.

Everything here is plain JSON once dumped, so the same messages serve the
in-process runtime and the subprocess shell.

Commands (``type`` field):

* ``start``      - create a session from a ``TaskAgentSpec``.
* ``prompt``     - hand the session work. Rejected while a run is in progress.
* ``steer``      - deliver text before the next model request of the current
  run; on an idle session it starts a run.
* ``follow_up``  - deliver text once the current run has nothing left to do; on
  an idle session it starts a run.
* ``abort``      - stop the current run. Queued steer / follow-up text is dropped.
* ``close``      - stop the run, kill the session's background jobs and forget
  the session.

Events (``type`` field). Every session event carries ``session_id`` and a
per-session ``seq`` that increases by one:

* ``started``     - the session exists; ``tools`` lists what the model is offered.
* ``text``        - an increment of assistant text.
* ``tool_start`` / ``tool_update`` / ``tool_end`` - one tool call; updates
  arrive while the tool is still running.
* ``report``      - a structured report made through the ``report`` tool.
* ``usage``       - token usage of one model request and the context size.
* ``ended``       - a run finished and the session is idle (``completed``,
  ``aborted``, ``error``), or the session is gone (``closed``). A finished
  run states ``inputs``: how many texts (``prompt``, ``steer``,
  ``follow_up``) the session had taken by then. A driver that has sent more
  knows that a later run is under way.
* ``error``       - a command was rejected.

The subprocess shell adds messages of its own: ``host_request`` (the runtime
asks the host to run a callback; answered by a ``host_response`` command), and
the process-level ``runtime_ready``, ``runtime_idle`` and ``runtime_exit``,
which carry no ``session_id``. ``runtime_idle`` is the process asking to exit;
the shell answers it itself and does not pass it on.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

SESSION_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"

# Receives every event as a JSON-ready dict.
EventSink = Callable[[dict[str, Any]], None]

ReportStatus = Literal["progress", "waiting", "ready", "blocked", "failed"]
ReportDelivery = Literal["proactive", "passive", "silent"]
EndReason = Literal["completed", "aborted", "error", "closed"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Session spec
# ---------------------------------------------------------------------------


class ModelConfig(_Strict):
    """Model endpoint of one session. ``http://`` endpoints are allowed."""

    model: str = Field(min_length=1)
    base_url: str | None = None
    # Travels inside the start command only; never read from argv or the environment.
    api_key: str | None = Field(default=None, repr=False)
    provider_type: str | None = None
    context_window: int = Field(default=128_000, ge=4_000)
    max_output_tokens: int = Field(default=8_192, ge=256)
    request_timeout_seconds: float = Field(default=300.0, gt=0)
    max_retries: int = Field(default=2, ge=0)
    supports_images: bool = False
    # ``extra_body`` given: sent as is. Otherwise ``auto_extra_body`` decides
    # between the provider default registered in config.providers and none.
    extra_body: dict[str, Any] | None = None
    auto_extra_body: bool = True


class GoalSpec(_Strict):
    """A goal the session starts with; see the goal tools."""

    objective: str = Field(min_length=1)
    max_rounds: int | None = Field(default=None, ge=1)


class RuntimeLimits(_Strict):
    # Model requests in one run; None leaves it unbounded.
    max_turns: int | None = Field(default=None, ge=1)
    command_timeout_ms: int = Field(default=120_000, ge=1)
    command_max_timeout_ms: int = Field(default=600_000, ge=1)
    # Rounds a goal gets when neither the spec nor the model names a number,
    # and the most the model may ask for.
    goal_default_rounds: int = Field(default=20, ge=1)
    goal_max_rounds: int = Field(default=100, ge=1)


class GatewaySpec(_Strict):
    """Where a session reaches the host's capability gateway (an MCP service)."""

    # Endpoint on the loopback address, e.g. "http://127.0.0.1:50123/mcp".
    url: str = Field(min_length=1)
    # Issued by the host for this one session; sent as a bearer token.
    token: str = Field(min_length=1, repr=False)


class TaskAgentSpec(_Strict):
    """Everything needed to create one task agent session."""

    session_id: str = Field(pattern=SESSION_ID_PATTERN)
    # Directory the tools resolve relative paths against.
    workdir: str = Field(min_length=1)
    # Directory owned by this session: the JSONL transcript and spilled tool
    # output are written below it. Chosen by the caller.
    session_dir: str = Field(min_length=1)
    model: ModelConfig
    # Host-supplied text appended to the built-in system prompt.
    instructions: str | None = None
    # Language for text meant for the user, e.g. "Simplified Chinese".
    reply_language: str | None = None
    # False for a task agent started by another task agent: such a session cannot
    # manage goals on the strength of the messages it receives.
    top_level: bool = True
    goal: GoalSpec | None = None
    limits: RuntimeLimits = Field(default_factory=RuntimeLimits)
    # Tools withheld from this session's model, e.g. ["subagent"] for a task agent
    # that may not start further task agents.
    disabled_tools: list[str] = Field(default_factory=list)
    # Tool name -> how many of its most recent image results stay in context.
    image_retention: dict[str, int] = Field(default_factory=dict)
    # The host's capability gateway. Its tools are offered next to the
    # built-in ones; without it the session has the built-in tools only.
    gateway: GatewaySpec | None = None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


class StartCommand(_Strict):
    type: Literal["start"] = "start"
    spec: TaskAgentSpec


class PromptCommand(_Strict):
    type: Literal["prompt"] = "prompt"
    session_id: str
    text: str = Field(min_length=1)


class SteerCommand(_Strict):
    type: Literal["steer"] = "steer"
    session_id: str
    text: str = Field(min_length=1)


class FollowUpCommand(_Strict):
    type: Literal["follow_up"] = "follow_up"
    session_id: str
    text: str = Field(min_length=1)


class AbortCommand(_Strict):
    type: Literal["abort"] = "abort"
    session_id: str


class CloseCommand(_Strict):
    type: Literal["close"] = "close"
    session_id: str


class HostResponseCommand(_Strict):
    """Answer to a ``host_request`` event (subprocess shell only)."""

    type: Literal["host_response"] = "host_response"
    request_id: str
    ok: bool
    result: dict[str, Any] | None = None
    error: str | None = None


class ShutdownCommand(_Strict):
    """Close every session and exit the runtime process (subprocess shell only)."""

    type: Literal["shutdown"] = "shutdown"
    # Reported back in ``runtime_exit``. ``idle`` answers a ``runtime_idle``.
    reason: Literal["shutdown", "idle"] = "shutdown"


Command = Annotated[
    Union[
        StartCommand,
        PromptCommand,
        SteerCommand,
        FollowUpCommand,
        AbortCommand,
        CloseCommand,
        HostResponseCommand,
        ShutdownCommand,
    ],
    Field(discriminator="type"),
]

_COMMAND_ADAPTER: TypeAdapter[Command] = TypeAdapter(Command)


def parse_command(payload: object) -> Command:
    """Validate one decoded command; raises ``pydantic.ValidationError``."""
    return _COMMAND_ADAPTER.validate_python(payload)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class _SessionEvent(_Strict):
    session_id: str
    seq: int = 0


class StartedEvent(_SessionEvent):
    type: Literal["started"] = "started"
    # True when the transcript already existed and the session continues it.
    resumed: bool = False
    tools: list[str] = Field(default_factory=list)


class TextEvent(_SessionEvent):
    type: Literal["text"] = "text"
    delta: str


class ToolStartEvent(_SessionEvent):
    type: Literal["tool_start"] = "tool_start"
    tool_call_id: str
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)


class ToolUpdateEvent(_SessionEvent):
    type: Literal["tool_update"] = "tool_update"
    tool_call_id: str
    tool_name: str
    text: str


class ToolEndEvent(_SessionEvent):
    type: Literal["tool_end"] = "tool_end"
    tool_call_id: str
    tool_name: str
    is_error: bool
    # The text the model receives for this call.
    text: str


class ReportEvent(_SessionEvent):
    type: Literal["report"] = "report"
    status: ReportStatus
    summary: str
    details: str | None = None
    delivery: ReportDelivery | None = None


class UsageEvent(_SessionEvent):
    type: Literal["usage"] = "usage"
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    reasoning: int | None = None
    total_tokens: int = 0
    # Size of the conversation as of this request, and the model's window.
    context_tokens: int = 0
    context_window: int = 0


class EndedEvent(_SessionEvent):
    type: Literal["ended"] = "ended"
    reason: EndReason
    # Stop reason of the last model response of the run, when there was one.
    stop_reason: str | None = None
    # Text of the last model response of the run.
    text: str = ""
    error: str | None = None
    # Texts the session had taken when the run finished; None for ``closed``.
    inputs: int | None = None


class ErrorEvent(_SessionEvent):
    type: Literal["error"] = "error"
    command: str
    message: str


class HostRequestEvent(_Strict):
    """The runtime asks the host to run a callback (subprocess shell only)."""

    type: Literal["host_request"] = "host_request"
    session_id: str
    request_id: str
    name: str
    payload: dict[str, Any] = Field(default_factory=dict)
    # True cancels a request sent earlier under the same ``request_id``.
    cancel: bool = False


class RuntimeReadyEvent(_Strict):
    type: Literal["runtime_ready"] = "runtime_ready"
    pid: int


class RuntimeIdleEvent(_Strict):
    """The runtime process has been idle long enough and asks to exit.

    Subprocess shell only, and never passed on to the event sink. ``received``
    is the number of messages the process has read. The parent lets it exit
    only when that equals the number it has sent, so no command can be on its
    way to a process that is about to stop reading.
    """

    type: Literal["runtime_idle"] = "runtime_idle"
    received: int


class RuntimeExitEvent(_Strict):
    type: Literal["runtime_exit"] = "runtime_exit"
    reason: Literal["idle", "shutdown", "parent_gone", "error"]
    detail: str | None = None


SessionEvent = Union[
    StartedEvent,
    TextEvent,
    ToolStartEvent,
    ToolUpdateEvent,
    ToolEndEvent,
    ReportEvent,
    UsageEvent,
    EndedEvent,
    ErrorEvent,
]

RuntimeEvent = Annotated[
    Union[
        StartedEvent,
        TextEvent,
        ToolStartEvent,
        ToolUpdateEvent,
        ToolEndEvent,
        ReportEvent,
        UsageEvent,
        EndedEvent,
        ErrorEvent,
        HostRequestEvent,
        RuntimeReadyEvent,
        RuntimeIdleEvent,
        RuntimeExitEvent,
    ],
    Field(discriminator="type"),
]

_EVENT_ADAPTER: TypeAdapter[RuntimeEvent] = TypeAdapter(RuntimeEvent)


def parse_event(payload: object) -> RuntimeEvent:
    """Validate one decoded event; raises ``pydantic.ValidationError``."""
    return _EVENT_ADAPTER.validate_python(payload)


def dump(message: BaseModel) -> dict[str, Any]:
    """Return the JSON-ready dict of a command or event."""
    return message.model_dump(mode="json")
