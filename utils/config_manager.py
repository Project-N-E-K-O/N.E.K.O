# -*- coding: utf-8 -*-
"""
配置文件管理模块
负责管理配置文件的存储位置和迁移
"""
import sys
import os
import json
import re
import shutil
import threading
import asyncio
import math
import uuid
from datetime import date
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from config import (
    APP_NAME,
    CONFIG_FILES,
    DEFAULT_CONFIG_DATA,
    RESERVED_FIELD_SCHEMA,
)
from config.prompts_chara import get_lanlan_prompt, is_default_prompt
from utils.api_config_loader import (
    get_core_api_profiles,
    get_assist_api_profiles,
    get_assist_api_key_fields,
    get_livestream_config,
    is_livestream_active,
)
from utils.custom_tts_adapter import check_custom_tts_voice_allowed
from utils.file_utils import atomic_write_json
from utils.logger_config import get_module_logger
from utils.persona_presets import PERSONA_OVERRIDE_FIELDS

# Workshop配置相关常量 - 将在ConfigManager实例化时使用self.workshop_dir


logger = get_module_logger(__name__)


def get_reserved(data: dict, *path, default=None, legacy_keys: tuple[str, ...] | None = None):
    """统一读取 `_reserved` 下的嵌套字段，支持旧平铺字段回退。

    如果 _reserved 中的嵌套路径存在（即使值为 None），直接返回该值；
    仅当路径不存在或 _reserved 本身缺失时，才回退到旧平铺字段。
    """
    if not isinstance(data, dict):
        return default

    reserved = data.get("_reserved")
    if isinstance(reserved, dict):
        current = reserved
        found = True
        for key in path:
            if not isinstance(current, dict) or key not in current:
                found = False
                break
            current = current[key]
        if found:
            return current

    # COMPAT(v1->v2): 旧平铺字段回退读取，避免历史配置在迁移前读不到值。
    if legacy_keys:
        for legacy_key in legacy_keys:
            if legacy_key in data and data[legacy_key] is not None:
                return data[legacy_key]
    return default


def set_reserved(data: dict, *path_and_value) -> bool:
    """统一写入 `_reserved` 下的嵌套字段，自动创建中间层。

    Returns ``True`` if the stored value was actually changed, ``False``
    otherwise (including invalid input).
    """
    if not isinstance(data, dict) or len(path_and_value) < 2:
        return False
    *path, value = path_and_value
    if not path:
        return False

    reserved = data.get("_reserved")
    if not isinstance(reserved, dict):
        reserved = {}
        data["_reserved"] = reserved

    current = reserved
    for key in path[:-1]:
        next_node = current.get(key)
        if not isinstance(next_node, dict):
            next_node = {}
            current[key] = next_node
        current = next_node

    last_key = path[-1]
    if last_key in current and current[last_key] == value:
        return False
    current[last_key] = value
    return True


def delete_reserved(data: dict, *path) -> bool:
    """删除 `_reserved` 下的嵌套字段，并尽量清理空的中间层。"""
    if not isinstance(data, dict) or not path:
        return False

    reserved = data.get("_reserved")
    if not isinstance(reserved, dict):
        return False

    current = reserved
    parents: list[tuple[dict, str]] = []
    for key in path[:-1]:
        if not isinstance(current, dict) or key not in current:
            return False
        parents.append((current, key))
        current = current.get(key)

    last_key = path[-1]
    if not isinstance(current, dict) or last_key not in current:
        return False

    current.pop(last_key, None)

    while parents:
        parent, key = parents.pop()
        child = parent.get(key)
        if isinstance(child, dict) and not child:
            parent.pop(key, None)
            continue
        break

    if isinstance(data.get("_reserved"), dict) and not data["_reserved"]:
        data.pop("_reserved", None)

    return True


def _normalize_persona_override_profile(raw_profile: object) -> dict[str, str]:
    if not isinstance(raw_profile, dict):
        return {}

    profile: dict[str, str] = {}
    for field in PERSONA_OVERRIDE_FIELDS:
        value = str(raw_profile.get(field) or "").strip()
        if value:
            profile[field] = value
    return profile


def _get_persona_override(character_payload: dict) -> dict | None:
    if not isinstance(character_payload, dict):
        return None

    reserved = character_payload.get("_reserved")
    if not isinstance(reserved, dict):
        return None

    override = reserved.get("persona_override")
    if not isinstance(override, dict):
        return None

    return override


def _build_effective_character_payload(character_payload: dict) -> dict:
    if not isinstance(character_payload, dict):
        return {}

    effective_payload = deepcopy(character_payload)
    override = _get_persona_override(character_payload)
    if not isinstance(override, dict):
        return effective_payload

    profile = _normalize_persona_override_profile(override.get("profile"))
    for field, value in profile.items():
        effective_payload[field] = value
    return effective_payload


def _append_persona_guidance_to_prompt(prompt_text: str, character_payload: dict) -> str:
    override = _get_persona_override(character_payload)
    if not isinstance(override, dict):
        return prompt_text

    guidance = str(override.get("prompt_guidance") or "").strip()
    if not guidance:
        return prompt_text

    return f"{prompt_text}\n\nAdditional role guidance: {guidance}"


def _has_generated_persona_selection_prompt(prompt_text: object) -> bool:
    if not isinstance(prompt_text, str):
        return False
    return "<NEKO_PERSONA_SELECTION>" in prompt_text


def strip_generated_persona_selection_prompt(prompt_text: object) -> str | None:
    if not isinstance(prompt_text, str):
        return None
    if not _has_generated_persona_selection_prompt(prompt_text):
        return prompt_text

    cleaned_prompt = re.sub(
        r"\s*<NEKO_PERSONA_SELECTION>.*?</NEKO_PERSONA_SELECTION>\s*",
        "\n\n",
        prompt_text,
        flags=re.DOTALL,
    )
    cleaned_prompt = re.sub(r"\n{3,}", "\n\n", cleaned_prompt).strip()
    return cleaned_prompt


def _resolve_effective_character_prompt(character_payload: dict) -> str:
    stored_prompt = get_reserved(
        character_payload,
        "system_prompt",
        default=None,
        legacy_keys=("system_prompt",),
    )
    if stored_prompt is None or is_default_prompt(stored_prompt):
        return get_lanlan_prompt()

    # 旧版人格功能会把整段模板化人格 prompt 直接写进 system_prompt。
    # 不论当前是否仍保留 persona_override，这类历史片段都不应继续直接喂给模型。
    if _has_generated_persona_selection_prompt(stored_prompt):
        cleaned_prompt = strip_generated_persona_selection_prompt(stored_prompt)
        return cleaned_prompt or get_lanlan_prompt()

    return stored_prompt


def _legacy_live2d_to_model_path(legacy_live2d: str) -> str:
    """将旧 live2d 目录名转为 model3 文件路径。"""
    if not legacy_live2d:
        return ""
    raw = str(legacy_live2d).strip().replace("\\", "/")
    if not raw:
        return ""
    if raw.endswith(".model3.json"):
        return raw
    # COMPAT(v1->v2): 历史配置只有目录名（如 mao_pro），迁移时自动补全默认 model3 文件名。
    return f"{raw}/{raw}.model3.json"


def _legacy_live2d_name_from_model_path(model_path: str) -> str:
    """将新 model_path 反向还原为旧 live2d 模型名（兼容旧前端字段）。"""
    if not model_path:
        return ""
    raw = str(model_path).strip().replace("\\", "/")
    if not raw:
        return ""
    if raw.endswith(".model3.json"):
        parent = raw.rsplit("/", 1)[0] if "/" in raw else ""
        if parent:
            return parent.rsplit("/", 1)[-1]
        filename = raw.rsplit("/", 1)[-1]
        name = filename[:-len(".model3.json")]
        return name
    return raw.rsplit("/", 1)[-1]


def validate_reserved_schema(reserved: dict) -> list[str]:
    """校验 `_reserved` 结构，返回错误列表（空列表表示通过）。"""
    errors: list[str] = []

    def _walk(value, schema, path: str):
        if isinstance(schema, dict):
            if not isinstance(value, dict):
                errors.append(f"{path} 需要 dict，实际 {type(value).__name__}")
                return
            for key, sub_schema in schema.items():
                if key in value and value[key] is not None:
                    _walk(value[key], sub_schema, f"{path}.{key}")
            return
        if isinstance(schema, tuple):
            if not isinstance(value, schema):
                expected = ",".join(t.__name__ for t in schema)
                errors.append(f"{path} 需要类型({expected})，实际 {type(value).__name__}")
            return
        if not isinstance(value, schema):
            errors.append(f"{path} 需要 {schema.__name__}，实际 {type(value).__name__}")

    if reserved is None:
        return errors
    _walk(reserved, RESERVED_FIELD_SCHEMA, "_reserved")
    return errors


def migrate_catgirl_reserved(catgirl_data: dict) -> bool:
    """迁移单个角色配置到 `_reserved` 结构，返回是否发生变更。"""
    if not isinstance(catgirl_data, dict):
        return False

    changed = False

    if not isinstance(catgirl_data.get("_reserved"), dict):
        catgirl_data["_reserved"] = {}
        changed = True

    voice_id = get_reserved(catgirl_data, "voice_id", default="", legacy_keys=("voice_id",))
    if voice_id is not None:
        changed |= set_reserved(catgirl_data, "voice_id", str(voice_id))

    system_prompt = get_reserved(catgirl_data, "system_prompt", default=None, legacy_keys=("system_prompt",))
    if system_prompt is not None:
        changed |= set_reserved(catgirl_data, "system_prompt", str(system_prompt))

    model_type = str(
        get_reserved(catgirl_data, "avatar", "model_type", default="", legacy_keys=("model_type",))
    ).strip().lower()
    if model_type not in {"live2d", "vrm", "live3d"}:
        has_vrm = catgirl_data.get("vrm") or get_reserved(catgirl_data, "avatar", "vrm", "model_path")
        has_mmd = catgirl_data.get("mmd") or get_reserved(catgirl_data, "avatar", "mmd", "model_path")
        model_type = "live3d" if (has_vrm or has_mmd) else "live2d"
    # 归一化：旧配置中的 'vrm' 统一为 'live3d'
    if model_type == "vrm":
        model_type = "live3d"
    changed |= set_reserved(catgirl_data, "avatar", "model_type", model_type)

    asset_source_id = get_reserved(
        catgirl_data,
        "avatar",
        "asset_source_id",
        default="",
        legacy_keys=("live2d_item_id", "item_id"),
    )
    asset_source_id = str(asset_source_id).strip() if asset_source_id is not None else ""
    changed |= set_reserved(catgirl_data, "avatar", "asset_source_id", asset_source_id)

    asset_source = get_reserved(catgirl_data, "avatar", "asset_source", default="")
    if not asset_source:
        asset_source = "steam_workshop" if asset_source_id else "local"
    changed |= set_reserved(catgirl_data, "avatar", "asset_source", str(asset_source))

    live2d_model_path = get_reserved(
        catgirl_data,
        "avatar",
        "live2d",
        "model_path",
        default="",
        legacy_keys=("live2d",),
    )
    if live2d_model_path:
        changed |= set_reserved(
            catgirl_data,
            "avatar",
            "live2d",
            "model_path",
            _legacy_live2d_to_model_path(str(live2d_model_path)),
        )

    live2d_idle_animation = get_reserved(
        catgirl_data,
        "avatar",
        "live2d",
        "idle_animation",
        default=None,
        legacy_keys=("live2d_idle_animation",),
    )
    if live2d_idle_animation is not None:
        if isinstance(live2d_idle_animation, str):
            changed |= set_reserved(catgirl_data, "avatar", "live2d", "idle_animation", live2d_idle_animation if live2d_idle_animation else None)
        elif isinstance(live2d_idle_animation, list):
            changed |= set_reserved(catgirl_data, "avatar", "live2d", "idle_animation", live2d_idle_animation[0] if live2d_idle_animation else None)

    vrm_model_path = get_reserved(
        catgirl_data,
        "avatar",
        "vrm",
        "model_path",
        default="",
        legacy_keys=("vrm",),
    )
    if vrm_model_path:
        changed |= set_reserved(catgirl_data, "avatar", "vrm", "model_path", str(vrm_model_path).strip())

    vrm_animation = get_reserved(
        catgirl_data,
        "avatar",
        "vrm",
        "animation",
        default=None,
        legacy_keys=("vrm_animation",),
    )
    if vrm_animation is not None:
        changed |= set_reserved(catgirl_data, "avatar", "vrm", "animation", vrm_animation)

    idle_animation = get_reserved(
        catgirl_data,
        "avatar",
        "vrm",
        "idle_animation",
        default=None,
        legacy_keys=("idleAnimation", "idleAnimations"),
    )
    if idle_animation is not None:
        # 向前兼容: 旧版存的是 string, 迁移为 list; 空值保留 []
        if isinstance(idle_animation, str):
            changed |= set_reserved(catgirl_data, "avatar", "vrm", "idle_animation", [idle_animation] if idle_animation else [])
        elif isinstance(idle_animation, list):
            changed |= set_reserved(catgirl_data, "avatar", "vrm", "idle_animation", idle_animation)

    lighting = get_reserved(
        catgirl_data,
        "avatar",
        "vrm",
        "lighting",
        default=None,
        legacy_keys=("lighting",),
    )
    if isinstance(lighting, dict):
        changed |= set_reserved(catgirl_data, "avatar", "vrm", "lighting", lighting)

    # MMD 模型路径迁移
    mmd_model_path = get_reserved(
        catgirl_data,
        "avatar",
        "mmd",
        "model_path",
        default="",
        legacy_keys=("mmd",),
    )
    if mmd_model_path:
        changed |= set_reserved(catgirl_data, "avatar", "mmd", "model_path", str(mmd_model_path).strip())

    mmd_animation = get_reserved(
        catgirl_data,
        "avatar",
        "mmd",
        "animation",
        default=None,
        legacy_keys=("mmd_animation",),
    )
    if mmd_animation is not None:
        changed |= set_reserved(catgirl_data, "avatar", "mmd", "animation", mmd_animation)

    mmd_idle_animation = get_reserved(
        catgirl_data,
        "avatar",
        "mmd",
        "idle_animation",
        default=None,
        legacy_keys=("mmd_idle_animation", "mmd_idle_animations"),
    )
    if mmd_idle_animation is not None:
        # 向前兼容: 旧版存的是 string, 迁移为 list; 空值保留 []
        if isinstance(mmd_idle_animation, str):
            changed |= set_reserved(catgirl_data, "avatar", "mmd", "idle_animation", [mmd_idle_animation] if mmd_idle_animation else [])
        elif isinstance(mmd_idle_animation, list):
            changed |= set_reserved(catgirl_data, "avatar", "mmd", "idle_animation", mmd_idle_animation)

    live3d_sub_type = str(
        get_reserved(
            catgirl_data,
            "avatar",
            "live3d_sub_type",
            default="",
            legacy_keys=("live3d_sub_type",),
        )
        or ""
    ).strip().lower()
    if live3d_sub_type not in {"vrm", "mmd"}:
        has_mmd_model = bool(get_reserved(catgirl_data, "avatar", "mmd", "model_path", default=""))
        has_vrm_model = bool(get_reserved(catgirl_data, "avatar", "vrm", "model_path", default=""))
        if model_type == "live3d":
            if has_mmd_model:
                live3d_sub_type = "mmd"
            elif has_vrm_model:
                live3d_sub_type = "vrm"
            else:
                live3d_sub_type = ""
        elif has_mmd_model and not has_vrm_model:
            live3d_sub_type = "mmd"
        elif has_vrm_model and not has_mmd_model:
            live3d_sub_type = "vrm"
        else:
            live3d_sub_type = ""
    if live3d_sub_type:
        changed |= set_reserved(catgirl_data, "avatar", "live3d_sub_type", live3d_sub_type)
    else:
        # 非 3D 角色或没有明确活动 3D 子类型时，不要强行写回空字符串，
        # 否则会让导出/导入后的角色配置出现无意义的额外字段。
        changed |= delete_reserved(catgirl_data, "avatar", "live3d_sub_type")

    # COMPAT(v1->v2): 保留字段统一迁入 _reserved 后，移除旧平铺字段，避免再次泄露到可编辑字段。
    for legacy_key in (
        "voice_id",
        "system_prompt",
        "model_type",
        "live3d_sub_type",
        "live2d_item_id",
        "item_id",
        "live2d",
        "live2d_idle_animation",
        "vrm",
        "vrm_animation",
        "idleAnimation",
        "idleAnimations",
        "lighting",
        "vrm_rotation",
        "mmd",
        "mmd_animation",
        "mmd_idle_animation",
        "mmd_idle_animations",
    ):
        if legacy_key in catgirl_data:
            catgirl_data.pop(legacy_key, None)
            changed = True

    return changed


def flatten_reserved(catgirl_data: dict) -> dict:
    """将 `_reserved` 展开成旧平铺字段（仅用于兼容旧调用方/前端）。"""
    if not isinstance(catgirl_data, dict):
        return catgirl_data
    result = dict(catgirl_data)

    voice_id = get_reserved(result, "voice_id", default="")
    if voice_id:
        result["voice_id"] = voice_id
    system_prompt = get_reserved(result, "system_prompt", default=None)
    if system_prompt is not None:
        result["system_prompt"] = system_prompt

    model_type = get_reserved(result, "avatar", "model_type", default="live2d")
    if model_type:
        result["model_type"] = model_type

    live3d_sub_type = get_reserved(result, "avatar", "live3d_sub_type", default="")
    if live3d_sub_type:
        result["live3d_sub_type"] = live3d_sub_type

    live2d_model_path = get_reserved(result, "avatar", "live2d", "model_path", default="")
    if live2d_model_path:
        result["live2d"] = _legacy_live2d_name_from_model_path(str(live2d_model_path))

    live2d_idle_animation = get_reserved(result, "avatar", "live2d", "idle_animation", default=None)
    if live2d_idle_animation is not None:
        result["live2d_idle_animation"] = live2d_idle_animation

    vrm_model_path = get_reserved(result, "avatar", "vrm", "model_path", default="")
    if vrm_model_path:
        result["vrm"] = vrm_model_path

    asset_source_id = get_reserved(result, "avatar", "asset_source_id", default="")
    if asset_source_id:
        result["live2d_item_id"] = asset_source_id

    vrm_animation = get_reserved(result, "avatar", "vrm", "animation", default=None)
    if vrm_animation is not None:
        result["vrm_animation"] = vrm_animation

    idle_animation = get_reserved(result, "avatar", "vrm", "idle_animation", default=None)
    if idle_animation is not None:
        # idleAnimation (string): 供 vrm-init / vrm-manager 等运行时消费
        # idleAnimations (list): 供 model_manager 多选 UI 消费
        if isinstance(idle_animation, str):
            result["idleAnimation"] = idle_animation
            result["idleAnimations"] = [idle_animation] if idle_animation else []
        elif isinstance(idle_animation, list):
            result["idleAnimation"] = idle_animation[0] if idle_animation else ""
            result["idleAnimations"] = idle_animation
        else:
            result["idleAnimation"] = ""
            result["idleAnimations"] = []

    lighting = get_reserved(result, "avatar", "vrm", "lighting", default=None)
    if isinstance(lighting, dict):
        result["lighting"] = lighting

    mmd_model_path = get_reserved(result, "avatar", "mmd", "model_path", default="")
    if mmd_model_path:
        result["mmd"] = mmd_model_path

    mmd_animation = get_reserved(result, "avatar", "mmd", "animation", default=None)
    if mmd_animation is not None:
        result["mmd_animation"] = mmd_animation

    mmd_idle_animation = get_reserved(result, "avatar", "mmd", "idle_animation", default=None)
    if mmd_idle_animation is not None:
        # mmd_idle_animation (string): 供 mmd-init / app-interpage 等运行时消费
        # mmd_idle_animations (list): 供 model_manager 多选 UI 消费
        if isinstance(mmd_idle_animation, str):
            result["mmd_idle_animation"] = mmd_idle_animation
            result["mmd_idle_animations"] = [mmd_idle_animation] if mmd_idle_animation else []
        elif isinstance(mmd_idle_animation, list):
            result["mmd_idle_animation"] = mmd_idle_animation[0] if mmd_idle_animation else ""
            result["mmd_idle_animations"] = mmd_idle_animation
        else:
            result["mmd_idle_animation"] = ""
            result["mmd_idle_animations"] = []

    touch_set = get_reserved(result, 'touch_set', default=None)
    if touch_set:
        result['touch_set'] = touch_set
    return result


class ConfigManager:
    """配置文件管理器"""
    _agent_quota_lock = threading.Lock()
    _selected_root_unavailable_recovery_override_roots: set[str] = set()
    _free_agent_daily_limit = 300 # 免费配额并非只在本地实施，本地计算是为了减少无效请求、节约网络带宽。
    ROOT_STATE_VERSION = 1
    CLOUDSAVE_LOCAL_STATE_VERSION = 1
    CHARACTER_TOMBSTONES_STATE_VERSION = 1

    @property
    def selected_root(self):
        return self.committed_selected_root

    @selected_root.setter
    def selected_root(self, value):
        self.committed_selected_root = value

    def __init__(self, app_name=None):
        """
        初始化配置管理器
        
        Args:
            app_name: 应用名称，默认使用配置中的 APP_NAME
        """
        self.app_name = app_name if app_name is not None else APP_NAME
        # 检测是否在子进程中，子进程静默初始化（通过 main_server.py 设置的环境变量）
        self._verbose = '_NEKO_MAIN_SERVER_INITIALIZED' not in os.environ
        self.docs_dir = self._get_documents_directory()
        default_app_docs_dir = self.docs_dir / self.app_name

        # CFA (Windows 受控文件夹访问/反勒索防护) 检测：
        # 如果原始 Documents 路径可读但不可写，记住它以便从中读取用户数据（模型等）
        first_readable_non_writable = getattr(self, '_first_non_writable_readable_candidate', None)
        self._cfa_fallback_write_docs_dir = None
        if (
            sys.platform == "win32"
            and first_readable_non_writable is not None
            and first_readable_non_writable != self.docs_dir
        ):
            self._readable_docs_dir = first_readable_non_writable
            self._cfa_fallback_write_docs_dir = self.docs_dir
            print("⚠ WARNING [ConfigManager] 文档目录不可写（可能受Windows安全策略/反勒索防护保护）!", file=sys.stderr)
            print(f"⚠ WARNING [ConfigManager] 原始文档路径(只读): {first_readable_non_writable}", file=sys.stderr)
            print(f"⚠ WARNING [ConfigManager] 回退写入路径: {self.docs_dir}", file=sys.stderr)
            print("⚠ WARNING [ConfigManager] 用户数据将从原始路径读取，写入操作将使用回退路径", file=sys.stderr)
        else:
            self._readable_docs_dir = None

        resolved_app_docs_dir = default_app_docs_dir
        resolved_anchor_root = default_app_docs_dir
        committed_selected_root = default_app_docs_dir
        recovery_committed_root_unavailable = False
        default_anchor_root = None
        try:
            from utils.storage_policy import (
                compute_anchor_root,
                is_runtime_root_available,
                load_storage_policy,
                normalize_runtime_root,
                paths_equal,
            )

            env_selected_root = os.environ.get("NEKO_STORAGE_SELECTED_ROOT", "").strip()
            env_anchor_root = os.environ.get("NEKO_STORAGE_ANCHOR_ROOT", "").strip()
            default_anchor_root = compute_anchor_root(self, current_root=default_app_docs_dir)
            resolved_anchor_root = default_anchor_root
            policy_anchor_root = normalize_runtime_root(env_anchor_root or default_anchor_root)
            policy = load_storage_policy(self, anchor_root=policy_anchor_root)

            if env_selected_root:
                resolved_app_docs_dir = normalize_runtime_root(env_selected_root)
                resolved_anchor_root = normalize_runtime_root(env_anchor_root or default_anchor_root)
                committed_selected_root = resolved_app_docs_dir
                if isinstance(policy, dict):
                    first_run_completed = bool(policy.get("first_run_completed"))
                    selected_root_value = str(policy.get("selected_root") or "").strip()
                    if selected_root_value:
                        committed_selected_root = normalize_runtime_root(selected_root_value)
                    anchor_root_value = str(policy.get("anchor_root") or "").strip()
                    if anchor_root_value and not env_anchor_root:
                        resolved_anchor_root = normalize_runtime_root(anchor_root_value)
                    if (
                        first_run_completed
                        and paths_equal(resolved_app_docs_dir, resolved_anchor_root)
                        and not paths_equal(committed_selected_root, resolved_anchor_root)
                        and not is_runtime_root_available(committed_selected_root)
                    ):
                        recovery_committed_root_unavailable = True
            else:
                if env_anchor_root:
                    resolved_anchor_root = normalize_runtime_root(env_anchor_root)
                if isinstance(policy, dict):
                    first_run_completed = bool(policy.get("first_run_completed"))
                    selected_root_value = str(policy.get("selected_root") or "").strip()
                    if selected_root_value:
                        committed_selected_root = normalize_runtime_root(selected_root_value)
                        resolved_app_docs_dir = committed_selected_root
                        if not env_anchor_root:
                            resolved_anchor_root = normalize_runtime_root(
                                str(policy.get("anchor_root") or "").strip() or default_anchor_root
                            )
                        if (
                            first_run_completed
                            and not paths_equal(committed_selected_root, resolved_anchor_root)
                            and not is_runtime_root_available(committed_selected_root)
                        ):
                            resolved_app_docs_dir = resolved_anchor_root
                            recovery_committed_root_unavailable = True
        except Exception as e:
            logger.warning(
                "Failed to resolve storage policy paths; falling back to default runtime root: %s",
                e,
                exc_info=True,
            )
            resolved_app_docs_dir = default_app_docs_dir
            if default_anchor_root is not None:
                resolved_anchor_root = default_anchor_root
            committed_selected_root = resolved_app_docs_dir

        self.app_docs_dir = resolved_app_docs_dir
        self.committed_selected_root = committed_selected_root
        self.anchor_root = resolved_anchor_root
        self.reported_current_root = (
            self.committed_selected_root if recovery_committed_root_unavailable else self.app_docs_dir
        )
        self.recovery_committed_root_unavailable = recovery_committed_root_unavailable
        self.recovery_committed_root_unavailable_override = False
        self.docs_dir = self.app_docs_dir.parent
        self.config_dir = self.app_docs_dir / "config"
        self.memory_dir = self.app_docs_dir / "memory"
        self.plugins_dir = self.app_docs_dir / "plugins"
        self.live2d_dir = self.app_docs_dir / "live2d"
        # VRM模型存储在用户文档目录下（与Live2D保持一致）
        self.vrm_dir = self.app_docs_dir / "vrm"
        self.vrm_animation_dir = self.vrm_dir / "animation"  # VRMA动画文件目录
        # MMD模型存储在用户文档目录下
        self.mmd_dir = self.app_docs_dir / "mmd"
        self.mmd_animation_dir = self.mmd_dir / "animation"  # VMD动画文件目录
        self.workshop_dir = self.app_docs_dir / "workshop"
        self._steam_workshop_path = None
        self._user_workshop_folder_persisted = False
        self.chara_dir = self.app_docs_dir / "character_cards"
        self.card_faces_dir = self.app_docs_dir / "card_faces"
        self._workshop_config_lock = threading.Lock()

        self._characters_cache: dict | None = None
        self._characters_cache_mtime: float | None = None
        self._characters_cache_path: str | None = None
        self._characters_dirty: bool = False
        self._characters_cache_lock = threading.Lock()
        self._characters_reload_lock = threading.Lock()

        self.project_config_dir = self._get_project_config_directory()
        self.project_memory_dir = self._get_project_memory_directory()

        if self.recovery_committed_root_unavailable:
            try:
                self._persist_selected_root_unavailable_recovery_state()
                self.__class__._selected_root_unavailable_recovery_override_roots.discard(
                    str(self.committed_selected_root)
                )
            except Exception as e:
                self.recovery_committed_root_unavailable_override = True
                self.__class__._selected_root_unavailable_recovery_override_roots.add(
                    str(self.committed_selected_root)
                )
                logger.warning(
                    "Failed to persist selected-root-unavailable recovery state; "
                    "continuing with in-memory recovery flag: %s",
                    e,
                    exc_info=True,
                )

    @property
    def cloudsave_dir(self) -> Path:
        """云存档导出根目录（运行时目录之外的规范化导出层）。"""
        return self.anchor_root / "cloudsave"

    @property
    def cloudsave_catalog_dir(self) -> Path:
        return self.cloudsave_dir / "catalog"

    @property
    def cloudsave_profiles_dir(self) -> Path:
        return self.cloudsave_dir / "profiles"

    @property
    def cloudsave_bindings_dir(self) -> Path:
        return self.cloudsave_dir / "bindings"

    @property
    def cloudsave_memory_dir(self) -> Path:
        return self.cloudsave_dir / "memory"

    @property
    def cloudsave_overrides_dir(self) -> Path:
        return self.cloudsave_dir / "overrides"

    @property
    def cloudsave_meta_dir(self) -> Path:
        return self.cloudsave_dir / "meta"

    @property
    def cloudsave_workshop_meta_dir(self) -> Path:
        return self.cloudsave_meta_dir / "workshop"

    @property
    def cloudsave_manifest_path(self) -> Path:
        return self.cloudsave_dir / "manifest.json"

    @property
    def cloudsave_staging_dir(self) -> Path:
        """本地 staging 区，不进入云端同步白名单。"""
        return self.anchor_root / ".cloudsave_staging"

    @property
    def cloudsave_backups_dir(self) -> Path:
        """本地冲突备份池，显式放在 cloudsave/ 外避免后续误同步。"""
        return self.anchor_root / "cloudsave_backups"

    @property
    def local_state_dir(self) -> Path:
        """本地状态目录，保存不进入云端的同步元数据。"""
        return self.anchor_root / "state"

    @property
    def root_state_path(self) -> Path:
        return self.local_state_dir / "root_state.json"

    @property
    def cloudsave_local_state_path(self) -> Path:
        return self.local_state_dir / "cloudsave_local_state.json"

    @property
    def character_tombstones_state_path(self) -> Path:
        return self.local_state_dir / "character_tombstones.json"

    def _build_selected_root_unavailable_recovery_state(self, state=None):
        unavailable_root = str(self.committed_selected_root)
        state = dict(state) if isinstance(state, dict) else {}
        state["version"] = self.ROOT_STATE_VERSION
        from utils.cloudsave_runtime import ROOT_MODE_DEFERRED_INIT

        state["mode"] = ROOT_MODE_DEFERRED_INIT
        state["current_root"] = unavailable_root
        state["last_known_good_root"] = unavailable_root
        if not str(state.get("last_migration_result") or "").strip():
            state["last_migration_result"] = f"selected_root_unavailable:{unavailable_root}"
        state.setdefault("last_migration_source", "")
        state.setdefault("last_migration_backup", "")
        state.setdefault("last_successful_boot_at", "")
        state.setdefault("legacy_cleanup_pending", False)
        return state

    def _has_selected_root_unavailable_recovery_override(self) -> bool:
        if not self.recovery_committed_root_unavailable:
            return False
        if bool(getattr(self, "recovery_committed_root_unavailable_override", False)):
            return True
        return str(self.committed_selected_root) in self.__class__._selected_root_unavailable_recovery_override_roots

    def _persist_selected_root_unavailable_recovery_state(self):
        state: dict = {}
        try:
            loaded = self._load_json_file(self.root_state_path, default_value={})
            if isinstance(loaded, dict):
                state = loaded
        except Exception:
            state = {}
        self.save_root_state(self._build_selected_root_unavailable_recovery_state(state))
    
    def _log(self, msg):
        """仅在主进程中打印调试信息"""
        if self._verbose:
            print(msg, file=sys.stderr)

    def _can_write_existing_directory(self, directory):
        """Check whether an existing directory accepts a real write probe."""
        try:
            directory = Path(directory)
            if not directory.exists():
                return False
            if not os.access(str(directory), os.R_OK | os.W_OK):
                return False

            test_path = directory / f".test_neko_write.{uuid.uuid4().hex}.tmp"
            test_path.touch()
            test_path.unlink()
            return True
        except Exception:
            return False

    @staticmethod
    def _dedupe_paths(paths):
        unique = []
        seen = set()
        for path in paths:
            if not path:
                continue
            normalized = str(Path(path))
            if normalized in seen:
                continue
            seen.add(normalized)
            unique.append(Path(path))
        return unique

    def _get_standard_data_directory_candidates(self):
        """返回当前平台的首选应用数据根目录候选。"""
        candidates = []
        if sys.platform == "win32":
            localappdata = os.environ.get("LOCALAPPDATA", "").strip()
            if localappdata:
                candidates.append(Path(localappdata))
        elif sys.platform == "darwin":
            candidates.append(Path.home() / "Library" / "Application Support")
        else:
            xdg_data_home = os.getenv("XDG_DATA_HOME", "").strip()
            if xdg_data_home:
                candidates.append(Path(xdg_data_home))
            candidates.append(Path.home() / ".local" / "share")
        return self._dedupe_paths(candidates)

    def _get_legacy_storage_candidates(self):
        """返回历史运行时根的父目录候选，仅用于旧数据导入。"""
        candidates = []

        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import windll, wintypes

                CSIDL_PERSONAL = 5
                SHGFP_TYPE_CURRENT = 0

                buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
                windll.shell32.SHGetFolderPathW(None, CSIDL_PERSONAL, None, SHGFP_TYPE_CURRENT, buf)
                api_path = Path(buf.value)
                self._log(f"[ConfigManager] Legacy Documents API returned path: {api_path}")
                candidates.append(api_path)

                if not api_path.exists() and api_path.drive:
                    drive = api_path.drive
                    for name in ("文档", "Documents", "My Documents"):
                        alt_path = Path(drive) / name
                        if alt_path.exists():
                            self._log(f"[ConfigManager] Found legacy Documents alternative: {alt_path}")
                            candidates.append(alt_path)
            except Exception as e:
                print(f"Warning: Failed to get legacy Documents path via API: {e}", file=sys.stderr)

            try:
                import winreg

                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
                )
                reg_path_str = winreg.QueryValueEx(key, "Personal")[0]
                winreg.CloseKey(key)
                reg_path = Path(os.path.expandvars(reg_path_str))
                self._log(f"[ConfigManager] Legacy Documents registry path: {reg_path}")
                candidates.append(reg_path)
            except Exception as e:
                print(f"Warning: Failed to get legacy Documents path from registry: {e}", file=sys.stderr)

            candidates.append(Path.home() / "Documents")
            candidates.append(Path.home() / "文档")
        elif sys.platform == "darwin":
            candidates.append(Path.home() / "Documents")
        else:
            xdg_docs = os.getenv("XDG_DOCUMENTS_DIR", "").strip()
            if xdg_docs:
                candidates.append(Path(xdg_docs))
            candidates.append(Path.home() / "Documents")

        if getattr(sys, 'frozen', False):
            candidates.append(Path(sys.executable).parent)
        candidates.append(Path.cwd())
        return self._dedupe_paths(candidates)

    def _get_legacy_document_candidates(self):
        """Return legacy document-folder candidates only."""
        candidates = []

        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import windll, wintypes

                CSIDL_PERSONAL = 5
                SHGFP_TYPE_CURRENT = 0

                buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
                windll.shell32.SHGetFolderPathW(None, CSIDL_PERSONAL, None, SHGFP_TYPE_CURRENT, buf)
                api_path = Path(buf.value)
                candidates.append(api_path)

                if not api_path.exists() and api_path.drive:
                    drive = api_path.drive
                    for name in ("文档", "Documents", "My Documents"):
                        alt_path = Path(drive) / name
                        if alt_path.exists():
                            candidates.append(alt_path)
            except Exception:
                pass

            try:
                import winreg

                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
                )
                reg_path_str = winreg.QueryValueEx(key, "Personal")[0]
                winreg.CloseKey(key)
                reg_path = Path(os.path.expandvars(reg_path_str))
                candidates.append(reg_path)
            except Exception:
                pass

            candidates.append(Path.home() / "Documents")
            candidates.append(Path.home() / "文档")
        elif sys.platform == "darwin":
            candidates.append(Path.home() / "Documents")
        else:
            xdg_docs = os.getenv("XDG_DOCUMENTS_DIR", "").strip()
            if xdg_docs:
                candidates.append(Path(xdg_docs))
            candidates.append(Path.home() / "Documents")

        return self._dedupe_paths(candidates)

    def get_legacy_app_root_candidates(self):
        """返回旧版用户根目录候选（带 app_name），用于阶段 0 启动导入。"""
        roots = []
        current_root = str(self.app_docs_dir)
        for base_dir in self._get_legacy_storage_candidates():
            app_root = base_dir / self.app_name
            if str(app_root) == current_root:
                continue
            roots.append(app_root)
        return self._dedupe_paths(roots)
    
    def _get_documents_directory(self):
        """获取运行时数据根目录的父目录。

        方法名保留为历史兼容，但阶段 0 之后它优先返回标准应用数据目录，
        Documents / exe 目录 / cwd 仅作为旧数据导入与兜底候选。
        """
        primary_candidates = self._get_standard_data_directory_candidates()
        legacy_candidates = self._get_legacy_storage_candidates()
        legacy_document_candidates = self._get_legacy_document_candidates()
        candidates = self._dedupe_paths(primary_candidates + legacy_candidates)
        first_readable = next(
            (
                path
                for path in legacy_document_candidates
                if path.exists() and os.access(str(path), os.R_OK)
            ),
            None,
        )
        first_readable_non_writable = next(
            (
                path
                for path in legacy_document_candidates
                if path.exists()
                and os.access(str(path), os.R_OK)
                and not self._can_write_existing_directory(path)
            ),
            None,
        )
        for docs_dir in candidates:
            try:
                if docs_dir.exists():
                    if self._can_write_existing_directory(docs_dir):
                        self._log(f"[ConfigManager] ✓ Using app data directory: {docs_dir}")
                        self._first_readable_candidate = first_readable
                        self._first_non_writable_readable_candidate = first_readable_non_writable
                        return docs_dir
                    self._log(f"[ConfigManager] Path exists but not writable: {docs_dir}")
                    continue

                if not docs_dir.exists():
                    dirs_to_create = []
                    current = docs_dir
                    while current and not current.exists():
                        dirs_to_create.append(current)
                        current = current.parent
                        if current == current.parent:
                            break

                    for dir_path in reversed(dirs_to_create):
                        if not dir_path.exists():
                            dir_path.mkdir(parents=False, exist_ok=True)

                    test_path = docs_dir / ".test_neko_write"
                    test_path.touch()
                    test_path.unlink()
                    self._log(f"[ConfigManager] ✓ Using app data directory (created): {docs_dir}")
                    self._first_readable_candidate = first_readable
                    self._first_non_writable_readable_candidate = first_readable_non_writable
                    return docs_dir
            except Exception as e:
                self._log(f"[ConfigManager] Failed to use path {docs_dir}: {e}")
                continue

        self._first_readable_candidate = first_readable
        self._first_non_writable_readable_candidate = first_readable_non_writable
        fallback = Path.cwd()
        self._log(f"[ConfigManager] ⚠ All app data directories failed, using fallback: {fallback}")
        return fallback
    
    def _get_project_root(self):
        """获取项目根目录（私有方法）。

        源码模式固定基于本文件位置回溯到仓库根目录，避免 IDE / 外部 cwd
        导致 static、config、memory/store 等项目资源解析到错误位置。
        """
        if getattr(sys, 'frozen', False):
            # 如果是打包后的exe（PyInstaller）
            if hasattr(sys, '_MEIPASS'):
                # 单文件模式：使用临时解压目录
                return Path(sys._MEIPASS)
            else:
                # 多文件模式：使用 exe 同目录
                return Path(sys.executable).parent
        else:
            # 开发模式：固定使用仓库根目录
            return Path(__file__).resolve().parents[1]
    
    @property
    def project_root(self):
        """获取项目根目录（公共属性）"""
        return self._get_project_root()
    
    def _get_project_config_directory(self):
        """获取项目的config目录"""
        return self._get_project_root() / "config"
    
    def _get_project_memory_directory(self):
        """获取项目的memory/store目录"""
        return self._get_project_root() / "memory" / "store"
    
    def _ensure_app_docs_directory(self):
        """确保应用文档目录存在（N.E.K.O目录本身）"""
        try:
            # 先确保父目录（docs_dir）存在
            if not self.docs_dir.exists():
                print(f"Warning: Documents directory does not exist: {self.docs_dir}", file=sys.stderr)
                print("Warning: Attempting to create documents directory...", file=sys.stderr)
                try:
                    # 尝试创建父目录（可能需要创建多级）
                    dirs_to_create = []
                    current = self.docs_dir
                    while current and not current.exists():
                        dirs_to_create.append(current)
                        current = current.parent
                        # 防止无限循环，到达根目录就停止
                        if current == current.parent:
                            break
                    
                    # 从最顶层开始创建目录
                    for dir_path in reversed(dirs_to_create):
                        if not dir_path.exists():
                            print(f"Creating directory: {dir_path}", file=sys.stderr)
                            dir_path.mkdir(exist_ok=True)
                except Exception as e2:
                    print(f"Warning: Failed to create documents directory: {e2}", file=sys.stderr)
                    return False
            
            # 创建应用目录
            if not self.app_docs_dir.exists():
                print(f"Creating app directory: {self.app_docs_dir}", file=sys.stderr)
                self.app_docs_dir.mkdir(exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create app directory {self.app_docs_dir}: {e}", file=sys.stderr)
            return False

    def _ensure_anchor_root_directory(self):
        """确保锚点目录存在（固定承载 cloudsave/state）。"""
        try:
            self.anchor_root.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create anchor directory {self.anchor_root}: {e}", file=sys.stderr)
            return False
    
    def ensure_config_directory(self):
        """确保我的文档下的config目录存在"""
        try:
            # 先确保app_docs_dir存在
            if not self._ensure_app_docs_directory():
                return False
            
            self.config_dir.mkdir(exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create config directory: {e}", file=sys.stderr)
            return False
    
    def ensure_memory_directory(self):
        """确保我的文档下的memory目录存在"""
        try:
            # 先确保app_docs_dir存在
            if not self._ensure_app_docs_directory():
                return False
            
            self.memory_dir.mkdir(exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create memory directory: {e}", file=sys.stderr)
            return False

    def ensure_plugins_directory(self):
        """确保我的文档下的plugins目录存在"""
        try:
            if not self._ensure_app_docs_directory():
                return False

            self.plugins_dir.mkdir(exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create plugins directory: {e}", file=sys.stderr)
            return False
    
    def ensure_live2d_directory(self):
        """确保我的文档下的live2d目录存在"""
        try:
            # 先确保app_docs_dir存在
            if not self._ensure_app_docs_directory():
                return False

            self.live2d_dir.mkdir(exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create live2d directory: {e}", file=sys.stderr)
            return False

    @property
    def readable_live2d_dir(self):
        """原始 Documents 下的 live2d 目录（只读，用于 CFA 场景）。

        当 Windows 受控文件夹访问(CFA/反勒索防护) 阻止写入 Documents 时，
        写入操作回退到 AppData，但用户的模型文件仍在原始 Documents 中。
        此属性返回原始 Documents 中的 live2d 路径以供读取。

        非 CFA 场景下返回 None（此时 live2d_dir 本身就指向 Documents）。
        """
        if self.is_windows_cfa_fallback_active and self._readable_docs_dir is not None:
            p = self._readable_docs_dir / self.app_name / "live2d"
            if p.exists():
                return p
        return None

    @property
    def is_windows_cfa_fallback_active(self) -> bool:
        """是否处于 Windows CFA 读写分离模式。"""
        if self._readable_docs_dir is None:
            return False
        write_docs_dir = getattr(self, "_cfa_fallback_write_docs_dir", None)
        if write_docs_dir is None:
            return False
        current_write_docs_dir = Path(self.app_docs_dir).parent
        return str(self._readable_docs_dir) != str(current_write_docs_dir) and str(write_docs_dir) == str(current_write_docs_dir)

    def get_live2d_lookup_roots(self, *, prefer_writable: bool = True) -> list[Path]:
        """返回 Live2D 查找路径（去重后）。

        默认优先可写运行时目录，命中失败时回退到只读 legacy 目录，
        避免 CFA 模式下“新导入模型存在但仍优先命中旧目录”。
        """
        readable = self.readable_live2d_dir
        writable = Path(self.live2d_dir)
        ordered_candidates = [writable, readable] if prefer_writable else [readable, writable]

        roots: list[Path] = []
        seen: set[str] = set()
        for candidate in ordered_candidates:
            if not candidate:
                continue
            normalized = os.path.normcase(os.path.normpath(str(candidate)))
            if normalized in seen:
                continue
            seen.add(normalized)
            roots.append(Path(candidate))
        return roots

    def ensure_vrm_directory(self):
        """确保用户文档目录下的vrm目录和animation子目录存在"""
        try:
            # 先确保app_docs_dir存在
            if not self._ensure_app_docs_directory():
                return False
            # 创建vrm目录
            self.vrm_dir.mkdir(parents=True, exist_ok=True)
            # 创建animation子目录
            self.vrm_animation_dir.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create vrm directory: {e}", file=sys.stderr)
            return False
    
    def ensure_mmd_directory(self):
        """确保用户文档目录下的mmd目录和animation子目录存在"""
        try:
            if not self._ensure_app_docs_directory():
                return False
            self.mmd_dir.mkdir(parents=True, exist_ok=True)
            self.mmd_animation_dir.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create mmd directory: {e}", file=sys.stderr)
            return False
        
    def ensure_chara_directory(self):
        """确保我的文档下的character_cards目录存在"""
        try:
            # 先确保app_docs_dir存在
            if not self._ensure_app_docs_directory():
                return False
            
            self.chara_dir.mkdir(exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create character_cards directory: {e}", file=sys.stderr)
            return False

    def ensure_card_faces_directory(self):
        """确保我的文档下的card_faces目录存在"""
        try:
            if not self._ensure_app_docs_directory():
                return False
            self.card_faces_dir.mkdir(exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create card_faces directory: {e}", file=sys.stderr)
            return False

    def card_face_meta_path(self, name: str):
        """返回猫娘卡面元数据 sidecar 文件路径（card_faces/{name}.json）。

        不做存在性检查，调用方需自行处理。仅用于读写 sidecar 元数据
        （作者、创建时间、来源等）。
        """
        return self.card_faces_dir / f"{name}.json"

    def ensure_cloudsave_structure(self):
        """确保本地 cloudsave 基础目录存在。

        这里只创建目录骨架和本地工作区，不创建 manifest 内容，
        以便阶段 0 先落地路径与状态基础设施，不改变现有同步语义。
        """
        try:
            if not self._ensure_anchor_root_directory():
                return False

            for directory in (
                self.cloudsave_dir,
                self.cloudsave_catalog_dir,
                self.cloudsave_profiles_dir,
                self.cloudsave_bindings_dir,
                self.cloudsave_memory_dir,
                self.cloudsave_overrides_dir,
                self.cloudsave_meta_dir,
                self.cloudsave_workshop_meta_dir,
                self.cloudsave_staging_dir,
                self.cloudsave_backups_dir,
            ):
                directory.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create cloudsave structure: {e}", file=sys.stderr)
            return False

    def ensure_local_state_directory(self):
        """确保本地状态目录存在。"""
        try:
            if not self._ensure_anchor_root_directory():
                return False
            self.local_state_dir.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create local state directory: {e}", file=sys.stderr)
            return False

    def build_default_root_state(self):
        """构建默认 root_state 内容。"""
        return {
            "version": self.ROOT_STATE_VERSION,
            "mode": "normal",
            "current_root": str(self.app_docs_dir),
            "last_known_good_root": str(self.app_docs_dir),
            "last_migration_source": "",
            "last_migration_backup": "",
            "last_migration_result": "",
            "last_successful_boot_at": "",
            "legacy_cleanup_pending": False,
        }

    def build_default_cloudsave_local_state(self, *, client_id=None):
        """构建默认 cloudsave_local_state 内容。"""
        return {
            "version": self.CLOUDSAVE_LOCAL_STATE_VERSION,
            "client_id": str(client_id or uuid.uuid4().hex),
            "next_sequence_number": 1,
            "last_applied_manifest_fingerprint": "",
            "last_successful_export_at": "",
            "last_successful_import_at": "",
        }

    def build_default_character_tombstones_state(self):
        """构建默认角色 tombstone 本地状态。"""
        return {
            "version": self.CHARACTER_TOMBSTONES_STATE_VERSION,
            "tombstones": [],
        }

    def _load_json_file(self, path, default_value=None):
        """加载任意 JSON 文件；文件缺失时返回默认值副本。"""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            if default_value is not None:
                return deepcopy(default_value)
            raise
        except Exception as e:
            logger.error("加载 JSON 文件失败: path=%s error=%s", path, e)
            raise

    def _save_json_file(self, path, data):
        """原子保存任意 JSON 文件。"""
        atomic_write_json(path, data, ensure_ascii=False, indent=2)

    def load_root_state(self, default_value=None):
        """加载 root_state；缺失时返回默认状态。"""
        if default_value is None:
            default_value = self.build_default_root_state()
        state = self._load_json_file(self.root_state_path, default_value)
        if self._has_selected_root_unavailable_recovery_override():
            return self._build_selected_root_unavailable_recovery_state(state)
        return state

    def save_root_state(self, data):
        """保存 root_state。"""
        if not self.ensure_local_state_directory():
            raise OSError("Failed to ensure local state directory before saving root_state")
        self._save_json_file(self.root_state_path, data)

    def load_cloudsave_local_state(self, default_value=None):
        """加载 cloudsave_local_state；缺失时返回带稳定字段结构的默认值。"""
        if default_value is None:
            default_value = self.build_default_cloudsave_local_state()
        return self._load_json_file(self.cloudsave_local_state_path, default_value)

    def save_cloudsave_local_state(self, data):
        """保存 cloudsave_local_state。"""
        if not self.ensure_local_state_directory():
            raise OSError("Failed to ensure local state directory before saving cloudsave_local_state")
        self._save_json_file(self.cloudsave_local_state_path, data)

    def load_character_tombstones_state(self, default_value=None):
        """加载角色 tombstone 本地状态。"""
        if default_value is None:
            default_value = self.build_default_character_tombstones_state()
        return self._load_json_file(self.character_tombstones_state_path, default_value)

    def save_character_tombstones_state(self, data):
        """保存角色 tombstone 本地状态。"""
        if not self.ensure_local_state_directory():
            raise OSError("Failed to ensure local state directory before saving character_tombstones_state")
        self._save_json_file(self.character_tombstones_state_path, data)

    def ensure_cloudsave_state_files(self):
        """确保本地 cloudsave 相关状态文件存在，返回是否发生创建。"""
        created = False
        if not self.ensure_local_state_directory():
            raise RuntimeError(
                "Failed to initialize local state directory for "
                f"{self.root_state_path.name}, "
                f"{self.cloudsave_local_state_path.name}, and "
                f"{self.character_tombstones_state_path.name}"
            )

        if not self.root_state_path.exists():
            self.save_root_state(self.build_default_root_state())
            created = True
        if not self.cloudsave_local_state_path.exists():
            self.save_cloudsave_local_state(self.build_default_cloudsave_local_state())
            created = True
        if not self.character_tombstones_state_path.exists():
            self.save_character_tombstones_state(self.build_default_character_tombstones_state())
            created = True
        return created
    
    def get_config_path(self, filename):
        """
        获取配置文件路径
        
        优先级：
        1. 我的文档/{APP_NAME}/config/
        2. 项目目录/config/
        
        Args:
            filename: 配置文件名
            
        Returns:
            Path: 配置文件路径
        """
        # 首选：我的文档下的配置
        docs_config_path = self.config_dir / filename
        if docs_config_path.exists():
            return docs_config_path
        
        # 备选：项目目录下的配置
        project_config_path = self.project_config_dir / filename
        if project_config_path.exists():
            return project_config_path
        
        # 都不存在，返回我的文档路径（用于创建新文件）
        return docs_config_path

    def get_runtime_config_path(self, filename):
        """获取运行时真源配置路径（始终位于 app_docs_dir/config）。"""
        return self.config_dir / filename
    
    def _get_localized_characters_source(self):
        """根据用户语言获取本地化的 characters.json 源文件路径。
        
        Returns:
            Path | None: 本地化文件路径，如果无法检测语言或文件不存在则返回 None（回退到默认）
        """
        try:
            from utils.language_utils import _get_steam_language, _get_system_language, normalize_language_code
            
            # 优先使用 Steam 语言，其次系统语言
            raw_lang = _get_steam_language()
            if not raw_lang:
                raw_lang = _get_system_language()
            if not raw_lang:
                return None
            
            lang = normalize_language_code(raw_lang, format='full')
        except Exception as e:
            self._log(f"[ConfigManager] Failed to detect language for characters config: {e}")
            return None
        
        if not lang:
            return None
        
        # 映射语言代码到文件后缀
        lang_lower = lang.lower()
        if lang_lower in ('zh-cn', 'zh'):
            suffix = 'zh-CN'
        elif 'tw' in lang_lower or 'hk' in lang_lower:
            suffix = 'zh-TW'
        elif lang_lower.startswith('ja'):
            suffix = 'ja'
        elif lang_lower.startswith('en'):
            suffix = 'en'
        elif lang_lower.startswith('ko'):
            suffix = 'ko'
        elif lang_lower.startswith('ru'):
            suffix = 'ru'
        else:
            # 未知语言，回退
            return None
        
        localized_path = self.project_config_dir / f"characters.{suffix}.json"
        return localized_path if localized_path.exists() else None
    
    def migrate_config_files(self):
        """
        迁移配置文件到我的文档
        
        策略：
        1. 检查我的文档下的config文件夹，没有就创建
        2. 对于每个配置文件：
           - 如果我的文档下有，跳过
           - 如果我的文档下没有：
             - characters.json: 根据语言选择本地化版本，回退到默认
             - 其他文件: 从项目config复制
           - 如果都没有，不做处理（后续会创建默认值）
        """
        # 确保目录存在
        if not self.ensure_config_directory():
            print("Warning: Cannot create config directory, using project config", file=sys.stderr)
            return
        
        # 显示项目配置目录位置（调试用）
        self._log(f"[ConfigManager] Project config directory: {self.project_config_dir}")
        self._log(f"[ConfigManager] User config directory: {self.config_dir}")
        
        # 迁移每个配置文件
        for filename in CONFIG_FILES:
            docs_config_path = self.config_dir / filename
            project_config_path = self.project_config_dir / filename
            
            # 如果我的文档下已有，跳过
            if docs_config_path.exists():
                self._log(f"[ConfigManager] Config already exists: {filename}")
                continue
            
            # 对 characters.json 特殊处理：根据语言选择本地化版本
            if filename == 'characters.json':
                lang_source = self._get_localized_characters_source()
                if lang_source:
                    try:
                        shutil.copy2(lang_source, docs_config_path)
                        self._log(f"[ConfigManager] ✓ Migrated localized config: {lang_source.name} -> {docs_config_path}")
                        continue
                    except Exception as e:
                        self._log(f"Warning: Failed to migrate localized {lang_source.name}: {e}")
                        # 继续走默认拷贝逻辑
            
            # 如果项目config下有，复制过去
            if project_config_path.exists():
                try:
                    shutil.copy2(project_config_path, docs_config_path)
                    self._log(f"[ConfigManager] ✓ Migrated config: {filename} -> {docs_config_path}")
                except Exception as e:
                    self._log(f"Warning: Failed to migrate {filename}: {e}")
            else:
                if filename in DEFAULT_CONFIG_DATA:
                    self._log(f"[ConfigManager] ~ Using in-memory default for {filename}")
                else:
                    self._log(f"[ConfigManager] ✗ Source config not found: {project_config_path}")
    
    def migrate_memory_files(self):
        """
        迁移记忆文件到我的文档
        
        策略：
        1. 检查我的文档下的memory文件夹，没有就创建
        2. 迁移所有记忆文件和目录
        """
        # 确保目录存在
        if not self.ensure_memory_directory():
            self._log("Warning: Cannot create memory directory, using project memory")
            return
        
        # 如果项目memory/store目录不存在，跳过
        if not self.project_memory_dir.exists():
            return
        
        # 迁移所有记忆文件
        try:
            for item in self.project_memory_dir.iterdir():
                dest_path = self.memory_dir / item.name
                
                # 如果目标已存在，跳过
                if dest_path.exists():
                    continue
                
                # 复制文件或目录
                if item.is_file():
                    shutil.copy2(item, dest_path)
                    print(f"Migrated memory file: {item.name}")
                elif item.is_dir():
                    shutil.copytree(item, dest_path)
                    print(f"Migrated memory directory: {item.name}")
        except Exception as e:
            print(f"Warning: Failed to migrate memory files: {e}", file=sys.stderr)

    def migrate_legacy_documents_memory(self):
        """
        启动时对 legacy 根目录（``Documents\\N.E.K.O`` / CFA 原始只读路径等）
        下的 ``memory/`` 仅做**软迁移**：把仍在 ``characters.json[猫娘]``
        的角色目录搬到当前 runtime ``memory_dir``；runtime 已有同名目录则
        保留 legacy 副本并打印 warning，绝不覆盖。

        **未关联条目**（目录名不在 ``characters.json[猫娘]`` 的孤立记忆）
        不在本方法处理范围内，完全交由创意工坊页面的"清理遗留记忆"按钮
        走 ``/api/memory/legacy/scan`` + ``purge`` 由用户主动勾选删除。

        该方法应在 ``migrate_config_files`` / ``migrate_memory_files`` 之后
        调用，此时 ``characters.json`` 已就位。任何失败只打日志不抛异常，
        绝不阻塞启动流程。
        """
        try:
            # get_legacy_app_root_candidates 已排除当前 app_docs_dir，且去重
            legacy_roots = list(self.get_legacy_app_root_candidates() or [])
        except Exception as exc:
            self._log(
                f"[ConfigManager] migrate_legacy_documents_memory: 获取 legacy roots 失败: {exc}"
            )
            return

        # CFA 回退场景：_readable_docs_dir 是只读原 Documents，也要纳入。
        # 只读根意味着 rmtree 永远失败、target 永远存在，下面会基于
        # readonly_legacy_roots 跳过 rmtree 并静默 target_exists 噪音，
        # 避免每次启动都打"清理失败/已存在"的重复日志。
        readonly_legacy_roots: set[str] = set()
        readable_docs = getattr(self, "_readable_docs_dir", None)
        if readable_docs:
            try:
                extra = Path(readable_docs) / self.app_name
                extra_str = str(extra)
                if all(extra_str != str(existing) for existing in legacy_roots):
                    legacy_roots.append(extra)
                readonly_legacy_roots.add(extra_str)
            except Exception:
                pass

        if not legacy_roots:
            return

        try:
            characters = self.load_characters()
        except Exception as exc:
            self._log(
                f"[ConfigManager] migrate_legacy_documents_memory: 加载 characters.json 失败: {exc}"
            )
            return

        # characters.json 是用户可写边界；"猫娘" 字段若被损坏成 list / 字符串等
        # 非空但非 dict 的值，.keys() 会抛 AttributeError 并被外层吞掉。
        catgirl_map = characters.get("猫娘")
        if not isinstance(catgirl_map, dict):
            if catgirl_map is not None:
                self._log(
                    f"[ConfigManager] migrate_legacy_documents_memory: "
                    f"characters.json 中猫娘字段类型异常 "
                    f"({type(catgirl_map).__name__})，跳过本次软迁移"
                )
            else:
                self._log(
                    "[ConfigManager] migrate_legacy_documents_memory: "
                    "characters.json 中无猫娘字段，跳过本次软迁移"
                )
            return

        known_characters = set(catgirl_map.keys())
        if not known_characters:
            # characters.json 异常/为空时无从判断哪些应当迁移，直接退出。
            self._log(
                "[ConfigManager] migrate_legacy_documents_memory: "
                "characters.json 中无角色，跳过本次软迁移"
            )
            return

        # 分项计数便于运维排查"到底为什么没迁"。隐藏/下划线前缀、未关联角色
        # 这两类 skip 是正常 no-op，不单独计数。
        migrated_count = 0
        target_exists_count = 0  # runtime 已存在同名目录，保留 legacy 副本
        non_dir_count = 0  # 命中角色名但条目不是目录（反常，需关注）
        failed_count = 0  # copytree/rename 失败

        def _legacy_error_summary(exc: BaseException) -> str:
            """
            把异常压成脱敏字符串：只保留类名 + errno + strerror，
            绝不打印 OSError/PermissionError 自带的 filename 参数（那会
            暴露 Documents 用户名 + 角色目录名）。
            """
            if isinstance(exc, OSError):
                parts = [type(exc).__name__]
                if exc.errno is not None:
                    parts.append(f"errno={exc.errno}")
                strerror = getattr(exc, "strerror", None)
                if strerror:
                    parts.append(f"reason={strerror}")
                return " ".join(parts)
            return type(exc).__name__

        # 日志脱敏策略：所有 self._log 绝不包含完整 legacy 路径 / 角色目录名 /
        # 用户 Documents 路径，只打 root 序号 + 计数 + 条目类型。这些日志可能
        # 被收集到日志文件或遥测，泄露用户本地信息不值当。
        for legacy_root_index, legacy_root in enumerate(legacy_roots, start=1):
            source_is_readonly = str(legacy_root) in readonly_legacy_roots
            try:
                legacy_memory = Path(legacy_root) / "memory"
            except Exception:
                continue
            if not legacy_memory.exists() or not legacy_memory.is_dir():
                continue
            # 保护：绝不处理 runtime memory 自身（防御性重复检查）
            try:
                if legacy_memory.resolve() == Path(self.memory_dir).resolve():
                    continue
            except Exception:
                pass

            # Per-root 兜底：权限错误或 I/O 错误不应中断后续 legacy roots 的迁移
            try:
                legacy_entries = list(legacy_memory.iterdir())
            except Exception as exc:
                self._log(
                    f"[ConfigManager] 枚举 legacy memory 根 #{legacy_root_index} "
                    f"失败，跳过该根: {_legacy_error_summary(exc)}"
                )
                continue

            for entry in legacy_entries:
                try:
                    entry_name = entry.name
                    # 只过滤真正的隐藏条目（dot-file），其它形态的合法性交给
                    # known_characters 裁定——用户如果把角色命名为 "_foo"，
                    # 之前的 "_" 前缀黑名单会直接把它当临时条目静默跳过。
                    if entry_name.startswith("."):
                        continue

                    # 未关联条目交给手动清理按钮，此处不做任何操作
                    if entry_name not in known_characters:
                        continue

                    # runtime 角色记忆期望是目录结构（memory_dir/{name}/time_indexed.db
                    # 等）；同名普通文件会占位并阻断后续写入，必须跳过。
                    if not entry.is_dir():
                        non_dir_count += 1
                        self._log(
                            f"[ConfigManager] legacy memory 根 #{legacy_root_index}: "
                            f"命中角色名的条目不是目录（类型异常），跳过自动软迁移"
                        )
                        continue

                    target = self.memory_dir / entry_name
                    # target.exists() 对断链软链接返回 False（跟随软链找不到目标），
                    # 但 os.replace 会直接覆盖该软链接，违反"绝不覆盖 runtime 已有
                    # 目标"的语义。is_symlink() 不跟随，把断链也当成"已存在"。
                    if target.exists() or target.is_symlink():
                        # 只读根（如 CFA _readable_docs_dir）上的源永远删不掉，
                        # target 存在是上一次成功迁移后的常态；静默跳过以免每次
                        # 启动都打"已存在"日志噪音。可写根仍正常计数 + 打日志。
                        if not source_is_readonly:
                            target_exists_count += 1
                            self._log(
                                f"[ConfigManager] legacy memory 根 #{legacy_root_index}: "
                                f"目标已存在于 runtime，保留 legacy 副本避免覆盖"
                            )
                        continue
                    # 跨盘 shutil.move 退化为 copy 时若半途失败，target 可能已
                    # 存在但不完整，下次启动会被 target.exists() 跳过。改为
                    # "复制到同父级临时路径 → 原子 rename → best-effort 清源"。
                    temp_target = target.parent / f".{entry_name}.migrating-{uuid.uuid4().hex}"
                    try:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        # symlinks=False：跟随 legacy 源里的软链，把实际内容拷到
                        # runtime。若保留软链（symlinks=True），legacy 里用户手动
                        # 创建的、指向 memory_dir 外部的链接会让 runtime 的
                        # memory_dir/{name}/time_indexed.db 写入逃出边界。
                        shutil.copytree(str(entry), str(temp_target), symlinks=False)
                        os.replace(str(temp_target), str(target))
                        # 只读根（CFA _readable_docs_dir）上根本不可写，rmtree
                        # 永远会抛 PermissionError。成功迁移后直接跳过清源，
                        # 避免每次启动都打一遍"legacy 源清理失败"日志。
                        if not source_is_readonly:
                            try:
                                shutil.rmtree(str(entry))
                            except Exception as cleanup_exc:
                                self._log(
                                    f"[ConfigManager] legacy memory 根 #{legacy_root_index}: "
                                    f"已复制到 runtime，但 legacy 源清理失败，保留 legacy 副本: "
                                    f"{_legacy_error_summary(cleanup_exc)}"
                                )
                        migrated_count += 1
                        self._log(
                            f"[ConfigManager] legacy memory 根 #{legacy_root_index}: "
                            f"已迁移 1 个条目到 runtime"
                        )
                    except Exception as exc:
                        failed_count += 1
                        # 清理可能残留的临时目录/文件，避免下次启动误判
                        try:
                            if temp_target.exists():
                                if temp_target.is_dir():
                                    shutil.rmtree(str(temp_target), ignore_errors=True)
                                else:
                                    temp_target.unlink()
                        except Exception:
                            pass
                        self._log(
                            f"[ConfigManager] legacy memory 根 #{legacy_root_index}: "
                            f"迁移条目失败: {_legacy_error_summary(exc)}"
                        )
                except Exception as exc:
                    failed_count += 1
                    self._log(
                        f"[ConfigManager] legacy memory 根 #{legacy_root_index}: "
                        f"处理条目时出错: {_legacy_error_summary(exc)}"
                    )

        if migrated_count or target_exists_count or non_dir_count or failed_count:
            self._log(
                f"[ConfigManager] legacy memory 软迁移汇总: "
                f"迁移 {migrated_count} 个, "
                f"目标已存在跳过 {target_exists_count} 个, "
                f"非目录跳过 {non_dir_count} 个, "
                f"失败 {failed_count} 个"
            )
    
    # --- Character configuration helpers ---

    def get_default_characters(self):
        """获取默认角色配置数据（根据Steam语言本地化内容值）"""
        from config import get_localized_default_characters
        return get_localized_default_characters()

    def load_characters(self, character_json_path=None):
        """加载角色配置"""
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
        """保存角色配置（同步版本，会阻塞事件循环；async 路径请用 asave_characters）"""
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
        """async 包装：事件循环上禁止直接走同步版本（atomic_write_json 会阻塞）。"""
        return await asyncio.to_thread(
            self.save_characters,
            data,
            character_json_path,
            bypass_write_fence=bypass_write_fence,
        )

    # --- Voice storage helpers ---

    def load_voice_storage(self):
        """加载音色配置存储"""
        try:
            return self.load_json_config('voice_storage.json', default_value=deepcopy(DEFAULT_CONFIG_DATA['voice_storage.json']))
        except Exception as e:
            logger.error("加载音色配置失败: %s", e)
            return {}

    def save_voice_storage(self, data):
        """保存音色配置存储"""
        try:
            self.save_json_config('voice_storage.json', data)
        except Exception as e:
            logger.error("保存音色配置失败: %s", e)
            raise

    @staticmethod
    def is_legacy_cosyvoice_id(voice_id: str) -> bool:
        """CosyVoice v2 / v3 的克隆音色 ID 已随 CosyVoice 3.5 升级而失效。"""
        return bool(voice_id) and (
            voice_id.startswith("cosyvoice-v2") or voice_id.startswith("cosyvoice-v3-")
        )

    def get_tts_api_key(self, provider: str) -> str | None:
        """根据 provider 统一获取 TTS API Key，返回 None 表示未配置。

        - cosyvoice: tts_custom 配置的 api_key
        - minimax:   ASSIST_API_KEY_MINIMAX → MINIMAX_API_KEY fallback
        - minimax_intl: ASSIST_API_KEY_MINIMAX_INTL → MINIMAX_INTL_API_KEY fallback
        """
        if provider == 'cosyvoice':
            tts_config = self.get_model_api_config('tts_custom')
            key = (tts_config.get('api_key') or '').strip()
            return key or None
        if provider in ('minimax', 'minimax_intl'):
            core_config = self.get_core_config()
            if provider == 'minimax_intl':
                key = (core_config.get('ASSIST_API_KEY_MINIMAX_INTL') or '').strip()
            else:
                key = (core_config.get('ASSIST_API_KEY_MINIMAX') or '').strip()
            if not key:
                try:
                    import utils.minimax_api_keys as _mm_keys
                    fallback = getattr(_mm_keys, 'MINIMAX_INTL_API_KEY', None) if provider == 'minimax_intl' else getattr(_mm_keys, 'MINIMAX_API_KEY', None)
                    key = (fallback or '').strip()
                except ImportError:
                    logger.debug("utils.minimax_api_keys not found, no fallback MiniMax keys available")
            return key or None
        return None

    def _get_minimax_storage_keys(self) -> list[str]:
        """返回当前 MiniMax API Key 对应的 voice_storage key 列表。

        通过 get_tts_api_key 获取已解析的 key（含 env fallback），
        分别为国服和国际服生成 bucket 前缀。
        """
        voice_storage = self.load_voice_storage()
        result = []

        # 国服 key → __MINIMAX__{suffix}
        cn_key = self.get_tts_api_key('minimax')
        if cn_key:
            suffix = cn_key[-8:] if len(cn_key) >= 8 else cn_key
            bucket = f'__MINIMAX__{suffix}'
            if bucket in voice_storage:
                result.append(bucket)

        # 国际服 key → __MINIMAX_INTL__{suffix}
        intl_key = self.get_tts_api_key('minimax_intl')
        if intl_key:
            suffix = intl_key[-8:] if len(intl_key) >= 8 else intl_key
            bucket = f'__MINIMAX_INTL__{suffix}'
            if bucket in voice_storage:
                result.append(bucket)

        return result

    @staticmethod
    def _infer_provider_from_storage_key(storage_key: str) -> str:
        """根据 voice_storage 的分区 key 推断 provider（仅用于兼容旧数据）。"""
        if storage_key == '__LOCAL_TTS__':
            return 'local'
        if storage_key.startswith('__MINIMAX_INTL__'):
            return 'minimax_intl'
        if storage_key.startswith('__MINIMAX__'):
            return 'minimax'
        return 'cosyvoice'

    def get_voices_for_current_api(self):
        """获取当前 TTS 配置对应的所有音色

        根据实际使用的 TTS 配置返回音色：
        1. 本地 TTS（ws/wss 协议）→ 返回 __LOCAL_TTS__ 下的音色
        2. 阿里云 TTS（通过 ASSIST_API_KEY_QWEN）→ 返回该 API Key 下的音色
        3. 其他情况 → 返回 AUDIO_API_KEY 下的音色
        结果中同时合并 MiniMax 音色（__MINIMAX__ 下的音色）。

        返回的每个 voice_data 都保证包含 ``provider`` 字段
        （``local`` / ``minimax`` / ``minimax_intl`` / ``cosyvoice``）。
        """
        voice_storage = self.load_voice_storage()

        tts_config = self.get_model_api_config('tts_custom')
        base_url = tts_config.get('base_url', '')
        is_local_tts = tts_config.get('is_custom') and base_url.startswith(('ws://', 'wss://'))

        if is_local_tts:
            storage_key = '__LOCAL_TTS__'
            all_voices = voice_storage.get(storage_key, {})
            result = dict(all_voices)
        else:
            tts_api_key = tts_config.get('api_key', '')
            if tts_api_key:
                storage_key = tts_api_key
                all_voices = voice_storage.get(storage_key, {})
                result = dict(all_voices)
            else:
                core_config = self.get_core_config()
                audio_api_key = core_config.get('AUDIO_API_KEY', '')
                if not audio_api_key:
                    storage_key = ''
                    result = {}
                else:
                    storage_key = audio_api_key
                    all_voices = voice_storage.get(storage_key, {})
                    result = dict(all_voices)

        # 确保主分区音色有 provider 字段
        default_provider = self._infer_provider_from_storage_key(storage_key) if storage_key else 'cosyvoice'
        for vdata in result.values():
            if isinstance(vdata, dict) and 'provider' not in vdata:
                vdata['provider'] = default_provider

        # 合并 MiniMax 音色，并确保 provider 字段
        for mk in self._get_minimax_storage_keys():
            mm_provider = self._infer_provider_from_storage_key(mk)
            minimax_voices = voice_storage.get(mk, {})
            for vid, vdata in minimax_voices.items():
                if vid not in result:
                    if isinstance(vdata, dict) and 'provider' not in vdata:
                        vdata['provider'] = mm_provider
                    result[vid] = vdata

        return result

    def save_voice_for_current_api(self, voice_id, voice_data):
        """为当前 AUDIO_API_KEY 保存音色"""
        core_config = self.get_core_config()
        audio_api_key = core_config.get('AUDIO_API_KEY', '')

        if not audio_api_key:
            raise ValueError("未配置 AUDIO_API_KEY")

        voice_storage = self.load_voice_storage()
        if audio_api_key not in voice_storage:
            voice_storage[audio_api_key] = {}

        voice_storage[audio_api_key][voice_id] = voice_data
        self.save_voice_storage(voice_storage)

    def save_voice_for_api_key(self, api_key: str, voice_id: str, voice_data: dict):
        """为指定的 API Key 保存音色（用于复刻时使用实际 API Key 而非 AUDIO_API_KEY）"""
        if not api_key:
            raise ValueError("API Key 不能为空")

        voice_storage = self.load_voice_storage()
        if api_key not in voice_storage:
            voice_storage[api_key] = {}

        voice_storage[api_key][voice_id] = voice_data
        self.save_voice_storage(voice_storage)

    def find_voice_by_audio_md5(self, api_key: str, audio_md5: str, ref_language: str | None = None):
        """在指定 API Key 下按参考音频 MD5（及可选 ref_language）查找已有音色。

        返回 (voice_id, voice_data) 或 None。
        旧条目没有 audio_md5 字段时会被自动跳过（向后兼容）。
        当 ref_language 不为 None 时，要求 voice_data 中的 ref_language 也匹配
        （旧条目无 ref_language 字段视为 'ch'）。
        """
        if not api_key or not audio_md5:
            return None
        voice_storage = self.load_voice_storage()
        voices = voice_storage.get(api_key, {})
        for vid, vdata in voices.items():
            if isinstance(vdata, dict) and vdata.get('audio_md5') == audio_md5:
                if ref_language is not None and vdata.get('ref_language', 'ch') != ref_language:
                    continue
                return (vid, vdata)
        return None

    def delete_voice_for_current_api(self, voice_id):
        """删除当前 TTS 配置下的指定音色（含 MiniMax 音色）"""
        voice_storage = self.load_voice_storage()

        # 先检查 MiniMax 存储（__MINIMAX__ / __MINIMAX_INTL__ 开头的 key）
        for storage_key in list(voice_storage.keys()):
            if (storage_key.startswith('__MINIMAX__') or storage_key.startswith('__MINIMAX_INTL__')) and voice_id in voice_storage.get(storage_key, {}):
                del voice_storage[storage_key][voice_id]
                self.save_voice_storage(voice_storage)
                return True
        
        tts_config = self.get_model_api_config('tts_custom')
        base_url = tts_config.get('base_url', '')
        is_local_tts = tts_config.get('is_custom') and base_url.startswith(('ws://', 'wss://'))

        if is_local_tts:
            api_key = '__LOCAL_TTS__'
        else:
            api_key = tts_config.get('api_key', '')
            if not api_key:
                core_config = self.get_core_config()
                api_key = core_config.get('AUDIO_API_KEY', '')

        if not api_key:
            return False

        if api_key not in voice_storage:
            return False

        if voice_id in voice_storage[api_key]:
            del voice_storage[api_key][voice_id]
            self.save_voice_storage(voice_storage)
            return True
        return False

    def validate_voice_id(self, voice_id):
        """校验 voice_id 是否在当前 AUDIO_API_KEY 下有效。
        
        校验覆盖四类 voice_id：
          1. "cosyvoice-v2/v3..." → 旧版格式，始终无效
          2. "gsv:xxx" → 委托 check_custom_tts_voice_allowed (custom_tts_adapter)
             判定，由适配器根据 tts_custom 配置决定有效性
          3. 普通 ID → 在 voice_storage (CosyVoice 云端克隆音色) 中查找
          4. 免费预设音色 → 这里只做静态白名单放行；运行时由 core.py
             _should_block_free_preset_voice 根据线路 (lanlan.tech / lanlan.app)
             动态决定是否实际启用（lanlan.app 海外节点不支持预设音色）
        """
        if not voice_id:
            return True

        custom_tts_allowed = check_custom_tts_voice_allowed(voice_id, self.get_model_api_config)
        if custom_tts_allowed is not None:
            return custom_tts_allowed

        voices = self.get_voices_for_current_api()
        if voice_id in voices:
            return True

        # 免费预设音色允许豁免保存校验，运行时再由 core.py 按当前线路动态判断可用性
        from utils.api_config_loader import get_free_voices
        free_voices = get_free_voices()
        if voice_id in free_voices.values():
            return True

        return False

    def validate_voice_id_for_api_key(self, api_key: str, voice_id: str) -> bool:
        """校验 voice_id 是否在指定 API Key 下有效"""
        if not voice_id:
            return True

        custom_tts_allowed = check_custom_tts_voice_allowed(voice_id, self.get_model_api_config)
        if custom_tts_allowed is not None:
            return custom_tts_allowed

        voice_storage = self.load_voice_storage()
        voices = voice_storage.get(api_key, {})
        if voice_id in voices:
            return True

        from utils.api_config_loader import get_free_voices
        free_voices = get_free_voices()
        if voice_id in free_voices.values():
            return True

        return False

    def cleanup_invalid_voice_ids(self):
        """清理 characters.json 中无效的 voice_id。
        
        通过 validate_voice_id 统一判定有效性，不含 provider 专属逻辑。
        注意：免费预设音色在此处不会被清理（validate_voice_id 白名单放行），
        实际可用性由 core.py 运行时按 free + lanlan.app/lanlan.tech 线路决定。

        Returns:
            (cleaned_count, legacy_cosyvoice_names): 清理总数 及 仍在使用旧版 CosyVoice 音色的角色名列表
        """
        character_data = self.load_characters()
        cleaned_count = 0
        legacy_cosyvoice_names: list[str] = []

        catgirls = character_data.get('猫娘', {})
        for name, config in catgirls.items():
            voice_id = get_reserved(config, 'voice_id', default='', legacy_keys=('voice_id',))
            if not voice_id:
                continue
            # 旧版 CosyVoice 音色：保留 voice_id 不清空，仅记录供通知
            if self.is_legacy_cosyvoice_id(voice_id):
                legacy_cosyvoice_names.append(name)
                continue
            # 其他无效 voice_id（storage 中已不存在）：清空
            if not self.validate_voice_id(voice_id):
                logger.warning(
                    "猫娘 '%s' 的 voice_id '%s' 在当前 API 的 voice_storage 中不存在，已清除",
                    name,
                    voice_id,
                )
                set_reserved(config, 'voice_id', '')
                cleaned_count += 1

        if cleaned_count > 0:
            self.save_characters(character_data)
            logger.info("已清理 %d 个无效的 voice_id 引用", cleaned_count)

        return cleaned_count, legacy_cosyvoice_names

    # --- Character metadata helpers ---

    def get_character_data(self):
        """获取角色基础数据及相关路径"""
        character_data = self.load_characters()
        defaults = self.get_default_characters()

        character_data.setdefault('主人', deepcopy(defaults['主人']))
        character_data.setdefault('猫娘', deepcopy(defaults['猫娘']))

        master_basic_config = character_data.get('主人', {})
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
        """异步包装 load_characters：cache hit 也要 deepcopy 整个字典，
        N 个 catgirl 时拷贝可达数 ms，offload 避免阻塞事件循环。"""
        return await asyncio.to_thread(self.load_characters, character_json_path)

    async def aget_core_config(self):
        """异步包装 get_core_config：内部 open()+json.load() 读 core_config.json，
        async endpoint 调用时必须 offload，避免事件循环阻塞。"""
        return await asyncio.to_thread(self.get_core_config)

    # --- Core config helpers ---

    # Combined region cache (None = not checked, True = non-mainland, False = mainland)
    _region_cache = None
    # Individual caches for dual check (None = not yet tried, True/False = result,
    # _GEO_INDETERMINATE = tried but got no usable answer → do not retry)
    _ip_check_cache = None
    _steam_check_cache = None
    # Sentinel stored in _ip_check_cache when the HTTP probe fails, so we never
    # re-attempt it (and never pay the timeout again) within the same process.
    _GEO_INDETERMINATE = object()
    _geo_indeterminate_logged = False

    @staticmethod
    def _check_ip_non_mainland_http():
        """Independent IP geolocation via China-fast HTTP API (ip-api.com over HTTP)."""
        cache = ConfigManager._ip_check_cache
        if cache is not None:
            # True/False → deterministic result; sentinel → tried-and-failed, skip retry
            return None if cache is ConfigManager._GEO_INDETERMINATE else cache
        try:
            import urllib.request
            req = urllib.request.Request(
                "http://ip-api.com/json/?fields=countryCode",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            # 显式禁用代理，避免探测到代理服务器所在国家而非用户真实 IP 所在地。
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=3) as resp:
                data = json.loads(resp.read().decode())
            country = (data.get("countryCode") or "").upper()
            if country:
                result = country != "CN"
                ConfigManager._ip_check_cache = result
                print(f"[GeoIP] HTTP IP check: country={country}, non_mainland={result}", file=sys.stderr)
                return result
        except Exception as e:
            print(f"[GeoIP] HTTP IP check failed: {e}", file=sys.stderr)
        # Mark as attempted-but-indeterminate so the network probe is never retried.
        ConfigManager._ip_check_cache = ConfigManager._GEO_INDETERMINATE
        return None

    @staticmethod
    def _check_steam_non_mainland():
        """Steam-based IP country check via Steamworks SDK."""
        if ConfigManager._steam_check_cache is not None:
            return ConfigManager._steam_check_cache
        try:
            from main_routers.shared_state import get_steamworks
            steamworks = get_steamworks()
            if steamworks is None:
                return None
            ip_country = steamworks.Utils.GetIPCountry()
            if isinstance(ip_country, bytes):
                ip_country = ip_country.decode('utf-8')
            if ip_country:
                result = ip_country.upper() != "CN"
                ConfigManager._steam_check_cache = result
                print(f"[GeoIP] Steam IP check: country={ip_country}, non_mainland={result}", file=sys.stderr)
                return result
        except ImportError:
            pass
        except Exception as e:
            print(f"[GeoIP] Steam IP check failed: {e}", file=sys.stderr)
        return None

    def _check_non_mainland(self) -> bool:
        """Dual validation: both HTTP IP geo AND Steam geo must indicate non-mainland."""
        if ConfigManager._region_cache is not None:
            return ConfigManager._region_cache

        ip_result = self._check_ip_non_mainland_http()
        steam_result = self._check_steam_non_mainland()

        if ip_result is True and steam_result is True:
            ConfigManager._region_cache = True
            ConfigManager._geo_indeterminate_logged = False
            print(f"[GeoIP] Dual check PASS: non-mainland (IP={ip_result}, Steam={steam_result})", file=sys.stderr)
            return True

        if ip_result is False or steam_result is False:
            ConfigManager._region_cache = False
            ConfigManager._geo_indeterminate_logged = False
            print(f"[GeoIP] Dual check FAIL: mainland (IP={ip_result}, Steam={steam_result})", file=sys.stderr)
            return False

        # Both sources simultaneously indeterminate (e.g. ip-api.com blocked AND Steam not
        # yet initialised).  Do NOT write to _region_cache: Steam may initialise shortly
        # after this call, and caching False here would permanently suppress re-evaluation.
        # Callers that iterate get_core_config() will simply retry the geo check on the
        # next invocation until at least one source becomes definitive.
        if not ConfigManager._geo_indeterminate_logged:
            ConfigManager._geo_indeterminate_logged = True
            print(f"[GeoIP] Dual check indeterminate (IP={ip_result}, Steam={steam_result}), transient mainland default", file=sys.stderr)
        return False

    # Livestream 派生只接管 free 路这三个已知端点，避免劫持其他 lanlan.tech 路径
    # （例如未来新增 /docs /metrics 之类的非数据端点）
    _LIVESTREAM_DERIVE_PATHS = frozenset({'/core', '/text/v1', '/tts'})

    def _adjust_free_api_url(self, url: str, is_free: bool) -> str:
        """Internal URL adjustment for free API users.

        优先级：livestream prefix 派生 > 海外 lanlan.tech→lanlan.app 切换 > 原样返回。
        livestream 启用时仅接管 lanlan.tech 域下白名单内的 free 路端点
        （/core /text/v1 /tts），其他 path 走原地区切换。
        """
        if not url or 'lanlan.tech' not in url:
            return url

        try:
            if is_livestream_active():
                orig_path = urlparse(url).path or ''
                if orig_path in self._LIVESTREAM_DERIVE_PATHS:
                    derived = self._derive_livestream_url(
                        url, get_livestream_config()['server_prefix']
                    )
                    if derived:
                        return derived
        except Exception as e:
            logger.warning(f"Livestream URL 派生失败，回退到原始路径: {e}")

        try:
            if self._check_non_mainland():
                return url.replace('lanlan.tech', 'lanlan.app')
        except Exception:
            pass

        return url

    @staticmethod
    def _derive_livestream_url(original_url: str, prefix: str) -> str:
        """从 livestream server_prefix 派生 lanlan.tech URL 的等价地址。

        - 保留原 URL 的 path（``/core`` / ``/tts`` / ``/text/v1``）拼到 prefix path 之后
        - scheme 家族不变（原 ws/wss → 输出 ws/wss；原 http/https → 输出 http/https）
        - 加密与否（``s`` 后缀）按 prefix 的 scheme 走（prefix 是 https/wss → 输出加密）

        例：
        - ``wss://www.lanlan.tech/core`` + ``http://host:port/tok`` → ``ws://host:port/tok/core``
        - ``https://www.lanlan.tech/text/v1`` + ``http://host:port/tok`` → ``http://host:port/tok/text/v1``
        - ``wss://www.lanlan.tech/tts`` + ``https://host/tok`` → ``wss://host/tok/tts``
        """
        if not original_url or not prefix:
            return ''
        try:
            orig = urlparse(original_url)
            pref = urlparse(prefix)
        except Exception:
            return ''
        if not pref.scheme or not pref.netloc:
            return ''

        is_ws_family = orig.scheme in ('ws', 'wss')
        is_secure = pref.scheme in ('https', 'wss')
        if is_ws_family:
            out_scheme = 'wss' if is_secure else 'ws'
        else:
            out_scheme = 'https' if is_secure else 'http'

        base_path = pref.path.rstrip('/')
        return f"{out_scheme}://{pref.netloc}{base_path}{orig.path}"

    def get_core_config(self):
        """动态读取核心配置"""
        # 从 config 模块导入所有默认配置值
        from config import (
            DEFAULT_CORE_API_KEY,
            DEFAULT_AUDIO_API_KEY,
            DEFAULT_OPENROUTER_API_KEY,
            DEFAULT_MCP_ROUTER_API_KEY,
            DEFAULT_CORE_URL,
            DEFAULT_CORE_MODEL,
            DEFAULT_OPENROUTER_URL,
            DEFAULT_CONVERSATION_MODEL,
            DEFAULT_SUMMARY_MODEL,
            DEFAULT_CORRECTION_MODEL,
            DEFAULT_EMOTION_MODEL,
            DEFAULT_VISION_MODEL,
            DEFAULT_REALTIME_MODEL,
            DEFAULT_TTS_MODEL,
            DEFAULT_AGENT_MODEL,
            DEFAULT_CONVERSATION_MODEL_URL,
            DEFAULT_CONVERSATION_MODEL_API_KEY,
            DEFAULT_SUMMARY_MODEL_URL,
            DEFAULT_SUMMARY_MODEL_API_KEY,
            DEFAULT_CORRECTION_MODEL_URL,
            DEFAULT_CORRECTION_MODEL_API_KEY,
            DEFAULT_EMOTION_MODEL_URL,
            DEFAULT_EMOTION_MODEL_API_KEY,
            DEFAULT_VISION_MODEL_URL,
            DEFAULT_VISION_MODEL_API_KEY,
            DEFAULT_AGENT_MODEL_URL,
            DEFAULT_AGENT_MODEL_API_KEY,
            DEFAULT_REALTIME_MODEL_URL,
            DEFAULT_REALTIME_MODEL_API_KEY,
            DEFAULT_TTS_MODEL_URL,
            DEFAULT_TTS_MODEL_API_KEY,
        )

        config = {
            'CORE_API_KEY': DEFAULT_CORE_API_KEY,
            'AUDIO_API_KEY': DEFAULT_AUDIO_API_KEY,
            'OPENROUTER_API_KEY': DEFAULT_OPENROUTER_API_KEY,
            'MCP_ROUTER_API_KEY': DEFAULT_MCP_ROUTER_API_KEY,
            'CORE_URL': DEFAULT_CORE_URL,
            'CORE_MODEL': DEFAULT_CORE_MODEL,
            'CORE_API_TYPE': 'qwen',
            'OPENROUTER_URL': DEFAULT_OPENROUTER_URL,
            'CONVERSATION_MODEL': DEFAULT_CONVERSATION_MODEL,
            'SUMMARY_MODEL': DEFAULT_SUMMARY_MODEL,
            'CORRECTION_MODEL': DEFAULT_CORRECTION_MODEL,
            'EMOTION_MODEL': DEFAULT_EMOTION_MODEL,
            'ASSIST_API_KEY_QWEN': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_OPENAI': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_GLM': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_STEP': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_SILICON': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_GEMINI': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_KIMI': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_DEEPSEEK': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_DOUBAO': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_QWEN_INTL': '',
            'ASSIST_API_KEY_MINIMAX': '',
            'ASSIST_API_KEY_MINIMAX_INTL': '',
            'ASSIST_API_KEY_GROK': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_OPENROUTER': DEFAULT_CORE_API_KEY,
            'IS_FREE_VERSION': False,
            'VISION_MODEL': DEFAULT_VISION_MODEL,
            'AGENT_MODEL': DEFAULT_AGENT_MODEL,
            'REALTIME_MODEL': DEFAULT_REALTIME_MODEL,
            'TTS_MODEL': DEFAULT_TTS_MODEL,
            'CONVERSATION_MODEL_URL': DEFAULT_CONVERSATION_MODEL_URL,
            'CONVERSATION_MODEL_API_KEY': DEFAULT_CONVERSATION_MODEL_API_KEY,
            'SUMMARY_MODEL_URL': DEFAULT_SUMMARY_MODEL_URL,
            'SUMMARY_MODEL_API_KEY': DEFAULT_SUMMARY_MODEL_API_KEY,
            'CORRECTION_MODEL_URL': DEFAULT_CORRECTION_MODEL_URL,
            'CORRECTION_MODEL_API_KEY': DEFAULT_CORRECTION_MODEL_API_KEY,
            'EMOTION_MODEL_URL': DEFAULT_EMOTION_MODEL_URL,
            'EMOTION_MODEL_API_KEY': DEFAULT_EMOTION_MODEL_API_KEY,
            'VISION_MODEL_URL': DEFAULT_VISION_MODEL_URL,
            'VISION_MODEL_API_KEY': DEFAULT_VISION_MODEL_API_KEY,
            'AGENT_MODEL_URL': DEFAULT_AGENT_MODEL_URL,
            'AGENT_MODEL_API_KEY': DEFAULT_AGENT_MODEL_API_KEY,
            'REALTIME_MODEL_URL': DEFAULT_REALTIME_MODEL_URL,
            'REALTIME_MODEL_API_KEY': DEFAULT_REALTIME_MODEL_API_KEY,
            'TTS_MODEL_URL': DEFAULT_TTS_MODEL_URL,
            'TTS_MODEL_API_KEY': DEFAULT_TTS_MODEL_API_KEY,
            'OPENCLAW_URL': "http://127.0.0.1:8088",
            'OPENCLAW_TIMEOUT': 300.0,
            'OPENCLAW_DEFAULT_SENDER_ID': "neko_user",
        }

        core_cfg = deepcopy(DEFAULT_CONFIG_DATA['core_config.json'])

        try:
            with open(str(self.get_config_path('core_config.json')), 'r', encoding='utf-8') as f:
                file_data = json.load(f)
            if isinstance(file_data, dict):
                core_cfg.update(file_data)
            else:
                logger.warning("core_config.json 格式异常，使用默认配置。")

        except FileNotFoundError:
            logger.info("未找到 core_config.json，使用默认配置。")
        except Exception as e:
            logger.error("Error parsing Core API Key: %s", e)
        finally:
            if not isinstance(core_cfg, dict):
                core_cfg = deepcopy(DEFAULT_CONFIG_DATA['core_config.json'])

        # API Keys — 仅对与 coreApi/assistApi 匹配的服务商回退到 CORE_API_KEY
        if core_cfg.get('coreApiKey'):
            config['CORE_API_KEY'] = core_cfg['coreApiKey']

        _core_api_provider = core_cfg.get('coreApi') or 'qwen'
        _assist_api_provider = core_cfg.get('assistApi') or 'qwen'
        _fallback_providers = {_core_api_provider, _assist_api_provider}

        def _fb(provider: str) -> str:
            return config['CORE_API_KEY'] if provider in _fallback_providers else ''

        config['ASSIST_API_KEY_QWEN'] = core_cfg.get('assistApiKeyQwen', '') or _fb('qwen')
        config['ASSIST_API_KEY_QWEN_INTL'] = core_cfg.get('assistApiKeyQwenIntl', '') or _fb('qwen_intl')
        config['ASSIST_API_KEY_OPENAI'] = core_cfg.get('assistApiKeyOpenai', '') or _fb('openai')
        config['ASSIST_API_KEY_GLM'] = core_cfg.get('assistApiKeyGlm', '') or _fb('glm')
        config['ASSIST_API_KEY_STEP'] = core_cfg.get('assistApiKeyStep', '') or _fb('step')
        config['ASSIST_API_KEY_SILICON'] = core_cfg.get('assistApiKeySilicon', '') or _fb('silicon')
        config['ASSIST_API_KEY_GEMINI'] = core_cfg.get('assistApiKeyGemini', '') or _fb('gemini')
        config['ASSIST_API_KEY_KIMI'] = core_cfg.get('assistApiKeyKimi', '') or _fb('kimi')
        config['ASSIST_API_KEY_DEEPSEEK'] = core_cfg.get('assistApiKeyDeepseek', '') or _fb('deepseek')
        config['ASSIST_API_KEY_DOUBAO'] = core_cfg.get('assistApiKeyDoubao', '') or _fb('doubao')
        # MiniMax 是 assist-only（TTS 专用），不在 coreApi 候选集里，
        # coreApiKey 永远不是 minimax 兼容的；不 fallback，以免把无效 key
        # 塞进 TTS 凭证槽位导致 401，掩盖"未配置 minimax key"的真实提示。
        config['ASSIST_API_KEY_MINIMAX'] = core_cfg.get('assistApiKeyMinimax', '')
        config['ASSIST_API_KEY_MINIMAX_INTL'] = core_cfg.get('assistApiKeyMinimaxIntl', '')
        config['ASSIST_API_KEY_GROK'] = core_cfg.get('assistApiKeyGrok', '') or _fb('grok')
        config['ASSIST_API_KEY_CLAUDE'] = core_cfg.get('assistApiKeyClaude', '') or _fb('claude')
        config['ASSIST_API_KEY_OPENROUTER'] = core_cfg.get('assistApiKeyOpenrouter', '') or _fb('openrouter')

        if core_cfg.get('mcpToken'):
            config['MCP_ROUTER_API_KEY'] = core_cfg['mcpToken']

        openclaw_url = core_cfg.get('openclawUrl')
        if isinstance(openclaw_url, str) and openclaw_url.strip():
            normalized_openclaw_url = openclaw_url.strip().rstrip('/')
            try:
                parsed_openclaw_url = urlparse(normalized_openclaw_url)
            except Exception:
                parsed_openclaw_url = None
            if parsed_openclaw_url and parsed_openclaw_url.netloc:
                try:
                    if parsed_openclaw_url.port == 8089:
                        host = parsed_openclaw_url.hostname or ""
                        if ":" in host and not host.startswith("["):
                            host = f"[{host}]"
                        userinfo = ""
                        if parsed_openclaw_url.username:
                            userinfo = parsed_openclaw_url.username
                            if parsed_openclaw_url.password:
                                userinfo += f":{parsed_openclaw_url.password}"
                            userinfo += "@"
                        migrated_openclaw_url = urlunparse(
                            parsed_openclaw_url._replace(netloc=f"{userinfo}{host}:8088")
                        )
                        core_cfg['openclawUrl'] = migrated_openclaw_url
                        openclaw_url = migrated_openclaw_url
                        try:
                            self.save_json_config('core_config.json', core_cfg)
                            logger.info("已自动将 openclawUrl 从 8089 迁移到 8088: %s", migrated_openclaw_url)
                        except Exception as exc:
                            logger.warning("自动迁移 openclawUrl 到 8088 失败: %s", exc)
                except ValueError:
                    pass
        if isinstance(openclaw_url, str) and openclaw_url.strip():
            config['OPENCLAW_URL'] = openclaw_url.strip()
        try:
            openclaw_timeout = core_cfg.get('openclawTimeout', config['OPENCLAW_TIMEOUT'])
            openclaw_timeout = float(openclaw_timeout)
            if not math.isfinite(openclaw_timeout) or openclaw_timeout <= 0:
                raise ValueError("openclawTimeout must be a positive finite number")
            config['OPENCLAW_TIMEOUT'] = openclaw_timeout
        except (TypeError, ValueError):
            config['OPENCLAW_TIMEOUT'] = 300.0
        openclaw_sender = core_cfg.get('openclawDefaultSenderId')
        if isinstance(openclaw_sender, str) and openclaw_sender.strip():
            config['OPENCLAW_DEFAULT_SENDER_ID'] = openclaw_sender.strip()

        core_api_profiles = get_core_api_profiles()
        assist_api_profiles = get_assist_api_profiles()
        assist_api_key_fields = get_assist_api_key_fields()

        # Core API profile
        core_api_value = core_cfg.get('coreApi') or config['CORE_API_TYPE']
        config['CORE_API_TYPE'] = core_api_value
        core_profile = core_api_profiles.get(core_api_value)
        if core_profile:
            config.update(core_profile)

        # Assist API profile
        assist_api_value = core_cfg.get('assistApi')
        if core_api_value == 'free':
            assist_api_value = 'free'
        if not assist_api_value:
            assist_api_value = 'qwen'

        config['assistApi'] = assist_api_value

        assist_profile = assist_api_profiles.get(assist_api_value)
        if not assist_profile and assist_api_value != 'qwen':
            logger.warning("未知的 assistApi '%s'，回退到 qwen。", assist_api_value)
            assist_api_value = 'qwen'
            config['assistApi'] = assist_api_value
            assist_profile = assist_api_profiles.get(assist_api_value)

        if assist_profile:
            config.update(assist_profile)
        # agent api 默认跟随辅助 API 的 agent_model，缺失时回退到 VISION_MODEL
        config['AGENT_MODEL'] = config.get('AGENT_MODEL') or config.get('VISION_MODEL', '')
        config['AGENT_MODEL_URL'] = config.get('AGENT_MODEL_URL') or config.get('VISION_MODEL_URL', '') or config.get('OPENROUTER_URL', '')
        config['AGENT_MODEL_URL'] = config['AGENT_MODEL_URL'].replace('lanlan.tech', 'lanlan.app') # TODO: 先放这里

        key_field = assist_api_key_fields.get(assist_api_value)
        derived_key = ''
        if key_field:
            derived_key = config.get(key_field, '')
            if derived_key:
                config['AUDIO_API_KEY'] = derived_key
                config['OPENROUTER_API_KEY'] = derived_key

        if not config['AUDIO_API_KEY']:
            config['AUDIO_API_KEY'] = config['CORE_API_KEY']
        if not config['OPENROUTER_API_KEY']:
            config['OPENROUTER_API_KEY'] = config['CORE_API_KEY']

        # Agent API Key 回退：未显式配置时跟随辅助 API Key
        if not config.get('AGENT_MODEL_API_KEY'):
            config['AGENT_MODEL_API_KEY'] = derived_key if derived_key else config.get('CORE_API_KEY', '')

        # 自定义API配置映射（使用大写下划线形式的内部键，且在未提供时保留已有默认值）
        enable_custom_api = core_cfg.get('enableCustomApi', False)
        config['ENABLE_CUSTOM_API'] = enable_custom_api

        # GPT-SoVITS 配置映射
        config['GPTSOVITS_ENABLED'] = core_cfg.get('gptsovitsEnabled', False)

        # 禁用TTS
        _raw_disable_tts = core_cfg.get('disableTts', False)
        if isinstance(_raw_disable_tts, bool):
            config['DISABLE_TTS'] = _raw_disable_tts
        elif isinstance(_raw_disable_tts, str):
            config['DISABLE_TTS'] = _raw_disable_tts.lower() in ('true', '1', 'yes', 'on')
        else:
            config['DISABLE_TTS'] = False

        # 文本模式回复长度守卫上限（tiktoken o200k_base tokens，超限触发 reroll；
        # reroll 耗尽后回退到最后一个句末标点截断后落定）
        try:
            config['TEXT_GUARD_MAX_LENGTH'] = int(core_cfg.get('textGuardMaxLength', 300))
            if config['TEXT_GUARD_MAX_LENGTH'] <= 0:
                config['TEXT_GUARD_MAX_LENGTH'] = 300
        except (TypeError, ValueError):
            config['TEXT_GUARD_MAX_LENGTH'] = 300
        
        # 只有在启用自定义API时才允许覆盖各模型相关字段
        if enable_custom_api:
            # URL / Model ID 字段：空值回退到已有配置。
            # API Key 字段：根据用户选择的 provider 决定是否覆盖：
            #   - follow_core / follow_assist / ''（老配置无此字段）→ 保留上方派生的值
            #   - 具体服务商或 'custom' → 允许覆盖（空串合法，本地服务商可能不需要 key）
            _custom_api_fields = [
                # (前端字段前缀, 模型config键, URL config键, API Key config键)
                ('conversation', 'CONVERSATION_MODEL', 'CONVERSATION_MODEL_URL', 'CONVERSATION_MODEL_API_KEY'),
                ('summary',      'SUMMARY_MODEL',      'SUMMARY_MODEL_URL',      'SUMMARY_MODEL_API_KEY'),
                ('correction',   'CORRECTION_MODEL',    'CORRECTION_MODEL_URL',   'CORRECTION_MODEL_API_KEY'),
                ('emotion',      'EMOTION_MODEL',       'EMOTION_MODEL_URL',      'EMOTION_MODEL_API_KEY'),
                ('vision',       'VISION_MODEL',        'VISION_MODEL_URL',       'VISION_MODEL_API_KEY'),
                ('agent',        'AGENT_MODEL',         'AGENT_MODEL_URL',        'AGENT_MODEL_API_KEY'),
                ('omni',         'REALTIME_MODEL',      'REALTIME_MODEL_URL',     'REALTIME_MODEL_API_KEY'),
                ('tts',          'TTS_MODEL',           'TTS_MODEL_URL',          'TTS_MODEL_API_KEY'),
            ]
            for prefix, model_key, url_key, apikey_key in _custom_api_fields:
                provider = core_cfg.get(f'{prefix}ModelProvider', '')
                # follow_core / follow_assist 的 URL 是前端联动 readonly 自填的提示值
                # （static/js/api_key_settings.js: onCustomModelProviderChange），不代表
                # 用户选择"自定义部署"。但只在 omni/tts 才会出问题：
                #   - omni: get_model_api_config 看见 REALTIME_MODEL+_URL 都非空 →
                #     强行 api_type='local'（TODO 未实现）→ core_api_type='local' →
                #     TTS 调度落 dummy_tts_worker → 静音
                #   - tts:  TTS_MODEL_URL 被联动值污染让 tts_custom 走错 provider
                # 其他 model type（conversation/summary/correction/emotion/vision/agent）
                # 走 chat completion REST，没有 'local' 分支；跳 URL 反而会改变它们的
                # follow_* 路由（详见 PR #1084 review thread），故仅对 omni/tts 跳。
                # 注：follow_* 下用户填的 modelId 当前在 get_model_api_config fallback
                # 路径里读不到（fallback 用 CORE_MODEL，不是 REALTIME_MODEL/TTS_MODEL），
                # 那是另一个层面的问题，下个 PR 跟进。
                is_follow = provider in ('follow_core', 'follow_assist')
                skip_url_for_follow = is_follow and prefix in ('omni', 'tts')

                # URL: 空值回退到已有配置；omni/tts follow_* 时跳过
                if not skip_url_for_follow:
                    cfg_url = core_cfg.get(f'{prefix}ModelUrl')
                    if cfg_url is not None:
                        config[url_key] = cfg_url or config.get(url_key, '')

                # Model ID: 空值回退到已有配置
                cfg_model = core_cfg.get(f'{prefix}ModelId')
                if cfg_model is not None:
                    config[model_key] = cfg_model or config.get(model_key, '')

                # API Key 处理：
                #   follow_core   → 从核心 API Key 派生
                #   follow_assist → 从辅助 API Key 派生（OPENROUTER_API_KEY 已含 assist→core 回退）
                #   具体服务商/custom/''(老配置) → 使用存储值（空串合法，本地服务商不需要 key）
                if provider == 'follow_core':
                    config[apikey_key] = config.get('CORE_API_KEY', '')
                elif provider == 'follow_assist':
                    config[apikey_key] = config.get('OPENROUTER_API_KEY', '')
                else:
                    cfg_key = core_cfg.get(f'{prefix}ModelApiKey')
                    if cfg_key is not None:
                        config[apikey_key] = cfg_key

            # TTS Voice ID 作为角色 voice_id 的回退
            if core_cfg.get('ttsVoiceId') is not None:
                config['TTS_VOICE_ID'] = core_cfg.get('ttsVoiceId', '')

        for key, value in config.items():
            if key.endswith('_URL') and isinstance(value, str):
                config[key] = self._adjust_free_api_url(value, True)

        # Agent model always uses international API regardless of region
        if isinstance(config.get('AGENT_MODEL_URL'), str):
            config['AGENT_MODEL_URL'] = config['AGENT_MODEL_URL'].replace('lanlan.tech', 'lanlan.app')

        return config

    def get_model_api_config(self, model_type: str) -> dict:
        """
        获取指定模型类型的 API 配置（自动处理自定义 API 优先级）
        
        Args:
            model_type: 模型类型，可选值：
                - 'summary': 摘要模型（回退到辅助API）
                - 'correction': 纠错模型（回退到辅助API）
                - 'emotion': 情感分析模型（回退到辅助API）
                - 'vision': 视觉模型（回退到辅助API）
                - 'realtime': 实时语音模型（回退到核心API）
                - 'tts_default': 默认TTS（回退到核心API，用于OmniOfflineClient）
                - 'tts_custom': 自定义TTS（回退到辅助API，用于voice_id场景）
                
        Returns:
            dict: 包含以下字段的配置：
                - 'model': 模型名称
                - 'api_key': API密钥
                - 'base_url': API端点URL
                - 'is_custom': 是否使用自定义API配置
        """
        core_config = self.get_core_config()
        enable_custom_api = core_config.get('ENABLE_CUSTOM_API', False)
        
        # 模型类型到配置字段的映射
        # fallback_type: 'assist' = 辅助API, 'core' = 核心API
        model_type_mapping = {
            'conversation': {
                'custom_model': 'CONVERSATION_MODEL',
                'custom_url': 'CONVERSATION_MODEL_URL',
                'custom_key': 'CONVERSATION_MODEL_API_KEY',
                'default_model': 'CONVERSATION_MODEL',
                'fallback_type': 'assist',
            },
            'summary': {
                'custom_model': 'SUMMARY_MODEL',
                'custom_url': 'SUMMARY_MODEL_URL',
                'custom_key': 'SUMMARY_MODEL_API_KEY',
                'default_model': 'SUMMARY_MODEL',
                'fallback_type': 'assist',
            },
            'correction': {
                'custom_model': 'CORRECTION_MODEL',
                'custom_url': 'CORRECTION_MODEL_URL',
                'custom_key': 'CORRECTION_MODEL_API_KEY',
                'default_model': 'CORRECTION_MODEL',
                'fallback_type': 'assist',
            },
            'emotion': {
                'custom_model': 'EMOTION_MODEL',
                'custom_url': 'EMOTION_MODEL_URL',
                'custom_key': 'EMOTION_MODEL_API_KEY',
                'default_model': 'EMOTION_MODEL',
                'fallback_type': 'assist',
            },
            'vision': {
                'custom_model': 'VISION_MODEL',
                'custom_url': 'VISION_MODEL_URL',
                'custom_key': 'VISION_MODEL_API_KEY',
                'default_model': 'VISION_MODEL',
                'fallback_type': 'assist',
            },
            'agent': {
                'custom_model': 'AGENT_MODEL',
                'custom_url': 'AGENT_MODEL_URL',
                'custom_key': 'AGENT_MODEL_API_KEY',
                'default_model': 'AGENT_MODEL',
                'fallback_type': 'assist',
            },
            'realtime': {
                'custom_model': 'REALTIME_MODEL',
                'custom_url': 'REALTIME_MODEL_URL',
                'custom_key': 'REALTIME_MODEL_API_KEY',
                'default_model': 'CORE_MODEL',
                'fallback_type': 'core',  # 实时模型回退到核心API
            },
            'tts_default': {
                'custom_model': 'TTS_MODEL',
                'custom_url': 'TTS_MODEL_URL',
                'custom_key': 'TTS_MODEL_API_KEY',
                'default_model': 'CORE_MODEL',
                'fallback_type': 'core',  # 默认TTS回退到核心API
            },
            'tts_custom': {
                'custom_model': 'TTS_MODEL',
                'custom_url': 'TTS_MODEL_URL',
                'custom_key': 'TTS_MODEL_API_KEY',
                'default_model': 'CORE_MODEL',
                'fallback_type': 'assist',  # 自定义TTS回退到辅助API
            },
        }
        
        if model_type not in model_type_mapping:
            raise ValueError(f"Unknown model_type: {model_type}. Valid types: {list(model_type_mapping.keys())}")
        
        mapping = model_type_mapping[model_type]
        
        # agent 始终走专用字段（AGENT_MODEL_URL 有 lanlan.app 归一化），
        # 但 is_custom 仅在 enableCustomApi 开启时为 True。
        if enable_custom_api or model_type == 'agent':
            custom_model = core_config.get(mapping['custom_model'], '')
            custom_url = core_config.get(mapping['custom_url'], '')
            custom_key = core_config.get(mapping['custom_key'], '')

            # 自定义配置完整时使用自定义配置
            if custom_model and custom_url:
                return {
                    'model': custom_model,
                    'api_key': custom_key,
                    'base_url': custom_url,
                    'is_custom': enable_custom_api,
                    # 对于 realtime 模型，自定义配置时 api_type 设为 'local'
                    # TODO: 后续完善 'local' 类型的具体实现（如本地推理服务等）
                    'api_type': 'local' if model_type == 'realtime' else None,
                }
        
        # 自定义音色(CosyVoice)的特殊回退逻辑：优先尝试用户保存的 Qwen Cosyvoice API，
        # 只有在缺少 Qwen Cosyvoice API 时才再回退到辅助 API（CosyVoice 目前是唯一支持 voice clone 的）
        if model_type == 'tts_custom':
            qwen_api_key = (core_config.get('ASSIST_API_KEY_QWEN') or '').strip()
            if qwen_api_key:
                qwen_profile = get_assist_api_profiles().get('qwen', {})
                return {
                    'model': core_config.get(mapping['default_model'], ''), # Placeholder only, will be overridden by the actual model
                    'api_key': qwen_api_key,
                    'base_url': qwen_profile.get('OPENROUTER_URL', core_config.get('OPENROUTER_URL', '')), # Placeholder only, will be overridden by the actual url
                    'is_custom': False,
                }

        # 根据 fallback_type 回退到不同的 API
        if mapping['fallback_type'] == 'core':
            # 回退到核心 API 配置
            return {
                'model': core_config.get(mapping['default_model'], ''),
                'api_key': core_config.get('CORE_API_KEY', ''),
                'base_url': core_config.get('CORE_URL', ''),
                'is_custom': False,
                # 对于 realtime 模型，回退到核心API时使用配置的 CORE_API_TYPE
                'api_type': core_config.get('CORE_API_TYPE', '') if model_type == 'realtime' else None,
            }
        else:
            # 回退到辅助 API 配置
            return {
                'model': core_config.get(mapping['default_model'], ''),
                'api_key': core_config.get('OPENROUTER_API_KEY', ''),
                'base_url': core_config.get('OPENROUTER_URL', ''),
                'is_custom': False,
            }

    def is_agent_api_ready(self) -> tuple[bool, list[str]]:
        """
        Agent 模式门槛检查：
        - 必须具备可用的 AGENT_MODEL(model/url/api_key)
        - free 版本允许使用但由前端提示风险
        """
        reasons = []
        core_config = self.get_core_config()
        is_free = bool(core_config.get('IS_FREE_VERSION'))
        agent_api = self.get_model_api_config('agent')
        if not (agent_api.get('model') or '').strip():
            reasons.append("Agent 模型未配置")
        if not (agent_api.get('base_url') or '').strip():
            reasons.append("Agent API URL 未配置")
        api_key = (agent_api.get('api_key') or '').strip()
        if not api_key:
            reasons.append("Agent API Key 未配置或不可用")
        elif api_key == 'free-access' and not is_free:
            reasons.append("Agent API Key 未配置或不可用")
        return len(reasons) == 0, reasons

    def is_free_version(self) -> bool:
        return bool(self.get_core_config().get('IS_FREE_VERSION'))

    def _get_agent_quota_path(self) -> Path:
        """本地 Agent 试用配额计数文件路径。"""
        return self.config_dir / "agent_quota.json"

    def consume_agent_daily_quota(self, source: str = "", units: int = 1) -> tuple[bool, dict]:
        """消费 Agent 模型每日配额（仅免费版生效）。配额并非只在本地实施，本地计算是为了减少无效请求、节约网络带宽。

        Returns:
            (ok, info)
            info:
              - limited: bool
              - date: YYYY-MM-DD
              - used: int
              - limit: int | None
              - remaining: int | None
              - source: str
        """
        if units <= 0:
            units = 1

        is_free = self.is_free_version()
        today = date.today().isoformat()
        limit = int(self._free_agent_daily_limit)

        if not is_free:
            return True, {
                "limited": False,
                "date": today,
                "used": 0,
                "limit": None,
                "remaining": None,
                "source": source or "",
            }

        self.ensure_config_directory()
        quota_path = self._get_agent_quota_path()

        with ConfigManager._agent_quota_lock:
            data = {"date": today, "used": 0}
            try:
                if quota_path.exists():
                    with open(quota_path, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict):
                        loaded_date = str(loaded.get("date") or today)
                        loaded_used = int(loaded.get("used", 0) or 0)
                        if loaded_date == today:
                            data = {"date": today, "used": max(0, loaded_used)}
            except Exception:
                data = {"date": today, "used": 0}

            used = int(data.get("used", 0))
            if used + units > limit:
                return False, {
                    "limited": True,
                    "date": today,
                    "used": used,
                    "limit": limit,
                    "remaining": max(0, limit - used),
                    "source": source or "",
                }

            used += units
            data = {"date": today, "used": used}
            try:
                atomic_write_json(quota_path, data, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.warning("保存 Agent 配额计数失败: %s", e)

            return True, {
                "limited": True,
                "date": today,
                "used": used,
                "limit": limit,
                "remaining": max(0, limit - used),
                "source": source or "",
            }

    async def aconsume_agent_daily_quota(self, source: str = "", units: int = 1) -> tuple[bool, dict]:
        """Async wrapper of ``consume_agent_daily_quota``.

        事件循环上禁止直接走同步版本（会 open+fsync 阻塞）。
        """
        return await asyncio.to_thread(self.consume_agent_daily_quota, source, units)

    def load_json_config(self, filename, default_value=None):
        """
        加载JSON配置文件
        
        Args:
            filename: 配置文件名
            default_value: 默认值（如果文件不存在）
            
        Returns:
            dict: 配置内容
        """
        config_path = self.get_config_path(filename)
        
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            if default_value is not None:
                return deepcopy(default_value)
            raise
        except Exception as e:
            print(f"Error loading {filename}: {e}", file=sys.stderr)
            if default_value is not None:
                return deepcopy(default_value)
            raise
    
    def save_json_config(self, filename, data, *, bypass_write_fence: bool = False):
        """
        保存JSON配置文件
        
        Args:
            filename: 配置文件名
            data: 要保存的数据
        """
        if not bypass_write_fence:
            from utils.cloudsave_runtime import assert_cloudsave_writable

            assert_cloudsave_writable(self, operation="save", target=filename)

        # 确保目录存在
        self.ensure_config_directory()
        
        config_path = self.config_dir / filename
        
        try:
            atomic_write_json(config_path, data, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error saving {filename}: {e}", file=sys.stderr)
            raise
    
    def get_memory_path(self, filename):
        """
        获取记忆文件路径
        
        优先级：
        1. 我的文档/{APP_NAME}/memory/
        2. 项目目录/memory/store/
        
        Args:
            filename: 记忆文件名
            
        Returns:
            Path: 记忆文件路径
        """
        # 首选：我的文档下的记忆
        docs_memory_path = self.memory_dir / filename
        if docs_memory_path.exists():
            return docs_memory_path
        
        # 备选：项目目录下的记忆
        project_memory_path = self.project_memory_dir / filename
        if project_memory_path.exists():
            return project_memory_path
        
        # 都不存在，返回我的文档路径（用于创建新文件）
        return docs_memory_path
    
    def get_config_info(self):
        """获取配置目录信息"""
        return {
            "documents_dir": str(self.docs_dir),
            "app_dir": str(self.app_docs_dir),
            "config_dir": str(self.config_dir),
            "memory_dir": str(self.memory_dir),
            "plugins_dir": str(self.plugins_dir),
            "live2d_dir": str(self.live2d_dir),
            "readable_live2d_dir": str(self.readable_live2d_dir) if self.readable_live2d_dir else "",
            "windows_cfa_fallback_active": self.is_windows_cfa_fallback_active,
            "workshop_dir": str(self.workshop_dir),
            "chara_dir": str(self.chara_dir),
            "cloudsave_dir": str(self.cloudsave_dir),
            "cloudsave_staging_dir": str(self.cloudsave_staging_dir),
            "cloudsave_backups_dir": str(self.cloudsave_backups_dir),
            "local_state_dir": str(self.local_state_dir),
            "character_tombstones_state_path": str(self.character_tombstones_state_path),
            "project_config_dir": str(self.project_config_dir),
            "project_memory_dir": str(self.project_memory_dir),
            "config_files": {
                filename: str(self.get_config_path(filename))
                for filename in CONFIG_FILES
            }
        }
    
    def get_workshop_config_path(self):
        """
        获取workshop配置文件路径
        
        Returns:
            str: workshop配置文件的绝对路径
        """
        return str(self.get_config_path('workshop_config.json'))

    def _normalize_workshop_folder_path(self, folder_path):
        """标准化 workshop 目录路径，失败时返回 None。"""
        if not isinstance(folder_path, str):
            return None

        path_str = folder_path.strip()
        if not path_str:
            return None

        try:
            # 与 workshop_utils 保持一致：相对路径按用户目录解析
            if not os.path.isabs(path_str):
                path_str = os.path.join(os.path.expanduser('~'), path_str)
            return os.path.normpath(path_str)
        except Exception:
            return None

    def _cleanup_invalid_workshop_config_file(self, config_path):
        """
        检查并清理无效的 workshop 配置文件。

        判定规则：如果配置中任一路径字段存在但不是有效目录，则删除整个配置文件。
        """
        if not config_path.exists():
            return False

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
        except Exception as e:
            logger.warning(f"workshop配置文件损坏，准备删除: {config_path}, error={e}")
            try:
                config_path.unlink()
                return True
            except Exception as delete_error:
                logger.error(f"删除损坏workshop配置文件失败: {config_path}, error={delete_error}")
                return False

        if not isinstance(config_data, dict):
            logger.warning(f"workshop配置格式非法（非对象），准备删除: {config_path}")
            try:
                config_path.unlink()
                return True
            except Exception as delete_error:
                logger.error(f"删除非法workshop配置文件失败: {config_path}, error={delete_error}")
                return False

        path_keys = ("user_mod_folder", "steam_workshop_path", "default_workshop_folder")
        for key in path_keys:
            if key not in config_data:
                continue

            normalized_path = self._normalize_workshop_folder_path(config_data.get(key))
            if not normalized_path or not os.path.isdir(normalized_path):
                logger.warning(
                    f"发现无效workshop路径，准备删除配置文件: {config_path}, "
                    f"field={key}, value={config_data.get(key)!r}"
                )
                try:
                    config_path.unlink()
                    return True
                except Exception as delete_error:
                    logger.error(f"删除无效workshop配置文件失败: {config_path}, error={delete_error}")
                    return False

        return False

    def _cleanup_invalid_workshop_configs(self):
        """同时检查文档目录和项目目录中的 workshop 配置并清理无效文件。"""
        candidates = (
            self.config_dir / "workshop_config.json",
            self.project_config_dir / "workshop_config.json",
        )
        for candidate in candidates:
            self._cleanup_invalid_workshop_config_file(candidate)

    def repair_workshop_configs(self):
        """显式修复 workshop 配置文件，仅在调用方明确允许写盘时执行。"""
        with self._workshop_config_lock:
            from utils.cloudsave_runtime import assert_cloudsave_writable

            assert_cloudsave_writable(self, operation="repair", target="workshop_config.json")
            self._cleanup_invalid_workshop_configs()

    def _rebase_workshop_config_after_storage_migration(self, config):
        if not isinstance(config, dict):
            return config

        try:
            root_state = self.load_root_state()
        except Exception:
            root_state = {}

        candidate_source_roots = []
        if isinstance(root_state, dict):
            for key in ("last_migration_backup", "last_migration_source"):
                raw_root = str(root_state.get(key) or "").strip()
                if raw_root:
                    candidate_source_roots.append(raw_root)

        if not candidate_source_roots:
            return config

        try:
            from utils.storage_path_rewrite import rebase_runtime_bound_workshop_config_paths
        except Exception:
            return config

        rebased_config = config
        for source_root in candidate_source_roots:
            next_config = rebase_runtime_bound_workshop_config_paths(
                rebased_config,
                source_root=source_root,
                target_root=self.app_docs_dir,
            )
            rebased_config = next_config

        if rebased_config is config:
            return config

        try:
            self.save_workshop_config(rebased_config)
        except Exception as exc:
            logger.warning("保存迁移后的 workshop 配置路径自愈结果失败: %s", exc)
        return rebased_config
    
    def load_workshop_config(self):
        """
        加载workshop配置
        
        Returns:
            dict: workshop配置数据
        """
        config_path = self.get_workshop_config_path()
        try:
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                config = self._rebase_workshop_config_after_storage_migration(config)
                logger.debug(f"成功加载workshop配置: {config}")
                return config
            else:
                # 配置不存在时直接返回默认值，避免只读查询链路隐式写入配置文件。
                with self._workshop_config_lock:
                    if os.path.exists(config_path):
                        with open(config_path, 'r', encoding='utf-8') as f:
                            config = json.load(f)
                        config = self._rebase_workshop_config_after_storage_migration(config)
                        logger.debug(f"成功加载workshop配置: {config}")
                        return config

                    default_config = {
                        "default_workshop_folder": str(self.workshop_dir),
                        "auto_create_folder": True
                    }
                    logger.debug(f"workshop配置不存在，返回默认配置: {default_config}")
                    return default_config
        except Exception as e:
            error_msg = f"加载workshop配置失败: {e}"
            logger.error(error_msg)
            print(error_msg)
            # 使用默认配置
            return {
                "default_workshop_folder": str(self.workshop_dir),
                "auto_create_folder": True
            }
    
    def save_workshop_config(self, config_data):
        """
        保存workshop配置
        
        Args:
            config_data: 要保存的配置数据
        """
        config_path = str(self.get_runtime_config_path('workshop_config.json'))
        try:
            from utils.cloudsave_runtime import assert_cloudsave_writable

            assert_cloudsave_writable(self, operation="save", target="workshop_config.json")

            # 确保配置目录存在
            self.ensure_config_directory()
            
            # 保存配置
            atomic_write_json(config_path, config_data, indent=4, ensure_ascii=False)
            
            logger.info(f"成功保存workshop配置: {config_data}")
        except Exception as e:
            error_msg = f"保存workshop配置失败: {e}"
            logger.error(error_msg)
            print(error_msg)
            raise
    
    def save_workshop_path(self, workshop_path):
        """
        设置Steam创意工坊根目录路径（运行时变量，不写入配置文件）
        
        Args:
            workshop_path: Steam创意工坊根目录路径
        """
        self._steam_workshop_path = workshop_path
        logger.info(f"已设置Steam创意工坊路径（运行时）: {workshop_path}")

    def persist_user_workshop_folder(self, workshop_path):
        """
        将Steam创意工坊实际路径持久化到配置文件（每次启动仅首次写入）。

        仅在动态获取Steam工坊位置成功时调用，后续读取可在Steam未运行时作为回退。
        """
        if self._user_workshop_folder_persisted:
            return
        if not workshop_path or not os.path.isdir(workshop_path):
            return
        try:
            config = self.load_workshop_config()
            config["user_workshop_folder"] = workshop_path
            self.save_workshop_config(config)
            self._user_workshop_folder_persisted = True
            logger.info(f"已持久化Steam创意工坊路径到配置文件: {workshop_path}")
        except Exception as e:
            logger.error(f"持久化user_workshop_folder失败: {e}")

    def get_steam_workshop_path(self):
        """
        获取Steam创意工坊根目录路径（仅运行时，由启动流程设置）
        
        Returns:
            str | None: Steam创意工坊根目录路径
        """
        return self._steam_workshop_path
    
    def get_workshop_path(self):
        """
        获取workshop根目录路径
        
        优先级: user_mod_folder(配置) > Steam运行时路径 > user_workshop_folder(缓存文件) > default_workshop_folder(配置) > self.workshop_dir
        
        Returns:
            str: workshop根目录路径
        """
        config = self.load_workshop_config()
        if config.get("user_mod_folder"):
            return config["user_mod_folder"]
        if self._steam_workshop_path:
            return self._steam_workshop_path
        cached = config.get("user_workshop_folder")
        if cached and os.path.isdir(cached):
            return cached
        return config.get("default_workshop_folder", str(self.workshop_dir))


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
    """获取配置管理器单例，默认使用配置中的 APP_NAME。"""
    global _config_manager, _config_manager_migrated
    if _config_manager is None:
        _config_manager = ConfigManager(app_name)
        _config_manager_migrated = False
    if migrate:
        _ensure_config_manager_migrated()
    return _config_manager


# 便捷函数
def get_config_path(filename):
    """获取配置文件路径"""
    return get_config_manager().get_config_path(filename)


def get_runtime_config_path(filename):
    """获取运行时真源配置路径。"""
    return get_config_manager().get_runtime_config_path(filename)


def get_plugins_directory(app_name=None):
    """获取用户插件根目录，默认位于应用文档目录下的 ``plugins``。"""
    manager = ConfigManager(app_name)
    manager.ensure_plugins_directory()
    return manager.plugins_dir


def load_json_config(filename, default_value=None):
    """加载JSON配置"""
    return get_config_manager().load_json_config(filename, default_value)


def save_json_config(filename, data):
    """保存JSON配置"""
    return get_config_manager().save_json_config(filename, data)

# Workshop配置便捷函数
def load_workshop_config():
    """加载workshop配置"""
    return get_config_manager().load_workshop_config()

def save_workshop_config(config_data):
    """保存workshop配置"""
    return get_config_manager().save_workshop_config(config_data)

def save_workshop_path(workshop_path):
    """设置Steam创意工坊根目录路径（运行时）"""
    return get_config_manager().save_workshop_path(workshop_path)

def persist_user_workshop_folder(workshop_path):
    """将Steam创意工坊实际路径持久化到配置文件（每次启动仅首次写入）"""
    return get_config_manager().persist_user_workshop_folder(workshop_path)

def get_steam_workshop_path():
    """获取Steam创意工坊根目录路径（运行时）"""
    return get_config_manager().get_steam_workshop_path()

def get_workshop_path():
    """获取workshop根目录路径"""
    return get_config_manager().get_workshop_path()


if __name__ == "__main__":
    # 测试代码
    manager = get_config_manager()
    print("配置管理器信息:")
    info = manager.get_config_info()
    for key, value in info.items():
        if isinstance(value, dict):
            print(f"{key}:")
            for k, v in value.items():
                print(f"  {k}: {v}")
        else:
            print(f"{key}: {value}")
