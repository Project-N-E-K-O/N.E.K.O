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
from uuid import uuid4

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from config.prompts.prompts_sys import _loc
from config.prompts.prompts_memory import (
    INNER_THOUGHTS_HEADER,
    CHAT_GAP_NOTICE, CHAT_GAP_LONG_HINT, CHAT_GAP_CURRENT_TIME,
    CHAT_HOLIDAY_CONTEXT,
    MEMORY_RECALL_HEADER, MEMORY_RESULTS_HEADER,
    PERSONA_HEADER, INNER_THOUGHTS_DYNAMIC,
    RECENT_HISTORY_INTRO, NO_RECENT_HISTORY,
)
from utils.frontend_utils import get_timestamp
from utils.language_utils import get_global_language
from utils.llm_client import convert_to_messages
from utils.time_format import format_elapsed as _format_elapsed
from utils.cloudsave_runtime import assert_cloudsave_writable
from memory.external_markdown_import import MAX_ENTRIES, MAX_ENTRY_CHARS
from memory.persona.fusion import ExternalMemoryImportTooLargeError

from . import gates, post_turn, review, runtime
from ._shared import logger, validate_lanlan_name
from .rows import _has_human_messages
from .runtime import app


class HistoryRequest(BaseModel):
    input_history: str


class ExternalMemoryImportRequest(BaseModel):
    character_name: str
    source_format: str
    imported_files: list[str]
    candidates: list[dict]
    warning_count: int = 0


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

    # ── persona 阶段：按 entity 并发 LLM 融合（不降级纯追加，见端点 docstring）──
    # 并发安全：afuse_external_facts 的 Phase 1/3 持同一把角色锁串行读写、且各
    # entity 只改写自己的 section（CAS 校验的也是本 entity 的指纹集合），慢的
    # Phase 2（LLM）不持锁——两个 entity 真正并行的只有 LLM 往返，落盘互斥。
    persona_entities = list(persona_candidates_by_entity.items())
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
NEW_DIALOG_QPS_FLUSH_INTERVAL = 60


def _format_legacy_settings_as_text(settings: dict, lanlan_name: str) -> str:
    """Convert legacy settings JSON into natural-language form, replacing the raw json.dumps output."""
    if not settings:
        return f"{lanlan_name}记得：（暂无记录）"

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
            sections.append(f"关于{name}：\n" + "\n".join(lines))

    if not sections:
        return f"{lanlan_name}记得：（暂无记录）"
    return f"{lanlan_name}记得：\n" + "\n".join(sections)


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
        reflection_result = await runtime.reflection_engine.reflect(lanlan_name)
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
            await runtime.reflection_engine.aauto_promote_stale(lanlan_name)
        else:
            await runtime.reflection_engine.aauto_promote_time_driven(lanlan_name)
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
        await post_turn._spawn_outbox_post_turn_signals(lanlan_name, input_history)
        return {"status": "cached", "count": len(input_history)}
    except Exception as e:
        logger.error(f"[MemoryServer] cache 失败: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


@app.post("/process/{lanlan_name}")
async def process_conversation(request: HistoryRequest, lanlan_name: str):
    lanlan_name = validate_lanlan_name(lanlan_name)
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
        await runtime.recent_history_manager.update_history(input_history, lanlan_name, on_compress_done=review._on_compress_done)
        # 旧模块已禁用（性能不足）：
        # await settings_manager.extract_and_update_settings(input_history, lanlan_name)
        # await semantic_manager.store_conversation(uid, input_history, lanlan_name)
        await runtime.time_manager.astore_conversation(uid, input_history, lanlan_name)

        # 异步事实提取（不阻塞返回，失败静默跳过）
        await post_turn._spawn_outbox_post_turn_signals(lanlan_name, input_history)

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
            await runtime.recent_history_manager.update_history(input_history, lanlan_name, detailed=True, on_compress_done=review._on_compress_done)
            await runtime.time_manager.astore_conversation(uid, input_history, lanlan_name)

        # 以下操作在锁外执行，不阻塞 /new_dialog
        # 异步事实提取
        await post_turn._spawn_outbox_post_turn_signals(lanlan_name, input_history)

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
            await runtime.recent_history_manager.update_history([], lanlan_name, detailed=True, on_compress_done=review._on_compress_done)

        if input_history:
            await post_turn._spawn_outbox_post_turn_signals(lanlan_name, input_history)

        # Phase C: 见 /process 的注释——不再 cancel-and-restart。
        await review.maybe_spawn_review(lanlan_name)

        return {"status": "settled"}
    except Exception as e:
        logger.error(f"[MemoryServer] settle 失败: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


@app.get("/get_recent_history/{lanlan_name}")
async def get_recent_history(lanlan_name: str):
    lanlan_name = validate_lanlan_name(lanlan_name)
    _lang = get_global_language()
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
async def get_memory(query: str, lanlan_name: str):
    """**Deprecated** — the old GET endpoint is kept only to avoid breaking old
    callers; new callers use POST ``/query_memory/{lanlan_name}`` for structured
    results. This endpoint keeps returning placeholder text to discourage the old
    path from coming back (semantic recall was taken off this GET long ago).
    """
    lanlan_name = validate_lanlan_name(lanlan_name)
    _lang = get_global_language()
    return (
        _loc(MEMORY_RECALL_HEADER, _lang).format(name=lanlan_name)
        + query
        + "\n\n"
        + _loc(MEMORY_RESULTS_HEADER, _lang).format(name=lanlan_name)
        + "\n（语义记忆已下线，暂无相关记忆片段。）"
    )


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
                return await recall_by_time(
                    lanlan_name=lanlan_name,
                    time_spec=time_spec,
                    fact_store=runtime.fact_store,
                    reflection_engine=runtime.reflection_engine,
                )
        # query（+ 可选 time_window）→ 语义检索；time_window 非空即"语义 +
        # 时间"联合检索（窗口内按 query 排序）。
        from memory.hybrid_recall import hybrid_recall
        return await hybrid_recall(
            lanlan_name=lanlan_name,
            query=query_text,
            fact_store=runtime.fact_store,
            reflection_engine=runtime.reflection_engine,
            config_manager=runtime._config_manager,
            time_window=time_window,
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

    # Render 前刷新 reflection suppress 状态（冷却期过 → 解除），语义对齐
    # persona render 的 update_suppressions 调用位置
    try:
        await runtime.reflection_engine.aupdate_suppressions(lanlan_name)
    except Exception as e:
        logger.debug(f"[MemoryServer] reflection suppress 刷新失败: {e}")
    # 优先使用 persona markdown 渲染（与 /new_dialog 保持一致），回退到旧 settings 格式
    pending_reflections = await runtime.reflection_engine.aget_pending_reflections(lanlan_name)
    confirmed_reflections = await runtime.reflection_engine.aget_confirmed_reflections(lanlan_name)
    persona_md = await runtime.persona_manager.arender_persona_markdown(
        lanlan_name, pending_reflections, confirmed_reflections,
    )
    if persona_md:
        return persona_md
    # 兼容回退（自然语言格式）
    legacy_settings = await asyncio.to_thread(runtime.settings_manager.get_settings, lanlan_name)
    return _format_legacy_settings_as_text(legacy_settings, lanlan_name)


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

@app.get("/new_dialog/{lanlan_name}")
async def new_dialog(lanlan_name: str):
    lanlan_name = validate_lanlan_name(lanlan_name)
    gates._touch_activity()

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
        _lang = get_global_language()

        # ── [静态前缀] Persona 长期记忆（变化极少 → 最大化 prefix cache） ──
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
            result += _format_legacy_settings_as_text(legacy_settings, lanlan_name) + "\n"

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
