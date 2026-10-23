from __future__ import annotations

import asyncio
import queue
import threading
import time
import uuid
from typing import Any, Dict, Optional

import ormsgpack

from plugin.logging_config import logger

# Configuration decides whether to start a listener. Backend availability must
# not be frozen by whichever plugin happens to import this module first.
try:
    import zmq
    import zmq.asyncio
except ImportError:  # pragma: no cover
    zmq = None

from plugin.core.rpc import encode_rpc_reply, log_rpc_failure, rpc_reply
from plugin.core.zmq_transport import pack_rpc_payload, unpack_rpc_payload


def _dumps(obj: Any) -> bytes:
    return ormsgpack.packb(obj)


def _loads(data: bytes) -> Any:
    return ormsgpack.unpackb(data)




class ZmqIpcServer:
    """Compatibility wire adapter; host request semantics live in RpcDispatcher."""

    def __init__(self, endpoint: str, request_handler, *, max_in_flight: int = 256):
        if zmq is None:
            raise RuntimeError("pyzmq is not available")
        self._request_handler = request_handler
        self._sock = zmq.asyncio.Context.instance().socket(zmq.ROUTER)
        self._sock.setsockopt(zmq.LINGER, 0)
        self._sock.bind(endpoint)
        self.endpoint = self._sock.getsockopt(zmq.LAST_ENDPOINT).decode()
        self._running = True
        self._limit = max_in_flight
        self._tasks: set[asyncio.Task] = set()
        self._send_lock = asyncio.Lock()

    async def _send(self, ident: bytes, req_id: bytes, response: dict) -> None:
        try:
            _, encoded = encode_rpc_reply(response, pack_rpc_payload)
            async with self._send_lock:
                await asyncio.wait_for(self._sock.send_multipart([ident, req_id, encoded]), timeout=1.0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_rpc_failure(logger, f"Plugin IPC reply delivery failed: request_id={req_id!r}", exc)

    async def _serve(self, ident: bytes, req_id: bytes, request: dict) -> None:
        try:
            response = await self._request_handler(request)
            if not isinstance(response, dict):
                raise TypeError("Invalid RPC handler response")
            await self._send(ident, req_id, response)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_rpc_failure(logger, f"Plugin IPC compatibility request failed: request_id={req_id!r}", exc)
            await self._send(ident, req_id, rpc_reply(request, error={"code": "INTERNAL_ERROR", "message": "Internal plugin request error"}))

    def _finished(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled():
            task.exception()

    async def serve_forever(self, shutdown_event) -> None:
        while self._running and not shutdown_event.is_set():
            try:
                frames = await asyncio.wait_for(self._sock.recv_multipart(), timeout=0.2)
            except asyncio.TimeoutError:
                continue
            if len(frames) != 3:
                continue
            ident, req_id, payload = frames
            try:
                request = unpack_rpc_payload(payload)
                if not isinstance(request, dict):
                    raise ValueError("RPC request must be an object")
            except Exception:
                await self._send(ident, req_id, {"request_id": req_id.decode("utf-8", errors="replace"),
                    "result": None, "error": {"code": "INVALID_REQUEST", "message": "Invalid IPC request"}})
                continue
            if len(self._tasks) >= self._limit:
                await self._send(ident, req_id, {"request_id": request.get("request_id"), "result": None,
                    "error": {"code": "BUSY", "message": "Plugin IPC listener is busy"}})
                continue
            task = asyncio.create_task(self._serve(ident, req_id, request))
            self._tasks.add(task)
            task.add_done_callback(self._finished)

    def close(self) -> None:
        self._running = False
        for task in tuple(self._tasks):
            task.cancel()
        self._sock.close(0)

    async def aclose(self) -> None:
        tasks = tuple(self._tasks)
        self.close()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)





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

