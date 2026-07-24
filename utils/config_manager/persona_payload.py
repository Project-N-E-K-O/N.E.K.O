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

"""Persona payload synthesis and YUI/live2d legacy helpers.

Builds the effective character payload (persona overrides, synthetic AI
context fields, prompt guidance) and hosts the default-YUI free-voice
binding helpers. Stateless module-level functions.
"""
import re
from copy import deepcopy

from config.prompts.prompts_chara import get_lanlan_prompt, is_default_prompt
from utils.tts.native_voice_registry import is_free_lanlan_app_route
from utils.persona_presets import PERSONA_OVERRIDE_FIELDS
from utils.voice_config import read_legacy_voice_id

from ._shared import logger
from .reserved_schema import get_reserved, set_reserved


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
