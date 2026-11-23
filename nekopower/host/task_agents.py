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
(``nekopower.runtime``). It

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
  conversation of a session that has been idle for long (``_idle_compact``);
* lets a session of one commission (``single``) end once its last commission
  has been over for ``SESSION_SINGLE_LIFETIME_DAYS`` days: nothing gives it
  new work after that (``Ledger.session_alive``, asked wherever new work is
  handed to a session).

Status changes all go through ``Ledger.transition``. In particular a
commission that was stopped is ``cancelled`` before its session is told to
stop, so nothing the session still says can turn it into anything else.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import logging
import sqlite3
import uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

from nekopower.host import call_audit
from nekopower.host.capabilities import CapabilityRegistry
from nekopower.host.execution_audit import ExecutionAudit
from nekopower.host.ledger import (
    ACTIVE_STATUSES,
    COMMISSION_OVER_REASON,
    ENDED_STATUSES,
    PROJECT_KINDS,
    Commission,
    Ledger,
    MessageLimits,
    Session,
    SessionRow,
    WakeRequest,
    new_session_id,
)
from nekopower.host.paths import DEFAULT_PROJECT, DSH_SESSION_NAME, TRANSCRIPT_NAME, HostPaths
from nekopower.host.permissions import (
    CHOICES,
    DENIED_BY_SETTINGS,
    GrantScope,
    OPEN_SESSION,
    SPACE_COMMAND,
    USAGE_LIMIT,
    PermissionAsk,
    PermissionDecision,
    PermissionGate,
    SessionActionScope,
)
from nekopower.host.plugin_runs import PluginRunner, audited_run_entry
from nekopower.host.preferences import PreferenceStore, feedback_text
from nekopower.host.reports import CHANNEL_TASK_AGENT, Reporter
from nekopower.host.runtimes import RUNTIME_BUILTIN, RUNTIME_DSH, Runtimes
from nekopower import TASK_CONTROL_TOOLS

logger = logging.getLogger(__name__)

LEAD_NAME = "lead"
WEB_SEARCH_PLUGIN_ID = "web_search"
WEB_SEARCH_ENTRY_ID = "search"
_WEB_SEARCH_PARALLEL = 3
_SHUTDOWN_START_WAIT_SECONDS = 5.0
# How long a runtime gets to say that a session the host had open by itself
# is closed before that is noted in the log; it is waited for all the same
# while the host is up, and whether it still is is looked at this often.
_SERVICE_CLOSE_SECONDS = 15.0
_SERVICE_CLOSE_POLL_SECONDS = 1.0
# How long a commission waits for a session the host has open by itself to
# be given back, before it fails with that as its reason.
_SERVICE_HANDOVER_SECONDS = 30.0
# How often, and how far apart, the directory of a copy that came to nothing
# is tried again when it could not be removed and the host is shutting down.
_FORK_REMOVE_RETRIES = 3
_FORK_REMOVE_RETRY_SECONDS = 1.0
_SESSION_NOT_GIVEN_BACK = (
    "its session is still being closed after the host had it open to sum it up; "
    "it can be handed in again in a moment"
)
# The key of what is put among the runtimes' events to learn when they are
# caught up with (``_caught_up``), and how long that is waited for.
_REACHED = "nekopower/reached"
# What is to be done at that point, by whoever handles the events.
_THEN = "nekopower/then"
_CAUGHT_UP_SECONDS = 10.0
_SHUT_DOWN_REASON = "the host was shut down"
# She reads them when she asks how the commission went: they name no period,
# price or plan (``TASK_AGENT_FAILED_WHY``). The first is before a task agent
# was ever at work on the commission, the second once one was (in this run,
# or in an earlier one it is taken up again after).
_QUOTA_REASON = "the task agent's model ran out of quota"
_QUOTA_RUN_REASON = "the task agent's model ran out of quota partway through"
# Failures of the host's own that she is not woken for and that nothing shows.
_QUIET_FAILURES = ("quota", "quota_run")
_STOPPED_MEANWHILE_REASON = "stopped while it was being taken up again"
_WOKEN_REASON = "it rested; what it waited for takes it up again"
_ASK_NOT_HANDED_OVER_REASON = "the question could not be handed to the copy that was to answer it"
_ASK_NOT_ASKED_REASON = "not asked after all"
# Tools a task agent that another task agent started is not offered: it
# starts none itself, writes to its own team only (and asks other sessions
# nothing), and hands its result back when its part is done instead of
# waiting to be woken. The same holds for a copy of a session that answers a
# question (``_cb_session_ask``), which is started under the one that asked.
_CHILD_DISABLED_TOOLS = ("subagent", "message_session", "schedule_wake", "ask_session")
# How many copies of copies are followed back to the session they began with,
# to open a copy by that one's preset.
_COPY_CHAIN_MAX = 8
# Among how many of a commission's first events the preset it was started
# with is looked for: it is put on record right after the one that files it.
_PRESET_ON_RECORD_WITHIN = 8
# How long what is to be handed to a session waits for that session's
# runtime to have closed it, when it is closing this moment.
_CLOSING_RETRY_SECONDS = 1.0
# The kinds of session another session finds and writes to: the leader and
# the members of a project, and the session of a commission of no project.
# A session of any other kind is somebody's helper (a task agent another one
# started), reached through whoever it helps.
_ADDRESSED_KINDS = ("project", "line", "single")
# How many sessions' last requests are kept before those too old to matter
# are let go of (``TaskAgentManager._last_request_at``).
_LAST_REQUESTS_KEPT = 64
_USAGE_REASON = "it used as many tokens as one commission may, and the user allowed no more"
# What a note or a wake that waits for a session is dropped with when the
# session has ended (``Ledger.session_alive``); said to nobody, kept on record.
_SESSION_ENDED_REASON = "its session has ended"
# The kinds of session the host sums up while they are idle: all that can be
# handed a commission again (a session of one commission as well as the
# leader and the members of a project). Those of a task agent another one
# started, and copies, are never summed up: they are done with when their
# part is.
_IDLE_COMPACT_KINDS = frozenset({"project", "line", "single"})
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
# ``nekopower.runtime.protocol.ModelConfig``, or None when none is configured.
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


class ForkRefused(RuntimeError):
    """A session could not be made from another by copying it; the message says why."""


class LedgerLessonSink:
    """Keeps lessons as ``lesson`` events of the commission that filed them."""

    def __init__(self, ledger: Ledger) -> None:
        self._ledger = ledger

    async def submit(self, commission: Commission, lesson: dict[str, Any]) -> str | None:
        await self._ledger.append_event(commission.id, "lesson", dict(lesson))
        return "recorded"


@dataclass(frozen=True, slots=True)
class _SessionAction:
    record: call_audit.Call
    active: Callable[[], bool]
    handing_over: asyncio.Event | None = None

    def require(self) -> None:
        if not self.active():
            self.record.status = "denied"
            raise RuntimeError(DENIED_BY_SETTINGS)


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
    # Output permissions of commands still represented in this session’s job registry.
    job_sources: dict[str, tuple[Callable[[], bool], Callable[[], Awaitable[bool]]]] = field(default_factory=dict)
    # Text increments and tool output waiting to be written as one event row.
    text: list[str] = field(default_factory=list)
    text_size: int = 0
    updates: dict[str, list[str]] = field(default_factory=dict)
    update_names: dict[str, str] = field(default_factory=dict)
    # Locally allocated audit references for native todo/goal events of this run.
    control_calls: dict[str, tuple[call_audit.Call, str]] = field(default_factory=dict)
    # Input number -> pending audit and (for a stand-alone steer) its original envelope.
    answer_receipts: dict[int, tuple[call_audit.Call, _Unsaid | None]] = field(default_factory=dict)
    # Set while a host callback waits for the user's answer.
    question: asyncio.Future[dict[str, Any]] | None = None
    # Done the moment the answer to that question is in, before whoever
    # asked goes on. Set and taken away with the question it was asked
    # with (``_ask``): the answer to another one does not do it.
    at_answer: Callable[[], None] | None = None
    # Which question the commission waits with (its ``id`` in what the
    # ledger has of it), asked through a tool call or by a report: an
    # answer that was read against another one is not this one's.
    question_id: str = ""
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
    # Commissions copies of this session finished that a text steered in
    # tells of, by the number of that text: told once the session took it
    # (``_on_taken``), and told again otherwise (``_tell_of_copies``).
    copies_told: dict[int, list[str]] = field(default_factory=dict)
    # Those of them in a text it was withdrawn (refused, or never sent).
    copies_dropped: list[str] = field(default_factory=list)
    # Likewise the commissions handed back to it that a text tells of, each
    # with the copy that handed it back: told again where it is not taken.
    handed_told: dict[int, list[tuple[str, str]]] = field(default_factory=dict)
    handed_dropped: list[tuple[str, str]] = field(default_factory=list)
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
    # Whether what it sends goes with the built-in free key: only then are
    # its requests counted against the free daily quota (``_charge``).
    free: bool = False
    # Set once the task agent was told to hand over what it has, the user
    # having allowed no more to be used: how many model requests it has left
    # for that. None while it works as usual.
    wind_up_left: int | None = None
    # The run it was told in is over. What the runtime was told goes with
    # the run it was told for, so it is said again before the next request,
    # should the session be given more of the same round.
    wind_up_told_again: bool = True
    # The host ended the commission itself when those were used, and closes
    # the session: what the run says as it is stopped decides nothing.
    wound_up: bool = False
    # The session is a copy of a project's session that a commission of
    # that project is carried on in, the session itself being at another
    # one (``_carry_on_in_copy``): which session it was copied from.
    carries_on_for: str | None = None
    # The tool whose call in such a copy hands the commission back to the
    # session itself, once one was called; the requests the copy has left
    # to write down what it found, once it was told to (None before); and
    # what it wrote since.
    hand_back: str | None = None
    hand_back_left: int | None = None
    findings: list[str] = field(default_factory=list)
    # Set once it is handed back, with what is carried back: the session is
    # closed, and what its runs say as they end decides nothing; the
    # commission goes on in the session itself once the copy is closed.
    handing_back: str | None = None
    # Set once the leader of a project handed the commission over
    # (``_cb_hand_over``): to which session, what that one is called, and
    # the leader's brief. What the leader's runs say as they end decides
    # nothing from then on; the session is closed once its run is over, and
    # the commission goes on in the other one (``_handed_over``).
    # Then its preset, and whether it was made for this commission.
    handing_over: tuple[str, str, str, str, bool] | None = None


# How many ends of runs a session keeps at the most (``_Session.ends``):
# a backstop, far above what texts are ever open at once.
_ENDS_KEPT_MAX = 64


def _carried_on(commission: Commission) -> bool:
    """Whether a commission is of the kind that is carried on in a copy of its session while that one is held.

    One that she dispatched (or promised), and nothing else: not a task
    agent another one started, a question another session asked, or what
    another session wrote, which are at home in a session of their own.
    """
    return commission.parent_id is None and commission.source in ("dispatch", "promised")


def _forked_work_text(done: list[Commission], handed: list[tuple[Commission, str]]) -> str:
    """Return what a project's session is told of commissions copies of it finished (``done``) and handed back.

    Each with the user's words (``TASK_AGENT_FORKED_WORK_ASKED_MAX_TOKENS``),
    what came of it, and the copy it was worked on in, all within
    ``TASK_AGENT_FORKED_WORK_MAX_TOKENS``. Composed here, with no model asked.
    """
    from config import nekopower_settings as settings
    from config.prompts import prompts_nekopower_brain as texts
    from utils.tokenize import count_tokens, truncate_to_tokens

    count = len(done) + len(handed)
    if not count:
        return ""
    # Each within an even share of what is left once the words around them
    # are counted: every one that is put on record as told is told, and
    # none is cut off whole by the ones before it. (The line breaks between
    # them counted too, one token each.)
    share = max(
        0,
        settings.TASK_AGENT_FORKED_WORK_MAX_TOKENS
        - count_tokens(texts.TASK_AGENT_FORKED_WORK.format(items=""))
        - (count - 1),
    ) // count

    def words(item: Commission) -> str:
        return " ".join((item.user_text or item.dispatch_text or item.title).split())

    def line(template: str, item: Commission, result: str | None, **fields: str) -> str:
        # What it is, where, and how it ended are said whole; what is cut
        # to the share is the user's words (``TASK_AGENT_FORKED_WORK_ASKED_MAX_TOKENS``
        # at most, a third of what is left) and then what came of it.
        bare = template.format(asked="", **fields, **({"result": ""} if result is not None else {}))
        room = max(0, share - count_tokens(bare))
        asked = truncate_to_tokens(words(item), min(settings.TASK_AGENT_FORKED_WORK_ASKED_MAX_TOKENS, room // 3))
        if result is None:
            return template.format(asked=asked, **fields)
        left = max(0, room - count_tokens(asked))
        return template.format(asked=asked, result=truncate_to_tokens(result, left), **fields)

    lines = [
        # Named by the copy it was worked on in (as ``read_session`` takes it).
        line(
            texts.TASK_AGENT_FORKED_WORK_ITEM,
            item,
            " ".join((item.result_summary or item.status_reason or "").split()),
            outcome=texts.TASK_AGENT_FORKED_WORK_OUTCOMES.get(item.status, item.status),
            session=str(item.session_id or "")[:8],
        )
        for item in done
    ] + [line(texts.TASK_AGENT_FORKED_WORK_HANDED_ITEM, item, None, session=copy_id[:8]) for item, copy_id in handed]
    # (Cut once more to the share, for where a tokenizer counts the joined
    # words otherwise than their pieces: what is cut then is the end.)
    return texts.TASK_AGENT_FORKED_WORK.format(items="\n".join(truncate_to_tokens(text, share) for text in lines))


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
    told = session.copies_told.pop(number, None)
    if told:
        session.copies_dropped.extend(told)
    handed = session.handed_told.pop(number, None)
    if handed:
        session.handed_dropped.extend(handed)
    for pending in (session.handed, session.answer_receipts, session.copies_told, session.handed_told):
        for later in sorted(key for key in pending if key > number):
            pending[later - 1] = pending.pop(later)
    for key, value in session.numbered.items():
        if value > number:
            session.numbered[key] = value - 1
    session.sent -= 1
    return wakes


def _refused_answer(session: _Session, event: dict[str, Any]):
    if not session.answer_receipts or event.get("command") not in ("prompt", "steer", "follow_up"):
        return None
    serial = _number(event.get("serial"))
    number = session.numbered.get(serial) if serial is not None else session.sent
    return session.answer_receipts.pop(number, None)


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


class _Unsaid(str):
    """A text nobody said: what a model wrote, which the host hands on (the answer of a copy of another session).

    It goes the way every text for a commission goes, as a ``_Handed``
    does, and wherever it is sent at last it is sent as no word of the
    user's (``_send_text``): it carries nobody's permissions, and the run
    it starts is not one the user spoke in.
    """

    def __new__(cls, text: str, recipient: str) -> "_Unsaid":
        answer = super().__new__(cls, text)
        answer.recipient = recipient
        answer.retried = False
        return answer


class _Found(str):
    """What a copy of a project's session wrote down for a commission it hands back (``_handed_back``).

    A model wrote it, in that commission's own work: it is sent as no word
    of the user's and is not read for what the user wants of the devices.
    It is no answer from another session (``_Unsaid``): it goes the way any
    text of the commission goes.
    """


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

    from nekopower.runtime.snapshot import SYSTEM_FILE

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


def _lies_in(
    directory: Any, files: set[tuple[int, int]], apart: dict[str, set[tuple[int, int]]], limit: int
) -> bool:
    """Whether one of some files lies in a directory under any of its names, however deep. Blocking.

    The files are told by device and number, which is what a second name
    (a hard link) shares with the first. Read as a search of the
    directory reads it: through no link and no junction, and not into a
    directory a version control system keeps of itself (a file of such a
    name is a file like any other: a working tree of git has one). The
    directories in ``apart`` are not gone into: other sessions' own
    directories, which are ruled on by themselves. They are given by
    name, each name with which directories of that name they are, by
    device and number: a directory is told by what it is, however the
    path to it is written, and is asked of itself only where its name
    is one of theirs. (Names are compared as ``os.path.normcase``
    leaves them, which is not everywhere as the file system compares
    them: where it takes two ways of writing a name for one and the
    directory is met under the other, it is not known for one of theirs
    and is gone into. That refuses a search which need not be refused,
    and allows none. What the listing says of a directory's number is
    not gone by: at a mount point it is another, and on Windows it
    costs the call all the same.)
    What kind of entry it is, the directory tells; the entry itself is
    asked only where more must be known (which file it is), as that
    costs a call each. No more than ``limit`` entries are looked at: a
    directory that has more is taken to hold one of the files, as it
    cannot be told that it does not. So is one whose reading breaks off
    part of the way; one that cannot be opened at all is passed over, as
    a search passes over it. A directory is read an entry at a time, and
    no further than the limit: one with a great many entries is not read
    whole to find that it has too many.
    """
    import os

    # (The search's own test of what a link is, junctions included.)
    from nekopower.runtime.tools.search import VCS_DIRECTORIES, _entry_is_link

    looked = 0
    pending = [os.fspath(directory)]
    while pending:
        try:
            listing = os.scandir(pending.pop())
        except OSError:
            continue  # Not to be read: nor is it by a search.
        with listing:
            try:
                for entry in listing:
                    looked += 1
                    if looked > limit:
                        return True
                    try:
                        if _entry_is_link(entry):
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name in VCS_DIRECTORIES:
                                continue
                            named = apart.get(os.path.normcase(entry.name))
                            if named:
                                # Named like one that is set apart: asked
                                # of itself which directory it is.
                                found = os.stat(entry.path, follow_symlinks=False)
                                if (found.st_dev, found.st_ino) in named:
                                    continue
                            pending.append(entry.path)
                            continue
                        # Whether it has a second name, and which file it
                        # is, only the file itself tells on every system.
                        found = os.stat(entry.path, follow_symlinks=False)
                    except OSError:
                        continue
                    if found.st_nlink > 1 and (found.st_dev, found.st_ino) in files:
                        return True
            except OSError:
                # Broken off part of the way (a share that went away, say):
                # it cannot be told what the rest of it holds.
                return True
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
    its sessions is this one's (``nekopower.dsh.driver.SESSION_FILE``).
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
    from nekopower.runtime import SubprocessRuntime

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


class _UsageCut:
    """Where what a commission had used ends when the user allows it more.

    Begun the moment the answer is in (``begin``, with nothing
    awaited before). From then on no write of usage begins, so what
    the ledger has is what was written before the answer; and a mark
    is put where what the runtimes had said by then ends. What is
    handled before the mark was used before the answer: it is in the
    ledger, or owed. What comes after the mark is put down as owed
    as well for the time being (nothing is written), and noted
    apart: used after the answer, it is not part of what the new
    limit is counted from, and comes out of what was allowed.
    """

    def __init__(self, manager: "TaskAgentManager") -> None:
        self._manager = manager
        self._begun = False
        self._over = False
        self._reached: "asyncio.Future[None] | None" = None
        self._late: dict[Any, Any] | None = None

    def begin(self) -> None:
        """Put the end where things stand now; nothing is awaited. Once only."""
        if self._begun or self._over:
            return
        self._begun = True
        manager = self._manager
        manager._usage_reading += 1
        self._reached = manager._mark(self._after)
        if self._reached is None:
            # Nothing handles what the runtimes say: nothing is on its way.
            self._after()

    def _after(self) -> None:
        # From here on what is put down as owed was said after the answer.
        if self._late is None and not self._over:
            self._late = {}
            self._manager._usage_late.append(self._late)

    async def owed(self, root: str) -> int:
        """Return what the work of ``root`` used before the answer that the ledger does not have."""
        manager = self._manager
        # The writes that were under way end in the ledger, or as owed.
        await manager._usage_at_rest()
        await manager._reach(self._reached)
        # (Not reached in time: what is owed by now is what there is to go by.)
        self._after()
        # Both as they stand at one moment: what is owed, less what of it
        # came after the answer. Taken off for each session and commission
        # by itself, before whose work each is of is looked up, once:
        # looked up for the two apart, one of the two may fail, and what
        # came after the answer would be allowed on top.
        late = self._late or {}
        before: dict[Any, Any] = {}
        for key, owed in manager._usage_owed.items():
            after = late.get(key, (0, 0, 0, 0, 0))
            left = tuple(max(0, owed[at] - after[at]) for at in range(1, 5))
            if any(left):
                before[key] = (owed[0], *left)
        return await manager._owed_tokens(root, owed_at=before)

    def end(self) -> None:
        """Let usage be written again. Once only, and only if it was begun."""
        if self._over:
            return
        self._over = True
        if not self._begun:
            return
        manager = self._manager
        if self._late is not None:
            manager._usage_late[:] = [late for late in manager._usage_late if late is not self._late]
        manager._usage_read_over()


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
        model_slots: Any = None,
    ) -> None:
        self._meter = meter
        # ``nekopower.host.conversation.RecentConversation``, or None: then a
        # commission that opens a new session is handed no excerpt of what
        # was said in the main dialogue just before it.
        self._conversation = conversation
        # Whether NekoPower is switched on, as the task brain reads it; None
        # when nobody says. Asked before the host makes a model request of
        # its own (summing up an idle session), which no dispatch stands behind.
        self._switched_on = switched_on
        # ``nekopower.host.space.TaskAgentSpace``, or None: then task agents
        # start with nothing from earlier commissions and lessons stay in
        # the ledger.
        self._space = space
        # How many looks for the files a repository keeps for agents have
        # not come back yet (``_repository_documents``).
        self._document_scans = 0
        # ``nekopower.host.leases.BuiltinLeases``: who occupies the desktop and
        # the browser. A commission lets go of them when its run is over.
        self._leases = leases
        # ``nekopower.host.model_capabilities.ModelCapabilityStore``, or None:
        # then every model is taken to see pictures.
        self._model_capabilities = model_capabilities
        self._ledger = ledger
        self._preferences = preferences
        self._permissions = permissions
        self._execution_audit = ExecutionAudit(ledger)
        self._runner = runner
        self._registry = registry
        self._reporter = reporter
        self._paths = paths
        # The presets sessions are opened by: the built-in ones and the
        # user's template files (``templates.py``). Reads the disk; asked
        # in a thread.
        from nekopower.host.templates import TemplateStore

        self.templates = TemplateStore(paths.space)
        self._model_config = model_config
        # Which model the sessions of each preset are sent to
        # (``role_models.py``). ``model_slots`` are the slots of the model
        # panel (``role_models.PluginModelSlots``); None where there are none
        # to read: then every session is sent to ``model_config``.
        from nekopower.host.role_models import RoleModels, RoleModelStore

        # One settling of a session's model at a time (``_session_model``):
        # the start of a queued commission and the settling as it was queued
        # do not each write a model of their own.
        # With how many hold or wait for it: it goes once none does.
        self._settle_locks: dict[str, tuple[asyncio.Lock, int]] = {}
        # Commissions being settled before they are queued (``submit``),
        # which nothing that stops commissions finds anywhere else; and
        # those of them that were stopped meanwhile (``abort``).
        self._settling_for_queue: set[str] = set()
        self._stopped_while_settling: set[str] = set()
        self.role_models = RoleModels(
            store=RoleModelStore(paths.role_models_json),
            slots=model_slots,
            # Read through the manager each time: what it was given may be
            # replaced while the host runs.
            agent_config=lambda: self._model_config(),
            default_of=self.model_default_of,
        )
        self._gateway = gateway
        self._enabled = enabled
        self._max_concurrent = max_concurrent
        self._lessons: LessonSink = lessons or space or LedgerLessonSink(ledger)
        # The built-in runtime and, for a user who installed it, dsh
        # (``dsh`` is the host's ``nekopower.dsh.DshInstallation``, or None).
        # Each is created when a commission first runs in it.
        self._runtimes = Runtimes(
            on_event=lambda event: self._events.put_nowait(event),
            callbacks=self._callbacks,
            builtin_factory=runtime_factory,
            dsh=dsh,
            **({"dsh_factory": dsh_runtime_factory} if dsh_runtime_factory is not None else {}),
        )
        self._sessions: dict[str, _Session] = {}
        # Direct /runs may ask without starting a model runtime or taking a session slot.
        self._direct_questions: dict[str, _Session] = {}
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
        self._unused_ask_copies: dict[str, Session] = {}
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
        self._search_rulings: dict[str, tuple[tuple[str, ...], PermissionDecision, float]] = {}
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
        # ``nekopower.host.wakes.SessionWakes``, or None: told when there is
        # something new to hand over, or when what was held may go now.
        self._waker: Any = None
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
        # says when; those being summed up right now, each with its task.
        self._idle_timers: dict[str, asyncio.TimerHandle] = {}
        self._compacting: dict[str, asyncio.Task[Any]] = {}
        # How many times in a row the host asked a session to sum itself up
        # and got no whole summary (``TASK_AGENT_IDLE_COMPACT_MAX_FAILURES``).
        # In memory only: a host that is started again counts from none.
        self._idle_compact_failures: dict[str, int] = {}
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
        # Held from when the names of a team are read for the name of a task
        # agent its lead starts, or of a question it asks, until that one is
        # filed (``_cb_subagent_start``, ``_cb_session_ask``): two started
        # at once are not given the same name.
        self._naming_members = asyncio.Lock()
        # Copies whose making was given up (the runtime took too long,
        # whoever asked for it left, the host shuts down), each with the
        # directory it was to be written into.
        self._forks_given_up: dict[str, str] = {}
        # Copies the runtime was asked for that are still waited for,
        # each with the directory it is written into.
        self._fork_dirs: dict[str, str] = {}
        # Copies the runtime has written that are being put on record.
        # Not among the tasks shutting down gives up on: each is carried
        # through, to a copy that is on record or to one that is removed.
        self._fork_records: set[asyncio.Task[Any]] = set()
        # What requests used that the ledger did not take when it was to be
        # written, by session and commission (requests, input, output,
        # cache reads, cache writes, and how long the conversation was as
        # of the last of them that said so): written with the next one,
        # when the session is let go of, or when the host shuts down.
        # The last two: that length, and when it was said (``_context_said``).
        self._usage_owed: dict[tuple[str, str | None], tuple[int, int, int, int, int, int | None, int]] = {}
        # Writes of usage under way. Not among the tasks shutting down
        # gives up on: what such a write comes to (written, or owed) is
        # for it alone to tell.
        self._usage_writes: set[asyncio.Task[Any]] = set()
        # How many are reading the account of a commission right now: no
        # write of usage begins meanwhile (``_usage_read``), and which
        # were put off for it. And where what is put off is noted apart
        # (``_UsageCut``).
        self._usage_reading = 0
        self._usage_put_off: set[tuple[str, str | None]] = set()
        self._usage_late: list[dict[Any, Any]] = []
        # Commissions whose task agent was told to hand over what it has,
        # each with the requests it has left for that: kept apart from
        # the session it was told in, which an idle runtime closes while
        # the task agent waits for an answer. The session that is opened
        # for the answer goes on with these, and is told again.
        self._winding_up: dict[str, int] = {}
        # A count that goes up with every request that says how long its
        # session's conversation is, and for each session the count of
        # the one the ledger has: what an older request said of it,
        # written late, does not take the place of what a newer one did.
        self._context_said = 0
        self._context_written: dict[str, int] = {}
        # One write of a session's usage at a time: whether a length is
        # the newest is looked at, the ledger written and the count
        # noted with nothing of the same session in between.
        self._usage_locks: dict[str, asyncio.Lock] = {}
        # When each session last made a model request (the loop's clock):
        # whether a provider most likely still has the head of its requests
        # in its cache, for a copy of it (``_carry_on_in_copy``).
        self._last_request_at: dict[str, float] = {}
        # Sessions a copy is being made of right now for commissions that
        # are to be carried on in it, each with those commissions.
        self._copying_for: dict[str, set[str]] = {}
        # For a commission whose opening message tells its session what
        # copies of it finished: those commissions (``_copies_said``).
        self._forks_said: dict[str, list[str]] = {}
        # What the message that opens a commission in a leader's session
        # tells of the commissions the leader handed over, by the commission
        # it opens: on record as told once the session took the message
        # (``_note_opened``), under a name of its own (``told_to_leader``).
        self._handed_said: dict[str, list[tuple[str, float | None]]] = {}
        # What tells whether a commission is a side question
        # (``LexicalDecisionModel.side_question``), and, for a commission it
        # was asked of, what it was asked about and what it said: asked once
        # for the same words (``_is_side_question``).
        self._side_question_judge: Callable[[str, str, str], Awaitable[bool | None]] | None = None
        # Where her commissions go, and the members and leaders of projects.
        from nekopower.host.families import Families

        self.families = Families(ledger, space, paths, clock=ledger._clock)
        # By session: the project (for which character) it writes files in.
        # One such session works in a project at a time (``_session_free_for``).
        self._writes_in: dict[str, tuple[str, str]] = {}
        # What the user said to a commission while its leader was handing
        # it over, by commission: from the leader's call until the
        # commission is queued for the task agent it goes to, which is
        # told it then, in the user's words (``_handed_over``). Kept
        # apart from the leader's session, which is let go of in between.
        self._said_while_handed: dict[str, list[str]] = {}
        self._side_said: dict[str, tuple[str, tuple[str, ...], bool]] = {}
        # Project sessions that copies of them have news for (a commission
        # they finished, or handed back), to be told while at work
        # (``_tell_of_copies``); those being told this moment; and, for
        # each, the commissions handed back to it that it was not told of.
        self._copy_news: set[str] = set()
        self._telling_of_copies: set[str] = set()
        # Those finished commissions that a text on its way to their
        # session tells of: not told again meanwhile.
        self._copies_on_the_way: set[str] = set()
        self._handed_back_news: dict[str, list[tuple[str, str]]] = {}
        # Sessions a commission a copy handed back is being queued for this
        # moment, or that a commission is being put on record with a copy
        # of, each with how many: nothing else starts in them meanwhile.
        self._handing_back_to: dict[str, int] = {}
        self._starting: set[asyncio.Task[Any]] = set()
        self._control_settlements: set[asyncio.Task[None]] = set()
        self._read_corrections: dict[str, tuple[call_audit.Call, str, str | None]] = {}
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._pump: asyncio.Task[None] | None = None
        self._closed = False
        permissions.set_asker(self.ask_permission)
        # A task agent started by another is covered by what the user allowed
        # for that one's session.
        permissions.set_parent_lookup(self._ledger.permission_parent)
        # A commission that holds a session without having it open (it waits
        # for an answer) can end without a word from a runtime: stopped, say.
        self._unsubscribe = ledger.subscribe(self._on_commission_change)

    # -- state ------------------------------------------------------------

    @property
    def limit(self) -> int:
        if self._max_concurrent is not None:
            return self._max_concurrent
        from config.nekopower_settings import TASK_AGENT_MAX_CONCURRENT

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

    async def available(self, session: Session | None = None) -> tuple[bool, str]:
        """Return whether a task agent could be started, and if not, why.

        In ``session`` when one is given: by the model it is sent to
        (``session_model``, which is looked at, not settled). Otherwise in
        a new session of a commission she hands over, one of no project
        (preset ``general``) or the lead of a project (``boss``), or in a
        session there is that she may go on with: one settled on a slot
        that has a model.
        """
        off = self._switched_off()
        if off:
            return False, off
        try:
            if session is not None:
                config = (await self.session_model(session, record=False)).config
            else:
                config = None
                for candidate in (
                    self._new_session_model, lambda: self._new_session_model("boss"), self._settled_model
                ):
                    # One that cannot be read does not keep the others from being asked.
                    try:
                        found = await candidate()
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - the next one is asked
                        logger.warning("a model task agents could go to cannot be read: %s", type(exc).__name__)
                        continue
                    if found is not None and found.config and found.config.get("model"):
                        config = found.config
                        break
        except Exception as exc:  # noqa: BLE001 - no usable configuration
            logger.warning("the task agent model configuration cannot be read: %s", type(exc).__name__)
            return False, "not_enabled"
        if not config or not config.get("model"):
            return False, "not_enabled"
        return True, ""

    def _switched_off(self) -> str:
        """Say why no task agent may be started whatever its model (``available``); "" when one may."""
        if self._closed:
            return "disabled"
        if self._enabled is not None:
            try:
                if not self._enabled():
                    return "not_enabled"
            except Exception:  # noqa: BLE001 - an unreadable switch counts as off
                return "not_enabled"
        return ""

    async def _settled_model(self) -> Any:
        """Return the model a session settled on a slot would be opened with now; None when there is none.

        By the rule such a session is opened by (``RoleModels.again``): a
        slot pointed elsewhere since does not count.
        """
        from nekopower.host.role_models import SlotUnavailable

        for slot, ident, tier in await self._ledger.settled_slots():
            try:
                choice = await self.role_models.again(slot, ident, tier)
            except SlotUnavailable:
                continue
            if choice.config and choice.config.get("model"):
                return choice
        return None

    async def _new_session_model(self, preset: str = "general") -> Any:
        """Return the model a new session of hers that no session started would be sent to (preset ``general`` unless named)."""

        async def nobody() -> Any:
            return None

        return await self.role_models.choose(preset, None, nobody)

    # -- runtime ----------------------------------------------------------

    def _callbacks(self) -> Any:
        from nekopower.runtime import HostCallbacks

        return HostCallbacks(
            decide_permission=self._cb_permission,
            execution_action=self._cb_execution_action,
            native_execution_action=self._cb_native_execution_action,
            task_control_begin=self._begin_task_control,
            ask_user=self._cb_ask_user,
            report=self._cb_report,
            note_lesson=self._session_action("note_lesson", self._cb_note_lesson),
            web_search=self._cb_web_search,
            subagent_start=self._cb_subagent_start,
            subagent_list=self._cb_subagent_list,
            subagent_send=self._cb_subagent_send,
            subagent_interrupt=self._cb_subagent_interrupt,
            session_list=self._session_action("list_sessions", self._cb_session_list),
            session_read=self._session_action("read_session", self._cb_session_read),
            session_send=self._session_action("message_session", self._cb_session_send),
            wake_schedule=self._session_action("schedule_wake", self._cb_wake_schedule),
            session_ask=self._session_action("ask_session", self._cb_session_ask),
            hand_over=self._cb_hand_over,
            budget=self._cb_budget,
        )

    async def _begin_task_control(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Admit a native local-state operation. Its terminal runtime event settles the same row."""
        commission_id = self._at_work_in(session_id)
        session = self._sessions.get(commission_id)
        name, key = payload.get("tool"), payload.get("call_id")
        if session is None or not isinstance(name, str) or name not in TASK_CONTROL_TOOLS or not isinstance(key, str) or not key:
            return {"allow": False}
        live = self._permissions.current_run(commission_id)
        try:
            record = await call_audit.start_call(self._ledger, commission_id, "task_control", name)
        except (call_audit.AuditFull, call_audit.CommissionEnded) as exc:
            return {"allow": False, "reason": str(exc)}
        scope = SessionActionScope("runtime:task-control", name, commission_id)
        grant = None
        admitted = False
        outcome = "denied"
        try:
            decision = await self._permissions.check(commission_id, "session_action", scope=scope, hold_once=True)
            grant = decision.grant_id
            record.grant_id = self._permissions.authorization_ref(grant)
            if (
                payload.get("refused") or not grant or not self._permissions.grant_active(grant)
                or not live() or self._sessions.get(commission_id) is not session or session.closing
                or self._at_work_in(session_id) != commission_id or key in session.control_calls
            ):
                return {"allow": False}
            session.control_calls[key] = (record, grant)
            admitted = True
            return {"allow": True}
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except Exception:
            outcome = "failed"
            raise
        finally:
            if not admitted:
                self._permissions.release_grant(grant)
                await call_audit.finish_call(record, outcome)

    def _session_action(self, tool: str, callback):
        """Capture the caller before SQLite yields. A note does not carry this receipt."""
        async def invoke(session_id: str, payload: dict[str, Any]):
            commission_id = self._at_work_in(session_id)
            live = self._permissions.current_run(commission_id)

            async def perform(action: _SessionAction):
                if self._at_work_in(session_id) != commission_id:
                    action.record.status = "denied"
                    raise RuntimeError("the originating run has ended")
                return await callback(session_id, payload, action)

            if tool != "ask_session":
                return await self._perform_action(commission_id, tool, perform, live)
            # Once filing has started, the handoff owns both the loan and the audit.
            # Losing the ACP waiter must neither cancel it nor keep the waiter blocked.
            handing_over = asyncio.Event()
            work = self._spawn(self._perform_action(commission_id, tool, perform, live, handing_over))
            try:
                return await asyncio.shield(work)
            except asyncio.CancelledError:
                if not handing_over.is_set():
                    work.cancel()
                work.add_done_callback(lambda done: None if done.cancelled() else done.exception())
                raise

        return invoke

    async def _correct_undelivered_read(self, record: call_audit.Call, status: str, reason: str | None = None) -> None:
        # A cancelled caller must still observe cancellation if SQLite is temporarily
        # unavailable. Keep only the receipt and the intended terminal status.
        correction = self._read_corrections.setdefault(record.call_id, (record, status, reason))
        try:
            await record.withhold_read(correction[1], correction[2])
        except (sqlite3.Error, OSError, RuntimeError) as exc:
            logger.warning("could not correct undelivered read %s (%s)", record.call_id, type(exc).__name__)
        else:
            self._read_corrections.pop(record.call_id, None)

    async def _perform_action(
        self, commission_id: str, tool: str, perform, live: Callable[[], bool],
        handing_over: asyncio.Event | None = None,
    ):
        if self._read_corrections:
            await self._correct_undelivered_read(*next(iter(self._read_corrections.values())))
        category = "outside_read" if tool in ("ask_session", "read_session") else "session_action"
        kind = "working_memory" if tool in ("space_brief", "note_lesson") else "session_action"
        scope = SessionActionScope("host:session", tool, commission_id)
        reads = tool in ("list_sessions", "read_session", "space_brief")
        record = None
        grant = None
        try:
            async with call_audit.call(self._ledger, commission_id, kind, tool) as record:
                if not live():
                    record.status = "denied"
                    raise RuntimeError("the originating run has ended")
                decision = await self._permissions.check(commission_id, category, scope=scope, hold_once=True)
                grant = decision.grant_id
                record.grant_id = self._permissions.authorization_ref(grant)
                if not (decision.allow and self._permissions.grant_active(grant) and live()):
                    record.status = "denied"
                    raise RuntimeError(decision.reason or "the originating run has ended")
                try:
                    action = _SessionAction(record, lambda: live() and self._permissions.grant_active(grant), handing_over)
                    result = await perform(action)
                except RuntimeError as exc:
                    if exc.args == (DENIED_BY_SETTINGS,):
                        record.status = "denied"
                    raise
                if reads and (not live() or not self._permissions.grant_active(grant)):
                    record.status, record.reason = "denied", "delivery_revoked"
                    raise RuntimeError("session records could not be delivered")
            if reads:
                if not record.closed:
                    raise RuntimeError("session records could not be audited")
                # Closing the row also awaited SQLite. Check again before releasing the result.
                if not live() or not self._permissions.grant_active(grant):
                    await self._correct_undelivered_read(record, "denied", "delivery_revoked")
                    raise RuntimeError("session records could not be delivered")
            return result
        except asyncio.CancelledError:
            if reads and record is not None and record.closed and record.status == "completed":
                await self._correct_undelivered_read(record, "cancelled")
            raise
        finally:
            self._permissions.release_grant(grant)

    def set_orchestrator_judge(self, judge: Callable[[list[dict[str, Any]]], Awaitable[str | None]] | None) -> None:
        """Set what asks the small model where a commission belongs (``LexicalDecisionModel.belongs``).

        Without one, a commission whose words neither name nor clearly
        match what is there is a new loose one, and no model is asked.
        """
        self.families.set_judge(judge)

    def set_side_question_judge(self, judge: Callable[[str, str, str], Awaitable[bool | None]] | None) -> None:
        """Set what tells whether a commission is a side question (``LexicalDecisionModel.side_question``).

        It is given what was dispatched, the user's words (with what they
        said to it since), and the question the session waits for the user
        to answer, if any. Without one, no commission is carried on in a
        copy of its session: each waits for the session.
        """
        self._side_question_judge = judge

    def set_waker(self, waker: Any) -> None:
        """Set what is told that something waits to be handed to a session (``wakes.SessionWakes``)."""
        self._waker = waker

    def _kick_waker(self) -> None:
        if self._waker is not None:
            self._waker.kick()

    async def _choose_runtime(
        self, commission: Commission, model: Any, begun_in: str | None, *, builtin_only: bool = False
    ) -> tuple[str, Any, str | None]:
        """Return the runtime a commission's task agent is started in; see ``runtimes.py``.

        Its name, the runtime, and, when the commission was to run in dsh
        and runs in the built-in runtime instead, why. With
        ``builtin_only`` it is the built-in runtime and dsh is not asked:
        for a session whose preset withholds tools, which only the
        built-in runtime keeps from it (``Template.needs_builtin``). That
        is where such a session belongs, so no reason is given for it.
        """
        if builtin_only:
            if self._pump is None:
                self._pump = asyncio.ensure_future(self._pump_events())
            return RUNTIME_BUILTIN, self._runtimes.builtin(), None
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
                        then = event.get(_THEN)
                        if then is not None:
                            # Done here, between two events: what it
                            # sees is as things stand at this point.
                            then()
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

    def _mark(self, then: Callable[[], None] | None = None) -> "asyncio.Future[None] | None":
        """Mark where what the runtimes have said up to now ends; None when nothing handles what they say.

        Not a coroutine: the mark is where it is put, with nothing
        awaited before. ``then`` is called at that very point by
        whoever handles the events, before what was said after it is
        handled (and not at all once nobody waits for the mark).
        """
        if self._pump is None or self._pump.done():
            return None
        reached: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._events.put_nowait({_REACHED: reached, _THEN: then})
        return reached

    async def _reach(self, reached: "asyncio.Future[None] | None", within: float = _CAUGHT_UP_SECONDS) -> None:
        """Wait until a mark is reached, and no longer than a moment."""
        if reached is None:
            return
        try:
            await asyncio.wait_for(reached, within)
        except asyncio.TimeoutError:
            logger.warning("the runtime's events were not caught up with in time; going on")

    async def _caught_up(self, within: float = _CAUGHT_UP_SECONDS) -> None:
        """Wait until what the runtimes have said up to now is handled, and no longer than a moment.

        For a callback of a session whose answer depends on what the
        session said just before: a runtime's events are handled one at a
        time, in a task of their own, and a callback is not one of them.
        Unlike ``settle`` this does not wait for what is said meanwhile.
        """
        await self._reach(self._mark(), within)

    # -- starting ---------------------------------------------------------

    async def submit(
        self,
        commission: Commission,
        *,
        candidate_plugin_ids: Any = (),
        wait: bool = True,
        still_wanted: Callable[[], bool] | None = None,
    ) -> bool:
        """Hand a commission to a task agent: now if a place is free, else when one is.

        With ``wait`` False a commission that finds no free place is failed
        instead of queued. Returns whether it was started or queued.

        ``still_wanted`` is asked once its session is made ready, which
        takes a moment, and before it takes a place or is queued, with
        nothing awaited in between: a commission that is not wanted
        any more (NekoPower was switched off meanwhile) is neither
        started nor queued, and is left as it is for whoever handed
        it over to say what became of it.
        """
        self._candidates[commission.id] = tuple(str(item) for item in candidate_plugin_ids)
        stops = self._idle_stops
        if commission.route != "task_agent":
            await self._ledger.update(commission.id, route="task_agent")
        if not self._closed:
            try:
                commission = await self._out_of_copy(commission)
                commission = await self._give_session(commission)
            except asyncio.CancelledError:
                self._excerpts.pop(commission.id, None)
                raise
            except Exception as exc:  # noqa: BLE001 - the commission fails with the reason
                logger.warning("giving commission %s a session failed: %s", commission.id, exc)
                self._candidates.pop(commission.id, None)
                self._excerpts.pop(commission.id, None)
                await self._fail(commission.id, f"the task agent could not be started: {exc}", said="start", detail=str(exc))
                return False
        if self._closed:
            self._candidates.pop(commission.id, None)
            self._excerpts.pop(commission.id, None)
            self._session_of.pop(commission.id, None)
            await self._ledger.transition(commission.id, "failed", reason=_SHUT_DOWN_REASON)
            return False
        if still_wanted is not None and not still_wanted():
            self._candidates.pop(commission.id, None)
            self._excerpts.pop(commission.id, None)
            self._session_of.pop(commission.id, None)
            return False
        self._new_work_in(self._session_of.get(commission.id), stops)
        # Up to when it was filed, which is when the sentence was dispatched.
        # (Kept then already for one that was filed by the task brain.)
        self._keep_excerpt(commission, upto=commission.created_at)
        placed = self._take_place(commission.id)
        if not placed and wait:
            # Settled before it is queued (``_settle_for_the_queue``). That
            # waits: whether it is still wanted is asked again after it, and
            # a place that came free meanwhile is taken.
            # On record as on its way into the queue for as long as anything
            # is awaited: from here to the queue nothing is.
            self._settling_for_queue.add(commission.id)
            try:
                await self._settle_for_the_queue(commission.id)
                queueable = await self._still_queueable(commission.id)
            finally:
                self._settling_for_queue.discard(commission.id)
            stopped = commission.id in self._stopped_while_settling
            self._stopped_while_settling.discard(commission.id)
            if stopped or not queueable:
                # Over meanwhile (cancelled with everything else, say), when
                # nothing could find it to stop it: not queued.
                self._candidates.pop(commission.id, None)
                self._excerpts.pop(commission.id, None)
                self._session_of.pop(commission.id, None)
                return False
            if self._closed:
                # Shut down meanwhile: what was queued then has been seen to,
                # and this would wait in the queue for nobody.
                self._candidates.pop(commission.id, None)
                self._excerpts.pop(commission.id, None)
                self._session_of.pop(commission.id, None)
                await self._ledger.transition(commission.id, "failed", reason=_SHUT_DOWN_REASON)
                return False
            if still_wanted is not None and not still_wanted():
                self._candidates.pop(commission.id, None)
                self._excerpts.pop(commission.id, None)
                self._session_of.pop(commission.id, None)
                return False
            placed = self._take_place(commission.id)
        if not placed:
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
            try:
                await self._ledger.append_event(commission.id, "queued", queued)
            except Exception:  # noqa: BLE001 - the note is for showing; the commission is queued
                # Told as a handing over that failed, it would be put down
                # as failed while it still has its place in the queue, and
                # be started from there later.
                logger.warning("noting that commission %s is queued failed", commission.id, exc_info=True)
            # Queued behind another commission of its session: it may be
            # carried on in a copy of the session meanwhile.
            self._try_carry_on(commission)
            return True
        # Started as a task of its own: whoever handed the commission over may
        # be cancelled (the host shutting down), and a session half set up
        # would then belong to nobody.
        if commission.source == "ask" and still_wanted is not None:
            return await asyncio.shield(self._spawn(self._launch(commission.id, still_wanted=still_wanted)))
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
        session_id = self._session_of.get(commission_id, "")
        if self._handing_back_to.get(session_id):
            # A commission a copy handed back is being put back in its place
            # among those that wait for the session (``_handed_back``).
            return False
        holder = self._held_by.get(session_id)
        if holder is not None and holder != commission_id:
            return False
        # One task agent that writes files works in a project at a time:
        # another member that does waits until that one's commission is
        # over. Those that read only do not wait, nor does the leader.
        where = self._writes_in.get(session_id)
        if where is not None:
            for other, held in self._held_by.items():
                theirs = self._writes_in.get(other)
                # (A member and the task agents it started are one: they work on its commission.)
                if other != session_id and held != commission_id and theirs is not None:
                    if theirs[0] == where[0] and theirs[1] != where[1]:
                        return False
        return True

    def _in_hand(self, commission_id: str) -> bool:
        """Whether a commission has a place, waits for one, or is on its way to one."""
        return (
            commission_id in self._sessions
            or commission_id in self._queue
            or commission_id in self._requeueing
            or commission_id in self._taking_up
        )

    def _in_hand_for(self, commission_id: str, session_id: str) -> bool:
        """Whether a commission holds a place, waits for one, or is on its way to one in ``session_id``."""
        live = self._sessions.get(commission_id)
        if live is not None and live.session_id == session_id:
            return True
        if self._session_of.get(commission_id) != session_id:
            return False
        return (
            commission_id in self._queue
            or commission_id in self._requeueing
            or commission_id in self._taking_up
        )

    async def _out_of_copy(self, commission: Commission) -> Commission:
        """Give a commission filed for a copy that carried another one on the session the copy was made of instead.

        Such a copy answered one side question and is not gone on in
        (``_carry_on_in_copy``): what follows that up, which names the
        copy (``TaskBrain._second_round``), goes to the session itself,
        which is told what came of it (``TASK_AGENT_COMMISSION_CONTINUES_IN_COPY``).
        Should the session be busy and this be a side question as well, it
        is answered in a new copy, of the session as it stands by then.
        """
        if not commission.session_id or not _carried_on(commission):
            return commission
        place = await self._ledger.get_session(commission.session_id)
        if place is None or place.kind != "fork" or not place.forked_from:
            return commission
        if not await self._ledger.carried_on_in(place.id) or await self._ledger.has_event(commission.id, "carried_on_in_copy"):
            # A copy made by other means, or the one this commission is
            # carried on in itself.
            return commission
        moved = await self._ledger.update(commission.id, session_id=place.forked_from)
        return moved if moved is not None else commission

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
        from config.nekopower_settings import TASK_AGENT_SESSION_NAME_MAX_TOKENS
        from utils.tokenize import truncate_to_tokens

        if commission.session_id:
            self._session_of[commission.id] = commission.session_id
            await self._note_writer(commission.session_id)
            return commission
        if (
            self._space is not None
            and commission.parent_id is None
            and not commission.session_dir
            and commission.started_at is None
        ):
            # One of hers that starts for the first time: where it goes is
            # worked out from what it says and what is there (``families.py``).
            # A project its words name has it, as ever.
            verdict = await self.families.decide(commission)
            commission, session = await self.families.place(commission, verdict)
            self._session_of[commission.id] = session.id
            await self._note_writer(session.id)
            for member in verdict.members:
                # Task agents that became a project's members with it: each may be at work.
                if member != session.id:
                    await self._note_writer(member)
            return commission
        commission = await self._file_under_project(commission)
        # What the session takes, as the first line of its card says until
        # a review writes a better one: what it was made for. No model is
        # asked. (A session that is there already keeps its card.)
        if commission.parent_id is not None:
            parent = await self._ledger.get(commission.parent_id)
            parent_session = await self._ledger.get_session(parent.session_id) if parent is not None else None
            lead = parent_session.name if parent_session is not None else commission.parent_id
            commission, session = await self._ledger.give_session(
                commission.id,
                kind="child",
                name=f"{lead}/{commission.name or commission.id}",
                parent_id=parent_session.id if parent_session is not None else None,
                # The one its starter named; a worker when it named none.
                preset=await self._preset_chosen(commission.id),
                duty=commission.title,
            )
        elif commission.project and commission.project != DEFAULT_PROJECT:
            commission, session = await self._project_home(commission)
        else:
            commission, session = await self._ledger.give_session(
                commission.id,
                kind="single",
                name=truncate_to_tokens(commission.title, TASK_AGENT_SESSION_NAME_MAX_TOKENS),
                duty=commission.title,
            )
        self._session_of[commission.id] = session.id
        await self._note_writer(session.id)
        # The model it is sent to is settled as the commission starts in it,
        # which is now unless it waits for a place (``_start``).
        return commission

    async def _project_home(self, commission: Commission) -> tuple[Commission, Session]:
        """Give a commission of a project that was not sent anywhere (``families.py``) its session; returns both.

        One that was started before it had a session, or that names its
        own directory. The leader of its project when that has one; else
        the member last in use that can still be gone on with; else a new
        member. No leader is made here: one that only hands work on is
        made where a project is found to want one (``Families.place``).
        """
        from nekopower.host.orchestrator import WORKING_PRESET

        project = str(commission.project)
        about = await self._project_about(project) or commission.title
        # As where a commission is placed: no leader is made, and no other member named, between the look and the giving.
        async with self.families.placing:
            if await self._ledger.lead_session(commission.lanlan_name, project) is not None:
                return await self._ledger.give_session(commission.id, kind="project", name=project, duty=about)
            for member in await self._ledger.members(commission.lanlan_name, project):
                if await self._ledger.session_alive(member):
                    moved = await self._ledger.update(commission.id, session_id=member.id)
                    return moved or commission, member
            name = await self._ledger.member_name(commission.lanlan_name, project, project)
            return await self._ledger.give_session(
                commission.id, kind="line", name=name, preset=WORKING_PRESET, duty=about
            )

    async def _note_writer(self, session_id: str | None) -> None:
        """Note whether a session writes files in a project, for one such to work there at a time (``_session_free_for``).

        A member of a project whose preset can change files, with a tool
        that writes or edits one or through the shell
        (``Template.changes_files``), and a task agent such a member
        started that can: noted with the member it works for, which it
        does not wait for, while the members of the project wait for it
        as they wait for the member (a task agent whose stop was not
        seen through may still be at it when the member is over). Not
        the project's leader, which changes nothing, nor what the leader
        starts, which looks and checks; not a copy, which reads. One
        whose preset cannot be read counts as one that changes files.
        Noted as the project and the member: by project alone do they
        wait, every character's members working in its one directory.
        """
        if not session_id:
            return
        try:
            # Asked again where the ledger cannot be read just now: left unnoted, it would not wait.
            kept = await self._looked(
                lambda: self._ledger.get_session(session_id), what="for whether a session changes files in a project"
            )
            member = kept
            if kept is not None and kept.kind == "child" and kept.parent_id:
                member = await self._looked(
                    lambda: self._ledger.get_session(kept.parent_id), what="for the member a task agent works for"
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - left as it was known
            logger.warning("whether session %s changes files in a project could not be read", session_id, exc_info=True)
            return
        if (
            kept is None
            or member is None
            or member.kind != "line"
            or not member.project
            or member.project == DEFAULT_PROJECT
        ):
            self._writes_in.pop(session_id, None)
            return
        try:
            changes = (await self._template_of(kept)).changes_files
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - not told: held to the rule
            changes = True
        if changes:
            self._writes_in[session_id] = (member.project, member.id)
        else:
            self._writes_in.pop(session_id, None)

    async def _settle_for_the_queue(self, commission_id: str) -> None:
        """Settle the model of the session of a commission that found no place, before it is queued.

        Settled as it is queued, and not by what the user sets while it
        waits (``settle_model``). A place may come free meanwhile, while it
        is not yet in the queue for whoever gives places to see: the caller
        tries again for one.
        """
        try:
            queued_in = await self._ledger.get_session(self._session_of.get(commission_id))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - queued all the same, and settled when it starts
            logger.warning("the session of commission %s could not be read to settle its model", commission_id)
            return
        if queued_in is not None:
            await self.settle_model(queued_in)

    async def _still_queueable(self, commission_id: str) -> bool:
        """Return whether a commission is still to be queued after a wait: not ended meanwhile. True when unknown."""
        try:
            current = await self._ledger.get(commission_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - queued, as it would have been
            return True
        return current is not None and current.status not in ENDED_STATUSES

    async def _preset_chosen(self, commission_id: str) -> str | None:
        """Return the preset a task agent was started with by the one that started it; None when that one named none.

        On record with its commission from the moment that was filed
        (``_cb_subagent_start``), and read from there whenever it is given
        its session: also when its start failed before it had one and it
        is taken up again later, or by another host process. A task agent
        that was to read only is not opened as a worker for that.
        """
        for event in await self._ledger.events(commission_id, limit=_PRESET_ON_RECORD_WITHIN):
            if event.type == "preset":
                return str(event.payload.get("preset") or "") or None
        return None

    async def _project_about(self, project: str) -> str:
        """Return what a project is for, as its record says; empty when it says nothing or cannot be read."""
        try:
            found = await self._space.get_project(project)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the session is made all the same, with its first commission's title
            logger.warning("reading what a project is for failed", exc_info=True)
            return ""
        return found.description if found is not None else ""

    async def _template_of(self, kept: Session) -> Any:
        """Return the template a session is opened by (``templates.Template``); raises when its preset cannot be used.

        A copy is opened as the session it was copied from (and without
        the tools every copy is without, ``_spec``): its preset is no
        template. One whose template cannot be used is not opened by
        another in its place: what it would then be allowed is not what
        its user set down.
        """
        from config.prompts.prompts_nekopower_presets import (
            PRESET_COPIED_TOO_OFTEN,
            PRESET_OF_STARTER_UNUSABLE,
            PRESET_UNUSABLE,
        )
        from nekopower.host.ledger import PRESET_OF_KIND
        from nekopower.host.templates import LEADER_WITHHELD, SHADOW, SOURCE_BUILTIN, TemplateError, builtin_templates

        source = kept
        for _ in range(_COPY_CHAIN_MAX):
            if source.kind != "fork" or not source.forked_from:
                break
            origin = await self._ledger.get_session(source.forked_from)
            if origin is None:
                break
            source = origin
        else:
            if source.kind == "fork" and source.forked_from:
                # Still a copy of something after as many steps as are
                # followed: the session it began with is there, and what
                # its preset withholds is not dropped for not looking further.
                raise RuntimeError(PRESET_COPIED_TOO_OFTEN.format(most=_COPY_CHAIN_MAX))
        if source.kind == "fork" or source.preset == SHADOW:
            # A copy whose origin is not on record any more: nothing to take
            # after. It has what every copy has, and no role of its own.
            return builtin_templates()["general"]
        try:
            template = await asyncio.to_thread(self.templates.resolve, source.preset, source.project)
        except TemplateError as exc:
            raise RuntimeError(PRESET_UNUSABLE.format(name=source.preset, reason=exc)) from exc
        if source.kind == "line" and source.preset == PRESET_OF_KIND["project"] and template.source == SOURCE_BUILTIN:
            # The one session a project had in a build without leaders: it
            # did the work under the leader's preset, and goes on doing it
            # as one that works. Under a template of the user's that stands
            # in the leader's place it keeps that one, and what it withholds.
            try:
                template = await asyncio.to_thread(self.templates.resolve, PRESET_OF_KIND["line"], source.project)
            except TemplateError as exc:
                raise RuntimeError(PRESET_UNUSABLE.format(name=PRESET_OF_KIND["line"], reason=exc)) from exc
        if source.kind == "project":
            # A leader does none of the work, whatever template stands in
            # the built-in one's place: what it hands out is done by those
            # it hands it to.
            missing = tuple(tool for tool in LEADER_WITHHELD if tool not in template.withheld())
            if missing:
                template = dataclasses.replace(template, withhold_own=(*template.withhold_own, *missing))
        if source.kind in ("child", "line") and source.parent_id:
            # (A member a leader handed a commission to keeps what the
            # leader is without itself only: ``Template.under``.)
            # A task agent another one started is that one's hands: it works
            # in the same directory, on the same commission, with what the
            # user allowed that commission. What the starter's preset keeps
            # from it, its task agent does not hand back. (No built-in preset
            # both withholds a tool and starts task agents; a template of the
            # user's may.) A starter that is not on record any more withholds
            # nothing that can be told.
            starter = await self._ledger.get_session(source.parent_id)
            if starter is not None:
                try:
                    over = await self._template_of(starter)
                except RuntimeError as exc:
                    # Said as what it is: the preset named is the starter's,
                    # not the one this task agent was started with.
                    raise RuntimeError(PRESET_OF_STARTER_UNUSABLE.format(reason=exc)) from exc
                template = template.under(over)
        return template

    # -- which model a session is sent to ----------------------------------

    async def model_default_of(self, preset: str, project: str | None) -> str:
        """Return a preset's default model for a project, as its template says (``Template.model``)."""
        from nekopower.host.role_models import INHERIT
        from nekopower.host.templates import TemplateError, builtin_templates

        try:
            template = await asyncio.to_thread(self.templates.resolve, preset, project)
        except TemplateError:
            # A session of it is not opened (``_template_of``); what it
            # would be sent to is said as the built-in preset of the name has it.
            found = builtin_templates().get(preset)
            return found.model if found is not None else INHERIT
        return template.model

    async def session_model(self, kept: Session, *, record: bool = True) -> Any:
        """Return the model a session's task agent is sent to now (``role_models.Choice``).

        Settled when the session is made: by the setting of its preset,
        else its default, and written on its row (with ``record``; a look
        that is only a look leaves the row alone). A session that was
        settled is sent to the slot it was settled on, which is read anew
        (an address or a key the user changed there is taken); when that
        slot cannot be used any more, to its preset's default, with why
        on its row. A copy is sent where the session it was copied from is.
        """
        return await self._session_model(kept, record=record, depth=0)

    async def _session_model(self, kept: Session, *, record: bool, depth: int) -> Any:
        from nekopower.host.role_models import LASTING_REASONS, SlotUnavailable, dataclass_replace, recorded

        if depth > _COPY_CHAIN_MAX:
            # Started by, or copied from, more sessions in a row than are
            # followed: as if there were none to follow.
            return await self.role_models.agent_default()
        if kept.kind == "fork" and kept.forked_from:
            origin = await self._ledger.get_session(kept.forked_from)
            if origin is not None:
                choice = await self._session_model(origin, record=record, depth=depth + 1)
                await self._keep_model(kept, choice, record)
                return choice
        if recorded(kept):
            try:
                choice = dataclass_replace(
                    await self.role_models.again(kept.model_slot, kept.model_ident, kept.model_tier),
                    fallback=kept.model_fallback,
                )
            except SlotUnavailable as exc:
                logger.info(
                    "the model slot session %s was settled on cannot be used (%s); its preset's default is",
                    kept.id,
                    exc.code,
                )
                choice = await self._choose_model(kept, record=record, depth=depth, use_setting=False, fallback=exc.code)
                if exc.code not in LASTING_REASONS:
                    # Perhaps only for now: sent to the default this time,
                    # and what it was settled on stays.
                    return choice
        elif record:
            return await self._settle_first(kept, depth=depth)
        else:
            choice = await self._choose_model(kept, record=record, depth=depth)
        await self._keep_model(kept, choice, record)
        return choice

    async def _settle_first(self, kept: Session, *, depth: int) -> Any:
        """Settle a session that was not settled, one settling of it at a time; see ``session_model``.

        Whoever comes second finds it settled by the first, and goes by that.
        """
        from nekopower.host.role_models import recorded

        lock, users = self._settle_locks.get(kept.id, (asyncio.Lock(), 0))
        self._settle_locks[kept.id] = (lock, users + 1)
        try:
            async with lock:
                fresh = await self._ledger.get_session(kept.id) or kept
                if recorded(fresh):
                    return await self._session_model(fresh, record=True, depth=depth)
                choice = await self._choose_model(fresh, record=True, depth=depth)
                await self._keep_model(fresh, choice, True)
                return choice
        finally:
            # Kept while anyone else holds or waits for it: one that came
            # now would otherwise make another, and settle beside them.
            held, left = self._settle_locks[kept.id]
            if left <= 1:
                del self._settle_locks[kept.id]
            else:
                self._settle_locks[kept.id] = (held, left - 1)

    async def _choose_model(
        self, kept: Session, *, record: bool, depth: int, use_setting: bool = True, fallback: str = ""
    ) -> Any:
        """Return the model a session is settled on by its preset (``RoleModels.choose``)."""

        async def inherited() -> Any:
            # The session that started it; else, for one that no session
            # started, the lead of its project.
            if kept.parent_id:
                lead = await self._ledger.get_session(kept.parent_id)
            elif kept.kind != "project":
                lead = await self._ledger.lead_session(kept.lanlan_name, kept.project)
            else:
                lead = None
            if lead is None:
                return None
            return await self._session_model(lead, record=record, depth=depth + 1)

        return await self.role_models.choose(
            kept.preset, kept.project, inherited, use_setting=use_setting, fallback=fallback
        )

    async def _keep_model(self, kept: Session, choice: Any, record: bool) -> None:
        """Write on a session's row what it is sent to, where that is not what it says already."""
        from nekopower.host.role_models import recorded, same_as_kept

        if not choice.ident and recorded(kept):
            # No model is configured where it goes just now: what it was
            # settled on stays, to be read again the next time.
            return
        if record and not same_as_kept(kept, choice):
            try:
                await self._ledger.update_session(kept.id, **choice.columns())
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a session settled before goes on; it is written the next time
                if not recorded(kept):
                    # Not settled before: opened now, it could be opened by
                    # another model next time, its conversation and all.
                    raise
                logger.warning("writing down the model of session %s failed", kept.id)

    async def model_of_commission(self, commission_id: str) -> dict[str, Any] | None:
        """Return what the session of a commission would be opened with now (looked at, not settled); None without a model.

        A commission that has no session yet: what a new one of hers would
        be. One at work in an open session: the model that session was
        opened with.
        """
        live = self._sessions.get(commission_id)
        if live is not None and live.model is not None:
            return {"base_url": live.model[0], "model": live.model[1]}
        commission = await self._ledger.get(commission_id)
        kept = await self._ledger.get_session(commission.session_id) if commission is not None else None
        choice = await self.session_model(kept, record=False) if kept is not None else await self._new_session_model()
        return choice.config

    async def settle_model(self, session: Session) -> None:
        """Settle the model of a session that was just made (``session_model``), unless it is settled.

        One that cannot be settled now is settled when it is first opened.
        """
        from nekopower.host.role_models import recorded

        if recorded(session):
            return
        try:
            await self.session_model(session)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - settled when it is opened instead
            logger.warning("settling the model of session %s failed; it is settled when it is opened", session.id)

    async def _goes_free(self, kept: Session | None) -> bool:
        """Return whether what a session would send now goes with the built-in free key.

        By where it is sent as things are now (``session_model``, looked
        at): its slot may have been given a key of the user's own since it
        last ran. None (a new one), by what it would be settled on. Not
        known: as if it did, so it is counted as before there were settings.
        """
        try:
            choice = await (self.session_model(kept, record=False) if kept is not None else self._new_session_model())
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - counted, as it was before
            logger.warning("whether a session goes with the free key could not be told; it is counted")
            return True
        return bool(choice.free)

    async def _charge(self, free: bool, key: str) -> bool:
        """Count a model request against the free daily quota, for a session whose requests go with the free key.

        Returns whether it may be made. One that goes with another key
        is not counted and always may.
        """
        if not free or self._meter is None:
            return True
        return bool(await self._meter(key))

    def _base_instructions(
        self, commission: Commission, *, with_gateway: bool, template: Any = None, roster: tuple[Any, ...] = ()
    ) -> str:
        """Return what the host adds to the system prompt of the session a commission is worked on in.

        ``template`` is the preset the session is opened by and ``roster``
        the presets it can start task agents with (``_roster``): after
        what every session is told come the list of those, for a session
        that may start task agents, and then what its own preset says of
        its role.

        Nothing of the commission itself: a session takes several, and its
        system prompt is kept as it was first opened with (the runtime
        reads these again only after the conversation was summed up).
        These instructions describe how the host's capabilities are reached
        and whom the task agent works for. The current workspace brief is
        appended separately when it can be released.

        In that order, which is from what every session is told alike to
        what only this one is: the system prompts of two sessions are then
        the same up to where they differ, and a provider that kept the
        head of one session's requests has it for the other's as well.
        (The runtime puts what it says itself of one session, its working
        directory, behind all of this: ``runtime/prompt.py``.)
        """
        from config.nekopower_settings import (
            TASK_AGENT_PRESET_ROSTER_MAX_TOKENS,
            TASK_AGENT_SESSION_INSTRUCTIONS_MAX_TOKENS,
        )
        from config.prompts import prompts_nekopower_brain as texts
        from config.prompts import prompts_nekopower_presets as presets
        from nekopower.host.templates import model_name
        from utils.tokenize import count_tokens, truncate_to_tokens

        parts = [texts.TASK_AGENT_BRIEF_GATEWAY_NOTE] if with_gateway else []
        if commission.parent_id is not None:
            parts.append(texts.TASK_AGENT_CHILD_FRAME)
        else:
            parts.append(texts.TASK_AGENT_SESSION_FRAME.format(lanlan_name=commission.lanlan_name))
        if roster:
            # Within a cap of its own, whole lines only: the names and the
            # words around them are not counted in the cap on the templates'
            # descriptions, and what the session is told of its own role
            # comes after this and is not to be cut off by it. A preset
            # that is left out can still be named.
            lines = [presets.PRESET_ROSTER_INTRO]
            room = TASK_AGENT_PRESET_ROSTER_MAX_TOKENS - count_tokens(presets.PRESET_ROSTER_INTRO)
            for item in roster:
                line = presets.PRESET_ROSTER_LINE.format(name=model_name(item.name), description=item.description)
                room -= count_tokens(line) + 1
                if room < 0:
                    break
                lines.append(line)
            if len(lines) > 1:
                # (With no room for even one preset, nothing is said of them.)
                parts.append("\n".join(lines))
        if template is not None and template.body:
            parts.append(template.body)
        return truncate_to_tokens("\n\n".join(parts), TASK_AGENT_SESSION_INSTRUCTIONS_MAX_TOKENS)

    async def _roster(self, commission: Commission, kept: Session, template: Any) -> tuple[Any, ...]:
        """Return the presets a session is told it can start task agents with; none for one that starts none.

        By what the session is, not by the tools this one start leaves it:
        a copy is told what the session it was copied from is told. A list
        that cannot be read leaves the session without one: it starts
        workers, as before there were presets.
        """
        if commission.parent_id is not None or not template.delegates:
            return ()
        try:
            return tuple(await asyncio.to_thread(self.templates.roster, kept.project))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - it starts all the same
            logger.warning("listing the presets for a session failed", exc_info=True)
            return ()

    def note_filed(self, commission: Commission) -> None:
        """Keep what she and the user said just before a commission that was filed this moment.

        For the task agent that may take it. Who does it is chosen
        next, which takes a model request, and what is said meanwhile
        pushes what was said before out of what is kept of the main
        dialogue. Dropped again when no task agent takes the
        commission (``drop_excerpt``). Nothing having been said is kept
        as that: a message that is told of late, while it is chosen who
        does the commission, is not read as said before it after all.
        """
        if commission.id not in self._excerpts:
            self._excerpts[commission.id] = self._said_before(commission, upto=commission.created_at)

    def drop_excerpt(self, commission_id: str) -> None:
        """Drop what ``note_filed`` kept for a commission that no task agent takes."""
        self._excerpts.pop(commission_id, None)

    def _keep_excerpt(self, commission: Commission, *, upto: float | None = None) -> None:
        """Keep what she and the user said just before a commission, until its session is opened.

        Her own commissions only: a task agent that another one started is
        told its part by that one. Taken now, when the commission is handed
        over, and not when its session is opened: it may wait for a place,
        and what is said meanwhile is not what it refers to. Kept in memory;
        no model is asked and nothing is read from anywhere else.
        """
        if commission.id in self._excerpts:
            return
        turns = self._said_before(commission, upto=upto)
        if turns:
            self._excerpts[commission.id] = turns

    def _said_before(self, commission: Commission, *, upto: float | None = None) -> list[Any]:
        """Return what she and the user said in the main dialogue just before now, or before ``upto``; empty for none.

        For her own commissions only (see ``_keep_excerpt``): not for one
        filed for what another session wrote, which nobody dispatched and
        which what she and the user said has nothing to do with.
        """
        if self._conversation is None or commission.parent_id is not None or commission.source == "session":
            return []
        try:
            turns = self._conversation.recent(commission.lanlan_name)
        except Exception:  # noqa: BLE001 - it starts without an excerpt
            logger.warning("reading what was said before a commission failed", exc_info=True)
            return []
        if upto is not None:
            # What was said after the sentence was dispatched (while it was
            # being routed, say) is not what it refers to.
            turns = [turn for turn in turns if turn.at <= upto]
        return list(turns)

    def _times_dropped(self, commission: Commission) -> int:
        """Return how many times what is kept of the main dialogue for a commission's character has been dropped."""
        return self._conversation.times_dropped(commission.lanlan_name) if self._conversation is not None else 0

    def _excerpt(self, commission: Commission) -> str:
        """Return the part of a commission's opening message that holds what was said just before it; empty for none.

        Word for word, the newest messages first to be kept, within a cap
        of its own (``TASK_AGENT_EXCERPT_MAX_TOKENS``; the lines that say
        what it is come on top). Wrapped as background that gives no
        instructions: what she said in it she said to the user.
        """
        from config.nekopower_settings import TASK_AGENT_EXCERPT_MAX_TOKENS, TASK_AGENT_EXCERPT_MESSAGE_MAX_TOKENS
        from config.prompts import prompts_nekopower_brain as texts
        from nekopower.host.conversation import excerpt_lines

        # (Nothing is kept for a task agent another one started: ``_keep_excerpt``.)
        turns = self._excerpts.get(commission.id)
        if turns:
            # Taken when the commission was filed or handed over, which
            # may be before what is kept of the main dialogue was last
            # dropped (NekoPower went off): what was said before that
            # is not handed to a task agent after it, wherever it was
            # kept meanwhile. (By how many times it had been dropped when
            # each was kept, and not by when each was said: a clock
            # may be set back, and says the same time twice.)
            now_kept_in = self._times_dropped(commission)
            turns = [turn for turn in turns if turn.kept_in == now_kept_in]
        if not turns:
            return ""
        # With the cap at nothing no line fits, and nothing is handed over.
        lines = excerpt_lines(
            turns,
            lanlan_name=commission.lanlan_name,
            max_tokens=TASK_AGENT_EXCERPT_MAX_TOKENS,
            message_max_tokens=TASK_AGENT_EXCERPT_MESSAGE_MAX_TOKENS,
            # Stated by the commission itself, right above.
            # As the opening message states the user's words.
            already_told=commission.user_text or commission.dispatch_text,
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

    async def _opening(
        self,
        commission: Commission,
        *,
        cleared: bool,
        back: bool = False,
        excerpt: str = "",
        documents: str = "",
        copies: str = "",
    ) -> str:
        """Return the message that opens a commission in its session.

        ``copies`` is what it says of copies of the session (``_copies_said``),
        put together once for every wording of one start.

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
        ``documents`` is the list of the files the repository it works in
        keeps for agents (``_repository_documents``), empty for none: said
        with every commission, since what was said in the session before
        is summed up in time and the message that opens the commission
        under way is what is kept.

        What a task agent hands to one it starts is not cut: it is all that
        one is told of its part, and the task agent that wrote it is told
        that the other was started. (What she dispatches is short by the
        time it is filed, ``DISPATCH_TEXT_MAX_TOKENS``.)
        """
        from config.nekopower_settings import TASK_AGENT_BRIEF_MAX_TOKENS, TASK_AGENT_CONTINUED_RESULT_MAX_TOKENS
        from config.prompts import prompts_nekopower_brain as texts
        from config.prompts.prompts_nekopower import REPLY_LANGUAGE_SENTENCE
        from utils.tokenize import truncate_to_tokens

        whole = ""
        if commission.source == "ask":
            # A question another session asked, framed: not cut either.
            whole = await self._opening_of_question(commission)
            parts = []
        elif commission.parent_id is not None:
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
        in_copy = ""
        if earlier is not None:
            if cleared:
                result = truncate_to_tokens(
                    earlier.result_summary or earlier.status_reason or earlier.status,
                    TASK_AGENT_CONTINUED_RESULT_MAX_TOKENS,
                )
                parts.append(texts.TASK_AGENT_COMMISSION_CONTINUES_CLEARED.format(title=earlier.title, result=result))
            elif commission.session_id and earlier.session_id and await self._not_above(earlier, commission.session_id):
                # Worked on in a copy of this session, or of the session this
                # is a copy of (it was followed up there, and this went to
                # the session itself): nothing of it is above. Said past the
                # cap below, whole: it is put on record as told
                # (``_copies_said``), and is within a cap of its own.
                result = truncate_to_tokens(
                    earlier.result_summary or earlier.status_reason or earlier.status,
                    TASK_AGENT_CONTINUED_RESULT_MAX_TOKENS,
                )
                in_copy = texts.TASK_AGENT_COMMISSION_CONTINUES_IN_COPY.format(title=earlier.title, result=result)
            else:
                parts.append(texts.TASK_AGENT_COMMISSION_CONTINUES.format(title=earlier.title))
        preferences = await self._preferences.render(f"{commission.dispatch_text}\n{commission.user_text}")
        if preferences:
            parts.append(f"{texts.TASK_AGENT_BRIEF_PREFERENCES_HEADER}\n{preferences}")
        capped = truncate_to_tokens("\n\n".join(parts), TASK_AGENT_BRIEF_MAX_TOKENS) if parts else ""
        opening = "\n\n".join(part for part in (whole, capped) if part)
        # After the cap above, each within a cap of its own.
        if in_copy:
            opening += "\n\n" + in_copy
        if copies:
            opening += "\n\n" + copies
        if excerpt:
            opening += "\n\n" + excerpt
        picked = await self._picked_entries(commission)
        if picked:
            opening += "\n\n" + picked
        if documents:
            opening += "\n\n" + documents
        language = _REPLY_LANGUAGES.get(commission.lang or "", None)
        if language:
            # Said here, with every commission, and nowhere else: a session's
            # commissions need not share a language, and its system prompt
            # stays as it was opened until it is summed up. The built-in
            # runtime keeps this message through a summary as it is
            # (``opening=True``); dsh sums up on its own. The language
            # is the one of her dialogue when it was dispatched, which is also
            # what the host writes in to her and on the HUD.
            opening += "\n\n" + REPLY_LANGUAGE_SENTENCE.replace("<<LANGUAGE>>", language)
        return opening

    async def _not_above(self, earlier: Commission, session_id: str) -> bool:
        """Whether a commission was worked on in another copy of the session a commission is opened in, and is not in its conversation.

        A copy of a project's session that carried it on, where the session
        is that project's session or another copy of it. Not above where
        neither took its opening message: a copy holds what the session
        itself held when it was made.
        """
        if earlier.session_id == session_id:
            return False
        was_in = await self._ledger.get_session(str(earlier.session_id))
        here = await self._ledger.get_session(session_id)
        if was_in is None or here is None or not was_in.forked_from:
            return False
        home = [session_id, *([str(here.forked_from)] if here.kind == "fork" and here.forked_from else [])]
        if was_in.forked_from not in home:
            return False
        for place in home:
            if await self._ledger.opened_in(earlier.id, place) is not None:
                return False
        return True

    async def _copies_said(self, commission: Commission) -> str:
        """Return what the message that opens a commission says of copies of its session and of its project; empty for nothing.

        Of copies: ``_copies_of_session``. Of its project: to a leader,
        what came of the commissions it handed over and who the members
        are (``Families.leader_notes``); to a member, where other members
        worked (``Families.member_notes``). Put together by the host; no
        model is asked.
        """
        self._handed_said.pop(commission.id, None)
        said = await self._copies_of_session(commission)
        if not commission.session_id or commission.parent_id is not None or commission.source == "ask":
            return said
        try:
            place = await self._ledger.get_session(commission.session_id)
            more = ""
            if place is not None and place.kind == "project" and place.project:
                more, told = await self.families.leader_notes(place, commission.id)
                if told:
                    # On record as told once the session took the message (``_note_opened``).
                    self._handed_said[commission.id] = list(told)
            elif place is not None and place.kind == "line":
                more = await self.families.member_notes(place)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - it opens without them
            logger.warning("what a commission is told of its project could not be put together", exc_info=True)
            more = ""
        return "\n\n".join(part for part in (said, more) if part)

    async def _copies_of_session(self, commission: Commission) -> str:
        """Return what the message that opens a commission says of copies of its session; empty for nothing.

        Worked on in a copy of its project's session (``_carry_on_in_copy``):
        that it is one, what it can do there, and how it goes back to the
        session itself. Worked on in a project's session that copies of it
        finished commissions in since it took an opening message last:
        which, and what came of them (``TASK_AGENT_FORKED_WORK_MAX_ITEMS``,
        within ``TASK_AGENT_FORKED_WORK_MAX_TOKENS``). What was said in
        them did not come back to it.
        """
        from config import nekopower_settings as settings
        from config.prompts import prompts_nekopower_brain as texts
        from utils.tokenize import count_tokens, truncate_to_tokens

        self._forks_said.pop(commission.id, None)
        if not commission.session_id or commission.parent_id is not None:
            return ""
        place = await self._ledger.get_session(commission.session_id)
        if place is None:
            return ""
        if place.kind == "fork":
            carried = _carried_on(commission) and place.forked_from and await self._ledger.carried_on_in(place.id)
            if not carried:
                return ""
            # What the session itself waits for the user to answer, if it
            # does: not this copy's to answer or to settle.
            waited_on = await self._waited_on(str(place.forked_from))
            if not waited_on:
                return texts.TASK_AGENT_COMMISSION_IN_COPY
            return f"{texts.TASK_AGENT_COMMISSION_IN_COPY}\n\n{texts.TASK_AGENT_COPY_SESSION_WAITS.format(question=waited_on)}"
        # Whichever commission the session itself is opened with next, one
        # filed for what another session wrote included.
        if place.kind not in PROJECT_KINDS:
            return ""
        finished = await self._ledger.forked_work(place.id, limit=settings.TASK_AGENT_FORKED_WORK_MAX_ITEMS)
        # The one it follows up is told of by the line that says so
        # (``TASK_AGENT_COMMISSION_CONTINUES_IN_COPY``), and not a second time
        # here; told all the same.
        told = [item.id for item in finished if item.id == commission.continues]
        done = [item for item in finished if item.id not in (commission.id, commission.continues)]
        if not done:
            if told:
                self._forks_said[commission.id] = told
            return ""
        # Told once the session took the message that says it (``_note_opened``).
        self._forks_said[commission.id] = [*told, *(item.id for item in done)]
        return _forked_work_text(done, [])

    def _news_of_copies(self, main: str) -> None:
        """Have the session itself told what copies of it finished or handed back, once it is at work (``_tell_of_copies``)."""
        self._copy_news.add(main)
        if main in self._telling_of_copies or self._closed:
            return
        self._telling_of_copies.add(main)
        self._spawn(self._tell_of_copies(main)).add_done_callback(lambda _done: self._telling_of_copies.discard(main))

    async def _ended_in_copy(self, session_id: str) -> None:
        """A commission ended in a session: news for the session it is a copy of, if it is one made to carry it on in."""
        try:
            place = await self._ledger.get_session(session_id)
            if place is None or place.kind != "fork" or not place.forked_from:
                return
            if not await self._ledger.carried_on_in(place.id):
                return
        except Exception:  # noqa: BLE001 - told with the next commission opened in it, as ever
            logger.warning("reading the session a commission ended in failed", exc_info=True)
            return
        self._news_of_copies(str(place.forked_from))

    async def _tell_of_copies(self, main: str) -> None:
        """Tell a project's session, while it is at work, what copies of it finished or handed back. For its information only.

        Said before its next step (``steer``), as the user's words are,
        but as nobody's: it approves nothing (``_Found``). Only while the
        commission that holds it is at work, with a run under way: not
        while it waits for the user (it is told once it goes on), nor
        while it rests or nothing holds it, which this would wake; what
        it finished is told then with the next commission opened in it
        (``_copies_said``), and what it handed back comes with that
        commission itself. Each finished one is told once (``told_to_session``).
        """
        from config import nekopower_settings as settings

        def at_work() -> tuple[str, _Session, Any] | None:
            # The commission that holds it, its place and its runtime, while
            # it may be told: at work, with a run under way.
            holder = self._held_by.get(main)
            live = self._sessions.get(holder) if holder is not None else None
            runtime = self._runtime_of(live) if live is not None else None
            if (
                holder is None
                or live is None
                or runtime is None
                or live.carries_on_for is not None
                or live.closing
                or not live.ready
                or live.idle
                or live.settling
                or live.ending is not None
                or live.awaiting_prompt
                or live.stands is None
                or live.stands.status != "working"
                or live.stands.resting
            ):
                return None
            return holder, live, runtime

        while main in self._copy_news and not self._closed:
            if at_work() is None:
                # Kept for when it is at work again (``_handle``).
                return
            self._copy_news.discard(main)
            most = settings.TASK_AGENT_FORKED_WORK_MAX_ITEMS
            news = self._handed_back_news.pop(main, [])
            handed: list[tuple[Commission, str]] = []
            try:
                # Past those already on their way to it: what is left over
                # once a text has told of the most it may goes with the next.
                # (One more than is told: whether any is left over.)
                passed = len(self._copies_on_the_way) + sum(len(items) for items in self._forks_said.values())
                found = await self._ledger.forked_work(main, limit=most + passed + 1)
                # Told by an opening message that is on its way (``_copies_said``)
                # until it is on record as told (``_note_opened``), as it stands
                # once that was read: not a second time here.
                told_in_openings = {item for items in self._forks_said.values() for item in items}
                waiting_to_be_told = [
                    item for item in found if item.id not in self._copies_on_the_way and item.id not in told_in_openings
                ]
                done = waiting_to_be_told[:most]
                while news and len(done) + len(handed) < most:
                    commission_id, copy_id = news[0]
                    # Only one that still waits for it: one that is started is
                    # opened with what the copy found, which says it all.
                    if commission_id in self._queue and self._session_of.get(commission_id) == main:
                        waiting = await self._ledger.get(commission_id)
                        if waiting is not None:
                            handed.append((waiting, copy_id))
                    news.pop(0)
            except Exception:  # noqa: BLE001 - kept, and tried again when it is next seen at work
                logger.warning("reading what copies of a session did failed; it is told later", exc_info=True)
                self._copy_news.add(main)
                self._handed_back_news[main] = [
                    *((waiting.id, copy_id) for waiting, copy_id in handed),
                    *news,
                    *self._handed_back_news.get(main, []),
                ]
                return
            if news or len(waiting_to_be_told) > len(done):
                # More than one text tells of: the rest with the next.
                self._handed_back_news[main] = [*news, *self._handed_back_news.get(main, [])]
                self._copy_news.add(main)
            if not done and not handed:
                continue
            now = at_work()
            if now is None:
                # Not at work any more once the ledger was read: kept for later.
                self._copy_news.add(main)
                self._handed_back_news[main] = [
                    *((waiting.id, copy_id) for waiting, copy_id in handed),
                    *self._handed_back_news.get(main, []),
                ]
                return
            holder, live, runtime = now
            text = _Found(_forked_work_text(done, handed))
            told = [item.id for item in done]
            # By the number the text is sent under (``_send_text`` counts it
            # before it awaits anything): put on record as told once the
            # session took it (``_on_taken``). A run that is stopped first
            # drops it, and so does a refusal (``_withdraw``): it is told again.
            number = live.sent + 1
            if told:
                live.copies_told[number] = told
                self._copies_on_the_way.update(told)
            if handed:
                live.handed_told[number] = [(waiting.id, copy_id) for waiting, copy_id in handed]
            # What a copy wrote, from what it read, is about to stand in the
            # conversation of the commission at work: what the user allowed
            # it for the session or the run is asked again from here on, as
            # for a note from another session. Nothing is awaited between
            # the look above and the text being sent.
            self._permissions.received_external_input(holder)
            try:
                await self._send_text(runtime, live, "steer", text)
            except BaseException:
                live.copies_told.pop(number, None)
                live.handed_told.pop(number, None)
                live.copies_dropped = [item for item in live.copies_dropped if item not in told]
                live.handed_dropped = [pair for pair in live.handed_dropped if pair[0] not in {w.id for w, _ in handed}]
                self._copies_on_the_way.difference_update(told)
                # Told again when it is next seen at work (or with the next
                # commission opened in it); what was handed back as well.
                self._copy_news.add(main)
                self._handed_back_news[main] = [
                    *((waiting.id, copy_id) for waiting, copy_id in handed),
                    *self._handed_back_news.get(main, []),
                ]
                raise
            try:
                # On record as what it was told, under a type of its own: no
                # word of the user's, which a review reads ``told`` for.
                await self._ledger.append_event(holder, "told_of_copies", {"text": str(text)})
            except Exception:  # noqa: BLE001 - the note is for the record; it was told all the same
                logger.warning("noting what a session was told of its copies failed", exc_info=True)

    def _copies_dropped(self, session: _Session) -> None:
        """Make what copies finished or handed back, told of in a text the session did not take, to be told again."""
        if not session.copies_dropped and not session.handed_dropped:
            return
        self._copies_on_the_way.difference_update(session.copies_dropped)
        if session.handed_dropped:
            self._handed_back_news[session.session_id] = [
                *session.handed_dropped,
                *self._handed_back_news.get(session.session_id, []),
            ]
        session.copies_dropped, session.handed_dropped = [], []
        self._news_of_copies(session.session_id)

    async def _copies_taken(self, session: _Session, told: list[str]) -> None:
        """Put on record that a session took the text that told it what copies of it finished (``_tell_of_copies``)."""
        for commission_id in told:
            try:
                await self._ledger.append_event(
                    commission_id, "told_to_session", {"commission_id": session.commission_id, "steered": True}
                )
            except Exception:  # noqa: BLE001 - told again with the next commission, which does little harm
                logger.warning("noting that a session was told of what a copy of it did failed", exc_info=True)
            finally:
                self._copies_on_the_way.discard(commission_id)

    async def _opening_of_question(self, commission: Commission) -> str:
        """Return what opens the commission in which a copy of a session answers a question another session asked it.

        The question is what the commission was filed with; who asked is
        the session of the commission it was filed under.
        """
        import secrets

        from config.nekopower_settings import SESSION_ASK_MAX_REQUESTS
        from config.prompts import prompts_nekopower_sessions as words

        asker = await self._ledger.get(commission.parent_id) if commission.parent_id else None
        place = await self._ledger.get_session(asker.session_id) if asker is not None and asker.session_id else None
        copy = await self._ledger.get_session(commission.session_id) if commission.session_id else None
        lanlan = place.lanlan_name if place is not None else commission.lanlan_name
        whose = (
            words.SESSION_WHOSE if copy is None or copy.lanlan_name == lanlan else words.SESSION_WHOSE_OTHER
        ).format(lanlan_name=lanlan)
        nonce = secrets.token_hex(8)
        intro = words.ASK_OPENING_FRAME.format(
            asker=place.name if place is not None else commission.parent_id,
            whose=whose,
            requests=SESSION_ASK_MAX_REQUESTS,
            nonce=nonce,
        )
        return words.frame(intro, commission.dispatch_text, nonce)

    async def _opening_of_notes(self, commission: Commission) -> str:
        """Return what opens a commission that was filed for notes from other sessions: what it is, and the notes, framed.

        Put together anew whenever the commission is opened (each time
        with frames of their own): the notes are on record with the
        commission they were handed to.
        """
        from config.prompts import prompts_nekopower_sessions as words

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
        from config.prompts import prompts_nekopower_brain as texts

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

    async def _space_brief(
        self, commission: Commission, receiving: Callable[[], bool] = lambda: True, *, rules_only: bool = False
    ) -> str | None:
        """Return workspace context, an empty string for an empty space, or None if unavailable.

        A space that cannot be read does not keep a task agent from starting.
        ``rules_only``: the user's rules without the indexes (``TaskAgentSpace.brief``).
        """
        if self._space is None:
            return ""
        async def read(action: _SessionAction) -> str:
            action.record.scope_ref = commission.session_id
            return await self._space.brief(commission.project, rules_only=rules_only)

        for attempt in range(2):
            if not receiving():
                break
            run = self._permissions.current_run(commission.id)
            try:
                return await self._perform_action(commission.id, "space_brief", read, lambda: run() and receiving())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # An unavailable brief does not stop a commission.
                if attempt == 0 and not run():
                    continue  # A peer note rotated the old loan. Read afresh once.
                logger.warning("reading the task agent space failed (%s)", type(exc).__name__)
                break
        return None

    async def _repository_documents(self, workdir: str, session_dir: str | None = None) -> str:
        """Return the list of the files the repository a task agent works in keeps for agents; empty when it has none.

        A list and nothing more: names and sizes, with a sentence that says
        they are reference material and not instructions (``repo_docs``).
        The host reads none of them. Looked for in a worker thread, since
        it reads the file system; a folder that cannot be looked at does
        not keep a task agent from starting.

        In a directory of the host's own (the one a commission is given
        that names none) only that directory is looked at, not what lies
        above it: see ``repo_docs.listing``. Such a directory is below
        the folder the host keeps its data in, or among the sessions
        where this commission's session is kept (``session_dir``): the
        folder for the data may have been changed since the commission
        was first taken up, and its session and working directory stayed
        where they were (``_session_dir_of``). A task agent started by
        another works inside that one's directory, among the same
        sessions.

        Waited for no longer than ``REPO_DOCUMENTS_SCAN_SECONDS``: a folder
        on a network share that does not answer would keep the commission
        from starting, at work by its status and with nothing done. Past
        that the task agent starts without the list. The look itself
        cannot be stopped. It is made in a thread of its own
        (``run_detached``), which the host does not wait for when it
        exits and which is none of the threads its other file work is
        done in; and no more than ``REPO_DOCUMENTS_SCANS_AT_ONCE`` are
        under way: with that many out, the next task agent starts
        without the list at once.
        """
        from pathlib import Path

        from config.nekopower_settings import REPO_DOCUMENTS_SCAN_SECONDS, REPO_DOCUMENTS_SCANS_AT_ONCE
        from nekopower.host import repo_docs
        from nekopower.host.detached import NoThread, run_detached
        from nekopower.runtime.permissions import is_inside

        def look() -> str:
            places = [self._paths.root()]
            if session_dir:
                # ``.../sessions/<commission>``: the sessions of its project.
                places.append(Path(session_dir).parent)
            own = False
            for place in places:
                try:
                    own = own or is_inside(Path(workdir), place)
                except (OSError, ValueError):
                    continue
            return repo_docs.listing(workdir, upward=not own)

        if self._document_scans >= REPO_DOCUMENTS_SCANS_AT_ONCE:
            logger.warning("looks for the files a repository keeps for agents have not come back; none is started")
            return ""

        def back(done: asyncio.Future[str]) -> None:
            self._document_scans -= 1
            if not done.cancelled() and done.exception() is not None:
                # Told here when nobody waits for it any more.
                logger.warning(
                    "listing the files a repository keeps for agents failed (%s)", type(done.exception()).__name__
                )

        self._document_scans += 1
        try:
            scan = run_detached(look, name="nekopower-repo-documents")
        except NoThread:
            # No thread could be started for it: nothing is under way.
            self._document_scans -= 1
            logger.warning("no thread could be started to list the files a repository keeps for agents")
            return ""
        scan.add_done_callback(back)
        try:
            # Shielded: the look goes on in its thread whoever stops waiting
            # for it, and is counted until it comes back.
            return await asyncio.wait_for(asyncio.shield(scan), REPO_DOCUMENTS_SCAN_SECONDS)
        except asyncio.TimeoutError:
            logger.warning("listing the files a repository keeps for agents took too long; it starts without the list")
            return ""
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - it starts without the list
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
            more: dict[str, Any] = {}
            if not commission.workdir and project.workdir:
                from pathlib import Path

                if await asyncio.to_thread(Path(project.workdir).is_dir):
                    more["workdir"] = project.workdir
            # The project and the note of it together (``Ledger.file_under_project``).
            return await self._ledger.file_under_project(commission.id, project.name, **more) or commission
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - it is one of the commissions that belong to no project
            logger.warning("filing a commission under a project failed", exc_info=True)
            return commission

    async def _launch(
        self, commission_id: str, *, prompt: str | None = None, still_wanted: Callable[[], bool] | None = None
    ) -> bool:
        """Start a commission whose place is taken; a place it leaves unused goes to the queue."""
        with call_audit.commission_entry():
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
        as well when NekoPower was switched off meanwhile.)
        """
        session = self._sessions.get(commission_id)
        if session is None:
            return False

        def unwanted() -> bool:
            return still_wanted is not None and not still_wanted()

        def receiving() -> bool:
            return (
                self._sessions.get(commission_id) is session and not session.closing and not unwanted()
                and (session.stands is None or session.stands.status not in ENDED_STATUSES)
            )

        async def open_if_authorized(runtime, spec) -> bool:
            # A preset without memory is told nothing that was noted before:
            # it works with what it is handed. The user's rules it is told
            # like any other; they are no memory, and it may run commands.
            brief = await self._space_brief(commission, receiving, rules_only=not template.remembers)
            if self._closed or self._sessions.get(commission_id) is not session:
                return False
            if session.closing or (session.stands is not None and session.stands.status in ENDED_STATUSES):
                self._discard(commission_id)
                return False
            if unwanted():
                await self._ledger.transition(commission_id, "cancelled", reason=_STOPPED_MEANWHILE_REASON)
                self._discard(commission_id)
                return False
            if brief is None:
                spec = spec.model_copy(update={"workspace_brief_unavailable": True})
            elif brief:
                spec = spec.model_copy(update={"instructions": f"{spec.instructions}\n\n{brief}"})
            await runtime.start(spec)
            session.opened_in_runtime = True
            return True

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
            if not await self._stop_compacting(session.session_id):
                # The runtime has not said that it closed the session the
                # host had open. Opened for the commission now, what the
                # runtime says of that earlier opening would be taken for
                # the commission's. So the commission fails, with this as
                # its reason, and can be handed in again; the session
                # stays the host's until the runtime has said that it is
                # closed, or is gone.
                raise RuntimeError(_SESSION_NOT_GIVEN_BACK)
            commission = await self._ledger.get(commission_id)
            if commission is None or commission.status not in ("queued", "blocked", "failed", "waiting_user"):
                self._discard(commission_id)
                return False
            kept = await self._ledger.get_session(session.session_id)
            if kept is None:
                raise RuntimeError("the session of the commission is not on record")
            # The model the session is sent to: settled when it was made
            # (now, where it was not), and read anew.
            choice = await self.session_model(kept)
            config = choice.config
            if not config or not config.get("model"):
                raise RuntimeError("no model is configured for task agents")
            await self._use_project(commission)
            kept = await self._ledger.get_session(session.session_id)
            if kept is None:
                raise RuntimeError("the session of the commission is not on record")
            session.cleared_before = kept.status == "cleared"
            # What the session may do and is told of its role. One whose
            # preset cannot be used fails here, with the reason.
            template = await self._template_of(kept)
            if (
                kept.kind == "fork"
                and kept.forked_from
                and _carried_on(commission)
                and await self._ledger.carried_on_in(kept.id)
            ):
                # A commission of the project carried on in a copy of its
                # session (``_carry_on_in_copy``), also when it is taken
                # up again there, or one that follows it up there; not one
                # filed in a copy made by other means.
                session.carries_on_for = kept.forked_from
            # Where a session is kept and where its task agent works are the
            # session's, on record from when it was first opened. Until then
            # they follow from what its first commission brings: the working
            # directory of its project, or the one it was started with.
            place = dataclasses.replace(
                kept, workdir=kept.workdir or commission.workdir, project=kept.project or commission.project
            )
            from nekopower.runtime.protocol import GatewaySpec

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
            # (In this session: one begun in a copy of it and handed back
            # took its opening message there, which this one never saw.)
            opened_at = await self._ledger.opened_in(commission.id, session.session_id)
            opened = opened_at is not None
            at_it = opened and await self._ledger.opened_last(session.session_id) == commission.id

            builtin_only = template.needs_builtin
            if not choice.agent:
                # A model the user set for the preset is used in the
                # built-in runtime only, for now: dsh starts its processes
                # by the task agent model. A session that was begun in dsh
                # goes on there, with that model, as before.
                if begun_in == RUNTIME_DSH:
                    choice = await self.role_models.agent()
                    config = choice.config
                    if not config or not config.get("model"):
                        raise RuntimeError("no model is configured for task agents")
                    # On record as what it is sent to, for whoever reads it.
                    await self._keep_model(kept, choice, True)
                else:
                    builtin_only = True
            model = await self._model_of(config)
            session.model = (config.get("base_url"), config.get("model"))
            session.free = choice.free

            runtime_name, runtime, fallback = await self._choose_runtime(
                commission, model, begun_in, builtin_only=builtin_only
            )
            # Uninstall/replace must see this commission before gateway setup yields.
            session.runtime, session.runtime_name = runtime, runtime_name
            url = await self._gateway.start() if self._gateway is not None else None
            # Freeze the opening in delivery order immediately before its grant.
            # Later messages stay in _notes and are delivered as steering.
            notes = self._notes.pop(commission.id, [])
            carried = self._carried.pop(commission.id, [])
            session.carried = [*carried, *session.carried, *notes]
            gateway_spec = None
            if self._gateway is not None:
                from config.nekopower_settings import FIRST_TURN_DEVICE_TOOLS

                groups: tuple[str, ...] = ()
                browser_context = None
                custom_tools = {"tools", "tool_choice"}.intersection(model.extra_body or {})
                if FIRST_TURN_DEVICE_TOOLS and kept.kind != "fork" and not custom_tools:
                    from nekopower.device_intent import classify_device_intent

                    # On relaunch, a short follow-up like "click login" belongs to the
                    # same commission's browser task. A new commission starts afresh.
                    # After this commission's session has been summed up, the
                    # original dispatch is history. A restart or idle wake
                    # must not put a folded device back in the request.
                    summed_up = opened_at is not None and kept.compacted_at is not None and kept.compacted_at >= opened_at
                    follow_up = "\n".join(piece for piece in session.carried if not isinstance(piece, (_Handed, _Unsaid, _Found)))
                    if not summed_up:
                        groups, browser_context = classify_device_intent(
                            commission.dispatch_text + "\n" + follow_up, browser_context=None,
                        )
                    elif follow_up:
                        _, browser_context = classify_device_intent(commission.dispatch_text, browser_context=None)
                        groups, browser_context = classify_device_intent(follow_up, browser_context=browser_context)
                    if groups and runtime_name == RUNTIME_DSH:
                        supports = getattr(runtime, "supports_device_schemas", None)
                        if supports is None or not supports(model):
                            groups = ()
                token = self._gateway.issue(
                    commission.id,
                    candidate_plugin_ids=self._candidates.get(commission.id, ()),
                    supports_images=model.supports_images,
                    # A copy of a session changes nothing, whichever way; nor
                    # does a session whose preset is without the tool that calls.
                    reads_only=kept.kind == "fork" or template.calls_nothing,
                    first_turn_groups=groups,
                )
                gateway_spec = GatewaySpec(
                    url=url, token=token, opening_browser_context=browser_context if groups else None,
                )

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
            # Where its session works was read above, and a commission that
            # was stuck or failed is not under way until the line before
            # this one: the user may have changed where the project works in
            # between, which nothing under way kept them from. From here on
            # it is under way and the directory stays (``Ledger.move_project``),
            # so it is read once more: a session that works somewhere else
            # by now starts there, and that is what is put on record, not
            # the directory this start began with.
            again = await self._ledger.get_session(session.session_id)
            if again is not None and again.workdir and again.workdir != kept.workdir:
                kept = again
                place = dataclasses.replace(
                    kept, workdir=kept.workdir, project=kept.project or commission.project
                )
                session_path, workdir, begun_in = await asyncio.to_thread(prepare)
                commission = await self._ledger.update(commission.id, workdir=workdir) or commission
            # A blocked or failed commission must be working again before its
            # opening brief can reserve a call against the new run.
            spec = await self._spec(
                commission, kept, model, workdir=workdir, session_dir=session_path, gateway=gateway_spec,
                template=template,
            )
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
            # What the commission is: said once, in the session it is worked
            # on in, and again only where that is not there to be read, or
            # where the session has been at another commission since.
            # In three wordings, of which one is used: for a session whose
            # conversation is there to be read in the runtime that takes the
            # commission, for one that has none there (never begun, removed
            # since, or in the other runtime), and for the first of these
            # when the session was at another commission since.
            def opens_in(runtime_name: str) -> bool:
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
                return prompt is None or not opened or begun_in != runtime_name or not at_it

            # With each of them, what the repository it works in keeps for
            # agents: looked for once, where the session works. Not looked
            # for when none of them will be said, which is known here for
            # the built-in runtime only: dsh may yet not take the session
            # up, and the commission is opened after all.
            unsaid = runtime_name == RUNTIME_BUILTIN and not opens_in(RUNTIME_BUILTIN)
            documents = "" if unsaid else await self._repository_documents(workdir, session_path)
            # Once, for whichever wording is said: what it says of copies of
            # the session is what is put on record as told (``_note_opened``).
            try:
                copies = await self._copies_said(commission)
            except Exception:  # noqa: BLE001 - it starts all the same; what copies did is told with the next
                logger.warning("reading what copies of a session finished failed", exc_info=True)
                self._forks_said.pop(commission.id, None)
                self._handed_said.pop(commission.id, None)
                copies = ""

            async def worded(*, cleared: bool, back: bool = False, excerpt: str = "") -> str:
                return await self._opening(
                    commission, cleared=cleared, back=back, excerpt=excerpt, documents=documents, copies=copies
                )

            # A session that holds nothing in the runtime that takes the
            # commission (the second wording) is told, besides, what she
            # and the user said just before it: a session with something
            # in it has that to tell what the commission refers to.
            excerpt_as_of = self._times_dropped(commission)
            excerpt = self._excerpt(commission)
            onward = await worded(cleared=False)
            anew = await worded(cleared=True, excerpt=excerpt) if commission.continues or excerpt else onward
            back_at_it = await worded(cleared=False, back=True) if opened and not at_it else onward

            def first_message(runtime_name: str) -> tuple[list[str], bool]:
                here = begun_in == runtime_name
                opens = opens_in(runtime_name)
                # Which runtime it is decides the wording, as it decides
                # whether it is said: a commission that follows another up
                # in a session that began in dsh and is taken by the
                # built-in runtime finds nothing "above", and is told what
                # came of the other instead.
                said = anew if not here else back_at_it if opened else onward
                parts = [*([said] if opens else []), *carried, *([prompt] if prompt is not None else [])]
                # Keep answer envelopes intact until the runtime is ready to receive them.
                return [*parts, *notes], opens

            text, opens = first_message(runtime_name)
            if not await open_if_authorized(runtime, spec):
                return False
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
                if not await open_if_authorized(runtime, spec):
                    return False
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
                if self._closed or self._sessions.get(commission.id) is not session or current is None or current.status != "working":
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
                # Taken up again after it was at work on it before (its quota
                # ran out then, say): part of it may be done, and what she is
                # told, and keeps, must not say it never started.
                await self._fail(
                    commission.id,
                    _QUOTA_RUN_REASON if opened else _QUOTA_REASON,
                    said="quota_run" if opened else "quota",
                )
                await self._close_session(session)
                # What was passed on while the failure was being written.
                self._keep_unsent(session)
                return False
            # The wake requests whose text this first message brings: what
            # was handed to the commission while it had no session open (it
            # rested, or was being started), and, for one that was filed for
            # notes and is told what it is, those notes. (Read before the
            # last look at whether it was stopped: reading waits.)
            riding = [wake for piece in session.carried for wake in getattr(piece, "wakes", ())]
            if opens and commission.source == "session":
                riding = [*riding, *await self._ledger.opening_wakes(commission.id)]
            has_answers = any(isinstance(piece, _Unsaid) for piece in session.carried)
            for attempt in range(2):
                run = self._permissions.current_run(commission.id) if has_answers else None
                async with self._answer_delivery(session, session.carried) as render:
                    while True:
                        if await stopped_meanwhile():
                            # The stop has closed the session already.
                            await self._close_session(session)
                            return False
                        if not excerpt or self._times_dropped(commission) == excerpt_as_of:
                            # (Nothing is awaited between this and the message
                            # being sent: it is not stopped, and the session is
                            # still its own, when it is.)
                            break
                        # What is kept of the main dialogue was dropped while the
                        # session was being set up, which takes a while (NekoPower
                        # went off; it may be on again by now). What was said
                        # before that is not handed over after it: the opening
                        # message is made again, without it. Making it waits for
                        # something, so whether the commission was stopped, and
                        # whether more was dropped, is looked at once more.
                        excerpt_as_of = self._times_dropped(commission)
                        excerpt = self._excerpt(commission)
                        anew = await worded(cleared=True, excerpt=excerpt) if commission.continues or excerpt else onward
                        text, opens = first_message(runtime_name)
                    # Whether a person's words are in it: what she dispatched or the
                    # user said, told to the session for the first time (the message
                    # that tells a commission what it is, unless it was filed for
                    # notes), or something kept to be said. Not where it is only
                    # what wake requests brought; nor is it for the message that
                    # says once more what the commission is, to a session that was
                    # at another one since or holds nothing of it: nobody spoke now.
                    # (A question another session asked, which a copy answers, is
                    # what a model wrote.)
                    if run is not None and not run() and attempt == 0:
                        # A peer message ended the receiving run while we checked.
                        # Close its pending row and borrow as the current run once.
                        continue
                    said_first = opens and not opened and commission.source not in ("session", "ask")
                    nobody = not said_first and all(isinstance(piece, (_Handed, _Unsaid, _Found)) for piece in session.carried)
                    await self._send_text(runtime, session, "prompt", render(text), opening=opens, wakes=riding, human=not nobody)
                break
            session.ready = True
            # What was passed on while the session was being set up. Like the
            # prompt, it is the user's until the run is seen at work.
            for note in self._notes.pop(commission.id, []):
                session.carried.append(note)
                if not await self._send_text(runtime, session, "steer", note):
                    await self._wake_closed_parent(commission.id, note)
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
            # Its work may have gone through before what came after it was
            # refused (what was passed on meanwhile): then it was at work.
            said = "run" if session.sent else "start"
            await self._fail(commission_id, f"the task agent could not be started: {exc}", said=said, detail=str(exc))
            self._keep_unsent(session)
            return False

    async def _model_of(self, config: dict[str, Any]) -> Any:
        """Return the ``ModelConfig`` a session is opened with, from the model as it is configured.

        With what is remembered of the model where the configuration does
        not say: whether it sees pictures, the window its sessions are
        managed for, and the length from which a conversation is summed up.
        """
        from nekopower.runtime import ModelConfig

        if "supports_images" not in config:
            config = {**config, "supports_images": await self._sees_images(config)}
        if "context_window" not in config:
            config = {**config, "context_window": await self._context_window(config)}
        if "compact_at" not in config:
            config = {**config, "compact_at": await self._compact_at(config)}
        return ModelConfig(**config)

    async def _spec(
        self, commission: Commission, kept: Session, model: Any, *, workdir: str, session_dir: str, gateway: Any,
        idle_compaction: bool = False, template: Any = None,
    ) -> Any:
        """Return what a runtime is given to open a session with, for the commission that is worked on in it.

        ``template`` is the preset the session is opened by (``_template_of``;
        looked up here when it is not given): the tools it withholds are
        withheld besides those below, its sessions make no more requests
        in one run than it allows, and what it says of the role is in the
        instructions.

        ``kept`` is the session as the ledger has it. One that was made
        from another by copying what was said in it is opened without the
        tools that write files, run commands and call capabilities
        (``TASK_AGENT_FORK_WITHHELD_TOOLS``): it has the working directory
        of the session it was copied from, and a working directory is
        written by one session at a time. Its token for the gateway
        calls nothing either (``_start``).
        """
        from config.nekopower_settings import SESSION_ASK_MAX_REQUESTS, TASK_AGENT_FORK_WITHHELD_TOOLS
        from nekopower.runtime import TaskAgentSpec
        from nekopower.runtime.protocol import RuntimeLimits

        image_retention: dict[str, int] = {}
        image_pools: dict[str, str] = {}
        if self._gateway is not None:
            # For every built-in tool that hands pictures back, whether it
            # can be called right now or not: every one of them is reached
            # through the gateway's call tool, one that is switched on while
            # the task agent works included, and its screenshots are
            # limited too.
            image_retention, image_pools = self._image_retention(self._registry.builtins())
        # A task agent started by a task agent starts none itself, and
        # neither writes to sessions outside its team nor asks to be woken
        # (``_CHILD_DISABLED_TOOLS``).
        withheld = set(_CHILD_DISABLED_TOOLS) if commission.parent_id is not None else set()
        if kept.kind == "fork":
            withheld.update(TASK_AGENT_FORK_WITHHELD_TOOLS)
        if template is None:
            template = await self._template_of(kept)
        withheld.update(template.withheld())
        if kept.kind != "project":
            # Only the leader of a project hands a commission over.
            withheld.add("hand_over")
        # A copy that answers a question makes no more requests than this,
        # and no session more than its preset allows.
        most = [SESSION_ASK_MAX_REQUESTS] if commission.source == "ask" else []
        if template.max_requests is not None:
            most.append(template.max_requests)
        limits = RuntimeLimits(max_turns=min(most)) if most else RuntimeLimits()
        roster = await self._roster(commission, kept, template)
        return TaskAgentSpec(
            session_id=kept.id,
            workdir=workdir,
            session_dir=session_dir,
            model=model,
            instructions=self._base_instructions(
                commission, with_gateway=gateway is not None, template=template, roster=roster
            ),
            idle_compaction=idle_compaction,
            top_level=commission.parent_id is None,
            disabled_tools=sorted(withheld),
            image_retention=image_retention,
            image_pools=image_pools,
            gateway=gateway,
            limits=limits,
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
        from config.nekopower_settings import TASK_AGENT_CONTEXT_WINDOW

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
        from config.nekopower_settings import TASK_AGENT_SCREENSHOTS_KEPT
        from nekopower.host.gateway import CALL_TOOL

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
        if not session.free:
            # Not sent with the free key: nothing is counted.
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

    @asynccontextmanager
    async def _answer_delivery(self, session: _Session, parts: list[str]):
        """Reserve at the receiving commission; render only at the final synchronous handoff."""
        if not any(isinstance(part, _Unsaid) for part in parts):
            yield "\n\n".join
            return

        unavailable = "the session answer could not be authorized for delivery"

        def withheld(texts: list[str], notice: str = DENIED_BY_SETTINGS) -> str:
            return "\n\n".join(notice if isinstance(part, _Unsaid) else part for part in texts)

        owner = session.commission_id
        live = self._permissions.current_run(owner)
        async with AsyncExitStack() as stack:
            # The answer was produced elsewhere. Its delivery belongs to the recipient.
            stack.enter_context(call_audit.commission_entry())
            record = None
            try:
                record = await call_audit.start_call(self._ledger, owner, "session_action", "receive_answer")
                record.status = "cancelled"
                record.scope_ref = session.session_id

                async def settle_unsubmitted():
                    if record.status != "running":
                        await call_audit.finish_call(record, record.status)

                stack.push_async_callback(settle_unsubmitted)
                decision = await self._permissions.check(
                    owner, "outside_read", scope=SessionActionScope("host:session", "receive_answer", owner), hold_once=True,
                )
            except Exception as exc:  # Fail closed without stopping the recipient's other work.
                if record is not None:
                    record.status, record.reason = "failed", "delivery_unavailable"
                logger.warning("could not register answer delivery (%s)", type(exc).__name__)
                yield lambda texts, **_: withheld(texts, unavailable)
                return
            grant = decision.grant_id
            stack.callback(self._permissions.release_grant, grant)
            record.grant_id = self._permissions.authorization_ref(grant)
            def render(texts: list[str], *, steer: bool = False) -> str:
                if not live():
                    return withheld(texts, unavailable)  # The unsubmitted receipt stays cancelled.
                if (
                    decision.allow and self._permissions.grant_active(grant)
                    and all(part.recipient == owner for part in texts if isinstance(part, _Unsaid))
                ):
                    record.status = "running"  # The runtime's taken or refusal event settles the row.
                    session.answer_receipts[session.sent + 1] = (record, texts[0] if steer else None)
                    return "\n\n".join(texts)
                record.status = "denied"
                if not decision.allow and decision.reason != DENIED_BY_SETTINGS:
                    return withheld(texts, unavailable)
                return withheld(texts)

            # A prompt can start a new task. It must not inherit this delivery as
            # the audit parent of everything the recipient does afterwards.
            yield render

    async def _send_text(
        self,
        runtime: Any,
        session: _Session,
        command: str,
        text: str,
        *,
        opening: bool = False,
        wakes: Iterable[int] | None = None,
        human: bool | None = None,
    ) -> bool:
        """Send and count the text; return False when an answer needs routing to the current run.

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
        from another session, a time that has come), and so is a
        ``_Unsaid`` (the answer of a copy of another session); every
        other for the user's. The runtime is told only where it is not: a note
        carries nobody's permissions, and the run it starts is not one
        the user spoke in.
        """
        if human is None:
            human = not isinstance(text, (_Handed, _Unsaid, _Found))
        if isinstance(text, _Unsaid):
            live = self._permissions.current_run(session.commission_id)
            async with self._answer_delivery(session, [text]) as render:
                if (
                    live() and self._sessions.get(session.commission_id) is session and not session.closing
                    and (session.stands is None or session.stands.status not in ENDED_STATUSES)
                ):
                    return await self._send_text(runtime, session, command, render([text], steer=command == "steer"), opening=opening, wakes=wakes, human=False)
            return False
        said = {} if human else {"human": False}
        riding = list(wakes if wakes is not None else getattr(text, "wakes", ()))
        session.sent += 1
        number = session.sent
        session.serial += 1
        serial = session.serial
        session.numbered[serial] = number
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
            failed = session.answer_receipts.pop(session.numbered.get(serial), None)
            _withdraw(session, serial)
            if opening:
                session.opening_sent = False
            if woke:
                session.run_ended = True
            if failed is not None:
                await call_audit.finish_call(failed[0], "failed")
            raise
        return True

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

    def _cancel_control_calls(self, commission_id: str) -> None:
        session = self._sessions.get(commission_id)
        if session is not None:
            for record, grant in session.control_calls.values():
                self._permissions.release_grant(grant)
                self._settle_call_soon(record, "cancelled")
            session.control_calls.clear()

    def _settle_call_soon(self, record: call_audit.Call, status: str) -> None:
        settlement = asyncio.create_task(call_audit.finish_call(record, status))
        self._control_settlements.add(settlement)
        settlement.add_done_callback(self._control_settlements.discard)

    def _finish_control_event(self, session: _Session, event: dict[str, Any]) -> None:
        pending = session.control_calls.pop(str(event.get("tool_call_id", "")), None)
        if pending is not None:
            record, grant = pending
            self._permissions.release_grant(grant)
            self._settle_call_soon(record, "failed" if event["is_error"] else "completed")

    def _close_answer_receipts(self, session: _Session) -> None:
        for record, _ in session.answer_receipts.values():
            self._settle_call_soon(record, "cancelled")
        session.answer_receipts.clear()

    def _discard(self, commission_id: str) -> None:
        """Give a commission's place back: its session is not, or no longer, open in a runtime for it.

        The commission goes on holding its session until it is known to be
        over (``_settle_hold``): one that waits for an answer keeps it.
        """
        self._cancel_control_calls(commission_id)
        session = self._sessions.pop(commission_id, None)
        if session is not None:
            self._close_answer_receipts(session)
        self._candidates.pop(commission_id, None)
        self._excerpts.pop(commission_id, None)
        # What its opening message was to tell of copies: said with the next
        # one, should this one's not have been taken.
        self._forks_said.pop(commission_id, None)
        self._handed_said.pop(commission_id, None)
        self._drop_search_ruling(commission_id)
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
        if commission_id is None or self._closed or self._in_hand_for(commission_id, session_id):
            return
        commission = await self._ledger.get(commission_id)
        if self._held_by.get(session_id) != commission_id:
            # Let go meanwhile, by another look at the same hold, and maybe
            # taken by the next commission: its status is that one's to say.
            return
        try:
            # By what the commission is when this is written, not by what
            # was read above: it may have been taken up again since (an
            # answer came), and is then at work, which is on record. What
            # follows goes by that same read, not a later one: a commission
            # that ended right after it had its session's status left as it
            # was, and keeps the hold for the look its end brings, which
            # writes the status. Let go here, that look would find the hold
            # gone and write nothing, and the session would stay ``running``.
            commission = await self._ledger.settle_session(session_id, commission_id)
        except Exception:  # noqa: BLE001 - the status is for showing; the hold is what counts
            # The hold is let go all the same: left here, nothing else
            # would ever start in the session.
            logger.warning("noting where a session stands failed", exc_info=True)
        # Looked at again: it may have been taken up, or let go, meanwhile.
        if self._held_by.get(session_id) != commission_id or self._closed or self._in_hand_for(commission_id, session_id):
            return
        same_session = (
            commission is not None
            and commission.session_id == session_id
            and self._session_of.get(commission_id, session_id) == session_id
        )
        if same_session and commission.status not in ENDED_STATUSES and not commission.resting:
            # It waits for the user, or for task agents it started.
            return
        # (One that rests lets its session go like one that is over: nobody
        # is at work on it until what it waits for comes, which may be
        # hours, and other commissions of the session are worked on
        # meanwhile. Woken, it waits for its session like any other.)
        del self._held_by[session_id]
        if same_session:
            # Nothing more is asked of the user for it, and nobody is told
            # to hand anything over: it is over (taken up again, a stuck
            # one is asked about anew).
            self._usage_settled(commission)
        if self._queue:
            self._spawn(self._start_next())
        # A note that waited because this commission was in the way may go now.
        self._kick_waker()
        # What its requests used that the ledger did not take is tried again.
        self._settle_usage_of(session_id)
        unused_copy = self._unused_ask_copies.pop(session_id, None)
        if unused_copy is not None:
            await self._discard_copy(unused_copy)
        else:
            await self._schedule_idle_compact(session_id)

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
        if commission.status in ENDED_STATUSES:
            self._side_said.pop(commission.id, None)
        if (
            commission.status in ENDED_STATUSES
            and previous not in ENDED_STATUSES
            and not self._closed
            and commission.route == "task_agent"
            and commission.session_id
            and _carried_on(commission)
        ):
            self._spawn(self._ended_in_copy(commission.session_id))
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
            and not self._in_hand_for(commission.id, session_id)
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
            # What copies finished that it was told of and did not take: told
            # with the next commission opened in it, or steered in again.
            for told in [*session.copies_told.values(), session.copies_dropped]:
                self._copies_on_the_way.difference_update(told)
            session.copies_told, session.copies_dropped = {}, []
            handed_back = [pair for pairs in session.handed_told.values() for pair in pairs] + session.handed_dropped
            session.handed_told, session.handed_dropped = {}, []
            if handed_back and session.session_id:
                # Told again, should they still wait for it then (``_tell_of_copies``).
                self._handed_back_news[session.session_id] = [
                    *handed_back,
                    *self._handed_back_news.get(session.session_id, []),
                ]
        if commission_id in self._queue or commission_id in self._requeueing:
            # Queued to be taken up again: what was said meanwhile goes along
            # with it, and so do its candidate plugins. It goes on holding
            # its session (unless it already moved out of an old copy).
            self._cancel_control_calls(commission_id)
            if session is not None:
                self._close_answer_receipts(session)
            self._sessions.pop(commission_id, None)
            if (
                session is not None
                and session.session_id
                and session.session_id != self._session_of.get(commission_id)
                and self._held_by.get(session.session_id) == commission_id
                and not self._closed
            ):
                self._spawn(self._settle_hold(session.session_id))
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
                with call_audit.commission_entry():
                    await self._start(commission_id, prompt=self._resume.pop(commission_id, None))
            else:
                # Its session is not known, which every way into the queue
                # sees to: it must not stay queued for nobody.
                logger.error("queued commission %s has no session to start in", commission_id)
                self._resume.pop(commission_id, None)
                await self._fail(
                    commission_id, "the task agent could not be started: its session is not known", said="start"
                )

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
        from config.nekopower_settings import TASK_AGENT_EVENT_TEXT_MAX_CHARS

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
        from config.nekopower_settings import TASK_AGENT_EVENT_TEXT_MAX_CHARS

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
        from config.nekopower_settings import TASK_AGENT_EVENT_TEXT_MAX_CHARS

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
            # Whichever it said: a copy that failed may have left what
            # it could not remove itself. And what cannot be removed now
            # either is still this host's to remove (``_remove_fork_dir``).
            # Among what shutting down waits for before it is taken off
            # the list of what shutting down removes: the host may be
            # shutting down this moment, and would be gone, with the
            # copy on neither, before it is removed. Not waited for
            # here: what the runtimes say next (what a request used,
            # among it) is not held up by a directory that is slow to
            # go, or tried again.
            self._remove_fork_dir_soon(spoken_of, given_up)
            del self._forks_given_up[spoken_of]
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
        refused: list[int] = []
        refused_answer = None
        if kind == "ended":
            # In hand from here on, before anything is awaited (the first
            # of the writes below may be the one that does not answer):
            # see ``_Session.ending``.
            session.ending = event
        elif kind == "error":
            # Out of the count from here on, likewise: stopped at a write
            # below as the host shuts down, what the runtime says next to
            # be taken would be taken for the text it refused.
            refused_answer = _refused_answer(session, event)
            refused = _refusal(session, event)
            if refused_answer is not None:
                self._settle_call_soon(refused_answer[0], "failed")
            self._copies_dropped(session)
        if kind == "tool_end" and session.control_calls:
            self._finish_control_event(session, event)
        if session.untaken_answer is not None and _shows_work_taken(event, session.sent):
            session.untaken_answer = None
        if kind == "taken":
            await self._on_taken(session, event)
            return
        # A dsh device-view acknowledgement comes from a model step for this
        # commission. It can precede usage on a long opening prompt. Idle
        # maintenance and a built-in snapshot catch-up do not acknowledge input.
        working = _shows_run_at_work(event) or (kind == "compacted" and session.runtime_name == RUNTIME_DSH and session.sent > 0)
        if kind != "ended" and working:
            # A run is under way, also one the session began by itself (a
            # background job of its own finished).
            session.idle = False
        at_work = session.sent > 0 and working
        if session.carries_on_for is not None:
            # Before anything is written: a write that fails below would
            # leave a call that hands the commission back unnoticed.
            self._watch_copy(session, event)
        if at_work:
            session.carried = []
            if session.session_id in self._copy_news and session.carries_on_for is None:
                # News from copies held for it while it waited, or rested.
                self._news_of_copies(session.session_id)
        if kind == "usage":
            # Counted whether or not it can be noted among the commission's
            # events (a request with no text in front of it is the first
            # the host sees of a run, so that the commission was opened
            # is noted here as well): a ledger that does not take a note
            # may take what was used a moment later, and what it does
            # not take is owed. Left out when a note fails, the request
            # would be missing from the account, and from what the limit
            # goes by. What the noting failed with is raised as before,
            # once it is counted.
            try:
                if at_work:
                    await self._note_opened(session)
                await self._flush(session)
                await self._ledger.append_event(session.commission_id, str(kind), self._compact(event))
            finally:
                await self._on_usage(session, event)
            return
        if at_work:
            try:
                await self._note_opened(session)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the end of a run is acted on all the same, see below
                if kind not in ("ended", "error"):
                    raise
                # Like the event itself where it cannot be put on record:
                # an end that is not acted on would leave the senders of
                # what the run took without word, and the session's place
                # held. That the commission was opened is noted later.
                logger.warning("noting that a commission was opened failed; its run's end is acted on all the same", exc_info=True)
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
            if refused_answer is not None:
                envelope = refused_answer[1]
                if envelope is not None and not envelope.retried and not self._closed:
                    envelope.retried = True
                    self._spawn(self._wake_closed_parent(session.commission_id, envelope))
        elif kind == "model_capability":
            await self._on_model_capability(session, event)
        elif kind == "compacted":
            # A summary made in the course of a commission ends the count
            # of failed ones while idle (``_idle_compact_failures``).
            self._idle_compact_failures.pop(session.session_id, None)
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
        try:
            await self._ledger.append_event(session.commission_id, "opened", {"session_id": session.session_id})
        except asyncio.CancelledError:
            raise
        except Exception:
            # Not on record: noted with the next event of the session.
            session.opening_sent = True
            raise
        # What copies of the session finished, said with that message: left
        # among what an opening tells until it is on record as told, so that
        # it is not steered in meanwhile (``_tell_of_copies``).
        for told in list(self._forks_said.get(session.commission_id, ())):
            try:
                await self._ledger.append_event(told, "told_to_session", {"commission_id": session.commission_id})
            except Exception:  # noqa: BLE001 - told again with the next commission, which does little harm
                logger.warning("noting that a session was told of what a copy of it did failed", exc_info=True)
        self._forks_said.pop(session.commission_id, None)
        # And what its leader handed over, said with the same message.
        for told, ended_at in list(self._handed_said.get(session.commission_id, ())):
            try:
                # With the ending it was told of: one that ended anew since is told once more.
                await self._ledger.append_event(
                    told, "told_to_leader", {"commission_id": session.commission_id, "ended_at": ended_at}
                )
            except Exception:  # noqa: BLE001 - told again with the next commission, which does little harm
                logger.warning("noting that a leader was told what came of a commission it handed over failed", exc_info=True)
        self._handed_said.pop(session.commission_id, None)

    def _summary(self, text: str) -> str:
        from config.nekopower_settings import TASK_AGENT_RESULT_SUMMARY_MAX_TOKENS
        from utils.tokenize import truncate_to_tokens

        return truncate_to_tokens(text, TASK_AGENT_RESULT_SUMMARY_MAX_TOKENS)

    async def _on_report(self, session: _Session, event: dict[str, Any]) -> None:
        status = str(event.get("status") or "")
        summary = str(event.get("summary") or "")
        details = str(event.get("details") or "")
        if status == "progress":
            return
        if session.handing_over is not None:
            # Handed over already: whoever has it now reports on it.
            return
        commission_id = session.commission_id
        if status == "waiting":
            if session.carries_on_for is not None and session.handing_back is not None:
                # Handed back already: the session itself goes on with it,
                # and asks what it needs to there.
                return
            question = {"kind": "report", "question": summary, "details": details, "id": uuid.uuid4().hex}
            moved = await self._ledger.transition(
                commission_id,
                "waiting_user",
                reason="the task agent needs an answer",
                pending_question=json.dumps(question, ensure_ascii=False),
            )
            if moved.applied and moved.commission is not None:
                session.awaiting_prompt = True
                session.question_id = question["id"]
                session.run_ended = False
                session.held_answer = None
                if session.carries_on_for is not None and session.handing_back is None:
                    # A copy that was to hand its commission back asked the
                    # user instead: what it is to do next is the answer's to
                    # say. A call that changes something after it hands the
                    # commission back anew.
                    session.hand_back, session.hand_back_left, session.findings = None, None, []
                await self._reporter.question(moved.commission, question)
            return
        target = _REPORT_TO_STATUS.get(status)
        if target is None:
            return
        text = f"{summary}\n{details}" if details else summary
        if session.carries_on_for is not None and (session.hand_back is not None or session.handing_back is not None):
            # A copy that called a tool which changes something, and reports
            # (done, stuck) all the same: the change was not made, so the
            # commission is not over. What it reports is what it found, and
            # it goes back to the session itself as any copy that called
            # such a tool does.
            if session.handing_back is None:
                # What it wrote since it was told to write down what it found
                # came in pieces as it was streamed: joined as it came.
                found = "".join(session.findings).strip()
                await self._hand_back_or_later(session, said=f"{found}\n{text}".strip() if found else text)
            return
        await self._end(
            commission_id,
            target,
            reason=f"the task agent reported {status}",
            text=text,
            suggested_delivery=event.get("delivery"),
            result_summary=self._summary(text),
        )

    async def _run_over(self, commission_id: str) -> None:
        """A commission's run is over: what lasted for the run ends with it.

        That is the desktop and the browser it occupied (another commission
        may have them now), and what the user allowed "once" for the
        keyboard and mouse and for the browser.
        """
        self._cancel_control_calls(commission_id)
        self._permissions.end_run(commission_id)
        if self._leases is not None:
            self._leases.release(commission_id)
        session = self._sessions.get(commission_id)

        def discard_source(receipt):
            if session is not None:
                session.job_sources.pop(receipt, None)

        await self._execution_audit.end_run(commission_id, discard_source)

    async def _remind_of_window(self, session: _Session, base_url: Any, model: Any, was: int) -> None:
        """Have the user told, once for each model, that its window is smaller than was assumed.

        The sessions of the model are managed for the smaller window from
        now on without anyone doing anything; the user is told so that
        they set the model's context length themselves. Said to her, with
        her next turn (``Reporter.note``): no model is called for it.
        """
        from config.prompts.prompts_nekopower_brain import MODEL_WINDOW_REMINDER_TEXT
        from nekopower.host.task_brain import _texts

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
        from nekopower.runtime.protocol import PROCESS_EXITED_ERROR

        commission_id = session.commission_id
        reason = event.get("reason")
        if session.wind_up_left is not None:
            # A run of one that was told to hand over what it has is over.
            # Given more of the same round (it asked the user something,
            # and is answered), it is told again: see ``_wind_up_step``.
            session.wind_up_told_again = False
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
            await self._run_over(commission_id)
        if session.handing_back is not None:
            # A copy that handed its commission back (``_hand_back``): what
            # its run says as it stops decides nothing, and once it is
            # closed the commission goes on in the session itself.
            if reason == "closed":
                await self._handed_back(session)
            return
        if session.handing_over is not None:
            # A leader that handed its commission over (``_cb_hand_over``):
            # what its run says as it stops decides nothing. The session is
            # closed, and once it is the commission goes on in the task
            # agent it was handed to.
            if reason == "closed":
                await self._handed_over(session)
            elif not session.closing:
                try:
                    await self._close_session(session)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - asked again by a task of its own
                    logger.warning(
                        "closing a leader's session to hand its commission over failed; asked again", exc_info=True
                    )
                    self._spawn(self._close_leader_later(session))
            return
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
            if session.hand_back is not None and await self._hand_back_or_later(session, said=str(event.get("text") or "")):
                # A copy that was told to write down what it found for the
                # session itself did, and ended its run: handed back. So is
                # one whose run ended right after the call that changes
                # something (it made as many requests as it may), before
                # it was told: what it said last is what it found.
                return
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
                error = str(event.get("error") or "")
                # Nothing of it was taken: it did not start, rather than stop.
                await self._fail(commission_id, error or "the run failed", said="start", detail=error)
                self._keep_unsent(session)
            else:
                # A run takes its prompt into the session's transcript before
                # its first model request, and the next start goes on from
                # that transcript: what was kept is there, and is not sent
                # again; nor is what the commission is, if this run took that.
                session.carried = []
                if event.get("error") != PROCESS_EXITED_ERROR:
                    try:
                        await self._note_opened(session)
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 - the run failed all the same, see below
                        # What the run failed with is what is written and
                        # acted on: the commission fails and its session is
                        # let go. That it was opened is noted with a later
                        # event, as where any event fails to note it.
                        logger.warning("noting that a commission was opened failed; its run failed all the same", exc_info=True)
                # (Said by the host for a runtime process that went away:
                # nothing tells how far that got with what it was sent. What
                # the commission is, is then said again when it is taken up;
                # twice in the transcript does less harm than not at all.)
                error = str(event.get("error") or "")
                await self._fail(commission_id, error or "the run failed", said="run", detail=error)
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
    async def _looked(look: Callable[[], Awaitable[Any]], *, what: str = "at the end of a run") -> Any:
        """Read the ledger for what the end of a run means, asking again after a failure before giving up.

        The end of a run comes once. Given up on at the first failure to
        read, its commission would stay at work with nobody at it; the
        ledger is asked a few more times, a short while apart
        (``REST_LOOK_RETRY_DELAYS``), and only the last failure is the
        caller's. ``what`` says in the log what the ledger was read for.
        """
        from config.nekopower_settings import REST_LOOK_RETRY_DELAYS

        for delay in (*REST_LOOK_RETRY_DELAYS, None):
            try:
                return await look()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - asked again; the last failure is the caller's
                if delay is None:
                    raise
                logger.warning("reading the ledger %s failed; it is asked again", what, exc_info=True)
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
        from nekopower.host.outbox import retry_delay

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
        receipt = session.answer_receipts.pop(number, None)
        if receipt is not None:
            self._settle_call_soon(receipt[0], "completed")
        session.handed_told.pop(number, None)
        told = session.copies_told.pop(number, None)
        if told:
            await self._copies_taken(session, told)
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
        from nekopower.host.wakes import STOPPED_REASON

        stopped_upto = getattr(self._waker, "stopped_upto", None)
        await self._ledger.wakes_unfire(wake_ids, stopped_upto=stopped_upto, reason=STOPPED_REASON)

    @staticmethod
    def _told(said: str, commission: Commission | None) -> tuple[str, str]:
        """Return what the senders of the notes a turn took are told: what it ended on, and how its commission stood.

        What it ended on is its last words, or what was reported where
        it ended with none.
        """
        from config.nekopower_settings import SESSION_NOTICE_TEXT_MAX_TOKENS
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

        # Nothing is said of the conversation's length by the request that
        # summed it up.
        context = number("context_tokens") or None
        said = 0
        if context is not None:
            self._context_said += 1
            said = self._context_said
        await self._write_usage(
            (session_id, commission_id),
            (1, number("input"), number("output"), number("cache_read"), number("cache_write")),
            context,
            said,
        )

    async def _write_usage(
        self,
        key: tuple[str, str | None],
        amounts: tuple[int, int, int, int, int],
        context: int | None,
        said: int,
    ) -> None:
        """Write what requests used, with what the ledger did not take before for the same session and commission.

        The account catches up once the ledger takes it. The writing is
        a task of its own, carried through whatever becomes of whoever
        waits for it: the ledger writes in a thread, which a
        cancellation does not stop, so only what sees how the writing
        ended can tell whether it is still owed. Put down as owed by
        whoever was cancelled, what was written all the same would be
        written a second time.
        """
        if self._usage_reading and not self._closed:
            # The account of a commission is being read: put down as
            # owed, where whoever reads finds it, and written once the
            # reading is over (``_usage_read``). Begun now, it would be
            # in neither place for a moment.
            self._owe(key, amounts, context, said)
            for late in self._usage_late:
                self._owe(key, amounts, context, said, into=late)
            self._usage_put_off.add(key)
            return
        owed = self._usage_owed.pop(key, None)
        if owed is not None:
            amounts = (
                amounts[0] + owed[0],
                amounts[1] + owed[1],
                amounts[2] + owed[2],
                amounts[3] + owed[3],
                amounts[4] + owed[4],
            )
            if context is None:
                # As of the latest request that said how long it was.
                context, said = owed[5], owed[6]
        # Not a task shutting down gives up on (``_spawn``): cut off while
        # the ledger writes, it could no longer say that the writing
        # failed, and what was used would be neither written nor owed.
        writing = asyncio.ensure_future(self._usage_written(key, amounts, context, said))
        self._usage_writes.add(writing)
        writing.add_done_callback(self._usage_writes.discard)
        await asyncio.shield(writing)

    async def _usage_written(
        self,
        key: tuple[str, str | None],
        amounts: tuple[int, int, int, int, int],
        context: int | None,
        said: int,
    ) -> None:
        # One at a time for a session: another write of it that is under
        # way has to be through (taken, or not) before this one looks at
        # what the ledger has of the conversation's length.
        async with self._usage_locks.setdefault(key[0], asyncio.Lock()):
            # How long the conversation was, only when nothing said later
            # of the same session is written already: a request that is
            # written late is counted, and what it said of the length is
            # the older.
            newest = context is not None and said > self._context_written.get(key[0], 0)
            try:
                await self._ledger.add_usage(
                    session_id=key[0],
                    commission_id=key[1],
                    requests=amounts[0],
                    tokens_in=amounts[1],
                    tokens_out=amounts[2],
                    cache_read=amounts[3],
                    cache_write=amounts[4],
                    context_tokens=context if newest else None,
                )
            except Exception:  # noqa: BLE001 - what was used is for showing and for the limit; the work goes on
                logger.warning("writing down what a model request used failed; written later", exc_info=True)
                # With what came to be owed meanwhile, whichever of the
                # two said later how long the conversation was.
                self._owe(key, amounts, context, said)
            else:
                if newest:
                    self._context_written[key[0]] = max(said, self._context_written.get(key[0], 0))

    def _owe(
        self,
        key: tuple[str, str | None],
        amounts: tuple[int, int, int, int, int],
        context: int | None,
        said: int,
        *,
        into: dict[Any, Any] | None = None,
    ) -> None:
        """Put what requests used down as owed, with what is owed of the same session and commission already.

        ``into``: noted there instead, in the same way.
        """
        owed = self._usage_owed if into is None else into
        before = owed.get(key, (0, 0, 0, 0, 0, None, 0))
        # How long the conversation was, as of whichever said so later.
        length = (context, said) if context is not None and said > before[6] else (before[5], before[6])
        owed[key] = (
            amounts[0] + before[0],
            amounts[1] + before[1],
            amounts[2] + before[2],
            amounts[3] + before[3],
            amounts[4] + before[4],
            *length,
        )

    @contextlib.asynccontextmanager
    async def _usage_read(self) -> AsyncIterator[None]:
        """Keep what was used where it is while the account of a commission is read.

        What was used is in the ledger or owed, and while it is being
        written in neither: taken from what is owed, and not in the
        ledger yet. So the writes under way are waited for first (each
        ends in the ledger or owed again), and none begins until the
        reading is over: what is to be written meanwhile is put down
        as owed (``_write_usage``), and written right after. Whoever
        reads the ledger and what is owed in here reads each thing
        that was used once.
        """
        self._usage_reading += 1
        try:
            await self._usage_at_rest()
            yield
        finally:
            self._usage_read_over()

    async def _usage_at_rest(self) -> None:
        """Wait until no write of usage is under way: each has ended in the ledger, or as owed again."""
        while self._usage_writes:
            await asyncio.wait(list(self._usage_writes))

    def _usage_read_over(self) -> None:
        """Let usage be written again, now that an account is read; what was put off is written."""
        self._usage_reading -= 1
        if not self._usage_reading and self._usage_put_off:
            # What was put off is written now, and that only: what was
            # owed before this, because the ledger did not take it, is
            # tried again when it always was, and not with every
            # reading. With the writes of usage, which shutting down
            # waits for and does not cut off.
            put_off, self._usage_put_off = self._usage_put_off, set()
            if not self._closed:
                writing = asyncio.ensure_future(self._write_put_off(put_off))
                self._usage_writes.add(writing)
                writing.add_done_callback(self._usage_writes.discard)

    async def _write_put_off(self, keys: set[tuple[str, str | None]]) -> None:
        """Write what was put off while the account of a commission was read, each once."""
        for key in keys:
            if key in self._usage_owed:
                await self._write_usage(key, (0, 0, 0, 0, 0), None, 0)

    async def _settle_usage(self) -> None:
        """Write what is owed of usage before the host is gone, as far as that can be done in the time a start gets.

        What is owed is tried once more, each once. Writes under way
        are waited for and not cancelled (how each ends is for it to
        tell), and what one of them turns out to have failed with
        while this goes on is tried as well. What is still owed after
        that, or still being written, is lost with the host.
        """
        loop = asyncio.get_running_loop()
        until = loop.time() + _SHUTDOWN_START_WAIT_SECONDS
        tried: set[tuple[str, str | None]] = set()

        def being_written(session_id: str) -> bool:
            lock = self._usage_locks.get(session_id)
            return lock is not None and lock.locked()

        while loop.time() < until:
            # What is owed first, and not what a write under way holds up
            # (one write of a session at a time): that is looked at again
            # once the write is through.
            owed = [key for key in self._usage_owed if key not in tried and not being_written(key[0])]
            if owed:
                tried.update(owed)
                for key in owed:
                    if key not in self._usage_owed:
                        continue
                    try:
                        await asyncio.wait_for(
                            self._write_usage(key, (0, 0, 0, 0, 0), None, 0), max(0.01, until - loop.time())
                        )
                    except asyncio.TimeoutError:
                        break
                continue
            if not self._usage_writes:
                break
            await asyncio.wait(
                list(self._usage_writes),
                timeout=max(0.0, until - loop.time()),
                return_when=asyncio.FIRST_COMPLETED,
            )
        if self._usage_owed or self._usage_writes:
            logger.warning("what model requests used could not all be written before the host shut down")

    def _settle_usage_of(self, session_id: str) -> None:
        """Have what is owed of a session's usage written, now that nothing is at work in it.

        Without this it would be written only with the next request of
        the same session and commission, and the last request of a
        commission has none after it. Tried once; what the ledger does
        not take this time either stays owed, and the host tries once
        more when it shuts down.
        """
        if not self._closed and any(key[0] == session_id for key in self._usage_owed):
            self._spawn(self._write_owed_usage(session_id))

    async def _write_owed_usage(self, session_id: str | None = None) -> None:
        """Try once to write what is owed of one session's usage, or of every session's."""
        for key in [key for key in self._usage_owed if session_id is None or key[0] == session_id]:
            if key in self._usage_owed:
                await self._write_usage(key, (0, 0, 0, 0, 0), None, 0)

    async def _on_usage(self, session: _Session, event: dict[str, Any]) -> None:
        """Count one model request; a commission whose quota is used up fails.

        What it used is added to the session and the commission. Against
        the daily quota it is counted as one request, unless it is the
        request that summed the conversation up, which is none of the
        run's own and was never counted there. The runtime reports usage
        once per request, after the request, so the request that uses the
        last unit still completes.
        """
        if not event.get("summary"):
            # A copy begins its requests as the session it was copied from
            # does: what a provider keeps of the one is the other's too.
            from config.nekopower_settings import TASK_AGENT_FORK_CONTINUE_HOT_SECONDS

            now = asyncio.get_running_loop().time()
            if len(self._last_request_at) > _LAST_REQUESTS_KEPT:
                # One made longer ago than that says as much as none.
                for kept, at in list(self._last_request_at.items()):
                    if now - at > TASK_AGENT_FORK_CONTINUE_HOT_SECONDS:
                        del self._last_request_at[kept]
            self._last_request_at[session.session_id] = now
            if session.carries_on_for is not None:
                self._last_request_at[session.carries_on_for] = now
        await self._note_usage(session.session_id, session.commission_id, event)
        if self._meter is None or not session.free or session.closing or event.get("summary"):
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
        # After requests of its own: some of the work may be done.
        await self._fail(commission_id, _QUOTA_RUN_REASON, said="quota_run")
        for child in await self._ledger.select(parent_id=commission_id, statuses=ACTIVE_STATUSES):
            await self._ledger.transition(child.id, "cancelled", reason="its parent finished")
            await self.abort(child.id)
        await self._close_session(session)

    async def _on_error(self, session: _Session, event: dict[str, Any], refused: list[int]) -> None:
        """Act on a command the runtime refused; ``refused`` is what the text it refused had brought (``_refusal``)."""
        command = event.get("command")
        # What the runtime said, if it said anything: only that is passed on to
        # her as the error. The fallback is for the record.
        stated = str(event.get("message") or "")
        message = stated or "the runtime rejected the command"
        commission_id = session.commission_id
        if refused:
            # What the refused text was to hand over is pending again, and
            # the session is not tried again at once.
            from config.nekopower_settings import WAKE_HELD_RETRY_SECONDS

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
            await self._fail(commission_id, f"the task agent could not be started: {message}", said="start", detail=stated)
            self._keep_unsent(session)
            await self._release(commission_id)
        elif command == "prompt":
            session.untaken_answer = None
            # Refused, so it is not in the transcript: what the user said
            # with it is kept, as when the start itself fails.
            # Counted back above: none taken means the work did not start.
            await self._fail(commission_id, message, said="run" if session.sent else "start", detail=stated)
            self._keep_unsent(session)
            await self._close_session(session)
        elif command == "close":
            # The session is gone already, for instance with its runtime process.
            if session.handing_back is not None and not self._closed:
                # A copy that was closed to hand its commission back: gone
                # is as good as closed (``_handed_back``).
                await self._handed_back(session)
                return
            if session.handing_over is not None and not self._closed:
                # A leader that was closed to hand its commission over:
                # likewise (``_handed_over``).
                await self._handed_over(session)
                return
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

    async def _fail(self, commission_id: str, reason: str, *, said: str, detail: str = "") -> None:
        """End a commission as failed for a reason of the host's own.

        ``reason`` is what goes on record, in English. ``said`` says what
        it was: ``quota`` (the task agent's model ran out of quota before a
        task agent was ever at work on it), ``quota_run`` (the same, once
        one was, now or before it was taken up again: part of the work may
        be done), ``start`` (the task agent
        could not start on it: nothing of it was taken) or ``run`` (its run
        stopped on an error). She is told that,
        in English, as the host tells a model what happened
        (``TASK_AGENT_FAILED_NOTICE``), with ``detail``, the error as it
        came, when there is one; the HUD shows it in her dialogue's language
        (``TASK_AGENT_FAILED_TEXTS``). Running out of quota is nothing she
        can do anything about there and then: she is not woken for it and
        nothing is shown (``quiet``), and the frontend has its own notice of
        the quota. A task agent that started the commission is told the
        reason on record: that is not for the user.
        """
        from config.prompts import prompts_nekopower_brain as texts
        from nekopower.host.task_brain import _texts

        told, shown = reason, None
        commission = await self._ledger.get(commission_id)
        if commission is not None and commission.parent_id is None:
            error = texts.TASK_AGENT_FAILED_ERROR.format(detail=detail) if detail else ""
            told = texts.TASK_AGENT_FAILED_NOTICE.format(why=texts.TASK_AGENT_FAILED_WHY[said]) + error
            if said not in _QUIET_FAILURES:
                words = _texts(texts.TASK_AGENT_FAILED_TEXTS, commission.lang or "en")
                shown = words[said] + (words["error"].format(detail=detail) if detail else "")
        await self._end(
            commission_id,
            "failed",
            reason=reason,
            text=told,
            shown=shown,
            quiet=said in _QUIET_FAILURES,
            result_summary=self._summary(told),
        )

    async def _end(
        self,
        commission_id: str,
        target: str,
        *,
        reason: str,
        text: str,
        shown: str | None = None,
        quiet: bool = False,
        suggested_delivery: Any = None,
        unless: Callable[[], bool] | None = None,
        **values: Any,
    ) -> bool:
        """Move a commission to a final status and tell whoever commissioned it.

        A report to her is stored in the same transaction as the status
        change, so a crash in between cannot leave a finished commission
        that is never reported. A parent task agent is told after the change.
        ``text`` is what she, or the parent, is told; ``shown``, what the
        HUD shows instead, when that differs. ``quiet``: she is not woken
        for it but told later (passive: with the user's next turn in a text
        chat, at the next session swap in a voice chat), and nothing is
        shown.
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
            if quiet and delivery != "silent":
                delivery = "passive"
            report = self._reporter.task_result_report(
                commission,
                channel=CHANNEL_TASK_AGENT,
                success=target == "done",
                summary=text,
                shown=shown,
                detail=text,
                error_message=text if target == "failed" else "",
                status=_STATUS_TO_REPORT.get(target, target),
                source_kind="task_agent",
                source_name=commission.title,
                delivery_mode=delivery,
                visibility=[] if quiet else None,
            )
        moved = await self._reporter.transition(commission_id, target, reason=reason, report=report, **values)
        if not moved.applied or moved.commission is None:
            return False
        if moved.commission.parent_id is not None:
            await self._tell_parent(moved.commission, _STATUS_TO_REPORT.get(target, target), text)
        return True

    async def _tell_parent(self, commission: Commission, status: str, text: str) -> None:
        import secrets

        from config.nekopower_settings import TASK_AGENT_CHILD_RESULT_MAX_TOKENS
        from config.prompts import prompts_nekopower_sessions as words
        from utils.tokenize import truncate_to_tokens

        parent_id = str(commission.parent_id)
        # What another task agent wrote, like a note from another session:
        # the host says whose it is, and frames it so that nothing in it
        # reads as words of the host or of the user.
        nonce = secrets.token_hex(8)
        if commission.source == "ask":
            # The answer of a copy of another session: named by that session,
            # and handed on as no word of the user's, whichever way it goes
            # (sent at once, kept for when the asker is started, or taking
            # it up again).
            message: str = _Unsaid(
                words.frame(
                    words.ASK_ANSWER_FRAME.format(name=await self._asked_name(commission), status=status, nonce=nonce),
                    truncate_to_tokens(text, TASK_AGENT_CHILD_RESULT_MAX_TOKENS) or words.ASK_ANSWER_NOTHING,
                    nonce,
                ),
                parent_id,
            )
        else:
            message = words.frame(
                words.CHILD_RESULT_FRAME.format(name=commission.name or commission.id, status=status, nonce=nonce),
                truncate_to_tokens(text, TASK_AGENT_CHILD_RESULT_MAX_TOKENS) or words.CHILD_RESULT_NOTHING,
                nonce,
            )
        try:
            # Asked again a few times where it cannot be read this moment.
            stands = await self._looked(
                lambda: self._ledger.get(parent_id), what="for whether the parent of a task agent is over"
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - handed on as ever where it cannot be told
            # Not known to be over: handed on. Held back, the result of one
            # that is at work would be lost, which is worse than a turn of
            # one that is over, which the close of its session cuts short.
            stands = None
        if stands is not None and stands.status in ENDED_STATUSES:
            # Over (stopped, or its run ended done, failed or blocked): its
            # session is about to be closed, if it is not yet, the task
            # agents it started are stopped, and nothing waits for the
            # result any more. Steered in, it would start a turn of a
            # commission that is over. (Nothing is awaited from here to
            # where it would be handed on.)
            return
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
        if not await self._send_text(runtime, parent, "steer", message):
            await self._wake_closed_parent(parent_id, message)

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
        while not self._closed:
            current = self._sessions.get(parent_id)
            starting = parent_id in self._taking_up and (current is None or not current.ready and not current.closing)
            if parent_id in self._queue or parent_id in self._requeueing or starting:
                # Already on its way back: this result goes along with it.
                self._notes.setdefault(parent_id, []).append(message)
                return True
            parent = await self._ledger.get(parent_id)
            if parent is None or parent.route != "task_agent" or parent.status != "working":
                return False
            if not await self._know_session(parent_id):
                return False
            session = self._sessions.get(parent_id)
            woken = isinstance(message, _Handed)
            if woken and not self._is_switched_on():
                # What a wake brings starts no work with NekoPower switched off
                # (switched off while this was awaited): it waits (``hand_over``).
                return False
            closing_answer = isinstance(message, _Unsaid) and session is not None and session.closing
            if session is not None and session.ready and not closing_answer and self._runtime_of(session) is not None:
                if woken and (session.awaiting_prompt or (session.question is not None and not session.question.done())):
                    # It came back while the ledger was read, and came to wait
                    # for its user: what a wake brings waits until that is
                    # settled, as it does in ``hand_over``, and does not set it
                    # to work with its question open.
                    return False
                # It came back, and was given its work, while the ledger was read.
                if await self._send_text(self._runtime_of(session), session, "steer", message):
                    return True
                # Its run or runtime changed during admission. Look up the owner again.
                continue
            break
        else:
            return False
        if (session is not None and not closing_answer) or parent_id in self._queue or parent_id in self._requeueing:
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
        if commission_id in self._settling_for_queue:
            # On its way into the queue: it is not put there.
            self._stopped_while_settling.add(commission_id)
        self._notes.pop(commission_id, None)
        self._carried.pop(commission_id, None)
        self._resume.pop(commission_id, None)
        self._prepaid_starts.discard(commission_id)
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
        was stopped meanwhile, or NekoPower switched off) leaves it as it
        is. Where its status was written just then, behind the back of the
        stop, it is stopped instead.
        """
        if self._closed:
            return False
        commission = await self._ledger.get(commission_id)
        if commission is None or commission.route != "task_agent" or self._closed:
            return False
        session = self._sessions.get(commission_id)
        if commission_id in self._said_while_handed:
            # Its leader is handing it over: said to the task agent it goes
            # to, once it is queued for that one (``_handed_over``). The
            # leader's session is being closed, and the other one sees
            # nothing of it.
            self._said_while_handed[commission_id].append(text)
            await self._ledger.append_event(commission_id, "told", {"text": text, "queued": True})
            return True
        if session is not None and self._runtime_of(session) is not None and session.awaiting_prompt:
            return await self.answer(commission_id, text)
        if session is None and commission.status == "waiting_user":
            # Its session was closed while it waited (an idle runtime exits).
            return await self.answer(commission_id, text)
        if commission.status in ("blocked", "failed"):
            # Take it up again where its transcript ends: unless its session
            # has ended (a session of one commission, over for long), which
            # nothing is given to any more. What was said is then work of
            # its own, as for a commission that was done.
            if commission.session_id and not await self._ledger.session_alive(
                await self._ledger.get_session(commission.session_id)
            ):
                return False
            if still_wanted is not None and not still_wanted():
                return False
            called_off = self._called_off(commission_id)
            if called_off is not None:
                # A taking up that was called off (everything was stopped
                # after it began) gives up once it gets to look, and takes
                # back what it was to bring: this is said once that is
                # through, and brings that itself (``_back_from_copy``).
                await asyncio.wait([called_off])
                return await self.tell(commission_id, text, still_wanted=still_wanted)
            # One that ended in a copy carried it on in is taken up in the
            # session itself, told what came of it in the copy.
            from_copy = await self._back_from_copy(commission)
            if still_wanted is not None and not still_wanted():
                return False
            await self._ledger.append_event(commission_id, "told", {"text": text, "resumed": True})
            # Should it open a session with nothing said in it (its start
            # failed before, or what was said was removed, being old), it is
            # handed what was said in the main dialogue just before this:
            # what was kept when it was first handed over went when it ended.
            # (Or is still there, when its start failed and it is taken up
            # before that was let go of: this round is about what is said
            # now, so that takes its place.) Read here, and kept only if
            # this call is the one that starts or queues the round: one
            # that joins a round under way adds its words to it, and the
            # round keeps what was said before it began.
            fresh = self._said_before(commission)
            taken = await self._take_up_again(
                commission_id, text, still_wanted=still_wanted, excerpt=fresh, from_copy=from_copy
            )
            if not taken and fresh and self._excerpts.get(commission_id) is fresh:
                del self._excerpts[commission_id]
            if not taken and from_copy:
                left = [item for item in self._notes.get(commission_id, []) if not any(item is piece for piece in from_copy)]
                if left:
                    self._notes[commission_id] = left
                else:
                    self._notes.pop(commission_id, None)
            return taken
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
            if commission_id in self._said_while_handed:
                # Its leader handed it over while that was written.
                self._said_while_handed[commission_id].append(text)
                return True
            if self._sessions.get(commission_id) is not session:
                # It moved on while that was written (its leader handed it over, and it waits for
                # the other task agent or is at work there): said where it is now.
                return await self._wake_working(commission_id, text, reason=_WOKEN_REASON)
            await self._send_text(self._runtime_of(session), session, "steer", text)
            return True
        return False

    async def _back_from_copy(self, commission: Commission) -> "list[str] | None":
        """Put a commission that ended stuck in a copy it was carried on in on record as the session itself's.

        A copy is not gone on in once its commission ended
        (``_carry_on_in_copy``): taken up again, the commission is the
        session's. Returns what the session is told of it, ahead of
        the words that take it up: what came of it in the copy, then what
        the user said to it and answered there, none of which is above in
        the session. None for any other commission. Told so as long as the
        session itself has not opened it: also where the first taking up
        was called off after it was moved (``back_from_copy``).
        """
        import secrets

        from config.nekopower_settings import TASK_AGENT_FORK_FINDINGS_MAX_TOKENS
        from config.prompts import prompts_nekopower_brain as texts
        from config.prompts.prompts_nekopower_sessions import frame
        from utils.tokenize import truncate_to_tokens

        if not commission.session_id or not _carried_on(commission):
            return None
        place = await self._ledger.get_session(commission.session_id)
        if place is not None and place.kind == "fork" and place.forked_from and await self._ledger.carried_on_in(place.id):
            copy_id, main = place.id, str(place.forked_from)
            moved_at = await self._ledger.append_event(commission.id, "back_from_copy", {"copy": copy_id, "to": main})
            await self._ledger.update(commission.id, session_id=main)
        else:
            # Moved before, and not opened in the session itself since (its
            # taking up was called off then): told now what it was to be told.
            # A closing copy session still in ``_sessions`` is not a taking-up
            # in ``main``; only work in hand for ``main`` itself already brings it.
            main = commission.session_id
            if self._in_hand_for(commission.id, main) and self._under_way.get(commission.id) is None:
                # Being taken up by what was said just before this, which
                # brings it: this goes along as more of what is said.
                return None
            moved = await self._last_back_from_copy(commission.id, main)
            if moved is None or await self._ledger.opened_in(commission.id, main) is not None:
                return None
            copy_id, moved_at = moved
        self._session_of[commission.id] = main
        # What was said to it before it was moved: not what was said to a
        # taking up that was called off after (it is said again, if at all).
        said_there = await self._told_in_copy(commission.id, before=moved_at)
        nonce = secrets.token_hex(8)
        found = _Found(
            frame(
                texts.TASK_AGENT_TAKEN_UP_FROM_COPY.format(status=commission.status, session=copy_id[:8], nonce=nonce),
                truncate_to_tokens(
                    " ".join((commission.result_summary or commission.status_reason or "").split()),
                    TASK_AGENT_FORK_FINDINGS_MAX_TOKENS,
                )
                or texts.TASK_AGENT_FORK_FOUND_NOTHING,
                nonce,
            )
        )
        return [found, *said_there]

    async def _last_back_from_copy(self, commission_id: str, main: str) -> tuple[str, int] | None:
        """Return the copy a commission was last moved out of to the session itself (``back_from_copy``), and that event's number; None for none."""
        if not await self._ledger.has_event(commission_id, "back_from_copy"):
            return None
        found: tuple[str, int] | None = None
        after = 0
        while True:
            page = await self._ledger.events(commission_id, after=after)
            for event in page:
                if event.type == "back_from_copy" and event.payload.get("to") == main and event.payload.get("copy"):
                    found = (str(event.payload["copy"]), event.seq)
            if not page:
                return found
            after = page[-1].seq

    async def _take_up_again(
        self,
        commission_id: str,
        text: str,
        *,
        still_wanted: Callable[[], bool] | None = None,
        same_round: bool = False,
        excerpt: list[Any] | None = None,
        from_copy: list[str] | None = None,
    ) -> bool:
        """Start a commission's task agent again where its transcript ends, ``text`` its next work.

        Its old session is gone or on its way out. Without a free place it is
        queued, which a stop can still end. Returns whether it was started or
        queued.

        ``same_round`` is for the answer to a question its task agent
        asked: more of the round it was at, not a new one (what was
        refused for the round, and what it has left to hand over, stand).
        ``excerpt`` is what was said in the main dialogue just before,
        for one that is taken up as a new round: kept for it when this
        call is the one that starts or queues it, and not when it only
        joins one that is doing so already.
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
        if commission_id in self._queue:
            # An earlier sentence took it up again, and it waits for a place
            # by now (this one read the commission before that was
            # written): it goes along with that one. Queued a second
            # time, it would take the place of the first sentence, or be
            # refused and be lost.
            self._notes.setdefault(commission_id, []).append(text)
            return True
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
                return await self._take_up_again(
                    commission_id,
                    text,
                    still_wanted=still_wanted,
                    same_round=same_round,
                    excerpt=excerpt,
                    from_copy=from_copy,
                )
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
            self._new_round_of(commission_id, same_round=same_round, excerpt=excerpt)
            if from_copy:
                self._notes.setdefault(commission_id, [])[0:0] = from_copy
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
        # meanwhile joins this one (and a closing old session's ``_release``
        # keeps ``from_copy`` in ``_notes`` instead of discarding it).
        self._requeueing.add(commission_id)
        self._new_round_of(commission_id, same_round=same_round, excerpt=excerpt)
        if from_copy:
            self._notes.setdefault(commission_id, [])[0:0] = from_copy
        queueing = self._spawn(self._requeue(commission_id, text, still_wanted=still_wanted))
        self._note_under_way(commission_id, still_wanted, queueing)
        return await asyncio.shield(queueing)

    def _new_round_of(
        self, commission_id: str, *, same_round: bool = False, excerpt: list[Any] | None = None
    ) -> None:
        """Note that a commission is taken up again: started now, or queued for a place.

        Its session has something new to be worked on in it (it may be
        summed up again once that is over, ``_new_work_in``). And
        whether more may be used was refused for the round that is over:
        that may still be on record, since this can come before the hold
        on its session is looked at, which is where it is forgotten.
        Kept, the new round would be told to hand over at once instead
        of the user being asked.

        Not so for more of the same round (``same_round``: the answer to
        a question the task agent asked, in a session that was closed
        meanwhile): the user answers that question, and is not asked
        about usage a second time; what the task agent had left to hand
        over stands as well.

        ``excerpt`` takes the place of what was kept for the commission
        before: this is where a round becomes this call's.
        """
        self._new_work_in(self._session_of.get(commission_id), self._idle_stops)
        if not same_round:
            self._refused_more.discard(commission_id)
            self._winding_up.pop(commission_id, None)
            self._excerpts.pop(commission_id, None)
            if excerpt:
                self._excerpts[commission_id] = excerpt

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

    async def answer(
        self, commission_id: str, text: str, choice: str | None = None, *, question_id: str | None = None
    ) -> bool:
        """Give the user's answer to a commission that waits for it.

        ``question_id`` is which question the answer was read against
        (the ``id`` of what the commission asked, as the ledger had
        it then). Reading a sentence as an answer takes a while, and
        that question may be answered or gone by now, with another
        one waiting in its place: the answer is not that one's, and
        is turned away with nothing changed. Not said (None), the
        answer is for whichever question waits.
        """
        session = self._sessions.get(commission_id) or self._direct_questions.get(commission_id)
        if session is None:
            return await self._answer_closed(commission_id, text, question_id=question_id)
        if question_id is not None and question_id != session.question_id:
            return False
        if session.question is not None and not session.question.done():
            if session.at_answer is not None:
                # With nothing awaited between this and the answer being
                # in: whoever asked gets to go on only later.
                session.at_answer()
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

    async def _answer_closed(
        self, commission_id: str, text: str, *, claimed: bool = False, question_id: str | None = None
    ) -> bool:
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
            if question_id is not None and question_id != question.get("id"):
                # Read against another question than the one it waits with now.
                return False
            await self._ledger.append_event(commission_id, "answered", {"text": text, "resumed": True})
            return await self._take_up_again(commission_id, text, same_round=True)
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
            if (
                moved.applied
                and not await self._take_up_again(commission_id, text, same_round=True)
                and self._closed
            ):
                # Shutting down began while the status was written: nothing will
                # take the answer up, and neither the queue nor the sessions hold
                # it for shutting down to end. It ends here, as they do.
                await self._ledger.transition(commission_id, "failed", reason=_SHUT_DOWN_REASON)
        finally:
            self._answering_closed.discard(commission_id)

    async def _ask(
        self, commission_id: str, question: dict[str, Any], *, at_answer: Callable[[], None] | None = None,
        direct: _Session | None = None,
    ) -> dict[str, Any]:
        """Put a question to the user and wait for the answer.

        The commission is ``waiting_user`` meanwhile and ``working`` again
        afterwards. ``at_answer`` is done the moment the answer to this
        question is in (``answer``), with nothing awaited before: this
        gets to go on only a while later.
        """
        session = direct or self._sessions.get(commission_id)
        if session is None:
            raise RuntimeError("the commission has no session to ask for")
        async with session.ask_lock:
            future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
            # Which question this is, for whoever reads a sentence as
            # its answer: by the time the answer is handed in, another
            # question may wait in its place (``answer``).
            question = {**question, "id": uuid.uuid4().hex}
            moved = await self._ledger.transition(
                commission_id,
                "waiting_user",
                reason="waiting for the user",
                pending_question=json.dumps(question, ensure_ascii=False),
            )
            if not moved.applied or moved.commission is None:
                raise RuntimeError(f"the commission cannot wait for the user ({moved.refusal})")
            session.question, session.at_answer, session.question_id = future, at_answer, question["id"]
            try:
                await self._reporter.question(moved.commission, question)
                return await future
            finally:
                session.question, session.at_answer = None, None
                if session.question_id == question["id"]:
                    # (Not what a report asked meanwhile, should there be one.)
                    session.question_id = ""
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
        direct = None
        if commission_id not in self._sessions:
            commission = await self._ledger.get(commission_id)
            if commission is None or commission.route != "plugin" or commission.status != "working":
                raise RuntimeError("the commission has no session to ask for")
            direct = _Session(commission_id)
            if commission_id in self._direct_questions:
                return "deny"
            self._direct_questions[commission_id] = direct
        try:
            answer = await self._ask(commission_id, ask.to_json(), at_answer=ask.at_answer, direct=direct)
        finally:
            if direct is not None:
                self._direct_questions.pop(commission_id, None)
        choice = answer.get("choice")
        # Anything that is not one of the four answers is a refusal. An
        # approval wider than the question offered is narrowed by the gate.
        return choice if choice in CHOICES else "deny"

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

    async def _cb_native_execution_action(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._cb_execution_action(session_id, payload, native=True)

    async def _cb_execution_action(
        self, session_id: str, payload: dict[str, Any], *, native: bool = False
    ) -> dict[str, Any]:
        from nekopower.runtime.execution import EXECUTION_TOOLS, JOB_TOOLS

        commission = self._held_by.get(session_id)
        session = self._sessions.get(commission) if commission else None
        if session is None or session.session_id != session_id or session.closing:
            return {"allow": False}
        kind = "dsh_native" if native else "runtime"
        if native and session.runtime_name != RUNTIME_DSH:
            return {"allow": False}
        if payload.get("phase") == "release" and native:
            session.job_sources.pop(payload.get("receipt"), None)
            return {"allow": False}
        if payload.get("phase") == "job_notice_live" and not native:
            source = session.job_sources.get(payload.get("receipt"))
            return {"allow": bool(source and source[0]())}
        if payload.get("phase") == "job_notice" and not native:
            batch = "receipts" in payload
            receipts = payload["receipts"] if batch else [payload.get("receipt")]
            if not isinstance(receipts, list) or not all(isinstance(r, str) for r in receipts):
                return {"allow": False}
            checked = {}
            for receipt in dict.fromkeys(receipts):
                source = session.job_sources.get(receipt)
                if source and source[0]() and await source[1]():
                    checked[receipt] = source
            # A later transfer may have waited on the ledger while an earlier
            # source was revoked. Check the whole batch without yielding.
            allowed = [r for r, source in checked.items() if session.job_sources.get(r) is source and source[0]()]
            return {"origins": allowed} if batch else {"allow": bool(allowed)}
        if payload.get("phase") == "check":
            return {"allow": self._execution_audit.active(commission, session_id, payload.get("receipt"), kind=kind)}
        if payload.get("phase") == "finish":
            receipt = payload.get("receipt", "")
            try:
                outcome = await self._execution_audit.finish(
                    commission, session_id, receipt, payload.get("status", ""), kind=kind
                )
            except (asyncio.CancelledError, Exception):
                session.job_sources.pop(receipt, None)
                raise
            if outcome is None:
                return {"allow": False}
            if payload.get("started_job") is True:
                keep = outcome.command_completed
            else:
                keep = outcome.delivered and payload.get("retain_source") is True
            if not keep:
                session.job_sources.pop(payload.get("receipt"), None)
            return {"allow": outcome.delivered}
        tool = payload.get("tool")
        if payload.get("phase") == "native_failure":
            from nekopower.dsh.profile import GATED_TOOLS
            from nekopower.host import call_audit

            if not native or tool not in (*GATED_TOOLS, *JOB_TOOLS):
                return {"allow": False}
            async with call_audit.call(self._ledger, commission, "dsh_native", tool) as record:
                record.status = "failed"
                record.reason = "native_failure"
            return {"allow": False}
        if payload.get("phase") != "begin" or tool not in EXECUTION_TOOLS:
            return {"allow": False}
        category = payload.get("category")
        if category not in {"workdir_files", "outside_read", "outside_write", "run_command", "other"}:
            return {"allow": False}
        if not native and session.runtime_name != RUNTIME_BUILTIN and tool != "web_fetch":
            return {"allow": False}
        scope_ref = payload.get("scope_ref")
        if not isinstance(scope_ref, str) or len(scope_ref) != 64 or any(c not in "0123456789abcdef" for c in scope_ref):
            return {"allow": False}

        import hashlib
        from urllib.parse import urlsplit
        from nekopower.host import call_audit

        # Only the host knows which model this live runtime was actually opened with.
        model = session.model
        if not model or not model[1]:
            return {"allow": False}
        domain = urlsplit(str(model[0] or "")).hostname
        label = f"{model[1]} ({domain})" if domain else str(model[1])
        recipient = "model:" + hashlib.sha256(json.dumps(model).encode()).hexdigest()
        current = self._permissions.current_run(commission)
        effective_category = category
        if (category == "outside_read" and self._space is not None and isinstance(payload.get("path"), str)
                and await self._space.contains(payload["path"])):
            effective_category = "workdir_files"
        delivery_rule = self._permissions.current_rules(commission, "managed_delivery")
        action_rule = self._permissions.current_rules(commission, effective_category)

        def bind_action_rule(category):
            # Path and session ownership checks may yield before the gate
            # decides which rule actually governs this action.
            nonlocal action_rule
            action_rule = self._permissions.current_rules(commission, category)

        grants = []
        origins = payload.get("origins", [])
        if not isinstance(origins, list) or not all(isinstance(ref, str) for ref in origins):
            return {"allow": False}
        if tool == "job_list":
            origins = [ref for ref in origins if ref in session.job_sources and session.job_sources[ref][0]()]
        if any(ref not in session.job_sources for ref in origins):
            return {"allow": False}
        sources = [session.job_sources[ref][0] for ref in origins]

        def source_live() -> bool:
            return (delivery_rule() and action_rule() and self._held_by.get(session_id) == commission
                    and self._sessions.get(commission) is session and not session.closing and session.model == model
                    and all(self._permissions.grant_active(grant) for grant in grants) and all(check() for check in sources))

        def live() -> bool:
            return current() and source_live()

        async def permit(record):
            record.scope_ref = scope_ref
            if native and payload.get("private_route") is not True and category != "workdir_files":
                # A customized native recipe can add observers outside the stock return path.
                return {"allow": False, "reason": DENIED_BY_SETTINGS}
            if await self._permissions.forbidden("managed_delivery"):
                return {"allow": False, "reason": DENIED_BY_SETTINGS}
            if sources and tool in {"read", "grep"} and category == "workdir_files":
                answer = {"allow": source_live() and not await self._permissions.forbidden(category)}
            elif tool in JOB_TOOLS or (kind == "runtime" and tool == "web_fetch"):
                if category != "other":
                    return {"allow": False}
                decision = await self._permissions.check(
                    commission, category, detail={"tool": tool, "content_to": label},
                    scope=GrantScope("dsh-stock" if native else "builtin-runtime", f"{tool}:{scope_ref}", recipient),
                )
                if decision.grant_id:
                    grants.append(decision.grant_id)
                record.grant_id = self._permissions.authorization_ref(decision.grant_id)
                answer = decision.to_json()
            else:
                answer = await self._cb_permission(
                    session_id, payload, audit=record, recipient=recipient, label=label,
                    grants=grants, before_check=bind_action_rule,
                )
            if answer.get("allow"):
                from contextvars import copy_context

                parent = copy_context()

                def available():
                    return record.closed and record.status in {"completed", "failed"} and source_live()

                async def notice():
                    return await asyncio.create_task(deliver(record, available), context=parent.copy())

                session.job_sources[record.call_id] = (available, notice)
            return answer

        async def deliver(record, eligible=live):
            async with call_audit.call(self._ledger, commission, "transfer_content", recipient) as transfer:
                transfer.scope_ref = scope_ref
                transfer.grant_id = record.grant_id
                if (not eligible() or await self._permissions.forbidden(effective_category)
                        or await self._permissions.forbidden("managed_delivery") or not eligible()):
                    transfer.status = "denied"
                    transfer.reason = "delivery_expired"
                    return False
            return transfer.closed and eligible()

        answer = await self._execution_audit.begin(commission, session_id, kind, tool, permit, deliver, live)
        if tool == "job_list" and answer.get("allow"):
            answer["origins"] = origins
        return answer

    async def _cb_permission(
        self, session_id: str, payload: dict[str, Any], *, audit=None, recipient="", label="", grants=None, before_check=None,
    ) -> dict[str, Any]:
        session_id = self._at_work_in(session_id)
        detail = {key: payload.get(key) for key in ("tool", "path", "command", "cwd") if payload.get(key)}
        if label:
            detail["content_to"] = label
        from nekopower.runtime.permissions import GATEWAY_CALL_CATEGORY, HOST_TOOL_CALL_CATEGORY, WRITE_TOOL_NAMES

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
                from config.prompts.prompts_nekopower_space import SPACE_WRITE_REFUSED

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
        if before_check is not None:
            before_check(category)
        session = self._sessions.get(session_id)
        if session is not None and session.runtime_name == RUNTIME_BUILTIN:
            decision = await self._permissions.check_builtin(session_id, category, detail=detail, writes=writes, recipient=recipient)
        else:
            stock_tool = session is not None and session.runtime_name == RUNTIME_DSH and payload.get("private_route") is True
            decision = await self._permissions.check_native(session_id, category, detail=detail, stock_tool=stock_tool, recipient=recipient)
        if grants is not None and decision.grant_id:
            grants.append(decision.grant_id)
        if audit is not None:
            audit.grant_id = self._permissions.authorization_ref(decision.grant_id)
        return decision.to_json()

    async def _rule_on_gateway_call(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Rule on a call of a gateway tool that a task agent in dsh is about to make.

        Only the dsh runtime asks this (``nekopower.runtime.host``). dsh gives
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
        from config.nekopower_settings import GATEWAY_CLEARED_CALL_SECONDS

        if payload.get("tool") == "web_search":
            commission = await self._ledger.get(session_id)
            if commission is not None and session_id in self._sessions:
                arguments = payload.get("arguments")
                queries = _search_queries(arguments if isinstance(arguments, dict) else {})
                decision = await self._search_permission(commission)
                if session_id in self._sessions:
                    self._drop_search_ruling(session_id)
                    until = asyncio.get_running_loop().time() + GATEWAY_CLEARED_CALL_SECONDS
                    self._search_rulings[session_id] = (queries, decision, until)
                else:
                    self._permissions.release_grant(decision.grant_id)
        return PermissionDecision(True).to_json()

    def _drop_search_ruling(self, commission_id: str) -> None:
        ruled = self._search_rulings.pop(commission_id, None)
        if ruled is not None:
            self._permissions.release_grant(ruled[1].grant_id)

    async def _search_permission(self, commission: Commission) -> PermissionDecision:
        """Decide whether the task agent may send this batch of web queries.

        The search is a plugin entry: the plugin switch and the
        ``plugin_entry`` permission apply as they do through the gateway.
        """
        if not self._registry.plugins_enabled():
            return PermissionDecision(False, "web search is not available: plugins are switched off")
        return await self._permissions.check(
            commission.id,
            "plugin_entry",
            hold_once=True,
            scope=GrantScope("plugin_entry", f"plugin:{WEB_SEARCH_PLUGIN_ID}:{WEB_SEARCH_ENTRY_ID}"),
            detail={"tool": "web_search", "capability": f"plugin:{WEB_SEARCH_PLUGIN_ID}:{WEB_SEARCH_ENTRY_ID}"},
        )

    async def _cb_ask_user(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = self._at_work_in(session_id)
        questions = list(payload.get("questions") or [])
        answer = await self._ask(session_id, {"kind": "questions", "questions": questions})
        text = str(answer.get("text") or "")
        live = self._sessions.get(session_id)
        if live is not None and live.carries_on_for is not None and text.strip():
            # What the user answered a copy goes back with the commission,
            # should it be handed back (``_handed_back``), as the answer to
            # a report does. (Not a permission or how much may be used:
            # those are no words for the commission's work.)
            try:
                await self._ledger.append_event(session_id, "answered", {"text": text, "asked_by_tool": True})
            except Exception:  # noqa: BLE001 - the answer is the tool's all the same
                logger.warning("noting what the user answered a copy failed", exc_info=True)
        return build_answers(questions, text)

    async def _cb_report(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        # The report arrives as an event as well; that is where it is acted on.
        # What it says the session takes from now on (``duty``) is no part of
        # that event: it is for the session's card, and written here.
        duty = payload.get("duty")
        if not isinstance(duty, str) or not duty.strip():
            return None
        try:
            return await self._own_duty(session_id, duty)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the report goes through whatever becomes of the card
            logger.warning("writing what a session says it takes failed", exc_info=True)
            return None

    async def _own_duty(self, session_id: str, duty: str) -> dict[str, Any] | None:
        """Write what a session says it takes on its card; returns what its task agent is told, if anything.

        Only a session with a card of its own to say it on: that of a
        project, or of a single commission. Not a task agent another one
        started, and not a copy,
        which is offered the parameter because it is opened with the
        tools of the session it was copied from. A line the user wrote
        stays, and the task agent is told so.
        """
        from config.prompts.prompts_nekopower_presets import CARD_DUTY_IS_THE_USERS, CARD_NOT_ITS_OWN

        commission = await self._ledger.get(self._at_work_in(session_id))
        if commission is None:
            return None
        session = await self._ledger.get_session(commission.session_id)
        if session is None:
            return None
        # (Of the kinds another session can address: neither a task agent
        # another one started nor a copy is one.)
        if session.kind not in _ADDRESSED_KINDS:
            return {"message": CARD_NOT_ITS_OWN}
        _kept, written = await self._ledger.write_card(session.id, by="self", duty=duty)
        return None if written else {"message": CARD_DUTY_IS_THE_USERS}

    async def _cb_note_lesson(
        self, session_id: str, payload: dict[str, Any], action: _SessionAction | None = None,
    ) -> dict[str, Any] | None:
        commission = await self._ledger.get(self._at_work_in(session_id))
        if commission is None:
            return None
        if action is not None:
            action.require()
            action.record.scope_ref = commission.session_id
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
            decision = ruled[1]
        else:
            if ruled is not None:
                self._permissions.release_grant(ruled[1].grant_id)
            decision = await self._search_permission(commission)
        refusal = decision.reason
        if not self._registry.plugins_enabled():
            refusal = "web search is not available: plugins are switched off"
        elif decision.allow and not self._permissions.grant_active(decision.grant_id):
            refusal = DENIED_BY_SETTINGS
        if refusal is not None:
            self._permissions.release_grant(decision.grant_id)
            await self._ledger.append_event(
                commission.id, "capability_denied", {"capability": capability, "reason": refusal}
            )
            raise RuntimeError(refusal)
        gate = asyncio.Semaphore(_WEB_SEARCH_PARALLEL)

        async def one(query: str) -> dict[str, Any]:
            async with gate:
                outcome = await audited_run_entry(
                    self._ledger, self._runner,
                    commission_id=commission.id,
                    plugin_id=WEB_SEARCH_PLUGIN_ID,
                    entry_id=WEB_SEARCH_ENTRY_ID,
                    args={"query": query},
                    lanlan_name=commission.lanlan_name,
                    conversation_id=commission.conversation_id,
                    latest_user_request=commission.user_text,
                    still_wanted=lambda: self._registry.plugins_enabled() and self._permissions.grant_active(decision.grant_id),
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

        try:
            return {"results": list(await asyncio.gather(*(one(query) for query in queries)))}
        finally:
            self._permissions.release_grant(decision.grant_id)

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

    async def _cb_hand_over(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Hand the commission a leader is at over to a member of its project (the ``hand_over`` tool).

        To a member that is named, or to a new one of the preset named (a
        worker when none is). A new one that can change files is opened
        only when the user allows it (``OPEN_SESSION``): asked first by
        default, and not asked about one that reads only. Nothing moves
        here: the session is marked, and once its run is over it is
        closed and the commission goes on in the other one
        (``_handed_over``), which reports to her as any task agent does.
        """
        from config.nekopower_settings import HAND_OVER_BRIEF_MAX_TOKENS
        from config.prompts import prompts_nekopower_brain as texts
        from nekopower.host.templates import TemplateError, model_name
        from utils.tokenize import truncate_to_tokens

        commission_id = self._at_work_in(session_id)
        live = self._sessions.get(commission_id)
        commission = await self._ledger.get(commission_id)
        leader = await self._ledger.get_session(session_id)
        if (
            live is None
            or commission is None
            or leader is None
            or leader.kind != "project"
            or not leader.project
            or commission.parent_id is not None
            or commission.session_id != leader.id
        ):
            raise RuntimeError(texts.HAND_OVER_NOT_A_LEADER)
        if live.handing_over is not None:
            raise RuntimeError(texts.HAND_OVER_TWICE)
        if commission.status != "working":
            raise RuntimeError("the commission is not at work; nothing is handed over")
        if await self._ledger.select(parent_id=commission_id, statuses=_STILL_WORKING):
            # What they hand back would wake the commission where it is by then: with a member that did not start them.
            raise RuntimeError(texts.HAND_OVER_AGENTS_AT_WORK)
        brief = truncate_to_tokens(str(payload.get("brief") or ""), HAND_OVER_BRIEF_MAX_TOKENS)
        duty = " ".join(str(payload.get("description") or "").split()) or commission.title
        members = await self._ledger.members(leader.lanlan_name, leader.project)
        named = payload.get("to")
        made = False
        if isinstance(named, str) and named.strip():
            wanted = named.strip()
            # By its id, else by its name. Members made here have names of their own
            # (``Ledger.member_name``); of those that were sessions of their own before and
            # share one, the one last in use that still takes work.
            found = [member for member in members if member.id == wanted] or [
                member for member in members if member.name == wanted
            ]
            if not found:
                names = ", ".join(member.name for member in members) or "none"
                raise RuntimeError(texts.HAND_OVER_NO_MEMBER.format(name=wanted, names=names))
            target = None
            for member in found:
                if await self._ledger.session_alive(member):
                    target = member
                    break
            if target is None:
                raise RuntimeError(texts.HAND_OVER_MEMBER_OVER.format(name=wanted))
        else:
            preset = await self._preset_wanted(commission, payload.get("preset")) or "worker"
            try:
                template = (await asyncio.to_thread(self.templates.resolve, preset, leader.project)).under(
                    await self._template_of(leader)
                )
            except TemplateError as exc:
                raise RuntimeError(f"the preset {preset!r} cannot be used: {exc}") from exc
            if template.changes_files:
                # Another task agent that can change files (through the shell as well): the user's to allow.
                decision = await self._permissions.check(
                    commission_id,
                    OPEN_SESSION,
                    detail={"tool": "hand_over", "title": f"{duty} <- {commission.title}", "preset": preset},
                )
                if not decision.allow:
                    raise RuntimeError(texts.HAND_OVER_NOT_ALLOWED.format(reason=decision.reason or "declined"))
                # The user may have taken a while: looked at once more.
                live = self._sessions.get(commission_id)
                commission = await self._ledger.get(commission_id)
                if live is None or live.closing or commission is None or commission.status != "working":
                    raise RuntimeError("the commission is not at work any more; nothing is handed over")
                if live.handing_over is not None:
                    raise RuntimeError(texts.HAND_OVER_TWICE)
            target = await self._ledger.create_session(
                name=await self._ledger.member_name(
                    leader.lanlan_name, leader.project, f"{leader.project}/{model_name(preset)}"
                ),
                lanlan_name=leader.lanlan_name,
                kind="line",
                project=leader.project,
                workdir=leader.workdir,
                parent_id=leader.id,
                preset=preset,
                duty=duty,
            )
            made = True
            # The model it is sent to, by its preset: the leader's unless the user set another.
            await self.settle_model(target)
        # (That it was handed over is put on record once the other task agent has it: ``_handed_over``.)
        live.handing_over = (target.id, target.name, brief, target.preset, made)
        self._said_while_handed[commission_id] = []
        return {"message": texts.HAND_OVER_DONE.format(name=target.name)}

    async def _close_leader_later(self, session: _Session) -> None:
        """Ask again to close a leader that hands its commission over, as for a copy that hands one back.

        Its close could not be asked for. Nothing comes by to ask again
        once its run is over, so the close is asked a few more times, a
        while apart; given up on, the commission fails as one whose run
        failed and lets its place go.
        """
        from config.nekopower_settings import REST_LOOK_RETRY_DELAYS

        commission_id = session.commission_id
        for delay in REST_LOOK_RETRY_DELAYS:
            await asyncio.sleep(delay)
            if self._closed or self._sessions.get(commission_id) is not session or session.closing:
                # Gone, or closing after all: its end takes it from there.
                return
            try:
                await self._close_session(session)
                return
            except Exception:  # noqa: BLE001 - asked again, and given up after the last
                logger.warning("closing a leader's session to hand its commission over failed again", exc_info=True)
        if self._sessions.get(commission_id) is session and not session.closing and not self._closed:
            session.handing_over = None
            self._said_while_handed.pop(commission_id, None)
            await self._fail(commission_id, "the lead's session could not be closed to hand it over", said="run")
            self._discard(commission_id)

    async def _handed_over(self, session: _Session) -> None:
        """Go on, in the task agent it was handed to, with a commission its project's leader handed over, the leader's session being closed.

        The leader lets go of its place and its session, and is free for
        the next commission of the project. The ledger has the other
        session as the commission's from here on, and which leader handed
        it over (the leader is told what came of it with the next
        commission that opens in it, ``Families.leader_notes``). The
        commission is queued for that session (``_wake_working``) with
        the leader's brief, framed as no word of the user's; the
        commission itself is what opens it there.
        """
        import secrets

        from config.prompts import prompts_nekopower_brain as texts
        from config.prompts.prompts_nekopower_sessions import frame

        commission_id, leader_id = session.commission_id, session.session_id
        assert session.handing_over is not None
        to, name, brief, preset, made = session.handing_over
        said_meanwhile = self._said_while_handed.setdefault(commission_id, [])
        candidates = self._candidates.get(commission_id)
        excerpt = self._excerpts.get(commission_id)
        # What was sent to the leader's session and not seen taken goes back
        # with it, as when any session goes (``_release``): it is pending
        # again, for the leader.
        back = [wake for wakes in session.handed.values() for wake in wakes]
        session.handed = {}
        self._discard(commission_id)
        if self._held_by.get(leader_id) == commission_id:
            # Let go of here: the commission is not over, and the look at
            # the hold ``_discard`` has made would leave it held.
            del self._held_by[leader_id]
        if candidates is not None:
            self._candidates[commission_id] = candidates
        if excerpt is not None:
            # What was said just before it was dispatched: the session it
            # goes to holds nothing to tell what it refers to.
            self._excerpts[commission_id] = excerpt
        if self._closed:
            self._said_while_handed.pop(commission_id, None)
            return
        try:
            if back:
                try:
                    await self._put_back(back)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - the commission is handed over all the same
                    logger.warning("giving back what a leader's session had not taken failed", exc_info=True)
            await self._ledger.update_session(leader_id, status="closed")
            stands = await self._looked(
                lambda: self._ledger.get(commission_id), what="for a commission a leader hands over"
            )
            if stands is not None and stands.status == "working":
                await self._ledger.update(commission_id, session_id=to, handed_by=leader_id)
                self._session_of[commission_id] = to
                await self._note_writer(to)
                nonce = secrets.token_hex(8)
                message = _Found(frame(texts.TASK_AGENT_HANDED_OVER.format(nonce=nonce), brief, nonce))
                if await self._wake_working(commission_id, message, reason="handed over by the lead of its project"):
                    while said_meanwhile:
                        # What the user said to it meanwhile, in the user's own words, after the brief:
                        # at once, with nothing awaited between, so that it goes along with the brief
                        # before the commission starts in the other task agent. (Whatever is said from
                        # here on finds it queued, and goes along with it.)
                        await self._wake_working(
                            commission_id, said_meanwhile.pop(0), reason="said while it was handed over"
                        )
                    # Nothing more is kept here from this moment, with nothing awaited since the last
                    # look: what is said while the record below is written finds the commission
                    # queued, or at work in the other task agent, and is said to it there (``tell``).
                    self._said_while_handed.pop(commission_id, None)
                    try:
                        # On record once it waits for the other task agent: one whose leader could
                        # not be closed, or that could not be queued, was not handed over.
                        await self._ledger.append_event(
                            commission_id,
                            "handed_over",
                            {"from": leader_id, "to": to, "name": name, "preset": preset, "new": made},
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 - the note is for the record; it is handed over all the same
                        logger.warning("noting that a leader handed its commission over failed", exc_info=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the commission fails with the reason
            logger.warning("handing commission %s over failed: %s", commission_id, exc)
            await self._fail(
                commission_id, f"it could not be handed over to a member of its project: {exc}", said="run", detail=str(exc)
            )
        finally:
            unsaid = self._said_while_handed.pop(commission_id, None)
            if unsaid:
                # Not handed over after all (stopped meanwhile, or it could not be queued): what the
                # user said to it meanwhile is on record with it (``told``) and reaches nobody.
                logger.warning(
                    "commission %s was not handed over; %d thing(s) the user said to it meanwhile were not passed on",
                    commission_id,
                    len(unsaid),
                )
            # The leader is free: what waits for it goes on.
            if self._queue and len(self._sessions) < self.limit and not self._closed:
                self._spawn(self._start_next())

    async def _cb_subagent_start(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = self._at_work_in(session_id)
        live = self._sessions.get(session_id)
        if live is not None and live.handing_over is not None:
            from config.prompts.prompts_nekopower_brain import HANDED_OVER_ALREADY

            raise RuntimeError(HANDED_OVER_ALREADY)
        parent = await self._ledger.get(session_id)
        if parent is None:
            raise RuntimeError("unknown commission")
        if parent.parent_id is not None:
            raise RuntimeError("a child task agent cannot start further task agents")
        workdir = await self._child_workdir(parent, payload.get("cwd"))
        preset = await self._preset_wanted(parent, payload.get("preset"))
        place = await self._ledger.get_session(parent.session_id) if parent.session_id else None
        if place is not None and place.kind == "project":
            await self._leader_starts_readers(place, preset)
        async with self._naming_members:
            # Read and filed with no other member of the team named between
            # (a question it asks is named under the same hold): one started
            # at once would read the names before this one is filed, and may
            # take the same.
            _lead, members = await self._team(session_id)
            taken = {member.name for member in members}
            name = payload.get("name")
            if not isinstance(name, str) or not name or name in taken or name == LEAD_NAME:
                number = len(members) + 1
                while f"agent-{number}" in taken:
                    number += 1
                name = f"agent-{number}"
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
                # Which preset it is to be opened by, on record with it from
                # the start: read when it is given its session, now or at a
                # later try (``_preset_chosen``).
                events=[("preset", {"preset": preset})] if preset is not None else [],
            )
        # A stop of the parent sets its status before it looks for children,
        # so a child filed after that look is caught here, and one filed
        # before it is stopped by the stop itself.
        current = await self._ledger.get(parent.id)
        if current is None or current.status not in ("working", "waiting_user"):
            await self._ledger.transition(child.id, "cancelled", reason="its parent was stopped")
            raise RuntimeError("the commission was stopped; no task agent is started")
        started = {"name": name, "commission_id": child.id}
        if preset is not None:
            started["preset"] = preset
        await self._ledger.append_event(parent.id, "subagent_started", started)
        # It starts now or not at all (see ``_NO_PLACE_REASON``); the parent
        # is told so and can do that part itself. Its result comes back to the
        # parent as a message.
        if not await self.submit(child, candidate_plugin_ids=self._candidates.get(parent.id, ()), wait=False):
            raise RuntimeError(f"{_NO_PLACE_REASON}; do this part yourself")
        return {"name": name}

    async def _leader_starts_readers(self, leader: Session, preset: str | None) -> None:
        """Raise unless the task agent a leader starts is of a preset that changes no file.

        A leader does none of the work, and not through a task agent it
        starts either: work that changes files, with a tool or through
        the shell, is a member's, handed over, where one does so in the
        project at a time and the user is asked about a new one
        (``_cb_hand_over``). What a leader starts itself looks or plans.
        Told by the preset as the task agent would have it
        (``Template.under``, ``Template.changes_files``); one that
        cannot be read counts as one that changes files.
        """
        from config.prompts.prompts_nekopower_brain import LEADER_SUBAGENT_WRITES
        from nekopower.host.ledger import PRESET_OF_KIND
        from nekopower.host.templates import NOT_FOR_CHILDREN, model_name

        wanted = preset or PRESET_OF_KIND["child"]
        over = await self._template_of(leader)
        listed = await asyncio.to_thread(self.templates.listing, leader.project)
        reads = [
            item.name
            for item in listed
            if item.template is not None and item.name not in NOT_FOR_CHILDREN and not item.template.under(over).changes_files
        ]
        if wanted not in reads:
            raise RuntimeError(
                LEADER_SUBAGENT_WRITES.format(preset=model_name(wanted), names=", ".join(model_name(name) for name in reads))
            )

    async def _preset_wanted(self, parent: Commission, named: Any) -> str | None:
        """Return the preset a task agent names for one it starts, or None when it names none; raises for one that cannot be used.

        By the name a model reads (``templates.model_name``) or the
        preset's own. Only one a task agent can be started with: not the
        lead of a project, nor a session of its own. The error lists the
        ones that can.
        """
        from config.prompts.prompts_nekopower_presets import PRESET_UNKNOWN, PRESET_UNUSABLE
        from nekopower.host.templates import NOT_FOR_CHILDREN, model_name

        if named is None:
            return None
        wanted = str(named).strip()
        listed = await asyncio.to_thread(self.templates.listing, parent.project)
        for item in listed:
            if item.name in NOT_FOR_CHILDREN or wanted not in (item.name, model_name(item.name)):
                continue
            if item.template is None:
                raise RuntimeError(PRESET_UNUSABLE.format(name=wanted, reason=item.error))
            return item.name
        names = ", ".join(model_name(item.name) for item in listed if item.valid and item.name not in NOT_FOR_CHILDREN)
        raise RuntimeError(PRESET_UNKNOWN.format(name=wanted, names=names))

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

        from nekopower.runtime.permissions import is_inside

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
        from config.nekopower_settings import WAKE_HELD_RETRY_SECONDS

        ids = [wake.id for wake in wakes]
        if self._closed or not ids:
            return Handover("held", WAKE_HELD_RETRY_SECONDS)
        target = await self._ledger.get_session(session_id)
        if target is None:
            await self._ledger.wakes_void(ids, "its session is not on record")
            return Handover("dropped")
        if not await self._ledger.session_alive(target):
            # Notes written to it, a time it set for itself that came when
            # nothing was at work in it: nothing wakes or opens a session
            # that has ended. (A commission that is under way in it keeps
            # it alive, so what it set for itself does not come here.)
            await self._ledger.wakes_void(ids, _SESSION_ENDED_REASON)
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
        from config.nekopower_settings import WAKE_HELD_RETRY_SECONDS

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
                # It hands over what it has, the user having allowed no
                # more to be used: a note would end with that, and its
                # sender be told what it stopped with. Once it is over, a
                # commission of its own is filed for the note.
                or live.wind_up_left is not None
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
            # for task agents). No turn is started with NekoPower switched
            # off or the quota used up; its first request is counted here,
            # ahead of it.
            held = await self._may_start(
                commission_id,
                session=await self._ledger.get_session(commission.session_id),
            )
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
            if counted and not self._is_switched_on():
                # Switched off while this was awaited: no turn is started.
                await self._put_back(fired, kick=False)
                return Handover("held", WAKE_HELD_RETRY_SECONDS)
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
                self._permissions.received_external_input(commission_id)
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

    async def _may_start(
        self, key: str, *, mark: str | None = None, session: Session | None = None
    ) -> Handover | None:
        """Say why a turn must not be started for what waits right now, or None when it may.

        NekoPower switched off, or no model to work with: it stays where it
        is and is tried again later. Otherwise the first model request of
        the turn is counted here, ahead of it: with the quota used up
        nothing is woken (the commission would fail on its way up, and
        what it waited for would be lost), and it is tried again when the
        quota has a request left, at the latest after the next midnight.

        ``mark`` is what the counted request is kept under, where that
        is not ``key``: one counted for a session with no commission yet
        (``_session_mark``). ``session`` is the session the turn is
        started in: whether there is a model to work with, and whether its
        requests are counted at all, is told by the model it is sent to
        (``session_model``); only those that go with the free key are.
        One whose run is over and that is still open in a runtime goes on
        with the model and the key it was opened with: by those.
        """
        from config.nekopower_settings import WAKE_HELD_RETRY_SECONDS

        mark = key if mark is None else mark
        if not self._is_switched_on():
            return Handover("held", WAKE_HELD_RETRY_SECONDS)
        kept = session
        live = self._sessions.get(key)
        if live is not None and not live.idle:
            live = None
        if live is not None:
            available = not self._switched_off()
        else:
            available, _reason = await self.available(kept)
        if not available:
            return Handover("held", WAKE_HELD_RETRY_SECONDS)
        if self._meter is None or mark in self._prepaid_starts:
            return None
        if not (live.free if live is not None else await self._goes_free(kept)):
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
        from config.nekopower_settings import COMMISSION_TITLE_MAX_TOKENS, DISPATCH_TEXT_MAX_TOKENS, WAKE_HELD_RETRY_SECONDS
        from config.prompts.prompts_nekopower_sessions import SESSION_COMMISSION_TITLE
        from nekopower.host.task_brain import _texts
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
        held = await self._may_start(target.id, mark=counted_for, session=target)
        if held is not None:
            return held
        # A request was counted just now, for the session (not where no
        # meter counts, nor where the meter could not be asked). It stays
        # counted for the session until a commission is filed that has
        # it: where nothing is filed after all (the ledger cannot be read
        # just now, the notes were dropped), the next try does not count
        # a second one.
        # The one the session was at last, which is the one whose opening
        # message it took last: not the one filed last, where an older one
        # was taken up again after it.
        opened = await self._ledger.opened_last(target.id)
        last = (await self._ledger.get(opened) if opened else None) or await self._ledger.last_commission(target.id)
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
        if not await self._ledger.session_alive(current):
            # Over while this waited (``hand_over`` looked before).
            await self._ledger.wakes_void(ids, _SESSION_ENDED_REASON)
            return Handover("dropped")
        if target.id in self._clearing:
            # Dropped the next time, should the removal go through.
            return Handover("held", _CLOSING_RETRY_SECONDS)
        if not self._is_switched_on():
            # Switched off while this waited: nothing is filed now. (The
            # request counted for the session stays counted, for then.)
            return Handover("held", WAKE_HELD_RETRY_SECONDS)
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
        from config.prompts import prompts_nekopower_sessions as words

        if other is None:
            return words.SESSION_WHOSE.format(lanlan_name="?")
        template = words.SESSION_WHOSE if other.lanlan_name == lanlan_name else words.SESSION_WHOSE_OTHER
        return template.format(lanlan_name=other.lanlan_name)

    async def _framed_note(self, note: Any, lanlan_name: str, *, answerable: bool) -> str:
        """Return a note from another session as its receiver reads it: framed, with a marker made for this once."""
        import secrets

        from config.prompts import prompts_nekopower_sessions as words

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

        from config.nekopower_settings import SESSION_NOTICE_WAIT_SECONDS
        from config.prompts import prompts_nekopower_sessions as words

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
        from config.prompts.prompts_nekopower_sessions import MESSAGE_REFUSALS

        found = await self._ledger.sessions_named(name)
        if not found:
            raise RuntimeError(MESSAGE_REFUSALS["unknown"].format(name=name))
        if len(found) > 1:
            ids = ", ".join(f"{item.id} ({item.name!r} of {item.lanlan_name})" for item in found[:5])
            raise RuntimeError(MESSAGE_REFUSALS["ambiguous"].format(name=name, ids=ids))
        return found[0]

    async def _state_of(self, session: Session) -> str:
        """Say where a session stands right now, as ``list_sessions`` tells it. No runtime is asked."""
        from config.prompts.prompts_nekopower_sessions import SESSION_STATE_WORDS as words

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
        if session.status == "cleared":
            return words["cleared"]
        return words["closed"] if await self._ledger.session_alive(session) else words["ended"]

    async def _cb_session_list(
        self, session_id: str, payload: dict[str, Any], action: _SessionAction | None = None,
    ) -> dict[str, Any]:
        from config.nekopower_settings import (
            CARD_DUTY_MAX_TOKENS,
            SESSION_LIST_COMMISSIONS,
            SESSION_LIST_LOOKED_AT,
            SESSION_LIST_ROW_MAX_TOKENS,
            SESSION_LIST_ROWS,
        )
        from config.prompts import prompts_nekopower_sessions as words
        from nekopower.host.selection import similarity
        from nekopower.host.templates import model_name
        from utils.tokenize import truncate_to_tokens

        caller = await self._caller(session_id)
        if action is not None:
            action.require()
            action.record.scope_ref = caller.session_id
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
                # Which preset it is, and what its card says it takes: what
                # a task agent that looks for whom to ask goes by.
                preset=model_name(session.preset),
                duty=truncate_to_tokens(session.duty, CARD_DUTY_MAX_TOKENS) or words.SESSION_LIST_NO_DUTY,
                state=await self._state_of(session),
                title=row.commissions[0].title if row.commissions else words.SESSION_LIST_NO_COMMISSION,
                active=_clock_time(session.last_active_at),
            )
            line = truncate_to_tokens(line, SESSION_LIST_ROW_MAX_TOKENS)
            lines.append(line + (words.SESSION_LIST_THIS if session.id == caller.session_id else ""))
        return {"text": "\n".join(lines)}

    async def _cb_session_read(
        self, session_id: str, payload: dict[str, Any], action: _SessionAction | None = None,
    ) -> dict[str, Any]:
        import secrets

        from config import nekopower_settings as settings
        from config.prompts import prompts_nekopower_sessions as words
        from nekopower.host import records
        from utils.tokenize import truncate_to_tokens

        caller = await self._caller(session_id)
        if await self._permissions.forbidden("outside_read"):
            # What another session left lies outside this one's working
            # directory, whichever tool reads it: a user who forbade
            # reading there has forbidden this too.
            raise RuntimeError(DENIED_BY_SETTINGS)
        target = await self._session_named(str(payload.get("session") or ""))
        if action is not None:
            action.require()
            action.record.scope_ref = target.id
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

    async def _cb_session_send(
        self, session_id: str, payload: dict[str, Any], action: _SessionAction | None = None,
    ) -> dict[str, Any]:
        import math

        from config import nekopower_settings as settings
        from config.prompts import prompts_nekopower_sessions as words
        from utils.tokenize import acount_tokens

        refusals = words.MESSAGE_REFUSALS
        caller = await self._caller(session_id)
        if caller.parent_id is not None:
            raise RuntimeError(refusals["from_child"])
        available, _reason = await self.available(await self._ledger.get_session(caller.session_id))
        if not available:
            raise RuntimeError(refusals["switched_off"])
        target = await self._session_named(str(payload.get("to") or ""))
        # What is written is worked on in the other session, by its own model.
        available, _reason = await self.available(target)
        if not available:
            raise RuntimeError(refusals["switched_off"])
        if action is not None:
            action.require()
            action.record.scope_ref = target.id
        if target.id == caller.session_id:
            raise RuntimeError(refusals["self"])
        if target.kind == "fork":
            raise RuntimeError(refusals["fork"].format(name=target.name))
        if target.kind not in _ADDRESSED_KINDS:
            raise RuntimeError(refusals["child"].format(name=target.name))
        if target.status == "cleared":
            raise RuntimeError(refusals["cleared"].format(name=target.name))
        if not await self._ledger.session_alive(target):
            raise RuntimeError(
                refusals["ended"].format(name=target.name, days=settings.SESSION_SINGLE_LIFETIME_DAYS)
            )
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
        if action is not None:
            action.require()
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

    async def _cb_wake_schedule(
        self, session_id: str, payload: dict[str, Any], action: _SessionAction | None = None,
    ) -> dict[str, Any]:
        from config import nekopower_settings as settings
        from config.prompts import prompts_nekopower_sessions as words
        from utils.tokenize import truncate_to_tokens

        caller = await self._caller(session_id)
        if action is not None:
            action.require()
            action.record.scope_ref = caller.session_id
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

    async def _cb_session_ask(
        self, session_id: str, payload: dict[str, Any], action: _SessionAction | None = None,
    ) -> dict[str, Any]:
        """Have a copy of another session answer a question; the answer comes back as a message.

        The other session must have a commission under way (one at work,
        resting, or waiting for its user): one that is done is read with
        ``read_session``. A copy of what was said in it is made
        (``fork_session``), and a commission is filed in the copy under
        the one that asks, the way a task agent another one starts is:
        it starts now or not at all, what it uses counts with the one that
        asks, it is stopped with it, and what it answers comes back to it
        framed (``_tell_parent``). The copy has none of the tools that
        write, run commands or call capabilities, nor those that reach
        other sessions, and makes no more than ``SESSION_ASK_MAX_REQUESTS``
        requests. What a session may ask is counted on its own, apart
        from the notes it writes (``Ledger.ask_add``).
        """
        import math

        from config import nekopower_settings as settings
        from config.prompts import prompts_nekopower_sessions as words
        from nekopower.host.task_brain import _texts
        from utils.tokenize import acount_tokens

        refusals = words.ASK_REFUSALS
        caller = await self._caller(session_id)
        if caller.parent_id is not None:
            raise RuntimeError(refusals["from_child"])
        if await self._permissions.forbidden("outside_read"):
            # The copy answers from the other session's record, which lies
            # outside this one's working directory: a user who forbade
            # reading there has forbidden this too, as for ``read_session``.
            raise RuntimeError(DENIED_BY_SETTINGS)
        available, _reason = await self.available(await self._ledger.get_session(caller.session_id))
        if not available:
            raise RuntimeError(refusals["switched_off"])
        target = await self._session_named(str(payload.get("session") or ""))
        # A copy of the other session answers, by that session's model.
        available, _reason = await self.available(target)
        if not available:
            raise RuntimeError(refusals["switched_off"])
        if action is not None:
            action.require()
            action.record.scope_ref = target.id
        if target.id == caller.session_id:
            raise RuntimeError(refusals["self"])
        if target.kind not in _ADDRESSED_KINDS:
            raise RuntimeError(refusals["child"].format(name=target.name))
        if target.status == "cleared":
            raise RuntimeError(refusals["cleared"].format(name=target.name))
        if not await self._ledger.session_alive(target):
            raise RuntimeError(
                refusals["ended"].format(name=target.name, days=settings.SESSION_SINGLE_LIFETIME_DAYS)
            )
        if target.runtime == RUNTIME_DSH:
            raise RuntimeError(refusals["dsh"].format(name=target.name))
        if not await self._ledger.select(session_id=target.id, statuses=_STILL_WORKING, limit=1):
            raise RuntimeError(refusals["not_at_work"].format(name=target.name))
        if self._closed or len(self._sessions) >= self.limit:
            # The copy would find no place to answer in: neither copied nor
            # counted. (A place taken while it is copied fails the question
            # there; the copy is removed with the other session's in time.)
            raise RuntimeError(refusals["no_place"])
        question = str(payload.get("question") or "").strip()
        tokens = await acount_tokens(question)
        if tokens > settings.SESSION_MESSAGE_MAX_TOKENS:
            raise RuntimeError(refusals["too_long"].format(tokens=tokens, limit=settings.SESSION_MESSAGE_MAX_TOKENS))
        if getattr(self._waker, "stopping", False):
            # Everything is being stopped, the caller with it (see
            # ``_cb_session_send``): nothing is started for it now.
            raise RuntimeError(refusals["over"])
        filed: list[Commission] = []
        copies: list[Session] = []

        async def hand_over() -> str:
            # Counted, copied, filed, given the copy and handed over in a
            # task of its own, which whoever asked does not cut short (a
            # tool call of dsh that is given up is cancelled, the copy may
            # take a minute): a question counted and then left would count
            # against what may be asked with nothing asked; a commission
            # left queued here would be started by nobody, and the one that
            # asked would wait for its answer for ever; a copy left on its
            # own would wait to be removed with old sessions. Where a step
            # fails, the commission that was filed is ended and the copy
            # removed. One that was not asked does not count against what
            # may be asked. Returns why it was not asked, as whoever asked
            # is told; nothing where it was handed over.
            if action is not None:
                action.require()
            asked, refusal, wait = await self._ledger.ask_add(
                from_session=str(caller.session_id),
                from_commission=caller.id,
                to_session=target.id,
                body=question,
                asks_per_hour=settings.SESSION_ASKS_PER_HOUR,
            )
            if asked is None:
                return refusals[refusal].format(name=target.name, wait=math.ceil(wait), limit=settings.SESSION_ASKS_PER_HOUR)

            async def not_asked() -> None:
                # Counted against nothing: it was not asked.
                try:
                    await self._ledger.message_void(asked.id, _ASK_NOT_ASKED_REASON)
                except Exception:  # noqa: BLE001 - it counts for the hour, which is all that is lost
                    logger.warning("taking a question that was not asked out of the count failed", exc_info=True)

            try:
                try:
                    if action is not None:
                        action.require()
                    copy = await self.fork_session(target.id)
                except ForkRefused as exc:
                    await not_asked()
                    return refusals["no_copy"].format(name=target.name, reason=str(exc))
                copies.append(copy)
                async with self._naming_members:
                    # Read and filed with no other member of a team named
                    # between: one started at once would read the names
                    # before this one is filed, and may take the same.
                    _lead, members = await self._team(caller.id)
                    taken = {member.name for member in members}
                    number = 1
                    while f"ask-{number}" in taken:
                        number += 1
                    child, _created = await self._ledger.create(
                        lanlan_name=caller.lanlan_name,
                        title=_texts(words.SESSION_ASK_TITLE, caller.lang).format(name=target.name),
                        dispatch_text=question,
                        user_text="",
                        source="ask",
                        route="task_agent",
                        parent_id=caller.id,
                        name=f"ask-{number}",
                        project=copy.project,
                        workdir=copy.workdir,
                        # Worked on in the copy: filed with it, so that it
                        # names the session it answers for whatever happens
                        # next.
                        session_id=copy.id,
                        conversation_id=caller.conversation_id,
                        lang=caller.lang,
                    )
                filed.append(child)
                # As for a task agent it starts (``_cb_subagent_start``):
                # one that was stopped meanwhile starts nothing.
                current = await self._ledger.get(caller.id)
                if current is None or current.status not in ("working", "waiting_user"):
                    await self._ledger.transition(child.id, "cancelled", reason="its parent was stopped")
                    await self._discard_copy(copy)
                    await not_asked()
                    return refusals["over"].format(name=target.name)
                await self._ledger.append_event(
                    caller.id, "subagent_started", {"name": child.name, "commission_id": child.id, "asks": target.id}
                )
                if action is not None:
                    action.require()
                if await self.submit(child, wait=False, still_wanted=action.active if action is not None else None):
                    return ""
                if action is not None:
                    action.require()
                # Not started, and failed by ``submit``: no place was free
                # (it was taken while the copy was made), the host is
                # shutting down, or the copy could not be given to it. Whoever
                # asked is told why by this call. Of the last it is told by
                # ``submit`` as well (``_fail``, the way it is told of a
                # question that fails later on), in the same words.
                ended = await self._ledger.get(child.id)
                await self._discard_copy(copy)
                await not_asked()
                why = ended.status_reason if ended is not None else ""
                if not why or why == _NO_PLACE_REASON:
                    return refusals["no_place"].format(name=target.name)
                return refusals["not_started"].format(name=target.name, reason=why)
            except BaseException:
                if filed:
                    try:
                        # Ended the way any of its task agents ends: the one
                        # that asked is told (``_tell_parent``), so that one
                        # that ended its turn to wait for the answer is not
                        # left waiting for it. One that was stopped is not
                        # woken by this.
                        await self._end(
                            filed[0].id,
                            "failed",
                            reason=_ASK_NOT_HANDED_OVER_REASON,
                            text=_ASK_NOT_HANDED_OVER_REASON,
                        )
                    except Exception:  # noqa: BLE001 - as much as can be done; the error is raised below
                        logger.warning("ending a question that could not be handed over failed", exc_info=True)
                if copies:
                    try:
                        await self._discard_copy(copies[0])
                    except Exception:  # noqa: BLE001 - removed with the other session's in time; the error is raised below
                        logger.warning("removing the copy made for a question that was not handed over failed", exc_info=True)
                await not_asked()
                raise

        if action is not None and action.handing_over is not None:
            action.handing_over.set()
            refused = await hand_over()
        else:
            refused = await asyncio.shield(self._spawn(hand_over()))
        if refused:
            raise RuntimeError(refused)
        return {"message": words.ASK_QUEUED.format(name=target.name)}

    async def _discard_copy(self, copy: Session) -> None:
        """Remove a copy that was made for a question that was not handed over, at once.

        Nothing was said in it, and nobody will: it would only wait to be
        removed with the session it was copied from. Its files are claimed
        as any removal claims them (``hold_files``); one that is in use
        after all is left to that. It is put on record as cleared only once
        its files are seen to be gone: one whose files could not be removed
        is left as it is, for the removal of old sessions, which passes over
        none that is not on record as cleared.
        """
        import os
        import shutil

        if not self.hold_files(copy.id):
            if copy.id in self._held_by:
                # A rejected ask can already be closing in the runtime.
                self._unused_ask_copies[copy.id] = copy
            return
        try:
            target = copy.session_dir

            def removed() -> bool:
                if not target:
                    return True
                shutil.rmtree(target, True)
                return not os.path.lexists(str(target))

            if await asyncio.to_thread(removed):
                await self._ledger.mark_session_cleared(copy.id)
            else:
                logger.warning("the copy %s made for a question could not be removed; it is removed with old sessions", copy.id)
        finally:
            self.release_files(copy.id)

    async def _asked_name(self, commission: Commission) -> str:
        """Return the name of the session a copy answers for (``_cb_session_ask``)."""
        copy = await self._ledger.get_session(commission.session_id) if commission.session_id else None
        source = await self._ledger.get_session(copy.forked_from) if copy is not None and copy.forked_from else None
        if source is not None:
            return source.name
        return copy.name if copy is not None else commission.name or commission.id

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

        from config.prompts.prompts_nekopower_sessions import (
            SESSION_RECORD_READ_REFUSED,
            SESSION_RECORD_SEARCH_REFUSED,
            SESSION_RECORD_WRITE_REFUSED,
            SESSION_WRITE_REFUSED,
        )
        from config.nekopower_settings import SESSION_RECORD_ALIAS_SCAN_ENTRIES
        from nekopower.runtime.permissions import is_inside
        from nekopower.runtime.snapshot import SYSTEM_FILE

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
            # A directory whose files would be read: not one that is
            # written to, which searches nothing. (Asked once, and only
            # where it is gone by: it costs a call, and every file a task
            # agent writes comes by here. Asked so that a directory which
            # is not to be reached says no, and does not raise.)
            searched = not writes and not names_only and os.path.isdir(target)
            holds = (
                (in_own or in_work)
                and searched
                and any(session_dir and is_inside(Path(session_dir), target) for _id, _workdir, session_dir in places)
            )
            aliased = False
            if searched:
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
                    apart: dict[str, set[tuple[int, int]]] = {}
                    for _id, _workdir, session_dir in places:
                        if not session_dir:
                            continue
                        try:
                            # (Under its real name: going through a
                            # directory follows no link, and meets it
                            # under that name.)
                            real = os.path.realpath(session_dir)
                            kept = os.stat(real)
                        except OSError:
                            continue  # Not there.
                        name = os.path.normcase(os.path.basename(real))
                        apart.setdefault(name, set()).add((kept.st_dev, kept.st_ino))
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

    # -- what a commission may use ----------------------------------------

    async def _owed_tokens(self, root: str, owed_at: dict[Any, Any] | None = None) -> int:
        """Return what the work a commission belongs to used that the ledger has not taken yet.

        Of the commission no task agent started (``root``) and of those
        that descend from it. Reported by a runtime, and not in what
        the ledger says was used: left out, the work would go past
        what it may use without the user being asked.
        """
        total = 0
        # (``owed_at``: what was owed at some point, in place of what is now.)
        for (_session_id, commission_id), owed in list((self._usage_owed if owed_at is None else owed_at).items()):
            if commission_id is None:
                # A request the host made by itself: no commission's.
                continue
            if commission_id != root:
                try:
                    standing = await self._ledger.usage_standing(commission_id)
                except Exception:  # noqa: BLE001 - whose work it is cannot be told: not counted
                    continue
                if standing is None or standing.root != root:
                    continue
            total += owed[1] + owed[2] + owed[3] + owed[4]
        return total

    def _usage_settled(self, commission: Commission) -> None:
        """Forget what was noted of the usage of a commission that is over: whether more was refused for it."""
        self._winding_up.pop(commission.id, None)
        if commission.parent_id is None:
            self._refused_more.discard(commission.id)
            self._usage_asks.pop(commission.id, None)

    async def _usage_stopped_text(self, commission: Commission) -> str:
        """Return what she is told of a commission that was put down for what it used, in its language."""
        from config.prompts.prompts_nekopower_brain import USAGE_LIMIT_STOPPED_TEXT
        from nekopower.host.reports import tokens_text
        from nekopower.host.task_brain import _texts

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
        from config.nekopower_settings import TASK_AGENT_USAGE_LIMIT_TOKENS, TASK_AGENT_USAGE_WIND_UP_REQUESTS
        from config.prompts.prompts_nekopower_brain import TASK_AGENT_USAGE_WIND_UP
        from nekopower.host.reports import tokens_text

        del payload
        limit = int(TASK_AGENT_USAGE_LIMIT_TOKENS or 0)
        commission_id = self._held_by.get(session_id)
        session = self._sessions.get(commission_id or "")
        if session is not None and session.carries_on_for is not None and not session.closing:
            # A copy that a commission is carried on in: one that called a
            # tool which changes something is handed back (``_watch_copy``,
            # seen once what the session said before it asked is handled).
            await self._caught_up()
            if self._sessions.get(commission_id or "") is session and session.hand_back is not None and not session.closing:
                verdict = await self._hand_back_step(session)
                if verdict is not None:
                    return verdict
                # (A request it has for writing down what it found, or one
                # whose commission ended meanwhile: what it may use is
                # looked at as for any.)
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
        if any(key[0] == session_id for key in self._usage_owed):
            # What requests of this session used that the ledger did not
            # take: tried once more before the account is looked at.
            await self._write_owed_usage(session_id)
            if self._sessions.get(commission_id) is not session or session.closing:
                return None
        if session.wind_up_left is None and commission_id in self._winding_up:
            # More of a round in which it was told to hand over, in a
            # session opened anew (the one it was told in was closed
            # while it waited for an answer): with what it had left.
            session.wind_up_left = self._winding_up[commission_id]
            session.wind_up_told_again = False
        if session.wind_up_left is not None:
            return await self._wind_up_step(session)

        async def standing_over() -> Any:
            # What stands against it, when nothing more may be used; else None.
            commission = await self._ledger.get(commission_id)
            if commission is None or commission.status != "working":
                # It has reported how it ended, or waits for the user as it is.
                return None
            # With what was used and is not in the ledger yet (it did not
            # take it): used all the same, and counted once, here or there.
            # Read with no write of usage under way or beginning, which
            # would have it in neither place (a session that was let go
            # of has what it owes written by a task of its own).
            async with self._usage_read():
                standing = await self._ledger.usage_standing(commission_id)
                if standing is None:
                    return None
                owed = await self._owed_tokens(standing.root)
            if owed:
                standing = dataclasses.replace(standing, used=standing.used + owed)
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
                    # As much again, if the user allows it, from what had
                    # been used when they said so: begun the moment the
                    # answer is in (``_UsageCut``), and not when this
                    # gets to go on, which is a while later (the
                    # commission is put back to work first).
                    # Of this question's answer, and of no other's: one
                    # that was asked for the commission before it (by
                    # something it called, say) is answered first.
                    cut = _UsageCut(self)
                    try:
                        decision = await self._permissions.ask_each_time(
                            commission_id,
                            USAGE_LIMIT,
                            detail={"used": tokens_text(standing.used), "more": tokens_text(limit)},
                            at_answer=cut.begin,
                        )
                        if decision.allow:
                            # (Answered by a way that does not come by the
                            # session: it ends here, then.)
                            cut.begin()
                            # What was used before the answer and is not
                            # in the ledger is allowed on top: left out,
                            # that much of what was allowed would be
                            # gone the moment it is written.
                            owed = await cut.owed(standing.root)
                            await self._ledger.allow_usage(commission_id, limit + owed)
                    finally:
                        cut.end()
                    if decision.allow:
                        # What the ledger did not take before is tried
                        # once more (the user may have taken a while).
                        await self._write_owed_usage()
                        return None
                    self._refused_more.add(standing.root)
        if self._sessions.get(commission_id) is not session or session.closing:
            # Stopped while the user was asked.
            return {"stop": True}
        # The request this is asked before is the first of those it has left.
        session.wind_up_left = max(0, TASK_AGENT_USAGE_WIND_UP_REQUESTS - 1)
        self._winding_up[commission_id] = session.wind_up_left
        try:
            await self._ledger.append_event(
                commission_id, "usage_refused", {"used": standing.used, "requests_left": TASK_AGENT_USAGE_WIND_UP_REQUESTS}
            )
        except Exception:  # noqa: BLE001 - the note is for the record; the task agent is told all the same
            # Left untold, it would go on working on the requests it has
            # left, and be stopped without ever having been told to hand
            # over what it has.
            logger.warning("noting that more usage was refused failed", exc_info=True)
        return {"notice": TASK_AGENT_USAGE_WIND_UP.format(requests=TASK_AGENT_USAGE_WIND_UP_REQUESTS)}

    async def _wind_up_step(self, session: _Session) -> dict[str, Any] | None:
        """Let a task agent that is handing over what it has make one more request, or end its commission.

        It was told how many requests it has for that. When they are used
        and it has reported nothing that ended the commission, the
        commission is put down as stuck with the reason, what it started
        is stopped, and its session is closed.
        """
        if session.wind_up_left:
            left = session.wind_up_left
            session.wind_up_left -= 1
            self._winding_up[session.commission_id] = session.wind_up_left
            if not session.wind_up_told_again:
                # The first request of another run of the same round: what
                # it was told went with the run it was told in, and without
                # it the task agent would go on working on what it is to
                # hand over, on the requests it has left for that.
                from config.prompts.prompts_nekopower_brain import TASK_AGENT_USAGE_WIND_UP

                session.wind_up_told_again = True
                return {"notice": TASK_AGENT_USAGE_WIND_UP.format(requests=left)}
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
        from config.nekopower_settings import TASK_AGENT_USAGE_LIMIT_TOKENS

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

    # -- carrying on in a copy of a project's session ---------------------

    def _carrying_on(self, session_id: str) -> int:
        """How many commissions are carried on in copies of a session, or are about to be.

        Each once: one that was started in its copy a moment ago is still
        among those being copied for until that is through.
        """
        at_work = {commission_id for commission_id, item in self._sessions.items() if item.carries_on_for == session_id}
        return len(at_work | self._copying_for.get(session_id, set()))

    def _waits_for(self, commission_id: str, session_id: str) -> bool:
        """Whether a commission is queued for a session that another commission holds, and nothing else.

        Held by another commission, that is: not only kept from starting
        while a commission is being put back among those that wait for it
        (``_handing_back_to``), which is over in a moment, and after which
        it starts there in its turn.
        """
        holder = self._held_by.get(session_id)
        return (
            commission_id in self._queue
            and commission_id not in self._sessions
            and commission_id not in self._resume
            and self._session_of.get(commission_id) == session_id
            and holder is not None
            and holder != commission_id
        )

    def _try_carry_on(self, commission: Commission) -> None:
        """Have a commission that was queued behind another one of its session carried on in a copy, if it may be.

        Looked at once, as it is queued, with nothing awaited: whether it
        is of the kind that is (``_carried_on``), whether its session is
        held by another commission, and whether that many are carried on
        in copies of it already (``TASK_AGENT_FORK_CONTINUE_MAX_PER_SESSION``).
        The rest is looked at by ``_carry_on_in_copy``, which runs by itself:
        the commission stays queued meanwhile, and is started in its
        session as ever should that be free first.
        """
        from config import nekopower_settings as settings

        if not settings.TASK_AGENT_FORK_CONTINUE_ENABLED or self._closed or not _carried_on(commission):
            return
        main = self._session_of.get(commission.id)
        if main is None or not self._waits_for(commission.id, main):
            return
        if self._carrying_on(main) >= settings.TASK_AGENT_FORK_CONTINUE_MAX_PER_SESSION:
            return
        waiting = self._copying_for.setdefault(main, set())
        waiting.add(commission.id)

        def over(_done: Any) -> None:
            waiting.discard(commission.id)
            if not waiting and self._copying_for.get(main) is waiting:
                del self._copying_for[main]

        self._spawn(self._carry_on_in_copy(commission.id, main)).add_done_callback(over)

    async def _carry_on_in_copy(self, commission_id: str, main: str) -> None:
        """Carry a queued commission on in a copy of the session it waits for, if that is worth it.

        Only a project's session of the built-in runtime is copied, and
        only while the commission that holds it is at work or waits for the
        user (one that rests lets the session go). Its conversation is sent
        with every request of the copy: from a provider's cache, most
        likely, while the session made a request lately
        (``TASK_AGENT_FORK_CONTINUE_HOT_SECONDS``); past that, it is not
        copied when it is long (``TASK_AGENT_FORK_CONTINUE_COLD_MAX_TOKENS``),
        and the commission waits as it would have. Nor while no place is
        free, NekoPower is switched off, or everything is being stopped.

        Copied (``fork_session``), and looked at again with nothing awaited
        from there to where it takes its place: still queued for the
        session, which is still held, with a place free. Then it is taken
        out of the queue and started in the copy, which the ledger has as
        its session from then on. Otherwise the copy is removed at once and
        the commission waits on.
        """
        from config import nekopower_settings as settings

        if not self._waits_for(commission_id, main):
            return
        kept = await self._ledger.get_session(main)
        if kept is None or kept.kind not in PROJECT_KINDS or kept.runtime == RUNTIME_DSH or kept.status == "cleared":
            return
        holder = self._held_by.get(main)
        stands = await self._ledger.get(holder) if holder is not None else None
        if stands is None or stands.status not in ("working", "waiting_user") or stands.resting:
            return

        def worth_it() -> bool:
            # Looked at again once it is copied, which takes a moment: a
            # cache that went cold meanwhile makes a long copy dear as well.
            last = self._last_request_at.get(main)
            hot = last is not None and asyncio.get_running_loop().time() - last <= settings.TASK_AGENT_FORK_CONTINUE_HOT_SECONDS
            return hot or kept.context_tokens <= settings.TASK_AGENT_FORK_CONTINUE_COLD_MAX_TOKENS

        def may() -> bool:
            return (
                worth_it()
                # Its record cleared meanwhile: the copy holds what is gone.
                and kept.status != "cleared"
                and self._waits_for(commission_id, main)
                and not self._closed
                and len(self._sessions) < self.limit
                and self._is_switched_on()
                and not getattr(self._waker, "stopping", False)
            )

        if not may():
            return
        # Only a side question: one answered by looking, with nothing
        # changed. Work waits for the session, and for whatever the session
        # waits for the user to answer first. Asked last, as it costs a
        # request, of a commission that would be carried on otherwise; and
        # all of it looked at again once it is told.
        if not await self._is_side_question(commission_id, may) or not may() or not self._still_side_question(commission_id):
            return
        try:
            copy = await self.fork_session(main)
        except ForkRefused as exc:
            logger.info("commission %s waits for its session: it could not be copied (%s)", commission_id, exc)
            return
        except Exception:  # noqa: BLE001 - it waits for its session as it would have
            logger.warning("copying a session for commission %s failed; it waits for the session", commission_id, exc_info=True)
            return
        try:
            # How long the session's conversation is by now: it may have
            # grown while it was copied.
            kept = await self._ledger.get_session(main) or kept
        except Exception:  # noqa: BLE001 - looked at as it was read before
            logger.warning("reading a session again after it was copied failed", exc_info=True)
        if not may() or not self._still_side_question(commission_id):
            await self._discard_copy(copy)
            return
        self._queue.remove(commission_id)
        self._session_of[commission_id] = copy.id
        if not self._take_place(commission_id):
            # (Not to be: a place is free, and the copy is held by nobody.)
            self._session_of[commission_id] = main
            self._queue.append(commission_id)
            await self._discard_copy(copy)
            return
        # (Counted among those carried on in copies of the session from here
        # on: by ``_copying_for`` until it is started, by its place after.)
        # Until it is on record with the copy, or back in the queue, nothing
        # else starts in the session itself: one queued behind it would
        # otherwise go first, should it come back.
        self._handing_back_to[main] = self._handing_back_to.get(main, 0) + 1
        on_record = False
        try:
            on_record = await self._put_on_record_with_copy(commission_id, copy, main, holder)
        finally:
            left = self._handing_back_to[main] - 1
            if left:
                self._handing_back_to[main] = left
            else:
                del self._handing_back_to[main]
            if self._queue and len(self._sessions) < self.limit and not self._closed:
                self._spawn(self._start_next())
        if on_record:
            await self._launch(commission_id)

    async def _is_side_question(self, commission_id: str, still: Callable[[], bool] | None = None) -> bool:
        """Whether a commission is a side question, as the judge says (``set_side_question_judge``).

        Asked once for the same words of the commission: again only when
        they changed (a follow-up went on with it). Not told (no judge, a
        request that failed, an answer that cannot be read): it is not one,
        and that is not kept, so it is asked again the next time.
        """
        judge = self._side_question_judge
        if judge is None:
            return False
        commission = await self._ledger.get(commission_id)
        if commission is None or (still is not None and not still()):
            # (Nor asked of one that would not be carried on by now.)
            return False
        # What the session waits for the user to answer, if it does: a
        # request that goes on with that is work, which the judge cannot
        # tell without it.
        main = self._session_of.get(commission_id)
        waiting_for = await self._waited_on(main) if main else ""
        if still is not None and not still():
            return False
        words = f"{commission.dispatch_text}\x00{commission.user_text}\x00{waiting_for}"
        added = self._said_meanwhile(commission_id)
        said = self._side_said.get(commission_id)
        if said is not None and said[0] == words and said[1] == added:
            return said[2]
        try:
            verdict = await judge(
                commission.dispatch_text, "\n".join([commission.user_text, *added]).strip(), waiting_for
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - not told: it waits for its session, as work does
            logger.warning("telling whether commission %s is a side question failed", commission_id, exc_info=True)
            verdict = None
        if verdict is None:
            logger.info("commission %s waits for its session: whether it is a side question was not told", commission_id)
            return False
        # Kept with what was said to it meanwhile, as it was asked: what is
        # said to it after that is looked at by ``_still_side_question``.
        if commission.status not in ENDED_STATUSES:
            self._side_said[commission_id] = (words, added, verdict)
        if not verdict:
            logger.info("commission %s waits for its session: it is work, not a side question", commission_id)
        return verdict

    def _said_meanwhile(self, commission_id: str) -> tuple[str, ...]:
        """What the user said to a queued commission since it was handed over, which goes along with it when it starts."""
        return tuple(
            str(note) for note in self._notes.get(commission_id, []) if not isinstance(note, (_Handed, _Unsaid, _Found))
        )

    def _still_side_question(self, commission_id: str) -> bool:
        """Whether what goes along with a commission is still what was told to be a side question (``_is_side_question``)."""
        said = self._side_said.get(commission_id)
        return said is not None and said[2] and said[1] == self._said_meanwhile(commission_id)

    async def _waited_on(self, session_id: str) -> str:
        """Return the question that the commission holding a session waits for the user to answer; empty for none."""
        from config.nekopower_settings import TASK_AGENT_FORK_PENDING_QUESTION_MAX_TOKENS
        from utils.tokenize import truncate_to_tokens

        holder = self._held_by.get(session_id)
        stands = await self._ledger.get(holder) if holder is not None else None
        if stands is None or stands.status != "waiting_user":
            return ""
        question = stands.question() or {}
        asked = question.get("question")
        if not isinstance(asked, str) or not asked.strip():
            parts: list[str] = []

            def walk(value: Any) -> None:
                if isinstance(value, str):
                    parts.append(value)
                elif isinstance(value, dict):
                    for key, item in value.items():
                        if key not in ("id", "kind"):
                            walk(item)
                elif isinstance(value, list):
                    for item in value:
                        walk(item)

            walk(question)
            asked = " ".join(parts)
        asked = " ".join(asked.split())
        return truncate_to_tokens(asked, TASK_AGENT_FORK_PENDING_QUESTION_MAX_TOKENS) if asked else ""

    async def _put_on_record_with_copy(self, commission_id: str, copy: Session, main: str, holder: str | None) -> bool:
        """Put a commission on record with the copy it is carried on in; back in the queue where that fails. Returns whether it is on record (``_carry_on_in_copy``)."""
        try:
            # On record with the copy from here on: what it is at, what it
            # used, and where it is taken up again. The copy is marked as
            # one made to carry a commission on in first: written the other
            # way round, a commission could be the copy's on record with
            # nothing to say the copy hands it back when it needs a change.
            await self._ledger.append_event(
                commission_id, "carried_on_in_copy", {"session_id": copy.id, "of": main, "behind": holder}
            )
            await self._ledger.update(commission_id, session_id=copy.id)
        except Exception as exc:  # noqa: BLE001 - it waits for the session itself as before
            logger.warning("carrying commission %s on in a copy of its session failed: %s", commission_id, exc)
            # Back where it was: on record it is still the session itself's.
            await self._back_in_line(commission_id, copy, main)
            return False
        if not self._still_side_question(commission_id):
            # More was said to it while it was put on record, which goes
            # with it and was not what was told about: back to the session
            # itself after all. (Said later than this, before the copy takes
            # it, it is in the copy, which hands it back for a change.)
            try:
                await self._ledger.update(commission_id, session_id=main)
            except Exception:  # noqa: BLE001 - it goes on in the copy (on record as its), which hands it back for a change
                logger.warning("taking commission %s back from its copy failed", commission_id, exc_info=True)
                return True
            # On record as the session itself's from here on: back there,
            # whether or not the note of it can be written.
            try:
                await self._ledger.append_event(commission_id, "not_carried_on", {"copy": copy.id, "of": main})
            except Exception:  # noqa: BLE001 - the note is for the record
                logger.warning("noting that commission %s was taken back from its copy failed", commission_id, exc_info=True)
            await self._back_in_line(commission_id, copy, main)
            return False
        return True

    async def _back_in_line(self, commission_id: str, copy: Session, main: str) -> None:
        """Let go of a commission's place in a copy it is not carried on in after all, and queue it for the session itself again."""
        # Its place and the copy are let go of.
        self._sessions.pop(commission_id, None)
        if self._held_by.get(copy.id) == commission_id:
            del self._held_by[copy.id]
        self._session_of[commission_id] = main
        if commission_id not in self._queue:
            # Back among those that wait for the session itself, in the
            # order the ledger took them (a commission handed back, or
            # queued again after a restart, included), as one handed
            # back is (``_put_back_in_order``); nothing starts in the
            # session meanwhile (``_handing_back_to``). Where that order
            # cannot be read, at the end.
            self._queue.append(commission_id)
            try:
                filed = await self._ledger.filed_order(
                    [item for item in list(self._queue) if self._session_of.get(item) == main]
                )
            except Exception:  # noqa: BLE001 - it waits at the end of the queue, which is all that is lost
                logger.warning("reading in which order commissions were filed failed", exc_info=True)
                filed = {}
            if commission_id in filed:
                self._put_back_in_order(commission_id, main, filed)
        await self._discard_copy(copy)

    def _watch_copy(self, session: _Session, event: dict[str, Any]) -> None:
        """Note, of a copy a commission is carried on in, a call that hands the commission back, and what it writes down after.

        The call itself is answered by what stands in for the tool (it is
        not carried out); before its next request the copy is told to
        write down what it found (``_hand_back_step``).
        """
        from config.nekopower_settings import TASK_AGENT_FORK_HAND_BACK_TOOLS

        kind = event.get("type")
        if kind == "tool_start" and session.hand_back is None:
            name = str(event.get("tool_name") or "")
            if name in TASK_AGENT_FORK_HAND_BACK_TOOLS:
                session.hand_back = name
        elif kind == "text" and session.hand_back_left is not None:
            session.findings.append(str(event.get("delta") or ""))

    async def _hand_back_step(self, session: _Session) -> dict[str, Any] | None:
        """Answer a copy that called a tool which changes something, before its next request.

        First it is told to write down what it found, and has
        ``TASK_AGENT_FORK_HAND_BACK_REQUESTS`` requests for that. When they
        are made and its run goes on, it is handed back with what it wrote
        (``_hand_back``) and the run ends. A copy whose commission ended
        meanwhile (it reported) goes on as any session does.
        """
        from config.nekopower_settings import TASK_AGENT_FORK_HAND_BACK_REQUESTS
        from config.prompts.prompts_nekopower_brain import TASK_AGENT_FORK_HAND_BACK

        if session.handing_back is not None:
            # Handed back already, its close asked again by the host: no
            # more requests of it.
            return {"stop": True}
        if session.hand_back_left is None:
            left = max(1, int(TASK_AGENT_FORK_HAND_BACK_REQUESTS))
            session.hand_back_left = left - 1
            session.findings = []
            try:
                await self._ledger.append_event(session.commission_id, "hand_back_told", {"tool": session.hand_back})
            except Exception:  # noqa: BLE001 - the note is for the record; the copy is told all the same
                logger.warning("noting that a copy is to hand its commission back failed", exc_info=True)
            return {"notice": TASK_AGENT_FORK_HAND_BACK.format(tool=session.hand_back, requests=left)}
        if session.hand_back_left > 0:
            session.hand_back_left -= 1
            return None
        if await self._hand_back_or_later(session):
            return {"stop": True}
        return None

    async def _hand_back(self, session: _Session, *, said: str = "") -> bool:
        """Hand a commission carried on in a copy back to the session the copy was made of. Returns whether it was.

        ``said`` is what the copy's run ended with; else what it wrote
        since it was told to write down what it found. Only a commission
        that is still at work is handed back. The copy is closed; once it
        is, the commission goes on in the session itself (``_handed_back``).
        A close that cannot be asked for raises, the commission marked as
        handed back all the same (``_hand_back_or_later`` tries again).
        """
        if session.handing_back is not None or session.closing or session.carries_on_for is None:
            return False
        commission = await self._ledger.get(session.commission_id)
        if commission is None or commission.status != "working" or session.closing:
            return False
        session.handing_back = (said or "".join(session.findings)).strip()
        try:
            await self._ledger.append_event(
                session.commission_id,
                "handed_back",
                {"tool": session.hand_back, "to": session.carries_on_for, "copy": session.session_id},
            )
        except Exception:  # noqa: BLE001 - the note is for the record; it is handed back all the same
            logger.warning("noting that a copy handed its commission back failed", exc_info=True)
        if self._runtime_of(session) is None:
            # No runtime holds it (it went away): as good as closed, and
            # nothing will say so.
            await self._handed_back(session)
            return True
        await self._close_session(session)
        return True

    async def _hand_back_or_later(self, session: _Session, *, said: str = "") -> bool:
        """Hand a copy's commission back (``_hand_back``); a close that cannot be asked for now is asked again by the host.

        Not by another model request (before which the copy would be
        looked at again): the copy has had what it gets for writing down
        what it found, and once its run is over nothing comes by at all.
        So the close is asked again a few times, a while apart, by a task
        of its own; given up on, the commission fails as one whose run
        failed, and lets its place go. Meanwhile what the copy's runs say
        as they end decides nothing (it is marked as handed back).
        """
        try:
            return await self._hand_back(session, said=said)
        except Exception:  # noqa: BLE001 - asked again by a task of its own
            logger.warning("handing a copy's commission back failed; its close is asked again", exc_info=True)
            if session.handing_back is None:
                # It failed before it was marked (the ledger was not to be
                # read): marked now, and whether it is still at work is
                # looked at once the copy is closed (``_back_in_session``).
                session.handing_back = (said or "".join(session.findings)).strip()
            self._spawn(self._close_copy_later(session))
            return True

    async def _close_copy_later(self, session: _Session) -> None:
        """Ask again to close a copy that hands its commission back (``_hand_back_or_later``)."""
        from config.nekopower_settings import REST_LOOK_RETRY_DELAYS

        commission_id = session.commission_id
        for delay in REST_LOOK_RETRY_DELAYS:
            await asyncio.sleep(delay)
            if self._closed or self._sessions.get(commission_id) is not session or session.closing:
                # Gone, or closing after all: its end takes it from there.
                return
            try:
                await self._close_session(session)
                return
            except Exception:  # noqa: BLE001 - asked again, and given up after the last
                logger.warning("closing a copy to hand its commission back failed again", exc_info=True)
        if self._sessions.get(commission_id) is session and not session.closing and not self._closed:
            session.handing_back = None
            await self._fail(commission_id, "its copy could not be closed to hand it back to its session", said="run")
            self._discard(commission_id)

    async def _handed_back(self, session: _Session) -> None:
        """Go on, in the session itself, with a commission a copy handed back, the copy being closed.

        The copy lets go of its place and its session; the ledger has the
        session itself as the commission's from here on; the commission is
        queued for it (``_wake_working``), with what the copy wrote down,
        framed as no word of the user's, and then what the user said to it
        while it was in the copy (which the session itself never saw).
        """
        commission_id, copy_id, main = session.commission_id, session.session_id, str(session.carries_on_for)
        candidates = self._candidates.get(commission_id)
        self._discard(commission_id)
        if self._held_by.get(copy_id) == commission_id:
            # Let go of here: the commission is not over, and the look at
            # the hold ``_discard`` has made would leave it held.
            del self._held_by[copy_id]
        if candidates is not None:
            self._candidates[commission_id] = candidates
        if self._closed:
            return
        # Until it is back in the queue, in its place, nothing else is
        # started in the session itself (``_session_free_for``): the session
        # may come free meanwhile, and one handed over after this one would
        # be worked on first. Other sessions go on as ever.
        self._handing_back_to[main] = self._handing_back_to.get(main, 0) + 1
        try:
            await self._back_in_session(session, commission_id, copy_id, main)
        finally:
            left = self._handing_back_to[main] - 1
            if left:
                self._handing_back_to[main] = left
            else:
                del self._handing_back_to[main]
            if self._queue and len(self._sessions) < self.limit and not self._closed:
                self._spawn(self._start_next())

    async def _back_in_session(self, session: _Session, commission_id: str, copy_id: str, main: str) -> None:
        """Put a commission a copy handed back on record as the session itself's, and queue it for it (``_handed_back``)."""
        import secrets

        from config.nekopower_settings import REST_LOOK_RETRY_DELAYS, TASK_AGENT_FORK_FINDINGS_MAX_TOKENS
        from config.prompts import prompts_nekopower_brain as texts
        from config.prompts.prompts_nekopower_sessions import frame
        from utils.tokenize import truncate_to_tokens

        try:
            await self._ledger.update_session(copy_id, status="closed")
            stands = await self._looked(lambda: self._ledger.get(commission_id), what="for a commission a copy hands back")
            if stands is None or stands.status != "working":
                # Over meanwhile (stopped, say), or never read before it was
                # handed back: it stays the copy's, and nothing is queued.
                return
            told = await self._told_in_copy(commission_id)
            await self._ledger.update(commission_id, session_id=main)
            # The session itself's here too, now that the ledger has it so.
            self._session_of[commission_id] = main
        except Exception as exc:  # noqa: BLE001 - the commission fails with the reason
            logger.warning("handing commission %s back to its session failed: %s", commission_id, exc)
            await self._fail(commission_id, f"it could not be handed back to its session: {exc}", said="run", detail=str(exc))
            return
        nonce = secrets.token_hex(8)
        message = _Found(
            frame(
                texts.TASK_AGENT_FORK_HANDED_BACK.format(tool=session.hand_back, nonce=nonce),
                truncate_to_tokens(session.handing_back or "", TASK_AGENT_FORK_FINDINGS_MAX_TOKENS)
                or texts.TASK_AGENT_FORK_FOUND_NOTHING,
                nonce,
            )
        )
        # Queued with what it carries, asked again a few times where the
        # ledger cannot be written this moment: left as it is, it would stay
        # at work with nobody at it and in no queue. (One that is on its way
        # back already is not queued a second time: ``_wake_working`` sees
        # to that.)
        for delay in (*REST_LOOK_RETRY_DELAYS, None):
            try:
                woken = await self._wake_working(commission_id, message, reason="handed back by a copy of its session")
                break
            except Exception as exc:  # noqa: BLE001 - asked again; the last failure ends the commission
                if delay is None:
                    logger.warning("queueing commission %s for its session again failed: %s", commission_id, exc)
                    await self._fail(commission_id, f"it could not be handed back to its session: {exc}", said="run", detail=str(exc))
                    return
                await asyncio.sleep(delay)
        if not woken:
            return
        self._notes.setdefault(commission_id, []).extend(told)
        # The session itself is told, should it be at work, that this waits
        # for it (``_tell_of_copies``).
        self._handed_back_news.setdefault(main, []).append((commission_id, copy_id))
        self._news_of_copies(main)
        # In its place among those that wait for the session itself, by
        # when each was filed: read now that it is queued, with nothing
        # started in the session meanwhile (``_handing_back_to``), so that
        # every one queued so far is among them. One that is queued while
        # this is read counts as later.
        try:
            filed = await self._ledger.filed_order(
                [item for item in list(self._queue) if self._session_of.get(item) == main]
            )
        except Exception:  # noqa: BLE001 - it waits at the end of the queue, which is all that is lost
            logger.warning("reading in which order commissions were filed failed", exc_info=True)
            return
        if commission_id in filed:
            self._put_back_in_order(commission_id, main, filed)

    async def _told_in_copy(self, commission_id: str, *, before: int | None = None) -> list[str]:
        """Return what the user said to a commission a copy hands back, and answered it there, in the order it was said.

        Everything the user said to it so far went to the copy: what came
        while it was queued went with the work it was given there. (It is
        handed back once: from then on it is the session's own.) Read a
        page at a time, each asked again a few times where the ledger
        cannot be read this moment, before the commission is moved.
        """
        told: list[str] = []
        after = 0
        while True:
            page = await self._looked(
                lambda: self._ledger.events(commission_id, after=after), what="for what was said to a copy"
            )
            told.extend(
                str(event.payload.get("text") or "")
                for event in page
                if event.type in ("told", "answered")
                and str(event.payload.get("text") or "").strip()
                and (before is None or event.seq < before)
            )
            if len(page) < 500 or (before is not None and page[-1].seq >= before):
                return told
            after = page[-1].seq

    def _put_back_in_order(self, commission_id: str, main: str, filed: dict[str, int]) -> None:
        """Put a commission handed back by a copy among those that wait for the session itself, in the order they were handed over.

        Queued again, it went to the end. Handed over before others that
        wait for the session, it is to be worked on before them: a later
        one may build on what this one changes. It goes in front of the
        first that was filed after it (in the order the ledger took them,
        ``filed``, which no two share), or that came to wait since that was
        read (which is later). Commissions of other
        sessions keep their places. Nothing is awaited here: whoever
        starts the next one finds it in its place.
        """
        if commission_id not in self._queue:
            return
        self._queue.remove(commission_id)
        at = len(self._queue)
        for index, item in enumerate(self._queue):
            if self._session_of.get(item) != main:
                continue
            known = filed.get(item)
            if known is None or known > filed[commission_id]:
                at = index
                break
        self._queue.insert(at, commission_id)

    # -- copying a session ------------------------------------------------

    async def fork_session(self, source_session_id: str, *, name: str | None = None, upto: str | None = None) -> Session:
        """Make a session from another by copying what was said in it, and return it. Raises ``ForkRefused``.

        The new session (kind ``fork``) holds the other's conversation up
        to one entry of its record (``upto``; all of it when none is
        named), cut back to the last place where it is whole: the other
        may be at work, and is not disturbed. It is opened with the system
        prompt and the tool definitions the other was opened with, so a
        provider that kept the head of the other's requests has the same
        head for this one's. See ``nekopower/runtime/fork.py``.

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
        registering: asyncio.Task[Session] | None = None
        try:
            registering = await self._fork(source_session_id, name=name, upto=upto)
        finally:
            if registering is None:
                # Not written, or no longer waited for.
                self._copied_from(source_session_id)
        # The copy is written. Putting it on record is a task of its own,
        # which lets go of the session it was made from when it is through.
        # Whoever asked may be cancelled meanwhile, and hears of that at
        # once; the record of that session is still not removed under a
        # copy that is on no record yet.
        return await asyncio.shield(registering)

    def _copied_from(self, source_session_id: str) -> None:
        """Let go of a session a copy was being made from (of this copy's share in it, if there are several)."""
        left = self._copying_from[source_session_id] - 1
        if left > 0:
            self._copying_from[source_session_id] = left
        else:
            del self._copying_from[source_session_id]

    async def _fork(self, source_session_id: str, *, name: str | None, upto: str | None) -> "asyncio.Task[Session]":
        """Have the runtime write the copy ``fork_session`` returns; returns the task that puts it on record.

        The session it is made from is held by the caller until this
        returns or raises, and by that task from then on.
        """
        from config.nekopower_settings import TASK_AGENT_FORK_SECONDS

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
        if self._closed:
            # Shut down while the session's files were looked at. Shutting
            # down did not see this (it was neither started nor waited
            # for yet), and is over or all but: no runtime is made for
            # it now, and nothing is copied.
            raise ForkRefused("the host is shutting down")
        session_id = new_session_id()
        target_dir = self._paths.session_dir(session_id, source.project)
        runtime = self._runtimes.builtin()
        if self._pump is None:
            self._pump = asyncio.ensure_future(self._pump_events())
        answer: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._forks[session_id] = answer
        self._fork_dirs[session_id] = str(target_dir)
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
            self._fork_given_up(session_id, target_dir, answer)
            raise ForkRefused("the runtime did not answer in time") from None
        except asyncio.CancelledError:
            # Whoever asked for the copy is gone, and the runtime may write
            # it all the same: likewise removed when it says it has.
            self._fork_given_up(session_id, target_dir, answer)
            raise
        finally:
            self._forks.pop(session_id, None)
            self._fork_dirs.pop(session_id, None)
        if event.get("type") != "forked":
            # It may have written a part of it and not got to remove that
            # (a snapshot something had open): the directory was set aside
            # for this copy, and is on no record. Removed by a task of
            # its own: whoever asked may be cancelled while it is, and
            # what cannot be removed yet would be nobody's to remove.
            await asyncio.shield(self._remove_fork_dir_soon(session_id, target_dir))
            raise ForkRefused(str(event.get("message") or "the runtime could not copy the session"))
        # With no await since the runtime's word was taken: nothing comes
        # between the copy being written and its being put on record.
        # A task that shutting down waits for and does not cancel: cut
        # off before the ledger got to it, the copy would be on the disk
        # and on no record, with nothing left that knows of it.
        registering = asyncio.ensure_future(
            self._put_fork_on_record(
                source, session_id, target_dir, name=name, forked_at=str(event.get("upto") or "") or None
            )
        )
        self._fork_records.add(registering)
        registering.add_done_callback(self._fork_records.discard)
        # What it fails with is read here, should whoever asked have left.
        registering.add_done_callback(lambda done: done.cancelled() or done.exception())
        return registering

    def _fork_given_up(self, session_id: str, target_dir: Any, answer: "asyncio.Future[dict[str, Any]]") -> None:
        """Note that a copy is no longer waited for: what the runtime writes of it is removed when it says it is through.

        It may have said so already, in the moment whoever waited left
        (the runtime in this process says it before its command
        returns). Nothing more is said of the copy then, so what there
        is of it is removed now, by a task of its own.
        """
        if answer.done() and not answer.cancelled() and answer.exception() is None:
            self._remove_fork_dir_soon(session_id, target_dir)
            return
        self._forks_given_up[session_id] = str(target_dir)

    def _remove_fork_dir_soon(self, session_id: str, target_dir: Any) -> "asyncio.Future[None]":
        """Begin to remove what was written of a copy that is on no record, in a task of its own.

        Kept with the copies that are being put on record: shutting
        down waits for these and does not cut them off (``shutdown``,
        ``forks_settled``).
        """
        removing = asyncio.ensure_future(self._remove_fork_dir(session_id, target_dir))
        self._fork_records.add(removing)
        removing.add_done_callback(self._fork_records.discard)

        def seen(removing: "asyncio.Future[None]") -> None:
            # Mostly nobody waits for it: what it ended with would go by unseen.
            if not removing.cancelled() and removing.exception() is not None:
                logger.error(
                    "removing what was copied to %s ended with an error; it may be left there, on no record",
                    target_dir,
                    exc_info=removing.exception(),
                )

        removing.add_done_callback(seen)
        return removing

    async def _put_fork_on_record(
        self, source: Session, session_id: str, target_dir: Any, *, name: str | None, forked_at: str | None
    ) -> Session:
        """Put a copy the runtime has written on record, and let go of the session it was made from.

        All of it, whatever becomes of whoever asked for the copy: the
        copy is written, and without its row nothing would find it;
        when the row cannot be written, what was written is removed
        (the runtime is through with it: it said so); and only then is
        the session it was made from let go of.
        """
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
                forked_at=forked_at,
                status="closed",
                # Sent where the session it was copied from is, or what a
                # provider kept of the conversation is not read.
                model_slot=source.model_slot,
                model_name=source.model_name,
                model_ident=source.model_ident,
                model_free=source.model_free,
                model_fallback=source.model_fallback,
                model_tier=source.model_tier,
            )
        except Exception as exc:  # noqa: BLE001 - told as a copy that was not made
            # Written, and not on record: nothing would ever come by it to remove it.
            logger.warning("a copied session could not be put on record; what was copied is removed", exc_info=True)
            await self._remove_fork_dir(session_id, target_dir)
            raise ForkRefused(f"the copy could not be put on record: {exc}") from exc
        finally:
            self._copied_from(source.id)

    async def _remove_fork_dir(self, session_id: str, target_dir: Any) -> None:
        """Remove the directory that was set aside for a copy that came to nothing; it is on no record.

        One that cannot be removed (something has it open, say) is
        still this host's to remove: with the copies nobody waited for
        any more, when it shuts down. Should the host be shutting down
        already, that may be over, and this tries again itself, a few
        times.
        """
        import os
        import shutil

        def removed() -> bool:
            shutil.rmtree(target_dir, True)
            return not os.path.lexists(str(target_dir))

        if await asyncio.to_thread(removed):
            return
        if not self._closed:
            logger.error("what was copied to %s could not be removed; tried again when the host shuts down", target_dir)
            self._forks_given_up[session_id] = str(target_dir)
            return
        for _attempt in range(_FORK_REMOVE_RETRIES):
            await asyncio.sleep(_FORK_REMOVE_RETRY_SECONDS)
            if await asyncio.to_thread(removed):
                return
        logger.error("what was copied to %s could not be removed; it is left there, on no record", target_dir)

    # -- summing up idle sessions -------------------------------------------

    def _drop_idle_timer(self, session_id: str) -> None:
        timer = self._idle_timers.pop(session_id, None)
        if timer is not None:
            timer.cancel()

    async def _idle_model(self, session_id: str) -> dict[str, Any] | None:
        """Return what a session would be opened with now (``session_model``, looked at only); None when it has no model.

        One that is not on record: what a new session would be. (Nothing
        is summed up for it: it is looked up when the time comes.)
        """
        kept = await self._ledger.get_session(session_id)
        choice = await self.session_model(kept, record=False) if kept is not None else await self._new_session_model()
        config = choice.config
        if kept is not None and kept.model_ident and choice.ident != kept.model_ident:
            # Not where it last ran: nothing of it goes there until the
            # user goes on with it.
            return None
        return config if config and config.get("model") else None

    async def _idle_after(self, session_id: str) -> float:
        """Return how long a session is idle before it is summed up, in seconds.

        What the user set for the model the session is sent to, else the
        setting (``ModelCapabilityStore.idle_compact_after``).
        """
        from config.nekopower_settings import TASK_AGENT_IDLE_COMPACT_AFTER_SECONDS

        if self._model_capabilities is not None:
            try:
                config = await self._idle_model(session_id)
                if config is not None:
                    return float(
                        await self._model_capabilities.idle_compact_after(config.get("base_url"), config.get("model"))
                    )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - not knowing is the same as nothing set
                logger.warning("looking up how long a session is idle before it is summed up failed", exc_info=True)
        return float(TASK_AGENT_IDLE_COMPACT_AFTER_SECONDS)

    async def _idle_compact_wanted(self, session_id: str) -> bool:
        """Return whether the user has idle sessions of the model a session is sent to summed up (``idle_compact_on``).

        True when it cannot be told: it is on unless switched off, and what
        decides which model a session is sent to is checked apart from this
        (``_task_model_key``).
        """
        if self._model_capabilities is None:
            return True
        try:
            config = await self._idle_model(session_id)
            if config is not None:
                return await self._model_capabilities.idle_compact_on(config.get("base_url"), config.get("model"))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - not knowing is the same as nothing set
            logger.warning("looking up whether idle sessions are summed up failed", exc_info=True)
        return True

    @staticmethod
    def _model_key_of(config: dict[str, Any] | None) -> str | None:
        """Return the key (address and name) of the model a configuration names; None when it names none."""
        from nekopower.host.model_capabilities import model_key

        if not config or not config.get("model"):
            return None
        return model_key(config.get("base_url"), config.get("model"))

    async def _task_model_key(self, session_id: str) -> str | None:
        """Return the key of the model a session is sent to as things are now; None when that cannot be told."""
        try:
            return self._model_key_of(await self._idle_model(session_id))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - not knowing is the same as no model
            logger.warning("looking up the model of a task agent session failed", exc_info=True)
            return None

    async def _schedule_idle_compact(
        self,
        session_id: str,
        *,
        after: float | None = None,
        idle_for: float | None = None,
        model: str | None = None,
    ) -> None:
        """Have a session summed up once nothing has been at work in it for the time the user set (``_idle_after``).

        Only while that is switched on (``TASK_AGENT_IDLE_COMPACT_ENABLED``).
        Whether it is worth it for this session is looked at when the
        time has come. A commission that takes the session before then
        drops the timer. ``idle_for`` is how long the session has to have
        been idle, read now when it is not given: the timer keeps it, and
        looks at the session by it when it comes due, whatever the user sets
        meanwhile (the next session that is let go gets the new time).
        ``model`` is the key of the model the session is sent to as the wait
        is set (``session_model``; read now when ``idle_for`` is): the timer
        keeps it, and nothing is summed up when the session is sent to
        another by the time it comes due (the
        user moved to another provider: what was said in the session is not
        sent there without the user going on with it). In memory only.
        ``after`` is how long from now, when that is not the whole time (the
        time had come by the timer, and not yet by what the ledger has of the
        session: ``_idle_compact``).
        """
        from config.nekopower_settings import TASK_AGENT_IDLE_COMPACT_ENABLED

        self._drop_idle_timer(session_id)
        if not TASK_AGENT_IDLE_COMPACT_ENABLED or self._closed:
            return
        if session_id in self._idle_stopped:
            # What was at work in it was stopped with everything else, and
            # nothing new has been asked for in it since: the host makes
            # no request for it by itself (``drop_idle_work``).
            return
        stops = self._idle_stops
        if idle_for is None:
            if not await self._idle_compact_wanted(session_id):
                # Switched off for this model by the user: no timer is set.
                return
            idle_for = await self._idle_after(session_id)
            model = await self._task_model_key(session_id)
            if model is None:
                # No model is configured, or it cannot be read: which provider the
                # wait is set under cannot be told, and a timer that checked nothing
                # would send the session to whichever one the user sets next.
                return
            # Looked at again after the wait: the session may have been taken
            # by a commission meanwhile (that drops its timer, and sets
            # another when it is over), everything stopped, or the host
            # shut down. And another look at the same session may have set
            # a timer in the meantime: it is replaced.
            if (
                self._closed
                or stops != self._idle_stops
                or session_id in self._idle_stopped
                or not self._idle_now(session_id)
            ):
                return
            self._drop_idle_timer(session_id)
        if after is None:
            after = idle_for

        def due() -> None:
            self._idle_timers.pop(session_id, None)
            if not self._closed:
                self._spawn(self._idle_compact(session_id, stops, idle_for, model))

        self._idle_timers[session_id] = asyncio.get_running_loop().call_later(max(0.0, float(after)), due)

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

    async def _stop_compacting(self, session_id: str) -> bool:
        """Cut short the summing up of an idle session, and wait until the runtime has let go of the session.

        Returns whether the session is free of it. The runtime gets
        ``_SERVICE_HANDOVER_SECONDS`` to say that it closed the session.
        When it does not, the session stays the host's (what the runtime
        says of that opening later is still taken there, and the
        session is given back then), and whoever asked goes without it.
        """
        task = self._compacting.get(session_id)
        if task is None or task is asyncio.current_task():
            return True
        self._cut_short(task)
        loop = asyncio.get_running_loop()
        until = loop.time() + _SERVICE_HANDOVER_SECONDS
        await asyncio.wait([task], timeout=_SERVICE_HANDOVER_SECONDS)
        over = self._idle_over.get(session_id)
        if over is not None:
            # The task left while the session was being given back.
            await asyncio.wait([over], timeout=max(0.0, until - loop.time()))
        return self._compacting.get(session_id) is not task

    def _idle_compact_allowed(self, kept: Session | None, now: float, after: float) -> bool:
        """Whether a session, as the ledger has it, is to be summed up while idle: by its kind, its length and its failures.

        ``after`` is how long it has to have been idle (``_idle_after``).
        That the session has not ended (``Ledger.session_alive``) is asked
        by whoever calls this: that is read from the ledger.
        """
        from config.nekopower_settings import TASK_AGENT_IDLE_COMPACT_MAX_FAILURES, TASK_AGENT_IDLE_COMPACT_MIN_TOKENS

        if kept is None or kept.status != "closed" or kept.kind not in _IDLE_COMPACT_KINDS:
            return False
        if kept.runtime != RUNTIME_BUILTIN or kept.context_tokens < TASK_AGENT_IDLE_COMPACT_MIN_TOKENS:
            # dsh sums up by itself; a short conversation costs more to sum up than it saves.
            return False
        if now - kept.last_active_at < after:
            return False
        # Summings up that got no whole summary, one after the other: no more
        # by itself once it was tried that often, until a summary is made
        # in the course of a commission.
        return self._idle_compact_failures.get(kept.id, 0) < TASK_AGENT_IDLE_COMPACT_MAX_FAILURES

    def _is_switched_on(self) -> bool:
        """Whether NekoPower is switched on, as far as this was told how to find out; a switch that cannot be read is off."""
        if self._switched_on is None:
            return True
        try:
            return bool(self._switched_on())
        except Exception:  # noqa: BLE001 - an unreadable switch counts as off
            return False

    def _idle_now(self, session_id: str) -> bool:
        """Whether nothing is at work in a session, or on its way to be, and nothing else has claimed it."""
        return not self._closed and not self._in_use(session_id)

    async def _idle_compact(
        self, session_id: str, stops: int | None = None, after: float | None = None, model: str | None = None
    ) -> bool:
        """Sum up the conversation of a session nothing has been at work in for long. Returns whether it was summed up.

        ``after`` is how long the session has to have been idle: the time
        its timer was set for (``_schedule_idle_compact``), so that what the
        user sets while a session waits does not change that wait; read now
        when it is not given. ``model`` is the key of the model the session
        was sent to when the wait was set: with it sent to another now, nothing is
        asked of it (no failure is counted and no wait is set again), so that
        what was said in the session does not go to a provider the user
        moved to meanwhile without the user going on with it.

        One model request, of the length of the conversation at the most,
        made by the host itself: for no commission, so it is counted with
        the session alone, against the daily quota of the agent model as
        one request, and against no commission's limit. Not made when
        NekoPower is switched off, when the quota is used up, or when the
        session, by the time this gets to it, is in use, has ended
        (``Ledger.session_alive``) or is not worth it
        (``_idle_compact_allowed``).

        The session is opened in the built-in runtime as a commission's
        would be (the system prompt and the tools it was opened with
        before, so what a provider kept of its requests is used), told to
        sum itself up, and closed. A commission that is to be worked on in
        it meanwhile cuts this short (``_start``) and waits only for the
        runtime to let go: nothing of the conversation changes unless the
        summary was written.

        There is no limit on how often or how many: what bounds it is that
        a timer is only set once a commission has ended in the session,
        that a session summed up holds nothing worth summing again until a
        request says how long it has grown (``Ledger.end_compacting``), and
        that one that got no whole summary (the request failed, came back
        empty or cut off by its length, or the model went for a tool) is
        not tried again by itself: the next time is when another
        commission has ended in it, and after ``TASK_AGENT_IDLE_COMPACT_MAX_FAILURES``
        such in a row not until it made a summary in the course of one.
        """
        from config.nekopower_settings import TASK_AGENT_IDLE_COMPACT_ENABLED, TASK_AGENT_IDLE_COMPACT_SECONDS
        from nekopower.runtime.protocol import GatewaySpec

        if not TASK_AGENT_IDLE_COMPACT_ENABLED or not self._idle_now(session_id):
            return False
        if stops is not None and stops != self._idle_stops:
            # Everything was stopped between the timer and this.
            return False
        if not self._is_switched_on():
            return False
        if not await self._idle_compact_wanted(session_id):
            # Switched off since the timer was set: it comes due and does nothing.
            return False
        if not self._idle_now(session_id) or (stops is not None and stops != self._idle_stops):
            # Taken by a commission, or everything stopped, while that was looked up.
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
        marked = opened = compacted = asked = cut_short = False
        # Set when the timer brought this here a moment early: how long to wait still.
        too_early: float | None = None
        try:
            if after is None:
                after = await self._idle_after(session_id)
            kept = await self._ledger.get_session(session_id)
            choice = await self.session_model(kept, record=False) if kept is not None else None
            config = choice.config if choice is not None else None
            if model is not None and self._model_key_of(config) != model:
                # The session's own model: where it is sent now is another
                # than where it was when the wait was set (the user moved
                # it, or what its slot follows, to another provider).
                logger.info("the model of session %s changed while it waited; it is not summed up", session_id)
                return False
            if (
                self._model_capabilities is not None
                and config
                and config.get("model")
                and not await self._model_capabilities.idle_compact_on(config.get("base_url"), config.get("model"))
            ):
                # Asked of the model it would go to, the one compared above.
                return False
            now = self._ledger._clock()
            kept = await self._ledger.get_session(session_id)
            # A session that has ended is not summed up, whatever else is so.
            alive = await self._ledger.session_alive(kept, now)
            if not alive or not self._idle_compact_allowed(kept, now, after):
                if alive and stops is not None and kept is not None:
                    # A timer set when the session was let go runs by the
                    # loop's clock; how long the session has been idle is
                    # told by what the ledger wrote down, by the clock on
                    # the wall, and that may have been written a moment
                    # after the timer was set. When nothing but that
                    # stands in the way, it is looked at again once the
                    # time is up by the ledger, and not given up.
                    # Asked as of when it is looked at again, the margin
                    # included: as of ``up`` itself, ``up - last_active_at``
                    # comes out a hair short of the idle time in floats
                    # (timestamps of the wall clock are large), and the
                    # session would never be found idle for long enough.
                    # For the same reason a clock that reads ``up`` exactly
                    # is still early here.
                    up = kept.last_active_at + after
                    if up >= now and self._idle_compact_allowed(kept, up + _IDLE_TIMER_MARGIN_SECONDS, after):
                        too_early = up - now
                return False
            last = await self._ledger.select(session_id=session_id, limit=1)
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
                # Make gateway tools available for the summary request. The next
                # real commission supplies its own workspace rules when it opens.
                url = await self._gateway.start()
                gateway_spec = GatewaySpec(
                    url=url,
                    token=self._gateway.issue(grant, candidate_plugin_ids=(), supports_images=model.supports_images),
                )
            spec = await self._spec(
                commission, kept, model, workdir=workdir, session_dir=session_path, gateway=gateway_spec,
                idle_compaction=True,
            )
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
                        if not self._is_switched_on():
                            # Switched off while the session was being
                            # opened: the host asks nothing of a model
                            # by itself, and nothing is charged.
                            return
                        # Charged here, when the request is about to be
                        # made, and not before the session was marked
                        # and opened: nothing is charged for a summing
                        # up that never got as far as asking.
                        if not await self._charge(choice is not None and choice.free, commission.id):
                            # The daily quota is used up: the host makes no request of its own.
                            return
                        if not self._is_switched_on():
                            # Switched off while the quota was looked at,
                            # which waits for something: nothing is asked
                            # of a model, though the request was paid for.
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
            # Cut short by a commission, a stop of everything, or the host
            # shutting down: that no summary was made is no failure of it.
            cut_short = True
            raise
        except Exception:  # noqa: BLE001 - the session is as it was; the next commission goes on from that
            logger.warning("summing up idle session %s failed", session_id, exc_info=True)
            return False
        finally:
            # Whatever became of it (also when a commission cut it short):
            # the runtime lets go of the session before anyone else has
            # it. In a task of its own, waited for here: cancelled while
            # it waits (this is where a stop or a commission finds a
            # summing up that is all but over), this leaves, and the
            # giving back is carried through all the same.
            # (Not a task shutting down gives up on, ``_spawn``: cut off
            # there, the gateway would keep its token and the ledger
            # would not be told that the session was summed up.)
            over = asyncio.ensure_future(
                self._idle_compact_over(
                    session_id, task, runtime=runtime, events=events, opened=opened, grant=grant,
                    marked=marked, compacted=compacted, too_early=too_early, stops=stops,
                    asked=asked, cut_short=cut_short, idle_for=after, model=model,
                )
            )
            self._idle_over[session_id] = over
            await asyncio.shield(over)

    def _note_idle_compact_result(self, session_id: str, *, compacted: bool, asked: bool, cut_short: bool) -> None:
        """Count a summing up of an idle session that was asked for and got no whole summary; a summary made ends the count.

        What counts as failed is that the host told the session to sum
        itself up, and no summary came of it: the request failed, came back
        empty or cut off by its length, the model went for a tool instead,
        or it took too long. Not that it was not asked (the quota was used
        up, NekoPower was switched off, a commission took the session), nor
        that it was cut short. A session is not tried again by itself after
        a failure, until another commission has ended in it
        (``_schedule_idle_compact`` is only called then), and, after
        ``TASK_AGENT_IDLE_COMPACT_MAX_FAILURES`` in a row, not until a
        summary is made in the course of one (``_handle``).
        """
        if compacted:
            self._idle_compact_failures.pop(session_id, None)
        elif asked and not cut_short:
            self._idle_compact_failures[session_id] = self._idle_compact_failures.get(session_id, 0) + 1

    async def _length_is_stale(self, session_id: str) -> None:
        """Have whatever was said of a session's length so far count as older than what is said from now on.

        For a session that was summed up (``Ledger.end_compacting`` writes
        its length as unknown): what a request made before the summary said
        of it, and that is owed because the ledger could not take it then,
        would be written afterwards as the newest and put the old length
        back. The usage owed stays owed; only its length no longer counts.
        """
        async with self._usage_locks.setdefault(session_id, asyncio.Lock()):
            self._context_written[session_id] = max(self._context_said, self._context_written.get(session_id, 0))

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
        asked: bool = False,
        cut_short: bool = False,
        idle_for: float | None = None,
        model: str | None = None,
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
            self._note_idle_compact_result(session_id, compacted=compacted, asked=asked, cut_short=cut_short)
            if self._gateway is not None:
                self._gateway.revoke(grant)
            if marked and compacted:
                await self._length_is_stale(session_id)
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
            # What the request that summed it up used, should the ledger not have taken it.
            self._settle_usage_of(session_id)
            if too_early is not None and stops == self._idle_stops and self._idle_now(session_id):
                # A little more than what is left, so that it is not a
                # moment early once more.
                await self._schedule_idle_compact(
                    session_id, after=too_early + _IDLE_TIMER_MARGIN_SECONDS, idle_for=idle_for, model=model
                )

    async def _end_service(
        self, session_id: str, runtime: Any, events: "asyncio.Queue[dict[str, Any]]", opened: bool
    ) -> bool:
        """Close a session the host opened by itself and wait until the runtime says it is gone.

        Returns whether the runtime said meanwhile that the conversation was summed up.
        """
        from nekopower.runtime.protocol import PROCESS_EXITED_ERROR

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

            # However long it takes, while the host is up. Given back
            # before the runtime said so, the session could be taken by
            # a commission, and what the runtime says of this opening of
            # it afterwards (that it is closed) would be taken for that
            # commission's. (A commission that is to be worked on in it
            # does not wait as long: ``_stop_compacting``.) A host that
            # shuts down stops its runtimes next and gives the session
            # to nobody: it waits as long as it waits for anything then,
            # and what is left to do for the session is done all the same.
            waiting = asyncio.ensure_future(gone())
            try:
                loop = asyncio.get_running_loop()
                asked = loop.time()
                noted = False
                done: set[Any] = set()
                while not done:
                    waited = loop.time() - asked
                    if self._closed and waited >= _SHUTDOWN_START_WAIT_SECONDS:
                        break
                    if not noted and waited >= _SERVICE_CLOSE_SECONDS:
                        noted = True
                        logger.warning(
                            "the runtime has not said that session %s is closed; still waited for", session_id
                        )
                    done, _pending = await asyncio.wait({waiting}, timeout=_SERVICE_CLOSE_POLL_SECONDS)
                if done:
                    # What the waiting failed with, if it did, is raised here.
                    waiting.result()
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
        from config.nekopower_settings import TURNS_OWED_CLOSE_WAIT_SECONDS

        self._closed = True
        for session_id in list(self._idle_timers):
            self._drop_idle_timer(session_id)
        for session_id, waiting in list(self._forks.items()):
            if not waiting.done():
                # The runtime may write the copy all the same. Removed when
                # it says it has, like the others nobody waits for any
                # more, or below once the runtimes are stopped.
                self._forks_given_up[session_id] = self._fork_dirs[session_id]
                waiting.set_exception(ForkRefused("the host is shutting down"))
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
            if pending:
                # A cancelled start may still be settling a SQLite transaction.
                # _starting owns it until completion, even if the grace period expires.
                await asyncio.wait(pending, timeout=_SHUTDOWN_START_WAIT_SECONDS)
        if self._fork_records:
            # Copies that are being put on record: waited for as long as
            # a start is, and left to go on after that. A ledger that is
            # closed before it got to one has it fail, and what was
            # written of that copy is removed.
            await asyncio.wait(list(self._fork_records), timeout=_SHUTDOWN_START_WAIT_SECONDS)
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
        # What the runtimes said as they stopped is handled before the
        # sessions are let go and nobody listens any more, a bounded while:
        # the ends of the turns that were under way are among it (let go
        # first, the end of a turn would find no session, and the senders
        # of the notes it took no word), and what the last requests used,
        # which would be on no account.
        await self._caught_up(within=max(_SHUTDOWN_START_WAIT_SECONDS, TURNS_OWED_CLOSE_WAIT_SECONDS))
        # Copies nobody waited for any more, of which no runtime said what
        # became of them before it stopped: they are on no record, and
        # nothing would ever come by them to remove them. No runtime
        # writes them from here on. (One that is on record stays: it was
        # waited for, and is not among these.) Each is seen to be gone,
        # and tried again a few times when it is not: nothing comes by
        # after this (``_remove_fork_dir``).
        given_up, self._forks_given_up = self._forks_given_up, {}
        if given_up:
            await asyncio.gather(
                *(self._remove_fork_dir(session_id, target) for session_id, target in given_up.items())
            )
        if self._idle_over:
            # Sessions the host had open by itself that are being given
            # back: each waits for its runtime no longer than this, and
            # then does what is left (the gateway's token taken back, the
            # ledger told). Waited for, so that the ledger is still open.
            await asyncio.wait(
                list(self._idle_over.values()),
                timeout=_SHUTDOWN_START_WAIT_SECONDS + _SERVICE_CLOSE_POLL_SECONDS,
            )
        pump, self._pump = self._pump, None
        if pump is not None:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
        # Before the sessions are let go: what they took and nobody was
        # told of is with them.
        self._owe_what_is_left()
        for commission_id in list(self._sessions):
            self._discard(commission_id)
        # Receipts and file cleanup share a grace period. A stalled worker keeps
        # its record or file claim while the rest of shutdown can proceed.
        settling = set(self._control_settlements)
        if self._read_corrections:
            async def correct_reads() -> None:
                # One SQLite writer can drain these without spawning a task per row.
                for correction in list(self._read_corrections.values()):
                    await self._correct_undelivered_read(*correction)
            settling.add(self._spawn(correct_reads()))
        self._held_by.clear()
        unused_copies, self._unused_ask_copies = self._unused_ask_copies, {}
        for copy in unused_copies.values():
            settling.add(self._spawn(recorded("unused question copy " + copy.id, lambda copy=copy: self._discard_copy(copy))))
        if settling:
            await asyncio.wait(settling, timeout=_SHUTDOWN_START_WAIT_SECONDS)
        self._unsubscribe()
        # And once more, now that no run ends any more: the sessions that
        # were closed above ended their turns meanwhile, and one whose word
        # to the senders of its notes could not be written then is owed
        # since. (Nothing else writes it: the loop that tries again ended
        # with the host.) The ledger is closed after this.
        await self._settle_at_close(TURNS_OWED_CLOSE_WAIT_SECONDS)
        # Likewise what requests used that the ledger did not take.
        await self._settle_usage()

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
                receipt = _refused_answer(session, event)
                if receipt is not None:
                    self._settle_call_soon(receipt[0], "failed")
                _refusal(session, event)
            elif event.get("type") == "taken" and number is not None:
                receipt = session.answer_receipts.pop(number, None)
                if receipt is not None:
                    self._settle_call_soon(receipt[0], "completed")
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
            elif event.get("type") == "tool_end":
                self._finish_control_event(session, event)
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

    async def forks_settled(self) -> None:
        """Wait for what is still being done about copies once the ledger is closed, for no longer than that takes.

        A copy whose record was being written when the host shut down
        is left to go on (``shutdown``). With the ledger closed its
        record fails, and what was written of it is removed, tried
        again a few times (``_remove_fork_dir``). The process may end
        right after the host has shut down, and would cut that off:
        so whoever closes the ledger waits here, for as long as a
        start gets and the tries take.
        """
        if self._fork_records:
            await asyncio.wait(
                list(self._fork_records),
                timeout=_SHUTDOWN_START_WAIT_SECONDS + _FORK_REMOVE_RETRIES * _FORK_REMOVE_RETRY_SECONDS,
            )
