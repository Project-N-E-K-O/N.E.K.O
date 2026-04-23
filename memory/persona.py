# -*- coding: utf-8 -*-
"""
PersonaManager — Tier 3 of the three-tier memory hierarchy.

Manages long-term persona data with dynamic entity support.
Core entities: master (human), neko (AI character), relationship (dynamics).
Storage is entity-agnostic: any entity key can be added at runtime
(e.g. QQ group IDs, other users, other nekos).

Key features:
- Dynamic entity sections: each entity stores a list of facts
- Pending reflections injected with "(还不太确定)" annotation
- Suppress mechanism: 5h window, >2 mentions → suppress (completely hidden from
  all rendering sections; suppress has highest priority)
- Contradiction detection → queued for batch correction via LLM
- Auto-migration from legacy settings files and v1 entity names
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import threading
from collections import defaultdict
from datetime import datetime, timedelta

from config import (
    PERSONA_RENDER_TOKEN_BUDGET,
    REFLECTION_RENDER_TOKEN_BUDGET,
    SETTING_PROPOSER_MODEL,
)
from memory.evidence import evidence_score
from utils.cloudsave_runtime import assert_cloudsave_writable
from utils.config_manager import get_config_manager
from utils.file_utils import (
    atomic_write_json,
    atomic_write_json_async,
    read_json_async,
    robust_json_loads,
)
from utils.logger_config import get_module_logger
from utils.tokenize import acount_tokens, count_tokens, tokenizer_identity

logger = get_module_logger(__name__, "Memory")

# ── 疲劳常量 ──────────────────────────────────────────────────────
# 5小时内提及2次以上 → suppress，5小时后刷新
SUPPRESS_MENTION_LIMIT = 2           # 窗口内提及次数上限
SUPPRESS_WINDOW_HOURS = 5            # 统计窗口（小时）
SUPPRESS_COOLDOWN_HOURS = 5          # suppress 后冷却（=窗口，5小时后刷新）

# ── 矛盾检测阈值 ─────────────────────────────────────────────────
SIMILARITY_THRESHOLD = 0.6           # 余弦相似度(如有embedding)或关键词重叠

# ── 自动晋升冷却 ─────────────────────────────────────────────────
AUTO_CONFIRM_DAYS = 3                # pending reflection N 天无反对 → 自动晋升

# Split on any CJK/Latin punctuation, symbols, whitespace
_SPLIT_RE = re.compile(r'[，。、！？；：\u201c\u201d\u2018\u2019（）()\[\]{}<>《》【】\s,.!?;:\-\u2014\u2026\xb7\u3000]+')

def _extract_keywords(text: str) -> set[str]:
    """从文本提取关键词/n-gram，支持 CJK 和拉丁文。

    - 拉丁文按空格分词，保留 len>=2 的 token
    - CJK 文本生成 2-gram 和 3-gram 滑动窗口
    """
    segments = _SPLIT_RE.split(text)
    keywords: set[str] = set()

    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        cjk_count = sum(
            1 for ch in seg
            if '\u4e00' <= ch <= '\u9fff' or '\u3040' <= ch <= '\u30ff' or '\uac00' <= ch <= '\ud7af'
        )
        if cjk_count > len(seg) / 2:
            for n in (2, 3):
                for i in range(len(seg) - n + 1):
                    keywords.add(seg[i:i + n])
        else:
            if len(seg) >= 2:
                keywords.add(seg)

    return keywords


def _is_mentioned(fact_text: str, response_text: str) -> bool:
    """判断 response 中是否"提及"了某条 persona 事实。"""
    if not fact_text or not response_text:
        return False
    keywords = _extract_keywords(fact_text)
    if not keywords:
        return False
    return any(kw in response_text for kw in keywords)


class PersonaManager:
    """Manages per-character persona files with dynamic entity sections.

    Core entities: 'master', 'neko', 'relationship'.
    Storage is entity-agnostic — any string key is accepted as an entity.
    Each entity section is ``{entity: {'facts': [...]}}``.
    """

    def __init__(self, event_log=None):
        self._config_manager = get_config_manager()
        self._personas: dict[str, dict] = {}
        # Per-character asyncio.Lock (P2.a.2). Protects load→mutate→save
        # sequences in add_fact / resolve_corrections / record_mentions /
        # queue_correction. Lazily created to avoid event-loop binding at
        # module-import time. threading.Lock guards the dict itself
        # (pure-Python block, no await inside).
        self._alocks: dict[str, asyncio.Lock] = {}
        self._alocks_guard = threading.Lock()
        # memory-evidence-rfc §3.3.3：evidence 写路径必须走 record_and_save，
        # 保证 event↔view 合约。event_log 注入；None 时 aapply_signal 不可用。
        self._event_log = event_log

    def _get_alock(self, name: str) -> asyncio.Lock:
        """Per-character asyncio.Lock; lazy + DCL-guarded.

        See reflection.py:_get_alock for full rationale. Thread-safety
        scope: event-loop-only caller. asyncio.Lock binds to the running
        loop at first acquire on CPython 3.10+.
        """
        if name not in self._alocks:
            with self._alocks_guard:
                if name not in self._alocks:
                    self._alocks[name] = asyncio.Lock()
        return self._alocks[name]

    # ── file paths ───────────────────────────────────────────────────

    def _persona_path(self, name: str) -> str:
        from memory import ensure_character_dir
        return os.path.join(ensure_character_dir(self._config_manager.memory_dir, name), 'persona.json')

    def _corrections_path(self, name: str) -> str:
        from memory import ensure_character_dir
        return os.path.join(ensure_character_dir(self._config_manager.memory_dir, name), 'persona_corrections.json')

    def _persona_archive_dir(self, name: str) -> str:
        """Sharded archive directory for persona entries (RFC §3.5.4).

        New in PR-2 — persona had no archival before this RFC, so there
        is no legacy flat file to migrate (RFC §3.5.5 末段).
        """
        from memory import ensure_character_dir
        return os.path.join(
            ensure_character_dir(self._config_manager.memory_dir, name),
            'persona_archive',
        )

    def _sync_save_persona_view(self, n: str, view: dict) -> None:
        """`_sync_save` helper for `arecord_and_save` paths on persona.json.

        Context (CodeRabbit PR #936 round-5 Major #1): all event-sourced
        mutation paths in this file follow the record_and_save contract,
        meaning `_sync_mutate_view` mutates `self._personas[n]` IN PLACE
        (the cached dict and the `view` arg are the same object — see
        `_aensure_persona_locked`). If the subsequent `atomic_write_json`
        fails (disk full, cloudsave read-only kicked in mid-call, …), the
        in-memory cache has already taken the mutation while the disk
        still sits at the pre-event state. Subsequent in-process reads
        would serve polluted state.

        Fix: on ANY save-step failure (cloudsave gate raise OR
        atomic_write raise), evict the polluted entry from
        `self._personas`. Next access goes through
        `_aensure_persona_locked` which re-reads from disk — the
        pre-event view. The event is already in the log (append runs
        before mutate, see event_log.record_and_save), so reconciler
        replay on next boot restores the mutation correctly. The
        exception propagates so the caller sees the failure.

        Why both calls share one try (CodeRabbit PR #936 round-6
        Major #1): `_sync_mutate_view` has already mutated the cached
        entry IN PLACE before this helper runs. If
        `assert_cloudsave_writable` raises (cloudsave flipped to
        read-only mid-flight) AFTER mutate but BEFORE atomic_write,
        the polluted cache lingers exactly the same way an
        atomic_write failure would. Wrapping both calls under the
        same evict-on-raise block keeps the "any save-step failure
        ⇒ cache evicted" invariant uniform — no corner where one
        failure mode leaves polluted memory state.

        The cache assignment AFTER atomic_write succeeds is a no-op in
        the common case (view IS self._personas[n]) but kept explicit
        for the rare initialization-race where a concurrent reload may
        have replaced the entry.
        """
        try:
            assert_cloudsave_writable(
                self._config_manager,
                operation="save",
                target=f"memory/{n}/persona.json",
            )
            atomic_write_json(
                self._persona_path(n), view, indent=2, ensure_ascii=False,
            )
        except Exception:
            # Evict the polluted cache; next _aensure_persona_locked
            # reloads from disk (pre-event state). Reconciler replays
            # the already-appended event on next boot.
            self._personas.pop(n, None)
            raise
        self._personas[n] = view

    # ── CRUD ─────────────────────────────────────────────────────────

    def _empty_persona(self) -> dict:
        return {}

    def _is_persona_empty(self, persona: dict) -> bool:
        """Check if all fact/dynamics lists are empty."""
        for section in persona.values():
            if isinstance(section, dict):
                for lst in section.values():
                    if isinstance(lst, list) and lst:
                        return False
        return True

    def ensure_persona(self, name: str) -> dict:
        """Load or create persona. Auto-migrate from legacy settings if needed.

        每次调用时自动与 characters.json 同步 character_card 条目。
        """
        if name in self._personas:
            # 每次读取时同步 character card
            if self._sync_character_card(name, self._personas[name]):
                self.save_persona(name, self._personas[name])
            return self._personas[name]

        path = self._persona_path(name)
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    migrated = self._migrate_v1_entity_keys(data)
                    if self._is_persona_empty(data):
                        self._migrate_from_settings(name, data)
                        migrated = True
                    # 同步 character card
                    if self._sync_character_card(name, data):
                        migrated = True
                    if migrated:
                        self.save_persona(name, data)
                    self._personas[name] = data
                    return data
                logger.warning(f"[Persona] {name}: persona 文件不是 dict，忽略")
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"[Persona] 加载失败: {e}")

        # Auto-migrate from legacy settings
        persona = self._empty_persona()
        self._migrate_from_settings(name, persona)
        self._sync_character_card(name, persona)
        self._personas[name] = persona
        self.save_persona(name, persona)
        return persona

    async def aensure_persona(self, name: str) -> dict:
        """Thread-safe wrapper. 首次创建 / character card 变更这两条分支会
        `asave_persona()` 写盘，必须在 per-character 锁下进行，否则会与
        `aadd_fact` / `arecord_mentions` / `aupdate_suppressions` /
        `_resolve_corrections_locked` 等持锁写盘路径竞争，导致刚落盘的新事实
        被锁外的 ensure/sync-card 分支覆盖。

        内部已持 `_get_alock(name)` 的调用点（如 aadd_fact 内）必须改用
        `_aensure_persona_locked` 以避免 asyncio.Lock 不可重入死锁。"""
        async with self._get_alock(name):
            return await self._aensure_persona_locked(name)

    async def _aensure_persona_locked(self, name: str) -> dict:
        """Inner body. Caller MUST hold self._get_alock(name)."""
        if name in self._personas:
            if await self._async_sync_character_card(name, self._personas[name]):
                await self.asave_persona(name, self._personas[name])
            return self._personas[name]

        path = self._persona_path(name)
        if await asyncio.to_thread(os.path.exists, path):
            try:
                data = await read_json_async(path)
                if isinstance(data, dict):
                    migrated = self._migrate_v1_entity_keys(data)
                    if self._is_persona_empty(data):
                        await self._async_migrate_from_settings(name, data)
                        migrated = True
                    if await self._async_sync_character_card(name, data):
                        migrated = True
                    if migrated:
                        await self.asave_persona(name, data)
                    self._personas[name] = data
                    return data
                logger.warning(f"[Persona] {name}: persona 文件不是 dict，忽略")
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"[Persona] 加载失败: {e}")

        persona = self._empty_persona()
        await self._async_migrate_from_settings(name, persona)
        await self._async_sync_character_card(name, persona)
        self._personas[name] = persona
        await self.asave_persona(name, persona)
        return persona

    @staticmethod
    def _migrate_v1_entity_keys(persona: dict) -> bool:
        """One-time migration: rename v1 entity keys and unify inner key to 'facts'.

        - 'user'  → 'master'
        - 'ai'    → 'neko'
        - relationship.dynamics → relationship.facts

        Returns True if any migration was performed.
        """
        changed = False

        # Rename top-level keys
        for old_key, new_key in [('user', 'master'), ('ai', 'neko')]:
            if old_key in persona and new_key not in persona:
                persona[new_key] = persona.pop(old_key)
                changed = True

        # Unify 'dynamics' → 'facts' for any section that still uses it
        for section in persona.values():
            if isinstance(section, dict) and 'dynamics' in section:
                section['facts'] = section.pop('dynamics')
                changed = True

        if changed:
            logger.info("[Persona] v1→v2 entity key 迁移完成 (user→master, ai→neko, dynamics→facts)")
        return changed

    @staticmethod
    def _card_entry_id(entity: str, field_name: str) -> str:
        """为 character card 条目生成确定性 ID（基于 entity + field_name 哈希）。"""
        raw = f"{entity}:{field_name}"
        return f"card_{hashlib.sha256(raw.encode()).hexdigest()[:8]}"

    def _resolve_settings_path(self, name: str) -> str | None:
        from memory import ensure_character_dir
        char_dir = ensure_character_dir(self._config_manager.memory_dir, name)
        settings_path = os.path.join(char_dir, 'settings.json')
        if not os.path.exists(settings_path):
            old_path = os.path.join(str(self._config_manager.memory_dir), f'settings_{name}.json')
            if os.path.exists(old_path):
                return old_path
            return None
        return settings_path

    def _apply_settings_migration(
        self, name: str, persona: dict, master_name: str,
        name_mapping: dict, old_settings: dict,
    ) -> int:
        def _is_migratable(val) -> bool:
            if val is None:
                return False
            if isinstance(val, str):
                return bool(val.strip())
            if isinstance(val, (list, dict, set, tuple)):
                return len(val) > 0
            return True

        def _existing_texts_for(facts_list):
            return {e.get('text', '') for e in facts_list if isinstance(e, dict)}

        migrated_count = 0
        for section_key, facts_dict in old_settings.items():
            if not isinstance(facts_dict, dict):
                continue
            if section_key == master_name or section_key == name_mapping.get('human', ''):
                target = persona['master']['facts']
            elif section_key == name:
                target = persona['neko']['facts']
            else:
                target = persona['relationship']['facts']

            seen = _existing_texts_for(target)
            for k, v in facts_dict.items():
                if _is_migratable(v):
                    text = f"{k}: {v}"
                    if text not in seen:
                        entry = self._normalize_entry(text)
                        content_hash = hashlib.sha256(text.encode()).hexdigest()[:8]
                        entry['id'] = f"legacy_{content_hash}"
                        entry['source'] = 'settings'
                        target.append(entry)
                        seen.add(text)
                        migrated_count += 1
        return migrated_count

    def _migrate_from_settings(self, name: str, persona: dict) -> None:
        """One-time migration from legacy settings.json to persona format.

        仅迁移 settings.json（LLM 从对话中提取的设定）。
        角色卡数据（characters.json）的同步统一由 _sync_character_card() 负责，
        此方法不再处理角色卡，避免两处写入导致重复条目。
        """
        _, _, _, _, name_mapping, _, _, _, _ = (
            self._config_manager.get_character_data()
        )
        master_name = name_mapping.get('human', '主人')

        for section_key in ('neko', 'master', 'relationship'):
            persona.setdefault(section_key, {}).setdefault('facts', [])

        settings_path = self._resolve_settings_path(name)
        migrated_count = 0
        if settings_path:
            try:
                with open(settings_path, encoding='utf-8') as f:
                    old_settings = json.load(f)
                if isinstance(old_settings, dict):
                    migrated_count = self._apply_settings_migration(
                        name, persona, master_name, name_mapping, old_settings,
                    )
            except Exception as e:
                logger.warning(f"[Persona] {name}: settings.json 读取失败: {e}")

        if migrated_count:
            logger.info(f"[Persona] {name}: 迁移了 {migrated_count} 条 persona 数据（settings）")

    async def _async_migrate_from_settings(self, name: str, persona: dict) -> None:
        _, _, _, _, name_mapping, _, _, _, _ = (
            await self._config_manager.aget_character_data()
        )
        master_name = name_mapping.get('human', '主人')

        for section_key in ('neko', 'master', 'relationship'):
            persona.setdefault(section_key, {}).setdefault('facts', [])

        settings_path = await asyncio.to_thread(self._resolve_settings_path, name)
        migrated_count = 0
        if settings_path:
            try:
                old_settings = await read_json_async(settings_path)
                if isinstance(old_settings, dict):
                    migrated_count = self._apply_settings_migration(
                        name, persona, master_name, name_mapping, old_settings,
                    )
            except Exception as e:
                logger.warning(f"[Persona] {name}: settings.json 读取失败: {e}")

        if migrated_count:
            logger.info(f"[Persona] {name}: 迁移了 {migrated_count} 条 persona 数据（settings）")

    # ── character card 同步 ───────────────────────────────────────────

    def _apply_character_card_sync(
        self, name: str, persona: dict,
        master_basic_config, lanlan_basic_config,
    ) -> bool:
        from config import CHARACTER_RESERVED_FIELDS
        excluded_fields = set(CHARACTER_RESERVED_FIELDS)
        changed = False

        def _is_syncable(val) -> bool:
            if val is None:
                return False
            if isinstance(val, str):
                return bool(val.strip())
            if isinstance(val, (list, dict, set, tuple)):
                return len(val) > 0
            return True

        def _build_expected(card_data: dict, entity: str) -> list[tuple[str, dict]]:
            """从 card 字段构建期望的 (id, entry) 列表，保持 card 字段顺序。"""
            expected = []
            for k, v in card_data.items():
                if k in excluded_fields or not _is_syncable(v):
                    continue
                if isinstance(v, (dict, set, tuple)):
                    continue
                if isinstance(v, list):
                    v = '、'.join(str(item) for item in v)
                entry_id = self._card_entry_id(entity, k)
                text = f"{k}: {v}"
                expected.append((entry_id, text))
            return expected

        def _sync_entity(entity: str, card_data: dict) -> bool:
            """同步单个 entity section。返回是否有变更。"""
            section = persona.setdefault(entity, {})
            facts = section.setdefault('facts', [])
            expected = _build_expected(card_data, entity)
            expected_ids = {eid for eid, _ in expected}

            # 分离 card 条目和非 card 条目
            existing_card = {}  # id → entry
            other_entries = []
            for entry in facts:
                if isinstance(entry, dict) and entry.get('source') == 'character_card':
                    existing_card[entry.get('id', '')] = entry
                else:
                    other_entries.append(entry)

            # 按 card 顺序构建新的 card 条目列表
            modified = False
            new_card_entries = []
            for eid, text in expected:
                if eid in existing_card:
                    entry = existing_card[eid]
                    if entry.get('text') != text:
                        # 文本变化 → 更新
                        old_text = entry.get('text', '')
                        entry['text'] = text
                        # token_count 缓存是从 text 派生的；这里原地改写
                        # text 必须同步失效缓存，否则渲染路径要等到
                        # fingerprint mismatch 才补算，还会额外浪费一次
                        # sha256（对偶于 amerge_into 的 _sync_mutate_entry）。
                        self._invalidate_token_count_cache(entry)
                        modified = True
                        logger.info(f"[Persona] {name}: card 同步更新 [{entity}] \"{old_text[:30]}\" → \"{text[:30]}\"")
                    new_card_entries.append(entry)
                else:
                    # 新字段 → 创建
                    entry = self._normalize_entry(text)
                    entry['id'] = eid
                    entry['source'] = 'character_card'
                    entry['protected'] = True
                    new_card_entries.append(entry)
                    modified = True
                    logger.info(f"[Persona] {name}: card 同步新增 [{entity}] \"{text[:40]}\"")

            # 检查是否有 card 中已删除的条目
            removed_ids = set(existing_card.keys()) - expected_ids
            if removed_ids:
                modified = True
                for rid in removed_ids:
                    removed_text = existing_card[rid].get('text', '')
                    logger.info(f"[Persona] {name}: card 同步移除 [{entity}] \"{removed_text[:40]}\"")


            if modified:
                # card 条目在前，其他条目在后
                section['facts'] = new_card_entries + other_entries

            return modified

        # 同步 neko entity
        if name in (lanlan_basic_config or {}):
            if _sync_entity('neko', lanlan_basic_config[name]):
                changed = True

        # 同步 master entity
        if master_basic_config and isinstance(master_basic_config, dict):
            if _sync_entity('master', master_basic_config):
                changed = True

        return changed

    def _sync_character_card(self, name: str, persona: dict) -> bool:
        """同步 character card 条目到 persona 头部，保持顺序与 characters.json 一致。

        规则：
        1. 读取当前 characters.json 的 neko/master 字段
        2. 为每个字段生成确定性 ID (card_{entity}_{hash})
        3. 与 persona 中 source=='character_card' 的条目对比
        4. 更新文本变化的、新增缺少的、删除 card 中已移除的
        5. card 条目始终排在 facts 列表头部，顺序与 card 一致

        Returns True if any change was made.
        """
        try:
            _, _, master_basic_config, lanlan_basic_config, _, _, _, _, _ = (
                self._config_manager.get_character_data()
            )
        except Exception:
            return False
        return self._apply_character_card_sync(
            name, persona, master_basic_config, lanlan_basic_config,
        )

    async def _async_sync_character_card(self, name: str, persona: dict) -> bool:
        try:
            _, _, master_basic_config, lanlan_basic_config, _, _, _, _, _ = (
                await self._config_manager.aget_character_data()
            )
        except Exception:
            return False
        return self._apply_character_card_sync(
            name, persona, master_basic_config, lanlan_basic_config,
        )

    def save_persona(self, name: str, persona: dict | None = None) -> None:
        """Persist persona to disk; on failure evict the cached entry.

        Round-7 Major (CodeRabbit PR #936): the cache assignment
        happens BEFORE the save step, so any exception from
        `assert_cloudsave_writable` (cloudsave flipped to read-only)
        OR `atomic_write_json` (disk full / IO error) would otherwise
        leave `self._personas[name]` polluted with state that never
        landed on disk. Subsequent in-process reads (incl. sibling
        async writers via the shared cache) would serve the stale
        view until restart.

        Mirrors the eviction-on-save-failure invariant already
        enforced by `_sync_save_persona_view` (round-5/6 fixes) but
        for the non-event-sourced public save paths used by
        `add_fact`, `ensure_persona`'s character-card sync, and
        manual save callers. Same try/except wraps both the
        cloudsave gate and the atomic write so both failure modes
        evict uniformly.
        """
        if persona is None:
            persona = self._personas.get(name, self._empty_persona())
        self._personas[name] = persona
        try:
            assert_cloudsave_writable(
                self._config_manager,
                operation="save",
                target=f"memory/{name}/persona.json",
            )
            atomic_write_json(
                self._persona_path(name), persona, indent=2, ensure_ascii=False,
            )
        except Exception:
            # Evict the polluted cache entry — next ensure/aensure
            # reload re-reads the (unchanged) on-disk state.
            self._personas.pop(name, None)
            raise

    async def asave_persona(self, name: str, persona: dict | None = None) -> None:
        """Async twin of `save_persona` with the same eviction-on-failure
        contract (round-7 Major, CodeRabbit PR #936)."""
        if persona is None:
            persona = self._personas.get(name, self._empty_persona())
        self._personas[name] = persona
        try:
            assert_cloudsave_writable(
                self._config_manager,
                operation="save",
                target=f"memory/{name}/persona.json",
            )
            await atomic_write_json_async(
                self._persona_path(name), persona, indent=2, ensure_ascii=False,
            )
        except Exception:
            self._personas.pop(name, None)
            raise

    def get_persona(self, name: str) -> dict:
        return self.ensure_persona(name)

    async def aget_persona(self, name: str) -> dict:
        return await self.aensure_persona(name)

    # ── entry normalization ──────────────────────────────────────────

    @staticmethod
    def _normalize_entry(entry) -> dict:
        """将纯字符串条目迁移为 dict 格式。

        每个条目包含以下溯源字段：
        - id: 唯一标识。card_xxx / legacy_xxx / prom_xxx / manual_xxx
        - source: 来源类型。character_card / settings / reflection / manual
        - source_id: 上游 ID（如 reflection_id），用于追溯来源链

        Evidence fields (RFC §3.2.3 user-driven evidence mechanism):
        - reinforcement / disputation: float 累加器，仅由 user signal 驱动
        - rein_last_signal_at / disp_last_signal_at: 各自独立的衰减时钟
        - sub_zero_days + sub_zero_last_increment_date: archive 倒计时
        - merged_from_ids: LLM merge_into 决策吸收的 reflection id 列表

        Token-count cache fields (derived, cache-only — not event-sourced):
        - token_count: int | None — cached acount_tokens(text)
        - token_count_text_sha256: str | None — fingerprint of the text that
          was tokenized; a mismatch triggers recompute on the next render.
        - token_count_tokenizer: str | None — fingerprint of the counter
          used when `token_count` was written (e.g. `tiktoken:o200k_base`
          or `heuristic:v1`). A mismatch with the current tokenizer
          identity also triggers recompute, so a cache warmed under
          tiktoken doesn't get served to a heuristic-fallback render.

        Zero-migration schema addition: existing on-disk entries without
        these fields naturally read as None via `.get()`, which counts as a
        cache miss and triggers a clean recompute on first render. No
        explicit migration event is needed.
        """
        defaults = {
            'id': '',                   # 唯一标识
            'text': '',
            'source': 'unknown',        # character_card | settings | reflection | manual
            'source_id': None,          # 上游 ID（reflection_id 等）
            'recent_mentions': [],      # 窗口内提及时间戳列表
            'suppress': False,          # 是否被抑制
            'suppressed_at': None,      # suppress 开始时间
            'protected': False,         # character_card 来源条目，不可 suppress
            # Evidence counters (RFC §3.2.3)
            'reinforcement': 0.0,
            'disputation': 0.0,
            'rein_last_signal_at': None,
            'disp_last_signal_at': None,
            'sub_zero_days': 0,
            'sub_zero_last_increment_date': None,
            # user_fact reinforces combo 计数（RFC §3.1.8）。终生累计，
            # decay 只作用于 reinforcement 数值本身不影响这个计数器。
            'user_fact_reinforce_count': 0,
            # 溯源：merge_into 吸收的 reflection id 列表
            'merged_from_ids': [],
            # Derived token-count cache — populated by the render path
            # (`_get_cached_token_count` / `_aget_cached_token_count`)
            # on first render and ride-alongs with normal persona saves.
            # Both text-sha and tokenizer-identity must match for a hit,
            # so a cache warmed under tiktoken can't be served to a
            # heuristic-fallback render (e.g. packaging without encoding
            # data file).
            'token_count': None,
            'token_count_text_sha256': None,
            'token_count_tokenizer': None,
        }
        if isinstance(entry, str):
            d = dict(defaults)
            d['text'] = entry
            return d
        if isinstance(entry, dict):
            for k, v in defaults.items():
                entry.setdefault(k, v)
            # 兼容旧字段
            entry.pop('mention_count', None)
            entry.pop('consecutive_mentions', None)
            entry.pop('last_mentioned', None)
            return entry
        d = dict(defaults)
        d['text'] = str(entry)
        return d

    # ── add facts to persona ─────────────────────────────────────────

    # add_fact return codes
    FACT_ADDED = 'added'
    FACT_REJECTED_CARD = 'rejected_card'      # contradicts character_card → permanent
    FACT_QUEUED_CORRECTION = 'queued'         # contradicts non-card → correction queue

    def _evaluate_fact_contradiction(
        self, name: str, text: str, section_facts: list, stop_names: list[str],
    ) -> tuple[str | None, str | None]:
        """Returns (rejection_code, conflicting_text) or (None, None) if OK."""
        for existing in section_facts:
            if isinstance(existing, dict):
                old_text = existing.get('text', '')
                is_card = existing.get('source') == 'character_card'
            else:
                old_text = str(existing)
                is_card = False
            if self._texts_may_contradict(old_text, text, stop_names=stop_names):
                if is_card:
                    logger.info(
                        f"[Persona] {name}: 新条目与角色卡矛盾，无条件拒绝: "
                        f"card=\"{old_text[:40]}\" vs new=\"{text[:40]}\""
                    )
                    return self.FACT_REJECTED_CARD, old_text
                return self.FACT_QUEUED_CORRECTION, old_text
        return None, None

    def _build_fact_entry(self, text: str, source: str, source_id: str | None) -> dict:
        entry = self._normalize_entry(text)
        if source == 'reflection' and source_id:
            entry['id'] = f"prom_{source_id}"
        else:
            entry['id'] = f"manual_{datetime.now().strftime('%Y%m%d%H%M%S')}_{hashlib.sha256(text.encode()).hexdigest()[:8]}"
        entry['source'] = source
        entry['source_id'] = source_id
        return entry

    def add_fact(self, name: str, text: str, entity: str = 'master',
                 source: str = 'manual', source_id: str | None = None) -> str:
        """Add a confirmed fact to persona. Checks for contradictions first.

        Args:
            source: 来源类型 (reflection / manual / ...)
            source_id: 上游 ID，如 reflection_id (ref_xxx)

        Returns:
            FACT_ADDED            — successfully appended
            FACT_REJECTED_CARD    — contradicts character_card, permanently blocked
            FACT_QUEUED_CORRECTION — contradicts existing non-card fact, queued for LLM review
        """
        persona = self.ensure_persona(name)
        section_facts = self._get_section_facts(persona, entity)
        stop_names = self._get_entity_stop_names()

        code, old_text = self._evaluate_fact_contradiction(name, text, section_facts, stop_names)
        if code == self.FACT_REJECTED_CARD:
            return self.FACT_REJECTED_CARD
        if code == self.FACT_QUEUED_CORRECTION:
            self._queue_correction(name, old_text, text, entity)
            return self.FACT_QUEUED_CORRECTION

        section_facts.append(self._build_fact_entry(text, source, source_id))
        self.save_persona(name, persona)
        return self.FACT_ADDED

    async def aadd_fact(self, name: str, text: str, entity: str = 'master',
                        source: str = 'manual', source_id: str | None = None) -> str:
        """P2.a.2: 角色级 asyncio.Lock 串行化 add_fact / resolve_corrections /
        record_mentions，避免 persona.json 竞写。

        Note: _aqueue_correction 被调用时已在本锁内，因此其独立锁使用
        asyncio.Lock（可重入？不，asyncio.Lock 不可重入）→ 所以在锁内调用
        _aqueue_correction 的 **unlocked** 版本。"""
        async with self._get_alock(name):
            persona = await self._aensure_persona_locked(name)
            section_facts = self._get_section_facts(persona, entity)
            stop_names = await self._aget_entity_stop_names()

            code, old_text = self._evaluate_fact_contradiction(name, text, section_facts, stop_names)
            if code == self.FACT_REJECTED_CARD:
                return self.FACT_REJECTED_CARD
            if code == self.FACT_QUEUED_CORRECTION:
                await self._aqueue_correction_locked(name, old_text, text, entity)
                return self.FACT_QUEUED_CORRECTION

            section_facts.append(self._build_fact_entry(text, source, source_id))
            await self.asave_persona(name, persona)
            return self.FACT_ADDED

    # ── evidence signals (RFC §3.4, §3.8.4) ─────────────────────────

    @staticmethod
    def _find_entry_in_section(section_facts: list, entry_id: str) -> dict | None:
        for entry in section_facts:
            if isinstance(entry, dict) and entry.get('id') == entry_id:
                return entry
        return None

    # Snapshot compute moved to `memory.evidence.compute_evidence_snapshot` —
    # shared with ReflectionEngine so combo/rein/disp semantics stay in one
    # place. Re-exported here as a @staticmethod for backward-compat with
    # any caller that reaches into _compute_evidence_after_delta.
    @staticmethod
    def _compute_evidence_after_delta(
        entry: dict, delta: dict, now_iso: str, source: str = 'unknown',
    ) -> dict:
        from memory.evidence import compute_evidence_snapshot
        return compute_evidence_snapshot(entry, delta, now_iso, source)

    async def aapply_signal(
        self, name: str, entity_key: str, entry_id: str,
        delta: dict, source: str,
    ) -> bool:
        """Mutate an entry's evidence counters via EVT_PERSONA_EVIDENCE_UPDATED.

        Full-snapshot payload, record_and_save 合约（RFC §3.3.3）。锁嵌套：
        先拿 PersonaManager async 锁，再在 record_and_save 内部拿 event_log
        threading.Lock——符合 §3.3.3 "外 async 内 sync" 规约。

        Returns True if the entry existed and was updated; False otherwise
        (unknown entry — migration marker case handled by caller).
        """
        from memory.event_log import EVT_PERSONA_EVIDENCE_UPDATED
        if self._event_log is None:
            raise RuntimeError(
                "[Persona.aapply_signal] event_log 未注入；PersonaManager() 构造时须传入 event_log"
            )

        async with self._get_alock(name):
            persona = await self._aensure_persona_locked(name)
            section = persona.get(entity_key)
            if not isinstance(section, dict):
                logger.warning(
                    f"[Persona] {name}: aapply_signal 找不到 entity_key={entity_key}"
                )
                return False
            section_facts = section.get('facts', [])
            entry = self._find_entry_in_section(section_facts, entry_id)
            if entry is None:
                logger.warning(
                    f"[Persona] {name}: aapply_signal 找不到 entry_id={entry_id}"
                )
                return False

            now_iso = datetime.now().isoformat()
            snapshot = self._compute_evidence_after_delta(
                entry, delta, now_iso, source,
            )
            payload = {
                'entity_key': entity_key,
                'entry_id': entry_id,
                'reinforcement': snapshot['reinforcement'],
                'disputation': snapshot['disputation'],
                'rein_last_signal_at': snapshot['rein_last_signal_at'],
                'disp_last_signal_at': snapshot['disp_last_signal_at'],
                'sub_zero_days': snapshot['sub_zero_days'],
                'user_fact_reinforce_count': snapshot['user_fact_reinforce_count'],
                'source': source,
            }

            def _sync_load(_n: str):
                # 我们已持 async 锁 + 内存 cache 就是当前 view，直接复用。
                return persona

            def _sync_mutate(_view):
                entry['reinforcement'] = snapshot['reinforcement']
                entry['disputation'] = snapshot['disputation']
                entry['rein_last_signal_at'] = snapshot['rein_last_signal_at']
                entry['disp_last_signal_at'] = snapshot['disp_last_signal_at']
                entry['sub_zero_days'] = snapshot['sub_zero_days']
                entry['user_fact_reinforce_count'] = snapshot['user_fact_reinforce_count']

            # _sync_save: cloudsave gate + write + cache-evict-on-failure
            # (CodeRabbit PR #929 for the gate, PR #936 round-5 for the
            # evict). See `_sync_save_persona_view` docstring.
            _sync_save = self._sync_save_persona_view

            await self._event_log.arecord_and_save(
                name, EVT_PERSONA_EVIDENCE_UPDATED, payload,
                sync_load_view=_sync_load,
                sync_mutate_view=_sync_mutate,
                sync_save_view=_sync_save,
            )
            return True

    @staticmethod
    def _find_entry_with_section(
        persona: dict, entry_id: str,
    ) -> tuple[str | None, dict | None]:
        """Locate an entry by id across all entity sections.

        Returns `(entity_key, entry_dict)` or `(None, None)` if absent.
        Used by `amerge_into` where the caller (LLM) supplies a fully-qualified
        target_id but we still need to know which entity section to address
        the event payload against.

        Accepts both bare ids ("p_001") and the fully-qualified
        prompt form ("persona.<entity>.p_001"). The reflection promote
        path strips the prefix before calling, but we accept both forms
        defensively so any callsite (tests, future plugins, manual
        replay) works without re-implementing the parser.
        """
        # Defensive parse of the qualified form. Anything that doesn't
        # match `persona.<entity>.<id>` falls through to direct equality.
        qualified_entity: str | None = None
        bare_id = entry_id
        if isinstance(entry_id, str) and entry_id.startswith('persona.'):
            parts = entry_id.split('.', 2)
            if len(parts) == 3 and parts[2]:
                qualified_entity = parts[1]
                bare_id = parts[2]

        for ek, section in persona.items():
            if not isinstance(section, dict):
                continue
            if qualified_entity is not None and ek != qualified_entity:
                continue
            for entry in section.get('facts', []):
                if isinstance(entry, dict) and entry.get('id') == bare_id:
                    return ek, entry
        return None, None

    async def amerge_into(
        self, name: str, target_entry_id: str, merged_text: str,
        *,
        reflection_evidence: dict,
        source_reflection_id: str,
        merged_from_ids: list[str] | None = None,
    ) -> str:
        """Merge a reflection's content into an existing persona entry.

        Atomically rewrites the target entry's `text`, evidence values, and
        appends `source_reflection_id` to its `merged_from_ids` audit list.
        Emits two events (RFC §3.9.6), in this deliberate order:

          1. EVT_PERSONA_EVIDENCE_UPDATED — evidence-only snapshot so the
             funnel API (§3.10) can scan for evidence changes without
             joining the entry-update stream. Emitted FIRST so that a crash
             between the two writes does not permanently orphan this
             signal.
          2. EVT_PERSONA_ENTRY_UPDATED — text rewrite + evidence + audit;
             carries `rewrite_text_sha256` so the reconciler can detect view
             drift on replay. This is also the event that actually writes
             `merged_from_ids` (the idempotency sentinel) onto the view.

        Order rationale (CodeRabbit PR #936 round-4 Major): the old order
        (entry_updated first, evidence_updated second) created a crash
        window where the sentinel `merged_from_ids` landed on disk but the
        evidence_updated event never did. On retry the idempotency gate at
        line ~911 (`source_reflection_id in existing_merged_from`) returned
        'noop' and the evidence event was permanently lost — funnel
        observability silently missed that merge. By emitting
        evidence_updated FIRST (it has no idempotency side-state), a crash
        between the two writes leaves a retry in the "still not merged"
        state, so on retry BOTH events re-emit and entry_updated finalizes.
        The trade-off is that a crash-retry may append an extra
        evidence_updated to the log (new event_id); the funnel then
        slightly over-counts this merge (rare, human-facing metric) —
        strictly better than the alternative of permanently missing it.

        Idempotency (RFC §3.9.6 "崩溃半程"): if `source_reflection_id` is
        already in the target's `merged_from_ids`, both events are skipped
        and the call returns 'noop'. Replaying persisted events by
        event_id is idempotent on the reconciler side (sha256 matches →
        no-op).

        Evidence aggregation (CodeRabbit PR #936 round-6 Major #2):
        callers MUST pass `reflection_evidence={'reinforcement': ...,
        'disputation': ...}` carrying the source reflection's own
        evidence values; the conservative max-rule against the target's
        CURRENT evidence is computed HERE under the per-character lock.
        The previous signature took pre-computed `merged_reinforcement`
        / `merged_disputation` from the caller, which forced the caller
        to snapshot the target outside the lock. A concurrent
        `aapply_signal` (or another merge) on the same entry between
        the snapshot and `amerge_into` would produce stale "max"
        values, and writing them here effectively rolled the newer
        signal back. Computing under the lock guarantees the merge
        consumes the freshest target state.

        Returns: 'merged' on success, 'noop' if already merged, 'not_found'
        if `target_entry_id` is missing from the persona.
        """
        from memory.event_log import (
            EVT_PERSONA_ENTRY_UPDATED,
            EVT_PERSONA_EVIDENCE_UPDATED,
            EVIDENCE_SOURCE_PROMOTE_MERGE,
        )
        from memory.reflection import ReflectionEngine
        if self._event_log is None:
            raise RuntimeError(
                "[Persona.amerge_into] event_log 未注入；"
                "PersonaManager() 构造时须传入 event_log"
            )

        async with self._get_alock(name):
            persona = await self._aensure_persona_locked(name)
            entity_key, target_entry = self._find_entry_with_section(
                persona, target_entry_id,
            )
            if target_entry is None or entity_key is None:
                logger.warning(
                    f"[Persona] {name}: amerge_into 找不到 target_entry_id="
                    f"{target_entry_id}"
                )
                return 'not_found'

            # Compute merged evidence UNDER THE LOCK against the
            # currently-locked target entry — see "Evidence aggregation"
            # block in docstring for the rollback hazard this prevents.
            merged_reinforcement, merged_disputation = (
                ReflectionEngine._compute_merged_evidence(
                    target_entry, reflection_evidence or {},
                )
            )

            # Normalize the id we put in event payloads + log lines to the
            # canonical bare form stored on disk. `_find_entry_with_section`
            # accepts both bare and fully-qualified (`persona.<entity>.<id>`)
            # forms; if a future caller passes the qualified form, the
            # downstream reconciler handlers (`make_persona_entry_handler`,
            # `make_persona_evidence_handler`) match strictly on the bare id
            # via `e.get('id') == entry_id`. Writing the qualified form into
            # the payload would make crash-replay miss the entry. RFC §3.9.6:
            # event payloads must reference the canonical on-disk id.
            canonical_entry_id = target_entry.get('id') or target_entry_id

            existing_merged_from = list(target_entry.get('merged_from_ids') or [])
            if source_reflection_id in existing_merged_from:
                logger.info(
                    f"[Persona] {name}: amerge_into idempotent skip "
                    f"target={canonical_entry_id} src={source_reflection_id}"
                )
                return 'noop'

            # Compute new audit list — dedup by id, preserve insertion order.
            # source_reflection_id MUST be in the final list because it is the
            # idempotency sentinel used at line ~911 (`if source_reflection_id
            # in existing_merged_from: return 'noop'`). If a caller passes a
            # non-empty `merged_from_ids` that omits `source_reflection_id`,
            # the previous fallback `(merged_from_ids or [source_reflection_id])`
            # would skip adding the sentinel and a retry of the same merge
            # would re-apply instead of no-op'ing — audit completeness /
            # idempotency bug (CodeRabbit PR #936 round-4 Minor).
            new_merged_from = list(existing_merged_from)
            for rid in list(merged_from_ids or []) + [source_reflection_id]:
                if rid not in new_merged_from:
                    new_merged_from.append(rid)

            now_iso = datetime.now().isoformat()
            new_text_sha = hashlib.sha256(
                (merged_text or '').encode('utf-8'),
            ).hexdigest()

            entry_payload = {
                'entity_key': entity_key,
                'entry_id': canonical_entry_id,
                'rewrite_text_sha256': new_text_sha,
                'reinforcement': float(merged_reinforcement),
                'disputation': float(merged_disputation),
                # Both clocks bumped — the merge IS a fresh signal on this
                # entry from both sides (rein from the absorbed reflection's
                # confirmations, disp likewise). RFC §3.1.1 says "只重置被
                # 触动的一侧" for normal aapply_signal, but merge is a
                # special case: target evidence values are RECOMPUTED from
                # both contributors via _compute_merged_evidence (max), so
                # both timestamps reflect the moment that recomputation
                # happened — semantic-clean, no half-stale clock.
                'rein_last_signal_at': now_iso,
                'disp_last_signal_at': now_iso,
                # sub_zero_days reset to 0 — the merge brought new positive
                # signal; archive countdown should restart.
                'sub_zero_days': 0,
                'merged_from_ids': new_merged_from,
                'source': EVIDENCE_SOURCE_PROMOTE_MERGE,
            }

            evidence_payload = {
                'entity_key': entity_key,
                'entry_id': canonical_entry_id,
                'reinforcement': float(merged_reinforcement),
                'disputation': float(merged_disputation),
                'rein_last_signal_at': now_iso,
                'disp_last_signal_at': now_iso,
                'sub_zero_days': 0,
                'user_fact_reinforce_count':
                    int(target_entry.get('user_fact_reinforce_count', 0) or 0),
                'source': EVIDENCE_SOURCE_PROMOTE_MERGE,
            }

            def _sync_load(_n: str):
                return persona

            def _sync_mutate_evidence(_view):
                # Evidence_updated emits FIRST and intentionally does NOT
                # write `merged_from_ids` — that sentinel is the idempotency
                # signal for the whole 2-event sequence (line ~911). If we
                # set it here, a crash between the two emits would make the
                # retry think the merge is already done and skip
                # entry_updated forever. Keeping this as a no-op means the
                # view on disk after event 1 still looks "un-merged" from
                # the idempotency gate's perspective, so retries fire both
                # events in order. The evidence_updated event payload
                # itself already carries the post-merge reinforcement /
                # disputation snapshot — replay handler will apply it.
                return None

            def _sync_mutate_entry(_view):
                # Entry_updated (event 2) writes the full final state,
                # including `merged_from_ids` (the idempotency sentinel).
                # By the time this runs, event 1 has already been recorded
                # to the log, so any crash from here onward is
                # replay-recoverable.
                target_entry['text'] = merged_text
                target_entry['reinforcement'] = float(merged_reinforcement)
                target_entry['disputation'] = float(merged_disputation)
                target_entry['rein_last_signal_at'] = now_iso
                target_entry['disp_last_signal_at'] = now_iso
                target_entry['sub_zero_days'] = 0
                target_entry['merged_from_ids'] = new_merged_from
                # Token-count cache is derived from `text`; rewriting text
                # must drop the cache so the next render recomputes. The
                # fingerprint check would catch the drift anyway, but
                # explicit invalidation avoids the tiny window where a
                # concurrent reader might see new text + stale count and
                # saves one sha256 compute on the next render.
                self._invalidate_token_count_cache(target_entry)

            # _sync_save: cloudsave gate + write + cache-evict-on-failure
            # (CodeRabbit PR #936 round-5 Major #1). See
            # `_sync_save_persona_view` docstring.
            _sync_save = self._sync_save_persona_view

            # Event 1: evidence_updated — emitted FIRST so a crash between
            # the two writes does NOT permanently orphan this signal. The
            # mutate is a no-op (see _sync_mutate_evidence above); the view
            # on disk is unchanged after this call, which keeps the
            # idempotency gate "still not merged" so a retry re-emits
            # both events. Slight funnel over-count on retry is
            # acceptable vs. permanent signal loss (RFC §3.10 is a
            # human-facing metric).
            await self._event_log.arecord_and_save(
                name, EVT_PERSONA_EVIDENCE_UPDATED, evidence_payload,
                sync_load_view=_sync_load,
                sync_mutate_view=_sync_mutate_evidence,
                sync_save_view=_sync_save,
            )
            # Event 2: entry_updated — canonical merge event. Writes the
            # text rewrite + evidence + audit list (`merged_from_ids`).
            # After this returns, persona.json is on disk with the full
            # merged state and the idempotency sentinel is in place.
            await self._event_log.arecord_and_save(
                name, EVT_PERSONA_ENTRY_UPDATED, entry_payload,
                sync_load_view=_sync_load,
                sync_mutate_view=_sync_mutate_entry,
                sync_save_view=_sync_save,
            )
            logger.info(
                f"[Persona] {name}: amerge_into target={canonical_entry_id} "
                f"src={source_reflection_id} rein={merged_reinforcement} "
                f"disp={merged_disputation}"
            )
            return 'merged'

    # ── score-driven archive (RFC §3.5, PR-2 #934) ───────────────────

    async def aincrement_sub_zero(
        self, name: str, entity_key: str, entry_id: str, now: datetime,
    ) -> int | None:
        """Increment one persona entry's `sub_zero_days` via EVT_PERSONA_EVIDENCE_UPDATED.

        Symmetric to `ReflectionEngine.aincrement_sub_zero`. Called by
        the periodic archive sweep loop. Returns the new count or None
        if no increment happened.
        """
        from memory.event_log import EVT_PERSONA_EVIDENCE_UPDATED
        from memory.evidence import maybe_mark_sub_zero
        if self._event_log is None:
            raise RuntimeError(
                "[Persona.aincrement_sub_zero] event_log 未注入"
            )

        async with self._get_alock(name):
            persona = await self._aensure_persona_locked(name)
            section = persona.get(entity_key)
            if not isinstance(section, dict):
                return None
            section_facts = section.get('facts', [])
            entry = self._find_entry_in_section(section_facts, entry_id)
            if entry is None:
                return None
            # Coderabbit PR #934 round-2 Major #2: probe on a staged copy
            # so the cached entry is NOT mutated until inside the locked
            # record_and_save critical section. If event append or save
            # raises, the cache stays clean (no orphan sub_zero_days
            # increment that never made it to the event log).
            staged_entry = dict(entry)
            if not maybe_mark_sub_zero(staged_entry, now):
                return None

            new_count = int(staged_entry.get('sub_zero_days', 0) or 0)
            new_date = staged_entry.get('sub_zero_last_increment_date')

            payload = {
                'entity_key': entity_key,
                'entry_id': entry_id,
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
                return persona

            def _sync_mutate(_view):
                # Apply the staged values to the cached entry only after
                # event append has already succeeded (record_and_save
                # guarantees this ordering).
                entry['sub_zero_days'] = new_count
                entry['sub_zero_last_increment_date'] = new_date

            # _sync_save: cloudsave gate + write + cache-evict-on-failure
            # (CodeRabbit PR #936 round-5 Major #1). See
            # `_sync_save_persona_view` docstring.
            _sync_save = self._sync_save_persona_view

            await self._event_log.arecord_and_save(
                name, EVT_PERSONA_EVIDENCE_UPDATED, payload,
                sync_load_view=_sync_load,
                sync_mutate_view=_sync_mutate,
                sync_save_view=_sync_save,
            )
            return new_count

    async def aarchive_persona_entry(
        self, name: str, entity_key: str, entry_id: str,
    ) -> bool:
        """Move one persona entry from main view to a sharded archive file.

        RFC §3.5.6: archive 复用 ``EVT_PERSONA_FACT_ADDED`` 事件 — payload
        carries an `archive_shard_path` field so consumers can distinguish
        the archive flow from a regular fact_added (regular adds have no
        such field). Mirrors `ReflectionEngine.aarchive_reflection`.

        Returns True if archived; False if not found / protected.
        """
        from memory.archive_shards import aappend_to_shard, apick_today_shard_path
        from memory.event_log import EVT_PERSONA_FACT_ADDED
        if self._event_log is None:
            raise RuntimeError(
                "[Persona.aarchive_persona_entry] event_log 未注入；"
                "PersonaManager() 构造时须传入 event_log"
            )

        async with self._get_alock(name):
            persona = await self._aensure_persona_locked(name)
            section = persona.get(entity_key)
            if not isinstance(section, dict):
                logger.warning(
                    f"[Persona] {name}: aarchive_persona_entry 找不到 "
                    f"entity_key={entity_key}"
                )
                return False
            section_facts = section.get('facts', [])
            entry = self._find_entry_in_section(section_facts, entry_id)
            if entry is None:
                logger.warning(
                    f"[Persona] {name}: aarchive_persona_entry 找不到 "
                    f"entry_id={entry_id}"
                )
                return False
            if entry.get('protected'):
                logger.debug(
                    f"[Persona] {name}: aarchive_persona_entry 跳过 protected "
                    f"entry_id={entry_id}"
                )
                return False

            now = datetime.now()
            now_iso = now.isoformat()
            archive_dir = self._persona_archive_dir(name)
            # Pre-pick the shard path BEFORE record_and_save so we can
            # stamp it into the event payload (and into the archive_entry
            # we'll write afterward). `apick_today_shard_path` materializes
            # the file on disk so the choice is stable across the
            # subsequent shard append.
            shard_path = await apick_today_shard_path(archive_dir, now=now)
            shard_basename = os.path.basename(shard_path)
            archive_entry = dict(entry)
            archive_entry['archived_at'] = now_iso
            archive_entry['archive_shard_path'] = shard_basename

            payload = {
                'entity_key': entity_key,
                'entry_id': entry_id,
                'archive_shard_path': shard_basename,
                'archived_at': now_iso,
                # Snapshot the text/source for replayability without
                # reading the shard back from disk.
                'text': entry.get('text', ''),
                'source': entry.get('source', 'unknown'),
                # Full entry snapshot — the persona archive handler in
                # evidence_handlers.py reads this on every replay and
                # idempotently recreates the shard if it's missing
                # (coderabbit PR #934 round-2 Major #3). Recoverable
                # crash window: any failure between record_and_save
                # and the shard append below is healed on the next
                # reconciler boot.
                'entry_snapshot': archive_entry,
            }

            def _sync_load(_n: str):
                return persona

            def _sync_mutate(_view):
                # Drop the archived entry from the entity section.
                section_facts[:] = [
                    e for e in section_facts
                    if not (isinstance(e, dict) and e.get('id') == entry_id)
                ]

            # _sync_save: cloudsave gate + write + cache-evict-on-failure
            # (CodeRabbit PR #936 round-5 Major #1). See
            # `_sync_save_persona_view` docstring.
            _sync_save = self._sync_save_persona_view

            # ORDER (coderabbit review #934 round-1 + round-2):
            # 1. record_and_save first — commits event + view mutation
            #    atomically. Avoids "duplicated shard entry + still
            #    active in view" (next sweep would re-archive into a
            #    second shard slot).
            # 2. aappend_to_shard second. If this raises, the active
            #    view has already lost the entry but the shard never
            #    got it. Self-heal: the persona archive handler in
            #    evidence_handlers.py reads `entry_snapshot` from the
            #    event payload and re-creates the shard on the next
            #    reconciler boot — event log is the source of truth
            #    (RFC §3.11), snapshot makes recovery automatic.
            await self._event_log.arecord_and_save(
                name, EVT_PERSONA_FACT_ADDED, payload,
                sync_load_view=_sync_load,
                sync_mutate_view=_sync_mutate,
                sync_save_view=_sync_save,
            )
            await aappend_to_shard(archive_dir, [archive_entry], now=now)
            logger.info(
                f"[Persona] {name}: 归档 entry {entity_key}/{entry_id} "
                f"→ {shard_basename}"
            )
            return True

    def _get_section_facts(self, persona: dict, entity: str) -> list:
        return persona.setdefault(entity, {}).setdefault('facts', [])

    def _get_entity_stop_names(self) -> list[str]:
        """Return master + neko names to strip from contradiction keywords."""
        try:
            master_name, her_name, _, _, _, _, _, _, _ = (
                self._config_manager.get_character_data()
            )
            names = []
            if master_name:
                names.append(master_name)
            if her_name:
                names.append(her_name)
            return names
        except Exception:
            return []

    async def _aget_entity_stop_names(self) -> list[str]:
        try:
            master_name, her_name, _, _, _, _, _, _, _ = (
                await self._config_manager.aget_character_data()
            )
            names = []
            if master_name:
                names.append(master_name)
            if her_name:
                names.append(her_name)
            return names
        except Exception:
            return []

    @staticmethod
    def _texts_may_contradict(old_text: str, new_text: str,
                              stop_names: list[str] | None = None) -> bool:
        """Lightweight keyword-overlap heuristic for contradiction detection.

        Uses the same CJK-aware tokenization as _is_mentioned.
        ``stop_names`` — entity names (master/neko) whose n-grams are
        stripped from both sides so that shared names alone don't inflate
        the overlap ratio.
        """
        if not old_text or not new_text:
            return False
        old_kw = _extract_keywords(old_text)
        new_kw = _extract_keywords(new_text)
        if stop_names:
            stop_kw: set[str] = set()
            for sn in stop_names:
                stop_kw |= _extract_keywords(sn)
            old_kw -= stop_kw
            new_kw -= stop_kw
        if not old_kw or not new_kw:
            return False
        overlap = old_kw & new_kw
        ratio = len(overlap) / min(len(old_kw), len(new_kw))
        return ratio >= 0.4

    # ── contradiction queue ──────────────────────────────────────────

    @staticmethod
    def _build_correction_list(
        corrections: list[dict], old_text: str, new_text: str, entity: str,
    ) -> list[dict] | None:
        """Returns the modified list or None if duplicate (no change needed)."""
        for existing in corrections:
            if (existing.get('old_text') == old_text
                    and existing.get('new_text') == new_text
                    and existing.get('entity') == entity):
                return None
        corrections.append({
            'old_text': old_text,
            'new_text': new_text,
            'entity': entity,
            'created_at': datetime.now().isoformat(),
        })
        return corrections

    def _queue_correction(self, name: str, old_text: str, new_text: str, entity: str) -> None:
        corrections = self.load_pending_corrections(name)
        updated = self._build_correction_list(corrections, old_text, new_text, entity)
        if updated is None:
            return
        assert_cloudsave_writable(
            self._config_manager,
            operation="save",
            target=f"memory/{name}/persona_corrections.json",
        )
        atomic_write_json(self._corrections_path(name), updated, indent=2, ensure_ascii=False)
        logger.info(f"[Persona] {name}: 发现潜在矛盾，加入审视队列")

    async def _aqueue_correction(self, name: str, old_text: str, new_text: str, entity: str) -> None:
        """Public async entry — acquires the per-character lock.
        Callers already holding the lock must use _aqueue_correction_locked."""
        async with self._get_alock(name):
            await self._aqueue_correction_locked(name, old_text, new_text, entity)

    async def _aqueue_correction_locked(self, name: str, old_text: str, new_text: str, entity: str) -> None:
        """Inner body. Caller must hold self._get_alock(name).
        Used by aadd_fact which already has the lock."""
        corrections = await self.aload_pending_corrections(name)
        updated = self._build_correction_list(corrections, old_text, new_text, entity)
        if updated is None:
            return
        assert_cloudsave_writable(
            self._config_manager,
            operation="save",
            target=f"memory/{name}/persona_corrections.json",
        )
        await atomic_write_json_async(self._corrections_path(name), updated, indent=2, ensure_ascii=False)
        logger.info(f"[Persona] {name}: 发现潜在矛盾，加入审视队列")

    def load_pending_corrections(self, name: str) -> list[dict]:
        path = self._corrections_path(name)
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        return []

    async def aload_pending_corrections(self, name: str) -> list[dict]:
        path = self._corrections_path(name)
        if not await asyncio.to_thread(os.path.exists, path):
            return []
        try:
            data = await read_json_async(path)
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, OSError):
            # 文件损坏或被并发进程替换：按空队列处理，下次 add_pending_correction 会重建
            pass
        return []

    async def resolve_corrections(self, name: str) -> int:
        """用 correction model 批量审视矛盾队列（单次 LLM 调用）。

        将所有 pending corrections 合并为一个 prompt 发给 correction model，
        返回处理的矛盾数量。

        P2.a.2: 角色级 asyncio.Lock 串行化；整个流程（load + LLM + write back）
        都在锁内，避免 aadd_fact / arecord_mentions 并发写 persona.json。
        """
        async with self._get_alock(name):
            return await self._resolve_corrections_locked(name)

    async def _resolve_corrections_locked(self, name: str) -> int:
        """resolve_corrections 的内部实现。调用方必须已持有
        self._get_alock(name)。"""
        from config.prompts_memory import persona_correction_prompt

        corrections = await self.aload_pending_corrections(name)
        if not corrections:
            return 0

        # 合并所有矛盾为单个 prompt
        pairs = []
        for i, item in enumerate(corrections):
            old_text = item.get('old_text', '')
            new_text = item.get('new_text', '')
            if old_text and new_text:
                pairs.append((i, item))
        if not pairs:
            return 0

        batch_text = "\n".join(
            f"[{i}] 已有: {item['old_text']} | 新观察: {item['new_text']}"
            for i, item in pairs
        )
        prompt = persona_correction_prompt.format(pairs=batch_text, count=len(pairs))

        try:
            from utils.token_tracker import set_call_type
            from utils.llm_client import create_chat_llm
            set_call_type("memory_correction")
            api_config = self._config_manager.get_model_api_config('correction')
            llm = create_chat_llm(
                api_config.get('model', SETTING_PROPOSER_MODEL),
                api_config['base_url'], api_config['api_key'],
                temperature=0.3,
            )
            try:
                resp = await llm.ainvoke(prompt)
            finally:
                await llm.aclose()
            raw = resp.content
            if raw.startswith("```"):
                raw = raw.replace("```json", "").replace("```", "").strip()
            results = robust_json_loads(raw)
            if not isinstance(results, list):
                results = [results]
        except Exception as e:
            logger.warning(f"[Persona] {name}: correction model 调用失败: {e}")
            return 0

        # 应用结果（本函数被 resolve_corrections 在锁下调用，故用 _locked 变体）
        persona = await self._aensure_persona_locked(name)
        resolved = 0
        for result in results:
            if not isinstance(result, dict):
                continue
            try:
                idx = int(result.get('index', -1))
                if idx < 0 or idx >= len(corrections):
                    continue
                item = corrections[idx]
            except (ValueError, TypeError):
                continue

            action = result.get('action', 'keep_both')
            merged_text = result.get('text', item.get('new_text', ''))
            entity = item.get('entity', 'master')
            old_text = item.get('old_text', '')
            new_text = item.get('new_text', '')
            section_facts = self._get_section_facts(persona, entity)

            if action == 'replace':
                for j, existing in enumerate(section_facts):
                    et = existing.get('text', '') if isinstance(existing, dict) else str(existing)
                    if et == old_text:
                        section_facts[j] = self._normalize_entry(merged_text)
                        break
            elif action == 'keep_new':
                section_facts[:] = [
                    e for e in section_facts
                    if (e.get('text', '') if isinstance(e, dict) else str(e)) != old_text
                ]
                section_facts.append(self._normalize_entry(new_text))
            elif action == 'keep_old':
                pass
            else:  # keep_both
                existing_texts = {
                    (e.get('text', '') if isinstance(e, dict) else str(e))
                    for e in section_facts
                }
                if new_text not in existing_texts:
                    section_facts.append(self._normalize_entry(new_text))

            resolved += 1

        if resolved:
            await self.asave_persona(name, persona)
            # 收集已处理条目的 created_at 作为精确匹配键
            processed_keys: set[str] = set()
            for r in results:
                raw_idx = r.get('index')
                if raw_idx is None:
                    continue
                try:
                    idx = int(raw_idx)
                    if 0 <= idx < len(corrections):
                        key = corrections[idx].get('created_at', '')
                        if key:
                            processed_keys.add(key)
                except (ValueError, TypeError):
                    continue
            # 重新读取文件，仅删除已处理的条目，保留 LLM 期间新增的
            # （防止并发 _aqueue_correction 新追加的矛盾被覆盖丢失）
            current = await self.aload_pending_corrections(name)
            remaining = [c for c in current if c.get('created_at', '') not in processed_keys]
            assert_cloudsave_writable(
                self._config_manager,
                operation="save",
                target=f"memory/{name}/persona_corrections.json",
            )
            await atomic_write_json_async(self._corrections_path(name), remaining,
                                          indent=2, ensure_ascii=False)
            logger.info(f"[Persona] {name}: 批量审视完成 {resolved} 条矛盾，剩余 {len(remaining)} 条")
        return resolved

    # ── 提及疲劳：记录 + 更新 suppress ───────────────────────────

    def _apply_record_mentions(self, persona: dict, response_text: str) -> bool:
        now_str = datetime.now().isoformat()
        now = datetime.now()
        cutoff = now - timedelta(hours=SUPPRESS_WINDOW_HOURS)
        changed = False

        for entry in self._collect_all_entries(persona):
            if not isinstance(entry, dict):
                continue
            if entry.get('protected'):
                continue
            if not _is_mentioned(entry.get('text', ''), response_text):
                continue

            mentions = entry.get('recent_mentions', [])
            mentions.append(now_str)
            mentions = [t for t in mentions if self._in_window(t, cutoff)]
            entry['recent_mentions'] = mentions

            if not entry.get('suppress') and len(mentions) > SUPPRESS_MENTION_LIMIT:
                entry['suppress'] = True
                entry['suppressed_at'] = now_str
            changed = True
        return changed

    def record_mentions(self, name: str, response_text: str) -> None:
        """主动搭话投递后，扫描 response 中哪些 persona 条目被提及。

        核心逻辑：5小时内提及 > SUPPRESS_MENTION_LIMIT 次 → suppress。
        """
        persona = self.ensure_persona(name)
        if self._apply_record_mentions(persona, response_text):
            self.save_persona(name, persona)

    async def arecord_mentions(self, name: str, response_text: str) -> None:
        async with self._get_alock(name):
            persona = await self._aensure_persona_locked(name)
            if self._apply_record_mentions(persona, response_text):
                await self.asave_persona(name, persona)

    def _apply_update_suppressions(self, persona: dict) -> bool:
        now = datetime.now()
        cutoff = now - timedelta(hours=SUPPRESS_WINDOW_HOURS)
        changed = False

        for entry in self._collect_all_entries(persona):
            if not isinstance(entry, dict):
                continue

            mentions = entry.get('recent_mentions', [])
            cleaned = [t for t in mentions if self._in_window(t, cutoff)]
            if len(cleaned) != len(mentions):
                entry['recent_mentions'] = cleaned
                changed = True

            if entry.get('suppress'):
                suppressed_str = entry.get('suppressed_at')
                if suppressed_str:
                    try:
                        hours_since = (now - datetime.fromisoformat(suppressed_str)).total_seconds() / 3600
                        if hours_since >= SUPPRESS_COOLDOWN_HOURS:
                            entry['suppress'] = False
                            entry['suppressed_at'] = None
                            entry['recent_mentions'] = []
                            changed = True
                    except (ValueError, TypeError):
                        pass
        return changed

    def update_suppressions(self, name: str) -> None:
        """刷新 suppress 状态：冷却期过 → 解除；清理窗口外的 recent_mentions。"""
        persona = self.ensure_persona(name)
        if self._apply_update_suppressions(persona):
            self.save_persona(name, persona)

    async def aupdate_suppressions(self, name: str) -> None:
        """P2.a.2: persona.json 写回必须在角色锁下，避免与 aadd_fact /
        arecord_mentions / aresolve_corrections 竞写。"""
        async with self._get_alock(name):
            persona = await self._aensure_persona_locked(name)
            if self._apply_update_suppressions(persona):
                await self.asave_persona(name, persona)

    @staticmethod
    def _in_window(ts_str: str, cutoff: datetime) -> bool:
        try:
            return datetime.fromisoformat(ts_str) >= cutoff
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _collect_all_entries(persona: dict) -> list[dict]:
        """收集 persona 中所有 entity section 的 facts 条目引用。"""
        entries = []
        for section in persona.values():
            if isinstance(section, dict):
                entries.extend(section.get('facts', []))
        return entries

    # ── rendering ────────────────────────────────────────────────────
    #
    # Three-phase pipeline (RFC §3.6.2):
    #   Phase 1 (split): protected vs non-protected per entity.
    #   Phase 2 (score-trim): per-section budget; protected always kept.
    #   Phase 3 (compose): emit headers + suppressed区 in stable order.
    #
    # Both sync (`render_persona_markdown` / `_compose_persona_markdown`)
    # and async (`arender_persona_markdown`) twins exist. Tests + migration
    # scripts use the sync path; the production hot path is async — only
    # `acount_tokens` differs (the rest of the math is sync). RFC §3.6.6:
    # tiktoken's Rust core releases the GIL, but we still hop to a worker
    # thread on the async path so the FastAPI event loop doesn't stall on
    # batches of ~100 entries.

    # ── token-count cache helpers ────────────────────────────────────
    #
    # Each persona entry dict may carry three derived fields populated
    # on first render: `token_count` (int), `token_count_text_sha256`
    # (str) and `token_count_tokenizer` (str). `_normalize_entry`
    # defaults all three to None.
    #
    # Reflection entries deliberately do NOT default these fields —
    # `_normalize_reflection` leaves them absent because reflections
    # have no process-resident cache (each render re-reads from disk),
    # so any writeback would be garbage-collected with the transient
    # list. Reflection renders therefore call the same helpers with
    # `writeback=False` and never persist cache fields. See the
    # commentary on `_get_cached_token_count` for the contract.
    #
    # Read path (persona): compute sha256(text); if it matches the
    # stored fingerprint AND the count is populated, use the cached
    # value. Otherwise compute (sync `count_tokens` / async
    # `acount_tokens`) and write all three fields back to the in-memory
    # entry. The cache is never written to disk directly — it rides
    # along whenever the persona is otherwise saved (add_fact,
    # amerge_into, asave_persona, etc.). A fresh process boot
    # re-tokenizes on first render which is an acceptable warm-up cost.
    #
    # Red line compliance: the cache is purely derived from `text`, so
    # event-sourcing it would duplicate the source of truth (see
    # RFC §3.6.8 + the "derived values shouldn't produce events"
    # principle). The in-memory update + ride-along-on-save approach
    # also avoids "view mutations outside an event" — we only ever
    # invoke a disk write through existing event-sourced or save-
    # permitted paths.

    @staticmethod
    def _text_fingerprint(text: str) -> str:
        """sha256 hex digest of `text` used as the cache key. Same
        encoding as the `rewrite_text_sha256` payload in amerge_into so
        the two stay consistent if we ever cross-check."""
        return hashlib.sha256((text or '').encode('utf-8')).hexdigest()

    @classmethod
    def _get_cached_token_count(cls, entry: dict, *, writeback: bool = True) -> int:
        """Sync cache-aware token count. Writes `token_count`,
        `token_count_text_sha256` and `token_count_tokenizer` back to
        `entry` on miss when `writeback=True` (the default, for persona
        entries that live in the `_personas` in-memory view and therefore
        benefit from across-render cache reuse).

        Callers should pass `writeback=False` for entries that do not have
        a process-resident view (currently: reflection entries, which are
        always loaded fresh from disk via `aload_reflections`). In that
        mode we still short-circuit on a pre-existing cache hit — that's
        free — but we never pollute the entry dict with fields that
        wouldn't survive the next render anyway.

        Cache hit requires BOTH fingerprints to match:
        - text sha256 (catches text mutation)
        - tokenizer identity (catches tiktoken↔heuristic transition;
          see `utils.tokenize.tokenizer_identity` docstring for the
          motivating scenario — packaging without encoding data file).

        Additionally, `token_count` must coerce cleanly to a non-negative
        int. A hand-edited or corrupted `persona.json` could plant a
        non-numeric or negative value with fingerprints that still happen
        to match (or match after someone also hand-rewrote the sha256
        field) — in which case `int(...)` on the cached value would
        either raise or return garbage and bomb the render. On coercion
        failure we treat it as a cache miss and recompute.
        """
        text = entry.get('text', '') or ''
        if not text:
            return 0
        fp = cls._text_fingerprint(text)
        tid = tokenizer_identity()
        cached_count = cls._coerce_cached_count(entry.get('token_count'))
        if (
            cached_count is not None
            and entry.get('token_count_text_sha256') == fp
            and entry.get('token_count_tokenizer') == tid
        ):
            return cached_count
        n = count_tokens(text)
        if writeback:
            entry['token_count'] = int(n)
            entry['token_count_text_sha256'] = fp
            entry['token_count_tokenizer'] = tid
        return int(n)

    @classmethod
    async def _aget_cached_token_count(cls, entry: dict, *, writeback: bool = True) -> int:
        """Async twin — uses `acount_tokens` (worker-thread tiktoken).
        Write-back semantics match the sync helper (both fingerprints).
        See `_get_cached_token_count` for the `writeback=False` contract
        (used by reflection render path, which has no in-memory view),
        and for the defensive coercion of poisoned `token_count` values
        from a hand-edited or corrupted `persona.json`."""
        text = entry.get('text', '') or ''
        if not text:
            return 0
        fp = cls._text_fingerprint(text)
        tid = tokenizer_identity()
        cached_count = cls._coerce_cached_count(entry.get('token_count'))
        if (
            cached_count is not None
            and entry.get('token_count_text_sha256') == fp
            and entry.get('token_count_tokenizer') == tid
        ):
            return cached_count
        n = await acount_tokens(text)
        if writeback:
            entry['token_count'] = int(n)
            entry['token_count_text_sha256'] = fp
            entry['token_count_tokenizer'] = tid
        return int(n)

    @staticmethod
    def _coerce_cached_count(raw) -> int | None:
        """Validate a `token_count` value loaded from an entry dict.

        Returns the non-negative int when `raw` is coercible and sane;
        returns None (→ force a cache miss) when `raw` is missing,
        non-numeric, a bool, a non-integer float (1.9 would silently
        truncate to 1), `inf` / `nan` (`int(inf)` raises
        `OverflowError`), or negative.

        `bool` is a subclass of `int` in Python, so the explicit
        `isinstance(raw, bool)` reject keeps us from accepting `True`/
        `False` as legitimate cached counts if persona.json was hand-
        edited with boolean-looking garbage."""
        if raw is None or isinstance(raw, bool):
            return None
        if isinstance(raw, float):
            if not raw.is_integer():
                return None
            if raw < 0:
                return None
            return int(raw)
        try:
            value = int(raw)
        except (TypeError, ValueError, OverflowError):
            return None
        if value < 0:
            return None
        return value

    @staticmethod
    def _invalidate_token_count_cache(entry: dict) -> None:
        """Explicitly drop the cached count. Called by code paths that
        rewrite `entry['text']` (e.g. `amerge_into`) to avoid the tiny
        window where a concurrent reader sees new text + stale count.
        The fingerprint check would catch it anyway, but explicit
        invalidation is clearer and saves one sha256 compute on the
        next render."""
        entry['token_count'] = None
        entry['token_count_text_sha256'] = None
        entry['token_count_tokenizer'] = None

    @classmethod
    def _score_trim_entries(
        cls, entries: list, budget: int, now: datetime,
        *, cache_writeback: bool = True,
    ) -> list:
        """Sync score-trim: sort by (evidence_score, importance) DESC, keep
        entries whose accumulated `count_tokens(text)` ≤ `budget`. Stops at
        the first entry that would push past the cap (lower-score remainder
        is dropped — see §3.6.3).

        `entries` is a list of dicts (no entity tagging — caller sorts/keys
        as needed). Returns the kept subset preserving the score-DESC order.

        `cache_writeback`: default True writes `token_count` fields back
        onto each entry for across-render reuse (persona path — entries
        live in `_personas`). Pass False for reflection entries, which are
        loaded fresh from disk every render and would have no persistent
        view to cache against; writing cache fields there would be
        misleading and pollute reflection.json on the next save.
        """
        sorted_entries = sorted(
            entries,
            key=lambda e: (
                evidence_score(e, now),
                float(e.get('importance', 0) or 0),
            ),
            reverse=True,
        )
        kept = []
        total = 0
        for e in sorted_entries:
            t = cls._get_cached_token_count(e, writeback=cache_writeback)
            if total + t > budget:
                break
            kept.append(e)
            total += t
        return kept

    @classmethod
    async def _ascore_trim_entries(
        cls, entries: list, budget: int, now: datetime,
        *, cache_writeback: bool = True,
    ) -> list:
        """Async twin of `_score_trim_entries`. Identical math; the only
        difference is `acount_tokens` (worker-thread tiktoken). See the
        sync twin for the `cache_writeback` contract."""
        sorted_entries = sorted(
            entries,
            key=lambda e: (
                evidence_score(e, now),
                float(e.get('importance', 0) or 0),
            ),
            reverse=True,
        )
        kept = []
        total = 0
        for e in sorted_entries:
            t = await cls._aget_cached_token_count(e, writeback=cache_writeback)
            if total + t > budget:
                break
            kept.append(e)
            total += t
        return kept

    def _split_persona_for_render(
        self, persona: dict,
    ) -> tuple[list[tuple[str, dict]], dict[str, list[dict]]]:
        """Phase 1 (RFC §3.6.2): split entries into:
          - `protected_entries`: list[(entity_key, entry)] — character_card
            sources, never trimmed (§3.5.7 + §3.6.1).
          - `non_protected_by_entity`: {entity_key: [entry, ...]} — the
            score-trim candidate pool (suppressed entries excluded; they go
            to the dedicated "暂不主动提及" section in compose).
        """
        protected_entries: list[tuple[str, dict]] = []
        non_protected_by_entity: dict[str, list[dict]] = defaultdict(list)
        for entity_key, section in persona.items():
            if not isinstance(section, dict):
                continue
            for entry in section.get('facts', []):
                if not isinstance(entry, dict):
                    # Pre-PR-1 schema sometimes stored facts as bare
                    # strings; the legacy render path (`_render_fact_entries`)
                    # used to emit them. Normalize ad-hoc here so they keep
                    # appearing in prompt context until a write touches the
                    # entry and migrates it to dict form via _normalize_entry.
                    if entry:
                        entry = {
                            'text': str(entry),
                            'protected': False,
                            'suppress': False,
                            'reinforcement': 0.0,
                            'disputation': 0.0,
                            'rein_last_signal_at': None,
                            'disp_last_signal_at': None,
                            'sub_zero_days': 0,
                            'user_fact_reinforce_count': 0,
                        }
                        non_protected_by_entity[entity_key].append(entry)
                    continue
                if entry.get('suppress'):
                    # Suppressed entries are rendered in their own section
                    # (compose phase) — they don't compete with protected/
                    # non-protected for budget.
                    continue
                if entry.get('protected'):
                    protected_entries.append((entity_key, entry))
                else:
                    non_protected_by_entity[entity_key].append(entry)
        return protected_entries, dict(non_protected_by_entity)

    @staticmethod
    def _filter_reflections_for_render(
        reflections: list[dict] | None, persona: dict,
        suppressed_text_set: set[str],
    ) -> list[dict]:
        """Drop reflections whose text matches a suppressed persona entry
        (existing semantic — see `_is_suppressed_text` callers below)."""
        if not reflections:
            return []
        out = []
        for r in reflections:
            if not isinstance(r, dict):
                continue
            text = r.get('text', '')
            if not text:
                continue
            if text in suppressed_text_set:
                continue
            out.append(r)
        return out

    def _compose_markdown_from_trimmed(
        self, name: str, persona: dict, name_mapping: dict,
        protected_entries: list[tuple[str, dict]],
        trimmed_non_protected: list[dict],
        non_protected_entity_index: dict[int, str],
        trimmed_pending_reflections: list[dict],
        trimmed_confirmed_reflections: list[dict],
    ) -> str:
        """Phase 3 (RFC §3.6.2): emit markdown sections in stable order.

        Headers: `关于主人` / `关于{ai_name}` / `关系动态` / 反思两类 / 抑制区.
        Within each entity section: protected entries first (deterministic
        order from persona file) then non-protected kept by score-trim,
        preserving the trim-order (which is score DESC).
        """
        master_name = name_mapping.get('human', '主人')
        ai_name = name
        _headers = {
            'master': f"关于{master_name}",
            'neko': f"关于{ai_name}",
            'relationship': "关系动态",
        }

        # Suppressed entries always render (small + the whole point is "AI
        # remembers but won't volunteer it"); not budget-counted.
        suppressed_lines: list[str] = []
        for entry in self._collect_all_entries(persona):
            if isinstance(entry, dict) and entry.get('suppress'):
                text = entry.get('text', '')
                if text:
                    suppressed_lines.append(f"- {text}")

        # Group kept entries by entity_key so each section is contiguous.
        # `non_protected_entity_index[id(entry)]` was populated by caller
        # to remember which entity each non-protected entry came from
        # (score-trim sorts globally so we lose that info).
        per_entity: dict[str, list[dict]] = defaultdict(list)
        for ek, entry in protected_entries:
            per_entity[ek].append(entry)
        for entry in trimmed_non_protected:
            ek = non_protected_entity_index.get(id(entry))
            if ek:
                per_entity[ek].append(entry)

        sections: list[str] = []
        # Iterate persona's natural key order so output is stable
        # regardless of which entries got trimmed.
        for entity_key in persona.keys():
            entries = per_entity.get(entity_key)
            if not entries:
                continue
            lines = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                text = entry.get('text', '')
                if text:
                    lines.append(f"- {text}")
            if lines:
                header = _headers.get(entity_key, entity_key)
                sections.append(f"### {header}\n" + "\n".join(lines))

        if trimmed_pending_reflections:
            lines = [f"- {r.get('text', '')}" for r in trimmed_pending_reflections
                     if r.get('text')]
            if lines:
                sections.append(
                    f"### {ai_name}最近的印象（还不太确定）\n" + "\n".join(lines)
                )

        if trimmed_confirmed_reflections:
            lines = [f"- {r.get('text', '')}" for r in trimmed_confirmed_reflections
                     if r.get('text')]
            if lines:
                sections.append(
                    f"### {ai_name}比较确定的印象\n" + "\n".join(lines)
                )

        if suppressed_lines:
            sections.append(
                f"### 暂不主动提及的内容（{ai_name}记得，但最近提到太多次了，不要再主动提起）\n"
                + "\n".join(suppressed_lines)
            )

        return "\n\n".join(sections) if sections else ""

    def _suppressed_text_set(self, persona: dict) -> set[str]:
        out: set[str] = set()
        for entry in self._collect_all_entries(persona):
            if isinstance(entry, dict) and entry.get('suppress'):
                t = entry.get('text', '')
                if t:
                    out.add(t)
        return out

    def _compose_persona_markdown(
        self, name: str, persona: dict, name_mapping: dict,
        pending_reflections: list[dict] | None,
        confirmed_reflections: list[dict] | None,
    ) -> str:
        """Sync 3-phase render path. Used by `render_persona_markdown` and
        any test/migration caller that doesn't have an event loop."""
        now = datetime.now()

        protected_entries, non_protected_by_entity = (
            self._split_persona_for_render(persona)
        )

        # Build entity-index by id() so we can regroup after the (entity-
        # blind) score-trim. Using id() is safe because we never mutate
        # entries during render — they're the same objects throughout.
        non_protected_entity_index: dict[int, str] = {}
        flat_non_protected: list[dict] = []
        for ek, entries in non_protected_by_entity.items():
            for e in entries:
                non_protected_entity_index[id(e)] = ek
                flat_non_protected.append(e)

        trimmed_non_protected = self._score_trim_entries(
            flat_non_protected, PERSONA_RENDER_TOKEN_BUDGET, now,
        )

        suppressed_text_set = self._suppressed_text_set(persona)
        trimmed_reflections_combined = self._score_trim_entries(
            self._filter_reflections_for_render(
                (pending_reflections or []) + (confirmed_reflections or []),
                persona, suppressed_text_set,
            ),
            REFLECTION_RENDER_TOKEN_BUDGET, now,
            # Reflections have no `_personas`-style in-memory view — they're
            # always loaded fresh from disk. Writing cache fields onto the
            # transient dicts would be garbage-collected on render exit and
            # could only pollute reflection.json on the next save.
            cache_writeback=False,
        )
        # Preserve the score-DESC order produced by _score_trim_entries.
        # The previous implementation filtered the ORIGINAL source lists by
        # id-membership in `trimmed_reflections_combined`, which lost the
        # sort order and emitted reflections in caller-supplied order. Fix:
        # iterate the already-sorted `trimmed_reflections_combined` and
        # split back into pending/confirmed by source-list membership
        # (CodeRabbit PR #936 round-4 Minor).
        trimmed_pending, trimmed_confirmed = self._partition_trimmed_reflections(
            trimmed_reflections_combined, pending_reflections, suppressed_text_set,
        )

        return self._compose_markdown_from_trimmed(
            name, persona, name_mapping,
            protected_entries, trimmed_non_protected,
            non_protected_entity_index,
            trimmed_pending, trimmed_confirmed,
        )

    @staticmethod
    def _partition_trimmed_reflections(
        trimmed_combined: list[dict],
        pending_source: list[dict] | None,
        suppressed_text_set: set[str],
    ) -> tuple[list[dict], list[dict]]:
        """Split score-sorted combined trim output back into
        (pending, confirmed) while preserving the sort order.

        Membership in `pending_source` decides pending vs confirmed; all
        entries not in `pending_source` are treated as confirmed (matches
        the original construction where the combined list was
        `pending + confirmed`). Suppressed entries are dropped defensively
        (the trim input already filtered them, but keep the guard so the
        render output never leaks suppressed text).
        """
        pending_ids = {id(r) for r in (pending_source or [])}
        trimmed_pending: list[dict] = []
        trimmed_confirmed: list[dict] = []
        for r in trimmed_combined:
            if r.get('text') in suppressed_text_set:
                continue
            if id(r) in pending_ids:
                trimmed_pending.append(r)
            else:
                trimmed_confirmed.append(r)
        return trimmed_pending, trimmed_confirmed

    def render_persona_markdown(self, name: str, pending_reflections: list[dict] | None = None,
                                   confirmed_reflections: list[dict] | None = None) -> str:
        """Render persona as markdown for LLM context injection.

        Suppressed entries are rendered in a separate "暂不主动提及" section,
        NOT in their original sections. suppress has highest priority.
        """
        # Refresh suppressions before rendering so expired cooldowns are released
        self.update_suppressions(name)
        persona = self.ensure_persona(name)
        _, _, _, _, name_mapping, _, _, _, _ = self._config_manager.get_character_data()
        return self._compose_persona_markdown(
            name, persona, name_mapping, pending_reflections, confirmed_reflections,
        )

    async def arender_persona_markdown(
        self, name: str,
        pending_reflections: list[dict] | None = None,
        confirmed_reflections: list[dict] | None = None,
    ) -> str:
        """Async 3-phase render path. Production hot path — uses
        `acount_tokens` so the event loop doesn't stall on tiktoken IO."""
        await self.aupdate_suppressions(name)
        persona = await self.aensure_persona(name)
        _, _, _, _, name_mapping, _, _, _, _ = await self._config_manager.aget_character_data()
        now = datetime.now()

        protected_entries, non_protected_by_entity = (
            self._split_persona_for_render(persona)
        )

        non_protected_entity_index: dict[int, str] = {}
        flat_non_protected: list[dict] = []
        for ek, entries in non_protected_by_entity.items():
            for e in entries:
                non_protected_entity_index[id(e)] = ek
                flat_non_protected.append(e)

        trimmed_non_protected = await self._ascore_trim_entries(
            flat_non_protected, PERSONA_RENDER_TOKEN_BUDGET, now,
        )

        suppressed_text_set = self._suppressed_text_set(persona)
        trimmed_reflections_combined = await self._ascore_trim_entries(
            self._filter_reflections_for_render(
                (pending_reflections or []) + (confirmed_reflections or []),
                persona, suppressed_text_set,
            ),
            REFLECTION_RENDER_TOKEN_BUDGET, now,
            # See sync twin: reflections have no `_personas`-style
            # in-memory view, so we compute fresh every render without
            # writing cache fields back onto the transient dicts.
            cache_writeback=False,
        )
        # Preserve score-DESC order from _ascore_trim_entries — mirror of
        # the sync path fix in _compose_persona_markdown (CodeRabbit PR
        # #936 round-4 Minor).
        trimmed_pending, trimmed_confirmed = self._partition_trimmed_reflections(
            trimmed_reflections_combined, pending_reflections, suppressed_text_set,
        )

        return self._compose_markdown_from_trimmed(
            name, persona, name_mapping,
            protected_entries, trimmed_non_protected,
            non_protected_entity_index,
            trimmed_pending, trimmed_confirmed,
        )

    def _is_suppressed_text(self, persona: dict, text: str) -> bool:
        """Check if a given text matches any suppressed entry."""
        for entry in self._collect_all_entries(persona):
            if isinstance(entry, dict) and entry.get('suppress') and entry.get('text') == text:
                return True
        return False

    @staticmethod
    def _render_fact_entries(entries: list) -> list[str]:
        """渲染 fact 条目列表。suppress 的条目不在此渲染（移至专用区域）。"""
        lines = []
        for entry in entries:
            if isinstance(entry, dict):
                if entry.get('suppress'):
                    continue  # suppress 的条目在专用区域渲染
                text = entry.get('text', '')
                if text:
                    lines.append(f"- {text}")
            elif entry:
                lines.append(f"- {entry}")
        return lines
