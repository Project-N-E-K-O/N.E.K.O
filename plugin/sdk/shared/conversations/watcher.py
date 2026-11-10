"""Event and recent-window observation using the host's existing bus."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections import OrderedDict, deque
from typing import Any

from plugin.sdk.shared.contracts.conversations import (
    ConversationChange,
    ConversationMessage,
    InputSubmittedEvent,
    ReplyEvent,
    PlaybackDetail,
)
from plugin.sdk.shared.models.exceptions import TransportError

log = logging.getLogger(__name__)
_MAX_PENDING = 512
_MAX_PENDING_BYTES = 2 * 1024 * 1024
_MAX_RUNNING = 8
_STOP_GRACE = 0.25
_MAX_BUFFER = 512
_MAX_BUFFER_BYTES = 2 * 1024 * 1024
_MAX_REVISIONS = 2048
_MAX_VIEW = 1000


def _matches(message, filters):
    for key, value in filters.items():
        actual = (
            message.origin.kind
            if key == "origin_kind" and message.origin
            else getattr(message, key, None)
        )
        if actual != value:
            return False
    return True


class ConversationWatcher:
    def __init__(self, raw_adapter, host_ctx, *, filters, view_limit=None):
        self._adapter = raw_adapter
        self._ctx = host_ctx
        self._filters = {k: v for k, v in filters.items() if v is not None}
        for k, v in self._filters.items():
            if (
                k not in ("character", "role", "kind", "input_source", "origin_kind")
                or not isinstance(v, str)
                or not v.strip()
            ):
                raise ValueError(f"Invalid filter: {k}")
        if self._filters.get("role") not in (
            None,
            "user",
            "assistant",
            "instruction",
            "unknown",
        ):
            raise ValueError("Invalid role")
        if self._filters.get("input_source") not in (None, "text", "voice", "unknown"):
            raise ValueError("Invalid input_source")
        if view_limit is not None and (
            isinstance(view_limit, bool)
            or not isinstance(view_limit, int)
            or view_limit < 1
        ):
            raise ValueError("view_limit must be a positive integer")
        self._view_limit = 50 if view_limit is None else view_limit
        self._effective_limit = min(self._view_limit, _MAX_VIEW)
        self._callbacks = []
        self._sub_id = None
        self._active = False
        self._starting = False
        self._generation = 0
        self._loop = None
        self._view = []
        self._view_watermark = 0
        self._view_versions = OrderedDict()
        self._initialized = False
        self._refresh_needed = False
        self._capacity_blocked = False
        self._refresh_task = None
        self._closing = None
        self._buffer = deque()
        self._buffer_bytes = 0
        self._buffer_overflow = False
        self._pending = deque()
        self._pending_bytes = 0
        self._running = {}
        self._delivered_view = {}
        self.dropped = 0

    def subscribe(self, *, on):
        if self._active or self._starting:
            raise RuntimeError("Subscribe before starting the watcher")
        if on not in (
            "record",
            "message.updated",
            "change",
            "input.submitted",
            "reply.started",
            "reply.closed",
            "reply.updated",
            "playback.started",
            "playback.stopped",
        ):
            raise ValueError(f"Unsupported event: {on}")
        role = self._filters.get("role")
        if on.startswith("reply.") and (
            "kind" in self._filters
            or "input_source" in self._filters
            or role not in (None, "assistant")
        ):
            raise ValueError("filters do not apply to reply events")
        if on.startswith("playback.") and any(
            k in self._filters for k in ("role", "kind", "input_source", "origin_kind")
        ):
            raise ValueError("filters do not apply to playback events")
        if on == "input.submitted" and (
            "kind" in self._filters
            or "origin_kind" in self._filters
            or role not in (None, "user")
        ):
            raise ValueError("filters do not apply to input.submitted")
        if self._filters.get("input_source") is not None and role in (
            "assistant",
            "instruction",
        ):
            raise ValueError("input_source only applies to user messages")
        if self._filters.get("origin_kind") is not None and role == "user":
            raise ValueError("origin_kind only applies to assistant messages")

        def decorate(fn):
            if self._active or self._starting:
                raise RuntimeError("Subscribe before starting the watcher")
            if not callable(fn):
                raise TypeError("callback must be callable")
            self._callbacks.append((on, fn))
            return fn

        return decorate

    async def start(self):
        if self._active:
            return self
        if self._starting:
            raise TransportError(
                "Conversation watcher is already starting",
                code="CONVERSATIONS_NOT_READY",
            )
        self._starting = True
        generation = self._generation
        try:
            return await self._start_once(generation)
        finally:
            self._starting = False

    async def _start_once(self, generation):
        if self._closing is not None:
            try:
                await asyncio.wait_for(
                    asyncio.shield(asyncio.wrap_future(self._closing)),
                    _STOP_GRACE + 0.5,
                )
            except (TimeoutError, RuntimeError):
                raise TransportError(
                    "Previous conversation watcher is still closing",
                    code="CONVERSATIONS_NOT_READY",
                )
            self._closing = None
        if not self._callbacks:
            raise ValueError("At least one subscription is required")
        sender = getattr(self._ctx, "_send_request_and_wait_async", None)
        register = getattr(self._adapter, "register_watcher", None)
        unregister = getattr(self._adapter, "unregister_watcher", None)
        if not callable(sender) or not callable(register) or not callable(unregister):
            raise TransportError(
                "conversations_v2 watch is unavailable", code="CAPABILITY_UNAVAILABLE"
            )
        has_view = any(on == "change" for on, _ in self._callbacks)
        rules = {on for on, _ in self._callbacks if on != "change"}
        if has_view:
            rules.update(("record", "message.updated"))
        try:
            result = await sender(
                method_name="conversations_subscribe",
                request_type="BUS_SUBSCRIBE",
                request_data={
                    "bus": "conversations",
                    "contract": "conversations_v2",
                    "deliver": "delta",
                    "rules": sorted(rules),
                    "filters": self._filters,
                    "view": has_view,
                },
                timeout=5.0,
                wrap_result=False,
            )
        except Exception as exc:
            if getattr(exc, "code", None) == "UNKNOWN_OPERATION":
                raise TransportError(
                    str(exc),
                    code="CAPABILITY_UNAVAILABLE",
                    details=getattr(exc, "details", None),
                ) from exc
            raise
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("sub_id"), str)
            or not result["sub_id"]
        ):
            raise TransportError("Invalid subscription response", code="PROTOCOL_ERROR")
        if generation != self._generation:
            await sender(
                method_name="conversations_unsubscribe",
                request_type="BUS_UNSUBSCRIBE",
                request_data={"bus": "conversations", "sub_id": result["sub_id"]},
                timeout=5.0,
                wrap_result=False,
            )
            raise TransportError(
                "Conversation watcher stopped during startup",
                code="CONVERSATIONS_NOT_READY",
            )
        self._sub_id = result["sub_id"]
        self._active = True
        self._initialized = False
        self._capacity_blocked = False
        self._refresh_needed = has_view
        try:
            register(self._sub_id, self)
            activate = getattr(self._adapter, "activate_watcher", None)
            if callable(activate):
                activate(self)
        except BaseException:
            self._active = False
            self._sub_id = None
            unregister(result["sub_id"])
            try:
                await sender(
                    method_name="conversations_unsubscribe",
                    request_type="BUS_UNSUBSCRIBE",
                    request_data={"bus": "conversations", "sub_id": result["sub_id"]},
                    timeout=5.0,
                    wrap_result=False,
                )
            except Exception:
                log.exception("Failed to clean up conversation subscription")
            raise
        return self

    async def stop(self):
        self._generation += 1
        sid = self._sub_id
        if sid is None:
            if (
                self._closing is not None
                and asyncio.current_task() not in self._running.values()
            ):
                try:
                    await asyncio.wait_for(
                        asyncio.shield(asyncio.wrap_future(self._closing)),
                        _STOP_GRACE + 0.5,
                    )
                except (TimeoutError, RuntimeError):
                    pass
                self._closing = None
            return
        self._active = False
        self._sub_id = None
        self._adapter.unregister_watcher(sid)
        loop = self._loop
        if loop and loop.is_running():
            if loop is asyncio.get_running_loop():
                await self._cleanup()
            else:
                future = asyncio.run_coroutine_threadsafe(self._cleanup(), loop)
                try:
                    await asyncio.wait_for(
                        asyncio.wrap_future(future), _STOP_GRACE + 0.25
                    )
                except (TimeoutError, RuntimeError):
                    future.cancel()
        else:
            self._pending.clear()
            self._pending_bytes = 0
        self._loop = None
        sender = getattr(self._ctx, "_send_request_and_wait_async", None)
        if callable(sender):
            await sender(
                method_name="conversations_unsubscribe",
                request_type="BUS_UNSUBSCRIBE",
                request_data={"bus": "conversations", "sub_id": sid},
                timeout=5.0,
                wrap_result=False,
            )

    def _on_host_closed(self):
        self._generation += 1
        self._active = False
        sid = self._sub_id
        self._sub_id = None
        if sid is not None:
            self._adapter.unregister_watcher(sid)
        loop = self._loop
        if loop and loop.is_running():
            self._closing = asyncio.run_coroutine_threadsafe(self._cleanup(), loop)
        else:
            self._pending.clear()
            self._pending_bytes = 0
            self._refresh_needed = False
            self._capacity_blocked = False
            self._buffer.clear()
            self._buffer_bytes = 0
            self._view = []
            self._view_watermark = 0
            self._view_versions.clear()
            self._delivered_view.clear()
        self._loop = None

    async def _cleanup(self):
        self._pending.clear()
        self._pending_bytes = 0
        self._refresh_needed = False
        self._capacity_blocked = False
        self._buffer.clear()
        self._buffer_bytes = 0
        if self._refresh_task:
            self._refresh_task.cancel()
        tasks = set(self._running.values())
        tasks.discard(asyncio.current_task())
        if self._refresh_task:
            tasks.add(self._refresh_task)
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=_STOP_GRACE)
            for task in pending:
                task.cancel()
        self._running.clear()
        self._refresh_task = None
        self._view = []
        self._view_watermark = 0
        self._view_versions = OrderedDict()
        self._delivered_view.clear()

    async def __aenter__(self):
        return await self.start()

    async def __aexit__(self, *args):
        await self.stop()

    def _on_receiver_ready(self):
        if not self._active:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._loop = loop
        self._ensure_refresh()

    def _on_read_capacity_available(self):
        if (
            not self._active
            or not self._capacity_blocked
            or not self._refresh_needed
            or self._refresh_task is not None
            or self._loop is None
        ):
            return
        try:
            if asyncio.get_running_loop() is not self._loop:
                return
        except RuntimeError:
            return
        self._capacity_blocked = False
        self._ensure_refresh()

    def _on_remote_change(self, *, bus, op, delta):
        if not self._active or bus != "conversations":
            return
        self._on_receiver_ready()
        if op == "subscription.closed":
            self._on_host_closed()
            return
        if op == "view.invalidated":
            if self._has_view():
                self._refresh_needed = True
                self._ensure_refresh()
            return
        if op.startswith("reply."):
            if op not in ("reply.started", "reply.closed", "reply.updated"):
                return
            try:
                event = ReplyEvent.from_payload(delta)
            except (TypeError, ValueError, KeyError):
                return
            reply = event.reply
            if (
                self._filters.get("character") is None
                or self._filters["character"] == reply.character
            ) and (
                self._filters.get("origin_kind") is None
                or reply.origin is not None
                and self._filters["origin_kind"] == reply.origin.kind
            ):
                self._dispatch(op, event)
            return
        if op in ("playback.started", "playback.stopped"):
            try:
                event = PlaybackDetail.from_payload(delta)
            except (TypeError, ValueError, KeyError):
                return
            self._dispatch(op, event)
            return
        if op == "input.submitted":
            try:
                event = InputSubmittedEvent.from_payload(delta)
            except (TypeError, ValueError, KeyError):
                return
            submission = event.submission
            if (
                self._filters.get("character") is None
                or self._filters["character"] == submission.character
            ) and (
                self._filters.get("input_source") is None
                or self._filters["input_source"] == submission.input_source
            ):
                self._dispatch(op, event)
            return
        if op not in ("record", "message.updated"):
            return
        try:
            wire = dict(delta)
            revision = wire.pop("_revision")
            if (
                isinstance(revision, bool)
                or not isinstance(revision, int)
                or revision <= 0
            ):
                raise ValueError("invalid message revision")
            message = ConversationMessage.from_payload(wire)
        except (TypeError, ValueError, KeyError):
            if self._has_view():
                self._refresh_needed = True
                self._ensure_refresh()
            return
        if _matches(message, self._filters):
            self._dispatch(op, message)
        if not self._has_view():
            return
        if self._refresh_task is not None:
            size = len(repr(message).encode("utf-8", errors="replace"))
            while self._buffer and (
                len(self._buffer) >= _MAX_BUFFER
                or self._buffer_bytes + size > _MAX_BUFFER_BYTES
            ):
                self._buffer_bytes -= self._buffer.popleft()[3]
                self._buffer_overflow = True
            if size > _MAX_BUFFER_BYTES:
                self._buffer_overflow = True
            else:
                self._buffer.append((op, message, revision, size))
                self._buffer_bytes += size
        if (
            self._initialized
            and revision > self._view_watermark
            and revision > self._view_versions.get(message.id, 0)
        ):
            old = self._view
            self._view = self._apply(old, op, message)
            self._remember_revision(self._view_versions, message.id, revision)
            self._emit_change(old, self._view, "update")
        if self._refresh_needed:
            self._ensure_refresh()

    def _has_view(self):
        return any(on == "change" for on, _ in self._callbacks)

    def _ensure_refresh(self, *, allow_overflow_retry=True):
        if (
            not self._active
            or not self._refresh_needed
            or self._refresh_task is not None
            or self._loop is None
        ):
            return
        self._refresh_needed = False
        self._buffer.clear()
        self._buffer_bytes = 0
        self._buffer_overflow = False
        self._refresh_task = self._loop.create_task(
            self._refresh(allow_overflow_retry=allow_overflow_retry)
        )

    async def _refresh(self, *, allow_overflow_retry):
        succeeded = False
        try:
            get_snapshot = getattr(self._adapter, "get_snapshot", None)
            if not callable(get_snapshot):
                raise TransportError(
                    "conversation snapshots are unavailable",
                    code="CAPABILITY_UNAVAILABLE",
                )
            result = await get_snapshot(
                **self._filters,
                max_count=self._view_limit,
                order="asc",
                since_ts=None,
                timeout=5.0,
            )
            if not self._active:
                return
            if not isinstance(result, dict) or not isinstance(
                result.get("items"), list
            ):
                raise ValueError("invalid conversation snapshot")
            effective = result.get("effective_limit")
            if (
                isinstance(effective, bool)
                or not isinstance(effective, int)
                or effective < 1
                or effective > self._view_limit
            ):
                raise ValueError("invalid snapshot limit")
            self._effective_limit = effective
            watermark = result.get("snapshot_seq")
            if (
                isinstance(watermark, bool)
                or not isinstance(watermark, int)
                or watermark < 0
            ):
                raise ValueError("invalid snapshot sequence")
            snapshot = []
            versions = OrderedDict()
            for item in result["items"]:
                wire = dict(item)
                revision = wire.pop("_revision")
                if (
                    isinstance(revision, bool)
                    or not isinstance(revision, int)
                    or revision <= 0
                    or revision > watermark
                ):
                    raise ValueError("invalid snapshot revision")
                message = ConversationMessage.from_payload(wire)
                snapshot.append(message)
                self._remember_revision(versions, message.id, revision)
            for op, message, revision, _ in self._buffer:
                if revision > watermark and revision > versions.get(message.id, 0):
                    snapshot = self._apply(snapshot, op, message)
                    self._remember_revision(versions, message.id, revision)
            self._view_watermark = watermark
            self._view_versions = versions
            old = self._view
            self._view = snapshot[-self._effective_limit :]
            reason = "update" if self._initialized else "initial"
            self._initialized = True
            self._emit_change(old, self._view, reason)
            if self._buffer_overflow:
                self._refresh_needed = True
            self._capacity_blocked = False
            succeeded = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._refresh_needed = True
            self._capacity_blocked = getattr(exc, "code", None) == "BUSY"
            log.exception("Conversation view refresh failed")
        finally:
            self._refresh_task = None
            self._buffer.clear()
            self._buffer_bytes = 0
        # One immediate retry can recover a burst that stopped after dropping
        # buffered items. A second overflow stays dirty until another signal;
        # this does not become an unbounded recovery loop.
        if succeeded and self._active and self._refresh_needed:
            if not self._buffer_overflow:
                self._ensure_refresh(allow_overflow_retry=allow_overflow_retry)
            elif allow_overflow_retry:
                self._ensure_refresh(allow_overflow_retry=False)

    @staticmethod
    def _remember_revision(versions, message_id, revision):
        versions[message_id] = revision
        versions.move_to_end(message_id)
        while len(versions) > _MAX_REVISIONS:
            versions.popitem(last=False)

    def _apply(self, view, op, message):
        values = list(view)
        index = next(
            (i for i, item in enumerate(values) if item.id == message.id), None
        )
        matches = _matches(message, self._filters)
        if index is not None:
            if matches:
                values[index] = message
            else:
                values.pop(index)
        elif matches and op == "record":
            values.append(message)
        return values[-self._effective_limit :]

    def _emit_change(self, old, current, reason):
        before = {m.id: m for m in old}
        after = {m.id: m for m in current}
        change = ConversationChange(
            tuple(current),
            tuple(m for m in current if m.id not in before),
            tuple(m for m in current if m.id in before and m != before[m.id]),
            tuple(m for m in old if m.id not in after),
            reason,
        )
        if reason == "initial" or change.added or change.updated or change.removed:
            self._dispatch("change", change)

    def _dispatch(self, on, value):
        if self._loop is None or not self._active:
            return
        size = None
        for index, (event, _) in enumerate(self._callbacks):
            if event != on:
                continue
            if size is None:
                size = len(repr(value).encode("utf-8", errors="replace"))
            if on == "change":
                # Keep the initial event, but coalesce pending updates to the latest view.
                for pending in tuple(self._pending):
                    if (
                        pending[0] == index
                        and pending[1] == "change"
                        and pending[2].reason == "update"
                    ):
                        self._pending.remove(pending)
                        self._pending_bytes -= pending[3]
            while self._pending and (
                len(self._pending) >= _MAX_PENDING
                or self._pending_bytes + size > _MAX_PENDING_BYTES
            ):
                old = self._pending.popleft()
                self._pending_bytes -= old[3]
                self.dropped += 1
            if size > _MAX_PENDING_BYTES:
                self.dropped += 1
                continue
            self._pending.append((index, on, value, size))
            self._pending_bytes += size
        self._schedule()

    def _schedule(self):
        while self._active and len(self._running) < _MAX_RUNNING:
            entry = next((e for e in self._pending if e[0] not in self._running), None)
            if entry is None:
                break
            self._pending.remove(entry)
            self._pending_bytes -= entry[3]
            index, _, value, _ = entry
            task = self._loop.create_task(self._invoke(index, value))
            self._running[index] = task
            task.add_done_callback(lambda done, i=index: self._finished(i, done))

    async def _invoke(self, index, value):
        if not self._active:
            return
        try:
            if isinstance(value, ConversationChange):
                old = self._delivered_view.get(index, ())
                before = {m.id: m for m in old}
                after = {m.id: m for m in value.current}
                value = ConversationChange(
                    value.current,
                    tuple(m for m in value.current if m.id not in before),
                    tuple(
                        m for m in value.current if m.id in before and m != before[m.id]
                    ),
                    tuple(m for m in old if m.id not in after),
                    value.reason,
                )
                self._delivered_view[index] = value.current
            result = self._callbacks[index][1](value)
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Conversation callback failed")

    def _finished(self, index, task):
        if self._running.get(index) is task:
            self._running.pop(index, None)
        self._schedule()
