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

from config import (
    EVIDENCE_DETECT_SIGNALS_MAX_OBSERVATIONS,
    EVIDENCE_DETECT_SIGNALS_MAX_NEW_FACTS,
    EVIDENCE_DETECT_SIGNALS_MODEL_TIER,
    EVIDENCE_EXTRACT_FACTS_MODEL_TIER,
)
from config.prompts_memory import (
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


class FactExtractionFailed(RuntimeError):
    """Stage-1 LLM call exhausted retries (RFC §3.4.2 末段).

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
    ):
        """Shared LLM helper: retry on network errors + JSON errors, same
        policy as the old `extract_facts`. Returns parsed JSON or None on
        terminal failure (caller decides whether to abort / swallow).

        Note: 不再接受 temperature。项目级约定一律不下发该参数（守门见
        scripts/check_no_temperature.py）。模型从 ``tier`` 对应的 api_config
        直接拿，不再走 SETTING_PROPOSER_MODEL fallback。"""
        from openai import APIConnectionError, InternalServerError, RateLimitError
        from utils.llm_client import create_chat_llm

        retries = 0
        while retries < max_retries:
            try:
                set_call_type(call_type)
                api_config = self._config_manager.get_model_api_config(tier)
                llm = create_chat_llm(
                    api_config['model'],
                    api_config['base_url'], api_config['api_key'],
                )
                try:
                    resp = await llm.ainvoke(prompt)
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
        to avoid self-cycling (LLM摘 existing reflection back as new fact).
        Returns raw LLM-extracted list, or None on terminal failure."""
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

    async def _apersist_new_facts(
        self, lanlan_name: str, extracted: list[dict],
    ) -> list[dict]:
        """Dedup (SHA-256 + FTS5) + persist. importance < 5 facts are KEPT
        (RFC §3.1.3)—downstream `get_unabsorbed_facts(min_importance=5)`
        filters at read time."""
        new_facts: list[dict] = []
        existing_facts = await self.aload_facts(lanlan_name)
        existing_hashes = {f.get('hash') for f in existing_facts if f.get('hash')}

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
                'entity': entity,
                # RFC §2.7: tags 字段保留位但新 fact 默认写空，LLM 不再填
                'tags': [],
                'hash': content_hash,
                'created_at': datetime.now().isoformat(),
                'absorbed': False,  # True when consumed by a reflection
                # Stage-2 signal detection drain marker. False → still in queue
                # for the next idle-loop tick. amark_signal_processed() flips
                # to True after Stage-2 LLM returns successfully. Old facts.json
                # without this key are read with default=True (i.e. treated as
                # already processed) so an upgrade doesn't replay months of
                # history through Stage-2.
                'signal_processed': False,
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
            existing_facts.append(fact_entry)
            existing_hashes.add(content_hash)
            new_facts.append(fact_entry)

            if self._time_indexed is not None:
                await self._time_indexed.aindex_fact(
                    lanlan_name, fact_entry['id'], text,
                )

        if new_facts:
            await self.asave_facts(lanlan_name)
            logger.info(
                f"[FactStore] {lanlan_name}: 提取了 {len(new_facts)} 条新事实"
            )
            for nf in new_facts:
                logger.debug(
                    f"   - [{nf.get('entity','?')}] {nf.get('text','')[:80]}"
                )

        return new_facts

    async def _aload_signal_targets(
        self, lanlan_name: str,
        reflection_engine=None, persona_manager=None,
        new_facts: list[dict] | None = None,
    ) -> list[dict]:
        """Assemble the Stage-2 `existing_observations` set.

        Per RFC §3.4.2 coverage rule:
          - confirmed + promoted reflection 全量（最 recent 优先）
          - 非 protected persona entry 全量

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

        new_facts 的数量上限由调用方在 ``aextract_facts_and_detect_signals``
        里按 ``EVIDENCE_DETECT_SIGNALS_MAX_NEW_FACTS`` 控制（drain 模式：
        超出部分留 signal_processed=False 下次 idle 再处理）。"""
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

        parsed = await self._allm_call_with_retries(
            prompt, lanlan_name,
            tier=EVIDENCE_DETECT_SIGNALS_MODEL_TIER,
            call_type="memory_signal_detection",
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
                # LLM 说的 target_type 与实际不符 → 以实际为准（修正），
                # 但仍记录原值便于 debug
                logger.info(
                    f"[FactStore] {lanlan_name}: Stage-2 target_type 修正 "
                    f"{ttype}→{obs['target_type']} for {candidate_full}"
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

        Drain (PR #976)：
        - Stage-1 抽取后 facts 落盘带 ``signal_processed=False``
        - Stage-2 拉**所有** signal_processed=False 的 facts（不止本轮新抽
          的，也包括上轮没处理完的尾部），按 importance DESC 取前 N
          (=EVIDENCE_DETECT_SIGNALS_MAX_NEW_FACTS) 进 Stage-2，多余的留
          原状下次 idle tick 再 drain

        Returns ``(new_facts_this_round, signals, batch_fact_ids)``：
        - ``new_facts_this_round``: 本轮 Stage-1 新抽出 + 落盘的 facts
          （供 outbox 等审计用途）
        - ``signals``: 待 dispatch 的 evidence 信号
        - ``batch_fact_ids``: 本次 Stage-2 处理的 fact id 列表 —— **caller
          在全部 signal 通过 aapply_signal 应用成功后**必须调用
          ``amark_signal_processed(lanlan_name, batch_fact_ids)`` 完成
          checkpoint。若 caller 在 dispatch 中途 / 之后崩溃，下轮 idle 会
          看到 signal_processed=False 的 facts 重抽 Stage-2 重新生成
          signals 重试 dispatch（CodeRabbit fingerprint c755101c）。

        Failure semantics (§3.4.2 末段):
        - Stage-1 failure → abort, no fact written; caller retries later
        - Stage-2 LLM failure → batch_fact_ids 仍然返回 []，caller 不会
          mark，下轮 idle 重试同一批
        - dispatch failure（caller 端）→ caller 不调 amark，下轮重试
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
        all_facts = await self.aload_facts(lanlan_name)
        unprocessed = [
            f for f in all_facts
            if not f.get('signal_processed', True)
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
        (memory_server's _extract_facts_and_check_feedback flow transitively
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
