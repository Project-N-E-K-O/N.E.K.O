from __future__ import annotations

import time
from typing import Any, Dict, Optional

import ormsgpack
import zmq
from loguru import logger

from plugin.settings import (
    MESSAGE_PLANE_INGEST_BACKPRESSURE_SLEEP_SECONDS,
    MESSAGE_PLANE_INGEST_RCVHWM,
    MESSAGE_PLANE_INGEST_STATS_INTERVAL_SECONDS,
    MESSAGE_PLANE_INGEST_STATS_LOG_ENABLED,
    MESSAGE_PLANE_INGEST_STATS_LOG_INFO,
    MESSAGE_PLANE_INGEST_STATS_LOG_VERBOSE,
    MESSAGE_PLANE_PAYLOAD_MAX_BYTES,
    MESSAGE_PLANE_PUB_ENABLED,
    MESSAGE_PLANE_TOPIC_MAX,
    MESSAGE_PLANE_TOPIC_NAME_MAX_LEN,
    MESSAGE_PLANE_VALIDATE_PAYLOAD_BYTES,
)

from .pub_server import MessagePlanePubServer
from .stores import StoreRegistry, TopicStore


def _loads(data: bytes) -> Any:
    return ormsgpack.unpackb(data)


class MessagePlaneIngestServer:
    def __init__(
        self,
        *,
        endpoint: str,
        stores: StoreRegistry,
        pub_server: Optional[MessagePlanePubServer],
    ) -> None:
        self.endpoint = str(endpoint)
        self._stores = stores
        self._pub = pub_server

        self._ctx = zmq.Context.instance()
        self._sock = self._ctx.socket(zmq.PULL)
        self._sock.linger = 0
        try:
            self._sock.setsockopt(zmq.RCVHWM, int(MESSAGE_PLANE_INGEST_RCVHWM))
        except Exception:
            pass
        self._sock.bind(self.endpoint)
        self._running = False

        self._stats_last_ts = time.time()
        self._stats_recv = 0
        self._stats_accepted = 0
        self._stats_dropped = 0
        self._stats_last_store: Optional[str] = None
        self._stats_last_topic: Optional[str] = None
        self._stats_last_plugin_id: Optional[str] = None
        self._stats_last_source: Optional[str] = None

    def stop(self) -> None:
        self._running = False

    def close(self) -> None:
        try:
            self._sock.close(linger=0)
        except Exception:
            pass

    def _resolve_store(self, name: Any) -> Optional[TopicStore]:
        return self._stores.get(None if name is None else str(name))

    def _ingest_delta_batch(self, msg: Dict[str, Any]) -> None:
        items = msg.get("items")
        if not isinstance(items, list):
            self._stats_dropped += 1
            return
        for it in items:
            if not isinstance(it, dict):
                self._stats_dropped += 1
                continue
            st = self._resolve_store(it.get("store") or it.get("bus"))
            if st is None:
                self._stats_dropped += 1
                continue
            topic = it.get("topic")
            if not isinstance(topic, str) or not topic:
                self._stats_dropped += 1
                continue
            if len(topic) > MESSAGE_PLANE_TOPIC_NAME_MAX_LEN:
                self._stats_dropped += 1
                continue
            try:
                is_new_topic = topic not in st.meta
            except Exception:
                is_new_topic = False
            if is_new_topic:
                try:
                    if len(st.meta) >= MESSAGE_PLANE_TOPIC_MAX:
                        self._stats_dropped += 1
                        continue
                except Exception:
                    self._stats_dropped += 1
                    continue
            payload = it.get("payload")
            if not isinstance(payload, dict):
                payload = {"value": payload}
            if bool(MESSAGE_PLANE_VALIDATE_PAYLOAD_BYTES):
                try:
                    if len(ormsgpack.packb(payload)) > MESSAGE_PLANE_PAYLOAD_MAX_BYTES:
                        self._stats_dropped += 1
                        continue
                except Exception:
                    self._stats_dropped += 1
                    continue
            try:
                event = st.publish(topic, payload)
            except Exception:
                self._stats_dropped += 1
                continue
            self._stats_accepted += 1
            self._stats_last_store = str(st.name)
            self._stats_last_topic = str(topic)
            try:
                pid = payload.get("plugin_id")
                self._stats_last_plugin_id = str(pid) if isinstance(pid, str) else None
            except Exception:
                self._stats_last_plugin_id = None
            try:
                src = payload.get("source")
                self._stats_last_source = str(src) if isinstance(src, str) else None
            except Exception:
                self._stats_last_source = None
            if self._pub is not None and bool(MESSAGE_PLANE_PUB_ENABLED):
                try:
                    self._pub.publish(f"{st.name}.{topic}", event)
                except Exception:
                    pass

    def _ingest_snapshot(self, msg: Dict[str, Any]) -> None:
        st = self._resolve_store(msg.get("store") or msg.get("bus"))
        if st is None:
            self._stats_dropped += 1
            return
        topic = msg.get("topic")
        if not isinstance(topic, str) or not topic:
            topic = "snapshot.all"
        if len(topic) > MESSAGE_PLANE_TOPIC_NAME_MAX_LEN:
            self._stats_dropped += 1
            return
        try:
            is_new_topic = topic not in st.meta
        except Exception:
            is_new_topic = False
        if is_new_topic:
            try:
                if len(st.meta) >= MESSAGE_PLANE_TOPIC_MAX:
                    self._stats_dropped += 1
                    return
            except Exception:
                self._stats_dropped += 1
                return
        mode = msg.get("mode")
        items = msg.get("items")
        if not isinstance(items, list):
            self._stats_dropped += 1
            return
        records = []
        for x in items:
            if not isinstance(x, dict):
                continue
            if bool(MESSAGE_PLANE_VALIDATE_PAYLOAD_BYTES):
                try:
                    if len(ormsgpack.packb(x)) > MESSAGE_PLANE_PAYLOAD_MAX_BYTES:
                        self._stats_dropped += 1
                        continue
                except Exception:
                    self._stats_dropped += 1
                    continue
            records.append(x)
        if str(mode or "replace") == "append":
            for rec in records:
                try:
                    event = st.publish(topic, rec)
                except Exception:
                    self._stats_dropped += 1
                    continue
                self._stats_accepted += 1
                self._stats_last_store = str(st.name)
                self._stats_last_topic = str(topic)
                if self._pub is not None and bool(MESSAGE_PLANE_PUB_ENABLED):
                    try:
                        self._pub.publish(f"{st.name}.{topic}", event)
                    except Exception:
                        pass
            return

        try:
            events = st.replace_topic(topic, records)
        except Exception:
            events = []
        self._stats_accepted += int(len(events))
        self._stats_last_store = str(st.name)
        self._stats_last_topic = str(topic)
        if self._pub is not None and bool(MESSAGE_PLANE_PUB_ENABLED):
            for ev in events:
                try:
                    self._pub.publish(f"{st.name}.{topic}", ev)
                except Exception:
                    continue

    def _maybe_log_stats(self) -> None:
        if not bool(MESSAGE_PLANE_INGEST_STATS_LOG_ENABLED):
            return
        interval = float(MESSAGE_PLANE_INGEST_STATS_INTERVAL_SECONDS)
        if interval <= 0:
            interval = 1.0
        now = time.time()
        if now - float(self._stats_last_ts) < interval:
            return
        recv = int(self._stats_recv)
        accepted = int(self._stats_accepted)
        dropped = int(self._stats_dropped)
        store = self._stats_last_store
        topic = self._stats_last_topic
        plugin_id = self._stats_last_plugin_id
        source = self._stats_last_source
        self._stats_recv = 0
        self._stats_accepted = 0
        self._stats_dropped = 0
        self._stats_last_ts = float(now)

        if bool(MESSAGE_PLANE_INGEST_STATS_LOG_VERBOSE):
            msg = (
                "ingest stats recv={} accepted={} dropped={} last_store={} last_topic={} last_plugin_id={} last_source={}"
            )
            args = (recv, accepted, dropped, store, topic, plugin_id, source)
        else:
            msg = "ingest stats recv={} accepted={} dropped={}"
            args = (recv, accepted, dropped)

        try:
            if bool(MESSAGE_PLANE_INGEST_STATS_LOG_INFO):
                logger.info(msg, *args)
            else:
                logger.debug(msg, *args)
        except Exception:
            pass

        sleep_s = float(MESSAGE_PLANE_INGEST_BACKPRESSURE_SLEEP_SECONDS)
        if sleep_s > 0:
            time.sleep(sleep_s)

    def serve_forever(self) -> None:
        self._running = True
        poller = zmq.Poller()
        poller.register(self._sock, zmq.POLLIN)
        logger.info("ingest server bound: {}", self.endpoint)
        try:
            while self._running:
                try:
                    events = dict(poller.poll(timeout=250))
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    if not self._running:
                        break
                    try:
                        time.sleep(0.01)
                    except Exception:
                        pass
                    continue
                if not self._running:
                    break
                if self._sock not in events:
                    continue
                try:
                    raw = self._sock.recv(flags=0)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    if not self._running:
                        break
                    try:
                        time.sleep(0.001)
                    except Exception:
                        pass
                    continue
                self._stats_recv += 1
                try:
                    obj = _loads(raw)
                except Exception:
                    self._stats_dropped += 1
                    continue
                if not isinstance(obj, dict):
                    self._stats_dropped += 1
                    continue
                kind = obj.get("kind")
                if kind == "snapshot":
                    try:
                        self._ingest_snapshot(obj)
                    except Exception:
                        self._stats_dropped += 1
                        pass
                    self._maybe_log_stats()
                    continue
                try:
                    self._ingest_delta_batch(obj)
                except Exception:
                    self._stats_dropped += 1
                    pass
                self._maybe_log_stats()
        finally:
            # IMPORTANT: ZeroMQ sockets are not thread-safe; close from the ingest thread.
            try:
                self.close()
            except Exception:
                pass
