"""Chat workspace API (P08 + P09).

Endpoints
---------
* **P08** ``GET  /api/chat/prompt_preview``            — :class:`PromptBundle`
                                                         for the Preview panel.
* **P09** ``GET    /api/chat/messages``                — list messages.
* **P09** ``POST   /api/chat/messages``                — append message
                                                         (body: role, content,
                                                         timestamp?, source?).
* **P09** ``PUT    /api/chat/messages/{id}``           — edit content.
* **P09** ``PATCH  /api/chat/messages/{id}/timestamp`` — edit timestamp
                                                         (``null`` → clock.now()).
* **P09** ``DELETE /api/chat/messages/{id}``           — delete.
* **P09** ``POST   /api/chat/messages/truncate``       — "Re-run from here"
                                                         helper; body:
                                                         ``{keep_id, include?}``.
* **P09** ``POST   /api/chat/inject_system``           — inject system-role
                                                         message; body:
                                                         ``{content}``.
* **P09** ``POST   /api/chat/send``                    — SSE (text/event-stream);
                                                         body: ``{content, role?,
                                                         source?, time_advance?}``.

Design notes
------------
* **Single writer invariant**: every mutating endpoint acquires the
  per-session lock via :meth:`SessionStore.session_operation`. Reads
  (``GET /messages``) bypass the lock because the messages list is
  append-only in steady state and transient inconsistency is acceptable
  for a display-only path.
* **SSE for /send**: the FastAPI layer wraps
  :meth:`OfflineChatBackend.stream_send` in a raw Starlette
  :class:`StreamingResponse` since the chunks are produced live. The
  lock spans the entire streaming lifetime; the UI must not launch a
  second send while one is in flight (the session state will be
  ``busy`` until completion).
* **Error shape parity**: structured errors mid-stream are emitted as
  ``{"event": "error", ...}`` SSE frames (HTTP 200 body). Pre-stream
  errors (no session, session busy) are returned as the usual
  ``HTTPException(detail=...)`` so the frontend's error bus intercepts
  them consistently.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from tests.testbench.chat_messages import (
    ALLOWED_ROLES,
    ALLOWED_SOURCES,
    ROLE_SYSTEM,
    ROLE_USER,
    SOURCE_INJECT,
    SOURCE_MANUAL,
    check_timestamp_monotonic,
    find_message_index,
    make_message,
)
from tests.testbench.logger import python_logger
from tests.testbench.pipeline.chat_runner import (
    ChatConfigError,
    get_chat_backend,
)
from tests.testbench.pipeline.prompt_builder import (
    PreviewNotReady,
    build_prompt_bundle,
)
from tests.testbench.session_store import (
    Session,
    SessionConflictError,
    SessionState,
    get_session_store,
)

router = APIRouter(prefix="/api/chat", tags=["chat"])


# ── helpers ──────────────────────────────────────────────────────────


def _require_session() -> Session:
    """Return active session or HTTP 404 (matches other routers)."""
    session = get_session_store().get()
    if session is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error_type": "NoActiveSession",
                "message": "No active session; create one via POST /api/session first.",
            },
        )
    return session


def _session_conflict_to_http(exc: SessionConflictError) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "error_type": "SessionConflict",
            "message": str(exc),
            "state": exc.state.value,
            "busy_op": exc.busy_op,
        },
    )


def _lookup_error_to_http(exc: LookupError) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"error_type": "NoActiveSession", "message": str(exc)},
    )


def _parse_iso(ts: str) -> datetime:
    """Loose ISO8601 parser; raises HTTP 422 on malformed input."""
    try:
        return datetime.fromisoformat(ts)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error_type": "InvalidTimestamp",
                "message": f"timestamp 不是合法的 ISO 格式: {ts!r} ({exc})",
            },
        ) from exc


# ── P08 endpoint (unchanged) ─────────────────────────────────────────


@router.get("/prompt_preview")
def get_prompt_preview() -> dict[str, Any]:
    """Return the :class:`PromptBundle` for the current session.

    Error contract:
        * **404 NoActiveSession** — no session has been created.
        * **409 PersonaNotReady** — session exists but persona has no
          ``character_name`` yet. UI handles this as an empty-state prompt,
          not as a red error (frontend ``expectedStatuses: [404, 409]``).
        * **500** — upstream memory/prompt modules crashed unexpectedly;
          surfaced as a generic error so the tester can open Diagnostics.
    """
    session = _require_session()
    try:
        bundle = build_prompt_bundle(session)
    except PreviewNotReady as exc:
        raise HTTPException(
            status_code=409,
            detail={"error_type": exc.code, "message": exc.message},
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        python_logger().exception(
            "chat.prompt_preview: build_prompt_bundle failed (session=%s): %s",
            session.id, exc,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error_type": "PromptBuildFailed",
                "message": f"构建 Prompt 预览失败: {exc}",
            },
        ) from exc

    session.logger.log_sync(
        "chat.prompt_preview",
        payload={
            "character_name": bundle.metadata.get("character_name"),
            "language_short": bundle.metadata.get("language_short"),
            "template_used": bundle.metadata.get("template_used"),
            "system_prompt_chars": bundle.char_counts.get("system_prompt_total"),
            "warnings": bundle.warnings,
        },
    )
    return bundle.to_json()


# ── message CRUD (P09) ───────────────────────────────────────────────


class _AddMessageRequest(BaseModel):
    """Body for ``POST /api/chat/messages`` — manually seed a message.

    Typical use: hand-crafted user/assistant lines for regression tests,
    or pre-populating a script-like opening without a full /send cycle.
    """

    role: str = Field(..., description="user / assistant / system")
    content: str = Field("", description="Message text; empty allowed for placeholders.")
    timestamp: str | None = Field(
        default=None,
        description="ISO8601; omitted → uses session.clock.now().",
    )
    source: str = Field(
        default=SOURCE_MANUAL,
        description="Audit tag (manual / inject / llm / simuser / script / auto).",
    )
    reference_content: str | None = Field(
        default=None,
        description="Optional 'expected assistant' text for scripted/comparative eval.",
    )


class _EditMessageRequest(BaseModel):
    content: str = Field(..., description="New message text.")


class _PatchTimestampRequest(BaseModel):
    timestamp: str | None = Field(
        default=None,
        description="ISO8601; null → session.clock.now().",
    )


class _TruncateRequest(BaseModel):
    """Truncate the message list. Used by the UI's Re-run from here.

    ``keep_id`` is the last message to retain. ``include=True`` (default)
    keeps ``keep_id`` itself and drops everything after; ``include=False``
    drops ``keep_id`` and everything after. ``keep_id=null`` clears all
    messages.
    """

    keep_id: str | None = None
    include: bool = True


class _InjectSystemRequest(BaseModel):
    content: str = Field(..., description="System note text to inject mid-conversation.")


class _SendRequest(BaseModel):
    """Body for ``POST /api/chat/send``.

    ``time_advance`` is a convenience shortcut — if set, it is applied to
    the clock's "pending next turn" staging *before* the send consumes it.
    Mirrors the composer's "Next turn +Δt" buttons, so the UI can send
    both in a single round-trip instead of /time/stage_next_turn then
    /chat/send.
    """

    content: str = Field(..., description="User message text.")
    role: str = Field(default=ROLE_USER, description="user or system.")
    source: str = Field(
        default=SOURCE_MANUAL,
        description="Audit tag; manual by default.",
    )
    time_advance_seconds: int | None = Field(
        default=None,
        description="Relative advance in seconds applied to next-turn staging.",
    )
    time_absolute: str | None = Field(
        default=None,
        description="ISO8601; if set, stages absolute cursor for next turn.",
    )


def _serialize_messages(session: Session) -> dict[str, Any]:
    return {
        "messages": list(session.messages),
        "count": len(session.messages),
    }


@router.get("/messages")
async def list_messages() -> dict[str, Any]:
    """Return the entire message list for the active session."""
    session = _require_session()
    return _serialize_messages(session)


@router.post("/messages")
async def add_message(body: _AddMessageRequest) -> dict[str, Any]:
    """Append a manually-constructed message.

    Returns the full messages list so the UI can refresh without an
    extra GET round-trip.
    """
    if body.role not in ALLOWED_ROLES:
        raise HTTPException(
            status_code=422,
            detail={"error_type": "InvalidRole", "message": f"role={body.role!r} 不受支持。"},
        )
    if body.source not in ALLOWED_SOURCES:
        raise HTTPException(
            status_code=422,
            detail={"error_type": "InvalidSource", "message": f"source={body.source!r} 不受支持。"},
        )

    store = get_session_store()
    try:
        async with store.session_operation("chat.messages.add") as session:
            ts = (
                _parse_iso(body.timestamp)
                if body.timestamp else session.clock.now()
            )
            # A manually-appended message must not be older than the
            # current tail — otherwise the conversation list ends up
            # non-monotonic and every downstream consumer (time separator,
            # prompt builder recent_history slice, UI scroll-to-latest)
            # breaks. Auto-filled ts from session.clock.now() normally
            # satisfies this, but user-supplied ts (or a virtual clock
            # that was rewound via /time/set_now) can violate it, so we
            # verify unconditionally.
            err = check_timestamp_monotonic(
                session.messages, len(session.messages), ts,
            )
            if err:
                raise HTTPException(
                    status_code=422,
                    detail={"error_type": err[0], "message": err[1]},
                )
            msg = make_message(
                role=body.role,
                content=body.content,
                timestamp=ts,
                source=body.source,
                reference_content=body.reference_content,
            )
            session.messages.append(msg)
            session.logger.log_sync(
                "chat.messages.add",
                payload={
                    "message_id": msg["id"],
                    "role": msg["role"],
                    "source": msg["source"],
                    "chars": len(body.content),
                },
            )
            payload = _serialize_messages(session)
            payload["message"] = msg
            return payload
    except SessionConflictError as exc:
        raise _session_conflict_to_http(exc) from exc
    except LookupError as exc:
        raise _lookup_error_to_http(exc) from exc


@router.put("/messages/{message_id}")
async def edit_message(message_id: str, body: _EditMessageRequest) -> dict[str, Any]:
    """Replace ``messages[id].content``.

    Other fields (role / timestamp / source / reference_content) are
    untouched — use ``PATCH /timestamp`` or a dedicated future endpoint
    for those.
    """
    store = get_session_store()
    try:
        async with store.session_operation("chat.messages.edit") as session:
            idx = find_message_index(session.messages, message_id)
            if idx < 0:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error_type": "MessageNotFound",
                        "message": f"消息 {message_id} 不存在。",
                    },
                )
            old = session.messages[idx]
            old_chars = len(old.get("content", "") or "")
            old["content"] = body.content
            session.logger.log_sync(
                "chat.messages.edit",
                payload={
                    "message_id": message_id,
                    "old_chars": old_chars,
                    "new_chars": len(body.content),
                },
            )
            return {"message": old, "count": len(session.messages)}
    except SessionConflictError as exc:
        raise _session_conflict_to_http(exc) from exc
    except LookupError as exc:
        raise _lookup_error_to_http(exc) from exc


@router.patch("/messages/{message_id}/timestamp")
async def patch_message_timestamp(
    message_id: str, body: _PatchTimestampRequest,
) -> dict[str, Any]:
    """Retroactively change a message's virtual timestamp.

    If the edited message is the **last** one, the session clock's
    cursor is also snapped to the new timestamp — the runtime's "clock
    resync" rule (see PLAN §时间轴与消息 timestamp).
    """
    store = get_session_store()
    try:
        async with store.session_operation("chat.messages.patch_timestamp") as session:
            idx = find_message_index(session.messages, message_id)
            if idx < 0:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error_type": "MessageNotFound",
                        "message": f"消息 {message_id} 不存在。",
                    },
                )
            new_ts = (
                _parse_iso(body.timestamp)
                if body.timestamp else session.clock.now()
            )
            # Retroactive retiming must keep the list monotonic relative
            # to its neighbours (the edited row itself is ignored — we're
            # about to overwrite its timestamp). Reject otherwise with
            # 422 so the UI can surface a specific toast ("新时间戳早于
            # 上一条") instead of silently committing a broken list.
            err = check_timestamp_monotonic(session.messages, idx, new_ts)
            if err:
                raise HTTPException(
                    status_code=422,
                    detail={"error_type": err[0], "message": err[1]},
                )
            session.messages[idx]["timestamp"] = new_ts.replace(
                microsecond=0,
            ).isoformat()
            clock_resynced = False
            if idx == len(session.messages) - 1:
                session.clock.set_now(new_ts)
                clock_resynced = True
            session.logger.log_sync(
                "chat.messages.patch_timestamp",
                payload={
                    "message_id": message_id,
                    "new_timestamp": session.messages[idx]["timestamp"],
                    "clock_resynced": clock_resynced,
                },
            )
            return {
                "message": session.messages[idx],
                "clock_resynced": clock_resynced,
                "clock": session.clock.to_dict(),
            }
    except SessionConflictError as exc:
        raise _session_conflict_to_http(exc) from exc
    except LookupError as exc:
        raise _lookup_error_to_http(exc) from exc


@router.delete("/messages/{message_id}")
async def delete_message(message_id: str) -> dict[str, Any]:
    """Remove one message by id. No cascading timestamp adjustments."""
    store = get_session_store()
    try:
        async with store.session_operation("chat.messages.delete") as session:
            idx = find_message_index(session.messages, message_id)
            if idx < 0:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error_type": "MessageNotFound",
                        "message": f"消息 {message_id} 不存在。",
                    },
                )
            removed = session.messages.pop(idx)
            session.logger.log_sync(
                "chat.messages.delete",
                payload={"message_id": message_id, "role": removed.get("role")},
            )
            return {"removed": removed, "count": len(session.messages)}
    except SessionConflictError as exc:
        raise _session_conflict_to_http(exc) from exc
    except LookupError as exc:
        raise _lookup_error_to_http(exc) from exc


@router.post("/messages/truncate")
async def truncate_messages(body: _TruncateRequest) -> dict[str, Any]:
    """Truncate the list. Powers the UI's "Re-run from here" action.

    Also rewinds ``session.clock.cursor`` to the last surviving
    message's timestamp (or clears it when the list is emptied) — the
    "Re-run from here 同时回滚时钟" rule from PLAN §时间轴.
    """
    store = get_session_store()
    try:
        async with store.session_operation("chat.messages.truncate") as session:
            if body.keep_id is None:
                removed = len(session.messages)
                session.messages.clear()
                session.clock.set_now(None)
            else:
                idx = find_message_index(session.messages, body.keep_id)
                if idx < 0:
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "error_type": "MessageNotFound",
                            "message": f"keep_id={body.keep_id} 不存在。",
                        },
                    )
                cut = idx + 1 if body.include else idx
                removed = len(session.messages) - cut
                del session.messages[cut:]
                # Rewind the cursor to the latest surviving timestamp.
                if session.messages:
                    try:
                        last_ts = _parse_iso(session.messages[-1]["timestamp"])
                        session.clock.set_now(last_ts)
                    except HTTPException:
                        # malformed legacy timestamp — leave cursor alone.
                        pass
                else:
                    session.clock.set_now(None)
            session.logger.log_sync(
                "chat.messages.truncate",
                payload={
                    "keep_id": body.keep_id,
                    "include": body.include,
                    "removed_count": removed,
                    "remaining_count": len(session.messages),
                },
            )
            return {
                "removed_count": removed,
                "count": len(session.messages),
                "clock": session.clock.to_dict(),
            }
    except SessionConflictError as exc:
        raise _session_conflict_to_http(exc) from exc
    except LookupError as exc:
        raise _lookup_error_to_http(exc) from exc


# ── inject system ────────────────────────────────────────────────────


@router.post("/inject_system")
async def inject_system(body: _InjectSystemRequest) -> dict[str, Any]:
    """Append an in-conversation ``role=system`` note without any LLM call.

    The note lands in ``session.messages`` with ``source=inject`` and
    becomes part of ``wire_messages`` on the next /send, so the target
    AI sees it as a mid-conversation instruction. Typical use: ``"(The
    character just received a text from an old friend.)"`` scenario
    nudges without having to hand-craft a full user line.
    """
    store = get_session_store()
    try:
        async with store.session_operation("chat.inject_system") as session:
            backend = get_chat_backend()
            msg = backend.inject_system(session, body.content)
            return {
                "message": msg,
                "count": len(session.messages),
            }
    except SessionConflictError as exc:
        raise _session_conflict_to_http(exc) from exc
    except LookupError as exc:
        raise _lookup_error_to_http(exc) from exc


# ── /send (SSE) ──────────────────────────────────────────────────────


def _sse_frame(event: dict[str, Any]) -> str:
    """Serialize one event dict as a single SSE frame."""
    return "data: " + json.dumps(event, ensure_ascii=False, default=str) + "\n\n"


async def _send_event_stream(
    body: _SendRequest,
) -> AsyncIterator[str]:
    """Async generator producing raw SSE lines for ``/chat/send``.

    All error paths yield a trailing ``{"event": "error", ...}`` frame
    and stop gracefully so the browser can render a helpful toast
    instead of seeing a half-dead EventSource.
    """
    if body.role not in {ROLE_USER, ROLE_SYSTEM}:
        yield _sse_frame({
            "event": "error",
            "error": {"type": "InvalidRole", "message": f"role={body.role!r} 仅接受 user / system。"},
        })
        return
    if body.source not in ALLOWED_SOURCES:
        yield _sse_frame({
            "event": "error",
            "error": {"type": "InvalidSource", "message": f"source={body.source!r} 不受支持。"},
        })
        return

    store = get_session_store()
    try:
        async with store.session_operation(
            "chat.send",
            state=SessionState.BUSY,
        ) as session:
            # Inline "time_advance" → clock.stage_next_turn(...). Consumed
            # immediately below inside OfflineChatBackend.stream_send.
            if body.time_absolute:
                try:
                    session.clock.stage_next_turn(absolute=_parse_iso(body.time_absolute))
                except HTTPException as http_exc:
                    yield _sse_frame({
                        "event": "error",
                        "error": {
                            "type": "InvalidTimestamp",
                            "message": http_exc.detail.get("message", "invalid timestamp"),
                        },
                    })
                    return
            elif body.time_advance_seconds:
                session.clock.stage_next_turn(
                    delta=timedelta(seconds=body.time_advance_seconds),
                )

            backend = get_chat_backend()
            try:
                async for event in backend.stream_send(
                    session,
                    user_content=body.content,
                    role=body.role,
                    source=body.source,
                ):
                    yield _sse_frame(event)
            except ChatConfigError as exc:
                yield _sse_frame({
                    "event": "error",
                    "error": {"type": exc.code, "message": exc.message},
                })
            except Exception as exc:  # noqa: BLE001 — last-chance safety net
                python_logger().exception(
                    "chat.send stream crashed (session=%s): %s",
                    session.id, exc,
                )
                yield _sse_frame({
                    "event": "error",
                    "error": {
                        "type": type(exc).__name__,
                        "message": f"流式发送失败: {exc}",
                    },
                })
    except SessionConflictError as exc:
        yield _sse_frame({
            "event": "error",
            "error": {
                "type": "SessionConflict",
                "message": str(exc),
                "state": exc.state.value,
                "busy_op": exc.busy_op,
            },
        })
    except LookupError as exc:
        yield _sse_frame({
            "event": "error",
            "error": {"type": "NoActiveSession", "message": str(exc)},
        })


@router.post("/send")
async def send_chat(body: _SendRequest) -> StreamingResponse:
    """Stream the target AI's response as ``text/event-stream``.

    Frontend consumes via a fetch+ReadableStream helper (EventSource can't
    carry a POST body). The response is always HTTP 200; structured
    errors travel inside the stream as ``{"event":"error", ...}`` frames.
    """
    headers = {
        # Disable proxy buffering so each delta reaches the browser live.
        # nginx / some reverse proxies honour this; dev server passes it
        # through harmlessly.
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(
        _send_event_stream(body),
        media_type="text/event-stream",
        headers=headers,
    )


# Convenience trailing-slash-free aliases are provided by the router
# prefix already; we don't register duplicates.

# Re-export the explicit role/source constants so other phase routers
# (``simuser``, ``script`` etc.) can share the validation vocabulary
# without reaching into ``chat_messages`` directly.
__all__ = ["router", "ROLE_SYSTEM", "ROLE_USER", "SOURCE_INJECT", "SOURCE_MANUAL"]
