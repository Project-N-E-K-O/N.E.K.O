"""Runtime compatibility API for live payload ingress actions."""

from __future__ import annotations

from typing import Any

from . import runtime_live_input
from . import runtime_live_listener
from .contracts import InteractionResult, ViewerEvent


class RuntimeLiveInputApiMixin:
    def _sync_douyin_listener_state(self, state: Any) -> None:
        runtime_live_listener.sync_douyin_listener_state(self, state)

    def record_result(self, result: InteractionResult) -> None:
        runtime_live_input.record_result(self, result)

    @staticmethod
    def _expose_request_metadata(payload: dict[str, Any]) -> None:
        runtime_live_input.expose_request_metadata(payload)

    async def handle_live_payload(self, payload: dict[str, Any]) -> InteractionResult:
        return await runtime_live_input.handle_live_payload(self, payload)

    def _record_live_signal_only_skip(self, event: ViewerEvent, event_type: str) -> InteractionResult:
        return runtime_live_input.record_live_signal_only_skip(self, event, event_type)

    async def lookup_live_room(self, room_id: Any) -> dict[str, Any]:
        return await runtime_live_input.lookup_live_room(self, room_id)
