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
  and lets task agents read the space without asking and never write it.

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
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from nekoclaw.host.capabilities import CapabilityRegistry
from nekoclaw.host.ledger import (
    ACTIVE_STATUSES,
    COMMISSION_OVER_REASON,
    ENDED_STATUSES,
    Commission,
    Ledger,
    MessageLimits,
    Session,
    SessionRow,
    WakeRequest,
)
from nekoclaw.host.paths import DEFAULT_PROJECT, DSH_SESSION_NAME, TRANSCRIPT_NAME, HostPaths
from nekoclaw.host.permissions import (
    CHOICES,
    DENIED_BY_SETTINGS,
    SPACE_COMMAND,
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
_SHUT_DOWN_REASON = "the host was shut down"
_QUOTA_REASON = "the free daily quota of the agent model is used up"
_STOPPED_MEANWHILE_REASON = "stopped while it was being taken up again"
_WOKEN_REASON = "it rested; what it waited for takes it up again"
# Tools a task agent that another task agent started is not offered: it
# starts none itself, writes to its own team only, and hands its result back
# when its part is done instead of waiting to be woken.
_CHILD_DISABLED_TOOLS = ("subagent", "message_session", "schedule_wake")
# How long what is to be handed to a session waits for that session's
# runtime to have closed it, when it is closing this moment.
_CLOSING_RETRY_SECONDS = 1.0
# The kinds of session another session finds and writes to: the session of
# a project, and that of a commission of no project. A session of any other
# kind is somebody's helper (a task agent another one started), reached
# through whoever it helps.
_ADDRESSED_KINDS = ("project", "single")
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


@dataclass(frozen=True, slots=True)
class Handover:
    """What came of handing a session what waited for it (``TaskAgentManager.hand_over``).

    ``handed``: it is with a commission, on record. ``dropped``: it never
    will be, on record with the reason. ``held``: not now; it stays as it
    was and is tried again after ``retry_in`` seconds, or sooner when
    something changes that the caller is told of.
    """

    outcome: str
    retry_in: float = 0.0


def _clock_time(timestamp: float) -> str:
    """Return a time as a task agent, or a person, is told it: local, to the minute."""
    from datetime import datetime

    return datetime.fromtimestamp(float(timestamp)).astimezone().strftime("%Y-%m-%d %H:%M")


def _until_tomorrow(now: float) -> float:
    """Return the seconds from ``now`` to a minute past the next local midnight.

    The seconds that really pass: where the clocks are changed before
    then, the two times of day are an hour more, or less, apart than
    they look.
    """
    from datetime import datetime, timedelta

    moment = datetime.fromtimestamp(float(now))
    tomorrow = (moment + timedelta(days=1)).replace(hour=0, minute=1, second=0, microsecond=0)
    return max(60.0, tomorrow.timestamp() - float(now))

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
    # Every text is sent under a number of its own that is never given
    # again (``serial`` counts them), which the runtime says with a
    # refusal. ``numbered`` has, for those that are neither known to be
    # taken nor refused, which of the texts sent each is: the next may be
    # on its way before a refusal of this one is heard of, so the one
    # refused need not be the last one sent (``_withdraw``).
    serial: int = 0
    numbered: dict[int, int] = field(default_factory=dict)
    # How runs ended that may still be said to have taken a text: which
    # run (where the runtime numbers them), which texts it left open as it
    # ended (``EndedEvent.open_inputs``), and what its senders were told
    # of it (its words, and how the commission stood). For a text that is
    # said to be taken only after its run ended (``_on_taken``): its
    # senders are told the same, and not what a later run made of the
    # commission. Kept for as long as one of those texts is neither taken
    # nor given up (``_ends_kept``), and no longer.
    ends: list[tuple[int | None, frozenset[int], str, str]] = field(default_factory=list)
    # The end of a run (the event) that is being acted on: from where it is
    # taken up until its senders are told. For where whoever handles the
    # events is stopped in between, as the host shuts down
    # (``_owe_what_is_left``).
    ending: dict[str, Any] | None = None
    # The end of a run is being looked at this moment: whether the commission
    # is done with it, rests, or waits on. What waits to be handed to the
    # session is kept back meanwhile (``_hand_to``): steered in now, it would
    # start a run in a session that is about to be closed.
    settling: bool = False
    # The session is open with no run under way and none to come by itself
    # soon: its commission waits for task agents it started, or for
    # background jobs. What is handed to it then starts a turn.
    idle: bool = False
    # Wake requests whose text was steered into the session and that no
    # event has yet shown it took. Should the session close first, they are
    # put back, to be handed over again.
    handed: dict[int, list[int]] = field(default_factory=dict)
    # Those among them that it took and whose senders were not yet told
    # that the turn is over, by the number of the text that brought them:
    # what the end of a run tells of, as far as that run got, and nothing
    # else (``_turn_over``).
    took: dict[int, list[int]] = field(default_factory=dict)
    # The commission as the ledger last told of it (every change of its
    # status is told), and as it was let rest since: how it stood when a
    # turn ended, for where the ledger cannot be read just then.
    stands: Commission | None = None
    # Until when (the loop's clock) nothing more is handed to the session:
    # it refused a text that brought wake requests, and is not asked again
    # at once.
    refused_until: float = 0.0
    # The address and the name of the model the session was started with.
    model: tuple[Any, Any] | None = None


# How many ends of runs a session keeps at the most (``_Session.ends``):
# a backstop, far above what texts are ever open at once.
_ENDS_KEPT_MAX = 64


def _number(value: Any) -> int | None:
    """Return the whole number an event carries; None for anything else (a boolean is not one)."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _ends_kept(session: _Session) -> None:
    """Let go of how those runs ended of which no text can be said to have been taken by them any more.

    A run is kept for the texts it named as open when it ended, while one
    of them is still open here (sent, and neither said to be taken nor
    given up): however many runs come and go meanwhile, none of which is
    kept for a text it did not name.
    """
    session.ends = [item for item in session.ends if any(number in session.handed for number in item[1])][
        -_ENDS_KEPT_MAX:
    ]


def _ended_as(session: _Session, event: dict[str, Any], number: int) -> tuple[str, str] | None:
    """Return what the senders were told of the run that took a text which is said to be taken late; None if not kept.

    Which run that was, the runtime says (``run``): the text may have
    gone to an earlier run first, whose answer never got there. One that
    does not say is taken for the first that left this text open.
    """
    run = _number(event.get("run"))
    if run is not None:
        ended = next((item for item in session.ends if item[0] == run), None)
    else:
        ended = next((item for item in session.ends if number in item[1]), None)
    return (ended[2], ended[3]) if ended is not None else None


def _not_refused(session: _Session, upto: int) -> None:
    """Forget the numbers the texts up to one were sent under: taken, they are not refused any more."""
    for key in [key for key, value in session.numbered.items() if value <= upto]:
        del session.numbered[key]


def _withdraw(session: _Session, serial: Any) -> list[int]:
    """Take a text the session did not get out of the count of those sent; returns the wake requests it brought.

    By the number it was sent under (``_Session.numbered``), and with no
    such number the last one sent, which is what a runtime that names
    none means. The texts sent after it move up by one: the runtime
    numbers what it takes, and this one it never took, so what it calls
    the n-th from here on is what was sent as the n+1-th. A number that
    is not known (the text was taken, or withdrawn before) withdraws
    nothing.
    """
    if _number(serial) is not None:
        number = session.numbered.pop(serial, None)
        if number is None:
            return []
    else:
        number = session.sent
        for key in [key for key, value in session.numbered.items() if value == number]:
            del session.numbered[key]
    wakes = session.handed.pop(number, None) or []
    for later in sorted(key for key in session.handed if key > number):
        session.handed[later - 1] = session.handed.pop(later)
    for key, value in session.numbered.items():
        if value > number:
            session.numbered[key] = value - 1
    session.sent -= 1
    return wakes


def _refusal(session: _Session, event: dict[str, Any]) -> list[int]:
    """Take the text a refusal is about out of the count of those sent; returns the wake requests it brought.

    Refused, the session never took it. What it was to hand over is not
    left under its number, which the next text gets (and whose being
    taken would then count for this one). Which text it was, the runtime
    says (``serial``): the next one may have been sent before this was
    heard of, and is not the one that was refused. A refusal of anything
    but a text, or with none sent, takes nothing out.
    """
    if event.get("command") in ("prompt", "steer", "follow_up") and session.sent > 0:
        return _withdraw(session, event.get("serial"))
    return []


class _Handed(str):
    """A text that brings what wake requests were to hand over; ``wakes`` are their ids.

    It goes the way every text for a commission goes (sent at once, kept
    for when the commission is started, carried over a start that failed),
    and where it is sent at last, the session is noted to have these
    requests on their way (``_send_text``).
    """

    wakes: tuple[int, ...]

    def __new__(cls, text: str, wakes: Iterable[int]) -> "_Handed":
        made = super().__new__(cls, text)
        made.wakes = tuple(wakes)
        return made


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


def _may_be_a_record(path: str) -> bool:
    """Return whether a path among the files a commission works on is to be looked at as another session's record.

    One that has a second name (a hard link), or that is named like a
    file a session keeps of itself, by the name it is asked under or by
    what that leads to (a link under another name). Blocking: it asks the
    file system, and reads no file.
    """
    import os
    from pathlib import Path

    from nekoclaw.runtime.snapshot import SYSTEM_FILE

    kept = (TRANSCRIPT_NAME, SYSTEM_FILE, DSH_SESSION_NAME)
    if Path(path).name in kept or _has_second_name(path):
        return True
    try:
        return Path(os.path.realpath(path)).name in kept
    except OSError:
        return False


def _has_second_name(path: str) -> bool:
    """Whether a path is a file that has more than one name (a hard link). Blocking."""
    import os
    import stat

    try:
        found = os.stat(path)
    except OSError:
        return False  # Not there: nothing to tell from it.
    return stat.S_ISREG(found.st_mode) and found.st_nlink > 1


def _lies_in(directory: Any, files: set[tuple[int, int]], apart: set[str], limit: int) -> bool:
    """Whether one of some files lies in a directory under any of its names, however deep. Blocking.

    The files are told by device and number, which is what a second name
    (a hard link) shares with the first. Read as a search of the
    directory reads it: through no link. The directories in ``apart``
    are not gone into (other sessions' own directories, which are ruled
    on by themselves). No more than ``limit`` entries are looked at: a
    directory that has more is taken to hold one of the files, as it
    cannot be told that it does not. A directory is read an entry at a
    time, and no further than that: one with a great many entries is not
    read whole to find that it has too many.
    """
    import os
    import stat

    looked = 0
    pending = [os.fspath(directory)]
    while pending:
        try:
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    looked += 1
                    if looked > limit:
                        return True
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if os.path.normcase(os.path.abspath(entry.path)) not in apart:
                                pending.append(entry.path)
                            continue
                        # (Asked of the file itself: what a directory tells
                        # of its entries does not have the number on every
                        # system.)
                        found = os.stat(entry.path, follow_symlinks=False)
                    except OSError:
                        continue
                    if (
                        stat.S_ISREG(found.st_mode)
                        and found.st_nlink > 1
                        and (found.st_dev, found.st_ino) in files
                    ):
                        return True
        except OSError:
            continue  # Not to be read (or gone meanwhile): nor is it by a search.
    return False


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
    ) -> None:
        self._meter = meter
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
        # Commissions whose next start has its first model request counted
        # already: counted before they were woken, so that one whose quota
        # is used up is left to rest instead of failing on the way up.
        self._prepaid_starts: set[str] = set()
        # Wake requests to be made pending again that the ledger could not be
        # written for yet, and the task that tries again.
        self._unreturned: set[int] = set()
        self._returning: asyncio.Task[Any] | None = None
        # Likewise those to be put on record as taken: their session has
        # them, and the ledger could not be written when it said so.
        self._unconfirmed: set[int] = set()
        # Turns that ended while the ledger could not be written: the
        # commission, what it ended on and how it stood, the wake requests
        # whose notes that turn took, and what its session had taken that
        # the ledger did not have yet. Told when the ledger can be written
        # again (it tells each sender once, whoever asks), and of that
        # turn's notes only: one the session took since belongs to a later
        # turn.
        self._turns_owed: list[tuple[str, str, str, list[int], list[int]]] = []
        # Commissions that are over whose requests on their way could not be
        # looked up when they ended (``_return_untaken``), and when that
        # was: looked up again, for what they were handed by then.
        self._ended_unlooked: dict[str, float] = {}
        # The last count against the quota was refused. Cleared by the next
        # one that is not, which has what waits for the quota tried again.
        self._quota_out = False
        # While it is: when the quota is next asked on behalf of something
        # that waits to be woken (ledger clock).
        self._quota_asked_again_at = 0.0
        # ``nekoclaw.host.wakes.SessionWakes``, or None: told when there is
        # something new to hand over, or when what was held may go now.
        self._waker: Any = None
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
            session_list=self._cb_session_list,
            session_read=self._cb_session_read,
            session_send=self._cb_session_send,
            wake_schedule=self._cb_wake_schedule,
        )

    def set_waker(self, waker: Any) -> None:
        """Set what is told that something waits to be handed to a session (``wakes.SessionWakes``)."""
        self._waker = waker

    def _kick_waker(self) -> None:
        if self._waker is not None:
            self._waker.kick()

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

    # -- starting ---------------------------------------------------------

    async def submit(self, commission: Commission, *, candidate_plugin_ids: Any = (), wait: bool = True) -> bool:
        """Hand a commission to a task agent: now if a place is free, else when one is.

        With ``wait`` False a commission that finds no free place is failed
        instead of queued. Returns whether it was started or queued.
        """
        self._candidates[commission.id] = tuple(str(item) for item in candidate_plugin_ids)
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
        if not self._take_place(commission.id):
            if not wait:
                self._candidates.pop(commission.id, None)
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

        A commission that follows another up was filed with that one's
        session. Otherwise: a task agent started by another gets a session
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
        Whom the task agent works for, how the host's capabilities are
        reached, and what the task agent space holds for the project.
        """
        from config.nekoclaw_settings import TASK_AGENT_BRIEF_MAX_TOKENS
        from config.prompts import prompts_nekoclaw_brain as texts
        from utils.tokenize import truncate_to_tokens

        if commission.parent_id is not None:
            parts = [texts.TASK_AGENT_CHILD_FRAME]
        else:
            parts = [texts.TASK_AGENT_SESSION_FRAME.format(lanlan_name=commission.lanlan_name)]
        if with_gateway:
            parts.append(texts.TASK_AGENT_BRIEF_GATEWAY_NOTE)
        own = truncate_to_tokens("\n\n".join(parts), TASK_AGENT_BRIEF_MAX_TOKENS)
        kept = await self._space_brief(commission)
        # After the cap above and with one of its own (``SPACE_BRIEF_MAX_TOKENS``):
        # neither part can push the other out.
        return own + "\n\n" + kept if kept else own

    async def _opening(self, commission: Commission, *, cleared: bool, back: bool = False) -> str:
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
        worked on others since.

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
        elif commission.source == "session":
            # Filed for what another session wrote: that, framed, is what
            # the commission is. Not cut either: a frame cut short would
            # leave what follows it looking like part of the note.
            whole = await self._opening_of_notes(commission)
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
        picked = await self._picked_entries(commission)
        if picked:
            opening += "\n\n" + picked
        language = _REPLY_LANGUAGES.get(commission.lang or "", None)
        if language:
            # Said with every commission: the session's system prompt names
            # the language of the commission it was first opened for.
            opening += "\n\n" + REPLY_LANGUAGE_SENTENCE.replace("<<LANGUAGE>>", language)
        return opening

    async def _opening_of_notes(self, commission: Commission) -> str:
        """Return what opens a commission that was filed for notes from other sessions: what it is, and the notes, framed.

        Put together anew whenever the commission is opened (each time
        with frames of their own): the notes are on record with the
        commission they were handed to.
        """
        from config.prompts import prompts_nekoclaw_sessions as words

        head = words.SESSION_COMMISSION_TEMPLATE.format(commission_id=commission.id, lanlan_name=commission.lanlan_name)
        notes = await self._ledger.messages_opening(commission.id)
        if not notes and await self._ledger.opening_handed_on(commission.id):
            # It never took them (its opening message was refused, say), and
            # they were handed to another commission since, or dropped: they
            # are not said a second time here, from the record or from what
            # was filed.
            return f"{head}\n\n{words.SESSION_COMMISSION_NOTES_GONE}"
        if not notes:
            # Its notes are gone from the record; what was filed says what they were.
            return f"{head}\n\n{commission.dispatch_text}"
        framed = [await self._framed_note(note, commission.lanlan_name, answerable=True) for note in notes]
        return "\n\n".join([head, *framed])

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
            from nekoclaw.runtime import TaskAgentSpec, ModelConfig
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

            if "supports_images" not in config:
                config = {**config, "supports_images": await self._sees_images(config)}
            if "context_window" not in config:
                config = {**config, "context_window": await self._context_window(config)}
            if "compact_at" not in config:
                config = {**config, "compact_at": await self._compact_at(config)}
            model = ModelConfig(**config)
            session.model = (config.get("base_url"), config.get("model"))

            gateway_spec = None
            image_retention: dict[str, int] = {}
            image_pools: dict[str, str] = {}
            if self._gateway is not None:
                # For every built-in tool that hands pictures back, whether it
                # can be called right now or not: every one of them is
                # reached through the gateway's call tool, one that is
                # switched on while the task agent works included, and its
                # screenshots are limited too.
                image_retention, image_pools = self._image_retention(self._registry.builtins())
                # Whether or not anything can be called right now: the
                # session keeps the tools it is opened with, and what can be
                # called changes while it lasts.
                url = await self._gateway.start()
                token = self._gateway.issue(
                    commission.id,
                    candidate_plugin_ids=self._candidates.get(commission.id, ()),
                    supports_images=model.supports_images,
                )
                gateway_spec = GatewaySpec(url=url, token=token)

            spec = TaskAgentSpec(
                session_id=session.session_id,
                workdir=workdir,
                session_dir=session_path,
                model=model,
                instructions=await self._instructions(commission, with_gateway=gateway_spec is not None),
                reply_language=_REPLY_LANGUAGES.get(commission.lang or "", None),
                top_level=commission.parent_id is None,
                # A task agent started by a task agent starts none itself,
                # and neither writes to sessions outside its team nor asks
                # to be woken (``_CHILD_DISABLED_TOOLS``).
                disabled_tools=list(_CHILD_DISABLED_TOOLS) if commission.parent_id is not None else [],
                image_retention=image_retention,
                image_pools=image_pools,
                gateway=gateway_spec,
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
            onward = await self._opening(commission, cleared=False)
            anew = await self._opening(commission, cleared=True) if commission.continues else onward
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
            # The wake requests whose text this first message brings: what
            # was handed to the commission while it had no session open (it
            # rested, or was being started), and, for one that was filed for
            # notes and is told what it is, those notes.
            riding = [wake for piece in session.carried for wake in getattr(piece, "wakes", ())]
            if opens and commission.source == "session":
                riding = [*riding, *await self._ledger.opening_wakes(commission.id)]
            # Whether a person's words are in it: what she dispatched or the
            # user said, told to the session for the first time (the message
            # that tells a commission what it is, unless it was filed for
            # notes), or something kept to be said. Not where it is only
            # what wake requests brought; nor is it for the message that
            # says once more what the commission is, to a session that was
            # at another one since or holds nothing of it: nobody spoke now.
            said_first = opens and not opened and commission.source != "session"
            nobody = not said_first and all(isinstance(piece, _Handed) for piece in session.carried)
            if await stopped_meanwhile():
                # The stop has closed the session already.
                await self._close_session(session)
                return False
            await self._send_text(runtime, session, "prompt", text, opening=opens, wakes=riding, human=not nobody)
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

        A meter that cannot count does not stop the work. One that was
        counted before the commission was woken (``_count_ahead``) is not
        counted again.
        """
        if self._meter is None:
            return True
        if session.commission_id in self._prepaid_starts:
            self._prepaid_starts.discard(session.commission_id)
            session.prepaid_requests += 1
            return True
        try:
            allowed = await self._meter(session.commission_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a meter that cannot count does not stop the work
            logger.warning("counting a task agent's model request failed", exc_info=True)
            return True
        self._note_quota(bool(allowed))
        if allowed:
            session.prepaid_requests += 1
        return bool(allowed)

    def _note_quota(self, allowed: bool, *, asked_again: bool = False) -> None:
        """Remember whether the quota had a request left; what waited for it is tried again when it has.

        ``asked_again``: it was asked though it was known to be used up
        (the time to ask again had come), so a refusal sets that time anew.
        """
        if allowed and self._quota_out:
            self._quota_out = False
            self._kick_waker()
        elif not allowed and (asked_again or not self._quota_out):
            self._quota_out = True
            # The quota is for a day: asked again after the next midnight,
            # and not on every occasion until then.
            now = self._ledger._clock()
            self._quota_asked_again_at = now + _until_tomorrow(now)

    @staticmethod
    async def _send_text(
        runtime: Any,
        session: _Session,
        command: str,
        text: str,
        *,
        opening: bool = False,
        wakes: Iterable[int] | None = None,
        human: bool | None = None,
    ) -> None:
        """Send ``prompt`` or ``steer`` text to a session, counting it.

        Counted before it is sent: the end of a run that has not taken it may
        be handled while the command is on its way. ``opening`` says that
        the text opens the commission (a ``prompt``): the runtime keeps it
        word for word when it sums the conversation up.

        ``wakes`` are the wake requests whose text this brings (those of
        the text itself when it is a ``_Handed``): they are on their way
        from here until the runtime says that this text, by its number,
        is in the session's conversation (``_on_taken``). A session that
        closes before that has them put back.

        ``human`` says whether a person wrote the text; left out, a text
        that brings wake requests is taken for one nobody wrote (a note
        from another session, a time that has come), and every other for
        the user's. The runtime is told only where it is not: a note
        carries nobody's permissions, and the run it starts is not one
        the user spoke in.
        """
        if human is None:
            human = not isinstance(text, _Handed)
        said = {} if human else {"human": False}
        session.sent += 1
        number = session.sent
        session.serial += 1
        serial = session.serial
        session.numbered[serial] = number
        riding = list(wakes if wakes is not None else getattr(text, "wakes", ()))
        if riding:
            session.handed[number] = riding
        session.idle = False
        # A steer to an idle session starts a new run. An answer arriving
        # meanwhile must then wait for that run to end (``held_answer``)
        # rather than be sent as a prompt the busy session refuses.
        woke = command == "steer" and session.run_ended
        if woke:
            session.run_ended = False
        try:
            if opening:
                session.opening_sent = True
                await runtime.prompt(session.session_id, text, opening=True, serial=serial, **said)
            else:
                await getattr(runtime, command)(session.session_id, text, serial=serial, **said)
        except BaseException:
            # By its own number: another text may have been sent meanwhile.
            _withdraw(session, serial)
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

        What wake requests brought is not kept: the commission is over,
        or is about to be, and they are pending again for whatever their
        session is at next (``_return_untaken``). Kept here as well, the
        same text would be said a second time when this commission is
        taken up again, and its being taken would count for a request
        that is another commission's by then.
        """
        commission_id = session.commission_id
        if session.closing and not session.keep_notes:
            session.carried = []
            return
        session.keep_notes = True
        unsent = [*session.carried, *self._notes.pop(commission_id, [])]
        session.carried = []
        kept = [text for text in unsent if not isinstance(text, _Handed)]
        if len(kept) != len(unsent) and not self._closed:
            # Looked up in the ledger, and not put back from what the text
            # names: one that was put back already (the session refused it)
            # may be on its way to another commission by now.
            self._spawn(self._return_untaken(commission_id, self._ledger.fired_mark()))
        if kept:
            self._carried[commission_id] = [*self._carried.get(commission_id, []), *kept]

    def _discard(self, commission_id: str) -> None:
        """Give a commission's place back: its session is not, or no longer, open in a runtime for it.

        The commission goes on holding its session until it is known to be
        over (``_settle_hold``): one that waits for an answer keeps it.
        """
        session = self._sessions.pop(commission_id, None)
        self._candidates.pop(commission_id, None)
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
        if commission is not None and commission.status not in ENDED_STATUSES and not commission.resting:
            # It waits for the user, or for task agents it started.
            return
        # (One that rests lets its session go like one that is over: nobody
        # is at work on it until what it waits for comes, which may be
        # hours, and other commissions of the session are worked on
        # meanwhile. Woken, it waits for its session like any other.)
        del self._held_by[session_id]
        if self._queue:
            self._spawn(self._start_next())
        # A note that waited because this commission was in the way may go now.
        self._kick_waker()

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
        if previous == "waiting_user" and not self._closed:
            # What was kept back while it waited for the user may go now.
            self._kick_waker()
        live = self._sessions.get(commission.id)
        if live is not None:
            live.stands = commission
        if (
            commission.status in ENDED_STATUSES
            and previous not in ENDED_STATUSES
            and not self._closed
            # Only a task agent's commission is handed anything: one that a
            # plugin carries out has no session to hand it to, and is not
            # looked up for nothing.
            and commission.route == "task_agent"
            and commission.session_id
        ):
            self._spawn(self._return_untaken(commission.id, self._ledger.fired_mark()))
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
        session = self._sessions.get(commission_id)
        # What was sent to the session and not seen taken goes back with it,
        # however it went (a runtime whose process is gone says so with an
        # error, and no ``closed`` follows): nothing is left on record as
        # handed to a session that is not there.
        back = [wake for wakes in session.handed.values() for wake in wakes] if session is not None else []
        if session is not None:
            session.handed = {}
        if commission_id in self._queue or commission_id in self._requeueing:
            # Queued to be taken up again: what was said meanwhile goes along
            # with it, and so do its candidate plugins. It goes on holding
            # its session.
            self._sessions.pop(commission_id, None)
            if self._gateway is not None:
                self._gateway.revoke(commission_id)
        else:
            self._discard(commission_id)
            if session is None or not session.keep_notes:
                dropped = self._notes.pop(commission_id, [])
                back.extend(wake for text in dropped for wake in getattr(text, "wakes", ()))
        if back and not self._closed:
            await self._put_back(back)
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
        # A runtime speaks of sessions; what it says is about the commission
        # that holds the session.
        session = self._sessions.get(self._held_by.get(str(event.get("session_id") or ""), ""))
        if session is None:
            # Process-level messages, and events of sessions already released.
            return
        refused: list[int] = []
        if kind == "ended":
            # In hand from here on, before anything is awaited (the first
            # of the writes below may be the one that does not answer):
            # see ``_Session.ending``.
            session.ending = event
        elif kind == "error":
            # Out of the count from here on, likewise: stopped at a write
            # below as the host shuts down, what the runtime says next to
            # be taken would be taken for the text it refused.
            refused = _refusal(session, event)
        if session.untaken_answer is not None and _shows_work_taken(event, session.sent):
            session.untaken_answer = None
        if kind == "taken":
            await self._on_taken(session, event)
            return
        if kind != "ended" and _shows_run_at_work(event):
            # A run is under way, also one the session began by itself (a
            # background job of its own finished).
            session.idle = False
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
        try:
            await self._flush(session)
            await self._ledger.append_event(session.commission_id, str(kind), self._compact(event))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - what the event means is acted on all the same, see below
            if kind not in ("ended", "error"):
                raise
            # That a run or the session ended, or that the session refused
            # what it was sent, is not on record. It is acted on all the
            # same: left at that, what the session was handed would stay on
            # record as on its way with nobody to put it back, and a
            # session that is gone would keep its place.
            logger.warning("putting a runtime event (%s) on record failed; it is acted on all the same", kind, exc_info=True)
        if kind == "report":
            await self._on_report(session, event)
        elif kind == "ended":
            await self._on_ended(session, event)
        elif kind == "error":
            await self._on_error(session, event, refused)
        elif kind == "usage":
            await self._on_usage(session)
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
        """Act on the end of a run, or of the session; nothing is handed to the session while this looks.

        What a run's end means takes a few looks at the ledger to tell
        (is the commission done, does it rest, does it wait for task
        agents). Something steered into the session in between would
        start a run that this then closes, and be lost with it. Whether
        the session took more than the run that ended (``inputs`` against
        ``sent``) is read first thing, before anything is awaited, and
        from then until this returns ``_hand_to`` keeps back what waits.
        """
        session.settling = True
        try:
            # Whoever wrote to the session and asked to hear back is told
            # that the turn is over, and what came of it: once, by the host,
            # with no model asked. Of the notes this run took, which the
            # event bounds (the texts the session had taken when the run
            # ended): where something said meanwhile has begun another run,
            # that one tells of its own notes with its own words, and not
            # of these. (An end that names no number is the session's: of
            # all it took.)
            upto = _number(event.get("inputs"))
            said = str(event.get("text") or "")
            if upto is not None:
                # What the run took is not refused any more.
                _not_refused(session, upto)

            async def told() -> None:
                self._end_kept(session, event)
                # Not in hand any more from here on, for shutting down to
                # keep a second time: what is told next is kept where it
                # is cut off (``_turn_over``).
                session.ending = None
                await self._turn_over(session, said, upto)

            try:
                await self._act_on_end(session, event)
            except asyncio.CancelledError:
                if self._closed:
                    # Cut off as the host shuts down (whoever handles the
                    # events was stopped while the ledger was asked). The
                    # run is over and took what it took: kept, with its
                    # words, where shutting down writes it before the
                    # ledger is closed.
                    self._owe_turn(session, said, upto)
                raise
            except Exception:
                # What the end means could not be made out (the ledger was
                # not to be read, asked again as it was). The run is over
                # all the same, and took the notes it took: their senders
                # are told of it, with these words, now or once the ledger
                # can be written. Left to a later run, they would be told
                # with that run's words, or of nothing.
                await told()
                raise
            await told()
        finally:
            session.settling = False

    def _end_kept(self, session: _Session, event: dict[str, Any]) -> None:
        """Keep what the senders of a run's notes are told of it, for a text that is said to be taken after it.

        With how the commission stands as the run is over, and not as a
        later run leaves it. Of a run that names texts it leaves open
        (``open_inputs``): only of those can it be said later that the
        run took them. Kept for as long as one of them is still open
        here, however many runs come and go before it is heard of
        (``_ends_kept``); a run that names none keeps nothing.
        """
        named = event.get("open_inputs")
        left = frozenset(number for number in (named if isinstance(named, list) else ()) if _number(number) is not None)
        said = str(event.get("text") or "")
        session.ends.append((_number(event.get("run")), left, *self._told(said, session.stands)))
        _ends_kept(session)

    async def _act_on_end(self, session: _Session, event: dict[str, Any]) -> None:
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
            # Likewise what was steered into it for a wake request and never
            # seen taken: the request is pending again, and is handed over
            # anew (to the commission as it stands then).
            handed = [wake for wakes in session.handed.values() for wake in wakes]
            session.handed = {}
            if handed and not self._closed:
                # (Shutting down, they stay as they are: on record as on
                # their way, which the next process puts right.)
                await self._put_back(handed)
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
        commission = await self._looked(lambda: self._ledger.get(commission_id))
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
            if commission.status == "working":
                # A blocked child has reported already and its session is
                # gone; it produces nothing more to wait for.
                if await self._looked(lambda: self._ledger.select(parent_id=commission_id, statuses=_STILL_WORKING)):
                    # Task agents it started are still at work. Their results
                    # arrive as messages and start its next run; it is not done.
                    session.idle = not (isinstance(inputs, int) and inputs < session.sent)
                    if not session.idle:
                        # Something was sent while the ledger was asked: a
                        # run is on its way. This end is kept for where that
                        # text is refused, and the session idle after all.
                        session.held_end = event
                    return
                if await self._rests(session, commission, event):
                    # It asked to be woken (a time it set, word of a note it
                    # sent) and that is still to come: it is not done either.
                    return
                if isinstance(inputs, int) and inputs < session.sent:
                    # Something was said to the session while the ledger was
                    # asked (it may have been asked more than once): a run
                    # is under way again, and ended here it would be closed
                    # under that run. Its end decides, as where this was
                    # seen at the outset.
                    session.held_end = event
                    return
                # The run ended without a closing report: its last words are the result.
                text = str(event.get("text") or "")

                def went_on() -> bool:
                    return isinstance(inputs, int) and inputs < session.sent

                # (Writing it asks the ledger once more, and again where
                # that fails: looked at there too, for what is said to the
                # session in that while.)
                ended = await self._end(
                    commission_id,
                    "done",
                    reason="the run completed",
                    text=text,
                    result_summary=self._summary(text),
                    unless=went_on,
                )
                if not ended and went_on():
                    session.held_end = event
                    return
        elif reason == "aborted":
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
        # (How it ended is written by now. Should what it started not be
        # readable, even asked again, its session is let go all the same:
        # left open, it would keep its place for a commission that is over,
        # and the senders of its notes would hear nothing.)
        try:
            started = await self._looked(lambda: self._ledger.select(parent_id=commission_id, statuses=ACTIVE_STATUSES))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - its own end stands, and its session is closed
            logger.warning("ending what a commission that is over had started failed", exc_info=True)
            started = []
        for child in started:
            # Each by itself, and each step of it: one that cannot be ended
            # this moment does not leave the others at work, and one whose
            # end is written is still stopped where that write failed.
            for what, step in (
                ("ending", lambda child=child: self._ledger.transition(child.id, "cancelled", reason="its parent finished")),
                ("stopping", lambda child=child: self.abort(child.id)),
            ):
                try:
                    await self._looked(step)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - the other step, and the others, are still done
                    logger.warning("%s a task agent whose parent is over failed", what, exc_info=True)
        await self._close_session(session)

    @staticmethod
    async def _looked(look: Callable[[], Awaitable[Any]]) -> Any:
        """Read the ledger for what the end of a run means, asking again after a failure before giving up.

        The end of a run comes once. Given up on at the first failure to
        read, its commission would stay at work with nobody at it; the
        ledger is asked a few more times, a short while apart
        (``REST_LOOK_RETRY_DELAYS``), and only the last failure is the
        caller's.
        """
        from config.nekoclaw_settings import REST_LOOK_RETRY_DELAYS

        for delay in (*REST_LOOK_RETRY_DELAYS, None):
            try:
                return await look()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - asked again; the last failure is the caller's
                if delay is None:
                    raise
                logger.warning("reading the ledger at the end of a run failed; it is asked again", exc_info=True)
                await asyncio.sleep(delay)

    async def _rests(self, session: _Session, commission: Commission, event: dict[str, Any]) -> bool:
        """Let a commission whose run ended rest, if something it asked to be woken for is still to come.

        Returns whether it is left ``working``. Its session is closed in
        the runtime, which gives its place back and lets go of the session
        (``_settle_hold``); what it waits for takes it up again from what
        was said (``hand_over``). With background jobs of the session
        still running that tell the session when they finish, the session
        stays open instead, idle, and keeps its place: closing it would end
        them, and the one that finishes starts the next run by itself. Never a task agent another one
        started: its result is what the other waits for.
        """
        if commission.parent_id is not None:
            return False
        looked = self._looked
        try:
            # That the ledger cannot be read this moment does not say that
            # nothing waits: taken for that, the commission would end, and
            # a time it set would be dropped with it. Asked again first.
            pending = await looked(lambda: self._ledger.wakes_pending(commission_id=commission.id))
            if not pending:
                return False
            inputs = event.get("inputs")

            def went_on() -> bool:
                # Something was sent to the session since the run ended (the
                # user said more of it, a task agent handed a result back):
                # a run is under way again, and its end is the one to look at.
                return isinstance(inputs, int) and inputs < session.sent

            if went_on():
                # Its end decides. This one's is kept, as where it is seen
                # at the outset (``_act_on_end``): should what was sent be
                # refused, no such run comes, and this end is looked at
                # again (``_on_error``). Not kept, the commission would stay
                # at work with nobody at it, and keep its place.
                session.held_end = event
                return True
            # Jobs whose end starts the next run. One that is being stopped
            # tells nobody when it is gone, and closing the session does it
            # no harm: it does not keep the session open, and its place taken.
            if (_number(event.get("waking_jobs")) or 0) > 0:
                session.idle = True
                return True
            resting = await looked(lambda: self._ledger.rest(commission.id))
            if resting is None:
                # What it waited for came, or was dropped, this moment.
                return False
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - it ends as a run without a report does
            logger.warning("looking whether a commission rests failed", exc_info=True)
            return False
        if went_on():
            # Sent while the rest was being written down: the session is not
            # closed under the run that started, and the commission does not
            # rest, being at work (told otherwise, a query would say that it
            # rests, and a host started anew would leave it waiting). That
            # run's end looks again, and finds it resting or not as it
            # stands then.
            try:
                await self._ledger.rest_over(commission.id, resting.since)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the run goes on all the same; its end writes it anew
                logger.warning("taking back that a commission rests failed", exc_info=True)
            # (Kept for where what was sent is refused; see above.)
            session.held_end = event
            return True
        # (That it rests is no change of status, and is told to nobody.)
        session.stands = dataclasses.replace(session.stands or commission, resting_since=resting.since)
        await self._close_session(session)
        return True

    async def _put_back(self, wake_ids: Iterable[int], *, kick: bool = True) -> None:
        """Make wake requests pending again whose text did not get into a session.

        It went into one that closed before taking it, or it was not sent
        after all. Kept here until the ledger has it: where it cannot be
        written this moment, whoever asked goes on all the same, and it
        is tried again after a growing pause. (What is still kept when the
        host ends is on record as on its way, which the next process puts
        right.) ``kick`` False is for the round that is handing over
        itself: it says by what it returns when to look again.
        """
        self._unreturned.update(int(item) for item in wake_ids)
        if await self._return_unreturned(kick=kick):
            return
        if self._returning is None or self._returning.done():
            self._returning = self._spawn(self._return_later())

    async def _return_untaken(self, commission_id: str, by: float) -> None:
        """A commission is over: what was handed to it and never reached a session is not left on record as handed over.

        What was kept to be said to it when it got a session (it waited
        for a place, or was being started) is forgotten with it, and no
        session closes to give it back. The notes among it are pending
        again, for whatever their session is at next; a stop of everything
        that is ending the commission has them dropped instead. What a
        session of it was sent and may yet take stays with that session,
        which gives it back when it closes.

        ``by`` is the ledger's mark of what had been handed over when it
        ended (``Ledger.fired_mark``). Only what it was handed by then
        goes back: this may run a while later (the ledger could not be
        read), and the commission may be under way again by then, with
        notes of its own that are about to be said to it.
        """
        try:
            await self._confirm_taken()
            on_their_way = await self._ledger.wakes_on_their_way(commission_id, by)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - kept, and looked up again
            # No session closes to give them back, and nothing else looks
            # for them in this process: left at that, they would stay on
            # record as on their way until the next one.
            logger.warning(
                "looking for what a commission that is over never took failed; it is looked up again", exc_info=True
            )
            self._ended_unlooked[commission_id] = max(by, self._ended_unlooked.get(commission_id, by))
            if self._returning is None or self._returning.done():
                self._returning = self._spawn(self._return_later())
            return
        session = self._sessions.get(commission_id)
        held = {wake for wakes in session.handed.values() for wake in wakes} if session is not None else set()
        back = [wake for wake in on_their_way if wake not in held and wake not in self._unconfirmed]
        if back:
            # Their text is not kept to be said to this commission should it
            # be taken up again (what a start that failed left behind is):
            # they go to another commission from here.
            going = set(back)

            def stays(text: Any) -> bool:
                return not going.intersection(getattr(text, "wakes", ()))

            for kept in (self._notes, self._carried):
                if commission_id in kept:
                    kept[commission_id] = [text for text in kept[commission_id] if stays(text)]
            if commission_id in self._resume and not stays(self._resume[commission_id]):
                del self._resume[commission_id]
            await self._put_back(back)

    async def _return_unreturned(self, *, kick: bool = True) -> bool:
        """Write what is kept to be put back; returns whether nothing is left to write."""
        ids = sorted(self._unreturned)
        if not ids:
            return True
        try:
            await self._unfire(ids)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - tried again; the session is let go all the same
            logger.warning("putting back what a closed session did not take failed; it is tried again", exc_info=True)
            return False
        self._unreturned.difference_update(ids)
        if kick:
            self._kick_waker()
        return True

    async def _return_later(self) -> None:
        from nekoclaw.host.outbox import retry_delay

        failures = 1
        # Looked at again after every pause, and after the last write: what
        # was added while one of them was awaited is not left behind.
        while (
            self._unreturned or self._unconfirmed or self._turns_owed or self._ended_unlooked
        ) and not self._closed:
            await asyncio.sleep(retry_delay(failures))
            written = await self._return_unreturned()
            # The turns before what was taken since: a note a turn took is
            # told of with that turn, and with nothing a later one says.
            written = await self._tell_turns_owed() and written
            written = await self._confirm_taken() and written
            written = await self._look_up_ended() and written
            failures = 1 if written else failures + 1

    async def _settle_owed(self) -> None:
        """Write what turns that are over owe, and what their sessions took; neither raises."""
        await self._tell_turns_owed()
        await self._confirm_taken()

    async def _look_up_ended(self) -> bool:
        """Look once more for what commissions that are over never took; returns whether none is left to look up."""
        ended, self._ended_unlooked = self._ended_unlooked, {}
        for commission_id, by in sorted(ended.items()):
            # (Kept again by the look itself where it fails again.)
            await self._return_untaken(commission_id, by)
        return not self._ended_unlooked

    async def _tell_turns_owed(self) -> bool:
        """Write what the senders of notes are owed for turns that ended while the ledger could not be written."""
        owed, self._turns_owed = self._turns_owed, []
        for index, (commission_id, text, state, took, taken) in enumerate(owed):
            try:
                written = await self._ledger.turn_over(
                    commission_id, word=lambda _note, text=text: text, state=state, taken=taken, only=took
                )
                self._unconfirmed.difference_update(taken)
            except asyncio.CancelledError:
                self._turns_owed = [*owed[index:], *self._turns_owed]
                raise
            except Exception:  # noqa: BLE001 - kept, and tried again
                logger.warning("telling the senders of notes that a turn is over failed again", exc_info=True)
                self._turns_owed = [*owed[index:], *self._turns_owed]
                return False
            if written:
                self._kick_waker()
        return True

    async def _confirm_taken(self) -> bool:
        """Write what is kept to be put on record as taken; returns whether nothing is left to write."""
        ids = sorted(self._unconfirmed)
        if not ids:
            return True
        try:
            await self._ledger.wakes_taken(ids)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - kept, and tried again
            logger.warning("putting on record that a session took what it was handed failed; it is tried again", exc_info=True)
            return False
        self._unconfirmed.difference_update(ids)
        return True

    async def _on_taken(self, session: _Session, event: dict[str, Any]) -> None:
        """Note that a text sent to a session is in its conversation: what it brought is handed over for good."""
        number = _number(event.get("inputs"))
        if number is None:
            return
        # Taken, so it is not refused any more.
        _not_refused(session, number)
        wakes = session.handed.pop(number, None)
        if not wakes:
            return
        # What the end of the run that took it tells the senders of.
        session.took.setdefault(number, []).extend(wakes)
        # Kept until the ledger has it. Where it cannot be written this
        # moment it is tried again, and the end of the turn writes it with
        # what it tells the senders (``_turn_over``): a note the session
        # took is then not passed over as one it did not. (What is still
        # kept when the host ends is handed over once more by the next
        # process, at worst.)
        self._unconfirmed.update(wakes)
        ended = _ended_as(session, event, number) if event.get("late") is True else None
        if ended is not None:
            # The run that took it has ended, and told its senders nothing
            # of it: it was not known to be taken then. They are told now,
            # with what that run came to. Left to the end of the next run,
            # they would be told what that one says, which is about
            # something else.
            # Of this text alone: what the session took of other texts
            # meanwhile is of the run that took it, and is told as that
            # run ends.
            # And before the ledger is asked for anything else (that the
            # text was taken is written with what is told): stopped there
            # as the host shuts down, it is kept as owed with that run's
            # words, and not left with the session for whatever its last
            # end is told with.
            await self._turn_over(session, ended[0], number, as_told=ended, only=number)
        elif not await self._confirm_taken() and (self._returning is None or self._returning.done()):
            self._returning = self._spawn(self._return_later())
        _ends_kept(session)

    async def _unfire(self, wake_ids: list[int]) -> None:
        """Make wake requests pending again that were not handed on after all; what a stop dropped meanwhile stays dropped.

        A stop of everything drops the notes that wait. One that was on
        record as handed over just then, and was not taken, is not made
        pending behind the stop's back: written before the stop, it is
        dropped as the stop would have dropped it.
        """
        from nekoclaw.host.wakes import STOPPED_REASON

        stopped_upto = getattr(self._waker, "stopped_upto", None)
        await self._ledger.wakes_unfire(wake_ids, stopped_upto=stopped_upto, reason=STOPPED_REASON)

    @staticmethod
    def _told(said: str, commission: Commission | None) -> tuple[str, str]:
        """Return what the senders of the notes a turn took are told: what it ended on, and how its commission stood.

        What it ended on is its last words, or what was reported where
        it ended with none.
        """
        from config.nekoclaw_settings import SESSION_NOTICE_TEXT_MAX_TOKENS
        from utils.tokenize import truncate_to_tokens

        words = said.strip()
        if not words and commission is not None:
            words = (commission.result_summary or "").strip()
        words = truncate_to_tokens(words, SESSION_NOTICE_TEXT_MAX_TOKENS)
        if commission is None:
            return words, "over"
        return words, "resting" if commission.resting else commission.status

    def _owe_turn(self, session: _Session, said: str, upto: int | None) -> None:
        """Keep what the senders of the notes a run took are to be told, where it cannot be told now.

        For a run whose end was cut off as the host shuts down: written
        by ``_settle_at_close``, with how the commission stood as the
        ledger last told of it.
        """
        numbers = sorted(number for number in session.took if upto is None or number <= upto)
        took = [wake for number in numbers for wake in session.took.pop(number)]
        if took:
            self._turns_owed.append(
                (session.commission_id, *self._told(said, session.stands), took, sorted(self._unconfirmed))
            )

    async def _turn_over(
        self,
        session: _Session,
        said: str,
        upto: int | None = None,
        *,
        as_told: tuple[str, str] | None = None,
        only: int | None = None,
    ) -> None:
        """Tell the sessions that wrote to a commission and asked to hear back that its turn is over.

        With what the turn ended on (its last words, or, where it ended
        with a report and no words after it, what was reported) and how
        the commission stood then: done, resting, waiting for its user.
        Written to the ledger and handed over like any note; nothing waits
        for it here, and a failure tells nobody and costs the turn nothing.

        Of the notes this turn took, by the wake requests that brought
        them (``took``), and of no others: where the ledger cannot be
        written now, the turn is told of later, and by then the session
        may have taken the notes of another turn. ``upto`` is the number
        of the last text the session had taken when the run ended: what
        a later text brought is a later run's.
        """
        commission_id = session.commission_id
        # This turn's from here on: what the session takes while the ledger
        # is asked is the next one's. (``only``: of the one text that is
        # said to be taken late, and of none the session took besides.)
        if only is not None:
            numbers = [only] if only in session.took else []
        else:
            numbers = sorted(number for number in session.took if upto is None or number <= upto)
        took = [wake for number in numbers for wake in session.took.pop(number)]
        if not took:
            # It took no note: nobody waits to hear of it.
            return
        def told(commission: Commission | None) -> tuple[str, str]:
            # ``as_told``: what was kept of a run that is over, for a text
            # that is said to be taken after it. The commission is not
            # asked about again: it may stand otherwise by now.
            return as_told if as_told is not None else self._told(said, commission)

        def cut_off(commission: Commission | None) -> None:
            # Cut off before it was written: the host is shutting down, and
            # whoever handles the events was stopped. Kept where shutting
            # down writes it before the ledger is closed
            # (``_settle_at_close``): the session is let go by then, and
            # what was left with it would be told to nobody. (Written
            # after all by the thread that was at it, it is not told
            # twice: a note that was answered is passed over.) Cut off for
            # any other reason, it is the session's again, as if this had
            # not begun.
            if self._closed:
                self._turns_owed.append((commission_id, *told(commission), took, sorted(self._unconfirmed)))
            else:
                session.took[numbers[0]] = [*took, *session.took.get(numbers[0], [])]

        commission = session.stands
        if as_told is None:
            try:
                commission = await self._ledger.get(commission_id)
            except asyncio.CancelledError:
                cut_off(session.stands)
                raise
            except Exception:  # noqa: BLE001 - told with how it was last seen to stand
                # How it stands cannot be read this moment. What is told,
                # now or once the ledger can be written, is what this turn
                # came to as the ledger last told it here (it tells of
                # every change of status), and not what a later turn makes
                # of the commission.
                logger.warning("reading how a commission stands at the end of its turn failed", exc_info=True)
                commission = session.stands
        text, state = told(commission)
        try:
            # With what its session took and the ledger does not have yet.
            taken = sorted(self._unconfirmed)
            written = await self._ledger.turn_over(
                commission_id, word=lambda _note: text, state=state, taken=taken, only=took
            )
            self._unconfirmed.difference_update(taken)
        except asyncio.CancelledError:
            cut_off(commission)
            raise
        except Exception:  # noqa: BLE001 - kept, and told when the ledger can be written again
            logger.warning("telling the senders of notes that a turn is over failed; it is tried again", exc_info=True)
            self._turns_owed.append((commission_id, text, state, took, taken))
            if self._returning is None or self._returning.done():
                self._returning = self._spawn(self._return_later())
            return
        if written:
            self._kick_waker()

    async def _on_usage(self, session: _Session) -> None:
        """Count one model request; a commission whose quota is used up fails.

        The runtime reports usage once per request, after the request, so the
        request that uses the last unit still completes.
        """
        if self._meter is None or session.closing:
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
        self._note_quota(bool(allowed))
        if allowed:
            return
        await self._fail(commission_id, _QUOTA_REASON)
        for child in await self._ledger.select(parent_id=commission_id, statuses=ACTIVE_STATUSES):
            await self._ledger.transition(child.id, "cancelled", reason="its parent finished")
            await self.abort(child.id)
        await self._close_session(session)

    async def _on_error(self, session: _Session, event: dict[str, Any], refused: list[int]) -> None:
        """Act on a command the runtime refused; ``refused`` is what the text it refused had brought (``_refusal``)."""
        command = event.get("command")
        message = str(event.get("message") or "the runtime rejected the command")
        commission_id = session.commission_id
        if refused:
            # What the refused text was to hand over is pending again, and
            # the session is not tried again at once.
            from config.nekoclaw_settings import WAKE_HELD_RETRY_SECONDS

            session.refused_until = asyncio.get_running_loop().time() + WAKE_HELD_RETRY_SECONDS
            if not self._closed:
                await self._put_back(refused)
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
        try:
            await runtime.close(session.session_id)
        except BaseException:
            # Not asked for after all: whoever tries again is not turned
            # away as if it were on its way.
            session.closing = False
            raise

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
        unless: Callable[[], bool] | None = None,
        **values: Any,
    ) -> bool:
        """Move a commission to a final status and tell whoever commissioned it.

        A report to her is stored in the same transaction as the status
        change, so a crash in between cannot leave a finished commission
        that is never reported. A parent task agent is told after the change.
        Returns whether the change was made.

        ``unless`` is asked once the commission is read, which may take a
        while (it is asked again where it cannot be read): where it says
        so, nothing is changed. For the end of a run that leaves its
        commission done, which is not to end it under a run that began in
        that while.
        """
        # Asked again where it cannot be read this moment: given up on at
        # once, a run that is over would leave its commission at work.
        commission = await self._looked(lambda: self._ledger.get(commission_id))
        if commission is None:
            return False
        if unless is not None and unless():
            return False
        report = None
        if commission.parent_id is None and target != "cancelled":
            # Whoever stopped it knows; the task brain reports a stop when one is due.
            # The commission's own setting decides; the task agent's wish is the fallback.
            # One that nobody dispatched (it was filed for a note from
            # another session) is told with her next turn unless its task
            # agent asks otherwise: nobody is waiting to hear of it.
            unasked = "passive" if commission.source == "session" else "proactive"
            delivery = commission.delivery or (
                suggested_delivery if suggested_delivery in ("proactive", "passive", "silent") else unasked
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
        import secrets

        from config.nekoclaw_settings import TASK_AGENT_CHILD_RESULT_MAX_TOKENS
        from config.prompts import prompts_nekoclaw_sessions as words
        from utils.tokenize import truncate_to_tokens

        parent_id = str(commission.parent_id)
        # What another task agent wrote, like a note from another session:
        # the host says whose it is, and frames it so that nothing in it
        # reads as words of the host or of the user.
        nonce = secrets.token_hex(8)
        message = words.frame(
            words.CHILD_RESULT_FRAME.format(name=commission.name or commission.id, status=status, nonce=nonce),
            truncate_to_tokens(text, TASK_AGENT_CHILD_RESULT_MAX_TOKENS) or words.CHILD_RESULT_NOTHING,
            nonce,
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
        await self._wake_working(
            parent_id, message, reason="its idle session was closed; a result takes it up again"
        )

    async def _wake_working(self, parent_id: str, message: str, *, reason: str) -> bool:
        """Take up again a commission that is ``working`` with no session open in a runtime, ``message`` its next work.

        Such a commission waits for something: for task agents it started
        (``_wake_closed_parent``), or, resting, for what it asked to be
        woken for (``hand_over``). It is queued and started where its
        transcript ends, as soon as a place and its session are free.
        Returns whether the message is on its way to it: handed over, or
        going along with the work it is about to be given.
        """
        if self._closed:
            return False
        if parent_id in self._queue or parent_id in self._requeueing or parent_id in self._taking_up:
            # Already on its way back: this result goes along with it.
            self._notes.setdefault(parent_id, []).append(message)
            return True
        parent = await self._ledger.get(parent_id)
        if parent is None or parent.route != "task_agent" or parent.status != "working":
            return False
        if not await self._know_session(parent_id):
            return False
        session = self._sessions.get(parent_id)
        if session is not None and session.ready and self._runtime_of(session) is not None:
            # It came back, and was given its work, while the ledger was read.
            await self._send_text(self._runtime_of(session), session, "steer", message)
            return True
        if session is not None or parent_id in self._queue or parent_id in self._requeueing:
            # It is on its way back: this goes along with its work.
            self._notes.setdefault(parent_id, []).append(message)
            return True
        self._requeueing.add(parent_id)
        try:
            moved = await self._ledger.transition(parent_id, "queued", reason=reason)
        finally:
            self._requeueing.discard(parent_id)
        if not moved.applied:
            # Not queued after all (it was stopped meanwhile, say).
            self._settle_hold_of(parent_id)
            return False
        if self._closed:
            await self._ledger.transition(parent_id, "failed", reason=_SHUT_DOWN_REASON)
            return False
        self._resume[parent_id] = message
        if parent_id not in self._queue:
            self._queue.append(parent_id)
        if len(self._sessions) < self.limit:
            self._spawn(self._start_next())
        return True

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
        self._prepaid_starts.discard(commission_id)
        session = self._sessions.get(commission_id)
        if session is None:
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
            return await self._take_up_again(commission_id, text, still_wanted=still_wanted)
        if session is None and commission.resting:
            # It rests, waiting for something it asked to be woken for: the
            # user's words wake it instead, and are its next work. It is
            # under way all along, so a stop of everything finds it; what is
            # asked here is whether that stop came before this sentence did.
            if still_wanted is not None and not still_wanted():
                return False
            await self._ledger.append_event(commission_id, "told", {"text": text, "resumed": True})
            return await self._wake_working(commission_id, text, reason=_WOKEN_REASON)
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
        if (
            isinstance(path, str)
            and path
            and (
                category in ("outside_read", "outside_write")
                or (writes and category == "workdir_files")
                # A file among those the commission works on that has a
                # second name may be another session's record under that
                # name. (A link that leads elsewhere counts as outside the
                # working directory already.) So may a search (it reads
                # what is in a directory), and a file named like one a
                # session keeps of itself, by its own name or by what it
                # leads to: another session's own directory may lie in the
                # working directory, and its record is not read by way of
                # that either. Only these are looked at: the others are
                # read as ever, with nothing asked.
                or (
                    category == "workdir_files"
                    and (payload.get("tool") == "grep" or await asyncio.to_thread(_may_be_a_record, path))
                )
            )
        ):
            # What another session left is read without asking, and its own
            # directory is never written (``_rule_on_other_session``): not
            # from a working directory that contains it either, where the
            # write counts as one "inside the working directory".
            ruled = await self._rule_on_other_session(
                session_id,
                path,
                writes=writes or category == "outside_write",
                # ``glob`` tells names; every other tool that takes a
                # directory reads the files in it.
                names_only=payload.get("tool") == "glob",
                # Among the files the commission works on. Other sessions may
                # work in the same folder: that makes nothing in it another
                # session's, and no setting about reading elsewhere applies.
                inside=category == "workdir_files",
            )
            if ruled is not None:
                return ruled
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
        if session_id in self._held_by or session_id in self._clearing:
            return False
        in_hand = (*self._sessions, *self._queue, *self._taking_up, *self._requeueing)
        if any(self._session_of.get(commission_id) == session_id for commission_id in in_hand):
            return False
        self._clearing[session_id] = asyncio.Event()
        return True

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

    # -- other sessions and wakes -----------------------------------------
    #
    # Sessions write to one another, look into what another left, and ask
    # to be woken later. Every turn is some commission's all the same: a
    # place, the quota, permissions, the gateway token, reports and events
    # are a commission's, and nothing here runs a session outside one.
    #
    # * What a commission asked for itself (a time it set, word of a note
    #   it sent) is that commission's: handed to it alone, and dropped when
    #   it is over. While it waits for that with its run ended it rests
    #   (``_rests``), and is woken through ``_wake_working``.
    # * What is for a session (a note another session wrote) goes to the
    #   commission the session is at: steered into the one at work, waking
    #   the one that rests, kept back while one waits for the user. With no
    #   commission under way, one is filed for it in that session
    #   (``_file_for_notes``), which follows up the session's last.

    async def hand_over(
        self,
        session_id: str,
        owner: str | None,
        wakes: list[WakeRequest],
        *,
        still_wanted: Callable[[], bool] | None = None,
    ) -> Handover:
        """Hand a session what waits for it: the wake requests of one owner, due now. See ``Handover``.

        ``owner`` is the commission that asked for them, or None for what
        is for the session, whatever it is at. Called by ``SessionWakes``,
        one call at a time. ``still_wanted`` says whether a stop of
        everything has come since these were read: a commission is not
        filed after one.
        """
        from config.nekoclaw_settings import WAKE_HELD_RETRY_SECONDS

        ids = [wake.id for wake in wakes]
        if self._closed or not ids:
            return Handover("held", WAKE_HELD_RETRY_SECONDS)
        target = await self._ledger.get_session(session_id)
        if target is None:
            await self._ledger.wakes_void(ids, "its session is not on record")
            return Handover("dropped")
        if owner is not None:
            commission = await self._ledger.get(owner)
            if commission is None or commission.status in ENDED_STATUSES:
                # Dropped with the commission as a rule; these were written
                # as it ended.
                await self._ledger.wakes_void(ids, COMMISSION_OVER_REASON)
                return Handover("dropped")
            return await self._hand_to(commission, target, wakes, still_wanted)
        holder = self._held_by.get(session_id)
        commission = await self._ledger.get(holder) if holder is not None else None
        if commission is None or commission.status in ENDED_STATUSES:
            # Nobody holds the session, or one that is over and on its way
            # out: what the session is at is then the commission that rests
            # in it, or the one that waits for a place to start in it.
            waiting = next((item for item in self._queue if self._session_of.get(item) == session_id), None)
            commission = await self._ledger.get(waiting) if waiting is not None else None
            if commission is None or commission.status in ENDED_STATUSES:
                commission = await self._ledger.resting_in(session_id)
        if commission is not None:
            return await self._hand_to(commission, target, wakes, still_wanted)
        return await self._file_for_notes(target, wakes, still_wanted)

    async def _hand_to(
        self,
        commission: Commission,
        target: Session,
        wakes: list[WakeRequest],
        still_wanted: Callable[[], bool] | None = None,
    ) -> Handover:
        """Hand what waits for a session to the commission that is under way in it.

        In this order, so that nothing is handed on that is no longer to
        be, and nothing is lost that was. The requests are put on record
        as carried out first: only those still pending are (one that was
        dropped by a stop of everything, or with its commission, or in
        favour of word that came after all, is not among them), and one
        that is on record is not handed on a second time by a round that
        tries again. The text is made from those. Then, with nothing
        awaited in between, it is looked at once more whether the session
        can take it, and it is handed on. Where it cannot after all, the
        requests are pending again.
        """
        from config.nekoclaw_settings import WAKE_HELD_RETRY_SECONDS

        ids = [wake.id for wake in wakes]
        commission_id = commission.id
        if commission.status == "waiting_user":
            # Not in between its question and the answer, and not so that
            # it gets to work with its question open. It goes once the
            # commission no longer waits (``_on_commission_change``).
            return Handover("held", WAKE_HELD_RETRY_SECONDS)

        def stopped() -> bool:
            # Everything was stopped since these were read: the stop is
            # ending this commission, and nothing more is said to it.
            return still_wanted is not None and not still_wanted()

        def in_the_way() -> bool:
            # Its runtime session is on its way out, the end of its run is
            # being looked at (it may be closed to rest this moment), or it
            # has come to wait for the user. What is sent now would be
            # lost, or would set it to work with its question open.
            live = self._sessions.get(commission_id)
            return live is not None and (
                live.closing
                or live.settling
                or live.awaiting_prompt
                or (live.question is not None and not live.question.done())
                # It refused what it was last handed, a moment ago.
                or live.refused_until > asyncio.get_running_loop().time()
            )

        def way() -> str:
            # How it reaches the commission as things stand: steered into
            # its open session, along with the work it is about to be
            # given, or by taking the commission up again.
            live = self._sessions.get(commission_id)
            if live is not None and live.ready and self._runtime_of(live) is not None:
                return "steer"
            return "along" if self._in_hand(commission_id) else "wake"

        if stopped() or in_the_way():
            return Handover("held", _CLOSING_RETRY_SECONDS)
        chosen = way()
        session = self._sessions.get(commission_id)
        # Whether the switch and the quota were asked about a turn this starts.
        counted = False
        if chosen == "wake" and commission.status != "working":
            # Queued in the ledger and in nobody's hands here: not this
            # process's to start.
            return Handover("held", WAKE_HELD_RETRY_SECONDS)
        if chosen == "wake" or (session is not None and session.idle):
            # It starts a turn: in a session that is closed (the commission
            # rests, or waits for task agents it started), or in one that
            # is open with its run over (it waits for background jobs, or
            # for task agents). No turn is started with NekoClaw switched
            # off or the quota used up; its first request is counted here,
            # ahead of it.
            held = await self._may_start(commission_id)
            if held is not None:
                return held
            counted = True
        fired = await self._ledger.wakes_fire(ids, into=commission_id)
        if not fired:
            # Dropped while this looked: there is nothing to say.
            return Handover("dropped")
        try:
            text = await self._wake_text(
                target, [wake for wake in wakes if wake.id in fired], answerable=commission.parent_id is None
            )
            if not text:
                # What they were to hand over is gone from the record:
                # dropped with the reason, and not left as handed over, which
                # the next process would take for something on its way.
                await self._ledger.wakes_drop(fired, "what it was to hand over is gone from the record")
                return Handover("dropped")
            # Looked at after everything that was awaited; nothing is
            # awaited from here to where it is handed on.
            if stopped() or in_the_way() or way() != chosen or self._sessions.get(commission_id) is not session:
                await self._put_back(fired, kick=False)
                return Handover("held", _CLOSING_RETRY_SECONDS)
            if chosen == "steer" and session is not None and session.idle and not counted:
                # Its run ended while this was awaited (it waits for task
                # agents it started, say): steered in now, the text would
                # start a turn that the switch and the quota were not
                # asked about. Taken back; the next time it is asked first.
                await self._put_back(fired, kick=False)
                return Handover("held", _CLOSING_RETRY_SECONDS)
            if any(wake.reason == "message" and wake.id in fired for wake in wakes):
                # What another session wrote is about to stand in this
                # commission's conversation. A note carries nobody's
                # permissions, and that is not left to the model alone:
                # what the user allowed "for this session", or once for
                # the run, was allowed for the commission as the user knew
                # it, and is asked again for whatever it does from here on.
                # (A time the commission set for itself brings none of
                # another's words.)
                self._permissions.end_session(commission_id)
            if chosen == "steer":
                assert session is not None
                if commission_id in self._prepaid_starts:
                    # Counted above, for the turn this starts.
                    self._prepaid_starts.discard(commission_id)
                    session.prepaid_requests += 1
                # Before its next model request; an idle session is started
                # by it. The requests are on their way until the session has
                # the text in its conversation: should it close first, they
                # are put back.
                await self._send_text(self._runtime_of(session), session, "steer", _Handed(text, fired))
            elif chosen == "along":
                # Being started, or waiting for a place: it goes along with
                # the work it is about to be given.
                self._notes.setdefault(commission_id, []).append(_Handed(text, fired))
            elif not await self._wake_working(commission_id, _Handed(text, fired), reason=_WOKEN_REASON):
                # It rests, or waits for task agents with its session
                # closed, and could not be taken up this moment. (The
                # request that was counted for it stays counted, for when
                # it can be.)
                await self._put_back(fired, kick=False)
                return Handover("held", WAKE_HELD_RETRY_SECONDS)
        except BaseException:
            # Not handed on after all. Kept until the ledger has it: where
            # it cannot be written this moment either, a request that
            # stayed on record as handed over would be read by no round.
            await asyncio.shield(self._put_back(fired, kick=False))
            raise
        return Handover("handed")

    async def _may_start(self, key: str, *, mark: str | None = None) -> Handover | None:
        """Say why a turn must not be started for what waits right now, or None when it may.

        NekoClaw switched off, or no model to work with: it stays where it
        is and is tried again later. Otherwise the first model request of
        the turn is counted here, ahead of it: with the quota used up
        nothing is woken (the commission would fail on its way up, and
        what it waited for would be lost), and it is tried again when the
        quota has a request left, at the latest after the next midnight.

        ``mark`` is what the counted request is kept under, where that
        is not ``key``: one counted for a session with no commission yet
        (``_session_mark``).
        """
        from config.nekoclaw_settings import WAKE_HELD_RETRY_SECONDS

        mark = key if mark is None else mark
        available, _reason = await self.available()
        if not available:
            return Handover("held", WAKE_HELD_RETRY_SECONDS)
        if self._meter is None or mark in self._prepaid_starts:
            return None
        now = self._ledger._clock()
        if self._quota_out and now < self._quota_asked_again_at:
            # Known to be used up, and nothing was counted since.
            return Handover("held", self._quota_asked_again_at - now)
        try:
            allowed = await self._meter(key)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a meter that cannot count does not stop the work
            logger.warning("counting a task agent's model request failed", exc_info=True)
            return None
        # Refused again where it was known to be used up: the next asking
        # is a day on.
        self._note_quota(bool(allowed), asked_again=True)
        if not allowed:
            return Handover("held", self._quota_asked_again_at - now)
        self._prepaid_starts.add(mark)
        return None

    @staticmethod
    def _session_mark(session_id: str) -> str:
        """Return what a request counted for a session, with no commission to have it yet, is kept under.

        Not the session's id alone: a session and its first commission
        have one id, and that commission, taken up again meanwhile,
        would take the session's counted request for its own.
        """
        return f"session:{session_id}"

    async def _file_for_notes(
        self, target: Session, wakes: list[WakeRequest], still_wanted: Callable[[], bool] | None
    ) -> Handover:
        """File a commission in a session for the notes that wait for it, nothing being under way there.

        The commission follows up the last one of the session, is the
        character's the session works for, and is about the notes: it
        takes a place, counts against the quota, asks the user where it
        has to, and reports to that character like any other. Nothing of
        what the user allowed an earlier commission "for this session"
        comes with it: a note carries nobody's permissions.
        """
        from config.nekoclaw_settings import COMMISSION_TITLE_MAX_TOKENS, DISPATCH_TEXT_MAX_TOKENS, WAKE_HELD_RETRY_SECONDS
        from config.prompts.prompts_nekoclaw_sessions import SESSION_COMMISSION_TITLE
        from nekoclaw.host.task_brain import _texts
        from utils.tokenize import truncate_to_tokens

        ids = [wake.id for wake in wakes]
        messages = await self._ledger.messages_of(ids)
        notes = [messages[wake.id] for wake in wakes if wake.id in messages and messages[wake.id].kind == "note"]
        if not notes:
            # Only a note has a commission filed for it.
            await self._ledger.wakes_void(ids, COMMISSION_OVER_REASON)
            return Handover("dropped")
        if target.kind not in _ADDRESSED_KINDS or target.status == "cleared":
            reason = (
                "its session's record was removed" if target.kind in _ADDRESSED_KINDS else "its session is a helper's"
            )
            await self._ledger.wakes_void(ids, reason)
            return Handover("dropped")

        def stopped() -> bool:
            return still_wanted is not None and not still_wanted()

        if stopped():
            # A stop of everything is dropping these this moment.
            return Handover("held", WAKE_HELD_RETRY_SECONDS)
        counted_for = self._session_mark(target.id)
        held = await self._may_start(target.id, mark=counted_for)
        if held is not None:
            return held
        # A request was counted just now, for the session (not where no
        # meter counts, nor where the meter could not be asked). It stays
        # counted for the session until a commission is filed that has
        # it: where nothing is filed after all (the ledger cannot be read
        # just now, the notes were dropped), the next try does not count
        # a second one.
        last = await self._ledger.last_commission(target.id)
        sender = await self._ledger.get_session(notes[0].from_session)
        lang = (last.lang if last is not None else None) or "en"
        title = _texts(SESSION_COMMISSION_TITLE, lang).format(name=sender.name if sender is not None else "?")
        # Looked at once more, and nothing awaited from here to the write
        # that files it. Nothing held the session while this waited, and
        # what was said in an old one may have been removed meanwhile, or
        # is being removed this moment: a commission filed now would be at
        # work on the notes without the conversation they were written to.
        # (One that is filed is seen by a removal that begins after this.)
        current = await self._ledger.get_session(target.id)
        if current is None or current.status == "cleared":
            await self._ledger.wakes_void(ids, "its session's record was removed")
            return Handover("dropped")
        if target.id in self._clearing:
            # Dropped the next time, should the removal go through.
            return Handover("held", _CLOSING_RETRY_SECONDS)
        # Filed and given its notes together, or not at all: a commission
        # without its notes would wait for nobody, and the notes would have
        # another one filed for them.
        commission = await self._ledger.create_for_wakes(
            ids,
            wanted=still_wanted,
            lanlan_name=target.lanlan_name,
            title=truncate_to_tokens(title, COMMISSION_TITLE_MAX_TOKENS),
            dispatch_text=truncate_to_tokens(notes[0].body, DISPATCH_TEXT_MAX_TOKENS),
            source="session",
            route="task_agent",
            session_id=target.id,
            continues=last.id if last is not None else None,
            project=target.project,
            workdir=target.workdir,
            conversation_id=last.conversation_id if last is not None else None,
            lang=last.lang if last is not None else None,
            chain_depth=max(note.depth for note in notes),
        )
        if commission is None:
            # The notes were dropped while this looked (everything was
            # stopped): no commission is filed for nothing. Or it is wanted
            # no longer (the host is shutting down), and they stay on
            # record as they are. (The request that was counted stays
            # counted, for what the session is next handed.)
            return Handover("held", _CLOSING_RETRY_SECONDS) if stopped() else Handover("dropped")
        if stopped():
            # Everything was stopped while it was being filed, and the stop
            # looked for commissions before this one was there: it is
            # stopped here, as it would have been there, and its notes are
            # dropped as the stop dropped the others. Or the host is
            # shutting down: nothing is started in it, and the notes wait
            # on record for the next process.
            await self._put_back(ids, kick=False)
            await self._ledger.transition(commission.id, "cancelled", reason=_STOPPED_MEANWHILE_REASON)
            return Handover("dropped")
        if counted_for in self._prepaid_starts:
            # Only one that was counted, and is still the session's, is
            # the commission's first, counted.
            self._prepaid_starts.discard(counted_for)
            self._prepaid_starts.add(commission.id)
        await self.submit(commission)
        return Handover("handed")

    async def _whose(self, other: Session | None, lanlan_name: str) -> str:
        """Say whom a session works for, as a frame says it to a task agent that works for ``lanlan_name``."""
        from config.prompts import prompts_nekoclaw_sessions as words

        if other is None:
            return words.SESSION_WHOSE.format(lanlan_name="?")
        template = words.SESSION_WHOSE if other.lanlan_name == lanlan_name else words.SESSION_WHOSE_OTHER
        return template.format(lanlan_name=other.lanlan_name)

    async def _framed_note(self, note: Any, lanlan_name: str, *, answerable: bool) -> str:
        """Return a note from another session as its receiver reads it: framed, with a marker made for this once."""
        import secrets

        from config.prompts import prompts_nekoclaw_sessions as words

        nonce = secrets.token_hex(8)
        sender = await self._ledger.get_session(note.from_session)
        intro = (words.SESSION_NOTE_FRAME if answerable else words.SESSION_NOTE_FRAME_NO_ANSWER).format(
            name=sender.name if sender is not None else note.from_session,
            whose=await self._whose(sender, lanlan_name),
            # As ``list_sessions`` shows it. Should a session be called
            # just that, both are meant, and the task agent is told to
            # name the one by its whole id (``_session_named``).
            address=note.from_session[:8],
            nonce=nonce,
        )
        return words.frame(intro, note.body, nonce)

    async def _wake_text(self, target: Session, wakes: list[WakeRequest], *, answerable: bool) -> str:
        """Return what a session is told for the wake requests handed to it now, each thing in a frame of its own."""
        import secrets

        from config.nekoclaw_settings import SESSION_NOTICE_WAIT_SECONDS
        from config.prompts import prompts_nekoclaw_sessions as words

        messages = await self._ledger.messages_of([wake.id for wake in wakes])
        parts: list[str] = []
        for wake in wakes:
            nonce = secrets.token_hex(8)
            message = messages.get(wake.id)
            if wake.reason == "timer":
                intro = words.WAKE_TIMER_NOTICE.format(set_at=_clock_time(wake.created_at), nonce=nonce)
                parts.append(words.frame(intro, wake.note, nonce))
            elif wake.reason == "notice_expiry" and message is not None:
                other = await self._ledger.get_session(message.to_session)
                parts.append(
                    words.SESSION_EXPIRED_NOTICE.format(
                        name=other.name if other is not None else message.to_session,
                        hours=round(SESSION_NOTICE_WAIT_SECONDS / 3600),
                    )
                )
            elif wake.reason == "message" and message is not None and message.kind == "note":
                parts.append(await self._framed_note(message, target.lanlan_name, answerable=answerable))
            elif wake.reason == "message" and message is not None and message.kind == "done":
                other = await self._ledger.get_session(message.from_session)
                # How the commission stood when the turn ended, as it was
                # written down then: by now it may be at something else.
                intro = words.SESSION_DONE_FRAME.format(
                    name=other.name if other is not None else message.from_session,
                    state=wake.note or "over",
                    nonce=nonce,
                )
                parts.append(words.frame(intro, message.body or words.SESSION_DONE_NOTHING_SAID, nonce))
        return "\n\n".join(parts)

    async def _caller(self, session_id: str) -> Commission:
        """Return the commission a callback comes from; raises when there is none."""
        commission = await self._ledger.get(self._at_work_in(session_id))
        if commission is None or not commission.session_id:
            raise RuntimeError("unknown commission")
        return commission

    async def _session_named(self, name: str) -> Session:
        """Return the one session a task agent means by a name or an id; raises with what to do otherwise."""
        from config.prompts.prompts_nekoclaw_sessions import MESSAGE_REFUSALS

        found = await self._ledger.sessions_named(name)
        if not found:
            raise RuntimeError(MESSAGE_REFUSALS["unknown"].format(name=name))
        if len(found) > 1:
            ids = ", ".join(f"{item.id} ({item.name!r} of {item.lanlan_name})" for item in found[:5])
            raise RuntimeError(MESSAGE_REFUSALS["ambiguous"].format(name=name, ids=ids))
        return found[0]

    async def _state_of(self, session: Session) -> str:
        """Say where a session stands right now, as ``list_sessions`` tells it. No runtime is asked."""
        from config.prompts.prompts_nekoclaw_sessions import SESSION_STATE_WORDS as words

        holder = self._held_by.get(session.id)
        if holder is not None:
            commission = await self._ledger.get(holder)
            if commission is not None and commission.status == "waiting_user":
                return words["waiting"]
            if commission is not None and commission.status not in ENDED_STATUSES:
                live = self._sessions.get(holder)
                return words["at_work"] if live is None or live.ready else words["starting"]
        resting = await self._ledger.resting_in(session.id)
        if resting is not None:
            pending = await self._ledger.wakes_pending(commission_id=resting.id)
            if pending:
                return words["resting"].format(time=_clock_time(pending[0].due_at))
        return words["cleared"] if session.status == "cleared" else words["closed"]

    async def _cb_session_list(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        from config.nekoclaw_settings import (
            SESSION_LIST_COMMISSIONS,
            SESSION_LIST_LOOKED_AT,
            SESSION_LIST_ROW_MAX_TOKENS,
            SESSION_LIST_ROWS,
        )
        from config.prompts import prompts_nekoclaw_sessions as words
        from nekoclaw.host.selection import similarity
        from utils.tokenize import truncate_to_tokens

        caller = await self._caller(session_id)
        project = payload.get("project")
        # A task agent another one started is that one's business, as it is
        # in every list she is shown: it is reached through its lead. Left
        # out where the sessions are read, so that the many sessions of
        # such task agents take up none of the rows that are looked at.
        rows = await self._ledger.find_sessions(
            project=str(project) if project else None,
            kinds=_ADDRESSED_KINDS,
            limit=SESSION_LIST_LOOKED_AT,
            commissions_each=SESSION_LIST_COMMISSIONS,
        )
        query = " ".join(str(payload.get("query") or "").split())
        if query:
            folded = query.casefold()

            def score(row: SessionRow) -> float:
                texts = [row.session.name, row.session.project or "", *(item.title for item in row.commissions)]
                if any(folded in text.casefold() for text in texts):
                    return 1.0
                return max((similarity(query, text) for text in texts if text), default=0.0)

            scored = [(score(row), index, row) for index, row in enumerate(rows)]
            # The best match first; among equals, the one last in use.
            rows = [row for value, _index, row in sorted(scored, key=lambda item: (-item[0], item[1])) if value > 0]
        if not rows:
            return {"text": words.SESSION_LIST_NONE}
        pages = (len(rows) + SESSION_LIST_ROWS - 1) // SESSION_LIST_ROWS
        try:
            page = min(max(int(payload.get("page") or 1), 1), pages)
        except (TypeError, ValueError):
            page = 1
        header = words.SESSION_LIST_HEADER_FOUND if query else words.SESSION_LIST_HEADER
        lines = [header.format(page=page, pages=pages, total=len(rows))]
        for row in rows[(page - 1) * SESSION_LIST_ROWS : page * SESSION_LIST_ROWS]:
            session = row.session
            line = words.SESSION_LIST_ROW.format(
                name=session.name,
                id=session.id[:8],
                whose=await self._whose(session, caller.lanlan_name),
                project=session.project or words.SESSION_LIST_NO_PROJECT,
                state=await self._state_of(session),
                title=row.commissions[0].title if row.commissions else words.SESSION_LIST_NO_COMMISSION,
                active=_clock_time(session.last_active_at),
            )
            line = truncate_to_tokens(line, SESSION_LIST_ROW_MAX_TOKENS)
            lines.append(line + (words.SESSION_LIST_THIS if session.id == caller.session_id else ""))
        return {"text": "\n".join(lines)}

    async def _cb_session_read(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        import secrets

        from config import nekoclaw_settings as settings
        from config.prompts import prompts_nekoclaw_sessions as words
        from nekoclaw.host import records
        from utils.tokenize import truncate_to_tokens

        caller = await self._caller(session_id)
        if await self._permissions.forbidden("outside_read"):
            # What another session left lies outside this one's working
            # directory, whichever tool reads it: a user who forbade
            # reading there has forbidden this too.
            raise RuntimeError(DENIED_BY_SETTINGS)
        target = await self._session_named(str(payload.get("session") or ""))
        what = str(payload.get("what") or "results")
        nonce = secrets.token_hex(8)

        async def framed(of: str, body: str) -> str:
            intro = words.SESSION_RECORD_FRAME.format(
                name=target.name, whose=await self._whose(target, caller.lanlan_name), what=of, nonce=nonce
            )
            return words.frame(intro, body, nonce)

        if what == "results":
            blocks: list[str] = []
            for item in await self._ledger.select(session_id=target.id, limit=settings.SESSION_RESULTS_COMMISSIONS):
                standing = "resting" if item.resting else item.status
                lines = [f'"{item.title}" ({standing}, last changed {_clock_time(item.updated_at)})']
                outcome = item.result_summary or item.status_reason
                if outcome:
                    lines.append(f"Outcome: {outcome}")
                review = None
                if self._space is not None:
                    try:
                        review = await self._space.read_review(item.id)
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 - told without its review
                        logger.warning("reading the review of a commission for another session failed", exc_info=True)
                if review is not None and review.content.strip():
                    lines.append(f"Review: {review.content.strip()}")
                blocks.append("\n".join(lines))
            body = truncate_to_tokens("\n\n".join(blocks), settings.SESSION_RESULTS_MAX_TOKENS)
            return {"text": await framed(words.RECORD_WHAT_RESULTS, body or "(no commission was worked on in it)")}

        def place() -> Any:
            # Where what was said is, and whether the built-in runtime keeps it there.
            session_dir = self._session_dir_of(target)
            return session_dir / TRANSCRIPT_NAME, _in_builtin(session_dir), _in_dsh(session_dir)

        path, in_builtin, in_dsh = await asyncio.to_thread(place)
        if not in_builtin and in_dsh:
            return {"text": words.RECORD_IN_DSH}
        try:
            if what == "search":
                query = str(payload.get("query") or "")
                hits, total = await asyncio.to_thread(
                    records.search_record,
                    path,
                    query,
                    hits=settings.SESSION_SEARCH_HITS,
                    excerpt_tokens=settings.SESSION_SEARCH_EXCERPT_MAX_TOKENS,
                    max_bytes=settings.SESSION_RECORD_MAX_BYTES,
                )
                if not hits:
                    return {"text": words.RECORD_SEARCH_NONE}
                body = "\n".join(f"#{hit.position} {hit.label}: {hit.excerpt}" for hit in hits)
                of = words.RECORD_WHAT_SEARCH.format(query=" ".join(query.split()), shown=len(hits), total=total)
                tail = words.RECORD_SEARCH_NEXT + (" " + words.RECORD_SEARCH_MORE if total > len(hits) else "")
                return {"text": await framed(of, body) + "\n" + tail}
            at = payload.get("at")
            stretch = await asyncio.to_thread(
                records.read_record,
                path,
                int(at) if at is not None else None,
                segment_tokens=settings.SESSION_READ_SEGMENT_MAX_TOKENS,
                entry_tokens=settings.SESSION_READ_ENTRY_MAX_TOKENS,
                max_bytes=settings.SESSION_RECORD_MAX_BYTES,
            )
        except records.RecordError as exc:
            raise RuntimeError(str(exc)) from exc
        of = words.RECORD_WHAT_STRETCH.format(first=stretch.first, last=stretch.last, entries=stretch.entries)
        tail = (
            words.RECORD_NEXT.format(following=stretch.following) if stretch.following is not None else words.RECORD_END
        )
        return {"text": await framed(of, stretch.text) + "\n" + tail}

    async def _cb_session_send(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        import math

        from config import nekoclaw_settings as settings
        from config.prompts import prompts_nekoclaw_sessions as words
        from utils.tokenize import acount_tokens

        refusals = words.MESSAGE_REFUSALS
        caller = await self._caller(session_id)
        if caller.parent_id is not None:
            raise RuntimeError(refusals["from_child"])
        available, _reason = await self.available()
        if not available:
            raise RuntimeError(refusals["switched_off"])
        target = await self._session_named(str(payload.get("to") or ""))
        if target.id == caller.session_id:
            raise RuntimeError(refusals["self"])
        if target.kind not in _ADDRESSED_KINDS:
            raise RuntimeError(refusals["child"].format(name=target.name))
        if target.status == "cleared":
            raise RuntimeError(refusals["cleared"].format(name=target.name))
        body = str(payload.get("message") or "").strip()
        tokens = await acount_tokens(body)
        if tokens > settings.SESSION_MESSAGE_MAX_TOKENS:
            raise RuntimeError(refusals["too_long"].format(tokens=tokens, limit=settings.SESSION_MESSAGE_MAX_TOKENS))
        depth = int(caller.chain_depth or 0) + 1
        if depth > settings.SESSION_MESSAGE_MAX_DEPTH:
            raise RuntimeError(refusals["too_deep"])
        notify = bool(payload.get("notify_when_done"))
        if getattr(self._waker, "stopping", False):
            # Everything is being stopped, the caller with it. What it writes
            # now would be written after the stop has dropped what waits,
            # and be handed over once the stop is through: work the stop
            # knows nothing of. Looked at with nothing awaited before the
            # write: what was on its way to the ledger before the stop began
            # is dropped by the stop as it ends.
            raise RuntimeError(refusals["over"])
        message, refusal, wait = await self._ledger.message_add(
            from_session=str(caller.session_id),
            from_commission=caller.id,
            to_session=target.id,
            body=body,
            depth=depth,
            notify_when_done=notify,
            notice_after=settings.SESSION_NOTICE_WAIT_SECONDS,
            limits=MessageLimits(
                target_interval=settings.SESSION_MESSAGE_TARGET_INTERVAL_SECONDS,
                per_hour=settings.SESSION_MESSAGES_PER_HOUR,
                queue_max=settings.SESSION_MESSAGE_QUEUE_MAX,
                dedup_seconds=settings.SESSION_MESSAGE_DEDUP_SECONDS,
            ),
        )
        if message is None:
            raise RuntimeError(
                refusals[refusal].format(
                    name=target.name,
                    wait=math.ceil(wait),
                    interval=round(settings.SESSION_MESSAGE_TARGET_INTERVAL_SECONDS),
                    limit=settings.SESSION_MESSAGES_PER_HOUR,
                )
            )
        # (Whoever hands notes over is told by the ledger that one was
        # written: ``Ledger.subscribe_notes``.)
        holder = self._held_by.get(target.id)
        at = await self._ledger.get(holder) if holder is not None else None
        waiting = at is not None and at.status == "waiting_user"
        said = (words.MESSAGE_QUEUED_WAITING if waiting else words.MESSAGE_QUEUED).format(name=target.name)
        if notify:
            said += words.MESSAGE_QUEUED_NOTIFY.format(hours=round(settings.SESSION_NOTICE_WAIT_SECONDS / 3600))
        return {"message": said}

    async def _cb_wake_schedule(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        from config import nekoclaw_settings as settings
        from config.prompts import prompts_nekoclaw_sessions as words
        from utils.tokenize import truncate_to_tokens

        caller = await self._caller(session_id)
        if caller.parent_id is not None:
            raise RuntimeError(words.WAKE_REFUSALS["from_child"])
        try:
            asked = float(payload.get("delay_seconds") or 0)
        except (TypeError, ValueError):
            asked = 0.0
        delay = min(max(asked, settings.WAKE_MIN_DELAY_SECONDS), settings.WAKE_MAX_DELAY_SECONDS)
        note = truncate_to_tokens(" ".join(str(payload.get("note") or "").split()), settings.WAKE_NOTE_MAX_TOKENS)
        due_at = self._ledger._clock() + delay
        wake, refusal = await self._ledger.wake_add(
            session_id=str(caller.session_id),
            commission_id=caller.id,
            note=note,
            due_at=due_at,
            depth=int(caller.chain_depth or 0),
            pending_max=settings.WAKE_PENDING_MAX,
            daily_max=settings.WAKE_DAILY_MAX,
        )
        if wake is None:
            limit = settings.WAKE_PENDING_MAX if refusal == "too_many_pending" else settings.WAKE_DAILY_MAX
            raise RuntimeError(words.WAKE_REFUSALS[refusal].format(limit=limit))
        self._kick_waker()
        said = words.WAKE_SET.format(at=_clock_time(due_at), minutes=round(delay / 60))
        if asked < delay:
            said += words.WAKE_SET_MOVED.format(seconds=round(settings.WAKE_MIN_DELAY_SECONDS))
        elif asked > delay:
            said += words.WAKE_SET_CAPPED.format(days=round(settings.WAKE_MAX_DELAY_SECONDS / 86400))
        return {"message": said}

    async def _rule_on_other_session(
        self, commission_id: str, path: str, *, writes: bool, names_only: bool = False, inside: bool = False
    ) -> dict[str, Any] | None:
        """Rule on a path that lies in a place of another session; None when it does not, or when the usual rules apply.

        What another session left is read without asking: the files in
        its working directory, and what it kept in its own directory (the
        output of its tools). Only places the ledger has on record for a
        session count, so this opens no path the host does not know as a
        session's. Its record itself is not read as a file: it is long,
        and ``read_session`` hands it over a stretch at a time. The files
        a session keeps of itself are told by where they are (in its own
        directory, under their names) and by what a path leads to: a
        link under another name is the file it points at, and a file of
        the same name among the files it works on is a file like any
        other. A second name for a record is refused wherever it lies,
        among the files the caller itself works on too (``inside``: there
        that is all that is looked at, a folder several sessions work in
        being the caller's as much as theirs). Nor is it read
        by way of the directory it is in: a search
        of a directory that holds a session's own directory would open
        the record with the rest, so such a directory is not searched
        (unless ``names_only``: listing names reads no file); the
        directories inside it are. Nor is a directory searched that holds
        a record under a second name, wherever that directory is: a
        search reads through no link, but a second name is a file like
        any other to it. Reading outside working directories
        that the settings forbid stays forbidden. (A shell command that
        reads a record is not looked into, as no shell command is.)

        Nothing is written into another session's own directory, whatever
        the settings allow: a session's record is its own, also where a
        path that leads to it lies among the files it works on. A working
        directory that lies elsewhere (a project folder of the user's) is
        ruled on as any path outside the working directory is.
        """
        import os
        from pathlib import Path

        from config.prompts.prompts_nekoclaw_sessions import (
            SESSION_RECORD_READ_REFUSED,
            SESSION_RECORD_SEARCH_REFUSED,
            SESSION_RECORD_WRITE_REFUSED,
            SESSION_WRITE_REFUSED,
        )
        from config.nekoclaw_settings import SESSION_RECORD_ALIAS_SCAN_ENTRIES
        from nekoclaw.runtime.permissions import is_inside
        from nekoclaw.runtime.snapshot import SYSTEM_FILE

        own = self._session_of.get(commission_id)
        places = [item for item in await self._ledger.session_places() if item[0] != own]
        if not places:
            return None
        kept_files = (TRANSCRIPT_NAME, SYSTEM_FILE, DSH_SESSION_NAME)

        def look() -> tuple[bool, bool, bool, bool, bool]:
            # Whether the path is in another session's own directory,
            # whether it is in another session's working directory,
            # whether it is one of the files a session keeps of itself,
            # whether it is a directory with such files somewhere in it,
            # and whether it is a directory with a second name of such a
            # file somewhere in it.
            target = Path(path)
            in_own = any(session_dir and is_inside(target, Path(session_dir)) for _id, _workdir, session_dir in places)
            in_work = any(workdir and is_inside(target, Path(workdir)) for _id, workdir, _session_dir in places)
            record = False
            # A file with a second name (a hard link) may be a record under
            # another name, wherever it lies: among the files the caller
            # works on, too. One with a single name is what its path says,
            # and is not compared file by file.
            linked = _has_second_name(path)
            if in_own or in_work or linked:
                real = os.path.normcase(os.path.realpath(target))
                try:
                    found = os.stat(target) if linked else None
                except OSError:
                    found = None  # Gone since it was looked at: no record.
                for _id, _workdir, session_dir in places:
                    for name in kept_files if session_dir else ():
                        kept = Path(session_dir) / name
                        if real == os.path.normcase(os.path.realpath(kept)):
                            record = True
                        elif found is not None and not record:
                            try:
                                # The same file under a second name.
                                other = os.stat(kept)
                                record = (other.st_dev, other.st_ino) == (found.st_dev, found.st_ino)
                            except OSError:
                                pass  # The record is not there: not the same file.
            holds = (
                (in_own or in_work)
                and not names_only
                and target.is_dir()
                and any(session_dir and is_inside(Path(session_dir), target) for _id, _workdir, session_dir in places)
            )
            aliased = False
            if not names_only and target.is_dir():
                # Only a record that has a second name can lie anywhere
                # under one, and it has none unless somebody gave it one:
                # as a rule nothing is looked for, and no directory is
                # gone through.
                twice: set[tuple[int, int]] = set()
                for _id, _workdir, session_dir in places:
                    for name in kept_files if session_dir else ():
                        try:
                            kept = os.stat(Path(session_dir) / name)
                        except OSError:
                            continue  # Not there.
                        if kept.st_nlink > 1:
                            twice.add((kept.st_dev, kept.st_ino))
                if twice:
                    # (The sessions' own directories, where the records
                    # lie under their first names, are ruled on above.)
                    apart = {
                        os.path.normcase(os.path.abspath(session_dir))
                        for _id, _workdir, session_dir in places
                        if session_dir
                    }
                    aliased = _lies_in(target, twice, apart, SESSION_RECORD_ALIAS_SCAN_ENTRIES)
            return in_own, in_work, record, holds, aliased

        in_own, in_work, record, holds, aliased = await asyncio.to_thread(look)
        if writes:
            if in_own:
                return {"allow": False, "reason": SESSION_WRITE_REFUSED}
            return {"allow": False, "reason": SESSION_RECORD_WRITE_REFUSED} if record else None
        if inside or (not in_own and not in_work):
            # Among the caller's own files, or in no place of another
            # session: the usual rules, unless it is another session's
            # record (under a second name, or where that session's own
            # directory lies in the caller's working directory), or a
            # search of that directory, which would open the record with
            # the rest. A search of a directory further up is left alone:
            # that is the working directory itself, or a folder in it.
            if record:
                return {"allow": False, "reason": SESSION_RECORD_READ_REFUSED}
            if aliased or (inside and in_own and holds):
                return {"allow": False, "reason": SESSION_RECORD_SEARCH_REFUSED}
            return None
        if await self._permissions.forbidden("outside_read"):
            return PermissionDecision(False, DENIED_BY_SETTINGS).to_json()
        if record:
            return {"allow": False, "reason": SESSION_RECORD_READ_REFUSED}
        if holds or aliased:
            return {"allow": False, "reason": SESSION_RECORD_SEARCH_REFUSED}
        return PermissionDecision(True).to_json()

    # -- shutdown ---------------------------------------------------------

    async def shutdown(self) -> None:
        """Stop the runtime and every session. Commissions under way end as failed."""
        from config.nekoclaw_settings import TURNS_OWED_CLOSE_WAIT_SECONDS

        self._closed = True
        # Before the commissions under way are ended below: what their
        # sessions took is on record as taken by then, and is not handed
        # over a second time by the next process.
        await self._settle_at_close(TURNS_OWED_CLOSE_WAIT_SECONDS)
        queued = list(self._queue)
        self._queue.clear()

        async def recorded(what: str, step: Callable[[], Awaitable[Any]]) -> None:
            # How the commissions under way end is written to the ledger.
            # Where that fails (it cannot be read or written this moment)
            # the rest of shutting down is done all the same: the runtimes
            # are closed, the events handled, what is owed written. What
            # is left unwritten is as a process that went away leaves it,
            # and is put right by the next (``Ledger._reconcile``): what
            # was at work is failed, what rests is left to rest.
            try:
                await step()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - shutting down goes on regardless
                logger.warning("writing down %s as the host shuts down failed", what, exc_info=True)

        async def fail_queued() -> None:
            for commission_id in queued:
                self._resume.pop(commission_id, None)
                # Refused for one that was stopped meanwhile, and rightly so.
                await self._ledger.transition(commission_id, "failed", reason=_SHUT_DOWN_REASON)

        async def fail_open(commission_id: str) -> None:
            commission = await self._ledger.get(commission_id)
            if commission is not None and commission.resting:
                # It came to rest and its session is on its way out of the
                # runtime: nobody is at work on it. It rests on, and what it
                # waits for wakes it in the next process.
                return
            await self._ledger.transition(commission_id, "failed", reason=_SHUT_DOWN_REASON)

        async def fail_closed() -> None:
            # A parent whose idle session was closed while its task agents
            # worked holds no session and is in no queue, yet is this host's
            # and under way. (With no runtime ever started there is none, and
            # the ledger is not opened for nothing.)
            for item in await self._ledger.select(statuses={"working"}):
                if (
                    item.route == "task_agent"
                    and item.host_instance == self._ledger.instance
                    and item.id not in self._sessions
                    # One that rests is not under way in this process: what
                    # it waits for is on record and wakes it in the next.
                    and not item.resting
                ):
                    await self._ledger.transition(item.id, "failed", reason=_SHUT_DOWN_REASON)

        await recorded("that what was queued failed", fail_queued)
        if self._starting:
            # Sessions on their way in are let in, so they are closed with the rest.
            _done, pending = await asyncio.wait(list(self._starting), timeout=_SHUTDOWN_START_WAIT_SECONDS)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        # Recorded first: what the closing sessions still say must not turn a
        # commission nobody stopped into a stopped one. Each by itself: one
        # that cannot be read does not leave the others unrecorded.
        for commission_id in list(self._sessions):
            await recorded("that a commission under way failed", lambda commission_id=commission_id: fail_open(commission_id))
        if self._runtimes.started:
            await recorded("that what waited for task agents failed", fail_closed)
        for _name, runtime in self._runtimes.take_all():
            try:
                await runtime.shutdown()
            except Exception:  # noqa: BLE001 - shutting down goes on regardless
                logger.exception("shutting the runtime down failed")
        # What the runtimes said as they closed (the ends of the turns that
        # were under way among it) is handled before the sessions are let
        # go, a bounded while: let go first, the end of a turn would find
        # no session, and the senders of the notes it took no word.
        try:
            await asyncio.wait_for(self._events.join(), TURNS_OWED_CLOSE_WAIT_SECONDS)
        except asyncio.TimeoutError:
            logger.warning("what the runtimes said as they closed was not all handled in time as the host shuts down")
        pump, self._pump = self._pump, None
        if pump is not None:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
        # Before the sessions are let go: what they took and nobody was
        # told of is with them.
        self._owe_what_is_left()
        for commission_id in list(self._sessions):
            self._discard(commission_id)
        self._held_by.clear()
        self._unsubscribe()
        # And once more, now that no run ends any more: the sessions that
        # were closed above ended their turns meanwhile, and one whose word
        # to the senders of its notes could not be written then is owed
        # since. (Nothing else writes it: the loop that tries again ended
        # with the host.) The ledger is closed after this.
        await self._settle_at_close(TURNS_OWED_CLOSE_WAIT_SECONDS)

    def _owe_what_is_left(self) -> None:
        """Keep, as the host shuts down, what the senders of notes are still to be told of.

        Whoever handles the events is stopped by now. In the order it
        all came: first the end of a run that was being acted on when
        that stopped (the ledger did not answer), which is earlier than
        anything still in the queue; then what was not got to, looked at
        for this alone: that a text was taken, and what a run ended on;
        last every session whose run was never seen to end, which took
        what it took all the same. Out of that order, what the runtime
        said as it closed a session (an end with no words, of all the
        session took) would be told in place of what its run came to.
        All of it is kept where ``_settle_at_close`` writes it; a note
        that was taken is not handed over again by the next process, so
        its sender would otherwise hear nothing.
        """
        for session in list(self._sessions.values()):
            ending, session.ending = session.ending, None
            if ending is not None:
                self._owe_end(session, ending)
        while True:
            try:
                event = self._events.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._events.task_done()
            session = self._sessions.get(self._held_by.get(str(event.get("session_id") or ""), ""))
            if session is None:
                continue
            number = _number(event.get("inputs"))
            if event.get("type") == "error":
                # A text was refused: those sent after it move up by one,
                # as where this is acted on as it comes (``_handle``), so
                # that what is said to be taken next is taken for the right
                # one. What the refused text brought stays on record as on
                # its way, which the next process puts right.
                _refusal(session, event)
            elif event.get("type") == "taken" and number is not None:
                wakes = session.handed.pop(number, None)
                if wakes:
                    self._unconfirmed.update(wakes)
                    told = _ended_as(session, event, number) if event.get("late") is True else None
                    if told is not None:
                        # Taken by a run that is over: owed as that run was
                        # told of, and not left to the end of a later one.
                        self._turns_owed.append((session.commission_id, *told, wakes, sorted(self._unconfirmed)))
                    else:
                        session.took.setdefault(number, []).extend(wakes)
            elif event.get("type") == "ended":
                self._owe_end(session, event)
        for session in list(self._sessions.values()):
            # What it took in a run that was not seen to end: told with how
            # the commission stands.
            self._owe_turn(session, "", None)

    def _owe_end(self, session: _Session, event: dict[str, Any]) -> None:
        """Keep, as the host shuts down, what the end of a run tells the senders of the notes it took.

        And how it ended (``_end_kept``): a text that is said, in what
        was not got to after it, to have been taken by that run is owed
        as this run was told of, and not left to the end of a later one.
        """
        self._end_kept(session, event)
        self._owe_turn(session, str(event.get("text") or ""), _number(event.get("inputs")))

    async def _settle_at_close(self, within: float) -> None:
        """Write what turns that are over still owe as the host shuts down, for ``within`` seconds at the most."""
        loop = asyncio.get_running_loop()
        # One limit for both steps below.
        limit = loop.time() + within
        returning, self._returning = self._returning, None
        if returning is not None and not returning.done():
            # The loop that tries again ends with the host. Stopped here,
            # and waited for: this moment it may hold what a turn owes
            # (taken out of what is kept for as long as the ledger is
            # asked), which it puts back as it is cancelled. Left to
            # itself it would sit out its pause among what shutting down
            # waits for, and what it holds would be seen by nobody.
            returning.cancel()
            await asyncio.wait({returning}, timeout=max(0.0, limit - loop.time()))
        if self._turns_owed or self._unconfirmed:
            # Tried once more, a bounded while, with the ledger still open:
            # the notes were taken and are not handed over anew, so what
            # is not written now is told to nobody (a sender that still
            # waits is told, when its wait is up, that no word came).
            try:
                await asyncio.wait_for(self._settle_owed(), max(0.0, limit - loop.time()))
            except asyncio.TimeoutError:
                logger.warning("what turns that are over still owe could not be written in time as the host shuts down")
