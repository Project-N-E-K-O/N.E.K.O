from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Set, Tuple

from fastapi import WebSocket

from plugin.core.state import state
from plugin.server.runs.manager import ExportListResponse, RunRecord, get_run, list_export_for_run
from plugin.settings import RUN_TOKEN_SECRET, RUN_TOKEN_TTL_SECONDS


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * ((4 - (len(s) % 4)) % 4)
    return base64.urlsafe_b64decode((s + pad).encode("ascii"))


def issue_run_token(*, run_id: str, perm: str = "read") -> Tuple[str, int]:
    exp = int(time.time()) + int(RUN_TOKEN_TTL_SECONDS)
    payload = {
        "run_id": str(run_id),
        "exp": exp,
        "nonce": secrets.token_urlsafe(16),
        "perm": str(perm),
    }
    payload_raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    payload_b64 = _b64url_encode(payload_raw)
    key = str(RUN_TOKEN_SECRET).encode("utf-8")
    sig = hmac.new(key, payload_b64.encode("ascii"), hashlib.sha256).digest()
    token = payload_b64 + "." + _b64url_encode(sig)
    return token, exp


def verify_run_token(token: str) -> Tuple[str, str, int]:
    if not isinstance(token, str) or "." not in token:
        raise ValueError("invalid token")
    p1, p2 = token.split(".", 1)
    if not p1 or not p2:
        raise ValueError("invalid token")
    key = str(RUN_TOKEN_SECRET).encode("utf-8")
    expected = hmac.new(key, p1.encode("ascii"), hashlib.sha256).digest()
    got = _b64url_decode(p2)
    if not hmac.compare_digest(expected, got):
        raise ValueError("invalid token")

    payload_raw = _b64url_decode(p1)
    payload = json.loads(payload_raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("invalid token")

    run_id = payload.get("run_id")
    perm = payload.get("perm")
    exp = payload.get("exp")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("invalid token")
    if not isinstance(perm, str) or not perm.strip():
        perm = "read"
    if isinstance(exp, bool):
        raise ValueError("invalid token")
    if not isinstance(exp, int):
        if exp is None:
            raise ValueError("invalid token")
        try:
            exp = int(exp)
        except Exception:
            raise ValueError("invalid token")
    if int(time.time()) > int(exp):
        raise ValueError("expired")
    return run_id.strip(), perm.strip(), int(exp)


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
            self._unsubs = []

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
        except Exception:
            pass
        self._dispatch_task = None
        async with self._lock:
            self._conns_by_run.clear()
        self._started = False

    def _try_enqueue(self, run_id: str, evt: Dict[str, Any]) -> None:
        try:
            self._dispatch_q.put_nowait((run_id, evt))
        except Exception:
            return

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
    except Exception as e:
        await _close(1008, str(e))
        return

    rec = get_run(run_id)
    if rec is None:
        await _close(1008, "run not found")
        return

    await ws_run_hub.start()

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
                await ws.send_text(json.dumps({"type": "ping"}, ensure_ascii=False, separators=(",", ":")))
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
        await ws.send_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))

    try:
        hello = {"type": "event", "event": "session.ready", "data": {"run_id": run_id, "perm": perm, "exp": exp}}
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
        except Exception:
            pass
        try:
            await hb_task
        except Exception:
            pass
        try:
            await _close(1000, "")
        except Exception:
            pass
