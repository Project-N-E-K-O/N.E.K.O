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
* **P11** ``GET    /api/chat/simulate_user/styles``    — 风格预设列表.
* **P11** ``POST   /api/chat/simulate_user``           — 调 simuser LLM
                                                         生成一条"用户消息
                                                         草稿"; **不落盘**,
                                                         供 composer 填充后
                                                         再走 /chat/send.
* **P12** ``GET    /api/chat/script/templates``        — 合并 builtin/user
                                                         两个目录, 返回可用
                                                         脚本 meta 列表.
* **P12** ``POST   /api/chat/script/load``             — 加载指定脚本到会话;
                                                         body: ``{name}``.
                                                         应用 bootstrap, 初始
                                                         化 ``script_state``.
* **P12** ``POST   /api/chat/script/unload``           — 清空 ``script_state``.
* **P12** ``GET    /api/chat/script/state``            — 当前脚本加载状态.
* **P12** ``POST   /api/chat/script/next``             — SSE; 跑一个 user
                                                         turn (自动累积 expected
                                                         到下次 AI 回复).
* **P12.5** ``GET    /api/chat/script/templates/{name}`` — 读模板详情 (编辑器用).
* **P12.5** ``POST   /api/chat/script/templates``        — 保存用户模板.
* **P12.5** ``DELETE /api/chat/script/templates/{name}`` — 删用户模板 (builtin 不可删).
* **P12.5** ``POST   /api/chat/script/templates/duplicate`` — 复制模板到用户目录.
* **P12** ``POST   /api/chat/script/run_all``          — SSE; 循环 Next 直到
                                                         脚本耗尽或 error.

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
from tests.testbench.pipeline.script_runner import (
    ScriptError,
    advance_one_user_turn,
    delete_user_template as delete_user_script_template,
    describe_script_state,
    duplicate_builtin_to_user as duplicate_script_template,
    list_templates as list_script_templates,
    load_script_into_session,
    read_template as read_script_template,
    run_all_turns,
    save_user_template as save_user_script_template,
    unload_script_from_session,
)
from tests.testbench.pipeline.simulated_user import (
    SimUserError,
    generate_simuser_message,
    list_style_presets,
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


# ── /simulate_user (P11) ─────────────────────────────────────────────


class _SimulateUserRequest(BaseModel):
    """Body for ``POST /api/chat/simulate_user``.

    All fields are optional; 空都给就是 "friendly 预设 + 不带额外人设 +
    没有临时提示" 的默认行为.
    """

    style: str | None = Field(
        default=None,
        description="风格 key (friendly / curious / picky / emotional); 未知值回落到 friendly.",
    )
    user_persona_prompt: str = Field(
        default="",
        description="自定义人设/背景, 追加到 system prompt.",
    )
    extra_hint: str = Field(
        default="",
        description="单次临时指示, 只对本次生成生效, 不写回任何持久状态.",
    )


@router.get("/simulate_user/styles")
async def simulate_user_styles() -> dict[str, Any]:
    """Return the closed-set style presets for the composer UI.

    Shape: ``{styles: [{id, prompt}, ...], default: str}``. 前端 i18n
    负责把 ``id`` 映射到中文 label, 这里只吐 id + prompt 文本.
    """
    from tests.testbench.pipeline.simulated_user import DEFAULT_STYLE

    return {"styles": list_style_presets(), "default": DEFAULT_STYLE}


@router.post("/simulate_user")
async def simulate_user(body: _SimulateUserRequest) -> dict[str, Any]:
    """Generate one user-message draft; **do not** persist it.

    Rationale: PLAN §P11 约定"生成到 composer textarea 供编辑再发送" —
    所以本端点**只**跑 LLM 拿文本返回, 不动 ``session.messages``、不动
    ``session.clock``. 用户编辑完后再点 Send, 走 ``/api/chat/send``
    (source=simuser) 那条老路写入会话、推进时钟、同步前端消息流.

    会话锁: 使用 ``BUSY`` 状态锁, 原因是 (a) SimUser 与 Chat.send 共享
    session.messages 读视角, 并发调 LLM 会让前端的流式 UI 和 composer
    互相干扰; (b) 后续 P12/P13 脚本/自动对话会复用同一锁粒度, 这里提前
    对齐. 代价是如果正在 /chat/send 流式进行中调 /simulate_user 会返回
    409, 前端 composer 自行禁用按钮避免误触.

    错误码映射:
        * 404 NoActiveSession — 还没建会话.
        * 409 SessionConflict — 并发调用.
        * 412 SimuserModelNotConfigured / SimuserApiKeyMissing — simuser
          组 base_url/model/api_key 缺失.
        * 502 LlmFailed — LLM 调用真的挂了 (超时/上游 500/认证失败).
    """
    store = get_session_store()
    try:
        async with store.session_operation(
            "chat.simulate_user",
            state=SessionState.BUSY,
        ) as session:
            try:
                draft = await generate_simuser_message(
                    session,
                    style=body.style,
                    user_persona_prompt=body.user_persona_prompt,
                    extra_hint=body.extra_hint,
                )
            except SimUserError as exc:
                raise HTTPException(
                    status_code=exc.status,
                    detail={"error_type": exc.code, "message": exc.message},
                ) from exc
            return draft.to_dict()
    except SessionConflictError as exc:
        raise _session_conflict_to_http(exc) from exc
    except LookupError as exc:
        raise _lookup_error_to_http(exc) from exc


# ── /script (P12) ────────────────────────────────────────────────────


class _ScriptLoadRequest(BaseModel):
    """Body for ``POST /api/chat/script/load``."""

    name: str = Field(..., description="脚本模板 name (对应 JSON 里的 name 字段).")


def _script_error_to_http(exc: ScriptError) -> HTTPException:
    """ScriptError → HTTPException, 把可能挂载的 ``errors`` 字段一并透出.

    P12.5 保存/校验接口需要把字段级错误清单透到前端红框高亮. ScriptError 的
    默认签名只接 message, 422 的场景下我们在 ``save_user_template`` 里 attach
    了 ``exc.errors``, 这里透传.
    """
    detail: dict[str, Any] = {"error_type": exc.code, "message": exc.message}
    errors = getattr(exc, "errors", None)
    if errors:
        detail["errors"] = errors
    return HTTPException(status_code=exc.status, detail=detail)


@router.get("/script/templates")
async def script_templates() -> dict[str, Any]:
    """Return merged builtin + user dialog-template meta list.

    Shape: ``{templates: [{name, description, user_persona_hint, turns_count,
    source, path, overriding_builtin}], count: int}``. 前端 composer 用
    ``name`` / ``description`` / ``turns_count`` / ``source`` 渲染下拉;
    ``path`` 仅供 Diagnostics 未来链接到文件位置.

    不需要会话 — 刚启动 UI 还没建 session 时就能预览可用脚本.
    """
    templates = list_script_templates()
    return {"templates": templates, "count": len(templates)}


# ── P12.5: Setup → Scripts 子页 CRUD ──────────────────────────────
#
# 这五个端点都不需要 session (脚本模板是全局资产, 测试人员没建会话也可以
# 编辑模板). 保持纯 IO, 不碰 session_store.


class _ScriptSaveRequest(BaseModel):
    """Body for ``POST /api/chat/script/templates``.

    模型故意松散 — 详细字段校验归 ``validate_template_dict`` 做软校验, UI
    想要"保存半成品"时有更友好的错误清单反馈. pydantic 只拦 "不是 dict"
    / "name 都没传" 这种粗粒度.
    """

    name: str = Field(..., description="模板 name (= 文件名).")
    description: str | None = Field(default=None)
    user_persona_hint: str | None = Field(default=None)
    bootstrap: dict[str, Any] | None = Field(default=None)
    turns: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"extra": "allow"}


class _ScriptDuplicateRequest(BaseModel):
    """Body for ``POST /api/chat/script/templates/duplicate``."""

    source_name: str = Field(..., description="要复制的源模板 name (builtin 或 user 都行).")
    target_name: str = Field(..., description="新 user 模板的 name.")
    overwrite: bool = Field(
        default=False,
        description="target_name 已存在的 user 模板时是否覆盖. 默认 False → 409.",
    )


@router.get("/script/templates/{name}")
async def script_template_read(name: str) -> dict[str, Any]:
    """Return the active template + builtin/user co-existence flags.

    Shape: ``{template, has_builtin, has_user, overriding_builtin}``.
    前端编辑器打开时先拉一次, 决定 readonly (builtin-only) / 可编辑 (user).
    """
    try:
        details = read_script_template(name)
    except ScriptError as exc:
        raise _script_error_to_http(exc) from exc
    return {
        "template": details["active"],
        "has_builtin": details["has_builtin"],
        "has_user": details["has_user"],
        "overriding_builtin": details["overriding_builtin"],
    }


@router.post("/script/templates")
async def script_template_save(body: _ScriptSaveRequest) -> dict[str, Any]:
    """Create or overwrite a user dialog template.

    - 422 ScriptSchemaInvalid (detail 含 ``errors`` 清单) — 字段校验失败.
    - 200 ``{template, overriding_builtin, path}`` — 写盘成功.

    不需要 session. 写完如果跟当前 session 加载中的脚本同 name, UI 端应该
    自己决定是否提示 "当前加载的剧本已被改写" — 后端不自动失效, 因为
    session.script_state.turns 是加载时的 snapshot, 改磁盘不影响已跑起来的
    脚本 (语义跟"锁定快照"一致, 避免跑到一半切底).
    """
    payload = body.model_dump(exclude_unset=False)
    try:
        return save_user_script_template(payload)
    except ScriptError as exc:
        raise _script_error_to_http(exc) from exc


@router.delete("/script/templates/{name}")
async def script_template_delete(name: str) -> dict[str, Any]:
    """Delete a user template by name.

    - 404 ScriptNotFound — user 目录没有这个 name (builtin 本来就不能删).
    - 200 ``{deleted_name, resurfaces_builtin, warnings}``.

    若当前 session 正加载同名脚本, 塞一条 warning 提示"磁盘被删, 内存中
    的快照还会继续跑完, 但下次 list 看不到了".
    """
    warnings: list[str] = []
    try:
        result = delete_user_script_template(name)
    except ScriptError as exc:
        raise _script_error_to_http(exc) from exc
    sess = get_session_store().get()
    if sess is not None and sess.script_state is not None:
        if sess.script_state.get("template_name") == name:
            warnings.append(
                f"当前会话正加载脚本 {name!r} 的内存快照 — 磁盘版本已删, 可继续"
                f" Next / Run all 跑完当前快照, 但重新 [刷新列表] 就看不到了."
            )
    result["warnings"] = warnings
    return result


@router.post("/script/templates/duplicate")
async def script_template_duplicate(body: _ScriptDuplicateRequest) -> dict[str, Any]:
    """Copy a template (usually builtin) into the user directory under a new name.

    200 返回跟 save 一样 ``{template, overriding_builtin, path}``.  409 =
    target_name 已被占且没带 overwrite=true.
    """
    try:
        return duplicate_script_template(
            body.source_name, body.target_name, overwrite=body.overwrite,
        )
    except ScriptError as exc:
        raise _script_error_to_http(exc) from exc


@router.post("/script/load")
async def script_load(body: _ScriptLoadRequest) -> dict[str, Any]:
    """Install ``body.name`` as the session's current script.

    副作用:
        * 读模板 → 应用 bootstrap (若 ``session.messages`` 为空) → 初始化
          ``session.script_state``. 已有脚本加载时直接覆盖, 不要求先 unload.
        * ``warnings`` 里可能包含 "bootstrap 跳过 (已有消息)" 等提示, 前端
          弹 toast 让测试人员知道.

    错误码映射:
        * 404 NoActiveSession / ScriptNotFound.
        * 409 SessionConflict.
        * 422 ScriptSchemaInvalid — JSON 读上来但 schema 错.
    """
    store = get_session_store()
    try:
        async with store.session_operation("chat.script.load") as session:
            try:
                state, warnings = load_script_into_session(session, body.name)
            except ScriptError as exc:
                raise _script_error_to_http(exc) from exc
            return {"script_state": state, "warnings": warnings}
    except SessionConflictError as exc:
        raise _session_conflict_to_http(exc) from exc
    except LookupError as exc:
        raise _lookup_error_to_http(exc) from exc


@router.post("/script/unload")
async def script_unload() -> dict[str, Any]:
    """Clear ``session.script_state``. No-op if no script is loaded."""
    store = get_session_store()
    try:
        async with store.session_operation("chat.script.unload") as session:
            unload_script_from_session(session)
            return {"script_state": None}
    except SessionConflictError as exc:
        raise _session_conflict_to_http(exc) from exc
    except LookupError as exc:
        raise _lookup_error_to_http(exc) from exc


@router.get("/script/state")
async def script_state() -> dict[str, Any]:
    """Return the current ``script_state`` or ``null`` if nothing loaded."""
    session = _require_session()
    return {"script_state": describe_script_state(session)}


async def _script_next_event_stream() -> AsyncIterator[str]:
    """SSE generator for ``/script/next`` — exactly one user turn."""
    store = get_session_store()
    try:
        async with store.session_operation(
            "chat.script.next",
            state=SessionState.BUSY,
        ) as session:
            try:
                async for event in advance_one_user_turn(session):
                    yield _sse_frame(event)
            except ScriptError as exc:
                yield _sse_frame({
                    "event": "error",
                    "error": {"type": exc.code, "message": exc.message},
                })
            except Exception as exc:  # noqa: BLE001
                python_logger().exception(
                    "script.next stream crashed (session=%s): %s",
                    session.id, exc,
                )
                yield _sse_frame({
                    "event": "error",
                    "error": {
                        "type": type(exc).__name__,
                        "message": f"脚本下一轮执行失败: {exc}",
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


async def _script_run_all_event_stream() -> AsyncIterator[str]:
    """SSE generator for ``/script/run_all`` — loop until exhausted or error."""
    store = get_session_store()
    try:
        async with store.session_operation(
            "chat.script.run_all",
            state=SessionState.BUSY,
        ) as session:
            try:
                async for event in run_all_turns(session):
                    yield _sse_frame(event)
            except ScriptError as exc:
                yield _sse_frame({
                    "event": "error",
                    "error": {"type": exc.code, "message": exc.message},
                })
            except Exception as exc:  # noqa: BLE001
                python_logger().exception(
                    "script.run_all stream crashed (session=%s): %s",
                    session.id, exc,
                )
                yield _sse_frame({
                    "event": "error",
                    "error": {
                        "type": type(exc).__name__,
                        "message": f"脚本连续执行失败: {exc}",
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


def _sse_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }


@router.post("/script/next")
async def script_next() -> StreamingResponse:
    """Stream one scripted user turn as ``text/event-stream``.

    事件 (在 :func:`advance_one_user_turn` 文档里详述): 用 user / assistant_start
    / delta / assistant / done / usage / wire_built 与手动 /chat/send 保持一致,
    另追加 script_turn_warnings / script_turn_done / script_exhausted.

    整条 SSE 期间持有会话 BUSY 锁, 防止 /chat/send 同时改 session.messages.
    """
    return StreamingResponse(
        _script_next_event_stream(),
        media_type="text/event-stream",
        headers=_sse_headers(),
    )


@router.post("/script/run_all")
async def script_run_all() -> StreamingResponse:
    """Stream a full script run until ``script_exhausted`` or ``error``.

    前端一旦看到 ``script_exhausted`` / ``error`` 就认定循环结束.
    """
    return StreamingResponse(
        _script_run_all_event_stream(),
        media_type="text/event-stream",
        headers=_sse_headers(),
    )


# Convenience trailing-slash-free aliases are provided by the router
# prefix already; we don't register duplicates.

# Re-export the explicit role/source constants so other phase routers
# (``simuser``, ``script`` etc.) can share the validation vocabulary
# without reaching into ``chat_messages`` directly.
__all__ = ["router", "ROLE_SYSTEM", "ROLE_USER", "SOURCE_INJECT", "SOURCE_MANUAL"]
