# -*- coding: utf-8 -*-
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

from config import SETTING_PROPOSER_MODEL
from config.prompts_memory import get_fact_extraction_prompt
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


class FactStore:
    """Manages raw fact extraction, deduplication, and persistence."""

    def __init__(self, *, time_indexed_memory: TimeIndexedMemory | None = None):
        self._config_manager = get_config_manager()
        self._time_indexed = time_indexed_memory
        self._facts: dict[str, list[dict]] = {}  # {lanlan_name: [fact, ...]}
        self._locks: dict[str, threading.Lock] = {}  # per-character 文件锁
        self._locks_guard = threading.Lock()  # 保护 _locks 字典本身

    def _get_lock(self, name: str) -> threading.Lock:
        """获取角色专属的文件锁（懒创建）"""
        if name not in self._locks:
            with self._locks_guard:
                if name not in self._locks:  # double-check
                    self._locks[name] = threading.Lock()
        return self._locks[name]

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
                            atomic_write_json(path, data, indent=2, ensure_ascii=False)
                            logger.info(f"[FactStore] {name}: v1→v2 entity 值迁移完成")
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
            facts = self._facts.get(name, [])
            path = self._facts_path(name)
            # Read-merge-write: 保护其他进程写入的 absorbed 标记
            if os.path.exists(path):
                try:
                    with open(path, encoding='utf-8') as f:
                        disk_facts = json.load(f)
                    if isinstance(disk_facts, list):
                        absorbed_ids = {
                            f['id'] for f in disk_facts
                            if isinstance(f, dict) and f.get('absorbed')
                        }
                        if absorbed_ids:
                            for f in facts:
                                if f.get('id') in absorbed_ids:
                                    f['absorbed'] = True
                except (json.JSONDecodeError, OSError):
                    pass
            atomic_write_json(path, facts, indent=2, ensure_ascii=False)
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
                with open(marker_path, 'w') as f:
                    f.write(datetime.now().isoformat())
            except Exception:
                pass

    async def asave_facts(self, name: str) -> None:
        await asyncio.to_thread(self.save_facts, name)

    def _facts_archive_path(self, name: str) -> str:
        from memory import ensure_character_dir
        return os.path.join(ensure_character_dir(self._config_manager.memory_dir, name), 'facts_archive.json')

    def _archive_absorbed(self, name: str) -> int:
        """将已 absorbed 且超过 _ARCHIVE_AGE_DAYS 的 facts 移入归档文件。"""
        from datetime import timedelta
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

    async def extract_facts(self, messages: list, lanlan_name: str) -> list[dict]:
        """Extract facts from a conversation using LLM.

        Returns list of new (non-duplicate) facts that were stored.
        """
        from openai import APIConnectionError, InternalServerError, RateLimitError
        from utils.llm_client import create_chat_llm

        _, _, _, _, name_mapping, _, _, _, _ = await self._config_manager.aget_character_data()
        name_mapping['ai'] = lanlan_name

        # Build conversation text
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
        conversation_text = "\n".join(lines)

        prompt = get_fact_extraction_prompt(get_global_language()).replace('{CONVERSATION}', conversation_text)
        prompt = prompt.replace('{LANLAN_NAME}', lanlan_name)
        prompt = prompt.replace('{MASTER_NAME}', name_mapping.get('human', '主人'))

        retries = 0
        max_retries = 3
        while retries < max_retries:
            try:
                set_call_type("memory_fact_extraction")
                api_config = self._config_manager.get_model_api_config('summary')
                llm = create_chat_llm(
                    api_config.get('model', SETTING_PROPOSER_MODEL),
                    api_config['base_url'], api_config['api_key'],
                    temperature=0.3,
                )
                try:
                    resp = await llm.ainvoke(prompt)
                finally:
                    await llm.aclose()
                raw = resp.content.strip()
                if raw.startswith("```"):
                    match = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw)
                    if match:
                        raw = match.group(1).strip()
                    else:
                        raw = raw.replace("```json", "").replace("```", "").strip()
                extracted = robust_json_loads(raw)
                if not isinstance(extracted, list):
                    logger.warning(f"[FactStore] {lanlan_name}: LLM 返回非数组类型 {type(extracted).__name__}，重试")
                    retries += 1
                    if retries < max_retries:
                        await asyncio.sleep(2 ** (retries - 1))
                    continue
                break
            except (APIConnectionError, InternalServerError, RateLimitError) as e:
                retries += 1
                logger.warning(f"[FactStore] {lanlan_name}: 网络错误 {type(e).__name__}，重试 {retries}/{max_retries}")
                if retries < max_retries:
                    await asyncio.sleep(2 ** (retries - 1))
                continue
            except json.JSONDecodeError as e:
                retries += 1
                print(f"⚠️ [FactStore] {lanlan_name}: JSON 解析失败 (重试 {retries}/{max_retries}): {e}")
                print(f"⚠️ [FactStore] 原始返回: {raw[:500]}")
                if retries < max_retries:
                    await asyncio.sleep(2 ** (retries - 1))
                continue
            except Exception as e:
                retries += 1
                logger.warning(f"[FactStore] {lanlan_name}: 事实提取失败 (重试 {retries}/{max_retries}): {type(e).__name__}: {e}")
                if retries < max_retries:
                    await asyncio.sleep(2 ** (retries - 1))
                continue
        else:
            logger.warning(f"[FactStore] {lanlan_name}: 事实提取达到最大重试次数 {max_retries}，放弃")
            return []

        # Deduplicate and store
        new_facts = []
        existing_facts = await self.aload_facts(lanlan_name)
        existing_hashes = {f.get('hash') for f in existing_facts if f.get('hash')}

        for fact in extracted:
            text = fact.get('text', '').strip()
            if not text:
                continue
            try:
                importance = int(fact.get('importance', 5))
            except (ValueError, TypeError):
                importance = 5
            if importance < 5:
                continue

            # Stage 1: SHA-256 exact dedup
            content_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
            if content_hash in existing_hashes:
                continue

            # Stage 2: FTS5 semantic dedup (lightweight, no LLM)
            if self._time_indexed is not None:
                similar = await self._time_indexed.asearch_facts(lanlan_name, text, 3)
                is_dup = False
                for fid, score in similar:
                    if score < -5:
                        is_dup = True
                        break
                if is_dup:
                    continue

            fact_entry = {
                'id': f"fact_{datetime.now().strftime('%Y%m%d%H%M%S')}_{content_hash[:8]}",
                'text': text,
                'importance': importance,
                'entity': fact.get('entity', 'master'),
                'tags': fact.get('tags', []),
                'hash': content_hash,
                'created_at': datetime.now().isoformat(),
                'absorbed': False,  # True when consumed by a reflection
            }
            existing_facts.append(fact_entry)
            existing_hashes.add(content_hash)
            new_facts.append(fact_entry)

            # Index in FTS5
            if self._time_indexed is not None:
                await self._time_indexed.aindex_fact(lanlan_name, fact_entry['id'], text)

        if new_facts:
            await self.asave_facts(lanlan_name)
            print(f"📝 [FactStore] {lanlan_name}: 提取了 {len(new_facts)} 条新事实")
            for nf in new_facts:
                print(f"   - [{nf.get('entity','?')}] {nf.get('text','')[:80]}")
            logger.info(f"[FactStore] {lanlan_name}: 提取了 {len(new_facts)} 条新事实")

        return new_facts

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
