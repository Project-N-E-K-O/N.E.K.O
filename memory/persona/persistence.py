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
"""Persistence methods for the memory manager."""

from __future__ import annotations

import asyncio

import hashlib

import json

import os









from utils.cloudsave_runtime import assert_cloudsave_writable


from utils.file_utils import (
    atomic_write_json,
    atomic_write_json_async,
    read_json_async,
)



from ._shared import (
    logger,
)

class PersistenceMixin:
    def _persona_path(self, name: str) -> str:
        from memory import ensure_character_dir
        return os.path.join(ensure_character_dir(self._config_manager.memory_dir, name), 'persona.json')

    def _corrections_path(self, name: str) -> str:
        from memory import ensure_character_dir
        return os.path.join(ensure_character_dir(self._config_manager.memory_dir, name), 'persona_corrections.json')

    def _persona_archive_dir(self, name: str) -> str:
        """Sharded archive directory for persona entries (RFC §3.5.4).

        New in PR-2 — persona had no archival before this RFC, so there
        is no legacy flat file to migrate (RFC §3.5.5, last paragraph).
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

        Every call automatically syncs the character_card entries with characters.json.
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
        """Thread-safe wrapper. The two branches that `asave_persona()` to disk —
        first creation and a character-card change — must run under the
        per-character lock; otherwise they race against locked write paths like
        `aadd_fact` / `arecord_mentions` / `aupdate_suppressions` /
        `_resolve_corrections_locked`, and a freshly persisted new fact can be
        clobbered by the lock-free ensure/sync-card branch.

        Call sites already holding `_get_alock(name)` (e.g. inside aadd_fact)
        must use `_aensure_persona_locked` instead, to avoid the deadlock from
        the non-reentrant asyncio.Lock."""
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
        """Generate a deterministic ID for a character-card entry (hash of entity + field_name)."""
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

        Migrates settings.json only (settings the LLM extracted from
        conversations). Character-card data (characters.json) is synced solely
        by _sync_character_card(); this method no longer touches the card,
        avoiding duplicate entries from two write paths.
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
            """Build the expected (id, entry) list from card fields, preserving card field order."""
            expected = []
            for k, v in card_data.items():
                if k in excluded_fields or not _is_syncable(v):
                    continue
                if isinstance(v, (dict, set, tuple)):
                    continue
                if isinstance(v, list):
                    v = '、'.join(str(item) for item in v)
                entry_id = self._card_entry_id(entity, k)
                if str(k).startswith("__ai_context."):
                    # 合成运行时上下文字段（如 __ai_context.profile_rename_events）：
                    # value 已是自带本地化标签的完整句子，不能再前缀内部键名，
                    # 否则裸键 "__ai_context.xxx: ..." 会原样泄漏进模型读到的 fact。
                    # 用带点的前缀精确匹配约定命名，避免误伤 __ai_contextual_* 这类普通键。
                    text = str(v)
                else:
                    text = f"{k}: {v}"
                expected.append((entry_id, text))
            return expected

        def _sync_entity(entity: str, card_data: dict) -> bool:
            """Sync a single entity section. Returns whether anything changed."""
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
                        # Same logic for the embedding cache: a stale
                        # vector under the old text would slip into
                        # cosine-based retrieval matches.
                        self._invalidate_embedding_cache(entry)
                        modified = True
                        # persona 文本不写 logger
                        logger.info(f"[Persona] {name}: card 同步更新 [{entity}] (old_len={len(old_text)} new_len={len(text)})")
                        print(f"[Persona] {name}: card 同步更新 [{entity}] \"{old_text[:30]}\" → \"{text[:30]}\"")
                    new_card_entries.append(entry)
                else:
                    # 新字段 → 创建
                    entry = self._normalize_entry(text)
                    entry['id'] = eid
                    entry['source'] = 'character_card'
                    entry['protected'] = True
                    new_card_entries.append(entry)
                    modified = True
                    logger.info(f"[Persona] {name}: card 同步新增 [{entity}] (len={len(text)})")
                    print(f"[Persona] {name}: card 同步新增 [{entity}] \"{text[:40]}\"")

            # 检查是否有 card 中已删除的条目
            removed_ids = set(existing_card.keys()) - expected_ids
            if removed_ids:
                modified = True
                for rid in removed_ids:
                    removed_text = existing_card[rid].get('text', '')
                    logger.info(f"[Persona] {name}: card 同步移除 [{entity}] (len={len(removed_text)})")
                    print(f"[Persona] {name}: card 同步移除 [{entity}] \"{removed_text[:40]}\"")


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
        """Sync character-card entries into the persona head, keeping the order consistent with characters.json.

        Rules:
        1. Read the current characters.json neko/master fields
        2. Generate a deterministic ID for each field (card_{entity}_{hash})
        3. Compare against persona entries with source=='character_card'
        4. Update changed texts, add missing ones, delete those removed from the card
        5. Card entries always sit at the head of the facts list, ordered as in the card

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
