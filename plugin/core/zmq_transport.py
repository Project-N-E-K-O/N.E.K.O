"""ZeroMQ transport for plugin host ↔ child process communication.

Replaces ``multiprocessing.Queue`` with three authenticated ZMQ channels:

* **Downlink** (host → child): commands, plugin-to-plugin responses
* **Control uplink** (child → host): results, status, plugin-to-plugin requests
* **Message uplink** (child → host): individual and batched plugin messages

Downlink messages are serialised with :mod:`pickle` for compatibility with
existing host commands. Uplink messages, which cross from untrusted plugin code
into the host, use MessagePack so decoding cannot execute plugin-controlled
objects. Both directions carry a *channel tag* so the receiver can demux.

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

import hashlib
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
from zmq.auth.thread import ThreadAuthenticator

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
_CURVE_DOMAIN = b"neko-plugin-host"


class _SingleClientCurveCredentials:
    def __init__(self, client_public_key: bytes) -> None:
        self._expected_key = client_public_key

    def callback(self, _domain: str, key: bytes) -> bool:
        return secrets.compare_digest(key, self._expected_key)


def _derive_permission_generation(uplink_token: str) -> str:
    if not uplink_token:
        return ""
    return hashlib.sha256(uplink_token.encode("utf-8")).hexdigest()


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


# ═══════════════════════════════════════════════════════════════════
# Host-side transport (runs in the user_plugin_server process)
# ═══════════════════════════════════════════════════════════════════

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
        server_public_key, server_secret_key = zmq.curve_keypair()
        client_public_key, client_secret_key = zmq.curve_keypair()
        self._downlink_curve_credentials = (
            server_public_key,
            client_public_key,
            client_secret_key,
        )
        self._authenticator = ThreadAuthenticator(self._ctx)
        self._authenticator.start()
        self._authenticator.configure_curve_callback(
            domain=_CURVE_DOMAIN.decode("ascii"),
            credentials_provider=_SingleClientCurveCredentials(client_public_key),
        )

        # Downlink: host → child (PUSH/PULL)
        self._dl_sock = self._ctx.socket(zmq.PUSH)
        self._dl_sock.curve_publickey = server_public_key
        self._dl_sock.curve_secretkey = server_secret_key
        self._dl_sock.curve_server = True
        self._dl_sock.zap_domain = _CURVE_DOMAIN
        self._dl_sock.setsockopt(zmq.LINGER, _LINGER_MS)
        self._dl_sock.setsockopt(zmq.SNDHWM, 5000)
        self._dl_sock.bind("tcp://127.0.0.1:*")
        self.downlink_endpoint: str = self._dl_sock.getsockopt(zmq.LAST_ENDPOINT).decode()

        # Uplink: child → host (PUSH/PULL)
        self._ul_sock = self._ctx.socket(zmq.PULL)
        self._ul_sock.curve_publickey = server_public_key
        self._ul_sock.curve_secretkey = server_secret_key
        self._ul_sock.curve_server = True
        self._ul_sock.zap_domain = _CURVE_DOMAIN
        self._ul_sock.setsockopt(zmq.LINGER, 0)
        self._ul_sock.setsockopt(zmq.RCVHWM, 5000)
        self._ul_sock.bind("tcp://127.0.0.1:*")
        self.uplink_endpoint: str = self._ul_sock.getsockopt(zmq.LAST_ENDPOINT).decode()

        # Message uplink: physically separate plugin traffic from lifecycle,
        # tool, and status responses so slow message routing cannot create
        # head-of-line blocking on the control uplink.
        self._msg_sock = self._ctx.socket(zmq.PULL)
        self._msg_sock.curve_publickey = server_public_key
        self._msg_sock.curve_secretkey = server_secret_key
        self._msg_sock.curve_server = True
        self._msg_sock.zap_domain = _CURVE_DOMAIN
        self._msg_sock.setsockopt(zmq.LINGER, 0)
        self._msg_sock.setsockopt(zmq.RCVHWM, 5000)
        self._msg_sock.bind("tcp://127.0.0.1:*")
        self.message_uplink_endpoint: str = self._msg_sock.getsockopt(
            zmq.LAST_ENDPOINT
        ).decode()

        self._closed = False

    @property
    def uplink_token(self) -> str:
        return self._uplink_token

    @property
    def downlink_curve_credentials(self) -> tuple[bytes, bytes, bytes]:
        return self._downlink_curve_credentials

    @property
    def permission_generation(self) -> str:
        return _derive_permission_generation(self._uplink_token)

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

    # ── lifecycle ────────────────────────────────────────────────

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for sock in (self._dl_sock, self._ul_sock, self._msg_sock):
            try:
                sock.close(linger=0)
            except Exception:
                pass
        try:
            self._authenticator.stop()
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
        downlink_curve: tuple[bytes, bytes, bytes] | None = None,
        message_uplink_endpoint: str | None = None,
    ) -> None:
        if downlink_curve is None or len(downlink_curve) != 3:
            raise ValueError("Plugin child process requires CURVE credentials")
        server_public_key, client_public_key, client_secret_key = downlink_curve

        # Sync context — used for the uplink PUSH socket (thread-safe via lock)
        self._sync_ctx = zmq.Context()

        self._ul_sock = self._sync_ctx.socket(zmq.PUSH)
        self._ul_sock.curve_serverkey = server_public_key
        self._ul_sock.curve_publickey = client_public_key
        self._ul_sock.curve_secretkey = client_secret_key
        self._ul_sock.setsockopt(zmq.LINGER, _LINGER_MS)
        self._ul_sock.setsockopt(zmq.SNDHWM, 5000)
        self._ul_sock.connect(uplink_endpoint)
        self._ul_lock = threading.Lock()

        self._msg_sock = self._ul_sock
        self._msg_lock = self._ul_lock
        if message_uplink_endpoint:
            self._msg_sock = self._sync_ctx.socket(zmq.PUSH)
            self._msg_sock.curve_serverkey = server_public_key
            self._msg_sock.curve_publickey = client_public_key
            self._msg_sock.curve_secretkey = client_secret_key
            self._msg_sock.setsockopt(zmq.LINGER, _LINGER_MS)
            self._msg_sock.setsockopt(zmq.SNDHWM, 5000)
            self._msg_sock.connect(message_uplink_endpoint)
            self._msg_lock = threading.Lock()
        self._message_batcher = None
        self._message_batcher_init_lock = threading.Lock()

        # Async context — used for the downlink PULL socket (event-loop only)
        self._async_ctx = zmq.asyncio.Context()
        self._dl_sock = self._async_ctx.socket(zmq.PULL)
        self._dl_sock.curve_serverkey = server_public_key
        self._dl_sock.curve_publickey = client_public_key
        self._dl_sock.curve_secretkey = client_secret_key
        self._dl_sock.setsockopt(zmq.LINGER, 0)
        self._dl_sock.connect(downlink_endpoint)

        self._downlink_endpoint = downlink_endpoint
        self._uplink_endpoint = uplink_endpoint
        self._message_uplink_endpoint = message_uplink_endpoint or uplink_endpoint
        self._uplink_token = uplink_token
        self._closed = False

    @property
    def permission_generation(self) -> str:
        return _derive_permission_generation(self._uplink_token)

    # ── downlink (async, event-loop only) ────────────────────────

    async def recv_downlink(self, timeout_ms: int = 1000) -> Optional[Tuple[str, dict]]:
        """Receive ``(channel, payload)`` from the downlink, or *None* on timeout."""
        if await self._dl_sock.poll(timeout=timeout_ms):
            raw = await self._dl_sock.recv()
            return pickle.loads(raw)  # type: ignore[return-value]
        return None

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

    def close(self) -> None:
        with self._message_batcher_init_lock:
            if self._closed:
                return
            self._closed = True
            batcher = self._message_batcher
            self._message_batcher = None
        if batcher is not None:
            try:
                batcher.stop(timeout=2.0)
            except Exception:
                pass
        sockets = [self._dl_sock, self._ul_sock]
        if self._msg_sock is not self._ul_sock:
            sockets.append(self._msg_sock)
        for sock in sockets:
            try:
                sock.close(linger=0)
            except Exception:
                pass
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
        while not self._stop.is_set() or not self._queue.empty():
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
                except Exception:
                    pass
                batch = []
                deadline = now + self._flush_interval_s
