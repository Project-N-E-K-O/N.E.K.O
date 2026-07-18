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

"""
FactStore — Tier 1 of the three-tier memory hierarchy.

Extracts atomic facts from conversations using LLM, deduplicates via
SHA-256 hash + FTS5 semantic search, and persists to JSON files.
Facts are indexed in TimeIndexedMemory's FTS5 table for later retrieval.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import asyncio
import threading
from datetime import datetime
from typing import TYPE_CHECKING

from config import (
    EVIDENCE_DETECT_SIGNALS_MAX_OBSERVATIONS,
    EVIDENCE_DETECT_SIGNALS_MAX_NEW_FACTS,
    EVIDENCE_DETECT_SIGNALS_MODEL_TIER,
    EVIDENCE_EXTRACT_FACTS_MODEL_TIER,
    EXTERNAL_IMPORT_DAILY_INPUT_MAX_TOKENS,
    EXTERNAL_IMPORT_DAILY_MAX_CONCURRENCY,
    EXTERNAL_IMPORT_DAILY_MAX_FILES,
    MEMORY_SCHEMA_VERSION_CURRENT,
)
from memory.temporal import (
    compute_event_timestamps,
    normalize_event_when,
)
from config.prompts.prompts_memory import (
    get_fact_extraction_prompt,
    get_signal_detection_prompt,
)
from memory.evidence import evidence_score
from utils.cloudsave_runtime import MaintenanceModeError, assert_cloudsave_writable
from utils.language_utils import get_global_language
from utils.config_manager import get_config_manager
from utils.file_utils import (
    atomic_write_json,
    robust_json_loads,
)
from utils.logger_config import get_module_logger
from utils.token_tracker import set_call_type

if TYPE_CHECKING:
    from memory.timeindex import TimeIndexedMemory

logger = get_module_logger(__name__, "Memory")


_ARCHIVE_AGE_DAYS = 7          # absorbed 且创建超过此天数的 facts 被归档
_ARCHIVE_COOLDOWN_HOURS = 24   # 两次归档尝试之间的最小间隔

# Sentinel：让 _allm_call_with_retries 区分"调用方没指定 extra_body"（默认走
# create_chat_llm 自动解析）和"调用方显式传 None"（关闭 extra_body 自动解析，
# 保留 thinking）。Phase D：Stage-2 signal detection 显式传 None 开 thinking。
_DEFAULT_EXTRA_BODY = object()


def safe_importance(f: dict, default: int = 5) -> int:
    """Defensively coerce ``f['importance']`` to int.

    Normal entries pass through `_apersist_new_facts` where importance is
    clamped to 1..10, so this only matters for hand-edited facts.json or
    legacy data — but a malformed value here would otherwise raise
    ValueError inside a sort key and stall the entire drain loop for that
    character. Falls back to ``default`` on any failure.
    """
    try:
        val = f.get('importance', default)
        return int(val) if val else default
    except (ValueError, TypeError):
        return default


def safe_int_field(d: dict, key: str, default: int = 0) -> int:
    """Defensively coerce ``d[key]`` to int (Codex P2 on PR #1412).

    Liveness attempt counters (``refine_attempts`` / ``resolve_attempts`` /
    ``_attempt_count``) are all read from dict fields deserialized from JSON /
    ndjson; once a manual edit / legacy data / migration noise writes a dirty
    value like ``""`` / ``"unknown"`` / list / dict, the original
    ``int(d.get(key, 0) or 0)`` raises ValueError / TypeError and takes down the
    whole list comprehension (candidate gather) → that pass fails forever → the
    liveness fallback itself becomes a new liveness gap.

    Difference from ``safe_importance``: this helper treats ``0`` / ``"0"`` as
    legitimate and returns 0 (an attempt counter of 0 is a valid count) instead
    of falling back to the default. ``safe_importance`` mapping all falsy values
    to the default is importance-specific semantics.
    """
    try:
        val = d.get(key)
        if val is None:
            return default
        return int(val)
    except (ValueError, TypeError):
        return default


class FactExtractionFailed(RuntimeError):
    """Stage-1 LLM call exhausted retries (RFC §3.4.2, last paragraph).

    Distinct from "Stage-1 returned an empty list" — the latter is a
    successful zero-result run that should advance the signal-extraction
    cursor, while the former must leave the cursor untouched so the next
    idle cycle retries the same message window.
    """


class FactStore:
    """Manages raw fact extraction, deduplication, and persistence."""

    def __init__(self, *, time_indexed_memory: TimeIndexedMemory | None = None):
        self._config_manager = get_config_manager()
        self._time_indexed = time_indexed_memory
        self._facts: dict[str, list[dict]] = {}  # {lanlan_name: [fact, ...]}
        self._locks: dict[str, threading.Lock] = {}  # per-character 文件锁
        self._locks_guard = threading.Lock()  # 保护 _locks 字典本身
        self._persist_alocks: dict[str, asyncio.Lock] = {}

    def _get_lock(self, name: str) -> threading.Lock:
        """Get the character-specific file lock (lazily created)"""
        if name not in self._locks:
            with self._locks_guard:
                if name not in self._locks:  # double-check
                    self._locks[name] = threading.Lock()
        return self._locks[name]

    def _get_persist_alock(self, name: str) -> asyncio.Lock:
        """Serialize each character's load/dedup/mutate/save fact pipeline."""
        if name not in self._persist_alocks:
            with self._locks_guard:
                if name not in self._persist_alocks:
                    self._persist_alocks[name] = asyncio.Lock()
        return self._persist_alocks[name]

    # ── persistence ──────────────────────────────────────────────────

    def _facts_path(self, name: str) -> str:
        from memory import ensure_character_dir
        return os.path.join(ensure_character_dir(self._config_manager.memory_dir, name), 'facts.json')

    # v1→v2 entity key renames
    _ENTITY_RENAMES = {'user': 'master', 'ai': 'neko'}

    def load_facts(self, name: str) -> list[dict]:
        path = self._facts_path(name)
        if name in self._facts:
            return self._facts[name]
        with self._get_lock(name):
            # double-check: 另一个线程可能在等锁期间已经加载了
            if name in self._facts:
                return self._facts[name]
            if os.path.exists(path):
                try:
                    with open(path, encoding='utf-8') as f:
                        data = json.load(f)
                    if isinstance(data, list):
                        if self._migrate_v1_entity_values(data):
                            try:
                                assert_cloudsave_writable(
                                    self._config_manager,
                                    operation="migrate",
                                    target=f"memory/{name}/facts.json",
                                )
                                atomic_write_json(path, data, indent=2, ensure_ascii=False)
                                logger.info(f"[FactStore] {name}: v1→v2 entity 值迁移完成")
                            except MaintenanceModeError as exc:
                                logger.debug(f"[FactStore] {name}: 维护态跳过 facts.json 迁移落盘: {exc}")
                        self._facts[name] = data
                        return data
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning(f"[FactStore] 加载 facts 文件失败: {e}")
            self._facts[name] = []
            return self._facts[name]

    async def aload_facts(self, name: str) -> list[dict]:
        if name in self._facts:
            return self._facts[name]
        return await asyncio.to_thread(self.load_facts, name)

    def load_facts_full(self, name: str) -> list[dict]:
        """Full fact pool: active + archived (Phase C-2).

        Archived = old entries already moved into facts_archive.json by
        `_archive_absorbed` (absorbed more than _ARCHIVE_AGE_DAYS = 7 days ago).

        For scenarios where "distant history must be searchable" — currently the
        RELATED_CONTEXT recall of reflection synthesis. Returns a new list; the
        archive never enters the cache.

        A corrupted archive file degrades best-effort to active-only, no raise
        (incl. invalid UTF-8: daily import's fingerprint scan now reads the
        archive via this loader before any per-day isolation, so a non-UTF-8
        archive must degrade here instead of aborting the whole import — Codex
        P2). ``UnicodeDecodeError`` is a ``ValueError`` subclass, distinct from
        ``JSONDecodeError``, so it is listed explicitly."""
        active = self.load_facts(name)
        archive_path = self._facts_archive_path(name)
        if not os.path.exists(archive_path):
            return list(active)
        try:
            with open(archive_path, encoding='utf-8') as f:
                archived = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
            logger.warning(f"[FactStore] {name}: 读取 archive 失败，降级仅 active: {e}")
            return list(active)
        if not isinstance(archived, list):
            return list(active)
        return list(active) + [f for f in archived if isinstance(f, dict)]

    async def aload_facts_full(self, name: str) -> list[dict]:
        return await asyncio.to_thread(self.load_facts_full, name)

    @classmethod
    def _migrate_v1_entity_values(cls, facts: list[dict]) -> bool:
        """Rename v1 entity values ('user'→'master', 'ai'→'neko') in-place."""
        changed = False
        for f in facts:
            old = f.get('entity')
            new = cls._ENTITY_RENAMES.get(old)
            if new:
                f['entity'] = new
                changed = True
        return changed

    def save_facts(self, name: str) -> None:
        with self._get_lock(name):
            try:
                assert_cloudsave_writable(
                    self._config_manager,
                    operation="save",
                    target=f"memory/{name}/facts.json",
                )
                facts = self._facts.get(name, [])
                path = self._facts_path(name)
                # Read-merge-write: 保护其他进程/路径写入的 monotonic 标记
                # （只能从 False → True 单向翻的字段：absorbed、signal_processed）。
                # 否则旧 cache 的写路径会用 False 覆盖磁盘上的 True，让同一批
                # facts 被 drain loop 重复送进 Stage-2 / 重复合成 reflection。
                if os.path.exists(path):
                    try:
                        with open(path, encoding='utf-8') as f:
                            disk_facts = json.load(f)
                        if isinstance(disk_facts, list):
                            absorbed_ids = {
                                f['id'] for f in disk_facts
                                if isinstance(f, dict) and f.get('absorbed')
                            }
                            signal_processed_ids = {
                                f['id'] for f in disk_facts
                                if isinstance(f, dict) and f.get('signal_processed')
                            }
                            if absorbed_ids or signal_processed_ids:
                                for f in facts:
                                    if f.get('id') in absorbed_ids:
                                        f['absorbed'] = True
                                    if f.get('id') in signal_processed_ids:
                                        f['signal_processed'] = True
                    except (json.JSONDecodeError, OSError):
                        # Read-merge is best-effort: if the on-disk
                        # file is corrupt or unreadable, fall through
                        # and write whatever we have. The atomic
                        # write below will overwrite the bad payload.
                        pass
                atomic_write_json(path, facts, indent=2, ensure_ascii=False)
            except Exception:
                # Cache divergence guard (CodeRabbit PR-956 Major,
                # mirroring `PersonaManager.asave_persona`'s round-7
                # fix from PR #936). Callers like
                # `FactDedupResolver._aapply_decisions` mutate the
                # in-memory list directly via `facts[:] = [...]` and
                # then call us; if the disk write raises, the cache
                # still holds the post-mutation state but disk
                # doesn't, so the next `aload_facts` returns
                # divergent data. Evicting forces a fresh disk read.
                self._facts.pop(name, None)
                raise
            # 基于文件修改时间节流归档：距上次归档超过 _ARCHIVE_COOLDOWN_HOURS 才尝试
            try:
                archive_path = self._facts_archive_path(name)
                if os.path.exists(archive_path):
                    mtime = datetime.fromtimestamp(os.path.getmtime(archive_path))
                    if (datetime.now() - mtime).total_seconds() < _ARCHIVE_COOLDOWN_HOURS * 3600:
                        return
                # 用 marker 文件记录上次归档尝试时间（即使归档文件尚不存在）
                marker_path = archive_path + '.last_attempt'
                if os.path.exists(marker_path):
                    mtime = datetime.fromtimestamp(os.path.getmtime(marker_path))
                    if (datetime.now() - mtime).total_seconds() < _ARCHIVE_COOLDOWN_HOURS * 3600:
                        return
                self._archive_absorbed(name)
                # 更新 marker（无论归档是否有实际条目都 touch 一次）
                with open(marker_path, 'w', encoding='utf-8') as f:
                    f.write(datetime.now().isoformat())
            except Exception:
                pass

    async def asave_facts(self, name: str) -> None:
        await asyncio.to_thread(self.save_facts, name)

    def _facts_archive_path(self, name: str) -> str:
        from memory import ensure_character_dir
        return os.path.join(ensure_character_dir(self._config_manager.memory_dir, name), 'facts_archive.json')

    def _archive_absorbed(self, name: str) -> int:
        """Move facts that are absorbed and older than _ARCHIVE_AGE_DAYS into the archive file."""
        from datetime import timedelta
        assert_cloudsave_writable(
            self._config_manager,
            operation="archive",
            target=f"memory/{name}/facts.json",
        )
        facts = self._facts.get(name, [])
        cutoff = datetime.now() - timedelta(days=_ARCHIVE_AGE_DAYS)
        active, to_archive = [], []
        for f in facts:
            try:
                created = datetime.fromisoformat(f.get('created_at', ''))
            except (ValueError, TypeError):
                active.append(f)
                continue
            if f.get('absorbed') and created < cutoff:
                to_archive.append(f)
            else:
                active.append(f)
        if not to_archive:
            return 0
        # 追加到归档文件
        archive_path = self._facts_archive_path(name)
        existing_archive: list[dict] = []
        if os.path.exists(archive_path):
            try:
                with open(archive_path, encoding='utf-8') as fh:
                    data = json.load(fh)
                if isinstance(data, list):
                    existing_archive = data
            except (json.JSONDecodeError, OSError) as e:
                # 归档文件损坏 → 放弃本次归档，避免覆盖丢数据
                logger.warning(f"[FactStore] {name}: 读取归档文件失败，跳过本次归档: {e}")
                return 0
        existing_archive.extend(to_archive)
        atomic_write_json(archive_path, existing_archive, indent=2, ensure_ascii=False)
        # 原地更新活跃列表（保持对象引用不变，避免外部持有旧引用导致修改丢失）
        facts.clear()
        facts.extend(active)
        atomic_write_json(self._facts_path(name), facts, indent=2, ensure_ascii=False)
        logger.info(f"[FactStore] {name}: 归档 {len(to_archive)} 条已吸收的旧 facts，剩余 {len(active)} 条")
        return len(to_archive)

    # ── extraction ───────────────────────────────────────────────────

    @staticmethod
    def _format_conversation(messages: list, name_mapping: dict) -> str:
        """Serialize messages into the 'role | content' shape used by LLM prompts."""
        lines = []
        for msg in messages:
            role = name_mapping.get(getattr(msg, 'type', ''), getattr(msg, 'type', ''))
            content = getattr(msg, 'content', '')
            if isinstance(content, str):
                lines.append(f"{role} | {content}")
            elif isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict):
                        parts.append(item.get('text', f"|{item.get('type', '')}|"))
                    else:
                        parts.append(str(item))
                lines.append(f"{role} | {''.join(parts)}")
        return "\n".join(lines)

    @staticmethod
    def _strip_code_fence(raw: str) -> str:
        """Remove ```json ... ``` fences if present."""
        if not raw.startswith("```"):
            return raw
        match = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw)
        if match:
            return match.group(1).strip()
        return raw.replace("```json", "").replace("```", "").strip()

    async def _allm_call_with_retries(
        self, prompt: str, lanlan_name: str, tier: str, call_type: str,
        max_retries: int = 3,
        timeout: float = 60,
        extra_body=_DEFAULT_EXTRA_BODY,
    ):
        """Shared LLM helper: retry on network errors + JSON errors, same
        policy as the old `extract_facts`. Returns parsed JSON or None on
        terminal failure (caller decides whether to abort / swallow).

        Note: no longer accepts temperature. The project-wide convention is to
        never send that parameter (gatekeeper: scripts/check_no_temperature.py).
        The model comes straight from the api_config of ``tier``; the
        SETTING_PROPOSER_MODEL fallback is gone.

        The 60s default timeout suits background LLM calls (Stage-1 fact extract
        / Stage-2 signal detect / negative keyword check); callers may raise it
        as needed (e.g. pass 90s for Stage-2 with thinking on). SDK
        max_retries=0 avoids double-layer retries (the business layer already
        controls retries via its max_retries parameter).

        extra_body: the default _DEFAULT_EXTRA_BODY lets create_chat_llm resolve
        it per model (for most providers this disables thinking); explicitly
        passing None means "send no extra_body" → the model's default behavior
        (thinking models enter thinking mode).
        Phase D: Stage-2 signal detection explicitly passes None to enable thinking."""
        from openai import APIConnectionError, InternalServerError, RateLimitError
        from utils.llm_client import create_chat_llm_async

        retries = 0
        while retries < max_retries:
            try:
                set_call_type(call_type)
                api_config = self._config_manager.get_model_api_config(tier)
                from config import LLM_OUTPUT_GUARD_MAX_TOKENS
                _llm_kwargs = dict(
                    timeout=timeout,
                    max_retries=0,
                    max_completion_tokens=LLM_OUTPUT_GUARD_MAX_TOKENS,
                    provider_type=api_config.get('provider_type'),
                )
                if extra_body is not _DEFAULT_EXTRA_BODY:
                    _llm_kwargs['extra_body'] = extra_body
                llm = await create_chat_llm_async(  # noqa: LLM_OUTPUT_BUDGET  # budget + timeout live in _llm_kwargs above (splat invisible to the lint); guard is generous for variable-length JSON.
                    api_config['model'],
                    api_config['base_url'], api_config['api_key'],
                    **_llm_kwargs,
                )
                try:
                    resp = await llm.ainvoke(prompt)  # noqa: LLM_INPUT_BUDGET  # extract-facts prompt assembled from token-capped recent history components.
                finally:
                    await llm.aclose()
                raw = resp.content.strip()
                raw = self._strip_code_fence(raw)
                return robust_json_loads(raw)
            except (APIConnectionError, InternalServerError, RateLimitError) as e:
                retries += 1
                logger.warning(
                    f"[FactStore] {lanlan_name}: {call_type} 网络错误 {type(e).__name__}, "
                    f"重试 {retries}/{max_retries}"
                )
                if retries < max_retries:
                    await asyncio.sleep(2 ** (retries - 1))
                continue
            except json.JSONDecodeError as e:
                retries += 1
                logger.warning(
                    f"[FactStore] {lanlan_name}: {call_type} JSON 解析失败 "
                    f"(重试 {retries}/{max_retries}): {e}"
                )
                if retries < max_retries:
                    await asyncio.sleep(2 ** (retries - 1))
                continue
            except Exception as e:
                retries += 1
                logger.warning(
                    f"[FactStore] {lanlan_name}: {call_type} 失败 "
                    f"(重试 {retries}/{max_retries}): {type(e).__name__}: {e}"
                )
                if retries < max_retries:
                    await asyncio.sleep(2 ** (retries - 1))
                continue

        logger.warning(
            f"[FactStore] {lanlan_name}: {call_type} 达到最大重试 {max_retries}，放弃"
        )
        return None

    async def _allm_extract_facts(
        self, lanlan_name: str, messages: list,
    ) -> list[dict] | None:
        """Stage-1: pure extraction. Prompt carries no existing observations
        to avoid self-cycling (the LLM quoting an existing reflection back as a
        new fact). Returns the raw LLM-extracted list, or None on terminal failure."""
        _, _, _, _, name_mapping, _, _, _, _ = await self._config_manager.aget_character_data()
        name_mapping['ai'] = lanlan_name
        conversation_text = self._format_conversation(messages, name_mapping)

        prompt = get_fact_extraction_prompt(get_global_language()) \
            .replace('{CONVERSATION}', conversation_text) \
            .replace('{LANLAN_NAME}', lanlan_name) \
            .replace('{MASTER_NAME}', name_mapping.get('human', '主人'))

        extracted = await self._allm_call_with_retries(
            prompt, lanlan_name,
            tier=EVIDENCE_EXTRACT_FACTS_MODEL_TIER,
            call_type="memory_fact_extraction",
        )
        if extracted is None:
            return None
        if not isinstance(extracted, list):
            logger.warning(
                f"[FactStore] {lanlan_name}: Stage-1 返回非数组 "
                f"{type(extracted).__name__}，当作空列表处理"
            )
            return []
        return extracted

    # Source-tier 白名单。'user_observation' = path A 抽出的 user msg ground truth；
    # 'ai_disclosure' = path B 抽出的 AI 自我披露/屏幕上下文（trust-tier 较低）。
    # 老 fact 没 source 字段时按 'user_observation' 回退（向后兼容——pre-#PR
    # 时代所有 fact 都源自 user msg）。
    _SOURCE_VALUES = frozenset({'user_observation', 'ai_disclosure'})
    _SOURCE_DEFAULT = 'user_observation'

    @staticmethod
    def _apply_external_import_provenance(entry: dict, external_import: dict) -> None:
        """Stamp external-import provenance onto a fact entry: metadata, tags, the
        event_start_at derived from event_date, and signal_processed=True
        (external_import facts skip the Stage-2 evidence loop)."""
        entry['external_import'] = dict(external_import)
        entry['tags'] = ['external_import', str(external_import.get('format') or 'unknown')]
        entry['signal_processed'] = True
        event_date = external_import.get('event_date')
        if isinstance(event_date, str) and event_date:
            entry['event_start_at'] = f"{event_date}T00:00:00"

    async def _apersist_new_facts(
        self, lanlan_name: str, extracted: list[dict],
        *,
        default_source: str = 'user_observation',
        semantic_dedup: bool = True,
    ) -> list[dict]:
        async with self._get_persist_alock(lanlan_name):
            return await self._apersist_new_facts_locked(
                lanlan_name,
                extracted,
                default_source=default_source,
                semantic_dedup=semantic_dedup,
            )

    async def _apersist_new_facts_locked(
        self, lanlan_name: str, extracted: list[dict],
        *,
        default_source: str = 'user_observation',
        semantic_dedup: bool = True,
    ) -> list[dict]:
        """Dedup (SHA-256 + FTS5) + persist. importance < 5 facts are KEPT
        (RFC §3.1.3)—downstream `get_unabsorbed_facts(min_importance=5)`
        filters at read time.

        ``default_source``: the fallback when an LLM-emitted fact dict has no
        ``source`` field. Path A callers pass ``'user_observation'`` (also the
        default), path B callers pass ``'ai_disclosure'`` — a source field
        explicitly emitted by the LLM wins over the default.

        External migration batches may set ``semantic_dedup=False`` after
        preview to avoid one FTS5 search per candidate while holding the
        persistence lock. Exact SHA-256 deduplication still applies.

        Monotonic source upgrade: when SHA-256 hits an existing fact, normally
        skip without writing. **Sole exception**: the existing fact's source is
        'ai_disclosure' and the new fact's is 'user_observation' → upgrade
        existing.source in place + reset signal_processed=False so Stage-2
        re-evaluates. The reverse (user→ai) never downgrades — user
        corroboration is irreversible.
        """
        if default_source not in self._SOURCE_VALUES:
            default_source = self._SOURCE_DEFAULT

        new_facts: list[dict] = []
        upgraded_count = 0
        existing_facts = await self.aload_facts(lanlan_name)
        existing_hashes = {f.get('hash') for f in existing_facts if f.get('hash')}
        # hash → fact 的快查表（仅 upgrade 路径用）。aload_facts 已经 in-place
        # 缓存了 list，这里读不复制。
        hash_to_existing = {
            f.get('hash'): f for f in existing_facts if f.get('hash')
        }
        # id → fact 快查表：Stage-2 语义命中后按 id 找到既存 fact，比较 daily
        # event_date 决定是否豁免（跨日期重复事件不算 dup，CodeRabbit）。
        facts_by_id = {f.get('id'): f for f in existing_facts if f.get('id')}

        for fact in extracted:
            if not isinstance(fact, dict):
                continue
            text = fact.get('text', '').strip()
            if not text:
                continue
            try:
                importance = int(fact.get('importance', 5))
            except (ValueError, TypeError):
                importance = 5
            # Clamp to the documented 1..10 range so downstream consumers
            # can assume a well-formed value; dirty LLM output (-3, 999)
            # would otherwise leak straight into reflection synthesis
            # weighting and audit dashboards (CodeRabbit PR #929).
            if importance < 1:
                importance = 1
            elif importance > 10:
                importance = 10
            # RFC §3.1.3: **不再**在抽取入口硬丢 importance < 5。所有 fact
            # 一律落盘，消费侧按场景 min_importance= 过滤；保留完整 audit。

            # Entity whitelist: RFC uses exactly these three values. Any
            # other LLM output (common mistake: "user"→"master") gets
            # snapped back to "master" with a debug log so the miss is
            # visible but not alarming.
            raw_entity = fact.get('entity', 'master')
            if raw_entity in ('master', 'neko', 'relationship'):
                entity = raw_entity
            else:
                logger.debug(
                    f"[FactStore] {lanlan_name}: LLM 返回非法 entity={raw_entity!r}，回退到 master"
                )
                entity = 'master'

            # Source resolution: LLM 显式 source 优先 + 白名单 + default fallback
            raw_source = fact.get('source')
            if raw_source in self._SOURCE_VALUES:
                source = raw_source
            else:
                source = default_source

            # Stage 1: SHA-256 exact dedup（+ source monotonic upgrade）。
            # daily 导入 fact 以「event_date + 文本」为精确键：同一天重试仍幂等，
            # 不同日期的重复事件（如连着两天"去了健身房"）各自落盘、各留 provenance
            # （CodeRabbit）。盐进 'hash' 持久字段，重试对比的是同样盐化的值。
            external_import = fact.get('_external_import')
            if not isinstance(external_import, dict):
                external_import = None
            daily_event_date = (
                str(external_import.get('event_date'))
                if external_import
                and external_import.get('section') == 'daily'
                and external_import.get('event_date')
                else None
            )
            hash_input = f"{daily_event_date}\n{text}" if daily_event_date else text
            content_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:16]
            if content_hash in existing_hashes:
                existing = hash_to_existing.get(content_hash)
                if (
                    existing is not None
                    and existing.get('source', self._SOURCE_DEFAULT) == 'ai_disclosure'
                    and source == 'user_observation'
                ):
                    # Path A 用 user msg 印证了之前 path B 写过的 ai_disclosure fact
                    # → 升级 source + 重新进 Stage-2 evidence loop
                    existing['source'] = 'user_observation'
                    existing['signal_processed'] = False
                    # 若这条印证来自外部导入，补上 external_import provenance——否则
                    # SHA 命中直接 continue 会漏掉标签（external_import 语义会把
                    # signal_processed 置回 True，不进 Stage-2）(Codex P2)。
                    if external_import is not None:
                        self._apply_external_import_provenance(existing, external_import)
                    upgraded_count += 1
                continue

            # Stage 2: FTS5 semantic dedup (lightweight, no LLM)
            if semantic_dedup and self._time_indexed is not None:
                similar = await self._time_indexed.asearch_facts(lanlan_name, text, 3)
                is_dup = False
                for fid, score in similar:
                    if score >= -5:
                        continue
                    if daily_event_date:
                        # daily 候选：命中的既存 fact 若也是 daily 且日期不同 →
                        # 跨日期重复事件，不算语义重复（同日期近似命中仍挡住，
                        # 兜 LLM 重抽输出不稳定的重试幂等）。
                        hit = facts_by_id.get(fid)
                        hit_meta = (hit or {}).get('external_import')
                        hit_date = (
                            str(hit_meta.get('event_date'))
                            if isinstance(hit_meta, dict) and hit_meta.get('event_date')
                            else None
                        )
                        if hit_date and hit_date != daily_event_date:
                            continue
                    is_dup = True
                    break
                if is_dup:
                    continue

            created_at_iso = datetime.now().isoformat()
            # Event timing (schema v2): LLM 输出相对时间 (offset+unit)，系统
            # 按 created_at 当锚点解算成 ISO。fact 没有 temporal_scope，但事件
            # 起始时间在过时 block 渲染和未来重判时都需要——fallback_start=True
            # 保证一定有 event_start_at。end_at 是 optional（fact 多数是即时
            # 观察，无明确 end）。
            event_when_raw = normalize_event_when(fact.get('event_when'))
            event_start_at, event_end_at = compute_event_timestamps(
                event_when_raw,
                created_at_iso,
                fallback_start=True,
                fallback_end=False,
            )
            fact_entry = {
                'id': f"fact_{datetime.now().strftime('%Y%m%d%H%M%S')}_{content_hash[:8]}",
                'text': text,
                'importance': importance,
                'entity': entity,
                # Trust-tier source（path A 写 'user_observation'，path B 写
                # 'ai_disclosure'）。Stage-2 / 其他 evidence-loop 消费者按需 filter。
                # 老 fact 缺该字段时读侧默认 'user_observation' 向后兼容。
                'source': source,
                # RFC §2.7: tags 字段保留位但新 fact 默认写空，LLM 不再填
                'tags': [],
                'hash': content_hash,
                'created_at': created_at_iso,
                # Schema v2 (memory/temporal.py)：事件发生时间，LLM 用相对偏移
                # 输出（offset+unit），系统按 created_at 解算。event_when_raw
                # 留底供后续重判 / debug 反查。
                'event_when_raw': event_when_raw,
                'event_start_at': event_start_at,
                'event_end_at': event_end_at,
                'schema_version': MEMORY_SCHEMA_VERSION_CURRENT,
                'absorbed': False,  # True when consumed by a reflection
                # Stage-2 signal detection drain marker. False → still in queue
                # for the next idle-loop tick. amark_signal_processed() flips
                # to True after Stage-2 LLM returns successfully. Old facts.json
                # without this key are read with default=True (i.e. treated as
                # already processed) so an upgrade doesn't replay months of
                # history through Stage-2.
                #
                # ⚠️ source='ai_disclosure' fact：写盘时直接置 True，让 Stage-2
                # 永不取它。配合 aextract_facts_and_detect_signals 内部的
                # source filter 做双重防御，防漏。
                'signal_processed': (source == 'ai_disclosure'),
                # Vector-embedding cache (memory-enhancements P2 — see
                # memory/embeddings.py). Written as None so /process
                # returns immediately without blocking on embedding;
                # the background warmup worker fills the triple in
                # batches once the EmbeddingService is ready. Used by
                # the upcoming fact dedup path (cosine > threshold →
                # LLM arbitration queue).
                'embedding': None,
                'embedding_text_sha256': None,
                'embedding_model_id': None,
            }
            if external_import is not None:
                self._apply_external_import_provenance(fact_entry, external_import)
            existing_facts.append(fact_entry)
            existing_hashes.add(content_hash)
            facts_by_id[fact_entry['id']] = fact_entry
            # 同步更新 hash_to_existing：若本 batch 后续还有同 text 的 fact
            # 出现（如 LLM 偶发重复 / 同 batch 跨段抽到同一观察），下一轮命
            # 中 `content_hash in existing_hashes` 时能拿到本轮刚写入的
            # fact_entry 走 monotonic upgrade 路径。否则 hash_to_existing.
            # get() 返 None → 跳过 upgrade，新观察的 user_observation 升级被
            # 静默丢弃 (Codex P2 round-10 on PR #1408)。
            hash_to_existing[content_hash] = fact_entry
            new_facts.append(fact_entry)

            if self._time_indexed is not None:
                await self._time_indexed.aindex_fact(
                    lanlan_name, fact_entry['id'], text,
                )

        # Save if we either added new facts OR upgraded existing ones'
        # source field. Without the upgrade path: A 后 B 跑时撞到 hash 但
        # 上下源不同会丢 in-place 改的字段，下次启动 reload facts.json 就
        # 把升级 wipe 了。
        if new_facts or upgraded_count:
            await self.asave_facts(lanlan_name)
        if new_facts:
            logger.info(
                f"[FactStore] {lanlan_name}: 提取了 {len(new_facts)} 条新事实"
            )
            for nf in new_facts:
                logger.debug(
                    f"   - [{nf.get('entity','?')}/{nf.get('source','?')}] {nf.get('text','')[:80]}"
                )
        if upgraded_count:
            logger.info(
                f"[FactStore] {lanlan_name}: 升级 {upgraded_count} 条 ai_disclosure → user_observation "
                f"(user 印证后重入 Stage-2 evidence loop)"
            )

        return new_facts

    async def _aload_signal_targets(
        self, lanlan_name: str,
        reflection_engine=None, persona_manager=None,
        new_facts: list[dict] | None = None,
    ) -> list[dict]:
        """Assemble the Stage-2 `existing_observations` set.

        Per RFC §3.4.2 coverage rule:
          - all confirmed + promoted reflections (most recent first)
          - all non-protected persona entries

        Scale control (§3.4.2 end):
          - When ``new_facts`` is provided, the pool is routed through
            ``MemoryRecallReranker.aretrieve_candidates``, which owns
            the full pipeline regardless of vector service state:
            hard_filter (drops suppress / terminal / score<0 /
            protected) → coarse rank (cosine top-K when vectors are
            ready, evidence_score order otherwise) → optional LLM
            rerank.  Vectors save Stage-2 prompt tokens when ready;
            when not ready, behaviour collapses to filtered top-N by
            evidence_score, matching the legacy contract but with
            the suppression filter still applied.
          - When ``new_facts`` is empty, falls through to the legacy
            local top-N by evidence_score (no hard_filter — that
            shape predates P2 and is what idle-maintenance entry
            points expect).

        Injection pattern: memory_server wires `reflection_engine` / `persona_manager`
        references at call time. Without them we return empty, which simply
        makes Stage-2 skip (fail-open for unit tests).

        Returns list of {id, text, entity, evidence_score} — id is already
        in the `{target_type}.{entity}.{suffix}` shape the prompt expects.
        """
        now = datetime.now()
        pool: list[dict] = []

        # CodeRabbit follow-up：之前 reflection / persona 加载失败时被 try-except
        # 吞掉，只 debug log 后继续返回 partial pool。下游 caller 看到非空 pool
        # 会正常 mark batch processed，那部分失败的池里可能 reinforce/negate 的
        # signal 永久丢失。改成不 catch、直接 raise，让 caller (drain 路径) 用
        # try-except 捕获并跳过 mark，保证下轮 idle 重试。
        # NegKW caller (memory_server.py) 已有自己的 try-except，不受影响。
        if reflection_engine is not None:
            all_refl = await reflection_engine._aload_reflections_full(lanlan_name)
            for r in all_refl:
                if r.get('status') not in ('confirmed', 'promoted'):
                    continue
                pool.append({
                    'id': f"reflection.{r.get('id', '')}",
                    'raw_id': r.get('id', ''),
                    'target_type': 'reflection',
                    'text': r.get('text', ''),
                    'entity': r.get('entity', 'relationship'),
                    'score': evidence_score(r, now),
                    'embedding': r.get('embedding'),
                    'embedding_text_sha256': r.get('embedding_text_sha256'),
                    'embedding_model_id': r.get('embedding_model_id'),
                    'status': r.get('status'),
                    # Carry the AI-mention rate-limit suppress flag
                    # so MemoryRecallReranker._hard_filter can drop
                    # suppressed reflections from the rerank pool —
                    # reflections share persona's 5h-window mention
                    # gating (see ReflectionEngine._normalize_reflection).
                    # Codex PR-958 P2: without this, a vector-recall
                    # path with a suppressed reflection would slip
                    # past the filter and re-enter Stage-2 signal
                    # detection, defeating the suppression contract.
                    'suppress': r.get('suppress'),
                })

        if persona_manager is not None:
            persona = await persona_manager.aensure_persona(lanlan_name)
            for entity_key, section in persona.items():
                if not isinstance(section, dict):
                    continue
                for entry in section.get('facts', []):
                    if not isinstance(entry, dict):
                        continue
                    if entry.get('protected'):
                        # protected = character_card；evidence 对它永远
                        # inf，signal 施加它也没语义。跳过。
                        continue
                    pool.append({
                        'id': f"persona.{entity_key}.{entry.get('id', '')}",
                        'raw_id': entry.get('id', ''),
                        'target_type': 'persona',
                        'entity_key': entity_key,
                        'text': entry.get('text', ''),
                        'entity': entity_key,
                        'score': evidence_score(entry, now),
                        'embedding': entry.get('embedding'),
                        'embedding_text_sha256': entry.get('embedding_text_sha256'),
                        'embedding_model_id': entry.get('embedding_model_id'),
                        'suppress': entry.get('suppress'),
                    })

        # P2 step 3: route through MemoryRecallReranker whenever we have
        # a query, regardless of vector service state.  The reranker
        # owns the unified pipeline:
        #
        #   _hard_filter (drops suppressed / terminal / score<0 /
        #     protected) → coarse rank (cosine top-K when vectors are
        #     ready, evidence_score order otherwise) → optional LLM
        #     rerank (skipped automatically when vectors aren't
        #     available).
        #
        # An earlier version gated the call on
        # `reranker._service.is_available()` and fell through to a
        # bare `pool.sort(score)` when the service was INIT / LOADING /
        # DISABLED.  That meant `suppress=True` rows leaked into
        # Stage-2 whenever vectors weren't ready, since the bare sort
        # path didn't apply `_hard_filter` (CodeRabbit PR-956 Major).
        # Behaviour now stays stable across the warmup window.
        if new_facts:
            try:
                from memory.recall import MemoryRecallReranker
                reranker = MemoryRecallReranker()
                query_texts = [
                    f.get('text', '') for f in new_facts if f.get('text')
                ]
                return await reranker.aretrieve_candidates(
                    pool, query_texts,
                    budget=EVIDENCE_DETECT_SIGNALS_MAX_OBSERVATIONS,
                    config_manager=self._config_manager,
                )
            except Exception as e:
                logger.warning(
                    "[FactStore] vector+LLM rerank failed (%s: %s); "
                    "falling back to evidence_score order",
                    type(e).__name__, e,
                )

        # Fallback / legacy path: top-N by score DESC (most relevant
        # first). Reached when (a) ``new_facts`` is empty (no recall
        # query to drive the reranker), or (b) the reranker raised
        # mid-call.  This matches the pre-P2 behaviour exactly —
        # `_hard_filter` is intentionally NOT applied here because
        # the upstream consumers in the no-new_facts shape (some
        # idle-maintenance entry points) already operate on the
        # unfiltered pool.  CodeRabbit PR-956's Major was specifically
        # about the new_facts branch above silently bypassing the
        # filter when vectors weren't ready.
        pool.sort(key=lambda o: o.get('score', 0.0), reverse=True)
        return pool[:EVIDENCE_DETECT_SIGNALS_MAX_OBSERVATIONS]

    async def _allm_detect_signals(
        self, lanlan_name: str, new_facts: list[dict],
        existing_observations: list[dict],
    ) -> list[dict] | None:
        """Stage-2: map new facts onto existing observations with
        reinforces/negates signals. Returns validated signals (target_ids
        already filtered against existing_observations), or None on
        terminal failure.

        The cap on new_facts is enforced by the caller in
        ``aextract_facts_and_detect_signals`` via
        ``EVIDENCE_DETECT_SIGNALS_MAX_NEW_FACTS`` (drain mode: the overflow
        stays signal_processed=False, handled on the next idle)."""
        if not new_facts or not existing_observations:
            return []

        # Build prompt sections.
        # 关键：先按预算累计构造 budgeted_observations 子集，prompt 和后面
        # 的 valid_ids / id_to_obs 都从同一个子集构造。否则总量截断把尾部
        # observation 砍掉后，valid_ids 还来自全集，LLM 可能 hallucinate
        # 一个被截掉的 id 通过校验落到错误条目（CodeRabbit fingerprint
        # e625b666 抓到的 race）。
        from config import (
            EVIDENCE_PER_OBSERVATION_MAX_TOKENS,
            EVIDENCE_OBSERVATIONS_TOTAL_MAX_TOKENS,
        )
        from utils.tokenize import truncate_to_tokens, count_tokens
        new_facts_text = "\n".join(
            f"[{f.get('id', '')}] {truncate_to_tokens(f.get('text', '') or '', EVIDENCE_PER_OBSERVATION_MAX_TOKENS)}"
            for f in new_facts
        )
        # 累计 token 直到撞到总量上限，超过的尾部 obs 直接丢出本次 prompt。
        budgeted_observations: list[tuple[dict, str]] = []  # (obs, formatted_line)
        running = 0
        for o in existing_observations:
            line = (
                f"[{o['id']}] "
                f"{truncate_to_tokens(o.get('text', '') or '', EVIDENCE_PER_OBSERVATION_MAX_TOKENS)}"
            )
            line_tokens = count_tokens(line) + 1  # +1 ≈ 一个换行符
            if budgeted_observations and running + line_tokens > EVIDENCE_OBSERVATIONS_TOTAL_MAX_TOKENS:
                # 至少保留一条；超过总量后丢尾部
                break
            budgeted_observations.append((o, line))
            running += line_tokens
        if not budgeted_observations:
            return []
        obs_text = "\n".join(line for _, line in budgeted_observations)
        prompt = get_signal_detection_prompt(get_global_language()) \
            .replace('{NEW_FACTS}', new_facts_text) \
            .replace('{EXISTING_OBSERVATIONS}', obs_text) \
            .replace('{LANLAN_NAME}', lanlan_name)

        # Phase D：Stage-2 signal detection 开 thinking——
        # 任务是 new_fact × existing_observation 的关系判断 + target_id 选择，
        # 现有 [memory/facts.py:670-708](memory/facts.py:670) 防御代码本身就是
        # 在补 LLM 幻觉，思考能减少 target_id 错位。完全后台 (signal extraction
        # loop)，无人等。timeout 拉到 90s 给 thinking 模型留余量。
        parsed = await self._allm_call_with_retries(
            prompt, lanlan_name,
            tier=EVIDENCE_DETECT_SIGNALS_MODEL_TIER,
            call_type="memory_signal_detection",
            timeout=90,
            extra_body=None,
        )
        if parsed is None:
            return None
        if not isinstance(parsed, dict):
            logger.warning(
                f"[FactStore] {lanlan_name}: Stage-2 返回非 dict "
                f"{type(parsed).__name__}，丢弃"
            )
            return []
        raw_signals = parsed.get('signals', [])
        if not isinstance(raw_signals, list):
            return []

        # Defensive: drop hallucinated target_ids (§3.4.8). 校验池**必须**和
        # prompt 看到的子集一致，否则被尾部预算切掉的 obs id 仍会被当成合法。
        valid_ids = {o['id'] for o, _ in budgeted_observations}
        id_to_obs = {o['id']: o for o, _ in budgeted_observations}
        # source_fact_id 也要校验在本批 new_facts 里（CodeRabbit 1f follow-up）。
        # 否则 LLM hallucinate 一个不在本次 prompt 里的 fact id 仍会被作为合法
        # source 落到 evidence 计数器更新里。
        new_fact_ids = {f['id'] for f in new_facts if f.get('id')}
        validated: list[dict] = []
        # 单次 Stage-2 调用可能返回 N 条 signal 都对同一 reflection 报告
        # target_type 不一致——LLM 在猜命名规范（"persona.relationship"
        # vs "persona" vs "persona.relationship.prom"），看到啥前缀就抄啥。
        # 兜底逻辑一直按设计在跑，但每条一行 log 会刷屏；按 (LLM值→实际值)
        # 去重计数，循环结束后一行汇总，方便看出"哪种猜法在被反复纠"。
        target_type_fixes: dict[tuple[str | None, str], int] = {}
        for s in raw_signals:
            if not isinstance(s, dict):
                continue
            tid = s.get('target_id')
            ttype = s.get('target_type')
            signal = s.get('signal')
            if signal not in ('reinforces', 'negates'):
                continue
            sid = s.get('source_fact_id')
            if sid is not None and sid not in new_fact_ids:
                logger.warning(
                    f"[FactStore] {lanlan_name}: Stage-2 返回 source_fact_id="
                    f"{sid!r} 不在本批 new_facts 里，丢弃"
                )
                continue
            # Reconstruct full prompt-space id if LLM returned just the raw id
            candidate_full = tid
            if tid not in valid_ids:
                # Try prefixing (LLM sometimes returns just "r_xxx" instead of
                # "reflection.r_xxx"). Match by endswith on prompt ids.
                candidates = [vid for vid in valid_ids if vid.endswith(f".{tid}")]
                if len(candidates) == 1:
                    candidate_full = candidates[0]
                else:
                    logger.warning(
                        f"[FactStore] {lanlan_name}: Stage-2 返回未知 "
                        f"target_id={tid}，丢弃"
                    )
                    continue
            obs = id_to_obs[candidate_full]
            if obs['target_type'] != ttype:
                # LLM 说的 target_type 与实际不符 → 以实际为准（修正）。
                # 不在 loop 里 log，循环结束统一汇总输出。
                # ttype 来自 LLM JSON，理论上是 str/None；hallucinate 成
                # list/dict 时直接进 dict key 会 TypeError 把整个 Stage-2 拖崩
                # （codex review #1414）。用 repr 把非 hashable 值兜成 str。
                key_ttype = ttype if ttype is None or isinstance(ttype, str) else repr(ttype)
                target_type_fixes[(key_ttype, obs['target_type'])] = (
                    target_type_fixes.get((key_ttype, obs['target_type']), 0) + 1
                )
            validated.append({
                'source_fact_id': s.get('source_fact_id'),
                'target_type': obs['target_type'],
                'target_id': obs['raw_id'],
                'target_full_id': candidate_full,
                'entity_key': obs.get('entity_key'),   # 只 persona 有
                'signal': signal,
                'reason': s.get('reason', ''),
            })
        if target_type_fixes:
            summary = ", ".join(
                f"{src!r}→{dst}×{n}" if n > 1 else f"{src!r}→{dst}"
                for (src, dst), n in sorted(
                    target_type_fixes.items(), key=lambda kv: -kv[1]
                )
            )
            logger.info(
                f"[FactStore] {lanlan_name}: Stage-2 target_type 修正 {summary}"
            )
        return validated

    async def aextract_facts_and_detect_signals(
        self, lanlan_name: str, messages: list,
        reflection_engine=None, persona_manager=None,
    ) -> tuple[list[dict], list[dict], list[str]]:
        """Two-stage extraction (RFC §3.4.2) with drain semantics.

        Stage-1: pure fact extraction from user messages — no existing
        observations in prompt to avoid self-cycling.
        Stage-2: new_facts × existing_observations → reinforces/negates
        signals (with defensive target_id validation).

        Drain (PR #976):
        - Facts extracted in Stage-1 are persisted with ``signal_processed=False``
        - Stage-2 pulls **all** facts with signal_processed=False (not just this
          round's new ones, also the unfinished tail of previous rounds), takes
          the top N (=EVIDENCE_DETECT_SIGNALS_MAX_NEW_FACTS) by importance DESC
          into Stage-2, and leaves the rest untouched for the next idle tick to
          drain

        Returns ``(new_facts_this_round, signals, batch_fact_ids)``:
        - ``new_facts_this_round``: facts newly extracted + persisted by this
          round's Stage-1 (for outbox and other audit purposes)
        - ``signals``: evidence signals awaiting dispatch
        - ``batch_fact_ids``: fact ids processed by this Stage-2 round — **the
          caller must call ``amark_signal_processed(lanlan_name, batch_fact_ids)``
          to complete the checkpoint only after every signal has been applied
          successfully via aapply_signal**. If the caller crashes during/after
          dispatch, the next idle sees the signal_processed=False facts, re-runs
          Stage-2, regenerates the signals and retries the dispatch (CodeRabbit
          fingerprint c755101c).

        Failure semantics (§3.4.2, last paragraph):
        - Stage-1 failure → abort, no fact written; caller retries later
        - Stage-2 LLM failure → batch_fact_ids still returns []; the caller
          won't mark, and the next idle retries the same batch
        - dispatch failure (caller side) → the caller doesn't call amark;
          retried next round
        """
        extracted = await self._allm_extract_facts(lanlan_name, messages)
        if extracted is None:
            # Stage-1 terminal failure — caller MUST NOT advance cursor
            # (§3.4.3 "Stage-1 失败 → 整次 abort，... 下次 idle 触发再试")
            raise FactExtractionFailed(
                f"Stage-1 LLM call exhausted retries for {lanlan_name!r}"
            )
        if not extracted:
            extracted = []

        # Persist 本轮新抽到的 facts（带 signal_processed=False 入库）。
        # 即使本轮抽到 0 条，下面仍要 drain 上轮没处理完的 unprocessed 尾部。
        persisted_this_round = await self._apersist_new_facts(lanlan_name, extracted)

        # Drain：拉所有 signal_processed=False 的 facts（含历史尾部 + 本轮新增）。
        # 老 facts 没这个字段时 default=True，避免升级后把几个月历史 fact
        # 一起重跑 Stage-2。
        #
        # ⚠️ Source filter：排除 source='ai_disclosure'——AI 自我披露的 fact
        # 不进 evidence loop（防自我强化死循环：AI 说"我喜欢 X" → 抽出 → Stage-2
        # 给 reflection "neko likes X" 涨分 → AI 更频繁说"我喜欢 X" → ...）。
        # path B 写盘时已经把 signal_processed 置 True 兜底（_apersist_new_facts），
        # 此处 source filter 做双重防御，防新加路径忘了置 signal_processed。
        # 老 fact 缺 source 字段时按 'user_observation' 回退（向后兼容）。
        all_facts = await self.aload_facts(lanlan_name)
        unprocessed = [
            f for f in all_facts
            if not f.get('signal_processed', True)
            and f.get('source', self._SOURCE_DEFAULT) != 'ai_disclosure'
        ]
        if not unprocessed:
            return persisted_this_round, [], []

        # 按 importance DESC + 创建时间 ASC 排序，取前 N 条做这一批 batch。
        # 多余的留 signal_processed=False 给下一轮 idle tick。
        unprocessed.sort(
            key=lambda f: (
                -safe_importance(f),
                str(f.get('created_at') or ''),
            ),
        )
        batch = unprocessed[:EVIDENCE_DETECT_SIGNALS_MAX_NEW_FACTS]

        try:
            existing_observations = await self._aload_signal_targets(
                lanlan_name,
                reflection_engine=reflection_engine,
                persona_manager=persona_manager,
                # 用 batch 而不是仅本轮新增作为 query，向量召回更聚焦。
                new_facts=batch,
            )
        except Exception as e:
            # _aload_signal_targets 现在不再吞 reflection/persona load 异常
            # （CodeRabbit 1f follow-up：partial pool + checkpoint 会让失败池
            # 那部分的 signal 永久丢失）。任一 manager raise 时整轮放弃 mark，
            # 下轮 idle 重试。
            logger.warning(
                f"[FactStore] {lanlan_name}: _aload_signal_targets 失败，"
                f"跳过本轮 Stage-2 mark，下轮 idle 重试: {e}"
            )
            return persisted_this_round, [], []
        if not existing_observations:
            # 真为空（冷启动 / persona 池只有 protected）：故意**不**返回
            # batch_fact_ids，让 caller 不 mark，下轮 idle tick 重试同一批。
            # 代价是冷启动每轮跑一次 _aload_signal_targets（无 LLM 调用）；
            # 收益是绝不丢 signal（CodeRabbit fingerprint e625b666）。
            return persisted_this_round, [], []

        signals = await self._allm_detect_signals(
            lanlan_name, batch, existing_observations,
        )
        if signals is None:
            # Stage-2 LLM failure: 不返回 batch_ids，caller 不 mark，下轮重试
            return persisted_this_round, [], []

        # Stage-2 成功 → 返回 batch_fact_ids 让 caller 在 dispatch 全部成功
        # 后调 amark_signal_processed。**不**在这里立刻 mark：caller 还没有
        # 把 signals 喂给 PersonaManager / ReflectionEngine.aapply_signal，
        # 中途崩溃或部分失败时这批 fact 必须能下轮重跑（CodeRabbit c755101c）。
        # 即使 signals=[]（LLM 看过认为没关联）也返回 batch_ids，caller 看到
        # 空 signals 直接当 dispatch_ok=True 调 amark，避免下轮空跑。
        batch_fact_ids = [f['id'] for f in batch]
        return persisted_this_round, signals, batch_fact_ids

    async def extract_facts(self, messages: list, lanlan_name: str) -> list[dict]:
        """Stage-1-only backward-compat entry.

        Kept for callers that predate the evidence mechanism
        (memory_server's _run_post_turn_signals OFF-mode fallback transitively
        calls this, plus outbox replay). Emits only new facts and skips
        signal detection — downstream `_periodic_signal_extraction_loop`
        runs Stage-1+Stage-2 together.

        Unlike the Stage-1+2 entry, a Stage-1 terminal failure is swallowed
        here (returns []): the legacy per-turn call site treats extraction as
        best-effort — the next turn / the background loop will retry.
        """
        extracted = await self._allm_extract_facts(lanlan_name, messages)
        if not extracted:
            return []
        return await self._apersist_new_facts(lanlan_name, extracted)

    # ── external import state (sidecar) ──────────────────────────────

    def _external_import_state_path(self, name: str) -> str:
        from memory import ensure_character_dir
        return os.path.join(
            ensure_character_dir(self._config_manager.memory_dir, name),
            'external_import_state.json',
        )

    def _load_external_import_state(self, name: str) -> dict:
        """Best-effort read of the per-character external-import sidecar.

        Missing / corrupt / non-dict payloads degrade to ``{}``: the worst
        case is re-extracting already-imported days (wasted LLM calls), never
        data loss, so this read must not fail an import.
        """
        path = self._external_import_state_path(name)
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
                # UnicodeDecodeError（非法 UTF-8 字节，ValueError 子类，非
                # JSONDecodeError/OSError）也要降级：_aload_imported_day_fps 在
                # per-day 隔离**之前**跑，一个损坏 sidecar 冒泡会 abort 整个导入，
                # 违背 docstring 承诺的「降级空集」（Codex P2）。
                logger.warning(
                    f"[FactStore] {name}: 读取 external_import_state 失败，降级为空: {e}"
                )
        return {}

    @staticmethod
    def _state_daily_fingerprints(state: dict) -> set[str]:
        """Processed-day fingerprint set held by the sidecar state dict."""
        daily = state.get('daily')
        fps = daily.get('imported_day_fingerprints') if isinstance(daily, dict) else None
        if not isinstance(fps, list):
            return set()
        return {str(x) for x in fps if x}

    @staticmethod
    def _facts_have_day_fingerprint(facts: list[dict], fingerprint: str) -> bool:
        """Whether any fact's ``external_import`` provenance carries this day's
        fingerprint — i.e. the day IS carried by a fact and must NOT get a
        sidecar entry.

        Covers both a newly appended fact and an existing fact whose provenance
        was upgraded in place (e.g. ai_disclosure→user_observation on a same-day
        exact-hash hit): both stamp ``day_fingerprint``, both ride facts.json's
        rollback lifecycle and self-heal, so a sidecar entry for such a day
        would outlive a rollback and suppress the re-extraction (Codex P2)."""
        for f in facts:
            meta = f.get('external_import')
            if isinstance(meta, dict) and str(meta.get('day_fingerprint') or '') == fingerprint:
                return True
        return False

    async def _aload_imported_day_fps(self, name: str) -> set[str]:
        """Union of processed-day fingerprints from two carriers, split by
        whether the day persisted any fact:

        - **Fact provenance** (``external_import.day_fingerprint`` inside
          facts.json / facts_archive.json) is authoritative for days that
          persisted at least one fact. It rides the fact data's own cloudsave
          sync / restore / rollback lifecycle, so a rolled-back day loses its
          fingerprint together with its facts and re-imports self-heal —
          exactly #2383's behavior. Days-with-facts therefore do **not** get a
          sidecar entry (see ``_arecord_unpersisted_day_fp``): a sidecar record
          that outlived a facts.json rollback would permanently suppress the
          re-extraction that restores the day.
        - **Sidecar** (``external_import_state.json``) carries **only** days the
          LLM judged to hold no fact at all (empty extraction). A day whose
          extracted facts were *all deduped away* is NOT recorded here — its
          content is already carried by the deduping facts (under a different
          fingerprint, so a rollback of those facts couldn't self-heal a sidecar
          entry), so it re-extracts instead (added=0, no data lost — chosen over
          a fact-carried multi-fingerprint scheme). Re-extracting a truly
          fact-less day is harmless, so a stale / desynced sidecar never loses
          data (Codex P2 follow-up).

        Provenance is read over active + archive (``aload_facts_full``) so a day
        whose facts were archived by ``_archive_absorbed`` (>7 days old) still
        skips instead of re-extracting. Used only for the up-front skip filter;
        the persist-time concurrent re-check reads active provenance directly
        (a rival import that just persisted writes the active list, and a
        sidecar-only rival must not suppress this request's real facts).
        """
        state = await asyncio.to_thread(self._load_external_import_state, name)
        fps = self._state_daily_fingerprints(state)
        for f in await self.aload_facts_full(name):
            meta = f.get('external_import')
            if isinstance(meta, dict) and meta.get('day_fingerprint'):
                fps.add(str(meta['day_fingerprint']))
        return fps

    async def _arecord_unpersisted_day_fp(self, name: str, fingerprint: str) -> None:
        """Best-effort: record a day's fingerprint in the sidecar **iff no fact
        carries it**.

        Called only for days the LLM judged fact-less (empty extraction); a day
        whose extracted facts all deduped away re-extracts instead of being
        sidecar-recorded (see ``_aload_imported_day_fps``). The
        active-provenance re-check runs **inside** the persist alock (the same
        lock as fact persistence): a concurrent same-character import that
        persists this day's facts either lands before us — we see its provenance
        and skip the sidecar — or after us, blocked on the lock. So a day that
        ends up with a fact carrier never also gets a sidecar entry (which would
        outlive a facts.json rollback and suppress the self-healing
        re-extraction, Codex P2). No TOCTOU; the same critical section also
        absorbs the sidecar's own read-modify-write.

        Best-effort by design: the sidecar is a pure idempotency accelerator.
        A write failure (disk full / permission; the cloudsave fence is already
        rejected at the import entrypoint) degrades to re-extracting this day on
        the next import — the exact #2383-era cost — so it must never escalate
        a day that otherwise succeeded into a failed_day / HTTP 500. Fact
        persistence keeps its own hard ``assert_cloudsave_writable`` semantics;
        only this accelerator layer is soft.
        """
        try:
            async with self._get_persist_alock(name):
                active = await self.aload_facts(name)
                if self._facts_have_day_fingerprint(active, fingerprint):
                    return
                await asyncio.to_thread(
                    self._record_imported_day_fp_locked, name, fingerprint
                )
        except (MaintenanceModeError, OSError) as exc:
            logger.warning(
                f"[FactStore] {name}: sidecar 落盘失败（降级为下次重导重抽该天）: {exc}"
            )

    def _record_imported_day_fp_locked(self, name: str, fingerprint: str) -> None:
        assert_cloudsave_writable(
            self._config_manager,
            operation="save",
            target=f"memory/{name}/external_import_state.json",
        )
        state = self._load_external_import_state(name)
        fps = self._state_daily_fingerprints(state)
        if fingerprint in fps:
            return
        fps.add(fingerprint)
        state.setdefault('version', 1)
        daily = state.get('daily')
        if not isinstance(daily, dict):
            daily = {}
            state['daily'] = daily
        # 对偶 persona folded_fingerprints：集合语义、sorted 落盘（稳定可 diff）。
        daily['imported_day_fingerprints'] = sorted(fps)
        atomic_write_json(
            self._external_import_state_path(name), state,
            indent=2, ensure_ascii=False,
        )

    async def _aclear_day_fp_if_present(self, name: str, fingerprint: str) -> None:
        """Best-effort: drop a day's fingerprint from the sidecar once a fact
        carries it, so a fact-backed day never also lives in the sidecar.

        Fires the write only when the sidecar actually holds this fingerprint —
        a concurrent sidecar-only import (empty / all-dedup LLM outcome) wrote it
        before this request's real facts landed. Left in place, the day would
        have both a fact provenance entry and a stale sidecar entry, and a later
        facts.json rollback would leave the sidecar to suppress the re-extraction
        that restores the fact (Codex P2). Same per-character alock +
        best-effort contract as ``_arecord_unpersisted_day_fp``."""
        try:
            async with self._get_persist_alock(name):
                await asyncio.to_thread(self._clear_day_fp_locked, name, fingerprint)
        except (MaintenanceModeError, OSError) as exc:
            logger.warning(
                f"[FactStore] {name}: sidecar 清理陈旧指纹失败（无害，下次导入再清）: {exc}"
            )

    def _clear_day_fp_locked(self, name: str, fingerprint: str) -> None:
        path = self._external_import_state_path(name)
        if not os.path.exists(path):
            return  # 绝大多数成功天无 sidecar 文件 → 只一次 stat、不碰盘。
        state = self._load_external_import_state(name)
        fps = self._state_daily_fingerprints(state)
        if fingerprint not in fps:
            return
        assert_cloudsave_writable(
            self._config_manager,
            operation="save",
            target=f"memory/{name}/external_import_state.json",
        )
        fps.discard(fingerprint)
        daily = state.get('daily')
        if not isinstance(daily, dict):
            return
        daily['imported_day_fingerprints'] = sorted(fps)
        atomic_write_json(
            self._external_import_state_path(name), state,
            indent=2, ensure_ascii=False,
        )

    async def aimport_external_daily(
        self, lanlan_name: str, candidates: list[dict], source_format: str,
        imported_at: str,
    ) -> dict:
        """LLM-extract facts from imported daily journals (Stage-1, no signals).

        Mirrors the persona side (``afuse_external_facts``): external daily files
        (``memory/`` or ``memories/YYYY-MM-DD.md``) are free-form journal prose,
        so rather than appending their raw fragments verbatim they are run through
        the conversation fact-extraction LLM. Candidates are grouped by source
        file (one file == one day); each day's fragments are joined into a single
        user turn and extracted independently so the day's ``event_date`` can be
        stamped onto every fact it yields. A day whose joined fragments exceed
        ``EXTERNAL_IMPORT_DAILY_INPUT_MAX_TOKENS`` is split into multiple
        extraction batches (``batch_daily_fragments``) rather than truncated —
        no journal tail is silently dropped (Greptile P1). Days run concurrently
        under ``EXTERNAL_IMPORT_DAILY_MAX_CONCURRENCY`` (a month of journals run
        sequentially would blow past the upstream 240s forwarding window);
        batches within a day run sequentially; persistence stays serialized by
        the per-character persist lock. Best-effort per day, atomic within a
        day: when any batch fails (None) or crashes the whole day persists
        nothing — no facts, no fingerprint — and is counted in ``failed_days``
        so the caller surfaces a retryable partial result; the retry re-extracts
        that day from scratch (a partially-persisted day would fingerprint-skip
        forever, Greptile P1).

        Idempotency mirrors persona ``folded_fingerprints`` via each day's
        content fingerprint, held by the fact carrier when there is one and by
        the sidecar only when there is none: days that persisted (or upgraded in
        place) a fact carry it in their ``external_import`` provenance (inside
        facts.json, so it shares the fact data's cloudsave lifecycle and
        self-heals on rollback); days the LLM judged fact-less (empty
        extraction) record it in the ``external_import_state.json`` sidecar,
        which would otherwise have no home and re-run the LLM + burn cap quota on
        every re-import (Codex P2 follow-up). A day whose extracted facts all
        deduped away is left to re-extract (its content is already carried by
        the deduping facts under a different fingerprint; recording a sidecar
        entry that a facts rollback couldn't self-heal was rejected). The read
        side unions provenance + sidecar (see ``_aload_imported_day_fps``). A
        re-imported day whose fragments are unchanged is skipped outright — zero
        LLM calls (Codex P2). Changed content re-extracts; near-identical
        re-extraction output is absorbed by the same-date FTS5 dedup in
        ``_apersist_new_facts``.

        After fingerprint filtering, more than ``EXTERNAL_IMPORT_DAILY_MAX_FILES``
        genuinely-new days raises ``ExternalMemoryImportTooLargeError`` — an
        unbounded workspace would mean hundreds of LLM calls and blow the 240s
        window even under bounded concurrency; the frontend guides splitting
        the import, and already-imported days keep skipping for free (Codex P2).

        Returns ``{'added': int, 'days': int, 'failed_days': int, 'skipped_days': int}``.
        """
        from memory.external_markdown_import import batch_daily_fragments
        from memory.persona.fusion import ExternalMemoryImportTooLargeError
        from utils.llm_client import convert_to_messages

        by_file: dict[str, list[dict]] = {}
        for cand in candidates:
            if not isinstance(cand, dict):
                continue
            by_file.setdefault(str(cand.get("source_file") or ""), []).append(cand)

        # 逐日指纹幂等（对偶 persona folded_fingerprints）：已导入且内容未变的
        # 天直接 skip，不进 LLM。双载体合并（有 fact 天靠 provenance，无 fact 天
        # 靠 sidecar；含 archive 覆盖已归档天，见 _aload_imported_day_fps）。
        imported_day_fps = await self._aload_imported_day_fps(lanlan_name)

        day_dates = {
            source_file: next(
                (str(g["event_date"]) for g in group if g.get("event_date")), None
            )
            for source_file, group in by_file.items()
        }
        # 指纹掺 event_date：不同日期的重复例行日记（文本逐字相同）各自是新的
        # 一天，不能被对方的指纹 skip（Codex P2）——与 fact 去重键含日期同理。
        day_fps = {
            source_file: self._daily_fingerprint(
                [str(g.get("text") or "") for g in group],
                event_date=day_dates[source_file],
            )
            for source_file, group in by_file.items()
        }
        pending = {
            source_file: group
            for source_file, group in by_file.items()
            if day_fps[source_file] not in imported_day_fps
        }
        skipped_days = len(by_file) - len(pending)

        # 分批预计算 + cap 按「总抽取调用数」而非天数：单个超大日记文件能拆出
        # 几十批串行调用，len(pending) 拦不住它撞 240s 墙（Codex P2）。tiktoken
        # 编码是同步 CPU，offload 线程池。
        batches_by_file: dict[str, list[str]] = await asyncio.to_thread(
            lambda: {
                source_file: batch_daily_fragments(
                    [p for p in (str(g.get("text") or "").strip() for g in group) if p],
                    EXTERNAL_IMPORT_DAILY_INPUT_MAX_TOKENS,
                )
                for source_file, group in pending.items()
            }
        )
        total_batches = sum(len(b) for b in batches_by_file.values())
        if total_batches > EXTERNAL_IMPORT_DAILY_MAX_FILES:
            raise ExternalMemoryImportTooLargeError(
                f"daily import needs {total_batches} extraction calls across "
                f"{len(pending)} new journal days (cap {EXTERNAL_IMPORT_DAILY_MAX_FILES}); "
                "split the workspace"
            )

        llm_slots = asyncio.Semaphore(EXTERNAL_IMPORT_DAILY_MAX_CONCURRENCY)

        async def _extract_one_day(source_file: str, group: list[dict]) -> tuple[int, bool]:
            """One day's extraction+persist; returns (added, day_failed)."""
            event_date = day_dates[source_file]
            batches = batches_by_file[source_file]
            # 先抽完该天全部批次、**任一批失败则整天不落盘**：若早批先落盘（带
            # 全天指纹）而后批失败，重试会被指纹整天 skip、失败批内容永久丢失
            # （Greptile P1）。整天原子化后，失败天既无 fact 也无指纹，重试从头
            # 重抽；persist 自身崩溃同理由 gather 计入 failed_days 且无指纹残留。
            day_extracted: list[dict] = []
            for batch_text in batches:
                messages = convert_to_messages(
                    [{"role": "user", "content": batch_text}]
                )
                # 只有 LLM 往返占并发槽；写盘走 _apersist_new_facts 的 per-character
                # 锁自行互斥，放在槽外让别的日子的 LLM 调用尽早起跑。
                async with llm_slots:
                    extracted = await self._allm_extract_facts(lanlan_name, messages)
                if extracted is None:
                    logger.warning(
                        f"[FactStore] {lanlan_name}: 外部 daily 抽取 LLM 失败，"
                        f"放弃 {source_file}（整天重试重抽）"
                    )
                    return 0, True
                day_extracted.extend(f for f in extracted if isinstance(f, dict))
            if not day_extracted:
                # 空抽取天：LLM 判该日无 fact，无 fact 载体存指纹，只能靠 sidecar，
                # 否则每次重导都重抽该天并占 cap 配额（Codex P2 follow-up）。
                # _arecord 锁内二次确认 active 无该天 provenance 才落（兜并发对方
                # 已 persist 真实 facts 的情况）；best-effort 写失败退回重抽、不升级
                # failed_day。
                await self._arecord_unpersisted_day_fp(lanlan_name, day_fps[source_file])
                return 0, False
            # 并发缩窗重查：两个同角色 commit 可能都在开头读过 imported_day_fps
            # 才各自跑 LLM；persist 前重读一次，若对方已把这天真实 facts 落盘则
            # 放弃本次写入（措辞不同的重复 facts 会绕过精确去重）。剩余极窄的
            # TOCTOU 窗口由同日期 FTS5 近似去重兜底；前端 in-flight 单飞锁已挡住
            # 单客户端的并发导入（Codex P2）。
            # 只认 active fact provenance、**不查 sidecar**：本请求已抽出非空
            # facts，不能被对方并发的 sidecar-only（空抽取/全去重、无 fact 载体）
            # 结果挤掉——那会让「无 fact 的 sidecar」压掉真实抽取的 facts（Codex）。
            active_now = await self.aload_facts(lanlan_name)
            if self._facts_have_day_fingerprint(active_now, day_fps[source_file]):
                logger.info(
                    f"[FactStore] {lanlan_name}: {source_file} 已被并发导入落盘，"
                    "放弃本次写入"
                )
                return 0, False
            for fact in day_extracted:
                # Stamp provenance; _apersist_new_facts_locked turns event_date
                # into event_start_at and tags the entry as external_import.
                # day_fingerprint 是重导幂等的依据（见 docstring）。
                fact["_external_import"] = {
                    "format": source_format,
                    "file": source_file,
                    "section": "daily",
                    "event_date": event_date,
                    "imported_at": imported_at,
                    "day_fingerprint": day_fps[source_file],
                }
            new_facts = await self._apersist_new_facts(
                lanlan_name, day_extracted, semantic_dedup=True,
            )
            # 有 fact 落盘的天不记 sidecar：指纹已在 fact provenance 里、与 facts
            # 同处 cloudsave 同步/回滚单元，回滚后一起消失、重导自愈（若额外记
            # sidecar，回滚后残留指纹永久压制重抽 → 记忆丢失）。new_facts 为空时
            # 尝试记 sidecar，但 _arecord 会在锁内二次确认 active 无该天 provenance
            # 才落——兜住「exact-hash 命中把 provenance upgrade 到既有 ai_disclosure
            # fact」这类 new_facts 为空却有载体的天（Codex P2 follow-up）。
            if new_facts:
                # 真实 facts 落盘（provenance 载体）：清掉并发 sidecar-only 竞态可能
                # 残留的该天 sidecar 指纹，维持「有 fact 载体的天不在 sidecar」不变式
                # （Codex）。绝大多数成功天 sidecar 无此天 → 一次 stat 即返回。
                await self._aclear_day_fp_if_present(lanlan_name, day_fps[source_file])
            # else 全去重天（抽出 fact 但全 exact-hash 命中既有、无新载体）：**不记
            # sidecar**。这天的内容已被既有 fact 承载，但那条 fact 带的是别的指纹、
            # 与本天新指纹不在同一 cloudsave 回滚单元，记 sidecar 会在 facts 回滚后
            # 残留压制重抽 → fact 回不来（Codex）。退回每次重导重抽（added=0、不丢
            # 数据）；只有 LLM 判定完全无 fact 的空抽取天才靠 sidecar。
            return len(new_facts), False

        outcomes = await asyncio.gather(
            *(_extract_one_day(f, g) for f, g in pending.items()),
            return_exceptions=True,
        )
        added = 0
        failed_days = 0
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                failed_days += 1
                logger.error(
                    f"[FactStore] {lanlan_name}: 外部 daily 抽取单日崩溃，已跳过该日",
                    exc_info=outcome,
                )
                continue
            day_added, day_failed = outcome
            added += day_added
            if day_failed:
                failed_days += 1
        return {
            "added": added,
            "days": len(by_file),
            "failed_days": failed_days,
            "skipped_days": skipped_days,
        }

    @staticmethod
    def _daily_fingerprint(texts: list[str], *, event_date: str | None = None) -> str:
        """Whitespace/case-normalized, **order-preserving** fingerprint over one
        day's fragment texts, salted with the day's ``event_date``. Journals are
        narrative — reordering entries (e.g. "stopped medication" vs "started
        medication" swapped) changes meaning, so an edited order must re-extract
        instead of fingerprint-skipping (Greptile P1); and a routine journal
        repeated verbatim on a different date is a **new** day, not a duplicate
        (Codex P2). persona's ``_fusion_fingerprint`` stays sorted and unsalted
        by design: its candidates are an unordered, date-less set."""
        norm = [" ".join((t or "").casefold().split()) for t in texts]
        payload = f"{event_date or ''}\n" + "\n".join(norm)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def aextract_facts_with_known_pool(
        self,
        lanlan_name: str,
        messages: list,
        known_pool: list[dict],
    ) -> list[dict] | None:
        """AI-aware Stage-1 (path B) extraction — input is the role-tagged full
        user+ai message set; the prompt embeds ``known_pool`` (facts path A
        already extracted in the same window) as a "do-not-repeat" list so the
        LLM dedupes proactively at the output layer.

        Differences from ``extract_facts``:
        - Uses the new prompt (``FACT_EXTRACTION_AI_AWARE_PROMPT``) instead of the basic one
        - The prompt adds a known-pool section + trust-tier guidance + a source-field output requirement
        - Persists with default_source='ai_disclosure' (a source explicitly emitted by the LLM still wins)
        - No Stage-2 (path B by design never enters the evidence loop)

        Returns:
            - ``None``: Stage-1 terminal failure — retries exhausted / LLM
              returned a non-array (e.g. wrapped as ``{"facts": [...]}``). The
              caller should keep the cursor so the next trigger retries the same
              window.
            - ``[]``: Stage-1 succeeded and the LLM judged the window to contain
              0 new facts (fully deduped). The caller may advance the cursor.
            - ``list[dict]``: succeeded with N new facts extracted and persisted.

            The None / [] distinction is critical: collapsing None into [] would
            make path B treat a failed window as "successfully extracted 0" on
            LLM transient failures / malformed payloads and advance the cursor,
            permanently skipping those messages (CodeRabbit / Codex P1 round-2 +
            Codex P1 round-9 on PR #1408).
        """
        extracted = await self._allm_extract_facts_with_known_pool(
            lanlan_name, messages, known_pool,
        )
        if extracted is None:
            return None
        if not extracted:
            return []
        return await self._apersist_new_facts(
            lanlan_name, extracted, default_source='ai_disclosure',
        )

    async def _allm_extract_facts_with_known_pool(
        self,
        lanlan_name: str,
        messages: list,
        known_pool: list[dict],
    ) -> list[dict] | None:
        """Stage-1 LLM call for path B: role-tagged conversation + known pool。

        Returns raw LLM-extracted list, or None on terminal failure
        (caller swallows in aextract_facts_with_known_pool)."""
        from config.prompts.prompts_memory import get_fact_extraction_ai_aware_prompt

        _, _, _, _, name_mapping, _, _, _, _ = await self._config_manager.aget_character_data()
        name_mapping['ai'] = lanlan_name
        conversation_text = self._format_conversation(messages, name_mapping)

        # Known pool 段渲染：按 importance DESC 排（最重要的在最前，给 LLM
        # 最强信号）。cap 已经在 caller 端做过，这里不重复。
        known_lines = []
        for f in known_pool:
            text = f.get('text', '') or ''
            if not text:
                continue
            imp = f.get('importance', 5)
            known_lines.append(f"- {text} (importance: {imp})")
        known_block = "\n".join(known_lines) if known_lines else "(none)"

        prompt = get_fact_extraction_ai_aware_prompt(get_global_language()) \
            .replace('{CONVERSATION}', conversation_text) \
            .replace('{KNOWN_POOL}', known_block) \
            .replace('{LANLAN_NAME}', lanlan_name) \
            .replace('{MASTER_NAME}', name_mapping.get('human', '主人'))

        extracted = await self._allm_call_with_retries(
            prompt, lanlan_name,
            tier=EVIDENCE_EXTRACT_FACTS_MODEL_TIER,
            call_type="memory_fact_extraction_ai_aware",
        )
        if extracted is None:
            return None
        if not isinstance(extracted, list):
            # 非数组 payload（如 `{"facts": [...]}` 包了一层、或 LLM 偶发瞎写）
            # 等同 Stage-1 terminal failure 处理——返 None 让 `_run_path_b`
            # 保留 cursor 下次 trigger 重试同窗口，而不是当成"成功 0 抽"推
            # cursor 永久 skip 这段消息（Codex P1 round-9 on PR #1408）。
            logger.warning(
                f"[FactStore] {lanlan_name}: path-B Stage-1 返回非数组 "
                f"{type(extracted).__name__}，按 terminal failure 处理 (cursor 不推进)"
            )
            return None
        return extracted

    # ── query helpers ────────────────────────────────────────────────

    def get_unabsorbed_facts(self, name: str, min_importance: int = 5) -> list[dict]:
        """Get facts that haven't been consumed by a reflection yet."""
        facts = self.load_facts(name)
        return [
            f for f in facts
            if not f.get('absorbed') and f.get('importance', 0) >= min_importance
        ]

    async def aget_unabsorbed_facts(self, name: str, min_importance: int = 5) -> list[dict]:
        facts = await self.aload_facts(name)
        return [
            f for f in facts
            if not f.get('absorbed') and f.get('importance', 0) >= min_importance
        ]

    def get_facts_by_entity(self, name: str, entity: str) -> list[dict]:
        facts = self.load_facts(name)
        return [f for f in facts if f.get('entity') == entity]

    def mark_absorbed(self, name: str, fact_ids: list[str]) -> None:
        """Mark facts as absorbed by a reflection."""
        facts = self.load_facts(name)
        id_set = set(fact_ids)
        changed = False
        for f in facts:
            if f.get('id') in id_set and not f.get('absorbed'):
                f['absorbed'] = True
                changed = True
        if changed:
            self.save_facts(name)

    async def amark_absorbed(self, name: str, fact_ids: list[str]) -> None:
        await asyncio.to_thread(self.mark_absorbed, name, fact_ids)

    def mark_signal_processed(self, name: str, fact_ids: list[str]) -> None:
        """Mark facts as having gone through Stage-2 signal detection.

        Mirrors `mark_absorbed`'s shape so the drain loop in
        `aextract_facts_and_detect_signals` can checkpoint a batch after
        the LLM call returns. Old on-disk facts that lack the field are
        treated as already processed (default=True) at read time, so
        re-flipping them here is a no-op.
        """
        facts = self.load_facts(name)
        id_set = set(fact_ids)
        changed = False
        for f in facts:
            if f.get('id') in id_set and not f.get('signal_processed', False):
                f['signal_processed'] = True
                changed = True
        if changed:
            self.save_facts(name)

    async def amark_signal_processed(self, name: str, fact_ids: list[str]) -> None:
        await asyncio.to_thread(self.mark_signal_processed, name, fact_ids)

    def _bump_fact_recheck_attempts(self, name: str, fid: str, reason: str) -> None:
        """Increment the given fact's ``recheck_attempts`` counter.

        Once failures reach the ``MEMORY_RECHECK_MAX_ATTEMPTS`` cap, the
        candidates filter excludes the fact so the loop gives its slot to other
        v1 entries. Directly mutates the cached list + save_facts (mirroring the
        mark_absorbed style; save_facts takes its own lock).
        Best-effort — save failures don't raise.
        """
        try:
            current = self.load_facts(name)
            for f in current:
                if f.get('id') == fid:
                    f['recheck_attempts'] = (f.get('recheck_attempts') or 0) + 1
                    # 戳失败时刻供 dead-letter 时间自愈（cooldown_elapsed）
                    f['last_recheck_attempt_at'] = datetime.now().isoformat()
                    self.save_facts(name)
                    logger.debug(
                        f"[Recheck-Fact] {name} {fid}: "
                        f"recheck_attempts → {f['recheck_attempts']} ({reason})"
                    )
                    return
        except Exception as e:
            logger.debug(f"[Recheck-Fact] {name} {fid}: bump attempts 失败: {e}")

    async def arecheck_one_legacy_fact(self, name: str) -> bool:
        """Schema v1 → v2 slow recheck (processes only 1 fact per call).

        Finds the character's oldest fact with schema_version < CURRENT (main
        facts.json only, archive shards excluded), asks the LLM to fill in
        event_when, resolves event_start_at / event_end_at against created_at
        and writes them back. Facts have no temporal_scope, so this is lighter
        than the reflection recheck.

        Returns: True when one fact was processed successfully; False when no
        candidate was found or processing failed.
        """
        from config import (
            MEMORY_RECHECK_MAX_ATTEMPTS,
            MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS,
        )
        from config.prompts.prompts_memory import MEMORY_RECHECK_FACT_PROMPT
        from memory.temporal import (
            normalize_event_when as _norm_when,
            compute_event_timestamps as _compute_ts,
            cooldown_elapsed,
        )

        facts = await self.aload_facts(name)
        candidates = [
            f for f in facts
            if (f.get('schema_version') or 1) < MEMORY_SCHEMA_VERSION_CURRENT
            # 重试预算：LLM 持续失败的 entry 累计达上限后不再阻塞队列
            # (Codex review on PR #1316 P2，对齐 reflection 同样写法)。
            # 时间自愈：达上限的 entry 过 MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS
            # 后放行一次 probe，让一次性写盘/网络故障恢复后自愈。
            and (
                (f.get('recheck_attempts') or 0) < MEMORY_RECHECK_MAX_ATTEMPTS
                or cooldown_elapsed(
                    f.get('last_recheck_attempt_at'),
                    MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS,
                )
            )
        ]
        if not candidates:
            return False
        candidates.sort(key=lambda f: (f.get('created_at', ''), f.get('id', '')))
        # Skip malformed candidates (missing id / created_at) instead of
        # aborting the whole call — otherwise a single bad legacy entry at
        # head of FIFO order would starve every later v1 fact forever
        # (Codex review on PR #1316 P2 catch).
        target: dict | None = None
        fid = ''
        created_at_iso = ''
        for c in candidates:
            cid = c.get('id')
            cts = c.get('created_at', '')
            if not cid or not cts:
                logger.debug(
                    f"[Recheck-Fact] {name}: skip malformed legacy fact "
                    f"(id={cid!r} created_at={cts!r})"
                )
                continue
            target = c
            fid = cid
            created_at_iso = cts
            break
        if target is None:
            return False

        prompt = MEMORY_RECHECK_FACT_PROMPT.format(
            FACT_TEXT=target.get('text', ''),
            CREATED_AT=created_at_iso,
        )

        failure_reason: str | None = None
        event_when_raw: dict | None = None
        try:
            from utils.llm_client import create_chat_llm_async
            set_call_type("memory_recheck_fact")
            api_config = self._config_manager.get_model_api_config('summary')
            from config import LLM_OUTPUT_GUARD_MAX_TOKENS
            llm = await create_chat_llm_async(
                api_config['model'],
                api_config['base_url'], api_config['api_key'],
                timeout=60, max_retries=0,
                max_completion_tokens=LLM_OUTPUT_GUARD_MAX_TOKENS,  # runaway guard; generous so variable-length JSON (incl. thinking) isn't truncated
                extra_body=None,
                provider_type=api_config.get('provider_type'),
            )
            try:
                resp = await llm.ainvoke(prompt)  # noqa: LLM_INPUT_BUDGET  # recheck prompt assembled from token-capped memory components.
            finally:
                await llm.aclose()
            raw = resp.content.strip()
            if raw.startswith("```"):
                raw = raw.replace("```json", "").replace("```", "").strip()
            result = robust_json_loads(raw)
            if not isinstance(result, dict):
                failure_reason = "non-dict response"
            else:
                event_when_raw = _norm_when(result.get('event_when'))
        except Exception as e:
            failure_reason = f"LLM call failed: {e}"

        # 失败路径统一收口：bump recheck_attempts，让连续失败的 entry 在达到
        # MAX 后被 candidates filter 排除（Codex review on PR #1316 P2，对齐
        # reflection 同样写法）。
        if failure_reason is not None:
            logger.debug(
                f"[Recheck-Fact] {name} {fid}: 跳过本轮 ({failure_reason})"
            )
            await asyncio.to_thread(self._bump_fact_recheck_attempts, name, fid, failure_reason)
            return False

        event_start_at, event_end_at = _compute_ts(
            event_when_raw,
            created_at_iso,
            fallback_start=True,
            fallback_end=False,
        )

        # 锁策略：和 mark_absorbed / mark_signal_processed (本文件 line 984
        # 附近) 一致——直接 mutate `load_facts` 返回的 cached list（CPython
        # 字段赋值是 atomic），不在外层套 _get_lock。save_facts 内部 (line 163)
        # 会自取 lock + read-merge-write 兜底并发安全。
        # 为什么不套外层锁：_get_lock 用 threading.Lock（非 reentrant），先
        # acquire 再调 save_facts 会自我死锁（Codex review on PR #1316 catch）。
        def _apply_update() -> bool:
            current = self.load_facts(name)
            found = None
            for f in current:
                if f.get('id') == fid:
                    found = f
                    break
            if found is None:
                return False
            if (found.get('schema_version') or 1) >= MEMORY_SCHEMA_VERSION_CURRENT:
                return False
            found['event_when_raw'] = event_when_raw
            found['event_start_at'] = event_start_at
            found['event_end_at'] = event_end_at
            found['schema_version'] = MEMORY_SCHEMA_VERSION_CURRENT
            self.save_facts(name)
            return True

        try:
            ok = await asyncio.to_thread(_apply_update)
        except Exception as e:
            logger.warning(f"[Recheck-Fact] {name} {fid}: save 失败: {e}")
            # 落盘失败也计入 recheck_attempts：否则 cloudsave 维护态 / 只读 FS /
            # 权限导致的持续写盘失败会让同一条 fact 每 30s 原样重判、熔断永不
            # 触发（对齐上面 LLM 失败路径的 bump）。
            await asyncio.to_thread(
                self._bump_fact_recheck_attempts, name, fid, f"save failed: {e}",
            )
            return False
        if ok:
            logger.info(
                f"[Recheck-Fact] {name} {fid}: v1→v{MEMORY_SCHEMA_VERSION_CURRENT} "
                f"when={event_when_raw}"
            )
        return ok
