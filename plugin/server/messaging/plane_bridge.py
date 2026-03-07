from __future__ import annotations

import queue
import socket
import threading
import time
import uuid

import ormsgpack
import zmq
from plugin.logging_config import get_logger

from plugin.settings import MESSAGE_PLANE_BRIDGE_ENABLED, MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT

logger = get_logger("server.messaging.plane_bridge")

_RUNTIME_ERRORS = (RuntimeError, ValueError, TypeError, AttributeError, KeyError, OSError, TimeoutError)


def _dumps(obj: object) -> bytes:
    return ormsgpack.packb(obj)


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

    def enqueue_delta(self, *, store: str, topic: str, payload: dict[str, object]) -> None:
        if not self._enabled:
            return
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
            return

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


def publish_record(*, store: str, record: dict[str, object], topic: str = "all") -> None:
    _bridge.enqueue_delta(store=store, topic=topic, payload=record)


def publish_snapshot(
    *,
    store: str,
    records: list[dict[str, object]],
    topic: str = "all",
    mode: str = "replace",
) -> None:
    _bridge.enqueue_snapshot(store=store, topic=topic, items=records, mode=mode)
