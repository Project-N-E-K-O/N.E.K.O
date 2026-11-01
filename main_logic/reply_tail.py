"""Opt-in, in-memory display attachments owned by one ordinary text reply."""
from __future__ import annotations

import asyncio
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from utils.logger_config import get_module_logger

MAX_SCOPES = 128
MAX_REGISTRATIONS = 64
MAX_REGISTRATION_ID_LENGTH = 128
# Settled receipts and tombstones are kept for status queries, bounded apart
# from the registrations still holding images.
MAX_SETTLED_RECORDS = 256
MAX_PAYLOAD_BYTES = 2 * 1024 * 1024
MAX_RETAINED_BYTES = 16 * 1024 * 1024
MAX_VALIDATIONS = 4
RETENTION_SECONDS = 120.0
SUBMISSION_TIMEOUT_SECONDS = 5.0
MAX_REPLY_HOOKS = 8
MAX_REPLY_HOOKS_PER_PLUGIN = 2
MAX_REPLY_HOOK_TASKS = 8
MAX_REPLY_HOOK_TEXT_CHARS = 65536
REPLY_HOOK_TIMEOUT_SECONDS = 15.0

logger = get_module_logger(__name__, "Main")


# The display validator lives in the main server, above main_logic in the
# module layering, so the server binds it here at import time.
_image_validation: tuple[Callable[[list], list], Callable[[Any], bool]] | None = None


def bind_image_validation(
    build_blocks: Callable[[list], list], payloads_conflict: Callable[[Any], bool],
) -> None:
    """Bind the plugin image chat validator and its dual-source check."""
    global _image_validation
    _image_validation = (build_blocks, payloads_conflict)


def _valid_registration_id(registration_id: Any) -> bool:
    return (
        isinstance(registration_id, str)
        and 0 < len(registration_id) <= MAX_REGISTRATION_ID_LENGTH
    )


def receipt(status: str, reason: str = "", **extra: Any) -> dict:
    return {
        "accepted": status in {"registered", "submitted"},
        "status": status,
        **({"reason": reason} if reason else {}),
        **extra,
    }


class ReplyHookUnavailableError(Exception):
    """The callback service confirmed that this plugin process is not running."""


@dataclass(eq=False)
class _ReplyHook:
    plugin_id: str
    entry_id: str
    dispatch: Callable[[dict], Awaitable[None]]
    role: str | None
    timeout: float


@dataclass(eq=False)
class ReplyTailReply:
    registry: Any
    manager: Any
    owner: Any
    websocket: Any
    reply_id: str = field(default_factory=lambda: secrets.token_urlsafe(18))
    attempt: int = 0
    completed: bool = False
    closed: bool = False
    text: str = field(default="", repr=False)
    user_text: str = field(default="", repr=False)
    hooks: tuple[_ReplyHook, ...] = ()

    def discard_attempt(self) -> None:
        self.registry.cancel_reply(self, "reply_discarded")
        self.attempt += 1
        self.completed = False
        self.text = ""


@dataclass
class _Scope:
    reply: ReplyTailReply
    context: dict
    expires: float
    hook: _ReplyHook | None = None
    accepting: bool = False


@dataclass
class _Registration:
    scope: _Scope
    registration_id: str
    blocks: list[dict]
    size: int
    status: str = "registered"
    reason: str = ""
    submitted_at: float | None = None

    @property
    def active(self) -> bool:
        return self.status in {"registered", "submitting"}


def _reply_open(reply: ReplyTailReply, attempt: int) -> bool:
    """Whether ``reply`` still accepts attachments for its ``attempt``."""
    owner = reply.owner
    return not (
        reply.closed or reply.completed or reply.attempt != attempt
        or owner.taken_over or owner.turn_ended
        or reply.manager.websocket is not reply.websocket
    )


def _scope_open(scope: _Scope) -> bool:
    if scope.hook is None:
        return _reply_open(scope.reply, scope.context["attempt"])
    return (
        scope.accepting and scope.reply.completed and scope.reply.owner.turn_ended
        and scope.reply.attempt == scope.context["attempt"]
        and scope.reply.manager.websocket is scope.reply.websocket
    )


class ReplyTailRegistry:
    def __init__(self) -> None:
        self._scopes: dict[str, _Scope] = {}
        self._registrations: dict[tuple[str, str], _Registration] = {}
        self._retained_bytes = 0
        self._validations = 0
        self._hooks: dict[tuple[str, str], _ReplyHook] = {}
        self._hook_tasks: dict[asyncio.Task, _Scope] = {}

    def subscribe(
        self, plugin_id: str, entry_id: str, dispatch: Callable[[dict], Awaitable[None]],
        *, role: str | None = None, timeout: float = REPLY_HOOK_TIMEOUT_SECONDS,
    ) -> dict:
        key = (plugin_id, entry_id)
        if key not in self._hooks and len(self._hooks) >= MAX_REPLY_HOOKS:
            return {"ok": False, "reason": "capacity"}
        if key not in self._hooks and sum(
            hook.plugin_id == plugin_id for hook in self._hooks.values()
        ) >= MAX_REPLY_HOOKS_PER_PLUGIN:
            return {"ok": False, "reason": "plugin_capacity"}
        self.unsubscribe(plugin_id, entry_id)
        self._hooks[key] = _ReplyHook(plugin_id, entry_id, dispatch, role, timeout)
        return {"ok": True}

    def _remove_hook(self, hook: _ReplyHook, reason: str) -> bool:
        key = (hook.plugin_id, hook.entry_id)
        # A failure from an old invocation must not remove a new subscription.
        if self._hooks.get(key) is not hook:
            return False
        del self._hooks[key]
        for task, scope in list(self._hook_tasks.items()):
            if scope.hook is hook:
                scope.accepting = False
                self._cancel_scope(scope, reason)
                if task is not asyncio.current_task():
                    task.cancel()
        return True

    def unsubscribe(self, plugin_id: str, entry_id: str | None = None, *, role: str | None = None) -> int:
        removed = [
            key for key, hook in self._hooks.items()
            if hook.plugin_id == plugin_id and (entry_id is None or hook.entry_id == entry_id)
            and (role is None or hook.role == role)
        ]
        for key in removed:
            self._remove_hook(self._hooks[key], "hook_unsubscribed")
        return len(removed)

    def prepare_reply(self, manager: Any, owner: Any, *, user_text: str) -> None:
        """Snapshot explicit subscribers before an ordinary text reply starts."""
        hooks = tuple(hook for hook in self._hooks.values() if hook.role in (None, manager.lanlan_name))
        if not hooks:
            return
        if len(user_text) > MAX_REPLY_HOOK_TEXT_CHARS:
            logger.warning("Reply completion hooks skipped: reason=input_too_large")
            return
        reply = self._reply_for_owner(manager, owner)
        if reply is not None:
            reply.hooks = hooks
            reply.user_text = user_text

    def _reply_for_owner(self, manager: Any, owner: Any) -> ReplyTailReply | None:
        websocket = getattr(manager, "websocket", None)
        client_version = getattr(getattr(websocket, "state", None), "reply_tail_version", None)
        if (
            not owner or not getattr(owner, "request_id", None)
            or getattr(owner, "session", None) is None
            or getattr(owner, "turn_ended", False) or getattr(owner, "taken_over", False)
            or client_version != 1
        ):
            return None
        reply = getattr(owner, "reply_tail", None)
        if reply is None:
            reply = ReplyTailReply(self, manager, owner, websocket)
            owner.reply_tail = reply
        return reply if not reply.closed and reply.websocket is websocket else None

    def _settle(self, item: _Registration, status: str, reason: str = "") -> None:
        self._retained_bytes -= item.size
        item.size = 0
        item.blocks = []
        item.status, item.reason = status, reason
        if status == "submitted":
            item.submitted_at = time.time()

    def _prune(self) -> None:
        now = time.monotonic()
        for token, scope in list(self._scopes.items()):
            if scope.expires > now:
                continue
            for key, item in list(self._registrations.items()):
                if key[0] == token and item.status != "submitting":
                    self._settle(item, "cancelled", "expired")
                    del self._registrations[key]
            if not any(key[0] == token for key in self._registrations):
                del self._scopes[token]

    def _active_count(self) -> int:
        return sum(1 for item in self._registrations.values() if item.active)

    def _room_for_scope(self) -> bool:
        """Make room for a new scope by evicting the oldest idle one.

        A scope is idle once its reply closed and nothing of it still holds an
        image; only its settled receipts go with it.
        """
        if len(self._scopes) < MAX_SCOPES:
            return True
        for token, scope in list(self._scopes.items()):
            if scope.accepting or not scope.reply.closed or any(
                key[0] == token and item.active for key, item in self._registrations.items()
            ):
                continue
            for key in [key for key in self._registrations if key[0] == token]:
                del self._registrations[key]
            del self._scopes[token]
            return True
        return False

    def _room_for_settled(self) -> bool:
        """Keep settled receipts within their own bound.

        Only a receipt of a closed reply is evicted: one of an open reply
        still decides a repeated registration ID (a duplicate, or a cancel
        that arrived first).
        """
        settled = [key for key, item in self._registrations.items() if not item.active]
        if len(settled) < MAX_SETTLED_RECORDS:
            return True
        for key in settled:
            scope = self._registrations[key].scope
            if scope.reply.closed and not scope.accepting:
                del self._registrations[key]
                return True
        return False

    def _room_for_registration(self) -> bool:
        return self._active_count() < MAX_REGISTRATIONS and self._room_for_settled()

    def tool_context(self, manager: Any, owner: Any, call_id: str, source: str) -> dict | None:
        self._prune()
        if not source.startswith("plugin:") or not source[7:] or not call_id:
            return None
        reply = self._reply_for_owner(manager, owner)
        if reply is None:
            return None
        return self._new_scope(reply, call_id, source)

    def _new_scope(self, reply: ReplyTailReply, call_id: str, source: str,
                   *, hook: _ReplyHook | None = None) -> dict | None:
        self._prune()
        # One scope per invocation: provider call IDs are not unique within a
        # reply (ID-less providers number each tool round from ``call_0``).
        if not self._room_for_scope():
            return None
        token = secrets.token_urlsafe(32)
        context = {
            "version": 1, "reply_id": reply.reply_id,
            "request_id": str(reply.owner.request_id), "role": str(reply.manager.lanlan_name),
            "call_id": call_id, "invocation_id": secrets.token_urlsafe(12),
            "source": source, "attempt": reply.attempt, "token": token,
        }
        self._scopes[token] = _Scope(
            reply, context, time.monotonic() + RETENTION_SECONDS, hook, hook is not None,
        )
        return dict(context)

    def _scope(self, context: dict, plugin_id: str) -> _Scope | None:
        self._prune()
        scope = self._scopes.get(str(context.get("token", "")))
        if (
            scope is None or scope.context != context
            or scope.context["source"] != "plugin:" + plugin_id
        ):
            return None
        return scope

    def _result(self, item: _Registration) -> dict:
        if item.status == "submitting":
            return receipt("uncertain", "submission_in_progress")
        return receipt(
            item.status, item.reason,
            **({"submitted_at": item.submitted_at} if item.submitted_at is not None else {}),
        )

    async def register(
        self, context: dict, registration_id: str, parts: list, *,
        plugin_id: str, ai_behavior: str = "blind",
    ) -> dict:
        scope = self._scope(context, plugin_id)
        if scope is None:
            return receipt("failed", "invalid_context")
        if not _valid_registration_id(registration_id):
            return receipt("failed", "invalid_attachment")
        key = (scope.context["token"], registration_id)
        if key in self._registrations:
            return {**self._result(self._registrations[key]), "duplicate": True}
        if not _scope_open(scope):
            return receipt("cancelled", "reply_closed")
        if (
            ai_behavior != "blind" or not isinstance(parts, list)
            or not 0 < len(parts) <= 2
            or any(not isinstance(part, dict) or part.get("type") != "image" for part in parts)
        ):
            return receipt("failed", "invalid_attachment")
        size = len(json.dumps(parts, ensure_ascii=True).encode("utf-8"))
        if size > MAX_PAYLOAD_BYTES:
            return receipt("failed", "attachment_too_large")
        # Reuse the display validator; unlike ToolImage this preserves GIF bytes
        # and only accepts inline pixels or host-minted media references.
        if _image_validation is None:
            return receipt("failed", "unavailable")
        build_blocks, payloads_conflict = _image_validation
        if any(payloads_conflict(part) for part in parts):
            return receipt("failed", "invalid_attachment")
        if (
            self._validations >= MAX_VALIDATIONS
            or self._active_count() >= MAX_REGISTRATIONS
            or self._retained_bytes + size > MAX_RETAINED_BYTES
        ):
            return receipt("failed", "capacity")
        self._validations += 1
        self._retained_bytes += size

        def release_validation(_task: Any) -> None:
            self._validations -= 1
            self._retained_bytes -= size
            # Consume a validator exception even if its HTTP caller was cancelled.
            if not _task.cancelled():
                _task.exception()

        validation = asyncio.create_task(asyncio.to_thread(build_blocks, parts))
        try:
            blocks = await asyncio.shield(validation)
        finally:
            if validation.done():
                release_validation(validation)
            else:
                validation.add_done_callback(release_validation)
        # Validation yielded: cancellation, duplicate registration and expiry
        # must win over the state captured before the decode.
        if self._scope(context, plugin_id) is not scope:
            return receipt("cancelled", "expired")
        if key in self._registrations:
            return {**self._result(self._registrations[key]), "duplicate": True}
        if not _scope_open(scope):
            return receipt("cancelled", "reply_closed")
        if len(blocks) != len(parts):
            return receipt("failed", "invalid_image")
        if (
            self._retained_bytes + size > MAX_RETAINED_BYTES
            or not self._room_for_registration()
        ):
            return receipt("failed", "capacity")
        self._registrations[key] = _Registration(scope, registration_id, blocks, size)
        self._retained_bytes += size
        return receipt("registered")

    def status(self, context: dict, registration_id: str, *, plugin_id: str) -> dict:
        scope = self._scope(context, plugin_id)
        if scope is None:
            return receipt("failed", "invalid_context")
        if not _valid_registration_id(registration_id):
            return receipt("failed", "registration_not_found")
        item = self._registrations.get((scope.context["token"], registration_id))
        return self._result(item) if item else receipt("failed", "registration_not_found")

    def cancel(self, context: dict, registration_id: str, *, plugin_id: str) -> dict:
        scope = self._scope(context, plugin_id)
        if scope is None:
            return receipt("failed", "invalid_context")
        if not _valid_registration_id(registration_id):
            return receipt("failed", "registration_not_found")
        key = (scope.context["token"], registration_id)
        item = self._registrations.get(key)
        if item is None:
            if not self._room_for_settled():
                return receipt("uncertain", "capacity")
            # A cancel arriving before the decode/registration is a tombstone.
            item = _Registration(scope, registration_id, [], 0, "cancelled", "plugin_cancelled")
            self._registrations[key] = item
        elif item.status == "registered":
            self._settle(item, "cancelled", "plugin_cancelled")
        return self._result(item)

    def cancel_reply(self, reply: ReplyTailReply, reason: str) -> None:
        for item in self._registrations.values():
            if item.scope.reply is reply and item.status == "registered":
                self._settle(item, "cancelled", reason)

    def _cancel_scope(self, scope: _Scope, reason: str) -> None:
        for item in self._registrations.values():
            if item.scope is scope and item.status == "registered":
                self._settle(item, "cancelled", reason)

    async def finish(self, reply: ReplyTailReply) -> None:
        if reply.closed:
            return
        reply.closed = True
        self._prune()
        if not reply.completed or not reply.owner.turn_ended:
            self.cancel_reply(reply, "reply_incomplete")
            return
        await self._submit(reply)
        # Optional plugin work runs after normal turn completion and never
        # holds the input task open while a plugin selects its attachment.
        if not reply.text or len(reply.text) > MAX_REPLY_HOOK_TEXT_CHARS:
            if reply.hooks and reply.text:
                logger.warning("Reply completion hooks skipped: reason=text_too_large")
            return
        for hook in reply.hooks:
            if (
                self._hooks.get((hook.plugin_id, hook.entry_id)) is not hook
                or reply.manager.websocket is not reply.websocket
            ):
                continue
            if len(self._hook_tasks) >= MAX_REPLY_HOOK_TASKS:
                logger.warning("Reply completion hook skipped: plugin=%s entry=%s reason=capacity",
                               hook.plugin_id, hook.entry_id)
                continue
            context = self._new_scope(reply, "reply_completed:" + hook.entry_id, "plugin:" + hook.plugin_id, hook=hook)
            if context is None:
                logger.warning("Reply completion hook skipped: plugin=%s entry=%s reason=scope_capacity",
                               hook.plugin_id, hook.entry_id)
                continue
            scope = self._scopes[context["token"]]
            task = asyncio.create_task(self._run_hook(scope))
            self._hook_tasks[task] = scope
            task.add_done_callback(self._hook_tasks.pop)

    async def _run_hook(self, scope: _Scope) -> None:
        hook, reply = scope.hook, scope.reply
        try:
            has_attachments = any(
                item.scope.reply is reply and item.scope.context["source"] == "plugin:" + hook.plugin_id
                and item.status in {"registered", "submitting", "submitted", "uncertain"}
                for item in self._registrations.values()
            )
            async with asyncio.timeout(hook.timeout):
                await hook.dispatch({
                    "text": reply.text, "user_text": reply.user_text,
                    "has_attachments": has_attachments, "host_reply": dict(scope.context),
                })
            scope.accepting = False
            await self._submit(reply, scope=scope)
        except asyncio.CancelledError:
            self._cancel_scope(scope, "hook_cancelled")
            raise
        except ReplyHookUnavailableError:
            self._cancel_scope(scope, "hook_unavailable")
            if self._remove_hook(hook, "hook_unavailable"):
                logger.warning("Reply completion hook removed: plugin=%s entry=%s reason=plugin_not_running",
                               hook.plugin_id, hook.entry_id)
        except Exception as exc:
            self._cancel_scope(scope, "hook_failed")
            # Plugin failures can contain conversation text or credentials.
            logger.warning("Reply completion hook failed: plugin=%s entry=%s type=%s",
                           hook.plugin_id, hook.entry_id, type(exc).__name__)
        finally:
            scope.accepting = False

    async def _submit(self, reply: ReplyTailReply, *, scope: _Scope | None = None) -> None:
        deadline = time.monotonic() + SUBMISSION_TIMEOUT_SECONDS
        for item in list(self._registrations.values()):
            if item.scope.reply is not reply or item.status != "registered":
                continue
            if scope is not None and item.scope is not scope:
                continue
            if reply.manager.websocket is not reply.websocket:
                self._settle(item, "failed", "connection_changed")
                continue
            item.status = "submitting"
            try:
                async with asyncio.timeout_at(min(deadline, item.scope.expires)):
                    sent = await reply.manager.render_chat_blocks(
                        item.blocks, request_id=reply.owner.request_id,
                        source="plugin", source_name=item.scope.context["source"][7:],
                        reply_tail={
                            "version": 1, "reply_id": reply.reply_id,
                            "request_id": str(reply.owner.request_id),
                            "call_id": item.scope.context["call_id"],
                            "invocation_id": item.scope.context["invocation_id"],
                            "registration_id": item.registration_id,
                        },
                    )
            except TimeoutError:
                # A write may have reached the peer before cancellation.
                self._settle(item, "uncertain", "submission_timeout")
                if scope is None:
                    self.cancel_reply(reply, "submission_timeout")
                else:
                    self._cancel_scope(scope, "submission_timeout")
                return
            except asyncio.CancelledError:
                self._settle(item, "uncertain", "submission_interrupted")
                if scope is None:
                    self.cancel_reply(reply, "transport_interrupted")
                else:
                    self._cancel_scope(scope, "transport_interrupted")
                raise
            except BaseException:
                # Like a cancellation: the write may have reached the peer.
                self._settle(item, "uncertain", "submission_interrupted")
                if scope is None:
                    self.cancel_reply(reply, "transport_interrupted")
                else:
                    self._cancel_scope(scope, "transport_interrupted")
                raise
            self._settle(item, "submitted" if sent else "failed", "" if sent else "transport_failed")


reply_tail_registry = ReplyTailRegistry()
