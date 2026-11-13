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

from nekoclaw.host.capabilities import CapabilityRegistry
from nekoclaw.host.decision import ConfiguredDecisionLLM, DecisionLLM, DecisionModel, LexicalDecisionModel
from nekoclaw.host.task_agents import (
    TaskAgentManager,
    LessonSink,
    ModelConfigProvider,
    RuntimeFactory,
    default_runtime_factory,
)
from nekoclaw.host.gateway import CapabilityGateway
from nekoclaw.host.ledger import Commission, Ledger
from nekoclaw.host.outbox import OutboxSender, SendReport
from nekoclaw.host.paths import HostPaths
from nekoclaw.host.permissions import PermissionGate, PermissionStore
from nekoclaw.host.plugin_runs import ClientFactory, PluginCatalog, PluginRunner
from nekoclaw.host.preferences import PreferenceStore
from nekoclaw.host.reports import Reporter
from nekoclaw.host.task_brain import QueryAnswer, Receipt, TaskBrain

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
    ) -> None:
        self.paths = HostPaths(root)
        self.ledger = Ledger(self.paths.ledger_db)
        self.preferences = PreferenceStore(self.ledger)
        self.permission_store = PermissionStore(self.paths.permissions_json)
        self.permissions = PermissionGate(self.permission_store)
        self.catalog = PluginCatalog(base_url=plugin_base_url, client_factory=client_factory, change_token=change_token)
        self.runner = PluginRunner(self.catalog, base_url=plugin_base_url, client_factory=client_factory)
        self.registry = CapabilityRegistry(self.catalog, plugins_enabled=plugins_enabled)
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
        )
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
        )
        # Set when shutting down starts; the task finishes when it is done.
        self._shut_down = False
        # Set by the first call that does work; see ``_first_use``.
        self._used = False
        self._shutdown_task: asyncio.Future[None] | None = None

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
