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
"""Config file management package.

Manages config file storage locations and migration. Formerly the monolithic
``utils/config_manager.py`` (5.3k lines); now split by domain:

- ``_shared``: package logger, ``LocalStateDirectoryError``, ``_as_bool``
- ``reserved_schema``: pure ``_reserved`` field helpers (get/set/delete,
  validate, migrate, flatten)
- ``persona_payload``: persona synthesis + YUI/live2d legacy helpers
- ``storage_roots`` / ``migrations`` / ``characters`` / ``voice_storage`` /
  ``core_config`` / ``quota`` / ``workshop``: one mixin each, assembled into
  the single ``ConfigManager`` class below.

The import path ``utils.config_manager`` and every top-level symbol are
unchanged -- this facade re-exports them all.

Monkeypatch compatibility notes:

- The ``ConfigManager`` class is ASSEMBLED HERE so its identity is unchanged
  and ``patch.object(ConfigManager, ...)`` / ``patch("utils.config_manager.
  ConfigManager.<method>")`` keep working. All class-level shared state
  (quota locks, notifier slot, geo caches, state-file versions) lives on
  this final class as its single owner; mixin methods that name the class
  explicitly resolve it late through this facade.
- The singleton block (``_config_manager`` / ``get_config_manager`` and the
  module-level convenience functions) intentionally stays in this module:
  their function ``__globals__`` must be THIS module dict so that existing
  ``patch("utils.config_manager._config_manager", ...)`` calls in tests keep
  rebinding the very name those functions resolve at call time.
- The stdlib/third-party imports below are re-exported on purpose:
  tests patch e.g. ``utils.config_manager.sys.platform`` and
  ``utils.config_manager.Path.home`` through this module's attributes.
- Cross-module helpers that existing tests patch through this facade
  (``check_custom_tts_voice_allowed`` and the ``utils.api_config_loader``
  getters) are resolved late through this facade inside the mixins, so
  ``patch("utils.config_manager.<helper>")`` keeps intercepting those
  call sites after the split.

Run directly with ``python -m utils.config_manager`` (replaces the former
``python utils/config_manager.py``).
"""
import sys  # noqa: F401
import os  # noqa: F401
import json  # noqa: F401
import re  # noqa: F401
import shutil  # noqa: F401
import threading
import asyncio  # noqa: F401
import time  # noqa: F401
import math  # noqa: F401
import uuid  # noqa: F401
from datetime import date  # noqa: F401
from copy import deepcopy  # noqa: F401
from pathlib import Path  # noqa: F401
from urllib.parse import urlparse, urlunparse  # noqa: F401

from config import (  # noqa: F401
    APP_NAME,
    CONFIG_FILES,
    DEFAULT_CONFIG_DATA,
    GEOIP_FORCE_NON_MAINLAND,
    RESERVED_FIELD_SCHEMA,
)
from config.prompts.prompts_chara import get_lanlan_prompt, is_default_prompt  # noqa: F401
from utils.api_config_loader import (  # noqa: F401
    get_core_api_profiles,
    get_assist_api_profiles,
    get_assist_api_key_fields,
    get_livestream_config,
    is_livestream_active,
)
from utils.custom_tts_adapter import check_custom_tts_voice_allowed  # noqa: F401
from utils.doubao_tts import DOUBAO_VOICE_STORAGE_KEY  # noqa: F401
from utils.file_utils import atomic_write_json  # noqa: F401
from utils.gptsovits_config import normalize_gsv_api_url  # noqa: F401
from utils.voice_config import read_legacy_voice_id  # noqa: F401
from utils.logger_config import get_module_logger  # noqa: F401
from utils.tts.native_voice_registry import (  # noqa: F401
    is_free_lanlan_app_route,
    is_saveable_native_voice,
)
from utils.persona_presets import PERSONA_OVERRIDE_FIELDS  # noqa: F401
from utils.steam_state import get_steamworks  # noqa: F401

from ._shared import (  # noqa: F401
    LocalStateDirectoryError,
    _as_bool,
    logger,
)
from .reserved_schema import (  # noqa: F401
    _legacy_live2d_name_from_model_path,
    _legacy_live2d_to_model_path,
    delete_reserved,
    flatten_reserved,
    get_reserved,
    migrate_catgirl_reserved,
    set_reserved,
    validate_reserved_schema,
)
from .persona_payload import (  # noqa: F401
    DEFAULT_YUI_LIVE2D_MODEL_PATH,
    _AI_CONTEXT_RENAME_EVENT_FIELD,
    _DEPRECATED_FREE_YUI_VOICE_IDS,
    _append_persona_guidance_to_prompt,
    _build_ai_context_fields,
    _build_effective_character_payload,
    _get_default_yui_free_voice_id,
    _get_persona_override,
    _has_generated_persona_selection_prompt,
    _is_default_yui_character,
    _join_profile_rename_old_names,
    _normalize_live2d_model_path,
    _normalize_persona_override_profile,
    _resolve_effective_character_prompt,
    _unique_ai_context_field_name,
    ensure_default_yui_voice_for_free_api,
    strip_generated_persona_selection_prompt,
)
from .characters import CharactersMixin
from .core_config import CoreConfigMixin
from .migrations import MigrationsMixin
from .quota import QuotaMixin
from .storage_roots import StorageRootsMixin
from .voice_storage import VoiceStorageMixin
from .workshop import WorkshopMixin


class ConfigManager(
    StorageRootsMixin,
    MigrationsMixin,
    CharactersMixin,
    VoiceStorageMixin,
    CoreConfigMixin,
    QuotaMixin,
    WorkshopMixin,
):
    """Config file manager"""
    _agent_quota_lock = threading.Lock()
    _selected_root_unavailable_recovery_override_roots: set[str] = set()
    _free_agent_daily_limit = 500 # 免费配额并非只在本地实施，本地计算是为了减少无效请求、节约网络带宽。
    # 本地每日配额只对真正的免费 Agent 模型计数；模型名与 config/api_providers.json 的 assist free profile 保持一致。
    _free_agent_model_name = "free-agent-model"
    # 配额耗尽时给前端弹提示的节流：与 _agent_quota_lock 不同的锁，避免在持有配额锁时重入。
    # notifier 由 agent_server 在启动时注册（进程级），收到耗尽信号最多每 _quota_notify_interval_s 秒触发一次。
    _quota_notify_lock = threading.Lock()
    _quota_notify_interval_s = 10.0
    _quota_notify_last_monotonic = 0.0
    _quota_exceeded_notifier = None
    ROOT_STATE_VERSION = 1
    CLOUDSAVE_LOCAL_STATE_VERSION = 1
    CHARACTER_TOMBSTONES_STATE_VERSION = 1

    # 区域判定的三个缓存（None=未定，True=非大陆，False=大陆）：
    #  - _region_cache：最终裁决，只由 IP 结论写（Steam 兜底票从不落定它）
    #  - _ip_check_cache：背景探测线程的产物，唯一写者是 _ip_probe_loop
    #  - _steam_check_cache：Steam SDK 的国家码
    _region_cache = None
    _ip_check_cache = None
    _steam_check_cache = None
    _geo_indeterminate_logged = False
    _geo_steam_fallback_logged = False
    # 保护背景探测线程的幂等启动（_ensure_ip_probe_started 的 check-and-set）。
    _geo_probe_lock = threading.Lock()
    # 背景探测线程：单个 daemon 循环，内部退避重试到成功即退出，是 _ip_check_cache
    # 的唯一写者。单线程单写者 → 无需票号 / 卡死顶替 / 泄漏封顶那套并发管理。
    _ip_probe_thread = None
    # 可中断退避：set 它让探测循环从退避 sleep 中醒来并退出（进程 shutdown / 测试
    # 清理）。生产从不 set，退避行为等同 time.sleep。
    _ip_probe_wake = threading.Event()


# 全局配置管理器实例
_config_manager = None
_config_manager_migrated = False


def _ensure_config_manager_migrated():
    global _config_manager_migrated
    if _config_manager is None or _config_manager_migrated:
        return _config_manager
    if bool(getattr(_config_manager, "recovery_committed_root_unavailable", False)):
        return _config_manager
    # 统一在首次真正需要运行时配置时再迁移，允许启动 phase-0
    # 先基于“尚未注入默认配置的运行根”判断是否需要导入云快照。
    _config_manager.migrate_config_files()
    _config_manager.migrate_default_card_faces()
    _config_manager.migrate_memory_files()
    # 在 config/memory 基础迁移完成后，对遗留 Documents/AppData 路径下的
    # N.E.K.O/memory 做一次性软迁移：只迁移已关联角色的条目，未关联条目
    # 留给前端 legacy cleanup UI 手动清理（不在启动时自动清除）。
    # 失败只打日志不抛异常，绝不阻塞启动。
    try:
        _config_manager.migrate_legacy_documents_memory()
    except Exception as exc:
        # "shouldn't happen" 路径（方法内部已吞所有异常），但 OSError 的 str(exc)
        # 带 filename 会泄露 Documents 用户名，只打类名避免绕过脱敏。
        try:
            _config_manager._log(
                f"[ConfigManager] migrate_legacy_documents_memory 抛异常（已忽略）: "
                f"{type(exc).__name__}"
            )
        except Exception:
            pass
    _config_manager_migrated = True
    return _config_manager


def reset_config_manager_cache() -> None:
    """Clear the process-local ConfigManager singleton cache."""
    global _config_manager, _config_manager_migrated
    _config_manager = None
    _config_manager_migrated = False


def get_config_manager(app_name=None, *, migrate=True):
    """Get the config manager singleton, defaulting to APP_NAME from config."""
    global _config_manager, _config_manager_migrated
    if _config_manager is None:
        _config_manager = ConfigManager(app_name)
        _config_manager_migrated = False
    if migrate:
        _ensure_config_manager_migrated()
    return _config_manager


# 便捷函数
def get_config_path(filename):
    """Get the config file path"""
    return get_config_manager().get_config_path(filename)


def get_runtime_config_path(filename):
    """Get the runtime source-of-truth config path."""
    return get_config_manager().get_runtime_config_path(filename)


def get_plugins_directory(app_name=None):
    """Get the user plugin root directory, defaulting to ``plugins`` under the app documents directory."""
    manager = ConfigManager(app_name)
    manager.ensure_plugins_directory()
    return manager.plugins_dir


def load_json_config(filename, default_value=None):
    """Load JSON config"""
    return get_config_manager().load_json_config(filename, default_value)


def save_json_config(filename, data):
    """Save JSON config"""
    return get_config_manager().save_json_config(filename, data)

# Workshop配置便捷函数
def load_workshop_config():
    """Load workshop config"""
    return get_config_manager().load_workshop_config()

def save_workshop_config(config_data):
    """Save workshop config"""
    return get_config_manager().save_workshop_config(config_data)

def save_workshop_path(workshop_path):
    """Set the Steam Workshop root directory path (runtime)"""
    return get_config_manager().save_workshop_path(workshop_path)

def persist_user_workshop_folder(workshop_path):
    """Persist the actual Steam Workshop path into the config file (written only once per startup)"""
    return get_config_manager().persist_user_workshop_folder(workshop_path)

def get_steam_workshop_path():
    """Get the Steam Workshop root directory path (runtime)"""
    return get_config_manager().get_steam_workshop_path()

def get_workshop_path():
    """Get the workshop root directory path"""
    return get_config_manager().get_workshop_path()
