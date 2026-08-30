"""ZeroMQ transport for plugin host ↔ child process communication.

Replaces ``multiprocessing.Queue`` with four ZMQ PUSH/PULL channels:

* **Downlink** (host → child): commands, plugin-to-plugin responses
* **Control uplink** (child → host): results, status, plugin-to-plugin requests
* **Message uplink** (child → host): size-bounded individual and batched
  plugin messages
* **Image uplink** (child → host): bounded raw image uploads

Downlink messages are serialised with :mod:`pickle` for compatibility with
existing host commands. Uplink messages, which cross from untrusted plugin code
into the host, use MessagePack so decoding cannot execute plugin-controlled
objects, and carry a per-host token the host checks before acting on them. Both
directions carry a *channel tag* so the receiver can demux.

The image uplink keeps its own framing — JSON metadata plus a separate raw
bytes frame — because re-packing megabytes through MessagePack would buy
nothing. The same token rides inside its metadata frame instead, so every
child → host channel is gated by one credential.

Channel tags
~~~~~~~~~~~~
- ``cmd``   – commands (downlink)
- ``res``   – request/response results (uplink)
- ``sts``   – status updates (uplink)
- ``msg``   — individual messages (message uplink)
- ``msg_batch`` — batched messages (message uplink)
- ``comm``  – plugin-to-plugin requests (uplink)
- ``resp``  – plugin-to-plugin responses (downlink)
"""
from __future__ import annotations

import asyncio
import json
import pickle
import queue
import secrets
import threading
import time
from pathlib import PurePath
from typing import Any, Optional, Tuple

import ormsgpack
import zmq
import zmq.asyncio

from plugin.logging_config import logger

# ── Channel constants ──────────────────────────────────────────────
CH_CMD = "cmd"
CH_RES = "res"
CH_STS = "sts"
CH_MSG = "msg"
CH_MSG_BATCH = "msg_batch"
CH_COMM = "comm"
CH_RESP = "resp"

_LINGER_MS = 1000
_UPLINK_CHANNELS = frozenset({CH_RES, CH_STS, CH_MSG, CH_MSG_BATCH, CH_COMM})
_UPLINK_PACK_OPTIONS = (
    ormsgpack.OPT_NON_STR_KEYS
    | ormsgpack.OPT_PASSTHROUGH_TUPLE
    | ormsgpack.OPT_SERIALIZE_NUMPY
    | ormsgpack.OPT_SERIALIZE_PYDANTIC
)


def _normalize_uplink_extension(value: object) -> object:
    if isinstance(value, PurePath):
        return str(value)
    raise TypeError(f"unsupported uplink value type: {type(value).__name__}")


def _encode_uplink(token: str, channel: str, payload: Any) -> bytes:
    if not token or channel not in _UPLINK_CHANNELS or not isinstance(payload, dict):
        raise TypeError("invalid uplink message")
    try:
        return ormsgpack.packb(
            [token, channel, payload],
            default=_normalize_uplink_extension,
            option=_UPLINK_PACK_OPTIONS,
        )
    except Exception as exc:
        if channel == CH_RES:
            req_id = payload.get("req_id")
            error_payload = {
                "req_id": req_id if isinstance(req_id, (str, int)) else "unknown",
                "success": False,
                "data": None,
                "error": "Plugin result is not MessagePack-serializable",
            }
            return ormsgpack.packb(
                [token, channel, error_payload],
                option=_UPLINK_PACK_OPTIONS,
            )
        raise TypeError("uplink payload must be MessagePack-serializable") from exc


def _decode_uplink(raw: bytes, *, expected_token: str) -> Tuple[str, dict]:
    try:
        decoded = ormsgpack.unpackb(raw)
    except Exception as exc:
        raise ValueError("invalid uplink payload") from exc
    if not isinstance(decoded, list) or len(decoded) != 3:
        raise ValueError("invalid uplink payload")
    supplied_token, channel, payload = decoded
    if (
        not isinstance(supplied_token, str)
        or not expected_token
        or not secrets.compare_digest(
            supplied_token.encode("utf-8"),
            expected_token.encode("utf-8"),
        )
    ):
        raise ValueError("invalid uplink credential")
    if channel not in _UPLINK_CHANNELS or not isinstance(payload, dict):
        raise ValueError("invalid uplink payload")
    return channel, payload


_IMAGE_HWM = 8
_IMAGE_MAX_BYTES = 8 * 1024 * 1024
_IMAGE_AUTH_KEY = "_auth"

# ── Message uplink size ceiling ────────────────────────────────────
#
# RCVHWM bounds how MANY frames may queue on a PULL socket, never how large one
# frame is. Without a size ceiling the host receives and MessagePack-decodes
# whatever a plugin writes onto the message uplink, and push_message()'s local
# size check is no defence: a plugin that writes an authenticated frame onto
# the socket directly never runs it. The image uplink has carried MAXMSGSIZE
# from the start; this is the same bound for the message plane.
#
# The number is derived, not chosen. Downstream, ingest measures each delta
# item against MESSAGE_PLANE_PAYLOAD_MAX_BYTES and drops the whole item when it
# is over, so a payload above that cap cannot survive ingest no matter what the
# transport does with it. One frame here is either a single such payload
# (CH_MSG) or one batch of them (CH_MSG_BATCH), and the batcher flushes at
# PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE items, so the largest frame legitimate
# traffic can produce is batch_size * payload_max. The headroom on top covers
# the msgpack envelope wrapped around those payloads -- the token, the channel
# tag, the "items" array header, tens of bytes in practice -- and is generous
# because the failure mode of a ceiling set too low is silent loss of real
# traffic (see below), while the cost of extra slack is bounded by the same
# per-frame allocation this limit exists to bound. Both settings are host-side,
# so plugin code cannot widen the ceiling by setting an env var.
#
# What this does at runtime, because it is easy to expect the wrong thing:
# libzmq enforces MAXMSGSIZE in the receiving engine, not in the recv() call.
# An oversized frame is discarded there and the offending peer's connection is
# torn down (ZMTP 3.x). recv() does NOT raise, and the host never sees the
# bytes -- so there is no error path to write here. The observable behaviour is
# that the frame simply never arrives, and the child's PUSH socket reconnects
# with whatever it had in flight lost.
_MESSAGE_ENVELOPE_HEADROOM_BYTES = 64 * 1024


def _message_uplink_max_bytes() -> int:
    """Return the byte ceiling for one frame on the message uplink."""
    from plugin.settings import (
        MESSAGE_PLANE_PAYLOAD_MAX_BYTES,
        PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE,
    )

    payload_max = max(1, int(MESSAGE_PLANE_PAYLOAD_MAX_BYTES))
    batch_max = max(1, int(PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE))
    return payload_max * batch_max + _MESSAGE_ENVELOPE_HEADROOM_BYTES


def _authenticate_image_metadata(
    metadata: dict,
    *,
    expected_token: str,
) -> dict:
    """Strip and verify the uplink token carried by an image metadata frame.

    The image uplink does not go through ``_decode_uplink`` — re-packing
    megabytes of pixels through MessagePack would buy nothing — so the same
    credential check lives here instead. It is the only gate on this socket:
    without it, this would be the one child → host path any local process that
    can guess the port could write to.
    """
    supplied_token = metadata.pop(_IMAGE_AUTH_KEY, None)
    if (
        not isinstance(supplied_token, str)
        or not expected_token
        or not secrets.compare_digest(
            supplied_token.encode("utf-8"),
            expected_token.encode("utf-8"),
        )
    ):
        raise ValueError("invalid image uplink credential")
    return metadata


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
        self._uplink_token = secrets.token_urlsafe(32)

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
        # Deliberately NO MAXMSGSIZE here, unlike the message and image
        # uplinks below -- read the asymmetry as an open gap, not as a socket
        # someone forgot. A tool result or status frame has no downstream size
        # contract to derive a ceiling from (there is no
        # MESSAGE_PLANE_PAYLOAD_MAX_BYTES equivalent for a tool result), so any
        # number here would be invented, and one set too low silently drops
        # real results: libzmq kills the frame and the peer without raising.
        # Closing it needs a decision on what a legitimate maximum result is.
        self._ul_sock.bind("tcp://127.0.0.1:*")
        self.uplink_endpoint: str = self._ul_sock.getsockopt(zmq.LAST_ENDPOINT).decode()

        # Message uplink: physically separate plugin traffic from lifecycle,
        # tool, and status responses so slow message routing cannot create
        # head-of-line blocking on the control uplink.
        self._msg_sock = self._ctx.socket(zmq.PULL)
        self._msg_sock.setsockopt(zmq.LINGER, 0)
        self._msg_sock.setsockopt(zmq.RCVHWM, 5000)
        # RCVHWM above is a frame count; this is the frame size. See
        # _message_uplink_max_bytes for how the bound is derived and what
        # libzmq does with a frame that exceeds it (it drops the frame and
        # drops the peer -- recv never raises).
        self._msg_sock.setsockopt(zmq.MAXMSGSIZE, _message_uplink_max_bytes())
        self._msg_sock.bind("tcp://127.0.0.1:*")
        self.message_uplink_endpoint: str = self._msg_sock.getsockopt(
            zmq.LAST_ENDPOINT
        ).decode()

        # Bulk image uplink: isolated from status/result/control traffic so a
        # full media queue cannot head-of-line block the plugin control plane.
        self._img_sock = self._ctx.socket(zmq.PULL)
        self._img_sock.setsockopt(zmq.LINGER, 0)
        self._img_sock.setsockopt(zmq.RCVHWM, _IMAGE_HWM)
        self._img_sock.setsockopt(zmq.MAXMSGSIZE, _IMAGE_MAX_BYTES)
        self._img_sock.bind("tcp://127.0.0.1:*")
        self.image_uplink_endpoint: str = self._img_sock.getsockopt(zmq.LAST_ENDPOINT).decode()

        self._closed = False

    @property
    def uplink_token(self) -> str:
        return self._uplink_token

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
            return _decode_uplink(raw, expected_token=self._uplink_token)
        return None

    async def recv_message(
        self,
        timeout_ms: int = 1000,
    ) -> Optional[Tuple[str, dict]]:
        """Receive one authenticated message or message batch."""
        if await self._msg_sock.poll(timeout=timeout_ms):
            raw = await self._msg_sock.recv()
            channel, payload = _decode_uplink(
                raw,
                expected_token=self._uplink_token,
            )
            if channel not in {CH_MSG, CH_MSG_BATCH}:
                raise ValueError("invalid message uplink channel")
            return channel, payload
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
            metadata = _authenticate_image_metadata(
                metadata,
                expected_token=self._uplink_token,
            )
            return metadata, bytes(frames[1])
        return None

    # ── lifecycle ────────────────────────────────────────────────

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for sock in (
            self._dl_sock,
            self._ul_sock,
            self._msg_sock,
            self._img_sock,
        ):
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
    * **Uplink sends** use regular ``zmq.PUSH`` sockets guarded by
      :class:`threading.Lock` instances so that timer threads can safely call
      ``channel_sender(...).put_nowait(...)`` without conflicting with the
      event-loop thread.
    """

    def __init__(
        self,
        downlink_endpoint: str,
        uplink_endpoint: str,
        uplink_token: str,
        *,
        message_uplink_endpoint: str | None = None,
        image_uplink_endpoint: str | None = None,
    ) -> None:
        # Sync context — used for the uplink PUSH socket (thread-safe via lock)
        self._sync_ctx = zmq.Context()

        self._ul_sock = self._sync_ctx.socket(zmq.PUSH)
        self._ul_sock.setsockopt(zmq.LINGER, _LINGER_MS)
        self._ul_sock.setsockopt(zmq.SNDHWM, 5000)
        self._ul_sock.connect(uplink_endpoint)
        self._ul_lock = threading.Lock()

        self._msg_sock = self._ul_sock
        self._msg_lock = self._ul_lock
        if message_uplink_endpoint:
            self._msg_sock = self._sync_ctx.socket(zmq.PUSH)
            self._msg_sock.setsockopt(zmq.LINGER, _LINGER_MS)
            self._msg_sock.setsockopt(zmq.SNDHWM, 5000)
            self._msg_sock.connect(message_uplink_endpoint)
            self._msg_lock = threading.Lock()
        self._message_batcher = None
        self._message_batcher_init_lock = threading.Lock()

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
        self._message_uplink_endpoint = message_uplink_endpoint or uplink_endpoint
        self._uplink_token = uplink_token
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
                # Same credential the MessagePack uplinks carry, in the frame
                # the host already parses: one answer to "who is allowed to
                # write into the host" across all uplinks.
                _IMAGE_AUTH_KEY: self._uplink_token,
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
        data = _encode_uplink(self._uplink_token, channel, msg)
        sock, lock = self._uplink_socket(channel)
        with lock:
            sock.send(data)

    def send_uplink_nowait(self, channel: str, msg: Any) -> None:
        """Thread-safe non-blocking send on the uplink."""
        data = _encode_uplink(self._uplink_token, channel, msg)
        sock, lock = self._uplink_socket(channel)
        with lock:
            sock.send(data, zmq.NOBLOCK)

    def _uplink_socket(self, channel: str):
        if channel in {CH_MSG, CH_MSG_BATCH}:
            return self._msg_sock, self._msg_lock
        return self._ul_sock, self._ul_lock

    def send_fast_message_nowait(self, msg: Any) -> None:
        if self._closed:
            raise RuntimeError("plugin transport is closed")
        batcher = self._message_batcher
        if batcher is None:
            with self._message_batcher_init_lock:
                if self._closed:
                    raise RuntimeError("plugin transport is closed")
                batcher = self._message_batcher
                if batcher is None:
                    from plugin.settings import (
                        MESSAGE_PLANE_PUSH_BATCHER_ENQUEUE_TIMEOUT_SECONDS,
                        MESSAGE_PLANE_PUSH_BATCHER_MAX_QUEUE,
                        MESSAGE_PLANE_PUSH_BATCHER_REJECT_RATIO,
                        PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE,
                        PLUGIN_ZMQ_MESSAGE_PUSH_FLUSH_INTERVAL_MS,
                    )

                    batcher = _AuthenticatedMessageBatcher(
                        self,
                        batch_size=PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE,
                        flush_interval_ms=PLUGIN_ZMQ_MESSAGE_PUSH_FLUSH_INTERVAL_MS,
                        max_queue=MESSAGE_PLANE_PUSH_BATCHER_MAX_QUEUE,
                        reject_ratio=MESSAGE_PLANE_PUSH_BATCHER_REJECT_RATIO,
                        enqueue_timeout_s=(
                            MESSAGE_PLANE_PUSH_BATCHER_ENQUEUE_TIMEOUT_SECONDS
                        ),
                    )
                    batcher.start()
                    self._message_batcher = batcher
        batcher.enqueue(msg)

    # ── channel senders (queue-compatible interface) ─────────────

    def channel_sender(self, channel: str) -> "ChannelSender":
        """Return a :class:`ChannelSender` that mimics ``mp.Queue.put`` / ``put_nowait``."""
        if channel == CH_MSG:
            return MessageChannelSender(self, channel)
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
        # getattr guards throughout: unit tests build this object with
        # ``__new__`` and populate only the members they exercise, so close()
        # must not assume every field the real ``__init__`` sets.
        batcher_init_lock = getattr(self, "_message_batcher_init_lock", None)
        if batcher_init_lock is None:
            batcher_init_lock = threading.Lock()
        with batcher_init_lock:
            if self._closed:
                return
            self._closed = True
            batcher = getattr(self, "_message_batcher", None)
            self._message_batcher = None
        if batcher is not None:
            try:
                batcher.stop(timeout=2.0)
            except Exception:
                pass
        sockets = [
            sock
            for sock in (
                getattr(self, "_dl_sock", None),
                getattr(self, "_ul_sock", None),
            )
            if sock is not None
        ]
        msg_sock = getattr(self, "_msg_sock", None)
        if msg_sock is not None and msg_sock is not getattr(self, "_ul_sock", None):
            sockets.append(msg_sock)
        for sock in sockets:
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


class MessageChannelSender(ChannelSender):
    """Message sender with an authenticated, bounded batching fast path."""

    def put_fast_nowait(self, obj: Any) -> None:
        self._transport.send_fast_message_nowait(obj)


class _AuthenticatedMessageBatcher:
    def __init__(
        self,
        transport: ChildTransport,
        *,
        batch_size: int,
        flush_interval_ms: int,
        max_queue: int,
        reject_ratio: float,
        enqueue_timeout_s: float,
    ) -> None:
        self._transport = transport
        self._batch_size = max(1, int(batch_size))
        self._flush_interval_s = max(0.001, float(flush_interval_ms) / 1000.0)
        self._max_queue = int(max_queue)
        self._queue: queue.Queue[dict] = queue.Queue(maxsize=self._max_queue)
        self._reject_ratio = min(1.0, max(0.0, float(reject_ratio)))
        self._enqueue_timeout_s = max(0.0, float(enqueue_timeout_s))
        self._dropped = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run,
            name="plugin-authenticated-message-batcher",
            daemon=True,
        )
        self._thread.start()

    def enqueue(self, item: Any) -> None:
        if self._stop.is_set() or not isinstance(item, dict):
            raise RuntimeError("authenticated message batcher unavailable")
        if (
            self._max_queue > 0
            and self._reject_ratio > 0
            and self._queue.qsize() >= self._queue.maxsize * self._reject_ratio
        ):
            raise queue.Full
        self._queue.put(item, timeout=self._enqueue_timeout_s)

    def stop(self, timeout: float = 1.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(0.0, float(timeout)))

    def _run(self) -> None:
        batch: list[dict] = []
        deadline = time.monotonic() + self._flush_interval_s
        while batch or not self._stop.is_set() or not self._queue.empty():
            remaining = max(0.0, deadline - time.monotonic())
            try:
                item = self._queue.get(timeout=remaining)
            except queue.Empty:
                item = None
            if item is not None:
                batch.append(item)
            now = time.monotonic()
            if not batch and item is None:
                deadline = now + self._flush_interval_s
                continue
            if batch and (
                len(batch) >= self._batch_size
                or now >= deadline
                or (self._stop.is_set() and self._queue.empty())
            ):
                try:
                    self._transport.send_uplink_nowait(
                        CH_MSG_BATCH,
                        {"items": batch},
                    )
                except Exception as exc:
                    self._dropped += len(batch)
                    logger.warning(
                        "Authenticated message batch dropped "
                        f"(items={len(batch)} total_dropped={self._dropped} "
                        f"error_type={type(exc).__name__})"
                    )
                batch = []
                deadline = now + self._flush_interval_s
