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

"""Character configuration mixin.

characters.json load/save (with mtime cache and reserved-field migration)
and the aggregated character data snapshot used by the runtime.
"""
import asyncio
import json
import os
from copy import deepcopy

from utils.file_utils import atomic_write_json

from ._shared import logger
from .persona_payload import (
    _append_persona_guidance_to_prompt,
    _build_effective_character_payload,
    _resolve_effective_character_prompt,
)
from .reserved_schema import migrate_catgirl_reserved, validate_reserved_schema


class CharactersMixin:
    """characters.json access and aggregated character data."""

    # --- Character configuration helpers ---

    def get_default_characters(self):
        """Get default character config data (content values localized per Steam language)"""
        from config import get_localized_default_characters
        return get_localized_default_characters()

    def load_characters(self, character_json_path=None):
        """Load character configs"""
        use_default_path = character_json_path is None
        if character_json_path is None:
            character_json_path = str(self.get_config_path('characters.json'))

        with self._characters_cache_lock:
            cache = self._characters_cache
            cache_path = self._characters_cache_path
            cache_mtime = self._characters_cache_mtime
        if cache is not None and cache_path == character_json_path:
            try:
                current_mtime = os.path.getmtime(character_json_path)
            except OSError:
                current_mtime = None
            if current_mtime is not None and current_mtime == cache_mtime:
                return deepcopy(cache)

        # 慢路径：独占锁，防止多个线程同时读文件、重复触发迁移和校验警告。
        with self._characters_reload_lock:
            # 双检：进锁后重新核对 mtime，另一个线程可能已经完成了加载。
            with self._characters_cache_lock:
                cache = self._characters_cache
                cache_path = self._characters_cache_path
                cache_mtime = self._characters_cache_mtime
            if cache is not None and cache_path == character_json_path:
                try:
                    current_mtime = os.path.getmtime(character_json_path)
                except OSError:
                    current_mtime = None
                if current_mtime is not None and current_mtime == cache_mtime:
                    return deepcopy(cache)

            try:
                with open(character_json_path, 'r', encoding='utf-8') as f:
                    character_data = json.load(f)
                try:
                    loaded_mtime = os.path.getmtime(character_json_path)
                except OSError:
                    loaded_mtime = None
            except FileNotFoundError:
                logger.info("未找到猫娘配置文件 %s，使用默认配置。", character_json_path)
                character_data = self.get_default_characters()
                loaded_mtime = None
            except Exception as e:
                logger.error("读取猫娘配置文件出错: %s，使用默认人设。", e)
                character_data = self.get_default_characters()
                loaded_mtime = None

            migrated = False
            if not isinstance(character_data, dict):
                logger.warning("角色配置文件结构异常（非 dict），使用默认配置。")
                character_data = self.get_default_characters()
            catgirl_map = character_data.get("猫娘")
            if isinstance(catgirl_map, dict):
                all_schema_errors: list[str] = []
                for name, catgirl_data in catgirl_map.items():
                    if not isinstance(catgirl_data, dict):
                        logger.warning("角色 '%s' 配置非 dict，跳过迁移。", name)
                        continue
                    if migrate_catgirl_reserved(catgirl_data):
                        migrated = True
                    reserved_errors = validate_reserved_schema(catgirl_data.get("_reserved"))
                    for err in reserved_errors:
                        all_schema_errors.append(f"{name}: {err}")
                if all_schema_errors:
                    logger.warning("检测到角色 _reserved 字段结构异常: %s", "; ".join(all_schema_errors))
            if migrated:
                try:
                    self.save_characters(character_data, character_json_path=character_json_path)
                    logger.info("检测到旧版角色保留字段，已自动迁移到 _reserved 结构。")
                except Exception as migrate_err:
                    # 维护态（只读快照阶段）不能持久化，降级为 debug 日志
                    try:
                        from utils.cloudsave_runtime import MaintenanceModeError
                    except Exception:
                        MaintenanceModeError = None
                    if MaintenanceModeError is not None and isinstance(migrate_err, MaintenanceModeError):
                        logger.debug("角色保留字段迁移在只读阶段跳过持久化: %s", migrate_err)
                    else:
                        logger.warning("自动迁移角色保留字段后写回失败: %s", migrate_err)
            else:
                with self._characters_cache_lock:
                    self._characters_cache = deepcopy(character_data)
                    self._characters_cache_mtime = loaded_mtime
                    self._characters_cache_path = character_json_path
                    self._characters_dirty = False
            return character_data

    def save_characters(self, data, character_json_path=None, *, bypass_write_fence: bool = False):
        """Save character configs (sync version, blocks the event loop; use asave_characters on async paths)"""
        if character_json_path is None:
            character_json_path = str(self.get_runtime_config_path('characters.json'))

        if not bypass_write_fence:
            from utils.cloudsave_runtime import assert_cloudsave_writable

            assert_cloudsave_writable(self, operation="save", target="characters.json")

        # 确保config目录存在
        self.ensure_config_directory()

        atomic_write_json(character_json_path, data, ensure_ascii=False, indent=2)
        try:
            new_mtime = os.path.getmtime(character_json_path)
        except OSError:
            new_mtime = None
        with self._characters_cache_lock:
            self._characters_cache = deepcopy(data)
            self._characters_cache_mtime = new_mtime
            self._characters_cache_path = character_json_path
            self._characters_dirty = False

    async def asave_characters(self, data, character_json_path=None, *, bypass_write_fence: bool = False):
        """Async wrapper: the sync version must not run directly on the event loop (atomic_write_json blocks)."""
        return await asyncio.to_thread(
            self.save_characters,
            data,
            character_json_path,
            bypass_write_fence=bypass_write_fence,
        )

    # --- Character metadata helpers ---

    def get_character_data(self):
        """Get character base data and related paths"""
        character_data = self.load_characters()
        defaults = self.get_default_characters()

        character_data.setdefault('主人', deepcopy(defaults['主人']))
        character_data.setdefault('猫娘', deepcopy(defaults['猫娘']))

        master_basic_config = _build_effective_character_payload(character_data.get('主人', {}), entity="master")
        master_name = master_basic_config.get('档案名', defaults['主人']['档案名'])

        raw_character_data = character_data.get('猫娘') or deepcopy(defaults['猫娘'])
        catgirl_names = list(raw_character_data.keys())

        current_catgirl = character_data.get('当前猫娘', '')
        if current_catgirl and current_catgirl in catgirl_names:
            her_name = current_catgirl
        else:
            her_name = catgirl_names[0] if catgirl_names else ''
            if her_name and current_catgirl != her_name:
                logger.info(
                    "当前猫娘配置无效 ('%s')，已自动切换到 '%s'",
                    current_catgirl,
                    her_name,
                )
                character_data['当前猫娘'] = her_name
                # 罕见分支（仅配置损坏/删除猫娘后触发），同步落盘以保证重启后修正仍生效。
                # save_characters 内部会刷新 cache，这里无需再手动同步。
                try:
                    self.save_characters(character_data)
                except Exception as persist_err:
                    logger.warning("自动纠正当前猫娘后写回失败，将仅保留内存修正: %s", persist_err)
                    with self._characters_cache_lock:
                        if self._characters_cache is not None:
                            self._characters_cache['当前猫娘'] = her_name
                        self._characters_dirty = True

        name_mapping = {'human': master_name, 'system': "SYSTEM_MESSAGE"}
        effective_character_data = {
            name: _build_effective_character_payload(raw_character_data.get(name, {}))
            for name in catgirl_names
        }
        lanlan_prompt_map = {}
        for name in catgirl_names:
            prompt_value = _resolve_effective_character_prompt(raw_character_data.get(name, {}))
            lanlan_prompt_map[name] = _append_persona_guidance_to_prompt(
                prompt_value,
                raw_character_data.get(name, {}),
            )

        memory_base = str(self.memory_dir)
        # 角色专属子目录: memory_dir/{name}/
        import os as _os
        time_store = {name: _os.path.join(memory_base, name, 'time_indexed.db') for name in catgirl_names}
        setting_store = {name: _os.path.join(memory_base, name, 'settings.json') for name in catgirl_names}
        recent_log = {name: _os.path.join(memory_base, name, 'recent.json') for name in catgirl_names}

        return (
            master_name,
            her_name,
            master_basic_config,
            effective_character_data,
            name_mapping,
            lanlan_prompt_map,
            time_store,
            setting_store,
            recent_log,
        )

    async def aget_character_data(self):
        return await asyncio.to_thread(self.get_character_data)

    async def aload_characters(self, character_json_path=None):
        """Async wrapper for load_characters: even a cache hit deepcopies the whole dict;
        with N catgirls the copy can take several ms — offload to avoid blocking the event loop."""
        return await asyncio.to_thread(self.load_characters, character_json_path)
