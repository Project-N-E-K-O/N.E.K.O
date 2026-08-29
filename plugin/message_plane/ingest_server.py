from __future__ import annotations

import time
from typing import Any, Dict, Optional

import ormsgpack
import zmq
from plugin.logging_config import logger

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


# Drop reasons that are terminal for the *author* of the push: the item is gone
# whole (text parts included), and nothing on the host side will bring it back
# -- only a change in the plugin will. These always reach the log at WARNING,
# even with MESSAGE_PLANE_INGEST_STATS_LOG_VERBOSE off, because the default
# ingest output is the anonymous "ingest stats recv=N accepted=N dropped=N"
# aggregate: it names neither the plugin, nor the size, nor the limit that was
# blown, so an author whose entire push vanished has literally no trace to
# follow. Every other reason (topic caps, malformed batch envelopes, unresolved
# stores) stays behind the verbose flag -- those are high-frequency host-side
# conditions, and promoting them to WARNING would bury exactly the signal this
# list exists to surface.
_ALWAYS_WARN_DROP_REASONS = frozenset(
    {
        "payload_too_big",
        "payload_pack_error",
        "publish_error",
    }
)

# ...but "terminal for the author" does not mean "rare": a plugin pushing an
# oversized screenshot cue every frame would hit payload_too_big at frame rate,
# and an unconditional warning there is the log flood the verbose flag was
# introduced to avoid. So the always-on warning is throttled per offender --
# (reason, store, topic, plugin_id): the first hit is reported immediately, and
# repeats inside the window are counted and folded into the next warning as
# ``suppressed=N``. One warning per minute per offender is enough to diagnose;
# one per frame is noise.
_DROP_WARN_THROTTLE_SECONDS = 60.0
_DROP_WARN_THROTTLE_KEYS_MAX = 256


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

        # key -> (last_warn_ts, suppressed_since_then); see the throttle notes
        # above _DROP_WARN_THROTTLE_SECONDS.
        self._drop_warn_state: Dict[tuple, tuple] = {}

    def stop(self) -> None:
        self._running = False

    def close(self) -> None:
        try:
            self._sock.close(linger=0)
        except Exception:
            pass

    def _resolve_store(self, name: Any) -> Optional[TopicStore]:
        return self._stores.get(None if name is None else str(name))

    def _throttle_drop_warning(self, reason: str, fields: Dict[str, Any]) -> Optional[int]:
        """Rate-limit the always-on drop warning per offender.

        Returns how many warnings were suppressed for this offender since the
        last one that got through (0 on the first hit), or ``None`` when the
        caller must stay silent because the throttle window has not elapsed.
        """
        key = (
            reason,
            str(fields.get("store")),
            str(fields.get("topic")),
            str(fields.get("plugin_id")),
        )
        now = time.time()
        last_ts, suppressed = self._drop_warn_state.get(key, (0.0, 0))
        if last_ts and (now - float(last_ts)) < _DROP_WARN_THROTTLE_SECONDS:
            self._drop_warn_state[key] = (last_ts, int(suppressed) + 1)
            return None
        # This map holds nothing but throttle bookkeeping, so a producer that
        # sprays unique topics is answered by dropping it wholesale rather than
        # by an LRU: the worst case is one extra warning per live offender right
        # after the reset, which is cheaper than the machinery to avoid it.
        if len(self._drop_warn_state) >= _DROP_WARN_THROTTLE_KEYS_MAX:
            self._drop_warn_state.clear()
        self._drop_warn_state[key] = (now, 0)
        return int(suppressed)

    def _record_drop(self, reason: str, **fields: Any) -> None:
        """Increment the drop counter and emit the precise drop reason.

        Reasons in ``_ALWAYS_WARN_DROP_REASONS`` are logged at WARNING
        unconditionally (throttled per offender), because they discard a
        plugin's push outright and the default aggregate line would leave its
        author with no trace at all. Every other reason is emitted only when
        verbose ingest logging is on, so a silently-dropped high-frequency
        "read" cue can still be diagnosed on demand instead of vanishing into
        the aggregate ``dropped`` count.
        """
        self._stats_dropped += 1
        always = reason in _ALWAYS_WARN_DROP_REASONS
        if not always and not bool(MESSAGE_PLANE_INGEST_STATS_LOG_VERBOSE):
            return
        try:
            suppressed = 0
            if always:
                throttled = self._throttle_drop_warning(reason, fields)
                if throttled is None:
                    return
                suppressed = throttled
            extra = " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
            if suppressed:
                extra = f"{extra} suppressed={suppressed}"
            if always:
                logger.warning("ingest DROP reason={} {}", reason, extra)
            else:
                logger.info("ingest DROP reason={} {}", reason, extra)
        except Exception:
            # Diagnostics must never take the ingest loop down with them. Two
            # ways this block can raise, both outside our control: a field
            # value whose __repr__/__format__ throws (fields carry plugin- and
            # payload-derived values, e.g. a topic name or a source string that
            # came off the wire), and a logger backend that is momentarily
            # unavailable or reentrant. The caller is on the path that decides
            # whether a message is accepted; losing the whole batch because a
            # DROP line could not be formatted would turn a reporting failure
            # into a delivery failure, which is strictly worse than the missing
            # line.
            #
            # Deliberately no logging in this handler: the thing that just
            # failed IS the logger, so reporting the failure through it would
            # either recurse or raise again. The drop is still COUNTED --
            # _stats_dropped is bumped by the caller before we get here, so the
            # periodic stats line keeps the event visible in aggregate even
            # when its detail line is lost.
            pass

    def _ingest_delta_batch(self, msg: Dict[str, Any]) -> None:
        items = msg.get("items")
        if not isinstance(items, list):
            self._record_drop("delta_items_not_list")
            return
        for it in items:
            if not isinstance(it, dict):
                self._record_drop("delta_item_not_dict")
                continue
            st = self._resolve_store(it.get("store") or it.get("bus"))
            if st is None:
                self._record_drop("store_unresolved", store=it.get("store") or it.get("bus"))
                continue
            topic = it.get("topic")
            if not isinstance(topic, str) or not topic:
                self._record_drop("topic_invalid", store=getattr(st, "name", None))
                continue
            if len(topic) > MESSAGE_PLANE_TOPIC_NAME_MAX_LEN:
                self._record_drop("topic_too_long", topic_len=len(topic))
                continue
            try:
                is_new_topic = topic not in st.meta
            except Exception:
                is_new_topic = False
            if is_new_topic:
                try:
                    if len(st.meta) >= MESSAGE_PLANE_TOPIC_MAX:
                        self._record_drop("topic_max", store=st.name, topic=topic)
                        continue
                except Exception:
                    self._record_drop("topic_meta_error", store=st.name, topic=topic)
                    continue
            payload = it.get("payload")
            if not isinstance(payload, dict):
                payload = {"value": payload}
            # Pulled out ahead of the drop paths on purpose: the always-on
            # warnings below are the only trace a plugin gets when its push is
            # discarded whole, and "which plugin" is the first thing anyone
            # reading that line needs to know.
            _drop_pid = payload.get("plugin_id")
            drop_plugin_id = _drop_pid if isinstance(_drop_pid, str) and _drop_pid else None
            if bool(MESSAGE_PLANE_VALIDATE_PAYLOAD_BYTES):
                try:
                    _size = len(ormsgpack.packb(payload))
                    if _size > MESSAGE_PLANE_PAYLOAD_MAX_BYTES:
                        self._record_drop(
                            "payload_too_big", store=st.name, topic=topic,
                            plugin_id=drop_plugin_id,
                            size=_size, limit=MESSAGE_PLANE_PAYLOAD_MAX_BYTES,
                            type=payload.get("type") or payload.get("message_type"),
                            source=payload.get("source"),
                        )
                        continue
                except Exception as _exc:
                    self._record_drop(
                        "payload_pack_error", store=st.name, topic=topic,
                        plugin_id=drop_plugin_id, err=type(_exc).__name__,
                    )
                    continue
            try:
                event = st.publish(topic, payload)
            except Exception as _exc:
                self._record_drop(
                    "publish_error", store=st.name, topic=topic,
                    plugin_id=drop_plugin_id, err=type(_exc).__name__,
                )
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
                # Same terminal drop as the delta path, so it goes through
                # _record_drop rather than bumping the counter in place: a
                # snapshot record lost to the payload cap is exactly as
                # invisible to its author as a dropped delta, and the reason
                # string has to match for both to be greppable as one.
                try:
                    _size = len(ormsgpack.packb(x))
                    if _size > MESSAGE_PLANE_PAYLOAD_MAX_BYTES:
                        _snap_pid = x.get("plugin_id")
                        self._record_drop(
                            "payload_too_big", store=st.name, topic=topic,
                            plugin_id=_snap_pid if isinstance(_snap_pid, str) and _snap_pid else None,
                            size=_size, limit=MESSAGE_PLANE_PAYLOAD_MAX_BYTES,
                        )
                        continue
                except Exception as _exc:
                    self._record_drop(
                        "payload_pack_error", store=st.name, topic=topic,
                        err=type(_exc).__name__,
                    )
                    continue
            records.append(x)
        if str(mode or "replace") == "append":
            for rec in records:
                try:
                    event = st.publish(topic, rec)
                except Exception as _exc:
                    self._record_drop(
                        "publish_error", store=st.name, topic=topic,
                        err=type(_exc).__name__,
                    )
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
        except Exception as _exc:
            # A failed replace loses the whole snapshot, which until now was not
            # even counted as a drop -- the caller saw an untouched aggregate and
            # a topic that simply never updated.
            #
            # ``records`` carries how many went down with it. The counter stays
            # at +1 on purpose: _stats_dropped counts DROP EVENTS, and the
            # append branch above bumps it once per failed record, so folding a
            # snapshot's whole length into it would make the two modes count
            # different things and silently distort the aggregate. The size
            # belongs in the log line instead, where it answers the only
            # question this warning has to answer on its own -- one lost record
            # or five hundred, i.e. whether to look now or at leisure.
            self._record_drop(
                "publish_error", store=st.name, topic=topic,
                records=len(records),
                err=type(_exc).__name__,
            )
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
