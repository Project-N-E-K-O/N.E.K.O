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

``NekoClawHost`` builds the parts and connects them. Building it does no
work: no file is opened, no thread or process is started, nothing listens and
nothing is fetched. The ledger file is opened by the first dispatch or query,
the runtime and the gateway are started by the first task agent, and the task
agent space is first read when a task agent starts. Whether the user
installed dsh, the optional second runtime (``nekoclaw.dsh``), is looked at
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
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from nekoclaw.host.capabilities import BuiltinCapabilities, CapabilityRegistry
from nekoclaw.host.decision import ConfiguredDecisionLLM, DecisionLLM, DecisionModel, LexicalDecisionModel
from nekoclaw.host.task_agents import (
    TaskAgentManager,
    LessonSink,
    ModelConfigProvider,
    RuntimeFactory,
    UsageMeter,
    default_runtime_factory,
)
from nekoclaw.host.gateway import CapabilityGateway
from nekoclaw.host.leases import ActivityListener, BuiltinLeases
from nekoclaw.host.ledger import ENDED_STATUSES, Commission, Ledger, Listener
from nekoclaw.host.model_capabilities import ModelCapabilityStore
from nekoclaw.host.outbox import OutboxSender, SendReport
from nekoclaw.host.paths import HostPaths
from nekoclaw.host.runtimes import RUNTIME_DSH
from nekoclaw.host.permissions import PermissionGate, PermissionStore
from nekoclaw.host.plugin_runs import ClientFactory, PluginCatalog, PluginRunner
from nekoclaw.host.preferences import PreferenceStore
from nekoclaw.host.reports import CHANNEL_PLUGIN, CHANNEL_TASK_AGENT, Reporter
from nekoclaw.host.short_descriptions import ShortDescriptions
from nekoclaw.host.space import TaskAgentSpace
from nekoclaw.host.space_keeper import SpaceKeeper
from nekoclaw.host.task_brain import ProgressListener, QueryAnswer, Receipt, TaskBrain, TurnCheck
from nekoclaw.host.wakes import SessionWakes

logger = logging.getLogger(__name__)


async def configured_task_agent_model() -> dict[str, Any] | None:
    """Return the model task agents use: the existing agent model slot.

    The address is taken as configured, ``http://`` included.
    """
    from utils.config_manager import get_config_manager

    config = await asyncio.to_thread(get_config_manager(migrate=False).get_model_api_config, "agent")
    if not config.get("model") or not config.get("base_url"):
        return None
    return {
        "model": config["model"],
        "base_url": config["base_url"],
        "api_key": config.get("api_key"),
        "provider_type": config.get("provider_type"),
    }


class NekoClawHost:
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
    ) -> None:
        self.paths = HostPaths(root)
        # What she and the user said lately, from which a commission that
        # opens a new session is handed an excerpt. This process's own
        # (``conversation.py``), which the agent server feeds, unless
        # another is given.
        if conversation is None:
            from nekoclaw.host.conversation import recent_conversation

            conversation = recent_conversation()
        self.conversation = conversation
        self._task_agent_model = task_agent_model
        # Where dsh is, for a user who downloaded it; a page about the
        # runtimes reads and changes it through the methods further down.
        # Only the module that knows where dsh would be is imported here;
        # dsh's driver is imported when a commission first runs in dsh.
        if dsh is None:
            from nekoclaw.dsh.install import DshInstallation

            dsh = DshInstallation(self.paths.dsh)
        self.dsh = dsh
        self.ledger = Ledger(self.paths.ledger_db, reconcile_report=self._restart_report)
        self.preferences = PreferenceStore(self.ledger)
        self.permission_store = PermissionStore(self.paths.permissions_json)
        self.permissions = PermissionGate(self.permission_store)
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
        )
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
        # A plugin's request for confirmation is put to the user by the
        # gateway, through the same waiting the task agents' own questions use.
        self.gateway.set_ask_user(self.task_agents.ask_user)
        if decision is None:
            decision = LexicalDecisionModel(self._decision_llm)
        self.decision = decision
        self.brain = TaskBrain(
            ledger=self.ledger,
            preferences=self.preferences,
            registry=self.registry,
            runner=self.runner,
            decision=self.decision,
            reporter=self.reporter,
            permission_store=self.permission_store,
            task_agents=self.task_agents,
            enabled=enabled,
            on_progress=on_progress,
        )
        # Set when shutting down starts; the task finishes when it is done.
        self._shut_down = False
        # Set by the first call that does work; see ``_first_use``.
        self._used = False
        self._shutdown_task: asyncio.Future[None] | None = None

    def _restart_report(self, commission: Commission) -> tuple[str, dict[str, Any]] | None:
        """Build the report for a commission an earlier host process left unfinished.

        It was failed when the ledger was opened. She is told along with her
        next turn (``passive``): nothing wakes her for it, so it costs no
        model call of its own. Runs on the ledger thread.
        """
        from config.prompts.prompts_nekoclaw_brain import HOST_RESTART_FAILED_TEXT
        from nekoclaw.host.task_brain import _texts

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
        if self._used or self._shut_down:
            return
        self._used = True
        self.outbox.kick()
        self.wakes.kick()

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
        self, commission_id: str, text: str, *, choice: str | None = None, question_id: str | None = None
    ) -> bool:
        self._first_use()
        return await self.brain.answer(commission_id, text, choice=choice, question_id=question_id)

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
        ``ModelCapabilityStore.describe_window`` says of the task agent
        model as it is configured now (its window, where a conversation is
        summed up, what was learned of it). None for an unknown commission.
        """
        self._first_use()
        usage = await self.task_agents.usage_of(commission_id)
        if usage is None:
            return None
        window = None
        try:
            config = await self._task_agent_model()
            if config and config.get("model"):
                window = await self.model_capabilities.describe_window(config.get("base_url"), config.get("model"))
        except Exception:  # noqa: BLE001 - what was used is told all the same
            logger.warning("reading the window of the task agent model failed", exc_info=True)
        return {**usage, "window": window}

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
            from nekoclaw.dsh import DshUnavailable

            raise DshUnavailable("dsh is in use: a commission is running in it")

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

    async def _release_builtins(self) -> None:
        """Give the desktop and the browser back and let the provider clean up after them."""
        self.leases.release_all()
        await self.leases.settle()

    async def _shutdown_steps(self) -> None:
        steps = (
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
