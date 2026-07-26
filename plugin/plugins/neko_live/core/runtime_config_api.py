"""Runtime compatibility API for configuration actions."""

from __future__ import annotations

import asyncio
from typing import Any

from . import runtime_config
from .contracts import LiveConfig


class RuntimeConfigApiMixin:
    async def reload_config(self) -> LiveConfig:
        return await runtime_config.reload_config(self)

    def _activate_config(self, config: LiveConfig) -> LiveConfig:
        return runtime_config.activate_config(self, config)

    def _get_config_lock(self) -> asyncio.Lock:
        return runtime_config.get_config_lock(self)

    async def update_config(self, updates: dict[str, Any]) -> LiveConfig:
        return await runtime_config.update_config(self, updates)

    async def _reconcile_live_listener_after_config(
        self,
        clean: dict[str, Any],
        *,
        old_room_id: int,
        old_platform: str = "bilibili",
        old_room_ref: str = "",
        was_listening: bool,
        old_provider: Any = None,
    ) -> None:
        await runtime_config.reconcile_live_listener_after_config(
            self,
            clean,
            old_room_id=old_room_id,
            old_platform=old_platform,
            old_room_ref=old_room_ref,
            was_listening=was_listening,
            old_provider=old_provider,
        )

    async def _start_live_listener(self, room_ref: Any) -> bool:
        return await runtime_config.start_live_listener(self, room_ref)

    async def _stop_live_listener(self, *, mark_disabled: bool) -> None:
        await runtime_config.stop_live_listener(self, mark_disabled=mark_disabled)

    async def _persist_config_best_effort(self, clean: dict[str, Any]) -> None:
        await runtime_config.persist_config_best_effort(self, clean)

    async def _persist_config_update(self, clean: dict[str, Any]) -> None:
        await runtime_config.persist_config_update(self, clean)
