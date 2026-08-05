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
import json
import re
from datetime import datetime, timedelta
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException, Request
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
    _normalize_memory_prompt_lang,
)
from utils.frontend_utils import get_timestamp
from utils.language_utils import (
    get_global_language_full,
    is_supported_language_code,
    language_context,
    normalize_language_code,
)
from utils.llm_client import convert_to_messages
from utils.time_format import format_elapsed as _format_elapsed
from utils.cloudsave_runtime import MaintenanceModeError, assert_cloudsave_writable
from memory.external_markdown_import import MAX_ENTRIES, MAX_ENTRY_CHARS
from memory.outbox import OP_PERSIST_PROMPT_LOCALE
from memory.persona.fusion import ExternalMemoryImportTooLargeError

from . import gates, locale_state, outbox_infra, post_turn, review, runtime
from ._shared import logger, validate_lanlan_name
from .rows import _has_human_messages
from .runtime import app


class HistoryRequest(BaseModel):
    input_history: str
    language: str | None = None


class PromptLocalePreferenceRequest(BaseModel):
    language: str


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
) -> str:
    """Resolve foreground prompt locale without persisting a process guess.

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
            "process locale for this request",
            lanlan_name,
        )
        return _activate_request_language(None)
    return _activate_request_language(durable_language)


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


@app.post("/internal/memory/import_external_markdown")
async def import_external_markdown(request: ExternalMemoryImportRequest):
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
    if request.source_format not in {"openclaw", "hermes"}:
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
    # persona 候选按 entity(master / neko) 分组各自送 LLM 融合；facts 里 MEMORY.md
    # 走纯追加，daily 日记(带 event_date)走 LLM 事实抽取。
    persona_candidates_by_entity: dict[str, list[dict]] = {}
    extracted_facts: list[dict] = []       # MEMORY.md → 确定性纯追加
    daily_candidates: list[dict] = []      # daily 日记 → LLM 事实抽取
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
            # 带齐 provenance（source_file / source_section / event_date）传给融合层：
            # source_section 用于融合 prompt 分节，source_file 进 Phase 3 落盘 metadata，
            # 指纹由 afuse_external_facts 内部按候选文本自算（幂等重导）。
            persona_candidates_by_entity.setdefault(entity, []).append({
                "text": text,
                "entity": entity,
                "source_file": source_file,
                "source_section": source_section,
                "event_date": event_date,
            })
        elif event_date:
            # daily 日记（memory/·memories/YYYY-MM-DD.md）：散文，不逐条追加，
            # 交给 aimport_external_daily 按日跑 LLM 事实抽取（见其 docstring）。
            daily_candidates.append({
                "text": text,
                "source_file": source_file,
                "source_section": source_section,
                "event_date": event_date,
            })
        else:
            # MEMORY.md：已是 fact 清单，确定性纯追加。
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

    # ── persona 阶段：按 entity 并发 LLM 融合（不降级纯追加，见端点 docstring）──
    # 并发安全：afuse_external_facts 的 Phase 1/3 持同一把角色锁串行读写、且各
    # entity 只改写自己的 section（CAS 校验的也是本 entity 的指纹集合），慢的
    # Phase 2（LLM）不持锁——两个 entity 真正并行的只有 LLM 往返，落盘互斥。
    persona_entities = list(persona_candidates_by_entity.items())
    memory_language = explicit_language or _activate_request_language(request.language)
    with language_context(memory_language):
        fusion_outcomes = await asyncio.gather(
            *(
                runtime.persona_manager.afuse_external_facts(
                    name, entity, entity_candidates, request.source_format,
                )
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
            # 全部失败都是确定性「太大」：候选超单次融合输入池，重试同一份必然再失败
            # （没记指纹）→ 返回不可重试的 too_large，让前端提示「拆分 workspace」。
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
        # 含可重试失败（融合终态失败 ExternalMemoryFusionError / asave_persona 崩溃
        # 等，或与 too_large 混合）→ 返回 partial 让前端幂等重试：已成功 entity 被
        # 指纹 skip、瞬态失败的收敛，收敛后若只剩 too_large 自然浮出 413。绝不回退
        # 成逐条 append 撑爆 persona 池。
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

    # daily 日记 → LLM 事实抽取（按日 best-effort，见 aimport_external_daily）。
    # 已落盘的 persona / MEMORY.md facts 不因 daily 失败回滚；系统性异常返回 partial。
    daily_added = 0
    if daily_candidates:
        try:
            with language_context(memory_language):
                daily_result = await runtime.fact_store.aimport_external_daily(
                    name, daily_candidates, request.source_format, imported_at,
                )
        except ExternalMemoryImportTooLargeError as exc:
            # 确定性超限（真正要抽取的日记天数超 cap）：重试同一份必然再超 →
            # too_large 引导拆分。已导入天会被逐日指纹 skip，分次导入零重复成本。
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
        if daily_result["failed_days"]:
            # 有日记天抽取失败：不能回 success（客户端会当导入完成、失败天永久
            # 丢失且无重试信号，Greptile P1）→ 返回可重试 partial。重试收敛：
            # persona 指纹幂等 skip、MEMORY.md 与已抽出 daily fact 被 SHA/FTS5
            # 去重挡住，只有失败天真正重抽。
            logger.warning(
                "External Markdown import: %s daily journal(s) failed extraction: character=%s",
                daily_result["failed_days"], name,
            )
            return JSONResponse(
                status_code=500,
                content={
                    "detail": (
                        f"{daily_result['failed_days']} daily journal(s) failed "
                        "extraction; retry to finish"
                    ),
                    "error_code": "external_import_partial",
                    "partial_import": {
                        "character_name": name,
                        "added_persona": added_persona,
                        "added_facts": memory_added + daily_added,
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


# /new_dialog QPS 观测：每角色累计调用次数，由 _periodic_new_dialog_qps_log_loop
# 每 NEW_DIALOG_QPS_FLUSH_INTERVAL 秒打一行 INFO 日志后清零。用于 A 之后观测
# proactive_chat 路径是否成为 memory_server 真正的负载来源；如不是，则不必再
# 上 main_server 端缓存（C+ 方案）。
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


# --- Reflection API（供 main_server/system_router 通过 HTTP 调用） ---

@app.post("/reflect/{lanlan_name}")
async def api_reflect(lanlan_name: str):
    """Synthesize reflections + automatic state migration, returning the result.

    Centralized in the memory_server process, avoiding the absorbed-flag race
    caused by main_server instantiating locally.
    """
    lanlan_name = validate_lanlan_name(lanlan_name)
    reflection_result = None
    # auto_promote_stale 改 fire-and-forget：开 thinking 后 promote_merge 单
    # 调用可能 30-90s，串行多个 confirmed reflection 累计能超 client 15s
    # timeout。periodic auto_promote loop 每 180s 跑一次会兜底，本端点不
    # 等也安全。caller (system_router) 仅用 auto_transitions 打 log，丢失
    # 计数无功能影响。
    runtime._spawn_background_task(_safe_auto_promote(lanlan_name))
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
        "auto_transitions": 0,  # fire-and-forget，本调用不返回真实计数
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
    )
    with language_context(memory_language):
        gates._touch_activity()
        try:
            input_history = convert_to_messages(json.loads(request.input_history))
            if not input_history:
                return {"status": "cached", "count": 0}
            if _has_human_messages(input_history):
                await gates._aclear_review_clean(lanlan_name)
            logger.info(f"[MemoryServer] cache: {lanlan_name} +{len(input_history)} 条消息")
            uid = str(uuid4())
            async with runtime._get_settle_lock(lanlan_name):
                await runtime.recent_history_manager.update_history(input_history, lanlan_name, compress=False)
                # store_conversation 必须在 lock 内、与 update_history 串行：和
                # /process / /renew 路径对偶，确保单角色 db 写顺序一致。
                await runtime.time_manager.astore_conversation(uid, input_history, lanlan_name)
            # outbox 登记走锁外——它会 spawn background task 跑 LLM，长持锁会
            # 阻塞下一轮 /cache 写盘。
            await post_turn._spawn_outbox_post_turn_signals(
                lanlan_name, input_history, language=request.language,
                locale_admission_order=locale_admission_order,
            )
            return {"status": "cached", "count": len(input_history)}
        except Exception as e:
            logger.error(f"[MemoryServer] cache 失败: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}


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
            # 检查角色是否存在于配置中，如果不存在则记录信息但继续处理（允许新角色）
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
            # 旧模块已禁用（性能不足）：
            # await settings_manager.extract_and_update_settings(input_history, lanlan_name)
            # await semantic_manager.store_conversation(uid, input_history, lanlan_name)
            await runtime.time_manager.astore_conversation(uid, input_history, lanlan_name)

            # 异步事实提取（不阻塞返回，失败静默跳过）
            await post_turn._spawn_outbox_post_turn_signals(
                lanlan_name, input_history, language=request.language,
                locale_admission_order=locale_admission_order,
            )

            # Phase C: 不再 cancel-and-restart review；让 maybe_spawn_review 在新消息
            # 门 + min_interval + in-flight 多重 gate 后决定起或不起。在跑的 review
            # 跑完会自行 patch 当前 history 末尾的可改区，新消息保留不动。
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
    )
    with language_context(memory_language):
        gates._touch_activity()
        # Same warmup hint as /process: /renew is also a "user actively
        # using the app" signal, so it counts as the unblock event.
        if runtime.embedding_warmup_worker is not None:
            runtime.embedding_warmup_worker.notify_first_process()
        try:
            # 检查角色是否存在于配置中，如果不存在则记录信息但继续处理（允许新角色）
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
            # 首轮摘要带锁：阻塞 /new_dialog 直到摘要+时间戳写入完成
            async with runtime._get_settle_lock(lanlan_name):
                await runtime.recent_history_manager.update_history(
                    input_history,
                    lanlan_name,
                    detailed=True,
                    on_compress_done=review._on_compress_done,
                )
                await runtime.time_manager.astore_conversation(uid, input_history, lanlan_name)

            # 以下操作在锁外执行，不阻塞 /new_dialog
            # 异步事实提取
            await post_turn._spawn_outbox_post_turn_signals(
                lanlan_name, input_history, language=request.language,
                locale_admission_order=locale_admission_order,
            )

            # Phase C: 见 /process 的注释——不再 cancel-and-restart。
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
                    locale_admission_order=locale_admission_order,
                )

            # Phase C: 见 /process 的注释——不再 cancel-and-restart。
            await review.maybe_spawn_review(lanlan_name)

            return {"status": "settled"}
        except Exception as e:
            logger.error(f"[MemoryServer] settle 失败: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}


@app.get("/get_recent_history/{lanlan_name}")
async def get_recent_history(lanlan_name: str, language: str | None = None):
    lanlan_name = validate_lanlan_name(lanlan_name)
    _lang = _normalize_memory_prompt_lang(_activate_request_language(language))
    # 检查角色是否存在于配置中
    try:
        character_data = await runtime._config_manager.aload_characters()
        catgirl_names = list(character_data.get('猫娘', {}).keys())
        if lanlan_name not in catgirl_names:
            logger.warning(f"角色 '{lanlan_name}' 不在配置中，返回空历史记录")
            return _loc(NO_RECENT_HISTORY, _lang)
    except Exception as e:
        logger.error(f"检查角色配置失败: {e}")
        return _loc(NO_RECENT_HISTORY, _lang)

    history = await runtime.recent_history_manager.aget_recent_history(lanlan_name)
    _, _, _, _, name_mapping, _, _, _, _ = await runtime._config_manager.aget_character_data()
    name_mapping['ai'] = lanlan_name
    result = _loc(RECENT_HISTORY_INTRO, _lang).format(name=lanlan_name)
    for i in history:
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
    call, which is what keeps the pool lock a leaf: it never overlaps the
    FactStore lock order. Any failure before this point shows up as "trust did
    not move at all".
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
    # query / time 都可选，至少给一个有效值即可（time-only 是新支持的用法）。
    # 两者都空时不报错，hybrid_recall 对空 query 短路返回空 results，调用方
    # 把空结果翻成"没有找到相关记忆"——和本端点"绝不让召回失败/空入参把
    # tool call 整死"的设计一致，所以这里不做 422/400 硬校验。
    query: str | None = None
    # 可选时间回溯：填了就把检索限定在该时间窗口。配合 query 时做"语义 +
    # 时间"联合检索（窗口内按 query 排序）；只给 time 时按事件时间返回最
    # 接近的 fact + reflection。格式见 memory.temporal.parse_time_window
    # （整点小时 / 单日 / 整月 / 整年 / 区间）。不填或解析失败则走常规全量
    # 语义检索。
    time: str | None = None
    # Explicit read boundary for group-chat callers. Omitting the field keeps
    # the pre-upgrade legacy-private behaviour. Supplying one or more subjects
    # excludes every unscoped legacy row; there is intentionally no request
    # flag that lets a plugin turn legacy-private into a wildcard corpus.
    # An explicit empty list is a caller contract bug and is rejected 422 at
    # the endpoint (fail-closed) — it must never fall back to legacy private.
    subjects: list[MemorySubjectRequest] | None = None
    language: str | None = None


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
    lanlan_name: str, subject, display_name: str | None,
) -> None:
    """Best-effort display-name refresh after a successful scoped write.

    Never fails the write: the facts are already persisted, and a display
    name is metadata the next write can supply again.
    """
    if not display_name or runtime.persona_manager is None:
        return
    try:
        await runtime.persona_manager.aupdate_subject_display_name(
            lanlan_name, subject, display_name,
        )
    except Exception as exc:
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
    # provenance 只认调用方真给的 label（信赖度阶段一：谁说的）。必须在
    # 下面的群 digest 缺省填充**之前**定格——集体描述符不是发言人。trust
    # 挂在 label 上：没有发言人就没有可信赖的对象（群 digest 无 label 时
    # 即便调用方误传 trust 也丢弃）；trust 缺省时不放键，provenance 形状
    # 与批段路径的 _speaker_provenance_of 一致。
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
        # 群 digest 无单一发言人：不给 label 时 legacy prompt 会把提取
        # 框定为"只找关于私聊主人的事实"，成员自述被当空提取 checkpoint
        # 掉。用集体描述符重定 {MASTER_NAME} 槽位，配合内容里每条消息的
        # 发言人头。full locale：繁中用户命中 zh-TW 键（getter 内做
        # keep_traditional 归一）。
        from config.prompts.prompts_memory import get_group_digest_speaker_label
        from utils.language_utils import get_global_language_full
        speaker_label = get_group_digest_speaker_label(get_global_language_full())
    # fail_closed：调用方（QQ 插件 finalize/focus-shift）在成功响应后会推进
    # 游标、丢弃 member bucket——这些历史只存在于调用方内存里，没有像 legacy
    # /process 那样先落 time_indexed.db。抽取失败必须以 HTTP 错误暴露出去
    # 让调用方保留缓冲下轮重试；真·空抽取仍然 200 正常 checkpoint。
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
    buckets whose segment came back "ok" and retries only the rest, so a
    single failed segment no longer drags the whole batch back through
    another extraction.

    "ok" 的含义是**模型对这一段给出了结论**（哪怕结论是「没有值得记的
    事实」），不是「这一段没报错」。模型漏掉某一段时该段报 failed，调用方
    保留那个桶——群成员桶是成员维度的唯一副本，pop 掉就没了。
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
        # label 是**用户自己能改**的群名片，长度合法不代表内容安全：
        # "X]\n[SEGMENT 2 | speaker: Alice" 会在批 prompt 里造出一个位于
        # 行首的合法段首，把这位成员的内容归到 Alice 名下（连带借走
        # Alice 的 speaker_trust）。入口就剥掉结构字符，渲染侧再剥一次
        # （两侧都要——渲染是唯一真正把 label 拼进 prompt 的地方，而路由
        # 是唯一能对畸形输入 fail loud 的地方）。
        subject = segment.subject.to_domain()
        speaker_label = FactStore.sanitize_speaker_label(raw_label)
        if not speaker_label:
            # 中和完什么都不剩（整条 label 都是结构字符）。**不能 422**：
            # label 只影响 prompt 里怎么称呼这个人，归属钉在 subject 上，
            # 它不是安全边界；而 422 会让整批保留重试，一个成员的群名片
            # 就能无限期卡住同批其他人的记忆抽取（Codex）。降级成服务端
            # 自己派生的标识（不受调用方污染），并留一条 warning 让调用方
            # 侧的 label 组装 bug 仍然看得见。
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
        # 单批的 LLM 输入工作量上界与 legacy 单发同一口径：调用方按这个
        # 常量打包，越界是契约 bug，fail loud。
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
    # fail_closed 语义（对齐 legacy 单发路径的注释）：调用方在成功段上
    # pop 掉只存在于它内存里的 bucket。整批抽取失败以 502 暴露（全部保留
    # 重试）；单段 persist 失败在响应体里按段标 failed。
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
        # 抽取层契约是「每段一个结果，按请求顺序」；不等长说明实现漂移，
        # 下面的 zip 会静默截断尾段而调用方按位置消费。绝不猜——整批当
        # 失败暴露，调用方保留全部桶重试。
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
        # 只给「模型对这一段给出了结论」的段刷新显示名：失败段整桶保留
        # 重试，下次照样带名字来，不必在失败路径上碰 persona。
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
                # 本段被丢弃的无内容垃圾条目数。嵌套输出下丢弃不损失内容
                # （归属由段对象给定），所以调用方仍按 status 决定推进/
                # 保留；回报它是为了让"模型输出在变脏"这件事在插件日志里
                # 有痕迹，而不是只留在记忆服务进程内。
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
    # suppress 的到期解除只发生在 aupdate_suppressions 里，而它此前只被
    # legacy 的 /get_settings、/new_dialog 调用——纯群聊部署永远走不到
    # 那两条路径，scoped reflection 第一次被 suppress 后就永久隐身。
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


@app.post("/internal/memory/{lanlan_name}/scoped_forget")
async def forget_scoped_subject(lanlan_name: str, req: ScopedForgetRequest):
    """Delete every stored memory of one exact (subject, scope) domain.

    撤回入口：删好友/退群之后，该 subject 的 facts（活跃 + 归档）、
    reflections（含 surfaced 引用）、persona section（含 display_name）、
    pending corrections 一次清干净——此前四个 scoped 端点只进不出，
    建档没有任何撤回路径。精确匹配 (key, scope)：legacy 无戳语料与其它
    scope 永不落入删除面。幂等：重复调用报 0。部分失败以 500 暴露，
    重试安全（已删的不会复活）。reflection/persona 归档分片作为事件溯源
    留底；持久化 forget 水位确保其即使被事件重放重建，也不能再被 restore。
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
        for target in targets:
            lock = runtime.fact_store._get_subject_forget_transaction_lock(
                lanlan_name, target,
            )
            await lock.acquire()
            acquired_locks.append(lock)
        # ALL tombstones open before ANY erase.
        for target in targets:
            await runtime.fact_store.abegin_subject_forget(lanlan_name, target)
            fact_forget_started.append(target)
        for target in targets:
            await runtime.reflection_engine.abegin_subject_forget(
                lanlan_name, target,
            )
            reflection_forget_started.append(target)
        for target in targets:
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
        for target in targets:
            await runtime.fact_store.afinalize_subject_forget(
                lanlan_name, target,
            )
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
    return {
        "status": "forgotten",
        "subject": subject.as_entry_fields(),
        "forgotten_subjects": [
            target.as_entry_fields() for target in targets
        ],
        **stats,
    }


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


class IdentityAccountRequest(BaseModel):
    account_id: str


class IdentityMergeRequest(BaseModel):
    entity_id: str
    other_entity_id: str


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
        result = await trust_store.aunbind_account(req.account_id)
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
        # Import 移进 try：若 memory.hybrid_recall 自身 import 失败（循环
        # import / 依赖缺失），仍然走下面的兜底返回空 results，避免端点
        # 直接 500 把 tool call 整死。
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
                # 只给 time、没 query → 按时间邻近返回最接近的若干条。
                from memory.hybrid_recall import recall_by_time
                with language_context(resolved_language):
                    return await recall_by_time(
                        lanlan_name=lanlan_name,
                        time_spec=time_spec,
                        fact_store=runtime.fact_store,
                        reflection_engine=runtime.reflection_engine,
                        subjects=subjects,
                    )
        # query（+ 可选 time_window）→ 语义检索；time_window 非空即"语义 +
        # 时间"联合检索（窗口内按 query 排序）。
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
            )
    except Exception as exc:
        # 永不让一次召回失败把 tool call 整死——返回空 results，main_server
        # 那边的 handler 会把空 results 翻译成 "没有找到相关记忆"，模型可以
        # 正常继续。完整 traceback 落 logger.exception（含 type + msg），
        # 响应体只回稳定 error_code，避免把内部细节（异常消息可能夹带敏感
        # 上下文）通过 HTTP body 泄出去。
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
    # 检查角色是否存在于配置中
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
        # Render 前刷新 reflection suppress 状态（冷却期过 → 解除），语义对齐
        # persona render 的 update_suppressions 调用位置
        try:
            await runtime.reflection_engine.aupdate_suppressions(lanlan_name)
        except Exception as e:
            logger.debug(f"[MemoryServer] reflection suppress 刷新失败: {e}")
        # 优先使用 persona markdown 渲染（与 /new_dialog 保持一致），回退到旧 settings 格式
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
        # 兼容回退（自然语言格式）
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

    # 文件 IO + 行级解析 → 跑 worker，避开 event loop 阻塞
    # (同样的模式见 EventLog 的 a-twins)。
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
    """中断指定角色的记忆整理任务（用于记忆编辑后立即生效）"""
    
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
    language = await asyncio.to_thread(
        locale_state.get_character_prompt_locale,
        name,
    )
    return {
        "success": True,
        "language": language,
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
    previous = await asyncio.to_thread(
        locale_state.get_character_prompt_locale,
        name,
    )
    order = await asyncio.to_thread(
        locale_state.reserve_character_prompt_locale_order,
        name,
    )
    persisted = await asyncio.to_thread(
        locale_state.record_character_prompt_locale,
        name,
        normalized,
        order=order,
    )
    if persisted != normalized:
        raise HTTPException(
            status_code=409,
            detail="A newer language preference superseded this request",
        )
    return {
        "success": True,
        "language": normalized,
        "previous_language": previous,
        "changed": previous != normalized,
    }


@app.get("/new_dialog/{lanlan_name}")
async def new_dialog(lanlan_name: str, language: str | None = None):
    with language_context(_activate_request_language(language)):
        return await _new_dialog(lanlan_name, language)


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


async def _new_dialog(lanlan_name: str, language: str | None = None):
    lanlan_name = validate_lanlan_name(lanlan_name)
    gates._touch_activity()
    has_explicit_language = is_supported_language_code(language)
    locale_admission_order = None
    if has_explicit_language:
        locale_admission_order = (
            locale_state.capture_character_prompt_locale_order(lanlan_name)
        )

    # 检查角色是否存在于配置中
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
        language = await asyncio.to_thread(
            locale_state.get_character_prompt_locale,
            lanlan_name,
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
                runtime._spawn_background_task(operation)
        else:
            _promote_new_dialog_locale_generation(
                lanlan_name,
                generation,
            )

    # 仅对合法角色计数：QPS 观测的目的是评估 C+ 缓存决策，无效请求不构成
    # cacheable 机会，记进来反而污染 per_char 分布。
    _new_dialog_qps_counter[lanlan_name] = _new_dialog_qps_counter.get(lanlan_name, 0) + 1

    # settle_lock 保留：等 /renew /settle 的首轮摘要完成，读到一致数据。
    # review 不持此锁，且写盘是「整体引用替换 + fingerprint patch」原子操作，
    # 与本路径读取无 race；Phase C 已让 review 设计成可与 /process 并行的后台
    # 任务，/new_dialog 不再 cancel 在跑的 review（之前的 cancel 是 Phase A
    # 遗留物，会让 review 在活跃会话里几乎永不完成）。
    async with runtime._get_settle_lock(lanlan_name):
        # 正则表达式：删除所有类型括号及其内容（包括[]、()、{}、<>、【】、（）等）
        brackets_pattern = re.compile(r'(\[.*?\]|\(.*?\)|（.*?）|【.*?】|\{.*?\}|<.*?>)')
        master_name, _, _, _, name_mapping, _, _, _, _ = await runtime._config_manager.aget_character_data()
        name_mapping['ai'] = lanlan_name
        _lang = _normalize_memory_prompt_lang(_activate_request_language(language))

        # ── [静态前缀] Persona 长期记忆（变化极少 → 最大化 prefix cache） ──
        # 请求没显式带语言时，上层 context 仍是进程回退值。耐久 locale
        # 读取发生在入口之后，因此必须在调用可能读取全局语言的嵌套 renderer
        # 前重新进入 context；显式 _lang 继续用于本函数内的表驱动字符串。
        with language_context(_activate_request_language(language)):
            # pending + confirmed 反思也注入上下文（分区标注）
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
            # 兼容回退：使用旧 settings（自然语言格式）
            # get_settings 内部 open() + json.load()，offload 避免阻塞（冷回退路径，但触发时多文件 IO）
            legacy_settings = await asyncio.to_thread(runtime.settings_manager.get_settings, lanlan_name)
            result += (
                _format_legacy_settings_as_text(
                    legacy_settings,
                    lanlan_name,
                    _lang,
                )
                + "\n"
            )

        # ── [动态部分] 内心活动（每次变化） ──
        result += _loc(INNER_THOUGHTS_HEADER, _lang).format(name=lanlan_name)
        result += _loc(INNER_THOUGHTS_DYNAMIC, _lang).format(
            name=lanlan_name,
            time=get_timestamp(),
        )

        for i in await runtime.recent_history_manager.aget_recent_history(lanlan_name):
            if isinstance(i.content, str):
                cleaned_content = brackets_pattern.sub('', i.content).strip()
                result += f"{name_mapping[i.type]} | {cleaned_content}\n"
            else:
                texts = [brackets_pattern.sub('', j['text']).strip() for j in i.content if j['type'] == 'text']
                result += f"{name_mapping[i.type]} | " + "\n".join(texts) + "\n"

        # ── 距上次聊天间隔提示（放在最末尾，紧接 CONTEXT_SUMMARY_READY 之前） ──
        try:
            from datetime import datetime as _dt
            last_time = await runtime.time_manager.aget_last_conversation_time(lanlan_name)
            if last_time:
                gap = _dt.now() - last_time
                gap_seconds = gap.total_seconds()
                if gap_seconds >= 1800:  # ≥ 30分钟才显示
                    elapsed = _format_elapsed(_lang, gap_seconds)

                    if gap_seconds >= 18000:  # ≥ 5小时：当前时间 + 间隔 + 长间隔提示
                        now_str = _dt.now().strftime("%Y-%m-%d %H:%M")
                        result += _loc(CHAT_GAP_CURRENT_TIME, _lang).format(now=now_str)
                        result += _loc(CHAT_GAP_NOTICE, _lang).format(master=master_name, elapsed=elapsed)
                        result += _loc(CHAT_GAP_LONG_HINT, _lang).format(name=lanlan_name, master=master_name) + "\n"
                    else:
                        result += _loc(CHAT_GAP_NOTICE, _lang).format(master=master_name, elapsed=elapsed) + "\n"
        except Exception as e:
            logger.warning(f"计算聊天间隔失败: {e}")

        # ── 节日/假期上下文（无关消费，始终注入） ──
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
