"""Douyin live ingest module.

This module owns the provider-facing contract, safe status projection, and
sanitized event normalization for the read-only Douyin live input path.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from ...core.contracts import LiveEvent, LiveRoomStatus, ViewerEvent
from .._base import BaseModule
from .bridge_plan import DouyinBridgeConnectionPlan
from .event_model import (
    is_routable_event_type,
    is_status_only_event_type,
    normalize_event_type,
    platform_uid,
    safe_payload,
    to_live_event,
    to_provider_event,
)
from .bridge_adapter import DouyinLiveBridgeAdapter
from .embedded_bridge import DouyinEmbeddedBridgeSupervisor
from .external_bridge import DouyinExternalBridgeTransport
from .public_projection import safe_public_bool, safe_public_float, safe_public_int, safe_public_text, safe_room_ref
from .retry_policy import DouyinReconnectPolicy, DouyinReconnectState
from .room_ref import parse_douyin_room_ref
from .transport_event import (
    DouyinTransportEvent,
    DouyinTransportStartRequest,
    DouyinTransportState,
    safe_transport_event_time,
)
from .webcast import DouyinWebcastInfo, fetch_webcast_info


_PUBLIC_STATES = {
    "disconnected",
    "auth_required",
    "metadata_unavailable",
    "unsupported",
    "connecting",
    "reconnecting",
    "connected",
    "receiving",
}


class DouyinLiveIngestModule(BaseModule):
    id = "douyin_live_ingest"
    title = "Douyin Live Input"

    def __init__(self) -> None:
        super().__init__()
        self._room_ref: str = ""
        self._state: str = "disconnected"
        self._last_error: str = ""
        self._last_event_at: float = 0.0
        self._last_event_type: str = ""
        self._last_published_event_type: str = ""
        self._last_status_only_event_type: str = ""
        self._status_only_count: int = 0
        self._connection_plan: DouyinBridgeConnectionPlan | None = None
        self._reconnect = DouyinReconnectState(DouyinReconnectPolicy())
        self._bridge_transport = DouyinExternalBridgeTransport()
        self._bridge_supervisor = DouyinEmbeddedBridgeSupervisor()
        self._lifecycle_lock = asyncio.Lock()
        self._generation = 0
        self._stop_requested = True

    async def teardown(self) -> None:
        await self.stop_listening()
        await super().teardown()

    def status(self) -> dict[str, Any]:
        return {
            "enabled": safe_public_bool(self.enabled),
            "listening": self.is_listening(),
            "room_ref": safe_room_ref(self._room_ref),
            "state": _public_state(self._state),
            "last_error": safe_public_text(self._last_error, limit=160),
            "connection_plan": self._connection_plan.to_public_dict() if self._connection_plan else None,
            "reconnect": self._reconnect.to_public_dict(),
            "last_event_at": safe_public_float(self._last_event_at),
            "last_event_type": _public_event_type(self._last_event_type),
            "last_published_event_type": _public_event_type(self._last_published_event_type),
            "last_status_only_event_type": _public_event_type(self._last_status_only_event_type),
            "status_only_count": safe_public_int(self._status_only_count),
        }

    def is_listening(self) -> bool:
        return _public_state(self._state) in {"connected", "receiving"}

    def listener_state(self) -> dict[str, Any]:
        return {
            "state": _public_state(self._state),
            "room_ref": safe_room_ref(self._room_ref),
            "room_id": 0,
            "viewer_count": 0,
            "last_error": safe_public_text(self._last_error, limit=160),
            "connection_plan": self._connection_plan.to_public_dict() if self._connection_plan else None,
            "reconnect": self._reconnect.to_public_dict(),
        }

    async def start_listening(self, room_ref: Any) -> bool:
        async with self._lifecycle_lock:
            return await self._start_listening_locked(room_ref)

    async def _start_listening_locked(self, room_ref: Any) -> bool:
        self._generation += 1
        generation = self._generation
        self._stop_requested = False
        parsed = parse_douyin_room_ref(room_ref)
        self._room_ref = parsed.room_ref
        self._connection_plan = None
        self._reconnect.reset()
        if not parsed.ok:
            self._state = "disconnected"
            self._last_error = parsed.message
            return False
        self._connection_plan = _bridge_connection_plan(parsed.room_ref)
        prepared = await self._prepare_external_bridge_transport()
        if prepared is not None:
            self._apply_transport_state(prepared)
            return False
        state = await self._start_external_bridge(parsed.room_ref, self._credential_cookie(), generation)
        self._apply_transport_state(state, generation=generation)
        if not self.is_listening():
            await self._bridge_supervisor.stop()
        return self.is_listening()

    async def _start_external_bridge(self, room_ref: str, cookie: str, generation: int) -> DouyinTransportState:
        return await self._bridge_transport.start(
            DouyinTransportStartRequest(
                room_ref=room_ref,
                cookie=cookie,
                connection_plan=self._connection_plan,
                emit=lambda event: self._publish_transport_event_for_generation(event, generation),
                on_state=lambda state: self._apply_transport_state(state, generation=generation),
            )
        )

    async def _prepare_external_bridge_transport(self) -> DouyinTransportState | None:
        if not isinstance(self._bridge_transport, DouyinExternalBridgeTransport):
            return None
        bridge = await self._bridge_supervisor.start()
        if not bridge.ok or not bridge.base_url:
            self._connection_plan = _bridge_connection_plan(
                self._room_ref,
                ready=False,
                missing=_bridge_missing_for_error(bridge.last_error),
                message=bridge.last_error,
            )
            return DouyinTransportState(state="unsupported", last_error=bridge.last_error)
        self._bridge_transport = DouyinExternalBridgeTransport(
            adapter=DouyinLiveBridgeAdapter(base_url=bridge.base_url)
        )
        return None

    async def stop_listening(self) -> None:
        async with self._lifecycle_lock:
            await self._stop_listening_locked()

    async def _stop_listening_locked(self) -> None:
        self._stop_requested = True
        self._generation += 1
        await self._bridge_transport.stop()
        await self._bridge_supervisor.stop()
        self._state = "disconnected"
        self._last_error = ""
        self._connection_plan = None
        self._reconnect.reset()

    def _publish_transport_event_for_generation(
        self,
        event: DouyinTransportEvent,
        generation: int,
    ) -> LiveEvent | None:
        if generation != self._generation or self._stop_requested:
            return None
        return self.publish_transport_event(event)

    def normalize(self, payload: Any) -> ViewerEvent:
        safe = safe_payload(payload)
        return ViewerEvent(
            uid=platform_uid(safe.get("uid") or safe.get("user_id") or safe.get("open_id")),
            nickname=str(safe.get("nickname") or safe.get("user_name") or "").strip(),
            avatar_url=str(safe.get("avatar_url") or "").strip(),
            danmaku_text=str(safe.get("danmaku_text") or safe.get("text") or safe.get("content") or "").strip(),
            target_lanlan=str(safe.get("target_lanlan") or safe.get("lanlan_name") or "").strip(),
            source="live_danmaku",
            live_mode=self.ctx.config.live_mode if self.ctx else "co_stream",
            raw=safe,
        )

    async def lookup_room_status(self, room_ref: Any) -> LiveRoomStatus:
        parsed = parse_douyin_room_ref(room_ref)
        if not parsed.ok:
            return LiveRoomStatus(room_id=0, ok=False, message=parsed.message)
        try:
            info = await asyncio.to_thread(
                fetch_webcast_info,
                parsed.room_ref,
                cookie=self._credential_cookie(),
            )
        except Exception as exc:
            return LiveRoomStatus(
                room_id=0,
                ok=False,
                live_status="unknown",
                message=f"douyin room page fetch failed: {type(exc).__name__}",
            )
        return info.to_live_room_status()

    def publish_transport_event(self, event: DouyinTransportEvent) -> LiveEvent | None:
        if not isinstance(event, DouyinTransportEvent):
            self._mark_event_seen("unknown")
            return None
        return self.publish_provider_event(event.safe_payload(), ts=safe_transport_event_time(event.ts))

    def publish_provider_event(self, payload: dict[str, Any], *, ts: float | None = None) -> LiveEvent | None:
        if self.ctx is None or not self._owns_active_target():
            return None
        event = to_provider_event(payload, room_ref=self._room_ref)
        if not event.room_ref:
            self._state = "disconnected"
            self._last_error = "douyin room_ref is required before publishing events"
            return None
        if self._room_ref and safe_room_ref(event.room_ref) != safe_room_ref(self._room_ref):
            self._state = "disconnected"
            self._last_error = "douyin room_ref mismatch before publishing events"
            return None
        if is_status_only_event_type(event.event_type):
            self._mark_status_only_event(event.event_type, ts)
            self._last_error = ""
            return None
        live_event = to_live_event(event, ts=ts)
        live_event.session_generation = max(
            0,
            int(getattr(self.ctx, "_live_session_generation", 0) or 0),
        )
        if not is_routable_event_type(live_event.type):
            self._mark_event_seen(live_event.type, live_event.ts)
            self._last_error = ""
            return None
        bus = getattr(self.ctx, "event_bus", None)
        if bus is not None:
            bus.publish(live_event.type, live_event)
        self._mark_published_event(live_event.type, live_event.ts)
        self._last_error = ""
        return live_event

    def mark_transport_failure(self, reason: Any) -> None:
        self._reconnect.record_failure(reason)
        if safe_public_bool(self._reconnect.exhausted):
            self._state = "disconnected"
            self._last_error = "douyin transport retry limit reached"
            return
        self._state = "reconnecting"
        self._last_error = self._reconnect.last_reason

    def _credential_cookie(self) -> str:
        credential = getattr(self.ctx, "douyin_credential", None) if self.ctx else None
        if not isinstance(credential, dict):
            return ""
        cookie = credential.get("cookie")
        return cookie.strip() if isinstance(cookie, str) else ""

    def _mark_event_seen(self, event_type: Any, ts: float | None = None) -> None:
        self._last_event_at = ts or time.time()
        self._last_event_type = _internal_event_type(event_type)

    def _mark_published_event(self, event_type: Any, ts: float | None = None) -> None:
        self._mark_event_seen(event_type, ts)
        self._last_published_event_type = self._last_event_type

    def _mark_status_only_event(self, event_type: Any, ts: float | None = None) -> None:
        self._mark_event_seen(event_type, ts)
        self._last_status_only_event_type = self._last_event_type
        self._status_only_count += 1

    def _apply_transport_state(self, state: DouyinTransportState, *, generation: int | None = None) -> None:
        if generation is not None and generation != self._generation:
            return
        self._state = state.safe_state()
        self._last_error = state.safe_error()
        if self._state in {"connected", "receiving"}:
            self._reconnect.reset()
        sync_runtime_state = getattr(self.ctx, "_sync_douyin_listener_state", None)
        if callable(sync_runtime_state):
            sync_runtime_state(self._state)
        event_type = _internal_event_type(state.last_event_type)
        if event_type != "unknown":
            self._mark_event_seen(event_type, safe_transport_event_time(state.last_event_at))

    def _owns_active_target(self) -> bool:
        if self.ctx is None or getattr(self.ctx, "_stopping", False) is True:
            return False
        provider = getattr(self.ctx, "live_provider", None)
        if provider is None:
            return True
        if getattr(provider, "platform", "") != "douyin":
            return False
        configured = getattr(provider, "configured_room_ref", None)
        if not callable(configured):
            return True
        configured_room = safe_room_ref(configured())
        return not configured_room or configured_room == safe_room_ref(self._room_ref)


def _public_event_type(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    return normalize_event_type(text) if text else ""


def _public_state(value: Any) -> str:
    if not isinstance(value, str):
        return "unknown"
    text = value.strip().lower()
    return text if text in _PUBLIC_STATES else "unknown"


def _bridge_connection_plan(
    room_ref: str,
    *,
    ready: bool = True,
    missing: tuple[str, ...] = (),
    message: str = "douyin external bridge transport ready",
) -> DouyinBridgeConnectionPlan:
    return DouyinBridgeConnectionPlan(
        ready=ready,
        room_ref=room_ref,
        missing=missing,
        message=message,
    )


def _bridge_missing_for_error(message: Any) -> tuple[str, ...]:
    if isinstance(message, str) and "executable is missing" in message:
        return ("bridge_executable",)
    return ("bridge_runtime",)


def _internal_event_type(value: Any) -> str:
    if not isinstance(value, str):
        return "unknown"
    text = value.strip()
    return normalize_event_type(text) if text else "unknown"
