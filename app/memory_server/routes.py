# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Session API endpoints of the memory server, registered on
``runtime.app`` at import time (process-lifecycle endpoints live in
``runtime``). Also owns the /new_dialog QPS observability counter together
with its flush loop.

Domain-specific route groups (external import, theater/visit, scoped trust &
history, keyed staging/cancellation, scoped forget, trust/identity, and
memory browser) live in sibling modules under ``app/memory_server/`` and are
re-exported here so existing imports and monkeypatches targeting
``app.memory_server.routes`` keep working.
"""

# ruff: noqa: F401
import asyncio
import copy
import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta
from typing import Annotated, Literal
from uuid import uuid4
from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field, ValidationError, field_validator
from config.prompts.prompts_sys import _loc
from config.prompts.prompts_memory import (
    INNER_THOUGHTS_HEADER,
    CHAT_GAP_NOTICE, CHAT_GAP_LONG_HINT, CHAT_GAP_CURRENT_TIME,
    CHAT_HOLIDAY_CONTEXT,
    LEGACY_SETTINGS_EMPTY,
    LEGACY_SETTINGS_HEADER,
    LEGACY_SETTINGS_SECTION_HEADER,
    MEMORY_RECALL_HEADER,
    MEMORY_RESULTS_HEADER,
    MEMORY_UNAVAILABLE_NOTICE,
    PERSONA_HEADER, INNER_THOUGHTS_DYNAMIC,
    RECENT_HISTORY_INTRO, NO_RECENT_HISTORY,
    get_theater_memory_context,
    _normalize_memory_prompt_lang,
)
from utils.frontend_utils import get_timestamp
from utils.recent_file import RecentFileLockTimeout
from utils.llm_tool_leak_filter import (
    strip_tool_call_leaks,
    strip_tool_call_leaks_from_parts,
)
from utils.screen_comment_guard import project_screen_history
from utils.language_utils import (
    get_global_language_full,
    is_supported_language_code,
    language_context,
    normalize_language_code,
)
from memory.message_sources import (
    is_theater_episode_summary,
    is_theater_memory_message,
    theater_memory_episode_key,
)
from utils.llm_client import (
    convert_to_messages,
    message_metadata,
    messages_to_dict,
)
from utils.time_format import format_elapsed as _format_elapsed
from utils.cloudsave_runtime import MaintenanceModeError, assert_cloudsave_writable
from memory.external_markdown_import import MAX_ENTRIES, MAX_ENTRY_CHARS
from memory.outbox import OP_PERSIST_PROMPT_LOCALE
from memory.persona.fusion import ExternalMemoryImportTooLargeError
from memory.pinned_facts import due_date as pinned_due_date
from utils.natural_expression_candidates import (
    CandidateMinerError,
    SourceMessage,
    build_user_review_report,
    normalize_language,
)
from . import gates, habitual_phrase_loop, locale_state, outbox_infra, post_turn, review, runtime
from ._shared import logger, validate_lanlan_name
from utils.character_name import PROFILE_NAME_MAX_UNITS, validate_character_name
from .rows import _has_human_messages
from memory.recent import (
    TheaterEpisodeRetracted,
    _positive_metadata_int,
    is_retracted_theater_episode,
    restored_theater_history,
)
from .runtime import app
from utils.recent_file import VISIT_DIARY_KEY_RE as _KEYED_CACHE_KEY_RE  # noqa: E402
from contextlib import contextmanager


class HistoryRequest(BaseModel):
    input_history: str
    language: str | None = None
    render_language: str | None = None
    # Theater archive request id; /cache accepts it only on a theater episode write.
    idempotency_key: str | None = None
    # Theater archive attempt number; lets a retraction fence late writes of
    # attempts issued before the player declined the archive.
    theater_archive_attempt: int | None = Field(default=None, ge=0)
    # Opaque marker returned by the latest story forget. Once a story is
    # forgotten, only writes carrying its current marker (issued after the
    # forget completed) are stored; late writes issued before it are dropped.
    theater_forget_marker: str | None = Field(default=None, max_length=128)


class PromptLocalePreferenceRequest(BaseModel):
    language: str


class RecordSurfacedRequest(BaseModel):
    commitment_ids: list[str] = Field(default_factory=list)
    reflection_ids: list[str] = Field(default_factory=list)

    @field_validator("commitment_ids", "reflection_ids", mode="before")
    @classmethod
    def _none_to_empty_list(cls, value: object) -> object:
        return [] if value is None else value


async def _refuse_browsers(request: Request) -> None:
    """Reject browser-originated requests before handling internal memory routes."""
    if any(header in request.headers for header in ("origin", "referer", "sec-fetch-site")):
        raise HTTPException(status_code=403, detail={"ok": False, "reason": "untrusted_origin"})


async def _require_json_content_type(request: Request) -> None:
    """Reject non-JSON Content-Type payloads such as text/plain CORS simple requests."""
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(status_code=415, detail={"ok": False, "reason": "unsupported_media_type"})


class RepetitionInsightsRequest(BaseModel):
    language: Literal["en", "es", "pt", "ru", "ja", "ko", "zh-CN", "zh-TW"]
    assistant_message_limit: int = Field(default=100, ge=3, le=100)


@app.post("/internal/memory/{lanlan_name}/repetition_insights")
async def repetition_insights(lanlan_name: str, req: RepetitionInsightsRequest):
    """Analyze persisted assistant text without models, writes, or egress."""
    name_validation = validate_character_name(
        lanlan_name,
        allow_dots=True,
        max_units=PROFILE_NAME_MAX_UNITS,
    )
    if name_validation.code not in {None, "reserved_route_name"}:
        raise HTTPException(status_code=400, detail="Invalid lanlan_name")
    # Validated on the STRIPPED form, read with the name as given. Every
    # check above -- path separator, "..", trailing dot, reserved name,
    # character class -- runs on the stripped value, and surrounding
    # whitespace cannot reintroduce any of them, so reading the raw name
    # loosens nothing.
    #
    # Re-stripping DOES lose the identity. The public route resolves the
    # request to a characters.json key before calling here, and a key
    # carrying padding was stripped straight back on arrival -- so the
    # analysis read memory/<trimmed>/, which is an unrelated orphan when a
    # delete left one behind. The two ends have to mean the same
    # character.
    if runtime.time_manager is None:
        raise HTTPException(
            status_code=503,
            detail="memory_server not fully initialized",
        )

    # Read-only is not the same as harmless while a cloud apply is running.
    #
    # The overwrite/import releases this character's SQLite handles and then
    # replaces memory/<name>/ wholesale. A read arriving in that window --
    # from a second browser tab or the Electron subtitle window -- opens the
    # database again and CACHES the engine, and on Windows a pooled handle is
    # enough to make the replacement fail. Nothing on this path takes the
    # writability assertion, because nothing on it writes.
    #
    # 503 rather than a stale answer: the fence is short, and the panel
    # already renders "unavailable" for this status.
    from utils.cloudsave_runtime.fence import is_write_fence_active
    from utils.config_manager import get_config_manager

    try:
        fenced = is_write_fence_active(get_config_manager())
    except Exception:
        # A fence we cannot read is not a reason to fail an analysis; the
        # window is narrow and the pre-existing behaviour is to proceed.
        fenced = False
    if fenced:
        raise HTTPException(
            status_code=503,
            detail="memory is being restored; try again shortly",
        )

    try:
        language = normalize_language(req.language)
        history = await runtime.time_manager.aretrieve_latest_assistant_texts(
            lanlan_name,
            req.assistant_message_limit,
        )
        source_messages = [
            SourceMessage(language, content, source_line)
            for source_line, content in enumerate(history.messages, start=1)
        ]
        report = await asyncio.to_thread(
            build_user_review_report,
            source_messages,
        )
    except CandidateMinerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning(
            "[RepetitionInsights] analysis unavailable for %s: %s",
            lanlan_name,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail="local memory analysis unavailable",
        ) from exc

    parameters = dict(report["parameters"])
    parameters["assistant_message_limit"] = req.assistant_message_limit
    summary = dict(report["summary"])
    summary["source_available"] = history.source_available
    # ``response_ids`` is positionally aligned with ``history.messages``, and
    # the source lines the miner reports are 1-based positions into that same
    # list, so they pick out exactly the replies it analyzed.
    #
    # Taking the last N instead assumed the survivors were a contiguous
    # suffix. They are not: the budget drops the oldest message that is over
    # its fair share, which can be an interior one, so the ids were offset --
    # a reply that was mined went unattributed while one that was dropped got
    # credited. The count-based form stays as the fallback for a report that
    # predates the field.
    aligned_ids = list(getattr(history, "response_ids", []))
    analyzed_count = int(
        summary.get("analyzed_message_count", summary["assistant_message_count"])
    )
    source_lines = summary.get("analyzed_source_lines")
    if isinstance(source_lines, list) and source_lines:
        window_ids = [
            aligned_ids[position - 1]
            for position in source_lines
            if isinstance(position, int) and 1 <= position <= len(aligned_ids)
        ]
        # Partial alignment is not alignment. Anything short of one id per
        # analyzed reply falls back to the day-scoped aggregate below, the
        # same way a missing id already does.
        if len(window_ids) != len(source_lines):
            window_ids = []
    else:
        window_ids = aligned_ids[-analyzed_count:] if analyzed_count > 0 else []
    # Message scope is only honest when EVERY analyzed reply is linkable. A
    # partial set (legacy rows without the key mixed with newer ones, or ids
    # belonging to messages the budget dropped) would let the panel label an
    # out-of-window aggregate as "handling for the latest N replies". Anything
    # short of full coverage falls back to the day-scoped aggregate.
    scoped_ids = (
        [str(response_id) for response_id in window_ids]
        if window_ids and all(window_ids)
        else []
    )
    logger.info(
        "[RepetitionInsights] character=%s language=%s limit=%s messages=%s "
        "analyzed=%s candidates=%s skipped=%s linked_ids=%s",
        lanlan_name,
        language,
        req.assistant_message_limit,
        summary["assistant_message_count"],
        summary.get("analyzed_message_count", summary["assistant_message_count"]),
        summary["candidate_count"],
        history.skipped_row_count,
        len(scoped_ids),
    )
    payload: dict[str, object] = {
        "success": True,
        "schema_version": report["schema_version"],
        "artifact_type": report["artifact_type"],
        "character_name": lanlan_name,
        "language": language,
        "parameters": parameters,
        "summary": summary,
        "candidates": report["candidates"],
    }
    if scoped_ids:
        # Internal-only join keys. The public router removes these before the
        # browser response, so runtime IDs never become UI/export data.
        #
        # Emitted ONLY when every analyzed reply carries one. Sending an empty
        # list still selects the message-scoped branch in
        # ``main_routers.memory_router``, which then reports "no linked records"
        # forever instead of falling back to the day-scoped aggregate that does
        # work. Assistant rows only carry the key when the writer preserved
        # ``additional_kwargs`` end to end; the streamed cross_server path that
        # feeds ``time_indexed_original`` today does not, so the fallback is the
        # normal case rather than an edge case.
        payload["_anti_repeat_response_ids"] = scoped_ids
    return payload


# Register external import routes in their original position (before
# ``/internal/memory/{lanlan_name}/persona`` so ``import_progress/persona``
# matches ``get_import_progress`` rather than ``get_private_persona_snapshot``).
from .external_import_routes import (  # noqa: F401, E402
    ExternalMemoryImportRequest,
    _IMPORT_PROGRESS,
    get_import_progress,
    import_external_markdown,
    _import_external_markdown,
)


def _activate_request_language(language: str | None) -> str:
    """Resolve the locale for this request without changing the process default.

    Falls back to the process-wide language when the request does not carry a
    usable one. That fallback is fine for the in-flight request, but it must not
    be persisted — see the ``language=request.language`` argument at each
    ``_spawn_outbox_post_turn_signals`` call site.
    """
    if is_supported_language_code(language):
        return normalize_language_code(language, format='full')
    return get_global_language_full()


async def _resolve_foreground_memory_language(
    lanlan_name: str,
    language: str | None,
    *,
    render_language: str | None = None,
) -> str:
    """Resolve foreground prompt locale without persisting a render fallback.

    Priority is explicit request > durable character preference > render-only
    fallback > process locale. Only callers decide whether ``language`` is
    durable evidence; this resolver never writes either input.

    Fail-soft on a durable-state read error. ``_load_locale_state_unlocked``
    raises ``PromptLocalePersistenceError`` on a transient ``OSError`` on
    purpose — a *writer* must never cache that as empty state or it would
    discard the real durable causal order. But this is a read for rendering
    only: a temporarily unreadable sidecar must not turn into a 500 that drops
    the caller's whole turn. Degrade to the request/process locale instead.
    """
    if is_supported_language_code(language):
        return _activate_request_language(language)
    try:
        durable_language = await asyncio.to_thread(
            locale_state.get_character_prompt_locale,
            lanlan_name,
        )
    except locale_state.PromptLocalePersistenceError:
        logger.warning(
            "[PromptLocale] %s: durable locale unreadable, rendering with the "
            "request fallback for this request",
            lanlan_name,
        )
        return _activate_request_language(render_language)
    if is_supported_language_code(durable_language):
        return _activate_request_language(durable_language)
    return _activate_request_language(render_language)


#: Upper bound on how many subjects one request may cost in durable-locale
#: lookups. Matches the scoped endpoints' documented ``1..8`` subject contract,
#: but is enforced independently so the resolver stays bounded even when it
#: runs ahead of an endpoint's own validation.
_SCOPED_LOCALE_LOOKUP_LIMIT = 8


def _locale_lookup_subjects(subjects) -> list:
    """Map the caller's subjects onto the canonical primaries to look up.

    Accepts either wire models or domain subjects — this resolver runs on the
    outer wrapper, i.e. before ``to_domain``. Coercion is local and does not
    change the resolver's signature. A malformed descriptor is passed through
    untouched: locale lookup must never be the thing that rejects a request.
    """
    from memory.scopes import MemoryScopeError, coerce_subject
    from memory.subject_identity import canonical_subject
    from memory import trust_store

    if not subjects:
        return []
    snap = trust_store.trust_snapshot()
    resolved: list = []
    seen: set[tuple[str, str]] = set()
    for raw in subjects:
        try:
            domain = coerce_subject(
                raw.model_dump() if hasattr(raw, "model_dump") else raw
            )
        except (MemoryScopeError, ValueError, TypeError):
            resolved.append(raw)
            continue
        if domain is None:
            resolved.append(raw)
            continue
        primary = canonical_subject(domain, snap)
        marker = (primary.key, primary.scope)
        if marker not in seen:
            seen.add(marker)
            resolved.append(primary)
    return resolved


async def _resolve_scoped_memory_language(
    lanlan_name: str,
    subjects,
    language: str | None,
) -> str:
    """Resolve scoped prompt locale: explicit request > subject > character.

    ``subjects`` arrives in the caller's own priority order (see
    ``_get_scoped_context``), so the first one carrying a durable locale wins.
    Without this chain a group request falls straight through to the calling
    process's locale, and the per-subject durable state is never read — which
    is the whole point of storing it.
    """
    if is_supported_language_code(language):
        return _activate_request_language(language)
    # Bounded on purpose: this resolver runs before the endpoint's own
    # ``1..8 subjects`` rejection, so an oversized list would otherwise
    # schedule one thread-pool lookup per supplied item on its way to a 422.
    # Bounding here (rather than requiring every caller to validate first)
    # keeps the work bound a property of the resolver itself.
    # L-1/L-2: resolve through the SAME canonical mapping the write side uses,
    # and feed only one subject per participant. Without the canonical step a
    # routed account reserves under S_canonical and reads under S_A, misses
    # forever and silently falls back to the character locale. Feeding the
    # expansion instead of the primaries would multiply this bounded
    # thread-pool budget by the number of accounts per person.
    # Slice BEFORE canonicalizing, not after: this resolver runs ahead of the
    # endpoint's own 1..8 rejection, and the comment above declares the bound to
    # be a property of the resolver itself. Canonicalizing the full list first
    # would do unbounded per-item work on input that is on its way to a 422.
    # For a valid request (<= 8 subjects) the two orders are identical, since
    # folding can only ever shrink the list.
    for subject in _locale_lookup_subjects(
        list(subjects or [])[:_SCOPED_LOCALE_LOOKUP_LIMIT]
    ):
        descriptor = (
            subject.model_dump()
            if hasattr(subject, "model_dump")
            else subject
        )
        try:
            durable = await locale_state.aget_subject_prompt_locale(
                lanlan_name,
                descriptor,
            )
        except locale_state.PromptLocalePersistenceError:
            # Same fail-soft contract as the character-level resolver: a
            # transient sidecar read error must not bubble out of a rendering
            # lookup and break the caller's fail-soft response contract.
            logger.warning(
                "[PromptLocale] %s: scoped locale unreadable, falling through "
                "to the character locale for this request",
                lanlan_name,
            )
            break
        except ValueError:
            # A malformed descriptor fails closed downstream (coerce_subject);
            # locale lookup must not be the thing that rejects the request.
            continue
        if is_supported_language_code(durable):
            return _activate_request_language(durable)
    return await _resolve_foreground_memory_language(lanlan_name, None)


# /new_dialog QPS observation: per-character cumulative calls; _periodic_new_dialog_qps_log_loop
# logs one INFO line every NEW_DIALOG_QPS_FLUSH_INTERVAL seconds, then resets. After A, observe
# whether proactive_chat truly drives memory_server load; if not, no need for
# main_server-side caching (C+ option).
_new_dialog_qps_counter: dict[str, int] = {}


_new_dialog_locale_generations: dict[str, int] = {}


NEW_DIALOG_QPS_FLUSH_INTERVAL = 60


def _promote_new_dialog_locale_generation(
    lanlan_name: str,
    generation: int,
) -> None:
    _new_dialog_locale_generations[lanlan_name] = max(
        _new_dialog_locale_generations.get(lanlan_name, 0),
        generation,
    )


def _format_legacy_settings_as_text(
    settings: dict,
    lanlan_name: str,
    language: str | None = None,
) -> str:
    """Convert legacy settings JSON into natural-language form, replacing the raw json.dumps output."""
    lang = _normalize_memory_prompt_lang(language or get_global_language_full())
    header = _loc(LEGACY_SETTINGS_HEADER, lang).format(name=lanlan_name)
    empty = _loc(LEGACY_SETTINGS_EMPTY, lang)
    if not settings:
        return header + empty

    sections = []
    for name, data in settings.items():
        if not isinstance(data, dict) or not data:
            continue
        lines = []
        for key, value in data.items():
            if value is None or value == '' or value == []:
                continue
            if isinstance(value, list):
                value_str = '、'.join(str(v) for v in value)
            elif isinstance(value, dict):
                parts = [f"{k}: {v}" for k, v in value.items() if v is not None and v != '']
                value_str = '、'.join(parts) if parts else str(value)
            else:
                value_str = str(value)
            lines.append(f"- {key}：{value_str}")
        if lines:
            section_header = _loc(
                LEGACY_SETTINGS_SECTION_HEADER,
                lang,
            ).format(subject=name)
            sections.append(section_header + "\n" + "\n".join(lines))

    if not sections:
        return header + empty
    return header + "\n" + "\n".join(sections)


async def _periodic_new_dialog_qps_log_loop():
    """Every NEW_DIALOG_QPS_FLUSH_INTERVAL seconds, log the /new_dialog call count and reset it.

    Logs a total=0 heartbeat even with no traffic — otherwise silence can't be
    distinguished between "genuinely zero traffic" and "the loop died".
    """
    while True:
        await asyncio.sleep(NEW_DIALOG_QPS_FLUSH_INTERVAL)
        snapshot = dict(_new_dialog_qps_counter)
        _new_dialog_qps_counter.clear()
        total = sum(snapshot.values())
        logger.debug(
            f"[QPS] /new_dialog last {NEW_DIALOG_QPS_FLUSH_INTERVAL}s: "
            f"total={total} per_char={snapshot}"
        )


@app.post("/reflect/{lanlan_name}", dependencies=[Depends(_refuse_browsers)])
async def api_reflect(lanlan_name: str):
    """Synthesize reflections + automatic state migration, returning the result.

    Centralized in the memory_server process, avoiding the absorbed-flag race
    caused by main_server instantiating locally.
    """
    lanlan_name = validate_lanlan_name(lanlan_name)
    reflection_result = None
    # Make auto_promote_stale fire-and-forget: with thinking enabled, one promote_merge call
    # can take 30–90s; serial confirmed reflections exceed the client's 15s
    # timeout. Periodic auto_promote runs every 180s as fallback, so this endpoint
    # can safely avoid waiting. Caller (system_router) only logs auto_transitions;
    # lost counts have no functional impact.
    # This 30–90s task outlives the request; rename/delete must drain it, or it will keep
    # writing reflection/persona state under the old identity after disposal.
    post_turn._track_character_post_turn_task(
        lanlan_name,
        runtime._spawn_background_task(_safe_auto_promote(lanlan_name)),
    )
    try:
        reflection_result = await locale_state.run_with_character_prompt_locale(
            lanlan_name,
            runtime.reflection_engine.reflect,
            lanlan_name,
        )
    except Exception as e:
        logger.debug(f"[ReflectAPI] {lanlan_name}: reflect 失败: {e}")
    return {
        "reflection": reflection_result,
        "auto_transitions": 0,  # Fire-and-forget: this call does not return actual counts.
    }


async def _safe_auto_promote(lanlan_name: str) -> None:
    """Fire-and-forget wrapper swallowing exceptions from reflection_engine.aauto_promote_*.

    Picks one of two based on the powerful-memory switch: on → score-driven +
    merge LLM; off → time-driven.
    """
    try:
        if await gates._ais_powerful_memory_enabled():
            operation = runtime.reflection_engine.aauto_promote_stale
        else:
            operation = runtime.reflection_engine.aauto_promote_time_driven
        await locale_state.run_with_character_prompt_locale(
            lanlan_name,
            operation,
            lanlan_name,
        )
    except Exception as e:
        logger.debug(f"[ReflectAPI] {lanlan_name}: 后台 auto_promote 失败: {e}")


async def _due_commitment_topic(lanlan_name: str) -> dict | None:
    """The open promise proactive chat may lead with, ``{id, text, due}``; None if none.

    At most one: due soon and not offered lately (see
    PinnedFactStore.get_due_commitment). Like the pinned section itself it
    is private-chat content, and proactive chat speaks in the private chat.
    """
    store = runtime.pinned_fact_store
    if store is None:
        return None
    try:
        entry = await store.aget_due_commitment(lanlan_name)
    except Exception as e:
        logger.debug(f"[ReflectAPI] {lanlan_name}: 承诺读取失败: {type(e).__name__}")
        return None
    if entry is None:
        return None
    return {"id": entry["id"], "text": entry["text"], "due": pinned_due_date(entry)}


async def _record_surfaced_commitments(lanlan_name: str, pinned_ids: list[str]) -> bool:
    """Start the offer cooldown of the promises proactive chat was given; True once written."""
    store = runtime.pinned_fact_store
    if store is None or not pinned_ids:
        return False
    try:
        written = await store.amark_surfaced(lanlan_name, pinned_ids)
    except Exception as e:
        logger.debug(f"[ReflectAPI] {lanlan_name}: 承诺 surfaced 记录失败: {type(e).__name__}")
        return False
    return bool(written)


@app.get("/followup_topics/{lanlan_name}")
async def api_followup_topics(lanlan_name: str):
    """Get follow-up topic candidates (does not mark them surfaced; the caller must call /record_surfaced afterwards).

    ``commitment`` is the open promise due soonest, offered ahead of the
    reflection topics; its id comes back through /record_surfaced like theirs.
    """
    lanlan_name = validate_lanlan_name(lanlan_name)
    try:
        topics = await runtime.reflection_engine.aget_followup_topics(lanlan_name)
    except Exception as e:
        logger.debug(f"[ReflectAPI] {lanlan_name}: get_followup_topics 失败: {e}")
        topics = []
    return {"topics": topics, "commitment": await _due_commitment_topic(lanlan_name)}


@app.post(
    "/record_surfaced/{lanlan_name}",
    dependencies=[Depends(_refuse_browsers), Depends(_require_json_content_type)],
)
async def api_record_surfaced(request: Request, lanlan_name: str):
    """Record which reflections this proactive chat mentioned, refreshing the cooldown.

    ``commitment_ids`` (the promise from /followup_topics, when it was
    offered) start that promise's own offer cooldown in the pinned section;
    ``commitments_recorded`` tells the caller whether that write landed, so
    a failed one is retried with the delivery record.
    """
    lanlan_name = validate_lanlan_name(lanlan_name)
    try:
        raw_body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail={"ok": False, "reason": "invalid_json"}) from exc
    if not isinstance(raw_body, dict):
        raise HTTPException(status_code=422, detail={"ok": False, "reason": "invalid_payload"})
    try:
        payload = RecordSurfacedRequest.model_validate(raw_body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail={"ok": False, "reason": "invalid_payload"}) from exc
    result: dict[str, object] = {"ok": True}
    commitment_ids = payload.commitment_ids
    if commitment_ids:
        result["commitments_recorded"] = await _record_surfaced_commitments(
            lanlan_name, commitment_ids,
        )
    reflection_ids = payload.reflection_ids
    if not reflection_ids:
        return result
    try:
        await runtime.reflection_engine.arecord_surfaced(lanlan_name, reflection_ids)
    except Exception as e:
        logger.debug(f"[ReflectAPI] {lanlan_name}: record_surfaced 失败: {e}")
    return result


@app.post("/internal/memory/{lanlan_name}/legacy_dedup/restore")
async def api_restore_legacy_dedup(request: Request, lanlan_name: str):
    """Bring back the facts the one-time duplicate cleanup folded away.

    They come back under new ids, so a peer that still holds the archived rows
    cannot fold them away again; the folded rows stay archived. A device that
    syncs should look at the result after its next sync.
    """
    from . import legacy_dedup
    # Loopback skips credentials, so a page in a browser could post here; only
    # the app's own services may (same rule as the knowledge routes).
    if any(header in request.headers for header in ("origin", "referer", "sec-fetch-site")):
        raise HTTPException(status_code=403, detail={"ok": False, "reason": "untrusted_origin"})
    lanlan_name = validate_lanlan_name(lanlan_name)
    restored = await legacy_dedup.restore(lanlan_name)
    return {"ok": True, "restored": restored, "restored_as_new_ids": True}


@app.post("/cache/{lanlan_name}")
async def cache_conversation(request: HistoryRequest, lanlan_name: str):
    """The "lightweight persistence" endpoint at every turn end: writes recent.json +
    stores into time_indexed.db + registers the per-turn signals outbox op
    (counter bump + local repetition sniffing + check_feedback). Does **not** run
    the Stage-1 fact_extract LLM — RFC §3.4.3 explicitly says "per-turn
    extract_facts is too expensive; move to background scheduling"; batch
    extraction is done by ``_periodic_signal_extraction_loop``, which pulls a
    window from ``time_indexed.db`` and runs Stage-1+Stage-2 at 10 accumulated
    turns or 5 min idle; nor does it run the review LLM rewriting history (that
    category is still run by /settle at session renew).

    History — commit cba377c5 ("Fix/memory hotswap timing", 2026-03-29)
    introduced /settle and gated "the LLM follow-up work left over from cache"
    entirely behind ``if input_history``, but cross_server's standard rhythm is
    "turn end /cache → renew session /settle(msgs=0)", so settle always received
    msgs=0 and both ``store_conversation`` and the outbox extract were silently
    skipped: ``time_indexed.db`` was never created (time perception broken) +
    ``outbox.ndjson`` / ``events.ndjson`` / ``facts.json`` never created
    (long-term memory + the evidence-RFC chain idling completely), **and the
    batch loop, which depends on the db for history, was paralyzed with it**.

    The fix moves store + post-turn signals back into the cache endpoint; at the
    same time the Stage-1 per-turn fact_extract that PR-1 had temporarily kept
    for "short-term behavior parity" (the ``legacy flow``) is migrated out too —
    the RFC always planned for only ``_periodic_signal_extraction_loop`` to run
    fact extraction. ``astore_conversation`` is a SQLite INSERT (~ms scale), and
    ``_spawn_outbox_post_turn_signals`` now only runs counter bump + local
    repetition sniffing + check_feedback (LLM only when surfaced has pending
    entries) — an ndjson append + spawned background task (non-blocking).
    ``cache`` keeps its "no LLM latency in the foreground" lightweight semantics,
    **and is lighter than the PR-1 implementation** — the per-turn fact_extract
    LLM waste is fully gone.
    """
    lanlan_name = validate_lanlan_name(lanlan_name)
    locale_admission_order = (
        locale_state.allocate_character_prompt_locale_order(lanlan_name)
        if is_supported_language_code(request.language)
        else None
    )
    # Same resolution as the sibling /process /renew /settle endpoints. Today
    # /cache runs update_history(compress=False), so nothing inside this
    # context reaches a prompt and the asymmetry is invisible — but any future
    # prompt work moved in here would silently render in the caller's process
    # locale instead of the character's durable one.
    memory_language = await _resolve_foreground_memory_language(
        lanlan_name,
        request.language,
        render_language=request.render_language,
    )
    with language_context(memory_language):
        gates._touch_activity()
        try:
            input_history = convert_to_messages(json.loads(request.input_history))
            if not input_history:
                if _KEYED_CACHE_KEY_RE.fullmatch(str(request.idempotency_key or "").strip()) is not None:
                    # Keyed writes cannot use the empty-batch-is-cached shortcut: callers would treat diary writing as complete.
                    return JSONResponse(
                        {"status": "error", "message": "keyed cache writes need a non-empty batch"},
                        status_code=422,
                    )
                return {"status": "cached", "count": 0}
            theater_episode_batch = (
                len(input_history) == 1
                and is_theater_episode_summary(input_history[0])
            )
            idempotency_key = str(request.idempotency_key or "").strip()
            if len(idempotency_key) > 160:
                return {"status": "error", "message": "idempotency_key_too_long"}
            if (
                not theater_episode_batch
                and _KEYED_CACHE_KEY_RE.fullmatch(idempotency_key) is not None
            ):
                # Visit "save as diary" (OD-16 v4): character-level idempotency; success requires confirmed persistence in both stores,
                # with no post-turn processing registration.
                return await _cache_keyed(
                    lanlan_name, request.input_history, input_history, idempotency_key,
                )
            # Writes without a visit-diary key strip all caller-provided idempotency_key metadata,
            # otherwise an ordinary message could impersonate an already-written diary.
            from utils.recent_file import strip_idempotency_metadata

            strip_idempotency_metadata(input_history)
            if idempotency_key and not theater_episode_batch:
                # Only a theater episode write is idempotent: it upserts one
                # capsule per Session and the key fences retracted attempts.
                # Ordinary batches have no dedupe, so refuse rather than
                # silently appending a retry twice.
                return {"status": "error", "message": "idempotency_key_requires_theater_episode"}
            if _has_human_messages(input_history):
                await gates._aclear_review_clean(lanlan_name)
            logger.info(f"[MemoryServer] cache: {lanlan_name} +{len(input_history)} 条消息")
            uid = str(uuid4())
            retracted_request = False
            theater_index_events = {}
            async with runtime._get_settle_lock(lanlan_name):
                if theater_episode_batch:
                    # Theater cold archives hold full scenario text; recent updates only a summary capsule
                    # per Session, so resuming after pause does not append the entire original again.
                    previous_theater_history = await runtime.recent_history_manager.aget_recent_history(
                        lanlan_name
                    )
                    try:
                        stored_episode = await runtime.recent_history_manager.upsert_theater_episode(
                            input_history[0],
                            lanlan_name,
                            archive_request_id=idempotency_key,
                            archive_attempt=request.theater_archive_attempt,
                            forget_marker=request.theater_forget_marker,
                        )
                    except TheaterEpisodeRetracted:
                        # The player declined this archive (or forgot the story)
                        # while the request was still in flight; the tombstone was
                        # checked under the same settle lock the retraction holds.
                        retracted_request = True
                    else:
                        input_history = [stored_episode]
                        updated_theater_history = await runtime.recent_history_manager.aget_recent_history(
                            lanlan_name
                        )
                        theater_index_events = _theater_index_events(
                            lanlan_name,
                            updated_theater_history,
                        )
                else:
                    await runtime.recent_history_manager.update_history(
                        input_history,
                        lanlan_name,
                        compress=False,
                    )
                if retracted_request:
                    pass
                elif theater_episode_batch:
                    # Rebuild theater time index from recent as the sole hot-memory baseline:
                    # evict excess playthroughs and full-text rows left from before the upgrade together.
                    try:
                        await runtime.time_manager.areconcile_theater_conversations(
                            theater_index_events,
                            lanlan_name,
                        )
                    except Exception:
                        try:
                            await runtime.recent_history_manager.restore_theater_cache_snapshot(
                                lanlan_name,
                                previous_theater_history,
                                updated_theater_history,
                            )
                        except Exception:
                            logger.exception("[MemoryServer] 剧场时间索引失败后 recent 回滚失败")
                        raise
                else:
                    # store_conversation must run under lock, serialized with update_history, symmetric
                    # with /process / /renew paths, preserving per-character DB write order.
                    await runtime.time_manager.astore_conversation(
                        uid,
                        input_history,
                        lanlan_name,
                    )
            if retracted_request:
                logger.info(f"[MemoryServer] cache: {lanlan_name} dropped a retracted theater archive write")
                return {"status": "retracted", "count": 0}
            if theater_episode_batch:
                # Theater capsules are already committed above. Ordinary
                # reflection/correction signals must not run for this archive.
                return {"status": "cached", "count": len(input_history)}
            # Register outbox outside the lock: it spawns background LLM tasks; holding the lock
            # would block next-turn /cache persistence.
            await post_turn._spawn_outbox_post_turn_signals(
                lanlan_name, input_history, language=request.language,
                render_language=request.render_language,
                locale_admission_order=locale_admission_order,
            )
            return {"status": "cached", "count": len(input_history)}
        except Exception as e:
            logger.error(f"[MemoryServer] cache 失败: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}


_RESERVED_KEY_PREFIXES = ("visit-debrief:", "visit-facts:")


def _keyed_cache_session_id(key: str) -> str:
    """The ``time_indexed.db`` session id of a keyed write (derived from the key, never random)."""
    from . import idempotency

    return f"idem-{idempotency.key_digest(key)}"


def _canonical_history_hash(raw: str) -> str:
    data = json.loads(raw)
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _keyed_unconfirmed(message: str) -> JSONResponse:
    # 503: keep key pending; caller retries this transient failure with the same key.
    return JSONResponse({"status": "error", "message": message}, status_code=503)


async def _cache_keyed(lanlan_name: str, raw_history: str, input_history: list, key: str):
    """Keyed ``/cache`` write, idempotent per character (design section 4.6 ``/cache``).

    The key record goes ``pending`` (with the canonical content hash) before
    anything is stored and ``done`` only after both stores confirmed: first
    ``time_indexed.db`` (rows under a session id derived from the key), then
    ``recent.json`` (entries carry the key in their metadata). A retry of a
    key that is not ``done`` decides from the stores themselves: a recent
    entry or retired-key record carrying the key proves it was written (then
    nothing is written); otherwise the time-index row is written unless
    present, then recent. A different payload under the same key is refused
    (422), even once ``done``. Unconfirmed writes answer 503 and leave the key
    ``pending``. Post-turn signals are never registered for keyed writes.
    Errors are returned, never raised: the caller's ``except`` turns anything
    raised into a 200 ``status:'error'``.
    """
    from utils.recent_file import RECENT_IDEMPOTENCY_META_KEY

    from . import idempotency

    if any(getattr(message, "type", "") != "ai" for message in input_history):
        # Keyed writes serve only save-as-diary AI monologue batches: no postprocessing; recovery checks both stores only.
        return JSONResponse({"status": "error", "message": "keyed cache writes accept assistant messages only"},
                            status_code=422)
    content_hash = _canonical_history_hash(raw_history)
    for message in input_history:
        message.metadata = {**(message.metadata or {}), RECENT_IDEMPOTENCY_META_KEY: key}
    session_id = _keyed_cache_session_id(key)
    async with idempotency.key_lock(lanlan_name, key):
        try:
            record = await idempotency.read_key(lanlan_name, key)
        except (idempotency.IdempotencyStateError, OSError):
            return _keyed_unconfirmed("idempotency state unreadable; retry later")
        if record is not None and record.get("kind") != "cache":
            # Key records are shared with keyed /scoped_history: same-name keys from other endpoints cannot prove this endpoint wrote.
            return JSONResponse({"status": "error", "message": "idempotency_key belongs to another endpoint"},
                                status_code=422)
        stored_hash = record.get("content_hash") if record is not None else None
        if stored_hash is not None and stored_hash != content_hash:
            # One diary per visit (client retries the same persisted body): changed content is caller error, not retryable failure;
            # even when done, newly supplied unpersisted content must not be reported cached.
            return JSONResponse({"status": "error", "message": "idempotency_key reused with different content"},
                                status_code=422)
        if (record is not None and record.get("state") in idempotency.TERMINAL_KEY_STATES
                and isinstance(stored_hash, str) and stored_hash):
            return {"status": "cached", "duplicate": True, "count": 0}
        # Missing content hash is malformed: never trust it as written; check both stores as if no record exists.
        try:
            async with runtime._get_settle_lock(lanlan_name):
                # Check storage evidence first: this key present in recent or removed through retirement proves prior writing;
                # write neither store again (cloud restore rolling back time_indexed.db must not refill restored-away diary index rows).
                # Perform no bookkeeping writes first either: return duplicate truthfully even when cloud-maintenance blocks writes.
                outcome = await runtime.recent_history_manager.keyed_state(lanlan_name, key)
                if outcome == "absent":
                    await idempotency.update_key(
                        lanlan_name, key,
                        idempotency.transition(idempotency.KEY_STATE_PENDING, content_hash=content_hash,
                                               kind="cache"),
                    )
                    had_row = await runtime.time_manager.ahas_conversation(session_id, lanlan_name)
                    if not had_row:
                        await runtime.time_manager.astore_conversation_strict(
                            session_id, input_history, lanlan_name,
                        )
                    outcome = await runtime.recent_history_manager.append_keyed(input_history, lanlan_name, key)
        except Exception as exc:  # noqa: BLE001 - Either store unconfirmed: keep key pending, return 503 for caller retry.
            logger.warning(f"[MemoryServer] keyed cache {lanlan_name}: not confirmed: {exc!r}")
            return _keyed_unconfirmed("keyed write not confirmed; retry later")
        # Both stores confirmed (written now or earlier): done only saves future checks. Failure to record it (cloud maintenance /
        # temporarily unwritable key file) does not affect truthful results: storage proves writing; retries return duplicate.
        try:
            # Record this request's hash only if its content was actually written to both stores now: present / retired proves only prior key writing,
            # not body equality; filling a missing hash from this request could incorrectly bind another content's hash to the key.
            await idempotency.update_key(lanlan_name, key, _done_repairing(
                content_hash=content_hash if outcome == "written" else None, kind="cache"))
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[MemoryServer] keyed cache {lanlan_name}: done not recorded: {exc!r}")
    if outcome != "written":
        return {"status": "cached", "duplicate": True, "count": 0}
    logger.info(f"[MemoryServer] cache: {lanlan_name} keyed +{len(input_history)} 条消息")
    return {"status": "cached", "count": len(input_history)}


def _done_repairing(**fields: object):
    """``update_key`` callback: move the record to ``done``, also repairing a malformed terminal one.

    A terminal record that lacks a usable identity (``content_hash``) or
    the counters this endpoint writes (``written`` / ``deduped``) was just
    reconciled against the stores; it gets those fields back so the next
    request compares against them. A well-formed terminal record and a
    cancelled one are left untouched.
    """
    from . import idempotency

    move = idempotency.transition(idempotency.KEY_STATE_DONE, **fields)

    def _fn(old: dict | None) -> dict | None:
        if old is None or old.get("state") not in idempotency.TERMINAL_KEY_STATES:
            return move(old)
        if old.get("state") != idempotency.KEY_STATE_DONE:
            return None
        stored = old.get("content_hash")
        hash_ok = isinstance(stored, str) and bool(stored)
        # Counts must match just-verified storage values, not merely be nonnegative integers; write corrected counts on mismatch.
        counters_ok = all(
            isinstance(old.get(name), int) and not isinstance(old.get(name), bool) and old.get(name) == fields[name]
            for name in ("written", "deduped") if fields.get(name) is not None
        )
        needs_hash = not hash_ok and bool(fields.get("content_hash"))
        if not needs_hash and counters_ok:
            # Intact, or missing hash still lacks a verified value this time: leave unchanged.
            return None
        record = dict(old)
        record.update({name: value for name, value in fields.items() if value is not None})
        record["updated_at"] = time.time()
        return record

    return _fn


# Register visit/theater routes in their original slot (after /cache, before /process).
from .theater_visit_routes import (  # noqa: F401, E402
    TheaterMemoryForgetRequest,
    TheaterEpisodeRetractRequest,
    _theater_story_event_id,
    _theater_index_events,
    _theater_memory_render_state,
    _iter_theater_rendered_history_unbounded,
    _iter_theater_rendered_history,
    _VISIT_FACT_IMPORTANCE,
    VisitFactItem,
    VisitFactsRequest,
    write_visit_facts,
    list_theater_memory_stories,
    _drop_theater_memory_reindexed,
    forget_theater_memory,
    retract_theater_episode,
)


@app.post("/process/{lanlan_name}")
async def process_conversation(request: HistoryRequest, lanlan_name: str):
    lanlan_name = validate_lanlan_name(lanlan_name)
    locale_admission_order = (
        locale_state.allocate_character_prompt_locale_order(lanlan_name)
        if is_supported_language_code(request.language)
        else None
    )
    memory_language = await _resolve_foreground_memory_language(
        lanlan_name,
        request.language,
        render_language=request.render_language,
    )
    with language_context(memory_language):
        gates._touch_activity()
        # P2 vector warmup: first /process is the cheapest "frontend ready"
        # signal we have — by the time the user sends a real conversation
        # turn, greeting and prominent drain are over. notify_first_process
        # is a setflag, not async, so it doesn't add latency to /process.
        if runtime.embedding_warmup_worker is not None:
            runtime.embedding_warmup_worker.notify_first_process()
        try:
            # Check character exists in config; if absent, log and continue (new characters allowed).
            try:
                character_data = await runtime._config_manager.aload_characters()
                catgirl_names = list(character_data.get('猫娘', {}).keys())
                if lanlan_name not in catgirl_names:
                    logger.info(f"[MemoryServer] 角色 '{lanlan_name}' 不在配置中，但继续处理（可能是新创建的角色）")
            except Exception as e:
                logger.warning(f"检查角色配置失败: {e}，继续处理")

            uid = str(uuid4())
            input_history = convert_to_messages(json.loads(request.input_history))
            if _has_human_messages(input_history):
                await gates._aclear_review_clean(lanlan_name)
            logger.info(f"[MemoryServer] 收到 {lanlan_name} 的对话历史处理请求，消息数: {len(input_history)}")
            # Not under the settle lock, but a starting session waits for this request to come
            # back (its memory writes are flushed first), so its summary is preemptible too.
            with runtime._summary_preemption(lanlan_name) as preempted:
                await runtime.recent_history_manager.update_history(
                    input_history,
                    lanlan_name,
                    on_compress_done=review._on_compress_done,
                    preempted=preempted,
                )
            await runtime.time_manager.astore_conversation(uid, input_history, lanlan_name)

            # Asynchronous fact extraction (nonblocking response, silently skip failures).
            await post_turn._spawn_outbox_post_turn_signals(
                lanlan_name, input_history, language=request.language,
                render_language=request.render_language,
                locale_admission_order=locale_admission_order,
            )

            # Phase C: no more cancel-and-restart review. maybe_spawn_review decides after new-message,
            # min_interval, and in-flight gates. Running reviews finish and patch the editable tail
            # of current history themselves, retaining new messages untouched.
            await review.maybe_spawn_review(lanlan_name)

            return {"status": "processed"}
        except Exception as e:
            logger.error(f"处理对话历史失败: {e}")
            return {"status": "error", "message": str(e)}


@app.post("/renew/{lanlan_name}")
async def process_conversation_for_renew(request: HistoryRequest, lanlan_name: str):
    lanlan_name = validate_lanlan_name(lanlan_name)
    locale_admission_order = (
        locale_state.allocate_character_prompt_locale_order(lanlan_name)
        if is_supported_language_code(request.language)
        else None
    )
    memory_language = await _resolve_foreground_memory_language(
        lanlan_name,
        request.language,
        render_language=request.render_language,
    )
    with language_context(memory_language):
        gates._touch_activity()
        # Same warmup hint as /process: /renew is also a "user actively
        # using the app" signal, so it counts as the unblock event.
        if runtime.embedding_warmup_worker is not None:
            runtime.embedding_warmup_worker.notify_first_process()
        try:
            # Check character exists in config; if absent, log and continue (new characters allowed).
            try:
                character_data = await runtime._config_manager.aload_characters()
                catgirl_names = list(character_data.get('猫娘', {}).keys())
                if lanlan_name not in catgirl_names:
                    logger.info(f"[MemoryServer] renew: 角色 '{lanlan_name}' 不在配置中，但继续处理（可能是新创建的角色）")
            except Exception as e:
                logger.warning(f"检查角色配置失败: {e}，继续处理")

            uid = str(uuid4())
            input_history = convert_to_messages(json.loads(request.input_history))
            if _has_human_messages(input_history):
                await gates._aclear_review_clean(lanlan_name)
            logger.info(f"[MemoryServer] renew: 收到 {lanlan_name} 的对话历史处理请求，消息数: {len(input_history)}")
            # Lock first-turn summary, blocking /new_dialog until summary and timestamp persist.
            # A cold-start /new_dialog preempts the summary instead (see runtime._preempting_summaries).
            async with runtime._get_settle_lock(lanlan_name):
                with runtime._summary_preemption(lanlan_name) as preempted:
                    await runtime.recent_history_manager.update_history(
                        input_history,
                        lanlan_name,
                        detailed=True,
                        on_compress_done=review._on_compress_done,
                        preempted=preempted,
                    )
                await runtime.time_manager.astore_conversation(uid, input_history, lanlan_name)

            # Run the following outside lock, without blocking /new_dialog.
            # Asynchronous fact extraction.
            await post_turn._spawn_outbox_post_turn_signals(
                lanlan_name, input_history, language=request.language,
                render_language=request.render_language,
                locale_admission_order=locale_admission_order,
            )

            # Phase C: see /process comment; no more cancel-and-restart.
            await review.maybe_spawn_review(lanlan_name)

            return {"status": "processed"}
        except Exception as e:
            return {"status": "error", "message": str(e)}


@app.post("/settle/{lanlan_name}")
async def settle_conversation(request: HistoryRequest, lanlan_name: str):
    """Settle the conversation already cached via /cache: trigger summary compression + timestamp writes + fact extraction.

    Called by cross_server's renew session when it finds the increment is 0 (all
    messages already /cache'd). /cache only does update_history(compress=False)
    without triggering LLM summarization or time_manager writes; this endpoint
    completes those operations.
    """
    lanlan_name = validate_lanlan_name(lanlan_name)
    locale_admission_order = (
        locale_state.allocate_character_prompt_locale_order(lanlan_name)
        if is_supported_language_code(request.language)
        else None
    )
    memory_language = await _resolve_foreground_memory_language(
        lanlan_name,
        request.language,
        render_language=request.render_language,
    )
    with language_context(memory_language):
        gates._touch_activity()
        try:
            uid = str(uuid4())
            input_history = convert_to_messages(json.loads(request.input_history))
            if _has_human_messages(input_history):
                await gates._aclear_review_clean(lanlan_name)
            logger.info(f"[MemoryServer] settle: 收到 {lanlan_name} 的结算请求，消息数: {len(input_history)}")

            async with runtime._get_settle_lock(lanlan_name):
                if input_history:
                    await runtime.time_manager.astore_conversation(uid, input_history, lanlan_name)
                with runtime._summary_preemption(lanlan_name) as preempted:
                    await runtime.recent_history_manager.update_history(
                        [],
                        lanlan_name,
                        detailed=True,
                        on_compress_done=review._on_compress_done,
                        preempted=preempted,
                    )

            if input_history or is_supported_language_code(request.language):
                await post_turn._spawn_outbox_post_turn_signals(
                    lanlan_name, input_history, language=request.language,
                    render_language=request.render_language,
                    locale_admission_order=locale_admission_order,
                )

            # Phase C: see /process comment; no more cancel-and-restart.
            await review.maybe_spawn_review(lanlan_name)

            return {"status": "settled"}
        except Exception as e:
            logger.error(f"[MemoryServer] settle 失败: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}


def _screen_guarded_recent_history(history):
    """Recent history as it may be rendered into a new session's prompt.

    Session renewal and restarts bring this history back as system-prompt
    text, where the offline client's request-view projection never sees it.
    Apply the same screen-chain rewrite here, on the structured messages
    before they are flattened. What follows this history in the new session
    is the user speaking, so the run at its end counts as the one before the
    current turn (``trailing_turn``). The independent-delivery marker does
    not survive this store, so an unmarked run is judged by position alone.
    Known boundary: core renders its own session cache right after this
    history and judges it separately (``NotifyMixin._convert_cache_to_str``),
    so a chain split between the two is not joined.

    Tool-call markup a reply once spoke instead of calling the tool is taken
    out first (``_without_tool_call_leaks``), so a call the model wrote into
    an earlier reply is not shown to the next session as something to copy.
    """
    return project_screen_history(_without_tool_call_leaks(history), trailing_turn=True)


def _without_tool_call_leaks(history) -> list:
    """Recent history with tool-call markup cut from the character's lines.

    Memory keeps no tool registry, so only syntax that is a tool call
    whatever the tool is cut (``strip_tool_call_leaks`` without names). The
    stored messages are not changed; a line left empty is not rendered.
    Already stored replies need no migration: this runs on every render.
    """
    cleaned = []
    for message in history:
        content = getattr(message, "content", None)
        if getattr(message, "type", None) != "ai" or is_theater_memory_message(message):
            cleaned.append(message)
            continue
        if isinstance(content, str):
            text = strip_tool_call_leaks(content)
            if text == content:
                cleaned.append(message)
                continue
            if not text.strip():
                continue
            message = copy.copy(message)
            message.content = text
        elif isinstance(content, list):
            text_at = [
                index for index, part in enumerate(content)
                if isinstance(part, dict) and part.get("type") == "text"
                and isinstance(part.get("text"), str)
            ]
            parts = list(content)
            # One stream over every text part: a call may be split across them.
            for index, text in zip(text_at, strip_tool_call_leaks_from_parts(
                [content[index]["text"] for index in text_at],
            )):
                parts[index] = {**content[index], "text": text}
            if parts == content:
                cleaned.append(message)
                continue
            # The renderers show text parts only: a reply left with nothing
            # but other parts would render as an empty line.
            if not any(
                isinstance(part, dict) and part.get("type") == "text"
                and str(part.get("text") or "").strip()
                for part in parts
            ):
                continue
            message = copy.copy(message)
            message.content = parts
        cleaned.append(message)
    return cleaned


# Below the game pregame caller's 5s HTTP timeout.
_RECENT_HISTORY_LOCK_TIMEOUT_SECONDS = 4.0


@app.get("/get_recent_history/{lanlan_name}")
async def get_recent_history(lanlan_name: str, language: str | None = None):
    lanlan_name = validate_lanlan_name(lanlan_name)
    _lang = _normalize_memory_prompt_lang(_activate_request_language(language))
    # The only caller (game pregame) gives up after 5s and falls back to empty history.
    # One deadline covers every step that can queue on the default executor or the recent
    # lock, and the worker thread's own lock wait ends at it too instead of outliving the 503.
    deadline = time.monotonic() + _RECENT_HISTORY_LOCK_TIMEOUT_SECONDS
    try:
        async with asyncio.timeout(_RECENT_HISTORY_LOCK_TIMEOUT_SECONDS):
            return await _render_recent_history(lanlan_name, _lang, deadline)
    except RecentFileLockTimeout:
        # Already reported by the lock or the manager; one stuck wait counts once.
        return JSONResponse(
            {"status": "error", "message": "recent history is busy"}, status_code=503,
        )
    except TimeoutError:
        from utils.stall_report import report_stall
        report_stall(
            f"/get_recent_history {lanlan_name} {_RECENT_HISTORY_LOCK_TIMEOUT_SECONDS}s 内未完成，返回 503",
            loop=asyncio.get_running_loop(),
        )
        return JSONResponse(
            {"status": "error", "message": "recent history is busy"}, status_code=503,
        )


async def _render_recent_history(lanlan_name: str, _lang: str, deadline: float):
    # Check whether character exists in configuration.
    try:
        character_data = await runtime._config_manager.aload_characters()
        catgirl_names = list(character_data.get('猫娘', {}).keys())
        if lanlan_name not in catgirl_names:
            logger.warning(f"角色 '{lanlan_name}' 不在配置中，返回空历史记录")
            return _loc(NO_RECENT_HISTORY, _lang)
    except Exception as e:
        logger.error(f"检查角色配置失败: {e}")
        return _loc(NO_RECENT_HISTORY, _lang)

    raw_history = await runtime.recent_history_manager.aget_recent_history(
        lanlan_name, lock_timeout=max(0.0, deadline - time.monotonic()),
    )
    history = _screen_guarded_recent_history(raw_history)
    master_name, _, _, _, name_mapping, _, _, _, _ = await runtime._config_manager.aget_character_data()
    name_mapping['ai'] = lanlan_name
    result = _loc(RECENT_HISTORY_INTRO, _lang).format(name=lanlan_name)
    rendered_history = await asyncio.to_thread(lambda: list(_iter_theater_rendered_history(
        history, lang=_lang, name=lanlan_name, master=master_name,
    )))
    for i, capsule_text in rendered_history:
        if capsule_text is not None:
            result += capsule_text + "\n"
            continue
        if isinstance(i.content, str):
            content = i.content
        else:
            texts = [j['text'] for j in i.content if isinstance(j, dict) and j.get('type') == 'text']
            content = "\n".join(texts)
        if i.type == 'system':
            result += content + "\n"
        else:
            speaker = name_mapping.get(i.type, i.type)
            result += f"{speaker} | {content}\n"
    return result


@app.get("/search_for_memory/{lanlan_name}/{query}")
async def get_memory(
    query: str,
    lanlan_name: str,
    language: str | None = None,
):
    """**Deprecated** — the old GET endpoint is kept only to avoid breaking old
    callers; new callers use POST ``/query_memory/{lanlan_name}`` for structured
    results. This endpoint keeps returning placeholder text to discourage the old
    path from coming back (semantic recall was taken off this GET long ago).
    """
    lanlan_name = validate_lanlan_name(lanlan_name)
    _lang = _normalize_memory_prompt_lang(_activate_request_language(language))
    return (
        _loc(MEMORY_RECALL_HEADER, _lang).format(name=lanlan_name)
        + query
        + "\n\n"
        + _loc(MEMORY_RESULTS_HEADER, _lang).format(name=lanlan_name)
        + "\n"
        + _loc(MEMORY_UNAVAILABLE_NOTICE, _lang)
    )


from .scoped_trust_helpers import (  # noqa: F401
    MemorySubjectRequest,
    ScopedFactInput,
    ScopedFactsWriteRequest,
    _ACTIVITY_EVENT_ID_PATTERN,
    _SPEAKER_CHANNEL_PATTERN,
    _IDEMPOTENCY_KEY_PATTERN,
    ActivityEvent,
    ScopedHistorySegment,
    ScopedHistoryRequest,
    _resolve_trust_source,
    _count_stranded_rows,
    _merge_forget_stats,
    _forget_fanout_targets,
    _fold_request_subjects,
    _trust_snapshot_for_request,
    _apply_canonical_write_routing,
    _stamp_resolved_trust,
    _trust_mutation_for,
    _apply_trust_for_segments,
    _trust_response_block,
    ScopedContextRequest,
    ScopedMentionsRequest,
    _sanitized_display_name,
    _stamp_subject_display_name,
)
# Register scoped history / forget / trust / identity routes in their original
# region (after /search_for_memory, before /query_memory and /{lanlan_name}/persona).
from .scoped_keyed_routes import (  # noqa: F401, E402
    _KEYED_ITEM_LOCALE,
    _KEYED_ITEM_FACTS,
    _KEYED_ITEM_DISPLAY_NAME,
    _KEYED_STAGING_CANCELLED,
    _DISPLAY_NAME_MAX_ATTEMPTS,
    _MALFORMED,
    _SEGMENT_DROPPED_BY_FORGET,
    _reject_owner_signal_on_keyed_request,
    _keyed_null_trust_block,
    _keyed_fact_identity,
    _keyed_duplicate_response,
    _keyed_request_hash,
    _keyed_staging_items_valid,
    _KEYED_DROP_MARKERS,
    _RESTORED_PROVENANCE_FIELDS,
    _restored_provenance_valid,
    _keyed_staging_matches,
    _generate_keyed_facts,
    _reserve_keyed_locale_orders,
    _build_keyed_staging,
    _apply_keyed_item,
    _restore_missing_display_items,
    _capture_forget_generations,
    _persist_fence_marks,
    _record_fence_fallback,
    _segment_fence_keys,
    _fenced_journal_keys,
    _staged_segment_subjects,
    _apply_keyed_staging,
    _keyed_response,
    _process_scoped_history_keyed,
)
from .scoped_keyed_cancel import (  # noqa: F401, E402
    _fenced_segments,
    _without_trust_mutation,
    _mark_items_forgotten_during_generation,
    _fingerprint_of_staging,
    _drop_leftover_staging,
    _staging_identity,
    _forget_fences,
    _wire_after_forget,
    _staged_after_forget,
    _stale_record_marks,
    _staged_subject_keys,
    _segment_subject_keys,
    _items_shape_valid,
    _applied_seqs_valid,
    _drop_forgotten_segments,
    _rewrite_forgotten_segments,
    _drop_segments_for_keys,
    _forgotten_keys_of,
    _mark_segments_dropped,
    _stripped_item,
    _is_cancelled_staging_marker,
    _cancelled_staging_marker,
    _cancel_staged_writes_for_subjects,
)
from .scoped_history_routes import (  # noqa: F401, E402
    append_scoped_facts,
    process_scoped_history,
    _process_scoped_history,
    _process_scoped_history_segments,
    MemoryTurnContextRequest,
    memory_turn_context,
    get_scoped_context,
    _get_scoped_context,
    record_scoped_mentions,
)
from .scoped_forget_routes import (  # noqa: F401, E402
    ScopedForgetRequest,
    _FORGET_TARGET_EPOCHS_MAX,
    forget_scoped_subject,
    _FORGET_FENCE_ATTEMPTS,
    _FanoutChangedUnderFence,
    _forget_fence_keys,
    _forget_scoped_subject,
    _validate_target_epochs,
    _fanout_target_epochs,
    _targets_still_to_erase,
    _erase_subject_keys,
    _retained_target_keys,
    _raise_fanout_fences,
    _recorded_fence,
    _forget_epoch_already_erased,
    _ForgetAlreadyErased,
    _forget_duplicate_after_cancel,
    _run_forget,
    _forget_duplicate_response,
    _read_json_list_for_listing,
    _read_locale_subjects_for_listing,
    _read_staged_subjects_for_listing,
    _read_correction_subjects_for_listing,
    _read_persona_for_listing,
    _FORGET_EPOCHS_MAX_SUBJECTS,
    get_forget_epochs,
    list_scoped_subjects,
)
from .trust_identity_routes import (  # noqa: F401, E402
    TrustLegacyImportRequest,
    TrustWaiveBarrierRequest,
    TrustReconcileRequest,
    IdentityBindRequest,
    IdentityAccountRequest,
    IdentityMergeRequest,
    IdentityScopeDeclareRequest,
    IdentityEntityRequest,
    _identity_error,
    _require_loaded_identity_pool,
    get_trust_profile,
    import_legacy_trust_profiles,
    waive_legacy_trust_barrier,
    reconcile_trust_from_facts,
    declare_identity_scope,
    ensure_identity_account,
    bind_identity_account,
    unbind_identity_account,
    merge_identity_entities,
    forget_identity_entity,
)


class QueryMemoryRequest(BaseModel):
    # query / time are optional; at least one valid value suffices (time-only is newly supported).
    # Empty inputs do not error: hybrid_recall short-circuits empty queries to empty results; callers
    # translate those to "no relevant memory found", matching the endpoint's design never to kill
    # tool calls for recall failure/empty input. Thus no strict 422/400 validation here.
    query: str | None = None
    # Optional temporal recall restricts search to that window. With query, combine semantic +
    # temporal retrieval (query ranking within window); with time only, return facts/reflections
    # nearest by event time. Formats: memory.temporal.parse_time_window
    # (hour / day / month / year / interval). Missing or invalid time uses ordinary full-pool
    # semantic retrieval.
    time: str | None = None
    # Explicit read boundary for group-chat callers. Omitting the field keeps
    # the pre-upgrade legacy-private behaviour. Supplying one or more subjects
    # excludes every unscoped legacy row; there is intentionally no request
    # flag that lets a plugin turn legacy-private into a wildcard corpus.
    # An explicit empty list is a caller contract bug and is rejected 422 at
    # the endpoint (fail-closed) — it must never fall back to legacy private.
    subjects: list[MemorySubjectRequest] | None = None
    language: str | None = None
    # recall_memory's budget: past it the vector half is dropped and the
    # keyword half still answers. Omitted: no limit (legacy callers).
    budget_ms: int | None = None


@app.post("/query_memory/{lanlan_name}")
async def query_memory(lanlan_name: str, req: QueryMemoryRequest):
    """Hybrid retrieval entry point — BM25 + cosine embedding parallel recall + RRF fusion.

    POST body: ``{"query": "<natural language query>", "time": "<optional ISO time>"}``

    Returns the structured result of ``hybrid_recall`` (see the
    ``memory.hybrid_recall`` docstring). ``main_server``'s ``recall_memory`` tool
    handler calls this endpoint for results, then formats them for the model.

    Routing (the three query / time combinations):
    - **query + time**: ``hybrid_recall(query, time_window=...)`` — first
      hard-filters the candidate pool by event time window, then runs semantic
      retrieval over the in-window entries ("memories related to query from that
      period").
    - **time only**: ``recall_by_time`` — returns the facts + reflections closest
      to that window by event-time anchor, without semantic scoring ("what
      happened that day/week").
    - **query only**: ``hybrid_recall(query)`` — full semantic retrieval.
    - When time parsing fails, treat it as "no time given" and fall back to pure
      query semantic retrieval (one bad time must not swallow the query's
      semantic recall and return empty, Codex P2).

    ⚠️ Candidate scope, thresholds, and budget are all configured in
    ``config.HYBRID_RECALL_*``; persona never enters the pool as a block (it's
    already rendered into the system prompt routinely), facts + reflections take
    the full path, facts_archive only enters the BM25 pool.
    """
    lanlan_name = validate_lanlan_name(lanlan_name)
    if runtime.fact_store is None or runtime.reflection_engine is None:
        raise HTTPException(
            status_code=503,
            detail="memory_server not fully initialized (limited mode or startup incomplete)",
        )
    time_spec = (req.time or "").strip()
    query_text = (req.query or "").strip()
    # Fail-closed on an explicit empty subjects list (mirror scoped_context):
    # a group-chat caller that has no authorized subject must get zero rows,
    # never the legacy-private corpus. Omitting the field (None) keeps the
    # pre-upgrade legacy behaviour — downstream filter_entries_for_subjects
    # treats () and None alike, so the distinction must be enforced here.
    if req.subjects is not None and not (1 <= len(req.subjects) <= 8):
        raise HTTPException(
            status_code=422,
            detail="subjects must be omitted (legacy private) or contain 1..8 items",
        )
    _groups, subjects = (
        _fold_request_subjects(req.subjects) if req.subjects else ((), [])
    )
    # Recall renders tier/entity tags and rerank prompts, so it needs the
    # subject's own durable locale when the caller has none — not whichever
    # locale the calling process happens to sit in.
    resolved_language = await _resolve_scoped_memory_language(
        lanlan_name,
        subjects,
        req.language,
    )
    try:
        # Move import into try: if memory.hybrid_recall import itself fails (circular
        # imports / missing dependencies), fallback below still returns empty results, avoiding
        # endpoint 500 killing the tool call.
        time_window = None
        if time_spec:
            from memory.temporal import parse_time_window
            time_window = parse_time_window(time_spec)
            if time_window is None:
                logger.info(
                    "[query_memory] %s: time=%r 无法解析为时间窗口，回落语义检索",
                    lanlan_name, time_spec,
                )
            elif not query_text:
                # time only, no query → return nearest entries by temporal proximity.
                from memory.hybrid_recall import recall_by_time
                with language_context(resolved_language):
                    return await recall_by_time(
                        lanlan_name=lanlan_name,
                        time_spec=time_spec,
                        fact_store=runtime.fact_store,
                        reflection_engine=runtime.reflection_engine,
                        subjects=subjects,
                    )
        # query (+ optional time_window) → semantic retrieval; nonempty time_window combines semantic +
        # temporal search (query ranking within window).
        from memory.hybrid_recall import hybrid_recall
        with language_context(resolved_language):
            return await hybrid_recall(
                lanlan_name=lanlan_name,
                query=query_text,
                fact_store=runtime.fact_store,
                reflection_engine=runtime.reflection_engine,
                config_manager=runtime._config_manager,
                time_window=time_window,
                subjects=subjects,
                persona_manager=getattr(runtime, "persona_manager", None),
                budget_ms=req.budget_ms,
            )
    except Exception as exc:
        # Never let recall failure kill a tool call: return empty results; main_server
        # handler converts them to "no relevant memory found", allowing the model to
        # continue normally. Full traceback goes to logger.exception (type + message);
        # response returns only stable error_code, avoiding leaking internal details (exception messages may include sensitive
        # context) through HTTP bodies.
        logger.exception(
            "[hybrid_recall] %s: 召回失败，返回空结果占位: %s: %s",
            lanlan_name, type(exc).__name__, exc,
        )
        return {
            "results": [], "query": req.query or "",
            "candidates_total": 0, "elapsed_ms": 0.0,
            "error_code": "hybrid_recall_failed",
        }


@app.get("/get_settings/{lanlan_name}")
async def get_settings(lanlan_name: str):
    lanlan_name = validate_lanlan_name(lanlan_name)
    # Check whether character exists in configuration.
    try:
        character_data = await runtime._config_manager.aload_characters()
        catgirl_names = list(character_data.get('猫娘', {}).keys())
        if lanlan_name not in catgirl_names:
            logger.warning(f"角色 '{lanlan_name}' 不在配置中，返回空设置")
            return f"{lanlan_name}记得{{}}"
    except Exception as e:
        logger.error(f"检查角色配置失败: {e}")
        return f"{lanlan_name}记得{{}}"

    async def render_settings():
        # Refresh reflection suppression before rendering (expired cooldown → release), matching
        # persona render's update_suppressions placement.
        try:
            await runtime.reflection_engine.aupdate_suppressions(lanlan_name)
        except Exception as e:
            logger.debug(f"[MemoryServer] reflection suppress 刷新失败: {e}")
        # Prefer persona Markdown rendering (as /new_dialog), falling back to old settings format.
        pending_reflections = await runtime.reflection_engine.aget_pending_reflections(
            lanlan_name,
        )
        confirmed_reflections = await runtime.reflection_engine.aget_confirmed_reflections(
            lanlan_name,
        )
        persona_md = await runtime.persona_manager.arender_persona_markdown(
            lanlan_name,
            pending_reflections,
            confirmed_reflections,
        )
        if persona_md:
            return persona_md
        # Compatibility fallback (natural-language format).
        legacy_settings = await asyncio.to_thread(
            runtime.settings_manager.get_settings,
            lanlan_name,
        )
        return _format_legacy_settings_as_text(legacy_settings, lanlan_name)

    return await locale_state.run_with_character_prompt_locale(
        lanlan_name,
        render_settings,
    )


@app.get("/internal/memory/{lanlan_name}/persona")
async def get_private_persona_snapshot(lanlan_name: str):
    """Return the native retained private persona projection."""
    lanlan_name = validate_lanlan_name(lanlan_name)
    manager = runtime.persona_manager
    if manager is None:
        raise HTTPException(status_code=503, detail="Memory components are not ready")
    from memory.long_term_sync import LongTermMemoryRecoveryRequired
    try:
        return await manager.aget_private_persona_snapshot(lanlan_name)
    except LongTermMemoryRecoveryRequired as exc:
        raise HTTPException(status_code=503, detail="long_term_memory_recovery_required") from exc
    except OverflowError as exc:
        raise HTTPException(status_code=413, detail={
            "code": "MEMORY_RESPONSE_TOO_LARGE",
            "message": "Persona snapshot exceeds internal limit",
        }) from exc
    except (ValueError, TypeError, UnicodeError) as exc:
        raise HTTPException(status_code=500, detail={
            "code": "MEMORY_PROTOCOL_ERROR", "message": "Malformed persona data",
        }) from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Persona storage unavailable") from exc


@app.get("/get_persona/{lanlan_name}")
async def get_persona(lanlan_name: str):
    """Return the full persona JSON (for the UI / memory_browser)."""
    lanlan_name = validate_lanlan_name(lanlan_name)
    from .long_term_sync import consume_for_name
    await consume_for_name(lanlan_name)
    return await runtime.persona_manager.aget_persona(lanlan_name)


@app.post("/internal/chat-sync/long-term-memory/{uid}")
async def apply_synced_long_term_memory(uid: str, request: Request):
    """Wake the native owner to consume its durable, locally authorized inbox."""
    from ipaddress import ip_address
    from utils.deployment import has_forwarding_metadata
    try:
        local = request.client is not None and ip_address(request.client.host).is_loopback
    except ValueError:
        local = False
    if not local or has_forwarding_metadata(request.headers):
        raise HTTPException(status_code=403, detail='local_request_required')
    if not re.fullmatch(r'[0-9a-f]{32}', uid):
        raise HTTPException(status_code=400, detail='invalid_character_uid')
    from .long_term_sync import consume
    return {'applied': await consume(uid)}


@app.get("/api/memory/funnel/{lanlan_name}")
async def api_memory_funnel(lanlan_name: str, since: str | None = None, until: str | None = None):
    """RFC §3.10 funnel analytics — read-only counts of evidence-pipeline
    transitions in a [since, until] window.

    Query params (both ISO8601, optional):
      - since: window lower bound, default = now - 7 days
      - until: window upper bound, default = now

    Timezone handling: `datetime.fromisoformat` happily accepts both naive
    (`2026-04-22T12:00:00`) and aware (`...Z`, `...+08:00`) values, but
    the underlying event log writes naive local-clock timestamps. We
    normalize both bounds via `to_naive_local` immediately after parse
    — *before* the `since_dt > until_dt` validation — so a client
    passing one aware bound and one naive (or default-naive `now()`)
    bound never trips
    `TypeError: can't compare offset-naive and offset-aware datetimes`
    and surfaces as a 500. `funnel_counts` re-normalizes internally
    too; the second pass is a cheap no-op once both are naive.

    Returns the 10-bucket dict from `funnel_counts`. PR-2 (decay+archive)
    populates `*_archived` buckets; PR-3 (merge-on-promote) populates
    `reflections_merged` / `persona_entries_rewritten`. Until those land
    the corresponding buckets stay at 0.
    """
    lanlan_name = validate_lanlan_name(lanlan_name)
    now = datetime.now()
    try:
        since_dt = datetime.fromisoformat(since) if since else now - timedelta(days=7)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid `since` ISO8601: {since!r}")
    try:
        until_dt = datetime.fromisoformat(until) if until else now
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid `until` ISO8601: {until!r}")
    # Normalize BEFORE the inequality check — `now` above is naive but a
    # client-supplied bound may be aware; comparing them directly would
    # raise TypeError → 500. coderabbitai PR #937 round-2.
    from memory.evidence_analytics import funnel_counts, to_naive_local
    since_dt = to_naive_local(since_dt)
    until_dt = to_naive_local(until_dt)
    if since_dt > until_dt:
        raise HTTPException(status_code=400, detail="`since` must be <= `until`")

    # Run file IO + row parsing in workers to avoid blocking event loop
    # (same pattern as EventLog's async counterparts).
    counts = await asyncio.to_thread(funnel_counts, lanlan_name, since_dt, until_dt)
    return {
        "lanlan_name": lanlan_name,
        "since": since_dt.isoformat(),
        "until": until_dt.isoformat(),
        "counts": counts,
    }


@app.post("/cancel_correction/{lanlan_name}", dependencies=[Depends(_refuse_browsers)])
async def cancel_correction(lanlan_name: str):
    lanlan_name = validate_lanlan_name(lanlan_name)
    """Interrupt a character's memory-organization tasks so memory edits take effect immediately."""
    
    if lanlan_name in review.correction_tasks and not review.correction_tasks[lanlan_name].done():
        logger.info(f"🛑 收到取消请求，中断 {lanlan_name} 的correction任务")
        
        if lanlan_name in review.correction_cancel_flags:
            review.correction_cancel_flags[lanlan_name].set()
        
        review.correction_tasks[lanlan_name].cancel()
        try:
            await review.correction_tasks[lanlan_name]
        except asyncio.CancelledError:
            logger.info(f"✅ {lanlan_name} 的correction任务已成功中断")
        except Exception as e:
            logger.warning(f"⚠️ 中断 {lanlan_name} 的correction任务时出现异常: {e}")
        
        return {"status": "cancelled"}
    
    return {"status": "no_task"}


@app.get("/prompt-locale/{lanlan_name}")
async def get_prompt_locale_preference(lanlan_name: str):
    """Return the durable internal-template locale for one character."""
    name = validate_lanlan_name(lanlan_name)
    language, order = await asyncio.to_thread(
        locale_state.get_character_prompt_locale_state,
        name,
    )
    return {
        "success": True,
        "language": language,
        # The write order identifies the individual write. Ownership checks must
        # use it: two writes of the same language are equal by value.
        "order": order,
        "effective_language": language or get_global_language_full(),
    }


@app.put("/prompt-locale/{lanlan_name}")
async def set_prompt_locale_preference(
    lanlan_name: str,
    request: PromptLocalePreferenceRequest,
):
    """Persist a character's template locale without injecting prompt text."""
    name = validate_lanlan_name(lanlan_name)
    if not is_supported_language_code(request.language):
        raise HTTPException(status_code=400, detail="Unsupported language")

    normalized = normalize_language_code(request.language, format="full")
    order = await asyncio.to_thread(
        locale_state.reserve_character_prompt_locale_order,
        name,
    )
    previous, persisted, applied = await asyncio.to_thread(
        locale_state.record_character_prompt_locale_state,
        name,
        normalized,
        order=order,
    )
    if not applied or persisted != normalized:
        # Structured detail on purpose: this server answers 409 for several
        # unrelated reasons (cloudsave maintenance fence, storage-limited
        # startup).  Callers must be able to tell a superseded write -- which
        # means "a newer preference already won" -- from a retryable failure.
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "language_preference_superseded",
                "message": "A newer language preference superseded this request",
            },
        )
    return {
        "success": True,
        "language": persisted,
        "order": order,
        "previous_language": previous,
        "changed": previous != persisted,
    }


# How long POST /preempt_summary keeps preempting summaries that start after it; the
# cold-start /new_dialog that follows closes the window sooner.
PREEMPT_SUMMARY_WINDOW_S = 10.0


@app.post("/preempt_summary/{lanlan_name}", dependencies=[Depends(_refuse_browsers)])
async def preempt_summary(lanlan_name: str):
    """Abandon ``lanlan_name``'s summary LLM calls to background compression (#3601).

    The main server calls this before a cold start: it then waits for its own queued memory
    writes, which may be stuck behind a summary, and only then sends
    ``/new_dialog?preempt_summary=true``.
    """
    lanlan_name = validate_lanlan_name(lanlan_name)
    runtime._open_summary_preemption_window(lanlan_name, PREEMPT_SUMMARY_WINDOW_S)
    return {"status": "ok"}


@app.get("/new_dialog/{lanlan_name}")
async def new_dialog(
    lanlan_name: str,
    language: str | None = None,
    render_language: str | None = None,
    preempt_summary: bool = False,
):
    """``preempt_summary``: the caller is starting a conversation and waits for this response
    (#3601), so a summary LLM call holding the settle lock is abandoned to background
    compression instead of being waited for. Hot-swap preparation leaves it off: the old
    session is still serving, and the new one should start from the finished summary."""
    request_language = language if is_supported_language_code(language) else render_language
    with language_context(_activate_request_language(request_language)):
        if not preempt_summary:
            return await _new_dialog(lanlan_name, language, render_language)
        with runtime._preempting_summaries(validate_lanlan_name(lanlan_name)):
            return await _new_dialog(lanlan_name, language, render_language)


async def _write_new_dialog_locale(
    lanlan_name: str,
    language: str,
    generation: int | None,
    *,
    locale_admission_order: int,
) -> None:
    """Persist one still-current new-dialog locale selection."""
    locale_order = await asyncio.to_thread(
        locale_state.reserve_character_prompt_locale_order,
        lanlan_name,
        order=locale_admission_order,
    )
    if (
        generation is not None
        and _new_dialog_locale_generations.get(lanlan_name) != generation
    ):
        return
    await asyncio.to_thread(
        locale_state.record_character_prompt_locale,
        lanlan_name,
        language,
        order=locale_order,
    )


async def _retry_new_dialog_locale(
    lanlan_name: str,
    language: str,
    generation: int | None,
    *,
    locale_admission_order: int,
) -> None:
    """Retry transient fences; let permanent failures reach the outbox."""
    maintenance_retry_delay = 0.25
    while (
        generation is None
        or _new_dialog_locale_generations.get(lanlan_name) == generation
    ):
        try:
            await _write_new_dialog_locale(
                lanlan_name,
                language,
                generation,
                locale_admission_order=locale_admission_order,
            )
            return
        except locale_state.PromptLocaleInvalidatedError:
            await asyncio.sleep(0.25)
        except MaintenanceModeError:
            await asyncio.sleep(maintenance_retry_delay)
            maintenance_retry_delay = min(
                maintenance_retry_delay * 2,
                30.0,
            )


async def _outbox_new_dialog_locale_handler(
    lanlan_name: str,
    payload: dict,
) -> None:
    """Replay one durable new-dialog locale intent until it is committed."""
    language = payload.get('language')
    locale_admission_order = payload.get('locale_admission_order')
    if not is_supported_language_code(language):
        raise ValueError("invalid prompt locale in outbox payload")
    if not isinstance(locale_admission_order, int) or isinstance(
        locale_admission_order,
        bool,
    ):
        raise ValueError("invalid prompt locale order in outbox payload")

    await _retry_new_dialog_locale(
        lanlan_name,
        language,
        None,
        locale_admission_order=locale_admission_order,
    )


async def _run_durable_new_dialog_locale_retry(
    lanlan_name: str,
    language: str,
    generation: int,
    *,
    locale_admission_order: int,
    op_id: str,
) -> None:
    """Run a newly queued locale intent through generic outbox liveness."""
    payload = {
        'language': language,
        'locale_admission_order': locale_admission_order,
        'generation': generation,
    }
    await outbox_infra._run_outbox_op(
        lanlan_name,
        {
            'op_id': op_id,
            'type': OP_PERSIST_PROMPT_LOCALE,
            'payload': payload,
        },
    )


outbox_infra.register_outbox_handler(
    OP_PERSIST_PROMPT_LOCALE,
    _outbox_new_dialog_locale_handler,
)


async def _arender_behavior_notes(name: str, lang: str) -> str:
    """The behavior-note block, under the user's "natural expression" switch.

    A read of one small file; nothing is computed on the session-start path, and
    a failure costs only this block.
    """
    from memory.behavior_notes import get_behavior_note_store
    from utils.slop_filter import is_slop_filter_enabled

    try:
        if not await asyncio.to_thread(is_slop_filter_enabled):
            return ""
        return await get_behavior_note_store().aprompt_block(name, lang)
    except Exception as e:
        logger.warning(f"[BehaviorNotes] {name}: 提醒注入跳过: {type(e).__name__}")
        return ""


async def _new_dialog(
    lanlan_name: str,
    language: str | None = None,
    render_language: str | None = None,
):
    lanlan_name = validate_lanlan_name(lanlan_name)
    from .long_term_sync import consume_for_name
    await consume_for_name(lanlan_name)
    gates._touch_activity()
    has_explicit_language = is_supported_language_code(language)
    locale_admission_order = None
    if has_explicit_language:
        locale_admission_order = (
            locale_state.capture_character_prompt_locale_order(lanlan_name)
        )

    # Check whether character exists in configuration.
    try:
        character_data = await runtime._config_manager.aload_characters()
        catgirl_names = list(character_data.get('猫娘', {}).keys())
        if lanlan_name not in catgirl_names:
            logger.warning(f"角色 '{lanlan_name}' 不在配置中，返回空上下文")
            return PlainTextResponse("")
    except Exception as e:
        logger.error(f"检查角色配置失败: {e}")
        return PlainTextResponse("")

    if not has_explicit_language:
        # This session speaks its request (UI) language; the habitual-phrase
        # miner follows it. Noted in memory only, never persisted.
        habitual_phrase_loop.note_session_render_language(lanlan_name, render_language)
        try:
            durable_language = await asyncio.to_thread(
                locale_state.get_character_prompt_locale,
                lanlan_name,
            )
        except locale_state.PromptLocalePersistenceError:
            logger.warning(
                "[PromptLocale] %s: durable locale unreadable for new-dialog; "
                "using the request render locale",
                lanlan_name,
            )
            durable_language = None
        language = (
            durable_language
            if is_supported_language_code(durable_language)
            else render_language
        )

    if has_explicit_language:
        locale_admission_order = await asyncio.to_thread(
            locale_state.rebase_character_prompt_locale_order,
            lanlan_name,
            locale_admission_order,
        )
        # The durable retry generation is the admission order itself.  This
        # prevents a slower, older validation from superseding a newer request
        # merely because it reached persistence later.
        generation = locale_admission_order
        try:
            await _write_new_dialog_locale(
                lanlan_name,
                language,
                None,
                locale_admission_order=locale_admission_order,
            )
        except (
            MaintenanceModeError,
            locale_state.PromptLocalePersistenceError,
        ) as exc:
            # /new_dialog is a read path. The request-scoped language context is
            # already active, so a cloud snapshot should only defer the durable
            # locale hint instead of preventing a new conversation from opening.
            logger.info(
                "[PromptLocale] %s: new-dialog locale persistence deferred: %s",
                lanlan_name,
                exc,
            )
            payload = {
                'language': language,
                'locale_admission_order': locale_admission_order,
                'generation': generation,
            }
            try:
                op_id = await runtime.outbox.aappend_pending(
                    lanlan_name,
                    OP_PERSIST_PROMPT_LOCALE,
                    payload,
                )
            except Exception as outbox_exc:
                logger.error(
                    "[PromptLocale] %s: durable locale retry registration failed; "
                    "rejecting new-dialog admission: %s",
                    lanlan_name,
                    outbox_exc,
                )
                raise HTTPException(
                    status_code=503,
                    detail="Prompt locale persistence is unavailable",
                ) from outbox_exc
            else:
                _promote_new_dialog_locale_generation(
                    lanlan_name,
                    generation,
                )
                operation = _run_durable_new_dialog_locale_retry(
                    lanlan_name,
                    language,
                    generation,
                    locale_admission_order=locale_admission_order,
                    op_id=op_id,
                )
                # This retry outlives /new_dialog: without registration, middleware release would count
                # as drained, yet it could write prompt_locale.json after rename/delete,
                # recreating the old identity's directory.
                post_turn._track_character_post_turn_task(
                    lanlan_name,
                    runtime._spawn_background_task(operation),
                )
        else:
            _promote_new_dialog_locale_generation(
                lanlan_name,
                generation,
            )

    # Count valid characters only: QPS observation evaluates C+ cache decisions; invalid requests offer
    # no cacheable opportunities and pollute per_char distribution.
    _new_dialog_qps_counter[lanlan_name] = _new_dialog_qps_counter.get(lanlan_name, 0) + 1

    # Retain settle_lock: wait for /renew /settle first summary, ensuring consistent reads.
    # Review does not hold this lock; persistence atomically replaces whole references plus fingerprint patch,
    # without racing these reads. Phase C makes review a background task concurrent with /process;
    # /new_dialog no longer cancels running reviews (old cancellation was Phase A
    # residue, making reviews almost never finish in active sessions).
    async with runtime._hold_settle_lock_reporting_stalls(lanlan_name, "/new_dialog"):
        # Regex removes all bracket types and contents (including [], (), {}, <>, 【】, （）, etc.).
        brackets_pattern = re.compile(r'(\[.*?\]|\(.*?\)|（.*?）|【.*?】|\{.*?\}|<.*?>)')
        master_name, _, _, _, name_mapping, _, _, _, _ = await runtime._config_manager.aget_character_data()
        name_mapping['ai'] = lanlan_name
        _lang = _normalize_memory_prompt_lang(_activate_request_language(language))

        # ── [Static prefix] Persona long-term memory (rarely changes → maximize prefix cache) ──
        # Without explicit request language, outer context still uses process fallback. Durable locale
        # is read after entry, so reenter context before nested renderers that may read
        # global language; explicit _lang still serves table-driven strings in this function.
        with language_context(_activate_request_language(language)):
            # Pinned important facts (their own section, ahead of the persona
            # sections; private chat only, no scoped renderer reads them) and
            # persona + pending/confirmed reflections (labeled sections).
            # Shared with weekly introspection, which must review exactly
            # this block. Read on every call, so a session start or a hot-swap
            # prompt rebuild picks up entries admitted since the previous one.
            from memory.introspection import arender_injected_memory
            result = _loc(PERSONA_HEADER, _lang).format(name=lanlan_name)
            injected = await arender_injected_memory(
                runtime.persona_manager, runtime.reflection_engine, lanlan_name,
                pinned_fact_store=runtime.pinned_fact_store, lang=_lang,
            )
        result += injected.pinned_prefix
        if injected.persona:
            result += injected.persona
        else:
            # Compatibility fallback: old settings (natural-language format).
            # get_settings uses open() + json.load(); offload to avoid blocking (cold fallback, but multi-file IO when triggered).
            legacy_settings = await asyncio.to_thread(runtime.settings_manager.get_settings, lanlan_name)
            result += (
                _format_legacy_settings_as_text(
                    legacy_settings,
                    lanlan_name,
                    _lang,
                )
                + "\n"
            )

        # ── [Dynamic section] Inner thoughts (changes each time) ──
        result += _loc(INNER_THOUGHTS_HEADER, _lang).format(name=lanlan_name)
        result += _loc(INNER_THOUGHTS_DYNAMIC, _lang).format(
            name=lanlan_name,
            time=get_timestamp(),
        )

        recent_history = _screen_guarded_recent_history(
            await runtime.recent_history_manager.aget_recent_history(lanlan_name)
        )
        rendered_history = await asyncio.to_thread(lambda: list(_iter_theater_rendered_history(
            recent_history, lang=_lang, name=lanlan_name, master=master_name,
        )))
        for i, capsule_text in rendered_history:
            if capsule_text is not None:
                result += capsule_text + "\n"
                continue
            if isinstance(i.content, str):
                cleaned_content = brackets_pattern.sub('', i.content).strip()
                result += f"{name_mapping[i.type]} | {cleaned_content}\n"
            else:
                texts = [brackets_pattern.sub('', j['text']).strip() for j in i.content if j['type'] == 'text']
                result += f"{name_mapping[i.type]} | " + "\n".join(texts) + "\n"

        # ── Time since last chat hint (last, immediately before CONTEXT_SUMMARY_READY) ──
        try:
            from datetime import datetime as _dt
            last_time = await runtime.time_manager.aget_last_conversation_time(lanlan_name)
            if last_time:
                gap = _dt.now() - last_time
                gap_seconds = gap.total_seconds()
                if gap_seconds >= 1800:  # Show only after ≥ 30 minutes.
                    elapsed = _format_elapsed(_lang, gap_seconds)

                    if gap_seconds >= 18000:  # ≥ 5 hours: current time + interval + long-absence hint.
                        now_str = _dt.now().strftime("%Y-%m-%d %H:%M")
                        result += _loc(CHAT_GAP_CURRENT_TIME, _lang).format(now=now_str)
                        result += _loc(CHAT_GAP_NOTICE, _lang).format(master=master_name, elapsed=elapsed)
                        result += _loc(CHAT_GAP_LONG_HINT, _lang).format(name=lanlan_name, master=master_name) + "\n"
                    else:
                        result += _loc(CHAT_GAP_NOTICE, _lang).format(master=master_name, elapsed=elapsed) + "\n"
        except Exception as e:
            logger.warning(f"计算聊天间隔失败: {e}")

        # ── Festival/holiday context (always injected, independent of consumption) ──
        try:
            from utils.holiday_cache import get_holiday_context_line
            holiday_name = get_holiday_context_line(_lang)
            if holiday_name:
                result += _loc(CHAT_HOLIDAY_CONTEXT, _lang).format(holiday=holiday_name)
        except Exception as e:
            logger.debug(f"Holiday context injection skipped: {e}")

        # Reminders from the weekly behavior review. In this text rather than the
        # session prompt so that every way she starts talking reads them: a session
        # start, a hot swap and voice as well as proactive chat. Last, after the
        # conversation: measured on real reopenings, the same note placed before
        # the history barely changed anything, since the history right after it
        # shows her doing the very thing and she follows that instead.
        result += await _arender_behavior_notes(lanlan_name, _lang)

        return PlainTextResponse(result)


@app.get("/last_conversation_gap/{lanlan_name}")
async def last_conversation_gap(lanlan_name: str):
    """Return the seconds elapsed since the last conversation, for the main server to decide whether to trigger proactive chat."""
    lanlan_name = validate_lanlan_name(lanlan_name)
    try:
        last_time = await runtime.time_manager.aget_last_conversation_time(lanlan_name)
        if last_time is None:
            return {"gap_seconds": -1}
        gap = (datetime.now() - last_time).total_seconds()
        return {"gap_seconds": gap}
    except Exception as e:
        logger.exception(f"查询对话间隔失败: {e}")
        return JSONResponse({"gap_seconds": -1, "error": "server_error"}, status_code=500)


from .browser_routes import (  # noqa: F401, E402
    _BrowserPersonaEditRequest,
    _BrowserPersonaFlagsRequest,
    _BrowserPersonaArchiveRequest,
    _BrowserPersonaRestoreRequest,
    _BrowserCorrectionResolveRequest,
    _BrowserReflectionReviewRequest,
    _BrowserFactEditRequest,
    _BrowserFactArchiveRequest,
    _BrowserDuplicateMergeRequest,
    _BrowserDirectiveRequest,
    _BrowserImprintNoteRequest,
    _BrowserSearchRequest,
    browser_memory_overview,
    browser_generate_imprint_note,
    browser_search_memory,
    _queue_long_term_capture,
    _admit_browser_character_write,
    browser_edit_persona,
    browser_set_persona_flags,
    browser_archive_persona,
    browser_restore_persona_version,
    browser_resolve_correction,
    browser_review_reflection,
    browser_edit_fact,
    browser_archive_fact,
    browser_merge_duplicate,
    browser_add_directive,
    browser_remove_directive,
    _BrowserStarMapLabelRequest,
    _BrowserRelatedMemoriesRequest,
    browser_memory_star_map,
    browser_label_star_map_clusters,
    browser_memory_lineage,
    browser_related_memories,
    _BrowserSnapshotCreateRequest,
    _BrowserSnapshotRestoreRequest,
    _BrowserDeepConflictRequest,
    browser_list_snapshots,
    browser_create_snapshot,
    browser_restore_snapshot,
    browser_export_memory_zip,
    browser_full_lineage_graph,
    browser_deep_conflict_check,
)
