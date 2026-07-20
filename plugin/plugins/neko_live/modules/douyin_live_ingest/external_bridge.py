"""Douyin external bridge transport boundary."""

from __future__ import annotations

from typing import Any

from ..live_bridge import LiveBridgeEvent, LiveBridgeStartRequest, LiveBridgeState, LiveBridgeTransport
from .bridge_adapter import DouyinLiveBridgeAdapter
from .transport_event import DouyinTransportEvent, DouyinTransportStartRequest, DouyinTransportState


class DouyinExternalBridgeTransport:
    """Thin Douyin wrapper around the generic local bridge transport."""

    def __init__(self, *, bridge: LiveBridgeTransport | None = None, adapter: Any = None) -> None:
        self._bridge = bridge or LiveBridgeTransport(reconnect_attempts=3)
        self._adapter = adapter or DouyinLiveBridgeAdapter()

    async def start(self, request: DouyinTransportStartRequest) -> DouyinTransportState:
        if not isinstance(request, DouyinTransportStartRequest):
            return DouyinTransportState(state="unsupported", last_error="external bridge request is missing")
        state = await self._bridge.start(
            LiveBridgeStartRequest(
                room_ref=request.room_ref,
                adapter=self._adapter,
                emit=lambda event: self._emit(request, event),
                on_state=lambda bridge_state: self._notify(request, bridge_state),
            )
        )
        return _state_from_bridge(state)

    async def stop(self) -> DouyinTransportState:
        state = await self._bridge.stop()
        return _state_from_bridge(state)

    def _emit(self, request: DouyinTransportStartRequest, event: LiveBridgeEvent) -> None:
        if request.emit is not None:
            request.emit(DouyinTransportEvent(payload=event.payload, ts=event.ts))

    def _notify(self, request: DouyinTransportStartRequest, state: LiveBridgeState) -> None:
        if request.on_state is not None:
            request.on_state(_state_from_bridge(state))


def _state_from_bridge(state: LiveBridgeState) -> DouyinTransportState:
    return DouyinTransportState(
        state=state.safe_state(),
        last_error=state.safe_error(),
        last_event_at=state.last_event_at,
        last_event_type=state.last_event_type,
    )
