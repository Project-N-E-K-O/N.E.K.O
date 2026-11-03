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
* **P11** ``GET    /api/chat/simulate_user/styles``    — list style presets.
* **P11** ``POST   /api/chat/simulate_user``           — invoke simuser LLM
                                                         to generate one user-message
                                                         draft; **no persistence**,
                                                         fill the composer, then
                                                         send through /chat/send.
* **P12** ``GET    /api/chat/script/templates``        — merge builtin/user
                                                         directories and return available
                                                         script metadata.
* **P12** ``POST   /api/chat/script/load``             — load a script into the session;
                                                         body: ``{name}``.
                                                         apply bootstrap and initialize
                                                         ``script_state``.
* **P12** ``POST   /api/chat/script/unload``           — clear ``script_state``.
* **P12** ``GET    /api/chat/script/state``            — current script-loading state.
* **P12** ``POST   /api/chat/script/next``             — SSE; run one user
                                                         turn (accumulate expected
                                                         until the next AI reply).
* **P12.5** ``GET    /api/chat/script/templates/{name}`` — read template details for the editor.
* **P12.5** ``POST   /api/chat/script/templates``        — save a user template.
* **P12.5** ``DELETE /api/chat/script/templates/{name}`` — delete a user template (builtins cannot be deleted).
* **P12.5** ``POST   /api/chat/script/templates/duplicate`` — copy a template into the user directory.
* **P12** ``POST   /api/chat/script/run_all``          — SSE; repeat Next until
                                                         script exhaustion or error.
* **P13** ``POST   /api/chat/auto_dialog/start``       — SSE; dual-AI automatic dialogue,
                                                         holding BUSY throughout; events
                                                         include turn_begin /
                                                         simuser_done / target
                                                         stream passthrough / turn_done
                                                         / paused / resumed /
                                                         stopped / error.
* **P13** ``POST   /api/chat/auto_dialog/pause``       — graceful pause (finish the current
                                                         step without entering the
                                                         next); no lock.
* **P13** ``POST   /api/chat/auto_dialog/resume``      — resume; no lock.
* **P13** ``POST   /api/chat/auto_dialog/stop``        — request termination; no lock.
* **P13** ``GET    /api/chat/auto_dialog/state``       — observe runtime state (after page
                                                         refresh, the frontend uses auto_
                                                         state to decide SSE reattachment).
                                                         SSE).

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
from tests.testbench.pipeline.messages_writer import append_message
from tests.testbench.pipeline.auto_dialog import (
    AutoDialogConfig,
    AutoDialogError,
    describe_auto_state,
    request_pause as request_auto_pause,
    request_resume as request_auto_resume,
    request_stop as request_auto_stop,
    run_auto_dialog,
)
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
from tests.testbench.pipeline.snapshot_store import capture_safe as _snapshot_capture
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

    # DEBUG level: this endpoint is a pure read-back called on every UI
    # refresh, so at INFO it dominates the log (~32% of all entries in
    # testing). Keep the payload shape intact so tuning DEBUG logs on
    # still surfaces persona/template/warnings drift when investigating.
    session.logger.debug(
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

    2026-04-22 Day 8 acceptance feedback #3: ``content`` becomes nullable. After rerun-from-user
    truncation leaves a trailing user, users expect a direct AI response to that existing message rather than
    typing another user message. Empty strings use ``stream_send(user_content=None)`` in the pipeline's
    LLM-only path, avoiding consecutive user messages. Backend ``chat_runner`` verifies
    session.messages ends with user before running; otherwise return 422.
    """

    content: str = Field(
        default="",
        description=(
            "User message text. 空字符串 = '不追加新消息, 直接让 AI 对末尾"
            "已有的 user 消息生成回复' (常用于 rerun-from-user 之后). 末尾不是"
            "user 时后端会 422."
        ),
    )
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
            # check_timestamp_monotonic was already called above (line ~384),
            # so the append cannot violate monotonicity here. Going through
            # the chokepoint anyway satisfies .cursor/rules/single-append-message
            # and keeps one code path. on_violation="raise" matches the existing
            # 422 UX for the manual-add endpoint. We discard the AppendResult
            # since raise mode never populates ``.coerced``.
            append_message(session, msg, on_violation="raise")
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
            _snapshot_capture(session, trigger="edit")
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
            _snapshot_capture(session, trigger="edit")
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
    resync" rule (see PLAN §时间轴与消息 timestamp, timeline and message timestamps).
    """  # noqa: DOCSTRING_CJK
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
            # 422 so the UI can surface a specific toast (new timestamp precedes
            # the previous one) instead of silently committing a broken list.
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
            _snapshot_capture(session, trigger="edit")
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
    "Re-run from here also rewinds the clock" rule from PLAN §时间轴 (timeline).
    """  # noqa: DOCSTRING_CJK
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
            _snapshot_capture(session, trigger="edit")
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
            # r5 T8: prompt-injection audit — inject_system was the only
            # *free-form tester text → wire* path without detection. The
            # content is role=system (not user), so the normal /send scan
            # never sees it. Scan here *before* the backend appends so
            # the audit record lands even if append fails downstream.
            try:
                from tests.testbench.pipeline import injection_audit as _ia
                _ia.scan_and_record(
                    body.content or "",
                    source="chat.inject_system",
                    session_id=getattr(session, "id", None),
                    extra={"content_length": len(body.content or "")},
                )
            except Exception as exc:  # noqa: BLE001
                python_logger().warning(
                    "inject_system injection_audit skipped: %s: %s",
                    type(exc).__name__, exc,
                )

            backend = get_chat_backend()
            msg = backend.inject_system(session, body.content)
            _snapshot_capture(session, trigger="edit")
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

            # 2026-04-23 P25 Day 2 polish r5: triage Send clicks with an empty input box
            # (or whitespace-only content):
            #
            #   - Final message is user: use Day 8 #3's valid resend-the-last-user's
            #     LLM-response path (user_content=None).
            #   - Final message is not user (or session empty): previously (r4 and earlier)
            #     pipeline ValueError -> SSE error frame -> UI error
            #     badge + Errors page all produced false signals. User feedback: this should
            #     not count as an error, only warn that empty messages are forbidden,
            #     while still recording it in logs. Degrade to:
            #       (a) emit an SSE `warning` frame (type=empty_content_
            #           ignored) to show a warning toast, **bypassing error handling**;
            #       (b) diagnostics_store.record_internal records
            #           CHAT_SEND_EMPTY_IGNORED (info), visible in logs but
            #           excluded from Errors-page error counts;
            #       (c) return immediately without invoking the LLM or changing session.messages.
            content_stripped = (body.content or "").strip()
            is_empty_click = (not content_stripped)
            if is_empty_click:
                tail_role = None
                tail_empty = not session.messages
                if session.messages:
                    tail_role = session.messages[-1].get("role")
                if tail_role != ROLE_USER:
                    # Not a valid resend: emit warning and return early.
                    from tests.testbench.pipeline import diagnostics_store as _ds
                    from tests.testbench.pipeline.diagnostics_ops import (
                        DiagnosticsOp,
                    )
                    try:
                        _ds.record_internal(
                            DiagnosticsOp.CHAT_SEND_EMPTY_IGNORED,
                            "空消息发送请求被忽略 (textarea 无内容, "
                            "且 session.messages 末尾不是 user).",
                            level="info",
                            session_id=getattr(session, "id", None),
                            detail={
                                "role": body.role,
                                "source": body.source,
                                "tail_role": tail_role,
                                "tail_empty": tail_empty,
                            },
                        )
                    except Exception as exc:  # noqa: BLE001 — audit must not block streaming.
                        python_logger().warning(
                            "chat.send empty-ignore audit record failed: "
                            "%s: %s", type(exc).__name__, exc,
                        )
                    yield _sse_frame({
                        "event": "warning",
                        "warning": {
                            "type": "empty_content_ignored",
                            "tail_role": tail_role,
                            "tail_empty": tail_empty,
                            "message": "消息内容不能为空",
                        },
                    })
                    return

            backend = get_chat_backend()
            stream_ok = False
            try:
                # 2026-04-22 Day 8 #3: empty string -> None, triggering the pipeline's
                # LLM-only reply to the existing trailing user message. The pipeline verifies
                # the final role is user; r5 pre-triage above guarantees the None path
                # is valid here.
                user_content_arg = body.content if content_stripped else None
                async for event in backend.stream_send(
                    session,
                    user_content=user_content_arg,
                    role=body.role,
                    source=body.source,
                ):
                    yield _sse_frame(event)
                stream_ok = True
            except ChatConfigError as exc:
                yield _sse_frame({
                    "event": "error",
                    "error": {"type": exc.code, "message": exc.message},
                })
            except ValueError as exc:
                # Pipeline prechecks (e.g. stream_send(user_content=None)
                # requiring a trailing user) raise ValueError. Give the frontend a friendly error_type.
                yield _sse_frame({
                    "event": "error",
                    "error": {
                        "type": "InvalidSendState",
                        "message": str(exc),
                    },
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
            # P18: snapshot after a successful round-trip. On error /
            # abort we skip — the partial state may be inconsistent and
            # the prior snapshot is still a valid rewind target.
            if stream_ok:
                _snapshot_capture(session, trigger="send")
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

    All fields are optional; omitting everything defaults to the friendly preset with no extra persona
    and no temporary guidance.
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

    Shape: ``{styles: [{id, prompt}, ...], default: str}``. Frontend i18n
    maps ``id`` to Chinese labels; expose only id + prompt text here.
    """
    from tests.testbench.pipeline.simulated_user import DEFAULT_STYLE

    return {"styles": list_style_presets(), "default": DEFAULT_STYLE}


@router.post("/simulate_user")
async def simulate_user(body: _SimulateUserRequest) -> dict[str, Any]:
    """Generate one user-message draft; **do not** persist it.

    Rationale: PLAN §P11 specifies generating into the composer textarea for editing before sending;
    this endpoint therefore **only** invokes the LLM and returns text, leaving ``session.messages`` and
    ``session.clock`` untouched. After editing, users click Send through ``/api/chat/send``
    (source=simuser), the existing path that writes the session, advances time, and synchronizes frontend messages.

    Session lock: use ``BUSY`` because (a) SimUser and Chat.send share a read view of
    session.messages, and concurrent LLM calls would interfere with frontend streaming and the composer;
    (b) future P12/P13 script/automatic dialogue shares this locking granularity, so align
    early. The cost is 409 for /simulate_user during an active /chat/send stream;
    the frontend composer disables the button to prevent accidental calls.

    Error-code mapping:
        * 404 NoActiveSession — no session created yet.
        * 409 SessionConflict — concurrent invocation.
        * 412 SimuserModelNotConfigured / SimuserApiKeyMissing — simuser
          group missing base_url/model/api_key.
        * 502 LlmFailed — actual LLM failure (timeout/upstream 500/authentication failure).
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
    """ScriptError -> HTTPException, exposing an attached ``errors`` field too.

    P12.5 save/validation endpoints expose field-level errors for frontend red highlights. ScriptError's
    default signature accepts only message; for 422 cases ``save_user_template`` attaches
    ``exc.errors``, passed through here.
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
    source, path, overriding_builtin}], count: int}``. The frontend composer renders dropdowns using
    ``name`` / ``description`` / ``turns_count`` / ``source``;
    ``path`` is reserved for future Diagnostics links to file locations.

    No session required: preview available scripts immediately after UI startup, before session creation.
    """
    templates = list_script_templates()
    return {"templates": templates, "count": len(templates)}


# ── P12.5: Setup -> Scripts sub-page CRUD ──
#
# These five endpoints need no session: script templates are global assets and testers may edit
# them before creating sessions. Keep pure I/O without session_store access.


class _ScriptSaveRequest(BaseModel):
    """Body for ``POST /api/chat/script/templates``.

    Deliberately permissive model: ``validate_template_dict`` provides detailed soft field validation, giving the UI
    friendlier error lists when saving unfinished work. pydantic catches only coarse failures such as non-dict input
    or an entirely missing name.
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
    The frontend editor fetches once on opening to choose readonly (builtin-only) or editable (user).
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

    - 422 ScriptSchemaInvalid (detail includes ``errors`` list) — field validation failure.
    - 200 ``{template, overriding_builtin, path}`` — persisted successfully.

    No session required. When the written name matches the current session's loaded script, the UI decides
    whether to warn that the loaded script changed. Backend state is not automatically invalidated because
    session.script_state.turns is a load-time snapshot; disk changes do not affect running
    scripts, preserving locked-snapshot semantics without switching content mid-run.
    """
    payload = body.model_dump(exclude_unset=False)
    try:
        return save_user_script_template(payload)
    except ScriptError as exc:
        raise _script_error_to_http(exc) from exc


@router.delete("/script/templates/{name}")
async def script_template_delete(name: str) -> dict[str, Any]:
    """Delete a user template by name.

    - 404 ScriptNotFound — name absent from user directory (builtins cannot be deleted).
    - 200 ``{deleted_name, resurfaces_builtin, warnings}``.

    If the session currently loads a same-named script, add a warning: disk content was deleted, but the in-memory
    snapshot will continue to completion; the next listing will omit it.
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

    200 returns ``{template, overriding_builtin, path}``, as save does. 409 means
    target_name is occupied without overwrite=true.
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

    Side effects:
        * Read template -> apply bootstrap if ``session.messages`` is empty -> initialize
          ``session.script_state``. Overwrite loaded scripts directly without requiring unload first.
        * ``warnings`` may include bootstrap skipped because messages already exist; the frontend
          shows a toast to inform testers.

    Error-code mapping:
        * 404 NoActiveSession / ScriptNotFound.
        * 409 SessionConflict.
        * 422 ScriptSchemaInvalid — JSON readable but schema invalid.
    """
    store = get_session_store()
    try:
        async with store.session_operation("chat.script.load") as session:
            try:
                state, warnings = load_script_into_session(session, body.name)
            except ScriptError as exc:
                raise _script_error_to_http(exc) from exc
            _snapshot_capture(session, trigger="script_load")
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
            stream_ok = False
            try:
                async for event in advance_one_user_turn(session):
                    yield _sse_frame(event)
                stream_ok = True
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
            # A scripted turn is semantically one round-trip, same as
            # manual /chat/send — reuse the ``send`` trigger so rapid
            # scripted turns debounce together like manual sends do.
            if stream_ok:
                _snapshot_capture(session, trigger="send")
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
            stream_ok = False
            try:
                async for event in run_all_turns(session):
                    yield _sse_frame(event)
                stream_ok = True
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
            # P18: one snapshot at the end of the full scripted run.
            # Individual turns inside the loop are already debounced
            # down to one ``send`` trigger each; the final ``script_run_all``
            # trigger marks the "script done" boundary distinctly so
            # users can rewind to "right before I fired run_all".
            if stream_ok:
                _snapshot_capture(session, trigger="script_run_all")
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

    Events (detailed in :func:`advance_one_user_turn`): user / assistant_start
    / delta / assistant / done / usage / wire_built match manual /chat/send,
    with additional script_turn_warnings / script_turn_done / script_exhausted.

    Hold session BUSY for the entire SSE stream, preventing concurrent /chat/send changes to session.messages.
    """
    return StreamingResponse(
        _script_next_event_stream(),
        media_type="text/event-stream",
        headers=_sse_headers(),
    )


@router.post("/script/run_all")
async def script_run_all() -> StreamingResponse:
    """Stream a full script run until ``script_exhausted`` or ``error``.

    The frontend considers the loop complete upon ``script_exhausted`` / ``error``.
    """
    return StreamingResponse(
        _script_run_all_event_stream(),
        media_type="text/event-stream",
        headers=_sse_headers(),
    )


# ── /auto_dialog (P13) ──────────────────────────────────────────────


def _auto_error_to_http(exc: AutoDialogError) -> HTTPException:
    """AutoDialogError -> HTTPException. Follow script/simuser error wrapping,
    letting frontend error_bus select toast wording by ``detail.error_type``.

    P24 Day 7 (§12.3.F): when ``exc.errors`` contains batch-validation errors, attach an additional
    ``errors: list[str]`` field for frontend ``auto_banner`` to render a collapsible multiline panel,
    avoiding 280-character toast truncation and making users parse a combined "err1; err2; err3"
    string unnecessary.
    """
    detail: dict[str, Any] = {"error_type": exc.code, "message": exc.message}
    if exc.errors:
        detail["errors"] = list(exc.errors)
    return HTTPException(status_code=exc.status, detail=detail)


class _AutoDialogStartRequest(BaseModel):
    """Body for ``POST /api/chat/auto_dialog/start``.

    All optional fields receive permissive initial pydantic validation; detailed ranges/presets are checked by
    :meth:`AutoDialogConfig.from_request`, providing Chinese errors and centrally defined error
    codes in ``AutoDialogError`` without translating pydantic errors.
    """

    total_turns: int = Field(..., ge=1, description="要跑的 target 回复总数.")
    simuser_style: str = Field(..., description="SimUser 风格预设 key.")
    step_mode: str = Field(default="off", description="fixed / off.")
    step_seconds: int | None = Field(default=None)
    simuser_persona_hint: str = Field(default="")
    simuser_extra_hint: str = Field(default="")


async def _auto_dialog_event_stream(body: _AutoDialogStartRequest) -> AsyncIterator[str]:
    """SSE generator for ``/auto_dialog/start``.

    Hold BUSY throughout, matching script/run_all. The pause/resume/stop
    control endpoints **take no lock**, communicating only through asyncio
    Events in ``session.auto_state``; taking the lock would deadlock with this function.

    Error paths: invalid configuration (InvalidConfig), session conflict (SessionConflict), or an
    existing running instance (AutoAlreadyRunning) emit SSE error frames immediately followed by
    stopped(reason=error), letting one frontend error channel (error bus + modal
    toast) handle every case.
    """
    store = get_session_store()
    try:
        config = AutoDialogConfig.from_request(body.model_dump())
    except AutoDialogError as exc:
        yield _sse_frame({
            "event": "error",
            "error": {"type": exc.code, "message": exc.message},
        })
        yield _sse_frame({
            "event": "stopped",
            "reason": "error",
            "completed_turns": 0,
            "total_turns": getattr(body, "total_turns", 0) or 0,
        })
        return

    try:
        async with store.session_operation(
            "chat.auto_dialog.start",
            state=SessionState.BUSY,
        ) as session:
            stream_ok = False
            try:
                async for event in run_auto_dialog(session, config):
                    yield _sse_frame(event)
                stream_ok = True
            except AutoDialogError as exc:
                yield _sse_frame({
                    "event": "error",
                    "error": {"type": exc.code, "message": exc.message},
                })
                yield _sse_frame({
                    "event": "stopped",
                    "reason": "error",
                    "completed_turns": 0,
                    "total_turns": config.total_turns,
                })
            except Exception as exc:  # noqa: BLE001
                python_logger().exception(
                    "auto_dialog stream crashed (session=%s): %s",
                    session.id, exc,
                )
                yield _sse_frame({
                    "event": "error",
                    "error": {
                        "type": type(exc).__name__,
                        "message": f"自动对话执行失败: {exc}",
                    },
                })
                yield _sse_frame({
                    "event": "stopped",
                    "reason": "error",
                    "completed_turns": 0,
                    "total_turns": config.total_turns,
                })
            # P18: one snapshot when the auto-dialog exits cleanly
            # (done / user_stop). Per-turn snapshots inside the loop
            # would flood the timeline — the loop can run dozens of
            # turns. One ``auto_dialog_start`` at the end is a clean
            # rewind target for "undo the whole auto run".
            if stream_ok:
                _snapshot_capture(session, trigger="auto_dialog_start")
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
        yield _sse_frame({
            "event": "stopped",
            "reason": "error",
            "completed_turns": 0,
            "total_turns": config.total_turns,
        })
    except LookupError as exc:
        yield _sse_frame({
            "event": "error",
            "error": {"type": "NoActiveSession", "message": str(exc)},
        })
        yield _sse_frame({
            "event": "stopped",
            "reason": "error",
            "completed_turns": 0,
            "total_turns": config.total_turns,
        })


@router.post("/auto_dialog/start")
async def auto_dialog_start(body: _AutoDialogStartRequest) -> StreamingResponse:
    """Start a double-AI auto-dialog; stream SSE until stop / completion.

    Lock granularity: hold BUSY for the whole SSE stream; pause/resume/stop change runtime flags through separate endpoints.
    """
    return StreamingResponse(
        _auto_dialog_event_stream(body),
        media_type="text/event-stream",
        headers=_sse_headers(),
    )


@router.post("/auto_dialog/pause")
async def auto_dialog_pause() -> dict[str, Any]:
    """Request graceful pause: finish the current step without starting the next.

    Take no session lock (start already holds it); access ``session.auto_state`` directly and call
    ``running_event.clear()``. No running instance -> 409.
    """
    store = get_session_store()
    try:
        session = store.require()
    except LookupError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error_type": "NoActiveSession", "message": str(exc)},
        ) from exc
    try:
        return request_auto_pause(session)
    except AutoDialogError as exc:
        raise _auto_error_to_http(exc) from exc


@router.post("/auto_dialog/resume")
async def auto_dialog_resume() -> dict[str, Any]:
    """Resume a paused auto-dialog. Idempotent: set has no side effects when not paused."""
    store = get_session_store()
    try:
        session = store.require()
    except LookupError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error_type": "NoActiveSession", "message": str(exc)},
        ) from exc
    try:
        return request_auto_resume(session)
    except AutoDialogError as exc:
        raise _auto_error_to_http(exc) from exc


@router.post("/auto_dialog/stop")
async def auto_dialog_stop() -> dict[str, Any]:
    """Request stop: the generator breaks at its next loop header.

    Finish the current step gracefully and retain persisted messages. The runner emits the actual stopped
    event over SSE; this endpoint merely signals the runner to stop.
    """
    store = get_session_store()
    try:
        session = store.require()
    except LookupError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error_type": "NoActiveSession", "message": str(exc)},
        ) from exc
    try:
        return request_auto_stop(session)
    except AutoDialogError as exc:
        raise _auto_error_to_http(exc) from exc


@router.get("/auto_dialog/state")
async def auto_dialog_state() -> dict[str, Any]:
    """Observe current Auto-Dialog runtime state or idleness.

    The frontend calls on mount to decide immediate SSE reconnection/banner attachment if automatic dialogue
    continues after refresh. Without a session, return ``{is_running: false,
    auto_state: null}`` rather than 404, simplifying UI logic.
    """
    store = get_session_store()
    session = store.get()
    if session is None:
        return {"is_running": False, "auto_state": None}
    return describe_auto_state(session)


# Convenience trailing-slash-free aliases are provided by the router
# prefix already; we don't register duplicates.

# Re-export the explicit role/source constants so other phase routers
# (``simuser``, ``script`` etc.) can share the validation vocabulary
# without reaching into ``chat_messages`` directly.
__all__ = ["router", "ROLE_SYSTEM", "ROLE_USER", "SOURCE_INJECT", "SOURCE_MANUAL"]
