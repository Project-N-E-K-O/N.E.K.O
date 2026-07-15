"""Local WebSocket bridge transport shared by live providers.

External danmaku tools are treated as replaceable local bridges. This transport
only connects to localhost URLs, parses JSON messages, and hands sanitized
adapter payloads back to the concrete provider.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol
from urllib.parse import quote, urlparse, urlunparse

from websockets.asyncio.client import connect as websockets_connect

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
_PUBLIC_STATES = {"disconnected", "connecting", "connected", "receiving", "reconnecting", "unsupported"}
_SENSITIVE_AUTH_RE = re.compile(r"(?i)\bauthorization\b\s*[:=]\s*[^,;]+")
_SENSITIVE_TEXT_RE = re.compile(
    r"(?i)\b(?:"
    r"cookie|authorization|x-tt-token|ttwid|odin_tt|sessionid|sessionid_ss|sid_tt|uid_tt|"
    r"webcast_sign|signature|sign|token"
    r")\b\s*[:=]\s*[^;&\s]+"
)


@dataclass(frozen=True, slots=True)
class LiveBridgeEvent:
    """Provider-neutral event emitted by a local bridge adapter."""

    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = 0.0


@dataclass(frozen=True, slots=True)
class LiveBridgeState:
    state: str = "unsupported"
    adapter_id: str = ""
    last_error: str = ""
    last_event_at: float = 0.0
    last_event_type: str = ""

    def safe_state(self) -> str:
        text = self.state.strip().lower() if isinstance(self.state, str) else ""
        return text if text in _PUBLIC_STATES else "unsupported"

    def safe_error(self) -> str:
        return _safe_public_text(self.last_error, limit=160)


class LiveBridgeAdapter(Protocol):
    """Adapter contract for one external bridge's JSON protocol."""

    adapter_id: str

    def bridge_url(self, room_ref: str) -> str:
        """Return a local WebSocket URL for the room."""

    def map_message(self, message: Any, *, room_ref: str) -> list[dict[str, Any]]:
        """Map one decoded JSON message to provider payload dictionaries."""


@dataclass(frozen=True, slots=True)
class LiveBridgeStartRequest:
    room_ref: str
    adapter: LiveBridgeAdapter
    emit: Callable[[LiveBridgeEvent], Any] | None = None
    on_state: Callable[[LiveBridgeState], Any] | None = None


class LiveBridgeTransport:
    """Bounded local WebSocket reader for replaceable external bridge tools."""

    def __init__(
        self,
        *,
        connect_factory: Any = None,
        reconnect_attempts: int = 0,
        reconnect_sleep: Any = None,
    ) -> None:
        self._connect_factory = connect_factory or websockets_connect
        self._reconnect_attempts = max(0, int(reconnect_attempts))
        self._reconnect_sleep = reconnect_sleep or asyncio.sleep
        self._task: asyncio.Task[None] | None = None

    async def start(self, request: LiveBridgeStartRequest) -> LiveBridgeState:
        await self.stop()
        adapter = request.adapter
        adapter_id = _safe_adapter_id(getattr(adapter, "adapter_id", ""))
        if not adapter_id:
            return LiveBridgeState(state="unsupported", last_error="external bridge adapter is missing")
        room_ref = _safe_room_ref(request.room_ref)
        if not room_ref:
            return LiveBridgeState(
                state="unsupported",
                adapter_id=adapter_id,
                last_error="external bridge room_ref is missing",
            )
        url = _safe_bridge_url(adapter.bridge_url(room_ref))
        if not url:
            return LiveBridgeState(
                state="unsupported",
                adapter_id=adapter_id,
                last_error="external bridge URL must be local websocket",
            )

        loop = asyncio.get_running_loop()
        ready: asyncio.Future[LiveBridgeState] = loop.create_future()
        self._task = loop.create_task(self._run(request, room_ref=room_ref, url=url, ready=ready))
        try:
            return await asyncio.wait_for(asyncio.shield(ready), timeout=8.0)
        except TimeoutError:
            await self.stop()
            return LiveBridgeState(
                state="disconnected",
                adapter_id=adapter_id,
                last_error="external bridge open timed out",
            )

    async def stop(self) -> LiveBridgeState:
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(task, timeout=3.0)
        return LiveBridgeState(state="disconnected")

    async def _run(
        self,
        request: LiveBridgeStartRequest,
        *,
        room_ref: str,
        url: str,
        ready: asyncio.Future[LiveBridgeState],
    ) -> None:
        adapter_id = _safe_adapter_id(request.adapter.adapter_id)
        connected_once = False
        attempt = 0
        while True:
            self._notify_state(
                request,
                LiveBridgeState(
                    state="reconnecting" if connected_once else "connecting",
                    adapter_id=adapter_id,
                ),
            )
            try:
                async with self._connect_factory(
                    url,
                    open_timeout=6,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=3,
                    max_size=1024 * 1024,
                    proxy=None,
                ) as ws:
                    connected = LiveBridgeState(state="connected", adapter_id=adapter_id)
                    self._notify_state(request, connected)
                    _resolve_ready(ready, connected)
                    connected_once = True
                    attempt = 0
                    async for raw_message in ws:
                        for payload in request.adapter.map_message(_json_message(raw_message), room_ref=room_ref):
                            event = LiveBridgeEvent(payload=payload, ts=time.time())
                            self._emit(request, event)
                            event_type = payload.get("event_type") or payload.get("type") or "unknown"
                            self._notify_state(
                                request,
                                LiveBridgeState(
                                    state="receiving",
                                    adapter_id=adapter_id,
                                    last_event_at=event.ts,
                                    last_event_type=event_type if isinstance(event_type, str) else "unknown",
                                ),
                            )
                failure = LiveBridgeState(state="disconnected", adapter_id=adapter_id)
            except asyncio.CancelledError:
                state = LiveBridgeState(state="disconnected", adapter_id=adapter_id)
                self._notify_state(request, state)
                _resolve_ready(ready, state)
                raise
            except Exception as exc:
                failure = LiveBridgeState(
                    state="disconnected",
                    adapter_id=adapter_id,
                    last_error=f"external bridge failed: {type(exc).__name__}",
                )
            if not connected_once or attempt >= self._reconnect_attempts:
                self._notify_state(request, failure)
                _resolve_ready(ready, failure)
                return
            attempt += 1
            self._notify_state(
                request,
                LiveBridgeState(
                    state="reconnecting",
                    adapter_id=adapter_id,
                    last_error=failure.safe_error(),
                ),
            )
            await self._reconnect_sleep(float(2 ** (attempt - 1)))

    def _emit(self, request: LiveBridgeStartRequest, event: LiveBridgeEvent) -> None:
        if request.emit is not None:
            request.emit(event)

    def _notify_state(self, request: LiveBridgeStartRequest, state: LiveBridgeState) -> None:
        if request.on_state is not None:
            request.on_state(state)


def local_bridge_url(base_url: Any, room_ref: str) -> str:
    """Build a local bridge URL by appending the quoted room path segment."""

    if not isinstance(base_url, str):
        return ""
    parsed = urlparse(base_url.strip())
    if parsed.scheme not in {"ws", "wss"} or parsed.username or parsed.password:
        return ""
    path = parsed.path.rstrip("/") + "/" + quote(_safe_room_ref(room_ref), safe="")
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def _safe_bridge_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"ws", "wss"} or parsed.username or parsed.password:
        return ""
    host = parsed.hostname
    if host not in _LOCAL_HOSTS:
        return ""
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def _json_message(value: Any) -> Any:
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return {}
    if not isinstance(value, str):
        return {}
    try:
        decoded = json.loads(value)
    except Exception:
        return {}
    return decoded


def _safe_adapter_id(value: Any) -> str:
    text = _safe_public_text(value, limit=48)
    return text if text.replace("_", "").replace("-", "").isalnum() else ""


def _safe_room_ref(value: Any) -> str:
    text = _safe_public_text(value, limit=160)
    return text if text else ""


def _resolve_ready(ready: asyncio.Future[LiveBridgeState], state: LiveBridgeState) -> None:
    if not ready.done():
        ready.set_result(state)


def _safe_public_text(value: Any, *, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split()).strip()
    text = _SENSITIVE_AUTH_RE.sub("[redacted]", text)
    text = _SENSITIVE_TEXT_RE.sub("[redacted]", text)
    return text[:limit] if len(text) > limit else text
