"""Judge / scoring-schema API (P15 + P16).

P15 shipped the **schema CRUD** surface — browse / edit / validate /
import / export / preview of ``ScoringSchema`` JSONs. P16 layers the
**run + results** endpoints on top: ``POST /judge/run`` orchestrates
one or more :class:`BaseJudger` calls against the active session, and
the ``/judge/results`` endpoints list / fetch / delete the
:class:`EvalResult` dicts those calls produce (persisted into
``session.eval_results`` alongside other session artefacts).

Endpoints
---------
Schema CRUD (P15)
* ``GET    /api/judge/schemas``                       — merged builtin+user list.
* ``GET    /api/judge/schemas/{id}``                  — full schema + co-existence flags.
* ``POST   /api/judge/schemas``                       — create/overwrite user schema.
* ``PUT    /api/judge/schemas/{id}``                  — same as POST; id in URL wins.
* ``DELETE /api/judge/schemas/{id}``                  — delete user schema (builtin protected).
* ``POST   /api/judge/schemas/validate``              — soft validation (returns error list).
* ``POST   /api/judge/schemas/duplicate``             — copy (usually builtin → user).
* ``POST   /api/judge/schemas/{id}/preview_prompt``   — render template with sample ctx.
* ``POST   /api/judge/schemas/import``                — import a raw JSON payload.
* ``GET    /api/judge/schemas/{id}/export``           — export raw JSON for saving to disk.

Run + results (P16)
* ``POST   /api/judge/run``                           — run one or more judger calls.
* ``GET    /api/judge/results``                       — filtered + paginated list.
* ``GET    /api/judge/results/{id}``                  — single EvalResult.
* ``DELETE /api/judge/results/{id}``                  — drop one result.
* ``DELETE /api/judge/results``                       — clear all results for session.

Design notes
------------
* Same pattern as ``chat_router`` script endpoints: soft validation lives in
  the pipeline module (``scoring_schema.validate_schema_dict``); the router
  only translates errors to HTTP and keeps the session-less (global asset)
  contract — scoring schemas exist independently of any active session.
* ``import`` vs ``POST``: they're functionally identical (both take a full
  schema JSON and write to ``USER_SCHEMAS_DIR``). ``import`` exists solely
  so the UI can wire a clearer "upload file" button while the plain POST is
  used by the inline editor's Save action. Both go through the same
  ``save_user_schema`` code path so the error contract stays uniform.
* ``export`` returns the plain JSON (not wrapped in an envelope) because
  the UI downloads it as a ``.json`` file via ``window.URL.createObjectURL``
  → `<a download>` — wrapping it in ``{schema: ...}`` would force the UI to
  re-unwrap before save. We still emit ``Content-Disposition`` so curl /
  direct navigation also works cleanly.
* ``/judge/run`` is *session-scoped* (unlike schema CRUD which is global).
  All target message IDs must resolve inside ``session.messages``; no
  active session → 404. Persistence writes under
  ``session_operation("judge.run")`` to serialize against other
  session mutators.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from tests.testbench.chat_messages import ROLE_ASSISTANT, ROLE_USER
from tests.testbench.logger import python_logger
from tests.testbench.model_config import ModelGroupConfig
from tests.testbench.pipeline import diagnostics_store
from tests.testbench.pipeline.diagnostics_ops import DiagnosticsOp
from tests.testbench.pipeline.judge_export import (
    aggregate_results,
    build_export_filename,
    build_report_json,
    build_report_markdown,
)
from tests.testbench.pipeline.judge_runner import (
    EvalResult,
    JudgeInputs,
    JudgeRunError,
    load_schema_by_id,
    make_judger,
)
# Module-level import to avoid Python scope trap: earlier version had
# `from pipeline.prompt_builder import ... PreviewNotReady` **inside**
# `_extract_persona_meta`'s try-block. That made `PreviewNotReady` a
# function-local; if `build_prompt_bundle(session)` raised something
# that needed `except PreviewNotReady:` to evaluate the class, AND the
# import had failed (transitively, e.g. memory loader ImportError),
# Python tried to resolve the local `PreviewNotReady` → UnboundLocalError
# "cannot access local variable 'PreviewNotReady' where it is not
# associated with a value" — which surfaced to the user as "整批运行失败"
# when F6 `match_main_chat` was checked. 2026-04-22 Day 8 手测 fix.
# Use the full `tests.testbench.pipeline.*` path (not short `pipeline.*`)
# so smoke tests work; `pipeline` short path only resolves under
# `run_testbench.py`'s sys.path shim.
from tests.testbench.pipeline.prompt_builder import (
    build_prompt_bundle,
    PreviewNotReady,
)
from tests.testbench.pipeline.scoring_schema import (
    ScoringSchemaError,
    delete_user_schema,
    duplicate_schema,
    list_schemas,
    preview_prompt,
    read_schema,
    save_user_schema,
    validate_schema_dict,
)
from tests.testbench.session_store import (
    SessionConflictError,
    get_session_store,
)

router = APIRouter(prefix="/api/judge", tags=["judge"])


#: Cap to prevent a runaway batch from OOM-ing the session. Matches the
#: auto-dialog's safeguard; UI batches above this get truncated with a
#: warning rather than silently dropped.
MAX_RESULTS_PER_SESSION: int = 200
MAX_BATCH_ITEMS: int = 50


# ── helpers ──────────────────────────────────────────────────────────


def _schema_error_to_http(exc: ScoringSchemaError) -> HTTPException:
    """ScoringSchemaError → HTTPException, preserving ``errors`` detail.

    Matches ``chat_router._script_error_to_http`` so the frontend's
    ``error_bus`` can apply the same toast / red-box logic regardless of
    whether the failing payload was a dialog template or a scoring schema.
    """
    detail: dict[str, Any] = {"error_type": exc.code, "message": exc.message}
    errors = getattr(exc, "errors", None)
    if errors:
        detail["errors"] = errors
    return HTTPException(status_code=exc.status, detail=detail)


# ── request models (deliberately loose — real validation in pipeline) ─


class _SchemaSaveRequest(BaseModel):
    """Body for ``POST /api/judge/schemas`` / ``PUT /api/judge/schemas/{id}``.

    ``model_config = {"extra": "allow"}`` keeps future schema fields
    working without a router change — the pipeline's soft validator is
    the single source of truth for which fields are accepted.
    """

    id: str = Field(..., description="schema id (= 文件名).")
    name: str | None = Field(default=None)
    description: str | None = Field(default=None)
    mode: str | None = Field(default=None, description="absolute / comparative")
    granularity: str | None = Field(default=None, description="single / conversation")
    dimensions: list[dict[str, Any]] = Field(default_factory=list)
    prompt_template: str | None = Field(default=None)
    ai_ness_penalty: dict[str, Any] | None = Field(default=None)
    pass_rule: str | None = Field(default=None)
    verdict_rule: str | None = Field(default=None)
    raw_score_formula: str | None = Field(default=None)
    normalize_formula: str | None = Field(default=None)
    version: int | None = Field(default=None)
    tags: list[str] | None = Field(default=None)

    model_config = {"extra": "allow"}


class _SchemaValidateRequest(BaseModel):
    """Soft validate — body is a full schema dict, returns errors list."""

    schema_dict: dict[str, Any] = Field(..., alias="schema")

    model_config = {"populate_by_name": True, "extra": "allow"}


class _SchemaDuplicateRequest(BaseModel):
    """Body for ``POST /api/judge/schemas/duplicate``."""

    source_id: str = Field(..., description="要复制的源 id (builtin 或 user).")
    target_id: str = Field(..., description="新 user schema 的 id.")
    overwrite: bool = Field(
        default=False,
        description="target_id 已存在的 user schema 时是否覆盖. 默认 False → 409.",
    )


class _SchemaPreviewRequest(BaseModel):
    """Body for ``POST /api/judge/schemas/{id}/preview_prompt``.

    ``context`` is optional — when absent, a synthetic sample is used so
    the editor renders something useful out of the box. Supplied keys
    override the sample verbatim; unknown keys are ignored by the
    template's safe-format machinery.
    """

    context: dict[str, Any] | None = Field(default=None)


# ── list / read ─────────────────────────────────────────────────────


@router.get("/schemas")
def schemas_list() -> dict[str, Any]:
    """Return merged builtin + user schema meta list.

    Session-agnostic (same as ``/api/chat/script/templates``): testers can
    browse schemas before any session has been created.
    """
    schemas = list_schemas()
    return {"schemas": schemas, "count": len(schemas)}


@router.get("/schemas/{schema_id}")
def schemas_read(schema_id: str) -> dict[str, Any]:
    """Return full active schema + builtin/user co-existence flags."""
    try:
        return read_schema(schema_id)
    except ScoringSchemaError as exc:
        raise _schema_error_to_http(exc) from exc


# ── mutating endpoints ─────────────────────────────────────────────


def _save_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Wrap the pipeline ``save_user_schema`` with HTTP translation.

    We strip keys with ``None`` values before handing off to the pipeline so
    that ``ScoringSchema.from_dict`` can fall back to its documented
    defaults (e.g. ``version=1``). Pydantic's ``model_dump`` would otherwise
    emit ``{"version": None}`` for every optional field the client omitted,
    which then trips ``int(None)`` downstream.
    """
    cleaned = {k: v for k, v in payload.items() if v is not None}
    try:
        return save_user_schema(cleaned)
    except ScoringSchemaError as exc:
        raise _schema_error_to_http(exc) from exc


@router.post("/schemas")
def schemas_save(body: _SchemaSaveRequest) -> dict[str, Any]:
    """Create or overwrite a user schema.

    - 422 SchemaInvalid (detail 含 ``errors`` 清单) — 字段校验失败.
    - 200 ``{schema, overriding_builtin, path}`` — 写盘成功.

    等价于 ``PUT /api/judge/schemas/{body.id}``. 提供 POST 变体是为了
    新建流程前端不必先知道 id (虽然我们确实要求 body.id 存在 — schema 的
    "id = 文件名" 契约决定的).
    """
    return _save_payload(body.model_dump(exclude_unset=False))


@router.put("/schemas/{schema_id}")
def schemas_save_put(
    schema_id: str, body: _SchemaSaveRequest,
) -> dict[str, Any]:
    """Same as POST, with URL ``schema_id`` overriding any body mismatch.

    If ``body.id`` differs from the URL id we overwrite body.id with the
    URL's value — matches the REST convention that the resource URL is
    authoritative for its key.
    """
    payload = body.model_dump(exclude_unset=False)
    if payload.get("id") != schema_id:
        python_logger().warning(
            "judge.schemas PUT: body.id=%r overridden by URL id=%r.",
            payload.get("id"), schema_id,
        )
        payload["id"] = schema_id
    return _save_payload(payload)


@router.delete("/schemas/{schema_id}")
def schemas_delete(schema_id: str) -> dict[str, Any]:
    """Delete a user schema. Builtin schemas are protected by the pipeline.

    - 404 SchemaNotFound — user 目录没有这个 id (builtin 本来就不能删).
    - 200 ``{deleted_id, resurfaces_builtin}``.
    """
    try:
        return delete_user_schema(schema_id)
    except ScoringSchemaError as exc:
        raise _schema_error_to_http(exc) from exc


@router.post("/schemas/validate")
def schemas_validate(body: _SchemaValidateRequest) -> dict[str, Any]:
    """Soft validate a schema draft — returns ``{ok, errors, normalized?}``.

    Used by the editor's realtime red-box feedback. Never raises; a
    completely broken input just gets ``ok=false`` + an ``errors`` list.
    """
    return validate_schema_dict(body.schema_dict)


@router.post("/schemas/duplicate")
def schemas_duplicate(body: _SchemaDuplicateRequest) -> dict[str, Any]:
    """Duplicate a schema into the user directory under a new id.

    Same error contract as ``save``: 409 ``SchemaTargetExists`` when
    target already exists and ``overwrite=False``; 404 when source
    doesn't exist; 422 on malformed ids.
    """
    try:
        return duplicate_schema(
            body.source_id, body.target_id, overwrite=body.overwrite,
        )
    except ScoringSchemaError as exc:
        raise _schema_error_to_http(exc) from exc


# ── preview / import / export ──────────────────────────────────────


@router.post("/schemas/{schema_id}/preview_prompt")
def schemas_preview_prompt(
    schema_id: str, body: _SchemaPreviewRequest,
) -> dict[str, Any]:
    """Render the schema's prompt with a sample or user-supplied context.

    Returns ``{prompt, char_count, used_placeholders, missing_placeholders}``.
    """
    try:
        return preview_prompt(schema_id, body.context)
    except ScoringSchemaError as exc:
        raise _schema_error_to_http(exc) from exc


@router.post("/schemas/import")
def schemas_import(body: _SchemaSaveRequest) -> dict[str, Any]:
    """Import a raw schema JSON payload. Alias of ``POST /schemas``.

    Exists separately so the UI can surface an ``<input type="file">``
    button distinct from the inline Save action while sharing the
    validation + write path.
    """
    return _save_payload(body.model_dump(exclude_unset=False))


@router.get("/schemas/{schema_id}/export")
def schemas_export(schema_id: str) -> JSONResponse:
    """Export the active schema as a downloadable JSON file.

    The ``Content-Disposition: attachment`` header triggers browser
    download even when the path is hit via direct navigation; the
    inline-editor UI still uses ``fetch`` + ``blob`` so this header is
    harmless for the frontend's save-as flow.
    """
    try:
        details = read_schema(schema_id)
    except ScoringSchemaError as exc:
        raise _schema_error_to_http(exc) from exc
    active = details["active"]
    body = json.dumps(active, ensure_ascii=False, indent=2) + "\n"
    return JSONResponse(
        content=active,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{schema_id}.json"'
            ),
            "Content-Length": str(len(body.encode("utf-8"))),
        },
    )


# ── run + results (P16) ────────────────────────────────────────────


def _require_session():
    """Return the active session or raise 404 NoActiveSession.

    Mirrors ``memory_router._require_session`` exactly — duplicated here
    so ``judge_router`` keeps its existing session-less surface for
    schema CRUD while only the run/results block takes a session
    dependency.
    """
    session = get_session_store().get()
    if session is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error_type": "NoActiveSession",
                "message": (
                    "No active session; create one via POST /api/session first."
                ),
            },
        )
    return session


def _judge_run_error_to_http(exc: JudgeRunError) -> HTTPException:
    """:class:`JudgeRunError` → :class:`HTTPException`, preserving code + message."""
    return HTTPException(
        status_code=exc.status,
        detail={"error_type": exc.code, "message": exc.message},
    )


class _JudgeModelOverride(BaseModel):
    """Thin override bundle for per-run judge model tweaks.

    Any field left ``None`` / absent falls back to the session-level
    judge config (``resolve_group_config(session, "judge")``). This lets
    a tester ab-test schemas across models by only setting ``model``
    here without having to re-type api_key / base_url.
    """

    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout: float | None = None

    model_config = {"extra": "ignore"}


class _JudgeRunRequest(BaseModel):
    """Body for ``POST /api/judge/run``.

    Shape is intentionally permissive — the pipeline layer does the
    heavy lifting. Key fields:

    * ``scope="messages"`` → drive from ``message_ids`` (list of
      assistant-message ids already in ``session.messages``). Each id
      produces one judger call for ``granularity=single``; the whole
      list is bundled into one call for ``granularity=conversation``.
    * ``scope="conversation"`` → drive from the full session history.
      Granularity=single still iterates each assistant message; =conv
      runs once against the whole transcript.
    * ``reference_response`` — inline comparative B text. Applied to
      every target in the batch (1:N same-B-for-all semantics).
      Highest priority: wins over ``reference_message_id`` and
      per-target fallback when set.
    * ``reference_message_id`` — pull B from one named message's
      ``reference_content``. Same string reused for every target
      (legacy 1:N pick-a-ref-for-all workflow, still accepted but no
      longer surfaced by the UI).
    * **Implicit per-target fallback** — when neither of the above is
      set and mode=comparative, each target uses *its own*
      ``reference_content`` field as B. This enables 1:1 pairing in a
      single batch call: select N assistant messages that each carry
      their own B, and the run produces N results each judging A
      against its matched B. Targets lacking ``reference_content``
      surface ``MissingReference`` as a per-item soft error.
    * ``reference_conversation`` — trajectory B for
      ``mode=comparative, granularity=conversation``.
    * ``persist=false`` → one-shot preview, don't store into
      ``session.eval_results``. Useful for the "try before saving" UI.
    """

    schema_id: str = Field(..., description="scoring schema id.")
    scope: Literal["messages", "conversation"] = Field(default="messages")
    message_ids: list[str] = Field(
        default_factory=list,
        description="scope=messages 时必填, 指定待评分的 assistant 消息 id.",
    )
    reference_response: str | None = None
    reference_message_id: str | None = None
    reference_conversation: list[dict[str, Any]] | None = None
    judge_model_override: _JudgeModelOverride | None = None
    extra_context: dict[str, Any] | None = None
    persist: bool = True
    # P24 §15.2 C / F6 — when ``True``, the judger prompt pulls the
    # same ``persona_system`` block that ``build_prompt_bundle`` sends
    # to the main chat model. Useful for reproducibility ("是不是因为
    # 评委看到了和对话模型不同的 system?"), especially when diagnosing
    # prompt-injection suspicions. Default ``False`` preserves the
    # historical behavior (evaluator sees only the scoring schema's
    # own ``prompt_template`` without main-chat persona).
    match_main_chat: bool = Field(
        default=False,
        description=(
            "F6: align evaluator's persona_system with main-chat's "
            "build_prompt_bundle output. Off by default."
        ),
    )

    model_config = {"extra": "ignore"}


def _collect_messages(
    session, scope: str, message_ids: list[str],
) -> list[dict[str, Any]]:
    """Return the message list to drive the run from.

    - ``scope=conversation`` → full ``session.messages``.
    - ``scope=messages`` → the specific ids, preserving order from
      ``session.messages`` (so a user selecting 3 assistant messages out
      of order still gets them chronologically). Missing ids raise 422.
    """
    all_messages = session.messages or []
    if scope == "conversation":
        return list(all_messages)
    if not message_ids:
        raise JudgeRunError(
            "EmptyMessageIds",
            "scope=messages 时必须提供 message_ids.",
            status=422,
        )
    index_by_id = {m.get("id"): i for i, m in enumerate(all_messages) if m.get("id")}
    missing = [mid for mid in message_ids if mid not in index_by_id]
    if missing:
        raise JudgeRunError(
            "UnknownMessageIds",
            f"未找到以下 message id: {missing[:5]} (共 {len(missing)} 条).",
            status=422,
        )
    # Sort requested ids by their position in the session so results
    # line up with conversation order (not click order in the UI).
    selected_indices = sorted(index_by_id[mid] for mid in message_ids)
    return [all_messages[i] for i in selected_indices]


def _build_single_inputs(
    *,
    session,
    granularity: str,
    target_idx: int,
    all_messages: list[dict[str, Any]],
    system_prompt: str,
    character_name: str,
    master_name: str,
    reference_response: str,
    extra_context: dict[str, Any] | None,
    scope: str,
) -> JudgeInputs:
    """Assemble inputs for a single-granularity judger call.

    The **preceding** user message (first user message upwards from
    ``target_idx``) is used as ``user_input``; everything before that
    is ``history``. This matches how the pre-P15 ``prompt_test_judger``
    slotted messages so builtin schemas keep their exact prompt shape.
    """
    target = all_messages[target_idx]
    user_input = ""
    history_slice: list[dict[str, Any]] = []
    # Walk backwards to locate the user turn immediately preceding the
    # target assistant message. ``history`` is everything *before* that
    # user turn (= truly prior context). Anything between the user turn
    # and the target (shouldn't normally exist, but guards against
    # adjacent assistant messages) is ignored.
    cursor = target_idx - 1
    while cursor >= 0 and all_messages[cursor].get("role") != ROLE_USER:
        cursor -= 1
    if cursor >= 0:
        user_input = str(all_messages[cursor].get("content") or "")
        history_slice = list(all_messages[:cursor])
    return JudgeInputs(
        system_prompt=system_prompt,
        history=history_slice,
        user_input=user_input,
        ai_response=str(target.get("content") or ""),
        reference_response=reference_response,
        character_name=character_name,
        master_name=master_name,
        target_message_ids=[str(target.get("id") or "")],
        scope=scope,
        extra_context=dict(extra_context or {}),
    )


def _build_conversation_inputs(
    *,
    granularity: str,
    messages: list[dict[str, Any]],
    reference_conversation: list[dict[str, Any]],
    system_prompt: str,
    character_name: str,
    master_name: str,
    extra_context: dict[str, Any] | None,
    scope: str,
) -> JudgeInputs:
    """Assemble inputs for a conversation-granularity judger call.

    Uses ``conversation`` + ``reference_conversation``; ``history`` is
    left empty because the AbsoluteConversationJudger formats
    ``conversation`` as the history text itself.
    """
    target_message_ids = [
        str(m.get("id"))
        for m in messages
        if m.get("role") == ROLE_ASSISTANT and m.get("id")
    ]
    return JudgeInputs(
        system_prompt=system_prompt,
        conversation=list(messages),
        reference_conversation=list(reference_conversation),
        character_name=character_name,
        master_name=master_name,
        target_message_ids=target_message_ids,
        scope=scope,
        extra_context=dict(extra_context or {}),
    )


def _resolve_reference_text(
    *,
    session,
    inline: str | None,
    ref_message_id: str | None,
    target_msg: dict[str, Any] | None = None,
) -> str:
    """Return the B-side text for a comparative single call.

    Resolution priority (first non-empty wins):

    1. ``inline`` — explicit inline B text supplied by the caller. Same
       string is reused for every target in the batch (1:N semantics).
    2. ``ref_message_id`` — pull ``reference_content`` from one named
       session message. Same string reused for every target (1:N,
       legacy one-ref-for-all workflow, still accepted by the backend
       but no longer surfaced in the UI).
    3. **per-target fallback** — each target carries its own attached
       ``reference_content`` field (populated by Script mode or manual
       edit). When neither of the above is set, we use *this specific
       target's* ``reference_content`` as B so a batch of N messages
       can do 1:1 pairwise comparison (each A judged against its own
       B) in a single API call. This is the behaviour the UI's
       msg_ref mode now drives.

    Empty return means "no reference available" for this target; the
    judger raises ``MissingReference`` which is caught and reported as a
    per-item error (does not abort the whole batch).
    """
    if inline is not None and inline.strip():
        return inline.strip()
    if ref_message_id:
        for m in session.messages or []:
            if m.get("id") == ref_message_id:
                content = m.get("reference_content") or m.get("content") or ""
                return str(content).strip()
    if target_msg is not None:
        # Deliberately do NOT fall back to ``content``: reference_content
        # is the explicit "here's a gold B for this A" slot; if the user
        # hasn't set one, we surface MissingReference rather than score
        # a message against its own output.
        content = target_msg.get("reference_content") or ""
        return str(content).strip()
    return ""


@dataclass
class _PersonaMetaResult:
    """Output of :func:`_extract_persona_meta`.

    P24 §3.4 F6: when ``match_main_chat`` was requested, callers need
    to know **whether the align actually happened** so the response
    payload can surface ``match_main_chat_applied`` — a checkbox that
    silently falls back to legacy behavior would defeat the debugging
    value of the feature.

    Attributes:

    * ``system_prompt`` / ``character_name`` / ``master_name`` — same
      3-tuple as the old return value.
    * ``applied`` — ``True`` iff ``match_main_chat=True`` was honored
      (i.e. the ``build_prompt_bundle`` path was used). ``False`` when
      the caller didn't request it, or when a fallback happened.
    * ``fallback_reason`` — short identifier when ``applied`` dropped
      to ``False`` despite a request; ``None`` otherwise. Values:
      ``"preview_not_ready"`` (no character_name), ``"bundle_error"``
      (unexpected bundle failure).
    """

    system_prompt: str
    character_name: str
    master_name: str
    applied: bool
    fallback_reason: str | None


def _extract_persona_meta(
    session, *, match_main_chat: bool = False,
) -> _PersonaMetaResult:
    """Derive ``(system_prompt, character_name, master_name, applied, reason)``.

    Two modes:

    * ``match_main_chat=False`` (default, historical behavior): use
      whatever is currently stored on ``session.persona`` with manual
      name interpolation. Lightweight, predictable, but does *not*
      include chat gap hints / recent history / holiday context that
      the real chat request receives. ``applied`` = ``False``,
      ``fallback_reason`` = ``None``.
    * ``match_main_chat=True`` (F6 opt-in): call
      :func:`build_prompt_bundle` and use the bundle's
      ``system_prompt``. This is byte-identical to what the chat
      model sees, making "why did the judger score this low?" much
      easier to answer when persona / memory is the suspect.
      Gracefully degrades to legacy path if the bundle can't build —
      but records the fallback reason so the UI can tell the user
      "you asked for it but it didn't apply because X".

    Empty strings on all three fields are OK for judging — the prompt
    will just have empty placeholders; the judger still functions.
    """
    persona = session.persona or {}
    character_name = str(persona.get("character_name") or "")
    master_name = str(persona.get("master_name") or "")

    if match_main_chat:
        try:
            bundle = build_prompt_bundle(session)
            return _PersonaMetaResult(
                system_prompt=bundle.system_prompt,
                character_name=character_name,
                master_name=master_name,
                applied=True,
                fallback_reason=None,
            )
        except PreviewNotReady:
            fallback_reason = "preview_not_ready"
        except Exception as exc:  # noqa: BLE001 — last-chance fallback
            python_logger().warning(
                "judge._extract_persona_meta: build_prompt_bundle raised "
                "%s; falling back to legacy stored-prompt path. detail=%s",
                type(exc).__name__, exc,
            )
            fallback_reason = "bundle_error"
    else:
        fallback_reason = None

    stored_prompt = str(persona.get("system_prompt") or "")
    resolved = stored_prompt
    if character_name:
        resolved = resolved.replace("{LANLAN_NAME}", character_name)
    if master_name:
        resolved = resolved.replace("{MASTER_NAME}", master_name)
    return _PersonaMetaResult(
        system_prompt=resolved,
        character_name=character_name,
        master_name=master_name,
        applied=False,
        fallback_reason=fallback_reason,
    )


def _override_to_model_config(
    override: _JudgeModelOverride | None,
) -> ModelGroupConfig | None:
    """Convert the request-body override into a typed ``ModelGroupConfig``.

    Returns ``None`` when nothing was set (all fields ``None`` / blank)
    so :meth:`BaseJudger._resolve_config` knows it can short-circuit.
    """
    if override is None:
        return None
    data = {k: v for k, v in override.model_dump().items() if v not in (None, "")}
    if not data:
        return None
    # ``ModelGroupConfig`` requires provider/base_url/model to all be
    # set; for an override we fill missing fields with placeholders
    # that the ``_resolve_config`` merge will then drop back to the
    # session defaults (because the merge uses ``exclude_unset`` style
    # logic). To keep Pydantic happy we fall back to empty strings.
    return ModelGroupConfig(
        provider=data.get("provider") or "",
        base_url=data.get("base_url") or "",
        api_key=data.get("api_key") or "",
        model=data.get("model") or "",
        temperature=data.get("temperature", 0.7),
        max_tokens=data.get("max_tokens", 2048),
        timeout=data.get("timeout", 60.0),
    )


#: Keys that :class:`BaseJudger` subclasses set in ``_build_ctx`` **before**
#: the ``ctx.update(inputs.extra_context or {})`` line. If the caller's
#: ``extra_context`` payload contains any of these, the supplied value
#: wins — effectively taking control of the judge prompt's core
#: placeholders. This is a legitimate (if specialised) feature for
#: testbench power users but crosses a security boundary worth an
#: audit-log line, see PLAN §13 F4 + AGENT_NOTES §4.27 #97 (I2).
#:
#: Not included here: purely decorative keys like ``target_message_ids``
#: / ``scope`` which the template may reference but ``_build_ctx`` does
#: not set (those are **additions**, not overrides).
_JUDGE_CTX_OVERRIDE_KEYS: frozenset[str] = frozenset({
    "system_prompt",
    "history",
    "conversation",
    "user_input",
    "ai_response",
    "reference_response",
    "character_name",
    "master_name",
})


def _audit_extra_context_override(
    extra_context: dict[str, Any] | None,
    *,
    schema_id: str,
    session_id: str | None,
) -> None:
    """Log a warning iff ``extra_context`` overrides judger-managed keys.

    PLAN §13 F4 (post-P22 hardening). ``extra_context`` is the escape
    hatch that lets a request inject custom ``{my_tag}`` values into a
    schema's ``prompt_template``. Because its ``dict.update`` is applied
    *after* :meth:`BaseJudger._build_ctx` assembles the canonical
    context, any key collision silently replaces testbench-managed
    values (``system_prompt``, ``history``, the ``<user_content>``-
    wrapped chat turn, etc.) with caller-controlled text. That is
    effectively full judge-prompt control — consent-worthy, but
    explicitly not blocked (core principle: testbench must let users
    mount adversarial tests on purpose).

    This helper produces two audit trails:

    * ``python_logger().warning(...)`` so the line lands in the
      rotating server log for offline review.
    * ``diagnostics_store.record_internal(level="warning", ...)`` so the
      Diagnostics → Errors UI surfaces it to whoever is running the
      session right now, matching the "report but don't block" pattern
      used for the P21.3 F3 injection-detection badges.

    No audit is emitted when:

    * ``extra_context`` is ``None`` / empty.
    * ``extra_context`` contains only **non-override** keys (i.e. user
      just added ``{custom_tag}`` placeholders their template uses —
      the safe / intended case).
    """
    if not extra_context:
        return
    overridden = sorted(k for k in extra_context if k in _JUDGE_CTX_OVERRIDE_KEYS)
    if not overridden:
        return
    python_logger().warning(
        "[judge] extra_context override detected: schema=%s session=%s "
        "keys=%s (these replace testbench-managed judge prompt context)",
        schema_id, session_id or "?", overridden,
    )
    try:
        diagnostics_store.record_internal(
            op=DiagnosticsOp.JUDGE_EXTRA_CONTEXT_OVERRIDE,
            message=(
                f"judge_run extra_context overrides core keys "
                f"{overridden}; caller controls judge prompt context."
            ),
            level="warning",
            session_id=session_id,
            detail={
                "schema_id": schema_id,
                "override_keys": overridden,
                "total_keys": sorted(extra_context.keys()),
            },
        )
    except Exception:  # noqa: BLE001 - audit must never break the run
        python_logger().exception(
            "[judge] failed to record extra_context audit event"
        )


@router.post("/run")
async def judge_run(body: _JudgeRunRequest) -> dict[str, Any]:
    """Run one or more judger calls against the active session.

    Returns ``{results: [...], persisted_count: N, error_count: N}``.
    Per-item failures are surfaced as EvalResult dicts with non-null
    ``error``; the endpoint itself only 4xx's on precondition failures
    (no session / unknown schema / empty message list).
    """
    session = _require_session()

    _audit_extra_context_override(
        body.extra_context,
        schema_id=body.schema_id,
        session_id=getattr(session, "id", None),
    )

    try:
        schema = load_schema_by_id(body.schema_id)
    except JudgeRunError as exc:
        raise _judge_run_error_to_http(exc) from exc

    override_cfg = _override_to_model_config(body.judge_model_override)
    try:
        judger = make_judger(
            session=session, schema=schema, judge_model_override=override_cfg,
        )
    except JudgeRunError as exc:
        raise _judge_run_error_to_http(exc) from exc

    try:
        messages = _collect_messages(session, body.scope, body.message_ids)
    except JudgeRunError as exc:
        raise _judge_run_error_to_http(exc) from exc

    persona_meta = _extract_persona_meta(
        session, match_main_chat=bool(body.match_main_chat),
    )
    system_prompt = persona_meta.system_prompt
    character_name = persona_meta.character_name
    master_name = persona_meta.master_name

    runs: list[JudgeInputs] = []
    if schema.granularity == "single":
        assistant_indices = [
            i for i, m in enumerate(messages) if m.get("role") == ROLE_ASSISTANT
        ]
        if not assistant_indices:
            raise HTTPException(
                status_code=422,
                detail={
                    "error_type": "NoAssistantTarget",
                    "message": (
                        "selected messages contain no assistant response to score."
                    ),
                },
            )
        # scope=messages: indices point into ``messages`` (which is a
        # subset of session.messages). For single-granularity we need
        # the surrounding user context; if the caller selected only an
        # assistant bubble without its paired user turn, we walk back
        # into the full session.messages to find it.
        all_session_messages = session.messages or []
        for local_idx in assistant_indices:
            target_msg = messages[local_idx]
            target_id = target_msg.get("id")
            # Map local index → global index in session.messages so the
            # preceding-user-turn lookup has full context available.
            global_idx = next(
                (
                    gi for gi, gm in enumerate(all_session_messages)
                    if gm.get("id") == target_id
                ),
                None,
            )
            if global_idx is None:
                continue
            reference_text = ""
            if schema.mode == "comparative":
                reference_text = _resolve_reference_text(
                    session=session,
                    inline=body.reference_response,
                    ref_message_id=body.reference_message_id,
                    target_msg=target_msg,
                )
            runs.append(_build_single_inputs(
                session=session,
                granularity=schema.granularity,
                target_idx=global_idx,
                all_messages=all_session_messages,
                system_prompt=system_prompt,
                character_name=character_name,
                master_name=master_name,
                reference_response=reference_text,
                extra_context=body.extra_context,
                scope=body.scope,
            ))
    else:  # conversation
        runs.append(_build_conversation_inputs(
            granularity=schema.granularity,
            messages=messages,
            reference_conversation=body.reference_conversation or [],
            system_prompt=system_prompt,
            character_name=character_name,
            master_name=master_name,
            extra_context=body.extra_context,
            scope=body.scope,
        ))

    if not runs:
        raise HTTPException(
            status_code=422,
            detail={
                "error_type": "EmptyRunPlan",
                "message": "no judger calls produced from the selected inputs.",
            },
        )

    if len(runs) > MAX_BATCH_ITEMS:
        python_logger().warning(
            "[judge] batch size %d exceeds MAX_BATCH_ITEMS=%d; truncating.",
            len(runs), MAX_BATCH_ITEMS,
        )
        runs = runs[:MAX_BATCH_ITEMS]

    results: list[EvalResult] = []
    for inp in runs:
        result = await judger.run(inp)
        results.append(result)

    error_count = sum(1 for r in results if r.error)

    persisted_count = 0
    if body.persist:
        store = get_session_store()
        try:
            async with store.session_operation("judge.run") as sess_lock:
                # FIFO eviction so long-running sessions don't grow
                # unbounded — we keep the most recent MAX_RESULTS.
                existing = sess_lock.eval_results or []
                new_serialized = [r.to_dict() for r in results]
                combined = existing + new_serialized
                if len(combined) > MAX_RESULTS_PER_SESSION:
                    combined = combined[-MAX_RESULTS_PER_SESSION:]
                sess_lock.eval_results = combined
                persisted_count = len(new_serialized)
        except SessionConflictError as exc:
            python_logger().warning(
                "[judge] persist failed due to session conflict: %s", exc,
            )
            persisted_count = 0

    return {
        "results": [r.to_dict() for r in results],
        "persisted_count": persisted_count,
        "error_count": error_count,
        "total": len(results),
        # P24 §3.4 F6: signal whether match_main_chat was honored.
        # Three states:
        #   - requested=False, applied=False → not asked (default path)
        #   - requested=True,  applied=True  → full build_prompt_bundle
        #   - requested=True,  applied=False → fallback, reason in field
        "match_main_chat_requested": bool(body.match_main_chat),
        "match_main_chat_applied": persona_meta.applied,
        "match_main_chat_fallback_reason": persona_meta.fallback_reason,
    }


# P24 §13.3 (2026-04-21): these helpers moved to
# ``pipeline/request_helpers.py`` as shared module-level utilities. Other
# routers (memory_router / config_router / persona_router) that parse
# raw dict bodies should also use them. The aliases below preserve
# legacy call sites in this file (``_coerce_bool(...)``) with zero diff.
from tests.testbench.pipeline.request_helpers import (  # noqa: E402
    coerce_bool as _coerce_bool,
    coerce_float as _coerce_float,
)


def _result_matches(
    r: dict[str, Any],
    *,
    schema_id: str | None,
    mode: str | None,
    granularity: str | None,
    scope: str | None,
    passed: bool | None,
    verdict: str | None,
    judge_model: str | None,
    message_id: str | None,
    min_overall: float | None,
    max_overall: float | None,
    min_gap: float | None,
    max_gap: float | None,
    since: str | None,
    until: str | None,
    errored: bool | None,
    query: str | None,
) -> bool:
    """Decide whether a stored EvalResult dict matches all filter fields.

    All filters are ANDed; ``None`` / empty string means "don't constrain".
    ``query`` is a case-insensitive substring match against analysis /
    diff_analysis / schema_id / target preview text so the Results table
    can offer a single free-text search box on top of the structured
    filters.

    ``min_overall`` / ``max_overall`` compare against ``scores.overall_score``
    for absolute results and ``scores.overall_a`` (the A-side) for
    comparative. ``min_gap`` / ``max_gap`` only apply to comparative and
    short-circuit other results to *not match* when the filter is set
    (otherwise a mixed list would get silently truncated to comparative).
    """
    if schema_id and r.get("schema_id") != schema_id:
        return False
    if mode and r.get("mode") != mode:
        return False
    if granularity and r.get("granularity") != granularity:
        return False
    if scope and r.get("scope") != scope:
        return False
    if passed is not None and bool(r.get("passed")) != bool(passed):
        return False
    if verdict and str(r.get("verdict") or "") != verdict:
        return False
    if judge_model:
        jm = r.get("judge_model") or {}
        if str(jm.get("model") or "") != judge_model:
            return False
    if message_id:
        target_ids = r.get("target_message_ids") or []
        if message_id not in target_ids:
            return False
    if errored is not None:
        if bool(r.get("error")) != bool(errored):
            return False
    created = str(r.get("created_at") or "")
    if since and created < since:
        return False
    if until and created > until:
        return False

    scores = r.get("scores") or {}
    r_mode = r.get("mode") or ""
    if min_overall is not None or max_overall is not None:
        overall = scores.get("overall_score")
        if overall is None:
            overall = scores.get("overall_a")
        try:
            ov = float(overall) if overall is not None else None
        except (TypeError, ValueError):
            ov = None
        if ov is None:
            return False
        if min_overall is not None and ov < float(min_overall):
            return False
        if max_overall is not None and ov > float(max_overall):
            return False
    if min_gap is not None or max_gap is not None:
        if r_mode != "comparative":
            return False
        try:
            gap = float(r.get("gap")) if r.get("gap") is not None else None
        except (TypeError, ValueError):
            gap = None
        if gap is None:
            return False
        if min_gap is not None and gap < float(min_gap):
            return False
        if max_gap is not None and gap > float(max_gap):
            return False
    if query:
        needle = query.strip().lower()
        if needle:
            haystack_parts = [
                str(r.get("analysis") or ""),
                str(r.get("diff_analysis") or ""),
                str(r.get("schema_id") or ""),
                str((r.get("target_preview") or {}).get("ai_response") or ""),
                str((r.get("target_preview") or {}).get("user_input") or ""),
                str((r.get("target_preview") or {}).get("reference_response") or ""),
                " ".join(r.get("strengths") or []),
                " ".join(r.get("weaknesses") or []),
                " ".join(r.get("problem_patterns") or []),
            ]
            if needle not in " ".join(haystack_parts).lower():
                return False
    return True


@router.get("/results")
def results_list(
    schema_id: str | None = None,
    mode: str | None = None,
    granularity: str | None = None,
    scope: str | None = None,
    passed: bool | None = None,
    verdict: str | None = None,
    judge_model: str | None = None,
    message_id: str | None = None,
    min_overall: float | None = None,
    max_overall: float | None = None,
    min_gap: float | None = None,
    max_gap: float | None = None,
    since: str | None = None,
    until: str | None = None,
    errored: bool | None = None,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Return a filtered + paginated slice of ``session.eval_results``.

    Filters are ANDed. Pagination is ``offset``-based (not cursor-
    based) because the list is in-memory and small; the UI needs to
    show totals anyway, so offset-pagination is a better fit.

    The filter surface is deliberately wider than P16 (which only knew
    ``schema_id`` / ``mode`` / ``granularity`` / ``passed``) so the P17
    Results subpage can drive a multi-facet filter bar off a single
    endpoint — see :func:`_result_matches` for the full predicate set.
    """
    session = _require_session()
    all_results = list(session.eval_results or [])

    filtered = [
        r for r in all_results if _result_matches(
            r,
            schema_id=schema_id,
            mode=mode,
            granularity=granularity,
            scope=scope,
            passed=passed,
            verdict=verdict,
            judge_model=judge_model,
            message_id=message_id,
            min_overall=min_overall,
            max_overall=max_overall,
            min_gap=min_gap,
            max_gap=max_gap,
            since=since,
            until=until,
            errored=errored,
            query=query,
        )
    ]
    total = len(filtered)
    # Most-recent first (eval_results is append-order; reverse for UI).
    filtered.reverse()
    capped_limit = max(1, min(limit, 200))
    sliced = filtered[offset : offset + capped_limit]
    return {
        "results": sliced,
        "total": total,
        "offset": offset,
        "limit": capped_limit,
    }


@router.get("/aggregate")
def results_aggregate(
    schema_id: str | None = None,
    mode: str | None = None,
    granularity: str | None = None,
    scope: str | None = None,
    passed: bool | None = None,
    verdict: str | None = None,
    judge_model: str | None = None,
    message_id: str | None = None,
    min_overall: float | None = None,
    max_overall: float | None = None,
    min_gap: float | None = None,
    max_gap: float | None = None,
    since: str | None = None,
    until: str | None = None,
    errored: bool | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    """Return an aggregate summary for the current filter slice.

    Shape documented on :func:`tests.testbench.pipeline.judge_export
    .aggregate_results`. The filter surface mirrors ``GET /results``
    field-for-field so the Aggregate subpage can reuse the same filter
    state it already computed for Results, without a new filter DSL.

    Does **not** paginate — aggregation over a 200-cap session is cheap
    and paginating a "sum" is meaningless.
    """
    session = _require_session()
    matched = [
        r for r in (session.eval_results or []) if _result_matches(
            r,
            schema_id=schema_id,
            mode=mode,
            granularity=granularity,
            scope=scope,
            passed=passed,
            verdict=verdict,
            judge_model=judge_model,
            message_id=message_id,
            min_overall=min_overall,
            max_overall=max_overall,
            min_gap=min_gap,
            max_gap=max_gap,
            since=since,
            until=until,
            errored=errored,
            query=query,
        )
    ]
    agg = aggregate_results(matched)
    return {"aggregate": agg, "matched": len(matched)}


class _ExportReportRequest(BaseModel):
    """Body for ``POST /api/judge/export_report``.

    The filter dict is optional — omitting it exports *all* eval results
    for the session. Accepts the same keys as ``GET /results`` (unknown
    keys are ignored rather than 422'd because the UI can freely pass
    through its cached filter state).
    """

    format: Literal["json", "markdown"] = Field(default="markdown")
    filter: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] | None = Field(default=None)
    scope_label: str = Field(
        default="filtered",
        description="Short slug used to name the downloaded file.",
    )

    model_config = {"extra": "ignore"}


@router.post("/export_report")
def export_report(body: _ExportReportRequest) -> Response:
    """Generate a JSON or Markdown evaluation report for download.

    Returns a raw ``Response`` with ``Content-Disposition: attachment``
    (not ``JSONResponse``) so the Markdown branch can set the correct
    MIME type (``text/markdown; charset=utf-8``) without wrapping the
    body in a JSON envelope. Session metadata is auto-injected into the
    report header when the caller doesn't supply its own.
    """
    session = _require_session()
    f = dict(body.filter or {})

    matched = [
        r for r in (session.eval_results or []) if _result_matches(
            r,
            schema_id=(f.get("schema_id") or None),
            mode=(f.get("mode") or None),
            granularity=(f.get("granularity") or None),
            scope=(f.get("scope") or None),
            passed=_coerce_bool(f.get("passed")),
            verdict=(f.get("verdict") or None),
            judge_model=(f.get("judge_model") or None),
            message_id=(f.get("message_id") or None),
            min_overall=_coerce_float(f.get("min_overall")),
            max_overall=_coerce_float(f.get("max_overall")),
            min_gap=_coerce_float(f.get("min_gap")),
            max_gap=_coerce_float(f.get("max_gap")),
            since=(f.get("since") or None),
            until=(f.get("until") or None),
            errored=_coerce_bool(f.get("errored")),
            query=(f.get("query") or None),
        )
    ]
    # Markdown convention: oldest-first reads naturally ("here's what
    # happened in order"). JSON keeps a stable explicit order too so a
    # later diff between two exports is minimal.
    ordered = list(matched)
    ordered.sort(key=lambda r: str(r.get("created_at") or ""))
    agg = aggregate_results(ordered)

    supplied_meta = dict(body.metadata or {})
    default_meta: dict[str, Any] = {
        "session_id": session.id,
        "session_name": getattr(session, "name", None),
    }
    persona = getattr(session, "persona", None) or {}
    if isinstance(persona, dict):
        if "character_name" in persona:
            default_meta["character_name"] = persona.get("character_name")
        if "master_name" in persona:
            default_meta["master_name"] = persona.get("master_name")
    merged_meta = {**default_meta, **supplied_meta}

    if body.format == "json":
        text = build_report_json(
            results=ordered,
            aggregate=agg,
            filter_payload=f,
            metadata=merged_meta,
        )
        media_type = "application/json; charset=utf-8"
        ext = "json"
    else:
        text = build_report_markdown(
            results=ordered,
            aggregate=agg,
            filter_payload=f,
            metadata=merged_meta,
        )
        media_type = "text/markdown; charset=utf-8"
        ext = "md"

    filename = build_export_filename(
        scope_label=body.scope_label or "filtered", fmt=ext,
    )
    return Response(
        content=text,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get("/results/{result_id}")
def results_read(result_id: str) -> dict[str, Any]:
    """Return a single :class:`EvalResult` by id."""
    session = _require_session()
    for r in session.eval_results or []:
        if r.get("id") == result_id:
            return {"result": r}
    raise HTTPException(
        status_code=404,
        detail={
            "error_type": "EvalResultNotFound",
            "message": f"no eval result with id={result_id!r} in session.",
        },
    )


@router.delete("/results/{result_id}")
async def results_delete(result_id: str) -> dict[str, Any]:
    """Drop one result from the session's ``eval_results`` list."""
    store = get_session_store()
    try:
        async with store.session_operation("judge.results.delete") as session:
            before = len(session.eval_results or [])
            session.eval_results = [
                r for r in (session.eval_results or []) if r.get("id") != result_id
            ]
            after = len(session.eval_results)
            if before == after:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error_type": "EvalResultNotFound",
                        "message": f"no eval result with id={result_id!r}.",
                    },
                )
            return {"deleted_id": result_id, "remaining": after}
    except SessionConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error_type": "SessionBusy", "message": str(exc)},
        ) from exc
    except LookupError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error_type": "NoActiveSession", "message": str(exc)},
        ) from exc


@router.delete("/results")
async def results_clear() -> dict[str, Any]:
    """Clear all eval results for the current session."""
    store = get_session_store()
    try:
        async with store.session_operation("judge.results.clear") as session:
            removed = len(session.eval_results or [])
            session.eval_results = []
            return {"removed": removed}
    except SessionConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error_type": "SessionBusy", "message": str(exc)},
        ) from exc
    except LookupError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error_type": "NoActiveSession", "message": str(exc)},
        ) from exc


__all__ = ["router"]
