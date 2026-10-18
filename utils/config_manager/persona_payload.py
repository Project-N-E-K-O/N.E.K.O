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
from utils.persona_presets import PERSONA_OVERRIDE_FIELDS
from utils.voice_config import read_legacy_voice_id

from ._shared import logger
from .reserved_schema import get_reserved, set_reserved


DEFAULT_YUI_LIVE2D_MODEL_PATH = "yui-lolita/yui-lolita.model3.json"

# After the default skin changed from yui-origin to yui-lolita, existing YUI cards
# still contained yui-origin. Both built-in models count as original YUI cards;
# otherwise existing users lose the free YUI voice fallback when the skin changes (see ensure_default_yui_voice_for_free_api).
_BUILTIN_YUI_LIVE2D_MODEL_NAMES = ("yui-lolita", "yui-origin")
BUILTIN_YUI_LIVE2D_MODEL_PATHS = frozenset(
    f"{name}/{name}.model3.json" for name in _BUILTIN_YUI_LIVE2D_MODEL_NAMES
)


def _normalize_live2d_model_path(value) -> str:
    model_path = str(value or "").strip().replace("\\", "/").lower()
    if model_path in _BUILTIN_YUI_LIVE2D_MODEL_NAMES:
        return f"{model_path}/{model_path}.model3.json"
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
    return _normalize_live2d_model_path(model_path) in BUILTIN_YUI_LIVE2D_MODEL_PATHS


# Former free YUI preset voice IDs historically used by free_voices["yui_cn"].
# These remain in existing users' characters.json but are no longer on the free_voices allowlist.
# cleanup_invalid_voice_ids would clear them as invalid, sending an empty voice to the free/step
# provider's default_voice (qingchunshaonv). Free users who always used the default YUI
# and never manually selected a voice would silently fall back to a generic female voice after an ID change.
# Remap them to the current yui_cn before cleanup checks validity. On future YUI voice changes,
# append the replaced values to this set.
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
    # The free preset YUI voice is available only with core=free, independently of assist.
    if (core_cfg.get("coreApi") or core_cfg.get("CORE_API_TYPE")) != "free":
        return False

    # Read the assembled snapshot once (offloaded aget). Reuse it for the provisional guard,
    # free-route recheck, and overseas decision, ensuring one snapshot per decision and a pure-memory
    # provisional predicate (its own configuration read uses synchronous open()+json.load(); all callers run on the event loop).
    try:
        assembled_cfg = await config_manager.aget_core_config() or {}
    except Exception:
        logger.debug("[GeoIP] 读取组装后的核心配置失败，跳过本轮默认音色绑定", exc_info=True)
        return False

    # Do not bind before the region settles: mainland uses yui_cn, overseas uses literal "yui"; they are not interchangeable.
    # Once this function writes a nonempty voice_id, it never overwrites it, so even a later authoritative
    # result cannot correct a wrong guess. Binding on the next settled call costs only one round.
    try:
        if config_manager._region_verdict_is_provisional(assembled_cfg):
            logger.info("[GeoIP] 区域判定尚未落定，暂不绑定默认 YUI 音色，等落定后再绑")
            return False
    except Exception:
        logger.debug("[GeoIP] 区域落定状态判断失败，跳过本轮默认音色绑定", exc_info=True)
        return False

    # The assembled snapshot is authoritative: after the caller's snapshot passes the free gate above,
    # the user may switch coreApi from free to paid during this await. Without rechecking, overseas=False
    # could bind the free-only yui_cn to paid configuration (a nonempty value is never corrected here).
    if (assembled_cfg.get("coreApi") or assembled_cfg.get("CORE_API_TYPE")) != "free":
        return False

    # Overseas free (free + *.lanlan.app): the default is branded yui (free_intl default_voice),
    # sent as literal "yui". Mainland free (lanlan.tech) still binds the language-specific YUI voice from free_voices.
    #
    # Ask the authoritative region source directly; **do not infer from the URL**. URL inference was the old
    # bug: the callers pass snapshots with different semantics. Save passes freshly persisted raw config
    # (CORE_URL still lanlan.tech), while session passes an earlier assembled, rewritten snapshot. The latter is dangerous:
    # a provisional Steam overseas decision already rewrites it to .app, but authoritative IP may later report mainland.
    # URL inference would permanently store "yui" in a mainland character card (nonempty voice_id is never overwritten),
    # and the free/free_intl catalogs do not overlap. The requirement that yui use overseas routing and mainland voices
    # use mainland routing must hold. _check_non_mainland gives both callers the same conclusion.
    #
    # The needs_region gate serves two purposes:
    #  - Livestream users with all free endpoints derived already have a fixed route and need no regional decision.
    #    _check_non_mainland internally calls _ensure_ip_probe_started; binding a voice must not send their
    #    IP to ip-api.com (invariant #2). Binding a mainland free voice is correct:
    #    the derived routes are equivalent to the lanlan.tech path structure.
    #  - When the gate passes, the provisional guard above guarantees _region_cache has settled, so this
    #    _check_non_mainland call only reads cache and starts no actual probe.
    #
    # Use the classmethod rather than config_manager instance: this is a pure predicate; instance lookup
    # would needlessly require every caller (including test doubles) to implement it.
    from utils.config_manager import ConfigManager

    # Evaluate needs_region against the **assembled** configuration (assembled_cfg read at entry),
    # not the caller's snapshot. The save path passes persisted raw configuration with selection fields
    # such as coreApi / assistApi, but no *_URL fields (get_core_config fills URLs when assembling
    # a profile). Testing raw config always returns False, skipping region resolution and permanently
    # binding **all** overseas free users to mainland yui_cn, precisely the failure this function must prevent.
    # The provisional guard uses the same assembled configuration, keeping both decisions consistent.
    overseas = False
    if ConfigManager._config_needs_region(assembled_cfg, ConfigManager._REGION_HOSTS_ADJUSTED):
        try:
            overseas = bool(config_manager._check_non_mainland())
        except Exception:
            overseas = False

    yui_voice_id = "yui" if overseas else _get_default_yui_free_voice_id()
    if not yui_voice_id:
        return False

    # Read character data **last**: each await between load and save enlarges the overlap with concurrent
    # character edits (this function performs whole-file load→save, last-writer-wins). Load only after
    # region/configuration decisions settle; mutate in memory and save immediately to minimize the window.
    # Eliminating the race requires a shared atomic update primitive for all character writes;
    # this affects every save_characters call and cannot be fixed solely here.
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


def _build_effective_character_payload(
    character_payload: dict,
    entity: str = "neko",
    lang: str | None = None,
) -> dict:
    if not isinstance(character_payload, dict):
        return {}

    effective_payload = deepcopy(character_payload)
    override = _get_persona_override(character_payload)
    if not isinstance(override, dict):
        for field, value in _build_ai_context_fields(
            character_payload,
            existing_fields=set(effective_payload.keys()),
            entity=entity,
            lang=lang,
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
        lang=lang,
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
    if override.get("source") == "chat_sync":
        # Synced static guidance is the reviewed portable version. A same-named
        # preset installed on this device may contain a different style/version.
        guidance = str(override.get("prompt_guidance") or "").strip()
        from_preset = bool(preset_id and guidance)
    elif preset_id:
        # Resolve using the current global language at runtime so the persona prompt follows language changes,
        # like the base LANLAN prompt. Fall back to the stored string only if code removed preset_id.
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

    # A preset's guidance is a **complete standalone** persona prompt whose skeleton (fictional-character
    # introduction + <Context Awareness> + <WARNING> + <IMPORTANT>) exactly matches the default base.
    # Appending directly repeats the whole skeleton (~1500 characters), and lanlan_prompt_map sends it to
    # **every** consumer: main-chat system prompt, proactive messages, break reminders, plugins, etc.
    # If base is still the default prompt, the preset already supplies the complete persona: replace base to avoid duplication.
    # Append only when the user wrote a nondefault custom system_prompt, preserving their content.
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


def _resolve_profile_rename_language(explicit: str | None) -> str | None:
    """Pick the language for a rename sentence that may be stored in persona.

    An explicit locale wins over ``get_global_language_full()``. The global
    value follows ``language_context`` and otherwise falls back to the process
    language, which on Windows/Steam is often English. Character-card sync
    persists whatever sentence it renders, so that fallback rewrites a saved
    Chinese rename fact into English as soon as the temporary context ends.
    """
    from utils.language_utils import (
        get_global_language_full,
        is_supported_language_code,
        normalize_language_code,
    )

    if is_supported_language_code(explicit):
        return normalize_language_code(str(explicit), format="full")
    try:
        return get_global_language_full()
    except Exception:
        return None


def _build_ai_context_fields(
    character_payload: dict,
    existing_fields: set[str] | None = None,
    entity: str = "neko",
    lang: str | None = None,
) -> dict[str, str]:
    """Expand hidden runtime events into synthetic fields used only for prompt/memory sync.

    entity indicates whether this payload is the catgirl (neko) or the master, which decides
    the person used in rename records: the master's records go into the master section of the
    catgirl persona and must be second person, never first person.

    ``lang`` pins the sentence. When it is absent, rendering follows the
    process language, including any active ``language_context``.
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

    lang = _resolve_profile_rename_language(lang)

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

    # The old personality feature wrote the entire templated personality prompt directly into system_prompt.
    # Such historical fragments must no longer be fed directly to the model, whether or not persona_override remains.
    if _has_generated_persona_selection_prompt(stored_prompt):
        cleaned_prompt = strip_generated_persona_selection_prompt(stored_prompt)
        return cleaned_prompt or get_lanlan_prompt()

    return stored_prompt
