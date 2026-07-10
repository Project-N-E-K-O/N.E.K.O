"""Runtime compatibility API for control-panel actions."""

from __future__ import annotations

from typing import Any

from .contracts import RoastConfig

try:
    from . import runtime_live_controls
except ImportError:
    runtime_live_controls = None


def _fallback_live_connection_snapshot(runtime: Any) -> dict[str, Any]:
    config = getattr(runtime, "config", None)
    return {
        "platform": str(getattr(config, "live_platform", "bilibili") or "bilibili"),
        "room_ref": str(getattr(config, "live_room_ref", "") or ""),
        "room_id": int(getattr(config, "live_room_id", 0) or 0),
        "state": "unsupported",
        "connected": False,
        "listening": False,
        "viewer_count": 0,
        "reason": "live_controls_unavailable",
    }


class RuntimeControlApiMixin:
    def pause(self) -> None:
        if runtime_live_controls is None:
            self.safety_guard.pause("manual pause from control panel")
            return
        runtime_live_controls.pause(self)

    def resume(self) -> None:
        if runtime_live_controls is None:
            self.safety_guard.resume()
            return
        runtime_live_controls.resume(self)

    def clear_queue(self) -> None:
        if runtime_live_controls is None:
            self.safety_guard.clear_queue()
            self.audit.record("queue_clear", "queue cleared")
            return
        runtime_live_controls.clear_queue(self)

    async def clear_viewer_profiles(self) -> dict[str, Any]:
        if runtime_live_controls is None:
            return {"ok": False, "reason": "live_controls_unavailable"}
        return await runtime_live_controls.clear_viewer_profiles(self)

    async def delete_viewer_profile(self, uid: str) -> dict[str, Any]:
        if runtime_live_controls is None:
            return {"ok": False, "uid": str(uid), "reason": "live_controls_unavailable"}
        return await runtime_live_controls.delete_viewer_profile(self, uid)

    async def reset_viewer_impression(self, uid: str) -> dict[str, Any]:
        if runtime_live_controls is None:
            return {"ok": False, "uid": str(uid), "reason": "live_controls_unavailable"}
        return await runtime_live_controls.reset_viewer_impression(self, uid)

    def live_connection_snapshot(self) -> dict[str, Any]:
        if runtime_live_controls is None:
            return _fallback_live_connection_snapshot(self)
        return runtime_live_controls.live_connection_snapshot(self)

    async def set_live_room(self, room_id: Any) -> RoastConfig:
        if runtime_live_controls is None:
            room_ref = str(room_id or "").strip()
            if room_ref:
                self.config.live_room_ref = room_ref
                if room_ref.isdigit():
                    self.config.live_room_id = int(room_ref)
            return self.config
        return await runtime_live_controls.set_live_room(self, room_id)

    async def connect_live_room(self, room_id: Any = 0) -> dict[str, Any]:
        if runtime_live_controls is None:
            await self.set_live_room(room_id)
            self.config.live_enabled = False
            self.live_connection_state = "unsupported"
            self.audit.record(
                "live_connect_unavailable",
                "live controls are not available in this runtime slice",
                level="warning",
            )
            return self.live_connection_snapshot()
        return await runtime_live_controls.connect_live_room(self, room_id)

    async def disconnect_live_room(self) -> dict[str, Any]:
        if runtime_live_controls is None:
            self.config.live_enabled = False
            self.live_connection_state = "disconnected"
            return self.live_connection_snapshot()
        return await runtime_live_controls.disconnect_live_room(self)
