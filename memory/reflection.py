# -*- coding: utf-8 -*-
"""
ReflectionEngine — Tier 2 of the three-tier memory hierarchy.

Synthesizes multiple Tier-1 facts into higher-level reflections (insights).
Reflections start as "pending" and require feedback confirmation before
being promoted to persona (Tier 3).

Cognitive flow:
  Facts(passive) → Reflection(active thinking) → Persona(confirmed & solidified)

Trigger: called during proactive chat (主动搭话), NOT during every conversation.
This allows reflection to double as a "callback" mechanism where the AI naturally
mentions its observations and gauges the user's response.

Auto-promotion: pending reflections that remain 3 days without denial are
automatically promoted to persona.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from config import (
    EVIDENCE_CONFIRMED_THRESHOLD,
    SETTING_PROPOSER_MODEL,
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


def _reflection_id_from_facts(source_fact_ids: list[str]) -> str:
    """根据 source fact ids 生成确定性 reflection id（P1 幂等性核心）。

    同一批 facts 的重复合成产生相同 id，save_reflections + mark_absorbed
    这对"半原子"操作在 kill 后重启重跑时可基于 id 去重——消灭致命点 3。

    取 sha256 前 16 字符（64 bit）。单角色 reflection 规模远低于生日碰撞阈值。
    """
    h = hashlib.sha256()
    for fid in sorted(source_fact_ids):
        h.update(fid.encode('utf-8'))
        h.update(b'\x00')  # 分隔符防 "ab" + "c" 与 "a" + "bc" 冲突
    return f"ref_{h.hexdigest()[:16]}"
# memory-evidence-rfc §3.9.1：time-based auto-promotion 删除。
# pending → confirmed / confirmed → promoted 改由 evidence_score 穿阈值
# 触发（§3.1.4）。本 PR (PR-1) 只实现 pending → confirmed 的 score 驱动；
# confirmed → promoted 的 merge-on-promote 路径在 PR-3。
# Cooldown between proactive chat candidacy
REFLECTION_COOLDOWN_MINUTES = 30
# promoted/denied reflections older than this are moved to archive
_REFLECTION_ARCHIVE_DAYS = 30


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

    def _reflections_archive_path(self, name: str) -> str:
        from memory import ensure_character_dir
        return os.path.join(ensure_character_dir(self._config_manager.memory_dir, name), 'reflections_archive.json')

    def _surfaced_path(self, name: str) -> str:
        from memory import ensure_character_dir
        return os.path.join(ensure_character_dir(self._config_manager.memory_dir, name), 'surfaced.json')

    # ── persistence ──────────────────────────────────────────────────

    @staticmethod
    def _normalize_reflection(entry: dict) -> dict:
        """Fill evidence/archive/promote 节流等新字段的默认值 in-place.

        Schema extension from memory-evidence-rfc §3.2.2. Defaults do NOT
        back-fill to "migrated" seed values — that's the migration-seed path
        (§5.2), which goes through aapply_signal so it's also event-sourced.
        Here we only guarantee the fields exist so downstream code
        (`evidence_score`, `derive_status` …) doesn't KeyError on legacy data.

        Also adds the `recent_mentions` / `suppress` mention抑制 fields so
        confirmed reflections can share persona's 5h-window rate-limit
        machinery (AI 自我克制，不在 5h 内反复提同一条)。
        """
        defaults = {
            # Evidence counters
            'reinforcement': 0.0,
            'disputation': 0.0,
            'rein_last_signal_at': None,
            'disp_last_signal_at': None,
            'sub_zero_days': 0,
            'sub_zero_last_increment_date': None,
            # user_fact reinforces combo counter (RFC §3.1.8)
            'user_fact_reinforce_count': 0,
            # Merge / archive 溯源
            'absorbed_into': None,
            # Promote 节流
            'last_promote_attempt_at': None,
            'promote_attempt_count': 0,
            'promote_blocked_reason': None,
            # AI-mention rate-limit，和 persona 同机制
            'recent_mentions': [],
            'suppress': False,
            'suppressed_at': None,
        }
        for k, v in defaults.items():
            entry.setdefault(k, v)
        return entry

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
        """Pure logic: compute (merged_main, to_archive, keep_in_main).

        RFC §3.11.3: `merged` and `promote_blocked` terminals stay in the
        main file indefinitely (merged carries `absorbed_into` for trace
        chains; promote_blocked is dead-letter awaiting manual / new-signal
        reset). Only `promoted` and `denied` are candidates for the
        `_REFLECTION_ARCHIVE_DAYS` age-based archival split.

        CodeRabbit PR #929 fix: earlier this function only preserved
        `promoted|denied` from `all_on_disk`, so when `_aauto_promote_stale_locked`
        filters its active set through REFLECTION_TERMINAL_STATUSES (which
        includes `merged`), any merged entry on disk would silently vanish
        from the main file on save.
        """
        active_ids = {r['id'] for r in reflections if 'id' in r}
        cutoff = datetime.now() - timedelta(days=_REFLECTION_ARCHIVE_DAYS)
        keep_in_main: list[dict] = []
        to_archive: list[dict] = []
        for r in all_on_disk:
            if r.get('id') in active_ids:
                continue
            status = r.get('status')
            if status in ('promoted', 'denied'):
                ts_key = (r.get('promoted_at') or r.get('denied_at')
                          or r.get('created_at', ''))
                try:
                    if datetime.fromisoformat(ts_key) < cutoff:
                        to_archive.append(r)
                        continue
                except (ValueError, TypeError):
                    # 时间戳缺失/格式异常：不归档，落回 main 保守保留
                    pass
                keep_in_main.append(r)
            elif status in ('merged', 'promote_blocked'):
                # Non-archivable terminal states — must survive save cycles
                keep_in_main.append(r)
            # `archived` should never appear on disk already (that's a
            # post-archive state for the shard file) — drop silently if
            # it sneaks in here.
        merged = reflections + keep_in_main
        return merged, to_archive, keep_in_main

    def save_reflections(self, name: str, reflections: list[dict]) -> None:
        """Save reflections, merging with archived entries on disk.

        promoted/denied 超过 _REFLECTION_ARCHIVE_DAYS 的条目自动移入归档文件。
        """
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
            archive_path = self._reflections_archive_path(name)
            existing: list[dict] = []
            if os.path.exists(archive_path):
                try:
                    with open(archive_path, encoding='utf-8') as f:
                        data = json.load(f)
                    if isinstance(data, list):
                        existing = data
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning(f"[Reflection] {name}: 读取归档文件失败，跳过本次归档: {e}")
                    merged = merged + to_archive
                    to_archive = []
            if to_archive:
                existing.extend(to_archive)
                atomic_write_json(archive_path, existing, indent=2, ensure_ascii=False)
                logger.info(f"[Reflection] {name}: 归档 {len(to_archive)} 条旧 reflections")

        atomic_write_json(path, merged, indent=2, ensure_ascii=False)

    async def asave_reflections(self, name: str, reflections: list[dict]) -> None:
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
            archive_path = self._reflections_archive_path(name)
            existing: list[dict] = []
            if await asyncio.to_thread(os.path.exists, archive_path):
                try:
                    data = await read_json_async(archive_path)
                    if isinstance(data, list):
                        existing = data
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning(f"[Reflection] {name}: 读取归档文件失败，跳过本次归档: {e}")
                    merged = merged + to_archive
                    to_archive = []
            if to_archive:
                existing.extend(to_archive)
                await atomic_write_json_async(archive_path, existing, indent=2, ensure_ascii=False)
                logger.info(f"[Reflection] {name}: 归档 {len(to_archive)} 条旧 reflections")

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

        幂等性（P1 修复致命点 3）：
          1. reflection id 由 source_fact_ids 决定（_reflection_id_from_facts）。
          2. LLM 调用前先查：同一批 unabsorbed facts 对应的 id 若已在
             reflections.json 中存在 → 跳过 LLM、仅补跑 mark_absorbed（幂等）。
          3. save_reflections 亦按 id dedup，防 concurrent synth 双写。
          4. 始终在末尾 amark_absorbed，确保 save 成功但 mark 失败后的
             重启补跑能真正把 facts 的 absorbed 置为 True。

        并发（P2.a.2）：整个方法在角色级 asyncio.Lock 下串行，避免与
        aauto_promote_stale / aconfirm_promotion 竞写 reflections.json。
        """
        async with self._get_alock(lanlan_name):
            return await self._synthesize_reflections_locked(lanlan_name)

    async def _synthesize_reflections_locked(self, lanlan_name: str) -> list[dict]:
        """synthesize_reflections 的内部实现。调用方必须已持有
        self._get_alock(lanlan_name)。"""
        from config.prompts_memory import get_reflection_prompt
        from utils.language_utils import get_global_language
        from utils.llm_client import create_chat_llm

        unabsorbed = await self._fact_store.aget_unabsorbed_facts(lanlan_name)
        if len(unabsorbed) < MIN_FACTS_FOR_REFLECTION:
            return []

        # 排序一次：on-disk 字段与 _reflection_id_from_facts 内部 sorted 对齐，
        # 消除 "hash 用 sorted，存盘不 sorted" 的隐式非对称
        source_fact_ids = sorted(f['id'] for f in unabsorbed)
        rid = _reflection_id_from_facts(source_fact_ids)

        # 幂等 short-circuit：同一批 facts 的 reflection 已持久化 →
        # 不重复调 LLM，仅补跑 mark_absorbed（致命点 3 的重启补救路径）
        existing_reflections = await self.aload_reflections(lanlan_name)
        existing = next((r for r in existing_reflections if r.get('id') == rid), None)
        if existing is not None:
            await self._fact_store.amark_absorbed(lanlan_name, source_fact_ids)
            logger.info(
                f"[Reflection] {lanlan_name}: 检测到同批 facts 已合成过 reflection "
                f"{rid}，跳过 LLM，补跑 mark_absorbed"
            )
            return []

        _, _, _, _, name_mapping, _, _, _, _ = await self._config_manager.aget_character_data()
        master_name = name_mapping.get('human', '主人')

        facts_text = "\n".join(f"- {f['text']} (importance: {f.get('importance', 5)})" for f in unabsorbed)
        reflection_prompt = get_reflection_prompt(get_global_language())
        prompt = reflection_prompt.replace('{FACTS}', facts_text)
        prompt = prompt.replace('{LANLAN_NAME}', lanlan_name)
        prompt = prompt.replace('{MASTER_NAME}', master_name)

        try:
            set_call_type("memory_reflection")
            api_config = self._config_manager.get_model_api_config('summary')
            llm = create_chat_llm(
                api_config.get('model', SETTING_PROPOSER_MODEL),
                api_config['base_url'], api_config['api_key'],
                temperature=0.5,
            )
            try:
                resp = await llm.ainvoke(prompt)
            finally:
                await llm.aclose()
            raw = resp.content.strip()
            if raw.startswith("```"):
                raw = raw.replace("```json", "").replace("```", "").strip()
            result = robust_json_loads(raw)
            if not isinstance(result, dict):
                logger.warning(f"[Reflection] LLM 返回非 dict: {type(result)}")
                return []
            reflection_text = result.get('reflection', '')
            if not isinstance(reflection_text, str):
                logger.warning(f"[Reflection] reflection 字段非 str: {type(reflection_text)}")
                return []
            reflection_text = reflection_text.strip()
            reflection_entity = result.get('entity', 'relationship')
            if reflection_entity not in ('master', 'neko', 'relationship'):
                reflection_entity = 'relationship'
        except Exception as e:
            logger.warning(f"[Reflection] 合成失败: {e}")
            return []

        if not reflection_text:
            return []

        # Create pending reflection — id 已在函数开头由 source_fact_ids 决定
        now = datetime.now()
        now_iso = now.isoformat()

        # Importance-based initial rein seed：让"关键节点"型 reflection 起步
        # 就带一点正分，不必等多轮 user confirms 才穿越 CONFIRMED 阈值。
        # 不走 aapply_signal（synthesis 本身不经 event log），直接写进初始
        # 字典——synth 不是 event-sourced，这些初始值就是 ground truth。
        max_importance = max(
            (int(f.get('importance', 5) or 5) for f in unabsorbed),
            default=5,
        )
        initial_rein = initial_reinforcement_from_importance(max_importance)

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
        })

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

        # 无条件 mark_absorbed：幂等，且覆盖 save 成功后但在此崩溃的补跑情况
        await self._fact_store.amark_absorbed(lanlan_name, source_fact_ids)

        if not created:
            # 并发分支已落盘对方的对象；返回内存副本会让调用方拿到一个
            # 未持久化、可能与磁盘版文本不同的"幽灵反思"，违反"返回值
            # = 本调用真正新建的反思"语义。
            return []
        logger.info(f"[Reflection] {lanlan_name}: 合成了新反思 {rid}: {reflection_text[:50]}...")
        return [reflection]

    # alias for backward compat (system_router calls .reflect())
    async def reflect(self, lanlan_name: str) -> dict | None:
        """Alias for synthesize_reflections. Returns first reflection or None."""
        results = await self.synthesize_reflections(lanlan_name)
        return results[0] if results else None

    # ── evidence signals (RFC §3.4, §3.8.4) ─────────────────────────

    @staticmethod
    def _find_reflection_in_list(reflections: list[dict], rid: str) -> dict | None:
        for r in reflections:
            if isinstance(r, dict) and r.get('id') == rid:
                return r
        return None

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

        record_and_save 合约（RFC §3.3.3）：
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
        try:
            return datetime.fromisoformat(ts_str) >= cutoff
        except (ValueError, TypeError):
            return False

    @classmethod
    def _apply_record_reflection_mentions(
        cls, reflections: list[dict], response_text: str,
    ) -> bool:
        """AI response 提到任一 **confirmed** reflection 的文本 → recent_mentions 累加。
        Pending reflection 本意就是"AI 主动试探"，抑制会反向破坏机制——
        所以只扫 confirmed。语义和 persona._apply_record_mentions 对齐。
        """
        now = datetime.now()
        now_str = now.isoformat()
        cutoff = now - timedelta(hours=SUPPRESS_WINDOW_HOURS)
        changed = False
        for r in reflections:
            if not isinstance(r, dict):
                continue
            if r.get('status') != 'confirmed':
                continue
            if not _is_mentioned(r.get('text', ''), response_text):
                continue
            mentions = r.get('recent_mentions', [])
            mentions.append(now_str)
            mentions = [t for t in mentions if cls._in_window(t, cutoff)]
            r['recent_mentions'] = mentions
            if not r.get('suppress') and len(mentions) > SUPPRESS_MENTION_LIMIT:
                r['suppress'] = True
                r['suppressed_at'] = now_str
            changed = True
        return changed

    @classmethod
    def _apply_update_reflection_suppressions(
        cls, reflections: list[dict],
    ) -> bool:
        now = datetime.now()
        cutoff = now - timedelta(hours=SUPPRESS_WINDOW_HOURS)
        changed = False
        for r in reflections:
            if not isinstance(r, dict):
                continue
            mentions = r.get('recent_mentions', [])
            cleaned = [t for t in mentions if cls._in_window(t, cutoff)]
            if len(cleaned) != len(mentions):
                r['recent_mentions'] = cleaned
                changed = True
            if r.get('suppress'):
                suppressed_str = r.get('suppressed_at')
                if suppressed_str:
                    try:
                        hours_since = (
                            now - datetime.fromisoformat(suppressed_str)
                        ).total_seconds() / 3600
                        if hours_since >= SUPPRESS_COOLDOWN_HOURS:
                            r['suppress'] = False
                            r['suppressed_at'] = None
                            r['recent_mentions'] = []
                            changed = True
                    except (ValueError, TypeError) as e:
                        # 坏时戳（手编 / 迁移瑕疵）：suppress 冷却本轮跳过、
                        # 下轮再评估；not raising keeps loop non-fatal.
                        logger.debug(
                            f"[Reflection] suppressed_at 解析失败 ({suppressed_str!r}): {e}"
                        )
        return changed

    async def arecord_mentions(self, lanlan_name: str, response_text: str) -> None:
        """AI 发完一轮回复后扫 confirmed reflection，按 5h 窗口累加 mention。
        连续提超过 SUPPRESS_MENTION_LIMIT (=2) 次 → 打上 suppress=True。
        """
        if not response_text:
            return
        async with self._get_alock(lanlan_name):
            reflections = await self._aload_reflections_full(lanlan_name)
            if self._apply_record_reflection_mentions(reflections, response_text):
                active = [
                    r for r in reflections
                    if r.get('status') not in REFLECTION_TERMINAL_STATUSES
                ]
                await self.asave_reflections(lanlan_name, active)

    async def aupdate_suppressions(self, lanlan_name: str) -> None:
        """Render 前刷新 suppress 状态：冷却期过 → 解除；清理窗口外的 recent_mentions。"""
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

        score <= 0：用户否认多次或刚好抵消，既不应进 render "比较确定的印象"
        段（语义漂移），也会随背景循环 tick 归档计数器（§3.5）。
        suppress=True：AI 刚在 5h 窗口内提过太多次这条，由 persona 的同款
        机制静默（§2.6 正交）。
        """
        if now is None:
            now = datetime.now()
        out = []
        for r in reflections:
            if r.get('status') != 'confirmed':
                continue
            if r.get('suppress'):
                continue
            if evidence_score(r, now) <= 0:
                continue
            out.append(r)
        return out

    def get_confirmed_reflections(self, lanlan_name: str) -> list[dict]:
        """Get all confirmed (soft persona) reflections that are still
        active — status='confirmed' AND score > 0 AND not mention-suppressed."""
        return self._filter_active_confirmed(self.load_reflections(lanlan_name))

    async def aget_confirmed_reflections(self, lanlan_name: str) -> list[dict]:
        return self._filter_active_confirmed(
            await self.aload_reflections(lanlan_name),
        )

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
        """
        if not pending:
            return []
        now = datetime.now()
        eligible = []
        for r in pending:
            next_eligible = r.get('next_eligible_at')
            if next_eligible:
                try:
                    if datetime.fromisoformat(next_eligible) > now:
                        continue
                except (ValueError, TypeError):
                    pass
            if evidence_score(r, now) < 0:
                continue
            eligible.append(r)
        return eligible[:2]

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
        surfaced.json 写入，避免与 synth / promote 竞写。"""
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

        P2.a.2: 本方法会写回 surfaced.json（line 572），因此必须在角色锁下
        与 arecord_surfaced / aconfirm_promotion / areject_promotion 串行。
        """
        async with self._get_alock(lanlan_name):
            return await self._check_feedback_locked(lanlan_name, user_messages)

    async def _check_feedback_locked(self, lanlan_name: str, user_messages: list[str]) -> list[dict] | None:
        from config.prompts_memory import get_reflection_feedback_prompt
        from utils.language_utils import get_global_language
        from utils.llm_client import create_chat_llm

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
            llm = create_chat_llm(
                api_config.get('model', SETTING_PROPOSER_MODEL),
                api_config['base_url'], api_config['api_key'],
                temperature=0.1,
            )
            try:
                resp = await llm.ainvoke(prompt)
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
        from config.prompts_memory import get_reflection_feedback_prompt
        from utils.language_utils import get_global_language
        from utils.llm_client import create_chat_llm

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
            llm = create_chat_llm(
                api_config.get('model', SETTING_PROPOSER_MODEL),
                api_config['base_url'], api_config['api_key'],
                temperature=0.1,
            )
            try:
                resp = await llm.ainvoke(prompt)
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
        now_str = datetime.now().isoformat()
        for r in reflections:
            if r.get('id') == reflection_id:
                r['status'] = status
                r[f'{status}_at'] = now_str
                return r.get('text', '')
        return None

    def confirm_promotion(self, lanlan_name: str, reflection_id: str) -> None:
        """Mark reflection as confirmed (soft persona). Does NOT write to persona yet.

        Confirmed reflections exist independently for AUTO_PROMOTE_DAYS days,
        during which they can still be rebutted. After that, auto_promote_stale()
        upgrades them to real persona entries.
        """
        reflections = self.load_reflections(lanlan_name)
        text = self._apply_promotion_status(reflections, reflection_id, 'confirmed')
        if text is not None:
            logger.info(f"[Reflection] {lanlan_name}: 反思已确认(软persona): {text[:50]}...")
        self.save_reflections(lanlan_name, reflections)
        self._mark_surfaced_handled(lanlan_name, reflection_id, 'confirmed')

    async def aconfirm_promotion(self, lanlan_name: str, reflection_id: str) -> None:
        async with self._get_alock(lanlan_name):
            reflections = await self.aload_reflections(lanlan_name)
            text = self._apply_promotion_status(reflections, reflection_id, 'confirmed')
            if text is not None:
                logger.info(f"[Reflection] {lanlan_name}: 反思已确认(软persona): {text[:50]}...")
            await self.asave_reflections(lanlan_name, reflections)
            await self._amark_surfaced_handled(lanlan_name, reflection_id, 'confirmed')

    def reject_promotion(self, lanlan_name: str, reflection_id: str) -> None:
        """Mark a reflection as denied — won't be promoted."""
        reflections = self.load_reflections(lanlan_name)
        text = self._apply_promotion_status(reflections, reflection_id, 'denied')
        if text is not None:
            logger.info(f"[Reflection] {lanlan_name}: 反思被否定: {text[:50]}...")
        self.save_reflections(lanlan_name, reflections)
        self._mark_surfaced_handled(lanlan_name, reflection_id, 'denied')

    async def areject_promotion(self, lanlan_name: str, reflection_id: str) -> None:
        async with self._get_alock(lanlan_name):
            reflections = await self.aload_reflections(lanlan_name)
            text = self._apply_promotion_status(reflections, reflection_id, 'denied')
            if text is not None:
                logger.info(f"[Reflection] {lanlan_name}: 反思被否定: {text[:50]}...")
            await self.asave_reflections(lanlan_name, reflections)
            await self._amark_surfaced_handled(lanlan_name, reflection_id, 'denied')

    @staticmethod
    def _apply_mark_surfaced_handled(
        surfaced: list[dict], reflection_id: str, feedback: str,
    ) -> bool:
        now_str = datetime.now().isoformat()
        changed = False
        for s in surfaced:
            if s.get('reflection_id') == reflection_id and s.get('feedback') is None:
                s['feedback'] = feedback
                s['feedback_at'] = now_str
                changed = True
        return changed

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

        - 删除时间跳级分支（`AUTO_CONFIRM_DAYS` / `AUTO_PROMOTE_DAYS` 已删）
        - 仅做 pending → confirmed：`evidence_score(r, now) >= EVIDENCE_CONFIRMED_THRESHOLD`
        - confirmed → promoted 由 PR-3 的 `_apromote_with_merge` 接管；
          本函数在 PR-1 不承担 promotion 职责
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
        """P2.a.2: 角色级 asyncio.Lock 串行化。score-driven pending →
        confirmed（§3.9.1）。confirmed → promoted 的 merge-on-promote 路径
        在 PR-3 补（本 PR 不承担 promotion）。"""
        async with self._get_alock(lanlan_name):
            return await self._aauto_promote_stale_locked(lanlan_name)

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

    # 允许从这些 feedback 状态转换到新状态（用于 promoted 覆盖 confirmed/auto_confirmed）
    _UPGRADABLE_FEEDBACK = {None, 'confirmed', 'auto_confirmed'}

    def _apply_batch_mark(
        self, surfaced: list[dict], reflection_ids: list[str], feedback: str,
    ) -> bool:
        id_set = set(reflection_ids)
        changed = False
        now = datetime.now().isoformat()
        for s in surfaced:
            if s.get('reflection_id') in id_set and s.get('feedback') in self._UPGRADABLE_FEEDBACK:
                s['feedback'] = feedback
                s['feedback_at'] = now
                changed = True
        return changed

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
