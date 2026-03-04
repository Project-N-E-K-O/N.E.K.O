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
from plugin.server.application.runs import RunService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.runs.tokens import verify_run_token

logger = get_logger("server.runs.websocket")

JsonObject = dict[str, object]


@dataclass(frozen=True)
class _Conn:
    ws: WebSocket
    run_id: str
    perm: str
    queue: asyncio.Queue[JsonObject]


def _normalize_mapping(raw: object) -> JsonObject | None:
    if not isinstance(raw, Mapping):
        return None

    normalized: JsonObject = {}
    for key, value in raw.items():
        if isinstance(key, str):
            normalized[key] = value
    return normalized


def _coerce_limit(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return 200

    if parsed <= 0:
        return 200
    if parsed > 500:
        return 500
    return parsed


class WsRunHub:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._conns_by_run: dict[str, set[_Conn]] = {}
        self._unsubs: list[Callable[[], None]] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._dispatch_q: asyncio.Queue[tuple[str, JsonObject]] = asyncio.Queue(maxsize=2000)
        self._dispatch_task: asyncio.Task[None] | None = None
        self._started = False

    async def start(self) -> None:
        if self._started:
            return

        self._started = True
        self._loop = asyncio.get_running_loop()

        def _enqueue_factory(bus: str) -> Callable[[str, dict[str, object]], None]:
            def _cb(op: str, payload: dict[str, object]) -> None:
                run_id_obj = payload.get("run_id")
                if not isinstance(run_id_obj, str):
                    return
                run_id = run_id_obj.strip()
                if not run_id:
                    return

                evt: JsonObject = {
                    "bus": bus,
                    "op": str(op),
                    "payload": _normalize_mapping(payload) or {},
                }
                event_loop = self._loop
                if event_loop is None:
                    return

                try:
                    event_loop.call_soon_threadsafe(self._try_enqueue, run_id, evt)
                except RuntimeError as exc:
                    logger.debug("failed to enqueue run hub event: err_type={}, err={}", type(exc).__name__, str(exc))

            return _cb

        try:
            self._unsubs.append(state.bus_change_hub.subscribe("runs", _enqueue_factory("runs")))
            self._unsubs.append(state.bus_change_hub.subscribe("export", _enqueue_factory("export")))
        except (RuntimeError, ValueError, TypeError) as exc:
            logger.warning(
                "failed to subscribe run hub events: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )
            self._unsubs.clear()

        if self._dispatch_task is None:
            self._dispatch_task = asyncio.create_task(self._dispatch_loop(), name="ws-run-hub-dispatch")

    async def stop(self) -> None:
        for unsub in list(self._unsubs):
            try:
                unsub()
            except (RuntimeError, ValueError, TypeError) as exc:
                logger.debug(
                    "failed to unsubscribe run hub callback: err_type={}, err={}",
                    type(exc).__name__,
                    str(exc),
                )
        self._unsubs.clear()

        if self._dispatch_task is not None:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                logger.debug("run hub dispatch task cancelled")
        self._dispatch_task = None

        async with self._lock:
            self._conns_by_run.clear()
        self._started = False

    def _try_enqueue(self, run_id: str, evt: JsonObject) -> None:
        try:
            self._dispatch_q.put_nowait((run_id, evt))
        except asyncio.QueueFull:
            logger.warning("run hub dispatch queue is full; dropping event")

    async def _dispatch_loop(self) -> None:
        while True:
            run_id, evt = await self._dispatch_q.get()
            try:
                await self._broadcast(run_id, evt)
            except asyncio.CancelledError:
                raise
            except (RuntimeError, ValueError, TypeError, ConnectionError) as exc:
                logger.debug(
                    "run hub broadcast failed: err_type={}, err={}",
                    type(exc).__name__,
                    str(exc),
                )

    async def register(self, conn: _Conn) -> None:
        async with self._lock:
            conn_set = self._conns_by_run.get(conn.run_id)
            if conn_set is None:
                conn_set = set()
                self._conns_by_run[conn.run_id] = conn_set
            conn_set.add(conn)

    async def unregister(self, conn: _Conn) -> None:
        async with self._lock:
            conn_set = self._conns_by_run.get(conn.run_id)
            if not conn_set:
                return

            conn_set.discard(conn)
            if conn_set:
                return

            self._conns_by_run.pop(conn.run_id, None)

    async def _broadcast(self, run_id: str, evt: JsonObject) -> None:
        async with self._lock:
            targets = list(self._conns_by_run.get(run_id, set()))
        if not targets:
            return

        out: JsonObject = {"type": "event", "event": "bus.change", "data": evt}
        for conn in targets:
            try:
                conn.queue.put_nowait(out)
            except asyncio.QueueFull:
                await self.unregister(conn)
                try:
                    await asyncio.wait_for(conn.ws.close(code=1013, reason="slow client"), timeout=1.0)
                except (WebSocketDisconnect, TimeoutError, RuntimeError, ConnectionError):
                    logger.debug("failed to close slow run websocket client")


ws_run_hub = WsRunHub()
run_service = RunService()


async def ws_run_endpoint(ws: WebSocket) -> None:
    await ws.accept()

    async def _close(code: int = 1008, reason: str = "") -> None:
        try:
            await ws.close(code=code, reason=reason)
        except (WebSocketDisconnect, RuntimeError, ConnectionError):
            logger.debug("run websocket already closed")

    async def _send_json(payload: JsonObject) -> bool:
        try:
            raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            logger.error(
                "failed to serialize run websocket payload: err_type={}, err={}",
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

    token_obj = auth.get("token")
    if not isinstance(token_obj, str):
        await _close(1008, "invalid token")
        return

    token = token_obj.strip()
    if not token:
        await _close(1008, "invalid token")
        return

    try:
        run_id, perm, exp = verify_run_token(token)
    except ValueError as exc:
        await _close(1008, str(exc))
        return

    try:
        run_service.get_run(run_id)
    except ServerDomainError as error:
        await _close(1008, error.message)
        return

    await ws_run_hub.start()

    queue: asyncio.Queue[JsonObject] = asyncio.Queue(maxsize=256)
    conn = _Conn(ws=ws, run_id=run_id, perm=perm, queue=queue)
    await ws_run_hub.register(conn)

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

    send_task = asyncio.create_task(_send_loop(), name="ws-run-send")
    hb_task = asyncio.create_task(_heartbeat_loop(), name="ws-run-heartbeat")

    async def _send_resp(req_id: str, ok: bool, result: object | None = None, error: str | None = None) -> bool:
        out: JsonObject = {"type": "resp", "id": req_id, "ok": bool(ok)}
        if ok:
            out["result"] = result
        else:
            out["error"] = str(error or "error")
        return await _send_json(out)

    try:
        hello: JsonObject = {
            "type": "event",
            "event": "session.ready",
            "data": {"run_id": run_id, "perm": perm, "exp": exp},
        }
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

            if msg.get("type") != "req":
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

            params_raw = msg.get("params")
            if params_raw is None:
                params: JsonObject = {}
            else:
                params_mapping = _normalize_mapping(params_raw)
                if params_mapping is None:
                    if not await _send_resp(req_id, False, error="invalid params"):
                        return
                    continue
                params = params_mapping

            if method == "run.get":
                try:
                    run_record = run_service.get_run(run_id)
                except ServerDomainError as error:
                    if not await _send_resp(req_id, False, error=error.message):
                        return
                    continue

                if not await _send_resp(req_id, True, result=run_record.model_dump()):
                    return
                continue

            if method == "export.list":
                after_obj = params.get("after")
                after: str | None = None
                if isinstance(after_obj, str):
                    stripped_after = after_obj.strip()
                    if stripped_after:
                        after = stripped_after

                limit = _coerce_limit(params.get("limit", 200))
                try:
                    export_resp = run_service.list_export_for_run(run_id=run_id, after=after, limit=limit)
                except ServerDomainError as error:
                    if not await _send_resp(req_id, False, error=error.message):
                        return
                    continue

                if not await _send_resp(req_id, True, result=export_resp.model_dump(by_alias=True)):
                    return
                continue

            if not await _send_resp(req_id, False, error="unknown method"):
                return
    finally:
        await ws_run_hub.unregister(conn)

        send_task.cancel()
        hb_task.cancel()

        try:
            await send_task
        except asyncio.CancelledError:
            logger.debug("run websocket send task cancelled")

        try:
            await hb_task
        except asyncio.CancelledError:
            logger.debug("run websocket heartbeat task cancelled")

        await _close(1000, "")
