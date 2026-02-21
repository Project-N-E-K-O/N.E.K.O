from __future__ import annotations

import asyncio
import time
import threading
import queue
import os
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from loguru import logger

import ormsgpack


_ENV_VAL = os.getenv("NEKO_PLUGIN_ZMQ_IPC_ENABLED")
if _ENV_VAL is None:
    _ZMQ_ENABLED = True
else:
    _ZMQ_ENABLED = _ENV_VAL.lower() in ("true", "1", "yes", "on")

if _ZMQ_ENABLED:
    try:
        import zmq
        import zmq.asyncio
    except Exception as e:  # pragma: no cover
        zmq = None
        zmq_asyncio = None

        # ZeroMQ IPC is enabled but pyzmq is missing; emit an explicit error log
        try:
            logger.bind(component="zmq").error(
                "ZeroMQ IPC is enabled (NEKO_PLUGIN_ZMQ_IPC_ENABLED) but pyzmq is not available: {}",
                type(e).__name__,
            )
        except Exception:
            # Logging failures must never break import path
            pass
else:
    # Explicitly disabled; never attempt to import pyzmq
    zmq = None
    zmq_asyncio = None


def _dumps(obj: Any) -> bytes:
    return ormsgpack.packb(obj)


def _loads(data: bytes) -> Any:
    return ormsgpack.unpackb(data)


@dataclass
class ZmqIpcClient:
    plugin_id: str
    endpoint: str

    def __post_init__(self) -> None:
        if zmq is None:
            raise RuntimeError("pyzmq is not available")
        self._tls = threading.local()

    def _get_sock(self):
        sock = getattr(self._tls, "sock", None)
        if sock is not None:
            return sock
        if zmq is None:
            return None
        ctx = zmq.Context.instance()
        sock = ctx.socket(zmq.DEALER)
        ident = f"{self.plugin_id}:{threading.get_ident()}".encode("utf-8")
        sock.setsockopt(zmq.IDENTITY, ident)
        sock.setsockopt(zmq.LINGER, 0)
        sock.connect(self.endpoint)
        try:
            self._tls.sock = sock
        except Exception:
            pass
        return sock

    def request(self, request: Dict[str, Any], timeout: float) -> Optional[Dict[str, Any]]:
        if zmq is None:
            return None
        sock = self._get_sock()
        if sock is None:
            return None
        req_id = request.get("request_id")
        if not isinstance(req_id, str) or not req_id:
            return None
        try:
            sock.send_multipart([req_id.encode("utf-8"), _dumps(request)], flags=0)
        except Exception as e:
            try:
                logger.bind(component="zmq.client").warning(
                    "[ZmqIpcClient] send_multipart failed for plugin={} req_id={} error={}: {}",
                    self.plugin_id,
                    req_id,
                    type(e).__name__,
                    str(e),
                )
            except Exception:
                pass
            return None

        poller = zmq.Poller()
        poller.register(sock, zmq.POLLIN)
        deadline = time.time() + max(0.0, float(timeout))
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            try:
                events = dict(poller.poll(timeout=int(remaining * 1000)))
            except Exception:
                return None
            if sock not in events:
                continue
            try:
                frames = sock.recv_multipart(flags=0)
            except Exception:
                return None
            if len(frames) < 2:
                continue
            rid = None
            try:
                rid = frames[0].decode("utf-8")
            except Exception:
                rid = None
            if rid != req_id:
                continue
            try:
                payload = _loads(frames[1])
            except Exception:
                return None
            if isinstance(payload, dict):
                return payload
            return None

    def close(self) -> None:
        try:
            sock = getattr(self._tls, "sock", None)
            if sock is not None:
                sock.close(0)
        except Exception:
            pass


class ZmqIpcServer:
    def __init__(self, endpoint: str, request_handler):
        if zmq is None:
            raise RuntimeError("pyzmq is not available")
        self._endpoint = str(endpoint)
        self._request_handler = request_handler
        self._ctx = zmq.asyncio.Context.instance()
        self._sock = self._ctx.socket(zmq.ROUTER)
        self._sock.setsockopt(zmq.LINGER, 0)
        self._sock.bind(self._endpoint)
        self._running = True
        self._recv_count = 0
        self._last_log_ts = 0.0
        self._enqueued_count = 0
        self._dropped_count = 0
        self._handled_count = 0
        self._handled_items = 0

    async def serve_forever(self, shutdown_event) -> None:
        while self._running and not shutdown_event.is_set():
            try:
                frames = await asyncio.wait_for(self._sock.recv_multipart(), timeout=0.2)
            except asyncio.TimeoutError:
                continue
            except Exception:
                await _async_sleep(0.01)
                continue
            if len(frames) < 3:
                # 无法解析出完整的 [ident, req_id, payload] 帧，只能丢弃
                continue
            ident = frames[0]
            try:
                req_id = frames[1].decode("utf-8")
            except Exception:
                # 连 request_id 都无法解析，客户端也无法匹配响应，只能丢弃
                continue
            try:
                request = _loads(frames[2])
            except Exception as e:
                # 解包失败时仍然返回一个结构化的错误响应，避免客户端一直超时拿不到任何响应
                try:
                    err_payload = {
                        "request_id": req_id,
                        "error": f"invalid ZMQ request payload: {type(e).__name__}",
                        "result": None,
                    }
                    await self._sock.send_multipart([
                        ident,
                        req_id.encode("utf-8"),
                        _dumps(err_payload),
                    ])
                except Exception:
                    # 发送错误响应失败时静默忽略，避免影响后续循环
                    pass
                continue
            if not isinstance(request, dict):
                # payload 解码成功但不是 dict，同样返回一个标准错误响应
                try:
                    err_payload = {
                        "request_id": req_id,
                        "error": "invalid ZMQ request payload type",
                        "result": None,
                    }
                    await self._sock.send_multipart([
                        ident,
                        req_id.encode("utf-8"),
                        _dumps(err_payload),
                    ])
                except Exception:
                    pass
                continue

            self._recv_count += 1
            try:
                now_ts = time.time()
                if now_ts - float(self._last_log_ts) >= 5.0:
                    self._last_log_ts = float(now_ts)
                    logger.bind(component="router").debug(
                        "[ZeroMQ IPC] recv={} last_type={} from={}",
                        int(self._recv_count),
                        str(request.get("type")),
                        str(request.get("from_plugin")),
                    )
            except Exception:
                pass

            try:
                resp = await self._request_handler(request)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                resp = {
                    "request_id": req_id,
                    "error": str(e),
                    "result": None,
                }

            if not isinstance(resp, dict):
                resp = {"request_id": req_id, "error": "invalid response", "result": None}
            resp.setdefault("request_id", req_id)
            try:
                await self._sock.send_multipart([ident, req_id.encode("utf-8"), _dumps(resp)])
            except Exception:
                continue

    def close(self) -> None:
        self._running = False
        try:
            self._sock.close(0)
        except Exception:
            pass


async def _async_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


class ZmqMessagePushBatcher:
    def __init__(
        self,
        *,
        plugin_id: str,
        endpoint: str,
        batch_size: int = 256,
        flush_interval_ms: int = 5,
        max_queue: int = 100000,
    ) -> None:
        if zmq is None:
            raise RuntimeError("pyzmq is not available")
        self._plugin_id = str(plugin_id)
        self._endpoint = str(endpoint)
        self._batch_size = max(1, int(batch_size))
        self._flush_interval_s = max(0.0, float(flush_interval_ms) / 1000.0)
        self._q: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=int(max_queue))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"zmq-push-batcher-{self._plugin_id}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 1.0) -> None:
        self._stop.set()
        t = self._thread
        if t is not None:
            try:
                t.join(timeout=float(timeout))
            except Exception:
                pass

    def enqueue(self, item: Dict[str, Any]) -> None:
        if self._stop.is_set():
            raise RuntimeError("push batcher stopped")
        try:
            self._q.put(item, timeout=1.0)
        except Exception as e:
            raise RuntimeError(f"push batcher queue full: {e}") from e

    def _run(self) -> None:
        if zmq is None:
            return
        ctx = zmq.Context.instance()
        sock = ctx.socket(zmq.PUSH)
        sock.setsockopt(zmq.LINGER, 0)
        try:
            sock.connect(self._endpoint)
        except Exception:
            try:
                sock.close(0)
            except Exception:
                pass
            return

        batch: list[Dict[str, Any]] = []
        last_flush = time.time()

        while not self._stop.is_set():
            timeout = self._flush_interval_s
            if timeout <= 0:
                timeout = 0.001
            if not batch:
                # When idle, avoid tight polling that can keep CPU high after a benchmark.
                # Still wake up frequently enough to react to stop requests.
                if timeout < 0.05:
                    timeout = 0.05
            try:
                item = self._q.get(timeout=timeout)
            except queue.Empty:
                item = None
            except Exception:
                item = None

            now_ts = time.time()
            if item is not None:
                if isinstance(item, dict):
                    batch.append(item)

            should_flush = False
            if len(batch) >= self._batch_size:
                should_flush = True
            elif batch and (now_ts - float(last_flush) >= self._flush_interval_s):
                should_flush = True

            if not should_flush:
                continue

            try:
                first_seq = batch[0].get("seq") if batch else None
                last_seq = batch[-1].get("seq") if batch else None
                payload = {
                    "type": "MESSAGE_PUSH_BATCH",
                    "from_plugin": self._plugin_id,
                    "first_seq": int(first_seq) if isinstance(first_seq, int) else first_seq,
                    "last_seq": int(last_seq) if isinstance(last_seq, int) else last_seq,
                    "count": int(len(batch)),
                    "items": batch,
                }
                sock.send(_dumps(payload), flags=0)
            except Exception:
                pass
            batch = []
            last_flush = float(now_ts)

        try:
            sock.close(0)
        except Exception:
            pass


class MessagePlaneIngestBatcher:
    def __init__(
        self,
        *,
        from_plugin: str,
        endpoint: str,
        batch_size: int = 256,
        flush_interval_ms: int = 5,
        max_queue: int = 100000,
        reject_ratio: float = 0.9,
        enqueue_timeout_s: float = 1.0,
    ) -> None:
        if zmq is None:
            raise RuntimeError("pyzmq is not available")
        self._from_plugin = str(from_plugin)
        self._endpoint = str(endpoint)
        self._batch_size = max(1, int(batch_size))
        self._flush_interval_s = max(0.0, float(flush_interval_ms) / 1000.0)
        self._q: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=int(max_queue))
        self._max_queue = int(max_queue)
        try:
            self._reject_ratio = float(reject_ratio)
        except Exception:
            self._reject_ratio = 0.9
        if self._reject_ratio < 0:
            self._reject_ratio = 0.0
        if self._reject_ratio > 1:
            self._reject_ratio = 1.0
        try:
            self._enqueue_timeout_s = float(enqueue_timeout_s)
        except Exception:
            self._enqueue_timeout_s = 1.0
        if self._enqueue_timeout_s < 0:
            self._enqueue_timeout_s = 0.0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"mp-ingest-batcher-{self._from_plugin}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 1.0) -> None:
        self._stop.set()
        t = self._thread
        if t is not None:
            try:
                t.join(timeout=float(timeout))
            except Exception:
                pass

    def enqueue(self, item: Dict[str, Any]) -> None:
        if self._stop.is_set():
            raise RuntimeError("message_plane ingest batcher stopped")
        try:
            if self._max_queue > 0 and self._reject_ratio > 0:
                qsize = int(self._q.qsize())
                if qsize >= int(self._max_queue * self._reject_ratio):
                    raise RuntimeError("message_plane ingest backpressure: queue high watermark")
        except RuntimeError:
            raise
        except Exception:
            pass
        try:
            self._q.put(item, timeout=float(self._enqueue_timeout_s))
        except Exception as e:
            raise RuntimeError(f"message_plane ingest batcher queue full: {e}") from e

    def _run(self) -> None:
        if zmq is None:
            return
        ctx = zmq.Context.instance()
        sock = ctx.socket(zmq.PUSH)
        sock.setsockopt(zmq.LINGER, 0)
        try:
            sock.connect(self._endpoint)
        except Exception:
            try:
                sock.close(0)
            except Exception:
                pass
            return

        batch: list[Dict[str, Any]] = []
        last_flush = time.time()

        while not self._stop.is_set():
            timeout = self._flush_interval_s
            if timeout <= 0:
                timeout = 0.001
            try:
                item = self._q.get(timeout=timeout)
            except queue.Empty:
                item = None
            except Exception:
                item = None

            now_ts = time.time()
            if item is not None:
                if isinstance(item, dict):
                    batch.append(item)

            should_flush = False
            if len(batch) >= self._batch_size:
                should_flush = True
            elif batch and (now_ts - float(last_flush) >= self._flush_interval_s):
                should_flush = True

            if not should_flush:
                continue

            try:
                payload = {
                    "v": 1,
                    "kind": "delta_batch",
                    "from": self._from_plugin,
                    "ts": time.time(),
                    "batch_id": str(uuid.uuid4()),
                    "items": batch,
                }
                sock.send(_dumps(payload), flags=0)
            except Exception:
                pass
            batch = []
            last_flush = float(now_ts)

        try:
            sock.close(0)
        except Exception:
            pass

