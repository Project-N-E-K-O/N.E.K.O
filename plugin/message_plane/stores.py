from __future__ import annotations

import time
import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, Optional

from plugin.logging_config import logger


@dataclass
class TopicStore:
    name: str
    maxlen: int

    def __post_init__(self) -> None:
        self.maxlen = int(self.maxlen)
        self.items: Dict[str, Deque[Dict[str, Any]]] = defaultdict(lambda: deque(maxlen=self.maxlen))
        self.meta: Dict[str, Dict[str, Any]] = {}
        self._seq: int = 0
        self._lock = threading.RLock()

    def _next_seq(self) -> int:
        # Caller is expected to hold _lock.
        self._seq += 1
        return self._seq

    def list_topics(self) -> list[Dict[str, Any]]:
        with self._lock:
            meta_items = list(self.meta.items())
        out: list[Dict[str, Any]] = []
        for topic, m in meta_items:
            out.append({"topic": topic, **(m or {})})
        out.sort(key=lambda x: float(x.get("last_ts") or 0.0), reverse=True)
        return out

    def publish(self, topic: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        t = str(topic)
        now = time.time()
        idx = self._extract_index(payload, now)
        with self._lock:
            seq = self._next_seq()
            event = {
                "seq": seq,
                "ts": now,
                "store": self.name,
                "topic": t,
                "payload": payload,
                "index": idx,
            }
            self.items[t].append(event)
            m = self.meta.get(t)
            if m is None:
                self.meta[t] = {"created_at": now, "last_ts": now, "count_total": 1}
            else:
                m["last_ts"] = now
                m["count_total"] = int(m.get("count_total") or 0) + 1
            return event

    def _extract_index(self, payload: Dict[str, Any], default_ts: float) -> Dict[str, Any]:
        plugin_id = payload.get("plugin_id")
        if not isinstance(plugin_id, str):
            plugin_id = None

        source = payload.get("source")
        if not isinstance(source, str):
            source = None

        try:
            priority = int(payload.get("priority", 0))
        except (ValueError, TypeError):
            priority = 0

        kind = payload.get("kind")
        if not isinstance(kind, str) or not kind:
            kind = None

        type_ = payload.get("type")
        if not isinstance(type_, str) or not type_:
            type_ = payload.get("message_type")
        if not isinstance(type_, str) or not type_:
            type_ = None

        ts_raw = payload.get("timestamp")
        if ts_raw is None:
            ts_raw = payload.get("time")
        if isinstance(ts_raw, (int, float)):
            ts = float(ts_raw)
        elif isinstance(ts_raw, str):
            try:
                ts = float(ts_raw)
            except Exception:
                ts = float(default_ts)
        else:
            ts = float(default_ts)

        record_id = None
        for k in ("message_id", "event_id", "lifecycle_id", "id", "task_id", "run_id"):
            v = payload.get(k)
            if isinstance(v, str) and v:
                record_id = v
                break

        # ``generation`` is projected because a light=True read gets ONLY the
        # index back. The frames contract (plugin/core/bus/frames.py) tells a
        # puller to dedupe on generation/id; id was already here, so leaving
        # generation in the payload alone made a light read silently report
        # generation=None for a frame that has one. None means "this record
        # carries no generation" -- the projection never invents a value.
        generation_raw = payload.get("generation")
        if isinstance(generation_raw, bool) or not isinstance(generation_raw, (int, float)):
            generation = None
        else:
            generation = int(generation_raw)

        # Extract conversation_id from metadata (for conversation-context association).
        conversation_id = None
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            cid = metadata.get("conversation_id")
            if isinstance(cid, str) and cid:
                conversation_id = cid

        return {
            "plugin_id": plugin_id,
            "source": source,
            "priority": priority,
            "kind": kind,
            "type": type_,
            "timestamp": ts,
            "id": record_id,
            "generation": generation,
            "conversation_id": conversation_id,
        }

    def get_recent(self, topic: str, limit: int) -> list[Dict[str, Any]]:
        t = str(topic)
        if limit <= 0:
            return []

        # Optimistic fast path: avoid waiting behind the publish lock under heavy ingest.
        # Deque iteration can raise if mutated concurrently; retry a few times then fall back.
        dq = self.items.get(t)
        if not dq:
            return []
        limit_i = int(limit)
        for _ in range(3):
            try:
                dq_len = len(dq)
                if limit_i >= dq_len:
                    return list(dq)
                # More efficient: directly slice from the end without multiple reversals
                start_idx = dq_len - limit_i
                return [dq[i] for i in range(start_idx, dq_len)]
            except RuntimeError:
                continue
            except Exception:
                break

        with self._lock:
            dq = self.items.get(t)
            if not dq:
                return []
            dq_len = len(dq)
            if limit_i >= dq_len:
                return list(dq)
            start_idx = dq_len - limit_i
            return [dq[i] for i in range(start_idx, dq_len)]

    def query(
        self,
        *,
        topic: Optional[str],
        plugin_id: Optional[str] = None,
        source: Optional[str] = None,
        kind: Optional[str] = None,
        type_: Optional[str] = None,
        conversation_id: Optional[str] = None,
        priority_min: Optional[int] = None,
        since_ts: Optional[float] = None,
        until_ts: Optional[float] = None,
        limit: int = 200,
    ) -> list[Dict[str, Any]]:
        nn = int(limit)
        if nn <= 0:
            return []

        topic_q = None if topic is None else str(topic)
        
        # Pre-normalize filter values outside the lock
        pid = str(plugin_id) if isinstance(plugin_id, str) and plugin_id else None
        src = str(source) if isinstance(source, str) and source else None
        kd = str(kind) if isinstance(kind, str) and kind else None
        tp = str(type_) if isinstance(type_, str) and type_ else None
        # Matched against ``index``, not the payload: the writer files
        # conversation_id inside ``metadata`` and _extract_index is what lifts
        # it to a comparable place. A top-level payload lookup matches nothing.
        cid = str(conversation_id) if isinstance(conversation_id, str) and conversation_id else None
        try:
            pmin = int(priority_min) if priority_min is not None else None
        except (ValueError, TypeError):
            pmin = None
        try:
            s_ts = float(since_ts) if since_ts is not None else None
        except (ValueError, TypeError):
            s_ts = None
        try:
            u_ts = float(until_ts) if until_ts is not None else None
        except (ValueError, TypeError):
            u_ts = None

        out: list[Dict[str, Any]] = []
        
        with self._lock:
            if topic_q is None or topic_q.strip() in ("", "*"):
                topics = list(self.items.keys())
            else:
                topics = [topic_q]
            
            # Filter while iterating, avoid copying entire deque
            for t in topics:
                dq = self.items.get(t)
                if not dq:
                    continue
                
                for ev in dq:
                    idx = ev.get("index")
                    if not isinstance(idx, dict):
                        continue

                    if pid is not None and idx.get("plugin_id") != pid:
                        continue
                    if src is not None and idx.get("source") != src:
                        continue
                    if kd is not None and idx.get("kind") != kd:
                        continue
                    if tp is not None and idx.get("type") != tp:
                        continue
                    # Exact match only. A record with no conversation_id has
                    # ``None`` here and is excluded, and an unknown id simply
                    # matches nothing -- an empty result, never an error.
                    if cid is not None and idx.get("conversation_id") != cid:
                        continue
                    if pmin is not None:
                        try:
                            if int(idx.get("priority") or 0) < pmin:
                                continue
                        except Exception:
                            continue
                    if s_ts is not None:
                        try:
                            if float(idx.get("timestamp") or 0.0) < s_ts:
                                continue
                        except Exception:
                            continue
                    if u_ts is not None:
                        try:
                            if float(idx.get("timestamp") or 0.0) > u_ts:
                                continue
                        except Exception:
                            continue

                    out.append(ev)

        out.sort(key=lambda e: int(e.get("seq") or 0), reverse=True)
        if nn >= len(out):
            return out
        return out[:nn]

    def replace_topic(self, topic: str, records: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
        t = str(topic)
        now = time.time()
        ml = int(self.maxlen)
        if ml <= 0:
            ml = 1
        dq = deque(maxlen=ml)
        with self._lock:
            self.items[t] = dq
            self.meta[t] = {"created_at": now, "last_ts": now, "count_total": 0}

            out: list[Dict[str, Any]] = []
            for rec in records:
                if not isinstance(rec, dict):
                    continue
                out.append(self.publish(t, rec))
            return out


@dataclass
class StoreRegistry:
    default_store: str

    def __post_init__(self) -> None:
        self._stores: Dict[str, TopicStore] = {}

    def register(self, store: TopicStore) -> None:
        self._stores[store.name] = store

    def get(self, name: Optional[str]) -> Optional[TopicStore]:
        if name is None:
            return self._stores.get(self.default_store)
        return self._stores.get(str(name))

    def list_store_names(self) -> list[str]:
        return sorted(self._stores.keys())


# ── Canonical store layout ─────────────────────────────────────────────
#
# This list used to be duplicated in three places (main.py / runner.py / rpc_server.py),
# and all three had to be kept in sync. The failure from changing only one was silent: ingest would only
# call _record_drop("store_unresolved") for an unregistered store,
# without reporting it to the publisher, so standalone mode would silently drop all records. A single constructor makes

# missed registrations impossible.
# The conversation turns the host copies to the plugin (the instruction + the line she actually says). Define the name once as a constant instead of having
# Each had its own "conversations"; adding another would create a third entry point where missed updates silently drop records.
CONVERSATIONS_STORE_NAME = "conversations"

# conversations also uses topic "all", hardcoded in BusRpcClientBase.
CONVERSATIONS_TOPIC = "all"


# Plugin-pushed messages. Like conversations, use a separate constant: readers subscribing in proactive_bridge
# to "messages.", registry code, and host writers otherwise repeat the literal, each repetition adding another
# risk of silent message loss after incomplete updates. This chain once broke, making push_message return
# submitted=True while the character said nothing.
MESSAGES_STORE_NAME = "messages"

# Like conversations / frames, the topic is hardcoded as "all".
MESSAGES_TOPIC = "all"


# Generic stores all use MESSAGE_PLANE_STORE_MAXLEN.
DEFAULT_STORE_NAMES: tuple[str, ...] = (
    MESSAGES_STORE_NAME,
    "events",
    "lifecycle",
    "runs",
    "export",
    "memory",
    # conversations is a separate store for conversation context, distinct from messages.
    CONVERSATIONS_STORE_NAME,
)

# Frames actually sent to the model by the host. Use a separate store rather than existing ones:
# - Not "messages": proactive_bridge subscribes to "messages." and would json.loads every frame
#   on the proactive-chat delivery thread.
# - Not a new events topic: BusRpcClientBase._build_query_args hardcodes
#   {"topic": "all"}, making new topics invisible to ctx.bus.events.get().
# - Give it its own MESSAGE_PLANE_FRAMES_STORE_MAXLEN instead of the default 20000 capacity.
FRAMES_STORE_NAME = "frames"

# frames also uses topic "all" because its pull path reuses BusRpcClientBase's hardcoded topic.
FRAMES_TOPIC = "all"


# Host-only stores. All writers use the authenticated ingest socket, where
# _route_message overwrites plugin_id. rpc_server's bus.publish is an unauthenticated loopback
# operation available to any connecting client. These three stores reach users directly or impersonate the host:
# messages delivers proactive chat through ProactiveBridge's "messages." subscription;
# frames represents model-visible images, and conversations contains context. Reject bus.publish for
# all three; no in-tree caller uses that path anyway (SDK publish() uses
# ctx.push_message; the host uses ingest).
HOST_OWNED_STORE_NAMES: frozenset[str] = frozenset(
    {MESSAGES_STORE_NAME, FRAMES_STORE_NAME, CONVERSATIONS_STORE_NAME}
)


def build_default_store_registry(
    *,
    maxlen: int,
    frames_maxlen: int,
    default_store: str = "messages",
) -> StoreRegistry:
    """Build the registry every message-plane entry point shares.

    ``frames_maxlen`` is separate on purpose: a frame is three orders of
    magnitude larger than an ordinary record, so it cannot share the generic
    per-topic deque length.
    """
    registry = StoreRegistry(default_store=default_store)
    for name in DEFAULT_STORE_NAMES:
        registry.register(TopicStore(name=name, maxlen=maxlen))
    registry.register(TopicStore(name=FRAMES_STORE_NAME, maxlen=frames_maxlen))
    return registry
