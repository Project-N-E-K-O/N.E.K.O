from __future__ import annotations

import queue
import socket
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import ormsgpack
import zmq
from loguru import logger

from plugin.settings import MESSAGE_PLANE_BRIDGE_ENABLED, MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT


def _dumps(obj: Any) -> bytes:
    return ormsgpack.packb(obj)


def _parse_tcp_endpoint(endpoint: str) -> Optional[Tuple[str, int]]:
    ep = str(endpoint)
    if not ep.startswith("tcp://"):
        return None
    rest = ep[len("tcp://") :]
    if ":" not in rest:
        return None
    host, port_s = rest.rsplit(":", 1)
    try:
        port = int(port_s)
    except Exception:
        return None
    host = host.strip() or "127.0.0.1"
    return host, port


class _Bridge:
    def __init__(self) -> None:
        self._endpoint = str(MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT)
        self._enabled = bool(MESSAGE_PLANE_BRIDGE_ENABLED)
        self._q: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=4096)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

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
        except Exception:
            pass

    def enqueue_delta(self, *, store: str, topic: str, payload: Dict[str, Any]) -> None:
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
        except Exception:
            return

    def enqueue_snapshot(self, *, store: str, topic: str, items: List[Dict[str, Any]], mode: str = "replace") -> None:
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
        except Exception:
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
            except Exception:
                time.sleep(0.2)

    def _run(self) -> None:
        try:
            self._wait_tcp_ready(self._endpoint)
        except Exception:
            pass
        if self._stop.is_set():
            return

        ctx = zmq.Context.instance()
        sock = ctx.socket(zmq.PUSH)
        sock.linger = 0
        try:
            sock.connect(self._endpoint)
        except Exception as e:
            try:
                logger.warning("[message_plane_bridge] connect failed: {}", e)
            except Exception:
                pass
            try:
                sock.close(0)
            except Exception:
                pass
            return

        try:
            while not self._stop.is_set():
                try:
                    msg = self._q.get(timeout=0.2)
                except queue.Empty:
                    continue
                except Exception:
                    continue
                try:
                    sock.send(_dumps(msg), flags=zmq.NOBLOCK)
                except Exception:
                    continue
        finally:
            try:
                sock.close(0)
            except Exception:
                pass


_bridge = _Bridge()


def start_bridge() -> None:
    _bridge.start()


def stop_bridge() -> None:
    _bridge.stop()


def publish_record(*, store: str, record: Dict[str, Any], topic: str = "all") -> None:
    _bridge.enqueue_delta(store=store, topic=topic, payload=record)


def publish_snapshot(*, store: str, records: List[Dict[str, Any]], topic: str = "all", mode: str = "replace") -> None:
    _bridge.enqueue_snapshot(store=store, topic=topic, items=records, mode=mode)
