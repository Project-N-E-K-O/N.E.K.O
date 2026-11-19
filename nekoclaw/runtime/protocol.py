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
  ``opening`` marks the text that opens a commission (a session takes
  several, one after the other): a summary of the conversation leaves the
  text that opened the commission under way as it is, word for word.
* ``steer``      - deliver text before the next model request of the current
  run; on an idle session it starts a run.
* ``follow_up``  - deliver text once the current run has nothing left to do; on
  an idle session it starts a run.
* ``abort``      - stop the current run. Queued steer / follow-up text is dropped.
* ``close``      - stop the run, kill the session's background jobs and forget
  the session.
* ``fork``       - make the record of a new session from that of another, by
  copying what was said in it up to one entry (``fork.py``). The new session
  is not opened: a ``start`` for it then goes on from the copy, with the
  system prompt and the tool definitions the other was opened with.
* ``compact``    - sum the conversation of an idle session up now, whatever
  its length. Rejected while a run is in progress.

Events (``type`` field). Every session event carries ``session_id`` and a
per-session ``seq`` that increases by one:

* ``started``     - the session exists; ``tools`` lists what the model is offered.
* ``text``        - an increment of assistant text.
* ``tool_start`` / ``tool_update`` / ``tool_end`` - one tool call; updates
  arrive while the tool is still running.
* ``report``      - a structured report made through the ``report`` tool.
* ``usage``       - token usage of one model request and the context size.
  ``summary`` marks the request that summed the conversation up, which is
  none of the run's own.
* ``forked``      - the record of a new session was made from another's
  (``fork``); ``upto`` is the entry of the other the copy ends with.
* ``model_capability`` - something the session learned about its model: the
  provider refused a request for the pictures in it, so the session goes on
  without pictures (once per session at most); or it refused a request as
  too long and stated a context window smaller than the one the session
  was started with, which the session is managed for from then on.
* ``compacted``   - the conversation was summed up. The session's system
  prompt and tool definitions were put together anew after it.
* ``taken``       - one of the texts the session was given (``prompt``,
  ``steer``, ``follow_up``) is in its conversation for good: where the
  session keeps a record of its own, the text is written to it, so that a
  session opened again finds it. ``inputs`` says which, counted from 1 in
  the order they were given. A text that was given and is dropped before
  that (an ``abort``, a ``close``, a record that could not be written) is
  never told of: a driver that needs to know whether what it sent stays
  in the conversation waits for this, not for the next thing the session
  puts out. It says that much and no more: not that a model request was
  made with the text (a run that comes to its limit of requests as it
  takes the text ends without one), only that the text is there for the
  next request the session makes.
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

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

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


# The smallest window a session is managed for (the host's setting of the
# same is ``TASK_AGENT_CONTEXT_WINDOW_MIN``; a test keeps the two equal).
MIN_CONTEXT_WINDOW = 4_000


class ModelConfig(_Strict):
    """Model endpoint of one session. ``http://`` endpoints are allowed."""

    model: str = Field(min_length=1)
    base_url: str | None = None
    # Travels inside the start command only; never read from argv or the environment.
    api_key: str | None = Field(default=None, repr=False)
    provider_type: str | None = None
    context_window: int = Field(default=128_000, ge=MIN_CONTEXT_WINDOW)
    # The length of the conversation from which it is summed up, for the
    # window above; None for the runtime's own part of the window. A window
    # the session comes to be managed for later keeps the same part.
    compact_at: int | None = Field(default=None, ge=1_000)
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
    # Tool name -> pool. Tools of one pool are counted together: the pool as a
    # whole keeps that many image results (the largest number among its tools).
    image_pools: dict[str, str] = Field(default_factory=dict)
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
    # True for the text that opens a commission: what the session works on
    # from here. A summary of the conversation leaves it as it is.
    opening: bool = False
    # False for text no person wrote for this session: what another session
    # sent it, or what the host tells it of a time that has come. It stands
    # in the conversation like any text, and entitles the run to nothing
    # that only the user's own words entitle it to.
    human: bool = True
    # A number of the sender's own for this text, or None. Said again by
    # the ``error`` event of a text that is refused: texts are sent without
    # waiting for the one before, so "the last one sent" need not be the
    # one a refusal is about.
    serial: int | None = None


class SteerCommand(_Strict):
    type: Literal["steer"] = "steer"
    session_id: str
    text: str = Field(min_length=1)
    # See ``PromptCommand.human``.
    human: bool = True
    # See ``PromptCommand.serial``.
    serial: int | None = None


class FollowUpCommand(_Strict):
    type: Literal["follow_up"] = "follow_up"
    session_id: str
    text: str = Field(min_length=1)
    # See ``PromptCommand.human``.
    human: bool = True
    # See ``PromptCommand.serial``.
    serial: int | None = None


class AbortCommand(_Strict):
    type: Literal["abort"] = "abort"
    session_id: str


class CloseCommand(_Strict):
    type: Literal["close"] = "close"
    session_id: str


class ForkCommand(_Strict):
    """Make the record of a new session from that of another; see ``nekoclaw.runtime.fork``.

    The copy is made of the other's record as it is on the disk. What a
    session that is open said and could not write there (the disk
    refused it, after the retries) is not part of the record: a
    session opened from it later does not have it, and neither does
    the copy. That is not an error of the copying.
    """

    type: Literal["fork"] = "fork"
    # The session that is made. It must have no record yet.
    session_id: str = Field(pattern=SESSION_ID_PATTERN)
    # Where its record is to be kept (``TaskAgentSpec.session_dir`` of its start).
    session_dir: str = Field(min_length=1)
    # The session that is copied, and where its record is kept. One that is
    # open in this runtime has what it said so far written out first.
    source_session_id: str
    source_dir: str = Field(min_length=1)
    # The entry of the other's record the copy is to end with; None for all
    # of it. The copy ends at the last place at or before it where no tool
    # call is left without its result.
    upto: str | None = None


class CompactCommand(_Strict):
    type: Literal["compact"] = "compact"
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
        ForkCommand,
        CompactCommand,
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
    # True for the request that summed the conversation up: it is counted
    # with what the session used, and says nothing of how long the
    # conversation is (``context_tokens`` is 0).
    summary: bool = False


class ForkedEvent(_SessionEvent):
    """The record of a new session (``session_id``) was made from another's."""

    type: Literal["forked"] = "forked"
    source_session_id: str
    # The entry of the other's record the copy ends with.
    upto: str
    # How many entries were copied.
    entries: int = 0


class ModelCapabilityEvent(_SessionEvent):
    """What the session learned about its model while it ran."""

    type: Literal["model_capability"] = "model_capability"
    # False: the provider said the model takes no pictures. None: this is
    # not about pictures.
    supports_images: bool | None = None
    # The context window the provider stated when it refused a request as
    # too long; only when that is smaller than the session's.
    context_window: int | None = None
    # What the provider said, shortened: kept with what is remembered, so
    # that it can be seen why the model was taken not to see pictures.
    reason: str = ""
    # False: said of this session only (its pictures were refused more than
    # once, for what they were); nothing is to be remembered about the model.
    lasting: bool = True

    @model_validator(mode="after")
    def _says_something(self) -> "ModelCapabilityEvent":
        if self.supports_images is None and self.context_window is None:
            raise ValueError("a model_capability event says what was learned")
        return self


class CompactedEvent(_SessionEvent):
    """The conversation was summed up; the session goes on from the summary."""

    type: Literal["compacted"] = "compacted"
    # How often the conversation of this session has been summed up so far.
    count: int = 0
    # Size of the conversation before the summary, as the context manager counted it.
    tokens_before: int = 0


# ``EndedEvent.error`` of a session whose runtime process went away. Nothing
# more comes for such a session, not even the end of a close asked for.
PROCESS_EXITED_ERROR = "the runtime process exited"


class TakenEvent(_SessionEvent):
    type: Literal["taken"] = "taken"
    # Which of the texts the session was given is in its conversation now,
    # counted from 1 in the order they were given.
    inputs: int
    # True where this is said after the run that took the text has ended:
    # it was not known until then that the text had got into the
    # conversation. What is told of the text is then what that run came to,
    # and not what the next one does.
    late: bool = False
    # With ``late``: which run took the text, by the number its ``ended``
    # event gave it (``EndedEvent.run``). A text may have gone to more than
    # one run before it got into the conversation; this says which.
    run: int | None = None


class EndedEvent(_SessionEvent):
    type: Literal["ended"] = "ended"
    reason: EndReason
    # Stop reason of the last model response of the run, when there was one.
    stop_reason: str | None = None
    # Text of the last model response of the run.
    text: str = ""
    error: str | None = None
    # Up to which of the texts the session was given this run is accounted
    # for: the number of the last one it was handed when it finished. An
    # upper bound, for telling the run's texts from those of the next. That
    # a given text is in the conversation is said by its ``taken`` event and
    # by nothing else: one whose answer never got there counts here, and is
    # given again. None for ``closed``.
    inputs: int | None = None
    # A number for this run among the session's runs, where the runtime
    # counts them: what a ``taken`` event that comes late names (its ``run``).
    run: int | None = None
    # Of the texts this run was handed, those of which it is not known, as
    # the run ends, whether they got into the conversation. A ``taken`` that
    # comes late and names this run is of one of them, and of no other: a
    # runtime that may say so late lists the text here. (One that knows of
    # every text as its run ends, like the built-in one, lists none.)
    open_inputs: list[int] = Field(default_factory=list)
    # Background jobs of the session not yet gone when the run finished.
    jobs: int = 0
    # Those of them that tell the session when they finish, which starts its
    # next run. Closing the session ends them, so a driver that would close
    # an idle session to let it rest leaves one with such jobs open. (One
    # that is being stopped is not among them: whoever stopped it knows,
    # and nothing follows its end.)
    waking_jobs: int = 0


class ErrorEvent(_SessionEvent):
    type: Literal["error"] = "error"
    command: str
    message: str
    # Of a text that was refused: the number it was sent under
    # (``PromptCommand.serial``), where it had one.
    serial: int | None = None


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
    ModelCapabilityEvent,
    CompactedEvent,
    TakenEvent,
    ForkedEvent,
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
        ModelCapabilityEvent,
        CompactedEvent,
        TakenEvent,
        ForkedEvent,
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
