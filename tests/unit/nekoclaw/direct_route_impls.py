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
from types import SimpleNamespace
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


class AnalyzerDirectRoute:
    """The route in use today: ``app.agent_server.channels.user_plugin.dispatch``."""

    name = "analyzer"

    def __init__(self, server: FakePluginServer, monkeypatch: Any) -> None:
        from app.agent_server import _shared
        from app.agent_server.channels import user_plugin
        from brain import task_executor
        from brain.task_executor import DirectTaskExecutor

        self._server = server
        self._user_plugin = user_plugin
        self._modules = _shared.Modules
        monkeypatch.setattr(task_executor.httpx, "AsyncClient", server.client)

        executor = object.__new__(DirectTaskExecutor)
        executor.plugin_list = server.catalog

        async def provider(force_refresh: bool = True) -> list[dict[str, Any]]:
            del force_refresh
            return server.catalog

        executor.plugin_list_provider = provider  # type: ignore[method-assign]
        monkeypatch.setattr(self._modules, "task_executor", executor)
        monkeypatch.setattr(self._modules, "agent_flags", {"user_plugin_enabled": True})
        monkeypatch.setattr(self._modules, "task_registry", {})
        monkeypatch.setattr(self._modules, "task_async_handles", {})
        monkeypatch.setattr(self._modules, "_background_tasks", set())

        self.reports: list[dict[str, Any]] = []

        async def emit_task_result(lanlan_name: Any, **payload: Any) -> None:
            del lanlan_name
            # Defaults of the real emitter that the contract relies on.
            status = payload.get("status")
            if status is None:
                status = "completed" if payload.get("success") else ("partial" if payload.get("detail") else "failed")
            payload = {
                "error_message": "",
                "delivery_mode": "proactive",
                "result_kind": "task_result",
                "expires_in_s": None,
                **payload,
                "status": status,
            }
            self.reports.append({name: payload.get(name) for name in REPORT_FIELDS})

        async def emit_main_event(event_type: str, lanlan_name: Any, **payload: Any) -> None:
            del event_type, lanlan_name, payload

        async def display_name(plugin_id: str) -> str:
            return plugin_id

        monkeypatch.setattr(user_plugin, "_emit_task_result", emit_task_result)
        monkeypatch.setattr(user_plugin, "_emit_main_event", emit_main_event)
        monkeypatch.setattr(user_plugin, "_get_plugin_friendly_name", display_name)
        monkeypatch.setattr(user_plugin, "_get_plugin_display_id", display_name)
        monkeypatch.setattr(user_plugin, "_bind_deferred_task", lambda *args: None)
        # A tracker of its own, so no record outlives the test.
        from app.agent_server.tracker import AgentTaskTracker

        monkeypatch.setattr(user_plugin, "_task_tracker", AgentTaskTracker())

    # -- pure pieces -------------------------------------------------------

    def terminal_status(self, success: bool, run_data: Any) -> str:
        return self._user_plugin._plugin_terminal_status(success, run_data)

    def delivery_mode(self, result: Any) -> str:
        return self._user_plugin._resolve_delivery_mode(result)

    def result_contract(self, plugin_id: str, entry_id: str | None, result: Any) -> tuple[str, float | None]:
        return self._user_plugin._resolve_plugin_result_contract(plugin_id, entry_id, result)

    # -- the whole route ---------------------------------------------------

    async def run(
        self,
        plugin_id: str,
        entry_id: str,
        args: dict[str, Any] | None = None,
        *,
        user_text: str = "",
    ) -> DirectRun:
        task_id = "task-direct"
        decision = SimpleNamespace(
            task_id=task_id,
            tool_name=plugin_id,
            tool_args=args or {},
            entry_id=entry_id,
            task_description="direct call",
            reason="",
            latest_user_request=user_text,
        )
        await self._user_plugin.dispatch(
            decision,
            messages=[],
            lanlan_name="Lan",
            conversation_id="conv-1",
            trigger_user_msg_sig=None,
        )
        handle = self._modules.task_async_handles.get(task_id)
        if handle is not None:
            await asyncio.wait_for(handle, 10)
        registered = self._modules.task_registry.get(task_id) or {}
        return DirectRun(reports=list(self.reports), final_status=str(registered.get("status", "")))


DIRECT_ROUTE_IMPLS: dict[str, Any] = {AnalyzerDirectRoute.name: AnalyzerDirectRoute}
