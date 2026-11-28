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

"""The host as one object: the task brain and everything it works with.

``NekoPowerHost`` builds the parts and connects them. Building it does no
work: no file is opened, no thread or process is started, nothing listens and
nothing is fetched. The ledger file is opened by the first dispatch or query,
the runtime and the gateway are started by the first task agent, and the task
agent space is first read when a task agent starts. Whether the user
installed dsh, the optional second runtime (``nekopower.dsh``), is looked at
when a task agent starts, too.

Whoever embeds the host supplies what it cannot know by itself, all optional:

* ``enabled`` / ``plugins_enabled`` / ``task_agents_enabled`` - the switches;
* ``change_token`` - changes whenever the plugin list may have changed;
* ``builtins`` - lists the built-in capabilities (keyboard and mouse,
  browser) and carries their calls out; without it they are not available;
* ``on_progress`` - told about the progress of a direct plugin call, for a
  display that shows it;
* ``task_agent_meter`` - counts each model request of a task agent against the
  quota of its model, and says when none is left;
* ``on_commission`` - told whenever a commission is filed or changes its
  status (a ``Ledger.subscribe`` listener), for a display of the tasks;
* ``on_builtin_activity`` - told when a commission begins and stops occupying
  the desktop or the browser (``leases.py``), for the banner that shows it;
* ``send_report`` - delivers an outbox row to the main server. Without it
  reports are stored and wait. Reports an earlier run left unsent go out
  from the first call that does work.

``mount.py`` does this for the agent process.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator, Callable, Sequence
from pathlib import Path
from typing import Any

from nekopower.host.board import Board
from nekopower.host.capabilities import BuiltinCapabilities, CapabilityRegistry
from nekopower.host.decision import ConfiguredDecisionLLM, DecisionLLM, DecisionModel, LexicalDecisionModel
from nekopower.host.task_agents import (
    TaskAgentManager,
    LessonSink,
    ModelConfigProvider,
    RuntimeFactory,
    UsageMeter,
    default_runtime_factory,
)
from nekopower.host.gateway import CapabilityGateway
from nekopower.host.leases import ActivityListener, BuiltinLeases
from nekopower.host.ledger import ENDED_STATUSES, PRESET_OF_KIND, Commission, Ledger, Listener
from nekopower.host.model_capabilities import ModelCapabilityStore
from nekopower.host.orchestrator import WORKING_PRESET
from nekopower.host.outbox import OutboxSender, SendReport
from nekopower.host.paths import DEFAULT_PROJECT, HostPaths
from nekopower.host.runtimes import RUNTIME_DSH
from nekopower.host.delivery import ManagedDelivery
from nekopower.host.permissions import PermissionGate, PermissionStore
from nekopower.host.plugin_runs import ClientFactory, PluginCatalog, PluginRunner
from nekopower.host.preferences import PreferenceStore
from nekopower.host.reports import CHANNEL_PLUGIN, CHANNEL_TASK_AGENT, Reporter
from nekopower.host.short_descriptions import ShortDescriptions
from nekopower.host.space import TaskAgentSpace
from nekopower.host.space_keeper import SpaceKeeper
from nekopower.host.task_brain import ProgressListener, QueryAnswer, Receipt, TaskBrain, TurnCheck
from nekopower.host.usage_limit import UsageLimitStore
from nekopower.host.wakes import SessionWakes

logger = logging.getLogger(__name__)


async def configured_task_agent_model() -> dict[str, Any] | None:
    """Return the model task agents use: the existing agent model slot.

    The address is taken as configured, ``http://`` included.
    """
    from nekopower.host.role_models import host_model_config
    from utils.config_manager import get_config_manager

    config = await asyncio.to_thread(get_config_manager(migrate=False).get_model_api_config, "agent")
    return host_model_config(config)


# The states of a session in which something is being done with it.
_SESSION_AT_WORK = ("running", "waiting", "compacting")


class HostShuttingDown(RuntimeError):
    """The host is being shut down: what was asked for is not begun."""


class ProjectUnreadable(RuntimeError):
    """The record of a project cannot be read, so it cannot be changed: it is left as it is."""


class ProjectBusy(RuntimeError):
    """Something is at work in a project, so where it works cannot be changed now. Its text is why (a code)."""


def _model_identity(base_url: Any, model: Any) -> str:
    """Return what a page tells a model by: its settings are kept by address and name, the page is given neither."""
    import hashlib

    from nekopower.host.model_capabilities import model_key

    return hashlib.sha256(model_key(base_url, model).encode("utf-8")).hexdigest()[:16]


class ProjectNotMoved(RuntimeError):
    """A project could not be put away: its directory could not be moved."""


class ModelChanged(RuntimeError):
    """The task agent model is another than the one the settings were read for. Its text is the model now."""


class NekoPowerHost:
    """The task brain with its ledger, task agents and gateway."""

    def __init__(
        self,
        *,
        root: Path | Callable[[], Path] | None = None,
        enabled: Callable[[], bool] | None = None,
        plugins_enabled: Callable[[], bool] | None = None,
        task_agents_enabled: Callable[[], bool] | None = None,
        change_token: Callable[[], Any] | None = None,
        send_report: SendReport | None = None,
        plugin_base_url: str | None = None,
        client_factory: ClientFactory | None = None,
        decision: DecisionModel | None = None,
        decision_llm: DecisionLLM | None = None,
        task_agent_model: ModelConfigProvider = configured_task_agent_model,
        runtime_factory: RuntimeFactory = default_runtime_factory,
        max_task_agents: int | None = None,
        lessons: LessonSink | None = None,
        builtins: BuiltinCapabilities | None = None,
        on_progress: ProgressListener | None = None,
        task_agent_meter: UsageMeter | None = None,
        on_commission: Listener | None = None,
        on_builtin_activity: ActivityListener | None = None,
        dsh: Any = None,
        dsh_runtime_factory: Any = None,
        conversation: Any = None,
        model_slots: Any = None,
    ) -> None:
        self.paths = HostPaths(root)
        # What she and the user said lately, from which a commission that
        # opens a new session is handed an excerpt. This process's own
        # (``conversation.py``), which the agent server feeds, unless
        # another is given.
        if conversation is None:
            from nekopower.host.conversation import recent_conversation

            conversation = recent_conversation()
        self.conversation = conversation
        self._task_agent_model = task_agent_model
        # Where dsh is, for a user who downloaded it; a page about the
        # runtimes reads and changes it through the methods further down.
        # Only the module that knows where dsh would be is imported here;
        # dsh's driver is imported when a commission first runs in dsh.
        if dsh is None:
            from nekopower.dsh.install import DshInstallation

            dsh = DshInstallation(self.paths.dsh)
        self.dsh = dsh
        self.ledger = Ledger(self.paths.ledger_db, reconcile_report=self._restart_report)
        self.preferences = PreferenceStore(self.ledger)
        self.permission_store = PermissionStore(self.paths.permissions_json)
        self.permissions = PermissionGate(self.permission_store)
        self.delivery = ManagedDelivery(self.ledger, self.permissions)
        # What the user allowed "for this session" ends with the commission.
        self.ledger.subscribe(self._on_commission_change)
        if on_commission is not None:
            self.ledger.subscribe(on_commission)
        self.catalog = PluginCatalog(base_url=plugin_base_url, client_factory=client_factory, change_token=change_token)
        self.runner = PluginRunner(self.catalog, base_url=plugin_base_url, client_factory=client_factory)
        self._decision_llm = decision_llm
        if decision is None and self._decision_llm is None:
            self._decision_llm = ConfiguredDecisionLLM()
        self.short_descriptions = ShortDescriptions(self.paths.short_descriptions_json, self._decision_llm)
        self.registry = CapabilityRegistry(
            self.catalog,
            plugins_enabled=plugins_enabled,
            builtins=builtins,
            short_descriptions=self.short_descriptions,
        )
        self.outbox = OutboxSender(self.ledger, send_report)
        self.reporter = Reporter(self.ledger, self.outbox)
        # Which commission is using the desktop, and which the browser.
        self.leases = BuiltinLeases(
            root_of=self._root_of,
            on_activity=on_builtin_activity,
            on_released=self.registry.builtin_released,
        )
        self.model_capabilities = ModelCapabilityStore(self.paths.model_capabilities_json)
        # What task agents keep for later commissions. The console reads and
        # edits it through this object.
        self.space = TaskAgentSpace(self.paths.space, ledger=self.ledger, outside_readable=self._outside_readable)
        self.gateway = CapabilityGateway(
            registry=self.registry,
            runner=self.runner,
            permissions=self.permissions,
            ledger=self.ledger,
            leases=self.leases,
        )
        self.task_agents = TaskAgentManager(
            ledger=self.ledger,
            preferences=self.preferences,
            permissions=self.permissions,
            runner=self.runner,
            registry=self.registry,
            reporter=self.reporter,
            paths=self.paths,
            model_config=task_agent_model,
            gateway=self.gateway,
            runtime_factory=runtime_factory,
            enabled=task_agents_enabled,
            max_concurrent=max_task_agents,
            lessons=lessons,
            meter=task_agent_meter,
            leases=self.leases,
            model_capabilities=self.model_capabilities,
            space=self.space,
            dsh=self.dsh,
            dsh_runtime_factory=dsh_runtime_factory,
            conversation=self.conversation,
            switched_on=enabled,
            # The slots of the model panel the presets' models are chosen
            # from (``role_models.PluginModelSlots``); None: every session
            # is sent to the task agent model, as before there were slots.
            model_slots=model_slots,
        )
        # How much the work on one commission may use before the user is
        # asked, where the user set a number of their own (``usage_limit.py``).
        self.usage_limit_store = UsageLimitStore(self.paths.usage_limit_json)
        self.task_agents.usage_limit_of = self.usage_limit_store.get
        # Hands sessions what waits for them: a note another session wrote,
        # a time a task agent set for itself. Started by the first call that
        # does work, never at startup.
        self.wakes = SessionWakes(self.ledger, self.task_agents)
        self.task_agents.set_waker(self.wakes)
        # A note that is on record is handed over whether or not whoever
        # wrote it is still there to say that it wrote one.
        self.ledger.subscribe_notes(self.wakes.kick)
        # Reviews of finished commissions and the tidying of the space, in
        # the background; started by a commission ending, never at startup.
        self.space_keeper = SpaceKeeper(
            space=self.space,
            ledger=self.ledger,
            llm=self._decision_llm,
            session_dir_of=self.task_agents.session_dir_of,
            hold_session=self.task_agents.hold_session,
            release_session=self.task_agents.release_session,
            hold_files=self.task_agents.hold_files,
            release_files=self.task_agents.release_files,
            forget_dsh_session=self.dsh.forget_session,
        )
        self.ledger.subscribe(self.space_keeper.watch)
        # The presets sessions are opened by, and the board of sessions a
        # page shows: both read when they are asked for.
        self.templates_store = self.task_agents.templates
        self._board = Board(
            ledger=self.ledger,
            space=self.space,
            templates=self.templates_store,
            paths=self.paths.space,
            model=self._task_agent_model_name,
        )
        # A plugin's request for confirmation is put to the user by the
        # gateway, through the same waiting the task agents' own questions use.
        self.gateway.set_ask_user(self.task_agents.ask_user)
        if decision is None:
            decision = LexicalDecisionModel(self._decision_llm)
        self.decision = decision
        # Whether a later commission of a busy project session is a side
        # question, which is answered in a copy of the session meanwhile.
        judge = getattr(decision, "side_question", None)
        if judge is not None:
            self.task_agents.set_side_question_judge(judge)
        # Whether work told apart from what a busy member is at goes on with it all
        # the same, before another member is opened for it: one request of the small model.
        beside = getattr(decision, "goes_on_with", None)
        if beside is not None:
            self.task_agents.set_beside_judge(beside)
        # Where a new commission belongs, when its words neither name nor
        # clearly match what is there: one request of the small model.
        belongs = getattr(decision, "belongs", None)
        if belongs is not None:
            self.task_agents.set_orchestrator_judge(belongs)
        self.brain = TaskBrain(
            ledger=self.ledger,
            preferences=self.preferences,
            registry=self.registry,
            runner=self.runner,
            decision=self.decision,
            reporter=self.reporter,
            permission_store=self.permission_store,
            permissions=self.permissions,
            task_agents=self.task_agents,
            enabled=enabled,
            on_progress=on_progress,
        )
        # Set when shutting down starts; the task finishes when it is done.
        self._shut_down = False
        # Held while a task agent is made for the user (the name is looked for and taken in one go)
        # and while where a project works is changed (its sessions and its record are written as one).
        self._project_lock = asyncio.Lock()
        # Changes of where a project works that are under way: kept so that they are carried through.
        self._changes: set[asyncio.Future[Any]] = set()
        # Held while a page's settings of a model are written: one request's going back does not undo another's.
        self._model_window_lock = asyncio.Lock()
        # Set by the first call that does work; see ``_first_use``.
        self._used = False
        # What goes through the git worktrees an earlier run left, once it was begun (``_first_use``).
        self._trees_looked_at: asyncio.Future[None] | None = None
        self._shutdown_task: asyncio.Future[None] | None = None

    def _restart_report(self, commission: Commission) -> tuple[str, dict[str, Any]] | None:
        """Build the report for a commission an earlier host process left unfinished.

        It was failed when the ledger was opened. She is told along with her
        next turn (``passive``): nothing wakes her for it, so it costs no
        model call of its own. Runs on the ledger thread.
        """
        from config.prompts.prompts_nekopower_brain import HOST_RESTART_FAILED_TEXT
        from nekopower.host.task_brain import _texts

        if commission.delivery == "silent":
            return None
        text = _texts(HOST_RESTART_FAILED_TEXT, commission.lang or "en")
        return self.reporter.task_result_report(
            commission,
            channel=CHANNEL_PLUGIN if commission.route == "plugin" else CHANNEL_TASK_AGENT,
            success=False,
            summary=text,
            detail=text,
            error_message=text,
            status="failed",
            source_kind=commission.route or "",
            source_name=commission.title,
            delivery_mode="passive",
        )

    def _on_commission_change(self, commission: Commission, previous: str | None) -> None:
        del previous
        # "For this session" lasts the whole commission: it ends when the
        # commission is done, has failed or is stopped. ``blocked`` does not
        # end it: the commission is taken up again where it stopped, with
        # what the user allowed (owner, 2026-11-13).
        if commission.status in ENDED_STATUSES - {"blocked"}:
            self.permissions.end_session(commission.id)
        # The desktop and the browser are given back as soon as a commission
        # is over or waits for the user: the user needs the desktop to
        # answer, and another commission may be waiting for its turn.
        if commission.status in ENDED_STATUSES or commission.status == "waiting_user":
            self.leases.release(commission.id)

    async def _outside_readable(self) -> bool:
        """Whether the user set reading outside the working directory to be allowed without asking.

        Only then does the task agent space look at a file out there that
        a note names as what it rests on.
        """
        return await self.permission_store.mode("outside_read") == "allow"

    async def _root_of(self, commission_id: str) -> str:
        """Return the commission a team is named after: the one no task agent started."""
        current = commission_id
        seen = {current}
        while True:
            commission = await self.ledger.get(current)
            parent = commission.parent_id if commission is not None else None
            if not parent or parent in seen:
                return current
            seen.add(parent)
            current = parent

    def _first_use(self) -> None:
        """Start sending reports left unsent by an earlier run of the host.

        Building the host does no work, so a sender given to the constructor
        is only started here, by the first call that does work. Reports
        written later start it themselves.

        Likewise what an earlier run left to be handed to sessions: the
        times task agents set for themselves that have not come, and the
        notes that were not handed over. Looked for here, in the
        background, and not at startup.
        """
        if self._shut_down:
            return
        if not self._used:
            self._used = True
            self.outbox.kick()
            self.wakes.kick()
        if self._trees_looked_at is not None:
            return
        # Likewise the git worktrees an earlier run left: gone through in the
        # background (it runs git once for every repository a project is in),
        # as a task the task agents stop with the rest when the host shuts down.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return  # called with no loop running: looked at the next time the host is used with one
        self._trees_looked_at = self.task_agents._tree_task(self._look_at_trees_left())

    async def _look_at_trees_left(self) -> None:
        try:
            found = await self.task_agents.reconcile_trees()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - they stay as they are, and are looked at when the host next starts
            logger.warning("going through the git worktrees an earlier run left failed", exc_info=True)
            return
        if found["removed"] or found["kept"]:
            logger.info(
                "git worktrees an earlier run left: %d removed, %d kept for the user",
                len(found["removed"]), len(found["kept"]),
            )

    # -- what the main dialogue uses --------------------------------------

    async def dispatch(self, text: str, **kwargs: Any) -> Receipt:
        """Hand a sentence to the task brain; see ``TaskBrain.dispatch``."""
        self._first_use()
        return await self.brain.dispatch(text, **kwargs)

    async def query(self, text: str, **kwargs: Any) -> QueryAnswer:
        """Ask the task brain; see ``TaskBrain.query``."""
        self._first_use()
        return await self.brain.query(text, **kwargs)

    async def brief(self, **kwargs: Any) -> list[str]:
        """Return what is under way for a new session; see ``TaskBrain.brief``."""
        self._first_use()
        return await self.brain.brief(**kwargs)

    def start(self) -> None:
        """Begin sending reports an earlier run left unsent, if that has not begun yet.

        Every call that does work does this by itself; this is for a caller
        that only reads.
        """
        self._first_use()

    async def check_turn(self, **kwargs: Any) -> TurnCheck:
        """Check a turn in which she called no task tool; see ``TaskBrain.check_turn``."""
        self._first_use()
        return await self.brain.check_turn(**kwargs)

    # -- what the commission board uses -----------------------------------

    async def abort(self, commission_id: str, *, reason: str = "stopped") -> Commission | None:
        self._first_use()
        return await self.brain.abort(commission_id, reason=reason)

    async def tell(self, commission_id: str, text: str) -> bool:
        self._first_use()
        return await self.brain.tell(commission_id, text)

    async def answer(
        self,
        commission_id: str,
        text: str,
        *,
        choice: str | None = None,
        question_id: str | None = None,
        selected: dict[str, list[str]] | None = None,
    ) -> bool:
        self._first_use()
        return await self.brain.answer(commission_id, text, choice=choice, question_id=question_id, selected=selected)

    async def complete_deferred(self, commission_id: str) -> bool:
        self._first_use()
        return await self.brain.complete_deferred(commission_id)

    async def stop_waiting(self) -> int:
        """Drop what waits to be handed to sessions; returns how many. Part of a stop of everything.

        The notes sessions wrote to one another that were not handed over:
        after a stop none of them starts a turn. What a commission set for
        itself (a time to be woken at) goes with that commission as the
        stop ends it. See ``SessionWakes.stop_all``.
        """
        return await self.wakes.stop_all()

    @contextlib.asynccontextmanager
    async def stopping_everything(self) -> AsyncIterator[None]:
        """Keep sessions from being handed anything while everything is stopped. For the body that stops the commissions.

        What waits is dropped as this is entered. While the commissions
        are being stopped nothing is handed over, and what one of them
        wrote meanwhile (its call was on its way when the stop came) is
        dropped as this is left: none of it starts work after the stop.
        A failure to drop is logged and made up for before anything is
        handed over again (``SessionWakes.stop_all``).
        """
        self.wakes.hold()
        try:
            try:
                await self.wakes.stop_all()
            except Exception as exc:  # noqa: BLE001 - the commissions are stopped all the same
                logger.warning("dropping the notes that waited for sessions failed, to be made up for: %s", exc)
            yield
        finally:
            try:
                await self.wakes.stop_all()
            except Exception as exc:  # noqa: BLE001 - made up for before anything is handed over
                logger.warning("dropping the notes written during the stop failed, to be made up for: %s", exc)
            finally:
                self.wakes.release()

    # -- sessions ---------------------------------------------------------
    #
    # For whoever works with sessions as such (a screen that shows what a
    # commission used; one session asking another something): nothing in
    # this build calls these yet.

    async def usage(self, commission_id: str) -> dict[str, Any] | None:
        """Return what a commission has used, what it may, and what its model's sessions are managed for.

        ``TaskAgentManager.usage_of``, with ``window``: what
        ``ModelCapabilityStore.describe_window`` says of the model the
        commission's session is sent to (``TaskAgentManager.session_model``:
        its window, where a conversation is summed up, what was learned of
        it). None for an unknown commission.
        """
        self._first_use()
        usage = await self.task_agents.usage_of(commission_id)
        if usage is None:
            return None
        return {**usage, "window": await self._window_of(commission_id)}

    async def _window_of(self, commission_id: str) -> dict[str, Any] | None:
        """Return what the model a commission's session is sent to is managed for; None where that cannot be told."""
        try:
            config = await self.task_agents.model_of_commission(commission_id)
            if config and config.get("model"):
                return await self.model_capabilities.describe_window(config.get("base_url"), config.get("model"))
        except Exception:  # noqa: BLE001 - what was used is told all the same
            logger.warning("reading the window of the task agent model failed", exc_info=True)
        return None

    async def fork_session(self, session_id: str, *, name: str | None = None, upto: str | None = None) -> Any:
        """Make a session from another by copying what was said in it; see ``TaskAgentManager.fork_session``."""
        self._first_use()
        return await self.task_agents.fork_session(session_id, name=name, upto=upto)

    async def discard_fork(self, session_id: str) -> bool:
        """Remove what was said in a session that is a copy of another, now. Returns whether it is gone.

        For a copy that was made to ask something and has answered. Only
        a session of kind ``fork``; False while a commission is at work
        in it. Its working directory is the other session's and is not
        touched.
        """
        self._first_use()
        session = await self.ledger.get_session(session_id)
        if session is None or session.kind != "fork":
            return False
        return await self.space_keeper.clear_session(session_id)

    # -- the board of sessions --------------------------------------------
    #
    # For a page that shows the task agents by project and one that sets a
    # new one up. No route leads here yet; whoever adds one calls these.

    async def _task_agent_model_name(self) -> str | None:
        """Return the name of the model task agents use, and nothing else of it."""
        config = await self._task_agent_model()
        name = config.get("model") if config else None
        return str(name) if name else None

    async def board(
        self, project: str | None = None, lanlan_name: str | None = None, known_revision: str | None = None
    ) -> dict[str, Any]:
        """Return the sessions by project, one card each; see ``nekopower/host/board.py``.

        ``project`` leaves one project's, ``lanlan_name`` one character's.
        With ``known_revision``, the revision a board had that the caller
        still holds, ``{"revision", "unchanged": True}`` comes back while
        nothing on it changed. Reads only: no model and no runtime is
        asked, and nothing is started.
        """
        return await self._board.view(project, lanlan_name, known_revision)

    # -- the model each preset's sessions are sent to ------------------------
    #
    # For a page that sets them (``role_models.py``). No route leads here
    # yet; whoever adds one calls these.

    async def role_models(self) -> dict[str, Any]:
        """Return the model setting of every preset, what it comes to, and the slots it can be set to.

        ``{"presets": [...], "slots": [...], "slots_error"}``. A preset
        (the built-in ones first, then the user's templates, as
        ``templates(None)`` lists them; never a copy's, which has no
        setting) is ``{"preset", "label", "source", "valid", "setting",
        "default", "can_inherit", "in_effect"}``: ``setting`` is a slot's
        id, ``"inherit"`` or None (its default); ``default`` is ``"agent"``
        or ``"inherit"``, as its template says; ``can_inherit`` whether it
        can be set to the model of the session that starts it;
        ``in_effect`` is what a new session of it is sent to now
        (``RoleModels.describe``), with why its setting could not be used.
        ``slots`` are the slots of the model panel that declare tool
        calling, in panel order: ``{"slot_id", "name", "source",
        "host_api", "model", "configured", "free", "error_code"}``.
        ``slots_error`` is why they could not be read (None when they
        could). Names and codes only: no address and no key.
        """
        from nekopower.host.role_models import REQUIRED_CAPABILITIES, SlotUnavailable
        from nekopower.host.templates import NOT_FOR_CHILDREN

        self._first_use()
        roles = self.task_agents.role_models
        settings = await roles.store.all()
        presets = []
        for item in await self.templates(None):
            name = item["name"]
            presets.append(
                {
                    "preset": name,
                    "label": item["label"],
                    "source": item["source"],
                    "valid": item["valid"],
                    "setting": settings.get(name),
                    "default": await self.task_agents.model_default_of(name, None),
                    "can_inherit": name not in NOT_FOR_CHILDREN,
                    # By the settings read above: one row says one state of them.
                    "in_effect": await roles.describe(name, settings=settings),
                }
            )
        slots: list[dict[str, Any]] = []
        slots_error = None
        if roles.slots is not None:
            try:
                listed = await roles.slots.list()
            except SlotUnavailable as exc:
                listed, slots_error = [], exc.code
            slots = [
                {
                    "slot_id": item.slot_id,
                    "name": item.name,
                    "source": item.source,
                    "host_api": item.host_api,
                    "model": item.model or None,
                    "configured": item.configured,
                    "free": item.free,
                    "error_code": item.error_code,
                }
                for item in listed
                if set(REQUIRED_CAPABILITIES) <= set(item.capabilities)
            ]
        return {"presets": presets, "slots": slots, "slots_error": slots_error}

    async def set_role_model(self, preset: str, value: str | None) -> dict[str, Any]:
        """Set the model a preset's new sessions are sent to; returns ``role_models``.

        ``value`` is a slot's id (one of the model panel that declares tool
        calling), ``"inherit"`` (the model of the session that starts it;
        not for a preset that no session starts) or None (its default).
        Sessions there are keep the model they were settled on. Raises
        ``ValueError`` with a code: ``unknown_preset`` (not one the
        presets list, or a copy's), ``cannot_inherit``, ``bad_value``,
        ``no_slots`` (there are none to choose from in this process), or
        why the slot will not do (``slot_not_found``,
        ``model_config_unreadable``, ``capability_missing``). Nothing is
        written then; nor when the file of settings is there and cannot be
        read (``OSError``).
        """
        from nekopower.host.role_models import INHERIT, REQUIRED_CAPABILITIES, SlotUnavailable
        from nekopower.host.templates import NOT_FOR_CHILDREN

        self._first_use()
        roles = self.task_agents.role_models
        if not isinstance(preset, str) or preset not in {item["name"] for item in await self.templates(None)}:
            raise ValueError("unknown_preset")
        if value == INHERIT:
            if preset in NOT_FOR_CHILDREN:
                raise ValueError("cannot_inherit")
        elif value is not None:
            if not isinstance(value, str) or not value.strip():
                raise ValueError("bad_value")
            if roles.slots is None:
                raise ValueError("no_slots")
            try:
                listed = await roles.slots.list()
            except SlotUnavailable as exc:
                raise ValueError(exc.code) from None
            found = next((item for item in listed if item.slot_id == value), None)
            if found is None:
                raise ValueError("slot_not_found")
            if not set(REQUIRED_CAPABILITIES) <= set(found.capabilities):
                raise ValueError("capability_missing")
        await roles.store.put(preset, value)
        return await self.role_models()

    async def templates(self, project: str | None = None) -> list[dict[str, Any]]:
        """Return the presets a session can have, for a project or for none; see ``nekopower/host/templates.py``.

        One ``{"name", "label", "description", "avatar", "source",
        "read_only", "writes", "memory", "model", "valid", "error"}``
        each, the built-in ones first. A template file that is wrong is
        listed with ``valid`` False and what is wrong with it.
        """
        listed = await asyncio.to_thread(self.templates_store.listing, project)
        return [item.to_json() for item in listed]

    async def draft_session(
        self, text: str, workdir: str | None = None, lanlan_name: str | None = None
    ) -> dict[str, str]:
        """Fill in the form of a new session from what the user wrote of it; see ``nekopower/host/drafts.py``.

        Returns ``{name, title, preset, duty, description, rules}``, or
        raises ``drafts.DraftError`` with a reason. One call of the
        summary model; nothing is made or written. ``workdir`` is the
        folder chosen for the session, of which names and sizes are read.
        ``lanlan_name`` is whose task agent it is to be: the form is the
        same for every character, and it is taken so that a caller need
        not know that.
        """
        from nekopower.host.drafts import draft_session

        del lanlan_name
        return await draft_session(self._decision_llm, self.templates_store, text, workdir)

    # -- what happened between sessions -----------------------------------
    #
    # For a screen that draws the task agents of a project as a group, with
    # the lines between them, and replays what they did (no route leads
    # here yet). Both only read the ledger; no model is called. See
    # ``nekopower/host/timeline.py`` for the kinds and the cursor.

    async def timeline(
        self,
        project: str | None = None,
        lanlan_name: str | None = None,
        after: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Return a page of what happened among sessions, in the order it happened.

        ``project`` / ``lanlan_name`` keep to one project, one character;
        neither, everything. ``after`` is the ``next`` of the page before,
        or the ``cursor`` of an entry; none starts from the beginning.
        ``limit`` entries at most (``TIMELINE_PAGE_ROWS`` when not given,
        at least one, never more than ``TIMELINE_PAGE_MAX_ROWS``). Returns
        ``{"entries", "next", "more", "latest"}``; see
        ``timeline.read_timeline``. A cursor that is not one raises
        ``ValueError``.
        """
        from config.nekopower_settings import TIMELINE_PAGE_MAX_ROWS, TIMELINE_PAGE_ROWS

        rows = TIMELINE_PAGE_ROWS if limit is None else max(1, min(int(limit), TIMELINE_PAGE_MAX_ROWS))
        page = await self.ledger.timeline(project=project, lanlan_name=lanlan_name, after=after, limit=rows)
        await self._shorten(page["entries"])
        return page

    async def timeline_start(self) -> dict[str, Any]:
        """Return where to begin reading the timeline for what happened lately: ``{"after", "latest", "before", "scene"}``.

        The timeline is handed out forwards from a cursor, and a page that
        opens wants the last stretch of it, not all there ever was.
        ``after`` is a cursor that stands ``CONSOLE_TIMELINE_RECENT_EVENT_ROWS``
        rows of the events and ``CONSOLE_TIMELINE_RECENT_MESSAGE_ROWS``
        messages before the end of what is on record (``""`` when that is
        the beginning: there is nothing before it to leave out);
        ``latest`` stands at the end. Good for any filter: a cursor is a
        place in the two tables, whatever is then kept of what follows it.
        ``before`` is how many entries come before ``after`` (as far as
        ``CONSOLE_TIMELINE_BEFORE_COUNT_MAX``) and ``scene``
        how the sessions stood there (``timeline.read_before``): a page that
        replays what follows knows who was at work already, and whether
        reading from the beginning would bring more than it keeps.
        """
        from config.nekopower_settings import (
            CONSOLE_TIMELINE_RECENT_EVENT_ROWS,
            CONSOLE_TIMELINE_RECENT_MESSAGE_ROWS,
            CONSOLE_TIMELINE_BEFORE_COUNT_MAX,
            CONSOLE_TIMELINE_SCENE_MAX_ROWS,
        )
        from nekopower.host.timeline import cursor, parse_cursor

        latest = (await self.ledger.timeline(project=None, lanlan_name=None, after=None, limit=0))["latest"]
        events, messages = parse_cursor(latest)
        events = max(0, events - CONSOLE_TIMELINE_RECENT_EVENT_ROWS)
        messages = max(0, messages - CONSOLE_TIMELINE_RECENT_MESSAGE_ROWS)
        after = cursor(events, messages) if events or messages else ""
        before = await self.ledger.timeline_before(
            after, limit=CONSOLE_TIMELINE_SCENE_MAX_ROWS, count_most=CONSOLE_TIMELINE_BEFORE_COUNT_MAX
        )
        return {"after": after, "latest": latest, **before}

    async def waits(self, project: str | None = None, lanlan_name: str | None = None) -> dict[str, Any]:
        """Return who waits for whom now: ``{"waits", "more"}``; see ``timeline.read_waits``.

        ``project`` keeps to the waits with one side in that project,
        ``lanlan_name`` to those of one character.
        """
        from config.nekopower_settings import WAITS_MAX_ROWS

        result = await self.ledger.waits(project=project, lanlan_name=lanlan_name, limit=WAITS_MAX_ROWS)
        await self._shorten(result["waits"])
        return result

    @staticmethod
    async def _shorten(items: list[dict[str, Any]]) -> None:
        """Cut the ``text`` of each item to ``TIMELINE_TEXT_MAX_TOKENS``, on a thread of its own.

        Not on the ledger's: counting tokens of a thousand long notes takes
        a good part of a second, and every write would wait for it.
        """
        from config.nekopower_settings import TIMELINE_TEXT_MAX_TOKENS
        from utils.tokenize import truncate_to_tokens

        def shorten() -> None:
            for item in items:
                item["text"] = truncate_to_tokens(item["text"], TIMELINE_TEXT_MAX_TOKENS)

        await asyncio.to_thread(shorten)

    # -- what a page shows of one session, of the commissions, of the MCP tools --
    #
    # For the console (``app/agent_server/api_console.py``). All of them
    # only read, the two setters apart; no model is called.

    async def transcript(
        self, session_id: str, before: int | None = None, limit: int | None = None, at: int | None = None
    ) -> dict[str, Any] | None:
        """Return a page of what was said in a session, for showing it to the user; None for an unknown session.

        ``{"readable": True, "entries": [...], "before": place | None}``:
        the ``limit`` entries before the place ``before`` in the order they
        were said (the last ones when ``before`` is None), and the place to
        pass for the page before this one (None at the beginning); see
        ``records.read_page`` for an entry. With ``at``, the one entry that
        begins there, within a larger cap. A session the built-in runtime
        keeps no record of comes back as ``{"readable": False, "reason":
        ...}``: ``"dsh"`` when dsh holds it (it keeps what was said itself,
        where the host cannot read it), ``"empty"`` when nothing was said
        in it yet or what was said was removed. Raises ``ValueError`` with
        a code: ``bad_place`` (no entry begins there), ``too_large`` (the
        one entry asked for is too large to show), ``record_unreadable``
        (the record is there and cannot be opened now).
        """
        from config import nekopower_settings as settings
        from nekopower.host import records
        from nekopower.host.paths import DSH_SESSION_NAME, TRANSCRIPT_NAME

        kept = await self.ledger.get_session(session_id)
        if kept is None:
            return None

        def place() -> tuple[Path, bool, bool, bool]:
            session_dir = Path(self.task_agents.session_dir_of(kept))
            path = session_dir / TRANSCRIPT_NAME
            held = False
            try:
                written = path.stat().st_size > 0
            except FileNotFoundError:
                written = False
            except OSError:
                # There, and not to be looked at now: not the same as nothing said.
                written, held = False, True
            return path, written, (session_dir / DSH_SESSION_NAME).is_file(), held

        path, in_builtin, in_dsh, held = await asyncio.to_thread(place)
        if held:
            raise ValueError("record_unreadable")
        if not in_builtin:
            return {"readable": False, "reason": "dsh" if in_dsh else "empty", "entries": [], "before": None}
        try:
            if at is not None:
                entry = await asyncio.to_thread(
                    records.read_whole_entry,
                    path,
                    int(at),
                    entry_tokens=settings.CONSOLE_TRANSCRIPT_WHOLE_ENTRY_MAX_TOKENS,
                    max_bytes=settings.CONSOLE_TRANSCRIPT_WHOLE_ENTRY_MAX_BYTES,
                )
                return {"readable": True, "entries": [entry], "before": None}
            rows = settings.CONSOLE_TRANSCRIPT_PAGE_ENTRIES if limit is None else int(limit)
            page = await asyncio.to_thread(
                records.read_page,
                path,
                None if before is None else int(before),
                max(1, min(rows, settings.CONSOLE_TRANSCRIPT_PAGE_MAX_ENTRIES)),
                entry_tokens=settings.CONSOLE_TRANSCRIPT_ENTRY_MAX_TOKENS,
                scan_bytes=settings.CONSOLE_TRANSCRIPT_SCAN_MAX_BYTES,
            )
        except records.NoSuchPlace:
            raise ValueError("bad_place") from None
        except records.EntryTooLarge:
            raise ValueError("too_large") from None
        except records.RecordUnreadable:
            # There, and not to be opened now: not the same as nothing said.
            raise ValueError("record_unreadable") from None
        except records.RecordError:
            # Gone between the look and the read (cleaned, removed).
            return {"readable": False, "reason": "empty", "entries": [], "before": None}
        return {"readable": True, **page}

    async def search_transcript(self, session_id: str, query: str) -> dict[str, Any] | None:
        """Return where words occur in what was said in a session, for showing it to the user; None for an unknown session.

        ``{"readable": True, "hits": [...], "total", "whole"}`` as
        ``records.search_page`` says, the latest first; a hit is opened
        with ``transcript(session_id, at=hit["at"])``. A session the
        built-in runtime keeps no record of comes back as for
        ``transcript``. The search the task agents have of each other's
        records reads the same file. Raises ``ValueError`` with a code:
        ``bad_query`` (no words to look for), ``record_unreadable``.
        """
        from config import nekopower_settings as settings
        from nekopower.host import records
        from nekopower.host.paths import DSH_SESSION_NAME, TRANSCRIPT_NAME

        if not isinstance(query, str) or not query.split():
            raise ValueError("bad_query")
        kept = await self.ledger.get_session(session_id)
        if kept is None:
            return None

        def place() -> tuple[Path, bool]:
            session_dir = Path(self.task_agents.session_dir_of(kept))
            return session_dir / TRANSCRIPT_NAME, (session_dir / DSH_SESSION_NAME).is_file()

        path, in_dsh = await asyncio.to_thread(place)
        try:
            found = await asyncio.to_thread(
                records.search_page,
                path,
                query,
                hits=settings.CONSOLE_TRANSCRIPT_SEARCH_HITS,
                excerpt_tokens=settings.CONSOLE_TRANSCRIPT_SEARCH_EXCERPT_TOKENS,
                max_bytes=settings.CONSOLE_TRANSCRIPT_SEARCH_MAX_BYTES,
            )
        except records.RecordUnreadable:
            raise ValueError("record_unreadable") from None
        except records.RecordError:
            # No record (nothing said yet, removed), or one dsh keeps where the host cannot read it.
            return {"readable": False, "reason": "dsh" if in_dsh else "empty", "hits": [], "total": 0, "whole": True}
        return {"readable": True, **found}

    async def session_usage(self, session_id: str) -> dict[str, Any] | None:
        """Return what a session used over all its commissions, and what the commission it has in hand may; None for an unknown one.

        ``{"session": {requests, input, output, cache_read, cache_write,
        tokens, context_tokens}, "window": ..., "commission": ...}``:
        ``window`` what sessions of its model are managed for
        (``ModelCapabilityStore.describe_window``; None when its model
        cannot be told), ``commission`` what ``usage`` says of the
        commission it has in hand (``id``, ``used``, ``limit``, its own
        count). That is the one the board shows for it
        (``Ledger.board_rows``): of those under way in the session
        (``UNDER_WAY_STATUSES``), the one at work or waiting for the user
        before one that rests, and that before one that waits for its
        turn; the oldest of several. None for a session that has none in
        hand: one that is over is not held against a limit any more.
        """
        self._first_use()
        # What it has in hand and what that used, read at one moment: one that is done between
        # two reads is not shown with what it used as it stood before.
        found = await self.ledger.usage_in_hand(session_id)
        if found is None:
            return None
        kept, held, latest, standing = found
        in_hand = None
        # The window is the model's: told by the latest commission where none is in hand.
        window = await self._window_of(latest.id) if latest is not None else None
        if held is not None:
            usage = await self.task_agents.usage_view(held, standing, kept)
            in_hand = {
                "id": held.id,
                "title": held.title,
                "status": "resting" if held.resting else held.status,
                "used": usage["used"],
                "limit": usage["limit"],
                "own": usage["commission"],
            }
        return {
            "session": {
                "requests": kept.requests,
                "input": kept.tokens_in,
                "output": kept.tokens_out,
                "cache_read": kept.tokens_cache_read,
                "cache_write": kept.tokens_cache_write,
                "tokens": kept.tokens,
                "context_tokens": kept.context_tokens,
            },
            "window": window,
            "commission": in_hand,
        }

    async def commissions(
        self,
        statuses: tuple[str, ...] | None = None,
        project: str | None = None,
        days: float | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Return the commissions for a page that lists them, the newest first: ``{"commissions", "more"}``.

        Those under way (``UNDER_WAY_STATUSES``; one that is stuck is
        over), and those that last changed within ``days`` days
        (``CONSOLE_COMMISSIONS_DAYS`` when not given; 0 or less: however
        long ago). ``statuses`` keeps to those in one of them; ``project``
        to those of one project (``""`` for those of none). ``limit`` of
        those that are over at most (``CONSOLE_COMMISSIONS_ROWS`` when not
        given, never more than ``CONSOLE_COMMISSIONS_MAX_ROWS``), besides
        those under way, which are all listed; ``more`` says some were left
        out, or that so many were looked through for a project that the
        look stopped (``CONSOLE_COMMISSIONS_SCAN_ROWS``). A status
        is the one a commission is shown with: one that rests is among
        those that rest and not among those at work. Each is ``{"id",
        "title", "status", "reason", "result", "source", "route",
        "project", "lanlan_name", "parent_id",
        "session_id", "session", "runtime", "plugin_id", "entry_id",
        "created_at", "started_at", "ended_at", "updated_at", "usage",
        "ask"}``: ``status`` says ``resting`` for one that rests;
        ``session`` is ``{"name", "kind", "preset", "title"}`` of the
        session it is worked on in (None for one a plugin does, or not
        handed over yet); ``usage`` its requests and tokens, those of the
        task agents it started counted in; ``ask`` what it asks the user
        while it waits for an answer, whole. Texts are cut to
        ``TIMELINE_TEXT_MAX_TOKENS``.
        """
        import time

        from config import nekopower_settings as settings
        from nekopower.host.ledger import UNDER_WAY_STATUSES

        rows = settings.CONSOLE_COMMISSIONS_ROWS if limit is None else int(limit)
        rows = max(1, min(rows, settings.CONSOLE_COMMISSIONS_MAX_ROWS))
        span = settings.CONSOLE_COMMISSIONS_DAYS if days is None else float(days)
        wanted = None if statuses is None else frozenset(statuses)

        def kept(item: Commission) -> bool:
            # By what the page shows it as: one that rests is not among those "at work".
            shown = "resting" if item.resting else item.status
            if wanted is not None and shown not in wanted:
                return False
            return project is None or (item.project or "") == project

        # Queued, at work, waiting for the user. One that is stuck is over until somebody takes
        # it up again: it is listed like the others that are over, while it is recent enough.
        under_way = await self.ledger.select(statuses=tuple(UNDER_WAY_STATUSES))
        criteria: dict[str, Any] = {}
        if span > 0:
            criteria["updated_since"] = time.time() - span * 86400.0
        if wanted is not None:
            # What the ledger can keep to by itself. It knows of none that "rests": those are at
            # work as it has it, and so among the ones under way, which were all read above and
            # are told apart here.
            criteria["statuses"] = tuple(sorted(wanted))
        # The ledger cuts off before anything is looked at here. So it is asked for as many as
        # are wanted only when nothing is left to look at; kept to a project, they are looked
        # through first and cut afterwards: one of a project with hundreds of another's filed
        # after it is found all the same.
        looked_at_here = project is not None
        reach = settings.CONSOLE_COMMISSIONS_SCAN_ROWS if looked_at_here else rows + 1 + len(under_way)
        read = await self.ledger.select(limit=reach, **criteria)
        # Read a moment apart: of one that is in both, what the later read says holds, so one
        # that was answered or ended between the two is not listed as it stood before.
        latest = {item.id: item for item in [*under_way, *read]}
        ordered = sorted((item for item in latest.values() if kept(item)), key=lambda item: item.created_at, reverse=True)
        # Those under way are all listed, however many and however old: one that waits for the
        # user is answered from this list. The limit is for those that are over. One more of
        # them than asked for was read: what is left over says there is more. So does a look
        # that went as far as it may: what lies beyond it was not looked at.
        over = [item for item in ordered if item.status not in UNDER_WAY_STATUSES]
        more = len(over) > rows or (looked_at_here and len(read) >= reach)
        left_out = {item.id for item in over[rows:]}
        queued_most = settings.CONSOLE_COMMISSIONS_QUEUED_MAX
        if queued_most is not None:
            # Those waiting for their turn, the newest first, as many as may be listed.
            queued = [item for item in ordered if item.status == "queued"]
            more = more or len(queued) > queued_most
            left_out |= {item.id for item in queued[max(0, queued_most) :]}
        listed = [item for item in ordered if item.id not in left_out]

        sessions = await self.ledger.get_sessions(item.session_id for item in listed if item.session_id)

        def session_of(item: Commission) -> dict[str, Any] | None:
            session = sessions.get(item.session_id or "")
            if session is None:
                return None
            return {"name": session.name, "kind": session.kind, "preset": session.preset, "title": session.title}

        out = [
            {
                "id": item.id,
                "title": item.title,
                "status": "resting" if item.resting else item.status,
                "reason": item.status_reason or "",
                "result": item.result_summary or "",
                "source": item.source,
                "route": item.route,
                "project": item.project,
                "lanlan_name": item.lanlan_name,
                "parent_id": item.parent_id,
                "session_id": item.session_id,
                "session": session_of(item),
                "runtime": item.runtime,
                "plugin_id": item.plugin_id,
                "entry_id": item.entry_id,
                "created_at": item.created_at,
                "started_at": item.started_at,
                "ended_at": item.ended_at,
                "updated_at": item.updated_at,
                "usage": {"requests": item.requests, "tokens": item.tokens},
                "ask": item.question() if item.status == "waiting_user" else None,
            }
            for item in listed
        ]

        def shorten() -> None:
            from utils.tokenize import truncate_to_tokens

            for row in out:
                for name in ("title", "reason", "result"):
                    row[name] = truncate_to_tokens(row[name], settings.TIMELINE_TEXT_MAX_TOKENS)

        await asyncio.to_thread(shorten)
        return {"commissions": out, "more": more}

    async def mcp_tools(self) -> dict[str, Any]:
        """Return the MCP tools there are by server, with how each server is set, for a page that shows them.

        ``{"adapter", "plugins_on", "category", "servers", "unreadable"}``.
        ``adapter`` is how the plugin that holds the connections stands
        (``mcp_adapter``): ``"running"``, ``"stopped"`` (installed, not
        running), or ``"missing"`` (not installed, or the plugin server
        did not answer). ``plugins_on`` whether the plugins are switched on
        (off, no tool is listed). ``category`` is ``{"mode", "auto"}`` of
        the setting all servers share. Each server is ``{"server", "mode",
        "effective", "tools"}``: ``mode`` what was set for it (``"auto"``:
        nothing of its own, it goes by the shared setting), ``effective``
        what that comes to, ``tools`` its tools as ``{"id", "name",
        "description"}``. A server that has a setting and no tool now is
        listed too, so that the setting can be seen and changed.
        ``unreadable`` says the permission file cannot be read: every mode
        reads as forbidden then, and none can be set.
        """
        from nekopower.host.capabilities import KIND_MCP_TOOL, MCP_ADAPTER_PLUGIN_ID
        from nekopower.host.permissions import AUTO_POLICY, MCP_SERVER_CATEGORY

        # A plugin server that does not answer lists nothing: the adapter reads as missing then.
        found = next((item for item in await self.catalog.installed() if item.get("id") == MCP_ADAPTER_PLUGIN_ID), None)
        adapter = "missing" if found is None else "running" if found.get("status") == "running" else "stopped"
        tools: dict[str, list[dict[str, Any]]] = {}
        for item in await self.registry.list():
            if item.kind != KIND_MCP_TOOL:
                continue
            # An MCP tool is guarded by its server's setting: the key is the server's name.
            server = str(item.permission_key).strip()
            name = item.id.split(":", 2)[-1] if item.id.count(":") >= 2 else item.title
            tools.setdefault(server, []).append({"id": item.id, "name": name, "description": item.description})
        snapshot = await self.permission_store.snapshot()
        configured = {str(name): mode for name, mode in snapshot["mcp_servers"].items() if name != "*"}
        servers = []
        for server in sorted({*tools, *configured}, key=str.casefold):
            servers.append(
                {
                    "server": server,
                    "mode": configured.get(server, "auto"),
                    "effective": await self.permission_store.mode(MCP_SERVER_CATEGORY, server),
                    "tools": tools.get(server, []),
                }
            )
        return {
            "adapter": adapter,
            "plugins_on": self.registry.plugins_enabled(),
            "category": {
                "mode": snapshot["categories"].get(MCP_SERVER_CATEGORY, "auto"),
                "auto": AUTO_POLICY[MCP_SERVER_CATEGORY],
            },
            "servers": servers,
            "unreadable": bool(snapshot.get("unreadable")),
        }

    async def usage_limit(self) -> dict[str, Any]:
        """Return how much the work on one commission may use before the user is asked, as a page shows it.

        ``{"tokens", "set", "default", "min", "max"}``: ``tokens`` what
        holds now (None for no limit), ``set`` what the user set (None
        while the default holds), ``default`` the setting of the build,
        ``min`` and ``max`` what it can be set within.
        """
        from config.nekopower_settings import TASK_AGENT_USAGE_LIMIT_TOKENS
        from nekopower.host.usage_limit import bounds

        chosen = await self.usage_limit_store.get()
        default = int(TASK_AGENT_USAGE_LIMIT_TOKENS or 0)
        least, most = bounds()
        return {
            "tokens": chosen if chosen is not None else (default or None),
            "set": chosen,
            "default": default or None,
            "min": least,
            "max": most,
        }

    async def set_usage_limit(self, tokens: int | None) -> dict[str, Any]:
        """Set how much one commission may use before the user is asked; None goes back to the default.

        Returns ``usage_limit``. Holds from the next model request on, for
        commissions under way as well; what the user allowed one of them
        beyond the limit stays. Raises ``ValueError`` (``out_of_range``)
        for what is no whole number within the bounds, and nothing is
        written; ``OSError`` when its file cannot be written, and the
        limit is what it was.
        """
        await self.usage_limit_store.put(tokens)
        return await self.usage_limit()

    async def archive_project(self, project: str) -> dict[str, Any]:
        """Put a project away into the archive, as the user asks; returns it as a page shows it.

        What the host does by itself for a project nothing was commissioned
        for in a long while, done now, and without the summary a model
        writes then: no model is called. Nothing is removed: its rules,
        entries and reviews go with it, its sessions stay in the ledger,
        and a commission that names it later brings it back. Raises
        ``LookupError`` for a project that is not there (or is archived
        already), ``ProjectUnreadable`` for one whose record cannot be
        read, ``ProjectBusy`` while something is at work in it or a
        commission was filed under it meanwhile, ``ProjectNotMoved`` when
        its directory could not be moved (a file in it is held open).
        """
        if self._shut_down:
            raise HostShuttingDown("the host is shutting down")
        # Not while its working directory is changed or a task agent is made for it.
        async with self._project_lock:
            # The one commissions of no project are filed under is not a project to put away.
            kept, readable = (None, True) if project == DEFAULT_PROJECT else await self.space.read_project(project)
            if kept is None:
                raise LookupError(project)
            if not readable:
                raise ProjectUnreadable(project)
            busy = await self._project_busy(project)
            if busy is not None:
                # (Why, as for a project that is to work somewhere else: ``set_project_workdir``.)
                raise ProjectBusy(busy)
            # Looked at again where it is moved, under the lock commissions are filed and taken
            # up under: one filed since the look above leaves it where it is, and so does one
            # taken up again since (that one was counted before; it shows in when the project
            # was last in use).
            archived = await self.space.archive_project(
                project, commissions=kept.commissions, unless_touched_since=kept.last_commission_at
            )
            if archived is None:
                again, readable = await self.space.read_project(project)
                if again is None:
                    # Put away meanwhile by the host itself, which does not wait for this lock.
                    raise LookupError(project)
                if not readable:
                    raise ProjectUnreadable(project)
                if again.commissions != kept.commissions or again.last_commission_at > kept.last_commission_at:
                    raise ProjectBusy("at_work")
                # Its directory could not be moved (on Windows, while a file in it is held open).
                raise ProjectNotMoved(project)
            return archived.to_json()

    # -- dsh --------------------------------------------------------------
    #
    # For a page about the runtimes (there is none yet, and no route leads
    # here): what it shows and what its buttons do.

    async def dsh_status(self) -> dict[str, Any]:
        """Return what there is to show of dsh.

        ``DshInstallation.status`` (whether it is installed, which version,
        whether a download is under way, which runtime the user wants), and
        of this host: ``sessions``, how many commissions are in dsh right
        now, and ``fallback_reason``, why the last commission that was to
        run in dsh ran in the built-in runtime instead, if one did.
        """
        status = await asyncio.to_thread(self.dsh.status)
        status["sessions"] = len(self.task_agents.sessions_in(RUNTIME_DSH))
        status["fallback_reason"] = self.task_agents.dsh_error
        return status

    async def install_dsh(self, on_progress: Any = None) -> dict[str, Any]:
        """Download dsh and install it; returns ``dsh_status``. Raises ``DshUnavailable`` with the reason.

        ``on_progress`` is called with the bytes fetched so far and the size
        of the file. With the version in use installed already nothing is
        done. Otherwise dsh is ended first (another version of it may be
        running, from before the version in use changed), which a commission
        that is in dsh right now forbids: its program cannot be replaced
        while it runs.
        """
        if not await asyncio.to_thread(self.dsh.installed):
            await self._with_dsh_stopped(lambda: self.dsh.install(on_progress))
        return await self.dsh_status()

    async def uninstall_dsh(self, *, purge: bool = False) -> dict[str, Any]:
        """Remove dsh; returns ``dsh_status``. Raises ``DshUnavailable`` while a commission is in dsh.

        Its program goes. What dsh kept (what was said in its sessions, the
        plugins the user added) stays unless ``purge`` is set: installed
        again, commissions that began in dsh go on there. Without dsh they
        start over in the built-in runtime when they are taken up again.
        """
        await self._with_dsh_stopped(lambda: self.dsh.uninstall(purge=purge))
        return await self.dsh_status()

    async def set_dsh_preference(self, preference: str) -> dict[str, Any]:
        """Set which runtime new commissions run in: ``auto`` (dsh when installed) or ``builtin``.

        Returns ``dsh_status``; raises ValueError for anything else.
        Commissions that are under way stay where they are.
        """
        await asyncio.to_thread(self.dsh.set_preference, preference)
        return await self.dsh_status()

    async def dsh_plugins(self) -> list[dict[str, Any]]:
        """Return the plugins the user added to dsh; see ``DshInstallation.plugins``."""
        return await asyncio.to_thread(self.dsh.plugins)

    async def _with_dsh_stopped(self, work: Any) -> None:
        """End dsh and await ``work()``; no commission starts dsh before it is done."""
        if not await self.task_agents.stop_dsh(work):
            from nekopower.dsh import DshUnavailable

            raise DshUnavailable("dsh is in use: a commission is running in it")

    # -- settings a page shows --------------------------------------------
    #
    # For the page about the runtime (``app/agent_server/api_console.py``).

    async def model_window(self) -> dict[str, Any] | None:
        """Return what sessions of the task agent model are managed for; None while no model is configured.

        ``ModelCapabilityStore.describe_window`` of the model as it is
        configured now, with ``model``: its name.
        """
        config = await self._task_agent_model()
        if not config or not config.get("model"):
            return None
        described = await self.model_capabilities.describe_window(config.get("base_url"), config.get("model"))
        return {
            "model": str(config.get("model")),
            "id": _model_identity(config.get("base_url"), config.get("model")),
            **described,
        }

    async def set_model_window(self, **changes: Any) -> dict[str, Any]:
        """Change what sessions of the task agent model are managed for; returns ``model_window``.

        ``context_window`` and ``compact_at`` in tokens, ``idle_compact_after``
        in seconds; None for one of them goes back to the setting, and one
        that is not given stays as it is. ``idle_compact_on`` (a bool)
        switches summing up idle sessions on or off. Raises ``LookupError``
        while no model is configured, ``ValueError`` for a value the store
        refuses (nothing is changed then) and ``TypeError`` for a name that
        is none of the four. With ``expected_model`` (the ``id`` of the
        model the page read the settings of, from ``model_window``),
        raises ``ModelChanged`` when the model configured now is another,
        or one of the same name at another address: nothing is written.
        """
        expected_model = changes.pop("expected_model", None)
        unknown = sorted(set(changes) - {"context_window", "compact_at", "idle_compact_after", "idle_compact_on"})
        if unknown:
            raise TypeError(f"unknown window setting: {', '.join(unknown)}")
        config = await self._task_agent_model()
        if not config or not config.get("model"):
            raise LookupError("no task agent model is configured")
        base_url, model = config.get("base_url"), config.get("model")
        if expected_model is not None and _model_identity(base_url, model) != expected_model:
            # The page shows the settings of one model; they are not written to another,
            # nor to one of the same name at another address.
            raise ModelChanged(str(model))
        store = self.model_capabilities
        async with self._model_window_lock:
            return await self._write_model_window(store, base_url, model, changes)

    async def _write_model_window(self, store: Any, base_url: Any, model: Any, changes: dict[str, Any]) -> dict[str, Any]:
        """Write what ``set_model_window`` was given; all of it, or none of it."""
        before = await store.describe_window(base_url, model)
        # The window last: setting it forgets what the user was told about the model, which
        # going back cannot bring back. By then the others were taken, and a window that
        # will not do is refused before anything of it is written.
        setters = (
            ("compact_at", store.set_compact_at, "compact_at_set"),
            ("idle_compact_after", store.set_idle_compact_after, "idle_compact_after_set"),
            ("idle_compact_on", store.set_idle_compact_on, "idle_compact_on"),
            ("context_window", store.set_context_window, "set"),
        )
        done: list[tuple[Any, Any]] = []
        try:
            for name, setter, was in setters:
                if name in changes:
                    await setter(base_url, model, changes[name])
                    done.append((setter, before.get(was)))
        except ValueError:
            # One page saves them together: none of them is to stay changed.
            for setter, value in reversed(done):
                await setter(base_url, model, value)
            raise
        window = await self.model_window()
        assert window is not None
        return window

    async def _project_busy(self, project: str) -> str | None:
        """Return why a project's working directory cannot be changed now, or None when it can.

        ``"at_work"`` while a session of the project is open in a runtime
        with a run under way, waiting for the user or being summed up, or
        has a commission that is queued, at work or waiting: what is under
        way goes on where it began. ``"tree_not_landed"`` while a git
        worktree of it holds work that waits for the user to decide on:
        it is landed where it was made from. Whoever adds another way for a project
        to be in the middle of something (a session writing in a copy of
        the directory, a copy not brought back yet) adds it here.
        """
        # Of every character: the directory is the project's, not one character's.
        rows = await self.ledger.find_sessions(project=project, limit=500, commissions_each=0)
        if any(row.session.status in _SESSION_AT_WORK for row in rows):
            return "at_work"
        # Every commission under way, not the last few of each session: one that waits may be old.
        sessions = {row.session.id for row in rows}
        under_way = await self.ledger.select(statuses=("queued", "working", "waiting_user"))
        if any(commission.project == project or commission.session_id in sessions for commission in under_way):
            return "at_work"
        if await self.ledger.trees_pending(project):
            # A git worktree of it holds work that was not landed in its directory:
            # landed, discarded or left to the user first (``worktrees``).
            return "tree_not_landed"
        return None

    async def working_directory(self, workdir: Any) -> Path:
        """Return a directory task agents may be given to work in; see ``_working_directory``."""
        return await self._working_directory(workdir)

    async def _working_directory(self, workdir: Any) -> Path:
        """Return a directory the user named for task agents to work in; ``ValueError`` with a code for one that will not do.

        ``not_absolute`` for what is not a whole path, ``not_a_directory``
        for one that is not there, ``host_directory`` for where the host
        keeps its own things (the ledger, the permissions, the task agent
        space), anything inside that, and anything that holds it.
        """
        from nekopower.runtime.permissions import is_inside

        if not isinstance(workdir, str) or not workdir.strip() or "\0" in workdir or not os.path.isabs(workdir):
            raise ValueError("not_absolute")
        wanted = Path(workdir)

        def looked_at() -> tuple[bool, Path, Path]:
            return wanted.is_dir(), Path(os.path.realpath(wanted)), Path(os.path.realpath(self.paths.root()))

        try:
            is_directory, actual, own = await asyncio.to_thread(looked_at)
        except (OSError, ValueError):
            # A name the system will not look at is no directory.
            raise ValueError("not_a_directory") from None
        if not is_directory:
            raise ValueError("not_a_directory")
        if is_inside(actual, own) or is_inside(own, actual):
            raise ValueError("host_directory")
        return wanted

    async def open_session(
        self,
        lanlan_name: str,
        name: str,
        *,
        preset: str = WORKING_PRESET,
        title: str = "",
        duty: str = "",
        description: str = "",
        workdir: str | None = None,
        rules: str = "",
    ) -> dict[str, Any]:
        """Make a task agent for something lasting, as the user asks: a new project and its first session.

        ``name`` is the project's and the session's; ``preset`` what kind
        of task agent it is (one of ``templates()`` that is valid): a
        member that does the project's work itself unless one is named
        (``orchestrator.WORKING_PRESET``), and the project's leader, which
        only hands work out, with the leader's preset;
        ``title`` and ``duty`` go on its card and ``description`` on the
        project, as the user's own words where the user wrote some, which
        nothing writes over (a line left empty is nobody's, and the first
        review fills it in);
        ``workdir`` is where it works (as for ``set_project_workdir``),
        the directory the host gives it when none is named; ``rules`` are
        written as the project's rules when there are some. Nothing is
        started: it has no commission until one is filed under the project.

        Returns ``{"project": ..., "session_id": ...}``. Raises
        ``ValueError`` with a code: ``no_character``, ``bad_name`` (no name
        of a directory, or the name commissions without a project are filed
        under), ``name_taken`` (a project of the name is there, archived
        ones included), ``bad_preset``, or one of the directory's.
        """
        from nekopower.host.paths import safe_segment
        from nekopower.host.space import normalize_name

        if not isinstance(lanlan_name, str) or not lanlan_name.strip():
            raise ValueError("no_character")
        # A name that begins with ``_`` is the host's own (``DEFAULT_PROJECT`` is one).
        if not isinstance(name, str) or not name.strip() or name.strip().startswith("_"):
            raise ValueError("bad_name")
        name = name.strip()
        try:
            safe_segment(name, "project")
        except ValueError:
            raise ValueError("bad_name") from None
        if not any(item["valid"] and item["name"] == preset for item in await self.templates(None)):
            raise ValueError("bad_preset")
        wanted = await self._working_directory(workdir) if workdir else None
        # One at a time: of two asked for at once under one name, the second finds the name taken.
        # The same lock as changing where a project works: the record of the project and its
        # session are written here one after the other, and no change comes between the two.
        async with self._project_lock:
            # Among the archived ones as well: commissions are filed by the name, and two of a name would be one.
            # Compared as the space compares names when it files a commission (``normalize_name``).
            if any(normalize_name(project.name) == normalize_name(name) for project in await self.space.list_projects()):
                raise ValueError("name_taken")
            project = await self.space.touch_project(name, workdir=str(wanted) if wanted else None, count=False)
            if description.strip():
                project = await self.space.describe_project(name, description, by="user") or project
            if rules.strip():
                await self.space.write_rules(rules, name)
            card = {
                "workdir": str(wanted) if wanted else None, "preset": preset,
                # The user's where the user wrote something. A line left empty in the form was not
                # emptied by the user: it is nobody's, and the first review fills it in.
                "title": title, "title_by": "user" if title.strip() else None,
                "duty": duty, "duty_by": "user" if duty.strip() else None,
            }
            # (The preset of a project's leader: a task agent the user makes with it is one.)
            if preset == PRESET_OF_KIND["project"]:
                # The project's leader: its commissions come to it, and it hands them out.
                session, _made = await self.task_agents.families.make_leader(lanlan_name.strip(), name, **card)
            else:
                # A member: it does the project's work itself.
                session = await self.ledger.create_session(
                    name=name, lanlan_name=lanlan_name.strip(), project=name, kind="line", **card
                )
        # The model it is sent to, by its preset, as it is made.
        await self.task_agents.settle_model(session)
        return {"project": project.to_json(), "session_id": session.id}

    async def open_member(
        self, lanlan_name: str, project: str, *, preset: str = WORKING_PRESET, title: str = "", duty: str = ""
    ) -> dict[str, Any]:
        """Add a task agent to a project that is there, as the user asks: a member that does work of the project.

        ``preset`` is what kind it is (one of ``templates()`` that is
        valid, and not the leader's: a project gets its leader with
        ``derive_leader``); ``title`` and ``duty`` go on its card, as the
        user's words where the user wrote some. It works where the
        project does. One that can change files, added beside a member
        that works in the project's directory, has a git worktree made
        for each of its commissions where one can be made, so that the
        two do not wait for each other (``seat``): told by the host
        here, and not for whoever calls this to work out. Nothing is
        started: it takes commissions that name it, or that its leader
        hands it.

        Returns ``{"session_id", "seat"}``. Raises ``ValueError`` with a
        code (``no_character``, ``bad_preset``, and ``too_many_members``
        for a project that has as many members that take work as it may,
        ``PROJECT_MEMBERS_MAX``) and ``LookupError`` for a project that
        is not there (or is archived).
        """
        if not isinstance(lanlan_name, str) or not lanlan_name.strip():
            raise ValueError("no_character")
        if preset == PRESET_OF_KIND["project"] or not any(
            item["valid"] and item["name"] == preset for item in await self.templates(project)
        ):
            raise ValueError("bad_preset")
        from nekopower.host.templates import model_name

        async with self._project_lock:
            known = {item.name: item for item in await self.space.list_projects(include_archived=False)}
            if project not in known or project == DEFAULT_PROJECT:
                raise LookupError(project)
            from config.nekopower_settings import PROJECT_MEMBERS_MAX

            taking = [
                member
                for member in await self.ledger.members(lanlan_name.strip(), project)
                if await self.ledger.session_alive(member)
            ]
            if len(taking) >= PROJECT_MEMBERS_MAX:
                # As many as its leader is told of, and may open itself (``TaskAgentManager._hand_over``).
                raise ValueError("too_many_members")
            resolved = await asyncio.to_thread(self.task_agents.templates.resolve, preset, project)
            seat = await self.task_agents.seat_of_new_member(project) if resolved.changes_files else ""
            session = await self.ledger.create_session(
                name=await self.ledger.member_name(
                    lanlan_name.strip(), project, f"{project}/{model_name(preset)}"
                ),
                lanlan_name=lanlan_name.strip(),
                project=project,
                kind="line",
                workdir=known[project].workdir,
                preset=preset,
                seat=seat,
                title=title, title_by="user" if title.strip() else None,
                duty=duty, duty_by="user" if duty.strip() else None,
            )
        await self.task_agents.settle_model(session)
        return {"session_id": session.id, "seat": session.seat}

    async def worktrees(self, project: str | None = None, lanlan_name: str | None = None) -> list[dict[str, Any]]:
        """Return the git worktrees task agents work in, or left for the user to decide on, the oldest first.

        Of one project, or one character, when named. Each as
        ``ledger.Tree.to_json`` has it (``state`` is one of
        ``ledger.TREE_STATES``, ``reason`` one of
        ``tree_work.TREE_REASONS`` or empty), with ``title`` (its
        commission's) and ``session_name``. Those whose ``state`` is
        ``held``, ``unlanded`` or ``kept`` are the user's to decide on
        (``land_worktree``, ``discard_worktree``, ``keep_worktree``).
        """
        self._first_use()
        return await self.task_agents.trees_view(project=project, lanlan_name=lanlan_name)

    async def land_worktree(self, tree_id: str, *, landing: Any = None) -> dict[str, Any]:
        """Merge what a git worktree holds into its project's directory (or deliver via ``landing``), as the user asks; see ``TreeWork.land_tree``."""
        self._first_use()
        if landing is None:
            return await self.task_agents.land_tree(tree_id)
        return await self.task_agents.land_tree(tree_id, landing=landing)

    async def discard_worktree(self, tree_id: str) -> dict[str, Any]:
        """Remove a git worktree with what it holds, as the user asks; see ``TreeWork.discard_tree``."""
        self._first_use()
        return await self.task_agents.discard_tree(tree_id)

    async def keep_worktree(self, tree_id: str) -> dict[str, Any]:
        """Leave a git worktree to the user, who sees to it personally; see ``TreeWork.keep_tree``."""
        self._first_use()
        return await self.task_agents.keep_tree(tree_id)

    async def landing_settings(self) -> dict[str, Any]:
        """Return what the user set about landing, with how many git worktrees a project may have.

        ``{"mode": "auto" | "ask", "inspect": bool, "beside": "ask" | "open" | "queue",
        "worktrees_per_project": int}`` (``landing_settings.py``). The last is no setting of the user's:
        it is for a page to say when a project has as many as it may
        (those the user sees to personally count, ``worktrees()`` lists
        them), which is when its next task agent works in the project's
        directory in its turn and a team is not started.
        """
        from config.nekopower_settings import WORKTREES_PER_PROJECT

        return {**await self.task_agents.landing.get(), "worktrees_per_project": WORKTREES_PER_PROJECT}

    async def set_landing_settings(
        self, *, mode: Any = None, inspect: Any = None, beside: Any = None
    ) -> dict[str, Any]:
        """Set what is given of the landing settings and return them as they stand.

        ``mode``: ``auto`` (what a task agent changed in its git
        worktree is merged into the project's directory when its
        commission is done) or ``ask`` (it waits there for the user to
        say so); ``inspect``: whether an inspector looks at what was
        changed before every landing; ``beside``: what becomes of work
        that waits for a member busy with something else, in a project
        without a leader: ``ask`` (the user is asked whether another
        member is to do it in a git worktree of its own), ``open``
        (another does, with nobody asked) or ``queue`` (it waits).
        Raises ``ValueError("bad_mode")``, ``ValueError("bad_inspect")``
        or ``ValueError("bad_beside")``.
        """
        from config.nekopower_settings import WORKTREES_PER_PROJECT

        stands = await self.task_agents.landing.put(mode=mode, inspect=inspect, beside=beside)
        return {**stands, "worktrees_per_project": WORKTREES_PER_PROJECT}

    async def derive_leader(self, project: str, lanlan_name: str) -> dict[str, Any]:
        """Give a project a leader for one character, as the user asks; returns ``{"session_id", "made"}``.

        From then on the project's commissions come to the leader, but
        for those that plainly go on with a member's own work
        (``orchestrator.settle``); it hands them out and does none of the
        work. The task agents the project has are its members. It is told where the project stands
        with the first commission that opens in it, from what is on
        record: no model is asked. A project that has a leader keeps it
        (``made`` is False). Raises ``ValueError("no_character")``, and
        ``LookupError`` for a project that is not there (or is archived).
        """
        if not isinstance(lanlan_name, str) or not lanlan_name.strip():
            raise ValueError("no_character")
        # With the lock a project's record and its sessions are written under: none is made
        # between a look at the project and the write of where it works.
        async with self._project_lock:
            session, made = await self.task_agents.families.derive_leader(lanlan_name.strip(), project)
        if made:
            # The model it is sent to, by its preset, as it is made.
            await self.task_agents.settle_model(session)
        return {"session_id": session.id, "made": made}

    async def set_project_workdir(self, project: str, workdir: str) -> dict[str, Any]:
        """Change where the task agents of a project work, as the user asks; returns the project as a page shows it.

        ``workdir`` is a directory that is there, given whole (not relative
        to anything), and not one the host keeps its own things in. The
        project's record and the sessions of the project that are its own
        (not the ones a task agent started, which work where they were
        sent) are written: the next commission starts there, and is told
        that the directory is another. Nothing is moved; what was in the
        old directory stays there.

        Raises ``LookupError`` for a project that is not there (or is
        archived), ``ValueError`` with a code for a directory that will not
        do (``not_absolute``, ``not_a_directory``, ``host_directory``),
        ``ProjectUnreadable`` for a project whose record cannot be read,
        and ``ProjectBusy`` while something is at work in the project.
        """
        wanted = await self._working_directory(workdir)
        # Carried through once begun, also when whoever asked for it goes away meanwhile: its two
        # writes are both made or both undone, with the lock held until then.
        # None begins once the host is shutting down: those under way are waited for before the
        # ledger is closed (``_settle_changes``), and one begun after that look would not be.
        if self._shut_down:
            raise HostShuttingDown("the host is shutting down")
        change = asyncio.ensure_future(self._change_project_workdir(project, wanted))
        self._changes.add(change)
        change.add_done_callback(self._changes.discard)
        return await asyncio.shield(change)

    async def _change_project_workdir(self, project: str, wanted: Path) -> dict[str, Any]:
        """Write what ``set_project_workdir`` was asked for; see there."""
        # One change at a time, and none while a task agent is being made for the user: the
        # sessions and the project's record end up saying the same directory. (And none of its
        # worktrees the user sees to personally is landed while it is half way.)
        async with self._project_lock, self.task_agents.project_changing(project):
            # Before anything is moved: a record that cannot be read cannot be written either
            # (``_project_to_change`` refuses), and the sessions would be moved for nothing.
            kept, readable = await self.space.read_project(project)
            if kept is None:
                raise LookupError(project)
            if not readable:
                raise ProjectUnreadable(project)
            busy = await self._project_busy(project)
            if busy is not None:
                raise ProjectBusy(busy)
            # The project's own sessions of every character first, looked at once more and written in
            # one go: each starts from the directory it keeps, so a commission that begins from here
            # on begins in the new one, and one that began since the look above stops the change.
            # (Where its leaders work, of every character: a member one of them handed work to works there.)
            led = await self.ledger.find_sessions(project=project, kind="project", commissions_each=0)
            leaders = [(row.session.id, row.session.workdir) for row in led]
            before = await self.ledger.move_project(
                project, str(wanted), moves=self._moves_with(project, kept.workdir, leaders)
            )
            if before is None:
                # Something began in it since the look above: said by what it is.
                raise ProjectBusy("tree_not_landed" if await self.ledger.trees_pending(project) else "at_work")
            # The record is written second: not written, the sessions go back, so that nothing is half changed.
            try:
                changed = await self.space.set_project_workdir(project, str(wanted))
            except BaseException:
                await self._undo_project_move(project, before, str(wanted))
                raise
            if changed is None:
                await self._undo_project_move(project, before, str(wanted))
                raise LookupError(project)
            return changed.to_json()

    def _moves_with(
        self, project: str, was: str | None, leaders: Sequence[tuple[str, str | None]] = ()
    ) -> Callable[[str, str | None], bool]:
        """Return what says whether a member of a project moves when the project is given another directory.

        Given the member's id and where it works. It moves when it works
        where the project did (``was``), in no directory of its own yet,
        or in the one the host gave it as a session of this project,
        having none to give it of the project's: its own, or that of
        one of the project's ``leaders`` (each with where it works on
        record), which is where a member works that a leader handed a
        commission to while the project had no directory (and the
        leader moves). Where the leader works on record counts as well
        as what the host would give it now: the two differ once the
        data root was moved by hand. It stays when it works
        anywhere else: it was a session of its own before it became the
        project's (a family is made of such), and its files are there.
        """
        import os

        def same(first: str, second: str) -> bool:
            return os.path.normcase(os.path.normpath(first)) == os.path.normcase(os.path.normpath(second))

        def moves(session_id: str, workdir: str | None) -> bool:
            if not workdir or (was and same(workdir, was)):
                return True
            if any(place and same(workdir, place) for _leader, place in leaders):
                return True
            for given_to in (session_id, *(leader for leader, _place in leaders)):
                try:
                    if same(workdir, str(self.paths.work_dir(given_to, project))):
                        return True
                except ValueError:
                    continue
            return False

        return moves

    async def _undo_project_move(self, project: str, before: dict[str, str | None], wanted: str) -> None:
        """Put a project's sessions back where they worked, after its record could not be written.

        Not when the record says ``wanted`` after all: a write that was
        given up or failed after the record was on disk did change it,
        and the sessions are right as they are. Nor when the record
        cannot be read to tell. A session in which
        something began meanwhile is left where it is (``Ledger.restore_project``).
        """
        try:
            # ``read_project`` and not ``get_project``: the latter shows a project whose file
            # cannot be read by its name alone, with no working directory, as if nothing were written.
            kept, readable = await self.space.read_project(project)
        except Exception as exc:  # noqa: BLE001 - whatever keeps the record from being read
            kept, readable = None, False
            logger.debug("the record of a project could not be read (exc_type=%s)", type(exc).__name__)
        if not readable:
            # Not known whether it was written: the sessions are left where they were put
            # sooner than put back under a record that may say the new directory.
            logger.warning(
                "where a project works could not be recorded, and its record cannot be read to tell "
                "whether it was: its sessions stay in the directory asked for"
            )
            return
        if kept is not None and kept.workdir == wanted:
            return
        left = await self.ledger.restore_project(before, wanted)
        if left:
            logger.warning(
                "where a project works could not be recorded and %d of its sessions began work meanwhile: "
                "they stay in the directory asked for", len(left),
            )

    def places(self) -> list[dict[str, Any]]:
        """Return where the host keeps what, for showing it: ``key`` and ``path``, in the order shown."""
        root = self.paths.root()
        return [
            {"key": "task_agents", "path": str(root / "task_agents")},
            {"key": "space", "path": str(self.paths.space().root)},
            {"key": "dsh", "path": str(self.paths.dsh())},
            {"key": "browser_profile", "path": str(root / "browser_profile")},
        ]

    # -- upkeep -----------------------------------------------------------

    def invalidate_capabilities(self) -> None:
        """Note that a plugin started, stopped, reloaded, was installed or removed.

        Safe to call from any thread; the next read fetches the list again.
        """
        self.registry.invalidate()

    def attach_report_sender(self, send_report: SendReport | None) -> None:
        self.outbox.attach(send_report)

    @property
    def closed(self) -> bool:
        """True once ``shutdown`` has finished every step."""
        return self._shutdown_task is not None and self._shutdown_task.done()

    async def shutdown(self) -> None:
        """Stop task agents, the gateway and background work, and close the ledger.

        The steps run in a task of their own. A caller that is cancelled stops
        waiting, but the steps still finish; calling again waits for them.
        """
        if self._shutdown_task is None:
            self._shut_down = True
            self._shutdown_task = asyncio.ensure_future(self._shutdown_steps())
        await asyncio.shield(self._shutdown_task)

    async def _settle_changes(self) -> None:
        """Wait for the changes of where a project works that have begun; what they raise is theirs to tell."""
        if self._changes:
            await asyncio.gather(*list(self._changes), return_exceptions=True)

    async def _release_builtins(self) -> None:
        """Give the desktop and the browser back and let the provider clean up after them."""
        self.leases.release_all()
        await self.leases.settle()

    async def _shutdown_steps(self) -> None:
        steps = (
            # First: a change of where a project works that is under way is carried
            # through (or undone) while the ledger it writes to is still open.
            ("changes under way", self._settle_changes),
            # Before the task agents are stopped: every commission that ends
            # by that would start work for the space that is then cut off.
            ("task agent space, no new work", self.space_keeper.stand_down),
            # Before everything that takes a while: nothing is woken, and no
            # commission filed for a note, in a host that is shutting down
            # (the task brain waits for the stops under way to finish, and
            # what was handed over meanwhile would be ended with the rest).
            # What waits stays on record for the next run.
            ("wakes", self.wakes.close),
            ("task brain", self.brain.close),
            ("task_agents", self.task_agents.shutdown),
            ("built-in tools", self._release_builtins),
            ("gateway", self.gateway.stop),
            ("plugin runs", self.runner.aclose),
            ("short descriptions", self.short_descriptions.aclose),
            ("task agent space", self.space_keeper.aclose),
            ("outbox", self.outbox.close),
        )
        for name, step in steps:
            try:
                await step()
            except Exception:  # noqa: BLE001 - the remaining parts are still shut down
                logger.exception("shutting down the %s failed", name)
        closer = getattr(self._decision_llm, "aclose", None)
        if closer is not None:
            try:
                await closer()
            except Exception:  # noqa: BLE001 - closing clients is best effort
                logger.debug("closing the decision model clients failed", exc_info=True)
        try:
            await self.ledger.close()
        except Exception:  # noqa: BLE001 - nothing more can be done for it
            logger.exception("closing the ledger failed")
        # Copies whose record was still being written: with the ledger
        # closed that fails, and what was written of them is removed by
        # tasks of their own. Waited for, for no longer than they try: a
        # process that ends right after this would cut them off.
        try:
            await self.task_agents.forks_settled()
        except Exception:  # noqa: BLE001 - what is left is left; the host is shut down all the same
            logger.exception("waiting for the copies that were being put on record failed")
