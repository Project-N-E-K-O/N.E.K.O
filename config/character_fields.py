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

"""Reserved character fields and legacy reserved-schema migration metadata."""

# Reserved character-profile fields (central management).
# - system: maintained by designated system features, never modified directly via generic character editors.
# - workshop: import/publish workflow only, never pass through external character cards.
CHARACTER_SYSTEM_RESERVED_FIELDS = (
    "_reserved",
    "character_id",
    "live2d",
    "voice_id",
    "system_prompt",
    "model_type",
    "live3d_sub_type",
    "vrm",
    "vrm_animation",
    "lighting",
    "vrm_rotation",
    "live2d_item_id",
    "live2d_idle_animation",
    "item_id",
    "idleAnimation",
    "idleAnimations",
    "mmd",
    "mmd_animation",
    "mmd_idle_animation",
    "mmd_idle_animations",
    "touch_set",
    "_field_order",
)

CHARACTER_WORKSHOP_RESERVED_FIELDS = (
    "原始数据",
    "文件路径",
    "创意工坊物品ID",
    "description",
    "tags",
    "name",
    "描述",
    "标签",
    "关键词",
)

CHARACTER_RESERVED_FIELDS = tuple(
    dict.fromkeys((*CHARACTER_SYSTEM_RESERVED_FIELDS, *CHARACTER_WORKSHOP_RESERVED_FIELDS))
)


# COMPAT(v1->v2): legacy flat field names from before the move into _reserved. Migration treats these top-level keys as
# legacy config, folds them into _reserved and deletes them from the profile, so no ordinary field written to a profile
# (e.g. the per-heading section fields from external imports) may use these names.
CHARACTER_LEGACY_FLAT_RESERVED_FIELDS = (
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
)

def get_character_reserved_fields() -> tuple[str, ...]:
    """Return the reserved character-profile fields (deduplicated, ordered)."""
    return CHARACTER_RESERVED_FIELDS


# Reserved character-field schema (v2).
# Centralize system-reserved fields in `_reserved`, grouped by avatar/live2d/vrm.
RESERVED_FIELD_SCHEMA = {
    "character_id": str,
    # voice_id supports legacy flat strings and unified voice-source objects {source,provider,ref}
    # (union-find-style lazy migration, one entry when users set voice). Otherwise migrated characters
    # get spurious _reserved.voice_id schema errors from validate_reserved_schema on every load.
    "voice_id": (str, dict),
    "system_prompt": str,
    "field_order": list,
    # Stable character ID (32 lowercase hex): generated on creation/import, survives rename; backfill_character_uids fills existing IDs at startup.
    "character_uid": str,
    "persona_override": {
        "preset_id": str,
        "selected_at": str,
        "source": str,
        "prompt_guidance": str,
        "profile": dict,
    },
    "ai_context": {
        "rename_events": list,
    },
    "character_origin": {
        "source": str,
        "source_id": str,
        "display_name": str,
        "model_ref": str,
    },
    "avatar": {
        "model_type": str,
        "live3d_sub_type": str,
        "asset_source": str,
        "asset_source_id": str,
        "live2d": {
            "model_path": str,
        },
        "vrm": {
            "model_path": str,
            "animation": (str, dict, list, type(None)),
            "idle_animation": (str, list, type(None)),
            "lighting": (dict, type(None)),
            "cursor_follow": (dict, type(None)),
        },
        "mmd": {
            "model_path": str,
            "animation": (str, dict, list, type(None)),
            "idle_animation": (str, list, type(None)),
            "lighting": (dict, type(None)),
            "rendering": (dict, type(None)),
            "physics": (dict, type(None)),
            "cursor_follow": (dict, type(None)),
        },
    },
}

# Compatibility migration map: old flat field → _reserved path.
# rotation / camera_position / position / scale / viewport / display remain local preferences,
# never migrated to characters.json.
LEGACY_FLAT_TO_RESERVED = {
    "voice_id": ("voice_id",),
    "system_prompt": ("system_prompt",),
    "model_type": ("avatar", "model_type"),
    "live3d_sub_type": ("avatar", "live3d_sub_type"),
    "live2d_item_id": ("avatar", "asset_source_id"),
    "item_id": ("avatar", "asset_source_id"),
    "live2d": ("avatar", "live2d", "model_path"),
    "vrm": ("avatar", "vrm", "model_path"),
    "vrm_animation": ("avatar", "vrm", "animation"),
    "idleAnimation": ("avatar", "vrm", "idle_animation"),
    "idleAnimations": ("avatar", "vrm", "idle_animation"),
    "lighting": ("avatar", "vrm", "lighting"),
    "mmd": ("avatar", "mmd", "model_path"),
    "mmd_animation": ("avatar", "mmd", "animation"),
    "mmd_idle_animation": ("avatar", "mmd", "idle_animation"),
    "mmd_idle_animations": ("avatar", "mmd", "idle_animation"),
}
