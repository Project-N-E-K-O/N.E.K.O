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
ReflectionEngine — Tier 2 of the three-tier memory hierarchy.

Synthesizes multiple Tier-1 facts into higher-level reflections (insights).
Reflections start as "pending" and require feedback confirmation before
being promoted to persona (Tier 3).

Cognitive flow:
  Facts(passive) → Reflection(active thinking) → Persona(confirmed & solidified)

Trigger: called during proactive chat, NOT during every conversation.
This allows reflection to double as a "callback" mechanism where the AI naturally
mentions its observations and gauges the user's response.

Auto-promotion: pending reflections that remain 3 days without denial are
automatically promoted to persona.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from config import (
    EVIDENCE_CONFIRMED_THRESHOLD,
    EVIDENCE_PROMOTE_MAX_RETRIES,
    EVIDENCE_PROMOTE_RETRY_BACKOFF_MINUTES,
    EVIDENCE_PROMOTED_THRESHOLD,
    EVIDENCE_PROMOTION_MERGE_MODEL_TIER,
)
from memory.evidence import evidence_score, initial_reinforcement_from_importance
from utils.cloudsave_runtime import assert_cloudsave_writable
from utils.config_manager import get_config_manager
from utils.file_utils import (
    atomic_write_json,
    atomic_write_json_async,
    read_json_async,
    robust_json_loads,
)
from utils.logger_config import get_module_logger
from utils.token_tracker import set_call_type
from memory.persona import (
    PersonaManager,
    SUPPRESS_COOLDOWN_HOURS,
    SUPPRESS_MENTION_LIMIT,
    SUPPRESS_WINDOW_HOURS,
    _is_mentioned,
)
from memory.stop_names import acollect_stop_names
from memory._reflection.ontology import (
    ENTITY_KINDS as _ONTOLOGY_ENTITY_KINDS,
    KIND_RELATION_MAP as _ONTOLOGY_KIND_RELATION_MAP,
    MAX_REFLECTION_TEXT_TOKENS as _ONTOLOGY_MAX_REFLECTION_TEXT_TOKENS,
    RELATION_TYPES as _ONTOLOGY_RELATION_TYPES,
    TEMPORAL_SCOPES as _ONTOLOGY_TEMPORAL_SCOPES,
    allowed_relation_types as _ontology_allowed_relation_types,
    entity_kind as _ontology_entity_kind,
    validate_reflection_ontology as _validate_reflection_ontology,
)
from memory._reflection.schema import (
    REFLECTION_ARCHIVE_DAYS,
    make_archive_stamper,
    normalize_reflection,
    prepare_save_reflections,
    refine_reflection_id,
    reflection_id_from_facts as _reflection_id_from_facts,
)
from memory._reflection.refine import (
    build_merge_reflection,
    build_split_reflection,
)
from memory._reflection.selection import (
    filter_active_confirmed,
    filter_followup_candidates,
    followup_render_key,
    in_window,
    record_mentions,
    update_suppressions,
)
from memory._reflection.transitions import (
    apply_batch_mark,
    apply_mark_surfaced_handled,
    apply_promotion_status,
    compute_merged_evidence,
    find_reflection,
)

# Compatibility re-exports: these names existed at memory.reflection before
# the internal split. Keep object identity so existing imports and monkeypatch
# seams continue to resolve through the facade.
RELATION_TYPES = _ONTOLOGY_RELATION_TYPES
ENTITY_KINDS = _ONTOLOGY_ENTITY_KINDS
KIND_RELATION_MAP = _ONTOLOGY_KIND_RELATION_MAP
TEMPORAL_SCOPES = _ONTOLOGY_TEMPORAL_SCOPES
MAX_REFLECTION_TEXT_TOKENS = _ONTOLOGY_MAX_REFLECTION_TEXT_TOKENS
_entity_kind = _ontology_entity_kind
_allowed_relation_types = _ontology_allowed_relation_types
_REFLECTION_ARCHIVE_DAYS = REFLECTION_ARCHIVE_DAYS

if TYPE_CHECKING:
    from memory.event_log import EventLog
    from memory.facts import FactStore
    from memory.persona import PersonaManager

logger = get_module_logger(__name__, "Memory")

# Minimum unabsorbed facts to trigger reflection synthesis
MIN_FACTS_FOR_REFLECTION = 5

# memory-evidence-rfc §3.2.2: new/updated reflection status vocabulary.
# pending | confirmed | denied | promoted | merged | archived | promote_blocked
# `merged` = LLM merge_into 吸收到某 persona entry（reflection 保留带 absorbed_into 溯源）
# `promote_blocked` = LLM 连续失败触发的死信状态（需人工或 user signal 重置）
REFLECTION_TERMINAL_STATUSES = frozenset({
    'promoted', 'denied', 'archived', 'merged', 'promote_blocked',
})

# memory-evidence-rfc §3.9.1：time-based auto-promotion 删除。
# pending → confirmed / confirmed → promoted 改由 evidence_score 穿阈值
# 触发（§3.1.4）。本 PR (PR-1) 只实现 pending → confirmed 的 score 驱动；
# confirmed → promoted 的 merge-on-promote 路径在 PR-3。
# Cooldown between proactive chat candidacy
REFLECTION_COOLDOWN_MINUTES = 30
class ReflectionEngine:
    """Synthesizes facts into reflections and manages the pending → confirmed lifecycle."""

    def __init__(
        self, fact_store: FactStore, persona_manager: PersonaManager,
        event_log: EventLog | None = None,
    ):
        self._config_manager = get_config_manager()
        self._fact_store = fact_store
        self._persona_manager = persona_manager
        # memory-evidence-rfc §3.3.3：evidence 写路径必须走 record_and_save。
        # event_log 注入；None 时 aapply_signal 不可用（冷启动 / 纯单元测试
        # 路径仍可用 synthesize / auto_promote 等不触 evidence 的方法）。
        self._event_log = event_log
        # Per-character asyncio.Lock (P2.a.2). ReflectionEngine's async mutating
        # methods span multiple awaits (e.g. aauto_promote_stale calls
        # persona.aadd_fact across an await boundary) — so asyncio.Lock is the
        # right choice per CLAUDE rule "threading.Lock 持锁跨 await → 改用
        # asyncio.Lock". Lock is lazily created to avoid event-loop binding
        # at module-import time.
        self._alocks: dict[str, asyncio.Lock] = {}
        # threading.Lock guards the dict itself (reads/writes of _alocks are
        # pure Python, no await inside this critical section).
        self._alocks_guard = threading.Lock()
        # synth 失败退避的进程内镜像 {name: {key: {"n", "at"}}}。它是 session 内
        # 的权威工作副本，磁盘只是持久化 + 重启恢复。这样即使 synth_backoff.json
        # 写盘失败（只读 FS / 权限），失败计数也不会丢、dead-letter 闸门照常生效
        # （Codex P2）。对齐 review 的 _maint_state 进程内持久语义。
        self._synth_backoff_mem: dict[str, dict] = {}

    def _get_alock(self, name: str) -> asyncio.Lock:
        """Get (or lazily create) the per-character asyncio.Lock.

        Thread-safety scope: this method is called from the single
        FastAPI event-loop thread, never from asyncio.to_thread workers.
        The outer `name not in self._alocks` check is therefore single-
        threaded by construction. The inner check inside the guard is
        for multi-loop robustness (e.g. test harnesses that spin up a
        fresh loop per test). Matches the DCL pattern already used in
        facts.py / outbox.py / cursors.py.

        asyncio.Lock binding: on CPython 3.10+ Lock binds to the running
        loop at first `acquire`/`__aenter__`, not at `__init__`. Lazy
        construction here is defensive for 3.9 and cleaner for fresh-
        loop tests; not strictly required on the target 3.11 runtime.
        """
        if name not in self._alocks:
            with self._alocks_guard:
                if name not in self._alocks:
                    self._alocks[name] = asyncio.Lock()
        return self._alocks[name]

    # ── file paths ───────────────────────────────────────────────────

    def _reflections_path(self, name: str) -> str:
        from memory import ensure_character_dir
        return os.path.join(ensure_character_dir(self._config_manager.memory_dir, name), 'reflections.json')

    def _reflections_archive_dir(self, name: str) -> str:
        """Sharded archive directory (RFC §3.5.4).

        Replaces the legacy flat ``reflections_archive.json`` file. The
        directory is created lazily by `aappend_to_shard` on first
        archive event so an idle character never carries an empty dir.
        """
        from memory import ensure_character_dir
        return os.path.join(
            ensure_character_dir(self._config_manager.memory_dir, name),
            'reflection_archive',
        )

    def _reflections_legacy_archive_path(self, name: str) -> str:
        """Legacy flat archive path (pre-RFC §3.5.4). Kept for the
        one-shot migration in `aone_shot_archive_migration` and to keep
        the migration sentinel test-discoverable. NOT referenced by any
        write path."""
        from memory import ensure_character_dir
        return os.path.join(
            ensure_character_dir(self._config_manager.memory_dir, name),
            'reflections_archive.json',
        )

    def _surfaced_path(self, name: str) -> str:
        from memory import ensure_character_dir
        return os.path.join(ensure_character_dir(self._config_manager.memory_dir, name), 'surfaced.json')

    def _synth_backoff_path(self, name: str) -> str:
        """Per-character sidecar tracking per-rid synthesis failure counts.

        When the LLM fails, synthesize_reflections has no entity to hang
        attempts on (the reflection doesn't exist yet), so failure counts land
        in this ``{rid: attempts}`` map. rid is determined by source_fact_ids
        (deterministic) — once the facts change the rid changes and attempts
        naturally reset, so a persistently failing fact batch gets
        dead-lettered while new input retries immediately.
        """
        from memory import ensure_character_dir
        return os.path.join(
            ensure_character_dir(self._config_manager.memory_dir, name),
            'synth_backoff.json',
        )

    async def _aload_synth_backoff_from_disk(self, name: str) -> dict:
        """Read the {key: {"n": attempts(int), "at": last_fail_iso|None}} map from disk;
        missing / corrupt file → empty dict. Compatible with the old format
        {key: int} (at backfilled as None)."""
        path = self._synth_backoff_path(name)
        if not await asyncio.to_thread(os.path.exists, path):
            return {}
        try:
            data = await read_json_async(path)
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}
        out: dict[str, dict] = {}
        for k, v in data.items():
            if isinstance(v, dict):
                try:
                    out[str(k)] = {"n": int(v.get("n", 0)), "at": v.get("at")}
                except (TypeError, ValueError):
                    continue
            else:
                # 旧格式 {key: int}
                try:
                    out[str(k)] = {"n": int(v), "at": None}
                except (TypeError, ValueError):
                    continue
        return out

    async def _aload_synth_backoff(self, name: str) -> dict:
        """Return the failure-backoff map. Prefers the in-process mirror (the freshest
        working copy — counts survive even if the last disk write failed);
        loads from disk and caches on first access for a character."""
        cached = self._synth_backoff_mem.get(name)
        if cached is not None:
            return cached
        loaded = await self._aload_synth_backoff_from_disk(name)
        self._synth_backoff_mem[name] = loaded
        return loaded

    async def _asave_synth_backoff(self, name: str, backoff: dict) -> None:
        """Update the in-process mirror first (authoritative within the session, so a
        failed disk write never loses the throttle), then best-effort persist.
        A write failure WARNs without raising — the mirror is already in effect
        and the dead-letter gate keeps working; only a restart reads from disk,
        and that is a different recovery context anyway (Codex P2)."""
        self._synth_backoff_mem[name] = backoff
        try:
            await atomic_write_json_async(
                self._synth_backoff_path(name), backoff,
                indent=2, ensure_ascii=False,
            )
        except Exception as e:
            logger.warning(
                f"[Reflection] {name}: synth_backoff 写盘失败（进程内镜像仍生效，"
                f"throttle 不受影响）: {e}"
            )

    async def _abump_synth_backoff(self, name: str, key: str, reason: str) -> int:
        """Record one synthesis failure for key; returns the cumulative count and
        stamps the failure time (for time-based self-healing). The caller holds
        no lock (failure branches all run outside the post-LLM lock), so this
        takes _get_alock itself for the read-modify-write."""
        async with self._get_alock(name):
            # 拷贝一份再改：_aload_synth_backoff 返回的是进程内镜像引用，
            # 由 _asave_synth_backoff 统一回写，避免半改状态被并发读看到。
            backoff = dict(await self._aload_synth_backoff(name))
            attempts = backoff.get(key, {}).get("n", 0) + 1
            entry = {"n": attempts, "at": datetime.now().isoformat()}
            backoff[key] = entry
            # 防失败 key 长期累积：超阈值时只留 attempts 最高的若干条
            # （dead-letter 候选优先保活，被丢的低分 key 下次失败重新计起）。
            if len(backoff) > 64:
                backoff = dict(
                    sorted(backoff.items(), key=lambda kv: -kv[1].get("n", 0))[:64]
                )
                backoff[key] = entry  # 确保当前 key 不被裁掉
            await self._asave_synth_backoff(name, backoff)
        logger.debug(
            f"[Reflection] {name}: key {key} 合成失败退避 → {attempts} ({reason})"
        )
        return attempts

    async def _aclear_synth_backoff(self, name: str, key: str) -> None:
        """Clear a key's failure record after it persists successfully. Caller holds no lock."""
        async with self._get_alock(name):
            backoff = dict(await self._aload_synth_backoff(name))
            if key in backoff:
                del backoff[key]
                await self._asave_synth_backoff(name, backoff)

    # ── persistence ──────────────────────────────────────────────────

    @staticmethod
    def _normalize_reflection(entry: dict) -> dict:
        """Fill current schema defaults in-place (compatibility seam)."""
        return normalize_reflection(entry)

    @classmethod
    def _filter_reflections(cls, data, include_archived: bool, path: str) -> list[dict]:
        if not isinstance(data, list):
            logger.warning(f"[Reflection] reflections 文件不是列表，忽略: {path}")
            return []
        items = [
            cls._normalize_reflection(item)
            for item in data if isinstance(item, dict) and 'id' in item
        ]
        if not include_archived:
            # Hides every terminal status (promoted / denied / merged /
            # archived / promote_blocked) from active reads — reading from
            # the shared constant so PR-3's promote_blocked dead-letter
            # state is excluded without an additional edit here.
            items = [
                r for r in items
                if r.get('status') not in REFLECTION_TERMINAL_STATUSES
            ]
        return items

    def load_reflections(self, name: str, include_archived: bool = False) -> list[dict]:
        path = self._reflections_path(name)
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
                return self._filter_reflections(data, include_archived, path)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"[Reflection] 加载失败: {e}")
        return []

    async def aload_reflections(self, name: str, include_archived: bool = False) -> list[dict]:
        path = self._reflections_path(name)
        if not await asyncio.to_thread(os.path.exists, path):
            return []
        try:
            data = await read_json_async(path)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[Reflection] 加载失败: {e}")
            return []
        return self._filter_reflections(data, include_archived, path)

    def _prepare_save_reflections(
        self, name: str, reflections: list[dict], all_on_disk: list[dict],
    ) -> tuple[list[dict], list[dict], list[dict]]:
        """Compute archive split without I/O (compatibility seam)."""
        del name  # retained in the method signature for existing callers
        return prepare_save_reflections(reflections, all_on_disk)

    @staticmethod
    def _make_archive_stamper(now_iso: str):
        """Build the per-shard archive-stamping callback."""
        return make_archive_stamper(now_iso)

    def save_reflections(self, name: str, reflections: list[dict]) -> None:
        """Save reflections, archiving stale promoted/denied entries to shards.

        promoted/denied entries older than _REFLECTION_ARCHIVE_DAYS are moved
        automatically into the sharded archive directory (RFC §3.5.4). This path covers the EXISTING age-based archival —
        score-driven `sub_zero_days >= EVIDENCE_ARCHIVE_DAYS` archival uses
        the dedicated `aarchive_reflection` event-sourced path instead.
        """
        from memory.archive_shards import append_to_shard_sync
        assert_cloudsave_writable(
            self._config_manager,
            operation="save",
            target=f"memory/{name}/reflections.json",
        )
        path = self._reflections_path(name)
        all_on_disk = []
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    all_on_disk = [r for r in data if isinstance(r, dict)]
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"[Reflection] {name}: 读取现有 reflections 失败，中止保存以保护归档数据: {e}")
                return

        merged, to_archive, _ = self._prepare_save_reflections(name, reflections, all_on_disk)

        if to_archive:
            archive_dir = self._reflections_archive_dir(name)
            try:
                stamper = self._make_archive_stamper(datetime.now().isoformat())
                shard_path = append_to_shard_sync(
                    archive_dir, list(to_archive), stamper=stamper,
                )
                logger.info(
                    f"[Reflection] {name}: 归档 {len(to_archive)} 条旧 reflections → {os.path.basename(shard_path)}"
                )
            except OSError as e:
                # Fall-back: keep the entries in main file rather than lose
                # them. Same posture as the old flat-file path.
                logger.warning(
                    f"[Reflection] {name}: 写归档分片失败，回滚到 main 保留: {e}"
                )
                merged = merged + to_archive

        atomic_write_json(path, merged, indent=2, ensure_ascii=False)

    async def asave_reflections(self, name: str, reflections: list[dict]) -> None:
        from memory.archive_shards import aappend_to_shard
        assert_cloudsave_writable(
            self._config_manager,
            operation="save",
            target=f"memory/{name}/reflections.json",
        )
        path = self._reflections_path(name)
        all_on_disk = []
        if await asyncio.to_thread(os.path.exists, path):
            try:
                data = await read_json_async(path)
                if isinstance(data, list):
                    all_on_disk = [r for r in data if isinstance(r, dict)]
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"[Reflection] {name}: 读取现有 reflections 失败，中止保存以保护归档数据: {e}")
                return

        merged, to_archive, _ = self._prepare_save_reflections(name, reflections, all_on_disk)

        if to_archive:
            archive_dir = self._reflections_archive_dir(name)
            try:
                stamper = self._make_archive_stamper(datetime.now().isoformat())
                shard_path = await aappend_to_shard(
                    archive_dir, list(to_archive), stamper=stamper,
                )
                logger.info(
                    f"[Reflection] {name}: 归档 {len(to_archive)} 条旧 reflections → {os.path.basename(shard_path)}"
                )
            except OSError as e:
                logger.warning(
                    f"[Reflection] {name}: 写归档分片失败，回滚到 main 保留: {e}"
                )
                merged = merged + to_archive

        await atomic_write_json_async(path, merged, indent=2, ensure_ascii=False)

    def load_surfaced(self, name: str) -> list[dict]:
        """Load the list of reflections that were surfaced in proactive chat."""
        path = self._surfaced_path(name)
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return [item for item in data if isinstance(item, dict)]
                logger.warning(f"[Reflection] surfaced 文件不是列表，忽略: {path}")
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"[Reflection] 加载 surfaced 失败: {e}")
        return []

    async def aload_surfaced(self, name: str) -> list[dict]:
        path = self._surfaced_path(name)
        if not await asyncio.to_thread(os.path.exists, path):
            return []
        try:
            data = await read_json_async(path)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[Reflection] 加载 surfaced 失败: {e}")
            return []
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        logger.warning(f"[Reflection] surfaced 文件不是列表，忽略: {path}")
        return []

    def save_surfaced(self, name: str, surfaced: list[dict]) -> None:
        assert_cloudsave_writable(
            self._config_manager,
            operation="save",
            target=f"memory/{name}/surfaced.json",
        )
        atomic_write_json(self._surfaced_path(name), surfaced, indent=2, ensure_ascii=False)

    async def asave_surfaced(self, name: str, surfaced: list[dict]) -> None:
        assert_cloudsave_writable(
            self._config_manager,
            operation="save",
            target=f"memory/{name}/surfaced.json",
        )
        await atomic_write_json_async(self._surfaced_path(name), surfaced, indent=2, ensure_ascii=False)

    # ── synthesis ────────────────────────────────────────────────────

    async def synthesize_reflections(self, lanlan_name: str) -> list[dict]:
        """Synthesize pending reflections from accumulated unabsorbed facts.

        Called during proactive chat. Returns newly created reflections.

        Idempotency (P1 fix for fatal issue #3):
          1. The reflection id is determined by source_fact_ids
             (_reflection_id_from_facts).
          2. Before the LLM call, check: if the id for this batch of unabsorbed
             facts already exists in reflections.json → skip the LLM and only
             re-run mark_absorbed (idempotent).
          3. save_reflections also dedups by id, guarding against concurrent
             synth double-writes.
          4. Always amark_absorbed at the end, so a restart re-run after "save
             succeeded but mark failed" really flips the facts' absorbed to True.

        Concurrency (C3 refactor + thinking): the LLM call is outside the lock.
        The lock only guards the tens-of-milliseconds critical section of
        "reload → id dedup → append → save". Thus:
          - During the LLM (90-120s with thinking), other reflection writes for
            the same character aren't blocked (aapply_signal /
            aauto_promote_stale's pending→confirmed section / arecord_mentions /
            aget_followup_topics, etc.)
          - Double-write defense: rid is determined by source_fact_ids
            (deterministic); concurrent synths over the same fact batch compute
            the same rid, and the post-LLM in-lock dedup append catches it. The
            losing side returns [] without polluting the caller's view.
        """
        from config.prompts.prompts_memory import get_reflection_prompt
        from utils.language_utils import get_global_language
        from utils.llm_client import create_chat_llm_async

        unabsorbed = await self._fact_store.aget_unabsorbed_facts(lanlan_name)
        if len(unabsorbed) < MIN_FACTS_FOR_REFLECTION:
            return []

        # 失败退避 key 必须覆盖**全部** unabsorbed facts，所以在 cap 之前先取。
        # 不能用 rid（下面那个 capped top-N 子集的 hash）当退避 key：否则一个
        # poison 的 top-N 批次 dead-letter 后，新到的低 importance facts 进不了
        # top-N → rid 不变 → 合成被永久跳过，把整个 unabsorbed 池饿死（Codex P2）。
        # 用全集 key 时任何新 fact 都会改 key → 预算复位 → 重试一次。
        all_unabsorbed_ids = sorted(f['id'] for f in unabsorbed)
        backoff_key = _reflection_id_from_facts(all_unabsorbed_ids)

        # Cap unabsorbed facts entering this synthesis call. 上游
        # aget_unabsorbed_facts 没有 limit 参数，长期不上线时可能堆几百
        # 条；按 importance(desc) → 创建时间(asc) 排序后取前 N 条，避免
        # 一次性塞超长 prompt。
        from config import REFLECTION_SYNTHESIS_FACTS_MAX
        from memory.facts import safe_importance
        if len(unabsorbed) > REFLECTION_SYNTHESIS_FACTS_MAX:
            unabsorbed = sorted(
                unabsorbed,
                key=lambda f: (
                    -safe_importance(f),
                    str(f.get('created_at') or ''),
                ),
            )[:REFLECTION_SYNTHESIS_FACTS_MAX]

        # 排序一次：on-disk 字段与 _reflection_id_from_facts 内部 sorted 对齐，
        # 消除 "hash 用 sorted，存盘不 sorted" 的隐式非对称
        source_fact_ids = sorted(f['id'] for f in unabsorbed)
        rid = _reflection_id_from_facts(source_fact_ids)

        # 幂等 short-circuit：同一批 facts 的 reflection 已持久化 →
        # 不重复调 LLM，仅补跑 mark_absorbed（致命点 3 的重启补救路径）
        # 注：这里是 lock-外的 advisory check，提早避免无谓 LLM；最终
        # dedup 在 lock-内重做（防 LLM 期间并发写入）。
        existing_reflections = await self.aload_reflections(lanlan_name)
        existing = next((r for r in existing_reflections if r.get('id') == rid), None)
        if existing is not None:
            await self._fact_store.amark_absorbed(lanlan_name, source_fact_ids)
            logger.info(
                f"[Reflection] {lanlan_name}: 检测到同批 facts 已合成过 reflection "
                f"{rid}，跳过 LLM，补跑 mark_absorbed"
            )
            return []

        # 失败退避（dead-letter）：同一批 unabsorbed facts(backoff_key) 合成已连续
        # 失败 ≥ MEMORY_LIVENESS_MAX_ATTEMPTS 次 → 不再每 180s 原样重抽空跑 LLM。
        # 任一 unabsorbed fact 增减 → backoff_key 变、计数天然复位（用户审计 #2）。
        # 时间自愈：池子不变（挂机）时，每过 MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS
        # 放行一次 probe，让一次性模型宕机恢复后自愈，poison 批仍被压到每 5h 一次。
        from config import (
            MEMORY_LIVENESS_MAX_ATTEMPTS,
            MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS,
        )
        from memory.temporal import cooldown_elapsed
        synth_entry = (await self._aload_synth_backoff(lanlan_name)).get(backoff_key, {})
        synth_attempts = synth_entry.get("n", 0)
        if synth_attempts >= MEMORY_LIVENESS_MAX_ATTEMPTS and not cooldown_elapsed(
            synth_entry.get("at"), MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS,
        ):
            logger.debug(
                f"[Reflection] {lanlan_name}: backoff_key {backoff_key} 合成连续失败 "
                f"{synth_attempts} 次 ≥ {MEMORY_LIVENESS_MAX_ATTEMPTS}，dead-letter 跳过"
                f"（未到 {MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS}s 自愈窗口）"
            )
            return []

        _, _, _, _, name_mapping, _, _, _, _ = await self._config_manager.aget_character_data()
        master_name = name_mapping.get('human', '主人')

        facts_text = "\n".join(f"- {f['text']} (importance: {f.get('importance', 5)})" for f in unabsorbed)
        related_block = await self._build_related_context_block(lanlan_name, unabsorbed)
        reflection_prompt = get_reflection_prompt(get_global_language())
        prompt = reflection_prompt.replace('{RELATED_CONTEXT_BLOCK}', related_block)
        prompt = prompt.replace('{FACTS}', facts_text)
        prompt = prompt.replace('{LANLAN_NAME}', lanlan_name)
        prompt = prompt.replace('{MASTER_NAME}', master_name)

        try:
            set_call_type("memory_reflection")
            api_config = self._config_manager.get_model_api_config('summary')
            # timeout: 见 MEMORY_LLM_HARD_TIMEOUT_SECONDS 注释（上游转发
            # 120s hard cap，client 必须 ≤110s）。开 thinking 后输出多字段
            # JSON ontology 比简单分类长，吃满 110 也算合理。LLM 在锁外
            # 不阻塞同角色其他 reflection 写。
            # max_retries=0: 禁 SDK 自动重试（无业务 retry，单次即终态，外层
            # try/except 兜底返回 []）。
            # extra_body=None: 显式开 thinking——synth 是创意+结构化合成，
            # 思考能改善 ontology 字段的一致性和 reflection text 的质量。
            from config import MEMORY_LLM_HARD_TIMEOUT_SECONDS, LLM_OUTPUT_GUARD_MAX_TOKENS
            llm = await create_chat_llm_async(
                api_config['model'],
                api_config['base_url'], api_config['api_key'],
                timeout=MEMORY_LLM_HARD_TIMEOUT_SECONDS, max_retries=0,
                max_completion_tokens=LLM_OUTPUT_GUARD_MAX_TOKENS,  # runaway guard; generous so variable-length JSON (incl. thinking) isn't truncated
                extra_body=None,
                provider_type=api_config.get('provider_type'),
            )
            try:
                resp = await llm.ainvoke(prompt)  # noqa: LLM_INPUT_BUDGET  # prompt assembled from token-capped memory components (REFLECTION_*/RECALL_* budgets in the prompt builder).
            finally:
                await llm.aclose()
            raw = resp.content.strip()
            if raw.startswith("```"):
                raw = raw.replace("```json", "").replace("```", "").strip()
            result = robust_json_loads(raw)
            if not isinstance(result, dict):
                logger.warning(f"[Reflection] LLM 返回非 dict: {type(result)}")
                await self._abump_synth_backoff(lanlan_name, backoff_key, "non-dict response")
                return []
            reflection_text = result.get('reflection', '')
            if not isinstance(reflection_text, str):
                logger.warning(f"[Reflection] reflection 字段非 str: {type(reflection_text)}")
                await self._abump_synth_backoff(lanlan_name, backoff_key, "reflection field non-str")
                return []
            reflection_text = reflection_text.strip()
            reflection_entity = result.get('entity', 'relationship')
            if reflection_entity not in ('master', 'neko', 'relationship'):
                reflection_entity = 'relationship'

            # Ontology fields (RFC §3). Missing fields are tolerated — we
            # only enforce consistency when the LLM does fill them in, so
            # older prompts stay compatible. Validation failure degrades
            # to null (soft fail) rather than dropping the whole reflection.
            rel_type = result.get('relation_type')
            if rel_type is not None and not isinstance(rel_type, str):
                rel_type = None
            temporal = result.get('temporal_scope')
            if temporal is not None and not isinstance(temporal, str):
                temporal = None
            subject = result.get('subject')
            if subject is not None and not isinstance(subject, str):
                subject = None
            # Schema v2: event_when_raw 由 LLM 输出（相对时间，offset+unit），
            # 系统按 created_at 解算。LLM 可能省略整段或单边 (start/end)，
            # normalize_event_when 把破损值兜底成 None。
            from memory.temporal import normalize_event_when as _norm_when
            event_when_raw = _norm_when(result.get('event_when'))

            ok, reason = _validate_reflection_ontology(
                reflection_entity, rel_type, temporal, reflection_text,
            )
            if not ok:
                logger.info(
                    f"[Reflection] ontology 验证不通过({reason})，降级为 null: "
                    f"entity={reflection_entity} rel_type={rel_type}"
                )
                # Strip the entire ontology tuple — keeping `subject` while
                # dropping the rest would leave a half-structured record
                # (no class but still a subject label), which downstream
                # grouping/filtering can't interpret consistently.
                rel_type = None
                temporal = None
                subject = None
        except Exception as e:
            logger.warning(f"[Reflection] 合成失败: {e}")
            await self._abump_synth_backoff(lanlan_name, backoff_key, f"LLM/parse exception: {e}")
            return []

        if not reflection_text:
            await self._abump_synth_backoff(lanlan_name, backoff_key, "empty reflection text")
            return []

        # Create pending reflection — id 已在函数开头由 source_fact_ids 决定
        now = datetime.now()
        now_iso = now.isoformat()

        # Importance-based initial rein seed：让"关键节点"型 reflection 起步
        # 就带一点正分，不必等多轮 user confirms 才穿越 CONFIRMED 阈值。
        # 不走 aapply_signal（synthesis 本身不经 event log），直接写进初始
        # 字典——synth 不是 event-sourced，这些初始值就是 ground truth。
        from memory.facts import safe_importance
        max_importance = max(
            (safe_importance(f) for f in unabsorbed),
            default=5,
        )
        initial_rein = initial_reinforcement_from_importance(max_importance)

        # Schema v2：event_start/end 是 fact 和 reflection 共用的 ISO 锚点。
        # fallback 策略按 temporal_scope 分流：
        #   - state / episode 需要 end（TTL 判定锚点），end 缺失时和 start 同值
        #   - pattern 允许 end=None（持续模式没有"何时结束"）
        #   - 兜底（temporal=None/legacy）按 pattern 处理（保守不淡出）
        from memory.temporal import (
            compute_event_timestamps as _compute_ts,
        )
        from config import MEMORY_SCHEMA_VERSION_CURRENT as _SCHEMA_V
        _needs_end = temporal in ('state', 'episode')
        event_start_at, event_end_at = _compute_ts(
            event_when_raw,
            now_iso,
            fallback_start=True,
            fallback_end=_needs_end,
        )

        reflection = self._normalize_reflection({
            'id': rid,
            'text': reflection_text,
            'entity': reflection_entity,
            'status': 'pending',  # pending | confirmed | denied | promoted | archived
            'source_fact_ids': source_fact_ids,
            'created_at': now_iso,
            'feedback': None,
            'next_eligible_at': (now + timedelta(minutes=REFLECTION_COOLDOWN_MINUTES)).isoformat(),
            'reinforcement': initial_rein,
            'rein_last_signal_at': now_iso if initial_rein > 0 else None,
            # Ontology (RFC §3). May be None if the model omitted them or
            # validation demoted them. Callers that want to filter or group
            # by relation_type should treat None as "uncategorized".
            'relation_type': rel_type,
            'temporal_scope': temporal,
            'subject': subject,
            # Schema v2 event timing (memory/temporal.py)
            'event_when_raw': event_when_raw,
            'event_start_at': event_start_at,
            'event_end_at': event_end_at,
            'schema_version': _SCHEMA_V,
        })

        # ── LOCK 仅护住 re-load + dedup append + save ──
        async with self._get_alock(lanlan_name):
            # 再次 load：LLM 调用期间可能有并发 synth；用最新 list 做 id dedup 追加
            reflections = await self.aload_reflections(lanlan_name)
            created = False
            if any(r.get('id') == rid for r in reflections):
                logger.info(
                    f"[Reflection] {lanlan_name}: reflection {rid} 已被并发 synth 写入，跳过重复 append"
                )
            else:
                reflections.append(reflection)
                await self.asave_reflections(lanlan_name, reflections)
                created = True

        # reflection 已落盘（本次 append 或并发对方先写），清掉本次 unabsorbed
        # 全集的失败退避记录——锁外调用（_aclear_synth_backoff 自取锁，避免
        # reentrant 死锁）。
        await self._aclear_synth_backoff(lanlan_name, backoff_key)

        # 无条件 mark_absorbed：幂等，且覆盖 save 成功后但在此崩溃的补跑情况
        # （fact_store 自己有锁，不需要在 reflection 锁内）
        await self._fact_store.amark_absorbed(lanlan_name, source_fact_ids)

        if not created:
            # 并发分支已落盘对方的对象；返回内存副本会让调用方拿到一个
            # 未持久化、可能与磁盘版文本不同的"幽灵反思"，违反"返回值
            # = 本调用真正新建的反思"语义。
            return []
        # reflection 原文不写 logger（隐私）；本地 print 兜底
        logger.info(f"[Reflection] {lanlan_name}: 合成了新反思 {rid} (len={len(reflection_text)} chars)")
        print(f"[Reflection] {lanlan_name}: 新反思 {rid}: {reflection_text[:50]}...")
        return [reflection]

    async def _build_related_context_block(
        self, lanlan_name: str, unabsorbed: list[dict]
    ) -> str:
        """When embeddings are available, recall absorbed facts as RELATED_CONTEXT;
        unavailable / empty recall → return an empty string (the
        {RELATED_CONTEXT_BLOCK} render disappears; the prompt is equivalent to
        the pre-change one). Distant facts are reference-only: they don't enter
        source_fact_ids and aren't mark_absorbed — idempotency is guaranteed by
        the rid hash of the unabsorbed set."""
        try:
            from memory.embeddings import (
                get_embedding_service,
                is_cached_embedding_valid,
            )
            # 同时检查 is_disabled (sticky 关闭) 和 is_available (READY) ——
            # INIT/LOADING 状态下 is_disabled=False 但 is_available=False，
            # 若只看前者，reranker 会降级到 evidence-only 排序，把无关历史
            # fact 当 "相关背景" 塞进 prompt（Codex P2 #1392）。
            service = get_embedding_service()
            if service.is_disabled() or not service.is_available():
                return ""
            model_id = service.model_id()
            if not model_id:
                return ""
        except Exception:
            return ""

        if not unabsorbed:
            return ""

        unabsorbed_ids = {f['id'] for f in unabsorbed if 'id' in f}
        try:
            # Phase C-2: 用 full 池含归档 fact，让远期 absorbed 也能被召回
            all_facts = await self._fact_store.aload_facts_full(lanlan_name)
        except Exception as e:
            logger.warning(f"[Reflection] related context load_facts 失败: {e}")
            return ""

        # Codex P2 #1392：必须 pre-filter 出有 valid embedding 的 fact 才能
        # 进 reranker。fact 没 evidence `score` 字段，若放进 rerank=False 的
        # coarse_rank 而 embedding 又是 stale/missing（model-id 切换后 / backfill
        # 前），fallback 路径会按 evidence_score=0 排序 → 顺序近似随机 →
        # 把任意 fact 当 "相关背景" 注入 prompt。这里先 filter，pool 为空就
        # early return，宁可没 RELATED_CONTEXT 也不要塞无关历史。
        absorbed_pool = [
            f for f in all_facts
            if f.get('absorbed')
            and f.get('id') not in unabsorbed_ids
            and is_cached_embedding_valid(f, f.get('text', ''), model_id)
        ]
        if not absorbed_pool:
            return ""

        query_texts = [f.get('text', '') for f in unabsorbed if f.get('text')]
        if not query_texts:
            return ""

        try:
            from memory.recall import MemoryRecallReranker
            from config import (
                REFLECTION_RELATED_PER_QUERY_K,
                REFLECTION_RELATED_TOTAL_CAP,
            )
            reranker = MemoryRecallReranker()
            # Per-query top-K（每条 unabsorbed fact 单独享 ``PER_QUERY_K`` 配额，
            # union dedup 后截到 ``TOTAL_CAP``），不走 max-pool 全局预算。详见
            # ``aretrieve_per_query_topk`` docstring 和 PR #1401 thread：max-pool
            # 在 unabsorbed 主题分散时会让冷门主题挤不进 anchor，per-query 配
            # 额保证每条 unabsorbed 至少能拿自己的近邻。
            related = await reranker.aretrieve_per_query_topk(
                absorbed_pool, query_texts,
                per_query_k=REFLECTION_RELATED_PER_QUERY_K,
                total_cap=REFLECTION_RELATED_TOTAL_CAP,
            )
        except Exception as e:
            logger.warning(f"[Reflection] related context 召回失败: {e}")
            return ""

        if not related:
            return ""

        related_text = "\n".join(
            f"- {f.get('text', '')} (importance: {f.get('importance', 5)})"
            for f in related
        )
        return (
            "======以下为相关历史背景======\n"
            f"{related_text}\n"
            "（仅供参考，本轮不要为它们单独产出 reflection）\n"
            "======以上为相关历史背景======\n\n"
        )

    # ── refine apply (Phase A-3 MemoryRefineEngine) ─────────────────

    @staticmethod
    def _refine_reflection_id(text: str) -> str:
        """Salted hash so split-into-N pieces get distinct ids even when
        the underlying fact set is shared."""
        return refine_reflection_id(text, now=datetime.now())

    def _build_split_reflection(
        self,
        src: dict,
        produce_item: dict,
        entity: str,
        now: datetime,
        *,
        split_count: int,
    ) -> dict:
        """Build a reflection produced by split; inherits src's source_fact_ids,
        ontology and event_when. reinforcement is divided evenly by the actual
        split_count (Codex P2 #1392: the old hardcoded /2 underestimated each
        item's strength when N>2). split_count is at least 2 (the caller
        already skips when len(produce)<2)."""
        return build_split_reflection(
            src,
            produce_item,
            entity,
            now,
            split_count=split_count,
            id_factory=self._refine_reflection_id,
            normalizer=self._normalize_reflection,
        )

    def _build_merge_reflection(
        self,
        srcs: list[dict],
        fact_source_ids: list[str],
        produce: dict,
        entity: str,
        now: datetime,
    ) -> dict:
        """Build a reflection produced by merge; merges source_fact_ids (including
        absorbed_from_fact_ids), status takes the highest (promoted > confirmed >
        pending), reinforcement takes the max."""
        return build_merge_reflection(
            srcs,
            fact_source_ids,
            produce,
            entity,
            now,
            id_factory=self._refine_reflection_id,
            normalizer=self._normalize_reflection,
        )

    async def apply_refine_actions(
        self,
        name: str,
        entity: str,
        cluster: list[dict],
        actions: list[dict],
        cluster_hash: str,
    ) -> int:
        """Apply the four MemoryRefineEngine action types to reflections.

        Fact entries within a cluster are read-only information sources — any
        split / discard / modify aimed at a fact id is rejected outright; facts
        may only serve as absorbed_from_fact_ids of merge / modify. Enforced in
        code, not left to prompt goodwill.

        Inside the lock: reload → validate → apply → stamp → save.
        Newly produced reflections aren't stamped — the next cron re-examines them.

        Returns: number of successfully applied actions."""
        from memory.refine import REFINE_TYPE_KEY, VALID_REFINE_ACTIONS

        cluster_refl_ids = {
            e.get('id') for e in cluster
            if isinstance(e, dict)
            and e.get(REFINE_TYPE_KEY) == 'reflection'
            and e.get('id')
        }
        cluster_fact_ids = {
            e.get('id') for e in cluster
            if isinstance(e, dict)
            and e.get(REFINE_TYPE_KEY) == 'fact'
            and e.get('id')
        }

        async with self._get_alock(name):
            reflections = await self.aload_reflections(name)
            refl_by_id = {
                r.get('id'): r for r in reflections
                if isinstance(r, dict) and r.get('id')
            }

            consumed: set[str] = set()
            produced: list[dict] = []
            applied = 0
            now = datetime.now()
            now_iso = now.isoformat()

            for act_obj in actions:
                if not isinstance(act_obj, dict):
                    continue
                act = act_obj.get('action')
                if act not in VALID_REFINE_ACTIONS:
                    logger.warning(f"[Refine apply] reflection: 非法 action {act!r}")
                    continue

                # fact 不可作 split / discard / modify 的 source_id —— 代码层硬拦
                if act in ('split', 'discard', 'modify'):
                    sid = act_obj.get('source_id')
                    if sid in cluster_fact_ids:
                        logger.warning(
                            f"[Refine apply] reflection: 拒绝对 fact id={sid} 做 {act}"
                        )
                        continue

                if act == 'split':
                    src_id = act_obj.get('source_id')
                    if not src_id or src_id not in cluster_refl_ids or src_id in consumed:
                        continue
                    src = refl_by_id.get(src_id)
                    if not src:
                        continue
                    produce = act_obj.get('produce')
                    if not isinstance(produce, list) or len(produce) < 2:
                        continue
                    # 先过滤出有效 produce items，再以实际数量作 split_count
                    valid_produce = [
                        p for p in produce
                        if isinstance(p, dict) and str(p.get('text', '')).strip()
                    ]
                    if len(valid_produce) < 2:
                        continue
                    new_entries = [
                        self._build_split_reflection(
                            src, p, entity, now, split_count=len(valid_produce),
                        )
                        for p in valid_produce
                    ]
                    produced.extend(new_entries)
                    consumed.add(src_id)
                    applied += 1

                elif act == 'merge':
                    src_ids_raw = act_obj.get('source_ids') or []
                    if not isinstance(src_ids_raw, list):
                        continue
                    # source_ids 必须全是 reflection；含 fact id → 拒绝整个 action
                    if any(sid in cluster_fact_ids for sid in src_ids_raw):
                        logger.warning(
                            "[Refine apply] reflection: merge source_ids 含 fact，拒绝"
                        )
                        continue
                    valid_refl_ids = [
                        sid for sid in src_ids_raw
                        if sid in cluster_refl_ids
                        and sid in refl_by_id
                        and sid not in consumed
                    ]
                    if len(valid_refl_ids) < 2:
                        continue
                    absorbed_fact_ids = act_obj.get('absorbed_from_fact_ids') or []
                    if not isinstance(absorbed_fact_ids, list):
                        absorbed_fact_ids = []
                    valid_fact_sources = [
                        fid for fid in absorbed_fact_ids if fid in cluster_fact_ids
                    ]
                    produce = act_obj.get('produce')
                    if not isinstance(produce, dict):
                        continue
                    text = str(produce.get('text', '')).strip()
                    if not text:
                        continue
                    new_refl = self._build_merge_reflection(
                        [refl_by_id[sid] for sid in valid_refl_ids],
                        valid_fact_sources, produce, entity, now,
                    )
                    produced.append(new_refl)
                    consumed.update(valid_refl_ids)
                    applied += 1

                elif act == 'modify':
                    src_id = act_obj.get('source_id')
                    if not src_id or src_id not in cluster_refl_ids or src_id in consumed:
                        continue
                    src = refl_by_id.get(src_id)
                    if not src:
                        continue
                    produce = act_obj.get('produce')
                    if not isinstance(produce, dict):
                        continue
                    new_text = str(produce.get('text', '')).strip()
                    if not new_text:
                        continue
                    absorbed_fact_ids = act_obj.get('absorbed_from_fact_ids') or []
                    if not isinstance(absorbed_fact_ids, list):
                        absorbed_fact_ids = []
                    valid_fact_sources = [
                        fid for fid in absorbed_fact_ids if fid in cluster_fact_ids
                    ]
                    reason = str(act_obj.get('reason') or 'refine_modify')
                    old_text = src.get('text', '')
                    src['text'] = new_text
                    existing_sources = list(src.get('source_fact_ids') or [])
                    src['source_fact_ids'] = sorted(
                        set(existing_sources + valid_fact_sources)
                    )
                    mod_history = list(src.get('modification_history') or [])
                    mod_history.append({
                        'old_text': old_text,
                        'modified_at': now_iso,
                        'reason': reason,
                        'absorbed_fact_ids': valid_fact_sources,
                    })
                    src['modification_history'] = mod_history
                    # 文本变 → embedding 失效（下次 worker 扫描重 embed）
                    src['embedding'] = None
                    src['embedding_text_sha256'] = None
                    src['embedding_model_id'] = None
                    applied += 1

                elif act == 'discard':
                    src_id = act_obj.get('source_id')
                    if not src_id or src_id not in cluster_refl_ids or src_id in consumed:
                        continue
                    consumed.add(src_id)
                    applied += 1

            # Stamp 决策（Codex P1 + P2 #1392 合并语义）：
            # - applied > 0：有变化，必 stamp + save
            # - applied == 0 + actions 为空：LLM 明确 no-op，stamp 防
            #   cluster_hash skip 失效（Codex P1）
            # - applied == 0 + actions 非空：所有 action 全 reject = LLM
            #   语义垃圾，不 stamp 等下轮重试（Codex P2）
            if applied == 0 and actions:
                return 0

            new_reflections = [
                r for r in reflections
                if not (isinstance(r, dict) and r.get('id') in consumed)
            ]
            new_reflections.extend(produced)

            stamped = 0
            for r in new_reflections:
                if not isinstance(r, dict):
                    continue
                rid = r.get('id')
                if rid in cluster_refl_ids and rid not in consumed:
                    r['last_refine_cluster_hash'] = cluster_hash
                    r['last_refine_at'] = now_iso
                    # 成功 stamp → 清 Site 4 liveness 计数器（对偶
                    # PersonaManager.apply_refine_actions）
                    if r.get('refine_attempts'):
                        r['refine_attempts'] = 0
                    stamped += 1

            if applied == 0 and stamped == 0:
                return 0

            await self.asave_reflections(name, new_reflections)
            logger.info(
                f"[Reflection] {name} entity={entity}: refine 应用 {applied} action "
                f"(cluster_hash={cluster_hash}, stamped={stamped}, "
                f"+{len(produced)} produced, -{len(consumed)} consumed)"
            )
        return applied

    async def _abump_refine_attempts(
        self, name: str, cluster: list[dict], cluster_hash: str,
    ) -> None:
        """Site 4 liveness fallback: bump ``refine_attempts`` on non-fact reflection
        members when a refine-cluster LLM call fails. Twin of
        ``PersonaManager._abump_refine_attempts`` — same cure, different storage
        (reflections.json vs persona.json).

        Recovery: a successful refine (apply_refine_actions reaching the stamp
        branch) resets ``refine_attempts`` to 0; or manually edit reflections.json.
        """
        from config import MEMORY_LIVENESS_MAX_ATTEMPTS
        from memory.facts import safe_int_field
        from memory.refine import REFINE_TYPE_KEY

        # fact 不计 attempts（fact 是 readonly 信息源，refine 永远不改它）；
        # 只 bump reflection 成员。reflection 不分 entity section 单独存（全
        # 在一个 list 里按 entity 字段区分），所以收集 set[rid] 即可。
        member_rids: set[str] = set()
        for e in cluster:
            if not isinstance(e, dict):
                continue
            if e.get(REFINE_TYPE_KEY) == 'fact':
                continue
            rid = e.get('id')
            if rid:
                member_rids.add(rid)
        if not member_rids:
            return

        async with self._get_alock(name):
            reflections = await self._aload_reflections_full(name)
            modified = False
            for r in reflections:
                if not isinstance(r, dict) or r.get('id') not in member_rids:
                    continue
                new_attempts = safe_int_field(r, 'refine_attempts') + 1
                r['refine_attempts'] = new_attempts
                # 戳失败时刻供 dead-letter 时间自愈（cooldown_elapsed），对偶
                # persona 侧——一次性宕机顶到 MAX 后过 5h 冷却重新 probe。
                r['last_refine_attempt_at'] = datetime.now().isoformat()
                modified = True
                if new_attempts == MEMORY_LIVENESS_MAX_ATTEMPTS:
                    logger.warning(
                        f"[ReflectionRefine] {name}: reflection id={r.get('id')} "
                        f"refine_attempts={new_attempts} ≥ "
                        f"{MEMORY_LIVENESS_MAX_ATTEMPTS}（dead-letter，"
                        f"cluster_hash={cluster_hash}）。下次 refine gather "
                        f"不再选入，避免毒 cluster 占用名额。"
                    )
            if modified:
                # CodeRabbit: 传 active-only 给 ``asave_reflections``。
                # ``_prepare_save_reflections`` 把 input 当 "想要存活的 active 集",
                # all_on_disk 里 id 在 input set 中的会跳过归档判断。如果传
                # full list（含 terminal），``to_archive`` 永远是空，老 promoted/
                # denied 永远 archive 不掉，跟 arecord_mentions / aupdate_suppressions
                # 走同一约定保证归档流程正常推进。
                active = [
                    r for r in reflections
                    if r.get('status') not in REFLECTION_TERMINAL_STATUSES
                ]
                await self.asave_reflections(name, active)

    # alias for backward compat (system_router calls .reflect())
    async def reflect(self, lanlan_name: str) -> dict | None:
        """Alias for synthesize_reflections. Returns first reflection or None."""
        results = await self.synthesize_reflections(lanlan_name)
        return results[0] if results else None

    # ── evidence signals (RFC §3.4, §3.8.4) ─────────────────────────

    @staticmethod
    def _find_reflection_in_list(reflections: list[dict], rid: str) -> dict | None:
        return find_reflection(reflections, rid)

    # Delegated to memory.evidence.compute_evidence_snapshot — shared with
    # PersonaManager so rein/disp/combo semantics stay in one place.
    @staticmethod
    def _compute_evidence_after_delta(
        entry: dict, delta: dict, now_iso: str, source: str = 'unknown',
    ) -> dict:
        from memory.evidence import compute_evidence_snapshot
        return compute_evidence_snapshot(entry, delta, now_iso, source)

    async def aapply_signal(
        self, lanlan_name: str, reflection_id: str, delta: dict, source: str,
    ) -> bool:
        """Mutate one reflection's evidence via EVT_REFLECTION_EVIDENCE_UPDATED.

        record_and_save contract (RFC §3.3.3):
          load → append event → mutate view → save view → advance sentinel.

        Returns True if applied; False if reflection not found (LLM may point
        at a stale id; signals are best-effort).
        """
        from memory.event_log import EVT_REFLECTION_EVIDENCE_UPDATED
        if self._event_log is None:
            raise RuntimeError(
                "[Reflection.aapply_signal] event_log 未注入；"
                "ReflectionEngine() 构造时须传入 event_log"
            )

        async with self._get_alock(lanlan_name):
            reflections_full = await self._aload_reflections_full(lanlan_name)
            entry = self._find_reflection_in_list(reflections_full, reflection_id)
            if entry is None:
                logger.warning(
                    f"[Reflection] {lanlan_name}: aapply_signal 找不到 reflection_id={reflection_id}"
                )
                return False

            now_iso = datetime.now().isoformat()
            snapshot = self._compute_evidence_after_delta(
                entry, delta, now_iso, source,
            )
            payload = {
                'reflection_id': reflection_id,
                'reinforcement': snapshot['reinforcement'],
                'disputation': snapshot['disputation'],
                'rein_last_signal_at': snapshot['rein_last_signal_at'],
                'disp_last_signal_at': snapshot['disp_last_signal_at'],
                'sub_zero_days': snapshot['sub_zero_days'],
                'user_fact_reinforce_count': snapshot['user_fact_reinforce_count'],
                'source': source,
            }

            def _sync_load(_n: str):
                return reflections_full

            def _sync_mutate(_view):
                entry['reinforcement'] = snapshot['reinforcement']
                entry['disputation'] = snapshot['disputation']
                entry['rein_last_signal_at'] = snapshot['rein_last_signal_at']
                entry['disp_last_signal_at'] = snapshot['disp_last_signal_at']
                entry['sub_zero_days'] = snapshot['sub_zero_days']
                entry['user_fact_reinforce_count'] = snapshot['user_fact_reinforce_count']

            def _sync_save(n: str, view):
                # Gate write behind the same cloudsave check as
                # save_reflections/asave_reflections — the evidence mutation
                # path must honour read-only/maintenance mode (CodeRabbit PR #929).
                assert_cloudsave_writable(
                    self._config_manager,
                    operation="save",
                    target=f"memory/{n}/reflections.json",
                )
                atomic_write_json(
                    self._reflections_path(n), view, indent=2, ensure_ascii=False,
                )

            await self._event_log.arecord_and_save(
                lanlan_name, EVT_REFLECTION_EVIDENCE_UPDATED, payload,
                sync_load_view=_sync_load,
                sync_mutate_view=_sync_mutate,
                sync_save_view=_sync_save,
            )
            return True

    # ── score-driven archive (RFC §3.5) ─────────────────────────────

    async def aincrement_sub_zero(
        self, lanlan_name: str, reflection_id: str, now: datetime,
    ) -> int | None:
        """Increment one reflection's `sub_zero_days` via EVT_REFLECTION_EVIDENCE_UPDATED.

        Called by the periodic archive sweep (`memory_server._periodic_archive_sweep_loop`).
        Goes through `arecord_and_save` so the increment is audit-logged
        and replayable — derived state but worth recording.

        Returns the new `sub_zero_days` value if incremented, or None if
        no increment happened (entry not found / protected / already
        incremented today / score >= 0).
        """
        from memory.event_log import EVT_REFLECTION_EVIDENCE_UPDATED
        from memory.evidence import maybe_mark_sub_zero
        if self._event_log is None:
            raise RuntimeError(
                "[Reflection.aincrement_sub_zero] event_log 未注入"
            )

        async with self._get_alock(lanlan_name):
            reflections_full = await self._aload_reflections_full(lanlan_name)
            entry = self._find_reflection_in_list(reflections_full, reflection_id)
            if entry is None:
                return None
            # Coderabbit PR #934 round-2 Major #2: probe on a staged copy
            # so the in-memory list is NOT mutated until inside the
            # locked record_and_save critical section. If the event
            # append or save raises, the live entry stays clean (no
            # orphan sub_zero_days increment lacking an audit-log row).
            staged_entry = dict(entry)
            if not maybe_mark_sub_zero(staged_entry, now):
                return None

            new_count = int(staged_entry.get('sub_zero_days', 0) or 0)
            new_date = staged_entry.get('sub_zero_last_increment_date')

            payload = {
                'reflection_id': reflection_id,
                'reinforcement': float(entry.get('reinforcement', 0.0) or 0.0),
                'disputation': float(entry.get('disputation', 0.0) or 0.0),
                'rein_last_signal_at': entry.get('rein_last_signal_at'),
                'disp_last_signal_at': entry.get('disp_last_signal_at'),
                'sub_zero_days': new_count,
                'sub_zero_last_increment_date': new_date,
                'user_fact_reinforce_count': int(
                    entry.get('user_fact_reinforce_count', 0) or 0,
                ),
                'source': 'archive_sweep',
            }

            def _sync_load(_n: str):
                return reflections_full

            def _sync_mutate(_view):
                # Apply staged values to the cached entry only after
                # event append succeeded (record_and_save orders
                # append → mutate → save, so a failure earlier never
                # reaches this callback).
                entry['sub_zero_days'] = new_count
                entry['sub_zero_last_increment_date'] = new_date

            def _sync_save(n: str, view):
                assert_cloudsave_writable(
                    self._config_manager,
                    operation="save",
                    target=f"memory/{n}/reflections.json",
                )
                atomic_write_json(
                    self._reflections_path(n), view, indent=2, ensure_ascii=False,
                )

            await self._event_log.arecord_and_save(
                lanlan_name, EVT_REFLECTION_EVIDENCE_UPDATED, payload,
                sync_load_view=_sync_load,
                sync_mutate_view=_sync_mutate,
                sync_save_view=_sync_save,
            )
            return new_count

    async def aarchive_reflection(self, lanlan_name: str, reflection_id: str) -> bool:
        """Move one reflection from main view to a sharded archive file.

        RFC §3.5.6: archiving reuses the ``EVT_REFLECTION_STATE_CHANGED`` event
        (no new event types). Payload carries `from`/`to`/`archive_shard_path`
        so the reconciler can replay the full transition.

        Flow (coderabbit PR #934 round-1 + round-2):
          1. Pre-pick the shard path (deterministic basename for the
             event payload).
          2. record_and_save — atomically removes the entry from the
             active view + appends the state_changed event whose
             payload carries `entry_snapshot` so a crash later is
             recoverable.
          3. aappend_to_shard — writes the snapshot into the shard
             file. If this raises, the next reconciler boot's archive
             handler self-heals by recreating the shard from
             `entry_snapshot` (idempotent, see
             `make_reflection_archive_handler`).

        Returns True if archived; False if `reflection_id` not found or
        the entry is `protected`.
        """
        from memory.archive_shards import aappend_to_shard
        from memory.event_log import EVT_REFLECTION_STATE_CHANGED
        if self._event_log is None:
            raise RuntimeError(
                "[Reflection.aarchive_reflection] event_log 未注入；"
                "ReflectionEngine() 构造时须传入 event_log"
            )

        async with self._get_alock(lanlan_name):
            reflections_full = await self._aload_reflections_full(lanlan_name)
            entry = self._find_reflection_in_list(reflections_full, reflection_id)
            if entry is None:
                logger.warning(
                    f"[Reflection] {lanlan_name}: aarchive_reflection 找不到 "
                    f"reflection_id={reflection_id}"
                )
                return False
            if entry.get('protected'):
                logger.debug(
                    f"[Reflection] {lanlan_name}: aarchive_reflection 跳过 protected "
                    f"reflection_id={reflection_id}"
                )
                return False

            prev_status = entry.get('status', 'pending')
            now = datetime.now()
            now_iso = now.isoformat()

            # Predict shard path BEFORE writing so we can stamp the
            # entry's own `archive_shard_path` field to match its on-disk
            # location. We deep-copy the entry so the in-memory original
            # remains untouched until the event log gates the mutation;
            # otherwise a crash between shard-write and event-append
            # would leave `archived_at` on a still-active entry.
            from memory.archive_shards import apick_today_shard_path
            archive_dir = self._reflections_archive_dir(lanlan_name)
            shard_path = await apick_today_shard_path(archive_dir, now=now)
            shard_basename = os.path.basename(shard_path)
            archive_entry = dict(entry)
            archive_entry['archived_at'] = now_iso
            archive_entry['status'] = 'archived'
            archive_entry['archive_shard_path'] = shard_basename

            payload = {
                'reflection_id': reflection_id,
                'from': prev_status,
                'to': 'archived',
                'archive_shard_path': shard_basename,
                'archived_at': now_iso,
                # Symmetry with aarchive_persona_entry — the reflection
                # archive handler in evidence_handlers.py reads this on
                # every replay and idempotently recreates the shard if
                # it's missing (coderabbit PR #934 round-2 Major #3).
                # Crash between record_and_save and the shard append
                # below is healed on the next reconciler boot.
                'entry_snapshot': archive_entry,
            }

            def _sync_load(_n: str):
                return reflections_full

            def _sync_mutate(view):
                # Drop the archived entry from active list (in-place
                # mutate, since `view` IS `reflections_full`).
                view[:] = [r for r in view if r.get('id') != reflection_id]

            def _sync_save(n: str, view):
                assert_cloudsave_writable(
                    self._config_manager,
                    operation="save",
                    target=f"memory/{n}/reflections.json",
                )
                atomic_write_json(
                    self._reflections_path(n), view, indent=2, ensure_ascii=False,
                )

            # ORDER (coderabbit review #934 round-1 + round-2):
            # 1. record_and_save first (commits event + active-view
            #    removal atomically). Avoids the "shard duplicate +
            #    still-active entry" race where a crash between shard
            #    write and view save would let the next sweep
            #    re-archive into a second shard slot.
            # 2. aappend_to_shard second. If THIS step crashes (or
            #    raises), the active view has already lost the entry
            #    but the shard never got it. Self-heal: the
            #    state_changed handler in evidence_handlers.py reads
            #    `entry_snapshot` from the payload and re-creates the
            #    shard on the next reconciler boot — the event log is
            #    the source of truth (RFC §3.11) and the snapshot
            #    keeps the data recoverable.
            await self._event_log.arecord_and_save(
                lanlan_name, EVT_REFLECTION_STATE_CHANGED, payload,
                sync_load_view=_sync_load,
                sync_mutate_view=_sync_mutate,
                sync_save_view=_sync_save,
            )
            await aappend_to_shard(archive_dir, [archive_entry], now=now)
            logger.info(
                f"[Reflection] {lanlan_name}: 归档 reflection {reflection_id} "
                f"(score-driven, prev_status={prev_status}) → {shard_basename}"
            )
            return True

    async def aone_shot_archive_migration(self, lanlan_name: str) -> bool:
        """Migrate legacy flat ``reflections_archive.json`` → sharded dir.

        RFC §3.5.5: idempotent one-shot, runs at startup. Returns True if
        a migration actually happened, False if it was a no-op (file
        absent or sentinel already present).

        Failure logs but does NOT raise — boot must not stall on archive
        migration. Worst case: the flat file stays in place and the next
        boot re-tries.
        """
        from memory.archive_shards import amigrate_flat_archive_to_shards
        flat_path = self._reflections_legacy_archive_path(lanlan_name)
        archive_dir = self._reflections_archive_dir(lanlan_name)
        try:
            migrated, n_entries, n_shards = await amigrate_flat_archive_to_shards(
                flat_path, archive_dir,
            )
        except Exception as e:
            logger.warning(
                f"[Reflection] {lanlan_name}: 旧 reflections_archive 迁移失败 "
                f"(保留原文件 fallback): {e}"
            )
            return False
        if migrated:
            logger.info(
                f"[Reflection] {lanlan_name}: 旧 reflections_archive 迁移完成 "
                f"({n_entries} 条 → {n_shards} 分片)"
            )
        return migrated

    async def _aload_reflections_full(self, name: str) -> list[dict]:
        """Like aload_reflections(include_archived=True) but also keeps
        `merged` entries. Needed for aapply_signal + score-driven promote
        paths — we need to reach any non-active reflection by id as well."""
        path = self._reflections_path(name)
        if not await asyncio.to_thread(os.path.exists, path):
            return []
        try:
            data = await read_json_async(path)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[Reflection] 加载失败: {e}")
            return []
        if not isinstance(data, list):
            return []
        return [
            self._normalize_reflection(item)
            for item in data if isinstance(item, dict) and 'id' in item
        ]

    # ── mention suppress (confirmed only, mirrors persona §2.6) ──────

    @staticmethod
    def _in_window(ts_str: str, cutoff: datetime) -> bool:
        return in_window(ts_str, cutoff)

    @classmethod
    def _apply_record_reflection_mentions(
        cls,
        reflections: list[dict],
        response_text: str,
        stop_names: list[str] | None = None,
    ) -> bool:
        """When the AI response mentions any **confirmed** reflection's text →
        increment recent_mentions. Pending reflections are by design "the AI
        probing proactively"; suppressing them would break the mechanism — so
        only confirmed ones are scanned. Semantics aligned with
        persona._apply_record_mentions.

        ``stop_names`` are stripped before ``_is_mentioned``, so the
        ever-present master/lanlan + nicknames don't get unrelated reflections
        judged as mentioned.
        """
        return record_mentions(
            reflections,
            response_text,
            stop_names=stop_names,
            now=datetime.now(),
            window_hours=SUPPRESS_WINDOW_HOURS,
            mention_limit=SUPPRESS_MENTION_LIMIT,
            is_mentioned=_is_mentioned,
            is_in_window=cls._in_window,
        )

    @classmethod
    def _apply_update_reflection_suppressions(
        cls, reflections: list[dict],
    ) -> bool:
        def _log_bad_timestamp(value: str, error: Exception) -> None:
            logger.debug(
                f"[Reflection] suppressed_at 解析失败 ({value!r}): {error}"
            )

        return update_suppressions(
            reflections,
            now=datetime.now(),
            window_hours=SUPPRESS_WINDOW_HOURS,
            cooldown_hours=SUPPRESS_COOLDOWN_HOURS,
            on_bad_timestamp=_log_bad_timestamp,
            is_in_window=cls._in_window,
        )

    async def arecord_mentions(self, lanlan_name: str, response_text: str) -> None:
        """After the AI finishes a reply, scan confirmed reflections and accumulate
        mentions within the 5h window. Mentioned more than
        SUPPRESS_MENTION_LIMIT (=2) times in a row → stamp suppress=True.
        """
        if not response_text:
            return
        stop_names = await acollect_stop_names(self._config_manager, lanlan_name)
        async with self._get_alock(lanlan_name):
            reflections = await self._aload_reflections_full(lanlan_name)
            if self._apply_record_reflection_mentions(
                reflections, response_text, stop_names=stop_names,
            ):
                active = [
                    r for r in reflections
                    if r.get('status') not in REFLECTION_TERMINAL_STATUSES
                ]
                await self.asave_reflections(lanlan_name, active)

    async def aupdate_suppressions(self, lanlan_name: str) -> None:
        """Refresh suppress states before render: cooldown elapsed → lift; prune recent_mentions outside the window."""
        async with self._get_alock(lanlan_name):
            reflections = await self._aload_reflections_full(lanlan_name)
            if self._apply_update_reflection_suppressions(reflections):
                active = [
                    r for r in reflections
                    if r.get('status') not in REFLECTION_TERMINAL_STATUSES
                ]
                await self.asave_reflections(lanlan_name, active)

    # ── feedback lifecycle ───────────────────────────────────────────

    def get_pending_reflections(self, lanlan_name: str) -> list[dict]:
        """Get all pending (unconfirmed) reflections."""
        reflections = self.load_reflections(lanlan_name)
        return [r for r in reflections if r.get('status') == 'pending']

    async def aget_pending_reflections(self, lanlan_name: str) -> list[dict]:
        reflections = await self.aload_reflections(lanlan_name)
        return [r for r in reflections if r.get('status') == 'pending']

    @staticmethod
    def _filter_active_confirmed(
        reflections: list[dict], now: datetime | None = None,
    ) -> list[dict]:
        """Active confirmed = status='confirmed' AND score > 0 AND not suppressed.

        score <= 0: the user denied it repeatedly or it nets out to zero; it
        should neither enter the render's "fairly certain impressions" section
        (semantic drift), and it also ticks the background loop's archive
        counter (§3.5).
        suppress=True: the AI just mentioned this too often within the 5h
        window; silenced by persona's same mechanism (§2.6, orthogonal).
        """
        return filter_active_confirmed(
            reflections,
            now=now or datetime.now(),
            score=evidence_score,
        )

    def get_confirmed_reflections(self, lanlan_name: str) -> list[dict]:
        """Get all confirmed (soft persona) reflections that are still
        active — status='confirmed' AND score > 0 AND not mention-suppressed."""
        return self._filter_active_confirmed(self.load_reflections(lanlan_name))

    async def aget_confirmed_reflections(self, lanlan_name: str) -> list[dict]:
        return self._filter_active_confirmed(
            await self.aload_reflections(lanlan_name),
        )

    @staticmethod
    def _followup_render_key(value) -> str:
        """Return the text key used when deciding whether a followup can render.

        Keep this local instead of importing main_logic.topic.common.clean_text:
        memory is a lower layer and should not depend on prompt-rendering code.
        """
        return followup_render_key(value)

    @staticmethod
    def _filter_followup_candidates(pending: list[dict]) -> list[dict]:
        """Filter pending reflections for proactive chat candidacy.

        RFC §3.8.6 adds an `evidence_score >= 0` gate on top of the existing
        `next_eligible_at` cooldown. A reflection with score < 0 is
        "coldshouldered" by user signals but not yet archived — it stays in
        `reflections.json` but is skipped from active selection.

        Note: we intentionally DO NOT gate on the CONFIRMED_THRESHOLD upper
        bound — a pending reflection whose score has crossed into the
        derived-confirmed range is still a valid followup candidate. AI
        picking it up gives user a natural chance to re-affirm (or push
        back) before the periodic loop finally flips the stored status.

        Sampling: when the candidate pool > TOP_K, draw a weighted random sample
        without replacement by `evidence_score + WEIGHT_BASE`
        (Efraimidis-Spirakis), avoiding always picking the same top-K batch and
        making proactive chats repetitive. WEIGHT_BASE gives the "fresh,
        unsignaled" score=0 entries a minimum weight; otherwise an
        all-zero-score pool would degenerate into an empty set.
        `REFLECTION_FOLLOWUP_WEIGHTED=False` reverts to the old "first K in
        list order" behavior (for tests / debugging).
        """
        from config import (
            REFLECTION_SURFACE_TOP_K,
            REFLECTION_FOLLOWUP_WEIGHTED,
            REFLECTION_FOLLOWUP_WEIGHT_BASE,
        )
        from memory.temporal import weighted_sample_no_replace

        return filter_followup_candidates(
            pending,
            now=datetime.now(),
            score=evidence_score,
            render_key=ReflectionEngine._followup_render_key,
            weighted=REFLECTION_FOLLOWUP_WEIGHTED,
            top_k=REFLECTION_SURFACE_TOP_K,
            weight_base=REFLECTION_FOLLOWUP_WEIGHT_BASE,
            sample=weighted_sample_no_replace,
        )

    def get_followup_topics(self, lanlan_name: str) -> list[dict]:
        """Get pending reflections suitable for natural mention in proactive chat.

        Returns candidates that have passed their cooldown period.
        Does NOT persist anything — call record_surfaced() after reply is sent.
        """
        return self._filter_followup_candidates(self.get_pending_reflections(lanlan_name))

    async def aget_followup_topics(self, lanlan_name: str) -> list[dict]:
        pending = await self.aget_pending_reflections(lanlan_name)
        return self._filter_followup_candidates(pending)

    def _apply_record_surfaced(
        self, reflection_ids: list[str], reflections: list[dict], surfaced: list[dict],
    ) -> tuple[bool, list[dict]]:
        now = datetime.now()
        now_str = now.isoformat()
        next_eligible = (now + timedelta(minutes=REFLECTION_COOLDOWN_MINUTES)).isoformat()

        id_to_text = {r['id']: r.get('text', '') for r in reflections}
        cooldown_changed = False
        for r in reflections:
            if r.get('id') in reflection_ids:
                r['next_eligible_at'] = next_eligible
                cooldown_changed = True

        for rid in reflection_ids:
            found = False
            for s in surfaced:
                if s.get('reflection_id') == rid:
                    s['surfaced_at'] = now_str
                    s['text'] = id_to_text.get(rid, s.get('text', ''))
                    s['feedback'] = None
                    found = True
                    break
            if not found:
                surfaced.append({
                    'reflection_id': rid,
                    'text': id_to_text.get(rid, ''),
                    'surfaced_at': now_str,
                    'feedback': None,
                })
        return cooldown_changed, surfaced

    def record_surfaced(self, lanlan_name: str, reflection_ids: list[str]) -> None:
        """Record which reflections were actually mentioned in proactive chat.

        Called AFTER the reply is sent, not during candidate selection.
        Also refreshes the cooldown on surfaced reflections.
        """
        if not reflection_ids:
            return
        surfaced = self.load_surfaced(lanlan_name)
        reflections = self.load_reflections(lanlan_name)
        cooldown_changed, surfaced = self._apply_record_surfaced(
            reflection_ids, reflections, surfaced,
        )
        if cooldown_changed:
            self.save_reflections(lanlan_name, reflections)
        self.save_surfaced(lanlan_name, surfaced)

    async def arecord_surfaced(self, lanlan_name: str, reflection_ids: list[str]) -> None:
        """P2.a.2: per-character asyncio.Lock serializes reflections.json /
        surfaced.json writes, avoiding races with synth / promote."""
        if not reflection_ids:
            return
        async with self._get_alock(lanlan_name):
            surfaced = await self.aload_surfaced(lanlan_name)
            reflections = await self.aload_reflections(lanlan_name)
            cooldown_changed, surfaced = self._apply_record_surfaced(
                reflection_ids, reflections, surfaced,
            )
            if cooldown_changed:
                await self.asave_reflections(lanlan_name, reflections)
            await self.asave_surfaced(lanlan_name, surfaced)

    async def check_feedback(self, lanlan_name: str, user_messages: list[str]) -> list[dict] | None:
        """Check if user's recent messages confirm/deny surfaced reflections.

        Returns list of {reflection_id, feedback} dicts, or None on LLM/processing failure.

        P2.a.2: this method writes back surfaced.json (line 572), so it must be
        serialized under the character lock with arecord_surfaced /
        aconfirm_promotion / areject_promotion.
        """
        async with self._get_alock(lanlan_name):
            return await self._check_feedback_locked(lanlan_name, user_messages)

    async def _check_feedback_locked(self, lanlan_name: str, user_messages: list[str]) -> list[dict] | None:
        from config.prompts.prompts_memory import get_reflection_feedback_prompt
        from utils.language_utils import get_global_language
        from utils.llm_client import create_chat_llm_async

        surfaced = await self.aload_surfaced(lanlan_name)
        pending_surfaced = [s for s in surfaced if s.get('feedback') is None]
        if not pending_surfaced:
            return []

        reflections_text = "\n".join(
            f"- [{s['reflection_id']}] {s['text']}" for s in pending_surfaced
        )
        messages_text = "\n".join(user_messages)

        prompt = get_reflection_feedback_prompt(get_global_language()).format(
            reflections=reflections_text,
            messages=messages_text,
        )

        try:
            set_call_type("memory_feedback_check")
            api_config = self._config_manager.get_model_api_config('summary')
            # timeout=60: 后台 task 内调用，二分类任务 prompt + 输出都不大。
            # max_retries=0: 禁 SDK 自动重试。
            from config import LLM_OUTPUT_GUARD_MAX_TOKENS
            llm = await create_chat_llm_async(
                api_config['model'],
                api_config['base_url'], api_config['api_key'],
                timeout=60, max_retries=0,
                max_completion_tokens=LLM_OUTPUT_GUARD_MAX_TOKENS,  # runaway guard; generous so variable-length JSON isn't truncated
                provider_type=api_config.get('provider_type'),
            )
            try:
                resp = await llm.ainvoke(prompt)  # noqa: LLM_INPUT_BUDGET  # prompt assembled from token-capped memory components (REFLECTION_*/RECALL_* budgets in the prompt builder).
            finally:
                await llm.aclose()
            raw = resp.content.strip()
            if raw.startswith("```"):
                raw = raw.replace("```json", "").replace("```", "").strip()
            feedbacks = robust_json_loads(raw)
            if not isinstance(feedbacks, list):
                feedbacks = [feedbacks]
        except Exception as e:
            logger.warning(f"[Reflection] 反馈检查失败: {e}")
            return None  # 区别于 []（无反馈），None 表示调用失败

        # Update surfaced records (whitelist valid feedback values)
        _VALID_FEEDBACK = {'confirmed', 'denied', 'ignored'}
        for fb in feedbacks:
            if not isinstance(fb, dict):
                continue
            rid = fb.get('reflection_id')
            feedback = fb.get('feedback')
            if rid and feedback in _VALID_FEEDBACK:
                for s in surfaced:
                    if s.get('reflection_id') == rid:
                        s['feedback'] = feedback
        await self.asave_surfaced(lanlan_name, surfaced)

        return feedbacks

    async def check_feedback_for_confirmed(
        self, lanlan_name: str, confirmed: list[dict], user_messages: list[str],
    ) -> list[dict] | None:
        """Check if recent user messages rebut any confirmed reflections.

        Used by periodic rebuttal check (every 5 min). Only returns 'denied' or 'ignored'.
        Returns None on LLM/processing failure (same convention as check_feedback).
        """
        from config.prompts.prompts_memory import get_reflection_feedback_prompt
        from utils.language_utils import get_global_language
        from utils.llm_client import create_chat_llm_async

        if not confirmed or not user_messages:
            return []

        reflections_text = "\n".join(
            f"- [{r['id']}] {r['text']}" for r in confirmed
        )
        messages_text = "\n".join(user_messages)

        prompt = get_reflection_feedback_prompt(get_global_language()).format(
            reflections=reflections_text,
            messages=messages_text,
        )

        try:
            set_call_type("memory_rebuttal_check")
            api_config = self._config_manager.get_model_api_config('summary')
            # timeout=90: 开 thinking 后判断"用户最近的话否定了哪条 confirmed
            # reflection"——drain 模式下每批最多 20 条 user msg × 多条
            # confirmed reflection，思考能改善误判（防止把 user 的反讽 / 情景
            # 转换误标为否定）。完全后台无锁，没人等结果，安全开 thinking。
            # max_retries=0: 禁 SDK 自动重试，失败 cursor 不推进自然下轮重试。
            # extra_body=None: 显式开 thinking。
            from config import LLM_OUTPUT_GUARD_MAX_TOKENS
            llm = await create_chat_llm_async(
                api_config['model'],
                api_config['base_url'], api_config['api_key'],
                timeout=90, max_retries=0,
                max_completion_tokens=LLM_OUTPUT_GUARD_MAX_TOKENS,  # runaway guard; generous so variable-length JSON (incl. thinking) isn't truncated
                extra_body=None,
                provider_type=api_config.get('provider_type'),
            )
            try:
                resp = await llm.ainvoke(prompt)  # noqa: LLM_INPUT_BUDGET  # prompt assembled from token-capped memory components (REFLECTION_*/RECALL_* budgets in the prompt builder).
            finally:
                await llm.aclose()
            raw = resp.content.strip()
            if raw.startswith("```"):
                raw = raw.replace("```json", "").replace("```", "").strip()
            feedbacks = robust_json_loads(raw)
            if not isinstance(feedbacks, list):
                feedbacks = [feedbacks]
            return feedbacks
        except Exception as e:
            logger.warning(f"[Reflection] 反驳检查失败: {e}")
            return None

    @staticmethod
    def _apply_promotion_status(
        reflections: list[dict], reflection_id: str, status: str,
    ) -> str | None:
        return apply_promotion_status(
            reflections, reflection_id, status, now=datetime.now(),
        )

    def confirm_promotion(self, lanlan_name: str, reflection_id: str) -> None:
        """Mark reflection as confirmed (soft persona). Does NOT write to persona yet.

        Confirmed reflections exist independently for AUTO_PROMOTE_DAYS days,
        during which they can still be rebutted. After that, auto_promote_stale()
        upgrades them to real persona entries.
        """
        reflections = self.load_reflections(lanlan_name)
        text = self._apply_promotion_status(reflections, reflection_id, 'confirmed')
        if text is not None:
            logger.info(f"[Reflection] {lanlan_name}: 反思已确认(软persona) id={reflection_id} len={len(text)}")
            print(f"[Reflection] {lanlan_name}: 反思已确认(软persona): {text[:50]}...")
        self.save_reflections(lanlan_name, reflections)
        self._mark_surfaced_handled(lanlan_name, reflection_id, 'confirmed')

    async def aconfirm_promotion(self, lanlan_name: str, reflection_id: str) -> None:
        async with self._get_alock(lanlan_name):
            reflections = await self.aload_reflections(lanlan_name)
            text = self._apply_promotion_status(reflections, reflection_id, 'confirmed')
            if text is not None:
                logger.info(f"[Reflection] {lanlan_name}: 反思已确认(软persona) id={reflection_id} len={len(text)}")
                print(f"[Reflection] {lanlan_name}: 反思已确认(软persona): {text[:50]}...")
            await self.asave_reflections(lanlan_name, reflections)
            await self._amark_surfaced_handled(lanlan_name, reflection_id, 'confirmed')

    def reject_promotion(self, lanlan_name: str, reflection_id: str) -> None:
        """Mark a reflection as denied — won't be promoted."""
        reflections = self.load_reflections(lanlan_name)
        text = self._apply_promotion_status(reflections, reflection_id, 'denied')
        if text is not None:
            logger.info(f"[Reflection] {lanlan_name}: 反思被否定 id={reflection_id} len={len(text)}")
            print(f"[Reflection] {lanlan_name}: 反思被否定: {text[:50]}...")
        self.save_reflections(lanlan_name, reflections)
        self._mark_surfaced_handled(lanlan_name, reflection_id, 'denied')

    async def areject_promotion(self, lanlan_name: str, reflection_id: str) -> None:
        async with self._get_alock(lanlan_name):
            reflections = await self.aload_reflections(lanlan_name)
            text = self._apply_promotion_status(reflections, reflection_id, 'denied')
            if text is not None:
                logger.info(f"[Reflection] {lanlan_name}: 反思被否定 id={reflection_id} len={len(text)}")
                print(f"[Reflection] {lanlan_name}: 反思被否定: {text[:50]}...")
            await self.asave_reflections(lanlan_name, reflections)
            await self._amark_surfaced_handled(lanlan_name, reflection_id, 'denied')

    @staticmethod
    def _apply_mark_surfaced_handled(
        surfaced: list[dict], reflection_id: str, feedback: str,
    ) -> bool:
        return apply_mark_surfaced_handled(
            surfaced, reflection_id, feedback, now=datetime.now(),
        )

    def _mark_surfaced_handled(self, lanlan_name: str, reflection_id: str, feedback: str) -> None:
        """Mark surfaced record as handled so check_feedback won't reprocess it."""
        surfaced = self.load_surfaced(lanlan_name)
        if self._apply_mark_surfaced_handled(surfaced, reflection_id, feedback):
            self.save_surfaced(lanlan_name, surfaced)

    async def _amark_surfaced_handled(
        self, lanlan_name: str, reflection_id: str, feedback: str,
    ) -> None:
        surfaced = await self.aload_surfaced(lanlan_name)
        if self._apply_mark_surfaced_handled(surfaced, reflection_id, feedback):
            await self.asave_surfaced(lanlan_name, surfaced)

    def auto_promote_stale(self, lanlan_name: str) -> int:
        """Score-driven pending → confirmed (RFC §3.9.1 / §4.1 PR-1 scope).

        Deprecated sync version — retained for backward-compat callers
        (tests / CLI scripts). Production path is the async twin below.

        - The time-skip branches are deleted (`AUTO_CONFIRM_DAYS` / `AUTO_PROMOTE_DAYS` removed)
        - Only does pending → confirmed: `evidence_score(r, now) >= EVIDENCE_CONFIRMED_THRESHOLD`
        - confirmed → promoted is taken over by PR-3's `_apromote_with_merge`;
          in PR-1 this function carries no promotion responsibility
        Returns number of transitions.
        """
        reflections = self.load_reflections(lanlan_name)
        now = datetime.now()
        transitions = 0
        confirmed_ids: list[str] = []

        for r in reflections:
            if r.get('status') != 'pending':
                continue
            if evidence_score(r, now) < EVIDENCE_CONFIRMED_THRESHOLD:
                continue
            r['status'] = 'confirmed'
            r['confirmed_at'] = now.isoformat()
            confirmed_ids.append(r['id'])
            transitions += 1
            logger.info(
                f"[Reflection] {lanlan_name}: pending→confirmed"
                f" (score driven): {r['text'][:50]}..."
            )

        if transitions:
            self.save_reflections(lanlan_name, reflections)
            if confirmed_ids:
                self._batch_mark_surfaced_handled(
                    lanlan_name, confirmed_ids, 'confirmed',
                )
        return transitions

    async def aauto_promote_stale(self, lanlan_name: str) -> int:
        """P2.a.2: serialized by the character-level asyncio.Lock. Score-driven
        pending → confirmed (§3.9.1) + confirmed → promoted via merge-on-promote
        (RFC §3.9.3, PR-3). The promote pass uses `_apromote_with_merge`,
        which dispatches LLM merge decisions and updates throttle state.

        Returns the number of pending→confirmed transitions (the legacy
        contract). Promote attempts are logged but not folded into the
        return count — they're a separate signal kept in
        `last_promote_attempt_at` / `promote_attempt_count` per reflection.
        """
        async with self._get_alock(lanlan_name):
            transitions = await self._aauto_promote_stale_locked(lanlan_name)
        # Promote pass runs OUTSIDE the per-character lock because each
        # `_apromote_with_merge` re-acquires the lock internally and also
        # acquires the persona lock. Keeping the outer lock here would
        # serialize the LLM call within the lock and block other reflection
        # writes (e.g. arecord_mentions) for the duration of the network
        # round-trip — RFC §3.3.3 outer-async-inner-sync chain breaks down
        # if we hold across the LLM await.
        await self._aauto_promote_score_driven(lanlan_name)
        return transitions

    async def _aauto_promote_score_driven(self, lanlan_name: str) -> int:
        """Score-driven confirmed → promoted (RFC §3.9.1).

        Iterates `confirmed` reflections; for each whose
        `evidence_score >= EVIDENCE_PROMOTED_THRESHOLD`, dispatches to
        `_apromote_with_merge`. Returns the count of attempts (NOT the
        count of successful promotions — the LLM may merge / reject /
        skip per-throttle).

        Skips reflections in `promote_blocked` dead-letter status. Caller
        does NOT need to hold the per-character lock — `_apromote_with_merge`
        re-acquires both reflection and persona locks internally.
        """
        # Read snapshot OUTSIDE the lock — the promote pass tolerates a
        # slightly stale view (any concurrent state change will just mean
        # we attempt promote on a no-longer-confirmed reflection, which
        # the inner lock + status recheck will catch).
        reflections = await self._aload_reflections_full(lanlan_name)
        now = datetime.now()
        attempts = 0
        for r in reflections:
            if r.get('status') != 'confirmed':
                continue
            if evidence_score(r, now) < EVIDENCE_PROMOTED_THRESHOLD:
                continue
            try:
                outcome = await self._apromote_with_merge(lanlan_name, r)
                attempts += 1
                logger.info(
                    f"[Promote] {lanlan_name}/{r.get('id')}: "
                    f"outcome={outcome}"
                )
            except Exception as e:  # noqa: BLE001
                # Per-reflection failures don't sink the loop. The throttle
                # state in the reflection itself gates retries.
                logger.warning(
                    f"[Promote] {lanlan_name}/{r.get('id')}: "
                    f"unhandled error: {e}"
                )
        return attempts

    async def _aauto_promote_stale_locked(self, lanlan_name: str) -> int:
        """Score-driven pending → confirmed only.

        Must run **after** all evidence signals for this tick have been
        applied (signal dispatch emits EVT_REFLECTION_EVIDENCE_UPDATED then
        this loop reads the updated view to decide promotions). The caller
        is responsible for that ordering — see memory_server background loops.
        """
        reflections = await self._aload_reflections_full(lanlan_name)
        now = datetime.now()
        transitions = 0
        confirmed_ids: list[str] = []

        for r in reflections:
            if r.get('status') != 'pending':
                continue
            if evidence_score(r, now) < EVIDENCE_CONFIRMED_THRESHOLD:
                continue
            r['status'] = 'confirmed'
            r['confirmed_at'] = now.isoformat()
            confirmed_ids.append(r['id'])
            transitions += 1
            logger.info(
                f"[Reflection] {lanlan_name}: pending→confirmed"
                f" (score driven): {r['text'][:50]}..."
            )

        if transitions:
            # 写回的集合用 filter 出 active（non-terminal）reflections，匹配
            # aload_reflections 的行为；terminal (archived/merged 等) 条目
            # 由 _prepare_save_reflections 的 merge 逻辑处理。
            active = [
                r for r in reflections
                if r.get('status') not in REFLECTION_TERMINAL_STATUSES
            ]
            await self.asave_reflections(lanlan_name, active)
            if confirmed_ids:
                await self._abatch_mark_surfaced_handled(
                    lanlan_name, confirmed_ids, 'confirmed',
                )
        return transitions

    async def aauto_promote_time_driven(self, lanlan_name: str) -> int:
        """Time-driven fallback for the "strong memory OFF" mode.

        Zero LLM cost. Mimics pre-RFC behavior, advancing the lifecycle purely
        by reflection age:
          - pending for ``WEAK_MEMORY_AUTO_CONFIRM_DAYS`` days (by created_at)
            → status='confirmed', confirmed_at=now, auto_confirmed=True
          - confirmed for ``WEAK_MEMORY_AUTO_PROMOTE_DAYS`` days (by
            confirmed_at) → call ``persona.aadd_fact`` directly via the simple
            merge-in path, **not** the merge LLM. aadd_fact's internal heuristic
            dedup + character-card contradiction check are the safety net.

        Returns: total number of pending→confirmed + confirmed→promoted transitions.

        Mutually exclusive with ``aauto_promote_stale`` — the caller picks one
        based on the strong-memory switch. This method never reads
        evidence_score; missing/zero evidence fields have no effect.
        """
        from config import (
            WEAK_MEMORY_AUTO_CONFIRM_DAYS,
            WEAK_MEMORY_AUTO_PROMOTE_DAYS,
        )

        from memory.persona import PersonaManager
        async with self._get_alock(lanlan_name):
            reflections = await self._aload_reflections_full(lanlan_name)
            now = datetime.now()
            transitions = 0
            confirmed_ids: list[str] = []
            promoted_ids: list[str] = []
            denied_ids: list[str] = []

            # Pass 1: pending → confirmed by created_at age
            for r in reflections:
                if r.get('status') != 'pending':
                    continue
                created_iso = r.get('created_at')
                if not created_iso:
                    continue
                try:
                    created = datetime.fromisoformat(created_iso)
                except (ValueError, TypeError):
                    continue
                age_days = (now - created).total_seconds() / 86400
                if age_days < WEAK_MEMORY_AUTO_CONFIRM_DAYS:
                    continue
                r['status'] = 'confirmed'
                r['confirmed_at'] = now.isoformat()
                r['auto_confirmed'] = True
                rid = r.get('id')
                if rid:
                    confirmed_ids.append(rid)
                transitions += 1
                logger.info(
                    f"[Reflection] {lanlan_name}: pending→confirmed "
                    f"(time-driven, {int(age_days)}d): {r.get('text', '')[:50]}..."
                )

            # Pass 2: confirmed → promoted/denied by confirmed_at age
            # 注：刚被 Pass 1 翻成 confirmed 的 confirmed_at = now，age = 0 不会
            # 命中 14 天阈值——所以同一条 reflection 不会一轮内 pending→promoted。
            for r in reflections:
                if r.get('status') != 'confirmed':
                    continue
                confirmed_iso = r.get('confirmed_at')
                if not confirmed_iso:
                    continue
                try:
                    confirmed_at = datetime.fromisoformat(confirmed_iso)
                except (ValueError, TypeError):
                    continue
                age_days = (now - confirmed_at).total_seconds() / 86400
                if age_days < WEAK_MEMORY_AUTO_PROMOTE_DAYS:
                    continue

                # 直接走 persona.aadd_fact——pre-RFC 简单合入。三种返回：
                #   FACT_ADDED → 把 reflection status 翻到 promoted
                #   FACT_REJECTED_CARD → 翻到 denied (角色卡矛盾)
                #   FACT_QUEUED_CORRECTION → 在强力记忆关时 corrections queue
                #     不会被消化（resolve_corrections gate off），所以这里
                #     reflection 留在 confirmed，下轮再试。属于已知的轻度
                #     "记忆失活" case；rare（启发式 ratio ≥0.4 才命中）。
                rid = r.get('id')
                try:
                    code = await self._persona_manager.aadd_fact(
                        lanlan_name, r.get('text', ''),
                        entity=r.get('entity', 'relationship'),
                        source='reflection_time_driven',
                        source_id=rid,
                    )
                except Exception as e:
                    logger.warning(
                        f"[Promote] {lanlan_name}/{rid}: time-driven aadd_fact 失败: {e}"
                    )
                    continue

                if code == PersonaManager.FACT_ADDED:
                    r['status'] = 'promoted'
                    r['promoted_at'] = now.isoformat()
                    if rid:
                        promoted_ids.append(rid)
                    transitions += 1
                    logger.info(
                        f"[Reflection] {lanlan_name}: confirmed→promoted "
                        f"(time-driven, {int(age_days)}d, no LLM): "
                        f"{r.get('text', '')[:50]}..."
                    )
                elif code == PersonaManager.FACT_REJECTED_CARD:
                    r['status'] = 'denied'
                    r['denied_reason'] = 'rejected_by_persona_card_time_driven'
                    if rid:
                        denied_ids.append(rid)
                    transitions += 1
                    logger.info(
                        f"[Reflection] {lanlan_name}: confirmed→denied "
                        f"(time-driven, 角色卡矛盾): {rid}"
                    )
                # FACT_QUEUED_CORRECTION → 留在 confirmed，下轮再试

            # 单次落盘：按完整 reflections 列表（含刚被翻成 promoted/denied 的
            # 终态条目）保存。**不过滤 REFLECTION_TERMINAL_STATUSES**——上一
            # 版 bug 是过滤后终态条目变成 inactive，被
            # _prepare_save_reflections 当成 stale 不写主文件，导致 promoted/
            # denied 翻转丢失。asave_reflections 内部 _prepare_save_reflections
            # 会按 _REFLECTION_ARCHIVE_DAYS 自然把陈旧 terminal 移到 archive
            # 分片，不需要这里再做过滤。
            # 任何 transition（confirmed/promoted/denied）都触发 save——只 gate
            # 在 promoted_ids 上会丢掉纯 FACT_REJECTED_CARD 批次的 denied 翻转。
            if transitions:
                await self.asave_reflections(lanlan_name, reflections)
                handled_ids = confirmed_ids + promoted_ids + denied_ids
                if handled_ids:
                    await self._abatch_mark_surfaced_handled(
                        lanlan_name, handled_ids, 'confirmed',
                    )

        return transitions

    async def _abump_reflection_recheck_attempts(
        self, lanlan_name: str, rid: str, reason: str,
    ) -> None:
        """Increment the given reflection's ``recheck_attempts`` counter.

        Once failures reach the ``MEMORY_RECHECK_MAX_ATTEMPTS`` cap, the
        candidates filter excludes the entry so the loop gives its slot to
        other v1 entries. The counter is persisted to the main file and
        survives restarts (so the same bad entry can't keep starving others by
        burning LLM quota).
        Best-effort — save failures don't raise; the next run tries again.
        """
        try:
            async with self._get_alock(lanlan_name):
                current = await self._aload_reflections_full(lanlan_name)
                for r in current:
                    if r.get('id') == rid:
                        r['recheck_attempts'] = (r.get('recheck_attempts') or 0) + 1
                        # 戳失败时刻供 dead-letter 时间自愈（cooldown_elapsed）
                        r['last_recheck_attempt_at'] = datetime.now().isoformat()
                        # 传 active-only：对齐 _abump_refine_attempts / arecord_mentions
                        # 的 save 约定，让 promoted/denied 等 terminal 条目正常归档，
                        # 不被多留一个周期（CodeRabbit 一致性 nitpick）。
                        active = [
                            x for x in current
                            if x.get('status') not in REFLECTION_TERMINAL_STATUSES
                        ]
                        await self.asave_reflections(lanlan_name, active)
                        logger.debug(
                            f"[Recheck-Reflection] {lanlan_name} {rid}: "
                            f"recheck_attempts → {r['recheck_attempts']} ({reason})"
                        )
                        return
        except Exception as e:
            logger.debug(
                f"[Recheck-Reflection] {lanlan_name} {rid}: bump attempts 失败: {e}"
            )

    async def arecheck_one_legacy_reflection(self, lanlan_name: str) -> bool:
        """Schema v1 → v2 slow recheck (processes only 1 entry per call).

        Finds the character's oldest non-archived reflection with
        schema_version < CURRENT, asks the LLM to re-label temporal_scope
        (pattern/state/episode) + event_when (relative offsets); the system
        resolves them into ISO against reflection.created_at and writes them
        back.

        Returns: True when one entry was processed successfully; False when no
        candidate was found or processing failed.
        The caller (memory_server._periodic_slow_memory_recheck_loop) may
        proceed to the next item without sleeping on True (in practice it still
        paces at one per 30s).

        Skip conditions:
        - schema_version >= CURRENT (already upgraded)
        - status in REFLECTION_TERMINAL_STATUSES (archived/merged etc., terminal)
        - already outside the main reflections.json (archive shards aren't
          loaded by load_reflections)
        """
        from config import (
            MEMORY_SCHEMA_VERSION_CURRENT as _SCHEMA_V,
            MEMORY_RECHECK_MAX_ATTEMPTS as _MAX_ATTEMPTS,
            MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS as _SELF_HEAL,
        )
        from config.prompts.prompts_memory import (
            MEMORY_RECHECK_REFLECTION_PROMPT,
        )
        from memory.temporal import (
            normalize_event_when as _norm_when,
            compute_event_timestamps as _compute_ts,
            cooldown_elapsed,
            ACTIVE_TEMPORAL_SCOPES,
        )

        # ── 锁外：选候选 + LLM 调用 ─────────────────────────────────
        reflections = await self.aload_reflections(lanlan_name)
        candidates = [
            r for r in reflections
            if (r.get('schema_version') or 1) < _SCHEMA_V
            and r.get('status') not in REFLECTION_TERMINAL_STATUSES
            # 重试预算：LLM 持续给出无效 temporal_scope 或抛异常的 entry
            # 累计达上限后不再阻塞队列（Codex review on PR #1316 P2 catch）。
            # 时间自愈：达上限的 entry 过 _SELF_HEAL 后放行一次 probe。
            and (
                (r.get('recheck_attempts') or 0) < _MAX_ATTEMPTS
                or cooldown_elapsed(r.get('last_recheck_attempt_at'), _SELF_HEAL)
            )
        ]
        if not candidates:
            return False
        # 最老优先（FIFO 迁移），id 兜底排序保稳定
        candidates.sort(key=lambda r: (r.get('created_at', ''), r.get('id', '')))
        # Skip malformed candidates (missing id / created_at) instead of
        # aborting the whole call — otherwise a single bad legacy entry at
        # head of FIFO order would starve every later v1 reflection forever
        # (Codex review on PR #1316 P2 catch).
        target: dict | None = None
        rid = ''
        created_at_iso = ''
        for c in candidates:
            cid = c.get('id')
            cts = c.get('created_at', '')
            if not cid or not cts:
                logger.debug(
                    f"[Recheck-Reflection] {lanlan_name}: skip malformed legacy "
                    f"reflection (id={cid!r} created_at={cts!r})"
                )
                continue
            target = c
            rid = cid
            created_at_iso = cts
            break
        if target is None:
            return False

        # 拉 source facts 上下文（最多 5 条文本，太多 prompt 太长）
        source_fact_ids = target.get('source_fact_ids') or []
        source_facts_text = ""
        if source_fact_ids:
            try:
                all_facts = await self._fact_store.aload_facts(lanlan_name)
                fact_by_id = {f.get('id'): f for f in all_facts}
                lines = []
                for fid in source_fact_ids[:5]:
                    f = fact_by_id.get(fid)
                    if f and f.get('text'):
                        lines.append(f"- {f['text']}")
                source_facts_text = "\n".join(lines) if lines else "（无）"
            except Exception:
                source_facts_text = "（无）"
        else:
            source_facts_text = "（无）"

        prompt = MEMORY_RECHECK_REFLECTION_PROMPT.format(
            REFLECTION_TEXT=target.get('text', ''),
            CREATED_AT=created_at_iso,
            SOURCE_FACTS=source_facts_text,
        )

        failure_reason: str | None = None
        new_scope: str | None = None
        event_when_raw: dict | None = None
        try:
            from utils.llm_client import create_chat_llm_async
            set_call_type("memory_recheck_reflection")
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
                resp = await llm.ainvoke(prompt)  # noqa: LLM_INPUT_BUDGET  # prompt assembled from token-capped memory components (REFLECTION_*/RECALL_* budgets in the prompt builder).
            finally:
                await llm.aclose()
            raw = resp.content.strip()
            if raw.startswith("```"):
                raw = raw.replace("```json", "").replace("```", "").strip()
            result = robust_json_loads(raw)
            if not isinstance(result, dict):
                failure_reason = "non-dict response"
            else:
                new_scope = result.get('temporal_scope')
                if new_scope not in ACTIVE_TEMPORAL_SCOPES:
                    failure_reason = f"invalid temporal_scope={new_scope!r}"
                else:
                    event_when_raw = _norm_when(result.get('event_when'))
        except Exception as e:
            failure_reason = f"LLM call failed: {e}"

        # 失败路径统一收口：bump recheck_attempts 计数器，让连续失败的 entry
        # 在达到 MAX 后被 candidates filter 排除（Codex review on PR #1316 P2）
        if failure_reason is not None:
            logger.debug(
                f"[Recheck-Reflection] {lanlan_name} {rid}: 跳过本轮 ({failure_reason})"
            )
            await self._abump_reflection_recheck_attempts(lanlan_name, rid, failure_reason)
            return False

        # 用 created_at 当锚点解算 ISO
        _needs_end = new_scope in ('state', 'episode')
        event_start_at, event_end_at = _compute_ts(
            event_when_raw,
            created_at_iso,
            fallback_start=True,
            fallback_end=_needs_end,
        )

        # ── 锁内：reload + 找同 id + 更新字段 + save ──────────────
        save_failed_reason: str | None = None
        async with self._get_alock(lanlan_name):
            current = await self._aload_reflections_full(lanlan_name)
            found = None
            for r in current:
                if r.get('id') == rid:
                    found = r
                    break
            if found is None:
                logger.debug(f"[Recheck-Reflection] {lanlan_name} {rid}: 锁内已不存在")
                return False
            # 再次检查 schema_version（防并发重复处理）
            if (found.get('schema_version') or 1) >= _SCHEMA_V:
                return False
            found['temporal_scope'] = new_scope
            found['event_when_raw'] = event_when_raw
            found['event_start_at'] = event_start_at
            found['event_end_at'] = event_end_at
            found['schema_version'] = _SCHEMA_V
            try:
                await self.asave_reflections(lanlan_name, current)
            except Exception as e:
                # 落盘失败也要计入 recheck_attempts：否则 cloudsave 维护态 /
                # 只读 FS / 权限导致的持续写盘失败会让同一条 reflection 每 30s
                # 原样重判、熔断永不触发（对齐上面 LLM 失败路径的 bump）。
                # bump helper 自取锁，asyncio.Lock 不可重入，必须退出本锁后再调。
                save_failed_reason = f"save failed: {e}"

        if save_failed_reason is not None:
            logger.warning(
                f"[Recheck-Reflection] {lanlan_name} {rid}: {save_failed_reason}"
            )
            await self._abump_reflection_recheck_attempts(
                lanlan_name, rid, save_failed_reason,
            )
            return False

        logger.info(
            f"[Recheck-Reflection] {lanlan_name} {rid}: v1→v{_SCHEMA_V} "
            f"scope={new_scope} when={event_when_raw}"
        )
        return True

    async def areset_confirmed_at_to_now(self, lanlan_name: str) -> int:
        """On→off migration: reset every confirmed reflection's confirmed_at to now,
        making the time-driven fallback run the full 14-day clock.

        Avoids the jarring experience of "user enabled it for a while, turned
        it off, and old confirmed entries immediately hit the 14-day mark and
        promote in bulk". Returns the number of affected entries.
        """
        async with self._get_alock(lanlan_name):
            reflections = await self._aload_reflections_full(lanlan_name)
            now_iso = datetime.now().isoformat()
            count = 0
            for r in reflections:
                if r.get('status') == 'confirmed':
                    r['confirmed_at'] = now_iso
                    count += 1
            if count:
                active = [
                    r for r in reflections
                    if r.get('status') not in REFLECTION_TERMINAL_STATUSES
                ]
                await self.asave_reflections(lanlan_name, active)
        return count

    # ── Merge-on-promote (RFC §3.9) ───────────────────────────────────

    @staticmethod
    def _compute_merged_evidence(
        target: dict, reflection: dict,
    ) -> tuple[float, float]:
        """Conservative max-rule for merging two evidence pairs (RFC §3.9.5).

        Why max not sum: sum would inflate evidence whenever the reflection
        and the target persona entry were independently reinforced from the
        same underlying user signals (Stage-2 + check_feedback dual-pickup
        is the canonical case). max represents "the strongest user assertion
        across either witness" — never invents evidence the user never gave.
        """
        return compute_merged_evidence(target, reflection)

    @staticmethod
    def _within_backoff(reflection: dict, now: datetime | None = None) -> bool:
        """Throttle gate (RFC §3.9.2): True if last attempt was within
        EVIDENCE_PROMOTE_RETRY_BACKOFF_MINUTES of `now`."""
        last = reflection.get('last_promote_attempt_at')
        if not last:
            return False
        if now is None:
            now = datetime.now()
        try:
            last_ts = datetime.fromisoformat(last)
        except (ValueError, TypeError):
            return False
        elapsed_min = (now - last_ts).total_seconds() / 60.0
        return elapsed_min < EVIDENCE_PROMOTE_RETRY_BACKOFF_MINUTES

    @staticmethod
    def _exceeds_max_retries(reflection: dict) -> bool:
        return (
            int(reflection.get('promote_attempt_count', 0) or 0)
            >= EVIDENCE_PROMOTE_MAX_RETRIES
        )

    async def _arecord_promote_attempt(
        self, name: str, reflection_id: str, now_iso: str,
    ) -> None:
        """Public wrapper: acquires the per-character lock then delegates
        to `_arecord_promote_attempt_locked`. See that method for details.
        """
        async with self._get_alock(name):
            await self._arecord_promote_attempt_locked(
                name, reflection_id, now_iso,
            )

    async def _arecord_promote_attempt_locked(
        self, name: str, reflection_id: str, now_iso: str,
    ) -> None:
        """Increment `promote_attempt_count` and stamp
        `last_promote_attempt_at` via EVT_REFLECTION_EVIDENCE_UPDATED.

        Caller MUST hold `self._get_alock(name)`. This locked variant
        exists so `_apromote_with_merge` can fuse throttle-check +
        attempt-record into a single critical section (CodeRabbit PR
        #936 round-5 Major #3) — otherwise two concurrent invocations
        could both pass the pre-lock backoff check and both record
        attempts, defeating the throttle.

        Why an evidence event for a counter (not a state change): the
        throttle counters live alongside evidence on each reflection and
        must be event-sourced so reconciler replay restores them after
        crash. Reusing the existing evidence handler avoids inventing a
        third event type — the handler whitelists keys it copies, and
        `promote_attempt_count` / `last_promote_attempt_at` are now on the
        whitelist (see evidence_handlers._EVIDENCE_SNAPSHOT_KEYS).
        """
        from memory.event_log import EVT_REFLECTION_EVIDENCE_UPDATED
        if self._event_log is None:
            raise RuntimeError(
                "[Reflection._arecord_promote_attempt] event_log 未注入"
            )

        reflections_full = await self._aload_reflections_full(name)
        entry = self._find_reflection_in_list(reflections_full, reflection_id)
        if entry is None:
            logger.warning(
                f"[Reflection] {name}: _arecord_promote_attempt 找不到 "
                f"reflection_id={reflection_id}"
            )
            return

        new_count = int(entry.get('promote_attempt_count', 0) or 0) + 1
        payload = {
            'reflection_id': reflection_id,
            # Full-snapshot evidence values (unchanged by this event,
            # but present so the handler+replay see the consistent view).
            'reinforcement': float(entry.get('reinforcement', 0.0) or 0.0),
            'disputation': float(entry.get('disputation', 0.0) or 0.0),
            'rein_last_signal_at': entry.get('rein_last_signal_at'),
            'disp_last_signal_at': entry.get('disp_last_signal_at'),
            'sub_zero_days': int(entry.get('sub_zero_days', 0) or 0),
            'user_fact_reinforce_count':
                int(entry.get('user_fact_reinforce_count', 0) or 0),
            # New throttle fields — whitelisted in the handler.
            'last_promote_attempt_at': now_iso,
            'promote_attempt_count': new_count,
            'source': 'promote_attempt',
        }

        def _sync_load(_n: str):
            return reflections_full

        def _sync_mutate(_view):
            entry['last_promote_attempt_at'] = now_iso
            entry['promote_attempt_count'] = new_count

        def _sync_save(n: str, view):
            assert_cloudsave_writable(
                self._config_manager,
                operation="save",
                target=f"memory/{n}/reflections.json",
            )
            atomic_write_json(
                self._reflections_path(n), view,
                indent=2, ensure_ascii=False,
            )

        await self._event_log.arecord_and_save(
            name, EVT_REFLECTION_EVIDENCE_UPDATED, payload,
            sync_load_view=_sync_load,
            sync_mutate_view=_sync_mutate,
            sync_save_view=_sync_save,
        )

    async def _arecord_state_change(
        self, name: str, reflection_id: str, from_status: str, to_status: str,
        *, absorbed_into: str | None = None, reason: str | None = None,
        reject_explanation: str | None = None,
    ) -> None:
        """Mutate a reflection's `status` and emit
        EVT_REFLECTION_STATE_CHANGED for audit + reconciler replay.

        Replay path: `make_reflection_state_changed_composite` in
        `memory/evidence_handlers.py` dispatches on the `to` field —
        `'archived'` routes to the archive handler (PR-2, RFC §3.5),
        any other status routes to `make_reflection_state_changed_handler`
        which re-applies `status`, `<status>_at` timestamp,
        `absorbed_into`, `promote_blocked_reason` (when to='promote_blocked'),
        `denied_reason` (when to='denied'), and `reject_reason` onto the
        reflection entry keyed by `reflection_id`. The persisted view
        updated in this same record_and_save call is therefore
        redundant with the replay path — both paths are kept (view for
        live reads, event for crash recovery). See RFC §3.9.6.
        """
        from memory.event_log import EVT_REFLECTION_STATE_CHANGED
        if self._event_log is None:
            raise RuntimeError(
                "[Reflection._arecord_state_change] event_log 未注入"
            )

        async with self._get_alock(name):
            reflections_full = await self._aload_reflections_full(name)
            entry = self._find_reflection_in_list(reflections_full, reflection_id)
            if entry is None:
                logger.warning(
                    f"[Reflection] {name}: _arecord_state_change 找不到 "
                    f"reflection_id={reflection_id}"
                )
                return

            # Compare-and-swap on `from_status`. _apromote_with_merge runs
            # the LLM call OUTSIDE the per-character lock (LLM is slow); a
            # concurrent rebuttal / archive / parallel promote can flip the
            # status during that window. Without this guard the late writer
            # silently overwrites a newer terminal status (e.g. promote
            # clobbers a freshly-set `denied` from rebuttal), losing the
            # newer signal AND emitting a misleading state-change event
            # whose `from` no longer matches the on-disk view.
            current_status = entry.get('status')
            if current_status != from_status:
                logger.warning(
                    f"[Reflection] {name}/{reflection_id}: "
                    f"_arecord_state_change CAS miss — expected from={from_status!r} "
                    f"but current status is {current_status!r}; dropping "
                    f"transition to {to_status!r} (newer signal wins)"
                )
                return

            now_iso = datetime.now().isoformat()
            payload: dict = {
                'reflection_id': reflection_id,
                'from': from_status,
                'to': to_status,
                'ts': now_iso,
            }
            if absorbed_into is not None:
                payload['absorbed_into'] = absorbed_into
            if reason is not None:
                payload['reason'] = reason
            if reject_explanation is not None:
                payload['reject_explanation'] = reject_explanation

            def _sync_load(_n: str):
                return reflections_full

            def _sync_mutate(_view):
                entry['status'] = to_status
                entry[f'{to_status}_at'] = now_iso
                if absorbed_into is not None:
                    entry['absorbed_into'] = absorbed_into
                if reason is not None:
                    # Route the `reason` audit string into a status-specific
                    # field so the semantics of each terminal-state field
                    # stay clean (RFC §3.9.2 / §3.9.7):
                    #   - promote_blocked → `promote_blocked_reason` (the
                    #     throttle/dead-letter cause; consumed by the dead-
                    #     letter retry path).
                    #   - denied → `denied_reason` (the rejection category;
                    #     audit only, no recovery path).
                    #   - merged → `absorbed_into` already captures
                    #     provenance; no separate reason needed and the
                    #     callers don't pass one.
                    # Without this gate any non-None reason on a denied/
                    # merged transition would pollute promote_blocked_reason,
                    # making dead-letter scans see false-positive blocks.
                    # Mirror of the reconciler handler in
                    # `make_reflection_state_changed_handler`, which already
                    # gates the same way on replay.
                    if to_status == 'promote_blocked':
                        entry['promote_blocked_reason'] = reason
                    elif to_status == 'denied':
                        entry['denied_reason'] = reason
                if reject_explanation is not None:
                    # Keep audit trail off the throttle field — separate key
                    # so promote_blocked vs llm_merge_rejected stays distinct.
                    entry['reject_reason'] = reject_explanation

            def _sync_save(n: str, view):
                # Save via _prepare_save_reflections so terminal entries
                # (merged / promote_blocked) round-trip correctly.
                # asave_reflections handles the merge with on-disk state.
                # We're already inside the per-character lock, so call the
                # raw save path (`asave_reflections` re-acquires no lock).
                assert_cloudsave_writable(
                    self._config_manager,
                    operation="save",
                    target=f"memory/{n}/reflections.json",
                )
                atomic_write_json(
                    self._reflections_path(n), view,
                    indent=2, ensure_ascii=False,
                )

            await self._event_log.arecord_and_save(
                name, EVT_REFLECTION_STATE_CHANGED, payload,
                sync_load_view=_sync_load,
                sync_mutate_view=_sync_mutate,
                sync_save_view=_sync_save,
            )

    async def _amark_promote_blocked(
        self, name: str, reflection: dict, reason: str,
    ) -> None:
        """Move reflection to promote_blocked dead-letter (RFC §3.9.2).

        Distinct from `_arecord_state_change` to keep the throttle-vs-merge
        decision points readable in `_apromote_with_merge`.
        """
        await self._arecord_state_change(
            name, reflection['id'], 'confirmed', 'promote_blocked',
            reason=reason,
        )

    async def _allm_call_promotion_merge(
        self, R: dict, persona_pool: list[tuple[str, dict]],
        reflection_pool: list[dict], lanlan_name: str, master_name: str,
    ) -> dict:
        """LLM call producing the merge decision JSON (RFC §3.9.7 prompt).

        Returns the parsed dict on success. Raises on any LLM / parse
        failure — caller (`_apromote_with_merge`) catches and treats as
        skip_retry_pending per §3.9.4.
        """
        from config.prompts.prompts_memory import get_promotion_merge_prompt
        from utils.language_utils import get_global_language
        from utils.llm_client import create_chat_llm_async

        now = datetime.now()
        # Build the impression pool block with stable ordering — protected
        # persona entries first, then non-protected by score DESC, then
        # confirmed/promoted reflections by score DESC.
        pool_lines: list[str] = []
        for ek, e in persona_pool:
            pool_lines.append(
                f"[persona.{ek}.{e.get('id')}] \"{e.get('text', '')}\""
                f" (evidence_score={evidence_score(e, now):.2f})"
            )
        for r in reflection_pool:
            pool_lines.append(
                f"[reflection.{r.get('id')}] \"{r.get('text', '')}\""
                f" (evidence_score={evidence_score(r, now):.2f})"
            )
        pool_text = "\n".join(pool_lines) if pool_lines else "(印象池为空)"
        # Cap the impression pool at PERSONA_MERGE_POOL_MAX_TOKENS — same
        # entity 长期累积下来 persona+reflection 池可能超 8k tokens；
        # 这里整段截尾（按 score DESC 已排序，超出的是低分项，可丢）。
        from config import PERSONA_MERGE_POOL_MAX_TOKENS
        from utils.tokenize import truncate_to_tokens
        pool_text = truncate_to_tokens(pool_text, PERSONA_MERGE_POOL_MAX_TOKENS)

        prompt = get_promotion_merge_prompt(get_global_language()).format(
            AI_NAME=lanlan_name,
            MASTER_NAME=master_name,
            R_TEXT=R.get('text', ''),
            R_SCORE=f"{evidence_score(R, now):.2f}",
            IMPRESSION_POOL=pool_text,
        )

        set_call_type("memory_promote_merge")
        api_config = self._config_manager.get_model_api_config(
            EVIDENCE_PROMOTION_MERGE_MODEL_TIER,
        )
        # timeout=90: 开 thinking 后 promote merge 决策（merge_into / promote_fresh /
        # reject + target_id 选择 + 重写 merged_text）值得思考——后果不可逆
        # （persona pollution），已有 throttle/backoff/dead-letter 兜底，开
        # thinking 完全在收益侧。LLM 调用本身在锁外（pre/post 短临界区分别拿
        # reflection 锁做 stamp 和 CAS），所以 90s 不阻塞同角色其他 reflection 写。
        # max_retries=0: 禁 SDK 自动重试，由 throttle/dead-letter 兜底。
        # extra_body=None: 显式开 thinking。
        from config import LLM_OUTPUT_GUARD_MAX_TOKENS
        llm = await create_chat_llm_async(
            api_config['model'],
            api_config['base_url'], api_config['api_key'],
            timeout=90, max_retries=0,
            max_completion_tokens=LLM_OUTPUT_GUARD_MAX_TOKENS,  # runaway guard; generous so variable-length JSON (incl. thinking) isn't truncated
            extra_body=None,
            provider_type=api_config.get('provider_type'),
        )
        try:
            resp = await llm.ainvoke(prompt)
        finally:
            await llm.aclose()
        raw = resp.content.strip()
        if raw.startswith("```"):
            raw = raw.replace("```json", "").replace("```", "").strip()
        decision = robust_json_loads(raw)
        if not isinstance(decision, dict):
            raise ValueError(
                f"LLM merge decision is not a dict: {type(decision).__name__}"
            )
        return decision

    async def _apromote_with_merge(
        self, lanlan_name: str, R: dict,
    ) -> str:
        """Score-driven confirmed → promoted via LLM merge decision
        (RFC §3.9.3).

        Returns one of:
          'promote_fresh' | 'merge_into' | 'reject' | 'reject_by_persona'
          | 'queued_correction' | 'skip_retry_pending' | 'blocked'
          | 'invalid_target' | 'noop' | 'no_longer_eligible'

        Throttling: backoff window before retry, max retries → dead-letter.
        LLM failures → skip_retry_pending (NOT promote_fresh — RFC §3.9.4
        explicitly forbids silent fresh-fallback because that breaks
        dedup semantics during outages).
        """
        # Pre-lock advisory throttle check — cheap early-exit when the
        # reflection is obviously inside its backoff window. The
        # authoritative throttle + attempt-record fuse happens under
        # the lock below (CodeRabbit PR #936 round-5 Major #3); this
        # pre-check just saves a lock acquisition in the common case.
        now = datetime.now()
        if self._within_backoff(R, now):
            return 'skip_retry_pending'
        if self._exceeds_max_retries(R):
            await self._amark_promote_blocked(
                lanlan_name, R, 'llm_unavailable',
            )
            return 'blocked'

        # Revalidate snapshot + throttle gate + record attempt — ALL
        # inside the same lock section. CodeRabbit PR #936 round-5
        # Major #3: without this fusion, two concurrent invocations
        # can both pass the pre-lock backoff check, both take the lock
        # serially, and both record attempts — defeating the throttle
        # and double-bumping promote_attempt_count toward the
        # max-retries dead-letter.
        #
        # The caller (`_aauto_promote_score_driven`) iterates a stale
        # snapshot collected outside the per-character lock; by the
        # time we reach this point another coroutine may have demoted,
        # denied, merged or otherwise disqualified `R`. Bumping
        # `promote_attempt_count` for an already-merged reflection
        # both wastes a retry slot and emits a misleading evidence
        # event. Re-read the on-disk view, find the same id, and
        # short-circuit if it's no longer eligible.
        async with self._get_alock(lanlan_name):
            current_list = await self._aload_reflections_full(lanlan_name)
            current = self._find_reflection_in_list(current_list, R['id'])
            if current is None or current.get('status') != 'confirmed':
                logger.info(
                    f"[Promote] {lanlan_name}/{R['id']}: no longer eligible "
                    f"(current status={current.get('status') if current else 'gone'!r}); "
                    f"skip"
                )
                return 'no_longer_eligible'
            if evidence_score(current, now) < EVIDENCE_PROMOTED_THRESHOLD:
                logger.info(
                    f"[Promote] {lanlan_name}/{R['id']}: evidence_score "
                    f"dropped below threshold under lock; skip"
                )
                return 'no_longer_eligible'
            # Re-check throttle against the freshly-read entry — this
            # closes the race where another coroutine recorded an
            # attempt between our pre-lock check and our lock
            # acquisition.
            if self._within_backoff(current, now):
                logger.info(
                    f"[Promote] {lanlan_name}/{R['id']}: another attempt "
                    f"recorded during lock wait; skip_retry_pending"
                )
                return 'skip_retry_pending'
            if self._exceeds_max_retries(current):
                # Must release lock before calling _amark_promote_blocked
                # (it re-acquires the same lock via _arecord_state_change).
                R = current
                break_for_blocked = True
            else:
                break_for_blocked = False
                # Use the freshly-read entry from here on so downstream
                # merge math sees the latest evidence values.
                R = current
                # Record the attempt INSIDE the lock so the throttle
                # stamp lands atomically with the eligibility decision.
                # `_arecord_promote_attempt_locked` expects the caller
                # to hold the lock — we do.
                now_iso = datetime.now().isoformat()
                await self._arecord_promote_attempt_locked(
                    lanlan_name, R['id'], now_iso,
                )

        if break_for_blocked:
            await self._amark_promote_blocked(
                lanlan_name, R, 'llm_unavailable',
            )
            return 'blocked'

        # Build candidate pool (RFC §3.9.3 step 1) — outside the lock
        # because same_entity_persona / same_entity_reflections are
        # advisory inputs to the LLM; stale reads cost at most a
        # suboptimal merge decision, not correctness.
        persona_view = await self._persona_manager.aget_persona(lanlan_name)
        target_entity = R.get('entity')
        same_entity_persona: list[tuple[str, dict]] = []
        if target_entity and isinstance(persona_view, dict):
            section = persona_view.get(target_entity)
            if isinstance(section, dict):
                for e in section.get('facts', []):
                    if isinstance(e, dict) and not e.get('protected'):
                        same_entity_persona.append((target_entity, e))
        all_reflections = await self._aload_reflections_full(lanlan_name)
        same_entity_reflections = [
            r for r in all_reflections
            if r.get('entity') == target_entity
            and r.get('status') in ('confirmed', 'promoted')
            and r.get('id') != R.get('id')
        ]

        try:
            _, _, _, _, name_mapping, _, _, _, _ = (
                await self._config_manager.aget_character_data()
            )
            master_name = name_mapping.get('human', '主人')
            decision = await self._allm_call_promotion_merge(
                R, same_entity_persona, same_entity_reflections,
                lanlan_name, master_name,
            )
        except Exception as e:  # noqa: BLE001
            # RFC §3.9.4: LLM failure does NOT downgrade to promote_fresh.
            # Reflection stays confirmed; throttle state was already bumped
            # above. Next cycle (after backoff) will retry; max-retries
            # tips the reflection into promote_blocked dead-letter.
            logger.warning(
                f"[Promote] {lanlan_name}/{R['id']}: LLM merge call failed: "
                f"{e}; reflection stays confirmed for retry"
            )
            return 'skip_retry_pending'

        action = decision.get('action')

        # Post-LLM revalidation (round-2 review). The pre-LLM check above
        # only fences the snapshot up to the LLM await; the LLM call itself
        # is multi-second. During that window another coroutine (rebuttal,
        # parallel promote of a duplicate signal, archive sweep) can flip
        # status to denied / merged / promote_blocked. Without re-checking
        # here, the post-LLM `aadd_fact` / `amerge_into` will write to
        # persona FIRST, and only then will `_arecord_state_change`'s CAS
        # guard refuse the status flip — leaving persona polluted with a
        # fact whose source reflection is no longer eligible. Re-read the
        # status under the lock; bail out cleanly if the gate has closed.
        async with self._get_alock(lanlan_name):
            current_list2 = await self._aload_reflections_full(lanlan_name)
            current2 = self._find_reflection_in_list(current_list2, R['id'])
            if current2 is None or current2.get('status') != 'confirmed':
                logger.info(
                    f"[Promote] {lanlan_name}/{R['id']}: status changed "
                    f"during LLM await (now "
                    f"{current2.get('status') if current2 else 'gone'!r}); "
                    f"discarding LLM decision={action!r} without persona write"
                )
                return 'no_longer_eligible'
            if evidence_score(current2, datetime.now()) < EVIDENCE_PROMOTED_THRESHOLD:
                logger.info(
                    f"[Promote] {lanlan_name}/{R['id']}: evidence_score "
                    f"dropped below threshold during LLM await; "
                    f"discarding LLM decision={action!r}"
                )
                return 'no_longer_eligible'
            # Refresh R from the freshly-read view so any merge-evidence
            # math below sees current values, not the pre-LLM snapshot.
            R = current2

        if action == 'promote_fresh':
            result = await self._persona_manager.aadd_fact(
                lanlan_name, R.get('text', ''),
                entity=target_entity or 'master',
                source='reflection', source_id=R['id'],
            )
            if result == self._persona_manager.FACT_ADDED:
                await self._arecord_state_change(
                    lanlan_name, R['id'], 'confirmed', 'promoted',
                )
                return 'promote_fresh'
            # FACT_REJECTED_CARD: contradicts character_card → reflection
            # is permanently denied (no recovery path; the card is fixed).
            if result == self._persona_manager.FACT_REJECTED_CARD:
                await self._arecord_state_change(
                    lanlan_name, R['id'], 'confirmed', 'denied',
                    reason=f'rejected_by_persona_add:{result}',
                )
                return 'reject_by_persona'
            # FACT_QUEUED_CORRECTION: contradicts an EXISTING non-card fact;
            # PersonaManager has queued an async LLM correction. The user's
            # confirming intent (which got the reflection to confirmed) is
            # NOT denied — the correction queue may resolve in either
            # direction once the LLM weighs in. Keep the reflection in
            # `confirmed` so a future promote cycle can revisit it after
            # backoff (or after the correction queue has resolved); the
            # throttle counter we already bumped this round prevents a
            # tight retry loop.
            logger.info(
                f"[Promote] {lanlan_name}/{R['id']}: aadd_fact returned "
                f"{result} (correction queued); reflection stays confirmed "
                f"for retry after correction resolves"
            )
            return 'queued_correction'

        if action == 'merge_into':
            target_id = decision.get('target_id')
            merged_text = decision.get('merged_text')
            if (not isinstance(target_id, str)
                    or not target_id.startswith('persona.')
                    or not isinstance(merged_text, str)
                    or not merged_text.strip()):
                # LLM returned malformed merge_into — RFC §3.9.7 constrains
                # target_id to persona.* prefix; reject silently as parse fail.
                logger.warning(
                    f"[Promote] {lanlan_name}/{R['id']}: invalid merge_into "
                    f"target_id={target_id!r} merged_text empty/non-str; "
                    f"treating as skip_retry_pending"
                )
                return 'invalid_target'
            # `target_id` is fully-qualified (persona.<entity>.<entry_id>);
            # PersonaManager keys entries by entry_id alone so strip the
            # prefix here. Format: persona.<entity_key>.<entry_id_with_dots_ok>
            #   "persona.master.card_master_abc" -> "card_master_abc"
            #   "persona.master.prom_ref_xyz" -> "prom_ref_xyz"
            parts = target_id.split('.', 2)
            if len(parts) < 3:
                logger.warning(
                    f"[Promote] {lanlan_name}/{R['id']}: target_id "
                    f"{target_id!r} missing entity/entry segments"
                )
                return 'invalid_target'
            target_entry_id = parts[2]

            # Pre-flight existence check (under PersonaManager's read
            # path). Note: the canonical re-lookup AND the conservative
            # max-rule evidence aggregation now both happen INSIDE
            # `amerge_into` under the per-character lock — see CodeRabbit
            # PR #936 round-6 Major #2. We pass the reflection's own
            # evidence values; `amerge_into` does the max() against the
            # locked target entry's current evidence so a concurrent
            # `aapply_signal` (or another merge) cannot be rolled back
            # by a stale snapshot computed up here.
            persona_view2 = await self._persona_manager.aget_persona(lanlan_name)
            target_entity_key, target_entry = (
                self._persona_manager._find_entry_with_section(
                    persona_view2, target_entry_id,
                )
            )
            if target_entry is None:
                logger.warning(
                    f"[Promote] {lanlan_name}/{R['id']}: merge target "
                    f"{target_entry_id} not found in persona; skip"
                )
                return 'invalid_target'

            merge_outcome = await self._persona_manager.amerge_into(
                lanlan_name, target_entry_id, merged_text,
                reflection_evidence={
                    'reinforcement': float(R.get('reinforcement', 0.0) or 0.0),
                    'disputation': float(R.get('disputation', 0.0) or 0.0),
                },
                source_reflection_id=R['id'],
                merged_from_ids=[R['id']],
            )
            if merge_outcome == 'not_found':
                logger.warning(
                    f"[Promote] {lanlan_name}/{R['id']}: amerge_into reported "
                    f"not_found mid-flight; treating as invalid_target"
                )
                return 'invalid_target'
            # 'merged' or 'noop' → both leave the persona in the desired
            # post-merge state, so reflection should flip to merged either way.
            await self._arecord_state_change(
                lanlan_name, R['id'], 'confirmed', 'merged',
                absorbed_into=target_entry_id,
            )
            # Surface tracking: treat merge as a confirm-equivalent terminal
            await self._abatch_mark_surfaced_handled(
                lanlan_name, [R['id']], 'confirmed',
            )
            return 'merge_into'

        if action == 'reject':
            await self._arecord_state_change(
                lanlan_name, R['id'], 'confirmed', 'denied',
                reason='llm_merge_rejected',
                reject_explanation=decision.get('reason'),
            )
            return 'reject'

        # Unknown action — treat as parse failure per §3.9.4 (do NOT
        # downgrade to promote_fresh). Throttle counter already bumped.
        logger.warning(
            f"[Promote] {lanlan_name}/{R['id']}: LLM returned unknown action "
            f"{action!r}; skip_retry_pending"
        )
        return 'skip_retry_pending'

    # 允许从这些 feedback 状态转换到新状态（用于 promoted 覆盖 confirmed/auto_confirmed）
    _UPGRADABLE_FEEDBACK = {None, 'confirmed', 'auto_confirmed'}

    def _apply_batch_mark(
        self, surfaced: list[dict], reflection_ids: list[str], feedback: str,
    ) -> bool:
        return apply_batch_mark(
            surfaced,
            reflection_ids,
            feedback,
            upgradable_feedback=self._UPGRADABLE_FEEDBACK,
            now=datetime.now(),
        )

    def _batch_mark_surfaced_handled(
        self, lanlan_name: str, reflection_ids: list[str], feedback: str,
    ) -> None:
        """Mark multiple surfaced records as handled in a single I/O round-trip.

        Allows transitions from None/confirmed/auto_confirmed to the new feedback value,
        so that promoted can overwrite confirmed/auto_confirmed.
        """
        if not reflection_ids:
            return
        surfaced = self.load_surfaced(lanlan_name)
        if self._apply_batch_mark(surfaced, reflection_ids, feedback):
            self.save_surfaced(lanlan_name, surfaced)

    async def _abatch_mark_surfaced_handled(
        self, lanlan_name: str, reflection_ids: list[str], feedback: str,
    ) -> None:
        if not reflection_ids:
            return
        surfaced = await self.aload_surfaced(lanlan_name)
        if self._apply_batch_mark(surfaced, reflection_ids, feedback):
            await self.asave_surfaced(lanlan_name, surfaced)
