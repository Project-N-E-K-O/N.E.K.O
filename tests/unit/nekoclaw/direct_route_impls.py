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

"""One face for every implementation of "call a plugin entry directly".

The direct route is: the entry was chosen, call it, turn its result into the
report the main dialogue receives. ``test_contract_plugin_results.py`` states
what that route does and runs against each implementation in
``DIRECT_ROUTE_IMPLS``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from tests.unit.nekoclaw.plugin_server_fake import FakePluginServer

# The fields of a report that the contract covers.
REPORT_FIELDS = (
    "status",
    "success",
    "summary",
    "detail",
    "error_message",
    "delivery_mode",
    "result_kind",
    "expires_in_s",
    "source_kind",
    "source_name",
)


@dataclass
class DirectRun:
    """What one direct call led to."""

    # Reports for the main dialogue, each reduced to ``REPORT_FIELDS``.
    reports: list[dict[str, Any]] = field(default_factory=list)
    # "completed", "blocked", "failed", or "running" for a deferred task.
    final_status: str = ""


class _FixedDecision:
    """A decision model that always picks the entry the test names."""

    def __init__(self) -> None:
        self.choice: tuple[str, str, dict[str, Any]] | None = None

    async def triage(self, request: Any) -> Any:
        from nekoclaw.host.decision import Triage

        del request
        return Triage(kind="new")

    async def probe(self, request: Any, *, ask_model: bool = True) -> Any:
        del ask_model
        return await self.triage(request)

    async def select(self, request: Any, triage: Any) -> Any:
        from nekoclaw.host.decision import Selection

        del request, triage
        assert self.choice is not None
        plugin_id, entry_id, args = self.choice
        return Selection(route="plugin", plugin_id=plugin_id, entry_id=entry_id, plugin_args=args)


class NekoclawDirectRoute:
    """The new route: ``nekoclaw.host.task_brain.TaskBrain`` calling an entry directly."""

    name = "nekoclaw"
    # What a commission's status is called in the analyzer's vocabulary.
    _STATUS = {"done": "completed", "blocked": "blocked", "failed": "failed", "working": "running"}

    def __init__(self, server: FakePluginServer, monkeypatch: Any, tmp_path: Any = None) -> None:
        from nekoclaw.host.service import NekoClawHost

        del monkeypatch
        self._server = server
        self._decision = _FixedDecision()
        self._host = NekoClawHost(
            root=tmp_path / "nekoclaw",
            client_factory=server.client,
            decision=self._decision,
            task_agents_enabled=lambda: False,
        )

    async def close(self) -> None:
        await self._host.shutdown()

    def _entry(self, plugin_id: str, entry_id: str | None) -> dict[str, Any] | None:
        for plugin in self._server.catalog:
            if plugin.get("id") != plugin_id:
                continue
            for entry in plugin.get("entries") or []:
                if isinstance(entry, dict) and entry.get("id") == entry_id:
                    return entry
        return None

    # -- pure pieces -------------------------------------------------------

    def terminal_status(self, success: bool, run_data: Any) -> str:
        from nekoclaw.host.plugin_runs import plugin_terminal_status

        return plugin_terminal_status(success, run_data)

    def delivery_mode(self, result: Any) -> str:
        from nekoclaw.host.plugin_runs import resolve_delivery_mode

        return resolve_delivery_mode(result)

    def result_contract(self, plugin_id: str, entry_id: str | None, result: Any) -> tuple[str, float | None]:
        from nekoclaw.host.plugin_runs import resolve_result_contract

        return resolve_result_contract(self._entry(plugin_id, entry_id), result)

    # -- the whole route ---------------------------------------------------

    async def run(
        self,
        plugin_id: str,
        entry_id: str,
        args: dict[str, Any] | None = None,
        *,
        user_text: str = "",
    ) -> DirectRun:
        self._decision.choice = (plugin_id, entry_id, args or {})
        receipt = await self._host.dispatch(
            "direct call", lanlan_name="Lan", user_text=user_text, conversation_id="conv-1", lang="en"
        )
        assert receipt.kind == "accepted", receipt
        await asyncio.wait_for(self._host.brain.settle(), 10)
        commission = await self._host.ledger.get(str(receipt.commission_id))
        assert commission is not None
        reports = [
            {name: item.payload.get(name) for name in REPORT_FIELDS}
            for item in await self._host.ledger.outbox_pending()
            if item.kind == "task_result"
        ]
        return DirectRun(reports=reports, final_status=self._STATUS.get(commission.status, commission.status))


DIRECT_ROUTE_IMPLS: dict[str, Any] = {
    NekoclawDirectRoute.name: NekoclawDirectRoute,
}
