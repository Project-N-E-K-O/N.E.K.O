"""ZeroMQ transport for plugin host ↔ child process communication.

Replaces ``multiprocessing.Queue`` with ZMQ PUSH/PULL sockets:

* **Downlink** (host → child): commands, plugin-to-plugin responses
* **Uplink** (child → host): results, status, messages, plugin-to-plugin requests
* **Image uplink** (child → host): bounded raw image uploads

Control messages are serialised with :mod:`pickle` (same as ``mp.Queue``) and
carry a *channel tag* so the receiver can demux. Image-upload metadata uses
JSON and the image bytes travel in a separate frame.

Channel tags
~~~~~~~~~~~~
- ``cmd``   – commands (downlink)
- ``res``   – request/response results (uplink)
- ``sts``   – status updates (uplink)
- ``msg``   – messages (uplink)
- ``comm``  – plugin-to-plugin requests (uplink)
- ``resp``  – plugin-to-plugin responses (downlink)
"""
from __future__ import annotations

import asyncio
import json
import pickle
import threading
import time
from typing import Any, Optional, Tuple

import zmq
import zmq.asyncio

# ── Channel constants ──────────────────────────────────────────────
CH_CMD = "cmd"
CH_RES = "res"
CH_STS = "sts"
CH_MSG = "msg"
CH_COMM = "comm"
CH_RESP = "resp"

_LINGER_MS = 1000
_IMAGE_HWM = 8
_IMAGE_MAX_BYTES = 8 * 1024 * 1024


# ═══════════════════════════════════════════════════════════════════
# Host-side transport (runs in the user_plugin_server process)
# ═══════════════════════════════════════════════════════════════════

_IMG_LOCK_SHUTDOWN_WAIT_S = 2.0


class HostTransport:
    """Async ZMQ transport for the host (main-process) side.

    Create in ``PluginHost.__init__`` — sockets are bound immediately so that
    the endpoint strings are available for the child process args.

    All public send/recv methods are *coroutines* and must be called from the
    event loop.
    """

    def __init__(self) -> None:
        self._ctx = zmq.asyncio.Context()

        # Downlink: host → child (PUSH/PULL)
        self._dl_sock = self._ctx.socket(zmq.PUSH)
        self._dl_sock.setsockopt(zmq.LINGER, _LINGER_MS)
        self._dl_sock.setsockopt(zmq.SNDHWM, 5000)
        self._dl_sock.bind("tcp://127.0.0.1:*")
        self.downlink_endpoint: str = self._dl_sock.getsockopt(zmq.LAST_ENDPOINT).decode()

        # Uplink: child → host (PUSH/PULL)
        self._ul_sock = self._ctx.socket(zmq.PULL)
        self._ul_sock.setsockopt(zmq.LINGER, 0)
        self._ul_sock.setsockopt(zmq.RCVHWM, 5000)
        self._ul_sock.bind("tcp://127.0.0.1:*")
        self.uplink_endpoint: str = self._ul_sock.getsockopt(zmq.LAST_ENDPOINT).decode()

        # Bulk image uplink: isolated from status/result/control traffic so a
        # full media queue cannot head-of-line block the plugin control plane.
        self._img_sock = self._ctx.socket(zmq.PULL)
        self._img_sock.setsockopt(zmq.LINGER, 0)
        self._img_sock.setsockopt(zmq.RCVHWM, _IMAGE_HWM)
        self._img_sock.setsockopt(zmq.MAXMSGSIZE, _IMAGE_MAX_BYTES)
        self._img_sock.bind("tcp://127.0.0.1:*")
        self.image_uplink_endpoint: str = self._img_sock.getsockopt(zmq.LAST_ENDPOINT).decode()

        self._closed = False

    # ── send helpers ─────────────────────────────────────────────

    async def send_command(self, msg: dict) -> None:
        """Send a command on the downlink."""
        await self._dl_sock.send(pickle.dumps((CH_CMD, msg)))

    async def send_response(self, msg: dict) -> None:
        """Send a plugin-to-plugin response on the downlink."""
        await self._dl_sock.send(pickle.dumps((CH_RESP, msg)))

    # ── recv helper ──────────────────────────────────────────────

    async def recv(self, timeout_ms: int = 1000) -> Optional[Tuple[str, dict]]:
        """Receive one ``(channel, payload)`` from the uplink, or *None* on timeout."""
        if await self._ul_sock.poll(timeout=timeout_ms):
            raw = await self._ul_sock.recv()
            return pickle.loads(raw)  # type: ignore[return-value]
        return None

    async def recv_image(self, timeout_ms: int = 1000) -> Optional[Tuple[dict, bytes]]:
        """Receive one metadata/raw-bytes upload from the isolated media socket."""
        if await self._img_sock.poll(timeout=timeout_ms):
            frames = await self._img_sock.recv_multipart()
            if len(frames) != 2:
                raise ValueError("image upload must contain metadata and data frames")
            try:
                metadata = json.loads(frames[0].decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("image upload metadata must be valid JSON") from exc
            if not isinstance(metadata, dict):
                raise TypeError("image upload metadata must be a dict")
            if not all(isinstance(key, str) for key in metadata):
                raise TypeError("image upload metadata keys must be strings")
            return metadata, bytes(frames[1])
        return None

    # ── lifecycle ────────────────────────────────────────────────

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for sock in (self._dl_sock, self._ul_sock, self._img_sock):
            try:
                sock.close(linger=0)
            except Exception:
                pass
        try:
            self._ctx.term()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════
# Child-side transport (runs in the plugin child process)
# ═══════════════════════════════════════════════════════════════════

class ChildTransport:
    """Transport for the child (plugin-process) side.

    * **Downlink receive** uses ``zmq.asyncio`` for native ``await``.
    * **Uplink send** uses a regular (blocking) ``zmq.PUSH`` socket guarded by
      a :class:`threading.Lock` so that timer threads can safely call
      ``channel_sender(...).put_nowait(...)`` without conflicting with the
      event-loop thread.
    """

    def __init__(
        self,
        downlink_endpoint: str,
        uplink_endpoint: str,
        image_uplink_endpoint: str | None = None,
    ) -> None:
        # Sync context — used for the uplink PUSH socket (thread-safe via lock)
        self._sync_ctx = zmq.Context()

        self._ul_sock = self._sync_ctx.socket(zmq.PUSH)
        self._ul_sock.setsockopt(zmq.LINGER, _LINGER_MS)
        self._ul_sock.setsockopt(zmq.SNDHWM, 5000)
        self._ul_sock.connect(uplink_endpoint)
        self._ul_lock = threading.Lock()

        # Async context — used for the downlink PULL socket (event-loop only)
        self._async_ctx = zmq.asyncio.Context()
        self._dl_sock = self._async_ctx.socket(zmq.PULL)
        self._dl_sock.setsockopt(zmq.LINGER, 0)
        self._dl_sock.connect(downlink_endpoint)

        self._img_sock: Any | None = None
        self._img_lock = threading.Lock()
        if image_uplink_endpoint:
            self._img_sock = self._sync_ctx.socket(zmq.PUSH)
            self._img_sock.setsockopt(zmq.LINGER, 0)
            self._img_sock.setsockopt(zmq.SNDHWM, _IMAGE_HWM)
            self._img_sock.connect(image_uplink_endpoint)

        self._downlink_endpoint = downlink_endpoint
        self._uplink_endpoint = uplink_endpoint
        self._closed = False

    # ── downlink (async, event-loop only) ────────────────────────

    async def recv_downlink(self, timeout_ms: int = 1000) -> Optional[Tuple[str, dict]]:
        """Receive ``(channel, payload)`` from the downlink, or *None* on timeout."""
        if await self._dl_sock.poll(timeout=timeout_ms):
            raw = await self._dl_sock.recv()
            return pickle.loads(raw)  # type: ignore[return-value]
        return None

    async def send_image(
        self,
        request_id: str,
        *,
        mime: str,
        data: bytes,
        timeout: float,
    ) -> None:
        """Send raw image bytes without using the shared control uplink."""
        if self._img_sock is None:
            raise RuntimeError("image transport is not configured")
        payload = bytes(data)
        if len(payload) > _IMAGE_MAX_BYTES:
            raise ValueError(
                f"image payload exceeds the {_IMAGE_MAX_BYTES} byte transport limit"
            )
        metadata = json.dumps(
            {
                "type": "IMAGE_UPLOAD",
                "request_id": str(request_id),
                "mime": str(mime),
            },
            separators=(",", ":"),
        ).encode("utf-8")
        await asyncio.to_thread(
            self._send_image_sync,
            metadata,
            payload,
            timeout,
        )

    def _send_image_sync(
        self,
        metadata: bytes,
        payload: bytes,
        timeout: float,
    ) -> None:
        """Bound one image send while serialising access across plugin threads."""
        if timeout <= 0:
            raise ValueError("image transport timeout must be positive")
        started_at = time.monotonic()
        if not self._img_lock.acquire(timeout=timeout):
            raise TimeoutError(f"image transport send timed out after {timeout}s")
        try:
            if self._closed or self._img_sock is None:
                raise RuntimeError("image transport is closed")
            remaining = timeout - (time.monotonic() - started_at)
            if remaining <= 0 or not self._img_sock.poll(
                timeout=max(1, int(remaining * 1000)),
                flags=zmq.POLLOUT,
            ):
                raise TimeoutError(f"image transport send timed out after {timeout}s")
            try:
                self._img_sock.send_multipart([metadata, payload], flags=zmq.NOBLOCK)
            except zmq.Again:
                raise TimeoutError(
                    f"image transport send timed out after {timeout}s"
                ) from None
        finally:
            # If a shutdown started while this send held the lock, the sender
            # is the last thread that will touch the socket, so the sender
            # closes it. libzmq sockets are not thread safe: closing one from
            # another thread while a send or poll is in progress is undefined
            # behaviour, not merely impolite (CodeRabbit).
            if self._closed:
                self._close_img_sock_locked()
            self._img_lock.release()

    # ── uplink (thread-safe, any thread) ─────────────────────────

    def send_uplink(self, channel: str, msg: Any, *, timeout: float = 10.0) -> None:
        """Thread-safe blocking send on the uplink."""
        data = pickle.dumps((channel, msg))
        with self._ul_lock:
            self._ul_sock.send(data)

    def send_uplink_nowait(self, channel: str, msg: Any) -> None:
        """Thread-safe non-blocking send on the uplink."""
        data = pickle.dumps((channel, msg))
        with self._ul_lock:
            self._ul_sock.send(data, zmq.NOBLOCK)

    # ── channel senders (queue-compatible interface) ─────────────

    def channel_sender(self, channel: str) -> "ChannelSender":
        """Return a :class:`ChannelSender` that mimics ``mp.Queue.put`` / ``put_nowait``."""
        return ChannelSender(self, channel)

    # ── lifecycle ────────────────────────────────────────────────

    def _close_img_sock_locked(self) -> None:
        """Close the media socket. Callers decide whether they hold _img_lock."""
        if self._img_sock is not None:
            try:
                self._img_sock.close(linger=0)
            except Exception:
                pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for sock in (self._dl_sock, self._ul_sock):
            try:
                sock.close(linger=0)
            except Exception:
                pass
        # Bounded, and deliberately not unconditional. A handler inside
        # _send_image_sync holds this lock for its whole upload, so an
        # unconditional acquire made shutdown wait on an in-flight upload
        # (Codex P2). Closing the socket anyway is not the answer either:
        # libzmq sockets are not thread safe, so closing one while a send is in
        # progress is undefined behaviour -- a crash instead of a hang
        # (CodeRabbit).
        if self._img_lock.acquire(timeout=_IMG_LOCK_SHUTDOWN_WAIT_S):
            try:
                self._close_img_sock_locked()
            finally:
                self._img_lock.release()

        # Both contexts terminate here, including the one that may still own
        # an open media socket.
        #
        # This looks like it should block -- term() does wait for every socket
        # in the context to close. It does not, because zmq_ctx_term first
        # interrupts blocked calls in that context with ETERM and only then
        # waits. The in-flight sender's poll/send raises ContextTerminated at
        # once, its finally closes the media socket, and term returns.
        # Measured on pyzmq 27.1.0 / libzmq 4.3.5: a poll(30_000) blocked on a
        # full PUSH socket is interrupted in 0.000s and term returns
        # immediately.
        #
        # An earlier revision deferred this termination to the sender, on the
        # belief that terminating here would wait out the sender's full upload
        # timeout. That belief was wrong, and the deferral was worse than the
        # thing it avoided: the flag was written outside _img_lock and read
        # inside it, so a sender that finished first read a stale False and
        # nobody terminated the context -- and the designated hand-off thread
        # is a daemon the process never joins.
        for ctx in (self._async_ctx, self._sync_ctx):
            try:
                ctx.term()
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════
# ChannelSender — drop-in for mp.Queue on the child side
# ═══════════════════════════════════════════════════════════════════

class ChannelSender:
    """Queue-like object that tags each message with a *channel* and sends it
    through the shared :class:`ChildTransport` uplink.

    Accepted by :class:`~plugin.core.context.PluginContext` in place of the
    old ``multiprocessing.Queue`` references (``status_queue``, ``message_queue``, etc.).
    """

    __slots__ = ("_transport", "_ch")

    def __init__(self, transport: ChildTransport, channel: str) -> None:
        self._transport = transport
        self._ch = channel

    def put(self, obj: Any, block: bool = True, timeout: float | None = None) -> None:
        self._transport.send_uplink(self._ch, obj, timeout=timeout or 10.0)

    def put_nowait(self, obj: Any) -> None:
        self._transport.send_uplink_nowait(self._ch, obj)

    def get(self, block: bool = True, timeout: float | None = None) -> Any:
        raise NotImplementedError("ChannelSender is send-only; use transport.recv_downlink() for reads")

    def get_nowait(self) -> Any:
        raise NotImplementedError("ChannelSender is send-only")

    # no-ops for mp.Queue compat
    def close(self) -> None:
        pass

    def cancel_join_thread(self) -> None:
        pass
