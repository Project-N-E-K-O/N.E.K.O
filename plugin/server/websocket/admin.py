from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Set, Tuple

from fastapi import WebSocket

from plugin.core.state import state
from plugin.server.infrastructure.auth import get_admin_code
from plugin.server.management import stop_plugin
from plugin.server.runs.manager import RunCreateRequest, get_run, list_export_for_run, list_runs, cancel_run, create_run


@dataclass(frozen=True)
class _Conn:
    ws: WebSocket
    plugin_id: Optional[str]
    queue: "asyncio.Queue[Dict[str, Any]]"


class WsAdminHub:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._conns: Set[_Conn] = set()
        self._unsubs: list[Any] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._dispatch_q: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(maxsize=2000)
        self._dispatch_task: Optional[asyncio.Task[None]] = None
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._loop = asyncio.get_running_loop()

        def _cb_factory(bus: str):
            def _cb(op: str, payload: Dict[str, Any]) -> None:
                evt = {"bus": bus, "op": str(op), "payload": dict(payload or {})}
                try:
                    if self._loop is None:
                        return
                    self._loop.call_soon_threadsafe(self._try_enqueue, evt)
                except Exception:
                    return

            return _cb

        try:
            self._unsubs.append(state.bus_change_hub.subscribe("runs", _cb_factory("runs")))
            self._unsubs.append(state.bus_change_hub.subscribe("export", _cb_factory("export")))
        except Exception:
            self._unsubs = []

        if self._dispatch_task is None:
            self._dispatch_task = asyncio.create_task(self._dispatch_loop(), name="ws-admin-hub-dispatch")

    async def stop(self) -> None:
        for u in list(self._unsubs):
            try:
                u()
            except Exception:
                pass
        self._unsubs.clear()
        try:
            if self._dispatch_task is not None:
                self._dispatch_task.cancel()
        except Exception:
            pass
        try:
            if self._dispatch_task is not None:
                await self._dispatch_task
        except Exception:
            pass
        self._dispatch_task = None
        async with self._lock:
            self._conns.clear()
        self._started = False

    def _try_enqueue(self, evt: Dict[str, Any]) -> None:
        try:
            self._dispatch_q.put_nowait(evt)
        except Exception:
            return

    async def _dispatch_loop(self) -> None:
        while True:
            evt = await self._dispatch_q.get()
            try:
                await self._broadcast(evt)
            except Exception:
                continue

    async def register(self, conn: _Conn) -> None:
        async with self._lock:
            self._conns.add(conn)

    async def unregister(self, conn: _Conn) -> None:
        async with self._lock:
            try:
                self._conns.discard(conn)
            except Exception:
                pass

    async def _broadcast(self, evt: Dict[str, Any]) -> None:
        bus = evt.get("bus")
        payload = evt.get("payload")
        plugin_id: Optional[str] = None

        if bus == "runs":
            if isinstance(payload, dict):
                pid = payload.get("plugin_id")
                if isinstance(pid, str) and pid:
                    plugin_id = pid
        elif bus == "export":
            rid = None
            if isinstance(payload, dict):
                rid = payload.get("run_id")
            if isinstance(rid, str) and rid:
                r = get_run(rid)
                if r is not None:
                    try:
                        plugin_id = r.plugin_id
                    except Exception:
                        plugin_id = None

        async with self._lock:
            targets = list(self._conns)

        for c in targets:
            if c.plugin_id is not None and plugin_id is not None:
                if c.plugin_id != plugin_id:
                    continue
            try:
                c.queue.put_nowait({"type": "event", "event": "bus.change", "data": evt})
            except Exception:
                try:
                    await self.unregister(c)
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(c.ws.close(code=1013, reason="slow client"), timeout=1.0)
                except Exception:
                    pass


ws_admin_hub = WsAdminHub()


async def ws_admin_endpoint(ws: WebSocket) -> None:
    await ws.accept()

    async def _close(code: int = 1008, reason: str = "") -> None:
        try:
            await ws.close(code=code, reason=reason)
        except Exception:
            pass

    try:
        auth_raw = await asyncio.wait_for(ws.receive_text(), timeout=5.0)
    except Exception:
        await _close(1008, "auth required")
        return

    try:
        auth = json.loads(auth_raw)
    except Exception:
        await _close(1008, "invalid auth")
        return

    if not isinstance(auth, dict) or auth.get("type") != "auth":
        await _close(1008, "auth required")
        return

    code = auth.get("code")
    if not isinstance(code, str) or not code:
        await _close(1008, "invalid code")
        return

    server_code = get_admin_code()
    if server_code is None:
        await _close(1011, "auth not initialized")
        return

    if code.strip().upper() != str(server_code).strip().upper():
        await _close(1008, "forbidden")
        return

    await ws_admin_hub.start()

    q: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=512)
    conn = _Conn(ws=ws, plugin_id=None, queue=q)
    await ws_admin_hub.register(conn)

    last_pong = float(time.time())

    async def _heartbeat_loop() -> None:
        nonlocal last_pong
        while True:
            await asyncio.sleep(15.0)
            if (time.time() - last_pong) > 45.0:
                await _close(1011, "heartbeat timeout")
                return
            try:
                await ws.send_text(json.dumps({"type": "ping"}, ensure_ascii=False, separators=(",", ":")))
            except Exception:
                return

    async def _send_loop() -> None:
        while True:
            msg = await q.get()
            await ws.send_text(json.dumps(msg, ensure_ascii=False, separators=(",", ":")))

    send_task = asyncio.create_task(_send_loop(), name="ws-admin-send")
    hb_task = asyncio.create_task(_heartbeat_loop(), name="ws-admin-heartbeat")

    async def _send_resp(req_id: str, ok: bool, result: Any = None, error: Optional[str] = None) -> None:
        out = {"type": "resp", "id": req_id, "ok": bool(ok)}
        if ok:
            out["result"] = result
        else:
            out["error"] = str(error or "error")
        await ws.send_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))

    try:
        hello = {"type": "event", "event": "session.ready", "data": {"role": "admin"}}
        await ws.send_text(json.dumps(hello, ensure_ascii=False, separators=(",", ":")))

        while True:
            raw = await ws.receive_text()
            if not isinstance(raw, str) or len(raw) > 262144:
                await _close(1009, "message too large")
                return
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if not isinstance(msg, dict):
                continue

            if msg.get("type") == "pong":
                last_pong = float(time.time())
                continue

            mtype = msg.get("type")
            if mtype == "subscribe":
                pid = msg.get("plugin_id")
                if pid is None:
                    new_conn = _Conn(ws=ws, plugin_id=None, queue=q)
                elif isinstance(pid, str) and pid.strip():
                    new_conn = _Conn(ws=ws, plugin_id=pid.strip(), queue=q)
                else:
                    new_conn = _Conn(ws=ws, plugin_id=None, queue=q)
                await ws_admin_hub.unregister(conn)
                conn = new_conn
                await ws_admin_hub.register(conn)
                await ws.send_text(json.dumps({"type": "event", "event": "subscribed", "data": {"plugin_id": conn.plugin_id}}, ensure_ascii=False, separators=(",", ":")))
                continue

            if mtype != "req":
                continue

            req_id = msg.get("id")
            method = msg.get("method")
            params = msg.get("params")
            if not isinstance(req_id, str) or not req_id:
                continue
            if not isinstance(method, str) or not method:
                await _send_resp(req_id, False, error="missing method")
                continue
            if params is None:
                params = {}
            if not isinstance(params, dict):
                await _send_resp(req_id, False, error="invalid params")
                continue

            try:
                if method == "runs.list":
                    pid = params.get("plugin_id")
                    plugin_id = pid.strip() if isinstance(pid, str) and pid.strip() else None
                    items = list_runs(plugin_id=plugin_id)
                    await _send_resp(req_id, True, result=[r.model_dump() for r in items])
                    continue

                if method == "run.get":
                    rid = params.get("run_id")
                    if not isinstance(rid, str) or not rid.strip():
                        await _send_resp(req_id, False, error="run_id required")
                        continue
                    r = get_run(rid.strip())
                    if r is None:
                        await _send_resp(req_id, False, error="run not found")
                    else:
                        await _send_resp(req_id, True, result=r.model_dump())
                    continue

                if method == "export.list":
                    rid = params.get("run_id")
                    if not isinstance(rid, str) or not rid.strip():
                        await _send_resp(req_id, False, error="run_id required")
                        continue
                    after = params.get("after")
                    limit = params.get("limit", 200)
                    if after is not None and not isinstance(after, str):
                        after = None
                    try:
                        limit_i = int(limit)
                    except Exception:
                        limit_i = 200
                    if limit_i <= 0:
                        limit_i = 200
                    if limit_i > 500:
                        limit_i = 500
                    resp = list_export_for_run(run_id=rid.strip(), after=after, limit=limit_i)
                    await _send_resp(req_id, True, result=resp.model_dump(by_alias=True))
                    continue

                if method == "run.create":
                    pid = params.get("plugin_id")
                    eid = params.get("entry_id")
                    args = params.get("args")
                    if not isinstance(pid, str) or not pid.strip():
                        await _send_resp(req_id, False, error="plugin_id required")
                        continue
                    if not isinstance(eid, str) or not eid.strip():
                        await _send_resp(req_id, False, error="entry_id required")
                        continue
                    if args is None:
                        args = {}
                    if not isinstance(args, dict):
                        await _send_resp(req_id, False, error="args must be object")
                        continue
                    req = RunCreateRequest(plugin_id=pid.strip(), entry_id=eid.strip(), args=args)
                    created = await create_run(req, client_host=None)
                    await _send_resp(req_id, True, result=created.model_dump())
                    continue

                if method == "run.cancel":
                    rid = params.get("run_id")
                    reason = params.get("reason")
                    if not isinstance(rid, str) or not rid.strip():
                        await _send_resp(req_id, False, error="run_id required")
                        continue
                    rec = cancel_run(rid.strip(), reason=str(reason) if isinstance(reason, str) else None)
                    if rec is None:
                        await _send_resp(req_id, False, error="run not found")
                    else:
                        await _send_resp(req_id, True, result=rec.model_dump())
                    continue

                if method == "plugin.stop":
                    pid = params.get("plugin_id")
                    if not isinstance(pid, str) or not pid.strip():
                        await _send_resp(req_id, False, error="plugin_id required")
                        continue
                    out = await stop_plugin(pid.strip())
                    await _send_resp(req_id, True, result=out)
                    continue

                await _send_resp(req_id, False, error="unknown method")
            except Exception as e:
                await _send_resp(req_id, False, error=str(e))

    except Exception:
        pass
    finally:
        try:
            await ws_admin_hub.unregister(conn)
        except Exception:
            pass
        try:
            send_task.cancel()
        except Exception:
            pass
        try:
            hb_task.cancel()
        except Exception:
            pass
        try:
            await send_task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        try:
            await hb_task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        try:
            await _close(1000, "")
        except Exception:
            pass
