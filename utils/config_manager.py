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
Config file management module
Manages config file storage locations and migration
"""
import sys
import os
import json
import re
import shutil
import threading
import asyncio
import time
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
    GEOIP_FORCE_NON_MAINLAND,
    RESERVED_FIELD_SCHEMA,
)
from config.prompts.prompts_chara import get_lanlan_prompt, is_default_prompt
from utils.api_config_loader import (
    get_core_api_profiles,
    get_assist_api_profiles,
    get_assist_api_key_fields,
    get_livestream_config,
    is_livestream_active,
)
from utils.custom_tts_adapter import check_custom_tts_voice_allowed
from utils.doubao_tts import DOUBAO_VOICE_STORAGE_KEY
from utils.file_utils import atomic_write_json
from utils.gptsovits_config import normalize_gsv_api_url
from utils.voice_config import read_legacy_voice_id
from utils.logger_config import get_module_logger
from utils.native_voice_registry import (
    is_free_lanlan_app_route,
    is_saveable_native_voice,
)
from utils.persona_presets import PERSONA_OVERRIDE_FIELDS
from utils.steam_state import get_steamworks

# Workshop配置相关常量 - 将在ConfigManager实例化时使用self.workshop_dir


logger = get_module_logger(__name__)


class LocalStateDirectoryError(OSError):
    """Raised when the local non-cloud state directory cannot be prepared."""

    local_state_directory_error = True

    def __init__(
        self,
        message,
        *,
        anchor_root=None,
        local_state_dir=None,
        failed_path=None,
        reason="",
    ):
        self.anchor_root = str(Path(anchor_root).expanduser().resolve(strict=False)) if anchor_root else ""
        self.local_state_dir = (
            str(Path(local_state_dir).expanduser().resolve(strict=False)) if local_state_dir else ""
        )
        self.failed_path = str(Path(failed_path).expanduser().resolve(strict=False)) if failed_path else ""
        self.reason = str(reason or "")
        parts = [str(message or "Local state directory is unavailable")]
        if self.anchor_root:
            parts.append(f"anchor_root={self.anchor_root}")
        if self.local_state_dir:
            parts.append(f"local_state_dir={self.local_state_dir}")
        if self.failed_path:
            parts.append(f"failed_path={self.failed_path}")
        if self.reason:
            parts.append(f"reason={self.reason}")
        super().__init__("\n".join(parts))


def _as_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ('true', '1', 'yes', 'on'):
            return True
        if lowered in ('false', '0', 'no', 'off', ''):
            return False
    if value is None:
        return default
    return bool(value)


def get_reserved(data: dict, *path, default=None, legacy_keys: tuple[str, ...] | None = None):
    """Unified read of nested fields under `_reserved`, with fallback to legacy flat fields.

    If the nested path exists in _reserved (even when the value is None), return it directly;
    only fall back to the legacy flat field when the path is absent or _reserved itself is missing.
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
    """Unified write of nested fields under `_reserved`, auto-creating intermediate levels.

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


DEFAULT_YUI_LIVE2D_MODEL_PATH = "yui-origin/yui-origin.model3.json"


def _normalize_live2d_model_path(value) -> str:
    model_path = str(value or "").strip().replace("\\", "/").lower()
    if model_path == "yui-origin":
        return DEFAULT_YUI_LIVE2D_MODEL_PATH
    return model_path


def _is_default_yui_character(character_name: str, character_data: dict) -> bool:
    if not isinstance(character_data, dict):
        return False

    name = str(character_name or "").strip().upper()
    nickname = str(character_data.get("昵称") or "").strip().upper()
    if name != "YUI" and nickname != "YUI":
        return False

    model_path = get_reserved(
        character_data,
        "avatar",
        "live2d",
        "model_path",
        default="",
        legacy_keys=("live2d",),
    )
    return _normalize_live2d_model_path(model_path) == DEFAULT_YUI_LIVE2D_MODEL_PATH


# 历史上 free_voices["yui_cn"] 用过、现已被替换的免费 YUI 预设音色 ID。
# 这些值仍残留在存量用户的 characters.json 里，但已不在 free_voices 白名单中，
# 会被 cleanup_invalid_voice_ids 判为 invalid 清空 → 空 voice 落到 free/step
# provider 的 default_voice（qingchunshaonv），导致「一直吃默认 YUI、从没手动
# 选过音色」的免费用户在音色 ID 更替后无声掉档到通用女声。cleanup 在判 invalid
# 前先把这些值平移到现役 yui_cn 即可兜住。将来再更替 YUI 音色时，把被替换掉的
# 旧值追加进这个集合。
_DEPRECATED_FREE_YUI_VOICE_IDS = frozenset({"voice-tone-R6NtLH3Hk0"})


def _get_default_yui_free_voice_id() -> str:
    from utils.api_config_loader import get_free_voices
    from utils.language_utils import get_global_language_full

    free_voices = get_free_voices() or {}
    try:
        language = str(get_global_language_full() or "").strip().lower().replace("-", "_")
    except Exception:
        language = ""

    language_aliases = {
        "zh": "cn",
        "zh_cn": "cn",
        "zh_hans": "cn",
        "zh_tw": "tw",
        "zh_hant": "tw",
    }
    suffix = language_aliases.get(language, language.split("_", 1)[0] if language else "")
    keys = []
    if suffix:
        keys.append(f"yui_{suffix}")
    keys.extend(("yui_cn", "cuteGirl"))

    for key in keys:
        voice_id = str(free_voices.get(key) or "").strip()
        if voice_id:
            return voice_id
    return next((str(voice_id).strip() for voice_id in free_voices.values() if str(voice_id or "").strip()), "")


async def ensure_default_yui_voice_for_free_api(config_manager, core_cfg: dict | None = None) -> bool:
    """Ensure the default YUI card has the free YUI voice when free API is active."""
    if not isinstance(core_cfg, dict):
        try:
            core_cfg = await config_manager.aget_core_config()
        except Exception:
            core_cfg = {}
    if not isinstance(core_cfg, dict):
        return False
    # 免费预设 YUI 音色只在 core=free 运行时可用，与 assist 无关。
    if (core_cfg.get("coreApi") or core_cfg.get("CORE_API_TYPE")) != "free":
        return False

    characters = await config_manager.aload_characters()
    if not isinstance(characters, dict):
        return False

    current_name = str(characters.get("当前猫娘") or "").strip()
    catgirls = characters.get("猫娘")
    if not current_name or not isinstance(catgirls, dict):
        return False

    current_character = catgirls.get(current_name)
    if not _is_default_yui_character(current_name, current_character):
        return False

    current_voice_id = read_legacy_voice_id(get_reserved(
        current_character,
        "voice_id",
        default="",
        legacy_keys=("voice_id",),
    ))
    if current_voice_id:
        return False

    # 海外免费（free + *.lanlan.app）：默认音色是品牌 yui（free_intl 的 default_voice），
    # 下发字面量 "yui"。国内免费（lanlan.tech）仍按语言绑定 free_voices 里的 yui 音色。
    #
    # 注意：update_core_config 传进来的 raw core_cfg 里 CORE_URL 还是 lanlan.tech，
    # get_core_config() 才会按非大陆改写成 lanlan.app，直接判 URL 会漏判海外。
    # 故 URL 命中 lanlan.app 走快路，否则用 _check_non_mainland 兜底判海外。
    core_url = str((core_cfg or {}).get("CORE_URL") or "")
    overseas = is_free_lanlan_app_route("free", core_url)
    if not overseas:
        try:
            overseas = bool(config_manager._check_non_mainland())
        except Exception:
            overseas = False
    yui_voice_id = "yui" if overseas else _get_default_yui_free_voice_id()
    if not yui_voice_id:
        return False

    changed = set_reserved(current_character, "voice_id", yui_voice_id)
    if not changed:
        return False

    await config_manager.asave_characters(characters)
    logger.info("已为 free API 下的默认 YUI 绑定音色: %s", yui_voice_id)
    return True


def delete_reserved(data: dict, *path) -> bool:
    """Delete a nested field under `_reserved`, cleaning up empty intermediate levels where possible."""
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


def _build_effective_character_payload(character_payload: dict, entity: str = "neko") -> dict:
    if not isinstance(character_payload, dict):
        return {}

    effective_payload = deepcopy(character_payload)
    override = _get_persona_override(character_payload)
    if not isinstance(override, dict):
        for field, value in _build_ai_context_fields(
            character_payload,
            existing_fields=set(effective_payload.keys()),
            entity=entity,
        ).items():
            effective_payload[field] = value
        return effective_payload

    profile = _normalize_persona_override_profile(override.get("profile"))
    for field, value in profile.items():
        effective_payload[field] = value
    for field, value in _build_ai_context_fields(
        character_payload,
        existing_fields=set(effective_payload.keys()),
        entity=entity,
    ).items():
        effective_payload[field] = value
    return effective_payload


def _append_persona_guidance_to_prompt(prompt_text: str, character_payload: dict) -> str:
    override = _get_persona_override(character_payload)
    if not isinstance(override, dict):
        return prompt_text

    guidance = ""
    from_preset = False
    preset_id = str(override.get("preset_id") or "").strip()
    if preset_id:
        # 运行时按当前全局语言重新解析，使 persona prompt 与基础 LANLAN prompt
        # 一样跟随语言切换；仅当 preset_id 已被代码移除时才退回到落盘字符串。
        try:
            from utils.persona_presets import get_persona_prompt_guidance
            guidance = (get_persona_prompt_guidance(preset_id) or "").strip()
            from_preset = bool(guidance)
        except Exception:
            guidance = ""

    if not guidance:
        guidance = str(override.get("prompt_guidance") or "").strip()

    if not guidance:
        return prompt_text

    # preset 的 guidance 是一份**完整独立**的人设 prompt，骨架（fictional-character
    # 前言 + <Context Awareness> + <WARNING> + <IMPORTANT>）与默认 base 逐字相同。
    # 直接 append 会让整套骨架重复一遍（~1500 字），且这段经 lanlan_prompt_map 流向
    # 主对话 system prompt、proactive、break reminder、各插件等**所有**消费点。
    # 当 base 仍是默认 prompt 时，preset 本身就是完整人设 → 用它替换 base，避免重复；
    # 仅当用户写过自定义 system_prompt（非默认）时才退回 append 以保留其自定义内容。
    if from_preset and is_default_prompt(prompt_text):
        return guidance

    return f"{prompt_text}\n\nAdditional role guidance: {guidance}"


_AI_CONTEXT_RENAME_EVENT_FIELD = "__ai_context.profile_rename_events"


def _unique_ai_context_field_name(existing_fields: set[str] | None) -> str:
    existing = {str(field) for field in (existing_fields or set())}
    if _AI_CONTEXT_RENAME_EVENT_FIELD not in existing:
        return _AI_CONTEXT_RENAME_EVENT_FIELD

    index = 2
    while f"{_AI_CONTEXT_RENAME_EVENT_FIELD}.{index}" in existing:
        index += 1
    return f"{_AI_CONTEXT_RENAME_EVENT_FIELD}.{index}"


def _join_profile_rename_old_names(lang: str | None, names: list[str]) -> str:
    normalized_lang = str(lang or "").strip().lower()
    separator = "、" if normalized_lang.startswith(("zh", "ja")) else ", "
    return separator.join(names)


def _build_ai_context_fields(
    character_payload: dict,
    existing_fields: set[str] | None = None,
    entity: str = "neko",
) -> dict[str, str]:
    """Expand hidden runtime events into synthetic fields used only for prompt/memory sync.

    entity indicates whether this payload is the catgirl (neko) or the master, which decides
    the person used in rename records: the master's records go into the master section of the
    catgirl persona and must be second person, never first person.
    """
    if not isinstance(character_payload, dict):
        return {}

    rename_events = get_reserved(
        character_payload,
        "ai_context",
        "rename_events",
        default=[],
    )
    if not isinstance(rename_events, list):
        return {}

    try:
        from utils.language_utils import get_global_language_full
        lang = get_global_language_full()
    except Exception:
        lang = None

    from config.prompts.prompts_memory import render_profile_rename_event_context

    field_name = _unique_ai_context_field_name(existing_fields)
    old_names: list[str] = []
    current_name = ""
    legacy_lines: list[str] = []
    for event in rename_events:
        if not isinstance(event, dict):
            continue
        if str(event.get("type") or "").strip() != "profile_rename":
            continue
        old_name = str(event.get("old_name") or "").strip()
        new_name = str(event.get("new_name") or "").strip()
        if old_name and new_name:
            if old_name not in old_names:
                old_names.append(old_name)
            current_name = new_name
        else:
            text = str(event.get("text") or "").strip()
            if not text:
                continue
            legacy_lines.append(text)

    if current_name:
        old_names = [name for name in old_names if name != current_name]

    lines: list[str] = []
    if old_names and current_name:
        old_names_text = _join_profile_rename_old_names(lang, old_names)
        label, text = render_profile_rename_event_context(lang, old_names_text, current_name, entity=entity)
        lines.append(f"{label}: {text}")

    lines.extend(legacy_lines)

    if not lines:
        return {}
    return {field_name: "\n".join(lines)}


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
    """Convert a legacy live2d directory name to a model3 file path."""
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
    """Map a new model_path back to the legacy live2d model name (for legacy frontend fields)."""
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
    """Validate the `_reserved` structure; returns a list of errors (empty list means pass)."""
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
    # voice_id 是 str | 结构对象的联合类型，schema 的 tuple 分支只做 isinstance，挡不住
    # {"foo": 1} 这种坏 dict。结构对象形态额外校验 source/provider/ref 都为 str，避免
    # 放宽 schema 后契约松掉（CodeRabbit）。
    vid = reserved.get("voice_id")
    if isinstance(vid, dict):
        for field in ("source", "provider", "ref"):
            if not isinstance(vid.get(field), str):
                errors.append(
                    f"_reserved.voice_id.{field} 需要 str，实际 {type(vid.get(field)).__name__}"
                )
    return errors


def migrate_catgirl_reserved(catgirl_data: dict) -> bool:
    """Migrate a single character config to the `_reserved` structure; returns whether changes occurred."""
    if not isinstance(catgirl_data, dict):
        return False

    changed = False

    if not isinstance(catgirl_data.get("_reserved"), dict):
        catgirl_data["_reserved"] = {}
        changed = True

    voice_id = get_reserved(catgirl_data, "voice_id", default="", legacy_keys=("voice_id",))
    # 把 voice_id 收口进 _reserved。结构对象（惰性迁移形态，可能来自顶层 legacy 字段）
    # 必须原样 set_reserved 搬进来：既不能被 str(dict) 损坏，也不能跳过——否则随后的旧
    # 顶层字段清理会 pop 掉它，导致绑定在加载时悄悄丢失（Codex/CodeRabbit P2）。
    # 普通 legacy 值仍规整成字符串。
    if isinstance(voice_id, dict):
        changed |= set_reserved(catgirl_data, "voice_id", voice_id)
    elif voice_id is not None:
        changed |= set_reserved(catgirl_data, "voice_id", str(voice_id))

    system_prompt = get_reserved(catgirl_data, "system_prompt", default=None, legacy_keys=("system_prompt",))
    if system_prompt is not None:
        changed |= set_reserved(catgirl_data, "system_prompt", str(system_prompt))

    model_type = str(
        get_reserved(catgirl_data, "avatar", "model_type", default="", legacy_keys=("model_type",))
    ).strip().lower()
    if model_type not in {"live2d", "vrm", "live3d", "pngtuber"}:
        has_vrm = catgirl_data.get("vrm") or get_reserved(catgirl_data, "avatar", "vrm", "model_path")
        has_mmd = catgirl_data.get("mmd") or get_reserved(catgirl_data, "avatar", "mmd", "model_path")
        has_pngtuber = catgirl_data.get("pngtuber") or get_reserved(catgirl_data, "avatar", "pngtuber", default={})
        if has_pngtuber:
            model_type = "pngtuber"
        else:
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

    pngtuber_config = get_reserved(catgirl_data, "avatar", "pngtuber", default=None, legacy_keys=("pngtuber",))
    if pngtuber_config is None:
        pngtuber_config = {}
    if not isinstance(pngtuber_config, dict):
        pngtuber_config = {"idle_image": str(pngtuber_config)} if str(pngtuber_config or "").strip() else {}
    pngtuber_config = dict(pngtuber_config)
    legacy_pngtuber_fields = {
        "idle_image": "pngtuber_idle_image",
        "talking_image": "pngtuber_talking_image",
        "happy_image": "pngtuber_happy_image",
        "sad_image": "pngtuber_sad_image",
        "angry_image": "pngtuber_angry_image",
        "surprised_image": "pngtuber_surprised_image",
    }
    for reserved_key, legacy_key in legacy_pngtuber_fields.items():
        if not pngtuber_config.get(reserved_key) and catgirl_data.get(legacy_key):
            pngtuber_config[reserved_key] = str(catgirl_data.get(legacy_key) or "")
    if pngtuber_config:
        changed |= set_reserved(catgirl_data, "avatar", "pngtuber", pngtuber_config)

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
        "pngtuber",
        "pngtuber_idle_image",
        "pngtuber_talking_image",
        "pngtuber_happy_image",
        "pngtuber_sad_image",
        "pngtuber_angry_image",
        "pngtuber_surprised_image",
    ):
        if legacy_key in catgirl_data:
            catgirl_data.pop(legacy_key, None)
            changed = True

    return changed


def flatten_reserved(catgirl_data: dict) -> dict:
    """Expand `_reserved` into legacy flat fields (only for compatibility with legacy callers/frontends)."""
    if not isinstance(catgirl_data, dict):
        return catgirl_data
    result = dict(catgirl_data)

    # 展平给 legacy 前端/调用方：始终吐 legacy 字符串形态（容忍结构对象）。
    voice_id = read_legacy_voice_id(get_reserved(result, "voice_id", default=""))
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

    pngtuber_config = get_reserved(result, "avatar", "pngtuber", default=None)
    if isinstance(pngtuber_config, dict) and pngtuber_config:
        result["pngtuber"] = dict(pngtuber_config)
        for key in (
            "idle_image",
            "talking_image",
            "happy_image",
            "sad_image",
            "angry_image",
            "surprised_image",
        ):
            if pngtuber_config.get(key):
                result[f"pngtuber_{key}"] = pngtuber_config.get(key)

    touch_set = get_reserved(result, 'touch_set', default=None)
    if touch_set:
        result['touch_set'] = touch_set
    return result


class ConfigManager:
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

    @property
    def selected_root(self):
        return self.committed_selected_root

    @selected_root.setter
    def selected_root(self, value):
        self.committed_selected_root = value

    def __init__(self, app_name=None):
        """
        Initialize the config manager
        
        Args:
            app_name: application name, defaults to APP_NAME from config
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
        self.pngtuber_dir = self.app_docs_dir / "pngtuber"
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
        """Cloud-save export root directory (the normalized export layer outside the runtime directory)."""
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
        """Local staging area; excluded from the cloud sync whitelist."""
        return self.anchor_root / ".cloudsave_staging"

    @property
    def cloudsave_backups_dir(self) -> Path:
        """Local conflict backup pool, kept explicitly outside cloudsave/ to avoid accidental future sync."""
        return self.anchor_root / "cloudsave_backups"

    @property
    def local_state_dir(self) -> Path:
        """Local state directory, holding sync metadata that never goes to the cloud."""
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
        """Print debug info only in the main process"""
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
        """Return preferred app-data root directory candidates for the current platform."""
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
        """Return parent-directory candidates of historical runtime roots, used only for legacy data import."""
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
        """Return legacy user root directory candidates (with app_name), used for phase-0 startup import."""
        roots = []
        current_root = str(self.app_docs_dir)
        for base_dir in self._get_legacy_storage_candidates():
            app_root = base_dir / self.app_name
            if str(app_root) == current_root:
                continue
            roots.append(app_root)
        return self._dedupe_paths(roots)
    
    def _get_documents_directory(self):
        """Get the parent directory of the runtime data root.

        The method name is kept for historical compatibility, but after phase 0 it prefers
        the standard app-data directory; Documents / exe directory / cwd are only candidates
        for legacy data import and last-resort fallback.
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
        """Get the project root directory (private method).

        In source mode this is fixed to backtracking from this file's location to the repo
        root, so static, config, memory/store and other project resources never resolve to
        wrong locations due to IDE / external cwd.
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
        """Get the project root directory (public property)"""
        return self._get_project_root()
    
    def _get_project_config_directory(self):
        """Get the project's config directory"""
        return self._get_project_root() / "config"
    
    def _get_project_memory_directory(self):
        """Get the project's memory/store directory"""
        return self._get_project_root() / "memory" / "store"
    
    def _ensure_app_docs_directory(self):
        """Ensure the app documents directory exists (the N.E.K.O directory itself)"""
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
        """Ensure the anchor directory exists (it permanently hosts cloudsave/state)."""
        try:
            self.anchor_root.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create anchor directory {self.anchor_root}: {e}", file=sys.stderr)
            return False
    
    def ensure_config_directory(self):
        """Ensure the config directory under Documents exists"""
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
        """Ensure the memory directory under Documents exists"""
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
        """Ensure the plugins directory under Documents exists"""
        try:
            if not self._ensure_app_docs_directory():
                return False

            self.plugins_dir.mkdir(exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create plugins directory: {e}", file=sys.stderr)
            return False
    
    def ensure_live2d_directory(self):
        """Ensure the live2d directory under Documents exists"""
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
        """The live2d directory under the original Documents (read-only, for CFA scenarios).

        When Windows Controlled Folder Access (CFA / anti-ransomware protection) blocks
        writes to Documents, write operations fall back to AppData, but the user's model
        files are still in the original Documents. This property returns the live2d path
        in the original Documents for reading.

        Returns None in non-CFA scenarios (live2d_dir itself then points to Documents).
        """
        if self.is_windows_cfa_fallback_active and self._readable_docs_dir is not None:
            p = self._readable_docs_dir / self.app_name / "live2d"
            if p.exists():
                return p
        return None

    @property
    def is_windows_cfa_fallback_active(self) -> bool:
        """Whether Windows CFA read/write split mode is active."""
        if self._readable_docs_dir is None:
            return False
        write_docs_dir = getattr(self, "_cfa_fallback_write_docs_dir", None)
        if write_docs_dir is None:
            return False
        current_write_docs_dir = Path(self.app_docs_dir).parent
        return str(self._readable_docs_dir) != str(current_write_docs_dir) and str(write_docs_dir) == str(current_write_docs_dir)

    def get_live2d_lookup_roots(self, *, prefer_writable: bool = True) -> list[Path]:
        """Return Live2D lookup paths (deduped).

        Prefers the writable runtime directory by default, falling back to the read-only
        legacy directory on miss, avoiding the CFA-mode pitfall where a newly imported
        model exists but the legacy directory is still matched first.
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
        """Ensure the vrm directory and its animation subdirectory exist under the user documents directory"""
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
        """Ensure the mmd directory and its animation subdirectory exist under the user documents directory"""
        try:
            if not self._ensure_app_docs_directory():
                return False
            self.mmd_dir.mkdir(parents=True, exist_ok=True)
            self.mmd_animation_dir.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create mmd directory: {e}", file=sys.stderr)
            return False

    def ensure_pngtuber_directory(self):
        """Ensure the user PNGTuber asset directory exists."""
        try:
            if not self._ensure_app_docs_directory():
                return False
            self.pngtuber_dir.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create pngtuber directory: {e}", file=sys.stderr)
            return False
        
    def ensure_chara_directory(self):
        """Ensure the character_cards directory under Documents exists"""
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
        """Ensure the card_faces directory under Documents exists"""
        try:
            if not self._ensure_app_docs_directory():
                return False
            self.card_faces_dir.mkdir(exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create card_faces directory: {e}", file=sys.stderr)
            return False

    def migrate_default_card_faces(self):
        """Backfill built-in default card faces without overwriting user-created ones."""
        source_dir = self.project_config_dir.parent / "static" / "default" / "card_faces"
        if not source_dir.exists():
            return
        if not self.ensure_card_faces_directory():
            return

        try:
            source_files = list(source_dir.glob("*.png"))
        except Exception as e:
            self._log(f"Warning: Failed to scan default card faces: {e}")
            return

        for source_path in source_files:
            target_path = self.card_faces_dir / source_path.name
            if not target_path.exists():
                try:
                    shutil.copy2(source_path, target_path)
                    self._log(f"[ConfigManager] Migrated default card face: {source_path.name}")
                except Exception as e:
                    self._log(f"Warning: Failed to migrate default card face {source_path.name}: {e}")

            source_meta_path = source_path.with_suffix(".json")
            target_meta_path = self.card_face_meta_path(source_path.stem)
            if source_meta_path.exists() and not target_meta_path.exists():
                try:
                    shutil.copy2(source_meta_path, target_meta_path)
                    self._log(f"[ConfigManager] Migrated default card face meta: {source_meta_path.name}")
                except Exception as e:
                    self._log(f"Warning: Failed to migrate default card face meta {source_meta_path.name}: {e}")

    def card_face_meta_path(self, name: str):
        """Return the catgirl card-face metadata sidecar file path (card_faces/{name}.json).

        No existence check is performed; callers must handle that themselves. Used only
        for reading/writing sidecar metadata (author, creation time, source, etc.).
        """
        return self.card_faces_dir / f"{name}.json"

    def ensure_cloudsave_structure(self):
        """Ensure the local cloudsave base directories exist.

        Only the directory skeleton and local workspace are created here, not manifest
        content, so phase 0 can land path and state infrastructure first without
        changing existing sync semantics.
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
        """Ensure the local state directory exists."""
        self._last_local_state_directory_error = None
        try:
            if self.anchor_root.exists() and not self.anchor_root.is_dir():
                raise LocalStateDirectoryError(
                    "Local state anchor root is unavailable",
                    anchor_root=self.anchor_root,
                    local_state_dir=self.local_state_dir,
                    failed_path=self.anchor_root,
                    reason="anchor_root exists but is not a directory",
                )
            self.anchor_root.mkdir(parents=True, exist_ok=True)
            if self.local_state_dir.exists() and not self.local_state_dir.is_dir():
                raise LocalStateDirectoryError(
                    "Local state directory is unavailable",
                    anchor_root=self.anchor_root,
                    local_state_dir=self.local_state_dir,
                    failed_path=self.local_state_dir,
                    reason="local_state_dir exists but is not a directory",
                )
            self.local_state_dir.mkdir(parents=True, exist_ok=True)
            probe_path = self.local_state_dir / f".neko_state_write_probe.{uuid.uuid4().hex}.tmp"
            try:
                with open(probe_path, "w", encoding="utf-8") as probe_file:
                    probe_file.write("probe")
                    probe_file.flush()
            finally:
                try:
                    probe_path.unlink()
                except FileNotFoundError:
                    pass
            return True
        except LocalStateDirectoryError as e:
            self._last_local_state_directory_error = e
            print(f"Warning: Failed to create local state directory: {e}", file=sys.stderr)
            return False
        except Exception as e:
            diagnostic = LocalStateDirectoryError(
                "Local state directory is unavailable",
                anchor_root=self.anchor_root,
                local_state_dir=self.local_state_dir,
                failed_path=self.local_state_dir,
                reason=str(e),
            )
            self._last_local_state_directory_error = diagnostic
            print(f"Warning: Failed to create local state directory: {diagnostic}", file=sys.stderr)
            return False

    def _raise_local_state_directory_error(self, operation):
        diagnostic = getattr(self, "_last_local_state_directory_error", None)
        message = f"Failed to ensure local state directory before {operation}"
        if isinstance(diagnostic, LocalStateDirectoryError):
            raise LocalStateDirectoryError(
                message,
                anchor_root=diagnostic.anchor_root,
                local_state_dir=diagnostic.local_state_dir,
                failed_path=diagnostic.failed_path,
                reason=diagnostic.reason,
            ) from diagnostic
        raise LocalStateDirectoryError(
            message,
            anchor_root=self.anchor_root,
            local_state_dir=self.local_state_dir,
            failed_path=self.local_state_dir,
            reason="ensure_local_state_directory returned False",
        )

    def _raise_local_state_file_error(self, operation, path, reason, cause=None):
        error = LocalStateDirectoryError(
            f"Failed to ensure local state file before {operation}",
            anchor_root=self.anchor_root,
            local_state_dir=self.local_state_dir,
            failed_path=path,
            reason=reason,
        )
        if cause is not None:
            raise error from cause
        raise error

    def _save_local_state_json_file(self, path, data, operation):
        path = Path(path)
        if path.exists() and not path.is_file():
            self._raise_local_state_file_error(
                operation,
                path,
                "state file target exists but is not a file",
            )
        try:
            self._save_json_file(path, data)
        except OSError as e:
            self._raise_local_state_file_error(operation, path, str(e), cause=e)

    def _load_local_state_json_file(self, path, default_value, operation):
        path = Path(path)
        if path.exists() and not path.is_file():
            self._raise_local_state_file_error(
                operation,
                path,
                "state file target exists but is not a file",
            )
        try:
            return self._load_json_file(path, default_value)
        except OSError as e:
            self._raise_local_state_file_error(operation, path, str(e), cause=e)

    def build_default_root_state(self):
        """Build default root_state content."""
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
        """Build default cloudsave_local_state content."""
        return {
            "version": self.CLOUDSAVE_LOCAL_STATE_VERSION,
            "client_id": str(client_id or uuid.uuid4().hex),
            "next_sequence_number": 1,
            "last_applied_manifest_fingerprint": "",
            "last_successful_export_at": "",
            "last_successful_import_at": "",
        }

    def build_default_character_tombstones_state(self):
        """Build default per-character tombstone local state."""
        return {
            "version": self.CHARACTER_TOMBSTONES_STATE_VERSION,
            "tombstones": [],
        }

    def _load_json_file(self, path, default_value=None):
        """Load an arbitrary JSON file; returns a copy of the default when the file is missing."""
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
        """Atomically save an arbitrary JSON file."""
        atomic_write_json(path, data, ensure_ascii=False, indent=2)

    def load_root_state(self, default_value=None):
        """Load root_state; returns the default state when missing."""
        if default_value is None:
            default_value = self.build_default_root_state()
        state = self._load_local_state_json_file(
            self.root_state_path,
            default_value,
            "loading root_state",
        )
        if self._has_selected_root_unavailable_recovery_override():
            return self._build_selected_root_unavailable_recovery_state(state)
        return state

    def save_root_state(self, data):
        """Save root_state."""
        if not self.ensure_local_state_directory():
            self._raise_local_state_directory_error("saving root_state")
        self._save_local_state_json_file(self.root_state_path, data, "saving root_state")

    def load_cloudsave_local_state(self, default_value=None):
        """Load cloudsave_local_state; returns a default with a stable field structure when missing."""
        if default_value is None:
            default_value = self.build_default_cloudsave_local_state()
        return self._load_local_state_json_file(
            self.cloudsave_local_state_path,
            default_value,
            "loading cloudsave_local_state",
        )

    def save_cloudsave_local_state(self, data):
        """Save cloudsave_local_state."""
        if not self.ensure_local_state_directory():
            self._raise_local_state_directory_error("saving cloudsave_local_state")
        self._save_local_state_json_file(
            self.cloudsave_local_state_path,
            data,
            "saving cloudsave_local_state",
        )

    def load_character_tombstones_state(self, default_value=None):
        """Load per-character tombstone local state."""
        if default_value is None:
            default_value = self.build_default_character_tombstones_state()
        return self._load_local_state_json_file(
            self.character_tombstones_state_path,
            default_value,
            "loading character_tombstones_state",
        )

    def save_character_tombstones_state(self, data):
        """Save per-character tombstone local state."""
        if not self.ensure_local_state_directory():
            self._raise_local_state_directory_error("saving character_tombstones_state")
        self._save_local_state_json_file(
            self.character_tombstones_state_path,
            data,
            "saving character_tombstones_state",
        )

    def ensure_cloudsave_state_files(self):
        """Ensure local cloudsave-related state files exist; returns whether anything was created."""
        created = False
        if not self.ensure_local_state_directory():
            diagnostic = getattr(self, "_last_local_state_directory_error", None)
            diagnostic_suffix = f"\n{diagnostic}" if diagnostic is not None else ""
            raise RuntimeError(
                "Failed to initialize local state directory for "
                f"{self.root_state_path.name}, "
                f"{self.cloudsave_local_state_path.name}, and "
                f"{self.character_tombstones_state_path.name}"
                f"{diagnostic_suffix}"
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
        Get the config file path
        
        Priority:
        1. Documents/{APP_NAME}/config/
        2. project directory/config/
        
        Args:
            filename: config file name
            
        Returns:
            Path: config file path
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
        """Get the runtime source-of-truth config path (always under app_docs_dir/config)."""
        return self.config_dir / filename
    
    def _get_localized_characters_source(self):
        """Get the localized characters.json source file path based on user language.
        
        Returns:
            Path | None: localized file path, or None when language detection fails or the file does not exist (fall back to default)
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
        elif lang_lower.startswith('es'):
            suffix = 'es'
        elif lang_lower.startswith('pt'):
            suffix = 'pt'
        else:
            # 未知语言，回退
            return None

        localized_path = self.project_config_dir / 'characters' / f"{suffix}.json"
        return localized_path if localized_path.exists() else None
    
    def migrate_config_files(self):
        """
        Migrate config files to Documents
        
        Strategy:
        1. Check the config folder under Documents; create it if missing
        2. For each config file:
           - if present under Documents, skip
           - if absent under Documents:
             - characters.json: pick the localized version by language, falling back to default
             - other files: copy from the project config
           - if neither exists, do nothing (defaults are created later)
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
        Migrate memory files to Documents
        
        Strategy:
        1. Check the memory folder under Documents; create it if missing
        2. Migrate all memory files and directories
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
        At startup, perform only a **soft migration** of ``memory/`` under legacy roots
        (``Documents\\N.E.K.O`` / original CFA read-only paths, etc.): move character
        directories still present in ``characters.json[猫娘]`` to the current runtime
        ``memory_dir``; if the runtime already has a directory of the same name, keep
        the legacy copy and print a warning — never overwrite.

        **Unlinked entries** (orphan memory whose directory name is not in
        ``characters.json[猫娘]``) are out of scope here; they are handled entirely by
        the Workshop page's "clean up legacy memory" button via
        ``/api/memory/legacy/scan`` + ``purge`` with explicit user selection.

        This method should be called after ``migrate_config_files`` /
        ``migrate_memory_files``, when ``characters.json`` is in place. Any failure is
        only logged, never raised — startup must not be blocked.
        """  # noqa: DOCSTRING_CJK
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
            Squash the exception into a sanitized string: keep only the class name +
            errno + strerror, never printing the filename argument carried by
            OSError/PermissionError (it would expose the Documents username +
            character directory name).
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

    # --- Voice storage helpers ---

    def load_voice_storage(self):
        """Load the voice config storage"""
        try:
            return self.load_json_config('voice_storage.json', default_value=deepcopy(DEFAULT_CONFIG_DATA['voice_storage.json']))
        except Exception as e:
            logger.error("加载音色配置失败: %s", e)
            return {}

    def save_voice_storage(self, data):
        """Save the voice config storage"""
        try:
            self.save_json_config('voice_storage.json', data)
        except Exception as e:
            logger.error("保存音色配置失败: %s", e)
            raise

    @staticmethod
    def is_legacy_cosyvoice_id(voice_id: str) -> bool:
        """CosyVoice v2 / v3 cloned voice IDs became invalid with the CosyVoice 3.5 upgrade."""
        return bool(voice_id) and (
            voice_id.startswith("cosyvoice-v2") or voice_id.startswith("cosyvoice-v3-")
        )

    @staticmethod
    def is_deprecated_free_yui_voice_id(voice_id) -> bool:
        """Whether voice_id is the replaced free YUI preset voice still lingering in existing saves."""
        return bool(voice_id) and str(voice_id).strip() in _DEPRECATED_FREE_YUI_VOICE_IDS

    def remap_deprecated_free_yui_voice_id(self, voice_id):
        """Deprecated free YUI preset voice → active CN yui_cn (CN free route migration only).

        Non-deprecated values are returned as-is (no strip normalization), so callers don't
        mistake a mere leading/trailing whitespace difference for "already migrated" and
        continue, skipping this round's cleanup of invalid voice_ids.

        The deprecated value is the CN StepFun YUI tone; only the CN free (lanlan.tech)
        route actually serves it, and only that route migrates to the active
        free_voices["yui_cn"]:
          - overseas free (lanlan.app → free_intl): returned as-is; the existing validate
            marks it invalid on the overseas route and clears it → server-side default
            voice fallback. The client does not inject the "yui"/native alias (PR #1643
            design principle: free_intl inherits the Gemini-native provider and must not
            leak StepFun magic ids or their aliases into that catalog; besides,
            unconditionally swapping to the CN voice tone would land a non-empty voice_id
            on free_intl into external TTS).
          - non-free routes: returned as-is; the deprecated StepFun preset is unusable
            there, left to the clear-and-fallback path.
        Also returned as-is when the active yui_cn is missing/empty/itself in the
        deprecated set — never borrow cuteGirl or other presets as stand-ins to morph YUI
        into a different voice, and never replace one deprecated value with another,
        creating an endless loop.
        """
        if not self.is_deprecated_free_yui_voice_id(voice_id):
            return voice_id
        core_cfg = self.get_core_config() or {}
        if (core_cfg.get("CORE_API_TYPE") or core_cfg.get("coreApi")) != "free":
            return voice_id

        # get_core_config() 已按非大陆把 CORE_URL 改写成 lanlan.app，URL 即可判海外；
        # _check_non_mainland 兜底地理判定。与 ensure_default 同源。海外不迁移（见上）。
        core_url = str(core_cfg.get("CORE_URL") or "")
        overseas = is_free_lanlan_app_route("free", core_url)
        if not overseas:
            try:
                overseas = bool(self._check_non_mainland())
            except Exception:
                overseas = False
        if overseas:
            return voice_id

        from utils.api_config_loader import get_free_voices
        current = str((get_free_voices() or {}).get("yui_cn") or "").strip()
        if current and current not in _DEPRECATED_FREE_YUI_VOICE_IDS:
            return current
        return voice_id

    def get_tts_api_key(self, provider: str) -> str | None:
        """Return the configured TTS API key for a provider, or None.

        - cosyvoice: api_key from the tts_custom model config
        - cosyvoice_intl: API key resolved by the CosyVoice intl runtime
        - minimax:   ASSIST_API_KEY_MINIMAX → MINIMAX_API_KEY fallback
        - minimax_intl: ASSIST_API_KEY_MINIMAX_INTL → MINIMAX_INTL_API_KEY fallback
        - mimo: ASSIST_API_KEY_MIMO
        - doubao_tts: ttsModelApiKey only when the active TTS provider is doubao_tts,
          then the dedicated Doubao Speech keybook entry
        """
        if provider == 'cosyvoice':
            core_config = self.get_core_config()
            if self._is_vllm_omni_tts_selected(core_config):
                return None
            tts_config = self.get_model_api_config('tts_custom')
            key = (tts_config.get('api_key') or '').strip()
            return key or None
        if provider == 'cosyvoice_intl':
            key = (self.get_cosyvoice_clone_runtime(provider).get('api_key') or '').strip()
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
        if provider == 'elevenlabs':
            core_config = self.get_core_config()
            key = (core_config.get('ASSIST_API_KEY_ELEVENLABS') or '').strip()
            if not key:
                key = (core_config.get('ELEVENLABS_API_KEY') or '').strip()
            if '***' in key:
                return None
            return key or None
        if provider == 'mimo':
            core_config = self.get_core_config()
            use_token_plan = (
                (core_config.get('assistApi') or '').strip() == 'mimo'
                and _as_bool(core_config.get('useMimoTokenPlan', False))
            )
            key_field = 'ASSIST_API_KEY_MIMO_TOKEN_PLAN' if use_token_plan else 'ASSIST_API_KEY_MIMO'
            key = (core_config.get(key_field) or '').strip()
            if '***' in key:
                return None
            return key or None
        if provider == 'doubao_tts':
            try:
                raw_core_config = self.load_json_config('core_config.json', {})
            except Exception:
                raw_core_config = {}
            key = ''
            if str(raw_core_config.get('ttsModelProvider') or '').strip() == 'doubao_tts':
                key = (raw_core_config.get('ttsModelApiKey') or '').strip()
                if '***' in key:
                    key = ''
            if not key:
                key = (raw_core_config.get('assistApiKeyDoubaoTts') or '').strip()
                if '***' in key:
                    key = ''
            return key or None
        return None

    @staticmethod
    def _is_vllm_omni_tts_selected(core_config: dict | None) -> bool:
        if not isinstance(core_config, dict):
            return False
        return _as_bool(core_config.get('ENABLE_CUSTOM_API'), False) and (
            str(core_config.get('ttsModelProvider') or '').strip() == 'vllm_omni'
        )

    def _is_local_tts_storage_active(
        self,
        tts_config: dict | None = None,
        core_config: dict | None = None,
    ) -> bool:
        """Return True when the current TTS config should use __LOCAL_TTS__ voices."""
        if tts_config is None:
            tts_config = self.get_model_api_config('tts_custom')
        if core_config is None:
            core_config = self.get_core_config()
        base_url = str((tts_config or {}).get('base_url') or '')
        return _as_bool((tts_config or {}).get('is_custom'), False) and base_url.startswith(('ws://', 'wss://')) and (
            not self._is_vllm_omni_tts_selected(core_config)
        )

    def get_cosyvoice_clone_runtime(self, provider: str = 'cosyvoice') -> dict:
        """Return the Alibaba CN/international runtime config explicitly selected on the voice-clone page."""
        normalized_provider = str(provider or 'cosyvoice').strip().lower()
        if normalized_provider not in ('cosyvoice', 'cosyvoice_intl'):
            normalized_provider = 'cosyvoice'

        qwen_provider = 'qwen_intl' if normalized_provider == 'cosyvoice_intl' else 'qwen'
        key_field = 'ASSIST_API_KEY_QWEN_INTL' if qwen_provider == 'qwen_intl' else 'ASSIST_API_KEY_QWEN'
        core_config = self.get_core_config()
        api_key = (core_config.get(key_field) or '').strip()

        profile = get_assist_api_profiles().get(qwen_provider, {})
        raw_core_cfg = deepcopy(DEFAULT_CONFIG_DATA['core_config.json'])
        try:
            file_data = self.load_json_config('core_config.json', {})
            if isinstance(file_data, dict):
                raw_core_cfg.update(file_data)
        except Exception:
            pass

        base_url = self._get_saved_provider_url(
            raw_core_cfg,
            'assist',
            qwen_provider,
            profile,
            'OPENROUTER_URL',
            'OPENROUTER_URLS',
        )
        if not base_url:
            base_url = profile.get('OPENROUTER_URL', '')

        if normalized_provider == 'cosyvoice' and not api_key:
            if not self._is_vllm_omni_tts_selected(core_config):
                try:
                    legacy_tts_config = self.get_model_api_config('tts_custom')
                except Exception:
                    legacy_tts_config = {}
                legacy_key = (legacy_tts_config.get('api_key') or '').strip()
                legacy_url = (legacy_tts_config.get('base_url') or '').strip()
                if legacy_key and not (
                    'dashscope-intl.aliyuncs.com' in legacy_url
                    or 'dashscope-us.aliyuncs.com' in legacy_url
                ):
                    api_key = legacy_key
                    if legacy_url:
                        base_url = legacy_url

        if normalized_provider == 'cosyvoice_intl' and api_key:
            suffix = api_key[-8:] if len(api_key) >= 8 else api_key
            storage_key = f'__COSYVOICE_INTL__{suffix}'
        else:
            storage_key = api_key

        return {
            'provider': normalized_provider,
            'qwen_provider': qwen_provider,
            'api_key': api_key,
            'base_url': base_url,
            'storage_key': storage_key,
            'provider_label': '阿里国际版CosyVoice' if normalized_provider == 'cosyvoice_intl' else '阿里百炼CosyVoice',
        }

    def _get_cosyvoice_storage_keys(self, voice_storage: dict | None = None) -> list[tuple[str, str]]:
        """Return the voice_storage key for the current Alibaba CN/international API key."""
        if voice_storage is None:
            voice_storage = self.load_voice_storage()
        result: list[tuple[str, str]] = []
        seen = set()

        def _add(bucket: str, provider: str):
            if bucket and bucket in voice_storage and bucket not in seen:
                seen.add(bucket)
                result.append((bucket, provider))

        domestic_runtime = self.get_cosyvoice_clone_runtime('cosyvoice')
        _add(domestic_runtime.get('storage_key', ''), 'cosyvoice')

        intl_runtime = self.get_cosyvoice_clone_runtime('cosyvoice_intl')
        intl_storage_key = intl_runtime.get('storage_key', '')
        _add(intl_storage_key, 'cosyvoice_intl')

        # 旧版国际版曾按原始 API Key 入库，存在时纳入当前视图以免音色丢失。
        intl_raw_key = (intl_runtime.get('api_key') or '').strip()
        if intl_raw_key and intl_raw_key != intl_storage_key:
            _add(intl_raw_key, 'cosyvoice_intl')

        return result

    def _get_minimax_storage_keys(self) -> list[str]:
        """Return the list of voice_storage keys for the current MiniMax API keys.

        Uses get_tts_api_key to obtain resolved keys (including env fallback),
        generating bucket prefixes for the CN and international services respectively.
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

    def _get_elevenlabs_storage_keys(self) -> list[str]:
        """Return the list of voice_storage keys for the current ElevenLabs API key."""
        voice_storage = self.load_voice_storage()
        result = []
        key = self.get_tts_api_key('elevenlabs')
        if key:
            suffix = key[-8:] if len(key) >= 8 else key
            bucket = f'__ELEVENLABS__{suffix}'
            if bucket in voice_storage:
                result.append(bucket)
        return result

    def _get_mimo_storage_keys(self) -> list[str]:
        """Return the list of voice_storage keys for the current MiMo API key.

        Dual to :meth:`_get_elevenlabs_storage_keys`: MiMo cloned voices live in
        a ``__MIMO__{suffix}`` bucket keyed by the MiMo API key, so they merge
        into the current-API voice list regardless of which core/TTS provider is
        otherwise active (a MiMo clone is selected by ``voice_meta.provider`` at
        dispatch, not by config — see ``workers/mimo.py``)."""
        voice_storage = self.load_voice_storage()
        result = []
        key = self.get_tts_api_key('mimo')
        if key:
            suffix = key[-8:] if len(key) >= 8 else key
            bucket = f'__MIMO__{suffix}'
            if bucket in voice_storage:
                result.append(bucket)
        return result

    def _get_doubao_tts_storage_keys(self) -> list[str]:
        voice_storage = self.load_voice_storage()
        result = []
        key = self.get_tts_api_key('doubao_tts')
        if key:
            suffix = key[-8:] if len(key) >= 8 else key
            bucket = f'{DOUBAO_VOICE_STORAGE_KEY}{suffix}'
            if bucket in voice_storage:
                result.append(bucket)
        return result

    def _get_vllm_omni_storage_keys(self) -> list[str]:
        """Return the list of voice_storage keys for vLLM-Omni cloned voices.

        Dual to :meth:`_get_mimo_storage_keys`, with one key difference: vLLM-Omni
        is a self-hosted local service with **no API key**, so cloned voices live
        in a single fixed ``__VLLM_OMNI__`` bucket (no key suffix) instead of a
        per-key ``__MIMO__{suffix}`` bucket. A vLLM-Omni clone is selected by
        ``voice_meta.provider`` at dispatch (the inline reference-audio model, see
        ``workers/vllm_omni.py``), so the bucket merges into the current-API voice
        list regardless of which core/TTS provider is otherwise active."""
        voice_storage = self.load_voice_storage()
        bucket = '__VLLM_OMNI__'
        return [bucket] if bucket in voice_storage else []

    @staticmethod
    def _infer_provider_from_storage_key(storage_key: str) -> str:
        """Infer the provider from a voice_storage partition key (only for legacy data compatibility)."""
        if storage_key == '__LOCAL_TTS__':
            return 'local'
        if storage_key.startswith('__VLLM_OMNI__'):
            return 'vllm_omni'
        if storage_key.startswith('__MIMO__'):
            return 'mimo'
        if storage_key.startswith(DOUBAO_VOICE_STORAGE_KEY):
            return 'doubao_tts'
        if storage_key.startswith('__ELEVENLABS__'):
            return 'elevenlabs'
        if storage_key.startswith('__MINIMAX_INTL__'):
            return 'minimax_intl'
        if storage_key.startswith('__MINIMAX__'):
            return 'minimax'
        if storage_key.startswith('__COSYVOICE_INTL__'):
            return 'cosyvoice_intl'
        return 'cosyvoice'

    def get_voices_for_current_api(self, for_listing: bool = False):
        """Get all voices for the current TTS config

        Returns voices based on the TTS config actually in use:
        1. Local TTS (ws/wss protocol) → voices under __LOCAL_TTS__
        2. Alibaba Cloud TTS (via ASSIST_API_KEY_QWEN) → voices under that API key
        3. Otherwise → voices under AUDIO_API_KEY
        The result also merges Alibaba international, MiniMax and ElevenLabs voices.

        Every returned voice_data is guaranteed to contain a ``provider`` field
        (``local`` / ``minimax`` / ``minimax_intl`` / ``elevenlabs`` / ``cosyvoice`` / ``cosyvoice_intl``).

        ``for_listing=True`` enables UI-list-oriented filtering: on the free edition, skip
        the *cloud* main partitions (CosyVoice / Qwen), because those voices require paid
        API key auth (and cannot actually be used at runtime via step_realtime_tts_worker
        free_mode); listing them would only mislead users. ``__LOCAL_TTS__`` runs local
        inference over WebSocket and still works on the free edition, so it must be shown
        even with for_listing+free. MiniMax and GSV use independent config/routing and
        also work on the free edition, so the MiniMax merge below is kept and the
        /custom_tts_voices GSV is unaffected.

        The default ``for_listing=False`` keeps the full view — validation chains like
        ``validate_voice_id`` / ``cleanup_invalid_voice_ids`` must see every voice actually
        present in storage, otherwise the free edition would misjudge voice_ids users saved
        during a paid period as nonexistent and clear them outright during cleanup.

        Provider-keyed CosyVoice clone buckets are still merged below: if a
        clone provider API key is configured and has stored voices, the clone
        list should show those voices even when the main cloud bucket is hidden.
        """
        voice_storage = self.load_voice_storage()
        storage_key = ''
        result: dict = {}

        tts_config = self.get_model_api_config('tts_custom')
        core_config = self.get_core_config()
        is_local_tts = self._is_local_tts_storage_active(tts_config, core_config)
        hide_cloud_main = for_listing and self.is_free_voice()

        if is_local_tts:
            # 本地 WebSocket TTS：免费版仍可用，列表必须可见
            storage_key = '__LOCAL_TTS__'
            all_voices = voice_storage.get(storage_key, {})
            result = dict(all_voices)
        elif not hide_cloud_main:
            tts_api_key = tts_config.get('api_key', '')
            if tts_api_key:
                storage_key = tts_api_key
                all_voices = voice_storage.get(storage_key, {})
                result = dict(all_voices)
            else:
                audio_api_key = core_config.get('AUDIO_API_KEY', '')
                if audio_api_key:
                    storage_key = audio_api_key
                    all_voices = voice_storage.get(storage_key, {})
                    result = dict(all_voices)

        cosyvoice_storage_keys = self._get_cosyvoice_storage_keys(voice_storage)

        # 确保主分区音色有 provider 字段
        default_provider = self._infer_provider_from_storage_key(storage_key) if storage_key else 'cosyvoice'
        for cosy_key, cosy_provider in cosyvoice_storage_keys:
            if cosy_key == storage_key:
                default_provider = cosy_provider
                break
        for vdata in result.values():
            if isinstance(vdata, dict):
                if 'provider' not in vdata:
                    vdata['provider'] = default_provider
                elif default_provider == 'cosyvoice_intl' and vdata.get('provider') == 'cosyvoice':
                    vdata['provider'] = 'cosyvoice_intl'

        # 合并阿里国际版音色，并确保 provider 字段与分区一致
        for ck, cosy_provider in cosyvoice_storage_keys:
            if ck == storage_key:
                continue
            cosy_voices = voice_storage.get(ck, {})
            for vid, vdata in cosy_voices.items():
                if vid not in result:
                    if isinstance(vdata, dict) and (
                        'provider' not in vdata or ck.startswith('__COSYVOICE_INTL__')
                    ):
                        vdata['provider'] = cosy_provider
                    result[vid] = vdata

        # 合并 MiniMax 音色，并确保 provider 字段
        for mk in self._get_minimax_storage_keys():
            mm_provider = self._infer_provider_from_storage_key(mk)
            minimax_voices = voice_storage.get(mk, {})
            for vid, vdata in minimax_voices.items():
                if vid not in result:
                    if isinstance(vdata, dict) and 'provider' not in vdata:
                        vdata['provider'] = mm_provider
                    result[vid] = vdata

        # 合并 ElevenLabs 音色，并确保 provider 字段
        for ek in self._get_elevenlabs_storage_keys():
            eleven_voices = voice_storage.get(ek, {})
            for vid, vdata in eleven_voices.items():
                if vid not in result:
                    if isinstance(vdata, dict) and 'provider' not in vdata:
                        vdata['provider'] = 'elevenlabs'
                    result[vid] = vdata

        # 合并 MiMo 克隆音色，并确保 provider 字段（dual to ElevenLabs/MiniMax；MiMo 克隆走
        # 独立 __MIMO__ 桶 + voice_meta 选中，与当前 core/TTS provider 无关）
        for mimo_key in self._get_mimo_storage_keys():
            mimo_voices = voice_storage.get(mimo_key, {})
            for vid, vdata in mimo_voices.items():
                if vid not in result:
                    if isinstance(vdata, dict) and 'provider' not in vdata:
                        vdata['provider'] = 'mimo'
                    result[vid] = vdata

        for doubao_key in self._get_doubao_tts_storage_keys():
            doubao_voices = voice_storage.get(doubao_key, {})
            for vid, vdata in doubao_voices.items():
                if vid not in result:
                    if isinstance(vdata, dict) and 'provider' not in vdata:
                        vdata['provider'] = 'doubao_tts'
                    result[vid] = vdata

        # 合并 vLLM-Omni 克隆音色（dual to MiMo；vLLM-Omni 克隆走固定 __VLLM_OMNI__ 桶
        # + voice_meta 选中，与 MiMo 同构。差异：vLLM-Omni 是本地服务无 API key，桶名固定）
        for vllm_key in self._get_vllm_omni_storage_keys():
            vllm_voices = voice_storage.get(vllm_key, {})
            for vid, vdata in vllm_voices.items():
                if vid not in result:
                    if isinstance(vdata, dict) and 'provider' not in vdata:
                        vdata['provider'] = 'vllm_omni'
                    result[vid] = vdata

        if for_listing:
            # UI 试听列表不需要 MiMo 克隆的参考样本 base64（可达 MB）——剥掉，避免把大 blob
            # 推给前端。dispatch / preview 走 for_listing=False，仍拿到完整 voice_meta。
            result = {
                vid: ({k: v for k, v in vdata.items() if k != 'clone_sample_b64'}
                      if isinstance(vdata, dict) and 'clone_sample_b64' in vdata else vdata)
                for vid, vdata in result.items()
            }

        return result

    def save_voice_for_current_api(self, voice_id, voice_data):
        """Save a voice for the current AUDIO_API_KEY"""
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
        """Save a voice for the given API key (used when cloning with the actual API key instead of AUDIO_API_KEY)"""
        if not api_key:
            raise ValueError("API Key 不能为空")

        voice_storage = self.load_voice_storage()
        if api_key not in voice_storage:
            voice_storage[api_key] = {}

        voice_storage[api_key][voice_id] = voice_data
        self.save_voice_storage(voice_storage)

    def voice_id_exists_in_any_storage(self, voice_id: str) -> bool:
        """Whether voice_id appears under any bucket of voice_storage.json.

        Wider than the view of get_voices_for_current_api(): the latter filters buckets by
        the current tts_custom config (AUDIO_API_KEY / __LOCAL_TTS__ / current
        ASSIST_API_KEY_QWEN etc.), so cloned voices saved in old buckets before a config
        switch don't appear in the view. Collision detection ("has the user ever explicitly
        cloned this voice_id") must look at the full storage, not just the current view,
        otherwise a same-named voice gets silently switched to the built-in provider.
        """
        if not voice_id:
            return False
        voice_storage = self.load_voice_storage()
        if not isinstance(voice_storage, dict):
            return False
        voice_id_key = voice_id.casefold()
        for bucket in voice_storage.values():
            if isinstance(bucket, dict) and any(
                isinstance(stored_voice_id, str)
                and stored_voice_id.casefold() == voice_id_key
                for stored_voice_id in bucket
            ):
                return True
        return False

    def find_voice_by_audio_md5(self, api_key: str, audio_md5: str, ref_language: str | None = None):
        """Look up an existing voice by reference-audio MD5 (and optional ref_language) under the given API key.

        Returns (voice_id, voice_data) or None.
        Old entries without an audio_md5 field are skipped automatically (backward compatible).
        When ref_language is not None, the ref_language in voice_data must also match
        (old entries without a ref_language field are treated as 'ch').
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

    def find_cosyvoice_voice_by_audio_md5(
        self,
        provider: str,
        audio_md5: str,
        ref_language: str | None = None,
    ):
        """Look up a reference-audio MD5 across the current and legacy CosyVoice storage partitions."""
        runtime = self.get_cosyvoice_clone_runtime(provider)
        storage_keys = []
        seen = set()

        def _add(storage_key: str):
            storage_key = (storage_key or '').strip()
            if storage_key and storage_key not in seen:
                seen.add(storage_key)
                storage_keys.append(storage_key)

        _add(runtime.get('storage_key', ''))
        if runtime.get('provider') == 'cosyvoice_intl':
            # 旧版国际版曾按原始 API Key 入库，MD5 去重也必须兼容该分区。
            _add(runtime.get('api_key', ''))

        for storage_key in storage_keys:
            existing = self.find_voice_by_audio_md5(storage_key, audio_md5, ref_language)
            if existing:
                return existing
        return None

    def delete_voice_for_current_api(self, voice_id):
        """Delete the given voice under the current TTS config (including standalone-provider voices)"""
        voice_storage = self.load_voice_storage()

        # 先检查带前缀的独立服务商存储（含 vLLM-Omni 固定桶 __VLLM_OMNI__）
        for storage_key in list(voice_storage.keys()):
            if (
                storage_key.startswith('__MINIMAX__')
                or storage_key.startswith('__MINIMAX_INTL__')
                or storage_key.startswith('__ELEVENLABS__')
                or storage_key.startswith('__MIMO__')
                or storage_key.startswith(DOUBAO_VOICE_STORAGE_KEY)
                or storage_key.startswith('__COSYVOICE_INTL__')
                or storage_key.startswith('__VLLM_OMNI__')
            ) and voice_id in voice_storage.get(storage_key, {}):
                # 克隆身份（含 MiMo 的样本 base64）都在 voice_data 里，删除 entry 随之消失，
                # 无旁路本地文件需清理（对偶 MiniMax/ElevenLabs）。
                del voice_storage[storage_key][voice_id]
                self.save_voice_storage(voice_storage)
                return True

        # 再检查当前阿里国内/国际 API Key 的原始分区
        for storage_key, _provider in self._get_cosyvoice_storage_keys():
            if voice_id in voice_storage.get(storage_key, {}):
                del voice_storage[storage_key][voice_id]
                self.save_voice_storage(voice_storage)
                return True
        
        tts_config = self.get_model_api_config('tts_custom')
        is_local_tts = self._is_local_tts_storage_active(tts_config)

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

    def _is_selected_hosted_preset_voice(self, voice_id):
        """Whether voice_id is a built-in (preset) voice of the currently selected
        TTS provider in tts_provider_registry (e.g. MiMo, a hosted SaaS).

        Dual to :func:`is_saveable_native_voice` but for the unified provider
        registry: a hosted/local provider's presets are only saveable while that
        provider is the one dispatch would route to, so this gates on the same
        selection the dispatcher uses.

        The hosted providers are registered as a side effect of importing
        ``main_logic.tts_client``. config_manager (utils layer) must NOT import it
        — that's a CI-enforced layer inversion (utils → main_logic). We rely on the
        running app having imported tts_client at startup (the TTS pipeline does),
        so the registry is populated by the time any voice is validated; if it
        isn't (or the lookup errors), this degrades to "not a preset voice" rather
        than breaking validation.
        """
        try:
            from utils import tts_provider_registry
            return tts_provider_registry.is_selected_preset_voice(
                self.get_core_config() or {}, self, voice_id
            )
        except Exception:
            logger.warning("hosted preset voice 校验异常，按非预制处理", exc_info=True)
            return False

    def validate_voice_id(self, voice_id):
        """Validate whether voice_id is valid under the current AUDIO_API_KEY.
        
        Validation covers four kinds of voice_id:
          1. "cosyvoice-v2/v3..." → legacy format, always invalid
          2. "gsv:xxx" → delegated to check_custom_tts_voice_allowed (custom_tts_adapter);
             the adapter decides validity from the tts_custom config
          3. plain IDs → looked up in voice_storage (CosyVoice cloud-cloned voices)
          4. free preset voices → only statically whitelisted here; at runtime core.py
             _should_block_free_preset_voice decides dynamically per route
             (lanlan.tech / lanlan.app) whether they are actually enabled
             (the lanlan.app overseas node does not support preset voices)
        """
        voice_id = str(voice_id or '').strip()
        if not voice_id:
            return True

        if voice_id.startswith('eleven:'):
            return len(voice_id) > len('eleven:')

        custom_tts_allowed = check_custom_tts_voice_allowed(voice_id, self.get_model_api_config)
        if custom_tts_allowed is not None:
            return custom_tts_allowed

        if self._is_vllm_omni_tts_selected(self.get_core_config()):
            return True

        voices = self.get_voices_for_current_api()
        if voice_id in voices:
            return True

        if is_saveable_native_voice(self, voice_id):
            return True

        # hosted/local provider 的内置预制音色（如选中 MiMo 时的预制声线），由
        # tts_provider_registry 收口，仅在该 provider 被选中时算合法
        if self._is_selected_hosted_preset_voice(voice_id):
            return True

        # 免费预设音色允许豁免保存校验，运行时再由 core.py 按当前线路动态判断可用性
        from utils.api_config_loader import get_free_voices
        free_voices = get_free_voices()
        if voice_id in free_voices.values():
            return True

        return False

    def validate_voice_id_for_api_key(self, api_key: str, voice_id: str) -> bool:
        """Validate whether voice_id is valid under the given API key"""
        voice_id = str(voice_id or '').strip()
        if not voice_id:
            return True

        if voice_id.startswith('eleven:'):
            return len(voice_id) > len('eleven:')

        custom_tts_allowed = check_custom_tts_voice_allowed(voice_id, self.get_model_api_config)
        if custom_tts_allowed is not None:
            return custom_tts_allowed

        voice_storage = self.load_voice_storage()
        voices = voice_storage.get(api_key, {})
        if voice_id in voices:
            return True

        if is_saveable_native_voice(self, voice_id):
            return True

        if self._is_selected_hosted_preset_voice(voice_id):
            return True

        from utils.api_config_loader import get_free_voices
        free_voices = get_free_voices()
        if voice_id in free_voices.values():
            return True

        return False

    def normalize_voice_id_to_config(self, voice_id):
        """Resolve a flat / prefixed ``voice_id`` into a structured ``VoiceConfig``.

        A bare id (no ``gsv:`` / ``eleven:`` prefix) needs runtime context to decide
        its ``source`` / ``provider``; this reuses the same resolution chain as
        :meth:`validate_voice_id` (vLLM selected / a clone in the current API's
        voice_storage / a saveable native voice / a free preset) and feeds that
        context to the pure :func:`utils.voice_config.normalize_voice_id`, so the
        migration stays faithful and unambiguous.

        An unresolvable bare id is carried through unchanged in ``ref`` (never
        dropped); callers treat it as "leave the value as-is".
        """
        from utils.voice_config import normalize_voice_id
        from utils.native_voice_registry import (
            get_active_realtime_native_provider,
            is_saveable_native_voice,
        )
        from utils.api_config_loader import get_free_voices

        _voices_cache = {}

        def _clone_lookup(ref):
            if 'voices' not in _voices_cache:
                _voices_cache['voices'] = self.get_voices_for_current_api()
            vdata = _voices_cache['voices'].get(ref)
            if isinstance(vdata, dict):
                return str(vdata.get('provider') or '')
            return None

        def _hosted_preset_provider(ref):
            """Selected hosted/local provider key when ``ref`` is one of its preset
            voices (e.g. MiMo's "Milo"), else None — so the structured object keeps
            the ``source=preset / provider=<key>`` ownership the flat string drops.

            Dual to :meth:`_is_selected_hosted_preset_voice` (used by validate); both
            gate on tts_provider_registry's selection so a hosted preset is only
            recognized while that provider is the one dispatch would route to. A
            single ``selected_preset_provider_key`` dispatch resolves both membership
            and key together, so the two can never disagree. Same layer rule:
            config_manager (utils) must NOT import main_logic, so we only query the
            same-layer registry, which the running app populates by importing
            main_logic.tts_client at startup. Degrades to None on error.
            """
            try:
                from utils import tts_provider_registry
                return tts_provider_registry.selected_preset_provider_key(
                    self.get_core_config() or {}, self, ref
                )
            except Exception:
                logger.warning("hosted preset voice 归一化异常，按非预制处理", exc_info=True)
                return None

        return normalize_voice_id(
            voice_id,
            vllm_selected=self._is_vllm_omni_tts_selected(self.get_core_config()),
            clone_provider_lookup=_clone_lookup,
            is_native=lambda ref: is_saveable_native_voice(self, ref),
            native_provider=get_active_realtime_native_provider(self) or '',
            hosted_preset_provider=_hosted_preset_provider,
            free_voice_ids=set(get_free_voices().values()),
        )

    def voice_id_to_storage_value(self, voice_id):
        """Convert a user-set legacy ``voice_id`` string into its at-rest storage form.

        Write side of the voice-source-unification "union-find style lazy migration":
        every time the user sets/changes a voice, that one entry is migrated to the
        structured object ``{source, provider, ref}`` (migrate-on-touch, never a
        full-table sweep). Empty value is stored as an empty string (= no voice set).
        The read side (:func:`utils.voice_config.read_legacy_voice_id`) tolerates both
        forms, so untouched legacy flat strings keep working.
        """
        s = str(voice_id or '').strip()
        if not s:
            return ''
        from utils.voice_config import to_legacy_voice_id
        vc = self.normalize_voice_id_to_config(s)
        # Round-trip guard: only migrate to the structured object when it reads back to
        # the exact submitted library key. Otherwise (e.g. a provider-tagged but
        # un-prefixed clone key, where to_legacy_voice_id would re-add a prefix and
        # change the key) keep the legacy string verbatim — never let migration rewrite
        # the key a binding points at, or _get_voice_meta would miss and TTS misroute.
        if to_legacy_voice_id(vc) != s:
            return s
        # Ownership guard: a bare id we could NOT resolve (no source/provider tagged)
        # carries zero information beyond the flat string. Storing it as
        # ``{source:"", provider:"", ref}`` is a half-migrated shell that only bloats
        # storage and disguises "ownership unknown" as "migrated" — keep the legacy
        # string until we can actually tag its ownership. (Only resolved bindings,
        # incl. hosted presets like MiMo's, become structured objects.)
        if not vc.source and not vc.provider:
            return s
        return vc.to_dict()

    def cleanup_invalid_voice_ids(self):
        """Clean up invalid voice_ids in characters.json.
        
        Validity is decided uniformly via validate_voice_id, with no provider-specific logic.
        Note: free preset voices are not cleaned here (whitelisted by validate_voice_id);
        actual availability is decided at runtime by core.py per free + lanlan.app/lanlan.tech route.

        Before clearing, deprecated free YUI preset voices are first remapped to the active
        yui_cn (remap_deprecated_free_yui_voice_id), so existing users aren't judged invalid
        and silently dropped to the generic default voice due to the YUI voice ID change.
        A migration hit also triggers a save.

        Returns:
            (cleaned_count, legacy_cosyvoice_names): total cleaned, and the list of character names still using legacy CosyVoice voices
        """
        character_data = self.load_characters()
        cleaned_count = 0
        migrated_count = 0
        legacy_cosyvoice_names: list[str] = []

        catgirls = character_data.get('猫娘', {})
        for name, config in catgirls.items():
            # 容忍扁平串 / 结构对象两形态，统一按 legacy 字符串做 remap / validate；
            # cleanup 不在此把有效条目压成对象（守住「不 bulk sweep」，迁移只在用户设音色时发生）。
            voice_id = read_legacy_voice_id(get_reserved(config, 'voice_id', default='', legacy_keys=('voice_id',)))
            if not voice_id:
                continue
            # 已废弃的免费 YUI 预设音色：先平移到现役 yui_cn，再 continue 跳过后续
            # invalid 判定（新值在 free_voices 白名单内本就合法），保住默认 YUI 音色
            remapped = self.remap_deprecated_free_yui_voice_id(voice_id)
            if remapped and remapped != voice_id:
                set_reserved(config, 'voice_id', remapped)
                migrated_count += 1
                logger.info(
                    "猫娘 '%s' 的废弃 YUI 预设音色 '%s' 已平移到 '%s'",
                    name,
                    voice_id,
                    remapped,
                )
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

        if cleaned_count > 0 or migrated_count > 0:
            self.save_characters(character_data)
            if cleaned_count > 0:
                logger.info("已清理 %d 个无效的 voice_id 引用", cleaned_count)
            if migrated_count > 0:
                logger.info("已平移 %d 个废弃 YUI 预设音色", migrated_count)

        return cleaned_count, legacy_cosyvoice_names

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

    async def aget_core_config(self):
        """Async wrapper for get_core_config: internally open()+json.load() reads core_config.json;
        async endpoints must offload it to avoid blocking the event loop."""
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
        # 调试开关：config.GEOIP_FORCE_NON_MAINLAND 非 None 时直接返回它，绕过真实检测。
        # 生产保持 None（走下方双判）。改 config/__init__.py 那个常量即可，不动这里。
        if GEOIP_FORCE_NON_MAINLAND is not None:
            print(
                f"[GeoIP] override active: forcing non-mainland={GEOIP_FORCE_NON_MAINLAND} "
                "(config.GEOIP_FORCE_NON_MAINLAND)",
                file=sys.stderr,
            )
            return GEOIP_FORCE_NON_MAINLAND

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

        Priority: livestream prefix derivation > overseas lanlan.tech→lanlan.app switch > return as-is.
        When livestream is enabled it only takes over whitelisted free-path endpoints under
        the lanlan.tech domain (/core /text/v1 /tts); other paths go through the original region switch.
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
                # 海外免费统一走 www.lanlan.app（含 /tts）：该节点透传客户端
                # voice 字段到 Gemini，支持 Gemini 全量 + yui。早期把 /tts 降级到
                # 裸 lanlan.app（硬覆盖 Leda 的旧端点）的 .replace 已移除。
                return url.replace('lanlan.tech', 'lanlan.app')
        except Exception:
            pass

        return url

    def _normalize_agent_url(self, url: str) -> str:
        """Temporarily do not rewrite the Agent URL.

        free-agent-model must use the CN ``lanlan.tech`` text entry from the config; keep
        AGENT_MODEL_URL as-is here to avoid normalizing it to ``lanlan.app``.
        """
        return url

    @staticmethod
    def _derive_livestream_url(original_url: str, prefix: str) -> str:
        """Derive the equivalent address of a lanlan.tech URL from the livestream server_prefix.

        - keeps the original URL's path (``/core`` / ``/tts`` / ``/text/v1``) appended after the prefix path
        - scheme family is unchanged (ws/wss in → ws/wss out; http/https in → http/https out)
        - encryption (the ``s`` suffix) follows the prefix's scheme (https/wss prefix → encrypted output)

        Examples:
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

    @staticmethod
    def _provider_url_candidates(profile: dict, url_key: str, list_key: str) -> list[str]:
        """Read the provider's primary URL and candidate URLs, deduped and order-preserving."""
        raw_candidates = [profile.get(url_key)]
        configured_candidates = profile.get(list_key)
        if isinstance(configured_candidates, list):
            raw_candidates.extend(configured_candidates)
        elif isinstance(configured_candidates, str):
            raw_candidates.append(configured_candidates)

        result = []
        seen = set()
        for raw_url in raw_candidates:
            url = str(raw_url or '').strip()
            if not url or url in seen:
                continue
            seen.add(url)
            result.append(url)
        return result

    def _get_saved_provider_url(
        self,
        core_cfg: dict,
        scope: str,
        provider_key: str,
        profile: dict,
        url_key: str,
        list_key: str,
    ) -> str:
        """Return the URL saved by the connectivity test that still belongs to the current provider candidate set."""
        resolved_urls = core_cfg.get('resolvedProviderUrls')
        if not isinstance(resolved_urls, dict):
            return ''
        saved_url = str(resolved_urls.get(f'{scope}:{provider_key}') or '').strip()
        if not saved_url:
            return ''
        candidates = set(self._provider_url_candidates(profile, url_key, list_key))
        return saved_url if saved_url in candidates else ''

    def get_core_config(self):
        """Read core config dynamically"""
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
            'GAME_MAIN_MODEL': DEFAULT_CONVERSATION_MODEL,
            'GAME_SUMMARY_MODEL': DEFAULT_SUMMARY_MODEL,
            'CORRECTION_MODEL': DEFAULT_CORRECTION_MODEL,
            'EMOTION_MODEL': DEFAULT_EMOTION_MODEL,
            'ASSIST_API_KEY_QWEN': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_OPENAI': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_GLM': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_STEP': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_SILICON': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_GEMINI': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_KIMI': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_KIMI_CODE': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_DEEPSEEK': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_DOUBAO': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_DOUBAO_TTS': '',
            'ASSIST_API_KEY_QWEN_INTL': '',
            'ASSIST_API_KEY_MINIMAX': '',
            'ASSIST_API_KEY_MINIMAX_INTL': '',
            'ASSIST_API_KEY_MIMO': '',
            'ASSIST_API_KEY_MIMO_TOKEN_PLAN': '',
            'ASSIST_API_KEY_GROK': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_OPENROUTER': DEFAULT_CORE_API_KEY,
            'VISION_MODEL': DEFAULT_VISION_MODEL,
            'AGENT_MODEL': DEFAULT_AGENT_MODEL,
            'REALTIME_MODEL': DEFAULT_REALTIME_MODEL,
            'TTS_MODEL': DEFAULT_TTS_MODEL,
            'CONVERSATION_MODEL_URL': DEFAULT_CONVERSATION_MODEL_URL,
            'CONVERSATION_MODEL_API_KEY': DEFAULT_CONVERSATION_MODEL_API_KEY,
            'SUMMARY_MODEL_URL': DEFAULT_SUMMARY_MODEL_URL,
            'SUMMARY_MODEL_API_KEY': DEFAULT_SUMMARY_MODEL_API_KEY,
            'GAME_MAIN_MODEL_URL': DEFAULT_CONVERSATION_MODEL_URL,
            'GAME_MAIN_MODEL_API_KEY': DEFAULT_CONVERSATION_MODEL_API_KEY,
            'GAME_SUMMARY_MODEL_URL': DEFAULT_SUMMARY_MODEL_URL,
            'GAME_SUMMARY_MODEL_API_KEY': DEFAULT_SUMMARY_MODEL_API_KEY,
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
                # 模板默认 assistApi='qwen' 会把文件里从未保存过的 assistApi 填
                # 上，导致下方「core=free 时 assist 默认 free」的跟随逻辑收不到
                # 缺失信号。仅当文件显式选了 core=free 且从未保存 assistApi 时
                # 恢复跟随语义；其余缺失场景维持模板默认。
                if 'assistApi' not in file_data and file_data.get('coreApi') == 'free':
                    core_cfg['assistApi'] = 'free'
            else:
                logger.warning("core_config.json 格式异常，使用默认配置。")

        except FileNotFoundError:
            logger.info("未找到 core_config.json，使用默认配置。")
        except Exception as e:
            logger.error("Error parsing Core API Key: %s", e)
        finally:
            if not isinstance(core_cfg, dict):
                core_cfg = deepcopy(DEFAULT_CONFIG_DATA['core_config.json'])
        config['RESOLVED_PROVIDER_URLS'] = (
            dict(core_cfg.get('resolvedProviderUrls'))
            if isinstance(core_cfg.get('resolvedProviderUrls'), dict)
            else {}
        )

        # API Keys — 仅对与 coreApi/assistApi 匹配的服务商回退到 CORE_API_KEY
        if core_cfg.get('coreApiKey'):
            config['CORE_API_KEY'] = core_cfg['coreApiKey']

        _core_api_provider = core_cfg.get('coreApi') or config['CORE_API_TYPE']
        _assist_api_provider = core_cfg.get('assistApi')
        if not _assist_api_provider:
            _assist_api_provider = 'free' if _core_api_provider == 'free' else 'qwen'
        _fallback_providers = {_core_api_provider, _assist_api_provider}
        _core_key_fallback = config['CORE_API_KEY'] if config['CORE_API_KEY'] != 'free-access' else ''

        def _fb(provider: str) -> str:
            return _core_key_fallback if provider in _fallback_providers else ''

        config['ASSIST_API_KEY_QWEN'] = core_cfg.get('assistApiKeyQwen', '') or _fb('qwen')
        config['ASSIST_API_KEY_QWEN_INTL'] = core_cfg.get('assistApiKeyQwenIntl', '') or _fb('qwen_intl')
        config['ASSIST_API_KEY_OPENAI'] = core_cfg.get('assistApiKeyOpenai', '') or _fb('openai')
        config['ASSIST_API_KEY_GLM'] = core_cfg.get('assistApiKeyGlm', '') or _fb('glm')
        config['ASSIST_API_KEY_STEP'] = core_cfg.get('assistApiKeyStep', '') or _fb('step')
        config['ASSIST_API_KEY_SILICON'] = core_cfg.get('assistApiKeySilicon', '') or _fb('silicon')
        config['ASSIST_API_KEY_GEMINI'] = core_cfg.get('assistApiKeyGemini', '') or _fb('gemini')
        config['ASSIST_API_KEY_KIMI'] = core_cfg.get('assistApiKeyKimi', '') or _fb('kimi')
        config['ASSIST_API_KEY_KIMI_CODE'] = core_cfg.get('assistApiKeyKimiCode', '') or _fb('kimi_code')
        config['ASSIST_API_KEY_DEEPSEEK'] = core_cfg.get('assistApiKeyDeepseek', '') or _fb('deepseek')
        config['ASSIST_API_KEY_DOUBAO'] = core_cfg.get('assistApiKeyDoubao', '') or _fb('doubao')
        config['ASSIST_API_KEY_DOUBAO_TTS'] = core_cfg.get('assistApiKeyDoubaoTts', '')
        # MiniMax / MiMo 是 assist-only TTS provider，coreApiKey 不保证兼容；
        # 不 fallback，以免把无效 key 塞进 TTS 凭证槽位导致 401，
        # 掩盖"未配置 TTS provider key"的真实提示。
        config['ASSIST_API_KEY_MINIMAX'] = core_cfg.get('assistApiKeyMinimax', '')
        config['ASSIST_API_KEY_MINIMAX_INTL'] = core_cfg.get('assistApiKeyMinimaxIntl', '')
        config['ASSIST_API_KEY_MIMO'] = core_cfg.get('assistApiKeyMimo', '')
        config['ASSIST_API_KEY_MIMO_TOKEN_PLAN'] = core_cfg.get('assistApiKeyMimoTokenPlan', '')
        config['useMimoTokenPlan'] = _as_bool(core_cfg.get('useMimoTokenPlan', False))
        config['ASSIST_API_KEY_ELEVENLABS'] = core_cfg.get('assistApiKeyElevenlabs', '')
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
            resolved_core_url = self._get_saved_provider_url(
                core_cfg, 'core', core_api_value, core_profile, 'CORE_URL', 'CORE_URLS'
            )
            if resolved_core_url:
                config['CORE_URL'] = resolved_core_url

        # Assist API profile
        # 显式选择的 assistApi 一律被尊重，即使 coreApi=free。这样用户可以组合
        # 「免费实时（core=free）+ 付费文本/Agent（assist=qwen 等）」——免费 realtime
        # 端点和付费 assist 端点是独立的两条链路，没有理由把后者绑死在 free 上。
        # 仅当用户没有显式选 assist 时，沿用 coreApi 的偏好做默认：core=free 默认 free，
        # 其他默认 qwen。
        assist_api_value = core_cfg.get('assistApi')
        if not assist_api_value:
            assist_api_value = 'free' if core_api_value == 'free' else 'qwen'

        config['assistApi'] = assist_api_value

        assist_profile = assist_api_profiles.get(assist_api_value)
        if not assist_profile and assist_api_value != 'qwen':
            logger.warning("未知的 assistApi '%s'，回退到 qwen。", assist_api_value)
            assist_api_value = 'qwen'
            config['assistApi'] = assist_api_value
            assist_profile = assist_api_profiles.get(assist_api_value)

        if assist_profile:
            config.update(assist_profile)
            resolved_assist_url = self._get_saved_provider_url(
                core_cfg, 'assist', assist_api_value, assist_profile, 'OPENROUTER_URL', 'OPENROUTER_URLS'
            )
            if resolved_assist_url:
                config['OPENROUTER_URL'] = resolved_assist_url
        use_mimo_token_plan = (
            assist_api_value == 'mimo'
            and _as_bool(core_cfg.get('useMimoTokenPlan', False))
        )
        if use_mimo_token_plan:
            token_plan_urls = config.get('MIMO_TOKEN_PLAN_OPENROUTER_URLS')
            if not isinstance(token_plan_urls, list):
                token_plan_urls = []
            token_plan_profile = {
                'OPENROUTER_URL': config.get(
                    'MIMO_TOKEN_PLAN_OPENROUTER_URL',
                    'https://token-plan-cn.xiaomimimo.com/v1',
                ),
                'OPENROUTER_URLS': token_plan_urls or [
                    'https://token-plan-cn.xiaomimimo.com/v1',
                    'https://token-plan-sgp.xiaomimimo.com/v1',
                    'https://token-plan-ams.xiaomimimo.com/v1',
                ],
            }
            token_plan_url = self._get_saved_provider_url(
                core_cfg,
                'assist',
                'mimo_token_plan',
                token_plan_profile,
                'OPENROUTER_URL',
                'OPENROUTER_URLS',
            )
            config['OPENROUTER_URL'] = token_plan_url or token_plan_profile['OPENROUTER_URL']
        # agent api 默认跟随辅助 API 的 agent_model，缺失时回退到 VISION_MODEL
        config['AGENT_MODEL'] = config.get('AGENT_MODEL') or config.get('VISION_MODEL', '')
        config['AGENT_MODEL_URL'] = config.get('AGENT_MODEL_URL') or config.get('VISION_MODEL_URL', '') or config.get('OPENROUTER_URL', '')
        config['AGENT_MODEL_URL'] = self._normalize_agent_url(config['AGENT_MODEL_URL'])

        key_field = (
            'ASSIST_API_KEY_MIMO_TOKEN_PLAN'
            if use_mimo_token_plan
            else assist_api_key_fields.get(assist_api_value)
        )
        derived_key = ''
        if key_field:
            derived_key = config.get(key_field, '')
            if derived_key:
                config['AUDIO_API_KEY'] = derived_key
                config['OPENROUTER_API_KEY'] = derived_key

        if not config['AUDIO_API_KEY']:
            config['AUDIO_API_KEY'] = _core_key_fallback
        if not config['OPENROUTER_API_KEY']:
            config['OPENROUTER_API_KEY'] = _core_key_fallback

        # Agent API Key 回退：未显式配置时跟随辅助 API Key
        if not config.get('AGENT_MODEL_API_KEY'):
            config['AGENT_MODEL_API_KEY'] = config.get('OPENROUTER_API_KEY', '')

        # 自定义API配置映射（使用大写下划线形式的内部键，且在未提供时保留已有默认值）
        enable_custom_api = core_cfg.get('enableCustomApi', False)
        config['ENABLE_CUSTOM_API'] = enable_custom_api

        # GPT-SoVITS「是否启用」收口到 ttsModelProvider 下拉这单一真相（见
        # docs/design/tts-voice-source-unification.md §3/§4）。choke-point 在此派生，
        # 13 个下游读点（core.py 路由 / 本文件 URL 解析与 TTS_VOICE_ID override /
        # get_model_api_config 的 is_custom 自愈 / 各 router）以及 worker 的
        # _gptsovits_is_selected 全部沿用 GPTSOVITS_ENABLED 不变。
        # 派生语义：ttsModelProvider 一旦「显式选了某个 TTS provider」即唯一真相——选中
        # gptsovits 才启用、选别家（vllm_omni/mimo/custom…）就关，旧 gptsovitsEnabled 不参与。
        # 仅当未显式选择时（provider 缺失/空串，或 follow_assist/follow_core 这两个「跟随
        # assist/core」哨兵——它们是各 model provider 下拉的默认值，等同未选）才回落旧开关，
        # 兜住 pre-#1830 存量（用 checkbox 开 GSV、下拉仍停在默认 follow_* 的用户）。
        # ⚠️ 不能把 follow_* 当显式 provider，否则存量 GSV 用户（gptsovitsEnabled=true +
        # ttsModelProvider='follow_assist'）会被误判为关 → 升级后 GSV 失效（Codex PR#1850 P1）。
        # 刻意不用 `旧flag OR 下拉` 的纯 OR：前端已退役 gptsovitsEnabled 写路径，旧 flag 在
        # 增量合并下会粘住；显式切走由「下拉为准」关掉，follow_* 切走由 config_router 的
        # save choke point 惰性落 False 清掉（见该处注释）。
        _tts_model_provider = str(core_cfg.get('ttsModelProvider', '') or '').strip()
        if _tts_model_provider in ('', 'follow_assist', 'follow_core'):
            config['GPTSOVITS_ENABLED'] = _as_bool(core_cfg.get('gptsovitsEnabled', False))
        else:
            config['GPTSOVITS_ENABLED'] = (_tts_model_provider == 'gptsovits')

        config['ELEVENLABS_API_KEY'] = core_cfg.get('assistApiKeyElevenlabs', '')
        config['TTS_PROVIDER'] = core_cfg.get('ttsProvider', '')

        # 将 vLLM-Omni TTS 的前端原始字段放进 core_config snapshot，供
        # core.py 判断是否启用外部 TTS，并生成与实际 worker 参数一致的复用 key。
        # 凭证字段 ttsModelApiKey 不放入 snapshot；它仍由 tts_client.py 从持久化
        # 配置读取，避免扩大通用配置快照中的敏感字段范围。
        for _model_provider_prefix in (
            'conversation', 'summary', 'gameMain', 'gameSummary', 'correction',
            'emotion', 'vision', 'agent', 'omni', 'tts',
        ):
            config[f'{_model_provider_prefix}ModelProvider'] = str(
                core_cfg.get(f'{_model_provider_prefix}ModelProvider', '') or ''
            )
        config['ttsModelUrl'] = str(core_cfg.get('ttsModelUrl', '') or '')
        config['ttsModelId'] = str(core_cfg.get('ttsModelId', '') or '')
        config['ttsVoiceId'] = str(core_cfg.get('ttsVoiceId', '') or '')

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
        
        # GPT-SoVITS 是本地 TTS 运行时，不依赖 enableCustomApi 总开关。用户
        # 保存的 ttsModelUrl 是 GSV server URL，不能被 follow_core/follow_assist
        # 的 LLM URL 覆盖；空值只在运行时默认到 127.0.0.1，不写回配置文件。
        if config['GPTSOVITS_ENABLED']:
            config['TTS_MODEL_URL'] = normalize_gsv_api_url(core_cfg.get('ttsModelUrl'))

        # 只有在启用自定义API时才允许覆盖各模型相关字段
        if enable_custom_api:
            # URL / Model ID 字段：空值回退到已有配置。
            # API Key 字段：根据用户选择的 provider 决定是否覆盖：
            #   - follow_core / follow_assist / ''（老配置无此字段）→ 保留上方派生的值
            #   - 具体服务商或 'custom' → 允许覆盖（空串合法，本地服务商可能不需要 key）
            def _resolve_follow_model_url(prefix: str, provider: str) -> str:
                """Recompute follow_* URLs for the current provider, avoiding stale regions saved historically."""
                if provider == 'follow_assist':
                    return config.get('OPENROUTER_URL', '')
                if provider == 'follow_conversation':
                    return config.get('CONVERSATION_MODEL_URL', '') or config.get('OPENROUTER_URL', '')
                if provider == 'follow_summary':
                    return config.get('SUMMARY_MODEL_URL', '') or config.get('OPENROUTER_URL', '')
                if provider != 'follow_core':
                    return ''

                if prefix == 'omni':
                    return config.get('CORE_URL', '')

                follow_core_profile = assist_api_profiles.get(core_api_value)
                if isinstance(follow_core_profile, dict):
                    resolved_url = self._get_saved_provider_url(
                        core_cfg,
                        'assist',
                        core_api_value,
                        follow_core_profile,
                        'OPENROUTER_URL',
                        'OPENROUTER_URLS',
                    )
                    return resolved_url or follow_core_profile.get('OPENROUTER_URL', '')

                if isinstance(core_profile, dict):
                    resolved_url = self._get_saved_provider_url(
                        core_cfg,
                        'core',
                        core_api_value,
                        core_profile,
                        'CORE_URL',
                        'CORE_URLS',
                    )
                    return resolved_url or core_profile.get('CORE_URL', '')
                return ''

            def _resolve_game_follow_model_id(prefix: str, provider: str) -> str:
                if prefix not in ('gameMain', 'gameSummary'):
                    return ''
                if provider == 'follow_core':
                    follow_core_profile = assist_api_profiles.get(core_api_value)
                    if isinstance(follow_core_profile, dict):
                        if prefix == 'gameSummary':
                            return follow_core_profile.get('SUMMARY_MODEL', '') or config.get('SUMMARY_MODEL', '')
                        return follow_core_profile.get('CONVERSATION_MODEL', '') or config.get('CONVERSATION_MODEL', '')
                    return config.get('CORE_MODEL', '')
                if provider != 'follow_assist':
                    return ''
                if prefix == 'gameSummary':
                    return config.get('SUMMARY_MODEL', '')
                return config.get('CONVERSATION_MODEL', '')

            _custom_api_fields = [
                # (前端字段前缀, 模型config键, URL config键, API Key config键)
                ('conversation', 'CONVERSATION_MODEL', 'CONVERSATION_MODEL_URL', 'CONVERSATION_MODEL_API_KEY'),
                ('summary',      'SUMMARY_MODEL',      'SUMMARY_MODEL_URL',      'SUMMARY_MODEL_API_KEY'),
                ('gameMain',     'GAME_MAIN_MODEL',    'GAME_MAIN_MODEL_URL',    'GAME_MAIN_MODEL_API_KEY'),
                ('gameSummary',  'GAME_SUMMARY_MODEL', 'GAME_SUMMARY_MODEL_URL', 'GAME_SUMMARY_MODEL_API_KEY'),
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
                # 注：follow_* 下 omni/tts 仍不能依赖 get_model_api_config fallback
                # 读取用户填的 modelId（fallback 用 CORE_MODEL，不是 REALTIME_MODEL/TTS_MODEL），
                # 因此这些特殊前缀继续走既有 follow 解析。
                is_follow = provider in ('follow_core', 'follow_assist', 'follow_conversation', 'follow_summary')
                # GSV 启用时 ttsModelUrl 是 GPT-SoVITS server URL，不是 follow_*
                # 联动出来的 LLM URL。即便 ttsModelProvider 仍是默认 follow_assist，
                # 也必须优先保留 GSV URL，否则对话 TTS 会连到辅助 LLM endpoint。
                gsv_enabled_for_url = config['GPTSOVITS_ENABLED']
                gsv_tts_url_override = prefix == 'tts' and gsv_enabled_for_url
                skip_url_for_follow = (
                    is_follow
                    and prefix in ('omni', 'tts')
                    and not gsv_tts_url_override
                )

                # URL: 空值回退到已有配置；omni/tts follow_* 时跳过
                cfg_url = core_cfg.get(f'{prefix}ModelUrl')
                if gsv_tts_url_override:
                    config[url_key] = normalize_gsv_api_url(cfg_url or config.get(url_key))
                elif not skip_url_for_follow:
                    if is_follow:
                        followed_url = _resolve_follow_model_url(prefix, provider)
                        if followed_url:
                            config[url_key] = followed_url
                    else:
                        if cfg_url is not None:
                            config[url_key] = cfg_url or config.get(url_key, '')

                # Model ID: 空值回退到已有配置
                cfg_model = core_cfg.get(f'{prefix}ModelId')
                if provider == 'follow_conversation':
                    config[model_key] = config.get('CONVERSATION_MODEL', '')
                elif provider == 'follow_summary':
                    config[model_key] = config.get('SUMMARY_MODEL', '')
                elif provider in ('follow_core', 'follow_assist'):
                    uses_fixed_free_assist_model = provider == 'follow_assist' and assist_api_value == 'free'
                    if (
                        not uses_fixed_free_assist_model
                        and
                        prefix not in ('gameMain', 'gameSummary', 'omni', 'tts')
                        and isinstance(cfg_model, str)
                        and cfg_model.strip()
                    ):
                        config[model_key] = cfg_model.strip()
                    else:
                        followed_model = _resolve_game_follow_model_id(prefix, provider)
                        if followed_model:
                            config[model_key] = followed_model
                elif cfg_model is not None:
                    config[model_key] = cfg_model or config.get(model_key, '')

                # API Key 处理：
                #   follow_core   → 从核心 API Key 派生
                #   follow_assist → 从辅助 API Key 派生（OPENROUTER_API_KEY 已含 assist→core 回退）
                #   具体服务商/custom/''(老配置) → 使用存储值（空串合法，本地服务商不需要 key）
                #
                # GSV 启用 + prefix='tts' + ttsModelProvider 默认 'follow_*' 时跳过派生：
                # 派生会把 TTS_MODEL_API_KEY 写成 OPENROUTER_API_KEY / CORE_API_KEY（这俩是
                # LLM key，可能是 Gemini / DeepSeek 等），随后 get_model_api_config('tts_custom')
                # 的 is_gsv_url 分支会原样返回这个无关 key；get_tts_api_key('cosyvoice') 因此
                # 拿到错的 key，CosyVoice clone 鉴权失败。跳过后 TTS_MODEL_API_KEY 保留其持久化
                # 值（用户开 GSV 一般不会同时填这个字段，留空即可），让下游 is_gsv_url 分支的
                # ASSIST_API_KEY_QWEN fallback 接手。
                skip_key_for_follow_gsv = (
                    is_follow
                    and prefix == 'tts'
                    and gsv_enabled_for_url
                )
                if provider == 'follow_core':
                    if not skip_key_for_follow_gsv:
                        config[apikey_key] = config.get('CORE_API_KEY', '')
                elif provider == 'follow_assist':
                    if not skip_key_for_follow_gsv:
                        config[apikey_key] = config.get('OPENROUTER_API_KEY', '')
                elif provider == 'follow_conversation':
                    config[apikey_key] = config.get('CONVERSATION_MODEL_API_KEY', '')
                elif provider == 'follow_summary':
                    config[apikey_key] = config.get('SUMMARY_MODEL_API_KEY', '')
                else:
                    cfg_key = core_cfg.get(f'{prefix}ModelApiKey')
                    if cfg_key is not None:
                        config[apikey_key] = cfg_key

            # TTS Voice ID 作为角色 voice_id 的回退
            if core_cfg.get('ttsVoiceId') is not None:
                config['TTS_VOICE_ID'] = core_cfg.get('ttsVoiceId', '')

        if config['GPTSOVITS_ENABLED'] and core_cfg.get('ttsVoiceId') is not None:
            config['TTS_VOICE_ID'] = core_cfg.get('ttsVoiceId', '')

        for key, value in config.items():
            if key.endswith('_URL') and isinstance(value, str):
                config[key] = self._adjust_free_api_url(value, True)

        # Agent model always uses international API regardless of region
        if isinstance(config.get('AGENT_MODEL_URL'), str):
            config['AGENT_MODEL_URL'] = self._normalize_agent_url(config['AGENT_MODEL_URL'])

        return config

    def get_model_api_config(self, model_type: str) -> dict:
        """
        Get the API config for the given model type (automatically handling custom API priority)
        
        Args:
            model_type: model type, one of:
                - 'summary': summary model (falls back to assist API)
                - 'correction': correction model (falls back to assist API)
                - 'emotion': emotion analysis model (falls back to assist API)
                - 'vision': vision model (falls back to assist API)
                - 'realtime': realtime speech model (falls back to core API)
                - 'tts_default': default TTS (falls back to core API, used by OmniOfflineClient)
                - 'tts_custom': custom TTS (falls back to assist API, used for voice_id scenarios)
                
        Returns:
            dict: config containing:
                - 'model': model name
                - 'api_key': API key
                - 'base_url': API endpoint URL
                - 'is_custom': whether a custom API config is used
        """
        core_config = self.get_core_config()
        enable_custom_api = core_config.get('ENABLE_CUSTOM_API', False)

        # GPT-SoVITS 启用时，tts_custom slot 视为自定义 API：UI 上勾 GSV 在产品语义上
        # 就是 "启用一个自定义 TTS"，但前端 (api_key_settings.js) 并不会顺手把
        # ENABLE_CUSTOM_API 也勾上。后端这里自愈，避免 "勾了 GSV 但没勾 ENABLE_CUSTOM_API"
        # 这条用户极易踩中的路径让 is_custom=False、整条 GSV 链路（dispatcher /
        # check_custom_tts_voice_allowed / /custom_tts_voices）全部失效。
        # 仅扩到 tts_custom，不影响其他 slot 的开关行为。
        gsv_enabled_for_tts = (
            model_type == 'tts_custom'
            and core_config.get('GPTSOVITS_ENABLED', False)
        )
        treat_as_custom = enable_custom_api or gsv_enabled_for_tts

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
            'game_main': {
                'custom_model': 'GAME_MAIN_MODEL',
                'custom_url': 'GAME_MAIN_MODEL_URL',
                'custom_key': 'GAME_MAIN_MODEL_API_KEY',
                'default_model': 'GAME_MAIN_MODEL',
                'fallback_type': 'conversation',
            },
            'game_summary': {
                'custom_model': 'GAME_SUMMARY_MODEL',
                'custom_url': 'GAME_SUMMARY_MODEL_URL',
                'custom_key': 'GAME_SUMMARY_MODEL_API_KEY',
                'default_model': 'GAME_SUMMARY_MODEL',
                'fallback_type': 'summary',
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

        def _normalize_provider_type_value(value: object) -> str:
            provider_type = str(value or 'openai_compatible').strip().lower()
            if provider_type not in ('openai_compatible', 'anthropic', 'websocket'):
                return 'openai_compatible'
            return provider_type

        def _provider_type_from_assist_key(provider_key: str) -> str:
            profile = get_assist_api_profiles().get(str(provider_key or '').strip(), {})
            if isinstance(profile, dict):
                return _normalize_provider_type_value(profile.get('PROVIDER_TYPE'))
            return 'openai_compatible'

        def _provider_type_from_core_key(provider_key: str) -> str:
            profile = get_core_api_profiles().get(str(provider_key or '').strip(), {})
            if isinstance(profile, dict):
                return _normalize_provider_type_value(profile.get('PROVIDER_TYPE'))
            return _provider_type_from_assist_key(provider_key)

        def _resolved_provider_type_for_model(target_model_type: str, _seen: frozenset[str] = frozenset()) -> str:
            if target_model_type in _seen:
                return _normalize_provider_type_value(core_config.get('PROVIDER_TYPE'))
            seen = _seen | frozenset((target_model_type,))
            prefix_by_type = {
                'conversation': 'conversation',
                'summary': 'summary',
                'game_main': 'gameMain',
                'game_summary': 'gameSummary',
                'correction': 'correction',
                'emotion': 'emotion',
                'vision': 'vision',
                'agent': 'agent',
                'realtime': 'omni',
                'tts_default': 'tts',
                'tts_custom': 'tts',
            }
            prefix = prefix_by_type.get(target_model_type, target_model_type)
            provider = str(core_config.get(f'{prefix}ModelProvider') or '').strip()
            if provider == 'custom':
                return 'openai_compatible'
            if provider == 'follow_conversation':
                if target_model_type == 'conversation':
                    return _normalize_provider_type_value(core_config.get('PROVIDER_TYPE'))
                return _resolved_provider_type_for_model('conversation', seen)
            if provider == 'follow_summary':
                if target_model_type == 'summary':
                    return _normalize_provider_type_value(core_config.get('PROVIDER_TYPE'))
                return _resolved_provider_type_for_model('summary', seen)
            if provider == 'follow_core':
                return _provider_type_from_core_key(core_config.get('CORE_API_TYPE', ''))
            if provider == 'follow_assist':
                return _normalize_provider_type_value(core_config.get('PROVIDER_TYPE'))
            if not provider:
                fallback_type = model_type_mapping.get(target_model_type, {}).get('fallback_type')
                if fallback_type == 'core':
                    return _provider_type_from_core_key(core_config.get('CORE_API_TYPE', ''))
                if fallback_type == 'conversation':
                    return _resolved_provider_type_for_model('conversation', seen)
                if fallback_type == 'summary':
                    return _resolved_provider_type_for_model('summary', seen)
                return _normalize_provider_type_value(core_config.get('PROVIDER_TYPE'))
            return _provider_type_from_assist_key(provider)

        if model_type == 'game_main':
            provider = str(core_config.get('gameMainModelProvider') or 'follow_conversation').strip()
            if not treat_as_custom or provider == 'follow_conversation':
                return self.get_model_api_config('conversation')
        elif model_type == 'game_summary':
            provider = str(core_config.get('gameSummaryModelProvider') or 'follow_summary').strip()
            if not treat_as_custom or provider == 'follow_summary':
                return self.get_model_api_config('summary')
        
        # agent 始终走专用字段（AGENT_MODEL_URL 有 lanlan.app 归一化），
        # 但 is_custom 仅在 enableCustomApi 开启时为 True。
        if treat_as_custom or model_type == 'agent':
            custom_model = core_config.get(mapping['custom_model'], '')
            custom_url = core_config.get(mapping['custom_url'], '')
            custom_key = core_config.get(mapping['custom_key'], '')

            # GSV 模式下 voice_id 即定位（无 model 概念），URL 即可视为已配置；
            # 不放宽到全部 tts_custom 场景，避免改变 cosyvoice 用户原有的 fallthrough 行为。
            is_gsv_url = (
                gsv_enabled_for_tts
                and custom_url.startswith(('http://', 'https://'))
            )

            # 自定义配置完整时使用自定义配置
            if (custom_model and custom_url) or is_gsv_url:
                resolved_api_key = custom_key
                # 仅勾选 GSV、未填 TTS_MODEL_API_KEY 时，tts_custom slot 仍会被
                # CosyVoice clone 路径复用 (register_voice → get_tts_api_key('cosyvoice')
                # → 这里取 api_key)。直接返回空 key 会让 CosyVoice 报
                # TTS_AUDIO_API_KEY_MISSING，回退到 ASSIST_API_KEY_QWEN 才能保住用户
                # 在 GSV 开启前就在用的 CosyVoice 克隆能力。
                if is_gsv_url and not resolved_api_key and model_type == 'tts_custom':
                    resolved_api_key = (core_config.get('ASSIST_API_KEY_QWEN') or '').strip()
                return {
                    'model': custom_model,
                    'api_key': resolved_api_key,
                    'base_url': custom_url,
                    'is_custom': treat_as_custom,
                    'provider_type': _resolved_provider_type_for_model(model_type),
                    # 对于 realtime 模型，自定义配置时 api_type 设为 'local'
                    # TODO: 后续完善 'local' 类型的具体实现（如本地推理服务等）
                    'api_type': 'local' if model_type == 'realtime' else None,
                }
        
        # 自定义音色(CosyVoice)的特殊回退逻辑：优先尝试用户保存的 Qwen Cosyvoice API，
        # 只有在缺少 Qwen Cosyvoice API 时才再回退到辅助 API（CosyVoice 目前是唯一支持 voice clone 的）
        if model_type == 'tts_custom':
            active_assist = str(core_config.get('assistApi') or '').strip()
            qwen_candidates = []
            if active_assist in ('qwen', 'qwen_intl'):
                qwen_candidates.append(active_assist)
            qwen_candidates.extend(['qwen', 'qwen_intl'])

            seen_qwen = set()
            for qwen_provider in qwen_candidates:
                if qwen_provider in seen_qwen:
                    continue
                seen_qwen.add(qwen_provider)
                key_field = 'ASSIST_API_KEY_QWEN_INTL' if qwen_provider == 'qwen_intl' else 'ASSIST_API_KEY_QWEN'
                qwen_api_key = (core_config.get(key_field) or '').strip()
                if not qwen_api_key:
                    continue
                if qwen_provider == active_assist:
                    base_url = core_config.get('OPENROUTER_URL', '')
                else:
                    qwen_profile = get_assist_api_profiles().get(qwen_provider, {})
                    resolved_urls = core_config.get('RESOLVED_PROVIDER_URLS')
                    resolved_core_cfg = {
                        'resolvedProviderUrls': resolved_urls if isinstance(resolved_urls, dict) else {},
                    }
                    base_url = (
                        self._get_saved_provider_url(
                            resolved_core_cfg,
                            'assist',
                            qwen_provider,
                            qwen_profile,
                            'OPENROUTER_URL',
                            'OPENROUTER_URLS',
                        )
                        or qwen_profile.get('OPENROUTER_URL', core_config.get('OPENROUTER_URL', ''))
                    )
                return {
                    'model': core_config.get(mapping['default_model'], ''), # 占位值，下游会覆盖成实际模型
                    'api_key': qwen_api_key,
                    'base_url': base_url,
                    'is_custom': False,
                    'provider_type': _provider_type_from_assist_key(qwen_provider),
                }

        # 根据 fallback_type 回退到不同的 API
        if mapping['fallback_type'] == 'core':
            # 回退到核心 API 配置
            return {
                'model': core_config.get(mapping['default_model'], ''),
                'api_key': core_config.get('CORE_API_KEY', ''),
                'base_url': core_config.get('CORE_URL', ''),
                'is_custom': False,
                'provider_type': _resolved_provider_type_for_model(model_type),
                # 对于 realtime 模型，回退到核心API时使用配置的 CORE_API_TYPE
                'api_type': core_config.get('CORE_API_TYPE', '') if model_type == 'realtime' else None,
            }
        elif mapping['fallback_type'] == 'conversation':
            return self.get_model_api_config('conversation')
        elif mapping['fallback_type'] == 'summary':
            return self.get_model_api_config('summary')
        else:
            # 回退到辅助 API 配置
            return {
                'model': core_config.get(mapping['default_model'], ''),
                'api_key': core_config.get('OPENROUTER_API_KEY', ''),
                'base_url': core_config.get('OPENROUTER_URL', ''),
                'is_custom': False,
                'provider_type': _resolved_provider_type_for_model(model_type),
            }

    def is_agent_api_ready(self) -> tuple[bool, list[str]]:
        """
        Agent mode readiness check:
        - a usable AGENT_MODEL (model/url/api_key) is required
        - whether it is free (quota counting / frontend hints) is decided separately by
          is_agent_free() and is unrelated to this check: readiness only cares whether the
          model/url/key trio is filled in and a request can be made. free-access is a valid
          placeholder token for the truly free agent and should pass the gate; dirty configs
          (placeholder key against a self-paid endpoint) are caught downstream by 401, not here.
        """
        reasons = []
        agent_api = self.get_model_api_config('agent')
        if not (agent_api.get('model') or '').strip():
            reasons.append("Agent 模型未配置")
        if not (agent_api.get('base_url') or '').strip():
            reasons.append("Agent API URL 未配置")
        api_key = (agent_api.get('api_key') or '').strip()
        if not api_key:
            reasons.append("Agent API Key 未配置或不可用")
        return len(reasons) == 0, reasons

    def is_agent_free(self) -> bool:
        """Whether the Agent actually in use is the built-in free Agent model (free-agent-model).

        The single source of truth for "is the agent free" — quota counting and the frontend
        "free model may be congested" hint both read it. Dual of is_free_voice() (the
        voice/core dimension): even when using the free voice (core=free), if the agent is
        switched to a self-paid/custom model, this returns False.
        """
        agent_model = (self.get_model_api_config('agent').get('model') or '').strip()
        return agent_model == self._free_agent_model_name

    def is_free_voice(self) -> bool:
        """Whether the built-in free voice is in use (core=free). The single source of truth for
        "is voice free" — free preset voices, hidden main cloud voices and the default YUI
        fallback all read it. Dual of is_agent_free().

        Realtime and text TTS share the same voice and follow core, independent of assist:
        hide_cloud_main hides the main CosyVoice/Qwen cloud bucket in free mode. Provider-keyed
        clone buckets are handled separately by get_voices_for_current_api(), and remain visible
        when the corresponding CosyVoice clone API key is configured.
        """
        return (self.get_core_config().get('CORE_API_TYPE') or '') == 'free'

    def _get_agent_quota_path(self) -> Path:
        """Local Agent trial quota counter file path."""
        return self.config_dir / "agent_quota.json"

    @classmethod
    def register_quota_exceeded_notifier(cls, notifier) -> None:
        """Register the "free Agent quota exhausted" notification callback (process-level, registered by agent_server at startup).

        notifier(used:int, limit:int) is invoked when the quota is exhausted, **at most once
        every 10 seconds** (see the throttling in ``_maybe_notify_quota_exceeded``). The
        callback itself must be non-blocking — it is invoked inside the critical section
        holding ``_agent_quota_lock`` and should normally just do one cross-thread schedule.
        """
        cls._quota_exceeded_notifier = notifier

    def _maybe_notify_quota_exceeded(self, used: int, limit: int) -> None:
        """On quota exhaustion, fire the registered frontend notification callback with throttling (at most once per _quota_notify_interval_s seconds)."""
        notifier = ConfigManager._quota_exceeded_notifier
        if notifier is None:
            return
        now = time.monotonic()
        with ConfigManager._quota_notify_lock:
            last = ConfigManager._quota_notify_last_monotonic
            if last and (now - last) < ConfigManager._quota_notify_interval_s:
                return
            ConfigManager._quota_notify_last_monotonic = now
        try:
            notifier(used, limit)
        except Exception as e:
            logger.debug("配额耗尽通知回调失败: %s", e)

    def consume_agent_daily_quota(self, source: str = "", units: int = 1) -> tuple[bool, dict]:
        """Consume the Agent model daily quota (only effective when the actual Agent model is free-agent-model). The quota is not enforced locally alone; local counting just reduces useless requests and saves network bandwidth.

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

        # 只对真正的免费 Agent 模型(free-agent-model)本地计数：用户换用自费/自定义 agent
        # model 后不该再被这条免费试用配额挡。判定收口在 is_agent_free()。analyzer/deduper
        # 这类判定器走的是 summary/emotion 模型而非 agent model，已不再调用本函数。
        is_metered = self.is_agent_free()
        today = date.today().isoformat()
        limit = int(self._free_agent_daily_limit)

        if not is_metered:
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
                # 配额耗尽：节流通知前端弹提示（最多每 10 秒一次）。回调非阻塞，
                # 在临界区里只做一次跨线程 schedule，不展开网络 IO。
                self._maybe_notify_quota_exceeded(used, limit)
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

        The sync version must not run directly on the event loop (open+fsync blocks).
        """
        return await asyncio.to_thread(self.consume_agent_daily_quota, source, units)

    def load_json_config(self, filename, default_value=None):
        """
        Load a JSON config file
        
        Args:
            filename: config file name
            default_value: default value (when the file does not exist)
            
        Returns:
            dict: config content
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
        Save a JSON config file
        
        Args:
            filename: config file name
            data: data to save
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
        Get a memory file path
        
        Priority:
        1. Documents/{APP_NAME}/memory/
        2. project directory/memory/store/
        
        Args:
            filename: memory file name
            
        Returns:
            Path: memory file path
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
        """Get config directory info"""
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
        Get the workshop config file path
        
        Returns:
            str: absolute path of the workshop config file
        """
        return str(self.get_config_path('workshop_config.json'))

    def _normalize_workshop_folder_path(self, folder_path):
        """Normalize a workshop directory path; returns None on failure."""
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
        Check and clean up invalid workshop config files.

        Rule: if any path field present in the config is not a valid directory, delete the whole config file.
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
        """Check workshop configs in both the documents and project directories and clean up invalid files."""
        candidates = (
            self.config_dir / "workshop_config.json",
            self.project_config_dir / "workshop_config.json",
        )
        for candidate in candidates:
            self._cleanup_invalid_workshop_config_file(candidate)

    def repair_workshop_configs(self):
        """Explicitly repair the workshop config file; runs only when the caller explicitly allows writing to disk."""
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
        Load workshop config
        
        Returns:
            dict: workshop config data
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
        Save workshop config
        
        Args:
            config_data: config data to save
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
        Set the Steam Workshop root directory path (runtime variable, not written to the config file)
        
        Args:
            workshop_path: Steam Workshop root directory path
        """
        self._steam_workshop_path = workshop_path
        logger.info(f"已设置Steam创意工坊路径（运行时）: {workshop_path}")

    def persist_user_workshop_folder(self, workshop_path):
        """
        Persist the actual Steam Workshop path into the config file (written only once per startup).

        Called only when the Steam Workshop location was obtained dynamically; later reads can serve as a fallback when Steam is not running.
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
        Get the Steam Workshop root directory path (runtime only, set by the startup flow)
        
        Returns:
            str | None: Steam Workshop root directory path
        """
        return self._steam_workshop_path
    
    def get_workshop_path(self):
        """
        Get the workshop root directory path
        
        Priority: user_mod_folder (config) > Steam runtime path > user_workshop_folder (cache file) > default_workshop_folder (config) > self.workshop_dir
        
        Returns:
            str: workshop root directory path
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
