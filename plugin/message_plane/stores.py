from __future__ import annotations

import time
import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from itertools import islice
from typing import Any, Deque, Dict, Optional


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
        with self._lock:
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
            event = {
                "seq": self._next_seq(),
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
        except Exception:
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

        return {
            "plugin_id": plugin_id,
            "source": source,
            "priority": priority,
            "kind": kind,
            "type": type_,
            "timestamp": ts,
            "id": record_id,
        }

    def get_recent(self, topic: str, limit: int) -> list[Dict[str, Any]]:
        t = str(topic)
        if limit <= 0:
            return []
        with self._lock:
            dq = self.items.get(t)
            if not dq:
                return []
            if limit >= len(dq):
                return list(dq)
            # Avoid copying the entire deque when only the tail is needed.
            tail_rev = list(islice(reversed(dq), int(limit)))
            tail_rev.reverse()
            return tail_rev

    def get_since(self, *, topic: Optional[str], after_seq: int, limit: int) -> list[Dict[str, Any]]:
        nn = int(limit)
        if nn <= 0:
            return []
        try:
            after = int(after_seq)
        except Exception:
            after = 0

        topic_q = None if topic is None else str(topic)
        with self._lock:
            if topic_q is None or topic_q.strip() in ("", "*"):
                topics = list(self.items.keys())
            else:
                topics = [topic_q]
            snapshots: list[Dict[str, Any]] = []
            for t in topics:
                dq = self.items.get(t)
                if not dq:
                    continue
                snapshots.extend(list(dq))

        out: list[Dict[str, Any]] = []
        for ev in snapshots:
            try:
                if int(ev.get("seq", 0)) > after:
                    out.append(ev)
            except Exception:
                continue

        out.sort(key=lambda e: int(e.get("seq") or 0))
        if nn >= len(out):
            return out
        return out[:nn]

    def query(
        self,
        *,
        topic: Optional[str],
        plugin_id: Optional[str] = None,
        source: Optional[str] = None,
        kind: Optional[str] = None,
        type: Optional[str] = None,
        priority_min: Optional[int] = None,
        since_ts: Optional[float] = None,
        until_ts: Optional[float] = None,
        limit: int = 200,
    ) -> list[Dict[str, Any]]:
        nn = int(limit)
        if nn <= 0:
            return []

        topic_q = None if topic is None else str(topic)
        with self._lock:
            if topic_q is None or topic_q.strip() in ("", "*"):
                topics = list(self.items.keys())
            else:
                topics = [topic_q]
            snapshots: list[Dict[str, Any]] = []
            for t in topics:
                dq = self.items.get(t)
                if not dq:
                    continue
                snapshots.extend(list(dq))

        pid = str(plugin_id) if isinstance(plugin_id, str) and plugin_id else None
        src = str(source) if isinstance(source, str) and source else None
        kd = str(kind) if isinstance(kind, str) and kind else None
        tp = str(type) if isinstance(type, str) and type else None
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
        for ev in snapshots:
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
