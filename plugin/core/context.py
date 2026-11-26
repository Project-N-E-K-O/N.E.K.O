"""
Plugin context module

Provides plugin runtime context, including status updates and message-push features.
"""
import asyncio
import base64
import contextlib
import contextvars
import copy
import math
import queue
import threading
import time
from collections import deque

try:
    import tomllib
except ImportError:
    import tomli as tomllib
import functools
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional, cast

try:
    import ormsgpack
except ImportError:  # pragma: no cover
    ormsgpack = None  # type: ignore[assignment]

try:
    import zmq
except ImportError:  # pragma: no cover
    zmq = None

from plugin.core.rpc import RpcPeer
from plugin.settings import (
    EXPORT_INLINE_BINARY_MAX_BYTES,
    PLUGIN_LOG_CTX_STATUS_UPDATE,
    PLUGIN_LOG_SYNC_CALL_WARNINGS,
    SYNC_CALL_IN_HANDLER_POLICY,
)

if TYPE_CHECKING:
    # Used only for type annotations on PluginContext.app; importing at runtime would pull all of fastapi/starlette into every plugin subprocess.
    from fastapi import FastAPI

    from plugin.core.bus.conversations import ConversationClient
    from plugin.core.bus.events import EventClient
    from plugin.core.bus.frames import FrameClient
    from plugin.core.bus.lifecycle import LifecycleClient
    from plugin.core.bus.memory import MemoryClient
    from plugin.core.bus.messages import MessageClient
    from plugin.core.bus.types import BusHubProtocol

    # ⚠ Never import loguru. The actual type of the logger field is plugin.logging_config.PluginLoggerAdapter.
    from plugin.logging_config import PluginLoggerAdapter as LoguruLogger
    from plugin.sdk.shared.core.call_chain_view import CallChainView
    from plugin.sdk.shared.core.types import PushMessageRejected, PushMessageResult


_IN_HANDLER: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("plugin_in_handler", default=None)

_CURRENT_RUN_ID: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("plugin_current_run_id", default=None)
_CURRENT_LANLAN: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("plugin_current_lanlan", default=None)


def _attach_parent_request_id(payload: Dict[str, Any]) -> None:
    """Let the host attribute this push to the invocation that made it."""
    from plugin.core.rpc import current_request_id

    parent = current_request_id()
    if parent is not None:
        payload["_rpc_parent_request_id"] = parent


def _is_submission_backpressure(error: BaseException) -> bool:
    """Return whether a non-blocking local submission path was full."""
    if isinstance(error, (asyncio.QueueFull, queue.Full)):
        return True
    again_type = getattr(zmq, "Again", None) if zmq is not None else None
    return isinstance(again_type, type) and isinstance(error, again_type)


# base64 spends 4 wire bytes for every 3 raw bytes, and the rest of the
# envelope is scalars plus whatever text parts ride along -- a few hundred
# bytes at most.  So this ratio is what turns MESSAGE_PLANE_PAYLOAD_MAX_BYTES
# into the raw-bytes budget an author can actually aim at, and it is the number
# the rejection log prints.  It only holds while an inline payload rides the
# wire ONCE: the envelope used to carry a raw duplicate in the legacy
# ``binary_data`` field as well, which put the real ratio at ~2.34x.
_INLINE_BASE64_WIRE_RATIO = 4.0 / 3.0

# Label for the deprecated top-level ``binary_data`` field in the rejection
# log.  It is not a part type and is deliberately not mapped onto one: it only
# survives translation when the caller passed it next to an explicit ``parts=``
# list, and in that shape nothing in the payload says what those bytes are.
_LEGACY_BINARY_CARRIER = "binary_data"


def _inline_binary_carriers(
    parts: Any, legacy_binary_data: Any
) -> tuple[tuple[str, int], ...]:
    """Return ``(carrier label, wire bytes)`` per inline payload, in wire order.

    This used to double as a gate: an empty tuple skipped the size probe
    entirely, on the theory that only inline bytes can realistically blow
    MESSAGE_PLANE_PAYLOAD_MAX_BYTES.  That gate is gone.  The host measures the
    WHOLE envelope, so an oversized text or metadata push was dropped there
    while push_message() had already answered submitted=True -- and the cost it
    was avoiding turned out to be 0.19us per typical cue, measured.  Every
    payload is probed now, and an empty tuple here means only "nothing travels
    inline", never "skip the check".

    The labels and sizes exist so the rejection can name the payload that
    actually blew the cap.  ``ctx.images.upload()`` is the remedy for an image
    and for nothing else -- there is no audio or video upload helper today --
    so a rejection that always pointed there sent the author of an inline
    audio part hunting for an API that does not exist (Codex).

    Both carriers are reported because they can appear independently:
    ``parts[].binary_base64`` is the canonical one, while ``binary_data`` is
    the legacy field that :func:`translate_push_message` leaves untranslated
    when a caller passes v2 ``parts`` and the deprecated ``binary_data=``
    kwarg together.  Either one alone is enough to reach the cap.
    """
    carriers: list[tuple[str, int]] = []
    if isinstance(parts, list):
        for part in parts:
            if not isinstance(part, dict):
                continue
            blob = part.get("binary_base64")
            if not isinstance(blob, str) or not blob:
                continue
            part_type = part.get("type")
            label = part_type if isinstance(part_type, str) and part_type else "unknown"
            carriers.append((label, len(blob)))
    if isinstance(legacy_binary_data, (bytes, bytearray)) and legacy_binary_data:
        carriers.append((_LEGACY_BINARY_CARRIER, len(legacy_binary_data)))
    return tuple(carriers)


def _inline_carrier_totals(
    carriers: tuple[tuple[str, int], ...]
) -> list[tuple[str, int]]:
    """Aggregate carriers per label, biggest total first.

    Aggregating before ranking is what makes "which one blew the cap" answer
    the question the author is actually asking: ten thumbnails that together
    outweigh one voice clip are the thing to fix, even though the clip is the
    single largest part.  The label breaks ties so the log line is stable.
    """
    totals: Dict[str, int] = {}
    for label, size in carriers:
        totals[label] = totals.get(label, 0) + size
    return sorted(totals.items(), key=lambda item: (-item[1], item[0]))


def _synthesize_legacy_message_type(canonical: Dict[str, Any]) -> str:
    """Best-effort legacy ``message_type`` for v1 consumers.

    Inspects ``canonical['parts']`` for ui_action shapes that map onto the
    deprecated music_* discriminators; otherwise classifies the call by
    visibility/ai_behavior so query_service still has a non-empty type.
    """
    parts = canonical.get("parts") or []
    for p in parts:
        if not isinstance(p, dict):
            continue
        if p.get("type") == "ui_action":
            action = p.get("action")
            if action == "media_play_url":
                return "music_play_url"
            if action == "media_allowlist_add":
                return "music_allowlist_add"
    if canonical.get("ai_behavior") in ("respond", "read"):
        return "proactive_notification"
    return "text"


def _synthesize_legacy_content(parts: list) -> Optional[str]:
    """Concatenate text parts so query_service has a non-empty ``content``."""
    pieces: list[str] = []
    for p in parts:
        if not isinstance(p, dict):
            continue
        if p.get("type") == "text":
            t = p.get("text")
            if isinstance(t, str) and t:
                pieces.append(t)
    return "\n".join(pieces) if pieces else None


class _BusHub:
    def __init__(self, ctx: "PluginContext"):
        self._ctx = ctx

    @functools.cached_property
    def memory(self) -> "MemoryClient":
        from plugin.core.bus.memory import MemoryClient

        return MemoryClient(self._ctx)

    @functools.cached_property
    def messages(self) -> "MessageClient":
        from plugin.core.bus.messages import MessageClient

        return MessageClient(self._ctx)

    @functools.cached_property
    def events(self) -> "EventClient":
        from plugin.core.bus.events import EventClient

        return EventClient(self._ctx)

    @functools.cached_property
    def lifecycle(self) -> "LifecycleClient":
        from plugin.core.bus.lifecycle import LifecycleClient

        return LifecycleClient(self._ctx)

    @functools.cached_property
    def conversations(self) -> "ConversationClient":
        from plugin.core.bus.conversations import ConversationClient

        return ConversationClient(self._ctx)

    @functools.cached_property
    def conversations_v2(self):
        from plugin.core.bus.conversations import ConversationV2Client

        return ConversationV2Client(self._ctx)

    @functools.cached_property
    def frames(self) -> "FrameClient":
        from plugin.core.bus.frames import FrameClient

        return FrameClient(self._ctx)


# The host normalizes the record before writing to the message plane, so the packed payload is larger than what the SDK measures
# inside push_message(). _reject_if_payload_too_large subtracts this overhead so a push that is
# "right at the lower edge of the limit" is rejected synchronously instead of receiving submitted=True and then being
# silently discarded during ingest.
#
# **Derived, not guessed** — an earlier choice of 128 was disproven in practice: the host-added plugin_id
# can be 128 characters, and that alone can cause 222 bytes of drift. Three contributors:
#
#   plugin_id   added by the host from authenticated identity, key + value (≤ _HOST_PLUGIN_ID_MAX_CHARS)
#   message_id  filled with a uuid4 string when missing (36 characters)
#   time        converted to an ISO string (28 characters) when missing or a float (fast_mode)
#
# Each also includes the msgpack key and length header. It is module-level rather than buried in a function because the guard
# reads it — the guard measures the **worst-case shape** (both missing + longest plugin_id), so an undersized value
# fails instead of silently reopening the gap.
_HOST_PLUGIN_ID_MAX_CHARS = 128
_HOST_ENVELOPE_HEADROOM_BYTES = (
    (4 + len("plugin_id") + _HOST_PLUGIN_ID_MAX_CHARS)
    + (4 + len("message_id") + 36)
    + (4 + len("time") + 28)
)


class _CrossLoopLockWaiter:
    __slots__ = ("loop", "future", "state")

    def __init__(self, loop: asyncio.AbstractEventLoop, future: asyncio.Future[None]) -> None:
        self.loop = loop
        self.future = future
        self.state = "waiting"  # "waiting" | "handed" | "aborted"


class _CrossLoopAsyncLock:
    """FIFO async mutex safe to share across threads and separate event loops."""

    def __init__(self) -> None:
        self._mu = threading.Lock()
        self._locked = False
        self._waiters: deque[_CrossLoopLockWaiter] = deque()

    @staticmethod
    def _wake_waiter(waiter: _CrossLoopLockWaiter) -> None:
        if not waiter.future.done():
            waiter.future.set_result(None)

    async def acquire(self, *, deadline: float) -> None:
        loop = asyncio.get_running_loop()
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            raise TimeoutError("Timed out waiting for config write lock")
        with self._mu:
            if not self._locked:
                self._locked = True
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                raise TimeoutError("Timed out waiting for config write lock")
            waiter = _CrossLoopLockWaiter(loop=loop, future=loop.create_future())
            self._waiters.append(waiter)
        try:
            async with asyncio.timeout(remaining):
                await waiter.future
        except BaseException:
            need_release = False
            with self._mu:
                if waiter.state == "waiting":
                    waiter.state = "aborted"
                    try:
                        self._waiters.remove(waiter)
                    except ValueError:
                        pass
                elif waiter.state == "handed":
                    # Ownership was already transferred to this waiter; pass it on.
                    waiter.state = "aborted"
                    need_release = True
            if need_release:
                self.release()
            raise

    def release(self) -> None:
        while True:
            with self._mu:
                if not self._locked:
                    raise RuntimeError("Lock is not acquired")
                waiter: _CrossLoopLockWaiter | None = None
                while self._waiters:
                    candidate = self._waiters.popleft()
                    if candidate.state == "waiting":
                        candidate.state = "handed"
                        waiter = candidate
                        break
                if waiter is None:
                    self._locked = False
                    return
            try:
                waiter.loop.call_soon_threadsafe(self._wake_waiter, waiter)
                return
            except RuntimeError:
                # Waiter loop closed before handoff callback could be scheduled.
                with self._mu:
                    if waiter.state == "handed":
                        waiter.state = "aborted"
                    else:
                        return

    @contextlib.asynccontextmanager
    async def hold(self, *, deadline: float) -> AsyncIterator[None]:
        await self.acquire(deadline=deadline)
        try:
            yield
        finally:
            self.release()


@dataclass
class PluginContext:
    """Plugin runtime context"""
    plugin_id: str
    config_path: Path
    logger: "LoguruLogger"
    status_queue: Any
    message_queue: Any = None  # Message-push queue
    app: Optional["FastAPI"] = None
    _plugin_comm_queue: Optional[Any] = None  # Inter-plugin communication queue (provided by the main process)
    _rpc_peer: Optional[RpcPeer] = field(default=None, init=False, repr=False)
    _rpc_closed: bool = field(default=False, init=False, repr=False)
    _conversation_watchers: Dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    _runtime_lock: Any = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
    )
    _image_transport: Optional[Any] = None
    _images: Optional[Any] = None
    _model_gateway_base_url: str = field(default="", repr=False)
    _model_gateway_token: str = field(default="", repr=False)
    _model_gateway_closed: bool = field(default=False, init=False, repr=False)
    _models: Optional[Any] = field(default=None, init=False, repr=False)
    _image_uploads_blocked: bool = False
    _entry_map: Optional[Dict[str, Any]] = None  # Entry-point mapping (for handling commands)
    _entry_meta_map: Optional[Dict[str, Any]] = None  # entry_id -> EventMeta
    _instance: Optional[Any] = None  # Plugin instance (for handling commands)
    _bus_hub: Optional[Any] = None
    _restored_from_freeze: bool = False  # Whether restored from frozen state
    _effective_config: Optional[Dict[str, Any]] = None
    _effective_config_uncertain: bool = False
    _effective_config_gen: int = 0
    _effective_config_publish_seq: int = 0
    _last_notified_config_seq: int = 0
    _profile_refresh_epoch: int = 0
    _config_state_lock: Any = field(default_factory=threading.RLock, init=False, repr=False)
    _config_notify_lock: Any = field(default_factory=threading.RLock, init=False, repr=False)
    _config_write_lock: _CrossLoopAsyncLock = field(
        default_factory=_CrossLoopAsyncLock,
        init=False,
        repr=False,
    )
    _current_lanlan: Optional[str] = None
    _permissions: Optional[Any] = field(default=None, init=False, repr=False)

    @property
    def bus(self) -> "BusHubProtocol":
        hub = self._bus_hub
        if hub is None:
            hub = _BusHub(self)
            self._bus_hub = hub
        return cast("BusHubProtocol", hub)

    @property
    def reply_tail(self) -> Any:
        from plugin.sdk.shared.core.reply_tail import ReplyTailClient

        return ReplyTailClient(self)

    @property
    def images(self) -> Any:
        images = self._images
        if images is None:
            from plugin.sdk.shared.core.images import PluginImages

            images = PluginImages(self)
            self._images = images
        return images

    @property
    def permissions(self) -> Any:
        """This plugin's permission states as the host last sent them (``get`` / ``on_change``)."""
        with self._runtime_lock:
            permissions = self._permissions
            if permissions is None:
                from plugin.sdk.shared.permissions.runtime import PluginPermissions

                permissions = PluginPermissions(self.logger)
                self._permissions = permissions
            return permissions

    @property
    def models(self) -> Any:
        with self._runtime_lock:
            models = self._models
            if models is None:
                from plugin.sdk.shared.core.models import PluginModels

                models = PluginModels(self)
                self._models = models
            return models

    async def _upload_image(self, data: bytes, *, mime: str, deadline: float | None = None, timeout: float) -> dict[str, object]:
        self._ensure_image_upload_available()
        if self._image_transport is None:
            raise RuntimeError("temporary image transport is not available")
        if timeout <= 0:
            raise ValueError("image upload timeout must be positive")
        deadline = deadline if deadline is not None else asyncio.get_running_loop().time() + timeout
        ready = getattr(self, "_downlink_ready", None)
        while ready is not None and not ready.is_set():
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"image upload timed out after {timeout}s (plugin downlink not ready)")
            await asyncio.sleep(min(0.02, remaining))
        try:
            # SDK image normalization uses the caller loop's clock. Transfer
            # its remaining budget, never that loop's arbitrary clock epoch.
            rpc_deadline = time.monotonic() + max(0.0, deadline - asyncio.get_running_loop().time())
            result = await self._get_rpc_peer().request("IMAGE_UPLOAD", {
                "data": data, "mime": mime, "_request_deadline_monotonic": rpc_deadline,
            }, timeout=timeout)
        except TimeoutError:
            raise TimeoutError(f"image upload timed out after {timeout}s") from None
        return self._unwrap_image_upload_response({"result": result})

    def _ensure_image_upload_available(self) -> None:
        if self._image_uploads_blocked:
            raise RuntimeError("ctx.images.upload() is not available while the plugin is freezing")

    def _get_rpc_peer(self) -> RpcPeer:
        with self._runtime_lock:
            if self._rpc_closed:
                raise RuntimeError("Plugin context is closed")
            if self._rpc_peer is None:
                self._rpc_peer = RpcPeer(self._send_rpc_request, cancel=self._cancel_rpc_request, logger=self.logger)
            return self._rpc_peer

    async def _send_rpc_request(self, request: dict, timeout: float | None) -> None:
        if request["type"] == "IMAGE_UPLOAD":
            await self._image_transport.send_image(request["request_id"], mime=request["mime"],
                data=request["data"], timeout=timeout)
            return
        queue = self._plugin_comm_queue
        if queue is None:
            raise RuntimeError(f"Plugin communication queue not available for plugin {self.plugin_id}")
        request = dict(request, from_plugin=self.plugin_id)
        await asyncio.to_thread(queue.put, request, timeout=timeout)

    async def _cancel_rpc_request(self, request: dict) -> None:
        if request["type"] != "IMAGE_UPLOAD" and self._plugin_comm_queue is not None:
            await asyncio.to_thread(self._plugin_comm_queue.put, {
                "type": "RPC_CANCEL", "from_plugin": self.plugin_id,
                "request_id": request["request_id"],
            }, timeout=0.1)

    def _dispatch_rpc_response(self, response: Any) -> bool:
        if not isinstance(response, dict) or response.get("type") not in ("IMAGE_UPLOAD_RESULT", "PLUGIN_TO_PLUGIN_RESPONSE"):
            return False
        request_id = response.get("request_id")
        with self._runtime_lock:
            peer = self._rpc_peer
        if peer is not None and isinstance(request_id, str):
            peer.receive(request_id, result=response.get("result"), error=response.get("error"))
        return True


    @staticmethod
    def _unwrap_image_upload_response(response: dict[str, Any]) -> dict[str, object]:
        error = response.get("error")
        if error:
            if isinstance(error, dict):
                message = error.get("message") or error.get("code") or "image upload failed"
            else:
                message = str(error)
            raise RuntimeError(str(message))
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("image upload returned no image part")
        return dict(result)

    def close(self) -> None:
        """Close this context and terminate all its outstanding requests."""
        from plugin.core.bus.rev import _watcher_pop

        self._model_gateway_closed = True
        with self._runtime_lock:
            self._rpc_closed = True
            peer = self._rpc_peer
            watchers = tuple(self._conversation_watchers.items())
            self._conversation_watchers.clear()
        for sid, watcher in watchers:
            _watcher_pop(sid)
            try:
                watcher._on_host_closed()
            except Exception:
                pass
        if peer is not None:
            peer.close()
        models = getattr(self, "_models", None)
        if models is not None:
            models.close()

    def _register_conversation_watcher(self, sub_id: str, watcher: object) -> None:
        from plugin.core.bus.rev import _watcher_set

        with self._runtime_lock:
            if self._rpc_closed:
                raise RuntimeError("Plugin context is closed")
            _watcher_set(sub_id, watcher)
            self._conversation_watchers[sub_id] = watcher

    def _unregister_conversation_watcher(self, sub_id: str) -> None:
        from plugin.core.bus.rev import _watcher_pop

        with self._runtime_lock:
            if sub_id in self._conversation_watchers:
                self._conversation_watchers.pop(sub_id)
                _watcher_pop(sub_id)

    def __del__(self) -> None:  # pragma: no cover - best-effort safety net
        try:
            self.close()
        except Exception:
            pass

    def _refresh_instance_runtime_config(self, effective_config: Dict[str, Any]) -> None:
        """Invoke the plugin instance's ``refresh_runtime_config`` callback.

        Runs synchronously under ``_config_notify_lock``; plugin callbacks must
        remain fast, non-blocking, and limited to in-memory state updates.
        """
        instance = getattr(self, "_instance", None)
        refresh = getattr(instance, "refresh_runtime_config", None)
        if not callable(refresh):
            return
        with self._get_config_notify_lock():
            try:
                refresh(effective_config)
            except Exception as exc:
                try:
                    self.logger.warning(
                        "[PluginContext] Failed to refresh runtime config: plugin_id={}, err_type={}, err={}",
                        self.plugin_id,
                        type(exc).__name__,
                        str(exc),
                    )
                except Exception:
                    pass
            else:
                if isinstance(effective_config, dict):
                    with self._get_config_state_lock():
                        self._last_runtime_config_snapshot = copy.deepcopy(effective_config)

    def _get_config_notify_lock(self) -> Any:
        lock = getattr(self, "_config_notify_lock", None)
        if lock is not None and callable(getattr(lock, "acquire", None)):
            return lock
        guard = getattr(self, "_runtime_lock", None)
        if guard is not None and callable(getattr(guard, "acquire", None)):
            with guard:
                lock = getattr(self, "_config_notify_lock", None)
                if lock is None or not callable(getattr(lock, "acquire", None)):
                    lock = threading.RLock()
                    self._config_notify_lock = lock
                return lock
        lock = threading.RLock()
        self._config_notify_lock = lock
        return lock

    def _get_config_state_lock(self) -> Any:
        lock = getattr(self, "_config_state_lock", None)
        if lock is not None and callable(getattr(lock, "acquire", None)):
            return lock
        guard = getattr(self, "_runtime_lock", None)
        if guard is not None and callable(getattr(guard, "acquire", None)):
            with guard:
                lock = getattr(self, "_config_state_lock", None)
                if lock is None or not callable(getattr(lock, "acquire", None)):
                    lock = threading.RLock()
                    self._config_state_lock = lock
                return lock
        lock = threading.RLock()
        self._config_state_lock = lock
        return lock

    def _current_effective_config_gen(self) -> int:
        with self._get_config_state_lock():
            try:
                return int(getattr(self, "_effective_config_gen", 0) or 0)
            except (TypeError, ValueError):
                return 0

    def _bump_effective_config_gen(self) -> int:
        with self._get_config_state_lock():
            next_gen = self._current_effective_config_gen() + 1
            self._effective_config_gen = next_gen
            self._last_installed_read_id = 0
            self._effective_config_publish_seq = (
                int(getattr(self, "_effective_config_publish_seq", 0) or 0) + 1
            )
            return next_gen

    def _allocate_owner_token_locked(self) -> int:
        token = int(getattr(self, "_next_effective_config_owner_token", 0) or 0) + 1
        self._next_effective_config_owner_token = token
        self._effective_config_owner_token = token
        return token

    def _record_effective_config_locked(
        self,
        config_copy: Dict[str, Any] | None,
        *,
        uncertain: bool,
        owner_token: int | None = None,
    ) -> int:
        self._effective_config = config_copy
        self._effective_config_uncertain = uncertain
        self._effective_config_fallback_notify = None
        self._effective_config_publish_seq = (
            int(getattr(self, "_effective_config_publish_seq", 0) or 0) + 1
        )
        if owner_token is None:
            self._rejected_host_rollback = None
            return self._allocate_owner_token_locked()
        self._effective_config_owner_token = owner_token
        return owner_token

    def _flush_instance_runtime_config(self) -> None:
        """Deliver the latest published effective config snapshot to the plugin instance.

        Serializes ``refresh_runtime_config`` calls synchronously under
        ``_config_notify_lock`` so callbacks observe snapshots in publication
        order. Callbacks must remain fast and non-blocking (in-memory updates only).
        """
        with self._get_config_notify_lock():
            while True:
                with self._get_config_state_lock():
                    seq = int(getattr(self, "_effective_config_publish_seq", 0) or 0)
                    last_seq = int(getattr(self, "_last_notified_config_seq", 0) or 0)
                    if seq <= last_seq:
                        return
                    pending_fallback = getattr(self, "_effective_config_fallback_notify", None)
                    self._effective_config_fallback_notify = None
                    if isinstance(self._effective_config, dict):
                        snapshot: Dict[str, Any] | None = copy.deepcopy(self._effective_config)
                    elif isinstance(pending_fallback, tuple) and pending_fallback[0] == seq:
                        snapshot = copy.deepcopy(pending_fallback[1])
                    else:
                        snapshot = None
                    self._last_notified_config_seq = seq
                if snapshot is not None:
                    self._refresh_instance_runtime_config(snapshot)

    def _begin_config_read(self) -> tuple[int, int]:
        with self._get_config_state_lock():
            read_id = int(getattr(self, "_next_config_read_id", 0) or 0) + 1
            self._next_config_read_id = read_id
            return self._current_effective_config_gen(), read_id

    def _get_config_write_lock(self) -> _CrossLoopAsyncLock:
        lock = getattr(self, "_config_write_lock", None)
        if isinstance(lock, _CrossLoopAsyncLock):
            return lock
        with self._get_config_state_lock():
            lock = getattr(self, "_config_write_lock", None)
            if not isinstance(lock, _CrossLoopAsyncLock):
                lock = _CrossLoopAsyncLock()
                self._config_write_lock = lock
            return lock

    @staticmethod
    def _normalize_config_timeout(timeout: float, *, cap: float | None = None) -> float:
        if isinstance(timeout, bool):
            raise TypeError("timeout must be a real number, not bool")
        value = float(timeout)
        if not math.isfinite(value):
            raise ValueError("timeout must be finite")
        if cap is not None:
            value = min(value, cap)
        return value

    def _mark_profile_write_uncertain(self) -> None:
        # Note: RPC_CANCEL is fire-and-forget on the client, so a subsequent
        # get_own_config() may still observe the previous on-disk profile and
        # clear _effective_config_uncertain before a late server commit lands.
        # Keeping reads able to re-anchor the cache avoids pinning uncertain=True
        # indefinitely when cancellation succeeded; any late commit is reconciled
        # by a subsequent config update or explicit reload.
        with self._get_config_state_lock():
            self._bump_effective_config_gen()
            self._effective_config_uncertain = True
            self._rejected_host_rollback = None
            self._allocate_owner_token_locked()

    def _invalidate_effective_config_for_profile_refresh(self) -> tuple[int, int]:
        with self._get_config_state_lock():
            refresh_gen = self._bump_effective_config_gen()
            refresh_epoch = int(getattr(self, "_profile_refresh_epoch", 0) or 0) + 1
            self._profile_refresh_epoch = refresh_epoch
            self._record_effective_config_locked(None, uncertain=False)
            return refresh_gen, refresh_epoch

    def _install_effective_config_value(
        self,
        config_obj: object,
        *,
        uncertain: bool = False,
    ) -> Dict[str, Any] | None:
        config_copy: Dict[str, Any] | None = None
        with self._get_config_state_lock():
            if not isinstance(config_obj, dict):
                self._record_effective_config_locked(None, uncertain=uncertain)
                return None
            config_copy = copy.deepcopy(config_obj)
            self._record_effective_config_locked(config_copy, uncertain=uncertain)
        self._flush_instance_runtime_config()
        return config_copy

    def _set_effective_config_cache(
        self,
        config_obj: object,
        *,
        uncertain: bool = False,
        fallback_notify_config: object = None,
    ) -> Dict[str, Any] | None:
        config_copy: Dict[str, Any] | None = None
        should_flush = False
        with self._get_config_state_lock():
            self._bump_effective_config_gen()
            if isinstance(config_obj, dict):
                config_copy = copy.deepcopy(config_obj)
                self._record_effective_config_locked(config_copy, uncertain=uncertain)
                should_flush = True
            else:
                self._record_effective_config_locked(None, uncertain=uncertain)
                if isinstance(fallback_notify_config, dict):
                    seq = int(getattr(self, "_effective_config_publish_seq", 0) or 0)
                    self._effective_config_fallback_notify = (
                        seq,
                        copy.deepcopy(fallback_notify_config),
                    )
                    should_flush = True
        if should_flush:
            self._flush_instance_runtime_config()
        return config_copy

    async def _wait_for_in_flight_config_write(self, *, deadline: float) -> bool:
        write_lock = self._get_config_write_lock()
        try:
            await write_lock.acquire(deadline=deadline)
        except TimeoutError:
            return False
        write_lock.release()
        return True

    def _replace_effective_config_cache_for_host(
        self,
        new_config: Mapping[str, Any],
    ) -> tuple[bool, bool, Dict[str, Any], Dict[str, Any], int, Dict[str, Any]]:
        with self._get_config_state_lock():
            had_cached = isinstance(self._effective_config, dict)
            had_uncertain = bool(getattr(self, "_effective_config_uncertain", False))
            old_config = copy.deepcopy(self._effective_config) if had_cached else {}
            last_runtime = getattr(self, "_last_runtime_config_snapshot", None)
            rollback_runtime_snapshot = (
                copy.deepcopy(old_config)
                if had_cached
                else (copy.deepcopy(last_runtime) if isinstance(last_runtime, dict) else {})
            )
            applied = copy.deepcopy(dict(new_config))
            self._bump_effective_config_gen()
            applied_owner_token = self._record_effective_config_locked(applied, uncertain=False)
        self._flush_instance_runtime_config()
        return (
            had_cached,
            had_uncertain,
            old_config,
            applied,
            applied_owner_token,
            rollback_runtime_snapshot,
        )

    def _merge_effective_config_cache_for_host(
        self,
        new_config: Mapping[str, Any],
        *,
        merge_fn: Any,
    ) -> tuple[str, bool, bool, Dict[str, Any], Dict[str, Any] | None, int, int, Dict[str, Any] | None]:
        with self._get_config_state_lock():
            if bool(getattr(self, "_config_write_in_progress", False)):
                return (
                    "wait_write",
                    False,
                    False,
                    {},
                    None,
                    0,
                    self._current_effective_config_gen(),
                    None,
                )
            had_cached = isinstance(self._effective_config, dict)
            had_uncertain = bool(getattr(self, "_effective_config_uncertain", False))
            last_runtime = getattr(self, "_last_runtime_config_snapshot", None)
            if not had_cached or had_uncertain:
                return (
                    "needs_hydrate",
                    False,
                    had_uncertain,
                    {},
                    None,
                    0,
                    self._current_effective_config_gen(),
                    copy.deepcopy(last_runtime) if isinstance(last_runtime, dict) else None,
                )
            old_config = copy.deepcopy(self._effective_config)
            merged = copy.deepcopy(old_config)
            merge_fn(merged, new_config)
            self._bump_effective_config_gen()
            applied_owner_token = self._record_effective_config_locked(
                merged,
                uncertain=had_uncertain,
            )
        self._flush_instance_runtime_config()
        return (
            "merged",
            True,
            had_uncertain,
            old_config,
            merged,
            applied_owner_token,
            0,
            copy.deepcopy(old_config),
        )

    def _apply_hydrated_merge_for_host(
        self,
        *,
        base_config: Mapping[str, Any] | None,
        new_config: Mapping[str, Any],
        merge_fn: Any,
        expected_gen: int,
        fallback_uncertain: bool,
        pre_runtime_snapshot: Dict[str, Any] | None,
    ) -> tuple[str, bool, bool, Dict[str, Any], Dict[str, Any] | None, int, int, Dict[str, Any] | None]:
        with self._get_config_state_lock():
            if bool(getattr(self, "_config_write_in_progress", False)):
                return (
                    "wait_write",
                    False,
                    fallback_uncertain,
                    {},
                    None,
                    0,
                    self._current_effective_config_gen(),
                    pre_runtime_snapshot,
                )
            current_gen = self._current_effective_config_gen()
            gen_changed = current_gen != expected_gen
            current_uncertain = bool(getattr(self, "_effective_config_uncertain", False))
            if isinstance(self._effective_config, dict) and not current_uncertain:
                old_config = copy.deepcopy(self._effective_config)
                merged = copy.deepcopy(old_config)
                merge_fn(merged, new_config)
                had_cached_for_rollback = True
                had_uncertain_for_rollback = False
                applied_uncertain = False
                rollback_runtime_snapshot: Dict[str, Any] = copy.deepcopy(old_config)
            else:
                if gen_changed:
                    return (
                        "retry_hydrate",
                        False,
                        current_uncertain,
                        {},
                        None,
                        0,
                        current_gen,
                        pre_runtime_snapshot,
                    )
                if not isinstance(base_config, Mapping):
                    return (
                        "hydrate_failed",
                        False,
                        current_uncertain,
                        {},
                        None,
                        0,
                        current_gen,
                        pre_runtime_snapshot,
                    )
                old_config = copy.deepcopy(dict(base_config))
                merged = copy.deepcopy(old_config)
                merge_fn(merged, new_config)
                had_cached_for_rollback = False
                had_uncertain_for_rollback = fallback_uncertain
                applied_uncertain = False
                rollback_runtime_snapshot = (
                    copy.deepcopy(old_config)
                    if old_config
                    else (
                        copy.deepcopy(pre_runtime_snapshot)
                        if isinstance(pre_runtime_snapshot, dict)
                        else {}
                    )
                )
            self._bump_effective_config_gen()
            applied_owner_token = self._record_effective_config_locked(
                merged,
                uncertain=applied_uncertain,
            )
        self._flush_instance_runtime_config()
        return (
            "merged",
            had_cached_for_rollback,
            had_uncertain_for_rollback,
            old_config,
            merged,
            applied_owner_token,
            0,
            rollback_runtime_snapshot,
        )

    def _rollback_effective_config_cache_for_host(
        self,
        *,
        expected_owner_token: int,
        had_cached: bool,
        old_config: object,
        uncertain: bool,
        fallback_notify_config: object = None,
    ) -> bool:
        should_flush = False
        with self._get_config_state_lock():
            current_owner = int(getattr(self, "_effective_config_owner_token", 0) or 0)
            if current_owner != expected_owner_token:
                if (
                    bool(getattr(self, "_config_write_in_progress", False))
                    and getattr(self, "_in_flight_write_base_owner_token", None)
                    == expected_owner_token
                ):
                    self._rejected_host_rollback = (
                        expected_owner_token,
                        had_cached,
                        copy.deepcopy(old_config) if isinstance(old_config, dict) else None,
                        uncertain,
                        (
                            copy.deepcopy(fallback_notify_config)
                            if isinstance(fallback_notify_config, dict)
                            else None
                        ),
                    )
                return False
            self._rejected_host_rollback = None
            self._bump_effective_config_gen()
            if had_cached and isinstance(old_config, dict):
                self._record_effective_config_locked(
                    copy.deepcopy(old_config),
                    uncertain=uncertain,
                )
                should_flush = True
            else:
                self._record_effective_config_locked(None, uncertain=uncertain)
                if isinstance(fallback_notify_config, dict):
                    seq = int(getattr(self, "_effective_config_publish_seq", 0) or 0)
                    self._effective_config_fallback_notify = (
                        seq,
                        copy.deepcopy(fallback_notify_config),
                    )
                    should_flush = True
        if should_flush:
            self._flush_instance_runtime_config()
        return True

    def _begin_host_config_change(self) -> None:
        with self._get_config_state_lock():
            self._host_config_change_pending = (
                int(getattr(self, "_host_config_change_pending", 0) or 0) + 1
            )

    def _end_host_config_change(self) -> None:
        with self._get_config_state_lock():
            current = int(getattr(self, "_host_config_change_pending", 0) or 0)
            self._host_config_change_pending = max(0, current - 1)

    def _cache_effective_config_from_read(
        self,
        config_obj: object,
        *,
        gen_before: int,
        read_id: int,
    ) -> bool:
        if not isinstance(config_obj, dict):
            return False
        with self._get_config_state_lock():
            if self._current_effective_config_gen() != gen_before:
                return False
            if bool(getattr(self, "_config_write_in_progress", False)):
                return False
            if int(getattr(self, "_host_config_change_pending", 0) or 0) > 0:
                return False
            if read_id <= int(getattr(self, "_last_installed_read_id", 0) or 0):
                return False
            self._last_installed_read_id = read_id
            config_copy = copy.deepcopy(config_obj)
            self._record_effective_config_locked(config_copy, uncertain=False)
        self._flush_instance_runtime_config()
        return True

    @staticmethod
    def _merge_config_copy(base: object, updates: Dict[str, Any]) -> Dict[str, Any]:
        merged = copy.deepcopy(base) if isinstance(base, dict) else {}
        for key, value in updates.items():
            if not isinstance(key, str):
                continue
            if value == "__DELETE__":
                merged.pop(key, None)
                continue
            if isinstance(value, Mapping):
                value_mapping = {
                    nested_key: nested_value
                    for nested_key, nested_value in value.items()
                    if isinstance(nested_key, str)
                }
                if value_mapping.get("__replace__") is True:
                    merged[key] = {
                        nested_key: copy.deepcopy(nested_value)
                        for nested_key, nested_value in value_mapping.items()
                        if nested_key != "__replace__"
                    }
                    continue
                current = merged.get(key)
                if isinstance(current, Mapping):
                    current_mapping = {
                        nested_key: nested_value
                        for nested_key, nested_value in current.items()
                        if isinstance(nested_key, str)
                    }
                    merged[key] = (
                        PluginContext._merge_config_copy(current_mapping, value_mapping)
                        if value_mapping
                        else {}
                    )
                else:
                    merged[key] = copy.deepcopy(value_mapping)
            else:
                merged[key] = copy.deepcopy(value)
        return merged

    def _get_sync_call_in_handler_policy(self) -> str:
        """Get the synchronous-call policy, preferring the plugin's own setting and then the global setting.

        Valid values: \"warn\" / \"reject\". Any invalid value falls back to the global policy.
        """
        try:
            st = self.config_path.stat()
            cache_mtime = getattr(self, "_a1_policy_mtime", None)
            cache_value = getattr(self, "_a1_policy_value", None)
            if cache_mtime == st.st_mtime and isinstance(cache_value, str):
                return cache_value

            with self.config_path.open("rb") as f:
                conf = tomllib.load(f)
            policy = (
                conf.get("plugin", {})
                .get("safety", {})
                .get("sync_call_in_handler")
            )
            if policy not in ("warn", "reject"):
                policy = SYNC_CALL_IN_HANDLER_POLICY
            self._a1_policy_mtime = st.st_mtime
            self._a1_policy_value = policy
            return policy
        except Exception:
            return SYNC_CALL_IN_HANDLER_POLICY

    def _enforce_sync_call_policy(self, method_name: str) -> None:
        handler_ctx = _IN_HANDLER.get()
        if handler_ctx is None:
            return
        # If no event loop is running on the current thread, we are in a
        # thread-pool thread (e.g. asyncio.to_thread).  Sync IPC calls are
        # safe there — they only block that worker thread, not the loop.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        policy = self._get_sync_call_in_handler_policy()
        msg = (
            f"Sync call '{method_name}' invoked inside handler ({handler_ctx}). "
            "This may block the event loop and cause deadlocks/timeouts. "
            "Use the async variant or wrap in asyncio.to_thread()."
        )
        if policy == "reject":
            raise RuntimeError(msg)
        if PLUGIN_LOG_SYNC_CALL_WARNINGS:
            self.logger.warning(msg)

    @contextlib.contextmanager
    def _handler_scope(self, handler_ctx: str):
        token = _IN_HANDLER.set(handler_ctx)
        try:
            yield
        finally:
            _IN_HANDLER.reset(token)

    @contextlib.contextmanager
    def _run_scope(self, run_id: Optional[str]):
        token = _CURRENT_RUN_ID.set(run_id if isinstance(run_id, str) and run_id.strip() else None)
        try:
            yield
        finally:
            _CURRENT_RUN_ID.reset(token)

    @contextlib.contextmanager
    def _lanlan_scope(self, lanlan_name: Optional[str]):
        token = _CURRENT_LANLAN.set(lanlan_name if isinstance(lanlan_name, str) and lanlan_name else None)
        try:
            yield
        finally:
            _CURRENT_LANLAN.reset(token)

    @property
    def current_lanlan(self) -> Optional[str]:
        """Recipient of this invocation, never the last caller on another task."""
        return _CURRENT_LANLAN.get()

    @property
    def call_chain(self) -> "CallChainView":
        """Read-only view of how this invocation was reached; never None (see CallChainView)."""
        from plugin.sdk.shared.core.call_chain_view import current_call_chain

        return current_call_chain(self.plugin_id)

    @property
    def handler_ctx(self) -> Optional[str]:
        return _IN_HANDLER.get()

    @property
    def current_entry_id(self) -> Optional[str]:
        handler_ctx = self.handler_ctx
        if not isinstance(handler_ctx, str):
            return None
        prefix = "plugin_entry."
        if not handler_ctx.startswith(prefix):
            return None
        entry_id = handler_ctx[len(prefix):].strip()
        return entry_id or None

    def get_current_entry_meta(self) -> Optional[Any]:
        entry_id = self.current_entry_id
        if not isinstance(entry_id, str) or not entry_id:
            return None

        entry_meta_map = getattr(self, "_entry_meta_map", None)
        if isinstance(entry_meta_map, dict):
            entry_meta = entry_meta_map.get(entry_id)
            if entry_meta is not None:
                return entry_meta

        instance = getattr(self, "_instance", None)
        collect_entries = getattr(instance, "collect_entries", None)
        if callable(collect_entries):
            try:
                collected = collect_entries(wrap_with_hooks=True)
            except Exception:
                return None
            if isinstance(collected, dict):
                handler_obj = collected.get(entry_id)
                return getattr(handler_obj, "meta", None)
        return None

    @property
    def run_id(self) -> Optional[str]:
        return _CURRENT_RUN_ID.get()

    def require_run_id(self) -> str:
        rid = self.run_id
        if not isinstance(rid, str) or not rid.strip():
            raise RuntimeError("run_id is required (this entry may not be triggered via /runs)")
        return rid

    def _is_in_event_loop(self) -> bool:
        """Detect whether currently running inside an event loop.
        
        Returns:
            True if currently in an event loop; False if there is no running event loop
        """
        try:
            asyncio.get_running_loop()
            return True
        except RuntimeError:
            return False

    def _run_coro_sync(self, coro: Any, *, operation: str) -> Any:
        """Run a coroutine from sync context.

        This is a convenience wrapper (e.g. run_update_sync) and is intentionally
        strict: it refuses to run when an event loop is already running.

        NOTE: ``asyncio.run()`` creates a **new** Context, which does NOT inherit
        the calling thread's contextvars.  We capture a snapshot of the current
        context and re-apply it inside the new event loop via a thin wrapper
        coroutine.
        """

        self._enforce_sync_call_policy(operation)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            ctx_snapshot = contextvars.copy_context()

            async def _with_ctx():
                # Re-apply every contextvar from the snapshot into the
                # asyncio.run()-created context so that require_run_id() etc.
                # see the correct values.
                for var in ctx_snapshot:
                    try:
                        var.set(ctx_snapshot[var])
                    except Exception:
                        pass
                return await coro

            return asyncio.run(_with_ctx())
        raise RuntimeError(f"{operation}_sync cannot be used inside a running event loop; use 'await {operation}(...)' instead")

    def update_status(self, status: Dict[str, Any]) -> None:
        """
        Calls from subprocesses / plugins enqueue the raw status for the main process to normalize centrally.
        """
        try:
            payload = {
                "type": "STATUS_UPDATE",
                "plugin_id": self.plugin_id,
                "data": status,
                "time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            self.status_queue.put_nowait(payload)
            if PLUGIN_LOG_CTX_STATUS_UPDATE:
                self.logger.info(f"Plugin {self.plugin_id} status updated: {payload}")
        except (AttributeError, RuntimeError) as e:
            # Queue operation error
            self.logger.warning(f"Queue error updating status for plugin {self.plugin_id}: {e}")
        except Exception:
            # Other unexpected exception
            self.logger.exception(f"Unexpected error updating status for plugin {self.plugin_id}")

    # ==================== Unified Export Push ====================

    async def _export_push_async(
        self,
        *,
        export_type: str,
        run_id: Optional[str] = None,
        text: Optional[str] = None,
        json_data: Optional[Dict[str, Any]] = None,
        url: Optional[str] = None,
        binary_data: Optional[bytes] = None,
        binary_url: Optional[str] = None,
        mime: Optional[str] = None,
        description: Optional[str] = None,
        label: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        """Unified core implementation for all export types."""
        rid = run_id if isinstance(run_id, str) and run_id.strip() else self.require_run_id()
        request_data: Dict[str, Any] = {
            "run_id": rid,
            "export_type": export_type,
            "description": description,
            "metadata": metadata or {},
        }
        if label is not None:
            request_data["label"] = label

        if export_type == "text":
            request_data["text"] = text
        elif export_type == "json":
            request_data["json"] = json_data
        elif export_type == "url":
            request_data["url"] = url
        elif export_type == "binary_url":
            request_data["binary_url"] = binary_url
            request_data["mime"] = mime
        elif export_type == "binary":
            if not isinstance(binary_data, (bytes, bytearray)):
                raise TypeError("binary_data must be bytes")
            data = bytes(binary_data)
            limit = int(EXPORT_INLINE_BINARY_MAX_BYTES) if EXPORT_INLINE_BINARY_MAX_BYTES is not None else 0
            if limit > 0 and len(data) > limit:
                raise ValueError("binary_data too large")
            request_data["binary_base64"] = base64.b64encode(data).decode("ascii")
            request_data["mime"] = mime
        else:
            raise ValueError(f"unsupported export_type: {export_type}")

        return await self._send_request_and_wait_async(
            method_name=f"export_push_{export_type}",
            request_type="EXPORT_PUSH",
            request_data=request_data,
            timeout=float(timeout),
            wrap_result=True,
        )

    def export_push(
        self,
        *,
        export_type: str,
        run_id: Optional[str] = None,
        text: Optional[str] = None,
        json_data: Optional[Dict[str, Any]] = None,
        url: Optional[str] = None,
        binary_data: Optional[bytes] = None,
        binary_url: Optional[str] = None,
        mime: Optional[str] = None,
        description: Optional[str] = None,
        label: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ):
        """Unified export push (smart sync/async proxy)."""
        coro = self._export_push_async(
            export_type=export_type, run_id=run_id, text=text, json_data=json_data,
            url=url, binary_data=binary_data, binary_url=binary_url, mime=mime,
            description=description, label=label, metadata=metadata, timeout=timeout,
        )
        if self._is_in_event_loop():
            return coro
        return self._run_coro_sync(coro, operation=f"export_push_{export_type}")

    async def export_push_async(
        self,
        *,
        export_type: str,
        run_id: Optional[str] = None,
        text: Optional[str] = None,
        json_data: Optional[Dict[str, Any]] = None,
        url: Optional[str] = None,
        binary_data: Optional[bytes] = None,
        binary_url: Optional[str] = None,
        mime: Optional[str] = None,
        description: Optional[str] = None,
        label: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        """Unified export push (explicit async)."""
        return await self._export_push_async(
            export_type=export_type, run_id=run_id, text=text, json_data=json_data,
            url=url, binary_data=binary_data, binary_url=binary_url, mime=mime,
            description=description, label=label, metadata=metadata, timeout=timeout,
        )

    # ==================== Convenience export wrappers ====================

    async def _export_push_text_async(self, *, run_id: Optional[str] = None, text: str, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        return await self._export_push_async(export_type="text", run_id=run_id, text=text, description=description, metadata=metadata, timeout=timeout)

    def export_push_text(self, *, run_id: Optional[str] = None, text: str, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, timeout: float = 5.0):
        """Push text export (smart sync/async proxy)."""
        return self.export_push(export_type="text", run_id=run_id, text=text, description=description, metadata=metadata, timeout=timeout)

    async def export_push_text_async(self, *, run_id: Optional[str] = None, text: str, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        return await self._export_push_text_async(run_id=run_id, text=text, description=description, metadata=metadata, timeout=timeout)

    def export_push_text_sync(self, *, run_id: Optional[str] = None, text: str, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        return self._run_coro_sync(self._export_push_text_async(run_id=run_id, text=text, description=description, metadata=metadata, timeout=timeout), operation="export_push_text")

    async def _export_push_binary_async(self, *, run_id: Optional[str] = None, binary_data: bytes, mime: Optional[str] = None, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        return await self._export_push_async(export_type="binary", run_id=run_id, binary_data=binary_data, mime=mime, description=description, metadata=metadata, timeout=timeout)

    def export_push_binary(self, *, run_id: Optional[str] = None, binary_data: bytes, mime: Optional[str] = None, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, timeout: float = 5.0):
        """Push binary export (smart sync/async proxy)."""
        return self.export_push(export_type="binary", run_id=run_id, binary_data=binary_data, mime=mime, description=description, metadata=metadata, timeout=timeout)

    async def export_push_binary_async(self, *, run_id: Optional[str] = None, binary_data: bytes, mime: Optional[str] = None, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        return await self._export_push_binary_async(run_id=run_id, binary_data=binary_data, mime=mime, description=description, metadata=metadata, timeout=timeout)

    def export_push_binary_sync(self, *, run_id: Optional[str] = None, binary_data: bytes, mime: Optional[str] = None, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        return self._run_coro_sync(self._export_push_binary_async(run_id=run_id, binary_data=binary_data, mime=mime, description=description, metadata=metadata, timeout=timeout), operation="export_push_binary")

    async def _export_push_url_async(self, *, run_id: Optional[str] = None, url: str, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        return await self._export_push_async(export_type="url", run_id=run_id, url=url, description=description, metadata=metadata, timeout=timeout)

    def export_push_url(self, *, run_id: Optional[str] = None, url: str, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, timeout: float = 5.0):
        """Push URL export (smart sync/async proxy)."""
        return self.export_push(export_type="url", run_id=run_id, url=url, description=description, metadata=metadata, timeout=timeout)

    async def export_push_url_async(self, *, run_id: Optional[str] = None, url: str, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        return await self._export_push_url_async(run_id=run_id, url=url, description=description, metadata=metadata, timeout=timeout)

    def export_push_url_sync(self, *, run_id: Optional[str] = None, url: str, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        return self._run_coro_sync(self._export_push_url_async(run_id=run_id, url=url, description=description, metadata=metadata, timeout=timeout), operation="export_push_url")

    async def _export_push_binary_url_async(self, *, run_id: Optional[str] = None, binary_url: str, mime: Optional[str] = None, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        return await self._export_push_async(export_type="binary_url", run_id=run_id, binary_url=binary_url, mime=mime, description=description, metadata=metadata, timeout=timeout)

    def export_push_binary_url(self, *, run_id: Optional[str] = None, binary_url: str, mime: Optional[str] = None, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, timeout: float = 5.0):
        """Push binary URL export (smart sync/async proxy)."""
        return self.export_push(export_type="binary_url", run_id=run_id, binary_url=binary_url, mime=mime, description=description, metadata=metadata, timeout=timeout)

    async def export_push_binary_url_async(self, *, run_id: Optional[str] = None, binary_url: str, mime: Optional[str] = None, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        return await self._export_push_binary_url_async(run_id=run_id, binary_url=binary_url, mime=mime, description=description, metadata=metadata, timeout=timeout)

    def export_push_binary_url_sync(self, *, run_id: Optional[str] = None, binary_url: str, mime: Optional[str] = None, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        return self._run_coro_sync(self._export_push_binary_url_async(run_id=run_id, binary_url=binary_url, mime=mime, description=description, metadata=metadata, timeout=timeout), operation="export_push_binary_url")

    async def _run_update_async(
        self,
        *,
        run_id: Optional[str] = None,
        progress: Optional[float] = None,
        stage: Optional[str] = None,
        message: Optional[str] = None,
        step: Optional[int] = None,
        step_total: Optional[int] = None,
        eta_seconds: Optional[float] = None,
        metrics: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        rid = run_id if isinstance(run_id, str) and run_id.strip() else self.require_run_id()
        data: Dict[str, Any] = {
            "run_id": rid,
        }
        if progress is not None:
            data["progress"] = progress
        if stage is not None:
            data["stage"] = stage
        if message is not None:
            data["message"] = message
        if step is not None:
            data["step"] = step
        if step_total is not None:
            data["step_total"] = step_total
        if eta_seconds is not None:
            data["eta_seconds"] = eta_seconds
        if metrics is not None:
            data["metrics"] = metrics

        return await self._send_request_and_wait_async(
            method_name="run_update",
            request_type="RUN_UPDATE",
            request_data=data,
            timeout=float(timeout),
            wrap_result=True,
        )

    def run_update(
        self,
        *,
        run_id: Optional[str] = None,
        progress: Optional[float] = None,
        stage: Optional[str] = None,
        message: Optional[str] = None,
        step: Optional[int] = None,
        step_total: Optional[int] = None,
        eta_seconds: Optional[float] = None,
        metrics: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ):
        """Smart agent: detect the execution environment and choose synchronous or asynchronous execution automatically."""
        coro = self._run_update_async(
            run_id=run_id,
            progress=progress,
            stage=stage,
            message=message,
            step=step,
            step_total=step_total,
            eta_seconds=eta_seconds,
            metrics=metrics,
            timeout=timeout,
        )
        if self._is_in_event_loop():
            return coro
        return self._run_coro_sync(coro, operation="run_update")

    async def run_update_async(
        self,
        *,
        run_id: Optional[str] = None,
        progress: Optional[float] = None,
        stage: Optional[str] = None,
        message: Optional[str] = None,
        step: Optional[int] = None,
        step_total: Optional[int] = None,
        eta_seconds: Optional[float] = None,
        metrics: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        return await self._run_update_async(
            run_id=run_id,
            progress=progress,
            stage=stage,
            message=message,
            step=step,
            step_total=step_total,
            eta_seconds=eta_seconds,
            metrics=metrics,
            timeout=timeout,
        )

    def run_update_sync(
        self,
        *,
        run_id: Optional[str] = None,
        progress: Optional[float] = None,
        stage: Optional[str] = None,
        message: Optional[str] = None,
        step: Optional[int] = None,
        step_total: Optional[int] = None,
        eta_seconds: Optional[float] = None,
        metrics: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        return self._run_coro_sync(
            self._run_update_async(
                run_id=run_id,
                progress=progress,
                stage=stage,
                message=message,
                step=step,
                step_total=step_total,
                eta_seconds=eta_seconds,
                metrics=metrics,
                timeout=timeout,
            ),
            operation="run_update",
        )

    async def _run_progress_async(
        self,
        *,
        run_id: Optional[str] = None,
        progress: float,
        stage: Optional[str] = None,
        message: Optional[str] = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        return await self._run_update_async(
            run_id=run_id,
            progress=float(progress),
            stage=stage,
            message=message,
            timeout=float(timeout),
        )

    def run_progress(
        self,
        *,
        run_id: Optional[str] = None,
        progress: float,
        stage: Optional[str] = None,
        message: Optional[str] = None,
        timeout: float = 5.0,
    ):
        """Smart agent: detect the execution environment and choose synchronous or asynchronous execution automatically."""
        coro = self._run_progress_async(
            run_id=run_id, progress=progress, stage=stage, message=message, timeout=timeout
        )
        if self._is_in_event_loop():
            return coro
        return self._run_coro_sync(coro, operation="run_progress")

    async def run_progress_async(
        self,
        *,
        run_id: Optional[str] = None,
        progress: float = 0.0,
        stage: Optional[str] = None,
        message: Optional[str] = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        return await self._run_progress_async(run_id=run_id, progress=progress, stage=stage, message=message, timeout=timeout)

    def run_progress_sync(
        self,
        *,
        run_id: Optional[str] = None,
        progress: float = 0.0,
        stage: Optional[str] = None,
        message: Optional[str] = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        return self._run_coro_sync(
            self._run_progress_async(run_id=run_id, progress=progress, stage=stage, message=message, timeout=timeout),
            operation="run_progress",
        )

    def push_message(
        self,
        source: str = "",
        message_type: Optional[str] = None,
        description: Optional[str] = None,
        priority: int = 0,
        content: Optional[str] = None,
        binary_data: Optional[bytes] = None,
        binary_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        unsafe: bool = False,
        fast_mode: bool = False,
        target_lanlan: Optional[str] = None,
        *,
        # ── v2 schema (preferred; see push_message_schema.py) ─────────
        visibility: Optional[list] = None,
        ai_behavior: Optional[str] = None,
        parts: Optional[list] = None,
        # Optional proactive-delivery coalescing key (keyword-only, OPT-IN).
        # Queued proactive cues sharing the SAME key collapse to the newest;
        # unset = never coalesce. Use distinct keys per cue category.
        coalesce_key: Optional[str] = None,
        # ── v1 legacy aliases — emit DeprecationWarning on use ────────
        mime: Optional[str] = None,
        delivery: Any = None,
        reply: Optional[bool] = None,
    ) -> "PushMessageResult":
        """Push a message from a plugin to the host.

        The v2 (canonical) parameters are ``visibility`` (list of
        ``"chat"`` / ``"hud"`` channels where the user sees the parts
        verbatim), ``ai_behavior`` (one of ``"respond"`` / ``"read"`` /
        ``"blind"``), and ``parts`` (ordered list of content parts).
        See :mod:`plugin.sdk.shared.core.push_message_schema` for the full
        schema and example part shapes.

        All other parameters (``message_type``, ``content``, ``binary_data``,
        ``binary_url``, ``mime``, ``delivery``, ``reply``, ``description``,
        ``unsafe``, ``fast_mode``) are deprecated.  They still work for the
        deprecation window but emit ``DeprecationWarning`` and are scheduled
        for removal in v0.9 (see ``docs/changelog``).

        The returned ``submitted`` flag only reports whether the SDK's
        authoritative local submission path accepted responsibility for the
        payload.  It does not acknowledge host consumption, model generation,
        or playback.
        """
        from plugin.sdk.shared.core.push_message_schema import (
            translate_push_message,
        )

        canonical = translate_push_message(
            visibility=visibility,
            ai_behavior=ai_behavior,
            parts=parts,
            message_type=message_type,
            description=description,
            content=content,
            binary_data=binary_data,
            binary_url=binary_url,
            mime=mime,
            delivery=delivery,
            reply=reply,
            unsafe=unsafe if unsafe else None,
            fast_mode=fast_mode if fast_mode else None,
            source=source,
            metadata=metadata,
            target_lanlan=target_lanlan,
            priority=priority,
            coalesce_key=coalesce_key,
        )
        # Stamp target_lanlan into metadata too — proactive_bridge and
        # main_server's session router still read ``metadata.target_lanlan``
        # and we keep that contract through the deprecation window.
        canonical_metadata = dict(canonical.get("metadata") or {})
        if target_lanlan and "target_lanlan" not in canonical_metadata:
            canonical_metadata["target_lanlan"] = target_lanlan
        # Synthesize legacy fields for downstream readers that haven't
        # migrated to v2 yet (notably plugin/server/application/messages/
        # query_service.py).  These are derived, not authoritative — the
        # v2 fields (parts/visibility/ai_behavior) own the real meaning.
        legacy_message_type = (
            message_type
            if isinstance(message_type, str) and message_type
            else _synthesize_legacy_message_type(canonical)
        )
        legacy_content = content if isinstance(content, str) else _synthesize_legacy_content(canonical.get("parts") or [])
        legacy_binary_url: Optional[str] = binary_url if isinstance(binary_url, str) else None
        # The deprecated top-level ``binary_data`` reaches the wire ONLY when the
        # caller passed it next to an explicit ``parts=`` list.  That is the one
        # shape translate_push_message leaves untranslated, so those bytes ride
        # in no part and dropping them here would be silent data loss.  Every
        # other shape is a duplicate of what ``parts[].binary_base64`` already
        # carries: either translate_push_message built the part FROM
        # ``binary_data`` (``parts=None``), or the loop below used to decode the
        # part's base64 back into raw bytes purely to re-attach them.  Carrying
        # both put one image on the wire at ~2.34x its raw size, which is what
        # made a 100 KiB screenshot blow a 256 KiB cap; the base64 copy alone is
        # ~1.34x, so the cap now means roughly what it says.  query_service is
        # the only reader of the field and decodes the canonical part on demand
        # instead.
        legacy_binary_data: Optional[bytes] = (
            bytes(binary_data)
            if isinstance(binary_data, (bytes, bytearray)) and parts is not None
            else None
        )
        legacy_mime: Optional[str] = mime if isinstance(mime, str) else None
        if legacy_binary_url is None or legacy_mime is None:
            for part in canonical.get("parts") or []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") not in ("image", "audio", "video"):
                    continue
                if legacy_binary_url is None:
                    url_obj = part.get("url")
                    if isinstance(url_obj, str) and url_obj:
                        legacy_binary_url = url_obj
                if legacy_mime is None:
                    mime_obj = part.get("mime")
                    if isinstance(mime_obj, str) and mime_obj:
                        legacy_mime = mime_obj
                if legacy_binary_url is not None and legacy_mime is not None:
                    break
        # ``description`` has no role in v2 (no semantic consumer; only
        # surfaces as a human label in legacy log lines and the
        # query_service messages-bus response).  Synthesised here purely
        # so v1 readers don't see ``None`` during the deprecation window.
        # Sources, in priority order:
        #   1. explicit ``description=`` kwarg (legacy v1 callers)
        #   2. ``metadata["description"]`` (already-migrated v2 callers
        #      that moved the label there during the migration)
        #   3. empty string (native v2 callers that never set a label)
        # TODO(v0.9): drop this field, the kwarg, the metadata fallback,
        # and the query_service fallback together; new callers should put
        # any human label in ``metadata`` if they want it surfaced.
        if isinstance(description, str):
            legacy_description = description
        else:
            md_description = canonical_metadata.get("description")
            legacy_description = md_description if isinstance(md_description, str) else ""
        # Resolve the legacy delivery/reply pair from the v2 axes so v1
        # consumers that branch on these fields still see a coherent value
        # during the deprecation window.
        if canonical["ai_behavior"] == "respond":
            legacy_delivery = "proactive"
        elif canonical["ai_behavior"] == "read":
            legacy_delivery = "passive"
        else:
            legacy_delivery = "silent"
        legacy_reply = legacy_delivery != "silent"

        def _build_wire_payload(*, message_id: str, ts: Any) -> Dict[str, Any]:
            """Construct the authenticated message envelope.

            Legacy compatibility fields keep downstream readers stable while
            the canonical v2 schema crosses the dedicated message uplink.
            """
            return {
                "type": "MESSAGE_PUSH",
                "message_id": message_id,
                "plugin_id": self.plugin_id,
                "time": ts,
                # v2 schema (canonical):
                "schema": canonical["schema"],
                "source": canonical["source"],
                "priority": canonical["priority"],
                "coalesce_key": canonical.get("coalesce_key", ""),
                "visibility": canonical["visibility"],
                "ai_behavior": canonical["ai_behavior"],
                "parts": canonical["parts"],
                "metadata": canonical_metadata,
                "target_lanlan": canonical.get("target_lanlan"),
                # Legacy compat fields for downstream consumers that have not
                # migrated to v2 yet (query_service, _types/models.py, etc.).
                # All derived from the canonical v2 payload so a v2-only
                # caller still surfaces something meaningful here.
                "message_type": legacy_message_type,
                "content": legacy_content,
                "binary_data": legacy_binary_data,
                "binary_url": legacy_binary_url,
                "mime": legacy_mime,
                "description": legacy_description,
                "unsafe": bool(unsafe),
                "delivery": legacy_delivery,
                "reply": legacy_reply,
            }

        def _reject_if_payload_too_large(payload: Dict[str, Any]) -> Optional["PushMessageRejected"]:
            """Refuse a push the host's ingest server would discard whole.

            The host measures ``len(ormsgpack.packb(payload))`` of each delta
            item -- the payload dict, NOT the batch envelope around it -- against
            MESSAGE_PLANE_PAYLOAD_MAX_BYTES, and on overflow it records
            ``payload_too_big`` and drops the entire item, text parts included.
            That verdict lands in the host process, long after push_message() has
            already returned ``{"submitted": True}``, so the author's only trace
            is a throttled line in someone else's log.  Measuring the same
            expression here turns that into a synchronous verdict the caller can
            branch on.  Both processes import the constant from plugin.settings,
            so the CONSTANT cannot drift apart -- but the OBJECT being measured
            can, and does: the host normalizes the record before it writes the
            plane (``_forward_message`` fills ``message_id`` / rewrites a
            non-string ``time``, and ``fast_mode`` sends a float timestamp the
            host swaps for a 28-character ISO string). Measured, that is up to
            ~19 bytes on the fast path. ``_HOST_ENVELOPE_HEADROOM_BYTES`` is
            held back here so a push sized into that window is refused
            synchronously rather than accepted and then dropped at ingest.
            A test packs both shapes and asserts the real drift stays inside
            the headroom, so a future host-side field cannot reopen the window
            unnoticed.

            The check is deliberately skipped when the host is not validating
            (MESSAGE_PLANE_VALIDATE_PAYLOAD_BYTES off): rejecting locally what
            the host would happily accept would be the SDK inventing a limit of
            its own, and this function exists precisely to agree with the host.
            A pack failure is likewise not our verdict to make -- the host's
            own ``payload_pack_error`` path owns it, and swallowing the
            exception here keeps a msgpack quirk from turning into a push that
            never even reaches the transport.

            Returns the rejection dict to hand back to the caller, or ``None``
            when the push may proceed.
            """
            carriers = _inline_binary_carriers(
                canonical.get("parts"), legacy_binary_data
            )
            # Every payload is measured, not just the ones carrying inline
            # bytes. This used to skip out when ``carriers`` was empty, to keep
            # a second msgpack pack off the high-frequency text cue path -- but
            # the host measures the WHOLE envelope, so an oversized text or
            # metadata push was still dropped there as payload_too_big while
            # push_message() had already answered submitted=True. That is the
            # exact invisible non-delivery this guard exists to end, left open
            # for the cheapest possible payload to walk through (CodeRabbit).
            #
            # The cost that justified the skip does not survive measurement:
            # ormsgpack.packb on a typical text cue (248 B) is 0.19 us, and
            # 50 us on a 200 KB one. Paying a fifth of a microsecond per cue to
            # close a silent-loss hole is not a trade that needs thinking about.
            if ormsgpack is None:
                return None
            from plugin.settings import (
                MESSAGE_PLANE_PAYLOAD_MAX_BYTES,
                MESSAGE_PLANE_VALIDATE_PAYLOAD_BYTES,
            )

            if not bool(MESSAGE_PLANE_VALIDATE_PAYLOAD_BYTES):
                return None
            try:
                size = len(ormsgpack.packb(payload))
            except Exception:
                return None
            # Subtract the bytes added by host normalization, as described above. Clamp to >=1 so
            # setting the limit below this overhead does not make everything get rejected.
            limit = max(
                1,
                int(MESSAGE_PLANE_PAYLOAD_MAX_BYTES) - _HOST_ENVELOPE_HEADROOM_BYTES,
            )
            if size <= limit:
                return None
            totals = _inline_carrier_totals(carriers)
            labels = [label for label, _size in totals]
            dominant = totals[0][0] if totals else ""
            # How much of this overage can be explained by inline carriers? How much remains after removing all of them?
            #
            # Sorting only by totals would always blame the inline carrier, even when it is not the cause:
            # 600 KiB of metadata plus a 1-byte image still makes "image" dominant,
            # so the author would be told to use ctx.images.upload() — and the request would remain over the limit,
            # because the image never used meaningful space. Bad advice is worse than none; it makes the author think
            # the problem is already fixed.
            carrier_bytes = sum(size_b for _label, size_b in totals)
            non_inline = max(0, size - carrier_bytes)
            if totals and non_inline > limit:
                # Even after removing all inline data the request is still too large: text or metadata is the actual cause.
                remedy = (
                    "The inline payloads are not what blew this cap: even with "
                    f"all of them removed the push is about {non_inline}B, still "
                    f"over the {limit}B limit. The text parts or the metadata "
                    "are what to shrink here; offloading the attachments alone "
                    "will not get this push through."
                )
            elif not totals:
                # No inline carrier at all: the text parts or the metadata are
                # what spent the budget. The base64 explanation below would be
                # actively misleading here -- there is nothing base64-encoded to
                # blame, the payload is simply that big -- so this branch gets
                # its own wording and the log line drops the ratio arithmetic.
                remedy = (
                    "Nothing in this push travels inline, so the text parts or "
                    "the metadata are what spent the budget: shorten them, or "
                    "move the bulk into a file or a URL the host can fetch and "
                    "reference it from a shorter message."
                )
            elif dominant == "image":
                remedy = (
                    "Send large images as a URL part instead: "
                    "`part = await ctx.images.upload(data, mime=...)` returns a "
                    "push-ready image part that does not travel inline."
                )
            else:
                # There is no upload helper for audio/video today, so naming one
                # would send the author after an API that is not there. Say what
                # IS true: make the payload smaller, or host it and reference it.
                remedy = (
                    f"There is no upload helper for an inline {dominant} payload "
                    "today, so the options are to shrink the payload itself "
                    "(shorter clip, lower bitrate or resolution) or to host it "
                    "and push the same part with `url=` instead of `data=`."
                )
                if "image" in labels:
                    remedy += (
                        " The image part in this push can also be offloaded with "
                        "`part = await ctx.images.upload(data, mime=...)`."
                    )
            try:
                # The author needs four things to act: how far over they are,
                # what the ceiling is, WHICH payload spent the budget, and why
                # their "300 KiB screenshot" blew a 512 KiB cap. The last one is
                # the non-obvious part -- inline bytes travel base64, 4/3 of the
                # raw size -- and without it the arithmetic looks broken and the
                # fix looks arbitrary. The per-carrier breakdown is what keeps
                # the remedy honest when a push carries more than one inline
                # part: the advice follows the payload that spent the budget.
                if totals:
                    self.logger.error(
                        "[PluginContext] push_message rejected: reason=payload_too_large "
                        "plugin_id={} size={}B limit={}B inline={}. Inline bytes travel "
                        "base64-encoded in parts[].binary_base64, about {}x their raw "
                        "size, so the effective raw-bytes ceiling for one inline payload "
                        "is about {}B. {}",
                        self.plugin_id,
                        int(size),
                        limit,
                        " ".join(f"{label}={size_b}B" for label, size_b in totals),
                        f"{_INLINE_BASE64_WIRE_RATIO:.2f}",
                        int(limit / _INLINE_BASE64_WIRE_RATIO),
                        remedy,
                    )
                else:
                    # No base64 arithmetic here: quoting a ratio and an
                    # "effective raw-bytes ceiling" for a push that carries no
                    # inline bytes would send the author hunting for an
                    # attachment that does not exist. The size and the limit are
                    # the whole story.
                    self.logger.error(
                        "[PluginContext] push_message rejected: reason=payload_too_large "
                        "plugin_id={} size={}B limit={}B inline=none. {}",
                        self.plugin_id,
                        int(size),
                        limit,
                        remedy,
                    )
            except Exception:
                # Diagnostic only. A logging failure (rotation race, bad
                # formatting arg) must not convert a clean local rejection into
                # an exception the plugin author never asked for.
                pass
            return {
                "ok": False,
                "submitted": False,
                "reason": "payload_too_large",
            }

        # Plugin-originated messages cross the authenticated per-host uplink.
        # The host binds plugin_id to that transport before any shared-state
        # write, so plugin code cannot self-assert another plugin's identity by
        # opening the public message-plane ingest socket directly.
        if self.message_queue is not None:
            try:
                use_fast_uplink = bool(fast_mode)
                authenticated_payload = _build_wire_payload(
                    message_id=str(uuid.uuid4()),
                    ts=(
                        time.time()
                        if use_fast_uplink
                        else datetime.now(timezone.utc).isoformat().replace(
                            "+00:00",
                            "Z",
                        )
                    ),
                )
                # Before the send, so the rejection is authoritative rather
                # than a report about a payload already on its way to be
                # discarded. #2999 put this probe on all three exits that
                # existed then; the authenticated uplink is now the only one,
                # and the cap is a property of the payload rather than of the
                # transport carrying it, so the one exit still gets it.
                oversized = _reject_if_payload_too_large(authenticated_payload)
                if oversized is not None:
                    return oversized
                # After the size check, so the local verdict is unchanged; the
                # host removes it before the record is stored or published.
                _attach_parent_request_id(authenticated_payload)
                fast_put = getattr(
                    self.message_queue,
                    "put_fast_nowait",
                    None,
                )
                if use_fast_uplink and callable(fast_put):
                    fast_put(authenticated_payload)
                else:
                    self.message_queue.put_nowait(authenticated_payload)
                return {"submitted": True}
            except Exception as e:
                try:
                    self.logger.warning(
                        "[PluginContext] authenticated message uplink failed ({})",
                        type(e).__name__,
                    )
                except Exception:
                    pass
                return {
                    "ok": False,
                    "submitted": False,
                    "reason": (
                        "backpressure"
                        if _is_submission_backpressure(e)
                        else "transport_error"
                    ),
                }

        try:
            self.logger.error(
                "[PluginContext] push_message failed: authenticated message "
                "uplink unavailable"
            )
        except Exception:
            pass
        return {
            "ok": False,
            "submitted": False,
            "reason": "transport_unavailable",
        }

    async def create_card(self, *, html: str, summary: str, css: str = "",
                          actions: Optional[Dict[str, Any]] = None,
                          target_lanlan: Optional[str] = None):
        """Create a display-only HTML card; awaiting confirms local submission."""
        from plugin.sdk.shared.core.cards import create_card
        return await create_card(self, html=html, summary=summary, css=css,
                                 actions=actions, target_lanlan=target_lanlan or self.current_lanlan)

    def get_card(self, card_id: str, *, target_lanlan: Optional[str] = None):
        """Recover an online card handle from a UI action's _ctx."""
        from plugin.sdk.shared.core.cards import ChatCard
        return ChatCard(self, card_id, target_lanlan or self.current_lanlan)

    async def create_view(self, *, title: str, html: str, css: str = "",
                          actions: Optional[Dict[str, Any]] = None,
                          summary: Optional[str] = None,
                          target_lanlan: Optional[str] = None):
        """Create online AgentHUD content; awaiting confirms local submission."""
        from plugin.sdk.shared.core.cards import create_view
        return await create_view(self, title=title, html=html, css=css,
                                 actions=actions, summary=summary,
                                 target_lanlan=target_lanlan or self.current_lanlan)

    def get_view(self, view_id: str, *, target_lanlan: Optional[str] = None):
        """Recover an AgentHUD view handle from a UI action's _ctx."""
        from plugin.sdk.shared.core.cards import PluginView
        return PluginView(self, view_id, target_lanlan or self.current_lanlan)

    async def push_message_async(self, *args: Any, **kwargs: Any) -> "PushMessageResult":
        """Async push_message wrapper, using asyncio.to_thread around the synchronous call.

        Note: the underlying ZMQ socket is synchronous; this method uses a thread pool to avoid blocking. See the new signature in
        :meth:`push_message`. This method only forwards parameters; it does not perform compatibility translation.
        """
        return await asyncio.to_thread(self.push_message, *args, **kwargs)

    def _send_request_and_wait(self, *, method_name: str, request_type: str,
                               request_data: Dict[str, Any], timeout: float, wrap_result: bool = True) -> Any:
        self._enforce_sync_call_policy(method_name)
        if self._is_in_event_loop():
            raise RuntimeError(f"Sync call '{method_name}' cannot be used inside a running event loop")
        return asyncio.run(self._send_request_and_wait_async(
            method_name=method_name, request_type=request_type, request_data=request_data,
            timeout=timeout, wrap_result=wrap_result,
        ))

    async def _send_request_and_wait_async(self, *, method_name: str, request_type: str,
                                            request_data: Dict[str, Any], timeout: float, wrap_result: bool = True) -> Any:
        payload = dict(request_data)
        for key in ("type", "from_plugin", "request_id", "timeout"):
            payload.pop(key, None)
        result = await self._get_rpc_peer().request(request_type, payload, timeout=timeout)
        return (result if isinstance(result, dict) else {"result": result}) if wrap_result else result
    
    def trigger_plugin_event_sync(
        self,
        target_plugin_id: str,
        event_type: str,
        event_id: str,
        params: Dict[str, Any],
        timeout: float = 10.0
    ) -> Dict[str, Any]:
        """Synchronous version: trigger a custom event in another plugin (inter-plugin communication)
        
        Args:
            target_plugin_id: Target plugin ID
            event_type: Custom event type
            event_id: Event ID
            params: Parameter dictionary
            timeout: Timeout in seconds
            
        Returns:
            Return value from the event handler
        """
        try:
            return self._send_request_and_wait(
                method_name="trigger_plugin_event",
                request_type="PLUGIN_TO_PLUGIN",
                request_data={
                    "to_plugin": target_plugin_id,
                    "event_type": event_type,
                    "event_id": event_id,
                    "args": params,  # The internal protocol still uses args
                },
                timeout=timeout,
                wrap_result=False,
            )
        except TimeoutError as e:
            raise TimeoutError(
                f"Plugin {target_plugin_id} event {event_type}.{event_id} timed out after {timeout}s"
            ) from e
    
    async def trigger_plugin_event_async(
        self,
        target_plugin_id: str,
        event_type: str,
        event_id: str,
        params: Dict[str, Any],
        timeout: float = 10.0
    ) -> Dict[str, Any]:
        """Asynchronous version: trigger a custom event in another plugin (inter-plugin communication)"""
        try:
            return await self._send_request_and_wait_async(
                method_name="trigger_plugin_event",
                request_type="PLUGIN_TO_PLUGIN",
                request_data={
                    "to_plugin": target_plugin_id,
                    "event_type": event_type,
                    "event_id": event_id,
                    "args": params,  # The internal protocol still uses args
                },
                timeout=timeout,
                wrap_result=False,
            )
        except TimeoutError as e:
            raise TimeoutError(
                f"Plugin {target_plugin_id} event {event_type}.{event_id} timed out after {timeout}s"
            ) from e
    
    def trigger_plugin_event(
        self,
        target_plugin_id: str,
        event_type: str,
        event_id: str,
        params: Dict[str, Any],
        timeout: float = 10.0
    ):
        """Adaptive version: detect the execution environment and choose synchronous or asynchronous execution.
        
        Returns:
            Return a coroutine in the event loop; otherwise return a result dictionary
        """
        if self._is_in_event_loop():
            return self.trigger_plugin_event_async(
                target_plugin_id=target_plugin_id,
                event_type=event_type,
                event_id=event_id,
                params=params,
                timeout=timeout,
            )
        return self.trigger_plugin_event_sync(
            target_plugin_id=target_plugin_id,
            event_type=event_type,
            event_id=event_id,
            params=params,
            timeout=timeout,
        )

    def query_plugins_sync(self, filters: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        """Synchronous version: query plugin list"""
        try:
            return self._send_request_and_wait(
                method_name="query_plugins",
                request_type="PLUGIN_QUERY",
                request_data={"filters": filters or {}},
                timeout=timeout,
                wrap_result=True,
            )
        except TimeoutError as e:
            raise TimeoutError(f"Plugin query timed out after {timeout}s") from e
    
    async def query_plugins_async(self, filters: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        """Asynchronous version: query plugin list"""
        try:
            return await self._send_request_and_wait_async(
                method_name="query_plugins",
                request_type="PLUGIN_QUERY",
                request_data={"filters": filters or {}},
                timeout=timeout,
                wrap_result=True,
            )
        except TimeoutError as e:
            raise TimeoutError(f"Plugin query timed out after {timeout}s") from e
    
    def query_plugins(self, filters: Optional[Dict[str, Any]] = None, timeout: float = 5.0):
        """Adaptive version: detect the execution environment and choose synchronous or asynchronous execution.
        
        Returns:
            Return a coroutine in the event loop; otherwise return a result dictionary
        """
        if self._is_in_event_loop():
            return self.query_plugins_async(filters=filters, timeout=timeout)
        return self.query_plugins_sync(filters=filters, timeout=timeout)

    async def _get_local_config_payload(
        self,
        *,
        payload_type: str,
        profile_name: Optional[str] = None,
        timeout: float = 5.0,
    ) -> Optional[Dict[str, Any]]:
        try:
            from plugin.server.application.config import ConfigQueryService

            service = ConfigQueryService()
            if payload_type == "config":
                cached_copy: Optional[Dict[str, Any]] = None
                with self._get_config_state_lock():
                    cached = getattr(self, "_effective_config", None)
                    uncertain = getattr(self, "_effective_config_uncertain", False)
                    if isinstance(cached, dict) and not uncertain:
                        cached_copy = copy.deepcopy(cached)
                if cached_copy is not None:
                    return {
                        "plugin_id": self.plugin_id,
                        "config": cached_copy,
                        "config_path": str(self.config_path),
                    }
                gen_before, read_id = self._begin_config_read()
                payload = await asyncio.wait_for(
                    service.get_plugin_config(plugin_id=self.plugin_id),
                    timeout=timeout,
                )
                config_obj = payload.get("config")
                self._cache_effective_config_from_read(
                    config_obj,
                    gen_before=gen_before,
                    read_id=read_id,
                )
                return payload
            if payload_type == "base":
                return await asyncio.wait_for(
                    service.get_plugin_base_config(plugin_id=self.plugin_id),
                    timeout=timeout,
                )
            if payload_type == "profiles":
                return await asyncio.wait_for(
                    service.get_plugin_profiles_state(plugin_id=self.plugin_id),
                    timeout=timeout,
                )
            if payload_type == "profile":
                if not isinstance(profile_name, str) or not profile_name.strip():
                    return None
                return await asyncio.wait_for(
                    service.get_plugin_profile_config(
                        plugin_id=self.plugin_id,
                        profile_name=profile_name.strip(),
                    ),
                    timeout=timeout,
                )
            if payload_type == "effective":
                return await asyncio.wait_for(
                    service.get_plugin_effective_config(
                        plugin_id=self.plugin_id,
                        profile_name=profile_name.strip() if isinstance(profile_name, str) and profile_name.strip() else None,
                    ),
                    timeout=timeout,
                )
        except TimeoutError:
            raise
        except Exception as exc:
            try:
                self.logger.debug(
                    "[PluginContext] local config read fallback failed: plugin_id={}, payload_type={}, err_type={}, err={}",
                    self.plugin_id,
                    payload_type,
                    type(exc).__name__,
                    str(exc),
                )
            except Exception:
                pass
        return None

    async def get_own_config(self, timeout: float = 5.0) -> Dict[str, Any]:
        request_timeout = self._normalize_config_timeout(timeout)
        request_deadline = time.monotonic() + request_timeout
        if request_timeout <= 0.0:
            raise TimeoutError(f"Plugin config get timed out after {timeout}s")
        try:
            local_payload = await self._get_local_config_payload(
                payload_type="config",
                timeout=request_timeout,
            )
        except TimeoutError as e:
            raise TimeoutError(f"Plugin config get timed out after {timeout}s") from e
        if isinstance(local_payload, dict):
            return local_payload
        remaining_timeout = request_deadline - time.monotonic()
        if remaining_timeout <= 0.0:
            raise TimeoutError(f"Plugin config get timed out after {timeout}s")
        gen_before, read_id = self._begin_config_read()
        try:
            payload = await self._send_request_and_wait_async(
                method_name="get_own_config",
                request_type="PLUGIN_CONFIG_GET",
                request_data={
                    "plugin_id": self.plugin_id,
                    "_request_deadline_monotonic": request_deadline,
                },
                timeout=remaining_timeout,
                wrap_result=True,
            )
            config_obj = payload.get("config") if isinstance(payload, dict) else None
            self._cache_effective_config_from_read(
                config_obj,
                gen_before=gen_before,
                read_id=read_id,
            )
            return payload
        except TimeoutError as e:
            raise TimeoutError(f"Plugin config get timed out after {timeout}s") from e

    async def get_own_base_config(self, timeout: float = 5.0) -> Dict[str, Any]:
        try:
            local_payload = await self._get_local_config_payload(payload_type="base", timeout=timeout)
            if isinstance(local_payload, dict):
                return local_payload
            return await self._send_request_and_wait_async(
                method_name="get_own_base_config",
                request_type="PLUGIN_CONFIG_BASE_GET",
                request_data={"plugin_id": self.plugin_id},
                timeout=timeout,
                wrap_result=True,
            )
        except TimeoutError as e:
            raise TimeoutError(f"Plugin base config get timed out after {timeout}s") from e

    async def get_own_profiles_state(self, timeout: float = 5.0) -> Dict[str, Any]:
        try:
            local_payload = await self._get_local_config_payload(payload_type="profiles", timeout=timeout)
            if isinstance(local_payload, dict):
                return local_payload
            return await self._send_request_and_wait_async(
                method_name="get_own_profiles_state",
                request_type="PLUGIN_CONFIG_PROFILES_GET",
                request_data={"plugin_id": self.plugin_id},
                timeout=timeout,
                wrap_result=True,
            )
        except TimeoutError as e:
            raise TimeoutError(f"Plugin profiles state get timed out after {timeout}s") from e

    async def get_own_profile_config(self, profile_name: str, timeout: float = 5.0) -> Dict[str, Any]:
        if not isinstance(profile_name, str) or not profile_name.strip():
            raise ValueError("profile_name must be a non-empty string")
        try:
            local_payload = await self._get_local_config_payload(
                payload_type="profile",
                profile_name=profile_name,
                timeout=timeout,
            )
            if isinstance(local_payload, dict):
                return local_payload
            return await self._send_request_and_wait_async(
                method_name="get_own_profile_config",
                request_type="PLUGIN_CONFIG_PROFILE_GET",
                request_data={
                    "plugin_id": self.plugin_id,
                    "profile_name": profile_name.strip(),
                },
                timeout=timeout,
                wrap_result=True,
            )
        except TimeoutError as e:
            raise TimeoutError(f"Plugin profile config get timed out after {timeout}s") from e

    async def get_own_effective_config(
        self,
        profile_name: Optional[str] = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        """Get effective config.

        - profile_name is None: returns active profile overlay (same as get_own_config).
        - profile_name is a string: returns base + that profile overlay.
        """

        request_data: Dict[str, Any] = {
            "plugin_id": self.plugin_id,
        }
        if isinstance(profile_name, str) and profile_name.strip():
            request_data["profile_name"] = profile_name.strip()

        gen_before, read_id = self._begin_config_read()
        try:
            local_payload = await self._get_local_config_payload(
                payload_type="effective",
                profile_name=profile_name,
                timeout=timeout,
            )
            if isinstance(local_payload, dict):
                effective_obj = local_payload.get("config")
                if profile_name is None:
                    self._cache_effective_config_from_read(
                        effective_obj,
                        gen_before=gen_before,
                        read_id=read_id,
                    )
                return local_payload

            return await self._send_request_and_wait_async(
                method_name="get_own_effective_config",
                request_type="PLUGIN_CONFIG_EFFECTIVE_GET",
                request_data=request_data,
                timeout=timeout,
                wrap_result=True,
            )
        except TimeoutError as e:
            raise TimeoutError(f"Plugin effective config get timed out after {timeout}s") from e

    async def get_system_config(self, timeout: float = 5.0) -> Dict[str, Any]:
        try:
            return await self._send_request_and_wait_async(
                method_name="get_system_config",
                request_type="PLUGIN_SYSTEM_CONFIG_GET",
                request_data={},
                timeout=timeout,
                wrap_result=True,
            )
        except TimeoutError as e:
            raise TimeoutError(f"System config get timed out after {timeout}s") from e

    def query_memory_sync(self, lanlan_name: str, query: str, timeout: float = 5.0) -> Dict[str, Any]:
        """Synchronous version: query memory data"""
        try:
            return self._send_request_and_wait(
                method_name="query_memory",
                request_type="MEMORY_QUERY",
                request_data={
                    "lanlan_name": lanlan_name,
                    "query": query,
                },
                timeout=timeout,
                wrap_result=True,
            )
        except TimeoutError as e:
            raise TimeoutError(f"Memory query timed out after {timeout}s") from e
    
    async def query_memory_async(self, lanlan_name: str, query: str, timeout: float = 5.0) -> Dict[str, Any]:
        """Asynchronous version: query memory data"""
        try:
            return await self._send_request_and_wait_async(
                method_name="query_memory",
                request_type="MEMORY_QUERY",
                request_data={
                    "lanlan_name": lanlan_name,
                    "query": query,
                },
                timeout=timeout,
                wrap_result=True,
            )
        except TimeoutError as e:
            raise TimeoutError(f"Memory query timed out after {timeout}s") from e
    
    def query_memory(self, lanlan_name: str, query: str, timeout: float = 5.0):
        """Adaptive version: detect the execution environment and choose synchronous or asynchronous execution.
        
        Returns:
            Return a coroutine in the event loop; otherwise return a result dictionary
        """
        if self._is_in_event_loop():
            return self.query_memory_async(lanlan_name=lanlan_name, query=query, timeout=timeout)
        return self.query_memory_sync(lanlan_name=lanlan_name, query=query, timeout=timeout)

    def _build_queued_update_timeout_payload(self) -> Dict[str, Any]:
        with self._get_config_state_lock():
            current_config = copy.deepcopy(getattr(self, "_effective_config", None))
        return {
            "success": False,
            "plugin_id": self.plugin_id,
            "config": current_config if isinstance(current_config, dict) else {},
            "requires_reload": False,
            "persisted": False,
            "message": "Config persistence timed out waiting for an in-flight config write; update was not applied",
        }

    def _commit_optimistic_write_result_if_current(
        self,
        *,
        write_gen: int,
        config_obj: Dict[str, Any],
        uncertain: bool,
    ) -> None:
        should_flush = False
        with self._get_config_state_lock():
            if self._current_effective_config_gen() == write_gen:
                self._bump_effective_config_gen()
                installed_copy = copy.deepcopy(config_obj)
                self._record_effective_config_locked(installed_copy, uncertain=uncertain)
                should_flush = True
            else:
                self._bump_effective_config_gen()
                self._effective_config_uncertain = True
                self._rejected_host_rollback = None
                self._allocate_owner_token_locked()
        if should_flush:
            self._flush_instance_runtime_config()

    def _rollback_optimistic_write_if_current(
        self,
        *,
        write_gen: int,
        old_effective_config: Any,
        old_effective_config_uncertain: bool,
        old_owner_token: int | None = None,
    ) -> None:
        should_flush = False
        with self._get_config_state_lock():
            if self._current_effective_config_gen() != write_gen:
                return
            pending_host_rb = getattr(self, "_rejected_host_rollback", None)
            if (
                old_owner_token is not None
                and isinstance(pending_host_rb, tuple)
                and pending_host_rb[0] == old_owner_token
            ):
                self._rejected_host_rollback = None
                _, rb_had_cached, rb_old_config, rb_uncertain, rb_fallback_notify = pending_host_rb
                self._bump_effective_config_gen()
                if rb_had_cached and isinstance(rb_old_config, dict):
                    self._record_effective_config_locked(
                        copy.deepcopy(rb_old_config),
                        uncertain=rb_uncertain,
                    )
                    should_flush = True
                else:
                    self._record_effective_config_locked(None, uncertain=rb_uncertain)
                    if isinstance(rb_fallback_notify, dict):
                        seq = int(getattr(self, "_effective_config_publish_seq", 0) or 0)
                        self._effective_config_fallback_notify = (
                            seq,
                            copy.deepcopy(rb_fallback_notify),
                        )
                        should_flush = True
            else:
                self._bump_effective_config_gen()
                if isinstance(old_effective_config, dict):
                    restored_copy = copy.deepcopy(old_effective_config)
                    self._record_effective_config_locked(
                        restored_copy,
                        uncertain=old_effective_config_uncertain,
                        owner_token=old_owner_token,
                    )
                    should_flush = True
                else:
                    self._record_effective_config_locked(
                        None,
                        uncertain=old_effective_config_uncertain,
                        owner_token=old_owner_token,
                    )
        if should_flush:
            self._flush_instance_runtime_config()

    async def update_own_config(self, updates: Dict[str, Any], timeout: float = 10.0) -> Dict[str, Any]:
        if not isinstance(updates, dict):
            raise TypeError("updates must be a dict")
        # Keep config writes from blocking plugin actions; timeouts fall back to the optimistic in-memory config.
        request_timeout = self._normalize_config_timeout(timeout, cap=4.5)
        request_deadline = time.monotonic() + request_timeout
        write_lock = self._get_config_write_lock()
        try:
            await write_lock.acquire(deadline=request_deadline)
        except TimeoutError:
            return self._build_queued_update_timeout_payload()
        try:
            remaining_timeout = request_deadline - time.monotonic()
            if remaining_timeout <= 0.0:
                return self._build_queued_update_timeout_payload()
            with self._get_config_state_lock():
                needs_baseline_hydration = not isinstance(
                    getattr(self, "_effective_config", None),
                    dict,
                )
            if needs_baseline_hydration:
                try:
                    await self._get_local_config_payload(
                        payload_type="config",
                        timeout=min(remaining_timeout, 0.5),
                    )
                except TimeoutError:
                    pass
                remaining_timeout = request_deadline - time.monotonic()
                if remaining_timeout <= 0.0:
                    return self._build_queued_update_timeout_payload()
            should_flush_optimistic = False
            with self._get_config_state_lock():
                old_effective_config = copy.deepcopy(getattr(self, "_effective_config", None))
                old_effective_config_uncertain = bool(
                    getattr(self, "_effective_config_uncertain", False)
                )
                old_owner_token = int(getattr(self, "_effective_config_owner_token", 0) or 0)
                has_known_baseline = isinstance(old_effective_config, dict)
                optimistic_config = self._merge_config_copy(old_effective_config, updates)
                write_gen = self._bump_effective_config_gen()
                self._in_flight_write_base_owner_token = old_owner_token
                if has_known_baseline:
                    optimistic_to_install = copy.deepcopy(optimistic_config)
                    self._record_effective_config_locked(optimistic_to_install, uncertain=False)
                    should_flush_optimistic = True
                else:
                    self._rejected_host_rollback = None
                    self._allocate_owner_token_locked()
                self._config_write_in_progress = True
            if should_flush_optimistic:
                self._flush_instance_runtime_config()
            try:
                payload = await self._send_request_and_wait_async(
                    method_name="update_own_config",
                    request_type="PLUGIN_CONFIG_UPDATE",
                    request_data={
                        "plugin_id": self.plugin_id,
                        "updates": updates,
                        "_request_deadline_monotonic": request_deadline,
                    },
                    timeout=remaining_timeout,
                    wrap_result=True,
                )
                config_obj = payload.get("config") if isinstance(payload, dict) else None
                if isinstance(config_obj, dict):
                    if payload.get("persisted") is False:
                        if has_known_baseline:
                            should_flush_in_memory = False
                            superseded_by_host_rollback = False
                            applied_config = copy.deepcopy(optimistic_config)
                            current_effective: Dict[str, Any] = {}
                            with self._get_config_state_lock():
                                if self._current_effective_config_gen() == write_gen:
                                    pending_host_rb = getattr(self, "_rejected_host_rollback", None)
                                    if (
                                        isinstance(pending_host_rb, tuple)
                                        and pending_host_rb[0] == old_owner_token
                                    ):
                                        self._rejected_host_rollback = None
                                        (
                                            _,
                                            rb_had_cached,
                                            rb_old_config,
                                            rb_uncertain,
                                            rb_fallback_notify,
                                        ) = pending_host_rb
                                        self._bump_effective_config_gen()
                                        if rb_had_cached and isinstance(rb_old_config, dict):
                                            applied_config = self._merge_config_copy(
                                                rb_old_config,
                                                updates,
                                            )
                                            self._record_effective_config_locked(
                                                copy.deepcopy(applied_config),
                                                uncertain=rb_uncertain,
                                            )
                                            should_flush_in_memory = True
                                        else:
                                            superseded_by_host_rollback = True
                                            self._record_effective_config_locked(
                                                None,
                                                uncertain=rb_uncertain,
                                            )
                                            if isinstance(rb_fallback_notify, dict):
                                                seq = int(
                                                    getattr(
                                                        self,
                                                        "_effective_config_publish_seq",
                                                        0,
                                                    )
                                                    or 0
                                                )
                                                self._effective_config_fallback_notify = (
                                                    seq,
                                                    copy.deepcopy(rb_fallback_notify),
                                                )
                                                should_flush_in_memory = True
                                    else:
                                        self._bump_effective_config_gen()
                                        installed_copy = copy.deepcopy(optimistic_config)
                                        self._record_effective_config_locked(
                                            installed_copy,
                                            uncertain=old_effective_config_uncertain,
                                        )
                                        should_flush_in_memory = True
                                elif isinstance(self._effective_config, dict):
                                    current_effective = copy.deepcopy(self._effective_config)
                            if should_flush_in_memory:
                                self._flush_instance_runtime_config()
                            if superseded_by_host_rollback:
                                payload = dict(payload)
                                payload["success"] = False
                                payload["persisted"] = False
                                payload["applied_in_memory"] = False
                                payload["config"] = {}
                                payload["message"] = (
                                    "Config persistence timed out and the baseline config was rolled back; "
                                    "update was not applied in plugin memory"
                                )
                            elif self._current_effective_config_gen() != write_gen and not should_flush_in_memory:
                                payload = dict(payload)
                                payload["success"] = False
                                payload["persisted"] = False
                                payload["applied_in_memory"] = False
                                payload["config"] = current_effective
                                payload["message"] = (
                                    "Config persistence timed out and a newer config snapshot superseded the draft; "
                                    "update was not applied in plugin memory"
                                )
                            else:
                                payload = dict(payload)
                                payload["config"] = copy.deepcopy(applied_config)
                        else:
                            with self._get_config_state_lock():
                                if self._current_effective_config_gen() == write_gen:
                                    self._bump_effective_config_gen()
                                    self._record_effective_config_locked(
                                        None,
                                        uncertain=old_effective_config_uncertain,
                                    )
                            payload = dict(payload)
                            payload["success"] = False
                            payload["persisted"] = False
                            payload["applied_in_memory"] = False
                            payload["config"] = {}
                            payload["message"] = (
                                "Config persistence timed out and no baseline config was available; "
                                "update was not applied in plugin memory"
                            )
                    else:
                        self._commit_optimistic_write_result_if_current(
                            write_gen=write_gen,
                            config_obj=config_obj,
                            uncertain=False,
                        )
                return payload
            except TimeoutError:
                with self._get_config_state_lock():
                    self._bump_effective_config_gen()
                    self._effective_config_uncertain = True
                    self._rejected_host_rollback = None
                    self._allocate_owner_token_locked()
                return {
                    "success": False,
                    "plugin_id": self.plugin_id,
                    "config": copy.deepcopy(optimistic_config) if has_known_baseline else {},
                    "requires_reload": False,
                    "persisted": None,
                    "message": "Config persistence response timed out; final persistence status is unknown",
                }
            except asyncio.CancelledError:
                # The request may already have crossed the atomic commit point.
                # Keep the optimistic view instead of restoring a known-stale one.
                with self._get_config_state_lock():
                    self._bump_effective_config_gen()
                    self._effective_config_uncertain = True
                    self._rejected_host_rollback = None
                    self._allocate_owner_token_locked()
                raise
            except Exception:
                self._rollback_optimistic_write_if_current(
                    write_gen=write_gen,
                    old_effective_config=old_effective_config,
                    old_effective_config_uncertain=old_effective_config_uncertain,
                    old_owner_token=old_owner_token,
                )
                raise
            finally:
                with self._get_config_state_lock():
                    self._config_write_in_progress = False
                    self._in_flight_write_base_owner_token = None
                    self._rejected_host_rollback = None
        finally:
            write_lock.release()

    async def replace_own_config(self, config: Dict[str, Any], timeout: float = 10.0) -> Dict[str, Any]:
        if not isinstance(config, dict):
            raise TypeError("config must be a dict")
        request_timeout = self._normalize_config_timeout(timeout)
        request_deadline = time.monotonic() + request_timeout
        write_lock = self._get_config_write_lock()
        try:
            await write_lock.acquire(deadline=request_deadline)
        except TimeoutError as e:
            raise TimeoutError(f"Plugin config replace timed out after {timeout}s") from e
        try:
            remaining_timeout = request_deadline - time.monotonic()
            if remaining_timeout <= 0.0:
                raise TimeoutError(f"Plugin config replace timed out after {timeout}s")
            should_flush_optimistic = False
            with self._get_config_state_lock():
                old_effective_config = copy.deepcopy(getattr(self, "_effective_config", None))
                old_effective_config_uncertain = bool(
                    getattr(self, "_effective_config_uncertain", False)
                )
                old_owner_token = int(getattr(self, "_effective_config_owner_token", 0) or 0)
                has_known_baseline = isinstance(old_effective_config, dict)
                optimistic_config = copy.deepcopy(config)
                write_gen = self._bump_effective_config_gen()
                self._in_flight_write_base_owner_token = old_owner_token
                if has_known_baseline:
                    optimistic_to_install = copy.deepcopy(optimistic_config)
                    self._record_effective_config_locked(optimistic_to_install, uncertain=False)
                    should_flush_optimistic = True
                else:
                    self._rejected_host_rollback = None
                    self._allocate_owner_token_locked()
                self._config_write_in_progress = True
            if should_flush_optimistic:
                self._flush_instance_runtime_config()
            try:
                payload = await self._send_request_and_wait_async(
                    method_name="replace_own_config",
                    request_type="PLUGIN_CONFIG_REPLACE",
                    request_data={
                        "plugin_id": self.plugin_id,
                        "config": config,
                        "_request_deadline_monotonic": request_deadline,
                    },
                    timeout=remaining_timeout,
                    wrap_result=True,
                )
                config_obj = payload.get("config") if isinstance(payload, dict) else None
                if isinstance(config_obj, dict):
                    self._commit_optimistic_write_result_if_current(
                        write_gen=write_gen,
                        config_obj=config_obj,
                        uncertain=False,
                    )
                return payload
            except TimeoutError:
                # No response does not prove that the atomic replace lost its
                # deadline race.  Keep the requested view; the next successful
                # config read will replace it with the persisted effective config.
                with self._get_config_state_lock():
                    self._bump_effective_config_gen()
                    self._effective_config_uncertain = True
                    self._rejected_host_rollback = None
                    self._allocate_owner_token_locked()
                raise
            except asyncio.CancelledError:
                with self._get_config_state_lock():
                    self._bump_effective_config_gen()
                    self._effective_config_uncertain = True
                    self._rejected_host_rollback = None
                    self._allocate_owner_token_locked()
                raise
            except Exception:
                self._rollback_optimistic_write_if_current(
                    write_gen=write_gen,
                    old_effective_config=old_effective_config,
                    old_effective_config_uncertain=old_effective_config_uncertain,
                    old_owner_token=old_owner_token,
                )
                raise
            finally:
                with self._get_config_state_lock():
                    self._config_write_in_progress = False
                    self._in_flight_write_base_owner_token = None
                    self._rejected_host_rollback = None
        finally:
            write_lock.release()

    async def _refresh_effective_config_after_profile_change(
        self,
        *,
        timeout: float,
        refresh_gen: int | None = None,
        refresh_epoch: int | None = None,
    ) -> None:
        deadline = time.monotonic() + max(float(timeout), 0.0)
        if refresh_gen is None or refresh_epoch is None:
            refresh_gen, refresh_epoch = self._invalidate_effective_config_for_profile_refresh()
        while True:
            with self._get_config_state_lock():
                if int(getattr(self, "_profile_refresh_epoch", 0) or 0) != refresh_epoch:
                    return
                if isinstance(self._effective_config, dict):
                    return
                if bool(getattr(self, "_effective_config_uncertain", False)):
                    return
                write_in_progress = bool(getattr(self, "_config_write_in_progress", False))
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                return
            if write_in_progress:
                write_lock = self._get_config_write_lock()
                try:
                    await write_lock.acquire(deadline=deadline)
                except TimeoutError:
                    return
                write_lock.release()
                continue
            attempt_gen = self._current_effective_config_gen()
            try:
                await self.get_own_config(timeout=remaining)
            except Exception:
                with self._get_config_state_lock():
                    if (
                        self._current_effective_config_gen() == attempt_gen
                        and self._effective_config is None
                    ):
                        self._record_effective_config_locked(None, uncertain=False)
                        return
                    if int(getattr(self, "_profile_refresh_epoch", 0) or 0) != refresh_epoch:
                        return
                    if isinstance(self._effective_config, dict):
                        return
                    if bool(getattr(self, "_effective_config_uncertain", False)):
                        return
                continue
            with self._get_config_state_lock():
                if int(getattr(self, "_profile_refresh_epoch", 0) or 0) != refresh_epoch:
                    return
                if isinstance(self._effective_config, dict):
                    return
                if bool(getattr(self, "_effective_config_uncertain", False)):
                    return
                if self._current_effective_config_gen() == attempt_gen:
                    return

    async def upsert_own_profile_config(
        self,
        profile_name: str,
        config: Dict[str, Any],
        *,
        make_active: bool = False,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        if not isinstance(profile_name, str) or not profile_name.strip():
            raise ValueError("profile_name must be a non-empty string")
        if not isinstance(config, dict):
            raise TypeError("config must be a dict")
        normalized_name = profile_name.strip()
        request_timeout = self._normalize_config_timeout(timeout)
        request_deadline = time.monotonic() + request_timeout
        write_lock = self._get_config_write_lock()
        try:
            await write_lock.acquire(deadline=request_deadline)
        except TimeoutError as e:
            raise TimeoutError(f"Plugin profile config upsert timed out after {timeout}s") from e
        refresh_info: tuple[int, int] | None = None
        try:
            remaining_timeout = request_deadline - time.monotonic()
            if remaining_timeout <= 0.0:
                raise TimeoutError(f"Plugin profile config upsert timed out after {timeout}s")
            try:
                payload = await self._send_request_and_wait_async(
                    method_name="upsert_own_profile_config",
                    request_type="PLUGIN_CONFIG_PROFILE_UPSERT",
                    request_data={
                        "plugin_id": self.plugin_id,
                        "profile_name": normalized_name,
                        "config": config,
                        "make_active": bool(make_active),
                        "_request_deadline_monotonic": request_deadline,
                    },
                    timeout=remaining_timeout,
                    wrap_result=True,
                )
            except TimeoutError as e:
                self._mark_profile_write_uncertain()
                raise TimeoutError(f"Plugin profile config upsert timed out after {timeout}s") from e
            except asyncio.CancelledError:
                self._mark_profile_write_uncertain()
                raise
            if isinstance(payload, dict) and isinstance(payload.get("affected_active"), bool):
                affected_active = payload["affected_active"]
            elif isinstance(payload, dict) and isinstance(payload.get("active_profile"), str):
                affected_active = bool(make_active) or payload["active_profile"] == normalized_name
            else:
                affected_active = bool(make_active)
            if affected_active:
                refresh_info = self._invalidate_effective_config_for_profile_refresh()
        finally:
            write_lock.release()
        if refresh_info is not None:
            refresh_gen, refresh_epoch = refresh_info
            refresh_timeout = request_deadline - time.monotonic()
            if refresh_timeout > 0.0:
                await self._refresh_effective_config_after_profile_change(
                    timeout=refresh_timeout,
                    refresh_gen=refresh_gen,
                    refresh_epoch=refresh_epoch,
                )
        return payload

    async def delete_own_profile_config(
        self,
        profile_name: str,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        if not isinstance(profile_name, str) or not profile_name.strip():
            raise ValueError("profile_name must be a non-empty string")
        normalized_name = profile_name.strip()
        request_timeout = self._normalize_config_timeout(timeout)
        request_deadline = time.monotonic() + request_timeout
        write_lock = self._get_config_write_lock()
        try:
            await write_lock.acquire(deadline=request_deadline)
        except TimeoutError as e:
            raise TimeoutError(f"Plugin profile config delete timed out after {timeout}s") from e
        refresh_info: tuple[int, int] | None = None
        try:
            remaining_timeout = request_deadline - time.monotonic()
            if remaining_timeout <= 0.0:
                raise TimeoutError(f"Plugin profile config delete timed out after {timeout}s")
            try:
                payload = await self._send_request_and_wait_async(
                    method_name="delete_own_profile_config",
                    request_type="PLUGIN_CONFIG_PROFILE_DELETE",
                    request_data={
                        "plugin_id": self.plugin_id,
                        "profile_name": normalized_name,
                        "_request_deadline_monotonic": request_deadline,
                    },
                    timeout=remaining_timeout,
                    wrap_result=True,
                )
            except TimeoutError as e:
                self._mark_profile_write_uncertain()
                raise TimeoutError(f"Plugin profile config delete timed out after {timeout}s") from e
            except asyncio.CancelledError:
                self._mark_profile_write_uncertain()
                raise
            affected_active = (
                payload.get("affected_active")
                if isinstance(payload, dict) and isinstance(payload.get("affected_active"), bool)
                else False
            )
            if affected_active:
                refresh_info = self._invalidate_effective_config_for_profile_refresh()
        finally:
            write_lock.release()
        if refresh_info is not None:
            refresh_gen, refresh_epoch = refresh_info
            refresh_timeout = request_deadline - time.monotonic()
            if refresh_timeout > 0.0:
                await self._refresh_effective_config_after_profile_change(
                    timeout=refresh_timeout,
                    refresh_gen=refresh_gen,
                    refresh_epoch=refresh_epoch,
                )
        return payload

    async def set_own_active_profile(
        self,
        profile_name: str,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        if not isinstance(profile_name, str) or not profile_name.strip():
            raise ValueError("profile_name must be a non-empty string")
        normalized_name = profile_name.strip()
        request_timeout = self._normalize_config_timeout(timeout)
        request_deadline = time.monotonic() + request_timeout
        write_lock = self._get_config_write_lock()
        try:
            await write_lock.acquire(deadline=request_deadline)
        except TimeoutError as e:
            raise TimeoutError(f"Plugin active profile update timed out after {timeout}s") from e
        refresh_info: tuple[int, int] | None = None
        try:
            remaining_timeout = request_deadline - time.monotonic()
            if remaining_timeout <= 0.0:
                raise TimeoutError(f"Plugin active profile update timed out after {timeout}s")
            try:
                payload = await self._send_request_and_wait_async(
                    method_name="set_own_active_profile",
                    request_type="PLUGIN_CONFIG_PROFILE_ACTIVATE",
                    request_data={
                        "plugin_id": self.plugin_id,
                        "profile_name": normalized_name,
                        "_request_deadline_monotonic": request_deadline,
                    },
                    timeout=remaining_timeout,
                    wrap_result=True,
                )
            except TimeoutError as e:
                self._mark_profile_write_uncertain()
                raise TimeoutError(f"Plugin active profile update timed out after {timeout}s") from e
            except asyncio.CancelledError:
                self._mark_profile_write_uncertain()
                raise
            refresh_info = self._invalidate_effective_config_for_profile_refresh()
        finally:
            write_lock.release()
        if refresh_info is not None:
            refresh_gen, refresh_epoch = refresh_info
            refresh_timeout = request_deadline - time.monotonic()
            if refresh_timeout > 0.0:
                await self._refresh_effective_config_after_profile_change(
                    timeout=refresh_timeout,
                    refresh_gen=refresh_gen,
                    refresh_epoch=refresh_epoch,
                )
        return payload
