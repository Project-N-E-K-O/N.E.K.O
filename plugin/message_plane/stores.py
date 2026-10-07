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

        # 从 metadata 中提取 conversation_id（用于对话上下文关联）
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
# 这份清单以前在三个地方各抄了一份（main.py / runner.py / rpc_server.py），
# 三份都得记得改。只改一处的后果是静默的：ingest 遇到没注册的 store 只会
# _record_drop("store_unresolved")，不会回报给 publisher，于是 standalone 模式
# 悄悄丢掉全部记录。收成一个构造函数就是为了让"漏注册"不再可能。

# 宿主抄给插件的对话轮（指令 + 她真正说出口的那句）。名字单独立常量而不是让
# 每个写入方各写一遍字面量：读侧（plugin/core/bus/conversations.py）和注册表原本
# 就各有一份 "conversations"，再多一份就是漏改时静默丢记录的第三个入口。
CONVERSATIONS_STORE_NAME = "conversations"

# conversations 也走 topic "all"：BusRpcClientBase 那边 topic 是写死的。
CONVERSATIONS_TOPIC = "all"


# 插件推送的消息。和 conversations 一样单列常量：读侧（proactive_bridge 订阅
# "messages." 前缀）、注册表、以及宿主的写入方原本会各写一遍字面量，多一处就
# 多一个漏改时静默丢消息的入口——这条链断过一次，代价是 push_message 返回
# submitted=True 而角色一句话都不说。
MESSAGES_STORE_NAME = "messages"

# 与 conversations / frames 同：topic 写死 "all"。
MESSAGES_TOPIC = "all"


# 通用 store：都用 MESSAGE_PLANE_STORE_MAXLEN。
DEFAULT_STORE_NAMES: tuple[str, ...] = (
    MESSAGES_STORE_NAME,
    "events",
    "lifecycle",
    "runs",
    "export",
    "memory",
    # conversations 是独立的 store，用于存储对话上下文（与 messages 分离）
    CONVERSATIONS_STORE_NAME,
)

# 宿主真正推给模型的那几张画面。单列一个 store 而不是复用现成的：
# - 不能是 "messages"：proactive_bridge 订阅了 "messages." 前缀，会在主动搭话
#   的投递线程上对每一帧 json.loads 一次；
# - 不能是 events 下新开一个 topic：BusRpcClientBase._build_query_args 把
#   {"topic": "all"} 写死了，新 topic 对 ctx.bus.events.get() 根本不可见；
# - 容量单独给（MESSAGE_PLANE_FRAMES_STORE_MAXLEN），不能吃 20000 的默认值。
FRAMES_STORE_NAME = "frames"

# frames 也走 topic "all"：pull 侧复用 BusRpcClientBase，那里的 topic 是写死的。
FRAMES_TOPIC = "all"


# 只有宿主能往里写的 store。写入方一律是 ingest socket（带 token，且由
# _route_message 盖过 plugin_id）；rpc_server 的 bus.publish 是无鉴权的 loopback
# op，谁连上都能发。这三个 store 的记录会直接落到用户面前或冒充宿主：
# messages 是主动搭话的投递路径（ProactiveBridge 订阅 "messages." 前缀），
# frames 是"模型看到的画面"，conversations 是对话上下文。所以 bus.publish 对它们
# 一律拒绝——树内本来也没有任何调用方走那条路（SDK 的 publish() 走的是
# ctx.push_message，宿主自己走 ingest）。
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
