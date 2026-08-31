from __future__ import annotations

import queue
import secrets
import socket
import threading
import time
import uuid

import ormsgpack
import zmq
from plugin.logging_config import get_logger

from plugin.message_plane.stores import FRAMES_STORE_NAME, FRAMES_TOPIC
from plugin.settings import (
    MESSAGE_PLANE_BRIDGE_ENABLED,
    MESSAGE_PLANE_FRAMES_BRIDGE_MAX_PENDING,
    MESSAGE_PLANE_PAYLOAD_MAX_BYTES,
    MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT,
)

logger = get_logger("server.messaging.plane_bridge")

_RUNTIME_ERRORS = (RuntimeError, ValueError, TypeError, AttributeError, KeyError, OSError, TimeoutError)

# The ingest credential is minted here and never leaves this process. The
# bridge is the only writer to the ingest socket, and the message plane it
# authenticates against is started by the same process (see
# ServerLifecycleService._start_message_plane), so a process-local secret is
# enough — plugin children reach the host over their own per-host uplink and
# never touch this socket.
_INGEST_AUTH_TOKEN = secrets.token_urlsafe(32)


def ingest_auth_token() -> str:
    """Credential the locally started message plane must accept."""
    return _INGEST_AUTH_TOKEN


def _dumps(obj: object) -> bytes:
    if not isinstance(obj, dict):
        raise TypeError("message-plane ingest payload must be an object")
    authenticated = dict(obj)
    authenticated["_auth"] = _INGEST_AUTH_TOKEN
    return ormsgpack.packb(authenticated)


def _parse_tcp_endpoint(endpoint: str) -> tuple[str, int] | None:
    ep = str(endpoint)
    if not ep.startswith("tcp://"):
        return None
    rest = ep[len("tcp://") :]
    if ":" not in rest:
        return None
    host, port_s = rest.rsplit(":", 1)
    try:
        port = int(port_s)
    except (ValueError, TypeError):
        return None
    host = host.strip() or "127.0.0.1"
    return host, port


class _Bridge:
    def __init__(self) -> None:
        self._endpoint = str(MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT)
        self._enabled = bool(MESSAGE_PLANE_BRIDGE_ENABLED)
        self._q: "queue.Queue[dict[str, object]]" = queue.Queue(maxsize=4096)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self._enabled:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        t = threading.Thread(target=self._run, daemon=True)
        self._thread = t
        t.start()

    def stop(self) -> None:
        try:
            self._stop.set()
        except _RUNTIME_ERRORS:
            pass

    def enqueue_delta(
        self,
        *,
        store: str,
        topic: str,
        payload: dict[str, object],
        max_queue_depth: int | None = None,
    ) -> bool:
        """Queue one record for the ingest socket. Returns whether it was queued.

        ``max_queue_depth`` lets a lossy, oversized producer yield to the small
        records that share this queue: when the bridge is already ``N`` records
        behind, the caller is refused instead of adding to the backlog. Left at
        ``None`` (every pre-existing caller) the only bound is the queue's own
        maxsize, exactly as before.
        """
        if not self._enabled:
            return False
        if max_queue_depth is not None:
            try:
                if self._q.qsize() >= int(max_queue_depth):
                    return False
            except NotImplementedError:
                # qsize() is documented as unreliable/absent on some platforms.
                # Losing the ceiling degrades to the old behaviour; refusing the
                # record instead would be worse.
                pass
        msg = {
            "v": 1,
            "kind": "delta_batch",
            "from": "control_plane",
            "ts": time.time(),
            "batch_id": str(uuid.uuid4()),
            "items": [
                {
                    "store": str(store),
                    "topic": str(topic),
                    "payload": dict(payload) if isinstance(payload, dict) else {"value": payload},
                }
            ],
        }
        try:
            self._q.put_nowait(msg)
        except queue.Full:
            return False
        return True

    def enqueue_snapshot(
        self,
        *,
        store: str,
        topic: str,
        items: list[dict[str, object]],
        mode: str = "replace",
    ) -> None:
        if not self._enabled:
            return
        msg = {
            "v": 1,
            "kind": "snapshot",
            "from": "control_plane",
            "ts": time.time(),
            "store": str(store),
            "topic": str(topic),
            "mode": str(mode),
            "items": list(items) if isinstance(items, list) else [],
        }
        try:
            self._q.put_nowait(msg)
        except queue.Full:
            return

    def _wait_tcp_ready(self, endpoint: str) -> None:
        parsed = _parse_tcp_endpoint(endpoint)
        if parsed is None:
            return
        host, port = parsed
        while not self._stop.is_set():
            try:
                with socket.create_connection((host, port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.2)

    def _run(self) -> None:
        try:
            self._wait_tcp_ready(self._endpoint)
        except _RUNTIME_ERRORS:
            pass
        if self._stop.is_set():
            return

        ctx = zmq.Context.instance()
        sock = ctx.socket(zmq.PUSH)
        sock.linger = 0
        try:
            sock.connect(self._endpoint)
        except (RuntimeError, ValueError, TypeError, AttributeError, OSError, zmq.ZMQError) as err:
            try:
                logger.warning("[message_plane_bridge] connect failed: {}", err)
            except _RUNTIME_ERRORS:
                pass
            try:
                sock.close(0)
            except (RuntimeError, ValueError, TypeError, AttributeError, OSError, zmq.ZMQError):
                pass
            return

        try:
            while not self._stop.is_set():
                try:
                    msg = self._q.get(timeout=0.2)
                except queue.Empty:
                    continue
                except _RUNTIME_ERRORS:
                    continue
                try:
                    sock.send(_dumps(msg), flags=zmq.NOBLOCK)
                except (RuntimeError, ValueError, TypeError, AttributeError, OSError, zmq.ZMQError):
                    continue
        finally:
            try:
                sock.close(0)
            except (RuntimeError, ValueError, TypeError, AttributeError, OSError, zmq.ZMQError):
                pass


_bridge = _Bridge()


def start_bridge() -> None:
    _bridge.start()


def stop_bridge() -> None:
    _bridge.stop()


def publish_record(*, store: str, record: dict[str, object], topic: str = "all") -> bool:
    """Queue one record for the plane. Returns whether it was accepted.

    The result used to be dropped. It is reported now because one caller --
    the plugin message uplink -- is the only thing standing between
    ``push_message()`` and the character actually speaking, and a silent
    refusal there is indistinguishable from a message that was delivered.
    """
    return bool(_bridge.enqueue_delta(store=store, topic=topic, payload=record))


def publish_snapshot(
    *,
    store: str,
    records: list[dict[str, object]],
    topic: str = "all",
    mode: str = "replace",
) -> None:
    _bridge.enqueue_snapshot(store=store, topic=topic, items=records, mode=mode)


# ── Provider frames ────────────────────────────────────────────────────
#
# What travels here is a COPY of a frame the host already pushed to the model
# provider. Nothing on this path can ask for a capture: a frame the session's
# throttle dropped was never sent, so it is never copied, and the bytes are the
# post-compression ones the provider actually received.
#
# The contract for readers is "the last few frames the provider got", never a
# log. Everything below is lossy on purpose -- see ``publish_frame``.

# One warning per minute per reason. A frame-rate bug would otherwise turn the
# terminal drop reasons into a log flood, which is how the ingest server's own
# always-on warnings are throttled too.
_FRAME_WARN_THROTTLE_SECONDS = 60.0
_frame_warn_last: dict[str, float] = {}
_frame_warn_suppressed: dict[str, int] = {}


def _warn_frame_drop(reason: str, detail: str) -> None:
    now = time.time()
    last = _frame_warn_last.get(reason, 0.0)
    if last and (now - last) < _FRAME_WARN_THROTTLE_SECONDS:
        _frame_warn_suppressed[reason] = _frame_warn_suppressed.get(reason, 0) + 1
        return
    suppressed = _frame_warn_suppressed.pop(reason, 0)
    _frame_warn_last[reason] = now
    try:
        if suppressed:
            logger.warning(
                "[message_plane_bridge] frame dropped reason={} {} suppressed={}",
                reason, detail, suppressed,
            )
        else:
            logger.warning("[message_plane_bridge] frame dropped reason={} {}", reason, detail)
    except _RUNTIME_ERRORS:
        pass


def _find_raw_bytes(value: object, path: str = "") -> str | None:
    """Return the path of the first ``bytes``-like value, else ``None``.

    A frame must ride as ONE base64 string. A raw twin packs cleanly and takes
    the wire cost from 1.33x the image to about 2.3x (measured on
    game_agent_minecraft), which is what pushes a large frame past
    MESSAGE_PLANE_PAYLOAD_MAX_BYTES -- and that drop happens on the other side
    of a socket, so the publisher is told nothing. Refusing the record here
    turns a silent far-side loss into a local, named error.
    """
    if isinstance(value, (bytes, bytearray, memoryview)):
        return path or "<root>"
    if isinstance(value, dict):
        for key, item in value.items():
            found = _find_raw_bytes(item, f"{path}.{key}" if path else str(key))
            if found is not None:
                return found
        return None
    if isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            found = _find_raw_bytes(item, f"{path}[{i}]")
            if found is not None:
                return found
        return None
    return None


def build_frame_record(
    *,
    image_base64: str,
    source: str,
    captured_at: float | None = None,
    turn_id: str | None = None,
    generation: int | None = None,
    mime: str = "image/jpeg",
    lanlan_name: str | None = None,
    frame_id: str | None = None,
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    """Shape one frames-store record.

    Split out from :func:`publish_frame` so the wire shape can be asserted
    without a bridge, and so the size probe measures the same dict that would
    have been queued.

    ``timestamp`` mirrors ``captured_at`` because ``TopicStore._extract_index``
    indexes on ``timestamp``; ``id`` is what that indexer picks up for the
    record id, so a puller can dedupe without unpacking the payload.
    """
    ts = float(captured_at) if isinstance(captured_at, (int, float)) else time.time()
    record: dict[str, object] = {
        "kind": "frame",
        "type": "provider_frame",
        "source": str(source or "unknown"),
        "timestamp": ts,
        "captured_at": ts,
        "id": str(frame_id or uuid.uuid4()),
        "mime": str(mime or "image/jpeg"),
        "image_base64": str(image_base64 or ""),
    }
    if turn_id is not None:
        record["turn_id"] = str(turn_id)
    if generation is not None:
        record["generation"] = int(generation)
    if lanlan_name is not None:
        record["lanlan_name"] = str(lanlan_name)
    if isinstance(metadata, dict) and metadata:
        record["metadata"] = dict(metadata)
    return record


def publish_frame(record: dict[str, object]) -> bool:
    """Queue one provider frame for the ``frames`` store. Returns acceptance.

    Three refusals, all deliberate and all lossy:

    * ``raw_bytes`` -- the record carries a bytes-like value somewhere. See
      :func:`_find_raw_bytes`.
    * ``payload_too_big`` -- the packed record exceeds
      MESSAGE_PLANE_PAYLOAD_MAX_BYTES. Ingest measures the same bound on the
      far side and would drop it there; measuring here means the failure is
      attributable and costs no queue slot and no socket write.
    * ``bridge_backlog`` -- the shared send queue is already
      MESSAGE_PLANE_FRAMES_BRIDGE_MAX_PENDING records deep. Not warned: it is
      ordinary host-side back-pressure, and the contract already says frames
      are dropped under load.

    ``False`` never means "retry". A frame that did not make it is simply gone;
    the next one is along shortly.
    """
    if not isinstance(record, dict):
        _warn_frame_drop("not_a_record", f"type={type(record).__name__}")
        return False

    offending = _find_raw_bytes(record)
    if offending is not None:
        _warn_frame_drop("raw_bytes", f"at={offending}")
        return False

    try:
        size = len(ormsgpack.packb(record))
    except (TypeError, ValueError) as err:
        _warn_frame_drop("pack_error", f"err={type(err).__name__}: {err}")
        return False
    limit = int(MESSAGE_PLANE_PAYLOAD_MAX_BYTES)
    if size > limit:
        _warn_frame_drop(
            "payload_too_big",
            f"source={record.get('source')} size={size} limit={limit}",
        )
        return False

    return _bridge.enqueue_delta(
        store=FRAMES_STORE_NAME,
        topic=FRAMES_TOPIC,
        payload=record,
        max_queue_depth=int(MESSAGE_PLANE_FRAMES_BRIDGE_MAX_PENDING),
    )
