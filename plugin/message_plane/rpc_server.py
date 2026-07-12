from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional, Tuple

import zmq
import ormsgpack
from plugin.logging_config import logger

from plugin.settings import (
    MESSAGE_PLANE_GET_RECENT_MAX_LIMIT,
    MESSAGE_PLANE_PAYLOAD_MAX_BYTES,
    MESSAGE_PLANE_STORE_MAXLEN,
    MESSAGE_PLANE_TOPIC_MAX,
    MESSAGE_PLANE_TOPIC_NAME_MAX_LEN,
    MESSAGE_PLANE_VALIDATE_MODE,
)

from pydantic import ValidationError

from .protocol import (
    PROTOCOL_VERSION,
    BusGetRecentArgs,
    BusQueryArgs,
    err_response,
    ok_response,
)
from .pub_server import MessagePlanePubServer
from .stores import StoreRegistry, TopicStore
from .validation import validate_rpc_envelope


class MessagePlaneRpcServer:
    def __init__(
        self,
        *,
        endpoint: str,
        pub_server: Optional[MessagePlanePubServer] = None,
        store_maxlen: int = MESSAGE_PLANE_STORE_MAXLEN,
        stores: Optional[StoreRegistry] = None,
    ) -> None:
        self.endpoint = endpoint
        self._ctx = zmq.Context.instance()
        self._sock = self._ctx.socket(zmq.ROUTER)
        self._sock.linger = 0
        # Warn if binding to non-localhost address
        if not any(x in endpoint for x in ("127.0.0.1", "localhost", "::1")):
            logger.warning("binding to non-localhost endpoint: {} - ensure this is intentional", endpoint)
        self._sock.bind(self.endpoint)
        if stores is not None:
            self._stores = stores
        else:
            self._stores = StoreRegistry(default_store="messages")
            # conversations 是独立的 store，用于存储对话上下文（与 messages 分离）
            for name in ("messages", "events", "lifecycle", "runs", "export", "memory", "conversations"):
                self._stores.register(TopicStore(name=name, maxlen=store_maxlen))
        self._pub = pub_server
        self._running = False

    def _resolve_store(self, args: Dict[str, Any]) -> Optional[TopicStore]:
        store = args.get("store")
        if store is None:
            store = args.get("bus")
        st = self._stores.get(store)
        return st

    def close(self) -> None:
        try:
            self._sock.close(linger=0)
        except Exception:
            pass

    def _recv(self) -> Optional[Tuple[list[bytes], Dict[str, Any], str]]:
        try:
            parts = self._sock.recv_multipart()
        except Exception:
            return None
        if len(parts) < 2:
            return None
        raw = parts[-1]
        enc = "json"
        try:
            msg = json.loads(raw.decode("utf-8"))
        except Exception:
            try:
                msg = ormsgpack.unpackb(raw)
                enc = "msgpack"
            except Exception:
                msg = {}
        envelope = parts[:-1]
        return envelope, msg, enc

    def _send(self, envelope: list[bytes], msg: Dict[str, Any], *, enc: str) -> None:
        if enc == "msgpack":
            payload = ormsgpack.packb(msg)
        else:
            payload = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        self._sock.send_multipart([*envelope, payload])

    def _light_item(self, ev: Dict[str, Any]) -> Dict[str, Any]:
        idx = ev.get("index")
        return {
            "seq": ev.get("seq"),
            "ts": ev.get("ts"),
            "store": ev.get("store"),
            "topic": ev.get("topic"),
            "index": idx if isinstance(idx, dict) else {},
        }

    def _handle(self, req: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(req, dict):
            return err_response("", "invalid rpc envelope")
        req_id = str(req.get("req_id") or "")
        env, err = validate_rpc_envelope(req, mode=MESSAGE_PLANE_VALIDATE_MODE)
        if err is not None:
            return err_response(req_id, err)

        if env is not None:
            op = env.op
            args = env.args
        else:
            op = str(req.get("op") or "")
            v = req.get("v")
            if v not in (None, PROTOCOL_VERSION):
                return err_response(req_id, f"unsupported protocol version: {v!r}")

            args = req.get("args")
            if not isinstance(args, dict):
                args = {}

        validate_mode = str(MESSAGE_PLANE_VALIDATE_MODE or "off")

        if op in ("ping", "health"):
            return ok_response(req_id, {"ok": True, "ts": time.time()})

        if op == "bus.list_topics":
            st = self._resolve_store(args)
            if st is None:
                return err_response(req_id, "invalid store")
            return ok_response(
                req_id,
                {
                    "store": st.name,
                    "stores": self._stores.list_store_names(),
                    "topics": st.list_topics(),
                    "topic_count": len(st.meta),
                },
            )

        if op == "bus.publish":
            st = self._resolve_store(args)
            if st is None:
                return err_response(req_id, "invalid store")
            topic = str(args.get("topic") or "")
            payload = args.get("payload")
            if not topic:
                return err_response(req_id, "topic is required")
            if len(topic) > MESSAGE_PLANE_TOPIC_NAME_MAX_LEN:
                return err_response(req_id, "topic too long")

            is_new_topic = topic not in st.meta
            if is_new_topic and len(st.meta) >= MESSAGE_PLANE_TOPIC_MAX:
                return err_response(req_id, "too many topics")
            if not isinstance(payload, dict):
                payload = {"value": payload}

            try:
                payload_bytes = ormsgpack.packb(payload)
            except Exception:
                try:
                    payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                except Exception:
                    return err_response(req_id, "payload not JSON-serializable")
            if len(payload_bytes) > MESSAGE_PLANE_PAYLOAD_MAX_BYTES:
                return err_response(req_id, "payload too large")

            event = st.publish(topic, payload)
            if self._pub is not None:
                self._pub.publish(f"{st.name}.{topic}", event)
            return ok_response(req_id, {"accepted": True, "event": event})

        if op == "bus.get_recent":
            if validate_mode in ("warn", "strict"):
                try:
                    _ = BusGetRecentArgs.model_validate(args)
                except ValidationError as e:
                    if validate_mode == "warn":
                        logger.warning("invalid args for {}: {}", op, e)
                    else:
                        return err_response(req_id, "invalid args", code="BAD_ARGS", details={"op": op})

            st = self._resolve_store(args)
            if st is None:
                return err_response(req_id, "invalid store")
            topic = str(args.get("topic") or "")
            light = bool(args.get("light", False))
            limit = args.get("limit", 200)
            try:
                limit_i = int(limit)
            except Exception:
                limit_i = 200
            if not topic:
                return err_response(req_id, "topic is required")
            if limit_i > MESSAGE_PLANE_GET_RECENT_MAX_LIMIT:
                limit_i = MESSAGE_PLANE_GET_RECENT_MAX_LIMIT
            items = st.get_recent(topic, limit_i)
            if light:
                try:
                    items = [self._light_item(ev) for ev in items]
                except Exception:
                    items = []
            return ok_response(req_id, {"store": st.name, "topic": topic, "items": items, "light": bool(light)})

        if op == "bus.query":
            if validate_mode in ("warn", "strict"):
                try:
                    _ = BusQueryArgs.model_validate(args)
                except ValidationError as e:
                    if validate_mode == "warn":
                        logger.warning("invalid args for {}: {}", op, e)
                    else:
                        return err_response(req_id, "invalid args", code="BAD_ARGS", details={"op": op})

            st = self._resolve_store(args)
            if st is None:
                return err_response(req_id, "invalid store")

            light = bool(args.get("light", False))

            topic_raw = args.get("topic")
            topic = None
            if topic_raw is not None:
                topic = str(topic_raw)

            limit = args.get("limit", 200)
            try:
                limit_i = int(limit)
            except Exception:
                limit_i = 200
            if limit_i > MESSAGE_PLANE_GET_RECENT_MAX_LIMIT:
                limit_i = MESSAGE_PLANE_GET_RECENT_MAX_LIMIT

            items = st.query(
                topic=topic,
                plugin_id=args.get("plugin_id"),
                source=args.get("source"),
                kind=args.get("kind"),
                type_=args.get("type"),
                priority_min=args.get("priority_min"),
                since_ts=args.get("since_ts"),
                until_ts=args.get("until_ts"),
                limit=limit_i,
            )
            if light:
                try:
                    items = [self._light_item(ev) for ev in items]
                except Exception:
                    items = []
            return ok_response(
                req_id,
                {
                    "store": st.name,
                    "topic": topic,
                    "items": items,
                    "light": bool(light),
                },
            )

        return err_response(req_id, f"unknown op: {op}")

    def serve_forever(self) -> None:
        self._running = True
        poller = zmq.Poller()
        poller.register(self._sock, zmq.POLLIN)
        logger.info("rpc server bound: {}", self.endpoint)
        while self._running:
            try:
                events = dict(poller.poll(timeout=250))
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                if not self._running:
                    break
                continue
            if self._sock not in events:
                continue
            recvd = self._recv()
            if recvd is None:
                continue
            envelope, req, enc = recvd
            try:
                resp = self._handle(req)
            except Exception:
                req_id = str(req.get("req_id") or "") if isinstance(req, dict) else ""
                logger.exception("rpc handler error for req_id={}", req_id)
                resp = err_response(req_id, "internal error")
            try:
                self._send(envelope, resp, enc=enc)
            except Exception:
                logger.warning("failed to send response")

    def stop(self) -> None:
        self._running = False
