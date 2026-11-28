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

"""``TaskAgentManager`` assembly and core lifecycle scheduling.

``__init__`` is the single home of every instance attribute (locks,
session tables, queues, caches, flags); the seven domain mixins
contribute methods only.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sqlite3
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from nekopower.host import call_audit
from nekopower.host.capabilities import CapabilityRegistry
from nekopower.host.execution_audit import ExecutionAudit
from nekopower.host.ledger import ACTIVE_STATUSES, ENDED_STATUSES, Commission, Ledger, Session, Transition
from nekopower.host.paths import DEFAULT_PROJECT, HostPaths
from nekopower.host.permissions import DENIED_BY_SETTINGS, PermissionDecision, PermissionGate, SessionActionScope
from nekopower.host.plugin_runs import PluginRunner
from nekopower.host.preferences import PreferenceStore
from nekopower.host.reports import CHANNEL_TASK_AGENT, Reporter
from nekopower.host.runtimes import RUNTIME_BUILTIN, RUNTIME_DSH, Runtimes
from nekopower.host.tree_work import TreeWork
from nekopower import TASK_CONTROL_TOOLS
from .types import (
    logger,
    _FORK_REMOVE_RETRIES,
    _SESSION_NOT_GIVEN_BACK,
    _REACHED,
    _THEN,
    _CAUGHT_UP_SECONDS,
    _SHUT_DOWN_REASON,
    _QUOTA_REASON,
    _QUOTA_RUN_REASON,
    _QUIET_FAILURES,
    _STOPPED_MEANWHILE_REASON,
    _WOKEN_REASON,
    _STOPPABLE_CHILD_STATUSES,
    _CHILD_DISABLED_TOOLS,
    _PRESET_ON_RECORD_WITHIN,
    _USAGE_REASON,
    _NO_PLACE_REASON,
    _STILL_WORKING,
    _CHANGING_TOOLS,
    _REPORT_TO_STATUS,
    SCREENSHOT_POOL,
    _STATUS_TO_REPORT,
    ModelConfigProvider,
    RuntimeFactory,
    UsageMeter,
    LessonSink,
    ForkRefused,
    LedgerLessonSink,
    _SessionAction,
    _Beside,
    _Session,
    _carried_on,
    _number,
    _ends_kept,
    _ended_as,
    _not_refused,
    _withdraw,
    _refused_answer,
    _refusal,
    _Handed,
    _Unsaid,
    _Found,
    _shows_work_taken,
    _shows_run_at_work,
    default_runtime_factory,
)
from .idle_compact import IdleCompactMixin
from .billing_usage import BillingUsageMixin
from .callbacks import CallbacksMixin
from .forks_beside import ForksBesideMixin
from .messaging import MessagingMixin
from .delegation import DelegationMixin
from .opening import OpeningMixin

# Late-binding read point for symbols that tests rebind on
# ``nekopower.host.task_agents`` via ``monkeypatch.setattr``. Do NOT
# from-import those names here: a from-import snapshots the value at
# import time and the facade patch would silently miss this module.
from nekopower.host import task_agents as _task_agents_facade


class TaskAgentManager(
    IdleCompactMixin,
    BillingUsageMixin,
    CallbacksMixin,
    ForksBesideMixin,
    MessagingMixin,
    DelegationMixin,
    OpeningMixin,
    TreeWork,
):
    """Runs task agents for commissions. See the module docstring.

    What it does about the git worktrees members of a project work in,
    and about a leader's team, is in ``tree_work.py`` (``TreeWork``).
    """

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
        self._handed_said: dict[str, list[tuple[str, int]]] = {}
        # What tells whether a commission is a side question
        # (``LexicalDecisionModel.side_question``), and, for a commission it
        # was asked of, what it was asked about and what it said: asked once
        # for the same words (``_is_side_question``).
        self._side_question_judge: Callable[[str, str, str], Awaitable[bool | None]] | None = None
        # What tells whether work told apart from what a busy member is at goes on with it
        # all the same (``set_beside_judge``).
        self._beside_judge: Callable[[str, str, str, list[str]], Awaitable[bool | None]] | None = None
        # Where her commissions go, and the members and leaders of projects.
        from nekopower.host.families import Families

        self.families = Families(ledger, space, paths, clock=ledger._clock, changes_files=self._opened_to_change_files)
        # By session: the project it writes files in, in the project's own
        # directory, and the member it does so for. One such member works
        # in a project at a time (``_session_free_for``); one that works in
        # a git worktree of its own is not among them.
        self._writes_in: dict[str, tuple[str, str]] = {}
        # The git worktrees members work in, and a leader's team (``tree_work.py``).
        self._init_tree_work()
        # (One manager of worktrees for the host: its locks are its own.)
        self.families._worktrees = self.worktrees
        # What the user said to a commission while its leader was handing
        # it over, by commission: from the leader's call until the
        # commission is queued for the task agent it goes to, which is
        # told it then, in the user's words (``_handed_over``). Kept
        # apart from the leader's session, which is let go of in between.
        self._said_while_handed: dict[str, list[str]] = {}
        # The projects whose task agents are being noted as ones that change
        # files or not, several together (task agents that just became its
        # members, and those they started): until all are, none that
        # changes files starts in it, whatever else comes free meanwhile.
        # Counted: two lots may be noted in one project at a time, and it is
        # held until the last of them is through.
        self._noting: dict[str, int] = {}
        # The commissions a ``hand_over`` call is at work on: a second call
        # for one, made beside it, is refused before either awaits anything.
        self._handing_claims: set[str] = set()
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
        # Commissions of hers that went to the member that works in a project without a
        # leader for being that member's project's, and say something plainly apart from
        # what it works on (``Verdict.apart``), each with that member: from where it is
        # queued (``submit``) to where it is looked at, once (``_try_beside``).
        self._apart: dict[str, str] = {}
        # The queued commissions the user is asked about, or that another member is being
        # made for: whether another member is to do them beside the busy one they wait
        # for (``_beside``). They keep their places in the queue meanwhile.
        self._asking_beside: dict[str, _Beside] = {}
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
            start_team=self._cb_start_team,
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

    def set_beside_judge(self, judge: Callable[[str, str, str, list[str]], Awaitable[bool | None]] | None) -> None:
        """Set what tells whether a commission goes on with what a busy member is at (``LexicalDecisionModel.goes_on_with``).

        It is given what was dispatched, the user's words (with what they said
        to it since), the member's name and what it has under way. Asked once,
        before the user is asked whether another member is to do it, or one is
        opened for it with nobody asked (``_beside``). Without one, the words
        alone tell it (``orchestrator.apart_from``).
        """
        self._beside_judge = judge

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
        # Whether it was told apart from what the member it went to works on (``_give_session``):
        # kept here, and on record only once it is queued behind that member (``_try_beside``).
        apart: str | None = None
        if not self._closed:
            try:
                commission = await self._out_of_copy(commission)
                commission = await self._give_session(commission)
                apart = self._apart.pop(commission.id, None)
            except asyncio.CancelledError:
                self._excerpts.pop(commission.id, None)
                self._apart.pop(commission.id, None)
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
            # carried on in a copy of the session meanwhile. Where it is not
            # looked at for that, or is not carried on after all, another
            # member may do it beside that session's (``_try_beside``).
            if apart is not None:
                self._apart[commission.id] = apart
            if not self._try_carry_on(commission):
                self._try_beside(commission.id)
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
        if commission_id in self._asking_beside:
            # The user is asked whether another member is to do it, or one is being made for
            # it: it starts nowhere until that is settled (``_beside``), whoever looks, and
            # however free its session is by then.
            return False
        if self._handing_back_to.get(session_id):
            # A commission a copy handed back is being put back in its place
            # among those that wait for the session (``_handed_back``).
            return False
        holder = self._held_by.get(session_id)
        if holder is not None and holder != commission_id:
            return False
        # One task agent that writes files works in a project's own
        # directory at a time: another member that does waits until that
        # one's commission is over. Those that read only do not wait, nor
        # does the leader, nor one that works in a git worktree of its own
        # (it is not noted as one that writes there, ``_note_writer``).
        where = self._writes_in.get(session_id)
        if where is not None:
            if where[0] in self._noting:
                # Who changes files in its project is being worked out: it waits until that is known.
                return False
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
            if verdict.apart and verdict.agent:
                # Should it wait for that member, the user may be asked whether another is to do it (``_try_beside``).
                self._apart[commission.id] = verdict.agent
            guessed: set[str] = set()
            if verdict.members and commission.project:
                # Those that became the project's members count as ones that change files from
                # here on, before anything is awaited: each is looked at below, and one that does
                # not is taken off again (at work or not: it was only guessed here).
                guessed = {member for member in verdict.members if member not in self._writes_in}
                for member in guessed:
                    self._writes_in[member] = (commission.project, member)
            if not verdict.members:
                await self._note_writer(session.id)
                return commission
            # Task agents that became a project's members with it: each may be at work, and so may
            # task agents they started, which were none of a project's when they began. Until all
            # of them are noted, none that changes files starts in the project (``_noting``): one
            # that came free meanwhile would start beside one not noted yet.
            noting = commission.project
            if noting:
                self._noting[noting] = self._noting.get(noting, 0) + 1
            try:
                for other in {session.id, *verdict.members, *self._held_by}:
                    await self._note_writer(other, guessed=other in guessed, batch=True)
            finally:
                if noting:
                    left = self._noting.get(noting, 1) - 1
                    if left > 0:
                        self._noting[noting] = left
                    else:
                        self._noting.pop(noting, None)
                # What waited is looked at once, now that all of them are noted.
                if self._queue and not self._closed:
                    self._spawn(self._start_next())
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
                # One of a leader's team that changes files works in a git worktree (``start_team``).
                seat="tree" if (await self._chosen(commission.id)).get("seat") == "tree" else None,
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

    async def _note_writer(self, session_id: str | None, *, guessed: bool = False, batch: bool = False) -> None:
        """Note whether a session writes files in a project, for one such to work there at a time (``_session_free_for``).

        A member of a project whose preset can change files, with a tool
        that writes or edits one or through the shell
        (``Template.changes_files``), and a task agent such a member
        started that can: noted with the member it works for, which it
        does not wait for, while the members of the project wait for it
        as they wait for the member (a task agent whose stop was not
        seen through may still be at it when the member is over). Not
        the project's leader, which has none of those tools, nor what
        the leader starts, which looks and checks; not a copy, which
        reads. One whose preset cannot be read counts as one that
        changes files. What a session does through ``capability_call``
        (a plugin, a connected service) is not counted, the leader's
        included: it is none of the runtime's tools that work in the
        project's directory, and goes by the permission of its own
        category. A plugin that changes a file of the project does so
        beside whichever member is at work.
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
            self._unnote_writer(session_id, guessed=guessed, batch=batch)
            return
        # A run under way has the tools it was opened with, whatever its preset says by now.
        live = self._sessions.get(self._held_by.get(session_id) or "")
        opened = live.changes_files if live is not None and live.session_id == session_id else None
        if opened is not None:
            changes = opened
        else:
            try:
                changes = (await self._template_of(kept)).changes_files
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - not told: held to the rule
                changes = True
        if changes and await self._works_in_tree(member):
            # It changes files in a git worktree of its own, and so does a task agent it
            # started: neither waits for whoever works in the project's directory, nor is
            # waited for there. (Known of where it works, not guessed: taken off at work too.)
            self._unnote_writer(session_id, guessed=True, batch=batch)
            return
        if changes:
            self._writes_in[session_id] = (member.project, member.id)
        else:
            # (Known of the run itself, it is as good as looked at: taken off though it is at work.)
            self._unnote_writer(session_id, guessed=guessed or opened is not None, batch=batch)

    def _unnote_writer(self, session_id: str, *, guessed: bool = False, batch: bool = False) -> None:
        """Take a session off the ones that change files in a project, unless a run of it is under way.

        One at work was opened with the tools its preset had then: a
        template changed since, or a new commission filed for it, does
        not make that run one that reads. It is looked at again with the
        next commission, once this one let go of it. One that was noted
        on a guess (``guessed``: as it became a project's member, before
        it was looked at) is taken off whether at work or not: nothing
        but the guess said it changes files. What waited for it is looked
        at then, unless this is one of several being noted together
        (``batch``): whoever notes them looks once they all are.
        """
        if guessed or self._held_by.get(session_id) is None:
            taken = self._writes_in.pop(session_id, None) is not None
            if taken and not batch and self._queue and not self._closed:
                # One that waited for it may start now, and nothing else may come by to say so.
                self._spawn(self._start_next())

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

    async def _opened_to_change_files(self, kept: Session) -> bool:
        """Whether a session can change files by the template it is opened by (``Template.changes_files``); raises as ``_template_of``."""
        return bool((await self._template_of(kept)).changes_files)

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
            if not commission.session_id:
                # On record with no session: the user was asked whether another member is to do it,
                # and it could not be put back among the queued with its session (``_ask_beside``).
                # It is the session's it starts in.
                commission = await self._ledger.update(commission_id, session_id=session.session_id) or commission
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
                return str(session_dir), str(workdir), _task_agents_facade._begun_in(place, session_dir)

            session_path, workdir, begun_in = await asyncio.to_thread(prepare)
            # Where its session works; the commission itself may be worked on
            # in a git worktree of that directory, made now or found again
            # (``tree_work.py``). The session keeps the directory it has on
            # record: the next commission's worktree is made from there.
            home = workdir
            tree = await self._tree_to_work_in(commission, kept, home, bool(template.changes_files))
            if tree is not None:
                workdir = tree.workdir
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
                await self._tree_not_started(commission.id)
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
                await self._tree_not_started(commission.id)
                return False
            # (A git worktree it goes on in is at work in from here on, and not before:
            # a start that came to nothing left it as the user was to decide on it.)
            await self._tree_at_work(commission.id, kept.id)
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
                home = workdir
                commission = await self._ledger.update(commission.id, workdir=workdir) or commission
            # (A session of one commission may have been made a project's member since it
            # was read, by a commission that came for the same matter: ``Ledger.adopt``. This
            # start goes on as it began, whole: its runtime was chosen and its gateway token
            # issued by the preset as it was read, and a start that took the project's preset
            # for its tools alone would be neither. It is as with one that is at work when it
            # is made a member: the project's preset and rules are its own from its next start.
            # Only whose it is on record must not be written over: ``note_project`` below.)
            # A blocked or failed commission must be working again before its
            # opening brief can reserve a call against the new run.
            spec = await self._spec(
                commission, kept, model, workdir=workdir, session_dir=session_path, gateway=gateway_spec,
                template=template,
            )
            session.changes_files = not _CHANGING_TOOLS <= set(spec.disabled_tools)
            if session.changes_files and kept.kind == "child" and kept.parent_id and tree is None:
                # One a leader started was looked at when it asked for it
                # (``_leader_starts_readers``); its preset was read anew for
                # these tools, and a template may have changed in between.
                # It is not opened with tools its leader is without: nothing
                # would make it wait for the member that changes files in
                # the project (``_note_writer`` counts it as no member's).
                # (One of the leader's team has a git worktree to change
                # files in, ``start_team``: that one is opened.)
                starter = await self._ledger.get_session(kept.parent_id)
                if starter is not None and starter.kind == "project":
                    from config.prompts.prompts_nekopower_brain import LEADER_AGENT_CHANGES_FILES

                    raise RuntimeError(LEADER_AGENT_CHANGES_FILES)
            if session.changes_files and kept.kind == "line" and kept.parent_id and commission.parent_id is None:
                # A member a leader made as one that only reads can change files by now.
                await self._ask_about_member(commission, kept)
            # Noted by the tools it is opened with: its preset was read anew for them, and may
            # say otherwise than when its session was looked at before (a template changed in
            # between). Others of its project wait for it from here on, or wait for it no more.
            await self._note_writer(session.session_id)
            # On record with the session from here on: a later commission of
            # it is opened in the same place. (That the session is at work
            # was written with the commission's status.)
            await self._ledger.update_session(
                session.session_id,
                runtime=runtime_name,
                # (Where the session works, not the worktree this commission has.)
                workdir=home,
                session_dir=session_path,
            )
            # Its project where it has none on record yet, and not over one it was made a
            # member of while this start was under way (what was read above is from before).
            await self._ledger.note_project(session.session_id, place.project)
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
            await self._fail(
                commission_id, f"the task agent could not be started: {exc}", said=said, detail=str(exc), free=session.free
            )
            # A git worktree this start took as its commission's is let go of (one that was
            # stuck before ends as it was, which nothing else acts on): the user decides on it.
            await self._tree_not_started(commission_id)
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
            # Only the leader of a project hands a commission over, or starts a team for one.
            withheld.update(("hand_over", "start_team"))
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
        # What follows for the git worktree it was worked on in, and for a leader's team.
        self._tree_commission_changed(commission, previous)
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
        # Those found, on a second look, to have to wait for another that changes files in their project.
        waits: set[str] = set()
        # A session a commission waits for while the user is asked about it may have come free:
        # what is asked is taken back, and nothing starts in that session until that is seen
        # to. Before places are looked at: taken back though none is free for it to start at.
        # (Whoever lets a session go has places given out anew, so this comes by; one let go
        # while this waits below is seen by that, and the commission itself takes no place
        # meanwhile, ``_session_free_for``.)
        self._beside_came_free()
        while not self._closed and len(self._sessions) < self.limit:
            # One whose old session is still on its way out keeps its turn;
            # it is started when that session has gone. So does one whose
            # session another commission holds: it is next when that one is
            # over, and those behind it in other sessions go ahead meanwhile.
            commission_id = next(
                (
                    item
                    for item in self._queue
                    if item not in self._sessions and item not in waits and self._session_free_for(item)
                ),
                None,
            )
            if commission_id is None:
                return
            # Whether its session changes files in a project is looked at once more, as it is
            # about to start: its preset may say otherwise than when it was queued (a template
            # changed meanwhile), and it is opened by what the preset says now.
            await self._note_writer(self._session_of.get(commission_id))
            if self._closed or len(self._sessions) >= self.limit:
                # The last place went to another while that was read: it stays queued, for the next one that comes free.
                return
            if commission_id not in self._queue or commission_id in self._sessions:
                # Taken, or gone, while that was read.
                continue
            if not self._session_free_for(commission_id):
                waits.add(commission_id)
                continue
            # (From the looks above to the place being taken below nothing is awaited.)
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
            # Whether it changes files in a project is looked at as well: after a restart nothing
            # is noted yet, and a commission taken up again comes by here and not by ``_give_session``.
            await self._note_writer(self._session_of[commission_id])
            return True
        commission = await self._ledger.get(commission_id)
        if commission is None or commission.route != "task_agent":
            return False
        await self._give_session(commission)
        # (Taken up again, it is not looked at for another member: ``_try_beside`` is for what is handed over.)
        self._apart.pop(commission_id, None)
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
        for told, ending in list(self._handed_said.get(session.commission_id, ())):
            try:
                # With the ending it was told of: one that ended anew since is told once more.
                await self._ledger.append_event(
                    told, "told_to_leader", {"commission_id": session.commission_id, "ending": ending}
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
                # (A report of a run on its way out, whose commission was queued again meanwhile, is
                # refused, as it was before a queued commission could wait for the user at all.)
                only_from=("working",),
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
        if target == "done" and await self._lands_first(
            session, text=text, reason=f"the task agent reported {status}", delivery=event.get("delivery")
        ):
            # Worked on in a git worktree: what it changed is landed in its
            # project's directory first, and the commission is done then.
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
        session.settling += 1
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
            session.settling -= 1

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
                if self._run_over_in_tree(session, event):
                    # It said it is done, in a git worktree, and what it
                    # changed is being landed: that decides, and its session
                    # stays for what a clash may send it back to.
                    return
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

                if not went_on() and await self._lands_first(
                    session, text=text, reason="the run completed", run_over=True
                ):
                    # Worked on in a git worktree: done once what it changed is landed.
                    session.idle = True
                    return
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
                await self._fail(commission_id, error or "the run failed", said="start", detail=error, free=session.free)
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
                await self._fail(commission_id, error or "the run failed", said="run", detail=error, free=session.free)
        await self._end_what_it_started(commission_id)
        await self._close_session(session)

    async def _end_what_it_started(self, commission_id: str) -> None:
        """End the task agents a commission that is over had started: they must not go on working for nobody.

        For any end: of a run (``_act_on_end``), at a landing, of the quota
        partway through (``_on_usage``), or of the usage the user allowed
        (``_wind_up_step``). How it ended is written by now, and the caller
        sees to its own session as it does once this returns (closes it, or,
        at a landing, waits for these to be gone from their runtime before
        what was changed is merged). These ends come once, so
        what the ledger is asked, and each one's stop, is tried again after
        a failure (``_looked``). Each by itself: one that cannot be stopped
        this moment does not leave the others at work, nor keep the
        commission's own session open; nor does a ledger that cannot say
        what it started (left open, the session would keep its place for a
        commission that is over, and the senders of its notes would hear
        nothing). One whose stop cannot be written, even asked again, is
        closed all the same: left open, it would go on working, and using
        requests, for a commission that is over (on record it stays at work,
        and can still be stopped). One whose stop is refused because it
        ended meanwhile, some other way, is not closed: its session is its
        own, and its own end lets it go. Every failure is logged and none is
        raised: no caller has anyone to hand it to (the runtime that asks
        ``_cb_budget`` takes a failure for room to go on, and would make the
        request it asked about; ``_pump_events`` would only log it).

        Not one whose work is done and waits in its git worktree for the
        user (held back, or not landed): it is stuck over that alone, and
        is done once the user has it landed. That is looked at as its stop
        is written (``_stopped_with_parent``), not read once before: one
        that comes to wait meanwhile is not missed, one taken up again
        meanwhile is not passed by, and a ledger that cannot be read does
        not make it look like none. Where its stop cannot be written at all,
        one that is stuck may be such a one, and is left as it is (it is not
        at work): as it stands then, read again (``_stands_stuck``).
        """
        what = "for a commission that is over"
        try:
            started = await self._looked(
                lambda: self._ledger.select(parent_id=commission_id, statuses=ACTIVE_STATUSES), what=what
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - its own session is closed all the same
            logger.warning("finding what commission %s started failed", commission_id, exc_info=True)
            return
        for child in started:
            try:
                moved = await self._looked(lambda child=child: self._stopped_with_parent(child.id), what=what)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - see below
                logger.warning("writing that commission %s stopped with its parent failed", child.id, exc_info=True)
                if await self._stands_stuck(child):
                    # Stuck, so not at work: it may be one whose changes wait in its worktree for
                    # the user, which cannot be told with the ledger as it is. It is left as it is.
                    continue
                # Else it is closed all the same.
            else:
                if moved is None:
                    # The user's decision on its worktree is being carried out this moment.
                    continue
                if not moved.applied and moved.commission is not None and moved.commission.status != "cancelled":
                    # It ended meanwhile, some other way, or its changes wait in its worktree for
                    # the user: its session is its own. (One that is gone from the ledger meanwhile
                    # is closed all the same: nothing is its own any more.)
                    continue
            try:
                # Asked again as well: stopping it reads and writes the ledger before its session
                # is closed (what it started in turn), and is safe to do twice.
                await self._looked(lambda child=child: self.abort(child.id), what=what)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the others are stopped all the same
                logger.warning("stopping commission %s with its parent failed", child.id, exc_info=True)

    async def _stands_stuck(self, child: Commission) -> bool:
        """Whether a task agent whose stop could not be written stands stuck now; as it was read where that cannot be told.

        Read again, once: taken up again since it was read (queued, or at
        work), it is to be stopped, and one that came to be stuck since is
        not. Where it cannot be read either, it is taken as it was read;
        gone from the ledger, it is not stuck.
        """
        try:
            current = await self._ledger.get(child.id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - taken as it was read
            logger.warning("reading commission %s again failed; it is taken as it was read", child.id, exc_info=True)
            return child.status == "blocked"
        return current is not None and current.status == "blocked"

    async def _stopped_with_parent(self, commission_id: str) -> Transition | None:
        """Write that a task agent stopped with its parent, unless its changes wait in its worktree for the user.

        Whether they do is looked at in the transaction that writes it
        (``Ledger.transition``'s ``unless_waiting_in_tree``): a worktree
        that comes to wait a moment before is not missed. None, with
        nothing written, while the user's decision on its worktree is
        carried out (``_decided_for``): the worktree may be on record as
        being landed by then, or be gone, with it not done yet. That is
        looked at each time this is asked, with nothing awaited before the
        write is asked for, and the user's decision is in hand
        (``_tree_in_hand``) before its worktree is written as being landed.
        A landing that writes how it ends this moment (``_landing_ends``,
        ``_nothing_more_said``) is waited for first, and it is looked at as
        that leaves it: left for the user, its worktree is on record as
        waiting before it is on record as stuck, and in between it is still
        at work; put down as done, it is over; and should the landing give
        way to more that was said to it, it is at work, and stopped. (What
        that landing writes waits, as it tells the parent, only for a
        landing of the parent's own; none of the ends this is asked from is
        within one.)

        Only one still under way is written (``only_from``): one that ended
        meanwhile is refused with nothing put on its record, as one that
        waits in its worktree is; one stopped already is refused as such.
        """
        await self._landing_end_written(commission_id)
        if commission_id in self._decided_for.values():
            return None
        return await self._ledger.transition(
            commission_id, "cancelled", reason="its parent finished", only_from=ACTIVE_STATUSES,
            unless_waiting_in_tree=True,
        )

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
            await self._fail(
                commission_id, f"the task agent could not be started: {message}", said="start", detail=stated, free=session.free
            )
            self._keep_unsent(session)
            await self._release(commission_id)
        elif command == "prompt":
            session.untaken_answer = None
            # Refused, so it is not in the transcript: what the user said
            # with it is kept, as when the start itself fails.
            # Counted back above: none taken means the work did not start.
            await self._fail(
                commission_id, message, said="run" if session.sent else "start", detail=stated, free=session.free
            )
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

    async def _fail(self, commission_id: str, reason: str, *, said: str, detail: str = "", free: bool = False) -> None:
        """End a commission as failed for a reason of the host's own.

        ``reason`` is what goes on record, in English. ``said`` says what
        it was: ``quota`` (the task agent's model ran out of quota before a
        task agent was ever at work on it), ``quota_run`` (the same, once
        one was, now or before it was taken up again: part of the work may
        be done), ``start`` (the task agent
        could not start on it: nothing of it was taken) or ``run`` (its run
        stopped on an error). A ``start`` or ``run`` whose error says the
        provider refused the model's API key, or that its account has no
        quota or balance left, is told as that (``model_key``,
        ``model_quota``): the user has to fix it, and is shown so in plain
        words; the keyboard-and-mouse and browser switches no longer ask the
        model beforehand, so this is where a wrong key first shows. Not with
        the built-in free key (``free``): there is no key or account of the
        user's to fix then, and the error is told as it came. She is told that,
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
        from nekopower.runtime.model import model_failure_kind

        if said in ("start", "run") and detail and not free:
            said = model_failure_kind(detail) or said
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
        if self._heard_of_inspection(commission, status, text):
            # An inspector the host started before a landing: the host acts
            # on what it found (``tree_work.py``), and nobody is told as such.
            return
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
        if parent_id in self._landing_ends:
            # A landing is putting the parent down as done this moment: looked at again once
            # that is written, as what the user says to it is (``tell``).
            await self._landing_end_written(parent_id)
            await self._tell_parent(commission, status, text)
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

    async def _wake_working(self, parent_id: str, message: str, *, reason: str, handed: bool = False) -> bool:
        """Take up again a commission that is ``working`` with no session open in a runtime, ``message`` its next work.

        Not one its leader is handing over (``_said_while_handed``), but
        for what hands it over itself (``handed``): until it is the other
        task agent's, whatever else would take it up would start it where
        it was. What a wake brings then waits, as below; anything else
        (what a task agent it started hands back, an answer sent anew) is
        kept with what the user said meanwhile, and goes to the other
        task agent after the leader's brief.

        Such a commission waits for something: for task agents it started
        (``_wake_closed_parent``), or, resting, for what it asked to be
        woken for (``hand_over``). It is queued and started where its
        transcript ends, as soon as a place and its session are free.
        Returns whether the message is on its way to it: handed over, or
        going along with the work it is about to be given.
        """
        if parent_id in self._said_while_handed and not handed:
            if isinstance(message, _Handed):
                return False
            self._said_while_handed[parent_id].append(message)
            return True
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

        The caller has set the commission ``cancelled`` already. Calling
        this again for it does nothing more than closing what is still open
        of it: one whose closing failed at the stop is closed then. So are
        the task agents it started that were stopped already (with it, or
        before it): one whose own closing failed is closed this way too.

        One of those that cannot be stopped or closed does not keep the
        others, nor this commission's own session, from being ended: the
        first failure is raised once the rest is done.
        """
        failed: Exception | None = None
        try:
            children = await self._ledger.select(parent_id=commission_id, statuses=_STOPPABLE_CHILD_STATUSES)
        except Exception as exc:  # noqa: BLE001 - raised once this one is ended
            logger.warning("finding what commission %s started failed", commission_id, exc_info=True)
            children, failed = [], exc
        for child in children:
            try:
                if child.status != "cancelled":
                    # Not closed unless its stop is written: closing is for a stopped one.
                    moved = await self._ledger.transition(child.id, "cancelled", reason="its parent was stopped")
                    if not moved.applied and (moved.commission is None or moved.commission.status != "cancelled"):
                        # It ended meanwhile, some other way: its session is its own.
                        continue
                # One stopped already is closed again with nothing written: a
                # ledger that cannot be written to does not keep it open.
                await self.abort(child.id)
            except Exception as exc:  # noqa: BLE001 - raised once the rest is ended
                logger.warning("stopping commission %s with its parent failed", child.id, exc_info=True)
                if failed is None:
                    failed = exc
        try:
            await self._abort_own(commission_id)
        except Exception:
            if failed is None:
                raise
            logger.warning("closing stopped commission %s failed", commission_id, exc_info=True)
        if failed is not None:
            raise failed

    async def _abort_own(self, commission_id: str) -> None:
        """``abort`` for the commission itself, once the task agents it started are seen to."""
        if commission_id in self._queue:
            self._queue.remove(commission_id)
        # Asked whether another member is to do it: nobody answers that any more.
        self._withdraw_beside(commission_id, "stopped")
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

    async def tell(
        self, commission_id: str, text: str, *, still_wanted: Callable[[], bool] | None = None, recorded: bool = False
    ) -> bool:
        """Pass a sentence on to a commission's task agent. Returns whether it was delivered.

        ``recorded``: it is on record as said already (it was being
        passed on as the commission came to be stuck, and takes it up
        again now), and is not put on record a second time.

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
        if commission_id in self._asking_beside and commission.status == "waiting_user":
            # Not started yet, the user being asked whether another member is to do it: this
            # is no answer to that (which is a choice), and goes along with the work when it
            # is given, as what is said to any commission that is queued. (Only while it
            # waits so: one stopped a moment ago, whose question is still being taken back,
            # is over, and takes nothing.)
            self._notes.setdefault(commission_id, []).append(text)
            await self._ledger.append_event(commission_id, "told", {"text": text, "queued": True})
            return True
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
                return await self.tell(commission_id, text, still_wanted=still_wanted, recorded=recorded)
            # One that ended in a copy carried it on in is taken up in the
            # session itself, told what came of it in the copy.
            from_copy = await self._back_from_copy(commission)
            if still_wanted is not None and not still_wanted():
                return False
            if not recorded:
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
            if commission_id in self._landing_ends:
                # What it changed is landed, and it is being put down as done this moment: whether
                # more was said to it was looked at just before (``_landed_or_not``), and steered
                # in now this would start a run of a commission that is done. Said to it as it
                # stands once that is written (done, this is work of its own).
                await self._landing_end_written(commission_id)
                return await self.tell(commission_id, text, still_wanted=still_wanted)
            await self._ledger.append_event(commission_id, "told", {"text": text})
            if commission_id in self._said_while_handed:
                # Its leader handed it over while that was written.
                self._said_while_handed[commission_id].append(text)
                return True
            # A landing may be writing how it ends this moment (as above), or the commission may
            # have come to an end while that was put on record, its session not yet on its way
            # out: gone by how it stands now, with nothing awaited between this look and the text
            # being sent.
            await self._landing_end_written(commission_id)
            stands = session.stands
            if stands is not None and stands.status in ("blocked", "failed"):
                # Stuck meanwhile (what it changed was left for the user, say): taken up again
                # with this, as what is said to any that is stuck. It is on record already.
                return await self.tell(commission_id, text, still_wanted=still_wanted, recorded=True)
            if stands is not None and stands.status in ENDED_STATUSES:
                # Over: steered in, it would start a run in a session that is about to be closed.
                return False
            if self._sessions.get(commission_id) is not session:
                moved = await self._ledger.get(commission_id)
                if moved is not None and moved.handed_by and moved.session_id != session.session_id:
                    # Its leader handed it over while that was written, and it waits for the other
                    # task agent or is at work there: said where it is now. (Only then: a session
                    # that went for another reason is told as it was before.)
                    return await self._wake_working(commission_id, text, reason=_WOKEN_REASON)
            elif session.closing:
                # Still the commission's, and on its way out meanwhile (closed to rest, say): what
                # is steered in now is lost with it.
                return False
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
        self,
        commission_id: str,
        text: str,
        choice: str | None = None,
        *,
        question_id: str | None = None,
        selected: dict[str, list[str]] | None = None,
    ) -> bool:
        """Give the user's answer to a commission that waits for it.

        ``selected`` is which options of the questions put with
        ``ask_user_question`` the user picked with a button
        (``build_answers``); nothing else that is asked reads it.

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
            session.question.set_result({"text": text, "choice": choice, "selected": selected})
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
                commission_id,
                "waiting_user",
                reason="its session closed before it took the answer",
                # (From where the answer moved it to: a queued one is asked about by ``_ask_beside`` alone.)
                only_from=("working",),
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

    # Returns the limit the user set on the page about the runtime, in
    # tokens, or None while the setting of the build holds
    # (``usage_limit.UsageLimitStore.get``). Set by the host; a manager
    # made without one goes by the setting.
    usage_limit_of: Callable[[], Awaitable[int | None]] | None = None

    # -- shutdown ---------------------------------------------------------

    async def shutdown(self) -> None:
        """Stop the runtime and every session. Commissions under way end as failed."""
        from config.nekopower_settings import TURNS_OWED_CLOSE_WAIT_SECONDS

        self._closed = True
        for session_id in list(self._idle_timers):
            self._drop_idle_timer(session_id)
        for commission_id in list(self._asking_beside):
            # Nobody answers what was asked of a queued commission any more: it ends with the others that are queued.
            self._withdraw_beside(commission_id, "closed")
        # What is being landed, checked or watched for git worktrees: stopped (a landing cut off
        # puts back what it began to write). The worktrees stay, for the next process to go through.
        await self._stop_tree_work()
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
            _done, pending = await asyncio.wait(list(self._starting), timeout=_task_agents_facade._SHUTDOWN_START_WAIT_SECONDS)
            for task in pending:
                task.cancel()
            if pending:
                # A cancelled start may still be settling a SQLite transaction.
                # _starting owns it until completion, even if the grace period expires.
                await asyncio.wait(pending, timeout=_task_agents_facade._SHUTDOWN_START_WAIT_SECONDS)
        if self._fork_records:
            # Copies that are being put on record: waited for as long as
            # a start is, and left to go on after that. A ledger that is
            # closed before it got to one has it fail, and what was
            # written of that copy is removed.
            await asyncio.wait(list(self._fork_records), timeout=_task_agents_facade._SHUTDOWN_START_WAIT_SECONDS)
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
        await self._caught_up(within=max(_task_agents_facade._SHUTDOWN_START_WAIT_SECONDS, TURNS_OWED_CLOSE_WAIT_SECONDS))
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
                timeout=_task_agents_facade._SHUTDOWN_START_WAIT_SECONDS + _task_agents_facade._SERVICE_CLOSE_POLL_SECONDS,
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
            await asyncio.wait(settling, timeout=_task_agents_facade._SHUTDOWN_START_WAIT_SECONDS)
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
                timeout=_task_agents_facade._SHUTDOWN_START_WAIT_SECONDS + _FORK_REMOVE_RETRIES * _task_agents_facade._FORK_REMOVE_RETRY_SECONDS,
            )
