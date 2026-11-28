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

"""Shared constants, data types, helpers, and package logger for ``nekopower.host.task_agents``.

Re-exported from ``nekopower.host.task_agents.__init__`` for backwards
compatibility.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from nekopower.host import call_audit
from nekopower.host.ledger import ACTIVE_STATUSES, Commission, Ledger
from nekopower.host.paths import DSH_SESSION_NAME, TRANSCRIPT_NAME
from nekopower.host.permissions import DENIED_BY_SETTINGS
from nekopower.host.runtimes import RUNTIME_BUILTIN, RUNTIME_DSH

if TYPE_CHECKING:
    from nekopower.host.task_agents.manager import TaskAgentManager

logger = logging.getLogger("nekopower.host.task_agents")

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
# The task agents a stopped commission started that ``abort`` stops with it:
# those under way, and those stopped already, whose closing may have failed.
# Not those that ended otherwise: their sessions are theirs to keep.
_STOPPABLE_CHILD_STATUSES = ACTIVE_STATUSES | {"cancelled"}
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
# The tools a session changes files with: one opened without all of them changes none
# (``Template.changes_files`` says the same of a preset). What goes through the gateway
# (``capability_call``) is not among them: see ``_note_writer``.
_CHANGING_TOOLS = frozenset({"write", "edit", "bash", "pwsh"})
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
class _Beside:
    """A queued commission the user is asked about: whether another member is to do it beside the busy one it waits for (``_beside``)."""

    # The session it waits for.
    main: str
    # What the user says: resolved by the answer (``answer``), or by taking back what was
    # asked (``_withdraw_beside``). Nobody waits for it where nobody is asked.
    answer: "asyncio.Future[dict[str, Any]]"
    # Why what was asked was taken back, when it was: ``free`` (the session came free: it starts
    # there as it would have), ``stopped`` (the commission was), ``closed`` (the host shuts down).
    withdrawn: str = ""
    # Nothing starts in the session until this is seen to (``_handing_back_to``): it came free
    # meanwhile.
    held: bool = False


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
    # start a run in a session that is about to be closed. Counted: a
    # landing that puts the commission down as done keeps it back the same
    # way (``tree_work.py``), and may do so while the end of the run before
    # is still being looked at.
    settling: int = 0
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
    # Then its preset, whether it was made for this commission, and whether
    # it changes files.
    handing_over: tuple[str, str, str, str, bool, bool] | None = None
    # Whether the session was opened, for this commission, with a tool that
    # changes files; None until it is opened. What its preset says by now
    # may differ (a template changed since): this is what the run has.
    changes_files: bool | None = None


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


def answer_words(answer: dict[str, Any]) -> str:
    """Return what the user answered a question with, as one line for the record: what was picked, then the words."""
    picked = answer.get("selected")
    labels = [
        label
        for group in (picked.values() if isinstance(picked, dict) else ())
        if isinstance(group, list)
        for label in group
        if isinstance(label, str) and label
    ]
    said = str(answer.get("text") or "").strip()
    return "; ".join([*labels, *([said] if said else [])])


def build_answers(questions: list[Any], text: str, selected: Any = None) -> dict[str, Any]:
    """Turn the user's reply into the answer shape ``ask_user_question`` expects.

    Every question gets the reply as its free-text answer; an option whose
    label occurs in the reply is selected as well.

    ``selected`` is what the user picked with a button, where the answer
    came from a page that shows the options: for each question, by where it
    stands among the questions ("0", "1", ...; the ids are the model's own
    and need not differ), the labels picked. Each question then gets the
    ones named for it that are options of that question, in the order the
    question has them, and none is looked for in the reply: the words are
    what the user added, and may hold the label of an option that was not
    picked.
    """
    lowered = text.lower()
    picked = selected if isinstance(selected, dict) else None
    answers = []
    for index, question in enumerate(questions):
        if not isinstance(question, dict):
            continue
        labels = []
        for option in question.get("options") or []:
            label = option.get("label") if isinstance(option, dict) else option
            # A bare number where a label belongs (not what the tool asks for, and a model may
            # write it all the same): the pages show it as its digits, and hand those back.
            if isinstance(label, (int, float)) and not isinstance(label, bool) and not isinstance(option, dict):
                label = str(int(label)) if float(label).is_integer() else str(label)
            if isinstance(label, str) and label:
                labels.append(label)
        if picked is not None:
            mine = picked.get(str(index))
            # The pages show a label without the blanks around it, and hand it back so: compared
            # without them, and given to the task agent as the question has it.
            wanted = {str(each).strip() for each in mine} if isinstance(mine, list) else set()
            chosen = [label for label in labels if label.strip() in wanted]
        else:
            chosen = [label for label in labels if label.lower() in lowered]
        if not question.get("multi_select"):
            chosen = chosen[:1]
        answers.append({"id": str(question.get("id", index)), "selected": chosen, "custom": text})
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
