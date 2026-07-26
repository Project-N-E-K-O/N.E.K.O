"""Runtime compatibility API for dashboard projections."""

from __future__ import annotations

from typing import Any

from . import runtime_co_stream_policy, runtime_dashboard


class RuntimeDashboardApiMixin:
    async def dashboard_state(self) -> dict[str, Any]:
        return await runtime_dashboard.dashboard_state(self)

    def runtime_health_rows(self) -> list[dict[str, Any]]:
        return runtime_dashboard.runtime_health_rows(self)

    def dashboard_actions(self) -> list[dict[str, str]]:
        return runtime_dashboard.dashboard_actions()

    def co_stream_participation_snapshot(self) -> dict[str, Any]:
        return runtime_co_stream_policy.co_stream_participation_snapshot(self)
