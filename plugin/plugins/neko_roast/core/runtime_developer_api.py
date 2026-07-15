"""Runtime compatibility API for developer tool actions."""

from __future__ import annotations

from typing import Any

from . import runtime_developer_tools
from .contracts import InteractionResult


class RuntimeDeveloperApiMixin:
    async def handle_sandbox_target(self, **kwargs: Any) -> InteractionResult:
        return await runtime_developer_tools.handle_sandbox_target(self, **kwargs)

    async def lookup_bili_user(self, **kwargs: Any) -> dict[str, Any]:
        return await runtime_developer_tools.lookup_bili_user(self, **kwargs)

    def clear_sandbox_data(self) -> dict[str, Any]:
        return runtime_developer_tools.clear_sandbox_data(self)

    def _require_developer_mode(self) -> None:
        runtime_developer_tools.require_developer_mode(self)

    async def handle_manual_event(self, **kwargs: Any) -> InteractionResult:
        return await runtime_developer_tools.handle_manual_event(self, **kwargs)
