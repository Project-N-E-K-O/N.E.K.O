from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from plugin.core.state import state
from plugin.logging_config import get_logger
from plugin.server.application.admin import AdminCommandService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.auth import get_admin_code

logger = get_logger("server.websocket.admin")

JsonObject = dict[str, object]


@dataclass(frozen=True)
class _Conn:
    ws: WebSocket
    plugin_id: str | None
    queue: asyncio.Queue[JsonObject]


def _normalize_mapping(raw: object) -> JsonObject | None:
    if not isinstance(raw, Mapping):
        return None

    normalized: JsonObject = {}
    for key, value in raw.items():
        if isinstance(key, str):
            normalized[key] = value
    return normalized


def _normalize_plugin_id(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if not value:
        return None
    return value


class WsAdminHub:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._conns: set[_Conn] = set()
        self._unsubs: list[Callable[[], None]] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._dispatch_q: asyncio.Queue[JsonObject] = asyncio.Queue(maxsize=2000)
        self._dispatch_task: asyncio.Task[None] | None = None
        self._started = False

    async def start(self) -> None:
        if self._started:
            return

        self._started = True
        self._loop = asyncio.get_running_loop()

        def _cb_factory(bus: str) -> Callable[[str, dict[str, object]], None]:
            def _cb(op: str, payload: dict[str, object]) -> None:
                evt: JsonObject = {
                    "bus": bus,
                    "op": str(op),
                    "payload": _normalize_mapping(payload) or {},
                }
                event_loop = self._loop
                if event_loop is None:
                    return
                try:
                    event_loop.call_soon_threadsafe(self._try_enqueue, evt)
                except RuntimeError as exc:
                    logger.debug("failed to enqueue admin hub event: err_type={}, err={}", type(exc).__name__, str(exc))

            return _cb

        try:
            self._unsubs.append(state.bus_change_hub.subscribe("runs", _cb_factory("runs")))
            self._unsubs.append(state.bus_change_hub.subscribe("export", _cb_factory("export")))
        except (RuntimeError, ValueError, TypeError) as exc:
            logger.warning(
                "failed to subscribe admin hub events: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )
            self._unsubs.clear()

        if self._dispatch_task is None:
            self._dispatch_task = asyncio.create_task(self._dispatch_loop(), name="ws-admin-hub-dispatch")

    async def stop(self) -> None:
        for unsub in list(self._unsubs):
            try:
                unsub()
            except (RuntimeError, ValueError, TypeError) as exc:
                logger.debug(
                    "failed to unsubscribe admin hub callback: err_type={}, err={}",
                    type(exc).__name__,
                    str(exc),
                )
        self._unsubs.clear()

        if self._dispatch_task is not None:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                logger.debug("admin hub dispatch task cancelled")
        self._dispatch_task = None

        async with self._lock:
            self._conns.clear()
        self._started = False

    def _try_enqueue(self, evt: JsonObject) -> None:
        try:
            self._dispatch_q.put_nowait(evt)
        except asyncio.QueueFull:
            logger.warning("admin hub dispatch queue is full; dropping event")

    async def _dispatch_loop(self) -> None:
        while True:
            evt = await self._dispatch_q.get()
            try:
                await self._broadcast(evt)
            except asyncio.CancelledError:
                raise
            except (RuntimeError, ValueError, TypeError, ConnectionError) as exc:
                logger.debug(
                    "admin hub broadcast failed: err_type={}, err={}",
                    type(exc).__name__,
                    str(exc),
                )

    async def register(self, conn: _Conn) -> None:
        async with self._lock:
            self._conns.add(conn)

    async def unregister(self, conn: _Conn) -> None:
        async with self._lock:
            self._conns.discard(conn)

    async def _broadcast(self, evt: JsonObject) -> None:
        payload = _normalize_mapping(evt.get("payload"))
        plugin_id = _normalize_plugin_id(payload.get("plugin_id")) if payload is not None else None

        async with self._lock:
            targets = list(self._conns)

        for conn in targets:
            if conn.plugin_id is not None and plugin_id is not None and conn.plugin_id != plugin_id:
                continue

            out: JsonObject = {"type": "event", "event": "bus.change", "data": evt}
            try:
                conn.queue.put_nowait(out)
            except asyncio.QueueFull:
                await self.unregister(conn)
                try:
                    await asyncio.wait_for(conn.ws.close(code=1013, reason="slow client"), timeout=1.0)
                except (WebSocketDisconnect, TimeoutError, RuntimeError, ConnectionError):
                    logger.debug("failed to close slow admin websocket client")


ws_admin_hub = WsAdminHub()
admin_command_service = AdminCommandService()


async def ws_admin_endpoint(ws: WebSocket) -> None:
    await ws.accept()

    async def _close(code: int = 1008, reason: str = "") -> None:
        try:
            await ws.close(code=code, reason=reason)
        except (WebSocketDisconnect, RuntimeError, ConnectionError):
            logger.debug("admin websocket already closed")

    async def _send_json(payload: JsonObject) -> bool:
        try:
            raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            logger.error(
                "failed to serialize admin websocket payload: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )
            return False

        try:
            await ws.send_text(raw)
            return True
        except (WebSocketDisconnect, RuntimeError, ConnectionError):
            return False

    try:
        auth_raw = await asyncio.wait_for(ws.receive_text(), timeout=5.0)
    except asyncio.TimeoutError:
        await _close(1008, "auth required")
        return
    except (WebSocketDisconnect, RuntimeError, ConnectionError):
        await _close(1008, "auth required")
        return

    if not isinstance(auth_raw, str) or len(auth_raw) > 16384:
        await _close(1008, "invalid auth")
        return

    try:
        auth_obj = json.loads(auth_raw)
    except json.JSONDecodeError:
        await _close(1008, "invalid auth")
        return

    auth = _normalize_mapping(auth_obj)
    if auth is None or auth.get("type") != "auth":
        await _close(1008, "auth required")
        return

    code_obj = auth.get("code")
    if not isinstance(code_obj, str):
        await _close(1008, "invalid code")
        return

    code = code_obj.strip()
    if not code:
        await _close(1008, "invalid code")
        return

    server_code = get_admin_code()
    if server_code is None:
        await _close(1011, "auth not initialized")
        return

    if code.upper() != str(server_code).strip().upper():
        await _close(1008, "forbidden")
        return

    await ws_admin_hub.start()

    queue: asyncio.Queue[JsonObject] = asyncio.Queue(maxsize=512)
    conn = _Conn(ws=ws, plugin_id=None, queue=queue)
    await ws_admin_hub.register(conn)

    last_pong = float(time.time())

    async def _heartbeat_loop() -> None:
        nonlocal last_pong
        while True:
            await asyncio.sleep(15.0)
            if (time.time() - last_pong) > 45.0:
                await _close(1011, "heartbeat timeout")
                return

            ping_payload: JsonObject = {"type": "ping"}
            sent = await _send_json(ping_payload)
            if not sent:
                return

    async def _send_loop() -> None:
        while True:
            msg = await queue.get()
            sent = await _send_json(msg)
            if not sent:
                return

    send_task = asyncio.create_task(_send_loop(), name="ws-admin-send")
    hb_task = asyncio.create_task(_heartbeat_loop(), name="ws-admin-heartbeat")

    async def _send_resp(req_id: str, ok: bool, result: object | None = None, error: str | None = None) -> bool:
        out: JsonObject = {"type": "resp", "id": req_id, "ok": bool(ok)}
        if ok:
            out["result"] = result
        else:
            out["error"] = str(error or "error")
        return await _send_json(out)

    try:
        hello: JsonObject = {"type": "event", "event": "session.ready", "data": {"role": "admin"}}
        if not await _send_json(hello):
            return

        while True:
            try:
                raw = await ws.receive_text()
            except WebSocketDisconnect:
                return
            except (RuntimeError, ConnectionError):
                return

            if len(raw) > 262144:
                await _close(1009, "message too large")
                return

            try:
                msg_obj = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg = _normalize_mapping(msg_obj)
            if msg is None:
                continue

            if msg.get("type") == "pong":
                last_pong = float(time.time())
                continue

            msg_type = msg.get("type")
            if msg_type == "subscribe":
                new_plugin_id = _normalize_plugin_id(msg.get("plugin_id"))
                new_conn = _Conn(ws=ws, plugin_id=new_plugin_id, queue=queue)
                await ws_admin_hub.unregister(conn)
                conn = new_conn
                await ws_admin_hub.register(conn)
                subscribed_payload: JsonObject = {
                    "type": "event",
                    "event": "subscribed",
                    "data": {"plugin_id": conn.plugin_id},
                }
                sent = await _send_json(subscribed_payload)
                if not sent:
                    return
                continue

            if msg_type != "req":
                continue

            req_id_obj = msg.get("id")
            if not isinstance(req_id_obj, str) or not req_id_obj:
                continue
            req_id = req_id_obj

            method_obj = msg.get("method")
            if not isinstance(method_obj, str):
                if not await _send_resp(req_id, False, error="missing method"):
                    return
                continue

            method = method_obj.strip()
            if not method:
                if not await _send_resp(req_id, False, error="missing method"):
                    return
                continue

            params_obj = msg.get("params")
            try:
                result = await admin_command_service.execute(method=method, raw_params=params_obj)
            except ServerDomainError as error:
                if not await _send_resp(req_id, False, error=error.message):
                    return
                continue

            if not await _send_resp(req_id, True, result=result):
                return
    finally:
        await ws_admin_hub.unregister(conn)

        send_task.cancel()
        hb_task.cancel()

        try:
            await send_task
        except asyncio.CancelledError:
            logger.debug("admin websocket send task cancelled")

        try:
            await hb_task
        except asyncio.CancelledError:
            logger.debug("admin websocket heartbeat task cancelled")

        await _close(1000, "")
