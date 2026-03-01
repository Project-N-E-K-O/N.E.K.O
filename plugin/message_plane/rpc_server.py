from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import zmq
import ormsgpack
from loguru import logger

try:
    import regex as safe_regex  # type: ignore
except ImportError:  # pragma: no cover
    safe_regex = None

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


_MAX_USER_REGEX_LEN = 128
_MAX_REGEX_TEXT_LEN = 1024
_REGEX_TIMEOUT_SECONDS = 0.02
_MAX_PLAN_DEPTH = 16


def _validate_regex_pattern(pattern: str, *, strict: bool) -> Optional[bool]:
    if not isinstance(pattern, str) or not pattern:
        return None
    if len(pattern) > _MAX_USER_REGEX_LEN:
        return False if strict else None
    
    if safe_regex is not None:
        try:
            safe_regex.compile(pattern, timeout=_REGEX_TIMEOUT_SECONDS)
            return True
        except Exception:
            return False if strict else None
    
    if any(ch in pattern for ch in ("*", "+", "{", "}", "(", ")", "|", "[", "]", "?", "\\")):
        return False if strict else None
    try:
        re.compile(pattern)
        return True
    except re.error:
        return False if strict else None


def _maybe_match_regex(pattern: str, value: Any, *, strict: bool) -> Optional[bool]:
    if not isinstance(pattern, str) or not pattern:
        return None
    if len(pattern) > _MAX_USER_REGEX_LEN:
        return False if strict else None
    s = str(value or "")
    if len(s) > _MAX_REGEX_TEXT_LEN:
        s = s[:_MAX_REGEX_TEXT_LEN]

    if safe_regex is not None:
        try:
            return bool(safe_regex.search(pattern, s, timeout=_REGEX_TIMEOUT_SECONDS))
        except Exception:
            return False if strict else None

    if any(ch in pattern for ch in ("*", "+", "{", "}", "(", ")", "|", "[", "]", "?", "\\")):
        return False if strict else None
    try:
        return bool(re.search(pattern, s))
    except re.error:
        return False if strict else None


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

            # Pre-extract filter values to avoid repeated dict lookups
            f_plugin_id = p.get("plugin_id")
            f_source = p.get("source")
            f_kind = p.get("kind")
            f_type = p.get("type")
            f_conversation_id = p.get("conversation_id")  # 新增：对话ID过滤
            f_pmin = p.get("priority_min")
            f_since_ts = p.get("since_ts")
            f_until_ts = p.get("until_ts")
            f_plugin_id_re = p.get("plugin_id_re")
            f_source_re = p.get("source_re")
            f_kind_re = p.get("kind_re")
            f_type_re = p.get("type_re")
            f_content_re = p.get("content_re")

            # Pre-convert numeric filters
            pmin_i: Optional[int] = None
            if f_pmin is not None:
                try:
                    pmin_i = int(f_pmin)
                except Exception:
                    pass
            since_f: Optional[float] = None
            if f_since_ts is not None:
                try:
                    since_f = float(f_since_ts)
                except Exception:
                    pass
            until_f: Optional[float] = None
            if f_until_ts is not None:
                try:
                    until_f = float(f_until_ts)
                except Exception:
                    pass

            # Check if we have any regex filters
            has_regex = bool(f_plugin_id_re or f_source_re or f_kind_re or f_type_re or f_content_re)

            matched: List[Dict[str, Any]] = []
            for ev in items:
                if not isinstance(ev, dict):
                    continue
                idx = ev.get("index")
                idx_is_dict = isinstance(idx, dict)

                # Fast path: check simple equality filters using index only
                if f_plugin_id is not None:
                    if not (idx_is_dict and idx.get("plugin_id") == f_plugin_id):
                        payload = ev.get("payload")
                        if not (isinstance(payload, dict) and payload.get("plugin_id") == f_plugin_id):
                            continue
                if f_source is not None:
                    if not (idx_is_dict and idx.get("source") == f_source):
                        payload = ev.get("payload")
                        if not (isinstance(payload, dict) and payload.get("source") == f_source):
                            continue
                if f_kind is not None:
                    if not (idx_is_dict and idx.get("kind") == f_kind):
                        payload = ev.get("payload")
                        if not (isinstance(payload, dict) and payload.get("kind") == f_kind):
                            continue
                if f_type is not None:
                    if not (idx_is_dict and idx.get("type") == f_type):
                        payload = ev.get("payload")
                        if not (isinstance(payload, dict) and payload.get("type") == f_type):
                            continue
                
                # conversation_id 过滤（用于对话上下文关联）
                if f_conversation_id is not None:
                    if not (idx_is_dict and idx.get("conversation_id") == f_conversation_id):
                        # 回退到 payload.metadata.conversation_id
                        payload = ev.get("payload")
                        if isinstance(payload, dict):
                            metadata = payload.get("metadata")
                            if not (isinstance(metadata, dict) and metadata.get("conversation_id") == f_conversation_id):
                                continue
                        else:
                            continue

                # Priority filter
                if pmin_i is not None:
                    try:
                        pri = int(idx.get("priority") or 0) if idx_is_dict else 0
                    except Exception:
                        pri = 0
                    if pri < pmin_i:
                        continue

                # Timestamp filters
                if since_f is not None or until_f is not None:
                    try:
                        t = float(idx.get("timestamp") or 0.0) if idx_is_dict else 0.0
                    except Exception:
                        t = 0.0
                    if since_f is not None and t < since_f:
                        continue
                    if until_f is not None and t > until_f:
                        continue

                # Regex filters (slower path, only if needed)
                if has_regex:
                    payload = ev.get("payload")
                    payload_is_dict = isinstance(payload, dict)
                    skip = False
                    for pat, key in ((f_plugin_id_re, "plugin_id"), (f_source_re, "source"), (f_kind_re, "kind"), (f_type_re, "type")):
                        if not pat or not isinstance(pat, str):
                            continue
                        val = idx.get(key) if idx_is_dict else None
                        if val is None and payload_is_dict:
                            val = payload.get(key)
                        verdict = _maybe_match_regex(pat, val, strict=strict)
                        if verdict is not None and not verdict:
                            skip = True
                            break
                    if skip:
                        continue
                    if f_content_re and isinstance(f_content_re, str):
                        content = payload.get("content") if payload_is_dict else None
                        verdict = _maybe_match_regex(f_content_re, content, strict=strict)
                        if verdict is not None and not verdict:
                            continue

                matched.append(ev)
            return matched

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
            try:
                s = set(values)
            except TypeError:
                return items
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
            pattern_ok = _validate_regex_pattern(pattern, strict=strict)
            if pattern_ok is False:
                return [] if strict else items
            if pattern_ok is None:
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
                verdict = _maybe_match_regex(pattern, got, strict=strict)
                if verdict:
                    matched_regex.append(ev)
            return matched_regex

        return None

    def _apply_binary_op(self, left: List[Dict[str, Any]], right: List[Dict[str, Any]], *, op: str) -> Optional[List[Dict[str, Any]]]:
        if op not in ("merge", "intersection", "difference"):
            return None
        right_keys = [self._dedupe_key(x) for x in right]
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

    def _eval_plan(self, st: TopicStore, node: Any, depth: int = 0) -> Optional[List[Dict[str, Any]]]:
        if depth > _MAX_PLAN_DEPTH:
            return None
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

            topic = p.get("topic")
            if topic is None:
                topic = "all"
            else:
                try:
                    topic = str(topic)
                except Exception:
                    topic = "all"
            if not isinstance(topic, str) or not topic:
                topic = "all"

            pid_norm = plugin_id if isinstance(plugin_id, str) and plugin_id.strip() else None
            src_norm = source if isinstance(source, str) and source.strip() else None
            kind_norm = kind_ if isinstance(kind_, str) and kind_.strip() else None
            type_norm = type_ if isinstance(type_, str) and type_.strip() else None

            # Fast path: when there is no filtering, use get_recent which avoids scanning.
            if (
                pid_norm is None
                and src_norm is None
                and kind_norm is None
                and type_norm is None
                and priority_min is None
                and since_ts is None
            ):
                return st.get_recent(str(topic), limit_i)

            return st.query(
                topic=str(topic),
                plugin_id=pid_norm,
                source=src_norm,
                kind=kind_norm,
                type_=type_norm,
                priority_min=priority_min,
                since_ts=since_ts,
                until_ts=None,
                limit=limit_i,
            )

        if kind == "unary":
            child = node.get("child")
            base = self._eval_plan(st, child, depth + 1)
            if base is None:
                return None
            out = self._apply_unary_op(base, op=op, params=params)
            return out

        if kind == "binary":
            left = self._eval_plan(st, node.get("left"), depth + 1)
            right = self._eval_plan(st, node.get("right"), depth + 1)
            if left is None or right is None:
                return None
            return self._apply_binary_op(left, right, op=op)

        return None

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

        if op == "bus.get_since":
            st = self._resolve_store(args)
            if st is None:
                return err_response(req_id, "invalid store")

            light = bool(args.get("light", False))

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
                    "after_seq": after_i,
                    "items": items,
                    "light": bool(light),
                },
            )

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

        if op == "bus.replay":
            # Progressive freeze (low overhead): bus.replay is a hot path for BusList.reload.
            # Avoid pydantic validation here; do minimal shape checks only.
            if validate_mode == "strict":
                st_raw = args.get("store") or args.get("bus")
                if not isinstance(st_raw, str) or not st_raw:
                    return err_response(req_id, "invalid args", code="BAD_ARGS", details={"op": op, "field": "store"})
                plan_raw = args.get("plan")
                if plan_raw is None:
                    plan_raw = args.get("trace")
                if not isinstance(plan_raw, dict):
                    return err_response(req_id, "invalid args", code="BAD_ARGS", details={"op": op, "field": "plan"})
                limit_raw = args.get("limit")
                if limit_raw is not None:
                    try:
                        if int(limit_raw) <= 0:
                            return err_response(
                                req_id,
                                "invalid args",
                                code="BAD_ARGS",
                                details={"op": op, "field": "limit"},
                            )
                    except Exception:
                        return err_response(req_id, "invalid args", code="BAD_ARGS", details={"op": op, "field": "limit"})
            elif validate_mode == "warn":
                try:
                    plan_raw = args.get("plan") or args.get("trace")
                    if not isinstance(plan_raw, dict):
                        logger.warning("invalid args for {}: missing/invalid plan", op)
                except Exception:
                    pass

            st = self._resolve_store(args)
            if st is None:
                return err_response(req_id, "invalid store")
            plan = args.get("plan")
            if plan is None:
                plan = args.get("trace")
            if not isinstance(plan, dict):
                return err_response(req_id, "plan is required")
            light = bool(args.get("light", False))
            items = self._eval_plan(st, plan)
            if items is None:
                return err_response(req_id, "unsupported plan")
            if len(items) > MESSAGE_PLANE_GET_RECENT_MAX_LIMIT:
                items = list(items)[:MESSAGE_PLANE_GET_RECENT_MAX_LIMIT]
            if light:
                try:
                    items = [self._light_item(ev) for ev in list(items)]
                except Exception:
                    items = []
            return ok_response(
                req_id,
                {
                    "store": st.name,
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
