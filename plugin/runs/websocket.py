from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Set, Tuple

from fastapi import WebSocket
from plugin.logging_config import logger

from plugin.core.state import state
from plugin.runs.manager import ExportListResponse, RunRecord, get_run, list_export_for_run
from plugin.runs.tokens import issue_run_token  # noqa: F401  (经 plugin.runs.__init__ 再导出)
from plugin.runs.tokens import verify_run_token


@dataclass(frozen=True)
class _Conn:
    ws: WebSocket
    run_id: str
    perm: str
    queue: "asyncio.Queue[Dict[str, Any]]"


class WsRunHub:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._conns_by_run: Dict[str, Set[_Conn]] = {}
        self._unsubs: list[Any] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._dispatch_q: "asyncio.Queue[Tuple[str, Dict[str, Any]]]" = asyncio.Queue(maxsize=2000)
        self._dispatch_task: Optional[asyncio.Task[None]] = None
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._loop = asyncio.get_running_loop()

        def _enqueue_factory(bus: str):
            def _cb(op: str, payload: Dict[str, Any]) -> None:
                rid = None
                try:
                    rid = payload.get("run_id") if isinstance(payload, dict) else None
                except Exception:
                    rid = None
                if not isinstance(rid, str) or not rid:
                    return
                evt = {"bus": bus, "op": str(op), "payload": dict(payload or {})}
                try:
                    if self._loop is None:
                        return
                    self._loop.call_soon_threadsafe(self._try_enqueue, rid, evt)
                except Exception:
                    return

            return _cb

        try:
            self._unsubs.append(state.bus_change_hub.subscribe("runs", _enqueue_factory("runs")))
            self._unsubs.append(state.bus_change_hub.subscribe("export", _enqueue_factory("export")))
        except Exception:
            for u in self._unsubs:
                try:
                    u()
                except Exception:
                    pass
            self._unsubs = []
            self._started = False
            self._loop = None
            raise

        if self._dispatch_task is None:
            self._dispatch_task = asyncio.create_task(self._dispatch_loop(), name="ws-run-hub-dispatch")

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
        except (asyncio.CancelledError, Exception):
            pass
        self._dispatch_task = None
        async with self._lock:
            self._conns_by_run.clear()
        self._started = False

    def _try_enqueue(self, run_id: str, evt: Dict[str, Any]) -> None:
        try:
            self._dispatch_q.put_nowait((run_id, evt))
        except asyncio.QueueFull:
            logger.debug("ws_run_hub dispatch queue full, dropping event for run={}", run_id)
        except Exception:
            pass

    async def _dispatch_loop(self) -> None:
        while True:
            run_id, evt = await self._dispatch_q.get()
            try:
                await self._broadcast(run_id, evt)
            except Exception:
                continue

    async def register(self, conn: _Conn) -> None:
        async with self._lock:
            s = self._conns_by_run.get(conn.run_id)
            if s is None:
                s = set()
                self._conns_by_run[conn.run_id] = s
            s.add(conn)

    async def unregister(self, conn: _Conn) -> None:
        async with self._lock:
            s = self._conns_by_run.get(conn.run_id)
            if not s:
                return
            try:
                s.discard(conn)
            except Exception:
                pass
            if not s:
                try:
                    self._conns_by_run.pop(conn.run_id, None)
                except Exception:
                    pass

    async def _broadcast(self, run_id: str, evt: Dict[str, Any]) -> None:
        async with self._lock:
            targets = list(self._conns_by_run.get(run_id, set()))
        if not targets:
            return
        for c in targets:
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


ws_run_hub = WsRunHub()


async def ws_run_endpoint(ws: WebSocket) -> None:
    await ws.accept()

    async def _close(code: int = 1008, reason: str = "") -> None:
        try:
            await ws.close(code=code, reason=reason)
        except Exception:
            pass

    try:
        auth_msg = await asyncio.wait_for(ws.receive_text(), timeout=5.0)
    except Exception:
        await _close(1008, "auth required")
        return

    if not isinstance(auth_msg, str) or len(auth_msg) > 16384:
        await _close(1008, "invalid auth")
        return

    try:
        auth = json.loads(auth_msg)
    except Exception:
        await _close(1008, "invalid auth")
        return

    if not isinstance(auth, dict) or auth.get("type") != "auth":
        await _close(1008, "auth required")
        return

    token = auth.get("token")
    if not isinstance(token, str) or not token:
        await _close(1008, "invalid token")
        return

    try:
        run_id, perm, exp = verify_run_token(token)
    except ValueError:
        await _close(1008, "invalid token")
        return
    except Exception:
        logger.debug("run websocket token verification failed", exc_info=True)
        await _close(1008, "authentication failed")
        return

    rec = get_run(run_id)
    if rec is None:
        await _close(1008, "run not found")
        return

    try:
        await ws_run_hub.start()
    except Exception:
        logger.debug("ws_run_hub.start() failed", exc_info=True)
        await ws.close(code=1011, reason="internal error")
        return

    q: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=256)
    conn = _Conn(ws=ws, run_id=run_id, perm=perm, queue=q)
    await ws_run_hub.register(conn)

    last_pong = float(time.time())

    async def _heartbeat_loop() -> None:
        nonlocal last_pong
        while True:
            await asyncio.sleep(15.0)
            if (time.time() - last_pong) > 45.0:
                await _close(1011, "heartbeat timeout")
                return
            try:
                q.put_nowait({"type": "ping"})
            except asyncio.QueueFull:
                await _close(1013, "slow client")
                return
            except Exception:
                return

    async def _send_loop() -> None:
        while True:
            msg = await q.get()
            await ws.send_text(json.dumps(msg, ensure_ascii=False, separators=(",", ":")))

    send_task = asyncio.create_task(_send_loop(), name="ws-run-send")
    hb_task = asyncio.create_task(_heartbeat_loop(), name="ws-run-heartbeat")

    async def _send_resp(rid: str, ok: bool, result: Any = None, error: Optional[str] = None) -> None:
        out = {"type": "resp", "id": rid, "ok": bool(ok)}
        if ok:
            out["result"] = result
        else:
            out["error"] = str(error or "error")
        try:
            q.put_nowait(out)
        except asyncio.QueueFull:
            await _close(1013, "slow client")

    try:
        hello = {"type": "event", "event": "session.ready", "data": {"run_id": run_id, "perm": perm, "exp": exp}}
        try:
            q.put_nowait(hello)
        except asyncio.QueueFull:
            await _close(1013, "slow client")
            return

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
            if msg.get("type") != "req":
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
                if method == "run.get":
                    r: Optional[RunRecord] = get_run(run_id)
                    if r is None:
                        await _send_resp(req_id, False, error="run not found")
                    else:
                        await _send_resp(req_id, True, result=r.model_dump())
                    continue

                if method == "export.list":
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
                    resp: ExportListResponse = list_export_for_run(run_id=run_id, after=after, limit=limit_i)
                    await _send_resp(req_id, True, result=resp.model_dump(by_alias=True))
                    continue

                await _send_resp(req_id, False, error="unknown method")
            except Exception as e:
                await _send_resp(req_id, False, error=str(e))

    except Exception:
        pass
    finally:
        try:
            await ws_run_hub.unregister(conn)
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
