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
"""

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

from fastapi import HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

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
from utils.natural_expression_candidates import (
    CandidateMinerError,
    SourceMessage,
    build_user_review_report,
    normalize_language,
)

from . import gates, locale_state, outbox_infra, post_turn, review, runtime
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


class TheaterMemoryForgetRequest(BaseModel):
    story_id: str = Field(min_length=1, max_length=256)


class TheaterEpisodeRetractRequest(BaseModel):
    story_id: str = Field(min_length=1, max_length=256)
    session_id: str = Field(min_length=1, max_length=256)
    archive_through_revision: int = Field(ge=0)
    # Identify the archive attempts to fence against late /cache writes.
    archive_request_id: str = Field(default="", max_length=160)
    archive_attempt: int = Field(default=0, ge=0)


def _theater_story_event_id(lanlan_name: str, message) -> str:
    """Always use the same bounded event ID for a story in the time index."""  # noqa: DOCSTRING_CJK

    story_id, _ = theater_memory_episode_key(message)
    digest = hashlib.sha256(
        f"{lanlan_name}\x1f{story_id}".encode("utf-8")
    ).hexdigest()
    return f"theater-story-{digest}"


def _theater_index_events(lanlan_name: str, messages: list) -> dict[str, tuple[str, list]]:
    """Build story-aggregated time-index events from all theater memories in recent."""  # noqa: DOCSTRING_CJK

    grouped: dict[str, list] = {}
    for message in messages:
        # When forgetting one scenario, other unmigrated legacy bodies remain in recent; rebuilding must preserve them.
        if not is_theater_memory_message(message):
            continue
        story_id, _ = theater_memory_episode_key(message)
        if story_id:
            grouped.setdefault(story_id, []).append(message)
    return {
        story_id: (
            _theater_story_event_id(lanlan_name, story_messages[-1]),
            story_messages,
        )
        for story_id, story_messages in grouped.items()
    }


def _theater_memory_render_state(history: list):
    """Select the latest state per Session and latest playthrough per Story."""  # noqa: DOCSTRING_CJK

    latest_by_episode = {
        theater_memory_episode_key(message): message_metadata(message)
        for message in history
        if is_theater_memory_message(message)
    }
    latest_episode_by_story: dict[str, tuple[str, str]] = {}
    latest_rank_by_story: dict[str, tuple[int, int]] = {}
    for position, (episode_key, metadata) in enumerate(latest_by_episode.items()):
        story_id = episode_key[0]
        rank = (_positive_metadata_int(metadata.get("run_index")), position)
        if rank >= latest_rank_by_story.get(story_id, (-1, -1)):
            latest_rank_by_story[story_id] = rank
            latest_episode_by_story[story_id] = episode_key
    return latest_by_episode, latest_episode_by_story


def _iter_theater_rendered_history_unbounded(history: list, *, lang: str, name: str, master: str):
    """Yield ``(message, capsule_text)`` for the prompt renderings of recent history.

    Each theater Session renders once, at its first position, from its latest
    metadata; later messages of the same Session are skipped. Only the latest
    run of a Story carries the story-wide run count and endings seen. Ordinary
    messages come back with ``capsule_text=None`` for the caller to render.
    """
    latest_by_episode, latest_episode_by_story = _theater_memory_render_state(history)
    rendered_episodes: set[tuple[str, str]] = set()
    for message in history:
        if not is_theater_memory_message(message):
            yield message, None
            continue
        episode_key = theater_memory_episode_key(message)
        if episode_key in rendered_episodes:
            continue
        rendered_episodes.add(episode_key)
        metadata = latest_by_episode[episode_key]
        is_latest_story_run = latest_episode_by_story.get(episode_key[0]) == episode_key
        ending_titles = metadata.get("ending_titles_seen")
        yield message, get_theater_memory_context(
            lang,
            name=name,
            master=master,
            title=str(metadata.get("story_title") or ""),
            status=str(metadata.get("episode_status") or "paused"),
            ending=str(metadata.get("ending_title") or ""),
            summary=str(
                metadata.get("episode_summary")
                or metadata.get("ending_summary")
                or ""
            ),
            run_index=_positive_metadata_int(metadata.get("run_index")),
            story_run_count=(
                _positive_metadata_int(metadata.get("story_run_count"))
                if is_latest_story_run
                else 0
            ),
            ending_titles=(
                ending_titles
                if is_latest_story_run and isinstance(ending_titles, list)
                else []
            ),
        )


def _iter_theater_rendered_history(history: list, *, lang: str, name: str, master: str):
    """Apply a separate theater prompt allowance without dropping ordinary text."""
    from memory.theater_budget import THEATER_MEMORY_BUDGET_TOKENS
    from utils.tokenize import count_tokens

    rendered = list(_iter_theater_rendered_history_unbounded(
        history, lang=lang, name=name, master=master,
    ))
    selected = set()
    texts = []
    for index in range(len(rendered) - 1, -1, -1):
        _, capsule_text = rendered[index]
        if capsule_text is None:
            selected.add(index)
            continue
        candidate = [capsule_text, *texts]
        if count_tokens("\n".join(candidate) + "\n") <= THEATER_MEMORY_BUDGET_TOKENS:
            selected.add(index)
            texts = candidate
    for index, entry in enumerate(rendered):
        if index in selected:
            yield entry


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


class ExternalMemoryImportRequest(BaseModel):
    character_name: str
    source_format: str
    imported_files: list[str]
    candidates: list[dict]
    warning_count: int = 0
    language: str | None = None
    render_language: str | None = None


# ── External import progress registry (polled by the Molili frontend progress bar) ──────────
# character → {stage, persona_total/done, daily_total/done, finished, ...}.
# Written by _import_external_markdown (register / fuse / finish) and by
# aimport_external_daily's on_progress callback (once per batch). One small entry per character, no eviction;
# read and written on the single-threaded event loop, so no lock is needed. Each import owns its own entry
# object and only edits that one: an overlapping request for the same character (a retry after a frontend
# timeout) re-registers over it, and the older request finishing first never marks the newer entry finished or writes counts into it.
_IMPORT_PROGRESS: dict[str, dict] = {}


@app.get("/internal/memory/import_progress/{character_name}")
async def get_import_progress(character_name: str):
    """External import progress for the Molili import progress bar.

    Pure in-memory counter read: no IO."""
    name = validate_lanlan_name(character_name)
    entry = _IMPORT_PROGRESS.get(name)
    if entry is None:
        return {'success': True, 'active': False}
    return {'success': True, 'active': not bool(entry.get('finished')), **entry}


@app.post("/internal/memory/import_external_markdown")
async def import_external_markdown(request: ExternalMemoryImportRequest):
    """Join the per-character admission ledger, then run the import.

    This endpoint is body-addressed, so the publication guard middleware cannot
    see which character it writes to. Its facts path reaches
    ``fact_store`` → ``aindex_fact`` → that character's SQLite index, and one
    persona fusion can run for minutes, so it has to register here explicitly —
    otherwise release sees no active request and can dispose underneath it.
    """
    name = validate_lanlan_name(request.character_name)
    context_token = runtime._begin_character_request(name)
    if context_token is None:
        return JSONResponse(
            {"status": "cancelled", "message": "character release in progress"},
            status_code=409,
        )
    progress: dict = {}
    try:
        return await _import_external_markdown(request, progress)
    finally:
        runtime._end_character_request(name, context_token)
        progress['finished'] = True


async def _import_external_markdown(
    request: ExternalMemoryImportRequest,
    progress: dict | None = None,
):
    """Persist already-previewed OpenClaw/Hermes entries via live managers.

    The persona and facts persistence paths are **asymmetric**, because their
    downstream budgets differ:

    - **facts** take the ``_apersist_new_facts(semantic_dedup=False)`` pure-append
      path -- the facts pool has no hard token ceiling for system-prompt rendering;
      entries are recalled on demand at retrieval time, so keeping each one is fine.
    - **persona** must first go through one LLM fusion via ``afuse_external_facts``.
      When persona is rendered into the system prompt, all non-protected entries
      compete for a single **strict token ceiling**; ``USER.md`` / ``SOUL.md`` are
      dozens of lines of free-form Markdown, and appending them verbatim would
      quickly overflow that pool and crowd out the impressions the character has
      naturally accumulated in conversation. Fusion summarises / merges / dedupes
      the material and truncates it to the per-entity budget before persisting.
      Candidates are grouped by entity (master / neko) and fused separately.

    On fusion failure (``ExternalMemoryFusionError``) there is **no fallback** to
    per-entry appends (that would bypass the budget and overflow the pool) -- the
    user's material is kept and ``external_import_partial`` is returned so the
    frontend can retry; retries are idempotent (same fingerprint -> skip the whole
    batch / changed -> replace-then-fuse).
    """
    name = validate_lanlan_name(request.character_name)
    if request.source_format not in {"openclaw", "hermes", "molili"}:
        raise HTTPException(status_code=400, detail="Invalid source_format")
    if not request.candidates or len(request.candidates) > MAX_ENTRIES:
        raise HTTPException(status_code=400, detail="Invalid candidate count")
    if runtime.fact_store is None or runtime.persona_manager is None:
        raise HTTPException(status_code=503, detail="Memory components are not ready")
    assert_cloudsave_writable(
        runtime._config_manager,
        operation="import",
        target=f"memory/{name}/external-markdown",
    )

    explicit_language = None
    locale_admission_order = None
    if is_supported_language_code(request.language):
        explicit_language = normalize_language_code(request.language, format='full')
        locale_admission_order = (
            locale_state.allocate_character_prompt_locale_order(name)
        )

    imported_at = datetime.now().astimezone().isoformat()
    # Group persona candidates by entity (master / neko) for separate LLM fusion; MEMORY.md facts
    # append directly, while daily diaries (with event_date) undergo LLM fact extraction.
    persona_candidates_by_entity: dict[str, list[dict]] = {}
    extracted_facts: list[dict] = []       # MEMORY.md → deterministic append-only.
    daily_candidates: list[dict] = []      # Daily diary → LLM fact extraction.
    for candidate in request.candidates:
        if not isinstance(candidate, dict):
            raise HTTPException(status_code=400, detail="Invalid candidate")
        text = str(candidate.get("text") or "").strip()
        entity = str(candidate.get("entity") or "master")
        target = candidate.get("target")
        source_file = str(candidate.get("source_file") or "")
        if (
            not text or len(text) > MAX_ENTRY_CHARS
            or entity not in {"master", "neko", "relationship"}
            or target not in {"persona", "facts"}
            or not source_file
        ):
            raise HTTPException(status_code=400, detail="Invalid candidate fields")
        source_section = str(candidate.get("source_section") or "")
        event_date = candidate.get("event_date")
        if target == "persona":
            # Pass complete provenance (source_file / source_section / event_date) to fusion:
            # source_section structures the fusion prompt; source_file enters Phase 3 persisted metadata;
            # afuse_external_facts computes fingerprints from candidate text internally (idempotent reimport).
            persona_candidates_by_entity.setdefault(entity, []).append({
                "text": text,
                "entity": entity,
                "source_file": source_file,
                "source_section": source_section,
                "event_date": event_date,
            })
        elif event_date:
            # Daily diaries (memory/·memories/YYYY-MM-DD.md) are prose, not appended item by item;
            # aimport_external_daily performs per-day LLM fact extraction (see its docstring).
            daily_candidates.append({
                "text": text,
                "source_file": source_file,
                "source_section": source_section,
                "event_date": event_date,
            })
        else:
            # MEMORY.md is already a fact list: deterministic append-only.
            extracted_facts.append({
                "text": text,
                "entity": entity,
                "importance": 7,
                "source": "user_observation",
                "_external_import": {
                    "format": request.source_format,
                    "file": source_file,
                    "section": source_section,
                    "event_date": event_date,
                    "imported_at": imported_at,
                },
            })

    if explicit_language is not None:
        locale_order = await asyncio.to_thread(
            locale_state.reserve_character_prompt_locale_order,
            name,
            order=locale_admission_order,
        )
        await asyncio.to_thread(
            locale_state.record_character_prompt_locale,
            name,
            explicit_language,
            order=locale_order,
        )

    # ── Persona stage: concurrent per-entity LLM fusion (no append-only downgrade; see endpoint docstring) ──
    # Concurrency safety: afuse_external_facts Phase 1/3 serialize reads/writes under one character lock;
    # each entity rewrites only its own section (CAS also checks only that entity's fingerprint set).
    # Slow Phase 2 (LLM) is unlocked: only LLM round trips run concurrently; persistence is mutually exclusive.
    persona_entities = list(persona_candidates_by_entity.items())

    # Progress registry: persona counts per entity; daily counts come from the on_progress callback
    if progress is None:
        progress = {}
    _IMPORT_PROGRESS[name] = progress
    progress.update({
        'stage': 'persona',
        'persona_total': len(persona_entities),
        'persona_done': 0,
        'daily_total': 0,
        'daily_done': 0,
        'finished': False,
        'started_at': imported_at,
    })
    # Browser imports deliberately omit ``language``. Resolve the durable
    # character preference at execution time so a preference changed while the
    # user was reading/confirming the preview cannot be overwritten by a stale
    # frontend snapshot. This value is for prompt rendering only; the persistence
    # block above remains reserved for explicit API callers.
    memory_language = await _resolve_foreground_memory_language(
        name,
        explicit_language,
        render_language=request.render_language,
    )
    with language_context(memory_language):
        async def _fuse_and_mark(entity: str, entity_candidates: list[dict]):
            outcome = await runtime.persona_manager.afuse_external_facts(
                name, entity, entity_candidates, request.source_format,
            )
            progress['persona_done'] = progress.get('persona_done', 0) + 1
            return outcome

        fusion_outcomes = await asyncio.gather(
            *(
                _fuse_and_mark(entity, entity_candidates)
                for entity, entity_candidates in persona_entities
            ),
            return_exceptions=True,
        )
    added_persona = sum(r["added"] for r in fusion_outcomes if isinstance(r, dict))
    skipped_persona = sum(r["skipped"] for r in fusion_outcomes if isinstance(r, dict))
    fusion_errors = [r for r in fusion_outcomes if isinstance(r, BaseException)]
    if fusion_errors:
        for exc in (e for e in fusion_errors if not isinstance(e, ExternalMemoryImportTooLargeError)):
            logger.error(
                "External Markdown import: persona fusion failed: character=%s",
                name, exc_info=exc,
            )
        if all(isinstance(e, ExternalMemoryImportTooLargeError) for e in fusion_errors):
            # All failures deterministically too large: candidates exceed the fusion input pool; identical retries must fail again.
            # (no fingerprint recorded) → return non-retryable too_large, prompting frontend to split the workspace.
            logger.warning(
                "External Markdown import: persona too large for single fusion: character=%s added_persona=%s",
                name,
                added_persona,
            )
            return JSONResponse(
                status_code=413,
                content={
                    "detail": "External memory import is too large for a single fusion pass",
                    "error_code": "external_import_too_large",
                    "partial_import": {
                        "character_name": name,
                        "added_persona": added_persona,
                        "added_facts": 0,
                    },
                },
            )
        # Retryable failures (terminal fusion failure ExternalMemoryFusionError / asave_persona crash,
        # etc., possibly mixed with too_large) → return partial for idempotent retries: successful entities
        # skip by fingerprint, transient failures converge, and remaining too_large naturally surfaces as 413.
        # Never fall back to per-entry append and overflow the persona pool.
        logger.error(
            "External Markdown import: persona stage failed: character=%s added_persona=%s",
            name,
            added_persona,
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": "External memory import was only partially completed",
                "error_code": "external_import_partial",
                "partial_import": {
                    "character_name": name,
                    "added_persona": added_persona,
                    "added_facts": 0,
                },
            },
        )

    try:
        new_facts = await runtime.fact_store._apersist_new_facts(
            name,
            extracted_facts,
            default_source="user_observation",
            semantic_dedup=False,
        )
    except Exception:
        logger.exception(
            "External Markdown import stopped after persona persistence: character=%s added_persona=%s",
            name,
            added_persona,
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": "External memory import was only partially completed",
                "error_code": "external_import_partial",
                "partial_import": {
                    "character_name": name,
                    "added_persona": added_persona,
                    "added_facts": 0,
                },
            },
        )
    memory_added = len(new_facts)

    # Daily diary → per-day best-effort LLM fact extraction (see aimport_external_daily).
    # Daily failure does not roll back persisted persona / MEMORY.md facts; systemic exceptions return partial.
    daily_added = 0
    if daily_candidates:
        try:
            with language_context(memory_language):
                def _on_daily_progress(update: dict) -> None:
                    progress.update(update)
                    progress['stage'] = 'daily'

                daily_result = await runtime.fact_store.aimport_external_daily(
                    name, daily_candidates, request.source_format, imported_at,
                    on_progress=_on_daily_progress,
                )
        except ExternalMemoryImportTooLargeError as exc:
            # Deterministic excess (a single day alone exceeds the per-import call cap): identical
            # retries must exceed again, so return too_large to guide splitting. Many days that each
            # fit do not land here; they resume in segments instead (see deferred_days below).
            logger.warning(
                "External Markdown import: daily too large: character=%s detail=%s",
                name, exc,
            )
            return JSONResponse(
                status_code=413,
                content={
                    "detail": str(exc),
                    "error_code": "external_import_too_large",
                    "partial_import": {
                        "character_name": name,
                        "added_persona": added_persona,
                        "added_facts": memory_added,
                    },
                },
            )
        except Exception:
            logger.exception(
                "External Markdown import: daily extraction failed after persona+memory: "
                "character=%s added_persona=%s memory_facts=%s",
                name, added_persona, memory_added,
            )
            return JSONResponse(
                status_code=500,
                content={
                    "detail": "External memory import was only partially completed",
                    "error_code": "external_import_partial",
                    "partial_import": {
                        "character_name": name,
                        "added_persona": added_persona,
                        "added_facts": memory_added,
                    },
                },
            )
        daily_added = daily_result["added"]
        failed_days = daily_result["failed_days"]
        deferred_days = daily_result.get("deferred_days", 0)
        oversized_days = daily_result.get("oversized_days", 0)
        if failed_days or deferred_days:
            # Failed or deferred (segmented resume) diary days forbid success (the client would treat
            # the import as complete, permanently losing the remaining days with no retry signal,
            # Greptile P1); return a retryable partial. Retries converge: persona skips idempotently by
            # fingerprint; SHA/FTS5 deduplication blocks MEMORY.md and already extracted daily facts;
            # finished days skip by daily fingerprint; only failed and deferred days actually extract.
            logger.warning(
                "External Markdown import: daily journals incomplete: character=%s "
                "failed=%s deferred=%s",
                name, failed_days, deferred_days,
            )
            detail_parts = []
            if failed_days:
                detail_parts.append(f"{failed_days} daily journal(s) failed extraction")
            if deferred_days:
                detail_parts.append(
                    f"{deferred_days} daily journal(s) deferred to the next import"
                )
            return JSONResponse(
                status_code=500,
                content={
                    "detail": "; ".join(detail_parts) + "; retry to finish",
                    "error_code": "external_import_partial",
                    "partial_import": {
                        "character_name": name,
                        "added_persona": added_persona,
                        "added_facts": memory_added + daily_added,
                        "failed_days": failed_days,
                        "remaining_days": deferred_days,
                        "oversized_days": oversized_days,
                    },
                },
            )
        if oversized_days:
            # Every other day is imported and only days that alone exceed the cap remain: retrying cannot progress -> too_large
            # guides splitting, carrying the counts persisted by this import (the frontend broadcasts memory_edited from them).
            logger.warning(
                "External Markdown import: %s daily journal(s) alone exceed the call cap: "
                "character=%s",
                oversized_days, name,
            )
            return JSONResponse(
                status_code=413,
                content={
                    "detail": (
                        f"{oversized_days} daily journal(s) each need more extraction "
                        "calls than one import allows; split those days"
                    ),
                    "error_code": "external_import_too_large",
                    "partial_import": {
                        "character_name": name,
                        "added_persona": added_persona,
                        "added_facts": memory_added + daily_added,
                        "oversized_days": oversized_days,
                    },
                },
            )

    added_facts = memory_added + daily_added
    skipped_facts = len(extracted_facts) - memory_added
    return {
        "status": "success",
        "character_name": name,
        "source_format": request.source_format,
        "imported_files": request.imported_files,
        "added_persona": added_persona,
        "added_facts": added_facts,
        "skipped_duplicates": skipped_persona + skipped_facts,
        "warning_count": max(0, request.warning_count),
    }


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


# memory-evidence-rfc §3.3.6 Reconciler handlers live in
# memory/evidence_handlers.py — imported at module top as
# `_register_evidence_handlers`. Keeping the handlers in their own module
# lets unit tests exercise the production apply path without booting FastAPI.


# --- Reflection API (HTTP callers: main_server/system_router) ---

@app.post("/reflect/{lanlan_name}")
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


@app.get("/followup_topics/{lanlan_name}")
async def api_followup_topics(lanlan_name: str):
    """Get follow-up topic candidates (does not mark them surfaced; the caller must call /record_surfaced afterwards)."""
    lanlan_name = validate_lanlan_name(lanlan_name)
    try:
        topics = await runtime.reflection_engine.aget_followup_topics(lanlan_name)
    except Exception as e:
        logger.debug(f"[ReflectAPI] {lanlan_name}: get_followup_topics 失败: {e}")
        topics = []
    return {"topics": topics}


@app.post("/record_surfaced/{lanlan_name}")
async def api_record_surfaced(request: Request, lanlan_name: str):
    """Record which reflections this proactive chat mentioned, refreshing the cooldown."""
    lanlan_name = validate_lanlan_name(lanlan_name)
    body = await request.json()
    reflection_ids = body.get("reflection_ids", [])
    if not reflection_ids:
        return {"ok": True}
    try:
        await runtime.reflection_engine.arecord_surfaced(lanlan_name, reflection_ids)
    except Exception as e:
        logger.debug(f"[ReflectAPI] {lanlan_name}: record_surfaced 失败: {e}")
    return {"ok": True}


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


# ── keyed private /cache and visit facts (catgirl visit "keep as diary", OD-16 v4) ──

from utils.recent_file import VISIT_DIARY_KEY_RE as _KEYED_CACHE_KEY_RE  # noqa: E402

# The only keyed private ``/cache`` writes: one visit's diary entry (``visit-debrief:{visit_id}``).

_RESERVED_KEY_PREFIXES = ("visit-debrief:", "visit-facts:")
"""Key prefixes owned by keyed ``/cache`` and ``visit_facts`` (refused on ``/scoped_history``)."""

_VISIT_FACT_IMPORTANCE = 4
"""Below the reflection threshold (5): visit facts are recalled but never synthesized."""


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


class VisitFactItem(BaseModel):
    text: str = Field(min_length=1, max_length=60)


class VisitFactsRequest(BaseModel):
    visit_id: str = Field(pattern=r"^[A-Za-z0-9_-]{22}$")
    facts: list[VisitFactItem] = Field(default_factory=list, max_length=3)


@app.post("/internal/memory/{lanlan_name}/visit_facts")
async def write_visit_facts(lanlan_name: str, req: VisitFactsRequest):
    """Write the (at most three) facts of one visit's diary into the private fact pool.

    Idempotent per ``visit_id`` (key ``visit-facts:{visit_id}``): a ``done``
    key answers the ``written`` count of the first write. The server stamps
    every new fact ``source='ai_disclosure'``, ``importance=4``,
    ``absorbed=True``, ``origin='neko_visit'`` and ``visit_id`` (callers
    cannot override them), so reflection never synthesizes them while recall
    still finds them; only exact dedup applies (a semantic near-duplicate
    would go to arbitration, whose ``replace`` could archive the user's own
    fact in favour of a visit fact). A key left
    ``pending`` (crash after the facts were saved) is settled by counting the
    visit's facts in the active and archived pools, read strictly: either
    file unreadable answers 503 and writes nothing; the facts still missing
    are then written (rows already there are skipped by their effect key)
    and ``written`` counts both. An empty list is a no-op
    that still records the key.
    """
    from . import idempotency

    lanlan_name = validate_lanlan_name(lanlan_name)
    if runtime.fact_store is None:
        raise HTTPException(
            status_code=503,
            detail="memory_server not fully initialized (limited mode or startup incomplete)",
        )
    texts = [item.text.strip() for item in req.facts]
    if any(not text for text in texts):
        raise HTTPException(status_code=422, detail="each fact text must contain 1..60 characters")
    key = f"visit-facts:{req.visit_id}"
    facts_hash = hashlib.sha256(json.dumps(texts, ensure_ascii=False).encode("utf-8")).hexdigest()
    unavailable = HTTPException(status_code=503, detail="visit facts not confirmed; retry later")
    async with idempotency.key_lock(lanlan_name, key):
        try:
            record = await idempotency.read_key(lanlan_name, key)
        except (idempotency.IdempotencyStateError, OSError) as exc:
            raise unavailable from exc
        if record is not None and record.get("kind") != "visit_facts":
            raise HTTPException(status_code=422, detail="idempotency key belongs to another endpoint")
        stored_hash = record.get("content_hash") if record is not None else None
        if stored_hash is not None and stored_hash != facts_hash:
            # One fact set per visit (client retries same persisted set): changed content is caller error, never report it written.
            raise HTTPException(status_code=422, detail="visit_id reused with different facts")
        def _counter(value: object) -> int | None:
            # Missing fields are malformed too: our terminal records always have both counts; without them never trust prior-written claims.
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return None
            return value

        written_n = _counter(record.get("written")) if record is not None else None
        deduped_n = _counter(record.get("deduped")) if record is not None else None
        if (record is not None and record.get("state") in idempotency.TERMINAL_KEY_STATES
                and isinstance(stored_hash, str) and stored_hash
                and written_n is not None and deduped_n is not None
                # Our terminal records always satisfy written + deduped == batch fact count; mismatch is malformed too.
                and written_n + deduped_n == len(texts)):
            return {"ok": True, "written": written_n, "deduped": deduped_n, "duplicate": True}
        # Missing content hash or malformed counts: infer from this visit's fact-pool rows as for absent records, filling only gaps.
        try:
            rows = await runtime.fact_store.aload_facts_strict(lanlan_name, include_archive=True)
        except RuntimeError as exc:
            # Never guess on read failure: permissive active-pool loading caches an empty list, whose writeback would erase existing facts.
            logger.warning(f"[MemoryServer] visit_facts {lanlan_name}: fact pool unreadable: {exc}")
            raise unavailable from exc
        mine = [row for row in rows if row.get("origin") == "neko_visit" and row.get("visit_id") == req.visit_id]
        already = len(mine)
        known_effects = {row.get("effect_key") for row in mine}
        expected = {idempotency.effect_key_for(key, i): text for i, text in enumerate(texts)}
        if any(expected.get(row.get("effect_key")) != row.get("text") for row in mine):
            # Reach fact-pool recovery only for missing/malformed key records: if this visit's rows differ from current request text,
            # current content cannot be reported written (same handling as content_hash mismatch).
            raise HTTPException(status_code=422, detail="visit_id reused with different facts")
        try:
            # Some rows persisted before interruption preceding done (or later deletion/restore removed rows): replay with effect_key,
            # skip existing rows by effect_key and fill only gaps; written reflects actual new row count.
            await idempotency.update_key(
                lanlan_name, key,
                idempotency.transition(idempotency.KEY_STATE_PENDING, kind="visit_facts", content_hash=facts_hash),
            )
            created: list[dict] = []
            if texts:
                extracted = [
                    {
                        "text": text,
                        "importance": _VISIT_FACT_IMPORTANCE,
                        "source": "ai_disclosure",
                        "entity": "neko",
                    }
                    for text in texts
                ]
                created = await runtime.fact_store._apersist_new_facts(
                    lanlan_name,
                    extracted,
                    default_source="ai_disclosure",
                    # Exact deduplication only: semantic near-duplicates trigger arbitration, whose replace could archive the user's own facts
                    # and substitute a neighbor's visit facts.
                    semantic_dedup=False,
                    effect_keys=[idempotency.effect_key_for(key, i) for i in range(len(extracted))],
                    visit_origin={"visit_id": req.visit_id},
                )
            # Returned rows include existing effect_key replays too; count only truly new writes.
            written = already + sum(1 for row in created if row.get("effect_key") not in known_effects)
            deduped = max(len(texts) - written, 0)
            await idempotency.update_key(
                lanlan_name, key,
                _done_repairing(written=written, deduped=deduped, kind="visit_facts", content_hash=facts_hash),
            )
        except Exception as exc:  # noqa: BLE001 - Unconfirmed: keep pending; 503 tells caller to retry with the same visit_id.
            logger.warning(f"[MemoryServer] visit_facts {lanlan_name}: not confirmed: {exc!r}")
            raise unavailable from exc
    body = {"ok": True, "written": written, "deduped": deduped}
    if already:
        body["duplicate"] = True
    return body


@app.get("/internal/memory/{lanlan_name}/theater/stories")
async def list_theater_memory_stories(lanlan_name: str):
    """Expose saved public summaries for management after a package is deleted."""
    lanlan_name = validate_lanlan_name(lanlan_name)
    characters = await runtime._config_manager.aload_characters()
    if lanlan_name not in characters.get("猫娘", {}):
        raise HTTPException(status_code=404, detail="character_not_found")
    history = await runtime.recent_history_manager.aget_recent_history(lanlan_name)
    latest, _ = _theater_memory_render_state(history)
    stories = {}
    for (story_id, _), metadata in latest.items():
        if not story_id:
            continue
        story = stories.setdefault(story_id, {"story_id": story_id, "title": "", "memory_summaries": []})
        story["title"] = str(metadata.get("story_title") or story["title"] or story_id)
        summary = str(metadata.get("episode_summary") or metadata.get("ending_summary") or "").strip()
        if summary:
            story["memory_summaries"].append(summary)
    return {"ok": True, "stories": list(stories.values())}


async def _drop_theater_memory_reindexed(
    lanlan_name: str,
    current: list,
    should_drop,
    drop_recent,
    operation: str,
    remaining=None,
):
    """Remove theater capsules from the time index, then from recent.

    The caller holds the character's settle lock and passes the recent history
    it read under it. The recallable index goes first, so a failed recent write
    still leaves the original summary in place. When ``drop_recent`` fails the
    index is rebuilt from what recent actually holds: the drop may have been
    partly persisted, and rolling back to ``current`` would put removed
    capsules back into the recallable index.
    """

    if remaining is None:
        remaining = [message for message in current if not should_drop(message)]
    reconcile_result = await runtime.time_manager.areconcile_theater_conversations(
        _theater_index_events(lanlan_name, remaining),
        lanlan_name,
    )
    try:
        removed_recent = await drop_recent()
    except Exception:
        try:
            try:
                actual = await runtime.recent_history_manager.aget_recent_history(
                    lanlan_name,
                )
            except Exception:
                logger.exception(
                    "[MemoryServer] %s: recent re-read failed; restoring index from snapshot",
                    operation,
                )
                actual = current
            await runtime.time_manager.areconcile_theater_conversations(
                _theater_index_events(lanlan_name, actual),
                lanlan_name,
            )
        except Exception:
            logger.exception("[MemoryServer] %s: time index rollback failed", operation)
        raise
    return removed_recent, reconcile_result


@app.post("/internal/memory/{lanlan_name}/theater/forget")
async def forget_theater_memory(
    lanlan_name: str,
    request: TheaterMemoryForgetRequest,
):
    """Idempotently delete the specified story's hot memory and time index."""  # noqa: DOCSTRING_CJK

    lanlan_name = validate_lanlan_name(lanlan_name)
    story_id = request.story_id.strip()
    if not story_id:
        raise HTTPException(status_code=422, detail="story_id_required")
    try:
        async with runtime._get_settle_lock(lanlan_name):
            # An archive request the theater timed out on may still land after
            # this forget. Record the story tombstone first, under the settle lock
            # /cache checks it under, so such a late write is dropped even when
            # the rest of this forget fails and is retried.
            # Every forget issues a fresh marker; the theater attaches it only to
            # archive requests issued after it recorded this forget, so no write
            # sent before the forget can carry it (no clock is compared).
            forget_marker = await runtime.recent_history_manager.record_theater_story_forget(
                lanlan_name,
                story_id,
            )
            current = await runtime.recent_history_manager.aget_recent_history(
                lanlan_name,
            )
            removed_recent, reconcile_result = await _drop_theater_memory_reindexed(
                lanlan_name,
                current,
                lambda message: (
                    is_theater_memory_message(message)
                    and str(message_metadata(message).get("story_id") or "") == story_id
                ),
                lambda: runtime.recent_history_manager.forget_theater_story(
                    story_id,
                    lanlan_name,
                ),
                "theater story forget",
            )
        return {
            "ok": True,
            "removed_recent": removed_recent,
            "removed_time_index": int(reconcile_result.get("removed") or 0),
            "forget_marker": forget_marker,
        }
    except Exception as exc:
        logger.error(
            "[MemoryServer] 删除 %s 的剧本 %s 记忆失败: %s",
            lanlan_name,
            story_id,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="theater_memory_forget_failed",
        ) from exc


@app.post("/internal/memory/{lanlan_name}/theater/retract")
async def retract_theater_episode(
    lanlan_name: str,
    request: TheaterEpisodeRetractRequest,
):
    """Idempotently remove the episode capsule of one declined theater archive.

    The theater may time out while this server still commits the archive; when
    the player then declines the archive, the theater calls this to take back
    exactly that range's capsule (matched by story, session and through-revision).
    With ``archive_request_id`` it also leaves a persistent tombstone so a /cache
    write of any attempt up to ``archive_attempt`` that is still in flight is
    dropped instead of resurrecting the declined summary.
    """

    lanlan_name = validate_lanlan_name(lanlan_name)
    story_id = request.story_id.strip()
    session_id = request.session_id.strip()
    through = int(request.archive_through_revision)
    if not story_id or not session_id:
        raise HTTPException(status_code=422, detail="theater_episode_identity_required")

    def is_target(message) -> bool:
        return is_retracted_theater_episode(message, story_id, session_id, through)

    archive_request_id = request.archive_request_id.strip()
    try:
        async with runtime._get_settle_lock(lanlan_name):
            if archive_request_id:
                # The archive request may still be in flight (the theater only timed
                # out). Record the tombstone first, under the settle lock /cache
                # checks it under, so a late write of these attempts is dropped even
                # when there is nothing to remove yet.
                await runtime.recent_history_manager.record_theater_retraction(
                    lanlan_name,
                    story_id=story_id,
                    session_id=session_id,
                    archive_through_revision=through,
                    archive_request_id=archive_request_id,
                    archive_attempt=request.archive_attempt,
                )
            current = await runtime.recent_history_manager.aget_recent_history(
                lanlan_name,
            )
            if not any(is_target(message) for message in current):
                return {"ok": True, "removed_recent": 0, "removed_time_index": 0}
            removed_recent, reconcile_result = await _drop_theater_memory_reindexed(
                lanlan_name,
                current,
                is_target,
                lambda: runtime.recent_history_manager.retract_theater_episode(
                    story_id,
                    session_id,
                    through,
                    lanlan_name,
                ),
                "theater episode retract",
                remaining=restored_theater_history(current, story_id, session_id, through),
            )
        return {
            "ok": True,
            "removed_recent": removed_recent,
            "removed_time_index": int(reconcile_result.get("removed") or 0),
        }
    except Exception as exc:
        logger.error(
            "[MemoryServer] retracting theater episode %s/%s for %s failed: %s",
            story_id,
            session_id,
            lanlan_name,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="theater_memory_retract_failed",
        ) from exc


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
            await runtime.recent_history_manager.update_history(
                input_history,
                lanlan_name,
                on_compress_done=review._on_compress_done,
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
            async with runtime._get_settle_lock(lanlan_name):
                await runtime.recent_history_manager.update_history(
                    input_history,
                    lanlan_name,
                    detailed=True,
                    on_compress_done=review._on_compress_done,
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
                await runtime.recent_history_manager.update_history(
                    [],
                    lanlan_name,
                    detailed=True,
                    on_compress_done=review._on_compress_done,
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


@app.get("/get_recent_history/{lanlan_name}")
async def get_recent_history(lanlan_name: str, language: str | None = None):
    lanlan_name = validate_lanlan_name(lanlan_name)
    _lang = _normalize_memory_prompt_lang(_activate_request_language(language))
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

    history = _screen_guarded_recent_history(
        await runtime.recent_history_manager.aget_recent_history(lanlan_name)
    )
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


class MemorySubjectRequest(BaseModel):
    subject_kind: Literal["group_chat", "participant", "group_participant"]
    subject_id: str
    scope: str | None = None

    def to_domain(self):
        from memory.scopes import MemoryScopeError, MemorySubject
        try:
            return MemorySubject.create(
                self.subject_kind, self.subject_id, scope=self.scope,
            )
        except MemoryScopeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


class ScopedFactInput(BaseModel):
    text: str
    importance: int = Field(default=5, ge=1, le=10)
    source: Literal["user_observation", "ai_disclosure"] = "user_observation"


class ScopedFactsWriteRequest(BaseModel):
    subject: MemorySubjectRequest
    facts: list[ScopedFactInput]
    language: str | None = None
    # Optional human-readable name for the subject (group name / member
    # nickname). Untrusted user data: sanitized like speaker_label, then
    # stamped onto the subject's existing persona section metadata so the
    # rendered section header can show a name instead of the bare id.
    # Purely cosmetic — never part of the isolation key.
    display_name: str | None = None


#: Wire-side anchored pattern. pydantic v2 compiles ``Field(pattern=...)`` with
#: the Rust regex crate under UNANCHORED SEARCH semantics, so a bare
#: ``[A-Za-z0-9_.:-]+`` accepts ``'participant:猫娘 A:12:34:56'`` and even values
#: containing newlines — i.e. it is zero validation. The anchors must be
#: ``\A...\z`` (LOWERCASE z) or ``^...$``: the Rust crate does not recognise
#: ``\Z`` and raises ``SchemaError`` at model-definition time. Note that
#: ``memory/identity.py`` goes through Python's ``re``, where ``\A...\Z`` is
#: correct — the two layers must NOT share a pattern string.
_ACTIVITY_EVENT_ID_PATTERN = r"\A[A-Za-z0-9_.:-]+\z"
_SPEAKER_CHANNEL_PATTERN = r"\A[a-z0-9_]{1,16}\z"
_IDEMPOTENCY_KEY_PATTERN = r"\A[A-Za-z0-9_.:-]+\z"


class ActivityEvent(BaseModel):
    """One idempotent per-message activity token.

    Per-message rather than per-batch: the old batch-level identity changed
    whenever a retry grew the batch, so already-acknowledged prefixes got
    counted again — which is the entire reason the plugin grew a three-layer
    ``cancelled.speaker_trust_persisted`` protocol. Deduplicating by id on the
    server makes an amplified retry harmless by construction.
    """

    id: str = Field(
        min_length=8, max_length=96, pattern=_ACTIVITY_EVENT_ID_PATTERN,
    )
    count: int = Field(default=1, ge=1, le=1000)


class ScopedHistorySegment(BaseModel):
    """One single-speaker slice of a batched /scoped_history request."""
    input_history: str
    subject: MemorySubjectRequest
    # Required per segment: the batch prompt attributes facts by segment,
    # and a segment IS one speaker's bucket — an unlabeled segment would
    # render anonymous turns the model cannot attribute.
    speaker_label: str
    # Legacy caller-computed trust. Kept only so a not-yet-flipped plugin build
    # keeps working; mutually exclusive with the server-derived source below.
    speaker_trust: float | None = Field(default=None, ge=0.0, le=1.0)
    # Server-derived trust source, exactly one of these two:
    #   * speaker_tier — platforms with a four-rung permission ladder. A Literal
    #     so a mistyped "Admin" 422s instead of silently landing on a default.
    #   * speaker_base_trust — platforms without a ladder (danmaku guard_level,
    #     medal level). Clamped server-side to SPEAKER_TRUST_MAX_REPORTED_BASE.
    speaker_tier: Literal["admin", "trusted", "normal", "none"] | None = None
    speaker_base_trust: float | None = Field(default=None, ge=0.0, le=1.0)
    # Per-message idempotent activity tokens for this speaker.
    speaker_activity_events: list[ActivityEvent] | None = None
    # Observed transport ("napcat" / "open"). An OBSERVED ATTRIBUTE, never a
    # key: it takes part in no ledger partitioning, no bind/merge predicate and
    # no permission decision. Its only jobs are collision detection and ops
    # diagnostics.
    speaker_channel: str | None = Field(
        default=None, pattern=_SPEAKER_CHANNEL_PATTERN,
    )
    # Stable internal identity. Unlike speaker_label this never enters a prompt.
    speaker_id: str | None = None
    # Request-side authorization bit. It is never rendered or copied from LLM
    # output; only owner-authored raw text may evolve another speaker's trust.
    speaker_is_owner: bool = False
    # Full fact identities authored after this retained owner's observation.
    # Bare ids are not unique across participant scopes.
    trust_signal_excluded_fact_identities: list[
        tuple[str, str, str, str]
    ] = Field(default_factory=list)
    # Optional display name for this segment's subject (see
    # ScopedFactsWriteRequest.display_name).
    display_name: str | None = None


class ScopedHistoryRequest(BaseModel):
    # Legacy single-subject shape (group digests still use it): both fields
    # required together. Optional at the model level only because the
    # batched shape below replaces them; the endpoint 422s when neither
    # shape is complete.
    input_history: str | None = None
    subject: MemorySubjectRequest | None = None
    # Optional speaker identity for single-speaker batches (group-member
    # buckets, private participant digests). The extraction prompt otherwise
    # renders every 'user' turn as the configured private-chat master and
    # extracts facts about the master, misattributing member statements.
    # Group digests omit it — their turns already carry per-message speaker
    # headers in the content.
    speaker_label: str | None = None
    # Optional 0..1 initial trust for the single speaker (same field the
    # batched segments carry; stage one of the speaker-trust mechanism).
    # Only meaningful alongside speaker_label — without a speaker there is
    # no one to trust, so the handler drops it when the label is absent.
    speaker_trust: float | None = Field(default=None, ge=0.0, le=1.0)
    # Server-derived trust source (see ScopedHistorySegment for the contract).
    speaker_tier: Literal["admin", "trusted", "normal", "none"] | None = None
    speaker_base_trust: float | None = Field(default=None, ge=0.0, le=1.0)
    speaker_activity_events: list[ActivityEvent] | None = None
    speaker_channel: str | None = Field(
        default=None, pattern=_SPEAKER_CHANNEL_PATTERN,
    )
    speaker_id: str | None = None
    speaker_is_owner: bool = False
    # Optional display name for the single-subject shape's subject (see
    # ScopedFactsWriteRequest.display_name). Group digests pass the group
    # name here.
    display_name: str | None = None
    # Batched multi-speaker shape: one extraction call covers every segment,
    # each dispatched back to its own subject. Mutually exclusive with the
    # legacy fields. Internal endpoint (the QQ plugin is the only caller,
    # shipped in the same deployment), but the legacy shape stays anyway —
    # the group-digest paths keep using it unchanged.
    segments: list[ScopedHistorySegment] | None = None
    language: str | None = None
    # Optional idempotency key (both shapes). Absent: the request behaves
    # exactly as before and the two fields below are ignored. Present: the
    # handler switches to the generate-then-apply journal (see
    # ``_process_scoped_history_keyed``). Same anchoring rule as
    # ``_ACTIVITY_EVENT_ID_PATTERN`` (Rust regex: ``\A...\z``).
    idempotency_key: str | None = Field(
        default=None, min_length=1, max_length=128,
        pattern=_IDEMPOTENCY_KEY_PATTERN,
    )
    # Diagnostics only. Never compared with anything: ordering against a
    # forget is decided by ``subject_epochs`` alone, so neither side's clock
    # (skew, rollback) can let a stale write through.
    client_requested_at: float | None = None
    # ``{MemorySubject.key: forget_epoch}`` captured by the caller when the
    # round started; reused verbatim by every retry of the same key.
    subject_epochs: dict[str, Annotated[int, Field(ge=0)]] | None = None


def _resolve_trust_source(
    source, *, position: str, speaker_id: str | None,
) -> dict:
    """Validate one segment's trust source and normalize it. 422 on conflict.

    The legacy caller-computed ``speaker_trust`` channel stays accepted while a
    not-yet-flipped plugin build may be in the field, and mutual-exclusion 422s
    make each request self-describing about which protocol it speaks. Removing
    the legacy field entirely is a separate, release-timed change.
    """
    tier = getattr(source, "speaker_tier", None)
    base = getattr(source, "speaker_base_trust", None)
    legacy = getattr(source, "speaker_trust", None)
    channel = getattr(source, "speaker_channel", None)
    raw_events = getattr(source, "speaker_activity_events", None) or []
    has_server_source = tier is not None or base is not None

    if tier is not None and base is not None:
        raise HTTPException(
            status_code=422,
            detail=f"{position}speaker_tier and speaker_base_trust are exclusive",
        )
    if legacy is not None and has_server_source:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{position}speaker_trust is exclusive with the "
                f"server-derived trust source"
            ),
        )
    if has_server_source and not speaker_id:
        # Same rule the label path already states: without a speaker there is
        # nobody to trust.
        raise HTTPException(
            status_code=422,
            detail=f"{position}trust source requires a valid speaker_id",
        )
    if raw_events and not has_server_source:
        raise HTTPException(
            status_code=422,
            detail=f"{position}speaker_activity_events requires a trust source",
        )
    if channel is not None and not has_server_source:
        raise HTTPException(
            status_code=422,
            detail=f"{position}speaker_channel requires a trust source",
        )
    if (
        getattr(source, "speaker_is_owner", False)
        and has_server_source
        and tier != "admin"
    ):
        # Hardening: with the tier on the wire no platform can mint an owner
        # channel by nickname matching, and the unauthenticated self-reported
        # base channel can never grant signing power over other speakers.
        raise HTTPException(
            status_code=422,
            detail=f"{position}speaker_is_owner requires the admin tier",
        )
    # Repeated ids inside one batch are legitimate (identical text sent twice),
    # so deduplicate rather than reject.
    events: list = []
    seen: set[str] = set()
    for event in raw_events:
        if event.id not in seen:
            seen.add(event.id)
            events.append(event)
    return {
        "tier": tier,
        "base": base,
        "legacy": legacy,
        "channel": channel,
        "activity_events": tuple(events),
        "has_server_source": has_server_source,
    }


async def _count_stranded_rows(account_id, snapshot_before) -> int | None:
    """Rows this account wrote into someone else's pile while routing was on.

    The one remediation signal an operator gets for the irreversible surface:
    rows written during a binding carry the CANONICAL subject_id and this
    account's ``speaker_id``, and after an unbind they stay there. There is
    deliberately no move-back endpoint — moving a row means recomputing its
    subject-salted hash, which can collapse it into an existing row at the
    destination. So the operator has to be told a count and decide whether the
    nuclear option (``scoped_forget``) is warranted.

    Best-effort: returns ``None`` if the scan cannot run. An unbind must never
    fail because a diagnostic count did.
    """
    from memory.identity import account_platform, normalize_account_id
    from memory.scopes import subject_from_entry
    from memory.subject_identity import subject_actor

    normalized = normalize_account_id(account_id)
    if normalized is None or runtime.fact_store is None:
        return None
    entity_id = snapshot_before.entity_of(normalized)
    if entity_id is None:
        return 0
    platform = account_platform(normalized)
    canonical = snapshot_before.canonical_account(entity_id, platform)
    if not canonical or canonical == normalized:
        # This account WAS the canonical, so nothing of its was ever routed
        # away from its own subject.
        return 0
    canonical_actor = str(canonical).partition(":")[2]
    try:
        character_data = await runtime._config_manager.aload_characters()
        names = list(character_data.get("猫娘", {}).keys())
    except Exception as exc:  # noqa: BLE001 - diagnostics must not break unbind
        logger.warning(f"[Identity] stranded_rows 无法枚举角色: {exc}")
        return None
    stranded = 0
    for name in names:
        try:
            # ``aload_facts_full`` = active + archived. Rows routed into the
            # canonical pile can have aged into ``facts_archive.json`` already,
            # and counting only the active file would report zero for an
            # account whose stranded copies all archived — while this count is
            # the operator's only cue that ``scoped_forget`` is still needed.
            # The loader already collapses rows present in both files and
            # degrades to active-only on a corrupt archive.
            rows = await runtime.fact_store.aload_facts_full(name)
        except Exception as exc:  # noqa: BLE001 - same
            logger.warning(f"[Identity] stranded_rows 读取 {name} 失败: {exc}")
            return None
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            if normalize_account_id(row.get("speaker_id")) != normalized:
                continue
            subject = subject_from_entry(row)
            if subject is None:
                continue
            if subject.subject_id.split(":")[0] != platform:
                continue
            # Through the DECODING accessor, never a raw segment compare: the
            # subject constructors percent-encode ``:``, so an actor that
            # legitimately contains one reads ``a%3Ab`` here while the account
            # id is ``a:b`` — a raw compare would silently never match and
            # report zero stranded rows.
            if subject_actor(subject) == canonical_actor:
                stranded += 1
    return stranded


def _merge_forget_stats(stats: dict, delta) -> None:
    """Accumulate one target's forget counters into the running total.

    Numeric counters add; booleans OR (a store either did or did not act);
    anything else keeps the last non-null value. Never silently replaces a
    non-zero count with a later zero.
    """
    if not isinstance(delta, dict):
        return
    for key, value in delta.items():
        current = stats.get(key)
        if isinstance(value, bool) or isinstance(current, bool):
            stats[key] = bool(current) or bool(value)
        elif isinstance(value, (int, float)) and isinstance(
            current, (int, float),
        ):
            stats[key] = current + value
        elif value is not None or key not in stats:
            stats[key] = value


def _forget_fanout_targets(subject):
    """Every subject a forget must erase, in a deterministic total order.

    Fans out to the WHOLE PARTICIPANT (maintainer decision): a participant is
    (entity × conversation) and is one isolation unit, so it must be one unit on
    the delete axis too. Erasing only the requested subject would also fail to
    be a real erase once canonical write routing is active, because the routed
    rows sit in the canonical account's pile — "left the group, wipe my data"
    would silently keep a copy.

    NEVER CROSSES A PLATFORM (also a maintainer decision). A conversation id is
    itself platform-prefixed, so a cross-platform account is structurally never
    part of this participant and ``expand_subject`` already filters on it. The
    assertion below makes that a CHECKED property rather than a coincidence, and
    is the seam where a future opt-in cross-platform sweep would plug in — that
    sweep is deliberately a separate, explicitly-requested operation, not a side
    effect of leaving one group.

    Sorted by ``(key, scope)`` so concurrent forgets acquire the per-subject
    locks in the same order and cannot deadlock; the requested subject is
    guaranteed present because expansion only ever grows the set.
    """
    from memory.subject_identity import expand_subject
    from memory import trust_store

    expanded = expand_subject(subject, trust_store.trust_snapshot())
    requested_platform = subject.subject_id.split(":")[0]
    targets = []
    for candidate in expanded:
        if candidate.subject_id.split(":")[0] != requested_platform:
            logger.warning(
                "[scoped_forget] 跳过跨平台扇出目标 %s（forget 不跨平台）",
                candidate.subject_id,
            )
            continue
        targets.append(candidate)
    if not any(
        (target.key, target.scope) == (subject.key, subject.scope)
        for target in targets
    ):  # pragma: no cover - expansion always contains the requested subject
        targets.append(subject)
    return sorted(targets, key=lambda item: (item.key, item.scope))


def _fold_request_subjects(wire_subjects):
    """``wire subjects -> (participant groups, flattened authorization list)``.

    Read-side expansion is never truncated: a participant's marker set is every
    account of that (entity, conversation), because a "first K" rule would make
    the set depend on which account the request happened to start from, and
    filtering is a set-membership test whose cost does not grow with set size
    anyway. The bound lives at bind time.
    """
    from memory.scopes import flatten_groups
    from memory.subject_identity import fold_participants
    from memory import trust_store

    domain = [subject.to_domain() for subject in wire_subjects]
    groups = fold_participants(domain, trust_store.trust_snapshot())
    return groups, list(flatten_groups(groups))


async def _trust_snapshot_for_request():
    """One pool snapshot per request. A single atomic attribute read."""
    from memory import trust_store

    return trust_store.trust_snapshot()


def _apply_canonical_write_routing(parsed: dict, snap) -> None:
    """Route this segment's write to the participant's canonical subject.

    READ-ONLY against the snapshot: it resolves the canonical subject and
    rewrites ``parsed["subject"]``, and seals NOTHING itself. R-CANON-1 lazy
    sealing lives in ``trust_store._apply_trust_mutations_locked``, inside the
    pool's critical section, so it shares that handler's single file write.

    A consequence worth stating on a function labelled IRREVERSIBLE SURFACE:
    ``/scoped_facts`` calls this but carries no trust mutation, so it never
    seals — it only routes by an ALREADY sealed canonical. That is correct, but
    it is not what "lazily seals on first write" would lead a reader to expect.

    IRREVERSIBLE SURFACE, stated plainly: rows written while routing is active
    carry the CANONICAL subject_id and the REAL account's speaker_id. After an
    unbind those rows stay in somebody else's pile, and there is deliberately no
    move-back endpoint — moving a row requires recomputing its subject-salted
    hash, which can collapse it into an existing row at the destination, i.e.
    exactly the trap being avoided. ``unbind`` reports a ``stranded_rows``
    count; the nuclear option is ``scoped_forget``.
    """
    from memory.subject_identity import canonical_subject

    subject = parsed.get("subject")
    if subject is None:
        return
    routed = canonical_subject(subject, snap)
    if routed is not subject and (
        routed.key != subject.key or routed.scope != subject.scope
    ):
        parsed["subject"] = routed
        parsed["canonical_routed"] = True


def _stamp_resolved_trust(parsed: dict, snap) -> None:
    """Resolve this segment's trust from the pool and stamp it, or abstain.

    The key name ``speaker_trust`` is unchanged on purpose: ``FactStore``'s
    ``_speaker_provenance_of`` / ``extract_facts`` / ``extract_facts_batch``
    then need no change at all.

    ``None`` means DO NOT WRITE THE KEY. Falling back to a default would stamp
    a finite value on rows that legitimately carry none today (group digests
    already go through a shape that omits it), flipping arbitration from
    abstention to an active vote.
    """
    # From the SAME snapshot that routed the subject — one pool read per
    # request (§4.4), so the stamp stays a pure function of the request-start
    # state even if a bind/unbind lands mid-request.
    speaker_id = parsed.get("speaker_id")
    if speaker_id:
        entity_id = snap.entity_of(speaker_id)
        if entity_id:
            parsed["speaker_entity_id"] = entity_id
    source = parsed.get("trust_source") or {}
    if not source.get("has_server_source"):
        return
    resolved = snap.resolve_trust(
        parsed.get("speaker_id"),
        tier=source.get("tier"),
        base=source.get("base"),
    )
    if resolved is None:
        # Either the platform's legacy barrier is still pending, or the id is
        # unusable. Both abstain; only the former is worth reporting.
        parsed["speaker_trust"] = None
        from memory.identity import account_platform

        if parsed.get("speaker_id") and snap.barrier_pending(
            account_platform(parsed["speaker_id"])
        ):
            parsed["trust_gated"] = "legacy_import_pending"
        return
    parsed["speaker_trust"] = resolved


def _trust_mutation_for(parsed: dict) -> "object | None":
    """Build the pool mutation for one parsed segment, or ``None``."""
    from memory.trust_store import ActivityEvent as PoolActivityEvent
    from memory.trust_store import TrustMutation

    speaker_id = parsed.get("speaker_id")
    trust_source = parsed.get("trust_source") or {}
    signal_events = tuple(parsed.get("trust_signal_events") or ())
    activity = tuple(
        PoolActivityEvent(id=event.id, count=event.count)
        for event in (parsed.get("trust_activity_events") or ())
    )
    if not signal_events and not activity and not trust_source.get("channel"):
        return None
    return TrustMutation(
        speaker_account_id=speaker_id,
        activity_events=activity,
        signal_events=signal_events,
        channel=trust_source.get("channel"),
    )


async def _apply_trust_for_segments(parsed_segments):
    """Fold the whole batch into the pool with ONE write. Never raises.

    Returns ``(result, outcomes)`` where ``outcomes`` is aligned index-for-index
    with ``parsed_segments`` so each segment can report its own numbers.

    This is the handler's last durable write and it comes after every FactStore
    call. The keyed path calls it while holding the fact persistence lock
    (key_lock -> persist_alock -> pool lock); the pool lock stays a leaf
    because nothing is acquired and no FactStore code runs while it is held.
    Any failure before this point shows up as "trust did not move at all".
    """
    from memory.trust_store import MutationOutcome

    mutations = []
    positions = []
    for index, segment in enumerate(parsed_segments):
        mutation = _trust_mutation_for(segment)
        if mutation is not None:
            mutations.append(mutation)
            positions.append(index)
    outcomes = [MutationOutcome() for _ in parsed_segments]
    if not mutations:
        return None, outcomes
    from memory import trust_store

    result = await trust_store.aapply_trust_mutations(mutations)
    for position, outcome in zip(positions, result.per_mutation):
        outcomes[position] = outcome
    if not result.persisted:
        logger.warning(
            "[Trust] 池未落盘，本批 %d 段回传 persisted=false 让调用方保留重试",
            len(mutations),
        )
    return result, outcomes


def _trust_response_block(parsed: dict, result, outcome) -> dict:
    """The per-segment ``trust`` block.

    ``persisted`` drives the caller's retain-or-pop decision and MUST be
    reported honestly:

    | segment status | trust.persisted   | caller action        |
    |----------------|-------------------|----------------------|
    | ok             | true / null       | pop the bucket       |
    | ok             | false             | RETAIN and retry     |
    | failed / lost  | —                 | retain (unchanged)   |

    ``false`` has to reach the plugin because at-least-once delivery of owner
    signals depends on it: the replay ring in ``memory/facts.py`` is gated on
    ``observation_id``, which is a hash of one of the CURRENT request's owner
    messages — retry semantics, not time semantics. Always answering 200 and
    letting the caller pop would break that chain, and one disk hiccup would
    silently and permanently lose a ±0.04/0.08 owner correction.

    ``persisted=null`` means this segment had NOTHING to settle, which is
    different from "the write failed".

    The reported condition is "did this segment attempt a pool mutation", NOT
    "did it carry a tier". An owner segment sent before the migration push
    lands still carries ``speaker_is_owner`` with no ``speaker_tier``, and the
    route still evaluates, persists and folds its owner signals — reporting
    ``null`` for those would let the caller pop a bucket whose correction was
    deferred by the barrier or lost to a failed pool write.
    """
    trust_source = parsed.get("trust_source") or {}
    attempted = bool(
        trust_source.get("has_server_source")
        or parsed.get("trust_signal_events")
    )
    if not attempted:
        # Same key set as the branch below — a field that appears in only one
        # shape of the same response block is a contract a caller cannot write
        # against without a KeyError guard.
        return {
            "resolved": parsed.get("speaker_trust"),
            "persisted": None,
            "signals_applied": 0,
            "activity_applied": 0,
            "gated": None,
            "channel_collision": False,
        }
    return {
        "resolved": parsed.get("speaker_trust"),
        "persisted": bool(result.persisted) if result is not None else None,
        "signals_applied": int(getattr(outcome, "signals_applied", 0) or 0),
        "activity_applied": int(getattr(outcome, "activity_applied", 0) or 0),
        "gated": (
            "legacy_import_pending"
            if parsed.get("trust_gated")
            or int(getattr(outcome, "signals_deferred", 0) or 0) > 0
            else None
        ),
        "channel_collision": bool(
            getattr(outcome, "channel_collision", False)
        ),
    }


class ScopedContextRequest(BaseModel):
    subjects: list[MemorySubjectRequest]
    language: str | None = None


class ScopedMentionsRequest(BaseModel):
    response_text: str
    subjects: list[MemorySubjectRequest]


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


def _sanitized_display_name(raw: str | None, *, context: str) -> str | None:
    """Normalize an untrusted display_name from a scoped write request.

    Same length contract as speaker_label (>64 is a caller bug, fail loud);
    same structural-character neutralization (the value ends up in a prompt
    section header, the exact attack surface #2605 closed for speaker_label).
    Unlike speaker_label there is no fallback when sanitization empties it:
    the name is cosmetic, absent is a valid state.
    """
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    if len(value) > 64:
        raise HTTPException(
            status_code=422,
            detail=f"{context}: display_name must contain at most 64 characters",
        )
    from memory.facts import FactStore

    return FactStore.sanitize_speaker_label(value) or None


async def _stamp_subject_display_name(
    lanlan_name: str, subject, display_name: str | None, *, strict: bool = False,
) -> None:
    """Best-effort display-name refresh after a successful scoped write.

    Never fails the write: the facts are already persisted, and a display
    name is metadata the next write can supply again. ``strict`` (the keyed
    journal) re-raises instead: there the item is only marked applied once
    the name is persisted, and a same-key retry is the only next write.
    """
    if not display_name or runtime.persona_manager is None:
        return
    try:
        # strict raises on unreadable persona files (False means normal no-op, such as missing section / no change).
        await runtime.persona_manager.aupdate_subject_display_name(
            lanlan_name, subject, display_name, **({"strict": True} if strict else {}),
        )
    except Exception as exc:
        if strict:
            raise
        logger.warning(
            f"[scoped] display_name 刷新失败（忽略，写入已完成）: {exc}"
        )


@app.post("/internal/memory/{lanlan_name}/scoped_facts")
async def append_scoped_facts(lanlan_name: str, req: ScopedFactsWriteRequest):
    """Append already-extracted facts to one explicit group/member subject.

    This is the low-cost group-chat write path: adapters submit a small batch of
    stable facts instead of forcing the full private-chat post-turn pipeline on
    every busy group message. The memory core owns subject stamping, exact and
    semantic deduplication, and persistence.
    """
    lanlan_name = validate_lanlan_name(lanlan_name)
    if runtime.fact_store is None:
        raise HTTPException(
            status_code=503,
            detail="memory_server not fully initialized (limited mode or startup incomplete)",
        )
    if not req.facts or len(req.facts) > 32:
        raise HTTPException(status_code=422, detail="facts must contain 1..32 items")
    extracted: list[dict] = []
    for item in req.facts:
        text = item.text.strip()
        if not text or len(text) > 2000:
            raise HTTPException(
                status_code=422,
                detail="each fact text must contain 1..2000 characters",
            )
        extracted.append({
            "text": text,
            "importance": item.importance,
            "source": item.source,
        })
    subject = req.subject.to_domain()
    # Canonical write routing, before the locale reservation for the same
    # read/write-same-key reason as the /scoped_history paths.
    _routing = {"subject": subject}
    _apply_canonical_write_routing(_routing, await _trust_snapshot_for_request())
    subject = _routing["subject"]
    display_name = _sanitized_display_name(
        req.display_name, context="scoped_facts",
    )
    locale_order = None
    if is_supported_language_code(req.language):
        locale_admission_order = (
            locale_state.allocate_subject_prompt_locale_order(
                lanlan_name,
                subject,
            )
        )
        locale_order = await asyncio.to_thread(
            locale_state.reserve_subject_prompt_locale_order,
            lanlan_name,
            subject,
            order=locale_admission_order,
        )
        await asyncio.to_thread(
            locale_state.record_subject_prompt_locale,
            lanlan_name,
            subject,
            req.language,
            order=locale_order,
        )
    created = await runtime.fact_store.apersist_scoped_facts(
        lanlan_name,
        extracted,
        subject=subject,
    )
    await _stamp_subject_display_name(lanlan_name, subject, display_name)
    return {
        "status": "stored",
        "subject": subject.as_entry_fields(),
        "created": len(created),
        "fact_ids": [fact.get("id") for fact in created if fact.get("id")],
    }


@app.post("/internal/memory/{lanlan_name}/scoped_history")
async def process_scoped_history(lanlan_name: str, req: ScopedHistoryRequest):
    """Extract scoped facts from a bounded group-chat digest/history batch."""
    with language_context(_activate_request_language(req.language)):
        return await _process_scoped_history(lanlan_name, req)


async def _process_scoped_history(lanlan_name: str, req: ScopedHistoryRequest):
    lanlan_name = validate_lanlan_name(lanlan_name)
    if runtime.fact_store is None:
        raise HTTPException(
            status_code=503,
            detail="memory_server not fully initialized (limited mode or startup incomplete)",
        )
    if req.idempotency_key is not None:
        if req.idempotency_key.startswith(_RESERVED_KEY_PREFIXES):
            # Shares key records with keyed /cache and visit_facts: these two prefixes belong exclusively to them.
            raise HTTPException(status_code=422, detail="idempotency_key uses a reserved prefix")
        _reject_owner_signal_on_keyed_request(req)
    if req.segments is not None:
        return await _process_scoped_history_segments(lanlan_name, req)
    if req.input_history is None or req.subject is None:
        raise HTTPException(
            status_code=422,
            detail="either segments or input_history+subject is required",
        )
    try:
        input_history = convert_to_messages(json.loads(req.input_history))
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="invalid input_history") from exc
    if not input_history or len(input_history) > 200:
        raise HTTPException(
            status_code=422,
            detail="input_history must contain 1..200 messages",
        )
    raw_speaker_label = (req.speaker_label or "").strip() or None
    if raw_speaker_label and len(raw_speaker_label) > 64:
        raise HTTPException(
            status_code=422,
            detail="speaker_label must contain at most 64 characters",
        )
    from memory.facts import (
        FactExtractionFailed,
        FactStore,
        _speaker_trust_fact_identity,
    )

    speaker_label = (
        FactStore.sanitize_speaker_label(raw_speaker_label)
        if raw_speaker_label else None
    )
    if raw_speaker_label and not speaker_label:
        raise HTTPException(
            status_code=422,
            detail="speaker_label must contain non-structural characters",
        )
    # Provenance accepts only caller-supplied labels (trust phase one: who said it). Freeze
    # before default group-digest filling below: collective descriptors are not speakers. Trust
    # attaches to label: without a speaker there is nobody to trust (discard even caller-supplied trust
    # for unlabeled group digests). Omit absent trust keys, matching provenance shape
    # from _speaker_provenance_of on the batch-segment path.
    from memory.speaker_trust import stable_speaker_id
    speaker_id = stable_speaker_id(req.speaker_id)
    if req.speaker_id is not None and speaker_id is None:
        raise HTTPException(status_code=422, detail="invalid speaker_id")
    trust_source = _resolve_trust_source(
        req, position="", speaker_id=speaker_id,
    )
    # One snapshot for the whole request, taken before any FactStore call.
    trust_snapshot_for_request = await _trust_snapshot_for_request()
    trust_state: dict = {
        "speaker_id": speaker_id,
        "trust_source": trust_source,
        "trust_activity_events": trust_source["activity_events"],
        "speaker_trust": req.speaker_trust,
    }
    _stamp_resolved_trust(trust_state, trust_snapshot_for_request)
    speaker_provenance = None
    if speaker_label:
        speaker_provenance = {"speaker_label": speaker_label}
        resolved_trust = trust_state.get("speaker_trust")
        if resolved_trust is not None:
            speaker_provenance["speaker_trust"] = resolved_trust
        if speaker_id is not None:
            speaker_provenance["speaker_id"] = speaker_id
            entity_id = trust_state.get("speaker_entity_id")
            if entity_id:
                speaker_provenance["speaker_entity_id"] = entity_id
    subject = req.subject.to_domain()
    # Canonical write routing, deliberately BEFORE the locale reservation below
    # so the durable per-subject locale is keyed by the same subject the read
    # side will resolve to.
    trust_state["subject"] = subject
    _apply_canonical_write_routing(trust_state, trust_snapshot_for_request)
    subject = trust_state["subject"]
    display_name = _sanitized_display_name(
        req.display_name, context="scoped_history",
    )
    if req.idempotency_key is not None:
        # Route keyed requests before any writes: above performs only validation and pure computation
        # (snapshots/routing); language state/extraction/persistence all use the generate-then-apply journal.
        if speaker_label is None and subject.kind == "group_chat":
            # Same default as unkeyed path below (another same-name local import exists in this function;
            # import locally here too, or the local name is unbound).
            from config.prompts.prompts_memory import get_group_digest_speaker_label
            from utils.language_utils import get_global_language_full
            speaker_label = get_group_digest_speaker_label(get_global_language_full())
        return await _process_scoped_history_keyed(
            lanlan_name,
            req,
            shape="single",
            contexts=[{
                "wire_subject": req.subject.to_domain(),
                "subject": subject,
                "display_name": display_name,
                "speaker_provenance": speaker_provenance,
                "speaker_label": speaker_label,
                "messages": input_history,
                "trust_state": trust_state,
            }],
        )
    locale_order = None
    if is_supported_language_code(req.language):
        locale_admission_order = (
            locale_state.allocate_subject_prompt_locale_order(
                lanlan_name,
                subject,
            )
        )
        locale_order = await asyncio.to_thread(
            locale_state.reserve_subject_prompt_locale_order,
            lanlan_name,
            subject,
            order=locale_admission_order,
        )
        await asyncio.to_thread(
            locale_state.record_subject_prompt_locale,
            lanlan_name,
            subject,
            req.language,
            order=locale_order,
        )
    if speaker_label is None and subject.kind == "group_chat":
        # Group digest has no single speaker: without a label, legacy prompts restrict extraction
        # to facts about the private-chat owner, checkpointing members' self-reports as empty extraction.
        # A collective descriptor retargets {MASTER_NAME}, paired with each message's speaker header
        # in content. Use full locale: Traditional Chinese users match zh-TW
        # (getter normalizes with keep_traditional).
        from config.prompts.prompts_memory import get_group_digest_speaker_label
        from utils.language_utils import get_global_language_full
        speaker_label = get_group_digest_speaker_label(get_global_language_full())
    # fail_closed: callers (QQ plugin finalize/focus-shift) advance cursors and discard member buckets
    # on success. Histories exist only in caller memory, unlike legacy
    # /process persisting time_indexed.db first. Expose extraction failures as HTTP errors
    # so callers retain buffers for retries next round; genuinely empty extraction still checkpoints with 200.
    signal_facts = None
    if req.speaker_is_owner:
        signal_facts = [
            dict(fact)
            for fact in await runtime.fact_store.aload_facts(lanlan_name)
            if isinstance(fact, dict)
        ]
    reconciled_facts = []
    try:
        created = await runtime.fact_store.extract_facts(
            input_history,
            lanlan_name,
            subject=subject,
            fail_closed=True,
            speaker_label=speaker_label,
            speaker_provenance=speaker_provenance,
            reconciled_facts=reconciled_facts,
        )
    except FactExtractionFailed as exc:
        raise HTTPException(
            status_code=502,
            detail="scoped fact extraction failed; retry later",
        ) from exc
    await _stamp_subject_display_name(lanlan_name, subject, display_name)
    trust_events = []
    if req.speaker_is_owner:
        # Evaluate against the authored-order view, before this owner's exact
        # dedup could mix away the target provenance.  The final reload still
        # revalidates concurrent forgets and provenance changes; only a change
        # reported by this extraction is replayed back to the pre-write row.
        def _key(fact: dict) -> tuple:
            identity = _speaker_trust_fact_identity(fact)
            if identity is not None:
                return identity
            return (
                str(fact.get("id")),
                fact.get("subject_kind"),
                fact.get("subject_id"),
                fact.get("scope"),
            )

        def _provenance(fact: dict) -> dict:
            return {
                key: fact[key]
                for key in (
                    "speaker_id", "speaker_label", "speaker_trust",
                    "speaker_entity_id", "speaker_provenance_mixed",
                )
                if key in fact
            }

        current_by_key = {
            _key(fact): dict(fact)
            for fact in await runtime.fact_store.aload_facts(lanlan_name)
            if isinstance(fact, dict) and fact.get("id") is not None
        }
        reconciled_by_key = {
            _key(fact): dict(fact)
            for fact in reconciled_facts
            if isinstance(fact, dict) and fact.get("id") is not None
        }
        authored_by_key = {
            _key(fact): dict(fact)
            for fact in signal_facts or []
            if isinstance(fact, dict) and fact.get("id") is not None
        }
        active_signal_facts = []
        for authored_fact in signal_facts or []:
            if authored_fact.get("id") is None:
                continue
            current_fact = current_by_key.get(_key(authored_fact))
            if current_fact is None:
                continue
            reconciled = reconciled_by_key.get(_key(authored_fact))
            active_signal_facts.append(
                authored_fact
                if (
                    reconciled is not None
                    and _provenance(current_fact) == _provenance(reconciled)
                )
                else current_fact
            )
        replay_signal_facts = list(active_signal_facts)
        replay_signal_facts.extend(
            await runtime.fact_store
            .aload_archived_speaker_trust_signal_facts(lanlan_name)
        )
        trust_events = await runtime.fact_store.aevaluate_speaker_trust_events(
            lanlan_name,
            input_history,
            subject=subject,
            speaker_provenance=speaker_provenance,
            speaker_is_owner=True,
            facts_snapshot=active_signal_facts,
            replay_facts_snapshot=replay_signal_facts,
            identity=trust_snapshot_for_request,
        )
        if trust_events:
            try:
                trust_events = await (
                    runtime.fact_store.apersist_speaker_trust_events(
                        lanlan_name,
                        trust_events,
                        expected_reconciliations=reconciled_by_key,
                    )
                )
            except Exception:
                # Exact dedup and trust-event attachment are separate durable
                # writes. Restore this request's provenance reconciliation
                # before retrying the event write so a transient second-write
                # failure cannot make the retained caller bucket lose its
                # authored signal on retry.
                await runtime.fact_store.arollback_speaker_trust_reconciliations(
                    lanlan_name,
                    expected_reconciliations=reconciled_by_key,
                    previous_facts=authored_by_key,
                )
                trust_events = await (
                    runtime.fact_store.apersist_speaker_trust_events(
                        lanlan_name,
                        trust_events,
                        expected_reconciliations=reconciled_by_key,
                    )
                )
    # Last durable write of the handler, same contract as the batched path.
    trust_state["trust_signal_events"] = tuple(trust_events or ())
    trust_result, trust_outcomes = await _apply_trust_for_segments(
        [trust_state],
    )
    return {
        "status": "processed",
        "subject": subject.as_entry_fields(),
        "created": len(created),
        "fact_ids": [fact.get("id") for fact in created if fact.get("id")],
        "trust": _trust_response_block(
            trust_state, trust_result, trust_outcomes[0],
        ),
        # Legacy field (see the batched path).
        "trust_events": trust_events,
    }


async def _process_scoped_history_segments(
    lanlan_name: str, req: ScopedHistoryRequest,
) -> dict:
    """The batched multi-speaker shape of /scoped_history.

    One extraction call covers all segments; the response reports one
    result per segment **in request order** — the caller pops exactly the
    buckets whose segment came back \"ok\" and retries only the rest, so a
    single failed segment no longer drags the whole batch back through
    another extraction.

    \"ok\" means the model reached a conclusion for this segment (even \"no facts
    worth remembering\"), not merely \"no error\". Omitted segments return failed; callers
    retain those buckets, the sole member-level copy: popping would lose them.
    """  # noqa: DOCSTRING_CJK
    from config import (
        SCOPED_HISTORY_BATCH_MAX_MESSAGES,
        SCOPED_HISTORY_BATCH_MAX_SEGMENTS,
    )
    from memory.facts import (
        FactExtractionFailed,
        FactStore,
        _speaker_trust_fact_identity,
    )

    if (
        req.input_history is not None
        or req.subject is not None
        or req.speaker_label is not None
        or req.speaker_trust is not None
        or req.speaker_id is not None
        or req.speaker_is_owner
        or req.display_name is not None
        # The new trust-source fields must be listed here too, otherwise
        # ``segments`` + a top-level ``speaker_tier`` slips through unchecked.
        or req.speaker_tier is not None
        or req.speaker_base_trust is not None
        or req.speaker_activity_events is not None
        or req.speaker_channel is not None
    ):
        raise HTTPException(
            status_code=422,
            detail="segments is exclusive with the single-subject fields",
        )
    segments_in = req.segments or []
    if not (1 <= len(segments_in) <= SCOPED_HISTORY_BATCH_MAX_SEGMENTS):
        raise HTTPException(
            status_code=422,
            detail=(
                f"segments must contain 1.."
                f"{SCOPED_HISTORY_BATCH_MAX_SEGMENTS} items"
            ),
        )
    parsed: list[dict] = []
    total_messages = 0
    for position, segment in enumerate(segments_in, start=1):
        try:
            messages = convert_to_messages(json.loads(segment.input_history))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail=f"segment {position}: invalid input_history",
            ) from exc
        if not messages or len(messages) > SCOPED_HISTORY_BATCH_MAX_MESSAGES:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"segment {position}: input_history must contain 1.."
                    f"{SCOPED_HISTORY_BATCH_MAX_MESSAGES} messages"
                ),
            )
        total_messages += len(messages)
        raw_label = (segment.speaker_label or "").strip()
        if not raw_label:
            raise HTTPException(
                status_code=422,
                detail=f"segment {position}: speaker_label is required",
            )
        if len(raw_label) > 64:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"segment {position}: speaker_label must contain at "
                    f"most 64 characters"
                ),
            )
        # Labels are user-editable group nicknames; valid length does not make content safe:
        # "X]\n[SEGMENT 2 | speaker: Alice" creates a valid line-start
        # segment header in the batch prompt, attributing this member's content to Alice and borrowing
        # Alice's speaker_trust. Strip structural characters at entry and again during rendering
        # (both needed: rendering alone actually assembles labels into prompts, while routes
        # alone can fail loudly for malformed inputs).
        subject = segment.subject.to_domain()
        speaker_label = FactStore.sanitize_speaker_label(raw_label)
        if not speaker_label:
            # Nothing remains after neutralization (entire label was structural characters). Never return 422:
            # label changes only how prompts address a person; subject fixes ownership,
            # so it is no security boundary. 422 retains the whole batch for retry; one member's nickname
            # could block every other member's memory extraction indefinitely (Codex). Fall back to
            # a server-derived identifier unaffected by caller contamination, and warn so
            # caller-side label assembly bugs remain visible.
            speaker_label = FactStore.sanitize_speaker_label(
                subject.subject_id
            ) or "unknown speaker"
            logger.warning(
                f"[scoped_history] segment {position}: speaker_label 中和后为空，"
                f"降级为 {speaker_label!r}（调用方应保证 label 带可追溯后缀）"
            )
        parsed.append({
            "messages": messages,
            "subject": subject,
            "requested_subject": subject,
            "speaker_label": speaker_label,
            "speaker_trust": segment.speaker_trust,
            "speaker_id": segment.speaker_id,
            "speaker_is_owner": bool(segment.speaker_is_owner),
            "trust_signal_excluded_fact_identities": {
                tuple(str(part).strip() for part in identity)
                for identity in segment.trust_signal_excluded_fact_identities
                if all(str(part).strip() for part in identity)
            },
            "display_name": _sanitized_display_name(
                segment.display_name, context=f"segment {position}",
            ),
        })
        from memory.speaker_trust import stable_speaker_id
        parsed_speaker_id = stable_speaker_id(segment.speaker_id)
        if segment.speaker_id is not None and parsed_speaker_id is None:
            raise HTTPException(
                status_code=422,
                detail=f"segment {position}: invalid speaker_id",
            )
        parsed[-1]["speaker_id"] = parsed_speaker_id
        parsed[-1]["trust_source"] = _resolve_trust_source(
            segment,
            position=f"segment {position}: ",
            speaker_id=parsed_speaker_id,
        )
        parsed[-1]["trust_activity_events"] = (
            parsed[-1]["trust_source"]["activity_events"]
        )
    if total_messages > SCOPED_HISTORY_BATCH_MAX_MESSAGES:
        # Batch LLM input work has the same bound as legacy single requests: callers package
        # using this constant; excess is a contract bug, fail loudly.
        raise HTTPException(
            status_code=422,
            detail=(
                f"segments must contain at most "
                f"{SCOPED_HISTORY_BATCH_MAX_MESSAGES} messages in total"
            ),
        )
    # ── trust: one snapshot for the whole request, taken BEFORE any FactStore
    # call, and write-side canonical routing BEFORE the locale reservation.
    #
    # Timing rule: the ``speaker_trust`` stamped on this request's facts is the
    # pool state as of the START of the request, before this request's own
    # events land. Otherwise "deduct the owner's correction of X, then stamp
    # X's own fact" would make the result depend on segment order, and the
    # handler would stop being retry-safe.
    #
    # Ordering rule: routing must precede the locale reservation below, because
    # the read side resolves the durable per-subject locale through the same
    # canonical mapping. Reserving under the requested subject and reading under
    # the canonical one would miss forever and silently fall back to the
    # character-level locale — which is the whole point of storing it.
    trust_snapshot_for_request = await _trust_snapshot_for_request()
    for segment in parsed:
        _apply_canonical_write_routing(segment, trust_snapshot_for_request)
        _stamp_resolved_trust(segment, trust_snapshot_for_request)

    if req.idempotency_key is not None:
        # Same branching point as single-subject form; no writes yet.
        return await _process_scoped_history_keyed(
            lanlan_name,
            req,
            shape="segments",
            contexts=[
                {
                    "wire_subject": segment["requested_subject"],
                    "subject": segment["subject"],
                    "display_name": segment.get("display_name"),
                    "speaker_provenance": FactStore._speaker_provenance_of(
                        segment,
                    ),
                    "trust_state": segment,
                }
                for segment in parsed
            ],
            prompt_segments=parsed,
        )

    signal_facts = None
    if any(segment.get("speaker_is_owner") for segment in parsed):
        # Freeze the pre-batch view. After extraction we replay successful
        # created rows into this private list in request order, so an owner
        # sees earlier member statements but never borrows knowledge from a
        # later segment that happened to persist in the same LLM batch.
        signal_facts = [
            dict(fact)
            for fact in await runtime.fact_store.aload_facts(lanlan_name)
            if isinstance(fact, dict)
        ]
    locale_orders: list[int | None]
    if is_supported_language_code(req.language):
        subjects = [segment["subject"] for segment in parsed]
        locale_admission_orders = (
            locale_state.allocate_subject_prompt_locale_orders(
                lanlan_name,
                subjects,
            )
        )
        locale_orders = await asyncio.to_thread(
            locale_state.reserve_subject_prompt_locale_orders,
            lanlan_name,
            subjects,
            orders=locale_admission_orders,
        )
        await asyncio.to_thread(
            locale_state.record_subject_prompt_locales,
            lanlan_name,
            [
                (segment["subject"], req.language, locale_order)
                for segment, locale_order in zip(parsed, locale_orders)
            ],
        )
    else:
        locale_orders = [None] * len(parsed)
    # fail_closed semantics (matching legacy single-request comments): callers pop successful
    # buckets existing only in their memory. Whole-batch extraction failure returns 502 (retain all
    # for retry); individual persistence failure marks that segment failed in response body.
    try:
        segment_results = await runtime.fact_store.extract_facts_batch(
            parsed, lanlan_name,
        )
    except FactExtractionFailed as exc:
        raise HTTPException(
            status_code=502,
            detail="scoped fact extraction failed; retry later",
        ) from exc
    if len(segment_results) != len(parsed):
        # Extraction contract: one result per segment in request order. Unequal lengths signal drift;
        # zip below silently truncates trailing segments while callers consume positionally. Never guess:
        # fail the entire batch so callers retain all buckets for retry.
        raise HTTPException(
            status_code=502,
            detail="scoped fact extraction returned mismatched segments",
        )

    def _speaker_provenance_fields(fact: dict) -> dict:
        return {
            key: fact[key]
            for key in (
                "speaker_id", "speaker_label", "speaker_trust",
                "speaker_entity_id", "speaker_provenance_mixed",
            )
            if key in fact
        }

    def _fact_identity(fact: dict) -> tuple:
        identity = _speaker_trust_fact_identity(fact)
        if identity is not None:
            return identity
        return (
            str(fact.get("id")), fact.get("subject_kind"),
            fact.get("subject_id"), fact.get("scope"),
        )

    owner_signal_jobs = []
    for segment, result in zip(parsed, segment_results):
        segment["trust_events"] = []
        owner_signal_job = None
        if (
            signal_facts is not None
            and segment.get("speaker_is_owner")
        ):
            # Every retained owner observation is evaluated, even when fact
            # extraction wholly failed for that segment.  The durable event is
            # hidden from a failed response below and replayed on retry, while
            # freezing here prevents a later segment's reconciliation from
            # erasing the provenance that was valid at authored time.
            # Freeze the authored-order rows as well as their allow-list.
            # The final reload below still revalidates concurrent changes,
            # except for ids reconciled by this or a later request segment:
            # those changes occurred after this owner's observation and must
            # not flow backward into its trust decision.
            owner_signal_job = {
                "segment": segment,
                "facts_by_key": {
                    _fact_identity(fact): dict(fact)
                    for fact in signal_facts
                    if isinstance(fact, dict) and fact.get("id") is not None
                },
                "later_reconciled_by_key": {},
                "own_reconciled_by_key": {},
            }
            owner_signal_jobs.append(owner_signal_job)
        if signal_facts is not None:
            reconciled_by_key = {
                _fact_identity(fact): dict(fact)
                for fact in (result.get("reconciled") or [])
                if isinstance(fact, dict) and fact.get("id") is not None
            }
            if reconciled_by_key:
                for job in owner_signal_jobs:
                    job["later_reconciled_by_key"].update(reconciled_by_key)
                if owner_signal_job is not None:
                    owner_signal_job["own_reconciled_by_key"].update(
                        reconciled_by_key
                    )
                signal_facts[:] = [
                    reconciled_by_key.get(_fact_identity(fact), fact)
                    for fact in signal_facts
                ]
            signal_facts.extend(
                dict(fact)
                for fact in (result.get("created") or [])
                if isinstance(fact, dict)
            )
        # Refresh display names only where the model reached a conclusion for that segment: failed buckets
        # remain whole for retry, including names next time; no need to touch persona on failure.
        if result.get("status") == "ok":
            await _stamp_subject_display_name(
                lanlan_name, segment["subject"], segment.get("display_name"),
            )
    if owner_signal_jobs:
        # This is the final I/O await in the endpoint. Every display-name
        # write for every segment has completed, so a forget racing any of
        # those writes is reflected in the active rows below.
        current_by_key = {
            _fact_identity(fact): dict(fact)
            for fact in await runtime.fact_store.aload_facts(lanlan_name)
            if isinstance(fact, dict) and fact.get("id") is not None
        }
        archived_signal_facts = await (
            runtime.fact_store.aload_archived_speaker_trust_signal_facts(
                lanlan_name,
            )
        )
        for job in owner_signal_jobs:
            segment = job["segment"]
            excluded_fact_identities = segment[
                "trust_signal_excluded_fact_identities"
            ]
            active_signal_facts = []
            for key, authored_fact in job["facts_by_key"].items():
                if key in excluded_fact_identities:
                    continue
                current_fact = current_by_key.get(key)
                if current_fact is None:
                    continue
                batch_reconciled = job["later_reconciled_by_key"].get(key)
                active_signal_facts.append(
                    authored_fact
                    if (
                        batch_reconciled is not None
                        and _speaker_provenance_fields(current_fact)
                        == _speaker_provenance_fields(batch_reconciled)
                    )
                    else current_fact
                )
            replay_signal_facts = active_signal_facts + [
                fact for fact in archived_signal_facts
                if _fact_identity(fact) not in excluded_fact_identities
            ]
            segment["trust_events"] = [
                event for event in (
                    await runtime.fact_store.aevaluate_speaker_trust_events(
                        lanlan_name,
                        segment["messages"],
                        subject=segment["subject"],
                        speaker_provenance={
                            "speaker_id": segment.get("speaker_id"),
                            "speaker_trust": segment.get("speaker_trust"),
                            "speaker_label": segment.get("speaker_label"),
                        },
                        speaker_is_owner=True,
                        facts_snapshot=active_signal_facts,
                        replay_facts_snapshot=replay_signal_facts,
                        identity=trust_snapshot_for_request,
                    )
                )
                if (
                    str(event.get("source_fact_id") or ""),
                    event.get("source_subject_kind"),
                    event.get("source_subject_id"),
                    event.get("source_scope"),
                ) not in excluded_fact_identities
            ]
            if segment["trust_events"]:
                try:
                    segment["trust_events"] = await (
                        runtime.fact_store.apersist_speaker_trust_events(
                            lanlan_name,
                            segment["trust_events"],
                            expected_reconciliations=job[
                                "later_reconciled_by_key"
                            ],
                        )
                    )
                except Exception:
                    await runtime.fact_store.arollback_speaker_trust_reconciliations(
                        lanlan_name,
                        expected_reconciliations=job[
                            "own_reconciled_by_key"
                        ],
                        previous_facts=job["facts_by_key"],
                    )
                    segment["trust_events"] = await (
                        runtime.fact_store.apersist_speaker_trust_events(
                            lanlan_name,
                            segment["trust_events"],
                            expected_reconciliations=job[
                                "later_reconciled_by_key"
                            ],
                        )
                    )
    # ── the handler's LAST durable write: one pool mutation for the whole batch.
    #
    # Invariant P1: every owner signal that became durable on a fact row is
    # folded into the pool within this same request (or its retry), idempotent
    # by event id. The server deliberately does NOT reproduce the plugin's
    # "hold back a signal when an earlier segment failed" semantics and does NOT
    # withhold signals by segment status: ``adjustment`` is a commutative sum,
    # so settlement order cannot change the final value, and per-message
    # activity ids make an amplified retry harmless. Without P1 the durable
    # ledger would mix "should have folded but didn't" with "deliberately not
    # folded yet" and the two would be indistinguishable afterwards.
    #
    # Activity, by contrast, is collected only for segments the model actually
    # concluded on — matching the pre-migration "only apply on ok segments" rule.
    for segment, result in zip(parsed, segment_results):
        segment["trust_signal_events"] = tuple(
            segment.get("trust_events") or ()
        )
        if result.get("status") != "ok":
            segment["trust_activity_events"] = ()
    trust_result, trust_outcomes = await _apply_trust_for_segments(parsed)
    return {
        "status": "processed",
        "segments": [
            {
                "subject": segment["subject"].as_entry_fields(),
                "trust": _trust_response_block(
                    segment, trust_result, outcome,
                ),
                "status": result.get("status"),
                "created": len(result.get("created") or []),
                # Number of content-free garbage entries discarded in this segment. Nested output discards
                # lose no content (ownership comes from the segment object), so callers still decide advance /
                # retain by status. Report it to leave evidence of degrading model output in plugin logs,
                # rather than only within the memory-service process.
                "dropped": int(result.get("dropped") or 0),
                "fact_ids": [
                    fact.get("id")
                    for fact in (result.get("created") or [])
                    if fact.get("id")
                ],
                "fact_identities": [
                    list(_fact_identity(fact))
                    for fact in (
                        (result.get("created") or [])
                        + (result.get("reconciled") or [])
                    )
                    if (
                        isinstance(fact, dict)
                        and fact.get("id")
                        and all(_fact_identity(fact))
                    )
                ],
                "created_fact_identities": [
                    list(_fact_identity(fact))
                    for fact in (result.get("created") or [])
                    if (
                        isinstance(fact, dict)
                        and fact.get("id") is not None
                        and all(_fact_identity(fact))
                    )
                ],
                # Exact/semantic dedup can update an existing fact without
                # creating a row.  Return the affected identities as well so
                # retry cutoffs can exclude facts introduced by later
                # authored segments; the plugin needs IDs, not fact content.
                "reconciled": [
                    {"id": fact.get("id")}
                    for fact in (result.get("reconciled") or [])
                    if isinstance(fact, dict) and fact.get("id")
                ],
                # Legacy field. The pool now settles these server-side, so a
                # flipped caller ignores it; it stays for a not-yet-flipped
                # build and is removed together with the legacy
                # ``speaker_trust`` request field.
                "trust_events": (
                    list(segment.get("trust_events") or [])
                    if result.get("status") == "ok" else []
                ),
            }
            for segment, result, outcome in zip(
                parsed, segment_results, trust_outcomes,
            )
        ],
    }


# ── keyed /scoped_history: generate first, apply later ─────────────────────
#
# Keyed scoped_history (single subject and segments forms) bypasses
# the integrated extract-and-persist path above, instead:
#   1. Hold key lock (idempotency.key_lock) for entire request; done / cancelled key → duplicate;
#   2. Staged data exists → no LLM; otherwise generate via LLM, atomically stage only complete output and
#      mark key pending (incomplete always 502, never staged, or partial output would be pinned forever);
#   3. Apply items (language state / fact batch / display name), appending applied after each in staging;
#      check each clear tombstone before applying (compare generations only);
#   4. After all applied and trust pool (if any) persisted, mark done and delete staging.
# See docs/design/visit-infrastructure.md §4.6.

_KEYED_ITEM_LOCALE = "locale"
_KEYED_ITEM_FACTS = "facts"
_KEYED_ITEM_DISPLAY_NAME = "display_name"
# On clear, unreadable key file prevents recording cancellation: record in staging; retries repair cancelled on read.
_KEYED_STAGING_CANCELLED = "cancelled_by_forget"
# Same-key retry cap for failed display-name writes (display data cannot block the whole key indefinitely).
_DISPLAY_NAME_MAX_ATTEMPTS = 3
# Field exists but is null, etc.: distinguish from absent fields and let validation classify as corrupt state.
_MALFORMED = object()
_SEGMENT_DROPPED_BY_FORGET = "dropped_by_forget"


def _reject_owner_signal_on_keyed_request(req: ScopedHistoryRequest) -> None:
    """Keyed requests carry no owner trust signals (422 otherwise).

    Owner signals produce trust events whose replay safety rests on the
    request-scoped observation chain of the unkeyed path; journaling them
    would need per-event effect keys. The only keyed caller (visit digests)
    always sends ``speaker_tier="none"`` without owner bits, so the keyed
    path rejects them instead of half-supporting them.
    """
    if req.speaker_is_owner or any(
        segment.speaker_is_owner for segment in (req.segments or [])
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "idempotency_key does not support speaker_is_owner: owner "
                "trust signals are not journaled; send owner batches without "
                "an idempotency_key"
            ),
        )


def _keyed_null_trust_block() -> dict:
    """A trust block with every key ``_trust_response_block`` reports, persisted null."""
    return {**_trust_response_block({}, None, None), "persisted": None}


def _keyed_fact_identity(fact: dict) -> tuple:
    from memory.facts import _speaker_trust_fact_identity

    identity = _speaker_trust_fact_identity(fact)
    if identity is not None:
        return identity
    return (
        str(fact.get("id")), fact.get("subject_kind"),
        fact.get("subject_id"), fact.get("scope"),
    )


def _keyed_duplicate_response(shape: str, contexts: list[dict]) -> dict:
    if shape == "single":
        return {
            "status": "processed",
            "duplicate": True,
            "subject": contexts[0]["subject"].as_entry_fields(),
            "created": 0,
            "fact_ids": [],
            "trust": _keyed_null_trust_block(),
            "trust_events": [],
        }
    return {
        "status": "processed",
        "duplicate": True,
        "segments": [
            {
                "subject": context["subject"].as_entry_fields(),
                "trust": _keyed_null_trust_block(),
                "status": "ok",
                "created": 0,
                "dropped": 0,
                "fact_ids": [],
                "fact_identities": [],
                "created_fact_identities": [],
                "reconciled": [],
                "trust_events": [],
            }
            for context in contexts
        ],
    }


def _keyed_request_hash(req: ScopedHistoryRequest) -> str:
    """Canonical hash of what a keyed request asks to be extracted.

    Covers every ``input_history`` (by position), ``subject_epochs``,
    ``language`` and the trust inputs (``speaker_id``, tier / base trust,
    activity events, channel): the fields that decide which facts, locale
    state and trust mutations are produced and whether they may land.
    Display names / speaker labels are left out on purpose: they are
    cosmetic set-to-value data a caller may legitimately refresh between
    retries of the same batch.
    """
    sources = [req] if req.segments is None else list(req.segments)
    histories = [source.input_history for source in sources]

    def _trust(source) -> dict:
        events = getattr(source, "speaker_activity_events", None) or []
        return {
            "speaker_id": getattr(source, "speaker_id", None),
            "speaker_tier": getattr(source, "speaker_tier", None),
            "speaker_base_trust": getattr(source, "speaker_base_trust", None),
            "speaker_trust": getattr(source, "speaker_trust", None),
            "speaker_channel": getattr(source, "speaker_channel", None),
            "activity": [[event.id, event.count] for event in events],
        }

    payload = {
        "histories": histories,
        # Subject scope is part of identity (same kind:id under different scopes means isolated subjects):
        # Reusing one key with changed scope cannot count as the same request.
        # Store normalized scope: omitted scope and explicit default identify the same subject;
        # switching representations on retry must not make a different request.
        "scopes": [
            source.subject.to_domain().scope if getattr(source, "subject", None) is not None else None
            for source in sources
        ],
        "trust": [_trust(source) for source in sources],
        # Include generations only for this request's wire subjects: application/staging validate those alone;
        # extra/missing generations for other subjects change no effects and must not cause same-key retry 422.
        "subject_epochs": {
            wire_key: epoch for wire_key, epoch in (req.subject_epochs or {}).items()
            if wire_key in {
                source.subject.to_domain().key for source in sources
                if getattr(source, "subject", None) is not None
            }
        },
        # Language determines extraction context and staged language-state items, so also affects identity.
        "language": req.language,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _keyed_staging_items_valid(
    staging: dict, routed_keys: list | None = None, request_epochs: dict | None = None,
    language: str | None = None, routed_positions: object = None, *, structural_only: bool = False,
) -> bool:
    """Whether every journal item of a restored staging document is safe to apply.

    Every segment's persisted destination (``subject``) must be a valid
    subject that is either its own wire subject or one of ``routed_keys``
    (the routed subjects recorded on the key when the journal was created).

    Items must be objects numbered ``seq == position`` with a known kind and a
    segment index inside ``segments``; an item not yet in ``applied`` must
    still carry its payload (facts with one effect key each, or a display
    name). Items already applied / dropped may have been stripped by a forget.

    ``structural_only`` skips the checks bound to the retrying request (the
    destination against the recorded routes, the locale count / language):
    a forget uses it to tell whether a journal it keeps could ever replay.
    """
    from memory.scopes import MemoryScopeError, coerce_subject

    from . import idempotency

    segments = staging.get("segments")
    items = staging.get("items")
    applied = staging.get("applied")
    if not isinstance(segments, list) or not isinstance(items, list) or not isinstance(applied, list):
        return False
    allowed_routes = {str(k) for k in routed_keys} if routed_keys is not None else None
    if routed_positions is not None and not (
        isinstance(routed_positions, list) and len(routed_positions) == len(segments)
        and all(isinstance(k, str) for k in routed_positions)
    ):
        # Present but corrupt positional routing cannot fall back to set-only checks (replace a target then truncate to bypass);
        # use sets only when this field genuinely does not exist.
        return False
    for position, segment in enumerate(segments):
        # Write targets must be well formed and match this segment's wire subject or recorded initial routed subject:
        # tampering to another valid subject would write facts into an unrelated memory domain.
        if not isinstance(segment, dict):
            return False
        try:
            destination = coerce_subject(segment.get("subject"))
        except (MemoryScopeError, TypeError, ValueError):
            return False
        if destination is None:
            return False
        # Without a key record (orphan staging), no trusted route exists to compare: accept only this segment's wire subject.
        permitted = (allowed_routes if allowed_routes is not None else set()) | {segment.get("wire_key")}
        if routed_positions is not None:
            # Check positions: swapping two targets leaves both in the overall routing set but writes into each other's domains.
            permitted = {routed_positions[position], segment.get("wire_key")}
        if destination.key not in permitted and not structural_only:
            return False
        dropped = segment.get("dropped", 0)
        if not isinstance(dropped, int) or isinstance(dropped, bool) or dropped < 0:
            # Response assembly calls int() on this: corrupt values cause 500 before finalization on every same-key retry.
            return False
        if destination.scope != destination.key:
            # Keyed writes support default scope only: a changed target scope writes into a different isolated domain.
            return False
        if segment.get("tombstone_keys") != [segment.get("wire_key")]:
            # Compare tombstones only by this segment's wire key (generation contract); empty/changed keys bypass clearing.
            return False
    if request_epochs is not None:
        # Staged request generations must match this request (content hash covers them); raising one would
        # bypass a generation-bearing clear that happened in between.
        staged_epochs = staging.get("epochs")
        wire_keys = {segment.get("wire_key") for segment in segments if isinstance(segment, dict)}
        if not isinstance(staged_epochs, dict) or any(
            staged_epochs.get(wire_key) != request_epochs.get(wire_key) for wire_key in wire_keys
        ):
            return False
    if any(not isinstance(entry, dict) for entry in applied):
        return False
    seqs = [entry.get("seq") for entry in applied]
    # Applied sequence numbers must be distinct in-range true integers: {"seq": true} would count as 1, skipping an item.
    if any(not isinstance(seq, int) or isinstance(seq, bool) or not 0 <= seq < len(items) for seq in seqs):
        return False
    if len(set(seqs)) != len(seqs):
        return False
    for entry in applied:
        # Result fields (fact IDs and identity recorded during application) must also be well formed: corrupt values break response assembly
        # (500 on every retry) or return forged IDs.
        for name in ("fact_ids", "reconciled"):
            if name in entry and not (
                isinstance(entry[name], list) and all(
                    (isinstance(v, str) and v) or (isinstance(v, int) and not isinstance(v, bool))
                    for v in entry[name]
                )
            ):
                return False
        for name in ("created_fact_identities", "reconciled_fact_identities"):
            if name in entry and not (
                isinstance(entry[name], list) and all(
                    isinstance(identity, list) and len(identity) == 4
                    and all(isinstance(part, str) and part for part in identity)
                    for identity in entry[name]
                )
            ):
                return False
    done = set(seqs)
    key = staging.get("key")
    if not isinstance(key, str) or not key:
        return False
    ordinal = 0
    for position, item in enumerate(items):
        if not isinstance(item, dict):
            return False
        seq, segment, kind = item.get("seq"), item.get("segment"), item.get("kind")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq != position:
            return False
        if not isinstance(segment, int) or isinstance(segment, bool) or not 0 <= segment < len(segments):
            return False
        if kind not in (_KEYED_ITEM_LOCALE, _KEYED_ITEM_FACTS, _KEYED_ITEM_DISPLAY_NAME):
            return False
        if kind == _KEYED_ITEM_FACTS:
            # Effect keys derive from staging key + global sequence; verify individually, not shape alone: another row's
            # existing effect key would incorrectly skip this fact as replay.
            effect_keys = item.get("effect_keys")
            if not isinstance(effect_keys, list) or effect_keys != [
                idempotency.effect_key_for(key, ordinal + offset) for offset in range(len(effect_keys))
            ]:
                return False
            ordinal += len(effect_keys)
        if seq in done:
            continue
        if kind == _KEYED_ITEM_FACTS:
            facts = item.get("facts")
            if not isinstance(facts, list) or len(facts) != len(item["effect_keys"]):
                return False
            if not _restored_provenance_valid(item.get("speaker_provenance")):
                # Corrupt speaker provenance is silently ignored/stringified by persistence, losing ownership or attaching a fabricated
                # label while still marking applied.
                return False
            # Same post-generation requirement: each item is an object with nonempty body. Persistence silently skips corrupt
            # entries but still marks applied, permanently losing their effects.
            if not all(
                isinstance(fact, dict) and isinstance(fact.get("text"), str) and fact["text"].strip()
                for fact in facts
            ):
                return False
        elif kind == _KEYED_ITEM_DISPLAY_NAME:
            if item.get("display_name") is not None and not isinstance(item.get("display_name"), str):
                return False
        elif kind == _KEYED_ITEM_LOCALE:
            # Language items must also be well formed: invalid order is silently ignored as absent yet marked applied.
            order = item.get("order")
            # Accept supported language codes only: unsupported codes persist as None, clearing the subject's
            # existing valid language while still marking applied.
            if not is_supported_language_code(item.get("language")):
                return False
            if language is not None and not structural_only and item.get("language") != language:
                # Another supported language is also invalid: request hash covers language; staging records this request's language only.
                return False
            # Order is a positive causal timestamp reserved at turn start: zero/negative is silently ignored yet marked applied.
            if not isinstance(order, int) or isinstance(order, bool) or order <= 0:
                return False
    # Request hash covers language; reserve one supported-language item per segment at start. Clearing erases content, not items:
    # mismatched counts mean an item was deleted; finalizing would permanently omit that segment's language write.
    manifest = staging.get("manifest")
    if not (
        isinstance(manifest, dict) and set(manifest) == {"facts", "effects", "language", "facts_segments"}
        and all(
            isinstance(manifest[name], int) and not isinstance(manifest[name], bool)
            for name in ("facts", "effects")
        )
        and manifest["facts"] == sum(1 for item in items if item.get("kind") == _KEYED_ITEM_FACTS)
        and manifest["effects"] == ordinal
        and manifest["facts_segments"] == [
            item.get("segment") for item in items if item.get("kind") == _KEYED_ITEM_FACTS
        ]
        and (manifest["language"] is None or is_supported_language_code(manifest["language"]))
    ):
        # Fact-item/effect counts differ from generation: an entire fact item was lost; finalizing remaining items permanently omits it.
        return False
    # Validate language items against manifest language (independent of retries, usable before partial clear rewrites): each uncleared segment
    # must have exactly one matching-language item; full validation also matches manifest to this request's language.
    staged_language = manifest["language"]
    if not structural_only and staged_language != (language if is_supported_language_code(language) else None):
        return False
    if any(
        item.get("kind") == _KEYED_ITEM_LOCALE and item.get("language") != staged_language for item in items
    ):
        return False
    expected_locale = 1 if staged_language is not None else 0
    locale_per_segment = [0] * len(segments)
    for item in items:
        if item.get("kind") == _KEYED_ITEM_LOCALE:
            locale_per_segment[item["segment"]] += 1
    if any(
        count != expected_locale
        # Cleared/discarded segments may lack language items (retry reserves none, avoiding recreating cleared subjects in language storage).
        and not (count == 0 and isinstance(segments[index], dict)
                 and segments[index].get(_SEGMENT_DROPPED_BY_FORGET) is True)
        for index, count in enumerate(locale_per_segment)
    ):
        return False
    # Every applied item needs type-specific completion evidence (result or discard marker); a record with only {"seq": n}
    # would skip that item yet mark the key done.
    kind_of = {item.get("seq"): item.get("kind") for item in items if isinstance(item, dict)}
    for entry in applied:
        if any(entry.get(name) is True for name in _KEYED_DROP_MARKERS):
            continue
        kind = kind_of.get(entry.get("seq"))
        if kind == _KEYED_ITEM_FACTS:
            # Empty fact_ids is itself a legal result, insufficient to prove application: require a dedicated completion marker.
            evidence = entry.get("facts_applied") is True
        elif kind == _KEYED_ITEM_LOCALE:
            evidence = entry.get("locale_recorded") is True
        else:
            evidence = (
                entry.get("display_name_stamped") is True
                or entry.get("display_name_gave_up") is True
                or isinstance(entry.get("display_name_from_request"), bool)
            )
        if not evidence:
            return False
    return True


# Fact-item discard markers written by this module (matching write sites): only these names prove completion.
_KEYED_DROP_MARKERS = ("dropped_tombstone", "dropped_forget", "dropped_forget_during_generation")
_RESTORED_PROVENANCE_FIELDS = frozenset({"speaker_label", "speaker_trust", "speaker_id", "speaker_entity_id"})


def _restored_provenance_valid(provenance) -> bool:
    """A restored ``speaker_provenance`` has exactly the shape the route builds (or is absent)."""
    if provenance is None:
        return True
    if not isinstance(provenance, dict) or not provenance or set(provenance) - _RESTORED_PROVENANCE_FIELDS:
        return False
    # Both provenance construction paths include label (required per segment; entirely None for unlabeled single requests),
    # already trimmed and limited to 64 characters; incomplete ownership missing label cannot be marked applied.
    label = provenance.get("speaker_label")
    if not isinstance(label, str) or not label:
        return False
    from memory.facts import FactStore

    # Both request paths use the same label sanitizer. A value differing from its output (newlines / brackets / structural characters)
    # was tampered with; persistence only truncates, never sanitizes, so corrupt labels enter ownership unchanged.
    if FactStore.sanitize_speaker_label(label) != label:
        return False
    if "speaker_trust" in provenance:
        trust = provenance["speaker_trust"]
        if not isinstance(trust, (int, float)) or isinstance(trust, bool) or not 0.0 <= float(trust) <= 1.0:
            return False
    if "speaker_id" in provenance:
        from memory.speaker_trust import stable_speaker_id

        speaker_id = provenance["speaker_id"]
        if not isinstance(speaker_id, str) or stable_speaker_id(speaker_id) != speaker_id:
            return False
    elif "speaker_entity_id" in provenance:
        return False
    if "speaker_entity_id" in provenance:
        entity_id = provenance["speaker_entity_id"]
        if not isinstance(entity_id, str) or not entity_id.strip():
            return False
    return True


def _keyed_staging_matches(
    staging: dict, shape: str, contexts: list[dict], request_hash: str | None = None,
) -> bool:
    """Same key, same request: shape, per-position wire subjects and content agree."""
    segments = staging.get("segments")
    if staging.get("shape") != shape or not isinstance(segments, list):
        return False
    stored_hash = staging.get("request_hash")
    # Missing staged content hash is no wildcard: truncating an old journal's hash would let a different request claim it.
    if request_hash is not None and (not isinstance(stored_hash, str) or stored_hash != request_hash):
        return False
    if len(segments) != len(contexts):
        return False
    return all(
        isinstance(stored, dict)
        and stored.get("wire_key") == context["wire_subject"].key
        for stored, context in zip(segments, contexts)
    )


async def _generate_keyed_facts(
    lanlan_name: str,
    shape: str,
    contexts: list[dict],
    prompt_segments: list[dict] | None,
    *,
    skip: set[int] = frozenset(),
) -> tuple[list[list[dict]], list[int]]:
    """Run the extraction LLM once; return ``(facts per context, dropped per context)``.

    Fail-closed and stricter than the unkeyed path: ANY incomplete outcome
    (terminal LLM failure, malformed / unplaceable / suspect entries, a
    segment missing from the batch output) is a 502 and nothing is staged.
    The unkeyed path can persist the recognised part and let the caller
    retry the rest; here the staged result is final for the key, so a
    partial one would be pinned forever.
    """
    fact_store = runtime.fact_store
    failure = HTTPException(
        status_code=502,
        detail="scoped fact extraction failed; retry later",
    )
    if len(skip) >= len(contexts):
        # All segments cleared: send nothing (callers usually finalized cancellation earlier).
        return [[] for _ in contexts], [0] * len(contexts)
    if shape == "single":
        context = contexts[0]
        extracted = await fact_store._allm_extract_facts(
            lanlan_name,
            context["messages"],
            treat_malformed_as_failure=True,
            speaker_label=context.get("speaker_label"),
        )
        if extracted is None:
            raise failure
        if any(
            not (
                isinstance(fact, dict)
                and isinstance(fact.get("text"), str)
                and fact["text"].strip()
            )
            for fact in extracted
        ):
            raise failure
        return [list(extracted)], [0]

    # Send only uncleared segments; map results positionally to original indices, with empty outputs for cleared segments.
    live = [index for index in range(len(contexts)) if index not in skip]
    extracted = await fact_store._allm_extract_facts_batch(
        lanlan_name, [prompt_segments[index] for index in live],
    )
    if extracted is None:
        raise failure
    count = len(live)
    per_segment: list[list[dict] | None] = [None] * count
    dropped_live = [0] * count
    for item in extracted:
        index, facts, item_dropped, suspect = fact_store._parse_batch_segment_entry(
            item, count,
        )
        if index is None or suspect:
            raise failure
        if per_segment[index] is None:
            per_segment[index] = []
        per_segment[index].extend(facts)
        dropped_live[index] += item_dropped
    if not extracted:
        per_segment = [[] for _ in range(count)]
    if any(facts is None for facts in per_segment):
        raise failure
    facts_all: list[list[dict]] = [[] for _ in contexts]
    dropped = [0] * len(contexts)
    for position, index in enumerate(live):
        facts_all[index] = list(per_segment[position] or [])
        dropped[index] = dropped_live[position]
    return facts_all, dropped


async def _reserve_keyed_locale_orders(
    lanlan_name: str, language: str | None, contexts: list[dict], key: str | None = None,
) -> list[int | None]:
    """Reserve one causal locale order per subject BEFORE the LLM call.

    The order is stored in the staging document and reused by every apply
    attempt. Because it is reserved before generation, a forget that lands
    later always ends up with a cutoff >= this order, so the locale item is
    also rejected by the locale store's own forget cutoff, independently of
    tombstones; and a newer unkeyed write keeps priority over a retried
    older digest.

    With ``key``, the first reservation is kept on the pending key record
    and reused by every later attempt of that key (a generation that failed
    before staging must not move the old request forward in causal order).
    """
    from . import idempotency

    if not is_supported_language_code(language):
        return [None] * len(contexts)
    stored = None
    forgotten: set[str] = set()
    if key is not None:
        try:
            record = await idempotency.read_key(lanlan_name, key)
            forgotten = _forgotten_keys_of(record)
        except (idempotency.IdempotencyStateError, OSError) as exc:
            raise HTTPException(status_code=503, detail="idempotency state unreadable; retry later") from exc
        candidate = record.get("locale_orders") if isinstance(record, dict) else None
        if candidate is not None:
            if not (
                isinstance(candidate, list) and len(candidate) == len(contexts)
                and all(
                    # None is permitted only for segments already cleared at start; elsewhere it is corrupt and would silently omit language writes.
                    (order is None and bool({contexts[index]["wire_subject"].key, contexts[index]["subject"].key} & forgotten))
                    or (isinstance(order, int) and not isinstance(order, bool) and order > 0)
                    for index, order in enumerate(candidate)
                )
            ):
                # Existing reservation is corrupt: fresh numbers cannot be recorded without overwriting fields, and every retry would move
                # the old request later. Treat as corrupt state.
                raise idempotency.IdempotencyStateError(f"locale reservation of {key!r} is malformed")
            stored = candidate
    # Do not reserve segments cleared during earlier attempts (forgotten_keys): clearing removed their language rows;
    # reservation would recreate cleared subjects in scoped_prompt_locales.json despite doomed language items.
    live = [
        index for index, context in enumerate(contexts)
        if not ({context["wire_subject"].key, context["subject"].key} & forgotten)
    ]
    if stored is None:
        allocated = locale_state.allocate_subject_prompt_locale_orders(
            lanlan_name, [contexts[index]["subject"] for index in live],
        )
        admission: list[int | None] = [None] * len(contexts)
        for index, order in zip(live, allocated):
            admission[index] = order
    else:
        admission = list(stored)
    if key is not None and stored is None:
        # Record allocated numbers on the key before persisting reservations: failed generation/crash between steps makes same-key retries reuse
        # them, rather than obtain later numbers and overwrite newer language written by intervening requests.
        def _remember(old, orders=list(admission)):
            if old is None or old.get("state") != idempotency.KEY_STATE_PENDING or old.get("locale_orders"):
                return None
            return {**old, "locale_orders": orders}

        try:
            await idempotency.update_key(lanlan_name, key, _remember)
        except (idempotency.IdempotencyStateError, OSError) as exc:
            raise HTTPException(status_code=503, detail="idempotency state unreadable; retry later") from exc
    if key is not None:
        # A clear may have added a segment to forgotten_keys during writes above; recheck before persisting reservations.
        try:
            forgotten |= _forgotten_keys_of(await idempotency.read_key(lanlan_name, key))
        except (idempotency.IdempotencyStateError, OSError) as exc:
            raise HTTPException(status_code=503, detail="idempotency state unreadable; retry later") from exc
        live = [
            index for index in live
            if not ({contexts[index]["wire_subject"].key, contexts[index]["subject"].key} & forgotten)
        ]
    reserve = [index for index in live if admission[index] is not None]
    if reserve:
        await asyncio.to_thread(
            locale_state.reserve_subject_prompt_locale_orders,
            lanlan_name,
            [contexts[index]["subject"] for index in reserve],
            orders=[admission[index] for index in reserve],
        )
    return [admission[index] if index in reserve else None for index in range(len(contexts))]


async def _build_keyed_staging(
    lanlan_name: str,
    req: ScopedHistoryRequest,
    *,
    shape: str,
    contexts: list[dict],
    prompt_segments: list[dict] | None,
) -> dict:
    from . import idempotency

    key = req.idempotency_key
    orders = await _reserve_keyed_locale_orders(lanlan_name, req.language, contexts, key)
    # Do not extract segments cleared during earlier attempts (forgotten_keys): output will be discarded,
    # and sending their retained original text to extraction models would disclose it again.
    forgotten = _forgotten_keys_of(await idempotency.read_key(lanlan_name, key))
    skip = {
        index for index, context in enumerate(contexts)
        if {context["wire_subject"].key, context["subject"].key} & forgotten
    }
    facts_per_context, dropped = await _generate_keyed_facts(
        lanlan_name, shape, contexts, prompt_segments, skip=skip,
    )
    segments = []
    items: list[dict] = []
    subject_keys: set[str] = set()
    effect_ordinal = 0
    for index, context in enumerate(contexts):
        wire_key = context["wire_subject"].key
        # Compare tombstones only with the request's own wire key: request generation belongs to its domain;
        # adding routed-key tombstones would compare two unrelated counters.
        tombstone_keys = [wire_key]
        subject_keys.update(tombstone_keys)
        segments.append({
            "wire_key": wire_key,
            "subject": context["subject"].as_entry_fields(),
            "tombstone_keys": tombstone_keys,
            "dropped": int(dropped[index]),
        })
        if orders[index] is not None:
            items.append({
                "seq": len(items),
                "kind": _KEYED_ITEM_LOCALE,
                "segment": index,
                "language": req.language,
                "order": orders[index],
            })
        facts = facts_per_context[index]
        if facts:
            effect_keys = []
            for _fact in facts:
                effect_keys.append(idempotency.effect_key_for(key, effect_ordinal))
                effect_ordinal += 1
            items.append({
                "seq": len(items),
                "kind": _KEYED_ITEM_FACTS,
                "segment": index,
                "facts": facts,
                "effect_keys": effect_keys,
                "speaker_provenance": context.get("speaker_provenance"),
            })
        if context.get("display_name"):
            items.append({
                "seq": len(items),
                "kind": _KEYED_ITEM_DISPLAY_NAME,
                "segment": index,
                "display_name": context["display_name"],
            })
    request_epochs = req.subject_epochs or {}
    return {
        "key": key,
        "state": idempotency.STAGING_STATE_GENERATED,
        "shape": shape,
        "subjects": sorted(subject_keys),
        "epochs": {
            subject_key: int(request_epochs[subject_key])
            for subject_key in sorted(subject_keys)
            if subject_key in request_epochs
        },
        # Diagnostics only; never participates in comparisons.
        "client_requested_at": req.client_requested_at,
        "created_at": time.time(),
        "request_hash": _keyed_request_hash(req),
        "segments": segments,
        "items": items,
        "applied": [],
        # Generated fact-item/effect counts detect wholly missing items during recovery (per-item checks cannot detect absence).
        # Language items are checked one per segment; missing display-name items are restored from current retry request and excluded.
        "manifest": {
            "facts": sum(1 for item in items if item["kind"] == _KEYED_ITEM_FACTS),
            "effects": effect_ordinal,
            "language": req.language if is_supported_language_code(req.language) else None,
            # Original segment of each generated fact item: tampering segment index (with valid sequence/effect key/targets) would
            # write this segment's facts into another subject's memory domain.
            "facts_segments": [item["segment"] for item in items if item["kind"] == _KEYED_ITEM_FACTS],
        },
    }


async def _apply_keyed_item(lanlan_name: str, item: dict, segment: dict, generation) -> dict:
    from memory.scopes import coerce_subject

    subject = coerce_subject(segment["subject"])
    kind = item.get("kind")
    entry: dict = {"seq": item["seq"]}
    if kind == _KEYED_ITEM_FACTS:
        reconciled: list[dict] = []
        created = await runtime.fact_store._apersist_new_facts(
            lanlan_name,
            list(item.get("facts") or []),
            subject=subject,
            speaker_provenance=item.get("speaker_provenance"),
            expected_subject_generation=generation,
            reconciled_facts=reconciled,
            effect_keys=list(item.get("effect_keys") or []),
        )
        # Store strings consistently with _keyed_fact_identity: FactStore retains legacy integer IDs.
        entry["fact_ids"] = [str(fact.get("id")) for fact in created if fact.get("id")]
        entry["facts_applied"] = True
        entry["created_fact_identities"] = [
            list(_keyed_fact_identity(fact))
            for fact in created
            if isinstance(fact, dict)
            and fact.get("id") is not None
            and all(_keyed_fact_identity(fact))
        ]
        entry["reconciled"] = [
            str(fact.get("id")) for fact in reconciled
            if isinstance(fact, dict) and fact.get("id")
        ]
        entry["reconciled_fact_identities"] = [
            list(_keyed_fact_identity(fact))
            for fact in reconciled
            if isinstance(fact, dict)
            and fact.get("id")
            and all(_keyed_fact_identity(fact))
        ]
    elif kind == _KEYED_ITEM_LOCALE:
        await asyncio.to_thread(
            locale_state.record_subject_prompt_locale,
            lanlan_name,
            subject,
            item.get("language"),
            order=item.get("order"),
        )
        entry["locale_recorded"] = True
    elif kind == _KEYED_ITEM_DISPLAY_NAME:
        # Setting a value is inherently idempotent; set names on existing sections only.
        await _stamp_subject_display_name(
            lanlan_name, subject, item.get("display_name"), strict=True,
        )
        entry["display_name_stamped"] = True
    else:  # pragma: no cover - staging written by this module only
        raise RuntimeError(f"unknown keyed item kind {kind!r}")
    return entry


def _restore_missing_display_items(staging: dict, display_names: dict[int, str | None]) -> None:
    """Re-add the display-name item of a segment whose item is missing from a restored journal.

    The display name is not part of the request identity and a restored retry
    applies the current request's value anyway, so a journal that lost a
    trailing display item is completed from the retry instead of finalizing
    the key without it. Segments already dropped by a forget are left alone.
    """
    items = staging["items"]
    dropped_seqs = {
        entry.get("seq") for entry in staging.get("applied") or []
        if isinstance(entry, dict) and any(entry.get(name) is True for name in _KEYED_DROP_MARKERS)
    }
    dropped_segments = {item.get("segment") for item in items if item.get("seq") in dropped_seqs}
    dropped_segments |= {
        index for index, segment in enumerate(staging.get("segments") or [])
        if isinstance(segment, dict) and segment.get(_SEGMENT_DROPPED_BY_FORGET) is True
    }
    present = {item.get("segment") for item in items if item.get("kind") == _KEYED_ITEM_DISPLAY_NAME}
    for index, name in sorted(display_names.items()):
        if name and index not in present and index not in dropped_segments:
            items.append({
                "seq": len(items),
                "kind": _KEYED_ITEM_DISPLAY_NAME,
                "segment": index,
                "display_name": name,
            })


def _capture_forget_generations(lanlan_name: str, subjects: list) -> dict[int, int]:
    """Segment index -> current forget generation of that segment's (routed) subject."""
    return {
        index: runtime.fact_store._subject_forget_generation(lanlan_name, subject)
        for index, subject in enumerate(subjects)
    }


async def _persist_fence_marks(lanlan_name: str, key: str, staging: dict, fenced: set[str]) -> None:
    """Persist the segment drop marks a recheck just added, or answer 503 with a durable fallback.

    ``fenced`` are the journal subjects of the segments this recheck fenced
    (each one's own subject moved or is being forgotten; never the routed
    destination of an older dropped segment, which a valid segment may share). When the journal write fails, the marks would
    exist only in memory and a same-key retry restored from the unmarked
    journal (its baseline captured after the forget) would write those
    segments; ``fenced`` is then merged into the key record's
    ``forgotten_keys``, which the restored path replays before applying. A
    cancellation landing during the journal write records the same fallback
    (the write's outcome is unknown) before propagating. If the fallback
    write fails too there is no durable copy of these marks.
    """
    from . import idempotency

    try:
        await idempotency.write_staging(lanlan_name, key, staging)
        return
    except MaintenanceModeError:
        raise
    except asyncio.CancelledError:
        # Threaded write finished but success is unknown here: still record fallback, then re-raise cancellation.
        fallback = await _record_fence_fallback(lanlan_name, key, fenced)
        logger.warning(
            f"[scoped_history] {lanlan_name}: 清除丢弃标记的暂存写入时请求被取消（键记录兜底：{fallback}）"
        )
        raise
    except Exception as exc:
        journal_error = exc
    # Log original persistence error first: cancellation during fallback could make the later log unreachable.
    logger.error(f"[scoped_history] {lanlan_name}: 清除丢弃标记的暂存落盘失败，改记键记录兜底: {journal_error}")
    fallback = await _record_fence_fallback(lanlan_name, key, fenced)
    logger.error(f"[scoped_history] {lanlan_name}: 清除丢弃标记的键记录兜底：{fallback}")
    raise HTTPException(
        status_code=503,
        detail="scoped history staging failed; retry with the same key",
    ) from journal_error


async def _record_fence_fallback(lanlan_name: str, key: str, fenced: set[str]) -> str:
    """Merge ``fenced`` into the pending key record's ``forgotten_keys``; return what happened.

    Runs to completion even when the calling task is cancelled meanwhile
    (the caller holds the key lock and must not release it before this
    record is written); the cancellation is re-raised afterwards.
    """
    from . import idempotency

    if not fenced:
        return "skipped"

    def _remember(old):
        if old is None or old.get("state") != idempotency.KEY_STATE_PENDING:
            return None
        prior = _forgotten_keys_of(old)
        if fenced <= prior:
            return None
        return {**old, "forgotten_keys": sorted(prior | fenced)}

    task = asyncio.ensure_future(idempotency.update_key(lanlan_name, key, _remember))
    cancellation = None
    while True:
        try:
            stored = await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                # Caller cancelled: wait for fallback write, then re-raise cancellation anyway.
                cancellation = cancellation or exc
            if task.cancelled():
                # Only fallback task cancelled (shutdown sweep): not written; caller logs and returns 503 normally.
                outcome = "cancelled before it was written"
                break
            # Caller cancelled while fallback still running / just completed or failed: inspect its real result next iteration.
            continue
        except MaintenanceModeError:
            if cancellation is not None:
                raise cancellation
            raise
        except Exception as exc:  # noqa: BLE001 - Neither location writable: caller still returns 503.
            outcome = f"failed too: {exc}"
            break
        outcome = (
            "recorded in forgotten_keys"
            if isinstance(stored, dict) and fenced <= set(stored.get("forgotten_keys") or [])
            else "not recorded (key not pending)"
        )
        break
    if cancellation is not None:
        logger.warning(f"[scoped_history] {lanlan_name}: 请求被取消，清除丢弃标记的键记录兜底：{outcome}")
        raise cancellation
    return outcome


def _segment_fence_keys(segment: object) -> set[str]:
    """Keys a fallback fence records for one segment: its journal subject and its stable wire key.

    The journal subject covers a restored retry of this journal. The wire key
    is the segment's stable identity: if the journal is later lost and the
    canonical routing changes, a regenerating retry resolves a new destination
    but still matches ``forgotten_keys`` through it.
    """
    return _segment_subject_keys(segment) - {"None", ""}


def _fenced_journal_keys(staging: dict, indexes: set[int]) -> set[str]:
    """Fallback fence keys of the segments a recheck fenced (see :func:`_segment_fence_keys`)."""
    segments = staging.get("segments") if isinstance(staging, dict) else None
    if not isinstance(segments, list):
        return set()
    return set().union(*(
        _segment_fence_keys(segments[index]) for index in indexes if 0 <= index < len(segments)
    ))


def _staged_segment_subjects(staging: dict, contexts: list[dict]) -> list:
    """The subject each staged segment is applied to (the journal's, not today's routing).

    A restored retry applies every segment to the subject recorded in the
    staging document, which may differ from the current routing; the
    forget-generation baseline and its recheck must watch that same subject.
    """
    from memory.scopes import coerce_subject

    segments = staging.get("segments") if isinstance(staging, dict) else None
    subjects = []
    for index, context in enumerate(contexts):
        segment = segments[index] if isinstance(segments, list) and index < len(segments) else None
        recorded = coerce_subject(segment.get("subject")) if isinstance(segment, dict) and segment.get("subject") else None
        subjects.append(recorded if recorded is not None else context["subject"])
    return subjects


async def _apply_keyed_staging(
    lanlan_name: str,
    key: str,
    staging: dict,
    generations: dict[int, int] | None = None,
    display_names: dict[int, str | None] | None = None,
    fact_generations: dict[int, int] | None = None,
) -> None:
    """Apply every item not yet in ``applied``; journal each one atomically.

    Tombstone rule, per item and by forget GENERATION only (never time, never
    arrival order): when any of the item's subject keys carries a tombstone
    whose ``forget_epoch`` exceeds the epoch the request was started with,
    the item is dropped and journaled as ``dropped_tombstone``.

    For fact items the fact store's forget generation is captured BEFORE the
    tombstone read and passed as ``expected_subject_generation``: a forget
    that starts after the tombstone check but before persistence bumps that
    generation and the fact store discards the write by itself.

    ``generations`` (segment index -> generation) is given only on the
    request that generated the staging: the values were captured BEFORE the
    LLM call, so a forget landing while the model ran (a window with no
    staging file to cancel and, without ``forget_epoch``, no tombstone)
    still invalidates the write, as on the unkeyed path. A retry restored
    from an existing staging file passes ``fact_generations`` instead,
    captured under the key lock just before applying: the pre-erase
    cancellation scan is best effort and may skip that staging, so a forget
    running during the restored apply must still invalidate its fact writes
    (and the trust step compares against the same baseline).
    ``generations is None`` keeps meaning "restored retry" for the display
    name handling.
    """
    from memory.scopes import coerce_subject

    from . import idempotency

    if generations is None and display_names:
        _restore_missing_display_items(staging, display_names)
    applied = list(staging.get("applied") or [])
    done_seqs = {
        entry.get("seq") for entry in applied if isinstance(entry, dict)
    }
    journaled_before = {
        entry.get("seq") for entry in applied
        if isinstance(entry, dict) and not any(entry.get(name) is True for name in _KEYED_DROP_MARKERS)
    }
    epochs = staging.get("epochs") or {}
    segments = staging.get("segments") or []
    for item in staging.get("items") or []:
        seq = item.get("seq")
        if seq in done_seqs:
            continue
        segment = segments[item["segment"]]
        generation = None
        fenced_now: set[str] = set()
        if item.get("kind") == _KEYED_ITEM_FACTS:
            baseline = generations if generations is not None else fact_generations
            if baseline is not None and item["segment"] in baseline:
                generation = baseline[item["segment"]]
            else:
                generation = runtime.fact_store._subject_forget_generation(
                    lanlan_name, coerce_subject(segment["subject"]),
                )
        tombstones = await idempotency.read_tombstones(lanlan_name)
        tombstone_epoch = idempotency.tombstone_epoch(
            tombstones, segment.get("tombstone_keys") or [],
        )
        request_epoch = epochs.get(segment.get("wire_key"), 0)
        if isinstance(segment, dict) and segment.get(_SEGMENT_DROPPED_BY_FORGET) is True:
            # Segment already marked cleared/discarded (e.g. generation changed while applying facts): do not write later items
            # of that segment (display names follow facts), or recreate names just erased by clear.
            entry = {"seq": seq, "dropped_forget": True}
        elif tombstone_epoch is not None and int(request_epoch) < tombstone_epoch:
            entry = {"seq": seq, "dropped_tombstone": True}
        elif item.get("kind") == _KEYED_ITEM_DISPLAY_NAME:
            if generations is None:
                # Recovery retry uses current request display names, not stale staged names:
                # newer writes may have changed them; restoring old values regresses, but omitting leaves this batch nameless forever.
                current = (display_names or {}).get(item["segment"])
                to_apply = {**item, "display_name": current} if current else None
                done_entry = {"seq": seq, "display_name_from_request": bool(current)}
            else:
                to_apply, done_entry = item, None
            try:
                applied_entry = (
                    await _apply_keyed_item(lanlan_name, to_apply, segment, None)
                    if to_apply is not None else None
                )
                entry = done_entry or applied_entry
            except Exception:
                # Display names are cosmetic: persistent failures (read-only directory/full disk) cannot block the key forever,
                # also starving trust-pool writes. After several failed retries, log and finalize as handled.
                attempts = int(item.get("display_attempts") or 0) + 1
                if attempts < _DISPLAY_NAME_MAX_ATTEMPTS:
                    item["display_attempts"] = attempts
                    await idempotency.write_staging(lanlan_name, key, staging)
                    raise
                logger.warning(f"[scoped_history] {lanlan_name}: 显示名写入连续失败，放弃这一项")
                entry = {"seq": seq, "display_name_gave_up": True}
        else:
            entry = await _apply_keyed_item(lanlan_name, item, segment, generation)
            if item.get("kind") == _KEYED_ITEM_FACTS and generation is not None and (
                runtime.fact_store._subject_forget_generation(lanlan_name, coerce_subject(segment["subject"]))
                != generation
            ):
                # Generation-free clear advanced generation around application: fact layer silently discarded these writes. Mark this
                # segment discarded so trust isolation and memory discard use identical evidence.
                _mark_segments_dropped(segments, {item["segment"]})
                fenced_now = _segment_fence_keys(segments[item["segment"]])
        applied.append(entry)
        done_seqs.add(seq)
        staging["applied"] = applied
        if fenced_now:
            # Persist new segment marks through _persist_fence_marks like the rechecks, falling back to key records on failure;
            # otherwise same-key retries baselined after clear could restore this segment's pre-clear facts.
            await _persist_fence_marks(lanlan_name, key, staging, fenced_now)
        else:
            await idempotency.write_staging(lanlan_name, key, staging)
    if generations is None and display_names:
        # Recovery retries reapply even previously written display-name items with current request values (idempotent set):
        # names are outside request identity; changes while pending must not finalize stale names. Do not rename cleared segments.
        fenced = await _fenced_segments(lanlan_name, staging)
        for item in staging.get("items") or []:
            if (
                item.get("kind") == _KEYED_ITEM_DISPLAY_NAME and item.get("seq") in journaled_before
                and item.get("segment") not in fenced
            ):
                current = display_names.get(item["segment"])
                if not current:
                    continue
                try:
                    await _apply_keyed_item(
                        lanlan_name, {**item, "display_name": current}, segments[item["segment"]], None,
                    )
                except MaintenanceModeError:
                    raise
                except Exception as exc:
                    # Best-effort refresh only: persistently unwritable persona must not prevent done forever.
                    # Retry even previously abandoned display-name items: once writable, finalization retries can restore names.
                    logger.warning(f"[scoped_history] {lanlan_name}: 恢复重试刷新显示名失败，跳过: {exc}")


def _keyed_response(
    shape: str,
    contexts: list[dict],
    staging: dict,
    trust_result,
    trust_outcomes,
) -> dict:
    segment_of_seq = {
        item.get("seq"): item.get("segment") for item in staging.get("items") or []
    }
    per_segment: list[dict] = [
        {
            "fact_ids": [],
            "created_fact_identities": [],
            "reconciled": [],
            "reconciled_fact_identities": [],
        }
        for _ in contexts
    ]
    for entry in staging.get("applied") or []:
        index = segment_of_seq.get(entry.get("seq"))
        if index is None or not (0 <= index < len(per_segment)):
            continue
        for field in (
            "fact_ids", "created_fact_identities", "reconciled",
            "reconciled_fact_identities",
        ):
            per_segment[index][field].extend(entry.get(field) or [])
    stored_segments = staging.get("segments") or []
    if shape == "single":
        context = contexts[0]
        return {
            "status": "processed",
            "subject": context["subject"].as_entry_fields(),
            "created": len(per_segment[0]["fact_ids"]),
            "fact_ids": per_segment[0]["fact_ids"],
            "trust": _trust_response_block(
                context["trust_state"], trust_result, trust_outcomes[0],
            ),
            "trust_events": [],
        }
    return {
        "status": "processed",
        "segments": [
            {
                "subject": context["subject"].as_entry_fields(),
                "trust": _trust_response_block(
                    context["trust_state"], trust_result, outcome,
                ),
                "status": "ok",
                "created": len(collected["fact_ids"]),
                "dropped": int(
                    (stored_segments[index] if index < len(stored_segments) else {})
                    .get("dropped") or 0
                ),
                "fact_ids": collected["fact_ids"],
                "fact_identities": (
                    list(collected["created_fact_identities"])
                    + list(collected["reconciled_fact_identities"])
                ),
                "created_fact_identities": list(collected["created_fact_identities"]),
                "reconciled": [{"id": fid} for fid in collected["reconciled"]],
                "trust_events": [],
            }
            for index, (context, collected, outcome) in enumerate(
                zip(contexts, per_segment, trust_outcomes)
            )
        ],
    }


async def _process_scoped_history_keyed(
    lanlan_name: str,
    req: ScopedHistoryRequest,
    *,
    shape: str,
    contexts: list[dict],
    prompt_segments: list[dict] | None = None,
) -> dict:
    """The keyed (journaled) form of /scoped_history, both shapes."""
    from . import idempotency

    key = req.idempotency_key
    missing_epochs = [
        context["wire_subject"].key for context in contexts
        if not req.subject_epochs or context["wire_subject"].key not in req.subject_epochs
    ]
    if missing_epochs:
        # Missing generation cannot mean zero: after one generation-bearing clear, tombstones would silently discard
        # all later writes while marking keys done. Keyed requests must provide every wire subject's generation.
        raise HTTPException(
            status_code=422,
            detail="idempotency_key requires subject_epochs for every subject",
        )
    if any(context["wire_subject"].scope != context["wire_subject"].key for context in contexts):
        # Tombstones/cancellation matching/cleared generations use MemorySubject.key (kind:id), without scope: only default
        # scope makes a key identify one domain uniquely. Sole caller (visit digest) uses default scope only.
        raise HTTPException(
            status_code=422,
            detail="idempotency_key requires the default subject scope",
        )
    # Request identity (form + positional wire subjects) remains permanently with key records; terminal key reuse by a different
    # request cannot silently count as already handled.
    fingerprint = {
        "shape": shape,
        "wire_keys": [context["wire_subject"].key for context in contexts],
        "content_hash": _keyed_request_hash(req),
    }
    # Actual routed write subjects are separately stored on pending records (not request identity: routes may change between
    # retries). Before staging exists, this alone lets clear identify keys routing writes to the cleared subject.
    routed_keys = sorted({context["subject"].key for context in contexts})
    # Store routed subject per position; recovery checks targets positionally because sets cannot detect swapped targets.
    routed_positions = [context["subject"].key for context in contexts]
    # Store request clear generations on pending records too: before staging exists, clear identifies valid requests started
    # after clearing and leaves them uncancelled (same accounting as _staged_after_forget for staged data).
    request_epochs = {
        wire_key: int(req.subject_epochs[wire_key])
        for wire_key in sorted({context["wire_subject"].key for context in contexts})
        if req.subject_epochs and wire_key in req.subject_epochs
    }
    async with idempotency.key_lock(lanlan_name, key):
        try:
            record = await idempotency.read_key(lanlan_name, key)
            # In every state, validate recorded request identity first: pending keys with lost staging cannot let
            # another request regenerate under this key and overwrite its original identity.
            stored = record.get("request") if record is not None else None
            if stored is not None and stored != fingerprint:
                raise HTTPException(
                    status_code=422,
                    detail="idempotency_key was already used for a different request",
                )
            if (
                record is not None
                and record.get("state") in idempotency.TERMINAL_KEY_STATES
            ):
                if stored is None:
                    # Terminal record lacks request identity: equality cannot be checked, so arbitrary new requests reusing the key
                    # cannot be swallowed as duplicate.
                    raise idempotency.IdempotencyStateError("terminal key record has no request identity")
                # Useless staging left after terminal finalization: delete now under key lock, without waiting for next startup cleanup
                # (startup skips busy key locks).
                await _drop_leftover_staging(lanlan_name, key)
                return _keyed_duplicate_response(shape, contexts)
            staging = await idempotency.read_staging(lanlan_name, key)
        except idempotency.IdempotencyStateError as exc:
            logger.error(f"[scoped_history] {lanlan_name}: 幂等记录不可读: {exc}")
            raise HTTPException(
                status_code=503,
                detail="idempotency state unreadable; retry later",
            ) from exc
        if staging is not None and staging.get(idempotency.UNREADABLE_CANCELLED_MARKER) is True:
            # Staging unreadable/unclaimed at clear time had original text erased: finalize cancelled (repair cancelled record), never
            # treat as absent staging and regenerate, which would restore the cleared subject.
            try:
                await idempotency.update_key(
                    lanlan_name, key,
                    idempotency.transition(idempotency.KEY_STATE_CANCELLED, request=fingerprint),
                )
                await idempotency.delete_staging(lanlan_name, key)
            except MaintenanceModeError:
                raise
            except Exception as exc:
                logger.error(f"[scoped_history] {lanlan_name}: 补记 cancelled 失败: {exc}")
                raise HTTPException(
                    status_code=503,
                    detail="idempotency state unreadable; retry later",
                ) from exc
            return _keyed_duplicate_response(shape, contexts)
        if staging is not None and not _keyed_staging_matches(
            staging, shape, contexts, fingerprint["content_hash"],
        ):
            raise HTTPException(
                status_code=422,
                detail="idempotency_key was already used for a different request",
            )
        if (
            staging is not None
            and staging.get(_KEYED_STAGING_CANCELLED) is True
            and not _is_cancelled_staging_marker(staging)
        ):
            # Cancellation flag without clear's fully erased shape: one boolean cannot cancel the whole request
            # and delete output; treat as corrupt staging.
            logger.error(f"[scoped_history] {lanlan_name}: 暂存取消标记形状不符，拒绝处理")
            raise HTTPException(
                status_code=503,
                detail="idempotency state unreadable; retry later",
            )
        routed_on_record = record.get("routed_keys") if isinstance(record, dict) else None
        positions_on_record = record.get("routed_positions") if isinstance(record, dict) else None
        if isinstance(record, dict) and (
            ("routed_positions" in record and positions_on_record is None)
            # These fields are always written together: set without positional routes is corrupt, never set-only fallback.
            or ("routed_keys" in record and "routed_positions" not in record)
        ):
            positions_on_record = _MALFORMED
        if record is None:
            # Orphan staging (lost record/reset key file): no trusted record; validate each write target against current request routing
            # (allow only matches), rather than accepting wire subjects alone and permanently 503'ing routed segments.
            positions_on_record = routed_positions
        if (
            staging is not None
            # Cancellation markers retain only identity (text erased); branch below records cancelled, without per-item validation.
            and staging.get(_KEYED_STAGING_CANCELLED) is not True
            and not _keyed_staging_items_valid(
                staging, routed_on_record if isinstance(routed_on_record, list) else None,
                dict(req.subject_epochs or {}), req.language,
                positions_on_record,
            )
        ):
            # Corrupt staged items (out-of-range/negative segment indices, bad sequences, mismatched effect keys) must never apply;
            # negative indices would write facts to another subject.
            logger.error(f"[scoped_history] {lanlan_name}: 暂存条目结构损坏，拒绝应用")
            raise HTTPException(
                status_code=503,
                detail="idempotency state unreadable; retry later",
            )
        if staging is not None and staging.get(_KEYED_STAGING_CANCELLED) is True:
            # Clear could not read key file and recorded cancellation in staging only: repair cancelled, delete staging, never apply.
            try:
                await idempotency.update_key(
                    lanlan_name,
                    key,
                    idempotency.transition(
                        idempotency.KEY_STATE_CANCELLED, request=fingerprint,
                    ),
                )
                await idempotency.delete_staging(lanlan_name, key)
            except MaintenanceModeError:
                raise
            except Exception as exc:
                logger.error(f"[scoped_history] {lanlan_name}: 补记 cancelled 失败: {exc}")
                raise HTTPException(
                    status_code=503,
                    detail="idempotency state unreadable; retry later",
                ) from exc
            return _keyed_duplicate_response(shape, contexts)
        if staging is None and record is not None and stored is None:
            # Pending record without request identity or staging offers no proof this retry matches the original
            # (some effects may have applied before staging loss, causing sequence-derived effect-key collisions).
            # Cannot adopt it as a new request.
            logger.error(f"[scoped_history] {lanlan_name}: pending 记录缺请求身份且无暂存，拒绝接手")
            raise HTTPException(
                status_code=503,
                detail="idempotency state unreadable; retry later",
            )
        if staging is not None and stored is None:
            # Orphan staging from failure between staged write and pending key record: first repair a pending record with
            # request identity, then apply; otherwise eventual done lacks identity to validate.
            try:
                await idempotency.update_key(
                    lanlan_name,
                    key,
                    idempotency.transition(
                        idempotency.KEY_STATE_PENDING,
                        client_requested_at=req.client_requested_at,
                        request=fingerprint,
                        routed_keys=routed_keys,
                        routed_positions=routed_positions,
                        epochs=request_epochs,
                    ),
                )
            except MaintenanceModeError:
                raise
            except Exception as exc:
                logger.error(f"[scoped_history] {lanlan_name}: 补记 pending 失败: {exc}")
                raise HTTPException(
                    status_code=503,
                    detail="scoped history staging failed; retry with the same key",
                ) from exc
        generations = None
        if staging is None:
            # Before LLM call, persist pending with request identity and routed subjects: even failed generation leaves
            # a durable record that later clear can cancel; otherwise same-key retry after clear re-extracts using post-clear
            # generation and restores old content.
            try:
                await idempotency.update_key(
                    lanlan_name,
                    key,
                    idempotency.transition(
                        idempotency.KEY_STATE_PENDING,
                        client_requested_at=req.client_requested_at,
                        request=fingerprint,
                        routed_keys=routed_keys,
                        routed_positions=routed_positions,
                        epochs=request_epochs,
                    ),
                )
            except MaintenanceModeError:
                raise
            except Exception as exc:
                logger.error(f"[scoped_history] {lanlan_name}: 预记 pending 失败: {exc}")
                raise HTTPException(
                    status_code=503,
                    detail="scoped history staging failed; retry with the same key",
                ) from exc
            # Clear during an earlier attempt already put all segment subjects in forgotten_keys: all outputs will be
            # discarded; skip full extraction under key lock and finalize cancellation directly.
            try:
                prior = await idempotency.read_key(lanlan_name, key)
            except idempotency.IdempotencyStateError as exc:
                raise HTTPException(
                    status_code=503, detail="idempotency state unreadable; retry later",
                ) from exc
            try:
                prior_forgotten = _forgotten_keys_of(prior)
            except idempotency.IdempotencyStateError as exc:
                raise HTTPException(
                    status_code=503, detail="idempotency state unreadable; retry later",
                ) from exc
            if prior_forgotten and all(
                {context["wire_subject"].key, context["subject"].key} & prior_forgotten
                for context in contexts
            ):
                try:
                    await idempotency.update_key(
                        lanlan_name, key, idempotency.transition(idempotency.KEY_STATE_CANCELLED),
                    )
                except MaintenanceModeError:
                    raise
                except Exception as exc:
                    raise HTTPException(
                        status_code=503, detail="idempotency state unreadable; retry later",
                    ) from exc
                return _keyed_duplicate_response(shape, contexts)
            # Same timing as unkeyed extract_facts: snapshot each subject's forget generation before LLM call,
            # retaining in memory only. Clear during generation (no staging to cancel yet,
            # and no tombstone without forget_epoch) advances generation, allowing fact storage
            # to discard this write during first application.
            generations = _capture_forget_generations(lanlan_name, [context["subject"] for context in contexts])
            try:
                staging = await _build_keyed_staging(
                    lanlan_name,
                    req,
                    shape=shape,
                    contexts=contexts,
                    prompt_segments=prompt_segments,
                )
            except idempotency.IdempotencyStateError as exc:
                # Corrupt key-record state (e.g. language-order reservation): respond as for unreadable idempotency state elsewhere.
                raise HTTPException(
                    status_code=503, detail="idempotency state unreadable; retry later",
                ) from exc
            except BaseException:
                # Failed/terminated generation: intervening clear durably recorded cleared subjects on this key
                # (forgotten_keys); same-key regeneration retries discard those segments, requiring no repair here.
                raise
            # If clear advanced a subject's forget generation during generation, mark its output discarded
            # before persistence. Otherwise a crash after staging but before application leaves retries only the post-clear
            # generation, wrongly treating pre-clear facts as new and restoring them.
            _mark_items_forgotten_during_generation(lanlan_name, staging, contexts, generations)
            # Clear during this or earlier generation (key lock busy, so only records cleared subjects):
            # mark those segment outputs discarded too. After earlier generation failure/process kill, this retry arrives here
            # with post-clear generation; only the durable record identifies them.
            try:
                forgotten_during = _forgotten_keys_of(await idempotency.read_key(lanlan_name, key))
            except idempotency.IdempotencyStateError as exc:
                raise HTTPException(
                    status_code=503, detail="idempotency state unreadable; retry later",
                ) from exc
            if forgotten_during:
                _drop_segments_for_keys(staging, forgotten_during)
            try:
                await idempotency.write_staging(lanlan_name, key, staging)
            except MaintenanceModeError:
                raise
            except Exception as exc:
                logger.error(f"[scoped_history] {lanlan_name}: 暂存落盘失败: {exc}")
                raise HTTPException(
                    status_code=503,
                    detail="scoped history staging failed; retry with the same key",
                ) from exc
            # Recheck after staging persists: clear completed during the two writes above could not see staging during
            # cancellation scan. It necessarily advanced generation before scanning, so this check sees it.
            # Later clears find/cancel staging under key lock. Persist newly marked segments like the other two rechecks via
            # _persist_fence_marks (fall back to key records on write failure).
            newly = _mark_items_forgotten_during_generation(lanlan_name, staging, contexts, generations)
            if newly:
                await _persist_fence_marks(
                    lanlan_name, key, staging, _fenced_journal_keys(staging, newly),
                )
        if generations is None:
            # Recovery retries likewise discard segments in recorded forgotten_keys: clear during generation marked only records,
            # then staging persisted but application failed; this recovery path must not restore cleared segments.
            try:
                forgotten_now = _forgotten_keys_of(await idempotency.read_key(lanlan_name, key))
            except idempotency.IdempotencyStateError as exc:
                raise HTTPException(
                    status_code=503, detail="idempotency state unreadable; retry later",
                ) from exc
            if forgotten_now & _staged_subject_keys(staging):
                _drop_segments_for_keys(staging, forgotten_now)
                try:
                    await idempotency.write_staging(lanlan_name, key, staging)
                except MaintenanceModeError:
                    raise
                except Exception as exc:
                    raise HTTPException(
                        status_code=503,
                        detail="scoped history staging failed; retry with the same key",
                    ) from exc
        # Shared generation baseline for application and trust: initial generation request uses the pre-LLM snapshot; recovery retries snapshot here
        # (under key lock, before application). Later clear advances generation, proving staging predates it; fact layer discards
        # facts and trust step isolates based on that same evidence.
        trust_generations = (
            generations if generations is not None
            else _capture_forget_generations(lanlan_name, _staged_segment_subjects(staging, contexts))
        )
        newly = (
            _mark_items_forgotten_during_generation(lanlan_name, staging, contexts, trust_generations)
            if generations is None else set()
        )
        if newly:
            # Symmetric with generation-path post-staging recheck: freshly taken baseline equals current generation, so only an active clear
            # triggers here. Fact layer rejects this segment without markers; mark first or later display names would recreate cleared subjects.
            await _persist_fence_marks(
                lanlan_name, key, staging, _fenced_journal_keys(staging, newly),
            )
        try:
            await _apply_keyed_staging(
                lanlan_name, key, staging, generations,
                {index: context.get("display_name") for index, context in enumerate(contexts)},
                fact_generations=trust_generations,
            )
        except (HTTPException, MaintenanceModeError):
            raise
        except Exception as exc:
            logger.error(
                f"[scoped_history] {lanlan_name}: 暂存应用中断（保留暂存，"
                f"同键重试只补剩余项）: {exc}"
            )
            raise HTTPException(
                status_code=503,
                detail="scoped history apply interrupted; retry with the same key",
            ) from exc
        # Keyed paths have no owner signals (entry already rejects with 422); activity / channel only,
        # Pool mutations are idempotent by event ID, so every attempt can replay unchanged.
        trust_states = [context["trust_state"] for context in contexts]
        for state in trust_states:
            state["trust_signal_events"] = ()
        # Generation-free clear may advance generation during application: only fact items compare it, missing segments with
        # activity/language items alone. Recheck against baseline under the fact-store persistence lock, together with trust writes:
        # clear advances generation under this same lock (abegin_subject_forget), matching compare-then-persist facts semantics;
        # no clear can intervene between recheck and trust persistence. Pool lock is a leaf, and no FactStore calls occur while held, avoiding cycles.
        async with runtime.fact_store._get_persist_alock(lanlan_name):
            newly = _mark_items_forgotten_during_generation(lanlan_name, staging, contexts, trust_generations)
            if newly:
                # Persist segment marks so same-key recovery retries are blocked even if pool persistence failed.
                await _persist_fence_marks(
                    lanlan_name, key, staging, _fenced_journal_keys(staging, newly),
                )
            # Segments blocked by clear (tombstone/discard) lost their memory; their activity/channel must not reenter trust pools.
            try:
                fenced = await _fenced_segments(lanlan_name, staging)
            except idempotency.IdempotencyStateError as exc:
                raise HTTPException(
                    status_code=503, detail="idempotency state unreadable; retry later",
                ) from exc
            # On cancellation wait for unstoppable threaded pool writes to finish (aapply_trust_mutations), retaining persistence lock until then.
            trust_result, trust_outcomes = await _apply_trust_for_segments([
                _without_trust_mutation(state) if index in fenced else state
                for index, state in enumerate(trust_states)
            ])
        response = _keyed_response(
            shape, contexts, staging, trust_result, trust_outcomes,
        )
        if trust_result is not None and not trust_result.persisted:
            # Pool not persisted: truthfully report persisted=false for same-key retry; keep key pending
            # and staging intact, or duplicate retries would discard this activity batch.
            return response
        try:
            await idempotency.update_key(
                lanlan_name,
                key,
                idempotency.transition(idempotency.KEY_STATE_DONE),
            )
        except MaintenanceModeError:
            raise
        except Exception as exc:
            logger.error(f"[scoped_history] {lanlan_name}: 键标 done 失败: {exc}")
            raise HTTPException(
                status_code=503,
                detail="scoped history finalize failed; retry with the same key",
            ) from exc
        try:
            await idempotency.delete_staging(lanlan_name, key)
        except Exception as exc:  # noqa: BLE001 - key is done; leftover is swept by TTL
            logger.warning(
                f"[scoped_history] {lanlan_name}: 删除暂存失败（键已 done，"
                f"残留由启动清理回收）: {exc}"
            )
        return response


async def _fenced_segments(lanlan_name: str, staging: dict) -> set[int]:
    """Segment indexes a forget fenced off: dropped items, a segment drop mark, or a newer tombstone."""
    from . import idempotency

    segments = staging.get("segments") or []
    fenced = {
        index for index, segment in enumerate(segments)
        if isinstance(segment, dict) and segment.get(_SEGMENT_DROPPED_BY_FORGET) is True
    }
    dropped_seqs = {
        entry.get("seq") for entry in staging.get("applied") or []
        if isinstance(entry, dict) and any(entry.get(name) is True for name in _KEYED_DROP_MARKERS)
    }
    fenced |= {
        item.get("segment") for item in staging.get("items") or []
        if isinstance(item, dict) and item.get("seq") in dropped_seqs
    }
    try:
        tombstones = await idempotency.read_tombstones(lanlan_name)
        epochs = staging.get("epochs") or {}
        for index, segment in enumerate(segments):
            if not isinstance(segment, dict):
                continue
            # Activity-only segments with no extracted entries never checked tombstones; check here.
            tombstone_epoch = idempotency.tombstone_epoch(tombstones, segment.get("tombstone_keys") or [])
            if tombstone_epoch is not None and int(epochs.get(segment.get("wire_key"), 0)) < tombstone_epoch:
                fenced.add(index)
    except idempotency.IdempotencyStateError:
        # Unreadable tombstones prevent identifying blocked segments. Do not drop all activity and finalize (done retries return
        # duplicate forever, losing trust writes); raise so caller returns 503 and retries once tombstones are readable.
        raise
    return fenced


def _without_trust_mutation(state: dict) -> dict:
    source = state.get("trust_source") or {}
    return {
        **state,
        "trust_signal_events": (),
        "trust_activity_events": (),
        "trust_source": {**source, "channel": None},
    }


def _mark_items_forgotten_during_generation(
    lanlan_name: str, staging: dict, contexts: list[dict], generations: dict[int, int] | None,
) -> set[int]:
    """Journal as dropped every item of a subject whose forget generation moved.

    Returns the indexes of the segments this call journaled something new for
    (dropped items or a fresh segment mark), else an empty set, so a caller
    can persist exactly those as a fallback; a segment fenced by an earlier
    recheck is not reported again.

    A subject whose forget is still running (between ``abegin`` and
    ``aend``) counts as moved too: a baseline captured inside that window
    equals the current generation, yet the fact store already refuses the
    segment's writes.
    """
    if not generations:
        return set()
    fact_store = runtime.fact_store
    changed = {
        index for index, subject in enumerate(_staged_segment_subjects(staging, contexts))
        if index in generations
        and (
            fact_store._subject_forget_generation(lanlan_name, subject) != generations[index]
            or fact_store._subject_forget_is_active(lanlan_name, subject)
        )
    }
    if not changed:
        return set()
    applied = staging.setdefault("applied", [])
    done = {entry.get("seq") for entry in applied if isinstance(entry, dict)}
    added = False
    touched: set[int] = set()
    items = staging.get("items") or []
    for position, item in enumerate(items):
        if (
            item.get("segment") in changed
            and item.get("kind") != _KEYED_ITEM_LOCALE
            and item["seq"] not in done
        ):
            applied.append({"seq": item["seq"], "dropped_forget_during_generation": True})
            # Erase cleared subjects' extraction source/display names too, leaving none in staging if later interrupted.
            items[position] = _stripped_item(item)
            added = True
            touched.add(item.get("segment"))
    segments = staging.get("segments")
    if isinstance(segments, list):
        newly = {
            index for index in changed
            if index < len(segments) and isinstance(segments[index], dict)
            and segments[index].get(_SEGMENT_DROPPED_BY_FORGET) is not True
        }
        _mark_segments_dropped(segments, newly)
        touched |= newly
        # Persist marks even for language-only segments (language items never record discard), or segment marks remain memory-only.
        added = added or bool(newly)
    # Report only newly marked segments; do not reinclude prior blocked ones in fallback (valid segments may use their wire keys).
    return touched if added else set()


def _fingerprint_of_staging(document: dict) -> dict | None:
    segments = document.get("segments")
    if not isinstance(segments, list) or not isinstance(document.get("shape"), str):
        return None
    return {
        "shape": document["shape"],
        "wire_keys": [seg.get("wire_key") for seg in segments if isinstance(seg, dict)],
        "content_hash": document.get("request_hash"),
    }


async def _drop_leftover_staging(lanlan_name: str, key: str) -> None:
    from . import idempotency

    try:
        await idempotency.delete_staging(lanlan_name, key)
    except Exception as exc:  # noqa: BLE001 - Space reclamation only: leave deletion failures to startup cleanup; duplicate response is unaffected.
        logger.warning(f"[scoped_history] {lanlan_name}: 终态键的残留暂存删不掉，留给启动清理: {exc}")


def _staging_identity(document: dict) -> dict:
    """The request identity a cancelled record of this staging gets (never empty).

    A staging whose own identity fields are damaged gets a placeholder that
    no real request matches: a same-key retry is refused with 422 instead of
    answering 503 forever on a terminal record without an identity.
    """
    fingerprint = _fingerprint_of_staging(document)
    if fingerprint is not None:
        return fingerprint
    return {"shape": None, "wire_keys": sorted(_staged_subject_keys(document)), "content_hash": None}


def _forget_fences(
    request_subject_key: str | None, forget_epoch: int | None, fanout_fences: dict[str, int] | None,
) -> dict[str, int]:
    """``{wire key: forget epoch}`` a staged write's own epochs can be compared against."""
    fences = dict(fanout_fences or {})
    if request_subject_key is not None and forget_epoch is not None:
        fences[request_subject_key] = forget_epoch
    return fences


def _wire_after_forget(wire_key: object, epochs: object, fences: dict[str, int]) -> bool:
    """Whether a write stamped for ``wire_key`` was started after that key's forget fence."""
    if not isinstance(wire_key, str) or wire_key not in fences or not isinstance(epochs, dict):
        return False
    epoch = epochs.get(wire_key)
    return isinstance(epoch, int) and not isinstance(epoch, bool) and epoch >= fences[wire_key]


def _staged_after_forget(document: dict, subject_keys: set[str], fences: dict[str, int]) -> bool:
    """Whether a staged write was started knowing this forget (so it must not be cancelled).

    Decided per segment, each against the fence of its OWN wire key (the same
    rule that lets the segment pass the tombstone): every segment touching a
    forgotten subject must carry an epoch at or above that wire key's fence.
    ``fences`` holds the request subject's epoch plus, when the caller sent
    them, each fan-out target's own epoch; a wire key without a fence (a
    fan-out target nobody gave an epoch for, or an unrelated account routed
    into the erased subject) has no comparable epoch and counts as before.
    """
    if not fences:
        return False
    epochs = document.get("epochs")
    # Decide per segment: each involving a cleared subject compares its own wire-key fence (routed subject is only
    # that wire's destination; sufficiently new wire generation makes it a valid post-clear write). No fence means incomparable,
    # so treat as pre-clear.
    segments = document.get("segments")
    if isinstance(segments, list) and segments:
        touching = [segment for segment in segments if _segment_subject_keys(segment) & subject_keys]
        return bool(touching) and all(
            isinstance(segment, dict) and _wire_after_forget(segment.get("wire_key"), epochs, fences)
            for segment in touching
        )
    wires = document.get("wire_keys")
    if not isinstance(wires, list):
        return False
    touching_wires = [str(wire) for wire in wires if str(wire) in subject_keys]
    return bool(touching_wires) and all(_wire_after_forget(wire, epochs, fences) for wire in touching_wires)


def _stale_record_marks(record: dict, subject_keys: set[str], fences: dict[str, int]) -> set[str]:
    """Keys to journal as ``forgotten_keys`` on a pending record that has no staging yet.

    Position by position (the record keeps the wire keys and, aligned with
    them, ``routed_positions``): a position touching a forgotten subject
    through its wire or its routed destination is marked by its WIRE key,
    unless that wire was started after its own fence. The retry drops every
    segment whose wire OR routed destination is a marked key, so marking the
    wire (not the shared destination) spares a fresh wire routed to the same
    forgotten subject; a fresh wire routed onto a stale wire's own key is
    still dropped (over-dropping, never under-erasing). A record without
    aligned positions (older records) also marks the forgotten routed keys,
    as before.
    """
    request = record.get("request")
    raw_wires = request.get("wire_keys") if isinstance(request, dict) else None
    wires = [str(wire) for wire in raw_wires] if isinstance(raw_wires, list) else []
    epochs = record.get("epochs") or {}
    positions = record.get("routed_positions")
    aligned = (
        isinstance(positions, list) and len(positions) == len(wires)
        and all(isinstance(position, str) and position for position in positions)
    )
    marks: set[str] = set()
    for index, wire in enumerate(wires):
        routed = str(positions[index]) if aligned else None
        if wire not in subject_keys and routed not in subject_keys:
            continue
        if _wire_after_forget(wire, epochs, fences):
            continue
        marks.add(wire)
    if not aligned:
        routed_keys = record.get("routed_keys")
        if isinstance(routed_keys, list):
            marks |= subject_keys.intersection(str(k) for k in routed_keys)
    return marks


def _staged_subject_keys(document: dict) -> set[str]:
    """Every subject key a staged journal touches: wire keys plus each segment's routed subject."""
    staged = document.get("subjects")
    keys = {str(s) for s in staged} if isinstance(staged, list) else set()
    segments = document.get("segments")
    for segment in segments if isinstance(segments, list) else []:
        keys |= _segment_subject_keys(segment)
    return keys


def _segment_subject_keys(segment: object) -> set[str]:
    if not isinstance(segment, dict):
        return set()
    keys = {str(segment.get("wire_key"))}
    subject = segment.get("subject")
    if isinstance(subject, dict) and subject.get("subject_kind") and subject.get("subject_id"):
        keys.add(f"{subject['subject_kind']}:{subject['subject_id']}")
    return keys


def _items_shape_valid(items: list, segments: list) -> bool:
    """Items numbered ``seq == position`` with a known kind and an in-range segment; every segment an object."""
    if not all(isinstance(segment, dict) for segment in segments):
        return False
    for position, item in enumerate(items):
        seq, segment = item.get("seq"), item.get("segment")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq != position:
            return False
        if not isinstance(segment, int) or isinstance(segment, bool) or not 0 <= segment < len(segments):
            return False
        if item.get("kind") not in (_KEYED_ITEM_LOCALE, _KEYED_ITEM_FACTS, _KEYED_ITEM_DISPLAY_NAME):
            return False
    return True


def _applied_seqs_valid(applied: list, item_count: int) -> bool:
    """Every applied entry is an object whose ``seq`` is a distinct in-range integer (as replay requires)."""
    seqs = [entry.get("seq") if isinstance(entry, dict) else None for entry in applied]
    return all(
        isinstance(seq, int) and not isinstance(seq, bool) and 0 <= seq < item_count for seq in seqs
    ) and len(set(seqs)) == len(seqs)


def _drop_forgotten_segments(
    document: dict, subject_keys: set[str], fences: dict[str, int] | None = None,
) -> bool:
    """Drop only the forgotten segments of a partly affected multi-segment journal.

    A segment is affected when it touches ``subject_keys`` and was not started
    after its own wire key's fence (``fences``, see :func:`_staged_after_forget`).
    Returns False (nothing changed) when no segment or every segment is
    affected: the whole key is cancelled then. Otherwise every not-yet-applied
    item of an affected segment is journaled as ``dropped_forget`` and every
    item of such a segment loses its extracted content, so a retry applies
    only the remaining segments.
    """
    segments = document.get("segments")
    if not isinstance(segments, list) or not segments:
        return False
    epochs = document.get("epochs")
    affected = {
        index for index, segment in enumerate(segments)
        if _segment_subject_keys(segment) & subject_keys
        and not (
            isinstance(segment, dict)
            and _wire_after_forget(segment.get("wire_key"), epochs, fences or {})
        )
    }
    if not affected or len(affected) == len(segments):
        return False
    # Inspect raw values before defaults: only missing fields mean empty lists. {} / "" / null are corrupt journals;
    # accepting through `or []` or None-as-empty leaves corrupt values in rewritten copies, making clear return 500.
    applied = document["applied"] if "applied" in document else []
    items = document["items"] if "items" in document else []
    if (
        not isinstance(applied, list) or not isinstance(items, list)
        or not all(isinstance(item, dict) for item in items)
        or not _applied_seqs_valid(applied, len(items))
        or not _items_shape_valid(items, segments)
    ):
        # Corrupt journal structure (non-list applied, noniterable items, etc.) prevents selective segment discard; cancel whole key.
        # Corrupt auxiliary state must not make every privacy clear return 500 without erasing a row.
        return False
    # Rewrite a copy, then validate: corrupt cleared payload is fine because erased; retain journals only if remaining
    # segments can replay per-item on retry. Otherwise (scalar effect keys, etc.) cancel whole key to avoid permanent 503 for uncleared segments.
    rewritten = copy.deepcopy(document)
    _rewrite_forgotten_segments(rewritten, affected)
    if not _keyed_staging_items_valid(rewritten, structural_only=True):
        return False
    document.clear()
    document.update(rewritten)
    return True


def _rewrite_forgotten_segments(document: dict, affected: set[int]) -> None:
    segments = document["segments"]
    applied = document.setdefault("applied", [])
    items = document.get("items") or []
    forgotten_seqs = {
        item.get("seq") for item in items
        if isinstance(item, dict) and item.get("segment") in affected
        and item.get("kind") != _KEYED_ITEM_LOCALE
    }
    # Applied results for cleared segments (fact_ids/identities) refer to erased rows: replace with discard records,
    # so retry responses no longer report nonexistent facts.
    applied[:] = [
        {"seq": entry.get("seq"), "dropped_forget": True}
        if isinstance(entry, dict) and entry.get("seq") in forgotten_seqs else entry
        for entry in applied
    ]
    done = {entry.get("seq") for entry in applied if isinstance(entry, dict)}
    for position, item in enumerate(items):
        if (
            not isinstance(item, dict) or item.get("segment") not in affected
            or item.get("kind") == _KEYED_ITEM_LOCALE
        ):
            # Retain language-order items as when cleared during generation (no cleared-subject content).
            continue
        if item.get("seq") not in done:
            applied.append({"seq": item.get("seq"), "dropped_forget": True})
        # Cleared subjects' extraction source / display names must not remain on disk.
        items[position] = _stripped_item(item)
    _mark_segments_dropped(segments, affected)


def _drop_segments_for_keys(document: dict, subject_keys: set[str]) -> None:
    """Journal every unapplied item of segments touching ``subject_keys`` as dropped, stripped.

    Locale items included: replaying a persisted ``forgotten_keys`` marker, the
    locale order was reserved after the forget and no longer proves the
    request predates it, so applying it would recreate erased locale state.
    """
    segments = document.get("segments") or []
    affected = {
        index for index, segment in enumerate(segments)
        if _segment_subject_keys(segment) & subject_keys
    }
    if not affected:
        return
    applied = document.setdefault("applied", [])
    items = document.get("items")
    items = [] if items is None else items
    if not isinstance(applied, list) or not isinstance(items, list):
        raise ValueError("staging journal is malformed")
    done = {entry.get("seq") for entry in applied if isinstance(entry, dict)}
    for position, item in enumerate(items):
        if not isinstance(item, dict) or item.get("segment") not in affected:
            continue
        if item.get("seq") not in done:
            applied.append({"seq": item.get("seq"), "dropped_forget": True})
        if item.get("kind") != _KEYED_ITEM_LOCALE:
            items[position] = _stripped_item(item)
    _mark_segments_dropped(segments, affected)


def _forgotten_keys_of(record) -> set[str]:
    """The ``forgotten_keys`` marker of a key record (empty when absent).

    It is the only durable evidence that a forget hit a key while it was
    generating; a present but malformed value raises ``IdempotencyStateError``
    instead of reading as "nothing forgotten", which would regenerate (and
    write back) the forgotten subject's history.
    """
    from . import idempotency

    if not isinstance(record, dict) or "forgotten_keys" not in record:
        return set()
    value = record["forgotten_keys"]
    if not isinstance(value, list) or not all(isinstance(key, str) and key for key in value):
        raise idempotency.IdempotencyStateError("forgotten_keys marker is malformed")
    return set(value)


def _mark_segments_dropped(segments: list, indexes: set[int]) -> None:
    # Mark segments cleared/discarded: language-only segments cannot be identified from items, which never mark discard;
    # recovery name restoration would otherwise apply pre-clear key metadata to subsequently recreated sections.
    for index in indexes:
        if isinstance(segments[index], dict):
            segments[index][_SEGMENT_DROPPED_BY_FORGET] = True


def _stripped_item(item: dict) -> dict:
    # Keep only sequence numbers needed for responses/skipping and effect keys (content-free; item validation derives sequences from them).
    stripped = {"seq": item.get("seq"), "kind": item.get("kind"), "segment": item.get("segment")}
    if item.get("kind") == _KEYED_ITEM_FACTS:
        effect_keys = item.get("effect_keys")
        stripped["effect_keys"] = list(effect_keys) if isinstance(effect_keys, list) else []
    return stripped


def _is_cancelled_staging_marker(document: dict) -> bool:
    """Whether ``document`` has exactly the stripped shape :func:`_cancelled_staging_marker` writes."""
    segments = document.get("segments")
    return (
        document.get(_KEYED_STAGING_CANCELLED) is True
        and document.get("items") == []
        and document.get("applied") == []
        and isinstance(segments, list)
        and all(isinstance(segment, dict) and set(segment) == {"wire_key"} for segment in segments)
    )


def _cancelled_staging_marker(document: dict) -> dict:
    """The staging document reduced to what a retry needs to recognise its key as cancelled.

    Drops every extracted product (facts, display names, the subjects'
    fields): a forget must not leave the forgotten subject's content on disk.
    """
    segments = document.get("segments")
    return {
        "key": document.get("key"),
        "state": document.get("state"),
        "shape": document.get("shape"),
        "subjects": document.get("subjects"),
        "epochs": document.get("epochs"),
        "created_at": document.get("created_at"),
        "request_hash": document.get("request_hash"),
        "segments": [
            {"wire_key": segment.get("wire_key")}
            for segment in (segments if isinstance(segments, list) else [])
            if isinstance(segment, dict)
        ],
        "items": [],
        "applied": [],
        _KEYED_STAGING_CANCELLED: True,
    }


async def _cancel_staged_writes_for_subjects(
    lanlan_name: str,
    subject_keys: set[str],
    *,
    request_subject_key: str | None = None,
    forget_epoch: int | None = None,
    fanout_fences: dict[str, int] | None = None,
    best_effort: bool = False,
) -> int:
    """Cancel every staged keyed write that touches one of ``subject_keys``.

    ``best_effort`` (the pass before the erase) skips a journal or key record
    whose handling fails and goes on with the rest; the pass after the erase
    raises instead, so the forget is retried.

    A staging started with an epoch at or above ``forget_epoch`` for the
    request subject was issued after this forget and is left alone; so is a
    segment whose wire key is a fan-out target stamped at or above that
    target's own fence in ``fanout_fences``.

    Runs after the erase, holding no other lock: each affected key's lock is
    taken on its own (an in-flight apply of the same key finishes first; an
    in-flight GENERATION never has a staging file yet, so a forget is never
    blocked behind an LLM call). The key is marked ``cancelled`` before its
    staging file is removed, so a crash in between leaves a cancelled key
    plus a leftover file the TTL sweep removes, never a revivable journal.
    """
    from . import idempotency

    fences = _forget_fences(request_subject_key, forget_epoch, fanout_fences)
    cancelled = 0
    async def _cancel_one_document(path, document) -> None:
        nonlocal cancelled
        if not isinstance(document, dict):
            # Unreadable staging hides its subjects; without an owning key record, record-based cancellation misses it too.
            # Retaining could preserve cleared source text, later claimed by same-key retry after repair. Delete it.
            if await idempotency.drop_unreadable_orphan_staging(lanlan_name, path):
                cancelled += 1
            return
        key = document.get("key")
        # Match recorded routed write subjects too: changed account bindings may exclude this staging's wire subject from current fanout,
        # yet application writes to the subject recorded in staging.
        # Missing/corrupt subjects index still requires per-segment matching: a bad index must not bypass clear; later same-key retries
        # could claim orphan staging and restore pre-clear facts.
        if not subject_keys.intersection(_staged_subject_keys(document)):
            return
        if not isinstance(key, str) or not key or not idempotency.is_staging_path_of(lanlan_name, key, path):
            # Embedded key differs from filename: never follow an untrusted key to another staging path.
            # Erase this file in place into a cancellation marker (retain mismatched key, so retries still fail closed),
            # leaving no cleared subject extraction source on disk.
            await idempotency.scrub_misplaced_staging(
                lanlan_name, path, _cancelled_staging_marker,
            )
            cancelled += 1
            return
        async with idempotency.key_lock(lanlan_name, key):
            current = await idempotency.read_staging(lanlan_name, key)
            if current is None or _staged_after_forget(current, subject_keys, fences):
                return
            if _drop_forgotten_segments(current, subject_keys, fences):
                # Multi-segment batch with only some subjects cleared: discard only those unapplied items,
                # erase their extraction source, retain pending key/staging; retry writes remaining segments normally.
                await idempotency.write_staging(lanlan_name, key, current)
                cancelled += 1
                return
            identity = _staging_identity(document)

            def _cancel(old, identity=identity):
                # Missing records inherit staged request identity: staging is deleted after cancellation; identity-less cancelled records let
                # other requests obtain duplicate with this key. Retain existing recorded identity: corrupt staged identity produces
                # a placeholder, which must not overwrite valid identity and turn legitimate duplicate retries into perpetual 422.
                stored = old.get("request") if isinstance(old, dict) else None
                request = stored if isinstance(stored, dict) else identity
                return idempotency.transition(idempotency.KEY_STATE_CANCELLED, request=request)(old)

            try:
                await idempotency.update_key(lanlan_name, key, _cancel)
            except idempotency.IdempotencyStateError as exc:
                # Unreadable key file: corrupt auxiliary data must not block privacy clear, but cancellation cannot be recorded there.
                # Deleting staging lets retries after key-file repair see pending/no-staging and re-extract with
                # post-clear generation (and no tombstone without forget_epoch), restoring content.
                # Instead retain cancellation in staging: retry repairs cancelled record and returns duplicate.
                logger.warning(
                    f"[scoped_forget] {lanlan_name}: 幂等键文件不可读，取消改记在暂存里: {exc}"
                )
                # Retain only identity needed to recognize cancellation; erase extracted fact text and display names together.
                await idempotency.write_staging(lanlan_name, key, _cancelled_staging_marker(current))
                cancelled += 1
                return
            await idempotency.delete_staging(lanlan_name, key)
            cancelled += 1
    for path, document, _mtime in await idempotency.list_staging(lanlan_name):
        try:
            await _cancel_one_document(path, document)
        except MaintenanceModeError:
            raise
        except Exception as exc:
            if not best_effort:
                raise
            # Best-effort pass: failure in one staging file skips only it; cancel others normally (post-erasure pass is fallback).
            logger.warning(f"[scoped_forget] {lanlan_name}: 取消暂存 {os.path.basename(path)} 失败，跳过: {exc}")
    # Pending precedes staging: a crash between leaves only records, invisible to the scan above.
    # Claim by recorded request identity and mark cancelled too, preventing later same-key retries regenerating
    # with post-clear generation and writing back.
    try:
        records = await asyncio.to_thread(
            idempotency._read_json_object, idempotency.keys_path(lanlan_name),
        )
    except idempotency.IdempotencyCorruptError as exc:
        # Corrupt key-file content: keyed requests already fail closed (503), writing nothing;
        # auxiliary corruption must not block privacy clear; log, skip claiming, and continue clearing.
        logger.warning(f"[scoped_forget] {lanlan_name}: 幂等键文件已损坏，跳过 pending 认领: {exc}")
        records = {}
    except Exception as exc:  # noqa: BLE001
        # Temporary unreadability (sharing retries exhausted/permissions) may resolve for next same-key retry. Skipping claiming
        # lets unstaged pending keys regenerate with post-clear generation and restore content. Pre-erasure pass
        # logs only (post-erasure pass covers it); post-erasure pass raises for clear retry (tombstones persisted, retry safe).
        if not best_effort:
            raise
        logger.warning(f"[scoped_forget] {lanlan_name}: 幂等键文件一时读不出，本遍跳过 pending 认领: {exc}")
        records = {}
    async def _cancel_one_record(key, record) -> None:
        nonlocal cancelled
        if not isinstance(record, dict) or record.get("state") != idempotency.KEY_STATE_PENDING:
            return
        request = record.get("request")
        wire_keys = request.get("wire_keys") if isinstance(request, dict) else None
        wire_only = [str(k) for k in wire_keys] if isinstance(wire_keys, list) else []
        routed = record.get("routed_keys")
        wire_keys = wire_only + ([str(k) for k in routed] if isinstance(routed, list) else [])
        # Staging scan above is only a snapshot; staging written later may route to cleared subjects while records
        # hold only wire keys. If staging exists, match all recorded wire+routed subjects; only absent staging
        # falls back to wire keys. Pre-read unlocked to exclude unrelated keys, whose requests may hold
        # key locks while awaiting LLM; privacy clear must not queue behind them. Re-read/recheck relevant keys under lock.
        peek_unreadable = False
        try:
            peek = await idempotency.read_staging(lanlan_name, key)
        except idempotency.IdempotencyStateError:
            peek = None
            peek_unreadable = True
        peek_touched = _staged_subject_keys(peek) | set(wire_keys) if peek is not None else set(wire_keys)
        if not subject_keys.intersection(peek_touched):
            return
        # Without staging, evaluate recorded positions individually: only wires predating their own fences enter forgotten_keys;
        # sufficiently new wires remain writable on retry (never reduce the whole request to one discard boolean). Include unfenced
        # wires routed to cleared subjects positionally too, not merely wires whose own key was cleared.
        stale_marks = _stale_record_marks(record, subject_keys, fences)
        if peek is None and not stale_marks:
            # No staging and no pre-fence positions: all are valid new writes started with knowledge of this clear.
            return
        def _mark_forgotten(touched: set[str]):
            def _mark(old, touched=touched):
                if old is None or old.get("state") != idempotency.KEY_STATE_PENDING:
                    return None
                # Corrupt old values (scalar / embedded objects): retain usable keys only; corrupt auxiliary state must not return
                # 500 before erasure.
                prior = old.get("forgotten_keys")
                if prior is None:
                    usable: set[str] = set()
                elif isinstance(prior, list) and all(isinstance(k, str) and k for k in prior):
                    usable = set(prior)
                else:
                    # Corrupt old markers are sole durable evidence of clearing during generation; do not simply drop them. Conservatively mark
                    # all wire/routed subjects in this key record (treat all as previously cleared).
                    request = old.get("request") if isinstance(old.get("request"), dict) else {}
                    every = [
                        value
                        for source in (request.get("wire_keys"), old.get("routed_keys"))
                        if isinstance(source, list)
                        for value in source
                    ]
                    usable = {k for k in every if isinstance(k, str) and k}
                merged = sorted(usable | touched)
                return {**old, "forgotten_keys": merged}

            return _mark

        if peek is None and not peek_unreadable and idempotency.key_lock(lanlan_name, key).locked():
            # No staging and key lock busy: owning request awaits LLM; do not queue behind it. Durably record cleared subjects
            # only on the record (atomic single-record rewrite under character lock). Before staging after generation, or regenerating
            # after failure/process kill, retries use this to discard segments rather than restore old content with post-clear generation.
            if not stale_marks:
                return
            await idempotency.update_key(lanlan_name, key, _mark_forgotten(stale_marks))
            cancelled += 1
            return
        async with idempotency.key_lock(lanlan_name, key):
            unreadable = None
            try:
                staged = await idempotency.read_staging(lanlan_name, key)
            except idempotency.IdempotencyStateError as exc:
                staged = None
                unreadable = exc
            touched = _staged_subject_keys(staged) | set(wire_keys) if staged is not None else set(wire_keys)
            if not subject_keys.intersection(touched):
                return
            if unreadable is not None:
                # Pending record with unreadable staging is invisible to content scans above. It must neither block
                # clear with repeated 500 nor remain for same-key retries to fail closed then apply after repair.
                # Claim by record, mark cancelled, then delete corrupt staging.
                logger.warning(f"[scoped_forget] {lanlan_name}: 暂存不可读，按键记录取消: {unreadable}")
            if staged is not None and _staged_after_forget(staged, subject_keys, fences):
                # New request carrying post-clear generation: output is valid new memory; do not cancel.
                return
            if staged is not None and _drop_forgotten_segments(staged, subject_keys, fences):
                # Like staging scan above: discard only cleared-subject segments, retaining others for retry.
                await idempotency.write_staging(lanlan_name, key, staged)
                cancelled += 1
                return
            if staged is None and unreadable is None and (
                not set(wire_keys) <= subject_keys or not set(wire_only) <= stale_marks
            ):
                # No staging (failed generation/process killed and lock released), but uncleared subjects or sufficiently new
                # wires remain: as for busy key locks, record only pre-fence wires; regeneration retries discard
                # those segments alone. Whole-key cancellation permanently loses others (client gets duplicate, never resends).
                if not stale_marks:
                    return
                await idempotency.update_key(lanlan_name, key, _mark_forgotten(stale_marks))
                cancelled += 1
                return
            # Cancel under key lock regardless of staging presence: scan above is a snapshot; staging written afterward
            # (failed request released lock) also needs cancellation, or same-key retries between erasure and second scan
            # could apply it using post-clear generation.
            await idempotency.update_key(
                lanlan_name, key, idempotency.transition(idempotency.KEY_STATE_CANCELLED),
            )
            await idempotency.delete_staging(lanlan_name, key)
            cancelled += 1
    for key, record in records.items():
        try:
            await _cancel_one_record(key, record)
        except MaintenanceModeError:
            raise
        except Exception as exc:
            if not best_effort:
                raise
            logger.warning(f"[scoped_forget] {lanlan_name}: 按记录取消键失败，跳过: {exc}")
    return cancelled


class MemoryTurnContextRequest(BaseModel):
    # The user's own words of this text turn (Main cuts them to
    # TURN_CONTEXT_QUERY_MAX_CHARS); never stored.
    text: str
    # Time left of Main's turn budget; the vector half is dropped past it.
    budget_ms: int = 1000
    language: str | None = None
    # When the session that asks began (epoch seconds): facts written since
    # come from a conversation the model still has in context.
    session_started_at: float | None = None


@app.post("/internal/memory/{lanlan_name}/turn_context")
async def memory_turn_context(lanlan_name: str, req: MemoryTurnContextRequest):
    """Memories a text turn brings in unasked (passive turn context).

    Private scope only. Returns the rendered lines without a fence (Main
    wraps them), the ids shown, and their token count; any failure reads as
    no result, so a broken memory store never delays a reply.
    """
    lanlan_name = validate_lanlan_name(lanlan_name)
    if runtime.fact_store is None or runtime.reflection_engine is None:
        return {"result": "unavailable", "context": "", "memory_ids": [], "tokens": 0}
    resolved_language = await _resolve_scoped_memory_language(lanlan_name, [], req.language)
    started_at = None
    if isinstance(req.session_started_at, (int, float)) and req.session_started_at > 0:
        try:
            started_at = datetime.fromtimestamp(float(req.session_started_at))
        except (OverflowError, OSError, ValueError):
            started_at = None
    from config.prompts.prompts_memory import _normalize_memory_prompt_lang
    from memory.turn_context import amemory_turn_context

    with language_context(resolved_language):
        return await amemory_turn_context(
            lanlan_name=lanlan_name,
            text=req.text,
            fact_store=runtime.fact_store,
            reflection_engine=runtime.reflection_engine,
            persona_manager=getattr(runtime, "persona_manager", None),
            config_manager=runtime._config_manager,
            budget_ms=max(50, min(int(req.budget_ms or 0), 5000)),
            language=_normalize_memory_prompt_lang(resolved_language),
            session_started_at=started_at,
        )


@app.post("/internal/memory/{lanlan_name}/scoped_context")
async def get_scoped_context(lanlan_name: str, req: ScopedContextRequest):
    # Validate before the locale lookup: it keys per-character state files by
    # this name, so it must not run on an unvalidated path component. The
    # inner handler validates again — the helper is idempotent.
    lanlan_name = validate_lanlan_name(lanlan_name)
    resolved_language = await _resolve_scoped_memory_language(
        lanlan_name,
        req.subjects,
        req.language,
    )
    with language_context(resolved_language):
        return await _get_scoped_context(lanlan_name, req)


async def _get_scoped_context(lanlan_name: str, req: ScopedContextRequest):
    """Render only explicitly authorized persona/reflection subjects.

    ⚠️ `subjects` ORDER IS THE BUDGET PRIORITY. The renderer allocates the
    overall scoped gate (`SCOPED_RENDER_TOTAL_MAX_TOKENS`) strictly first-
    come-first-served down this list, and a subject that arrives after the
    gate has dropped below `SCOPED_RENDER_SUBJECT_MIN_TOKENS` loses its
    whole section — not a shortened version, the whole thing, because half
    a persona reads to the model as a complete one. No subject kind is
    special-cased; an earlier attempt to reserve a slice for a group
    subject queued behind its members was deleted because every one of its
    interactions was a way to invert the order it was meant to protect.

    One exception, and it is deliberate: a subject whose only content is
    budget-EXEMPT (`protected` character-card lines, `suppress`ed
    do-not-mention entries) still renders when the gate is spent. Those
    sections never cost the gate anything, so there is no fragment to
    avoid — and dropping them would take a do-not-mention list with it,
    after which the character volunteers exactly what it was told to sit
    on. Only subjects with budgeted content they cannot afford are dropped
    whole. See `test_a_group_holding_only_suppressed_facts_still_renders_them`.

    So the caller owns the ranking. The one shipped caller
    (`session_instruction_service._build_core_memory_section`, via
    `memory_bridge.fetch_scoped_bootstrap_memory`) sends the group subject
    FIRST and then at most one `group_participant` for the current
    speaker. If a later PR widens that to several recent speakers, the
    group still has to lead. That is a contract, not a coincidence — send
    members first and the group's own persona is what falls off the end.

    Deliberately not validated here: rejecting an order would turn a
    ranking choice into a 422 for callers with a legitimately different one
    (a private-DM-style render with no group subject at all is already
    valid input). The endpoint accepts 1..8 subjects in any order; what it
    does NOT do is second-guess the order it was given.
    """
    lanlan_name = validate_lanlan_name(lanlan_name)
    if runtime.persona_manager is None or runtime.reflection_engine is None:
        raise HTTPException(
            status_code=503,
            detail="memory_server not fully initialized (limited mode or startup incomplete)",
        )
    if not req.subjects or len(req.subjects) > 8:
        raise HTTPException(status_code=422, detail="subjects must contain 1..8 items")
    # Fold FIRST, then expand: the ``1..8`` check above runs on the raw request
    # and folding can only shrink the slot list, so the wire contract still
    # holds. Authorization uses the flattened expansion, while rendering gets
    # the participant groups so budget, heading and id stay one-per-person.
    groups, subjects = _fold_request_subjects(req.subjects)
    # Suppression expiry only occurs in aupdate_suppressions, previously called only by
    # legacy /get_settings and /new_dialog. Group-only deployments never reach
    # them, making scoped reflections permanently invisible after their first suppression.
    try:
        await runtime.reflection_engine.aupdate_suppressions(lanlan_name)
    except Exception as exc:
        logger.warning(f"[scoped] 刷新 reflection suppression 失败: {exc}")
    pending_reflections = await runtime.reflection_engine.aget_pending_reflections(
        lanlan_name,
        subjects=subjects,
        include_legacy_private=False,
    )
    confirmed_reflections = await runtime.reflection_engine.aget_confirmed_reflections(
        lanlan_name,
        subjects=subjects,
        include_legacy_private=False,
    )
    rendered = await runtime.persona_manager.arender_persona_markdown(
        lanlan_name,
        pending_reflections,
        confirmed_reflections,
        subjects=subjects,
        include_legacy_private=False,
        participant_groups=groups,
    )
    return PlainTextResponse(rendered)


class ScopedForgetRequest(BaseModel):
    subject: MemorySubjectRequest
    # Optional caller-side forget generation. When present, every erased
    # subject gets a durable tombstone holding the largest epoch seen, and a
    # keyed scoped_history whose ``subject_epochs`` for that subject is lower
    # is dropped at apply time. Absent: no tombstone is written.
    forget_epoch: int | None = Field(default=None, ge=0)
    # Optional ``{MemorySubject.key: forget_epoch}`` of every OTHER subject
    # identity expansion erases along with the requested one, each from its
    # own per-key counter (requires ``forget_epoch``). Absent: only the
    # requested subject is fenced, exactly as before. Present but missing a
    # fan-out target: 409 listing the missing keys, before anything is
    # written or erased, so the caller bumps those counters and resends.
    # A NEW forget must bump EVERY target's counter, not only the missing
    # ones: a target whose own completion marker already reaches the epoch
    # given for it is treated as a replay and is not erased again, so reusing
    # its last epoch leaves the rows routed into its pile since then.
    target_epochs: dict[str, Annotated[int, Field(ge=0)]] | None = None


_FORGET_TARGET_EPOCHS_MAX = 64


@app.post("/internal/memory/{lanlan_name}/scoped_forget")
async def forget_scoped_subject(lanlan_name: str, req: ScopedForgetRequest):
    """Erase one subject domain; epoch-tagged forgets of a subject run one at a time.

    The per-subject fence spans the completed-epoch check, the erase, both
    cancellation passes and the completion marker: without it an older
    forget could recheck after a newer one released its erase locks but
    before it published its completed epoch, and erase again.

    The fence is taken for the requested subject AND every fan-out target,
    all of them in sorted key order before any other lock: two forgets that
    name different linked aliases share targets, and one must not recheck
    or raise a shared target's fence between the other's erase and its
    completion markers. When the fan-out changes while the fences are being
    taken, they are released and taken again for the new set.
    """
    if req.forget_epoch is None:
        return await _run_forget(lanlan_name, req)
    import contextlib

    from . import idempotency

    try:
        subject = req.subject.to_domain()
        fence_name = validate_lanlan_name(lanlan_name)
    except Exception:  # noqa: BLE001 - Let implementation below report invalid requests normally.
        return await _run_forget(lanlan_name, req)
    for _attempt in range(_FORGET_FENCE_ATTEMPTS):
        fence_keys = _forget_fence_keys(subject)
        async with contextlib.AsyncExitStack() as stack:
            # Acquire all fences once in key order: every clear uses the same order, avoiding mutual waits.
            for fence_key in fence_keys:
                await stack.enter_async_context(idempotency.forget_fence(fence_name, fence_key))
            try:
                return await _run_forget(lanlan_name, req, fenced_keys=frozenset(fence_keys))
            except _FanoutChangedUnderFence:
                # Bindings changed while acquiring fences, adding unfenced fanout targets: release/reacquire (no writes yet).
                continue
    raise HTTPException(
        status_code=503,
        detail="identity links kept changing during the scoped forget; retry",
    )


_FORGET_FENCE_ATTEMPTS = 3


class _FanoutChangedUnderFence(Exception):
    """The fan-out grew a target whose forget fence this request does not hold."""


def _forget_fence_keys(subject) -> list[str]:
    """Sorted subject keys whose forget fences an epoch-tagged forget of ``subject`` takes."""
    from memory import trust_store as _trust_store

    if not _trust_store.trust_snapshot().loaded:
        # Unreadable identity pool makes clear itself return 503 without partial erasure; acquire only request subject's own fence.
        return [subject.key]
    return sorted({subject.key} | {target.key for target in _forget_fanout_targets(subject)})


async def _forget_scoped_subject(lanlan_name: str, req: ScopedForgetRequest, *, fenced_keys=None):
    """Delete every stored memory of one exact (subject, scope) domain.

    Retraction entry: after removing a friend/leaving a group, clear this subject's facts
    (active + archived), reflections (including surfaced references), persona sections
    (including display_name), and pending corrections together. Previously the four scoped
    endpoints only added data, without retraction. Match (key, scope) exactly: unstamped legacy
    data and other scopes are excluded. Idempotent: repeats report zero. Partial failures return 500;
    safe to retry, with deleted data never revived. Reflection/persona archive shards remain as event-sourcing
    records; persistent forget watermarks prevent restoration even after event replay rebuilds them.
    """  # noqa: DOCSTRING_CJK
    lanlan_name = validate_lanlan_name(lanlan_name)
    if (
        runtime.fact_store is None
        or runtime.fact_dedup_resolver is None
        or runtime.persona_manager is None
        or runtime.reflection_engine is None
    ):
        raise HTTPException(
            status_code=503,
            detail="memory_server not fully initialized (limited mode or startup incomplete)",
        )
    subject = req.subject.to_domain()
    _validate_target_epochs(req, subject)
    from memory import trust_store as _trust_store
    if not _trust_store.trust_snapshot().loaded:
        # FAIL CLOSED. With the pool unreadable the fan-out set is unknown, and
        # ``expand_subject`` degrades to just the requested subject — so a
        # non-canonical account whose rows were routed into the canonical pile
        # would get a PARTIAL erase reported as ``forgotten``. Under-deleting
        # on a privacy path and calling it success is the one outcome worth a
        # hard failure; the caller retries once the pool loads.
        #
        # Narrow by construction: a fresh or empty pool is ``loaded`` with no
        # entities, so a deployment that never linked accounts never sees this.
        raise HTTPException(
            status_code=503,
            detail=(
                "identity pool unreadable; refusing a partial scoped forget, "
                "retry once the trust pool loads"
            ),
        )
    targets = _forget_fanout_targets(subject)
    if fenced_keys is not None and not {target.key for target in targets} <= fenced_keys:
        # Fanout contains unfenced target: return to outer layer to reacquire before tombstone writes/erasure.
        raise _FanoutChangedUnderFence()
    forgotten_subject_keys = {subject.key} | {target.key for target in targets}
    default_scope = subject.scope == subject.key
    if req.forget_epoch is not None and not default_scope:
        # Generations/tombstones/erased markers use kind:id; a nondefault generation-bearing scope could make another
        # scope under the same key appear erased and skip deletion. Generation-bearing clears support default scope only.
        raise HTTPException(status_code=422, detail="forget_epoch requires the default subject scope")
    # Per-fanout-target generations (when client supplies target_epochs): any missing target returns 409 before writes.
    fanout_epochs = _fanout_target_epochs(req, subject, targets)
    fence_at_start = None
    effective_fence = req.forget_epoch
    erase_targets = list(targets)
    # Fanout targets: pre-erasure fences / effective cancellation fences are each recorded by their own keys.
    fanout_fence_at_start: dict[str, int] = {}
    effective_fanout_fences = dict(fanout_epochs or {})
    if req.forget_epoch is not None:
        # Persist tombstones before any erasure: application snapshots generation then checks tombstone,
        # so applications starting afterward see it; items that already passed tombstone checks
        # are discarded by the forget generation advanced during erasure below.
        from . import idempotency

        try:
            # Generations count separately by MemorySubject.key: request subject uses forget_epoch; fanout targets only
            # their client-supplied generations, never the request subject's (which would permanently block smaller target generations).
            # Without target_epochs, omit fanout tombstones; fact-store forget generation alone protects in-flight writes.
            recorded = await idempotency.record_tombstone_epochs(
                lanlan_name, {**effective_fanout_fences, subject.key: req.forget_epoch},
            )
            # Fence present before erasure (a newer clear may have raised it then failed before erasing): this erasure
            # follows it, so completion also covers that generation.
            fence_at_start = _recorded_fence(recorded, subject.key)
            if fence_at_start is not None and fence_at_start > effective_fence:
                # Cancel staging against the effective existing fence, not this possibly stale replay's lower generation:
                # all pre-fence staging must be cancelled.
                effective_fence = fence_at_start
            _raise_fanout_fences(recorded, effective_fanout_fences, fanout_fence_at_start)
            # Caller supplied a generation for a key no longer in current fanout (unbound between requests): write no new tombstone,
            # but if previously cleared (existing fence), include that persisted fence in cancellation scans,
            # or pre-clear staging arriving after original erasure stays pending with plaintext forever. Use persisted fences only:
            # current supplied generations could wrongly cancel its valid independent post-unbind writes.
            for retained_key in _retained_target_keys(req, subject, targets):
                fence = _recorded_fence(recorded, retained_key)
                if fence is not None:
                    effective_fanout_fences[retained_key] = fence
        except MaintenanceModeError:
            raise
        except idempotency.IdempotencyStateError as exc:
            # Entire tombstone file unreadable: corrupt auxiliary data must not block privacy erasure; erase normally. All keyed
            # writes also fail closed (503) reading it, so old requests cannot restore content; no completion marker until repaired.
            logger.warning(f"[scoped_forget] {lanlan_name}: 墓碑文件不可读，跳过墓碑照常擦除: {exc}")
        except Exception as exc:
            logger.error(f"[scoped_forget] {lanlan_name}: 墓碑落盘失败: {exc}")
            raise HTTPException(
                status_code=500,
                detail="scoped forget failed; retry is safe and idempotent",
            ) from exc
        # This or newer generation already erased: do not erase again on replay/late old clear,
        # which would delete subsequent valid new-generation memory. Still erase newly linked fanout targets
        # carrying generations not yet erased; erase only those. Recheck after acquiring erasure transaction lock.
        erase_targets = await _targets_still_to_erase(
            lanlan_name, subject, targets, req.forget_epoch, fanout_epochs,
        )
        if not erase_targets:
            return await _forget_duplicate_after_cancel(
                lanlan_name, subject, targets, effective_fence, effective_fanout_fences,
            )
    erase_keys = _erase_subject_keys(
        subject, targets, erase_targets, forgotten_subject_keys, effective_fanout_fences,
    )
    # Before erasure, cancel existing keyed staging/records once (no other locks held, so no cycle
    # with same-key application). Later retries get duplicate, unable to race between erasure and
    # the cancellation scan below, restoring old output with post-clear generation.
    try:
        if default_scope:
            # Keyed writes support default scope only; clears of other scopes must not wrongly cancel by kind:id.
            await _cancel_staged_writes_for_subjects(
                lanlan_name, erase_keys,
                request_subject_key=subject.key, forget_epoch=effective_fence,
                fanout_fences=effective_fanout_fences, best_effort=True,
            )
    except MaintenanceModeError:
        raise
    except Exception as exc:
        # Pre-erasure pass is best effort (per-file/key tolerance; skip only failures): one unreadable/unwritable staging file cannot block unrelated privacy clear
        # (including legacy callers never using keyed writes). Post-erasure cancellation and tombstone/generation provide the real fallback;
        # only that pass's failure returns an error for caller retry.
        logger.warning(f"[scoped_forget] {lanlan_name}: 擦除前取消带键暂存未完成（擦除后再取消一遍）: {exc}")
    stats: dict = {}
    fact_forget_started: list = []
    reflection_forget_started: list = []
    acquired_locks: list = []
    # Component references are atomically replaced under this lock. Keep the
    # same generation alive until every tombstone is closed, otherwise reload
    # can split one forget transaction across old and new managers.
    #
    # ONE TRANSACTION FOR ALL TARGETS, not N independent ones. Splitting would
    # break two things at once: (a) ``_reload_lock`` would be released between
    # subjects, letting a reload cut in; (b) subject i's tombstone would close
    # BEFORE subject i+1's opens, and fact extraction / reflection synthesis
    # release their locks during LLM calls — an in-flight write captured before
    # the forget could then land after its tombstone closed, growing data back
    # inside a domain that was just erased. This is a privacy path; ordering
    # arguments ("delete canonical last") are not enough.
    await runtime._reload_lock.acquire()
    try:
        for target in erase_targets:
            lock = runtime.fact_store._get_subject_forget_transaction_lock(
                lanlan_name, target,
            )
            await lock.acquire()
            acquired_locks.append(lock)
        # Concurrent older/newer clears may both pass unlocked checks; newer erases/releases lock,
        # then older acquires it. Recheck under lock to preserve valid writes after newer clearing.
        if req.forget_epoch is not None:
            # Erased markers only increase: targets still requiring erasure under lock are a subset calculated outside it.
            still = await _targets_still_to_erase(
                lanlan_name, subject, targets, req.forget_epoch, fanout_epochs,
            )
            locked = {(target.key, target.scope) for target in erase_targets}
            still = [target for target in still if (target.key, target.scope) in locked]
            if not still:
                # After releasing lock, run the outer cancellation pass too, cancelling pre-clear staging written while waiting for lock.
                raise _ForgetAlreadyErased(
                    lanlan_name, subject, targets, effective_fence, effective_fanout_fences,
                )
            if len(still) != len(erase_targets):
                erase_targets = still
                erase_keys = _erase_subject_keys(
                    subject, targets, erase_targets, forgotten_subject_keys, effective_fanout_fences,
                )
        if req.forget_epoch is not None:
            # Between unlocked fence read and erasure lock, another clear may raise a target fence (fanout clears are not
            # protected by this request's forget_fence). This erasure follows it; completion/cancellation use the last fence
            # present before erasure, increasing only.
            try:
                fences_now = await idempotency.read_tombstones(lanlan_name)
            except idempotency.IdempotencyStateError:
                fences_now = None
            if fences_now is not None:
                fence = _recorded_fence(fences_now, subject.key)
                if fence is not None and (fence_at_start is None or fence > fence_at_start):
                    fence_at_start = fence
                    effective_fence = max(effective_fence, fence)
                _raise_fanout_fences(fences_now, effective_fanout_fences, fanout_fence_at_start)
        # ALL tombstones open before ANY erase.
        for target in erase_targets:
            await runtime.fact_store.abegin_subject_forget(lanlan_name, target)
            fact_forget_started.append(target)
        for target in erase_targets:
            await runtime.reflection_engine.abegin_subject_forget(
                lanlan_name, target,
            )
            reflection_forget_started.append(target)
        for target in erase_targets:
            # SUM, never replace. Every store returns the same counter keys, so
            # a per-target ``update`` reports only the LAST account's numbers —
            # if the first account deleted rows and a later one deleted none,
            # the endpoint would report zero for data it just erased. This is a
            # privacy operation whose response is the operator's only receipt.
            _merge_forget_stats(
                stats,
                await runtime.fact_dedup_resolver.aforget_subject(
                    lanlan_name, target,
                ),
            )
            _merge_forget_stats(
                stats,
                await runtime.fact_store.aforget_subject(lanlan_name, target),
            )
            _merge_forget_stats(
                stats,
                await runtime.reflection_engine.aforget_subject(
                    lanlan_name, target,
                ),
            )
            _merge_forget_stats(
                stats,
                await runtime.persona_manager.aforget_subject(
                    lanlan_name, target,
                ),
            )
            _merge_forget_stats(stats, {
                "prompt_locale": await asyncio.to_thread(
                    locale_state.forget_subject_prompt_locale,
                    lanlan_name,
                    target,
                ),
            })
        # Reflection/persona archive writers take their store locks. Their
        # forget calls above therefore drain any writer that had already
        # snapshotted this subject. Advance the persistent cutoff only now,
        # while every write tombstone is still open, so a snapshot archived
        # after the initial facts erase cannot become restore-eligible.
        #
        # The cutoff is PER SUBJECT and persistent
        # (``subject_forget_tombstones.json``). Skipping any target's cutoff
        # would leave that account's archived shards restorable through event
        # replay — i.e. a "left the group, wiped my data" request that quietly
        # keeps a copy.
        for target in erase_targets:
            await runtime.fact_store.afinalize_subject_forget(
                lanlan_name, target,
            )
    except _ForgetAlreadyErased:
        raise
    except Exception as exc:
        logger.error(f"[scoped_forget] {lanlan_name}: 删除失败: {exc}")
        raise HTTPException(
            status_code=500,
            detail="scoped forget failed; retry is safe and idempotent",
        ) from exc
    finally:
        try:
            try:
                for target in reversed(reflection_forget_started):
                    await runtime.reflection_engine.aend_subject_forget(
                        lanlan_name, target,
                    )
            finally:
                # Never strand a fact-write tombstone if the independent
                # reflection close encounters an unexpected failure.
                for target in reversed(fact_forget_started):
                    await runtime.fact_store.aend_subject_forget(
                        lanlan_name, target,
                    )
        finally:
            for lock in reversed(acquired_locks):
                lock.release()
            runtime._reload_lock.release()
    # Cancel keyed staging involving these subjects only after erasure completes and all locks above release.
    # Cancellation acquires each key lock; applying requests hold those locks while awaiting fact-pool persistence
    # lock (held by this handler during erasure). Awaiting key locks within erasure transactions would
    # deadlock; here no other locks are held while waiting, avoiding cycles.
    try:
        if default_scope:
            await _cancel_staged_writes_for_subjects(
                lanlan_name, erase_keys,
                request_subject_key=subject.key, forget_epoch=effective_fence,
                fanout_fences=effective_fanout_fences,
            )
    except MaintenanceModeError:
        raise
    except Exception as exc:
        logger.error(f"[scoped_forget] {lanlan_name}: 取消带键暂存失败: {exc}")
        raise HTTPException(
            status_code=500,
            detail="scoped forget failed; retry is safe and idempotent",
        ) from exc
    if req.forget_epoch is not None:
        from . import idempotency

        try:
            # Mark generation erased only after erasure and both cancellation passes finish. Mark fanout first, request subject last:
            # duplicate-clear shortcut checks only request subject completion, which implies every target marker already persisted.
            erased_keys = {target.key for target in erase_targets}
            # Partial erasure skipped request subject/already-erased targets: do not mark targets erased using higher existing fences.
            # That higher-generation clear (requesting this target and fanning out to the whole participant) has not finished;
            # borrowing it would make retry take duplicate shortcut, missing intervening writes from other participant accounts.
            full_erase = len(erase_targets) == len(targets)
            for target_key, target_epoch in sorted((fanout_epochs or {}).items()):
                if target_key not in erased_keys:
                    continue
                await idempotency.mark_tombstone_erased(
                    lanlan_name, target_key, target_epoch,
                    covered_epoch=fanout_fence_at_start.get(target_key) if full_erase else None,
                )
            if subject.key in erased_keys:
                # When only additional fanout targets erased, request subject stayed untouched; existing completion already covers forget_epoch.
                # Do not use covered_epoch to mark higher fences that failed before erasure, or that erasure can never be retried.
                await idempotency.mark_tombstone_erased(
                    lanlan_name, subject.key, req.forget_epoch, covered_epoch=fence_at_start,
                )
        except MaintenanceModeError:
            raise
        except idempotency.IdempotencyStateError as exc:
            # Unreadable tombstones/corrupt row: erasure finished but generation completion cannot persist. Return 503 for
            # retries until repaired and marked; keyed writes for this subject also return 503 reading tombstones meanwhile.
            logger.warning(f"[scoped_forget] {lanlan_name}: 墓碑损坏，未记擦除完成: {exc}")
            raise HTTPException(
                status_code=503,
                detail="scoped forget erased but its completion marker could not be recorded; retry",
            ) from exc
        except Exception as exc:
            # Erasure complete but generation-complete marker not persisted: success would stop retries, allowing same/older generation
            # replay to erase subsequent valid memory again. Return error until completion persists.
            logger.error(f"[scoped_forget] {lanlan_name}: 记录擦除完成失败: {exc}")
            raise HTTPException(
                status_code=500,
                detail="scoped forget failed; retry is safe and idempotent",
            ) from exc
    return {
        "status": "forgotten",
        "subject": subject.as_entry_fields(),
        "forgotten_subjects": [
            target.as_entry_fields() for target in targets
        ],
        # Subjects actually erased this time: replay filling newly linked targets erases fewer than forgotten_subjects;
        # privacy clear's sole receipt must distinguish additional erasure from complete erasure.
        "erased_subjects": [
            target.as_entry_fields() for target in erase_targets
        ],
        **stats,
    }


def _validate_target_epochs(req: ScopedForgetRequest, subject) -> None:
    """422 for a ``target_epochs`` map that cannot be honoured as sent."""
    target_epochs = req.target_epochs
    if target_epochs is None:
        return
    if req.forget_epoch is None:
        raise HTTPException(status_code=422, detail="target_epochs requires forget_epoch")
    if len(target_epochs) > _FORGET_TARGET_EPOCHS_MAX or any(
        not key or len(key) > 512 or not key.isprintable() for key in target_epochs
    ):
        raise HTTPException(status_code=422, detail="invalid target_epochs keys")
    if subject.key in target_epochs and target_epochs[subject.key] != req.forget_epoch:
        # Request subject generation has one source: differing values at the two locations leave no trustworthy choice.
        raise HTTPException(status_code=422, detail="target_epochs disagrees with forget_epoch")


def _fanout_target_epochs(req: ScopedForgetRequest, subject, targets) -> dict[str, int] | None:
    """``{fan-out target key: its own epoch}``, or None when the caller did not opt in.

    Raises 409 (before anything is written or erased) listing every fan-out
    target the caller gave no epoch for: the server cannot pick one for it,
    since a copied or guessed epoch either blocks that subject's own later
    writes or lets its delayed pre-forget writes through.
    """
    if req.target_epochs is None:
        return None
    fanout_keys = sorted({target.key for target in targets} - {subject.key})
    missing = [key for key in fanout_keys if key not in req.target_epochs]
    if missing:
        raise HTTPException(
            status_code=409,
            detail={"error": "missing_target_epochs", "missing_target_epochs": missing},
        )
    # Extra keys outside fanout (unbound between requests) get no tombstones: outside this erasure's scope.
    return {key: int(req.target_epochs[key]) for key in fanout_keys}


async def _targets_still_to_erase(lanlan_name: str, subject, targets, forget_epoch, fanout_epochs) -> list:
    """The fan-out targets this forget still has to erase (empty: a duplicate).

    Each target is judged by its own completion marker: the request subject
    and every target the caller gave no epoch for go with the request
    subject's marker for ``forget_epoch``; a target the caller gave its own
    epoch for is erased only while its own marker is below that epoch. So a
    replay naming an account linked after the original erase erases just
    that account, and a retry after a partly recorded completion does not
    erase again a target whose marker already landed (either would delete
    writes made legitimately after that erase). An unreadable tombstone file
    counts as "not erased" (bias towards deleting).
    """
    from . import idempotency

    if forget_epoch is None:
        return list(targets)
    request_done = await _forget_epoch_already_erased(lanlan_name, subject.key, forget_epoch)
    if request_done and not fanout_epochs:
        return []
    tombstones: dict = {}
    if fanout_epochs:
        try:
            tombstones = await idempotency.read_tombstones(lanlan_name)
        except idempotency.IdempotencyStateError:
            tombstones = {}
    pending = []
    for target in targets:
        epoch = None if target.key == subject.key else (fanout_epochs or {}).get(target.key)
        if epoch is None:
            # Request subject and fanout targets without generations follow request subject's completion marker.
            if not request_done:
                pending.append(target)
            continue
        done = idempotency.erased_epoch(tombstones, target.key)
        if done is None or done < epoch:
            pending.append(target)
    return pending


def _erase_subject_keys(
    subject, targets, erase_targets, forgotten_subject_keys: set[str], fenced_keys,
) -> set[str]:
    """Subject keys the cancellation passes match for this erase.

    The whole fan-out for a full erase. For a partial one (some targets
    were erased already) the erase scope is narrower, but cancellation is
    not: the request subject, the targets erased now, and every key with a
    comparable fence (``fenced_keys``), the same set the duplicate path
    cancels for. ``fenced_keys`` also holds keys the caller named that left
    the fan-out but were fenced by an earlier erase. A pre-forget journal
    of an already erased key that staged after its marker landed is
    cancelled too, while a fresh one survives the per-key fence comparison.
    """
    if len(erase_targets) == len(targets):
        return set(forgotten_subject_keys) | set(fenced_keys)
    return {subject.key} | {target.key for target in erase_targets} | set(fenced_keys)


def _retained_target_keys(req: ScopedForgetRequest, subject, targets) -> list[str]:
    """Keys the caller gave an epoch for that are no longer in the current fan-out (unlinked since)."""
    current = {target.key for target in targets} | {subject.key}
    return sorted(set(req.target_epochs or {}) - current)


def _raise_fanout_fences(tombstones, effective: dict[str, int], at_start: dict[str, int]) -> None:
    """Raise each fan-out target's fences to the tombstone epoch now stored for it (never lowers)."""
    for target_key in list(effective):
        fence = _recorded_fence(tombstones, target_key)
        if fence is None:
            continue
        if fence > at_start.get(target_key, -1):
            at_start[target_key] = fence
        if fence > effective[target_key]:
            effective[target_key] = fence


def _recorded_fence(recorded, subject_key: str) -> int | None:
    """The tombstone epoch now stored for ``subject_key`` (None when absent or damaged)."""
    row = recorded.get(subject_key) if isinstance(recorded, dict) else None
    fence = row.get("forget_epoch") if isinstance(row, dict) else None
    if isinstance(fence, int) and not isinstance(fence, bool) and fence >= 0:
        return fence
    return None


async def _forget_epoch_already_erased(lanlan_name: str, subject_key: str, forget_epoch: int) -> bool:
    """Whether the erase of ``forget_epoch`` (or a newer one) already completed; unreadable reads as no."""
    from . import idempotency

    try:
        done = idempotency.erased_epoch(await idempotency.read_tombstones(lanlan_name), subject_key)
    except idempotency.IdempotencyStateError:
        # On unreadability erase normally (favor extra deletion).
        return False
    return done is not None and done >= forget_epoch


class _ForgetAlreadyErased(Exception):
    """Raised under the erase locks when the epoch turns out to be erased already."""

    def __init__(self, lanlan_name, subject, targets, effective_fence, fanout_fences=None) -> None:
        super().__init__("forget epoch already erased")
        self.args_for_cancel = (lanlan_name, subject, targets, effective_fence, fanout_fences)


async def _forget_duplicate_after_cancel(
    lanlan_name, subject, targets, effective_fence, fanout_fences=None,
) -> dict:
    """Answer an already-erased forget after cancelling pre-forget staging of the fenced subjects.

    The store is not erased again, but a pre-forget keyed request that arrived
    after the original erase may have staged plaintext and crashed; pending,
    it is never swept by the TTL. Only subjects with a comparable epoch are
    matched: the request subject, plus each fan-out target the caller gave
    its own epoch for (``fanout_fences``). A target without one is left
    alone: its later keyed write carries an epoch from another counter that
    cannot be compared to this forget's, and is legitimate.
    """
    fanout_fences = dict(fanout_fences or {})
    try:
        await _cancel_staged_writes_for_subjects(
            lanlan_name, {subject.key} | set(fanout_fences),
            request_subject_key=subject.key, forget_epoch=effective_fence,
            fanout_fences=fanout_fences,
        )
    except MaintenanceModeError:
        raise
    except Exception as exc:
        logger.error(f"[scoped_forget] {lanlan_name}: 重复清除取消带键暂存失败: {exc}")
        raise HTTPException(
            status_code=500,
            detail="scoped forget failed; retry is safe and idempotent",
        ) from exc
    return _forget_duplicate_response(subject, targets)


async def _run_forget(lanlan_name: str, req: ScopedForgetRequest, *, fenced_keys=None):
    try:
        return await _forget_scoped_subject(lanlan_name, req, fenced_keys=fenced_keys)
    except _ForgetAlreadyErased as done:
        return await _forget_duplicate_after_cancel(*done.args_for_cancel)


def _forget_duplicate_response(subject, targets) -> dict:
    return {
        "status": "forgotten",
        "subject": subject.as_entry_fields(),
        "forgotten_subjects": [target.as_entry_fields() for target in targets],
        "erased_subjects": [],
        "duplicate": True,
    }


def _read_json_list_for_listing(path: str) -> list:
    """Write-free read of one JSON list file for the listing endpoint (never creates directories)."""
    from utils.file_utils import read_json_tolerating_replace

    if not os.path.exists(path):
        return []
    try:
        # Tolerate concurrent os.replace sharing conflicts on Windows, avoiding subjects appearing empty or disappearing.
        data = read_json_tolerating_replace(path)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError, RecursionError) as exc:
        logger.warning(f"[scoped_subjects] {os.path.basename(path)} 读取失败，按空处理: {exc}")
        return []
    return data if isinstance(data, list) else []


def _read_locale_subjects_for_listing(path: str, lanlan_name: str) -> list:
    """Write-free read of the subjects that have a live scoped prompt-locale row."""
    from memory.scopes import subject_from_entry
    from utils.file_utils import read_json_tolerating_replace

    if not os.path.exists(path):
        return []
    try:
        data = read_json_tolerating_replace(path)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError, RecursionError) as exc:
        logger.warning(f"[scoped_subjects] scoped_prompt_locales.json 读取失败，按空处理: {exc}")
        return []
    rows = data.get("subjects") if isinstance(data, dict) else None
    live = None
    if isinstance(rows, dict):
        try:
            # Filter cleared rows still on disk (crash after cutoff write, before sidecar rewrite) as the loader does,
            # or cleared subjects keep appearing via prompt_locale and resist repeated clearing.
            live = locale_state.live_subject_locale_keys(lanlan_name, rows)
        except locale_state.PromptLocalePersistenceError as exc:
            logger.warning(f"[scoped_subjects] 语言清除 cutoff 读不出，按全部列出: {exc}")
    subjects = []
    for key, row in (rows.items() if isinstance(rows, dict) else ()):
        if not isinstance(key, str) or not isinstance(row, dict):
            continue
        if live is not None and key not in live:
            continue
        try:
            parts = json.loads(key)
        except (json.JSONDecodeError, RecursionError):
            continue
        if not isinstance(parts, list) or len(parts) != 3 or not all(isinstance(p, str) for p in parts):
            continue
        subject = subject_from_entry({"subject_kind": parts[0], "subject_id": parts[1], "scope": parts[2]})
        if subject is not None:
            subjects.append(subject)
    return subjects


def _read_staged_subjects_for_listing(directory: str) -> list:
    """Write-free read of the subjects named by pending keyed staging journals."""
    from memory.scopes import subject_from_entry

    from .idempotency import _list_staging_sync

    from .idempotency import IDEMPOTENCY_KEYS_FILENAME, TERMINAL_KEY_STATES, key_digest

    # Look up key records by filename: terminal done/cancelled staging left by failed cleanup has no unapplied work,
    # so must not appear staged. Orphans can be claimed on retry: list normally; unreadable records list all (favor inclusion).
    terminal_files: set[str] = set()
    from utils.file_utils import read_json_tolerating_replace

    try:
        records = read_json_tolerating_replace(os.path.join(os.path.dirname(directory), IDEMPOTENCY_KEYS_FILENAME))
    except (OSError, ValueError, RecursionError):
        records = {}
    if isinstance(records, dict):
        for key, record in records.items():
            # Recognize explicit terminal states only: missing/corrupt state fails closed on write; staging may hold sole plaintext, so list it.
            state = record.get("state") if isinstance(record, dict) else None
            if isinstance(key, str) and key and isinstance(state, str) and state in TERMINAL_KEY_STATES:
                terminal_files.add(f"{key_digest(key)}.json")
    subjects = []
    for path, document, _mtime in _list_staging_sync(directory):
        if not isinstance(document, dict) or document.get(_KEYED_STAGING_CANCELLED) is True:
            continue
        if os.path.basename(path) in terminal_files:
            continue
        segments = document.get("segments")
        for segment in segments if isinstance(segments, list) else []:
            if isinstance(segment, dict) and segment.get(_SEGMENT_DROPPED_BY_FORGET) is True:
                # Cleared/discarded segments (content erased) have no pending memory: do not make cleared entities reappear.
                continue
            raw = segment.get("subject") if isinstance(segment, dict) else None
            subject = subject_from_entry(raw) if isinstance(raw, dict) else None
            if subject is not None:
                subjects.append(subject)
    return subjects


def _read_correction_subjects_for_listing(path: str) -> list:
    """Write-free read of the subjects owning a pending persona correction (one entry per row).

    Same attribution as ``PersonaManager.aforget_subject``: the subject stamp,
    or for older unstamped rows the ``@subject/<kind>:<id>`` entity.
    """
    from memory.scopes import SCOPED_PERSONA_PREFIX, MemoryScopeError, MemorySubject, subject_from_entry
    from utils.file_utils import read_json_tolerating_replace

    if not os.path.exists(path):
        return []
    try:
        data = read_json_tolerating_replace(path)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError, RecursionError) as exc:
        logger.warning(f"[scoped_subjects] persona_corrections.json 读取失败，按空处理: {exc}")
        return []
    subjects = []
    for correction in data if isinstance(data, list) else []:
        if not isinstance(correction, dict):
            continue
        subject = subject_from_entry(correction)
        if subject is None:
            entity_raw = correction.get("entity")
            entity = entity_raw.strip() if isinstance(entity_raw, str) else ""
            if not entity.startswith(SCOPED_PERSONA_PREFIX):
                continue
            body = entity[len(SCOPED_PERSONA_PREFIX):]
            kind, _, subject_id = body.partition(":")
            scope = correction.get("scope")
            try:
                subject = MemorySubject.create(kind, subject_id, scope=scope if isinstance(scope, str) and scope else body)
            except MemoryScopeError:
                continue
        subjects.append(subject)
    return subjects


def _read_persona_for_listing(path: str) -> dict:
    """Strict, write-free persona read for the listing endpoint."""
    from utils.file_utils import read_json_tolerating_replace

    if not os.path.exists(path):
        return {}
    try:
        data = read_json_tolerating_replace(path)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError, RecursionError) as exc:
        logger.warning(f"[scoped_subjects] persona 读取失败，按无 persona 处理: {exc}")
        return {}
    return data if isinstance(data, dict) else {}


_FORGET_EPOCHS_MAX_SUBJECTS = 64


@app.get("/internal/memory/{lanlan_name}/forget_epochs")
async def get_forget_epochs(lanlan_name: str, subject: list[str] = Query(default=[])):
    """Current forget-epoch fence of each requested subject key (read-only).

    Answers ``{"epochs": {subject_key: forget_epoch}}`` for the keys that
    carry a tombstone; keys without one are left out. A client whose local
    epoch counter was reset (cloud restore, new installation) raises it to
    this value before opening a keyed write or sending a forget, so neither
    falls below a restored tombstone. A tombstone file or row that cannot be
    read answers 503: an unknown fence must not read as "no fence".
    """
    from . import idempotency

    lanlan_name = validate_lanlan_name(lanlan_name)
    if runtime._config_manager is None:
        raise HTTPException(
            status_code=503,
            detail="memory_server not fully initialized (limited mode or startup incomplete)",
        )
    if len(subject) > _FORGET_EPOCHS_MAX_SUBJECTS or any(
        not key or len(key) > 512 or not key.isprintable() for key in subject
    ):
        raise HTTPException(status_code=422, detail="invalid subject keys")
    try:
        tombstones = await idempotency.read_tombstones(lanlan_name)
        epochs = {}
        for key in dict.fromkeys(subject):
            fence = idempotency.tombstone_epoch(tombstones, [key])
            if fence is not None:
                epochs[key] = fence
    except idempotency.IdempotencyStateError as exc:
        raise HTTPException(status_code=503, detail="forget epochs unreadable; retry later") from exc
    return {"epochs": epochs}


@app.get("/internal/memory/{lanlan_name}/scoped_subjects")
async def list_scoped_subjects(lanlan_name: str, platform: str):
    """List the scoped subjects stored for one platform (read-only).

    Filters on the platform component of ``subject_id`` (its first
    ``:``-separated segment) for every subject kind, NOT on a raw key
    prefix: a ``participant`` id ``neko_visit:<uid>`` and a ``group_chat``
    id ``neko_visit:<pair>`` share their prefix, and ``neko_visit_x:...``
    must not match ``neko_visit``. Read-only: persona is read straight from
    disk instead of through ``aensure_persona`` (which recovers and saves a
    malformed file), and a character without a directory answers an empty
    list without creating one. Not part of the write fence (GET is never in
    ``_CHARACTER_SCOPED_WRITE_OPS``).
    """
    from memory.scopes import (
        SCOPED_PERSONA_PREFIX,
        entry_matches_subject,
        persona_subject_from_section,
        subject_from_entry,
    )
    from memory.subject_archive import collect_subject_last_writes

    lanlan_name = validate_lanlan_name(lanlan_name)
    if runtime._config_manager is None:
        # Listing reads disk directly (facts / reflections / persona), depending only on config manager.
        raise HTTPException(
            status_code=503,
            detail="memory_server not fully initialized (limited mode or startup incomplete)",
        )
    platform = (platform or "").strip()
    if not platform or ":" in platform or len(platform) > 64:
        raise HTTPException(status_code=422, detail="invalid platform")
    character_dir = os.path.join(
        str(runtime._config_manager.memory_dir), lanlan_name,
    )
    if not await asyncio.to_thread(os.path.isdir, character_dir):
        return {"subjects": []}

    def _platform_of(subject) -> str:
        return subject.subject_id.split(":", 1)[0]

    # Read disk directly, bypassing store loaders whose path lookup calls ensure_character_dir:
    # this GET has no write fence; concurrent delete/rename would recreate just-deleted character directories.
    active_facts = await asyncio.to_thread(
        _read_json_list_for_listing, os.path.join(character_dir, "facts.json"),
    )
    archived_facts = await asyncio.to_thread(
        _read_json_list_for_listing, os.path.join(character_dir, "facts_archive.json"),
    )
    def _fact_id(fact: dict):
        # Old/manual files may have unhashable list/object IDs: accept strings/integers only;
        # treat others as no ID, preventing one corrupt row from making the entire listing return 500.
        fid = fact.get("id")
        if isinstance(fid, bool) or not isinstance(fid, (str, int)):
            return None
        return fid

    def _identity(fact: dict):
        # Same full identity as fact layer: (id, subject_kind, subject_id, scope);
        # identical bare IDs across different subjects/scopes must not suppress one another.
        fid = _fact_id(fact)
        if fid is None:
            return None
        parts = (fact.get("subject_kind"), fact.get("subject_id"), fact.get("scope"))
        # Manually corrupted list/object identity fields are unhashable: treat as no identity, excluding from deduplication.
        if any(part is not None and not isinstance(part, str) for part in parts):
            return None
        return (fid, *parts)

    active_fact_ids = {
        _identity(fact)
        for fact in active_facts
        if isinstance(fact, dict) and _identity(fact) is not None
    }
    # Deduplicate IDs like load_facts_full: archiving writes facts_archive.json first, then
    # facts.json; interruption between steps temporarily puts one fact in both files.
    active_rows = [fact for fact in active_facts if isinstance(fact, dict)]
    # Activity depends on source file, not ID: active facts with corrupt IDs are not archive-only.
    active_row_refs = {id(fact) for fact in active_rows}
    facts_full = active_rows + [
        fact for fact in archived_facts
        if isinstance(fact, dict)
        and (_identity(fact) is None or _identity(fact) not in active_fact_ids)
    ]
    reflections_path = os.path.join(character_dir, "reflections.json")
    from memory.reflection._shared import REFLECTION_TERMINAL_STATUSES

    # List all stored reflections, including terminal promoted/denied/merged states and missing/corrupt IDs:
    # still on disk and in scoped_forget's deletion scope (subject-based, not ID-based). Subjects containing only these
    # must remain discoverable/clearable, so bypass the active loader's ID filtering.
    reflections = [
        row for row in await asyncio.to_thread(_read_json_list_for_listing, reflections_path)
        if isinstance(row, dict)
    ]
    persona = await asyncio.to_thread(
        _read_persona_for_listing,
        os.path.join(character_dir, "persona.json"),
    )
    locale_subjects = await asyncio.to_thread(
        _read_locale_subjects_for_listing,
        os.path.join(character_dir, "scoped_prompt_locales.json"),
        lanlan_name,
    )
    correction_subjects = await asyncio.to_thread(
        _read_correction_subjects_for_listing,
        os.path.join(character_dir, "persona_corrections.json"),
    )
    from .idempotency import STAGING_DIRNAME

    staged_subjects = await asyncio.to_thread(
        _read_staged_subjects_for_listing, os.path.join(character_dir, STAGING_DIRNAME),
    )

    rows: dict[tuple[str, str], dict] = {}

    def _row(subject) -> dict | None:
        if _platform_of(subject) != platform:
            return None
        marker = (subject.key, subject.scope)
        row = rows.get(marker)
        if row is None:
            row = {
                "subject": subject,
                "display_name": None,
                "facts": 0,
                "active_facts": 0,
                "reflections": 0,
                "active_reflections": 0,
                "persona": False,
                "prompt_locale": False,
                "corrections": 0,
                "staged": False,
            }
            rows[marker] = row
        return row

    for fact in facts_full:
        if not isinstance(fact, dict):
            continue
        subject = subject_from_entry(fact)
        row = _row(subject) if subject is not None else None
        if row is None:
            continue
        row["facts"] += 1
        if id(fact) in active_row_refs and not fact.get("subject_archived_at"):
            row["active_facts"] += 1
    for reflection in reflections:
        if not isinstance(reflection, dict):
            continue
        subject = subject_from_entry(reflection)
        row = _row(subject) if subject is not None else None
        if row is not None:
            row["reflections"] += 1
            status = reflection.get("status")
            # Corrupt unhashable states (list/object) count as nonterminal, preventing one bad reflection from breaking listing with 500.
            if not isinstance(status, str) or status not in REFLECTION_TERMINAL_STATUSES:
                row["active_reflections"] += 1
    persona_entries: list[dict] = []
    for section_key, section in persona.items():
        if not isinstance(section, dict) or not str(section_key).startswith(
            SCOPED_PERSONA_PREFIX,
        ):
            continue
        section_subject = persona_subject_from_section(section_key, section)
        raw_entries = section.get("facts")
        # A section's corrupt non-list facts count as empty, never breaking the entire listing with 500.
        entries = [
            entry for entry in (raw_entries if isinstance(raw_entries, list) else [])
            if isinstance(entry, dict)
        ]
        persona_entries.extend(entries)
        # Section keys exclude scope: different scopes sharing kind:id may share a section, whose
        # metadata tracks only latest writer. Record each item's own subject stamp, or persona-only subjects in other scopes disappear.
        for entry in entries:
            entry_subject = subject_from_entry(entry)
            entry_row = _row(entry_subject) if entry_subject is not None else None
            if entry_row is not None:
                entry_row["persona"] = True
        if section_subject is None:
            continue
        has_entries = any(
            entry_matches_subject(entry, section_subject) for entry in entries
        )
        display_name = section.get("display_name")
        if not has_entries and not display_name:
            continue
        row = _row(section_subject)
        if row is None:
            continue
        if has_entries:
            row["persona"] = True
        if isinstance(display_name, str) and display_name:
            row["display_name"] = display_name
    # Language-only subjects (no extracted facts, or interrupted keyed generation leaving reserved language rows) also fall
    # within scoped_forget deletion scope and must be discoverable/clearable.
    for locale_subject in locale_subjects:
        row = _row(locale_subject)
        if row is not None:
            row["prompt_locale"] = True
    # Pending persona corrections also fall within deletion scope (resolution can restore deleted persona entries); subjects with only these
    # must remain discoverable/clearable.
    for correction_subject in correction_subjects:
        row = _row(correction_subject)
        if row is not None:
            row["corrections"] += 1
    # Staging left by crashes after keyed generation but before first application may be a subject's sole data: clear targets it,
    # and same-key retries can still apply it, so it must remain discoverable too.
    for staged_subject in staged_subjects:
        row = _row(staged_subject)
        if row is not None:
            row["staged"] = True
    last_writes, _no_timestamp = collect_subject_last_writes(
        [facts_full, reflections, persona_entries],
    )
    subjects = []
    for marker in sorted(rows, key=lambda m: (rows[m]["subject"].kind, m)):
        row = rows[marker]
        subject = row["subject"]
        last = last_writes.get(marker)
        subjects.append({
            "subject_kind": subject.kind,
            "subject_id": subject.subject_id,
            "scope": subject.scope,
            "display_name": row["display_name"],
            "facts": row["facts"],
            "reflections": row["reflections"],
            "persona": row["persona"],
            "prompt_locale": row["prompt_locale"],
            "corrections": row["corrections"],
            "staged": row["staged"],
            "last_write_at": last[1].isoformat() if last is not None else None,
            # Facts remain only in archives; no active facts / reflections / persona.
            # Terminal reflections (promoted / denied, etc.) are listed/counted in reflections but excluded from active data.
            "archived": (
                row["facts"] > 0
                and row["active_facts"] == 0
                and row["active_reflections"] == 0
                and not row["persona"]
            ),
        })
    return {"subjects": subjects}


# ── trust pool / identity endpoints ─────────────────────────────────────────
# All character-agnostic (precedent: /internal/memory/import_external_markdown)
# and NONE of them is in ``_STORAGE_LIMITED_MODE_ALLOWED_PATHS``: before the
# runtime is ready they answer 409 ``storage_startup_blocked``. A caller must
# retry that, and must NEVER read it as "this user has no trust".


class TrustLegacyImportRequest(BaseModel):
    source: str
    platform: str
    chunk_index: int = Field(default=0, ge=0)
    final: bool = False
    # Deliberately ``dict``, not a strict sub-model: the legacy normalizer this
    # replaces was per-field tolerant, and all-or-nothing validation would let a
    # single dirty profile 422 the whole request — which wedges the migration
    # permanently, because a 422 never succeeds on retry either.
    profiles: dict = Field(default_factory=dict)


class TrustWaiveBarrierRequest(BaseModel):
    platform: str


class TrustReconcileRequest(BaseModel):
    character_names: list[str] | None = None


class IdentityBindRequest(BaseModel):
    account_id: str
    entity_id: str
    bound_by: str | None = None
    require_unbound: bool = False


class IdentityAccountRequest(BaseModel):
    account_id: str
    require_provenance: bool = False


class IdentityMergeRequest(BaseModel):
    entity_id: str
    other_entity_id: str


class IdentityScopeDeclareRequest(BaseModel):
    platform: str
    channel: str
    actor_scope: str
    conversation_scope: str
    asserted_by: str


class IdentityEntityRequest(BaseModel):
    entity_id: str


def _identity_error(exc) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


def _require_loaded_identity_pool() -> None:
    """Refuse identity mutations while the pool is read-only degraded.

    ``_with_pool_write`` vetoes the write and returns ``persisted=False``, but
    the endpoints would still answer 200 — so a human-triggered bind/unbind/
    merge/forget silently becomes a no-op that reads as success. For unbind it
    is worse than a no-op: ``_count_stranded_rows`` also resolves nothing on an
    unloaded snapshot, so the operator's only remediation signal comes back as
    a confident ``0``.

    Same fail-closed rule already applied to ``scoped_forget``.
    """
    from memory import trust_store

    if not trust_store.trust_snapshot().loaded:
        raise HTTPException(
            status_code=503,
            detail=(
                "identity pool unreadable; identity changes are refused while "
                "the trust pool is read-only, retry once it loads"
            ),
        )


@app.get("/internal/trust/profile")
async def get_trust_profile(account_id: str):
    """Read-only diagnostics for one account. Never returns the ledger rings."""
    from memory import trust_store

    return trust_store.trust_snapshot().profile(account_id)


@app.post("/internal/trust/import_legacy_profiles")
async def import_legacy_trust_profiles(req: TrustLegacyImportRequest):
    """Import one chunk of a platform's legacy trust ledger.

    Additive merge, idempotent per (source, account_id). Safe to merge rather
    than overwrite ONLY because the barrier guarantees this platform had zero
    server-side evolution beforehand — that is the barrier's entire purpose.
    A malformed profile lands in ``skipped``; the request never 422s as a whole.
    """
    from config import SPEAKER_TRUST_LEGACY_IMPORT_CHUNK_MAX
    from memory import trust_store

    if not re.fullmatch(r"[A-Za-z0-9_.-]+", req.platform or ""):
        raise HTTPException(status_code=422, detail="invalid platform")
    if not (req.source or "").strip():
        raise HTTPException(status_code=422, detail="source is required")
    if len(req.profiles) > SPEAKER_TRUST_LEGACY_IMPORT_CHUNK_MAX:
        # Chunking is the caller's job; exceeding it is a contract bug.
        raise HTTPException(
            status_code=422,
            detail=(
                f"profiles must contain at most "
                f"{SPEAKER_TRUST_LEGACY_IMPORT_CHUNK_MAX} items"
            ),
        )
    return await trust_store.aimport_legacy_profiles(
        platform=req.platform,
        source=req.source,
        profiles=req.profiles,
        final=bool(req.final),
    )


@app.post("/internal/trust/waive_legacy_barrier")
async def waive_legacy_trust_barrier(req: TrustWaiveBarrierRequest):
    """Escape hatch: give up on a platform's legacy import and open its gate."""
    from memory import trust_store

    if not re.fullmatch(r"[A-Za-z0-9_.-]+", req.platform or ""):
        raise HTTPException(status_code=422, detail="invalid platform")
    return await trust_store.awaive_legacy_barrier(req.platform)


@app.post("/internal/trust/reconcile_from_facts")
async def reconcile_trust_from_facts(req: TrustReconcileRequest):
    """Disaster recovery only — manual trigger, never automatic.

    NOT a complete self-healer: ``scoped_forget`` deletes the fact rows that
    carry ``_speaker_trust_signal_events``, so signals on a forgotten subject
    have no reconstruction source. Correctness comes from the
    ``trust.persisted`` round-trip and the caller's retain-and-retry, not from
    this endpoint.
    """
    from memory import trust_store

    if runtime.fact_store is None:
        raise HTTPException(
            status_code=503,
            detail="memory_server not fully initialized (limited mode or startup incomplete)",
        )
    names = req.character_names
    if not names:
        character_data = await runtime._config_manager.aload_characters()
        names = list(character_data.get("猫娘", {}).keys())
    return await trust_store.areconcile_from_facts(
        runtime.fact_store, [validate_lanlan_name(name) for name in names],
    )


@app.post("/internal/identity/scope")
async def declare_identity_scope(req: IdentityScopeDeclareRequest):
    """Record what a platform's identifiers mean on the wire.

    A connector may call this on every startup: the declaration is a transcript
    of the vendor's published protocol, so it is a constant of the connection
    mode rather than something learned from traffic. Re-declaring the same
    tuple writes nothing.

    This is emphatically NOT a place to report an observation. The request body
    carries no account id and no sample precisely so that "we saw two different
    ids, so it must be per_conversation" cannot be expressed — see the kill list
    in ``memory.trust_store``. Deriving a scope from traffic and posting it here
    would launder an inference into an assertion, and downstream consumers show
    this value to the operator as ground truth.
    """
    from memory import trust_store

    _require_loaded_identity_pool()
    try:
        return await trust_store.adeclare_platform_identity_scope(
            req.platform,
            channel=req.channel,
            actor_scope=req.actor_scope,
            conversation_scope=req.conversation_scope,
            asserted_by=req.asserted_by,
        )
    except trust_store.TrustIdentityError as exc:
        raise _identity_error(exc) from exc


@app.post("/internal/identity/accounts/ensure")
async def ensure_identity_account(req: IdentityAccountRequest):
    """Register one account so it has an entity to be bound to.

    ``bind`` takes an entity id and 404s on an unknown one, but an entity is
    only born from ledger activity -- so a roster account that has never
    accrued a trust event has none, and on a fresh install that describes the
    very account everything else needs to merge INTO (the owner authorised in
    DMs). This is the seam that lets the dashboard offer it as a merge target.

    Creating the seed entity is not an edge and asserts nothing about who the
    person is: it links the account to itself. The human assertion is the bind
    that follows. No channel is recorded either -- ``channels_seen`` is an
    observation of traffic, and this call is not traffic.
    """
    from memory import trust_store

    _require_loaded_identity_pool()
    entity_id, persisted = await trust_store.aensure_account(
        req.account_id, report_persisted=True,
    )
    if entity_id is None:
        raise HTTPException(status_code=422, detail="invalid account_id")
    # Pass ``persisted`` through like bind/unbind do. Without it a failed disk
    # write is invisible here and only surfaces one step later as the bind's
    # 404 on an unknown entity -- the operator is then told "merge failed"
    # instead of "the write failed", which points at the wrong thing.
    return {
        "account_id": req.account_id,
        "entity_id": entity_id,
        "persisted": persisted,
    }


@app.post("/internal/identity/accounts/bind")
async def bind_identity_account(req: IdentityBindRequest):
    """Link one account to an entity. HUMAN-TRIGGERED ONLY.

    The number of automatic bind paths is zero and must stay zero. Never derive
    an edge from a nickname, a bootstrap elevation, temporal adjacency, an edit
    distance, or the observed channel — see the kill list in
    ``memory.trust_store``. A dashboard offering candidates must rank them by
    LEDGER WEIGHT ONLY and pre-select nothing; ranking by name similarity would
    hand the user a rejected heuristic as the default answer.

    Operational note, and both halves have to be stated together: on the TRUST
    axis bind EARLY (a late bind lets self-attested signals accumulate first,
    and merge does not refund them), while on the SUBJECT axis bind LATE (rows
    written while a wrong binding is active stay in the canonical pile
    irreversibly). Publishing only one of these is worse than publishing both.
    """
    from memory import trust_store

    _require_loaded_identity_pool()
    try:
        return await trust_store.abind_account(
            req.account_id, req.entity_id, bound_by=req.bound_by,
            require_unbound=bool(req.require_unbound),
        )
    except trust_store.TrustIdentityError as exc:
        raise _identity_error(exc) from exc


@app.post("/internal/identity/accounts/unbind")
async def unbind_identity_account(req: IdentityAccountRequest):
    """Detach one account into a fresh entity. The only rollback for a mis-bind.

    Returns BOTH ``ledger_delta`` and ``effective_delta``, and they are usually
    different numbers — that is not a bug to be "fixed". Under a clamped
    aggregate, "how much did this account take with it" has no unique answer:
    removing an account can move the effective score by more than the clamp
    itself, because it also releases the other accounts' saturation. An operator
    given only the ledger number can never reconcile it with the score.

    Known under-count: the activity write-amplification no-op skips recording
    event ids once the ENTITY is saturated, so an account unbound below the cap
    may have those messages counted again later. Bounded by
    ``SPEAKER_TRUST_ACTIVITY_MAX_BONUS`` (0.02), far under the 0.15 margin.
    """
    from memory import trust_store

    _require_loaded_identity_pool()
    snapshot_before = trust_store.trust_snapshot()
    try:
        result = await trust_store.aunbind_account(
            req.account_id,
            require_provenance=bool(req.require_provenance),
        )
    except trust_store.TrustIdentityError as exc:
        raise _identity_error(exc) from exc
    result["stranded_rows"] = await _count_stranded_rows(
        req.account_id, snapshot_before,
    )
    return result


@app.post("/internal/identity/entities/merge")
async def merge_identity_entities(req: IdentityMergeRequest):
    """Merge two entities. HUMAN-TRIGGERED ONLY. Idempotent/commutative/associative."""
    from memory import trust_store

    _require_loaded_identity_pool()
    try:
        return await trust_store.amerge_entities(
            req.entity_id, req.other_entity_id,
        )
    except trust_store.TrustIdentityError as exc:
        raise _identity_error(exc) from exc


@app.post("/internal/identity/entities/forget")
async def forget_identity_entity(req: IdentityEntityRequest):
    """Drop one entity's identity records. Minimal by design.

    Known weakness, stated rather than hidden: the signal EVENTS themselves live
    on other people's fact rows and in the archive, so if the same account comes
    back and the owner repeats the same sentence, a "forgotten" correction is
    applied again.
    """
    from memory import trust_store

    _require_loaded_identity_pool()
    try:
        return await trust_store.aforget_entity(req.entity_id)
    except trust_store.TrustIdentityError as exc:
        raise _identity_error(exc) from exc


@app.post("/internal/memory/{lanlan_name}/scoped_mentions")
async def record_scoped_mentions(lanlan_name: str, req: ScopedMentionsRequest):
    """Bump mention counters for scoped persona/reflection entries.

    Group replies bypass the legacy post-turn flow, so without this the
    anti-repeat suppression never engages for scoped entries and the model
    keeps volunteering the same scoped fact on every group reply. Zero LLM
    cost: mention scanning is a local token-overlap pass. Legacy-private
    entries are explicitly excluded (fail-closed)."""
    lanlan_name = validate_lanlan_name(lanlan_name)
    if runtime.persona_manager is None or runtime.reflection_engine is None:
        raise HTTPException(
            status_code=503,
            detail="memory_server not fully initialized (limited mode or startup incomplete)",
        )
    if not req.subjects or len(req.subjects) > 8:
        raise HTTPException(status_code=422, detail="subjects must contain 1..8 items")
    response_text = (req.response_text or "").strip()
    if not response_text:
        return {"status": "skipped"}
    _groups, subjects = _fold_request_subjects(req.subjects)
    await runtime.persona_manager.arecord_mentions(
        lanlan_name, response_text,
        subjects=subjects, include_legacy_private=False,
    )
    await runtime.reflection_engine.arecord_mentions(
        lanlan_name, response_text,
        subjects=subjects, include_legacy_private=False,
    )
    return {"status": "recorded"}


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


@app.get("/get_persona/{lanlan_name}")
async def get_persona(lanlan_name: str):
    """Return the full persona JSON (for the UI / memory_browser)."""
    lanlan_name = validate_lanlan_name(lanlan_name)
    return await runtime.persona_manager.aget_persona(lanlan_name)


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


@app.post("/cancel_correction/{lanlan_name}")
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


@app.get("/new_dialog/{lanlan_name}")
async def new_dialog(
    lanlan_name: str,
    language: str | None = None,
    render_language: str | None = None,
):
    request_language = language if is_supported_language_code(language) else render_language
    with language_context(_activate_request_language(request_language)):
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


async def _new_dialog(
    lanlan_name: str,
    language: str | None = None,
    render_language: str | None = None,
):
    lanlan_name = validate_lanlan_name(lanlan_name)
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
    async with runtime._get_settle_lock(lanlan_name):
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
            # Inject pending + confirmed reflections into context too (labeled sections).
            try:
                await runtime.reflection_engine.aupdate_suppressions(lanlan_name)
            except Exception as e:
                logger.debug(f"[MemoryServer] reflection suppress 刷新失败: {e}")
            pending_reflections = await runtime.reflection_engine.aget_pending_reflections(lanlan_name)
            confirmed_reflections = await runtime.reflection_engine.aget_confirmed_reflections(lanlan_name)
            result = _loc(PERSONA_HEADER, _lang).format(name=lanlan_name)
            persona_md = await runtime.persona_manager.arender_persona_markdown(
                lanlan_name, pending_reflections, confirmed_reflections,
            )
        if persona_md:
            result += persona_md
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
