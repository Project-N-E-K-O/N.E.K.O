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
the runtime and the gateway are started by the first task agent.

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
* ``send_report`` - delivers an outbox row to the main server. Without it
  reports are stored and wait. Reports an earlier run left unsent go out
  from the first call that does work.

``mount.py`` does this for the agent process.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
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
from nekoclaw.host.ledger import ENDED_STATUSES, Commission, Ledger, Listener
from nekoclaw.host.outbox import OutboxSender, SendReport
from nekoclaw.host.paths import HostPaths
from nekoclaw.host.permissions import PermissionGate, PermissionStore
from nekoclaw.host.plugin_runs import ClientFactory, PluginCatalog, PluginRunner
from nekoclaw.host.preferences import PreferenceStore
from nekoclaw.host.reports import CHANNEL_PLUGIN, CHANNEL_TASK_AGENT, Reporter
from nekoclaw.host.task_brain import ProgressListener, QueryAnswer, Receipt, TaskBrain, TurnCheck

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
    ) -> None:
        self.paths = HostPaths(root)
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
        self.registry = CapabilityRegistry(self.catalog, plugins_enabled=plugins_enabled, builtins=builtins)
        self.outbox = OutboxSender(self.ledger, send_report)
        self.reporter = Reporter(self.ledger, self.outbox)
        self.gateway = CapabilityGateway(
            registry=self.registry, runner=self.runner, permissions=self.permissions, ledger=self.ledger
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
        )
        # A plugin's request for confirmation is put to the user by the
        # gateway, through the same waiting the task agents' own questions use.
        self.gateway.set_ask_user(self.task_agents.ask_user)
        self._decision_llm = decision_llm
        if decision is None:
            if self._decision_llm is None:
                self._decision_llm = ConfiguredDecisionLLM()
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
        if commission.status in ENDED_STATUSES:
            self.permissions.end_session(commission.id)

    def _first_use(self) -> None:
        """Start sending reports left unsent by an earlier run of the host.

        Building the host does no work, so a sender given to the constructor
        is only started here, by the first call that does work. Reports
        written later start it themselves.
        """
        if self._used or self._shut_down:
            return
        self._used = True
        self.outbox.kick()

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

    async def answer(self, commission_id: str, text: str, *, choice: str | None = None) -> bool:
        self._first_use()
        return await self.brain.answer(commission_id, text, choice=choice)

    async def complete_deferred(self, commission_id: str) -> bool:
        self._first_use()
        return await self.brain.complete_deferred(commission_id)

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

    async def _shutdown_steps(self) -> None:
        steps = (
            ("task brain", self.brain.close),
            ("task_agents", self.task_agents.shutdown),
            ("gateway", self.gateway.stop),
            ("plugin runs", self.runner.aclose),
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
