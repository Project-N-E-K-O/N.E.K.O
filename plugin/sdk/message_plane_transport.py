from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, Optional

import ormsgpack

try:
    import zmq
except Exception:  # pragma: no cover
    zmq = None


class MessagePlaneRpcClient:
    def __init__(self, *, plugin_id: str, endpoint: str) -> None:
        if zmq is None:
            raise RuntimeError("pyzmq is not available")
        self._plugin_id = str(plugin_id)
        self._endpoint = str(endpoint)
        try:
            import threading

            self._tls = threading.local()
        except Exception:
            self._tls = None

    def _get_sock(self):
        if self._tls is not None:
            sock = getattr(self._tls, "sock", None)
            if sock is not None:
                return sock
        if zmq is None:
            return None
        ctx = zmq.Context.instance()
        sock = ctx.socket(zmq.DEALER)
        ident = f"mp:{self._plugin_id}:{int(time.time() * 1000)}".encode("utf-8")
        try:
            sock.setsockopt(zmq.IDENTITY, ident)
        except Exception:
            pass
        try:
            sock.setsockopt(zmq.LINGER, 0)
        except Exception:
            pass
        sock.connect(self._endpoint)
        if self._tls is not None:
            try:
                self._tls.sock = sock
            except Exception:
                pass
        return sock

    def _next_req_id(self) -> str:
        if self._tls is not None:
            try:
                n = int(getattr(self._tls, "req_seq", 0) or 0) + 1
                self._tls.req_seq = n
                return f"{self._plugin_id}:{n}"
            except Exception:
                pass
        return str(uuid.uuid4())

    def request(self, *, op: str, args: Dict[str, Any], timeout: float) -> Optional[Dict[str, Any]]:
        if zmq is None:
            return None
        sock = self._get_sock()
        if sock is None:
            return None
        req_id = self._next_req_id()
        req = {"v": 1, "op": str(op), "req_id": req_id, "args": dict(args or {}), "from_plugin": self._plugin_id}

        enc = "msgpack"
        try:
            raw = ormsgpack.packb(req)
        except Exception:
            enc = "json"
            try:
                raw = json.dumps(req, ensure_ascii=False).encode("utf-8")
            except Exception:
                return None

        try:
            sock.send(raw, flags=0)
        except Exception:
            return None

        deadline = time.time() + max(0.0, float(timeout))
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            try:
                if sock.poll(timeout=int(remaining * 1000), flags=zmq.POLLIN) == 0:
                    continue
            except Exception:
                return None
            try:
                resp_raw = sock.recv(flags=0)
            except Exception:
                return None

            resp = None
            if enc == "msgpack":
                try:
                    resp = ormsgpack.unpackb(resp_raw)
                except Exception:
                    resp = None
            if resp is None:
                try:
                    resp = json.loads(resp_raw.decode("utf-8"))
                except Exception:
                    resp = None

            if not isinstance(resp, dict):
                continue

            if resp.get("req_id") != req_id:
                continue

            v = resp.get("v")
            if not isinstance(v, int) or v != 1:
                continue

            ok = resp.get("ok")
            if not isinstance(ok, bool):
                continue

            # Keep the rest of the response flexible (result/error payload) for forward compatibility,
            # but guarantee that we only return well-formed envelopes.
            return resp


def format_rpc_error(err: Any) -> str:
    if err is None:
        return "message_plane error"
    if isinstance(err, str):
        return err
    if isinstance(err, dict):
        code = err.get("code")
        msg = err.get("message")
        if isinstance(code, str) and isinstance(msg, str):
            return f"{code}: {msg}" if code else msg
        if isinstance(msg, str):
            return msg
    try:
        return str(err)
    except Exception:
        return "message_plane error"
