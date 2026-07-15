"""Runtime compatibility API for control-panel actions."""

from __future__ import annotations

from typing import Any

from . import runtime_live_controls
from .contracts import RoastConfig


class RuntimeControlApiMixin:
    def pause(self) -> None:
        runtime_live_controls.pause(self)

    def resume(self) -> None:
        runtime_live_controls.resume(self)

    def clear_queue(self) -> None:
        runtime_live_controls.clear_queue(self)

    async def clear_viewer_profiles(self) -> dict[str, Any]:
        return await runtime_live_controls.clear_viewer_profiles(self)

    async def delete_viewer_profile(self, uid: str) -> dict[str, Any]:
        return await runtime_live_controls.delete_viewer_profile(self, uid)

    async def reset_viewer_impression(self, uid: str) -> dict[str, Any]:
        return await runtime_live_controls.reset_viewer_impression(self, uid)

    def live_connection_snapshot(self) -> dict[str, Any]:
        return runtime_live_controls.live_connection_snapshot(self)

    async def set_live_room(self, room_id: Any) -> RoastConfig:
        return await runtime_live_controls.set_live_room(self, room_id)

    async def connect_live_room(
        self,
        room_id: Any = 0,
        *,
        allow_accountless: bool = False,
    ) -> dict[str, Any]:
        return await runtime_live_controls.connect_live_room(
            self,
            room_id,
            allow_accountless=allow_accountless,
        )

    async def disconnect_live_room(self) -> dict[str, Any]:
        return await runtime_live_controls.disconnect_live_room(self)
