from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import zmq
from loguru import logger

from plugin.settings import (
    MESSAGE_PLANE_GET_RECENT_MAX_LIMIT,
    MESSAGE_PLANE_PAYLOAD_MAX_BYTES,
    MESSAGE_PLANE_STORE_MAXLEN,
    MESSAGE_PLANE_TOPIC_MAX,
    MESSAGE_PLANE_TOPIC_NAME_MAX_LEN,
    MESSAGE_PLANE_VALIDATE_MODE,
)

from .protocol import PROTOCOL_VERSION, err_response, ok_response
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
        self._sock.bind(self.endpoint)
        if stores is not None:
            self._stores = stores
        else:
            self._stores = StoreRegistry(default_store="messages")
            for name in ("messages", "events", "lifecycle", "runs", "export", "memory"):
                self._stores.register(TopicStore(name=name, maxlen=store_maxlen))
        self._pub = pub_server
        self._running = False

    def _resolve_store(self, args: Dict[str, Any]) -> Optional[TopicStore]:
        store = args.get("store")
        if store is None:
            store = args.get("bus")
        st = self._stores.get(store)
        return st

    def _dedupe_key(self, ev: Dict[str, Any]) -> Tuple[str, Any]:
        idx = ev.get("index")
        if isinstance(idx, dict):
            v = idx.get("id")
            if isinstance(v, str) and v:
                return ("id", v)
        try:
            return ("seq", int(ev.get("seq") or 0))
        except Exception:
            return ("obj", id(ev))

    def _apply_unary_op(self, items: List[Dict[str, Any]], *, op: str, params: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        if op == "limit":
            try:
                n = int(params.get("n") or 0)
            except Exception:
                n = 0
            if n <= 0:
                return []
            return list(items)[:n]

        if op == "sort":
            by = params.get("by")
            if by is None:
                by_fields = ["timestamp", "created_at", "time"]
            elif isinstance(by, str):
                by_fields = [by]
            elif isinstance(by, (list, tuple)):
                by_fields = [str(x) for x in by]
            else:
                by_fields = ["timestamp", "created_at", "time"]
            reverse = bool(params.get("reverse", False))

            def _field_value(ev: Dict[str, Any], f: str) -> Any:
                idx = ev.get("index")
                if isinstance(idx, dict) and f in idx:
                    return idx.get(f)
                payload = ev.get("payload")
                if isinstance(payload, dict) and f in payload:
                    return payload.get(f)
                if f in ev:
                    return ev.get(f)
                return None

            def _sort_key(ev: Dict[str, Any]) -> Tuple[Tuple[int, Any], ...]:
                key_parts: List[Tuple[int, Any]] = []
                for f in by_fields:
                    v = _field_value(ev, f)
                    if v is None:
                        key_parts.append((2, ""))
                    elif isinstance(v, (int, float)):
                        key_parts.append((0, v))
                    else:
                        key_parts.append((1, str(v)))
                return tuple(key_parts)

            return sorted(list(items), key=_sort_key, reverse=reverse)

        if op == "filter":
            p = dict(params) if isinstance(params, dict) else {}
            strict = bool(p.pop("strict", True))
            flt = p.get("flt")
            if isinstance(flt, dict):
                p = {**p, **flt}

            def _match(ev: Dict[str, Any]) -> bool:
                idx = ev.get("index") if isinstance(ev, dict) else None
                payload = ev.get("payload") if isinstance(ev, dict) else None
                for k in ("plugin_id", "source", "kind", "type"):
                    v = p.get(k)
                    if v is None:
                        continue
                    if isinstance(idx, dict) and idx.get(k) == v:
                        continue
                    if isinstance(payload, dict) and payload.get(k) == v:
                        continue
                    return False

                pmin = p.get("priority_min")
                if pmin is not None:
                    try:
                        pmin_i = int(pmin)
                    except Exception:
                        pmin_i = None
                    if pmin_i is not None:
                        try:
                            pri = int(idx.get("priority") or 0) if isinstance(idx, dict) else 0
                        except Exception:
                            pri = 0
                        if pri < pmin_i:
                            return False

                since_ts = p.get("since_ts")
                if since_ts is not None:
                    try:
                        s = float(since_ts)
                        t = float(idx.get("timestamp") or 0.0) if isinstance(idx, dict) else 0.0
                        if t < s:
                            return False
                    except Exception:
                        return False

                until_ts = p.get("until_ts")
                if until_ts is not None:
                    try:
                        u = float(until_ts)
                        t = float(idx.get("timestamp") or 0.0) if isinstance(idx, dict) else 0.0
                        if t > u:
                            return False
                    except Exception:
                        return False

                for prefix, key in (("plugin_id", "plugin_id"), ("source", "source"), ("kind", "kind"), ("type", "type")):
                    pat = p.get(f"{prefix}_re")
                    if pat is None:
                        continue
                    if not isinstance(pat, str) or not pat:
                        continue
                    val = None
                    if isinstance(idx, dict):
                        val = idx.get(key)
                    if val is None and isinstance(payload, dict):
                        val = payload.get(key)
                    try:
                        if not re.search(pat, str(val or "")):
                            return False
                    except re.error:
                        if strict:
                            return False
                content_re = p.get("content_re")
                if isinstance(content_re, str) and content_re:
                    content = None
                    if isinstance(payload, dict):
                        content = payload.get("content")
                    try:
                        if not re.search(content_re, str(content or "")):
                            return False
                    except re.error:
                        if strict:
                            return False

                return True

            return [ev for ev in items if _match(ev)]

        if op == "where_eq":
            field = str(params.get("field") or "").strip()
            value = params.get("value")
            if not field:
                return items
            matched_eq: List[Dict[str, Any]] = []
            for ev in items:
                idx = ev.get("index")
                payload = ev.get("payload")
                got = None
                if isinstance(idx, dict) and field in idx:
                    got = idx.get(field)
                elif isinstance(payload, dict) and field in payload:
                    got = payload.get(field)
                if got == value:
                    matched_eq.append(ev)
            return matched_eq

        if op == "where_in":
            field = str(params.get("field") or "").strip()
            values = params.get("values")
            if not field or not isinstance(values, list):
                return items
            s = set(values)
            matched_in: List[Dict[str, Any]] = []
            for ev in items:
                idx = ev.get("index")
                payload = ev.get("payload")
                got = None
                if isinstance(idx, dict) and field in idx:
                    got = idx.get(field)
                elif isinstance(payload, dict) and field in payload:
                    got = payload.get(field)
                if got in s:
                    matched_in.append(ev)
            return matched_in

        if op == "where_contains":
            field = str(params.get("field") or "").strip()
            value = str(params.get("value") or "")
            if not field or not value:
                return items
            matched_contains: List[Dict[str, Any]] = []
            for ev in items:
                idx = ev.get("index")
                payload = ev.get("payload")
                got = None
                if isinstance(idx, dict) and field in idx:
                    got = idx.get(field)
                elif isinstance(payload, dict) and field in payload:
                    got = payload.get(field)
                if value in str(got or ""):
                    matched_contains.append(ev)
            return matched_contains

        if op == "where_regex":
            field = str(params.get("field") or "").strip()
            pattern = str(params.get("pattern") or "")
            strict = bool(params.get("strict", True))
            if not field or not pattern:
                return items
            try:
                rr = re.compile(pattern)
            except re.error:
                if strict:
                    return []
                return items
            matched_regex: List[Dict[str, Any]] = []
            for ev in items:
                idx = ev.get("index")
                payload = ev.get("payload")
                got = None
                if isinstance(idx, dict) and field in idx:
                    got = idx.get(field)
                elif isinstance(payload, dict) and field in payload:
                    got = payload.get(field)
                if rr.search(str(got or "")):
                    matched_regex.append(ev)
            return matched_regex

        return None

    def _apply_binary_op(self, left: List[Dict[str, Any]], right: List[Dict[str, Any]], *, op: str) -> Optional[List[Dict[str, Any]]]:
        if op not in ("merge", "intersection", "difference"):
            return None
        left_keys = [self._dedupe_key(x) for x in left]
        right_keys = [self._dedupe_key(x) for x in right]
        set_left = set(left_keys)
        set_right = set(right_keys)
        if op == "merge":
            merged: List[Dict[str, Any]] = []
            seen_merge: set[Tuple[str, Any]] = set()
            for ev in list(left) + list(right):
                k = self._dedupe_key(ev)
                if k in seen_merge:
                    continue
                seen_merge.add(k)
                merged.append(ev)
            merged.sort(key=lambda e: int(e.get("seq") or 0), reverse=True)
            return merged
        if op == "intersection":
            kept: List[Dict[str, Any]] = []
            seen_intersection: set[Tuple[str, Any]] = set()
            for ev in left:
                k = self._dedupe_key(ev)
                if k in seen_intersection:
                    continue
                if k not in set_right:
                    continue
                seen_intersection.add(k)
                kept.append(ev)
            kept.sort(key=lambda e: int(e.get("seq") or 0), reverse=True)
            return kept
        if op == "difference":
            kept = []
            seen_difference: set[Tuple[str, Any]] = set()
            for ev in left:
                k = self._dedupe_key(ev)
                if k in seen_difference:
                    continue
                if k in set_right:
                    continue
                seen_difference.add(k)
                kept.append(ev)
            kept.sort(key=lambda e: int(e.get("seq") or 0), reverse=True)
            return kept
        return None

    def _eval_plan(self, st: TopicStore, node: Any) -> Optional[List[Dict[str, Any]]]:
        if not isinstance(node, dict):
            return None
        kind = node.get("kind")
        op = str(node.get("op") or "")
        params = node.get("params")
        if not isinstance(params, dict):
            params = {}

        if kind == "get":
            p = params.get("params")
            if not isinstance(p, dict):
                p = {}
            max_count = p.get("max_count", p.get("limit", 200))
            try:
                limit_i = int(max_count)
            except Exception:
                limit_i = 200
            if limit_i > MESSAGE_PLANE_GET_RECENT_MAX_LIMIT:
                limit_i = MESSAGE_PLANE_GET_RECENT_MAX_LIMIT

            plugin_id = p.get("plugin_id")
            since_ts = p.get("since_ts")
            priority_min = p.get("priority_min")
            source = p.get("source")
            type_ = p.get("type")
            kind_ = p.get("kind")

            return st.query(
                topic="*",
                plugin_id=plugin_id if isinstance(plugin_id, str) and plugin_id.strip() else None,
                source=source if isinstance(source, str) and source.strip() else None,
                kind=kind_ if isinstance(kind_, str) and kind_.strip() else None,
                type=type_ if isinstance(type_, str) and type_.strip() else None,
                priority_min=priority_min,
                since_ts=since_ts,
                until_ts=None,
                limit=limit_i,
            )

        if kind == "unary":
            child = node.get("child")
            base = self._eval_plan(st, child)
            if base is None:
                return None
            out = self._apply_unary_op(base, op=op, params=params)
            return out

        if kind == "binary":
            left = self._eval_plan(st, node.get("left"))
            right = self._eval_plan(st, node.get("right"))
            if left is None or right is None:
                return None
            return self._apply_binary_op(left, right, op=op)

        return None

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
        raw = parts[-1]
        try:
            msg = json.loads(raw.decode("utf-8"))
        except Exception:
            msg = {}
        envelope = parts[:-1]
        return envelope, msg

    def _send(self, envelope: list[bytes], msg: Dict[str, Any]) -> None:
        payload = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        self._sock.send_multipart([*envelope, payload])

    def _handle(self, req: Dict[str, Any]) -> Dict[str, Any]:
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
            st = self._resolve_store(args)
            if st is None:
                return err_response(req_id, "invalid store")
            topic = str(args.get("topic") or "")
            limit = args.get("limit", 200)
            try:
                limit_i = int(limit)
            except Exception:
                limit_i = 200
            if not topic:
                return err_response(req_id, "topic is required")
            if limit_i > MESSAGE_PLANE_GET_RECENT_MAX_LIMIT:
                limit_i = MESSAGE_PLANE_GET_RECENT_MAX_LIMIT
            return ok_response(req_id, {"store": st.name, "topic": topic, "items": st.get_recent(topic, limit_i)})

        if op == "bus.get_since":
            st = self._resolve_store(args)
            if st is None:
                return err_response(req_id, "invalid store")

            topic_raw = args.get("topic")
            topic = None
            if topic_raw is not None:
                topic = str(topic_raw)

            after_seq = args.get("after_seq", 0)
            limit = args.get("limit", 200)
            try:
                limit_i = int(limit)
            except Exception:
                limit_i = 200
            if limit_i > MESSAGE_PLANE_GET_RECENT_MAX_LIMIT:
                limit_i = MESSAGE_PLANE_GET_RECENT_MAX_LIMIT

            try:
                after_i = int(after_seq)
            except Exception:
                after_i = 0

            items = st.get_since(topic=topic, after_seq=after_i, limit=limit_i)
            return ok_response(
                req_id,
                {
                    "store": st.name,
                    "topic": topic,
                    "after_seq": after_i,
                    "items": items,
                },
            )

        if op == "bus.query":
            st = self._resolve_store(args)
            if st is None:
                return err_response(req_id, "invalid store")

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
                type=args.get("type"),
                priority_min=args.get("priority_min"),
                since_ts=args.get("since_ts"),
                until_ts=args.get("until_ts"),
                limit=limit_i,
            )
            return ok_response(
                req_id,
                {
                    "store": st.name,
                    "topic": topic,
                    "items": items,
                },
            )

        if op == "bus.replay":
            st = self._resolve_store(args)
            if st is None:
                return err_response(req_id, "invalid store")
            plan = args.get("plan")
            if plan is None:
                plan = args.get("trace")
            if not isinstance(plan, dict):
                return err_response(req_id, "plan is required")
            items = self._eval_plan(st, plan)
            if items is None:
                return err_response(req_id, "unsupported plan")
            if len(items) > MESSAGE_PLANE_GET_RECENT_MAX_LIMIT:
                items = list(items)[:MESSAGE_PLANE_GET_RECENT_MAX_LIMIT]
            return ok_response(
                req_id,
                {
                    "store": st.name,
                    "items": items,
                },
            )

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
