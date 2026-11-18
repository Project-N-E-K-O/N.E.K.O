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

"""Starting and driving task agents: commissions, each worked on in a session.

``TaskAgentManager`` is the only user of the runtime's driver interface
(``nekoclaw.runtime``). It

* starts a runtime when the first task agent that runs in it is needed, never
  before, and gives every commission the runtime it runs in: the built-in
  one, or dsh for a user who installed it (``runtimes.py``);
* gives every commission the session it is worked on in (``ledger.Session``):
  the one session of its project, for a commission filed under a project;
  the session of the commission it follows up; a new one otherwise, and for
  every task agent another task agent starts. A session is a conversation
  that is kept and gone on with, so one takes several commissions, one
  after the other: the commission at work holds its session, and the next
  one waits ``queued`` until that one is over and has left the runtime. A
  commission that waits for the user's answer holds its session meanwhile.
  The runtime knows sessions, the ledger and everything else here know
  commissions: one session, one commission at a time, so the one tells the
  other (``_held_by``);
* opens the runtime's session anew for every commission and closes it when
  the commission is over, as it always did; what was said stays in the
  session's directory and the next commission goes on from it. What a
  commission is (what was dispatched, what the user said, how the user wants
  such things done, the entries picked for it) is the message that opens it
  (``_opening``); the system prompt says nothing of one commission and is
  kept as the session was first opened with it (``runtime/snapshot.py``);
* keeps at most ``TASK_AGENT_MAX_CONCURRENT`` task agents at work and leaves the
  rest ``queued`` until a place is free. Task agents started by other task agents
  count the same;
* turns the runtime's events into ledger rows and status changes, and into
  reports for her;
* serves the runtime's host callbacks: permissions, questions for the user,
  reports, lessons, web search and child task agents;
* hands a task agent what the task agent space (``space.py``) holds for its
  commission when it starts, files the commission under the project it names,
  and lets task agents read the space without asking and never write it;
* keeps count of what the model requests of every session use, with the
  session and with the commission (``Ledger.add_usage``), and asks the user
  before the work on one commission uses more than it may (``_cb_budget``);
* makes a session from another by copying what was said in it
  (``fork_session``), and, when that is switched on, sums up the
  conversation of a session that has been idle for long (``_idle_compact``).

Status changes all go through ``Ledger.transition``. In particular a
commission that was stopped is ``cancelled`` before its session is told to
stop, so nothing the session still says can turn it into anything else.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from nekoclaw.host.capabilities import CapabilityRegistry
from nekoclaw.host.ledger import ACTIVE_STATUSES, ENDED_STATUSES, Commission, Ledger, Session, new_session_id
from nekoclaw.host.paths import DEFAULT_PROJECT, DSH_SESSION_NAME, TRANSCRIPT_NAME, HostPaths
from nekoclaw.host.permissions import (
    CHOICES,
    DENIED_BY_SETTINGS,
    SPACE_COMMAND,
    USAGE_LIMIT,
    PermissionAsk,
    PermissionDecision,
    PermissionGate,
)
from nekoclaw.host.plugin_runs import PluginRunner
from nekoclaw.host.preferences import PreferenceStore, feedback_text
from nekoclaw.host.reports import CHANNEL_TASK_AGENT, Reporter
from nekoclaw.host.runtimes import RUNTIME_BUILTIN, RUNTIME_DSH, Runtimes

logger = logging.getLogger(__name__)

LEAD_NAME = "lead"
WEB_SEARCH_PLUGIN_ID = "web_search"
WEB_SEARCH_ENTRY_ID = "search"
_WEB_SEARCH_PARALLEL = 3
_SHUTDOWN_START_WAIT_SECONDS = 5.0
# How long a runtime gets to say that a session the host had open by itself
# is closed before that is noted in the log; it is waited for all the same.
_SERVICE_CLOSE_SECONDS = 15.0
# The key of what is put among the runtimes' events to learn when they are
# caught up with (``_caught_up``), and how long that is waited for.
_REACHED = "nekoclaw/reached"
_CAUGHT_UP_SECONDS = 10.0
_SHUT_DOWN_REASON = "the host was shut down"
_QUOTA_REASON = "the free daily quota of the agent model is used up"
_STOPPED_MEANWHILE_REASON = "stopped while it was being taken up again"
_USAGE_REASON = "it used as many tokens as one commission may, and the user allowed no more"
# The kinds of session the host sums up while they are idle: those that go
# on being handed commissions. A session of one commission, or of a task
# agent another started, is seldom opened again.
_IDLE_COMPACT_KINDS = frozenset({"project"})
# Added to what is left of the idle time when a timer came due a moment
# before the ledger had the session idle for long enough: more than the
# steps of a wall clock, so that the next look is not early again.
_IDLE_TIMER_MARGIN_SECONDS = 0.05
# A task agent started by a task agent does not wait in the queue: its parent
# keeps its own place while it waits, so with every place held by parents
# nothing would ever move.
_NO_PLACE_REASON = "no task agent place was free"
# Children that can still produce a result their parent should wait for.
_STILL_WORKING = frozenset({"queued", "working", "waiting_user"})
_REPORT_TO_STATUS = {"ready": "done", "blocked": "blocked", "failed": "failed"}
# The pool the screenshots of every built-in tool are counted in (``TaskAgentSpec.image_pools``).
SCREENSHOT_POOL = "builtin_screenshots"
_STATUS_TO_REPORT = {"done": "completed", "blocked": "blocked", "failed": "failed", "cancelled": "cancelled"}
_REPLY_LANGUAGES = {
    "zh": "Simplified Chinese",
    "zh-CN": "Simplified Chinese",
    "zh-TW": "Traditional Chinese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "ru": "Russian",
    "es": "Spanish",
    "pt": "Portuguese",
}

# Returns the model endpoint for task agents as keyword arguments of
# ``nekoclaw.runtime.protocol.ModelConfig``, or None when none is configured.
ModelConfigProvider = Callable[[], Awaitable["dict[str, Any] | None"]]
# Builds a runtime from the event sink and the host callbacks.
RuntimeFactory = Callable[[Callable[[dict[str, Any]], None], Any], Any]


# Counts one model request of a task agent against whatever quota applies to
# its model. Returns False when no request is left; the commission then fails.
UsageMeter = Callable[[str], Awaitable[bool]]


def _search_queries(arguments: dict[str, Any]) -> tuple[str, ...]:
    """Return the queries of a call of the web search as the search tool hands them on: blank ones dropped, each once.

    See ``runtime.tools.web._clean_queries``: what was ruled on before a call
    is compared with what the call then brings.
    """
    queries: list[str] = []
    for query in arguments.get("queries") or []:
        text = str(query)
        if text.strip() and text not in queries:
            queries.append(text)
    return tuple(queries)


class LessonSink(Protocol):
    """Receives what ``note_lesson`` files, except ``feedback``.

    The task agent space (``space.TaskAgentSpace``) is the sink the host
    uses. A manager built without a space keeps the lessons as events of the
    commission (``LedgerLessonSink``).
    """

    async def submit(self, commission: Commission, lesson: dict[str, Any]) -> str | None:
        """Store one lesson; the returned text is told to the model."""


class ForkRefused(RuntimeError):
    """A session could not be made from another by copying it; the message says why."""


class LedgerLessonSink:
    """Keeps lessons as ``lesson`` events of the commission that filed them."""

    def __init__(self, ledger: Ledger) -> None:
        self._ledger = ledger

    async def submit(self, commission: Commission, lesson: dict[str, Any]) -> str | None:
        await self._ledger.append_event(commission.id, "lesson", dict(lesson))
        return "recorded"


@dataclass(slots=True)
class _Session:
    """A commission's place: the session it is worked on in, open in a runtime or on its way there."""

    commission_id: str
    # The session the commission is worked on in (``ledger.Session.id``):
    # what the runtime knows it by.
    session_id: str = ""
    # The message that opens the commission was sent to the session, and no
    # event has yet shown that the session took it.
    opening_sent: bool = False
    # Text increments and tool output waiting to be written as one event row.
    text: list[str] = field(default_factory=list)
    text_size: int = 0
    updates: dict[str, list[str]] = field(default_factory=dict)
    update_names: dict[str, str] = field(default_factory=dict)
    # Set while a host callback waits for the user's answer.
    question: asyncio.Future[dict[str, Any]] | None = None
    ask_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # True after a ``waiting`` report: the answer becomes the session's next work.
    awaiting_prompt: bool = False
    # Whether the run that made that report has ended, and an answer that
    # arrived before it did.
    run_ended: bool = False
    held_answer: str | None = None
    # An answer sent as the session's next work that no event has yet shown
    # it took. Should the session close first, it is taken up again with it.
    untaken_answer: str | None = None
    # Set once the session has been given its first work. Until then, what is
    # passed on to it waits in the notes, which go to it right after that work.
    ready: bool = False
    closing: bool = False
    # A runtime was asked to open its session and took the request: from
    # then on the session is the runtime's to close, also when the start
    # fails after it.
    opened_in_runtime: bool = False
    # What was said in the session had been removed, being old, when this
    # start came to it (``_leave_cleared``).
    cleared_before: bool = False
    # The runtime the session is in and its name (``runtimes.py``), from the
    # moment it is started there.
    runtime: Any = None
    runtime_name: str | None = None
    # dsh, though it was made ready, opened no session for it and the
    # built-in runtime took it: the event in which dsh says why is on its way.
    dsh_refused: bool = False
    # Model requests already counted against the quota before they were
    # sent; that many usage reports are not counted again.
    prepaid_requests: int = 0
    # Set when the session is closed before its work was given: what was
    # passed on for that work is kept when the session is released.
    keep_notes: bool = False
    # What this start is to send that came from the user: what an earlier
    # failed start kept, the sentence it was given, what was passed on
    # while it was set up. Sending is not taking: it stays until an event
    # shows the session at work on it, and is kept for the next start when
    # this one fails before its prompt reached the session's transcript.
    carried: list[str] = field(default_factory=list)
    # Texts sent to the session (prompt and steer). A finished run that had
    # taken fewer is followed by a run that takes the rest; until that one
    # ends, the earlier one's end is held here and decides nothing.
    sent: int = 0
    held_end: dict[str, Any] | None = None
    # The address and the name of the model the session was started with.
    model: tuple[Any, Any] | None = None
    # Set once the task agent was told to hand over what it has, the user
    # having allowed no more to be used: how many model requests it has left
    # for that. None while it works as usual.
    wind_up_left: int | None = None
    # The host ended the commission itself when those were used, and closes
    # the session: what the run says as it is stopped decides nothing.
    wound_up: bool = False


def _shows_work_taken(event: dict[str, Any], sent: int) -> bool:
    """Whether a session event shows the session took everything sent to it.

    A refusal does not, nor does the session closing. The end of a run that
    took fewer texts than were sent is an earlier run's end.
    """
    kind = event.get("type")
    if kind == "error":
        return False
    if kind == "ended":
        if event.get("reason") == "closed":
            return False
        inputs = event.get("inputs")
        return not isinstance(inputs, int) or inputs >= sent
    return True


def _shows_run_at_work(event: dict[str, Any]) -> bool:
    """Whether a session event shows a run at work on what it was given.

    ``started`` does not: it answers the start, before any work is sent.
    Nor does an error or an end other than a completed run.
    """
    kind = event.get("type")
    if kind == "ended":
        return event.get("reason") == "completed"
    return kind in ("text", "tool_start", "tool_update", "tool_end", "report", "usage", "host_request")


def _in_builtin(session_dir: Any) -> bool:
    """Whether the built-in runtime keeps a transcript in a session directory. Blocking."""
    try:
        return (session_dir / TRANSCRIPT_NAME).stat().st_size > 0
    except OSError:
        return False


def _in_dsh(session_dir: Any) -> bool:
    """Whether dsh holds a session for a session directory. Blocking.

    dsh keeps what was said itself; the driver leaves a note of which of
    its sessions is this one's (``nekoclaw.dsh.driver.SESSION_FILE``).
    """
    try:
        return (session_dir / DSH_SESSION_NAME).is_file()
    except OSError:
        return False


def _has_transcript(session_dir: Any) -> bool:
    """Whether something was ever said in a session directory, in either runtime. Blocking."""
    return _in_builtin(session_dir) or _in_dsh(session_dir)


def _begun_in(session: Any, session_dir: Any) -> str | None:
    """Return the runtime that holds what was said in a session, if one does. Blocking.

    The ledger tells which runtime had the session last (``session`` is a
    ``ledger.Session``); the session directory tells whether anything came
    of it. A runtime that was given the session and never opened it holds
    nothing of it, and neither does one whose record was removed since,
    being old: such a session is free to be opened in the other.
    """
    if session.runtime == RUNTIME_DSH:
        return RUNTIME_DSH if _in_dsh(session_dir) else None
    if session.runtime == RUNTIME_BUILTIN:
        return RUNTIME_BUILTIN if _in_builtin(session_dir) else None
    return None


def _has_files(session_dir: Any) -> bool:
    """Whether a session directory holds anything but a transcript: a working directory, say. Blocking."""
    try:
        return any(item.name != TRANSCRIPT_NAME for item in session_dir.iterdir())
    except OSError:
        return False


def default_runtime_factory(on_event: Callable[[dict[str, Any]], None], host: Any) -> Any:
    """Return the built-in runtime in a child process."""
    from nekoclaw.runtime import SubprocessRuntime

    return SubprocessRuntime(on_event, host=host)


def build_answers(questions: list[Any], text: str) -> dict[str, Any]:
    """Turn the user's reply into the answer shape ``ask_user_question`` expects.

    Every question gets the reply as its free-text answer; an option whose
    label occurs in the reply is selected as well.
    """
    lowered = text.lower()
    answers = []
    for index, question in enumerate(questions):
        if not isinstance(question, dict):
            continue
        labels = []
        for option in question.get("options") or []:
            label = option.get("label") if isinstance(option, dict) else option
            if isinstance(label, str) and label:
                labels.append(label)
        selected = [label for label in labels if label.lower() in lowered]
        if not question.get("multi_select"):
            selected = selected[:1]
        answers.append({"id": str(question.get("id", index)), "selected": selected, "custom": text})
    return {"answers": answers}


class TaskAgentManager:
    """Runs task agents for commissions. See the module docstring."""

    def __init__(
        self,
        *,
        ledger: Ledger,
        preferences: PreferenceStore,
        permissions: PermissionGate,
        runner: PluginRunner,
        registry: CapabilityRegistry,
        reporter: Reporter,
        paths: HostPaths,
        model_config: ModelConfigProvider,
        gateway: Any = None,
        runtime_factory: RuntimeFactory = default_runtime_factory,
        enabled: Callable[[], bool] | None = None,
        max_concurrent: int | None = None,
        lessons: LessonSink | None = None,
        meter: UsageMeter | None = None,
        leases: Any = None,
        model_capabilities: Any = None,
        space: Any = None,
        dsh: Any = None,
        dsh_runtime_factory: Any = None,
        conversation: Any = None,
        switched_on: Callable[[], bool] | None = None,
    ) -> None:
        self._meter = meter
        # ``nekoclaw.host.conversation.RecentConversation``, or None: then a
        # commission that opens a new session is handed no excerpt of what
        # was said in the main dialogue just before it.
        self._conversation = conversation
        # Whether NekoClaw is switched on, as the task brain reads it; None
        # when nobody says. Asked before the host makes a model request of
        # its own (summing up an idle session), which no dispatch stands behind.
        self._switched_on = switched_on
        # ``nekoclaw.host.space.TaskAgentSpace``, or None: then task agents
        # start with nothing from earlier commissions and lessons stay in
        # the ledger.
        self._space = space
        # ``nekoclaw.host.leases.BuiltinLeases``: who occupies the desktop and
        # the browser. A commission lets go of them when its run is over.
        self._leases = leases
        # ``nekoclaw.host.model_capabilities.ModelCapabilityStore``, or None:
        # then every model is taken to see pictures.
        self._model_capabilities = model_capabilities
        self._ledger = ledger
        self._preferences = preferences
        self._permissions = permissions
        self._runner = runner
        self._registry = registry
        self._reporter = reporter
        self._paths = paths
        self._model_config = model_config
        self._gateway = gateway
        self._enabled = enabled
        self._max_concurrent = max_concurrent
        self._lessons: LessonSink = lessons or space or LedgerLessonSink(ledger)
        # The built-in runtime and, for a user who installed it, dsh
        # (``dsh`` is the host's ``nekoclaw.dsh.DshInstallation``, or None).
        # Each is created when a commission first runs in it.
        self._runtimes = Runtimes(
            on_event=lambda event: self._events.put_nowait(event),
            callbacks=self._callbacks,
            builtin_factory=runtime_factory,
            dsh=dsh,
            **({"dsh_factory": dsh_runtime_factory} if dsh_runtime_factory is not None else {}),
        )
        self._sessions: dict[str, _Session] = {}
        self._queue: deque[str] = deque()
        # The session each commission this manager has in hand is worked on
        # in (``ledger.Session.id``), from the moment it is handed over.
        self._session_of: dict[str, str] = {}
        # Which commission holds each session: the one at work in it, from
        # the moment it takes its place until it is over and its session has
        # left the runtime. A commission that waits for the user, or for
        # task agents it started, holds its session also while no runtime
        # has it open. This is how what a runtime says of a session is told
        # to be about a commission, and why the next commission of a session
        # waits for the one before it.
        self._held_by: dict[str, str] = {}
        # Text passed on to a commission that has not started yet.
        self._notes: dict[str, list[str]] = {}
        # What a start given up for the quota was to send, kept for the
        # next start; it comes before what that start is told, being older.
        self._carried: dict[str, list[str]] = {}
        # What a commission taken up again is told: an addition to a blocked or
        # failed one, or the answer to one whose waiting session was closed.
        self._resume: dict[str, str] = {}
        # Commissions whose task agent is being started again right now.
        self._taking_up: set[str] = set()
        # Commissions whose closed session an answer is taking up again right
        # now; a second answer to the same question is refused meanwhile.
        self._answering_closed: set[str] = set()
        # Being queued to be taken up again, while that status is written.
        self._requeueing: set[str] = set()
        # What is taking a commission up again, or queueing it, right now
        # for a sentence that may be called off (``still_wanted``): whether
        # it is still wanted, and the task that does it (``_called_off``).
        self._under_way: dict[str, tuple[Callable[[], bool], asyncio.Future[Any]]] = {}
        self._candidates: dict[str, tuple[str, ...]] = {}
        # What was ruled on a commission's web search before the call was
        # made (``_rule_on_host_tool``): the queries it was for, the refusal
        # or None, and until when it waits for the call (event loop time).
        self._search_rulings: dict[str, tuple[tuple[str, ...], str | None, float]] = {}
        # Commissions whose review is being written down right now
        # (``hold_session``); a start of one of them waits for that.
        self._held: dict[str, asyncio.Event] = {}
        # Sessions whose old files are being removed right now
        # (``hold_files``); a commission is started in one of them once that
        # is done.
        self._clearing: dict[str, asyncio.Event] = {}
        # What she and the user said just before a commission was handed
        # over (``conversation.Turn``), kept from then until its session is
        # opened: a commission may wait for its place, and what is said
        # meanwhile is not what it refers to.
        self._excerpts: dict[str, list[Any]] = {}
        # Sessions whose record a runtime is making from another's right
        # now (``fork_session``), each with what waits for the runtime's word.
        self._forks: dict[str, asyncio.Future[dict[str, Any]]] = {}
        # The commissions no task agent started whose work has used as much
        # as it may and for which the user allowed no more: every task agent
        # of such a one is told to hand over what it has. And, for each such
        # commission, the lock that keeps the user from being asked the same
        # thing by two of its task agents at once.
        self._refused_more: set[str] = set()
        self._usage_asks: dict[str, asyncio.Lock] = {}
        # Sessions the host has open by itself, for no commission (summing
        # up an idle one): what the runtime says of such a session goes to
        # whoever opened it, here, and to no commission.
        self._service: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        # Idle sessions that are to be summed up, each with the timer that
        # says when; those being summed up right now, each with its task;
        # and when sessions were summed up while idle, for the daily limit.
        self._idle_timers: dict[str, asyncio.TimerHandle] = {}
        self._compacting: dict[str, asyncio.Task[Any]] = {}
        self._idle_compacted_at: deque[float] = deque()
        # Those that got past the daily limit and have not asked for their
        # summary yet: counted with the ones above, so that sessions whose
        # time comes together do not get past the limit side by side.
        self._idle_compacts_under_way = 0
        # Sessions that are being given back after a summing up, each with
        # the task that does it.
        self._idle_over: dict[str, asyncio.Task[Any]] = {}
        # How often everything was stopped, and the sessions that were in
        # use when it last was: nothing is asked of a model for them by
        # the host itself until something new is to be worked on in them.
        self._idle_stops = 0
        self._idle_stopped: set[str] = set()
        # Sessions a copy is being made from right now, each with how many:
        # what was said in them is not removed until the copy is on record
        # (``hold_files``), or it would be made of a record that is going,
        # and kept when the session it came from starts over.
        self._copying_from: dict[str, int] = {}
        # Copies whose making was given up (the runtime took too long),
        # each with the directory it was to be written into.
        self._forks_given_up: dict[str, str] = {}
        self._starting: set[asyncio.Task[Any]] = set()
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._pump: asyncio.Task[None] | None = None
        self._closed = False
        permissions.set_asker(self.ask_permission)
        # A task agent started by another is covered by what the user allowed
        # for that one's session.
        permissions.set_parent_lookup(self._parent_of)
        # A commission that holds a session without having it open (it waits
        # for an answer) can end without a word from a runtime: stopped, say.
        self._unsubscribe = ledger.subscribe(self._on_commission_change)

    # -- state ------------------------------------------------------------

    @property
    def limit(self) -> int:
        if self._max_concurrent is not None:
            return self._max_concurrent
        from config.nekoclaw_settings import TASK_AGENT_MAX_CONCURRENT

        return TASK_AGENT_MAX_CONCURRENT

    @property
    def running(self) -> list[str]:
        """Return the commissions that hold a session right now."""
        return list(self._sessions)

    @property
    def queued(self) -> list[str]:
        return list(self._queue)

    @property
    def runtime_started(self) -> bool:
        return self._runtimes.started

    @property
    def _runtime(self) -> Any:
        """The built-in runtime, if it was created."""
        return self._runtimes.get(RUNTIME_BUILTIN)

    def _runtime_of(self, session: _Session) -> Any:
        """Return the runtime a session is in, or None.

        A session that was not started anywhere yet is spoken of to the
        built-in runtime, as far as that was created: it answers that it
        does not know the session, which is what the caller acts on.
        """
        return session.runtime if session.runtime is not None else self._runtimes.get(RUNTIME_BUILTIN)

    @property
    def dsh_error(self) -> str | None:
        """Why dsh could not be used the last time a commission was to run in it, if it could not."""
        return self._runtimes.dsh_error

    def sessions_in(self, runtime_name: str) -> list[str]:
        """Return the commissions whose sessions are in a runtime right now."""
        return [session.commission_id for session in self._sessions.values() if session.runtime_name == runtime_name]

    async def stop_dsh(self, then: Any = None) -> bool:
        """End the dsh runtime and its process, unless a session is in it. Returns whether it is stopped.

        For removing dsh or installing it anew: its program cannot be
        replaced while it runs. ``then`` (a coroutine function) does that,
        and no commission starts dsh until it is done.
        """
        return await self._runtimes.retire_dsh(lambda: bool(self.sessions_in(RUNTIME_DSH)), then)

    async def available(self) -> tuple[bool, str]:
        """Return whether a task agent could be started, and if not, why."""
        if self._closed:
            return False, "disabled"
        if self._enabled is not None:
            try:
                if not self._enabled():
                    return False, "not_enabled"
            except Exception:  # noqa: BLE001 - an unreadable switch counts as off
                return False, "not_enabled"
        try:
            config = await self._model_config()
        except Exception as exc:  # noqa: BLE001 - no usable configuration
            logger.warning("the task agent model configuration cannot be read: %s", type(exc).__name__)
            return False, "not_enabled"
        if not config or not config.get("model"):
            return False, "not_enabled"
        return True, ""

    # -- runtime ----------------------------------------------------------

    def _callbacks(self) -> Any:
        from nekoclaw.runtime import HostCallbacks

        return HostCallbacks(
            decide_permission=self._cb_permission,
            ask_user=self._cb_ask_user,
            report=self._cb_report,
            note_lesson=self._cb_note_lesson,
            web_search=self._cb_web_search,
            subagent_start=self._cb_subagent_start,
            subagent_list=self._cb_subagent_list,
            subagent_send=self._cb_subagent_send,
            subagent_interrupt=self._cb_subagent_interrupt,
            budget=self._cb_budget,
        )

    async def _choose_runtime(
        self, commission: Commission, model: Any, begun_in: str | None
    ) -> tuple[str, Any, str | None]:
        """Return the runtime a commission's task agent is started in; see ``runtimes.py``.

        Its name, the runtime, and, when the commission was to run in dsh
        and runs in the built-in runtime instead, why.
        """
        parent_runtime = None
        if commission.parent_id is not None:
            # Where the task agent that started it runs: told by the ledger,
            # since that one's session may have been closed meanwhile.
            parent = await self._ledger.get(commission.parent_id)
            parent_runtime = parent.runtime if parent is not None else None
        chosen = await self._runtimes.choose(model, begun_in=begun_in, parent_runtime=parent_runtime)
        if self._pump is None:
            self._pump = asyncio.ensure_future(self._pump_events())
        return chosen

    async def _pump_events(self) -> None:
        """Handle runtime events one at a time, in the order they arrived."""
        while True:
            event = await self._events.get()
            try:
                reached = event.get(_REACHED)
                if reached is not None:
                    # Not a runtime's word: someone waits to know that
                    # everything said before this has been handled.
                    if not reached.done():
                        reached.set_result(None)
                    continue
                await self._handle(event)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad event must not stop the rest
                logger.exception("handling runtime event %s failed", event.get("type"))
            finally:
                self._events.task_done()

    async def settle(self) -> None:
        """Wait until every event received so far has been handled."""
        await self._events.join()

    async def _caught_up(self) -> None:
        """Wait until what the runtimes have said up to now is handled, and no longer than a moment.

        For a callback of a session whose answer depends on what the
        session said just before: a runtime's events are handled one at a
        time, in a task of their own, and a callback is not one of them.
        Unlike ``settle`` this does not wait for what is said meanwhile.
        """
        if self._pump is None or self._pump.done():
            return
        reached: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._events.put_nowait({_REACHED: reached})
        try:
            await asyncio.wait_for(reached, _CAUGHT_UP_SECONDS)
        except asyncio.TimeoutError:
            logger.warning("the runtime's events were not caught up with in time; going on")

    # -- starting ---------------------------------------------------------

    async def submit(self, commission: Commission, *, candidate_plugin_ids: Any = (), wait: bool = True) -> bool:
        """Hand a commission to a task agent: now if a place is free, else when one is.

        With ``wait`` False a commission that finds no free place is failed
        instead of queued. Returns whether it was started or queued.
        """
        self._candidates[commission.id] = tuple(str(item) for item in candidate_plugin_ids)
        stops = self._idle_stops
        if commission.route != "task_agent":
            await self._ledger.update(commission.id, route="task_agent")
        if not self._closed:
            try:
                commission = await self._give_session(commission)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the commission fails with the reason
                logger.warning("giving commission %s a session failed: %s", commission.id, exc)
                self._candidates.pop(commission.id, None)
                await self._fail(commission.id, f"the task agent could not be started: {exc}")
                return False
        if self._closed:
            self._candidates.pop(commission.id, None)
            self._session_of.pop(commission.id, None)
            await self._ledger.transition(commission.id, "failed", reason=_SHUT_DOWN_REASON)
            return False
        self._new_work_in(self._session_of.get(commission.id), stops)
        self._keep_excerpt(commission)
        if not self._take_place(commission.id):
            if not wait:
                self._candidates.pop(commission.id, None)
                self._excerpts.pop(commission.id, None)
                self._session_of.pop(commission.id, None)
                await self._ledger.transition(commission.id, "failed", reason=_NO_PLACE_REASON)
                return False
            self._queue.append(commission.id)
            queued: dict[str, Any] = {"ahead": len(self._queue) - 1}
            holder = self._held_by.get(self._session_of.get(commission.id, ""))
            if holder is not None and holder != commission.id:
                # Its session is at work on another commission: it is next
                # after that one, however many places are free.
                queued["behind"] = holder
            await self._ledger.append_event(commission.id, "queued", queued)
            return True
        # Started as a task of its own: whoever handed the commission over may
        # be cancelled (the host shutting down), and a session half set up
        # would then belong to nobody.
        await asyncio.shield(self._spawn(self._launch(commission.id)))
        return True

    def _spawn(self, work: Awaitable[Any]) -> asyncio.Task[Any]:
        """Run starting work as a task that shutting down waits for."""
        task = asyncio.ensure_future(work)
        self._starting.add(task)
        task.add_done_callback(self._starting.discard)
        return task

    def _take_place(self, commission_id: str) -> bool:
        """Take a place for a commission, or return False when none is free.

        Checking and taking happen with no await in between, so callers that
        run at the same time cannot together go over the limit. Nothing gets a
        place once the manager is closed.

        A place is a session open in a runtime, and with it the commission
        takes hold of its session: none is free while another commission
        holds that session, whatever the limit says. The caller has made
        the commission's session known (``_give_session``).
        """
        session_id = self._session_of.get(commission_id)
        if session_id is None or not self._session_free_for(commission_id):
            return False
        if self._closed or commission_id in self._sessions or len(self._sessions) >= self.limit:
            return False
        self._sessions[commission_id] = _Session(commission_id, session_id=session_id)
        self._held_by[session_id] = commission_id
        # It is no longer idle. (One that is being summed up right now is
        # cut short where the commission is started, ``_start``.)
        self._drop_idle_timer(session_id)
        return True

    def _session_free_for(self, commission_id: str) -> bool:
        """Whether a commission's session is held by no other commission."""
        holder = self._held_by.get(self._session_of.get(commission_id, ""))
        return holder is None or holder == commission_id

    def _in_hand(self, commission_id: str) -> bool:
        """Whether a commission has a place, waits for one, or is on its way to one."""
        return (
            commission_id in self._sessions
            or commission_id in self._queue
            or commission_id in self._requeueing
            or commission_id in self._taking_up
        )

    async def _give_session(self, commission: Commission) -> Commission:
        """Make known the session a commission is worked on in, giving it one when it has none.

        A commission that names a session keeps it: one that follows
        another up was given that one's session once it was chosen for a
        task agent (``TaskBrain._second_round``), and one that is to be
        worked on in a copy of a session was filed with the copy
        (``fork_session``). Otherwise: a task agent started by another gets a session
        of its own, whose parent is the session of the one that started it;
        a commission filed under a project gets the project's session, the
        one all commissions of that project are worked on in, for this
        character; any other gets a new session. No model is asked.

        A session that is made for a commission takes that commission's id
        as its own, as every session did when a commission and its session
        were one: its directory is named as it always was, and only a
        commission that joins a session another one opened has a session
        id that is not its own.
        """
        from config.nekoclaw_settings import TASK_AGENT_SESSION_NAME_MAX_TOKENS
        from utils.tokenize import truncate_to_tokens

        if commission.session_id:
            self._session_of[commission.id] = commission.session_id
            return commission
        commission = await self._file_under_project(commission)
        if commission.parent_id is not None:
            parent = await self._ledger.get(commission.parent_id)
            parent_session = await self._ledger.get_session(parent.session_id) if parent is not None else None
            lead = parent_session.name if parent_session is not None else commission.parent_id
            commission, session = await self._ledger.give_session(
                commission.id,
                kind="child",
                name=f"{lead}/{commission.name or commission.id}",
                parent_id=parent_session.id if parent_session is not None else None,
            )
        elif commission.project and commission.project != DEFAULT_PROJECT:
            commission, session = await self._ledger.give_session(
                commission.id, kind="project", name=commission.project
            )
        else:
            commission, session = await self._ledger.give_session(
                commission.id,
                kind="single",
                name=truncate_to_tokens(commission.title, TASK_AGENT_SESSION_NAME_MAX_TOKENS),
            )
        self._session_of[commission.id] = session.id
        return commission

    async def _instructions(self, commission: Commission, *, with_gateway: bool) -> str:
        """Return what the host adds to the system prompt of the session a commission is worked on in.

        Nothing of the commission itself: a session takes several, and its
        system prompt is kept as it was first opened with (the runtime
        reads these again only after the conversation was summed up).
        How the host's capabilities are reached, whom the task agent works
        for, and what the task agent space holds: for every task agent,
        then for the project.

        In that order, which is from what every session is told alike to
        what only this one is: the system prompts of two sessions are then
        the same up to where they differ, and a provider that kept the
        head of one session's requests has it for the other's as well.
        (The runtime puts what it says itself of one session, its working
        directory, behind all of this: ``runtime/prompt.py``.)
        """
        from config.nekoclaw_settings import TASK_AGENT_BRIEF_MAX_TOKENS
        from config.prompts import prompts_nekoclaw_brain as texts
        from utils.tokenize import truncate_to_tokens

        parts = [texts.TASK_AGENT_BRIEF_GATEWAY_NOTE] if with_gateway else []
        if commission.parent_id is not None:
            parts.append(texts.TASK_AGENT_CHILD_FRAME)
        else:
            parts.append(texts.TASK_AGENT_SESSION_FRAME.format(lanlan_name=commission.lanlan_name))
        own = truncate_to_tokens("\n\n".join(parts), TASK_AGENT_BRIEF_MAX_TOKENS)
        kept = await self._space_brief(commission)
        # After the cap above and with one of its own (``SPACE_BRIEF_MAX_TOKENS``):
        # neither part can push the other out.
        return own + "\n\n" + kept if kept else own

    def _keep_excerpt(self, commission: Commission) -> None:
        """Keep what she and the user said just before a commission, until its session is opened.

        Her own commissions only: a task agent that another one started is
        told its part by that one. Taken now, when the commission is handed
        over, and not when its session is opened: it may wait for a place,
        and what is said meanwhile is not what it refers to. Kept in memory;
        no model is asked and nothing is read from anywhere else.
        """
        if self._conversation is None or commission.parent_id is not None or commission.id in self._excerpts:
            return
        try:
            turns = self._conversation.recent(commission.lanlan_name)
        except Exception:  # noqa: BLE001 - it starts without an excerpt
            logger.warning("reading what was said before a commission failed", exc_info=True)
            return
        if turns:
            self._excerpts[commission.id] = list(turns)

    def _excerpt(self, commission: Commission) -> str:
        """Return the part of a commission's opening message that holds what was said just before it; empty for none.

        Word for word, the newest messages first to be kept, within a cap
        of its own (``TASK_AGENT_EXCERPT_MAX_TOKENS``; the lines that say
        what it is come on top). Wrapped as background that gives no
        instructions: what she said in it she said to the user.
        """
        from config.nekoclaw_settings import TASK_AGENT_EXCERPT_MAX_TOKENS, TASK_AGENT_EXCERPT_MESSAGE_MAX_TOKENS
        from config.prompts import prompts_nekoclaw_brain as texts
        from nekoclaw.host.conversation import excerpt_lines

        # (Nothing is kept for a task agent another one started: ``_keep_excerpt``.)
        turns = self._excerpts.get(commission.id)
        if not turns:
            return ""
        # With the cap at nothing no line fits, and nothing is handed over.
        lines = excerpt_lines(
            turns,
            lanlan_name=commission.lanlan_name,
            max_tokens=TASK_AGENT_EXCERPT_MAX_TOKENS,
            message_max_tokens=TASK_AGENT_EXCERPT_MESSAGE_MAX_TOKENS,
            # Stated by the commission itself, right above.
            already_told=commission.user_text,
        )
        if not lines:
            return ""
        return "\n".join(
            [
                texts.TASK_AGENT_COMMISSION_EXCERPT_INTRO.format(lanlan_name=commission.lanlan_name),
                texts.TASK_AGENT_COMMISSION_EXCERPT_OPEN,
                *lines,
                texts.TASK_AGENT_COMMISSION_EXCERPT_CLOSE,
            ]
        )

    async def _opening(self, commission: Commission, *, cleared: bool, back: bool = False, excerpt: str = "") -> str:
        """Return the message that opens a commission in its session.

        What was dispatched and what the user said; which earlier commission
        it follows up, if one; how the user wants such things done; the
        entries the task brain picked for it, which are called by name
        through the gateway's call tool (they are not among the session's
        tools, which stay the same from one commission to the next); the
        language to answer in. ``cleared`` says that what was said in the
        session before was removed since: a commission that follows another
        up is then told what came of that one. ``back`` says that the
        commission was opened in this session before and that the session
        worked on others since. ``excerpt`` is what was said in the main
        dialogue just before the commission (``_excerpt``), for a session
        that holds nothing to tell what the commission refers to.

        What a task agent hands to one it starts is not cut: it is all that
        one is told of its part, and the task agent that wrote it is told
        that the other was started. (What she dispatches is short by the
        time it is filed, ``DISPATCH_TEXT_MAX_TOKENS``.)
        """
        from config.nekoclaw_settings import TASK_AGENT_BRIEF_MAX_TOKENS, TASK_AGENT_CONTINUED_RESULT_MAX_TOKENS
        from config.prompts import prompts_nekoclaw_brain as texts
        from config.prompts.prompts_nekoclaw import REPLY_LANGUAGE_SENTENCE
        from utils.tokenize import truncate_to_tokens

        whole = ""
        if commission.parent_id is not None:
            whole = texts.TASK_AGENT_CHILD_COMMISSION_TEMPLATE.format(
                description=commission.title, dispatch_text=commission.dispatch_text
            )
            parts = []
        else:
            parts = [
                texts.TASK_AGENT_COMMISSION_TEMPLATE.format(
                    commission_id=commission.id,
                    dispatch_text=commission.dispatch_text,
                    user_text=commission.user_text or commission.dispatch_text,
                )
            ]
        if back:
            parts.append(texts.TASK_AGENT_COMMISSION_BACK)
        earlier = await self._ledger.get(commission.continues) if commission.continues else None
        if earlier is not None:
            if cleared:
                result = truncate_to_tokens(
                    earlier.result_summary or earlier.status_reason or earlier.status,
                    TASK_AGENT_CONTINUED_RESULT_MAX_TOKENS,
                )
                parts.append(texts.TASK_AGENT_COMMISSION_CONTINUES_CLEARED.format(title=earlier.title, result=result))
            else:
                parts.append(texts.TASK_AGENT_COMMISSION_CONTINUES.format(title=earlier.title))
        preferences = await self._preferences.render(f"{commission.dispatch_text}\n{commission.user_text}")
        if preferences:
            parts.append(f"{texts.TASK_AGENT_BRIEF_PREFERENCES_HEADER}\n{preferences}")
        capped = truncate_to_tokens("\n\n".join(parts), TASK_AGENT_BRIEF_MAX_TOKENS) if parts else ""
        opening = "\n\n".join(part for part in (whole, capped) if part)
        # After the cap above, each within a cap of its own.
        if excerpt:
            opening += "\n\n" + excerpt
        picked = await self._picked_entries(commission)
        if picked:
            opening += "\n\n" + picked
        language = _REPLY_LANGUAGES.get(commission.lang or "", None)
        if language:
            # Said with every commission: the session's system prompt names
            # the language of the commission it was first opened for.
            opening += "\n\n" + REPLY_LANGUAGE_SENTENCE.replace("<<LANGUAGE>>", language)
        return opening

    async def _picked_entries(self, commission: Commission) -> str:
        """Return the part of a commission's opening message that tells of the entries picked for it; empty for none."""
        from config.prompts import prompts_nekoclaw_brain as texts

        if self._gateway is None:
            return ""
        try:
            definitions = await self._gateway.candidate_definitions(commission.id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - it starts all the same; the search tool finds them
            logger.warning("listing the entries picked for a commission failed", exc_info=True)
            return ""
        if not definitions:
            return ""
        lines = [json.dumps(definition, ensure_ascii=False) for definition in definitions]
        return "\n".join([texts.TASK_AGENT_COMMISSION_CANDIDATES_HEADER, *lines])

    async def _space_brief(self, commission: Commission) -> str:
        """Return what the task agent space holds for a commission; empty when that is nothing.

        A space that cannot be read does not keep a task agent from starting.
        """
        if self._space is None:
            return ""
        try:
            return await self._space.brief(commission.project)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - it starts without what was kept
            logger.warning("reading the task agent space for a task agent failed", exc_info=True)
            return ""

    async def _use_project(self, commission: Commission) -> None:
        """Tell the space that a commission of a project is being taken up; see ``TaskAgentSpace.use_project``.

        Before its instructions are put together and long before the ledger
        says it is working: a project that just now looked idle is not
        archived under it.
        """
        if self._space is None or not commission.project:
            return
        try:
            await self._space.use_project(commission.project)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - it starts all the same
            logger.warning("noting that a project is in use failed", exc_info=True)

    async def _file_under_project(self, commission: Commission) -> Commission:
        """File a commission that starts for the first time under the project its words name.

        Only her own commissions, and only before anything of theirs is on
        the disk: where the session is kept follows from the project. With
        the project comes its working directory, where its files are. No
        model is asked; see ``TaskAgentSpace.assign_project``.
        """
        if (
            self._space is None
            or commission.parent_id is not None
            or commission.project is not None
            or commission.session_dir
            or commission.started_at is not None
        ):
            return commission
        try:
            project = await self._space.assign_project(commission)
            if project is None:
                return commission
            values: dict[str, Any] = {"project": project.name}
            if not commission.workdir and project.workdir:
                from pathlib import Path

                if await asyncio.to_thread(Path(project.workdir).is_dir):
                    values["workdir"] = project.workdir
            return await self._ledger.update(commission.id, **values) or commission
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - it is one of the commissions that belong to no project
            logger.warning("filing a commission under a project failed", exc_info=True)
            return commission

    async def _launch(
        self, commission_id: str, *, prompt: str | None = None, still_wanted: Callable[[], bool] | None = None
    ) -> bool:
        """Start a commission whose place is taken; a place it leaves unused goes to the queue."""
        started = await self._start(commission_id, prompt=prompt, still_wanted=still_wanted)
        if not started:
            await self._start_next()
        return started

    async def _start(
        self, commission_id: str, *, prompt: str | None = None, still_wanted: Callable[[], bool] | None = None
    ) -> bool:
        """Open the session of a commission and give it its work. Returns whether it started.

        The caller has taken the commission's place with ``_take_place``. When
        this returns False the place is given back, at once or, for a session
        that has to be closed again, once it is closed.

        ``still_wanted`` comes with a commission that was over and is taken
        up again (``tell``). A stop of everything ends what is under way,
        and such a commission is not until its status is written here: the
        stop does not find it. So it is asked here, after everything this
        waits for and before the commission is given its work. (It says no
        as well when NekoClaw was switched off meanwhile.)
        """
        session = self._sessions.get(commission_id)
        if session is None:
            return False

        def unwanted() -> bool:
            return still_wanted is not None and not still_wanted()

        # The sentence this start was given is the user's from the outset:
        # a failure while it is set up keeps it too.
        session.carried = [prompt] if prompt is not None else []
        carried: list[str] = []
        try:
            held = self._held.get(commission_id)
            if held is not None:
                # Its review is being written down this moment: it goes on
                # once that is done.
                await held.wait()
            clearing = self._clearing.get(session.session_id)
            if clearing is not None:
                # What was said in its session long ago is being removed
                # this moment; the session is opened once that is done.
                await clearing.wait()
            # The host is summing its session up this moment, nothing having
            # been at work in it: that is cut short, and the session is the
            # commission's once the runtime has let go of it.
            await self._stop_compacting(session.session_id)
            commission = await self._ledger.get(commission_id)
            if commission is None or commission.status not in ("queued", "blocked", "failed", "waiting_user"):
                self._discard(commission_id)
                return False
            config = await self._model_config()
            if not config or not config.get("model"):
                raise RuntimeError("no model is configured for task agents")
            await self._use_project(commission)
            kept = await self._ledger.get_session(session.session_id)
            if kept is None:
                raise RuntimeError("the session of the commission is not on record")
            session.cleared_before = kept.status == "cleared"
            # Where a session is kept and where its task agent works are the
            # session's, on record from when it was first opened. Until then
            # they follow from what its first commission brings: the working
            # directory of its project, or the one it was started with.
            place = dataclasses.replace(
                kept, workdir=kept.workdir or commission.workdir, project=kept.project or commission.project
            )
            from nekoclaw.runtime.protocol import GatewaySpec

            def prepare() -> tuple[str, str, str | None]:
                # In here: where the session is kept, and which runtime holds
                # what was said in it, is told by what is on the disk.
                session_dir = self._session_dir_of(place)
                session_dir.mkdir(parents=True, exist_ok=True)
                workdir = self._workdir_of(place, session_dir)
                workdir.mkdir(parents=True, exist_ok=True)
                return str(session_dir), str(workdir), _begun_in(place, session_dir)

            session_path, workdir, begun_in = await asyncio.to_thread(prepare)
            # Whether this commission was opened in its session before: the
            # message that says what it is was sent, and taken. And whether
            # it is the one its session is at: a commission that was stuck
            # lets the session go on with the next, and what it is told
            # after that reads as part of that other one.
            opened = await self._ledger.has_event(commission.id, "opened")
            at_it = opened and await self._ledger.opened_last(session.session_id) == commission.id

            model = await self._model_of(config)
            session.model = (config.get("base_url"), config.get("model"))

            gateway_spec = None
            if self._gateway is not None:
                # Whether or not anything can be called right now: the
                # session keeps the tools it is opened with, and what can be
                # called changes while it lasts.
                url = await self._gateway.start()
                token = self._gateway.issue(
                    commission.id,
                    candidate_plugin_ids=self._candidates.get(commission.id, ()),
                    supports_images=model.supports_images,
                    # A copy of a session changes nothing, whichever way.
                    reads_only=kept.kind == "fork",
                )
                gateway_spec = GatewaySpec(url=url, token=token)

            spec = await self._spec(
                commission, kept, model, workdir=workdir, session_dir=session_path, gateway=gateway_spec
            )
            runtime_name, runtime, fallback = await self._choose_runtime(commission, model, begun_in)
            session.runtime, session.runtime_name = runtime, runtime_name
            if unwanted():
                # Everything was stopped while this was being set up, and
                # the stop left a commission that was over alone: it stays
                # as it was. What it was to be told goes with the stop.
                self._notes.pop(commission.id, None)
                self._discard(commission.id)
                return False
            moved = await self._ledger.transition(
                commission.id,
                "working",
                reason="a task agent took it up",
                runtime=runtime_name,
                route="task_agent",
                workdir=workdir,
                session_dir=session_path,
            )
            if not moved.applied:
                # Stopped while it was being prepared.
                self._discard(commission.id)
                return False
            # On record with the session from here on: a later commission of
            # it is opened in the same place. (That the session is at work
            # was written with the commission's status.)
            await self._ledger.update_session(
                session.session_id,
                runtime=runtime_name,
                workdir=workdir,
                session_dir=session_path,
                project=place.project,
            )
            if fallback is not None:
                # It was to run in dsh and runs in the built-in runtime instead.
                await self._ledger.append_event(
                    commission.id, "runtime_fallback", {"wanted": RUNTIME_DSH, "runtime": runtime_name, "reason": fallback}
                )
            notes = self._notes.pop(commission.id, [])
            carried = self._carried.pop(commission.id, [])
            session.carried = [*carried, *([prompt] if prompt is not None else []), *notes]

            # What the commission is: said once, in the session it is worked
            # on in, and again only where that is not there to be read, or
            # where the session has been at another commission since.
            # In three wordings, of which one is used: for a session whose
            # conversation is there to be read in the runtime that takes the
            # commission, for one that has none there (never begun, removed
            # since, or in the other runtime), and for the first of these
            # when the session was at another commission since.
            # A session that holds nothing in the runtime that takes the
            # commission (the second wording) is told, besides, what she
            # and the user said just before it: a session with something
            # in it has that to tell what the commission refers to.
            excerpt = self._excerpt(commission)
            onward = await self._opening(commission, cleared=False)
            anew = (
                await self._opening(commission, cleared=True, excerpt=excerpt)
                if commission.continues or excerpt
                else onward
            )
            back_at_it = await self._opening(commission, cleared=False, back=True) if opened and not at_it else onward

            def first_message(runtime_name: str) -> tuple[str, bool]:
                # The commission is opened when it starts, and again when it
                # is taken up where its opening message is not: it never
                # reached the session (the start failed before the session
                # took it), or what was said is in the other runtime (began
                # in dsh, dsh is gone or would not take it), or nowhere any
                # more (removed, being old). Also when its session took the
                # opening message of another commission since: the session
                # is told which commission it is back at, and the runtime
                # keeps this message, not the other one's, through a summary.
                # Otherwise it goes on from what the session holds, with
                # what it is told now.
                here = begun_in == runtime_name
                opens = prompt is None or not opened or not here or not at_it
                # Which runtime it is decides the wording, as it decides
                # whether it is said: a commission that follows another up
                # in a session that began in dsh and is taken by the
                # built-in runtime finds nothing "above", and is told what
                # came of the other instead.
                said = anew if not here else back_at_it if opened else onward
                parts = [*([said] if opens else []), *carried, *([prompt] if prompt is not None else [])]
                return "\n\n".join([*parts, *notes]), opens

            text, opens = first_message(runtime_name)
            await runtime.start(spec)
            session.opened_in_runtime = True
            if runtime_name == RUNTIME_DSH and session.session_id not in runtime.session_ids:
                # dsh was made ready and still opened no session. It says
                # why in an event, which is on its way and is where the
                # reason is recorded (``_on_error``). dsh does not hold the
                # commission, so the built-in runtime takes it here and now,
                # as when dsh could not be made ready (starting over, should
                # dsh have a session of it from before): the user is not
                # left with a failed commission to hand in again.
                session.dsh_refused = True
                session.opened_in_runtime = False
                runtime_name, runtime = RUNTIME_BUILTIN, self._runtimes.builtin()
                session.runtime, session.runtime_name = runtime, runtime_name
                text, opens = first_message(runtime_name)
                await self._ledger.update(commission.id, runtime=runtime_name)
                await self._ledger.update_session(session.session_id, runtime=runtime_name)
                await runtime.start(spec)
                session.opened_in_runtime = True
            if runtime_name == RUNTIME_DSH and begun_in == RUNTIME_DSH and not runtime.resumed(session.session_id):
                # dsh is noted to have the session, and could not take it up
                # (its log is gone, say): it began a new one, in which
                # nothing was said. So the commission is opened there as in
                # a session that holds nothing, with what it is and, when
                # it follows another up, what came of that one.
                begun_in = None
                text, opens = first_message(runtime_name)

            async def stopped_meanwhile() -> bool:
                current = await self._ledger.get(commission.id)
                if self._sessions.get(commission.id) is not session or current is None or current.status != "working":
                    return True
                if unwanted():
                    # Everything was stopped while its status was being
                    # written: the stop looked at a commission that was
                    # over, and this one is at work with nothing to end it.
                    # It is stopped here, as it would have been there.
                    await self._ledger.transition(commission.id, "cancelled", reason=_STOPPED_MEANWHILE_REASON)
                    return True
                return False

            # Starting can take a while (the runtime process coming up). A stop
            # meanwhile found no session to close; this one must get no work.
            # The first request is paid for after that and before the prompt
            # that makes it: with the quota used up, a task agent sends
            # nothing at all, and a start that fails or is stopped costs
            # nothing. (Later requests are counted as their usage is reported.)
            # Counting takes a moment too, so a stop is looked for again; a
            # stop that lands then has paid for a request it does not make.
            if await stopped_meanwhile():
                session.closing = True
                await runtime.close(session.session_id)
                return False
            if not await self._pay_first_request(session):
                # What the user added before and while it was being set up
                # stays for when it is taken up again, as it did when this
                # failed before anything was started. The session keeps its
                # place until its own close comes back, as every other close
                # does: a new session of the same commission started before
                # that would be released by it.
                self._keep_unsent(session)
                await self._fail(commission.id, _QUOTA_REASON)
                await self._close_session(session)
                # What was passed on while the failure was being written.
                self._keep_unsent(session)
                return False
            if await stopped_meanwhile():
                # The stop has closed the session already.
                await self._close_session(session)
                return False
            await self._send_text(runtime, session, "prompt", text, opening=opens)
            session.ready = True
            # What was passed on while the session was being set up. Like the
            # prompt, it is the user's until the run is seen at work.
            for note in self._notes.pop(commission.id, []):
                session.carried.append(note)
                await self._send_text(runtime, session, "steer", note)
            return True
        except asyncio.CancelledError:
            if not self._closed and self._close_unstarted(session):
                # Its session is open in a runtime: the place goes to the
                # queue when its close comes back.
                raise
            self._discard(commission_id)
            if not self._closed:
                # Whoever started it was cancelled; the queue still gets the place.
                self._spawn(self._start_next())
            else:
                # Shutting down gave up on this start, possibly after it was
                # recorded as working: with its session gone, the commission
                # ends here rather than be left working for nobody.
                await asyncio.shield(
                    self._ledger.transition(commission_id, "failed", reason=_SHUT_DOWN_REASON)
                )
            raise
        except Exception as exc:  # noqa: BLE001 - the commission fails with the reason
            logger.warning("starting a task agent for %s failed: %s", commission_id, exc)
            # Kept before the session is let go: the user's next words may
            # take the commission up again while the failure is written.
            self._keep_unsent(session)
            if not self._close_unstarted(session):
                # Before the place is given back, which has the hold on the
                # session looked at, and its status written.
                await self._leave_cleared(session)
                self._discard(commission_id)
            await self._fail(commission_id, f"the task agent could not be started: {exc}")
            self._keep_unsent(session)
            return False

    async def _model_of(self, config: dict[str, Any]) -> Any:
        """Return the ``ModelConfig`` a session is opened with, from the model as it is configured.

        With what is remembered of the model where the configuration does
        not say: whether it sees pictures, the window its sessions are
        managed for, and the length from which a conversation is summed up.
        """
        from nekoclaw.runtime import ModelConfig

        if "supports_images" not in config:
            config = {**config, "supports_images": await self._sees_images(config)}
        if "context_window" not in config:
            config = {**config, "context_window": await self._context_window(config)}
        if "compact_at" not in config:
            config = {**config, "compact_at": await self._compact_at(config)}
        return ModelConfig(**config)

    async def _spec(
        self, commission: Commission, kept: Session, model: Any, *, workdir: str, session_dir: str, gateway: Any
    ) -> Any:
        """Return what a runtime is given to open a session with, for the commission that is worked on in it.

        ``kept`` is the session as the ledger has it. One that was made
        from another by copying what was said in it is opened without the
        tools that write files, run commands and call capabilities
        (``TASK_AGENT_FORK_WITHHELD_TOOLS``): it has the working directory
        of the session it was copied from, and a working directory is
        written by one session at a time. Its token for the gateway
        calls nothing either (``_start``).
        """
        from config.nekoclaw_settings import TASK_AGENT_FORK_WITHHELD_TOOLS
        from nekoclaw.runtime import TaskAgentSpec

        image_retention: dict[str, int] = {}
        image_pools: dict[str, str] = {}
        if self._gateway is not None:
            # For every built-in tool that hands pictures back, whether it
            # can be called right now or not: every one of them is reached
            # through the gateway's call tool, one that is switched on while
            # the task agent works included, and its screenshots are
            # limited too.
            image_retention, image_pools = self._image_retention(self._registry.builtins())
        # A task agent started by a task agent starts none itself.
        withheld = {"subagent"} if commission.parent_id is not None else set()
        if kept.kind == "fork":
            withheld.update(TASK_AGENT_FORK_WITHHELD_TOOLS)
        return TaskAgentSpec(
            session_id=kept.id,
            workdir=workdir,
            session_dir=session_dir,
            model=model,
            instructions=await self._instructions(commission, with_gateway=gateway is not None),
            reply_language=_REPLY_LANGUAGES.get(commission.lang or "", None),
            top_level=commission.parent_id is None,
            disabled_tools=sorted(withheld),
            image_retention=image_retention,
            image_pools=image_pools,
            gateway=gateway,
        )

    async def _leave_cleared(self, session: _Session) -> None:
        """Write a session down as cleared again after a start that failed before a runtime had it open.

        What was said in it had been removed, being old. The commission
        was set working on the way, which says of the session that it is
        at work; when the hold on it is let go it would be written down as
        closed, as one with a conversation in it. No runtime opened it:
        nothing is in it still.
        """
        if not session.cleared_before or session.opened_in_runtime:
            return
        try:
            await self._ledger.update_session(session.session_id, status="cleared")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the status is for showing; the start has failed as it is
            logger.warning("noting that a session is still cleared failed", exc_info=True)

    def _close_unstarted(self, session: _Session) -> bool:
        """Close the runtime session of a start that failed after a runtime had opened it. False when none had.

        Such a start is not over when it fails: the session is still open
        in the runtime, without its work. Discarded as one that was never
        opened, its place and the session it holds would be free while the
        runtime still has it, and the next commission of the session would
        be started into a session that is there already. Closed instead, it
        keeps both until its close comes back, as every session does.
        """
        runtime = self._runtime_of(session)
        if not session.opened_in_runtime or runtime is None or session.closing:
            return False
        session.closing = True

        async def close() -> None:
            try:
                await runtime.close(session.session_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a runtime that cannot close it no longer has it
                logger.warning("closing the session of a start that failed did not work", exc_info=True)
                if self._sessions.get(session.commission_id) is session:
                    self._discard(session.commission_id)
                    self._spawn(self._start_next())

        self._spawn(close())
        return True

    async def _sees_images(self, config: dict[str, Any]) -> bool:
        """Return whether the task agent model is sent pictures; see ``model_capabilities.py``."""
        if self._model_capabilities is None:
            return True
        try:
            return bool(await self._model_capabilities.sees_images(config.get("base_url"), config.get("model")))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - not knowing is the same as not having learned otherwise
            logger.warning("looking up what the task agent model can see failed", exc_info=True)
            return True

    async def _context_window(self, config: dict[str, Any]) -> int:
        """Return the window a session with the task agent model is managed for; see ``model_capabilities.py``.

        What the user set for the model, else the setting; and no more than
        the model's provider said it takes, when that was learned.
        """
        from config.nekoclaw_settings import TASK_AGENT_CONTEXT_WINDOW

        if self._model_capabilities is None:
            return TASK_AGENT_CONTEXT_WINDOW
        try:
            return int(await self._model_capabilities.context_window(config.get("base_url"), config.get("model")))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - not knowing is the same as nothing set and nothing learned
            logger.warning("looking up the window of the task agent model failed", exc_info=True)
            return TASK_AGENT_CONTEXT_WINDOW

    async def _compact_at(self, config: dict[str, Any]) -> int | None:
        """Return the length from which a session with the task agent model is summed up; see ``model_capabilities.py``.

        None leaves it to the runtime, which takes the same part of the
        window as the presets are of each other.
        """
        if self._model_capabilities is None:
            return None
        try:
            # Of the window this session is started with, which the model's
            # configuration may have named itself.
            window = config.get("context_window")
            return int(
                await self._model_capabilities.compact_at(
                    config.get("base_url"), config.get("model"), window=int(window) if window else None
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - not knowing is the same as nothing set
            logger.warning("looking up where a conversation with the task agent model is summed up failed", exc_info=True)
            return None

    @staticmethod
    def _image_retention(capabilities: list[Any]) -> tuple[dict[str, int], dict[str, str]]:
        """Return how many screenshots of the built-in tools stay in a task agent's context.

        Every tool that needs a model that sees pictures hands screenshots
        back. The first value gives the number for each such tool; the second
        puts them all in one pool, so the number is the most that stay in
        the context at all, whichever tools they came from: what one
        screenshot may weigh is set for that many (``SCREENSHOT_MAX_BYTES``).
        """
        from config.nekoclaw_settings import TASK_AGENT_SCREENSHOTS_KEPT
        from nekoclaw.host.gateway import CALL_TOOL

        retention: dict[str, int] = {}
        for capability in capabilities:
            if capability.needs_images and capability.group:
                retention[capability.tool_name] = TASK_AGENT_SCREENSHOTS_KEPT
        if retention:
            # A task agent calls these tools by name through the gateway's
            # own call tool (they are loaded a group at a time and never
            # listed), so their screenshots come back as results of that
            # tool: counted along. Their own names stay in, for a runtime
            # that calls the gateway by them.
            retention[CALL_TOOL] = TASK_AGENT_SCREENSHOTS_KEPT
        return retention, {name: SCREENSHOT_POOL for name in retention}

    async def _pay_first_request(self, session: _Session) -> bool:
        """Count a session's first model request before it is made; False when the quota is used up.

        A meter that cannot count does not stop the work.
        """
        if self._meter is None:
            return True
        try:
            allowed = await self._meter(session.commission_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a meter that cannot count does not stop the work
            logger.warning("counting a task agent's model request failed", exc_info=True)
            return True
        if allowed:
            session.prepaid_requests += 1
        return bool(allowed)

    @staticmethod
    async def _send_text(runtime: Any, session: _Session, command: str, text: str, *, opening: bool = False) -> None:
        """Send ``prompt`` or ``steer`` text to a session, counting it.

        Counted before it is sent: the end of a run that has not taken it may
        be handled while the command is on its way. ``opening`` says that
        the text opens the commission (a ``prompt``): the runtime keeps it
        word for word when it sums the conversation up.
        """
        session.sent += 1
        # A steer to an idle session starts a new run. An answer arriving
        # meanwhile must then wait for that run to end (``held_answer``)
        # rather than be sent as a prompt the busy session refuses.
        woke = command == "steer" and session.run_ended
        if woke:
            session.run_ended = False
        try:
            if opening:
                session.opening_sent = True
                await runtime.prompt(session.session_id, text, opening=True)
            else:
                await getattr(runtime, command)(session.session_id, text)
        except BaseException:
            session.sent -= 1
            if opening:
                session.opening_sent = False
            if woke:
                session.run_ended = True
            raise

    def _keep_unsent(self, session: _Session) -> None:
        """Keep what the user said that a failed start did not get into the session, for the next start.

        What this start was to send, then what was passed on since, in the
        order it came: the next start sends it ahead of what it is given.
        Called once the failure is written, so what came meanwhile is kept too.
        A session closed by a stop keeps nothing: its commission is over.
        """
        commission_id = session.commission_id
        if session.closing and not session.keep_notes:
            session.carried = []
            return
        session.keep_notes = True
        kept = [*session.carried, *self._notes.pop(commission_id, [])]
        session.carried = []
        if kept:
            self._carried[commission_id] = [*self._carried.get(commission_id, []), *kept]

    def _discard(self, commission_id: str) -> None:
        """Give a commission's place back: its session is not, or no longer, open in a runtime for it.

        The commission goes on holding its session until it is known to be
        over (``_settle_hold``): one that waits for an answer keeps it.
        """
        session = self._sessions.pop(commission_id, None)
        self._candidates.pop(commission_id, None)
        self._excerpts.pop(commission_id, None)
        self._search_rulings.pop(commission_id, None)
        if self._gateway is not None:
            self._gateway.revoke(commission_id)
        session_id = session.session_id if session is not None else self._session_of.get(commission_id)
        if session_id and not self._closed:
            self._spawn(self._settle_hold(session_id))

    async def _settle_hold(self, session_id: str) -> None:
        """Let a session go once the commission that holds it is over and has left the runtime.

        Until then nothing else is worked on in it: the next commission of
        the session would find a runtime session still closing, or take its
        turn in the middle of a question and its answer. Over means done,
        failed, stopped, or blocked (a blocked commission is taken up again
        when the user says more of it, and queues for its session then like
        any other). The session's status is written while it is still held.
        """
        commission_id = self._held_by.get(session_id)
        if commission_id is None or self._closed or self._in_hand(commission_id):
            return
        commission = await self._ledger.get(commission_id)
        if self._held_by.get(session_id) != commission_id:
            # Let go meanwhile, by another look at the same hold, and maybe
            # taken by the next commission: its status is that one's to say.
            return
        try:
            # By what the commission is when this is written, not by what
            # was read above: it may have been taken up again since (an
            # answer came), and is then at work, which is on record.
            await self._ledger.settle_session(session_id, commission_id)
        except Exception:  # noqa: BLE001 - the status is for showing; the hold is what counts
            # The hold is let go all the same: left here, nothing else
            # would ever start in the session.
            logger.warning("noting where a session stands failed", exc_info=True)
        # Looked at again: it may have been taken up, or let go, meanwhile.
        if self._held_by.get(session_id) != commission_id or self._closed or self._in_hand(commission_id):
            return
        if commission is not None and commission.status not in ENDED_STATUSES:
            # It waits for the user, or for task agents it started.
            return
        del self._held_by[session_id]
        if commission is not None:
            # Nothing more is asked of the user for it, and nobody is told
            # to hand anything over: it is over (taken up again, a stuck
            # one is asked about anew).
            self._usage_settled(commission)
        if self._queue:
            self._spawn(self._start_next())
        # Nothing is at work in it from here on: when that lasts, and the
        # host is set to, its conversation is summed up.
        self._schedule_idle_compact(session_id)

    def _settle_hold_of(self, commission_id: str) -> None:
        """Have the hold on a commission's session looked at again, if it holds one.

        For where a commission leaves the hands it was in (the queue, a
        start or a queueing under way) with no runtime session of its own
        to end: whoever looked while it was in hand left the hold alone,
        and nobody would look again. The session would stay held by a
        commission that is over, and nothing else would ever start in it.
        """
        session_id = self._session_of.get(commission_id)
        if session_id is not None and self._held_by.get(session_id) == commission_id and not self._closed:
            self._spawn(self._settle_hold(session_id))

    def _on_commission_change(self, commission: Commission, previous: str | None) -> None:
        """A ledger listener: a commission that held its session without a place in a runtime is over."""
        del previous
        session_id = commission.session_id
        if (
            not self._closed
            and session_id
            and commission.status in ENDED_STATUSES
            and self._held_by.get(session_id) == commission.id
            and not self._in_hand(commission.id)
        ):
            self._spawn(self._settle_hold(session_id))

    async def _release(self, commission_id: str) -> None:
        """Forget a session that is gone and give its place to the next in the queue.

        Called while a runtime event is handled. The next session is started
        as a task of its own, which shutting down waits for: waiting here for
        it to come up would hold up every other event meanwhile.
        """
        if commission_id in self._queue or commission_id in self._requeueing:
            # Queued to be taken up again: what was said meanwhile goes along
            # with it, and so do its candidate plugins. It goes on holding
            # its session.
            self._sessions.pop(commission_id, None)
            if self._gateway is not None:
                self._gateway.revoke(commission_id)
        else:
            session = self._sessions.get(commission_id)
            self._discard(commission_id)
            if session is None or not session.keep_notes:
                self._notes.pop(commission_id, None)
        if self._queue and not self._closed:
            self._spawn(self._start_next())

    async def _start_next(self) -> None:
        """Give free places to queued commissions, in the order they were queued."""
        while not self._closed and len(self._sessions) < self.limit:
            # One whose old session is still on its way out keeps its turn;
            # it is started when that session has gone. So does one whose
            # session another commission holds: it is next when that one is
            # over, and those behind it in other sessions go ahead meanwhile.
            commission_id = next(
                (item for item in self._queue if item not in self._sessions and self._session_free_for(item)), None
            )
            if commission_id is None:
                return
            self._queue.remove(commission_id)
            if self._take_place(commission_id):
                await self._start(commission_id, prompt=self._resume.pop(commission_id, None))
            else:
                # Its session is not known, which every way into the queue
                # sees to: it must not stay queued for nobody.
                logger.error("queued commission %s has no session to start in", commission_id)
                self._resume.pop(commission_id, None)
                await self._fail(commission_id, "the task agent could not be started: its session is not known")

    async def _know_session(self, commission_id: str) -> bool:
        """Make a commission's session known to this manager, from the ledger; False for a commission without one.

        For a commission that is taken up again: this process may never
        have handed it to a task agent itself.
        """
        if commission_id in self._session_of:
            return True
        commission = await self._ledger.get(commission_id)
        if commission is None or commission.route != "task_agent":
            return False
        await self._give_session(commission)
        return commission_id in self._session_of

    # -- events -----------------------------------------------------------

    async def _flush(self, session: _Session) -> None:
        """Write buffered text and tool output as event rows."""
        from config.nekoclaw_settings import TASK_AGENT_EVENT_TEXT_MAX_CHARS

        rows: list[tuple[str, dict[str, Any]]] = []
        if session.text:
            text = "".join(session.text)
            session.text.clear()
            session.text_size = 0
            for start in range(0, len(text), TASK_AGENT_EVENT_TEXT_MAX_CHARS):
                rows.append(("text", {"text": text[start : start + TASK_AGENT_EVENT_TEXT_MAX_CHARS]}))
        for call_id, parts in session.updates.items():
            text = "".join(parts)
            # Of long output the end is what shows where the tool stands.
            rows.append(
                (
                    "tool_update",
                    {
                        "tool_call_id": call_id,
                        "tool_name": session.update_names.get(call_id, ""),
                        "text": text[-TASK_AGENT_EVENT_TEXT_MAX_CHARS:],
                    },
                )
            )
        session.updates.clear()
        session.update_names.clear()
        if rows:
            await self._ledger.append_events(session.commission_id, rows)

    @staticmethod
    def _compact(event: dict[str, Any]) -> dict[str, Any]:
        """Return the event without ids the row already has, long strings cut."""
        from config.nekoclaw_settings import TASK_AGENT_EVENT_TEXT_MAX_CHARS

        compact: dict[str, Any] = {}
        for key, value in event.items():
            if key in ("type", "session_id"):
                continue
            if isinstance(value, (dict, list)):
                dumped = json.dumps(value, ensure_ascii=False, default=str)
                if len(dumped) > TASK_AGENT_EVENT_TEXT_MAX_CHARS:
                    value = dumped[:TASK_AGENT_EVENT_TEXT_MAX_CHARS] + "…"
            elif isinstance(value, str) and len(value) > TASK_AGENT_EVENT_TEXT_MAX_CHARS:
                value = value[:TASK_AGENT_EVENT_TEXT_MAX_CHARS] + "…"
            compact[key] = value
        return compact

    async def _handle(self, event: dict[str, Any]) -> None:
        from config.nekoclaw_settings import TASK_AGENT_EVENT_TEXT_MAX_CHARS

        kind = event.get("type")
        spoken_of = str(event.get("session_id") or "")
        forking = self._forks.get(spoken_of)
        if forking is not None and (kind == "forked" or (kind == "error" and event.get("command") == "fork")):
            # The runtime's word on a session it was asked to make from another.
            if not forking.done():
                forking.set_result(event)
            return
        given_up = self._forks_given_up.get(spoken_of)
        if given_up is not None and (kind == "forked" or (kind == "error" and event.get("command") == "fork")):
            # Its making was no longer waited for, and the runtime is
            # through with it now: what it wrote is on no record, and
            # nothing would ever come by it to remove it.
            del self._forks_given_up[spoken_of]
            if kind == "forked":
                import shutil

                await asyncio.shield(asyncio.to_thread(shutil.rmtree, given_up, True))
            return
        service = self._service.get(spoken_of)
        if service is not None:
            # Of a session the host has open by itself: no commission's.
            service.put_nowait(event)
            return
        # A runtime speaks of sessions; what it says is about the commission
        # that holds the session.
        session = self._sessions.get(self._held_by.get(spoken_of, ""))
        if session is None:
            # Process-level messages, and events of sessions already released.
            return
        if session.untaken_answer is not None and _shows_work_taken(event, session.sent):
            session.untaken_answer = None
        if session.sent > 0 and _shows_run_at_work(event):
            session.carried = []
            await self._note_opened(session)
        if kind == "text":
            delta = str(event.get("delta") or "")
            session.text.append(delta)
            session.text_size += len(delta)
            if session.text_size >= TASK_AGENT_EVENT_TEXT_MAX_CHARS:
                await self._flush(session)
            return
        if kind == "tool_update":
            call_id = str(event.get("tool_call_id") or "")
            session.updates.setdefault(call_id, []).append(str(event.get("text") or ""))
            session.update_names[call_id] = str(event.get("tool_name") or "")
            return
        await self._flush(session)
        await self._ledger.append_event(session.commission_id, str(kind), self._compact(event))
        if kind == "report":
            await self._on_report(session, event)
        elif kind == "ended":
            await self._on_ended(session, event)
        elif kind == "error":
            await self._on_error(session, event)
        elif kind == "usage":
            await self._on_usage(session, event)
        elif kind == "model_capability":
            await self._on_model_capability(session, event)
        elif kind == "compacted":
            await self._ledger.update_session(session.session_id, compacted_at=self._ledger._clock())

    async def _note_opened(self, session: _Session) -> None:
        """Put on record that a commission's opening message is in its session.

        Written once the session is seen to have taken it. A commission
        that is taken up again is then not told a second time what it is;
        one whose start failed before that is.
        """
        if not session.opening_sent:
            return
        session.opening_sent = False
        await self._ledger.append_event(session.commission_id, "opened", {"session_id": session.session_id})

    def _summary(self, text: str) -> str:
        from config.nekoclaw_settings import TASK_AGENT_RESULT_SUMMARY_MAX_TOKENS
        from utils.tokenize import truncate_to_tokens

        return truncate_to_tokens(text, TASK_AGENT_RESULT_SUMMARY_MAX_TOKENS)

    async def _on_report(self, session: _Session, event: dict[str, Any]) -> None:
        status = str(event.get("status") or "")
        summary = str(event.get("summary") or "")
        details = str(event.get("details") or "")
        if status == "progress":
            return
        commission_id = session.commission_id
        if status == "waiting":
            question = {"kind": "report", "question": summary, "details": details}
            moved = await self._ledger.transition(
                commission_id,
                "waiting_user",
                reason="the task agent needs an answer",
                pending_question=json.dumps(question, ensure_ascii=False),
            )
            if moved.applied and moved.commission is not None:
                session.awaiting_prompt = True
                session.run_ended = False
                session.held_answer = None
                await self._reporter.question(moved.commission, question)
            return
        target = _REPORT_TO_STATUS.get(status)
        if target is None:
            return
        text = f"{summary}\n{details}" if details else summary
        await self._end(
            commission_id,
            target,
            reason=f"the task agent reported {status}",
            text=text,
            suggested_delivery=event.get("delivery"),
            result_summary=self._summary(text),
        )

    def _run_over(self, commission_id: str) -> None:
        """A commission's run is over: what lasted for the run ends with it.

        That is the desktop and the browser it occupied (another commission
        may have them now), and what the user allowed "once" for the
        keyboard and mouse and for the browser.
        """
        self._permissions.end_run(commission_id)
        if self._leases is not None:
            self._leases.release(commission_id)

    async def _remind_of_window(self, session: _Session, base_url: Any, model: Any, was: int) -> None:
        """Have the user told, once for each model, that its window is smaller than was assumed.

        The sessions of the model are managed for the smaller window from
        now on without anyone doing anything; the user is told so that
        they set the model's context length themselves. Said to her, with
        her next turn (``Reporter.note``): no model is called for it.
        """
        from config.prompts.prompts_nekoclaw_brain import MODEL_WINDOW_REMINDER_TEXT
        from nekoclaw.host.task_brain import _texts

        window = await self._model_capabilities.claim_window_reminder(base_url, model)
        if window is None:
            return
        try:
            commission = await self._ledger.get(session.commission_id)
            if commission is None:
                raise LookupError("the commission that learned it is gone")
            text = _texts(MODEL_WINDOW_REMINDER_TEXT, commission.lang or "en").format(
                model=str(model or ""), was=was, window=window
            )
            await self._reporter.note(commission.lanlan_name, about="context_window", text=text, commission=commission)
        except asyncio.CancelledError:
            # Shutting down: not told, so not on record as told.
            self._model_capabilities.unclaim_window_reminder(base_url, model)
            raise
        except Exception:  # noqa: BLE001 - not told this time: told with the next refusal
            logger.warning("telling the user of the window of the task agent model failed", exc_info=True)
            self._model_capabilities.unclaim_window_reminder(base_url, model)
            return
        # Written down once it is written out. Stopped in between, the user
        # is told a second time rather than never.
        await self._model_capabilities.confirm_window_reminder(base_url, model, window)

    async def _on_model_capability(self, session: _Session, event: dict[str, Any]) -> None:
        """Remember what a session learned about its model: that it takes no pictures, or how small its window is."""
        window = event.get("context_window")
        if window is not None and self._model_capabilities is not None and session.model is not None:
            # The session manages its conversation for that window already;
            # sessions started for this model later are given it from the start.
            base_url, model = session.model
            try:
                # What its sessions are managed for when nothing was learned:
                # what the user set, else the preset.
                described = await self._model_capabilities.describe_window(base_url, model)
                was = int(described.get("set") or described["default"])
                if await self._model_capabilities.note_context_window(base_url, model, window):
                    logger.info("task agent model %s states a window of %s tokens; used from now on", model, window)
                await self._remind_of_window(session, base_url, model, was)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - learned again from the next refusal
                logger.warning("remembering the window of the task agent model failed", exc_info=True)
        if event.get("supports_images") is not False:
            return
        if self._gateway is not None:
            # For this session at once: its screenshots no longer reach the
            # model, so the tools that work from them are of no use to it.
            self._gateway.images_refused(session.commission_id)
        if event.get("lasting") is False or self._model_capabilities is None or session.model is None:
            # Said of this session's pictures only: nothing about the model.
            return
        base_url, model = session.model
        try:
            if await self._model_capabilities.note_images_refused(base_url, model, event.get("reason")):
                logger.info("task agent model %s takes no pictures; tools that need them are left out from now on", model)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - learned again from the next refusal
            logger.warning("remembering that the task agent model takes no pictures failed", exc_info=True)

    async def _on_ended(self, session: _Session, event: dict[str, Any]) -> None:
        from nekoclaw.runtime.protocol import PROCESS_EXITED_ERROR

        commission_id = session.commission_id
        reason = event.get("reason")
        if session.closing and reason == "error" and event.get("error") == PROCESS_EXITED_ERROR:
            # A close was asked for, and the runtime process went away
            # before answering it: the close will not come back. It ends
            # as that close would have, an answer it held included; the
            # commission's status is the close's business, not an error's.
            # Any other error while closing is followed by the close's own end.
            reason = "closed"
        inputs = event.get("inputs")
        # The end of a run that took fewer texts than were sent is followed
        # by another run, which may be under way already and using what this
        # one would let go of. Only an end that leaves the session idle, or
        # gone, ends what lasted for the run.
        if not (reason == "completed" and isinstance(inputs, int) and inputs < session.sent):
            self._run_over(commission_id)
        if reason == "closed":
            # An answer kept for a run that never came is not lost with the
            # session: it takes the commission up again from its transcript.
            kept, session.held_answer = session.held_answer, None
            # An answer already sent but not taken: the refusal that would
            # fail the commission now finds no session and is ignored, so
            # without this it would stay working for nobody.
            untaken, session.untaken_answer = session.untaken_answer, None
            claimed = (kept is not None or untaken is not None) and not self._closed
            if claimed:
                # Claimed for that answer before anything is awaited, so an
                # answer arriving before it is handed on is refused rather
                # than taking its place.
                self._answering_closed.add(commission_id)
            await self._release(commission_id)
            if not claimed:
                return
            if kept is not None:
                self._spawn(self._answer_closed(commission_id, kept, claimed=True))
            else:
                self._spawn(self._answer_untaken(commission_id, str(untaken)))
            return
        commission = await self._ledger.get(commission_id)
        if commission is None:
            return
        if reason == "completed":
            if isinstance(inputs, int) and inputs < session.sent:
                # Text sent after this run had finished (a result from a
                # task agent it started, or something the user said) started
                # another run. That run's end decides; this one's is kept in
                # case the text is refused and no such run comes.
                session.held_end = event
                return
            session.held_end = None
            if commission.status == "waiting_user" and session.awaiting_prompt:
                # It asked for an answer and stopped; the session stays for the answer.
                session.run_ended = True
                if session.held_answer is not None:
                    await self._deliver_answer(session, session.held_answer)
                return
            if commission.status == "working" and session.wind_up_left is not None:
                # It was told to hand over what it has, the user having
                # allowed no more to be used, and ended its run without a
                # closing report: its last words are what it handed over,
                # and the commission is stuck, not done. Whatever task
                # agents it started are not waited for.
                text = str(event.get("text") or "") or await self._usage_stopped_text(commission)
                await self._end(
                    commission_id, "blocked", reason=_USAGE_REASON, text=text, result_summary=self._summary(text)
                )
            elif commission.status == "working":
                # A blocked child has reported already and its session is
                # gone; it produces nothing more to wait for.
                if await self._ledger.select(parent_id=commission_id, statuses=_STILL_WORKING):
                    # Task agents it started are still at work. Their results
                    # arrive as messages and start its next run; it is not done.
                    return
                # The run ended without a closing report: its last words are the result.
                text = str(event.get("text") or "")
                await self._end(
                    commission_id, "done", reason="the run completed", text=text, result_summary=self._summary(text)
                )
        elif reason == "aborted":
            if not session.wound_up:
                # (One the host ended itself, for what it used, was put
                # down as stuck before its run was stopped: it stays so.)
                await self._end(commission_id, "cancelled", reason="the run was stopped", text="")
        elif reason == "error":
            if session.sent == 0:
                # Ended before its prompt was sent (the runtime process
                # went away meanwhile): what it was to send is kept, and
                # again once the failure is written, for what came meanwhile.
                self._keep_unsent(session)
                await self._fail(commission_id, str(event.get("error") or "the run failed"))
                self._keep_unsent(session)
            else:
                # A run takes its prompt into the session's transcript before
                # its first model request, and the next start goes on from
                # that transcript: what was kept is there, and is not sent
                # again; nor is what the commission is, if this run took that.
                session.carried = []
                if event.get("error") != PROCESS_EXITED_ERROR:
                    await self._note_opened(session)
                # (Said by the host for a runtime process that went away:
                # nothing tells how far that got with what it was sent. What
                # the commission is, is then said again when it is taken up;
                # twice in the transcript does less harm than not at all.)
                await self._fail(commission_id, str(event.get("error") or "the run failed"))
        # It is over; what it started must not go on working for nobody.
        for child in await self._ledger.select(parent_id=commission_id, statuses=ACTIVE_STATUSES):
            await self._ledger.transition(child.id, "cancelled", reason="its parent finished")
            await self.abort(child.id)
        await self._close_session(session)

    async def _note_usage(self, session_id: str, commission_id: str | None, event: dict[str, Any]) -> None:
        """Add what one model request used to its session and to the commission it was made for, if for one.

        As the runtime reports it, which is as the provider counted it:
        what was sent that no cache had, what was read from a cache and
        written to one, and what came back. A provider that does not say
        how much it read from a cache has it all counted as sent; nothing
        is estimated. A ledger that cannot be written does not stop the work.
        """

        def number(key: str) -> int:
            value = event.get(key)
            return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0

        try:
            await self._ledger.add_usage(
                session_id=session_id,
                commission_id=commission_id,
                tokens_in=number("input"),
                tokens_out=number("output"),
                cache_read=number("cache_read"),
                cache_write=number("cache_write"),
                # Nothing is said of the conversation's length by the
                # request that summed it up.
                context_tokens=number("context_tokens") or None,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - what was used is for showing and for the limit; the work goes on
            logger.warning("writing down what a model request used failed", exc_info=True)

    async def _on_usage(self, session: _Session, event: dict[str, Any]) -> None:
        """Count one model request; a commission whose quota is used up fails.

        What it used is added to the session and the commission. Against
        the daily quota it is counted as one request, unless it is the
        request that summed the conversation up, which is none of the
        run's own and was never counted there. The runtime reports usage
        once per request, after the request, so the request that uses the
        last unit still completes.
        """
        await self._note_usage(session.session_id, session.commission_id, event)
        if self._meter is None or session.closing or event.get("summary"):
            return
        if session.prepaid_requests > 0:
            # Counted before it was sent.
            session.prepaid_requests -= 1
            return
        commission_id = session.commission_id
        try:
            allowed = await self._meter(commission_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a meter that cannot count does not stop the work
            logger.warning("counting a task agent's model request failed", exc_info=True)
            return
        if allowed:
            return
        await self._fail(commission_id, _QUOTA_REASON)
        for child in await self._ledger.select(parent_id=commission_id, statuses=ACTIVE_STATUSES):
            await self._ledger.transition(child.id, "cancelled", reason="its parent finished")
            await self.abort(child.id)
        await self._close_session(session)

    async def _on_error(self, session: _Session, event: dict[str, Any]) -> None:
        command = event.get("command")
        message = str(event.get("message") or "the runtime rejected the command")
        commission_id = session.commission_id
        if command in ("prompt", "steer", "follow_up") and session.sent > 0:
            # Refused, so the session never took it.
            session.sent -= 1
        if command == "steer":
            held = session.held_end
            if held is not None and isinstance(held.get("inputs"), int) and held["inputs"] >= session.sent:
                # The run the refused text was to start never came: the
                # earlier run's end stands after all.
                session.held_end = None
                await self._on_ended(session, held)
            elif held is None and session.awaiting_prompt and not session.run_ended:
                # The steer that was to wake a session waiting for an answer
                # was refused, so no run started: it is idle again, and an
                # answer kept for the end of that run is handed over now.
                session.run_ended = True
                if session.held_answer is not None:
                    answer, session.held_answer = session.held_answer, None
                    await self._deliver_answer(session, answer)
            return
        if command == "start":
            # The runtime reports a start it could not make here, not by
            # raising from ``start``.
            if session.dsh_refused:
                # dsh's word on the session it did not open. The built-in
                # runtime has the commission by now (``_start``); dsh is
                # left alone for a while, and why is on the record.
                session.dsh_refused = False
                self._runtimes.dsh_refused(message)
                await self._ledger.append_event(
                    commission_id,
                    "runtime_fallback",
                    {"wanted": RUNTIME_DSH, "runtime": RUNTIME_BUILTIN, "reason": message},
                )
                return
            await self._fail(commission_id, f"the task agent could not be started: {message}")
            self._keep_unsent(session)
            await self._release(commission_id)
        elif command == "prompt":
            session.untaken_answer = None
            # Refused, so it is not in the transcript: what the user said
            # with it is kept, as when the start itself fails.
            await self._fail(commission_id, message)
            self._keep_unsent(session)
            await self._close_session(session)
        elif command == "close":
            # The session is gone already, for instance with its runtime process.
            await self._release(commission_id)

    async def _close_session(self, session: _Session) -> None:
        runtime = self._runtime_of(session)
        if session.closing or runtime is None:
            return
        session.closing = True
        await runtime.close(session.session_id)

    async def _fail(self, commission_id: str, reason: str) -> None:
        await self._end(commission_id, "failed", reason=reason, text=reason, result_summary=self._summary(reason))

    async def _end(
        self,
        commission_id: str,
        target: str,
        *,
        reason: str,
        text: str,
        suggested_delivery: Any = None,
        **values: Any,
    ) -> bool:
        """Move a commission to a final status and tell whoever commissioned it.

        A report to her is stored in the same transaction as the status
        change, so a crash in between cannot leave a finished commission
        that is never reported. A parent task agent is told after the change.
        Returns whether the change was made.
        """
        commission = await self._ledger.get(commission_id)
        if commission is None:
            return False
        report = None
        if commission.parent_id is None and target != "cancelled":
            # Whoever stopped it knows; the task brain reports a stop when one is due.
            # The commission's own setting decides; the task agent's wish is the fallback.
            delivery = commission.delivery or (
                suggested_delivery if suggested_delivery in ("proactive", "passive", "silent") else "proactive"
            )
            report = self._reporter.task_result_report(
                commission,
                channel=CHANNEL_TASK_AGENT,
                success=target == "done",
                summary=text,
                detail=text,
                error_message=text if target == "failed" else "",
                status=_STATUS_TO_REPORT.get(target, target),
                source_kind="task_agent",
                source_name=commission.title,
                delivery_mode=delivery,
            )
        moved = await self._reporter.transition(commission_id, target, reason=reason, report=report, **values)
        if not moved.applied or moved.commission is None:
            return False
        if moved.commission.parent_id is not None:
            await self._tell_parent(moved.commission, _STATUS_TO_REPORT.get(target, target), text)
        return True

    async def _tell_parent(self, commission: Commission, status: str, text: str) -> None:
        from config.nekoclaw_settings import TASK_AGENT_CHILD_RESULT_MAX_TOKENS
        from config.prompts import prompts_nekoclaw_brain as texts
        from utils.tokenize import truncate_to_tokens

        parent_id = str(commission.parent_id)
        message = texts.TASK_AGENT_CHILD_RESULT_TEMPLATE.format(
            name=commission.name or commission.id,
            status=status,
            text=truncate_to_tokens(text, TASK_AGENT_CHILD_RESULT_MAX_TOKENS),
        )
        parent = self._sessions.get(parent_id)
        if parent is None:
            await self._wake_closed_parent(parent_id, message)
            return
        if not parent.ready:
            # Its session is being started (taken up again by an earlier
            # result, say): this one follows its first work.
            self._notes.setdefault(parent_id, []).append(message)
            return
        runtime = self._runtime_of(parent)
        if runtime is None:
            return
        # Before the parent's next model request, so a parent that waits for
        # its task agents sees the result as soon as it looks; an idle parent is
        # started by it.
        await self._send_text(runtime, parent, "steer", message)

    async def _wake_closed_parent(self, parent_id: str, message: str) -> None:
        """Take up again a working parent whose idle session was closed, a child's result its next work.

        A parent that ended its run while its task agents worked is idle, and an
        idle runtime closes idle sessions after a while. The result then
        queues it to be started again where its transcript ends.
        """
        if self._closed:
            return
        if parent_id in self._queue or parent_id in self._requeueing or parent_id in self._taking_up:
            # Already on its way back: this result goes along with it.
            self._notes.setdefault(parent_id, []).append(message)
            return
        parent = await self._ledger.get(parent_id)
        if parent is None or parent.route != "task_agent" or parent.status != "working":
            return
        if not await self._know_session(parent_id):
            return
        session = self._sessions.get(parent_id)
        if session is not None and session.ready and self._runtime_of(session) is not None:
            # It came back, and was given its work, while the ledger was read.
            await self._send_text(self._runtime_of(session), session, "steer", message)
            return
        if session is not None or parent_id in self._queue or parent_id in self._requeueing:
            # It is on its way back: this goes along with its work.
            self._notes.setdefault(parent_id, []).append(message)
            return
        self._requeueing.add(parent_id)
        try:
            moved = await self._ledger.transition(
                parent_id, "queued", reason="its idle session was closed; a result takes it up again"
            )
        finally:
            self._requeueing.discard(parent_id)
        if not moved.applied:
            # Not queued after all (it was stopped meanwhile, say).
            self._settle_hold_of(parent_id)
            return
        if self._closed:
            await self._ledger.transition(parent_id, "failed", reason=_SHUT_DOWN_REASON)
            return
        self._resume[parent_id] = message
        if parent_id not in self._queue:
            self._queue.append(parent_id)
        if len(self._sessions) < self.limit:
            self._spawn(self._start_next())

    # -- driving ----------------------------------------------------------

    async def abort(self, commission_id: str) -> None:
        """Stop a commission's task agent, and the task agents it started.

        The caller has set the commission ``cancelled`` already.
        """
        for child in await self._ledger.select(parent_id=commission_id, statuses=ACTIVE_STATUSES):
            await self._ledger.transition(child.id, "cancelled", reason="its parent was stopped")
            await self.abort(child.id)
        if commission_id in self._queue:
            self._queue.remove(commission_id)
        self._notes.pop(commission_id, None)
        self._carried.pop(commission_id, None)
        self._resume.pop(commission_id, None)
        session = self._sessions.get(commission_id)
        if session is None:
            # Stopped before it had a place: what was kept of the main
            # dialogue for its opening message is of no use any more.
            self._excerpts.pop(commission_id, None)
            # It may have held its session without a place in a runtime (it
            # waited for an answer, or for a place to be taken up again at):
            # no runtime session ends, so the hold is looked at here.
            self._settle_hold_of(commission_id)
            return
        if session.question is not None and not session.question.done():
            session.question.cancel()
        # Closing stops the run, kills its background jobs and frees the place.
        await self._close_session(session)

    async def tell(self, commission_id: str, text: str, *, still_wanted: Callable[[], bool] | None = None) -> bool:
        """Pass a sentence on to a commission's task agent. Returns whether it was delivered.

        ``still_wanted``, when given, is asked before a commission that is
        blocked or failed is taken up again, after whatever that had to
        wait for and up to where it is given its work: False (everything
        was stopped meanwhile, or NekoClaw switched off) leaves it as it
        is. Where its status was written just then, behind the back of the
        stop, it is stopped instead.
        """
        if self._closed:
            return False
        commission = await self._ledger.get(commission_id)
        if commission is None or commission.route != "task_agent" or self._closed:
            return False
        session = self._sessions.get(commission_id)
        if session is not None and self._runtime_of(session) is not None and session.awaiting_prompt:
            return await self.answer(commission_id, text)
        if session is None and commission.status == "waiting_user":
            # Its session was closed while it waited (an idle runtime exits).
            return await self.answer(commission_id, text)
        if commission.status in ("blocked", "failed"):
            # Take it up again where its transcript ends.
            if still_wanted is not None and not still_wanted():
                return False
            await self._ledger.append_event(commission_id, "told", {"text": text, "resumed": True})
            # Should it open a session with nothing said in it (its start
            # failed before, or what was said was removed, being old), it is
            # handed what was said in the main dialogue just before this:
            # what was kept when it was first handed over went when it ended.
            self._keep_excerpt(commission)
            taken = await self._take_up_again(commission_id, text, still_wanted=still_wanted)
            if not taken:
                self._excerpts.pop(commission_id, None)
            return taken
        if commission.status == "queued" or (session is not None and not session.ready and not session.closing):
            called_off = self._called_off(commission_id)
            if called_off is not None:
                # What is queueing it, or setting it up, was called off and
                # ends the commission once it looks: this sentence, said
                # after that, is not added to what goes down with it.
                await asyncio.wait([called_off])
                return await self.tell(commission_id, text, still_wanted=still_wanted)
            # Not started yet, or its session not yet given its work: it goes
            # along with the work when that is given.
            self._notes.setdefault(commission_id, []).append(text)
            await self._ledger.append_event(commission_id, "told", {"text": text, "queued": True})
            return True
        if session is not None and self._runtime_of(session) is not None:
            if session.closing or commission.status not in ("working", "waiting_user"):
                return False
            await self._ledger.append_event(commission_id, "told", {"text": text})
            await self._send_text(self._runtime_of(session), session, "steer", text)
            return True
        return False

    async def _take_up_again(
        self, commission_id: str, text: str, *, still_wanted: Callable[[], bool] | None = None
    ) -> bool:
        """Start a commission's task agent again where its transcript ends, ``text`` its next work.

        Its old session is gone or on its way out. Without a free place it is
        queued, which a stop can still end. Returns whether it was started or
        queued.
        """
        if self._closed:
            return False
        held = self._held.get(commission_id)
        if held is not None:
            # Its review is being written down, or its old session files are
            # being removed, this moment. Queueing it changes its status as
            # starting it does, so both wait: what is written is about the
            # commission as it was, and is finished before it goes on.
            await held.wait()
            if self._closed:
                return False
        if still_wanted is not None and not still_wanted():
            # Everything was stopped while this waited. A stop does not end
            # a commission that is over; taken up now, it would start its
            # work after the stop.
            return False
        # Which session it goes on in is read from the ledger when this
        # process has not had the commission in hand before.
        if not await self._know_session(commission_id) or self._closed:
            return False
        if still_wanted is not None and not still_wanted():
            # Stopped while the ledger was read.
            return False
        if commission_id in self._taking_up or commission_id in self._requeueing:
            called_off = self._called_off(commission_id)
            if called_off is not None:
                # The one under way was called off (everything was stopped
                # after it began) and gives up once it gets to look. This
                # sentence came after that: it does not go down with the
                # other, said to have been passed on. It waits for the
                # other to be out of the way and takes the commission up
                # by itself.
                await asyncio.wait([called_off])
                return await self._take_up_again(commission_id, text, still_wanted=still_wanted)
            # An earlier sentence is taking it up again or queueing it right
            # now: this one goes along with that rather than starting or
            # queueing a second time, which would then be refused.
            self._notes.setdefault(commission_id, []).append(text)
            return True
        if commission_id not in self._sessions and self._take_place(commission_id):
            # As in ``submit``: a task of its own that shutting down waits for,
            # so a caller cancelled meanwhile does not leave a half set-up
            # session behind a commission that says it is working.
            self._taking_up.add(commission_id)
            self._new_work_in(self._session_of.get(commission_id), self._idle_stops)
            # Whether more may be used was refused for the round that is
            # over. It may still be on record here: this can come before
            # the hold on its session is looked at, which is where it is
            # forgotten. Kept, the new round would be told to hand over
            # at once instead of the user being asked.
            self._refused_more.discard(commission_id)
            launch = self._spawn(self._launch(commission_id, prompt=text, still_wanted=still_wanted))
            self._note_under_way(commission_id, still_wanted, launch)

            def taken_up(_done: Any) -> None:
                self._taking_up.discard(commission_id)
                # A start that failed left the commission over while it was
                # still on its way here: its session is let go now.
                self._settle_hold_of(commission_id)

            launch.add_done_callback(taken_up)
            return await asyncio.shield(launch)
        # Its old session is still on its way out, or no place is free: it is
        # queued. That runs as a task shutting down waits for, so a shutdown
        # while the status is written still finds the commission afterwards.
        # Marked at once, before anything is awaited, so a sentence arriving
        # meanwhile joins this one.
        self._requeueing.add(commission_id)
        queueing = self._spawn(self._requeue(commission_id, text, still_wanted=still_wanted))
        self._note_under_way(commission_id, still_wanted, queueing)
        return await asyncio.shield(queueing)

    def _note_under_way(
        self, commission_id: str, still_wanted: Callable[[], bool] | None, task: asyncio.Future[Any]
    ) -> None:
        """Keep, for as long as it runs, the task that takes a commission up again for a sentence that may be called off."""
        if still_wanted is None:
            return
        entry = (still_wanted, task)
        self._under_way[commission_id] = entry

        def over(_done: Any) -> None:
            if self._under_way.get(commission_id) is entry:
                del self._under_way[commission_id]

        # Before whoever waits for the task goes on: callbacks run in the
        # order they were added.
        task.add_done_callback(over)

    def _called_off(self, commission_id: str) -> asyncio.Future[Any] | None:
        """Return the task that is taking a commission up again when what it does that for is no longer wanted.

        Everything was stopped after it began, say: it gives the commission
        up, or ends it, once it gets to look. What is said of the
        commission until then was said after the stop, and is not the
        stop's to end.
        """
        under_way = self._under_way.get(commission_id)
        if under_way is None or under_way[0]():
            return None
        return under_way[1]

    async def _requeue(
        self, commission_id: str, text: str, *, still_wanted: Callable[[], bool] | None = None
    ) -> bool:
        # Marked (by the caller) while the status is written: the old
        # session's end meanwhile then keeps the candidate plugins and notes
        # for the retry.
        self._requeueing.add(commission_id)
        try:
            moved = await self._ledger.transition(commission_id, "queued", reason="taken up again; waiting for a place")
        finally:
            self._requeueing.discard(commission_id)
        stopped = False
        if moved.applied and self._closed:
            # Shutting down emptied the queue while the status was written.
            await self._ledger.transition(commission_id, "failed", reason=_SHUT_DOWN_REASON)
        elif moved.applied and still_wanted is not None and not still_wanted():
            # Everything was stopped while the status was written: the stop
            # looked at a commission that was over, and from here on it
            # waits for a place with nothing to end it. It is stopped here,
            # as it would have been there. (Queued, a later stop finds it:
            # nothing more is asked when it gets its place.)
            await self._ledger.transition(commission_id, "cancelled", reason=_STOPPED_MEANWHILE_REASON)
            stopped = True
            # Over for good, as after any stop: nothing said is kept for it.
            self._carried.pop(commission_id, None)
            self._notes.pop(commission_id, None)
        if not moved.applied or self._closed or stopped:
            if commission_id not in self._sessions:
                self._candidates.pop(commission_id, None)
                self._notes.pop(commission_id, None)
                # Not queued after all (it was stopped meanwhile, say).
                self._settle_hold_of(commission_id)
            return False
        self._resume[commission_id] = text
        if commission_id not in self._queue:
            self._queue.append(commission_id)
        # A place may have come free while the status was written; the queue
        # was empty then, so nothing took it. Move it on now.
        if len(self._sessions) < self.limit:
            self._spawn(self._start_next())
        return True

    async def answer(self, commission_id: str, text: str, choice: str | None = None) -> bool:
        """Give the user's answer to a commission that waits for it."""
        session = self._sessions.get(commission_id)
        if session is None:
            return await self._answer_closed(commission_id, text)
        if session.question is not None and not session.question.done():
            session.question.set_result({"text": text, "choice": choice})
            return True
        if session.awaiting_prompt and self._runtime_of(session) is not None:
            if not session.run_ended:
                # The run that asked is still winding down. The answer is kept
                # and handed over the moment it has ended. A question takes
                # one answer: a later one does not replace the kept one.
                if session.held_answer is not None or session.untaken_answer is not None:
                    return False
                session.held_answer = text
                return True
            return await self._deliver_answer(session, text)
        return False

    async def _answer_closed(self, commission_id: str, text: str, *, claimed: bool = False) -> bool:
        """Answer a ``waiting`` report whose session was closed meanwhile.

        A runtime with nothing to do exits after a while and closes its idle
        sessions, those waiting for an answer included. The answer then takes
        the commission up again where its transcript ends. ``claimed`` is
        True for an answer its session kept, claimed for it when the session
        closed.
        """
        if not claimed:
            if self._closed or commission_id in self._answering_closed:
                # Another answer is taking it up again: that one is its next work.
                return False
            self._answering_closed.add(commission_id)
        try:
            if self._closed:
                return False
            commission = await self._ledger.get(commission_id)
            if commission is None or commission.route != "task_agent" or commission.status != "waiting_user":
                return False
            question = commission.question() or {}
            # A question asked through a tool call is answered to the session
            # that waits for it; only the question of a report can be answered
            # this way.
            if question.get("kind") != "report":
                return False
            await self._ledger.append_event(commission_id, "answered", {"text": text, "resumed": True})
            return await self._take_up_again(commission_id, text)
        finally:
            self._answering_closed.discard(commission_id)

    async def _deliver_answer(self, session: _Session, text: str) -> bool:
        """Give an idle session the answer to its ``waiting`` report as its next work."""
        commission_id = session.commission_id
        if session.untaken_answer is not None:
            # Another answer is on its way to it: that one is its next work.
            return False
        # Marked before anything is awaited: a session that closes meanwhile
        # leaves the answer to its end, which takes the commission up again.
        session.untaken_answer = text
        moved = await self._ledger.transition(commission_id, "working", reason="the user answered")
        if not moved.applied:
            if session.untaken_answer == text:
                session.untaken_answer = None
            return False
        session.awaiting_prompt = False
        session.held_answer = None
        await self._ledger.append_event(commission_id, "answered", {"text": text})
        if self._sessions.get(commission_id) is not session or session.untaken_answer != text:
            # Closed while the status was written: its end has the answer.
            return True
        try:
            await self._send_text(self._runtime_of(session), session, "prompt", text)
        except BaseException:
            session.untaken_answer = None
            raise
        return True

    async def _answer_untaken(self, commission_id: str, text: str) -> None:
        """Take a commission up again with an answer its session closed before taking.

        The answer moved it to ``working``; it goes back to waiting for that
        answer, which then takes it up again as an answer to a closed session does.
        The commission was claimed for this answer when its session closed.
        """
        try:
            moved = await self._ledger.transition(
                commission_id, "waiting_user", reason="its session closed before it took the answer"
            )
            if moved.applied and not await self._take_up_again(commission_id, text) and self._closed:
                # Shutting down began while the status was written: nothing will
                # take the answer up, and neither the queue nor the sessions hold
                # it for shutting down to end. It ends here, as they do.
                await self._ledger.transition(commission_id, "failed", reason=_SHUT_DOWN_REASON)
        finally:
            self._answering_closed.discard(commission_id)

    async def _ask(self, commission_id: str, question: dict[str, Any]) -> dict[str, Any]:
        """Put a question to the user and wait for the answer.

        The commission is ``waiting_user`` meanwhile and ``working`` again
        afterwards.
        """
        session = self._sessions.get(commission_id)
        if session is None:
            raise RuntimeError("the commission has no session to ask for")
        async with session.ask_lock:
            future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
            moved = await self._ledger.transition(
                commission_id,
                "waiting_user",
                reason="waiting for the user",
                pending_question=json.dumps(question, ensure_ascii=False),
            )
            if not moved.applied or moved.commission is None:
                raise RuntimeError(f"the commission cannot wait for the user ({moved.refusal})")
            session.question = future
            try:
                await self._reporter.question(moved.commission, question)
                return await future
            finally:
                session.question = None
                # Back to working only from waiting: a commission stopped, or
                # failed (by shutting down, say) meanwhile, stays as it is.
                await asyncio.shield(
                    self._ledger.transition(
                        commission_id, "working", reason="the user answered", only_from=("waiting_user",)
                    )
                )

    async def ask_user(self, commission_id: str, question: dict[str, Any]) -> dict[str, Any]:
        """Put a question to the user for a commission and wait; returns ``{"text", "choice"}``.

        For the gateway: a plugin that wants the user's confirmation is not
        answered by the task agent that called it.
        """
        return await self._ask(commission_id, question)

    async def ask_permission(self, commission_id: str, ask: PermissionAsk) -> str:
        """Ask the user to approve something; the ``Asker`` of the permission gate."""
        answer = await self._ask(commission_id, ask.to_json())
        choice = answer.get("choice")
        # Anything that is not one of the four answers is a refusal. An
        # approval wider than the question offered is narrowed by the gate.
        return choice if choice in CHOICES else "deny"

    async def _parent_of(self, commission_id: str) -> str | None:
        """Return the commission that started this one; the permission gate's lookup."""
        commission = await self._ledger.get(commission_id)
        return commission.parent_id if commission is not None else None

    # -- host callbacks ---------------------------------------------------

    def _at_work_in(self, session_id: str) -> str:
        """Return the commission a session is at work on.

        A runtime names the session a callback comes from; permissions,
        questions, reports and everything else the host keeps are a
        commission's. A session no commission holds here is taken by its
        own id, which is that of the commission it was made for, as every
        session's was: what such a callback asks is decided as it always
        was (by the settings alone, there being no session to ask through).
        """
        return self._held_by.get(session_id, session_id)

    async def _cb_permission(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = self._at_work_in(session_id)
        detail = {key: payload.get(key) for key in ("tool", "path", "command", "cwd") if payload.get(key)}
        from nekoclaw.runtime.permissions import GATEWAY_CALL_CATEGORY, HOST_TOOL_CALL_CATEGORY, WRITE_TOOL_NAMES

        category = str(payload.get("category") or "other")
        if category == GATEWAY_CALL_CATEGORY:
            return await self._rule_on_gateway_call(session_id, payload)
        if category == HOST_TOOL_CALL_CATEGORY:
            return await self._rule_on_host_tool(session_id, payload)
        path = payload.get("path")
        writes = payload.get("tool") in WRITE_TOOL_NAMES
        # A path outside the working directory, or one a tool is about to
        # write: the space may lie inside a working directory that is one of
        # its parents, and writing it is then a write "inside the working
        # directory" as far as the category goes.
        if (
            self._space is not None
            and (category in ("outside_read", "outside_write") or (writes and category == "workdir_files"))
            and isinstance(path, str)
            and path
            and await self._space.contains(path)
        ):
            if writes or category == "outside_write":
                # Only the host writes the space, whatever the settings allow
                # and wherever the working directory is.
                from config.prompts.prompts_nekoclaw_space import SPACE_WRITE_REFUSED

                return {"allow": False, "reason": SPACE_WRITE_REFUSED}
            # What was kept for task agents is read like the files a task
            # agent works on: nobody is asked.
            category = "workdir_files"
        if self._space is not None and category == "run_command":
            command, cwd = payload.get("command"), payload.get("cwd")
            if (isinstance(command, str) and await self._space.named_in(command)) or (
                isinstance(cwd, str) and cwd and await self._space.contains(cwd)
            ):
                # What a shell command does is not looked into. One that
                # points at the directory of the space, or runs inside it,
                # may change what task agents keep for good, past the host.
                # The user is asked about every such command, and told that
                # it is one: an approval of commands given before, for good
                # or for the session, does not cover it, and nothing is kept
                # of the answer. Commands the settings forbid stay forbidden.
                # A command that reaches the space another way (a relative
                # path, a variable) is not seen here; there is no sandbox.
                if await self._permissions.forbidden("run_command"):
                    return PermissionDecision(False, DENIED_BY_SETTINGS).to_json()
                decision = await self._permissions.ask_each_time(session_id, SPACE_COMMAND, detail=detail)
                if decision.allow and await self._permissions.forbidden("run_command"):
                    # Forbidden while the question was open.
                    return PermissionDecision(False, DENIED_BY_SETTINGS).to_json()
                return decision.to_json()
        decision = await self._permissions.check(session_id, category, detail=detail)
        return decision.to_json()

    async def _rule_on_gateway_call(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Rule on a call of a gateway tool that a task agent in dsh is about to make.

        Only the dsh runtime asks this (``nekoclaw.runtime.host``). dsh gives
        a call of a gateway tool up after a minute, so whatever the call
        needs the user for is asked now, before it is made
        (``Gateway.clear_call``). The call is let through whatever came of
        it: the gateway carries it out or answers it with the refusal, in the
        words every call refused at its entry is answered with, and neither
        decides nor asks again.
        """
        arguments = payload.get("arguments")
        if self._gateway is not None:
            await self._gateway.clear_call(
                session_id, str(payload.get("tool") or ""), arguments if isinstance(arguments, dict) else {}
            )
        return PermissionDecision(True).to_json()

    async def _rule_on_host_tool(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Rule on a call of one of the host's own tools that a task agent in dsh is about to make.

        Only the dsh runtime asks this, and only about the web search: with
        plugin entries set to be asked about, the search asks the user, and
        dsh gives a call less than a minute. So the user is asked now. The
        call is let through whatever came of it: the search answers it with
        what was decided here, in the words it refuses a call with in the
        built-in runtime, and does not ask again.
        """
        from config.nekoclaw_settings import GATEWAY_CLEARED_CALL_SECONDS

        if payload.get("tool") == "web_search":
            commission = await self._ledger.get(session_id)
            if commission is not None and session_id in self._sessions:
                arguments = payload.get("arguments")
                queries = _search_queries(arguments if isinstance(arguments, dict) else {})
                refusal = await self._search_refusal(commission)
                if session_id in self._sessions:
                    # Waits for its call as long as a ruling on a gateway tool does.
                    until = asyncio.get_running_loop().time() + GATEWAY_CLEARED_CALL_SECONDS
                    self._search_rulings[session_id] = (queries, refusal, until)
        return PermissionDecision(True).to_json()

    async def _search_refusal(self, commission: Commission) -> str | None:
        """Return why a commission's task agent may not search the web, or None; the user is asked where the settings say so.

        The search is a plugin entry: the plugin switch and the
        ``plugin_entry`` permission apply as they do through the gateway.
        """
        if not self._registry.plugins_enabled():
            return "web search is not available: plugins are switched off"
        decision = await self._permissions.check(
            commission.id,
            "plugin_entry",
            detail={"tool": "web_search", "capability": f"plugin:{WEB_SEARCH_PLUGIN_ID}:{WEB_SEARCH_ENTRY_ID}"},
        )
        return None if decision.allow else (decision.reason or "permission denied")

    async def _search_forbidden_now(self) -> str | None:
        """Return why the settings forbid the web search as of now, or None; nobody is asked."""
        if not self._registry.plugins_enabled():
            return "web search is not available: plugins are switched off"
        if await self._permissions.forbidden("plugin_entry"):
            return DENIED_BY_SETTINGS
        return None

    async def _cb_ask_user(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = self._at_work_in(session_id)
        questions = list(payload.get("questions") or [])
        answer = await self._ask(session_id, {"kind": "questions", "questions": questions})
        return build_answers(questions, str(answer.get("text") or ""))

    async def _cb_report(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        # The report arrives as an event as well; that is where it is acted on.
        del session_id, payload
        return None

    async def _cb_note_lesson(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        commission = await self._ledger.get(self._at_work_in(session_id))
        if commission is None:
            return None
        if payload.get("category") == "feedback":
            # A correction or approval from the user is a task preference. It is
            # an inference, so it takes effect once the same thing was seen twice.
            await self._preferences.observe(
                feedback_text(payload), lanlan_name=commission.lanlan_name, source_commission=commission.id
            )
            await self._ledger.append_event(commission.id, "feedback", dict(payload))
            return {"message": "filed as a task preference"}
        message = await self._lessons.submit(commission, payload)
        return {"message": message} if message else None

    async def _cb_web_search(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        commission = await self._ledger.get(self._at_work_in(session_id))
        if commission is None:
            raise RuntimeError("unknown commission")
        queries = list(_search_queries(payload))
        capability = f"plugin:{WEB_SEARCH_PLUGIN_ID}:{WEB_SEARCH_ENTRY_ID}"
        ruled = self._search_rulings.pop(commission.id, None)
        if ruled is not None and ruled[0] == tuple(queries) and asyncio.get_running_loop().time() <= ruled[2]:
            # Ruled on before the call was made (a task agent in dsh): the
            # user was asked then, if they had to be. Only for what they
            # were asked about: an allowance for once is not another search's.
            refusal = ruled[1]
            if refusal is None:
                # Settings changed since win, as they do over an answer
                # (plugins switched off, the entry denied). Nobody is asked again.
                refusal = await self._search_forbidden_now()
        else:
            refusal = await self._search_refusal(commission)
        if refusal is not None:
            await self._ledger.append_event(
                commission.id, "capability_denied", {"capability": capability, "reason": refusal}
            )
            raise RuntimeError(refusal)
        gate = asyncio.Semaphore(_WEB_SEARCH_PARALLEL)

        async def one(query: str) -> dict[str, Any]:
            async with gate:
                outcome = await self._runner.run_entry(
                    commission_id=commission.id,
                    plugin_id=WEB_SEARCH_PLUGIN_ID,
                    entry_id=WEB_SEARCH_ENTRY_ID,
                    args={"query": query},
                    lanlan_name=commission.lanlan_name,
                    conversation_id=commission.conversation_id,
                    latest_user_request=commission.user_text,
                )
            if not outcome.success:
                raise RuntimeError(f"web search failed: {outcome.error or 'the web_search plugin is not available'}")
            data = outcome.data if isinstance(outcome.data, dict) else {}
            sources = []
            for item in data.get("results") or []:
                if isinstance(item, dict) and item.get("url"):
                    source = {"url": str(item["url"])}
                    if item.get("title"):
                        source["title"] = str(item["title"])
                    if item.get("snippet"):
                        source["snippet"] = str(item["snippet"])
                    sources.append(source)
            result: dict[str, Any] = {"query": query, "sources": sources}
            if isinstance(data.get("summary"), str) and data["summary"].strip():
                result["answer"] = data["summary"]
            return result

        return {"results": list(await asyncio.gather(*(one(query) for query in queries)))}

    # -- child task agents ----------------------------------------------------

    async def _team(self, session_id: str) -> tuple[Commission | None, list[Commission]]:
        """Return the lead of the caller's team and the task agents the lead started."""
        commission = await self._ledger.get(session_id)
        if commission is None:
            return None, []
        lead = commission
        if commission.parent_id is not None:
            lead = await self._ledger.get(commission.parent_id) or commission
        members = await self._ledger.select(parent_id=lead.id, oldest_first=True)
        return lead, members

    async def _member(self, session_id: str, name: str) -> Commission | None:
        lead, members = await self._team(session_id)
        if lead is None:
            return None
        if name == LEAD_NAME:
            return lead
        return next((member for member in members if member.name == name), None)

    async def _cb_subagent_start(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = self._at_work_in(session_id)
        parent = await self._ledger.get(session_id)
        if parent is None:
            raise RuntimeError("unknown commission")
        if parent.parent_id is not None:
            raise RuntimeError("a child task agent cannot start further task agents")
        _lead, members = await self._team(session_id)
        taken = {member.name for member in members}
        name = payload.get("name")
        if not isinstance(name, str) or not name or name in taken or name == LEAD_NAME:
            number = len(members) + 1
            while f"agent-{number}" in taken:
                number += 1
            name = f"agent-{number}"
        workdir = await self._child_workdir(parent, payload.get("cwd"))
        child, _created = await self._ledger.create(
            lanlan_name=parent.lanlan_name,
            title=str(payload.get("description") or name),
            dispatch_text=str(payload.get("prompt") or ""),
            user_text=parent.user_text,
            source="task_agent",
            route="task_agent",
            parent_id=parent.id,
            name=name,
            project=parent.project,
            workdir=workdir,
            conversation_id=parent.conversation_id,
            lang=parent.lang,
        )
        # A stop of the parent sets its status before it looks for children,
        # so a child filed after that look is caught here, and one filed
        # before it is stopped by the stop itself.
        current = await self._ledger.get(parent.id)
        if current is None or current.status not in ("working", "waiting_user"):
            await self._ledger.transition(child.id, "cancelled", reason="its parent was stopped")
            raise RuntimeError("the commission was stopped; no task agent is started")
        await self._ledger.append_event(parent.id, "subagent_started", {"name": name, "commission_id": child.id})
        # It starts now or not at all (see ``_NO_PLACE_REASON``); the parent
        # is told so and can do that part itself. Its result comes back to the
        # parent as a message.
        if not await self.submit(child, candidate_plugin_ids=self._candidates.get(parent.id, ()), wait=False):
            raise RuntimeError(f"{_NO_PLACE_REASON}; do this part yourself")
        return {"name": name}

    def session_dir_of(self, session: Any) -> Any:
        """Return where a session is kept (``session`` is a ``ledger.Session``). Blocking; see ``_session_dir_of``."""
        return self._session_dir_of(session)

    def hold_files(self, session_id: str) -> bool:
        """Claim a session's files for removal. False when the session is in use.

        In use means a commission holds it, or one that is worked on in it
        has a place, waits for one, or is being taken up again. Looking and
        claiming happen with no await in between. Until ``release_files``,
        a commission that is to be started in the session waits instead of
        opening files that are being removed under it.
        """
        if self._in_use(session_id):
            return False
        self._clearing[session_id] = asyncio.Event()
        # Not summed up later either: there is nothing left to sum up.
        self._drop_idle_timer(session_id)
        return True

    def _in_use(self, session_id: str) -> bool:
        """Whether something is at work in a session or on its way into it, or the host has claimed it for itself.

        A commission holds it; its files are being removed; it is being
        summed up; a copy of it is being made (looked at again later);
        or a commission that is worked on in it has a place, waits for
        one, or is being taken up again.
        """
        if session_id in self._held_by or session_id in self._clearing or session_id in self._compacting:
            return True
        if session_id in self._copying_from:
            return True
        in_hand = (*self._sessions, *self._queue, *self._taking_up, *self._requeueing)
        return any(self._session_of.get(commission_id) == session_id for commission_id in in_hand)

    def release_files(self, session_id: str) -> None:
        """Give back what ``hold_files`` claimed; a start that waited goes on."""
        clearing = self._clearing.pop(session_id, None)
        if clearing is not None:
            clearing.set()

    def hold_session(self, commission_id: str) -> bool:
        """Claim a commission for writing its review down. False when the commission is in use.

        In use means it holds a session, waits for a place, or is being
        taken up again. Looking and claiming happen with no await in
        between. Until ``release_session``, the commission is not taken up
        again: what is written is about the commission as it was.
        """
        if (
            commission_id in self._sessions
            or commission_id in self._queue
            or commission_id in self._taking_up
            or commission_id in self._requeueing
            or commission_id in self._held
        ):
            return False
        self._held[commission_id] = asyncio.Event()
        return True

    def release_session(self, commission_id: str) -> None:
        """Give back what ``hold_session`` claimed; a start that waited goes on."""
        held = self._held.pop(commission_id, None)
        if held is not None:
            held.set()

    def _session_dir_of(self, commission: Any) -> Any:
        """Return where a session is kept. Blocking: it looks at files.

        ``commission`` is whatever has the session's ``id``, ``project``,
        ``session_dir`` and ``workdir`` on record: a ``ledger.Session``,
        with the working directory its first commission brings while it
        has none itself. (Named for what it was when a commission and its
        session were one; what follows says commission in that sense.)

        Where it was when the commission was first taken up, when that is on
        record: the data root may have been moved since through the storage
        settings, and the sessions do not move with it. Worked out from the
        root of now, a commission taken up again after such a move would
        find an empty directory and start from nothing instead of going on.

        Unless nothing was ever said there, and something was below the root
        of now: the user moved the whole data folder by hand and pointed the
        application at it. The sessions came along then; the place on record
        is where they used to be, and would be made anew, empty.
        """
        current = self._paths.session_dir(commission.id, commission.project)
        recorded = self._recorded_session_dir(commission)
        if recorded is None:
            return current
        if not _has_transcript(recorded) and _has_transcript(current):
            return current
        if not recorded.exists() and _has_files(current):
            # Moved by hand as above, and what was said there has been
            # removed since, being old (``SpaceKeeper``): the session is
            # still where its working directory is, not at the place on
            # record, which is gone.
            return current
        return recorded

    @staticmethod
    def _recorded_session_dir(commission: Commission) -> Any:
        """Return where the ledger has a commission's session, or None when it does not say."""
        from pathlib import Path

        if commission.session_dir:
            return Path(commission.session_dir)
        if commission.workdir:
            # Taken up by a build from before the place was recorded: its
            # working directory was, and is by default the ``work`` directory
            # of its session.
            workdir = Path(commission.workdir)
            if workdir.name == "work" and workdir.parent.name == commission.id:
                return workdir.parent
        return None

    def _workdir_of(self, commission: Commission, session_dir: Any) -> Any:
        """Return a commission's working directory, its session being kept in ``session_dir``.

        The one on record. When the sessions are no longer where the record
        has them (``_session_dir_of``: the data folder was moved by hand), a
        working directory that was among them went along with them: the
        ``work`` directory of its own session, or a directory inside that of
        the commission it was started by. It is that directory where the
        sessions are now; at the place on record there would be an empty one,
        made anew, and none of what the task agent had written. A directory
        elsewhere (a project of the user's) is where it was.
        """
        from pathlib import Path

        if not commission.workdir:
            return self._paths.work_dir(commission.id, commission.project)
        workdir = Path(commission.workdir)
        recorded = self._recorded_session_dir(commission)
        if recorded is None:
            return workdir
        try:
            among = workdir.relative_to(recorded.parent)
        except ValueError:
            return workdir
        # (The directory it was, when the session is where the record has it.)
        return session_dir.parent / among

    async def _child_workdir(self, parent: Commission, cwd: Any) -> str | None:
        """Return the working directory of a task agent the parent starts.

        Inside its working directory a task agent writes without being asked, so
        a child may only work inside its parent's. A directory elsewhere falls
        back to the parent's own: the child then has to ask before writing
        there, exactly as the parent has to.
        """
        from pathlib import Path

        from nekoclaw.runtime.permissions import is_inside

        if not isinstance(cwd, str) or not cwd.strip():
            return parent.workdir
        root = Path(parent.workdir) if parent.workdir else self._paths.work_dir(parent.id, parent.project)
        wanted = root / cwd
        if await asyncio.to_thread(is_inside, wanted, root):
            return str(wanted)
        logger.info("child task agent of %s asked for a folder outside its parent's; using the parent's", parent.id)
        return str(root)

    async def _cb_subagent_list(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        del payload
        lead, members = await self._team(self._at_work_in(session_id))
        if lead is None:
            return {"agents": []}
        agents = [{"name": LEAD_NAME, "status": lead.status, "description": lead.title}]
        agents.extend(
            {"name": member.name or member.id, "status": member.status, "description": member.title}
            for member in members
        )
        return {"agents": agents}

    async def _cb_subagent_send(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        target = await self._member(self._at_work_in(session_id), str(payload.get("agent") or ""))
        if target is None:
            raise RuntimeError(f"no agent named {payload.get('agent')!r}")
        message = str(payload.get("message") or "")
        if not await self.tell(target.id, message):
            raise RuntimeError(f"agent {payload.get('agent')!r} is not running ({target.status})")
        return None

    async def _cb_subagent_interrupt(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        session_id = self._at_work_in(session_id)
        target = await self._member(session_id, str(payload.get("agent") or ""))
        if target is None or target.id == session_id:
            raise RuntimeError(f"no other agent named {payload.get('agent')!r}")
        await self._ledger.transition(target.id, "cancelled", reason="interrupted by another task agent")
        await self.abort(target.id)
        return None

    # -- what a commission may use ----------------------------------------

    def _usage_settled(self, commission: Commission) -> None:
        """Forget what was noted of the usage of a commission that is over: whether more was refused for it."""
        if commission.parent_id is None:
            self._refused_more.discard(commission.id)
            self._usage_asks.pop(commission.id, None)

    async def _usage_stopped_text(self, commission: Commission) -> str:
        """Return what she is told of a commission that was put down for what it used, in its language."""
        from config.prompts.prompts_nekoclaw_brain import USAGE_LIMIT_STOPPED_TEXT
        from nekoclaw.host.reports import tokens_text
        from nekoclaw.host.task_brain import _texts

        standing = await self._ledger.usage_standing(commission.id)
        used = standing.used if standing is not None else commission.tokens
        return _texts(USAGE_LIMIT_STOPPED_TEXT, commission.lang or "en").format(used=tokens_text(used))

    async def _cb_budget(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Say, before a model request of a session, whether the work it belongs to may use more.

        The built-in runtime asks this before every request of a run,
        between two steps (``runtime/host.py``): nothing of the run is
        under way then, so nothing is cut off. What is looked at is what
        the commission no task agent started has used, the task agents it
        started counted in (``Ledger.usage_standing``), against what it
        may: ``TASK_AGENT_USAGE_LIMIT_TOKENS``, or what the user allowed
        since. Looked at once everything the session said before it asked
        is handled (``_caught_up``): what its last request used is written
        down by then, and a report that ended the commission has ended it.
        Only a commission that is at work is asked about.

        With room left, the answer is at once and empty. With none, the
        user is asked, as for a permission (the commission waits for the
        user meanwhile, and holds its session): how much was used, and
        that as much again is asked for. Allowed, that much more may be
        used from there. Refused, or with nobody to ask, the task agent
        is told to hand over what it has (``notice``), and has a few
        requests left for that; when those are used and it has not
        reported, the commission is put down as stuck and the run ends
        (``stop``). Every task agent of the same commission is told the
        same, and none of them asks the user a second time.
        """
        from config.nekoclaw_settings import TASK_AGENT_USAGE_LIMIT_TOKENS, TASK_AGENT_USAGE_WIND_UP_REQUESTS
        from config.prompts.prompts_nekoclaw_brain import TASK_AGENT_USAGE_WIND_UP
        from nekoclaw.host.reports import tokens_text

        del payload
        limit = int(TASK_AGENT_USAGE_LIMIT_TOKENS or 0)
        commission_id = self._held_by.get(session_id)
        session = self._sessions.get(commission_id or "")
        if limit <= 0 or commission_id is None or session is None or session.closing:
            return None
        # Always, however far from the line the account stands: what is
        # still on its way from the runtimes has no bound to go by (a
        # window can be of any size, and a commission has several task
        # agents), so nothing is concluded from what is written down so
        # far alone.
        await self._caught_up()
        if self._sessions.get(commission_id) is not session or session.closing:
            # Over meanwhile: its run ends by itself.
            return None
        if session.wind_up_left is not None:
            return await self._wind_up_step(session)

        async def standing_over() -> Any:
            # What stands against it, when nothing more may be used; else None.
            commission = await self._ledger.get(commission_id)
            if commission is None or commission.status != "working":
                # It has reported how it ended, or waits for the user as it is.
                return None
            standing = await self._ledger.usage_standing(commission_id)
            if standing is None:
                return None
            allowed = standing.allowed if standing.allowed is not None else limit
            return standing if standing.used >= allowed else None

        standing = await standing_over()
        if standing is None:
            return None
        if standing.root not in self._refused_more:
            async with self._usage_asks.setdefault(standing.root, asyncio.Lock()):
                # Looked at again: another task agent of the commission
                # may have asked meanwhile, and been answered.
                standing = await standing_over()
                if standing is None:
                    return None
                if standing.root not in self._refused_more:
                    decision = await self._permissions.ask_each_time(
                        commission_id,
                        USAGE_LIMIT,
                        detail={"used": tokens_text(standing.used), "more": tokens_text(limit)},
                    )
                    if decision.allow:
                        await self._ledger.allow_usage(commission_id, limit)
                        return None
                    self._refused_more.add(standing.root)
        if self._sessions.get(commission_id) is not session or session.closing:
            # Stopped while the user was asked.
            return {"stop": True}
        # The request this is asked before is the first of those it has left.
        session.wind_up_left = max(0, TASK_AGENT_USAGE_WIND_UP_REQUESTS - 1)
        await self._ledger.append_event(
            commission_id, "usage_refused", {"used": standing.used, "requests_left": TASK_AGENT_USAGE_WIND_UP_REQUESTS}
        )
        return {"notice": TASK_AGENT_USAGE_WIND_UP.format(requests=TASK_AGENT_USAGE_WIND_UP_REQUESTS)}

    async def _wind_up_step(self, session: _Session) -> dict[str, Any] | None:
        """Let a task agent that is handing over what it has make one more request, or end its commission.

        It was told how many requests it has for that. When they are used
        and it has reported nothing that ended the commission, the
        commission is put down as stuck with the reason, what it started
        is stopped, and its session is closed.
        """
        if session.wind_up_left:
            session.wind_up_left -= 1
            return None
        commission_id = session.commission_id
        commission = await self._ledger.get(commission_id)
        if commission is not None and commission.status in ("working", "waiting_user"):
            text = await self._usage_stopped_text(commission)
            # Before its run is stopped: what the run says as it stops
            # must not turn it into a stopped one.
            session.wound_up = True
            await self._end(commission_id, "blocked", reason=_USAGE_REASON, text=text, result_summary=self._summary(text))
            for child in await self._ledger.select(parent_id=commission_id, statuses=ACTIVE_STATUSES):
                await self._ledger.transition(child.id, "cancelled", reason="its parent finished")
                await self.abort(child.id)
        await self._close_session(session)
        return {"stop": True}

    async def usage_of(self, commission_id: str) -> dict[str, Any] | None:
        """Return what a commission has used and what it may, for a display; None for an unknown one.

        ``commission`` is the commission's own count (the task agents it
        started included); ``limit`` what the work it belongs to may use
        before the user is asked, and ``used`` what that work has used
        (the same numbers, for a commission no task agent started);
        ``session`` what its session used over all its commissions, with
        the length of its conversation as of its last request. What the
        model's window is and where a conversation is summed up is told
        by ``ModelCapabilityStore.describe_window``.
        """
        from config.nekoclaw_settings import TASK_AGENT_USAGE_LIMIT_TOKENS

        commission = await self._ledger.get(commission_id)
        if commission is None:
            return None
        standing = await self._ledger.usage_standing(commission_id)
        kept = await self._ledger.get_session(commission.session_id)

        def counted(row: Any) -> dict[str, int]:
            return {
                "requests": row.requests,
                "input": row.tokens_in,
                "output": row.tokens_out,
                "cache_read": row.tokens_cache_read,
                "cache_write": row.tokens_cache_write,
                "tokens": row.tokens,
            }

        limit = int(TASK_AGENT_USAGE_LIMIT_TOKENS or 0)
        allowed = standing.allowed if standing is not None and standing.allowed is not None else limit
        return {
            "commission": counted(commission),
            # For a commission no task agent started, its own count as it
            # was read above: one reading, so the two numbers agree.
            "used": standing.used if standing is not None and standing.root != commission.id else commission.tokens,
            "limit": allowed if limit > 0 else None,
            "session": ({**counted(kept), "context_tokens": kept.context_tokens} if kept is not None else None),
        }

    # -- copying a session ------------------------------------------------

    async def fork_session(self, source_session_id: str, *, name: str | None = None, upto: str | None = None) -> Session:
        """Make a session from another by copying what was said in it, and return it. Raises ``ForkRefused``.

        The new session (kind ``fork``) holds the other's conversation up
        to one entry of its record (``upto``; all of it when none is
        named), cut back to the last place where it is whole: the other
        may be at work, and is not disturbed. It is opened with the system
        prompt and the tool definitions the other was opened with, so a
        provider that kept the head of the other's requests has the same
        head for this one's. See ``nekoclaw/runtime/fork.py``.

        It is the other's in everything but what was said: the same
        character, project and working directory; a directory of its own
        for its record. Nothing is at work in it: it is ``closed``, and a
        commission whose ``session_id`` names it is worked on in it like
        in any session (``submit``), without the tools that write files
        and run commands (``_spec``). What its requests use is counted
        with it and with that commission. What was said in it is removed
        like any session's when it is old, with the session it was copied
        from when that one's is, and at once by ``SpaceKeeper.clear_session``.

        Only a session the built-in runtime holds can be copied: dsh keeps
        what was said itself and has no command for it.
        """
        if self._closed:
            raise ForkRefused("the host is shutting down")
        # From before the session is looked at until its copy is on record
        # (or given up), with no await before this: what was said in it
        # is not removed meanwhile (``hold_files``; one whose record is
        # being removed this moment is refused below). Removed in between,
        # the copy would not be found among the session's copies, which
        # go when it does, and would keep what the session no longer has.
        self._copying_from[source_session_id] = self._copying_from.get(source_session_id, 0) + 1
        try:
            return await self._fork(source_session_id, name=name, upto=upto)
        finally:
            left = self._copying_from[source_session_id] - 1
            if left > 0:
                self._copying_from[source_session_id] = left
            else:
                del self._copying_from[source_session_id]

    async def _fork(self, source_session_id: str, *, name: str | None, upto: str | None) -> Session:
        """Make the copy ``fork_session`` returns; the session it is made from is held by the caller."""
        import shutil

        from config.nekoclaw_settings import TASK_AGENT_FORK_SECONDS

        source = await self._ledger.get_session(source_session_id)
        if source is None:
            raise ForkRefused(f"there is no session {source_session_id!r}")
        if source.runtime == RUNTIME_DSH:
            raise ForkRefused("a session that runs in dsh cannot be copied: dsh has no command for it")
        if source.status == "cleared" or source_session_id in self._clearing:
            raise ForkRefused("what was said in the session is no longer kept")
        source_dir = await asyncio.to_thread(self._session_dir_of, source)
        if not await asyncio.to_thread(_in_builtin, source_dir):
            raise ForkRefused("nothing was said in the session yet")
        session_id = new_session_id()
        target_dir = self._paths.session_dir(session_id, source.project)
        runtime = self._runtimes.builtin()
        if self._pump is None:
            self._pump = asyncio.ensure_future(self._pump_events())
        answer: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._forks[session_id] = answer
        try:
            await runtime.fork(
                session_id,
                session_dir=str(target_dir),
                source_session_id=source.id,
                source_dir=str(source_dir),
                upto=upto,
            )
            event = await asyncio.wait_for(answer, TASK_AGENT_FORK_SECONDS)
        except asyncio.TimeoutError:
            # It may still write the copy: removed when it says it has.
            self._forks_given_up[session_id] = str(target_dir)
            raise ForkRefused("the runtime did not answer in time") from None
        except asyncio.CancelledError:
            # Whoever asked for the copy is gone, and the runtime may write
            # it all the same: likewise removed when it says it has.
            self._forks_given_up[session_id] = str(target_dir)
            raise
        finally:
            self._forks.pop(session_id, None)
        if event.get("type") != "forked":
            raise ForkRefused(str(event.get("message") or "the runtime could not copy the session"))
        try:
            return await self._ledger.create_session(
                id=session_id,
                name=name or f"{source.name}/fork",
                lanlan_name=source.lanlan_name,
                kind="fork",
                project=source.project,
                workdir=source.workdir,
                session_dir=str(target_dir),
                runtime=RUNTIME_BUILTIN,
                forked_from=source.id,
                forked_at=str(event.get("upto") or "") or None,
                status="closed",
            )
        except Exception as exc:  # noqa: BLE001 - told as a copy that was not made
            # Written, and not on record: nothing would ever come by it to
            # remove it. The runtime is through with it (it said so).
            logger.warning("a copied session could not be put on record; what was copied is removed", exc_info=True)
            await asyncio.shield(asyncio.to_thread(shutil.rmtree, target_dir, True))
            raise ForkRefused(f"the copy could not be put on record: {exc}") from exc

    # -- summing up idle sessions -------------------------------------------

    def _drop_idle_timer(self, session_id: str) -> None:
        timer = self._idle_timers.pop(session_id, None)
        if timer is not None:
            timer.cancel()

    def _schedule_idle_compact(self, session_id: str, *, after: float | None = None) -> None:
        """Have a session summed up once nothing has been at work in it for ``TASK_AGENT_IDLE_COMPACT_AFTER_SECONDS``.

        Only while that is switched on (``TASK_AGENT_IDLE_COMPACT_ENABLED``).
        Whether it is worth it for this session is looked at when the
        time has come. A commission that takes the session before then
        drops the timer. ``after`` is how long from now, when that is
        not the whole time (the time had come by the timer, and not yet
        by what the ledger has of the session: ``_idle_compact``).
        """
        from config.nekoclaw_settings import TASK_AGENT_IDLE_COMPACT_AFTER_SECONDS, TASK_AGENT_IDLE_COMPACT_ENABLED

        self._drop_idle_timer(session_id)
        if not TASK_AGENT_IDLE_COMPACT_ENABLED or self._closed:
            return
        if session_id in self._idle_stopped:
            # What was at work in it was stopped with everything else, and
            # nothing new has been asked for in it since: the host makes
            # no request for it by itself (``drop_idle_work``).
            return
        stops = self._idle_stops

        def due() -> None:
            self._idle_timers.pop(session_id, None)
            if not self._closed:
                self._spawn(self._idle_compact(session_id, stops))

        self._idle_timers[session_id] = asyncio.get_running_loop().call_later(
            max(0.0, float(TASK_AGENT_IDLE_COMPACT_AFTER_SECONDS if after is None else after)), due
        )

    def drop_idle_work(self) -> None:
        """Give up everything the host was going to do by itself in idle sessions, and what it is doing there now.

        For when the user stops everything: no model request is made
        after that unless something is dispatched anew. A session comes to
        be summed up again once another commission has ended in it.

        That holds for the sessions in use right now as well: the
        commissions in them are stopped next, and let their sessions go
        after this. Letting go does not set a timer for a session that
        was in use when everything was stopped, until a commission is
        handed over or taken up again in it (``_new_work_in``).
        """
        self._idle_stops += 1
        in_hand = (*self._sessions, *self._queue, *self._taking_up, *self._requeueing)
        self._idle_stopped.update(self._held_by)
        self._idle_stopped.update(
            self._session_of[commission_id] for commission_id in in_hand if commission_id in self._session_of
        )
        for session_id in list(self._idle_timers):
            self._drop_idle_timer(session_id)
        for task in list(self._compacting.values()):
            self._cut_short(task)

    @staticmethod
    def _cut_short(task: "asyncio.Task[Any]") -> None:
        """Cancel a summing up once.

        Cancelled, it closes the session in the runtime and waits for
        that before it gives the session up. A second cancel would reach
        it while it waits, and have it leave: the runtime would still
        hold the session, the gateway its token, and the session would
        stay marked as being summed up.
        """
        if not task.done() and not task.cancelling():
            task.cancel()

    def _new_work_in(self, session_id: str | None, stops: int) -> None:
        """Note that something new is to be worked on in a session: it may be summed up again once that has ended.

        ``stops`` is how often everything had been stopped when whoever
        brings the work began to. If everything was stopped since, the
        work is stopped with it, and the session stays one that was in
        use when everything was stopped.
        """
        if not session_id:
            return
        if stops == self._idle_stops:
            self._idle_stopped.discard(session_id)
        else:
            self._idle_stopped.add(session_id)

    async def _stop_compacting(self, session_id: str) -> None:
        """Cut short the summing up of an idle session, and wait until the runtime has let go of the session."""
        task = self._compacting.get(session_id)
        if task is None or task is asyncio.current_task():
            return
        self._cut_short(task)
        await asyncio.wait([task])
        over = self._idle_over.get(session_id)
        if over is not None:
            # The task left while the session was being given back.
            await asyncio.wait([over])

    def _idle_compact_allowed(self, kept: Session | None, now: float) -> bool:
        """Whether a session, as the ledger has it, is to be summed up while idle: by its kind, its length and the limits."""
        from config.nekoclaw_settings import (
            TASK_AGENT_IDLE_COMPACT_AFTER_SECONDS,
            TASK_AGENT_IDLE_COMPACT_MIN_GAP_SECONDS,
            TASK_AGENT_IDLE_COMPACT_MIN_TOKENS,
            TASK_AGENT_IDLE_COMPACTS_PER_DAY,
        )

        if kept is None or kept.status != "closed" or kept.kind not in _IDLE_COMPACT_KINDS:
            return False
        if kept.runtime != RUNTIME_BUILTIN or kept.context_tokens < TASK_AGENT_IDLE_COMPACT_MIN_TOKENS:
            # dsh sums up by itself; a short conversation costs more to sum up than it saves.
            return False
        if now - kept.last_active_at < TASK_AGENT_IDLE_COMPACT_AFTER_SECONDS:
            return False
        if kept.compacted_at is not None and now - kept.compacted_at < TASK_AGENT_IDLE_COMPACT_MIN_GAP_SECONDS:
            return False
        while self._idle_compacted_at and now - self._idle_compacted_at[0] >= 24 * 3600.0:
            self._idle_compacted_at.popleft()
        return len(self._idle_compacted_at) + self._idle_compacts_under_way < TASK_AGENT_IDLE_COMPACTS_PER_DAY

    def _idle_now(self, session_id: str) -> bool:
        """Whether nothing is at work in a session, or on its way to be, and nothing else has claimed it."""
        return not self._closed and not self._in_use(session_id)

    async def _idle_compact(self, session_id: str, stops: int | None = None) -> bool:
        """Sum up the conversation of a session nothing has been at work in for long. Returns whether it was summed up.

        One model request, of the length of the conversation at the most,
        made by the host itself: for no commission, so it is counted with
        the session alone, against the daily quota of the agent model as
        one request, and against no commission's limit. Not made when
        NekoClaw is switched off, when the quota is used up, or when the
        session, by the time this gets to it, is in use or not worth it
        (``_idle_compact_allowed``).

        The session is opened in the built-in runtime as a commission's
        would be (the system prompt and the tools it was opened with
        before, so what a provider kept of its requests is used), told to
        sum itself up, and closed. A commission that is to be worked on in
        it meanwhile cuts this short (``_start``) and waits only for the
        runtime to let go: nothing of the conversation changes unless the
        summary was written.
        """
        from config.nekoclaw_settings import (
            TASK_AGENT_IDLE_COMPACT_AFTER_SECONDS,
            TASK_AGENT_IDLE_COMPACT_ENABLED,
            TASK_AGENT_IDLE_COMPACT_SECONDS,
        )
        from nekoclaw.runtime.protocol import GatewaySpec

        if not TASK_AGENT_IDLE_COMPACT_ENABLED or not self._idle_now(session_id):
            return False
        if stops is not None and stops != self._idle_stops:
            # Everything was stopped between the timer and this.
            return False
        if self._switched_on is not None:
            try:
                if not self._switched_on():
                    return False
            except Exception:  # noqa: BLE001 - an unreadable switch counts as off
                return False
        task = asyncio.current_task()
        assert task is not None
        # Claimed before anything is awaited: a commission that takes the
        # session from here on finds this, and cuts it short.
        self._compacting[session_id] = task
        events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        runtime: Any = None
        # What the gateway knows this opening of the session by: no
        # commission's name, so no commission's token is touched.
        grant = f"session:{session_id}"
        marked = opened = compacted = counted = asked = False
        # Set when the timer brought this here a moment early: how long to wait still.
        too_early: float | None = None
        now = self._ledger._clock()
        try:
            kept = await self._ledger.get_session(session_id)
            if kept is None or not self._idle_compact_allowed(kept, now):
                if stops is not None and kept is not None:
                    # A timer set when the session was let go runs by the
                    # loop's clock; how long the session has been idle is
                    # told by what the ledger wrote down, by the clock on
                    # the wall, and that may have been written a moment
                    # after the timer was set. When nothing but that
                    # stands in the way, it is looked at again once the
                    # time is up by the ledger, and not given up.
                    up = kept.last_active_at + float(TASK_AGENT_IDLE_COMPACT_AFTER_SECONDS)
                    if up > now and self._idle_compact_allowed(kept, up):
                        too_early = up - now
                return False
            # Counted from here on, before anything else is awaited.
            self._idle_compacts_under_way += 1
            counted = True
            last = await self._ledger.select(session_id=session_id, limit=1)
            config = await self._model_config()
            if not last or not config or not config.get("model"):
                return False
            commission = last[0]
            place = dataclasses.replace(
                kept, workdir=kept.workdir or commission.workdir, project=kept.project or commission.project
            )

            def prepare() -> tuple[str, str, bool]:
                session_dir = self._session_dir_of(place)
                return str(session_dir), str(self._workdir_of(place, session_dir)), _in_builtin(session_dir)

            session_path, workdir, has_record = await asyncio.to_thread(prepare)
            if not has_record:
                return False
            # Before the ledger is asked: cancelled while it writes, the
            # status may be written all the same, and has to be taken
            # back (which changes nothing if it was not).
            marked = True
            if not await self._ledger.begin_compacting(session_id):
                return False
            model = await self._model_of(config)
            gateway_spec = None
            if self._gateway is not None:
                # As a commission's session is opened: after the summary the
                # runtime puts the system prompt and the tool definitions
                # together anew from what it has, and that has to be what
                # the next commission is to find.
                url = await self._gateway.start()
                gateway_spec = GatewaySpec(
                    url=url,
                    token=self._gateway.issue(grant, candidate_plugin_ids=(), supports_images=model.supports_images),
                )
            spec = await self._spec(commission, kept, model, workdir=workdir, session_dir=session_path, gateway=gateway_spec)
            runtime = self._runtimes.builtin()
            if self._pump is None:
                self._pump = asyncio.ensure_future(self._pump_events())
            self._service[session_id] = events
            opened = True
            await runtime.start(spec)

            async def summed_up() -> None:
                nonlocal asked, compacted
                while True:
                    event = await events.get()
                    kind = event.get("type")
                    if kind == "started" and not asked:
                        # Charged here, when the request is about to be
                        # made, and not before the session was marked
                        # and opened: nothing is charged for a summing
                        # up that never got as far as asking.
                        if self._meter is not None and not await self._meter(commission.id):
                            # The daily quota is used up: the host makes no request of its own.
                            return
                        asked = True
                        await runtime.compact(session_id)
                    elif kind == "usage":
                        await self._note_usage(session_id, None, event)
                    elif kind == "compacted":
                        # Written, whatever becomes of this from here on.
                        compacted = True
                    elif kind in ("ended", "error"):
                        # The summing up is over, or was refused; or the
                        # session could not be opened at all.
                        return

            await asyncio.wait_for(summed_up(), TASK_AGENT_IDLE_COMPACT_SECONDS)
            return compacted
        except asyncio.TimeoutError:
            logger.warning("summing up idle session %s took too long; given up", session_id)
            return False
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the session is as it was; the next commission goes on from that
            logger.warning("summing up idle session %s failed", session_id, exc_info=True)
            return False
        finally:
            if counted:
                self._idle_compacts_under_way -= 1
                if asked or compacted:
                    # The session was told to sum itself up: a request the
                    # host made by itself, whatever became of it, and one
                    # of those the day has a limit for.
                    self._idle_compacted_at.append(now)
            # Whatever became of it (also when a commission cut it short):
            # the runtime lets go of the session before anyone else has
            # it. In a task of its own, waited for here: cancelled while
            # it waits (this is where a stop or a commission finds a
            # summing up that is all but over), this leaves, and the
            # giving back is carried through all the same.
            over = self._spawn(
                self._idle_compact_over(
                    session_id, task, runtime=runtime, events=events, opened=opened, grant=grant,
                    marked=marked, compacted=compacted, too_early=too_early, stops=stops,
                )
            )
            self._idle_over[session_id] = over
            await asyncio.shield(over)

    async def _idle_compact_over(
        self,
        session_id: str,
        task: "asyncio.Task[Any]",
        *,
        runtime: Any,
        events: "asyncio.Queue[dict[str, Any]]",
        opened: bool,
        grant: str,
        marked: bool,
        compacted: bool,
        too_early: float | None,
        stops: int | None,
    ) -> None:
        """Give a session back after the host had it to sum it up (``_idle_compact``).

        The runtime closes it, its token for the gateway is taken back,
        the ledger has it closed again, and it is no longer claimed.
        All of it, whatever becomes of the task that summed it up:
        ``_stop_compacting`` waits for this.
        """
        try:
            if await self._end_service(session_id, runtime, events, opened):
                # Said while the session was being closed.
                compacted = True
            if self._gateway is not None:
                self._gateway.revoke(grant)
            if marked:
                try:
                    await self._ledger.end_compacting(session_id, compacted=compacted)
                except Exception:  # noqa: BLE001 - the status is for showing
                    logger.warning("noting that a session is no longer being summed up failed", exc_info=True)
        finally:
            if self._compacting.get(session_id) is task:
                del self._compacting[session_id]
            if self._idle_over.get(session_id) is asyncio.current_task():
                del self._idle_over[session_id]
            if too_early is not None and stops == self._idle_stops and self._idle_now(session_id):
                # A little more than what is left, so that it is not a
                # moment early once more.
                self._schedule_idle_compact(session_id, after=too_early + _IDLE_TIMER_MARGIN_SECONDS)

    async def _end_service(
        self, session_id: str, runtime: Any, events: "asyncio.Queue[dict[str, Any]]", opened: bool
    ) -> bool:
        """Close a session the host opened by itself and wait until the runtime says it is gone.

        Returns whether the runtime said meanwhile that the conversation was summed up.
        """
        from nekoclaw.runtime.protocol import PROCESS_EXITED_ERROR

        summed_up = False
        try:
            if not opened or runtime is None:
                return False
            await runtime.close(session_id)

            async def gone() -> None:
                nonlocal summed_up
                while True:
                    event = await events.get()
                    kind = event.get("type")
                    if kind == "usage":
                        await self._note_usage(session_id, None, event)
                    elif kind == "compacted":
                        summed_up = True
                    elif kind == "ended" and event.get("reason") == "closed":
                        return
                    elif kind == "error" and event.get("command") in ("close", "start"):
                        # It was never there, or is gone already.
                        return
                    elif kind == "ended" and event.get("error") == PROCESS_EXITED_ERROR:
                        # The runtime process went away, and the session with it.
                        return

            # However long it takes. Given back before the runtime said
            # so, the session could be taken by a commission, and what
            # the runtime says of this opening of it afterwards (that it
            # is closed) would be taken for that commission's.
            waiting = asyncio.ensure_future(gone())
            try:
                done, _pending = await asyncio.wait({waiting}, timeout=_SERVICE_CLOSE_SECONDS)
                if not done:
                    logger.warning("the runtime has not said that session %s is closed; still waited for", session_id)
                await waiting
            finally:
                if not waiting.done():
                    waiting.cancel()
        except Exception:  # noqa: BLE001 - nothing more can be done for it
            logger.warning("closing a session the host had open failed", exc_info=True)
        finally:
            if self._service.get(session_id) is events:
                del self._service[session_id]
        return summed_up

    # -- shutdown ---------------------------------------------------------

    async def shutdown(self) -> None:
        """Stop the runtime and every session. Commissions under way end as failed."""
        self._closed = True
        for session_id in list(self._idle_timers):
            self._drop_idle_timer(session_id)
        for waiting in list(self._forks.values()):
            if not waiting.done():
                waiting.set_exception(ForkRefused("the host is shutting down"))
        self._forks_given_up.clear()
        queued = list(self._queue)
        self._queue.clear()
        for commission_id in queued:
            self._resume.pop(commission_id, None)
            # Refused for one that was stopped meanwhile, and rightly so.
            await self._ledger.transition(commission_id, "failed", reason=_SHUT_DOWN_REASON)
        if self._starting:
            # Sessions on their way in are let in, so they are closed with the rest.
            _done, pending = await asyncio.wait(list(self._starting), timeout=_SHUTDOWN_START_WAIT_SECONDS)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        # Recorded first: what the closing sessions still say must not turn a
        # commission nobody stopped into a stopped one.
        for commission_id in list(self._sessions):
            await self._ledger.transition(commission_id, "failed", reason=_SHUT_DOWN_REASON)
        if self._runtimes.started:
            # A parent whose idle session was closed while its task agents
            # worked holds no session and is in no queue, yet is this host's
            # and under way. (With no runtime ever started there is none, and
            # the ledger is not opened for nothing.)
            for item in await self._ledger.select(statuses={"working"}):
                if (
                    item.route == "task_agent"
                    and item.host_instance == self._ledger.instance
                    and item.id not in self._sessions
                ):
                    await self._ledger.transition(item.id, "failed", reason=_SHUT_DOWN_REASON)
        for _name, runtime in self._runtimes.take_all():
            try:
                await runtime.shutdown()
            except Exception:  # noqa: BLE001 - shutting down goes on regardless
                logger.exception("shutting the runtime down failed")
        for commission_id in list(self._sessions):
            self._discard(commission_id)
        self._held_by.clear()
        self._unsubscribe()
        pump, self._pump = self._pump, None
        if pump is not None:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
