from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, Optional, Tuple

import zmq
from loguru import logger

from .protocol import PROTOCOL_VERSION, err_response, ok_response
from .pub_server import MessagePlanePubServer


@dataclass
class _TopicStore:
    maxlen: int
    items: Dict[str, Deque[Dict[str, Any]]]

    def __init__(self, *, maxlen: int):
        self.maxlen = int(maxlen)
        self.items = defaultdict(lambda: deque(maxlen=self.maxlen))

    def publish(self, topic: str, payload: Dict[str, Any]) -> None:
        self.items[str(topic)].append(payload)

    def get_recent(self, topic: str, limit: int) -> list[Dict[str, Any]]:
        dq = self.items.get(str(topic))
        if not dq:
            return []
        if limit <= 0:
            return []
        if limit >= len(dq):
            return list(dq)
        return list(dq)[-limit:]


class MessagePlaneRpcServer:
    def __init__(
        self,
        *,
        endpoint: str,
        pub_server: Optional[MessagePlanePubServer] = None,
        store_maxlen: int = 20000,
    ) -> None:
        self.endpoint = endpoint
        self._ctx = zmq.Context.instance()
        self._sock = self._ctx.socket(zmq.ROUTER)
        self._sock.linger = 0
        self._sock.bind(self.endpoint)
        self._store = _TopicStore(maxlen=store_maxlen)
        self._pub = pub_server
        self._running = False

    def close(self) -> None:
        try:
            self._sock.close(linger=0)
        except Exception:
            pass

    def _recv(self) -> Optional[Tuple[list[bytes], Dict[str, Any]]]:
        try:
            parts = self._sock.recv_multipart()
        except Exception:
            return None
        if len(parts) < 2:
            return None
        identity = parts[0]
        raw = parts[-1]
        try:
            msg = json.loads(raw.decode("utf-8"))
        except Exception:
            msg = {}
        envelope = [identity]
        if len(parts) >= 3 and parts[1] == b"":
            envelope.append(b"")
        return envelope, msg

    def _send(self, envelope: list[bytes], msg: Dict[str, Any]) -> None:
        payload = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        self._sock.send_multipart([*envelope, payload])

    def _handle(self, req: Dict[str, Any]) -> Dict[str, Any]:
        req_id = str(req.get("req_id") or "")
        op = str(req.get("op") or "")
        v = req.get("v")
        if v not in (None, PROTOCOL_VERSION):
            return err_response(req_id, f"unsupported protocol version: {v!r}")

        args = req.get("args")
        if not isinstance(args, dict):
            args = {}

        if op in ("ping", "health"):
            return ok_response(req_id, {"ok": True, "ts": time.time()})

        if op == "bus.publish":
            topic = str(args.get("topic") or "")
            payload = args.get("payload")
            if not topic:
                return err_response(req_id, "topic is required")
            if not isinstance(payload, dict):
                payload = {"value": payload}
            self._store.publish(topic, payload)
            if self._pub is not None:
                self._pub.publish(topic, payload)
            return ok_response(req_id, {"accepted": True})

        if op == "bus.get_recent":
            topic = str(args.get("topic") or "")
            limit = args.get("limit", 200)
            try:
                limit_i = int(limit)
            except Exception:
                limit_i = 200
            if not topic:
                return err_response(req_id, "topic is required")
            return ok_response(req_id, {"topic": topic, "items": self._store.get_recent(topic, limit_i)})

        return err_response(req_id, f"unknown op: {op}")

    def serve_forever(self) -> None:
        self._running = True
        poller = zmq.Poller()
        poller.register(self._sock, zmq.POLLIN)
        logger.info("[message_plane] rpc server bound: {}", self.endpoint)
        while self._running:
            try:
                events = dict(poller.poll(timeout=250))
            except Exception:
                continue
            if self._sock not in events:
                continue
            recvd = self._recv()
            if recvd is None:
                continue
            envelope, req = recvd
            try:
                resp = self._handle(req)
            except Exception as e:
                req_id = str(req.get("req_id") or "") if isinstance(req, dict) else ""
                resp = err_response(req_id, f"internal error: {e}")
            try:
                self._send(envelope, resp)
            except Exception:
                pass

    def stop(self) -> None:
        self._running = False
