"""ZeroMQ transport for plugin host ↔ child process communication.

Replaces ``multiprocessing.Queue`` with a pair of ZMQ PUSH/PULL sockets:

* **Downlink** (host → child): commands, plugin-to-plugin responses
* **Uplink** (child → host): results, status, messages, plugin-to-plugin requests

All messages are serialised with :mod:`json` (replacing the previous
``pickle``-based scheme for security — pickle deserialisation can execute
arbitrary code) and carry a *channel tag* so the receiver can demux.

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

import json
import logging
import threading
from typing import Any, Optional, Tuple

import zmq
import zmq.asyncio

logger = logging.getLogger(__name__)

# ── Channel constants ──────────────────────────────────────────────
CH_CMD = "cmd"
CH_RES = "res"
CH_STS = "sts"
CH_MSG = "msg"
CH_COMM = "comm"
CH_RESP = "resp"

_LINGER_MS = 1000


# ── JSON serialisation helpers ──────────────────────────────────────

class _SafeJSONEncoder(json.JSONEncoder):
    """JSON encoder that falls back to ``str()`` for non-serialisable types.

    This ensures that unexpected objects (e.g. ``datetime``, custom classes)
    do not crash the transport, while logging a warning so developers can
    identify and fix the root cause.
    """

    def default(self, o):  # noqa: D401
        logger.warning(
            "ZMQ transport: falling back to str() for non-JSON type %s",
            type(o).__name__,
        )
        return str(o)


def _serialize_msg(channel: str, msg: Any) -> bytes:
    """Serialise ``(channel, msg)`` as JSON bytes.

    Replaces ``pickle.dumps`` to eliminate arbitrary-code-execution risk
    from deserialised ZMQ payloads.
    """
    return json.dumps(
        [channel, msg], cls=_SafeJSONEncoder, ensure_ascii=False
    ).encode("utf-8")


def _deserialize_msg(raw: bytes) -> Tuple[str, Any]:
    """Deserialise JSON bytes to ``(channel, msg)`` tuple.

    Replaces ``pickle.loads`` to eliminate arbitrary-code-execution risk.
    """
    result = json.loads(raw.decode("utf-8"))
    return result[0], result[1]


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

        self._closed = False

    # ── send helpers ─────────────────────────────────────────────

    async def send_command(self, msg: dict) -> None:
        """Send a command on the downlink."""
        await self._dl_sock.send(_serialize_msg(CH_CMD, msg))

    async def send_response(self, msg: dict) -> None:
        """Send a plugin-to-plugin response on the downlink."""
        await self._dl_sock.send(_serialize_msg(CH_RESP, msg))

    # ── recv helper ──────────────────────────────────────────────

    async def recv(self, timeout_ms: int = 1000) -> Optional[Tuple[str, dict]]:
        """Receive one ``(channel, payload)`` from the uplink, or *None* on timeout."""
        if await self._ul_sock.poll(timeout=timeout_ms):
            raw = await self._ul_sock.recv()
            return _deserialize_msg(raw)
        return None

    # ── lifecycle ────────────────────────────────────────────────

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for sock in (self._dl_sock, self._ul_sock):
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

    def __init__(self, downlink_endpoint: str, uplink_endpoint: str) -> None:
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

        self._downlink_endpoint = downlink_endpoint
        self._uplink_endpoint = uplink_endpoint
        self._closed = False

    # ── downlink (async, event-loop only) ────────────────────────

    async def recv_downlink(self, timeout_ms: int = 1000) -> Optional[Tuple[str, dict]]:
        """Receive ``(channel, payload)`` from the downlink, or *None* on timeout."""
        if await self._dl_sock.poll(timeout=timeout_ms):
            raw = await self._dl_sock.recv()
            return _deserialize_msg(raw)
        return None

    # ── uplink (thread-safe, any thread) ─────────────────────────

    def send_uplink(self, channel: str, msg: Any, *, timeout: float = 10.0) -> None:
        """Thread-safe blocking send on the uplink."""
        data = _serialize_msg(channel, msg)
        with self._ul_lock:
            self._ul_sock.send(data)

    def send_uplink_nowait(self, channel: str, msg: Any) -> None:
        """Thread-safe non-blocking send on the uplink."""
        data = _serialize_msg(channel, msg)
        with self._ul_lock:
            self._ul_sock.send(data, zmq.NOBLOCK)

    # ── channel senders (queue-compatible interface) ─────────────

    def channel_sender(self, channel: str) -> "ChannelSender":
        """Return a :class:`ChannelSender` that mimics ``mp.Queue.put`` / ``put_nowait``."""
        return ChannelSender(self, channel)

    # ── lifecycle ────────────────────────────────────────────────

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for sock in (self._dl_sock, self._ul_sock):
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
