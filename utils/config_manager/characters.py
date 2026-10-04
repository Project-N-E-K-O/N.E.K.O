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
import time
from copy import deepcopy

from utils.file_utils import atomic_write_json

from ._shared import logger
from .persona_payload import (
    _append_persona_guidance_to_prompt,
    _build_effective_character_payload,
    _resolve_effective_character_prompt,
)
from .reserved_schema import (
    ensure_catgirl_character_id,
    ensure_character_uids,
    get_reserved,
    migrate_catgirl_reserved,
    normalize_character_id,
    set_reserved,
    validate_reserved_schema,
)


# A migration whose write-back failed (cloud-save maintenance fence, read-only
# disk) leaves the cache dirty. Retrying the write on every read would cost a
# reload lock, a deepcopy and a rejected save per request for the whole outage,
# so retries back off from the base delay up to the cap.
_CHARACTERS_DIRTY_RETRY_BASE_SECONDS = 30.0
_CHARACTERS_DIRTY_RETRY_MAX_SECONDS = 300.0


def _characters_file_signature(path):
    """Return the cache-validation signature of a characters file.

    A float mtime alone misses a rewrite that lands in the same timestamp tick
    (coarse on Windows), so the cache would keep serving the previous content.
    The nanosecond mtime plus the size catches every rewrite that changes the
    length and narrows same-length collisions to the filesystem's real
    resolution. One ``os.stat`` runs on every cached read; its errors
    (``FileNotFoundError`` for a missing source, another ``OSError`` for an
    unreadable one) drive the dirty-identity fallbacks.
    """
    stat_result = os.stat(path)
    return (stat_result.st_mtime_ns, stat_result.st_size)


def _catgirl_character_ids(characters) -> dict[str, str]:
    """Map each catgirl name to its valid ``character_id`` in a characters payload."""
    catgirl_map = characters.get("猫娘") if isinstance(characters, dict) else None
    if not isinstance(catgirl_map, dict):
        return {}
    character_ids: dict[str, str] = {}
    for name, catgirl_data in catgirl_map.items():
        character_id = normalize_character_id(
            get_reserved(catgirl_data, "character_id", default="")
        )
        if character_id:
            character_ids[name] = character_id
    return character_ids


class CharactersMixin:
    """characters.json access and aggregated character data."""

    # Monotonic time before which a dirty cache is served without retrying its
    # write-back, and the delay that produced it (0 when no failure is pending).
    _characters_dirty_retry_at: float | None = None
    _characters_dirty_retry_delay: float = 0.0

    def _characters_dirty_retry_due_locked(self) -> bool:
        """Whether a dirty cache may retry its write now (hold the cache lock)."""
        retry_at = self._characters_dirty_retry_at
        return retry_at is None or time.monotonic() >= retry_at

    def _note_characters_persist_failure_locked(self) -> None:
        """Back off the next dirty-cache write retry (hold the cache lock)."""
        delay = self._characters_dirty_retry_delay
        delay = (
            _CHARACTERS_DIRTY_RETRY_BASE_SECONDS
            if delay <= 0
            else min(delay * 2, _CHARACTERS_DIRTY_RETRY_MAX_SECONDS)
        )
        self._characters_dirty_retry_delay = delay
        self._characters_dirty_retry_at = time.monotonic() + delay

    def _clear_characters_persist_backoff_locked(self) -> None:
        """Forget any pending write-back backoff (hold the cache lock)."""
        self._characters_dirty_retry_at = None
        self._characters_dirty_retry_delay = 0.0

    # --- Character configuration helpers ---

    def get_default_characters(self):
        """Get default character config data (content values localized per Steam language)"""
        from config import get_localized_default_characters
        return get_localized_default_characters()

    def load_character_binding_snapshot(self, catgirl_name=None):
        """Verify only the selected persisted identity, without auditing other cards."""
        snapshot = self.load_characters()
        selected = str(catgirl_name or snapshot.get("当前猫娘") or "").strip()
        with open(self.get_config_path('characters.json'), 'r', encoding='utf-8') as stream:
            persisted = json.load(stream)
        profiles = persisted.get("猫娘") if isinstance(persisted, dict) else None
        profile = profiles.get(selected) if isinstance(profiles, dict) else None
        if not isinstance(profile, dict) or not normalize_character_id(get_reserved(profile, "character_id", default="")):
            raise ValueError("current_catgirl_identity_unavailable")
        # Use the persisted card for both its identity and its personality hash.
        snapshot["猫娘"][selected] = profile
        return snapshot

    def load_characters(self, character_json_path=None, *, require_authoritative=False):
        """Load profiles; authoritative callers reject fallbacks and unpersisted IDs."""
        # Migration results are written back to the file they came from, except
        # when the default lookup fell back to a project/seed copy (the runtime
        # copy is missing): those must land in the runtime config path instead,
        # never in the seed, which is source-controlled or read-only when frozen.
        persist_json_path = character_json_path
        if character_json_path is None:
            character_json_path = str(self.get_config_path('characters.json'))
            runtime_json_path = str(self.get_runtime_config_path('characters.json'))
            persist_json_path = (
                character_json_path
                if os.path.normcase(os.path.abspath(character_json_path))
                == os.path.normcase(os.path.abspath(runtime_json_path))
                else runtime_json_path
            )

        with self._characters_cache_lock:
            cache = self._characters_cache
            cache_path = self._characters_cache_path
            cache_mtime = self._characters_cache_mtime
            cache_dirty = self._characters_dirty
            dirty_retry_due = self._characters_dirty_retry_due_locked()
        if cache is not None and cache_path == character_json_path:
            try:
                current_mtime = _characters_file_signature(character_json_path)
            except OSError:
                current_mtime = None
            if current_mtime == cache_mtime and (
                (not cache_dirty and current_mtime is not None)
                # A dirty cache whose write-back just failed is served as is
                # until the backoff expires; a changed file or an authoritative
                # caller still takes the slow path and retries right away.
                or (cache_dirty and not require_authoritative and not dirty_retry_due)
            ):
                return deepcopy(cache)

        # 慢路径：独占锁，防止多个线程同时读文件、重复触发迁移和校验警告。
        with self._characters_reload_lock:
            # 双检：进锁后重新核对 mtime，另一个线程可能已经完成了加载。
            with self._characters_cache_lock:
                cache = self._characters_cache
                cache_path = self._characters_cache_path
                cache_mtime = self._characters_cache_mtime
                cache_dirty = self._characters_dirty
            if cache is not None and cache_path == character_json_path:
                source_missing = False
                try:
                    current_mtime = _characters_file_signature(character_json_path)
                except FileNotFoundError:
                    current_mtime = None
                    source_missing = True
                except OSError:
                    current_mtime = None
                if cache_dirty and current_mtime is None and not (
                    source_missing and cache_mtime is None
                ):
                    # An unreadable source cannot supersede an unpersisted identity.
                    # Only a never-persisted default may retry creating a missing file.
                    if require_authoritative:
                        raise ValueError("character_config_not_authoritative")
                    return deepcopy(cache)
                if current_mtime == cache_mtime and (
                    current_mtime is not None or cache_dirty
                ):
                    if not cache_dirty:
                        return deepcopy(cache)
                    if not require_authoritative:
                        # Another thread may have just failed the same retry.
                        with self._characters_cache_lock:
                            dirty_retry_due = self._characters_dirty_retry_due_locked()
                        if not dirty_retry_due:
                            return deepcopy(cache)
                    # 上次迁移已生成稳定角色 ID，但被维护栅栏或暂时性 I/O 阻止写回；
                    # 每次恢复可写后都先重试持久化，再把该身份交给后续持久化业务使用。
                    dirty_cache = deepcopy(cache)
                    try:
                        self.save_characters(
                            dirty_cache,
                            character_json_path=persist_json_path,
                        )
                        logger.info("已补写此前未持久化的角色保留字段迁移。")
                    except Exception as persist_err:
                        with self._characters_cache_lock:
                            self._note_characters_persist_failure_locked()
                        if require_authoritative:
                            raise
                        try:
                            from utils.cloudsave_runtime import MaintenanceModeError
                        except Exception:
                            MaintenanceModeError = None
                        if MaintenanceModeError is not None and isinstance(
                            persist_err,
                            MaintenanceModeError,
                        ):
                            logger.debug("角色保留字段迁移仍处于只读阶段: %s", persist_err)
                        else:
                            logger.warning("重试写回角色保留字段迁移失败: %s", persist_err)
                    return dirty_cache

            migration_persistence_allowed = True
            try:
                with open(character_json_path, 'r', encoding='utf-8') as f:
                    character_data = json.load(f)
                try:
                    loaded_mtime = _characters_file_signature(character_json_path)
                except OSError:
                    loaded_mtime = None
            except FileNotFoundError:
                if require_authoritative:
                    raise
                if cache_dirty and cache is not None and cache_path == character_json_path:
                    return deepcopy(cache)
                logger.info("未找到猫娘配置文件 %s，使用默认配置。", character_json_path)
                character_data = self.get_default_characters()
                loaded_mtime = None
            except Exception as e:
                if require_authoritative:
                    raise
                if cache_dirty and cache is not None and cache_path == character_json_path:
                    # Preserve the dirty flag and original mtime for a later retry.
                    return deepcopy(cache)
                logger.error("读取猫娘配置文件出错: %s，使用默认人设。", e)
                # 故障回退不是磁盘文件的权威内容，后续迁移只能在内存中使用，绝不能反写覆盖原文件。
                character_data = (
                    deepcopy(cache)
                    if cache is not None
                    and cache_path == character_json_path
                    and not cache_dirty
                    else self.get_default_characters()
                )
                loaded_mtime = None
                migration_persistence_allowed = False

            migrated = False
            if not isinstance(character_data, dict):
                if require_authoritative:
                    raise ValueError("character_config_not_authoritative")
                logger.warning("角色配置文件结构异常（非 dict），使用默认配置。")
                character_data = self.get_default_characters()
                loaded_mtime = None
                migration_persistence_allowed = False
            catgirl_map = character_data.get("猫娘")
            if isinstance(catgirl_map, dict):
                # A dirty cache may hold ids that were already handed out but never
                # written back. A re-read of a rewritten file that still lacks them
                # must keep those identities instead of minting new random ones; a
                # card whose file entry carries its own id keeps that id, and no
                # carried id may collide with an id the file already uses.
                carried_character_ids: dict[str, str] = {}
                if cache_dirty and cache_path == character_json_path:
                    carried_character_ids = _catgirl_character_ids(cache)
                    if carried_character_ids:
                        disk_character_ids = set(
                            _catgirl_character_ids(character_data).values()
                        )
                        carried_character_ids = {
                            name: character_id
                            for name, character_id in carried_character_ids.items()
                            if character_id not in disk_character_ids
                        }
                all_schema_errors: list[str] = []
                used_character_ids: set[str] = set()
                for name, catgirl_data in catgirl_map.items():
                    if not isinstance(catgirl_data, dict):
                        logger.warning("角色 '%s' 配置非 dict，跳过迁移。", name)
                        continue
                    if migrate_catgirl_reserved(catgirl_data):
                        migrated = True
                    carried_id = carried_character_ids.get(name)
                    if (
                        carried_id
                        and carried_id not in used_character_ids
                        and not normalize_character_id(
                            get_reserved(catgirl_data, "character_id", default="")
                        )
                    ):
                        set_reserved(catgirl_data, "character_id", carried_id)
                        migrated = True
                    _, character_id_changed = ensure_catgirl_character_id(
                        catgirl_data,
                        used_ids=used_character_ids,
                    )
                    migrated |= character_id_changed
                    reserved_errors = validate_reserved_schema(catgirl_data.get("_reserved"))
                    for err in reserved_errors:
                        all_schema_errors.append(f"{name}: {err}")
                if all_schema_errors:
                    logger.warning("检测到角色 _reserved 字段结构异常: %s", "; ".join(all_schema_errors))
            if migrated and migration_persistence_allowed:
                try:
                    self.save_characters(character_data, character_json_path=persist_json_path)
                    logger.info("检测到旧版角色保留字段，已自动迁移到 _reserved 结构。")
                except Exception as migrate_err:
                    # character_id 即使在临时只读阶段也必须在本进程内保持稳定；
                    # 否则每次 load 都会为同一张旧卡生成不同身份。后续正常写入会
                    # 连同其它迁移结果一起持久化。
                    with self._characters_cache_lock:
                        self._characters_cache = deepcopy(character_data)
                        self._characters_cache_mtime = loaded_mtime
                        self._characters_cache_path = character_json_path
                        self._characters_dirty = True
                        self._note_characters_persist_failure_locked()
                    if require_authoritative:
                        raise
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
                if migrated and not migration_persistence_allowed:
                    logger.warning("角色配置读取失败，保留原文件并仅在内存中应用保留字段迁移。")
                with self._characters_cache_lock:
                    self._characters_cache = deepcopy(character_data)
                    self._characters_cache_mtime = loaded_mtime
                    self._characters_cache_path = character_json_path
                    self._characters_dirty = False
                    self._clear_characters_persist_backoff_locked()
            return character_data

    def save_characters(self, data, character_json_path=None, *, bypass_write_fence: bool = False):
        """Save character configs (sync version, blocks the event loop; use asave_characters on async paths)"""
        if character_json_path is None:
            character_json_path = str(self.get_runtime_config_path('characters.json'))

        if not bypass_write_fence:
            from utils.cloudsave_runtime import assert_cloudsave_writable

            assert_cloudsave_writable(self, operation="save", target="characters.json")

        with self._characters_reload_lock:
            # 确保config目录存在
            self.ensure_config_directory()

            atomic_write_json(character_json_path, data, ensure_ascii=False, indent=2)
            try:
                new_mtime = _characters_file_signature(character_json_path)
            except OSError:
                new_mtime = None
            with self._characters_cache_lock:
                self._characters_cache = deepcopy(data)
                self._characters_cache_mtime = new_mtime
                self._characters_cache_path = character_json_path
                self._characters_dirty = False
                self._clear_characters_persist_backoff_locked()

    async def asave_characters(self, data, character_json_path=None, *, bypass_write_fence: bool = False):
        """Async wrapper: the sync version must not run directly on the event loop (atomic_write_json blocks)."""
        return await asyncio.to_thread(
            self.save_characters,
            data,
            character_json_path,
            bypass_write_fence=bypass_write_fence,
        )

    def backfill_character_uids(self) -> bool:
        """Give every stored character a stable ``_reserved.character_uid`` once.

        Runs as an explicit startup step after cloudsave bootstrap/import, never
        from ``load_characters``: a load-time write would make a freshly seeded
        characters.json look user-modified and stop the legacy-root import.
        Only an existing runtime characters.json is touched, and it is written
        (atomically) only when some character lacked a valid id. Returns
        whether anything was written. Raises what ``save_characters`` raises
        (e.g. the cloudsave write fence in maintenance mode).
        """
        character_json_path = str(self.get_runtime_config_path('characters.json'))
        if not os.path.isfile(character_json_path):
            return False
        # load_characters falls back to the default profiles when the file is
        # unreadable or not an object; saving those back would overwrite every
        # user-created profile. Only a file that parses as an object is touched.
        try:
            with open(character_json_path, 'r', encoding='utf-8') as f:
                on_disk = json.load(f)
        except (OSError, ValueError) as read_err:
            logger.warning("角色配置文件无法解析，跳过 character_uid 补发: %s", read_err)
            return False
        if not isinstance(on_disk, dict):
            logger.warning("角色配置文件结构异常（非 dict），跳过 character_uid 补发。")
            return False
        # Write back exactly what was parsed: a second read (load_characters)
        # could hit a transient lock / replacement and silently yield defaults.
        # The raw file may still hold legacy top-level reserved fields; migrate
        # them as load_characters does, so the write below (which also seeds
        # the cache) never stores an unmigrated profile.
        catgirls = on_disk.get('猫娘')
        changed = False
        used_character_ids: set[str] = set()
        if isinstance(catgirls, dict):
            for catgirl_data in catgirls.values():
                if isinstance(catgirl_data, dict):
                    changed |= migrate_catgirl_reserved(catgirl_data)
                    _, theater_id_changed = ensure_catgirl_character_id(
                        catgirl_data, used_ids=used_character_ids,
                    )
                    changed |= theater_id_changed
        changed |= ensure_character_uids(catgirls)
        if not changed:
            return False
        self.save_characters(on_disk, character_json_path=character_json_path)
        logger.info("已为缺少稳定 id 的角色补发 character_uid。")
        return True

    async def abackfill_character_uids(self) -> bool:
        """Async wrapper for ``backfill_character_uids`` (file IO off the event loop)."""
        return await asyncio.to_thread(self.backfill_character_uids)

    # --- Character metadata helpers ---

    def get_character_data(self, *, lang: str | None = None):
        """Get character base data and related paths.

        ``lang`` renders the synthetic rename-fact fields in that language
        instead of the process language (see ``_build_ai_context_fields``).
        """
        character_data = self.load_characters()
        defaults = self.get_default_characters()

        character_data.setdefault('主人', deepcopy(defaults['主人']))
        character_data.setdefault('猫娘', deepcopy(defaults['猫娘']))

        master_basic_config = _build_effective_character_payload(
            character_data.get('主人', {}), entity="master", lang=lang,
        )
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
                        self._note_characters_persist_failure_locked()

        name_mapping = {'human': master_name, 'system': "SYSTEM_MESSAGE"}
        effective_character_data = {
            name: _build_effective_character_payload(raw_character_data.get(name, {}), lang=lang)
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

    async def aget_character_data(self, *, lang: str | None = None):
        return await asyncio.to_thread(self.get_character_data, lang=lang)

    def _read_durable_prompt_locale(self, name: str) -> str | None:
        """Read ``memory/{name}/prompt_locale.json`` without creating the directory.

        This is the conversation language persisted for long-lived jobs. Card
        sync must use it when writing rename facts; the process-global language
        flips for the duration of those jobs and would rewrite the same fact.

        A missing or malformed file means no persisted language (``None``).
        Any other ``OSError`` propagates, mirroring the canonical reader in
        ``app/memory_server/locale_state.py``: a transient failure must not
        look like "no locale", or the caller would write the fact in the
        process language.
        """
        if not name:
            return None
        path = os.path.join(str(self.memory_dir), str(name), "prompt_locale.json")
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, UnicodeError):
            return None
        if not isinstance(payload, dict):
            return None
        language = payload.get("language")
        from utils.language_utils import is_supported_language_code, normalize_language_code
        if not is_supported_language_code(language):
            return None
        return normalize_language_code(str(language), format="full")

    async def aload_characters(self, character_json_path=None):
        """Async wrapper for load_characters: even a cache hit deepcopies the whole dict;
        with N catgirls the copy can take several ms — offload to avoid blocking the event loop."""
        return await asyncio.to_thread(self.load_characters, character_json_path)
